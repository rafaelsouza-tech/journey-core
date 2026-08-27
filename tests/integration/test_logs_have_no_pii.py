"""Telefone, nome e nascimento não podem aparecer em logs — nem por engano."""

import logging

import pytest
from fastapi.testclient import TestClient

from app.core.logging import get_logger, redact_pii
from tests.conftest import PII_NEEDLES, CreatePatient, run_protocol

pytestmark = pytest.mark.integration


def test_full_flow_logs_contain_events_but_no_pii(
    client: TestClient, create_patient: CreatePatient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    patient = create_patient()
    run_protocol(client, patient["id"], [1, 1])
    client.post("/followups/evaluate", json={"patient_id": patient["id"]})
    client.post(f"/patients/{patient['id']}/consent/revoke")
    client.post(
        "/patients",
        json={
            "phone": "123",
            "name": "x",
            "birth_date": "1990-01-01",
            "sex": "female",
            "terms_accepted": True,
        },
    )

    rendered = [f"{record.getMessage()} {record.msg!r}" for record in caplog.records]
    assert rendered, "nenhum log capturado"
    assert any("patient_created" in line for line in rendered)
    assert any("request_completed" in line for line in rendered)
    for line in rendered:
        for needle in PII_NEEDLES:
            assert needle not in line, f"PII em log: {needle!r}"


def test_redaction_processor_masks_keys_and_phone_like_values() -> None:
    event = {
        "event": "debug_dump",
        "phone": "5511900000001",
        "patient_phone": "x",
        "note": "ligar para 11 90000-0001 amanhã",
        "nested": {"name": "Fulano", "score": 3},
        "score": 27,
    }

    redacted = redact_pii(None, "info", event)

    assert redacted["phone"] == "[redacted]"
    assert redacted["patient_phone"] == "[redacted]"
    assert redacted["note"] == "[redacted]"
    assert redacted["nested"] == {"name": "[redacted]", "score": 3}
    assert redacted["score"] == 27
    assert redacted["event"] == "debug_dump"


def test_even_a_careless_log_call_is_redacted(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    get_logger("careless").info("oops", phone="5511900000001", name="Fulano")

    joined = " ".join(repr(record.msg) for record in caplog.records)
    assert "oops" in joined
    assert "5511900000001" not in joined and "Fulano" not in joined
