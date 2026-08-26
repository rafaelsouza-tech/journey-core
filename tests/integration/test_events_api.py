from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.container import Container
from app.core.exceptions import PIIGuardViolationError
from app.features.events.models import EventName
from tests.conftest import assert_no_pii

pytestmark = pytest.mark.integration

ENVELOPE_KEYS = {"event_id", "occurred_at", "event_name", "patient_id_hash", "properties"}


def test_trail_uses_internal_id_in_query_and_hash_in_events(
    client: TestClient, create_patient: Any
) -> None:
    patient = create_patient()
    response = client.get("/events", params={"patient_id": patient["id"]})

    assert response.status_code == 200
    body = response.json()
    assert body["patient_id"] == patient["id"]
    assert body["patient_id_hash"] == patient["phone_hash"]
    assert [e["event_name"] for e in body["data"]] == ["patient_created", "terms_accepted"]
    for event in body["data"]:
        assert set(event) >= ENVELOPE_KEYS
        assert event["patient_id_hash"] == patient["phone_hash"]
        assert "patient_id" not in event


def test_trail_has_no_pii(client: TestClient, create_patient: Any) -> None:
    patient = create_patient()
    response = client.get("/events", params={"patient_id": patient["id"]})

    assert_no_pii(response.text)


def test_trail_can_be_filtered_by_event_name(client: TestClient, create_patient: Any) -> None:
    patient = create_patient()
    response = client.get(
        "/events", params={"patient_id": patient["id"], "event_name": "terms_accepted"}
    )

    assert response.json()["total"] == 1
    assert response.json()["data"][0]["event_name"] == "terms_accepted"


def test_trail_for_unknown_patient_returns_404(client: TestClient) -> None:
    response = client.get("/events", params={"patient_id": "00000000-0000-0000-0000-000000000000"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PATIENT_NOT_FOUND"


def test_events_carry_correlation_id_from_request(client: TestClient, create_patient: Any) -> None:
    patient = create_patient()
    response = client.get("/events", params={"patient_id": patient["id"]})

    assert all(event["correlation_id"] for event in response.json()["data"])


def test_store_has_no_update_or_delete_and_events_are_frozen(container: Container) -> None:
    store = container.events
    assert not hasattr(store, "update")
    assert not hasattr(store, "delete")
    assert not hasattr(store, "remove")

    event = store.append(EventName.PATIENT_CREATED, "hash", {"consent_status": "accepted"})
    with pytest.raises(TypeError):
        event.properties["consent_status"] = "x"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        event.event_name = EventName.TERMS_ACCEPTED  # type: ignore[misc]


def test_store_refuses_pii_in_properties(container: Container) -> None:
    with pytest.raises(PIIGuardViolationError):
        container.events.append(EventName.PATIENT_CREATED, "hash", {"phone": "5511900000001"})
    with pytest.raises(PIIGuardViolationError):
        container.events.append(EventName.PATIENT_CREATED, "hash", {"note": "5511900000001"})
    assert len(container.events) == 0
