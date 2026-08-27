from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import CreatePatient, event_names, run_protocol, start_protocol

pytestmark = pytest.mark.integration


def test_no_journey_before_protocol_completion(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    start_protocol(client, patient["id"])
    response = client.get(f"/patients/{patient['id']}/journeys")

    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert "journey_created" not in event_names(client, patient["id"])


def test_journey_created_on_protocol_completion(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    step = run_protocol(client, patient["id"], [1, 1])
    response = client.get(f"/journeys/{step['journey_id']}")

    assert response.status_code == 200
    journey = response.json()
    assert journey["status"] == "em_andamento"
    assert journey["patient_id"] == patient["id"]
    assert journey["source_session_id"] == step["session_id"]
    assert journey["template_id"] == "phq9"
    assert isinstance(journey["objective"], str) and journey["objective"]
    assert [t["status"] for t in journey["tasks"]] == ["em_andamento"] * 3
    assert all({"id", "title", "status"} <= set(t) for t in journey["tasks"])
    assert event_names(client, patient["id"])[-2:] == ["protocol_completed", "journey_created"]


def test_journey_is_also_created_when_protocol_runs_to_the_end(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    step = run_protocol(client, patient["id"], [3] * 9)

    assert step["journey_id"] is not None
    assert client.get(f"/patients/{patient['id']}/journeys").json()["total"] == 1


def test_there_is_no_endpoint_to_create_a_journey_directly(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()

    assert client.post("/journeys", json={}).status_code in (404, 405)
    assert client.post(f"/patients/{patient['id']}/journeys", json={}).status_code in (404, 405)


def test_complete_task_emits_event_and_journey_completes_on_last_task(
    client: TestClient, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    journey_id = completed_patient["journey_id"]
    tasks = client.get(f"/journeys/{journey_id}").json()["tasks"]

    first = client.post(f"/journeys/{journey_id}/tasks/{tasks[0]['id']}/complete")
    assert first.status_code == 200
    assert first.json()["tasks"][0]["status"] == "concluida"
    assert first.json()["tasks"][0]["completed_at"] is not None
    assert first.json()["status"] == "em_andamento"

    for task in tasks[1:]:
        last = client.post(f"/journeys/{journey_id}/tasks/{task['id']}/complete")
        assert last.status_code == 200

    assert last.json()["status"] == "concluida"
    assert last.json()["completed_at"] is not None
    names = event_names(client, patient_id)
    assert names.count("task_completed") == 3
    assert names[-1] == "journey_completed"
    trail = client.get("/events", params={"patient_id": patient_id}).json()["data"]
    task_events = [e for e in trail if e["event_name"] == "task_completed"]
    assert [e["properties"]["remaining_tasks"] for e in task_events] == [2, 1, 0]


def test_completing_a_task_twice_returns_409_without_duplicate_event(
    client: TestClient, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    journey_id = completed_patient["journey_id"]
    task_id = client.get(f"/journeys/{journey_id}").json()["tasks"][0]["id"]
    client.post(f"/journeys/{journey_id}/tasks/{task_id}/complete")

    response = client.post(f"/journeys/{journey_id}/tasks/{task_id}/complete")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TASK_ALREADY_COMPLETED"
    assert event_names(client, patient_id).count("task_completed") == 1


def test_unknown_task_returns_404(client: TestClient, completed_patient: dict[str, Any]) -> None:
    journey_id = completed_patient["journey_id"]
    response = client.post(
        f"/journeys/{journey_id}/tasks/00000000-0000-0000-0000-000000000000/complete"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TASK_NOT_FOUND"


def test_unknown_journey_returns_404(client: TestClient) -> None:
    response = client.get("/journeys/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOURNEY_NOT_FOUND"


def test_completing_task_requires_active_consent(
    client: TestClient, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    journey_id = completed_patient["journey_id"]
    task_id = client.get(f"/journeys/{journey_id}").json()["tasks"][0]["id"]
    client.post(f"/patients/{patient_id}/consent/pause")

    response = client.post(f"/journeys/{journey_id}/tasks/{task_id}/complete")

    assert response.status_code == 403
    assert response.json()["error"]["details"] == {"consent_status": "paused"}


def test_list_journeys_for_unknown_patient_returns_404(client: TestClient) -> None:
    response = client.get("/patients/00000000-0000-0000-0000-000000000000/journeys")

    assert response.status_code == 404
