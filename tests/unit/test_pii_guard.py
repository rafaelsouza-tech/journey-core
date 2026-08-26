import pytest

from app.core.exceptions import PIIGuardViolationError
from app.core.pii import find_pii, looks_like_phone, redact
from app.features.events.pii_guard import validate_properties

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "text",
    ["+55 11 90000-0001", "5511900000001", "(11) 9 0000-0001", "contato: 11900000001 ok"],
)
def test_detects_phone_like_values(text: str) -> None:
    assert looks_like_phone(text)


@pytest.mark.parametrize(
    "text",
    [
        "2026-08-26",
        "2026-08-26T12:00:00+00:00",
        "550e8400-e29b-41d4-a716-446655440000",
        "9f1234567890123b",
        "score 27",
        "phq9",
        "",
    ],
)
def test_ignores_dates_uuids_hashes_and_short_numbers(text: str) -> None:
    assert not looks_like_phone(text)


def test_find_pii_reports_paths_but_never_values() -> None:
    violations = find_pii({"nested": {"patient_phone": "x"}, "note": "5511900000001", "ok": 1})
    assert "$.nested.patient_phone (forbidden_key)" in violations
    assert "$.note (phone_like_value)" in violations
    assert not any("5511900000001" in v for v in violations)


def test_validate_properties_raises_typed_error_with_paths_only() -> None:
    with pytest.raises(PIIGuardViolationError) as exc:
        validate_properties("protocol_completed", {"name": "Fulano", "score": 3})
    assert exc.value.error_code == "PII_GUARD_VIOLATION"
    assert exc.value.details["violations"] == ["properties.name (forbidden_key)"]
    assert "Fulano" not in str(exc.value.details)


def test_validate_properties_accepts_clean_payload() -> None:
    validate_properties("protocol_completed", {"score": 3, "ended_by_skip": True, "ids": ["q1"]})


def test_redact_replaces_forbidden_keys_and_phone_values() -> None:
    assert redact({"phone": "x", "msg": "5511900000001", "score": 2}) == {
        "phone": "[redacted]",
        "msg": "[redacted]",
        "score": 2,
    }
