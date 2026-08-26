from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.clock import FixedClock
from tests.conftest import assert_no_pii, run_protocol

pytestmark = pytest.mark.integration


def evaluate(client: TestClient, patient_id: str) -> Any:
    return client.post("/followups/evaluate", json={"patient_id": patient_id})


def trail(client: TestClient, patient_id: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = client.get("/events", params={"patient_id": patient_id}).json()[
        "data"
    ]
    return events


def test_not_eligible_before_protocol_completion(client: TestClient, create_patient: Any) -> None:
    patient = create_patient()
    response = evaluate(client, patient["id"])

    assert response.status_code == 200
    body = response.json()
    assert body["eligible"] is False
    assert body["reason"] == "protocol_not_completed"
    assert body["template_key"] == "checkin_adesao"
    skipped = trail(client, patient["id"])[-1]
    assert skipped["event_name"] == "followup_skipped"
    assert skipped["properties"]["reason"] == "protocol_not_completed"
    assert skipped["event_id"] == body["event_id"]


def test_missing_consent_reason(client: TestClient, create_patient: Any) -> None:
    patient = create_patient(terms_accepted=False)

    assert evaluate(client, patient["id"]).json()["reason"] == "missing_consent"


def test_eligible_after_completed_protocol_emits_followup_eligible(
    client: TestClient, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    body = evaluate(client, patient_id).json()

    assert body["eligible"] is True
    assert body["reason"] is None
    assert body["template_key"] == "checkin_adesao"
    assert [item["rule_id"] for item in body["trace"]] == [
        "consent",
        "protocol_completed",
        "journey_active",
        "active_task",
        "cooldown",
    ]
    assert all(item["passed"] for item in body["trace"])
    event = trail(client, patient_id)[-1]
    assert event["event_name"] == "followup_eligible"
    assert event["properties"]["template_key"] == "checkin_adesao"
    assert event["properties"]["trace"] == body["trace"]


def test_second_evaluation_within_72h_is_skipped_by_cooldown(
    client: TestClient, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    first = evaluate(client, patient_id).json()
    second = evaluate(client, patient_id).json()

    assert first["eligible"] is True
    assert second["eligible"] is False
    assert second["reason"] == "cooldown"
    cooldown = second["trace"][-1]
    assert cooldown["passed"] is False
    assert cooldown["observed"] == 0.0
    assert cooldown["expected"] == {"gte": 72}
    assert cooldown["details"]["remaining"] == 72.0
    assert cooldown["details"]["last_event_at"] is not None
    assert [e["event_name"] for e in trail(client, patient_id)[-2:]] == [
        "followup_eligible",
        "followup_skipped",
    ]


def test_eligible_again_after_72h(
    client: TestClient, clock: FixedClock, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    assert evaluate(client, patient_id).json()["eligible"] is True

    clock.advance(hours=71, minutes=59)
    still = evaluate(client, patient_id).json()
    assert still["reason"] == "cooldown"
    assert still["trace"][-1]["details"]["remaining"] == pytest.approx(0.02, abs=0.01)

    clock.advance(minutes=1)
    again = evaluate(client, patient_id).json()
    assert again["eligible"] is True
    assert again["trace"][-1]["observed"] == 72.0


def test_no_active_journey_after_all_tasks_completed(
    client: TestClient, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    journey_id = completed_patient["journey_id"]
    for task in client.get(f"/journeys/{journey_id}").json()["tasks"]:
        client.post(f"/journeys/{journey_id}/tasks/{task['id']}/complete")

    body = evaluate(client, patient_id).json()

    assert body["reason"] == "no_active_journey"
    assert [item["passed"] for item in body["trace"]] == [True, True, False, False, True]


def test_followup_events_have_no_pii(client: TestClient, completed_patient: dict[str, Any]) -> None:
    patient_id = completed_patient["patient"]["id"]
    evaluate(client, patient_id)
    evaluate(client, patient_id)

    assert_no_pii(client.get("/events", params={"patient_id": patient_id}).text)


def test_evaluate_unknown_patient_returns_404(client: TestClient) -> None:
    response = evaluate(client, "00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PATIENT_NOT_FOUND"


def test_new_protocol_after_journey_completion_makes_patient_eligible_again(
    client: TestClient, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    journey_id = completed_patient["journey_id"]
    for task in client.get(f"/journeys/{journey_id}").json()["tasks"]:
        client.post(f"/journeys/{journey_id}/tasks/{task['id']}/complete")
    assert evaluate(client, patient_id).json()["reason"] == "no_active_journey"

    run_protocol(client, patient_id, [3, 3, 0, 0, 0, 0, 0, 0, 0])

    assert evaluate(client, patient_id).json()["eligible"] is True
