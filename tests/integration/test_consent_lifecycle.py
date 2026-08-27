"""Proposta: consentimento como ciclo de vida, compatível com trilha append-only."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    FAKE_BIRTH_DATE,
    FAKE_NAME,
    CreatePatient,
    answer,
    assert_no_pii,
    event_names,
    patient_payload,
    start_protocol,
)

pytestmark = pytest.mark.integration


def consent(client: TestClient, patient_id: str, action: str) -> Any:
    return client.post(f"/patients/{patient_id}/consent/{action}")


def test_accept_from_pending_emits_terms_accepted(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient(terms_accepted=False)
    response = consent(client, patient["id"], "accept")

    assert response.status_code == 200
    assert response.json()["consent_status"] == "accepted"
    assert response.json()["terms_accepted_at"] is not None
    assert event_names(client, patient["id"]) == ["patient_created", "terms_accepted"]
    assert start_protocol(client, patient["id"])["status"] == "in_progress"


def test_pause_blocks_processing_and_resume_restores_it(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    assert consent(client, patient["id"], "pause").json()["consent_status"] == "paused"

    blocked = client.post(f"/patients/{patient['id']}/protocols", json={"template_id": "phq9"})
    assert blocked.status_code == 403
    assert blocked.json()["error"]["details"] == {"consent_status": "paused"}
    decision = client.post("/followups/evaluate", json={"patient_id": patient["id"]}).json()
    assert decision["reason"] == "consent_paused"

    assert consent(client, patient["id"], "resume").json()["consent_status"] == "accepted"
    assert start_protocol(client, patient["id"])["status"] == "in_progress"
    assert event_names(client, patient["id"])[2:5] == [
        "consent_paused",
        "followup_skipped",
        "consent_resumed",
    ]


def test_revoke_erases_pii_but_keeps_the_trail_intact(
    client: TestClient, completed_patient: dict[str, Any]
) -> None:
    patient = completed_patient["patient"]
    before = client.get("/events", params={"patient_id": patient["id"]}).json()["data"]

    response = consent(client, patient["id"], "revoke")

    assert response.status_code == 200
    body = response.json()
    assert body["consent_status"] == "revoked"
    assert body["name"] is None and body["birth_date"] is None
    assert body["phone_hash"] == patient["phone_hash"]  # correlação com a trilha preservada
    assert FAKE_NAME not in response.text and FAKE_BIRTH_DATE not in response.text

    fetched = client.get(f"/patients/{patient['id']}").json()
    assert fetched["name"] is None and fetched["consent_status"] == "revoked"

    after = client.get("/events", params={"patient_id": patient["id"]}).json()["data"]
    assert after[: len(before)] == before  # nada foi alterado ou removido
    assert after[-1]["event_name"] == "consent_revoked"
    assert after[-1]["properties"] == {
        "previous_status": "accepted",
        "erased_fields": ["phone", "name", "birth_date"],
    }
    assert_no_pii(client.get("/events", params={"patient_id": patient["id"]}).text)


def test_revoked_patient_is_blocked_everywhere(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    step = start_protocol(client, patient["id"])
    consent(client, patient["id"], "revoke")

    blocked_answer = answer(client, step["session_id"], "q1", 1)
    assert blocked_answer.status_code == 403
    assert blocked_answer.json()["error"]["details"] == {"consent_status": "revoked"}

    blocked_start = client.post(
        f"/patients/{patient['id']}/protocols", json={"template_id": "phq9"}
    )
    assert blocked_start.status_code == 403

    decision = client.post("/followups/evaluate", json={"patient_id": patient["id"]}).json()
    assert decision["eligible"] is False
    assert decision["reason"] == "consent_revoked"


@pytest.mark.parametrize(
    ("setup_actions", "action", "expected_from"),
    [
        ([], "resume", "accepted"),
        ([], "accept", "accepted"),
        (["pause"], "pause", "paused"),
        (["pause"], "accept", "paused"),
        (["revoke"], "accept", "revoked"),
        (["revoke"], "pause", "revoked"),
        (["revoke"], "resume", "revoked"),
        (["revoke"], "revoke", "revoked"),
    ],
)
def test_invalid_transitions_return_409_typed(
    client: TestClient,
    create_patient: CreatePatient,
    setup_actions: list[str],
    action: str,
    expected_from: str,
) -> None:
    patient = create_patient()
    for setup in setup_actions:
        assert consent(client, patient["id"], setup).status_code == 200

    response = consent(client, patient["id"], action)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_CONSENT_TRANSITION"
    assert response.json()["error"]["details"] == {"from": expected_from, "action": action}


def test_pending_patient_cannot_pause_but_can_revoke(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient(terms_accepted=False)

    assert consent(client, patient["id"], "pause").status_code == 409
    assert consent(client, patient["id"], "revoke").json()["consent_status"] == "revoked"


def test_unknown_action_returns_422(client: TestClient, create_patient: CreatePatient) -> None:
    patient = create_patient()

    assert consent(client, patient["id"], "delete").status_code == 422


def test_consent_action_on_unknown_patient_returns_404(client: TestClient) -> None:
    assert consent(client, "00000000-0000-0000-0000-000000000000", "revoke").status_code == 404


def test_revoke_releases_the_phone_and_a_new_registration_has_its_own_trail(
    client: TestClient, completed_patient: dict[str, Any]
) -> None:
    """Quem revogou pode voltar: novo cadastro, novo id, trilha própria — a antiga fica com o id antigo."""
    old = completed_patient["patient"]
    consent(client, old["id"], "revoke")
    old_trail = client.get("/events", params={"patient_id": old["id"]}).json()["data"]

    again = client.post("/patients", json=patient_payload())
    assert again.status_code == 201
    new = again.json()
    assert new["id"] != old["id"]
    assert new["phone_hash"] == old["phone_hash"]  # mesmo telefone, mesmo hash determinístico

    new_trail = client.get("/events", params={"patient_id": new["id"]}).json()["data"]
    assert [e["event_name"] for e in new_trail] == ["patient_created", "terms_accepted"]
    assert client.get("/events", params={"patient_id": old["id"]}).json()["data"] == old_trail
    assert event_names(client, old["id"])[-1] == "consent_revoked"

    # O cooldown também é por cadastro: a história antiga não bloqueia o novo titular.
    decision = client.post("/followups/evaluate", json={"patient_id": new["id"]}).json()
    assert decision["reason"] == "protocol_not_completed"
    assert decision["trace"][-1]["passed"] is True


def test_revoked_patient_still_readable_but_cannot_be_found_by_phone_again(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    consent(client, patient["id"], "revoke")

    assert client.get(f"/patients/{patient['id']}").json()["consent_status"] == "revoked"
    assert client.post("/patients", json=patient_payload()).status_code == 201
