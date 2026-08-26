from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FAKE_NAME, FAKE_PHONE, assert_no_pii, patient_payload

pytestmark = pytest.mark.integration


def test_create_patient_returns_hash_and_never_the_phone(client: TestClient) -> None:
    response = client.post("/patients", json=patient_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["consent_status"] == "accepted"
    assert body["terms_accepted_at"] is not None
    assert len(body["phone_hash"]) == 64
    assert "phone" not in body
    assert FAKE_PHONE not in response.text
    assert "5511900000001" not in response.text
    assert body["name"] == FAKE_NAME  # cadastro operacional pode ter nome


def test_create_without_terms_creates_pending_patient(client: TestClient) -> None:
    response = client.post("/patients", json=patient_payload(terms_accepted=False))

    assert response.status_code == 201
    assert response.json()["consent_status"] == "pending"
    assert response.json()["terms_accepted_at"] is None


def test_duplicate_phone_returns_409_without_echoing_phone(client: TestClient) -> None:
    client.post("/patients", json=patient_payload())
    response = client.post("/patients", json=patient_payload(phone="55 (11) 90000-0001"))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PATIENT_ALREADY_EXISTS"
    assert_no_pii(response.text)


@pytest.mark.parametrize("bad_phone", ["123", "abc", "1" * 20])
def test_invalid_phone_returns_422_without_echoing_input(
    client: TestClient, bad_phone: str
) -> None:
    response = client.post("/patients", json=patient_payload(phone=bad_phone))

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "phone" in body["error"]["details"]["field_errors"]
    assert bad_phone not in response.text
    assert "input" not in response.text


def test_get_patient(client: TestClient, create_patient: Any) -> None:
    created = create_patient()
    response = client.get(f"/patients/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]
    assert "phone" not in response.json()


def test_get_unknown_patient_returns_typed_404(client: TestClient) -> None:
    response = client.get("/patients/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PATIENT_NOT_FOUND"
    assert response.headers["X-Request-ID"]


def test_error_envelope_carries_request_id(client: TestClient) -> None:
    response = client.get("/patients/00000000-0000-0000-0000-000000000000")

    assert response.json()["request_id"] == response.headers["X-Request-ID"]
