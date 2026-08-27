"""Fluxo de ponta a ponta: cadastro → protocolo → jornada → follow-up → trilha → revogação."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.clock import FixedClock
from tests.conftest import assert_no_pii, patient_payload, run_protocol

pytestmark = pytest.mark.e2e

MINIMUM_TAXONOMY = {
    "patient_created",
    "terms_accepted",
    "protocol_started",
    "protocol_completed",
    "journey_created",
    "task_completed",
    "followup_eligible",
    "followup_skipped",
}


def test_end_to_end_walkthrough(client: TestClient, clock: FixedClock) -> None:
    # 1. Criar paciente
    created = client.post("/patients", json=patient_payload())
    assert created.status_code == 201
    patient = created.json()
    assert "phone" not in patient

    # 2. Responder PHQ até o skip → ver a jornada criada
    step = run_protocol(client, patient["id"], [1, 1])
    assert step["status"] == "completed" and step["result"]["ended_by_skip"] is True
    journey = client.get(f"/journeys/{step['journey_id']}").json()
    assert journey["status"] == "em_andamento" and len(journey["tasks"]) == 3

    # 2b. Concluir uma tarefa
    task = journey["tasks"][0]
    assert client.post(f"/journeys/{journey['id']}/tasks/{task['id']}/complete").status_code == 200

    # 3. Avaliar follow-up duas vezes e observar o cooldown de 72h
    first = client.post("/followups/evaluate", json={"patient_id": patient["id"]}).json()
    second = client.post("/followups/evaluate", json={"patient_id": patient["id"]}).json()
    assert first["eligible"] is True and first["template_key"] == "checkin_adesao"
    assert second["eligible"] is False and second["reason"] == "cooldown"
    assert second["trace"][-1]["details"]["remaining"] == 72.0
    clock.advance(hours=72)
    third = client.post("/followups/evaluate", json={"patient_id": patient["id"]}).json()
    assert third["eligible"] is True

    # 4. Inspecionar GET /events e confirmar ausência de PII
    events_response = client.get("/events", params={"patient_id": patient["id"]})
    assert events_response.status_code == 200
    assert_no_pii(events_response.text)
    events = events_response.json()["data"]
    assert all(e["patient_id_hash"] == patient["phone_hash"] for e in events)
    assert [e["event_name"] for e in events] == [
        "patient_created",
        "terms_accepted",
        "protocol_started",
        "protocol_completed",
        "journey_created",
        "task_completed",
        "followup_eligible",
        "followup_skipped",
        "followup_eligible",
    ]
    assert {e["event_name"] for e in events} >= MINIMUM_TAXONOMY

    # 5. Revogar consentimento: cadastro apagado, trilha intacta, follow-up recusado
    revoked = client.post(f"/patients/{patient['id']}/consent/revoke").json()
    assert revoked["consent_status"] == "revoked" and revoked["name"] is None
    after = client.get("/events", params={"patient_id": patient["id"]}).json()["data"]
    assert after[: len(events)] == events
    assert after[-1]["event_name"] == "consent_revoked"
    fourth = client.post("/followups/evaluate", json={"patient_id": patient["id"]}).json()
    assert fourth["reason"] == "consent_revoked"
    assert_no_pii(client.get("/events", params={"patient_id": patient["id"]}).text)


def test_walkthrough_variant_running_the_full_phq9(client: TestClient) -> None:
    patient = client.post("/patients", json=patient_payload(phone="+55 11 90000-0009")).json()
    step = run_protocol(client, patient["id"], [2, 1, 1, 1, 1, 1, 1, 1, 1])

    assert step["status"] == "completed"
    assert step["result"] == {
        "score": 10,
        "max_score": 27,
        "ended_by_skip": False,
        "skip_rule_id": None,
        "answered_questions": [f"q{i}" for i in range(1, 10)],
    }
    assert client.get(f"/journeys/{step['journey_id']}").json()["status"] == "em_andamento"


def test_health(client: TestClient) -> None:
    response: Any = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
