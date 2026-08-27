"""
Hardening de cadastro/consentimento, event store, hashing, erros, middleware e config.

Cada teste prova uma borda: entrada inválida sem echo, PII bloqueada em todas as
superfícies, request_id saneado, boot falhando cedo com mensagem amigável.
"""

import json
import logging
import shutil
import traceback
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import AppEnv, Settings, load_settings
from app.container import Container
from app.core.clock import FixedClock
from app.core.exceptions import ConfigurationError, PIIGuardViolationError
from app.core.logging import get_logger
from app.features.events.models import Event, EventName
from app.main import create_app
from tests.conftest import (
    FAKE_BIRTH_DATE,
    FAKE_NAME,
    FAKE_PHONE,
    FAKE_PHONE_DIGITS,
    FROZEN_NOW,
    CreatePatient,
    assert_no_pii,
    patient_payload,
)

pytestmark = pytest.mark.integration

UNKNOWN_ID = "00000000-0000-0000-0000-000000000000"
PHONE_LIKE_INT = 5511900000001


def field_errors(response: Any) -> dict[str, list[str]]:
    """Extrai `field_errors` de um 422, garantindo o envelope e a ausência de `input`."""
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    # Só `field_errors` — nunca as chaves `input`/`ctx` que o Pydantic inclui por padrão.
    assert set(body["error"]["details"]) == {"field_errors"}
    assert '"input"' not in response.text and '"ctx"' not in response.text
    errors: dict[str, list[str]] = body["error"]["details"]["field_errors"]
    return errors


# -----------------------------------------------------------------------------
# Cadastro: validação de entrada sem echo
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("phone", ["+" + "1" * 10, "+55 (11) 9 0000-0001", "5" * 15])
def test_phone_with_10_to_15_digits_is_accepted(client: TestClient, phone: str) -> None:
    response = client.post("/patients", json=patient_payload(phone=phone))

    assert response.status_code == 201, response.text
    assert len(response.json()["phone_hash"]) == 64
    assert phone not in response.text


@pytest.mark.parametrize("phone", ["3" * 9, "4" * 16, "+55 11 900-00"])
def test_phone_outside_10_to_15_digits_is_rejected_without_echo(
    client: TestClient, phone: str
) -> None:
    response = client.post("/patients", json=patient_payload(phone=phone))

    errors = field_errors(response)
    assert list(errors) == ["phone"]
    assert phone not in response.text


@pytest.mark.parametrize(
    "phone",
    [
        "abc" + FAKE_PHONE_DIGITS,  # letras coladas
        "ligar para " + FAKE_PHONE,  # texto livre com telefone dentro
        "٥٥١١٩٠٠٠٠٠٠٠١",  # dígitos não-ASCII (indo-arábicos)
        "55 11 90000-0001 ramal 12",
    ],
)
def test_phone_with_letters_or_non_ascii_digits_is_rejected(client: TestClient, phone: str) -> None:
    response = client.post("/patients", json=patient_payload(phone=phone))

    errors = field_errors(response)
    assert list(errors) == ["phone"]
    assert_no_pii(response.text)
    assert phone not in response.text


def test_phone_sent_as_number_is_rejected_without_echo(client: TestClient) -> None:
    response = client.post("/patients", json=patient_payload(phone=PHONE_LIKE_INT))

    assert list(field_errors(response)) == ["phone"]
    assert str(PHONE_LIKE_INT) not in response.text


def test_name_is_stripped_and_blank_name_is_rejected(client: TestClient) -> None:
    created = client.post("/patients", json=patient_payload(name=f"   {FAKE_NAME}   "))
    assert created.status_code == 201, created.text
    assert created.json()["name"] == FAKE_NAME

    blank = client.post("/patients", json=patient_payload(name="     ", phone="+55 11 90000-0002"))
    assert list(field_errors(blank)) == ["name"]


def test_birth_date_in_the_future_is_rejected_without_echo(client: TestClient) -> None:
    response = client.post("/patients", json=patient_payload(birth_date="2999-01-01"))

    assert list(field_errors(response)) == ["birth_date"]
    assert "2999-01-01" not in response.text


@pytest.mark.parametrize("birth_date", ["20/05/1990", "1990-13-01", "ontem"])
def test_malformed_birth_date_is_rejected_without_echo(client: TestClient, birth_date: str) -> None:
    response = client.post("/patients", json=patient_payload(birth_date=birth_date))

    assert list(field_errors(response)) == ["birth_date"]
    assert birth_date not in response.text


def test_invalid_sex_is_rejected_without_echo(client: TestClient) -> None:
    response = client.post("/patients", json=patient_payload(sex="desconhecido"))

    assert list(field_errors(response)) == ["sex"]
    assert "desconhecido" not in response.text


def test_terms_accepted_must_be_boolean(client: TestClient) -> None:
    response = client.post("/patients", json=patient_payload(terms_accepted="sim"))

    assert list(field_errors(response)) == ["terms_accepted"]
    assert "sim" not in response.json()["error"]["details"]["field_errors"]["terms_accepted"][0]


def test_missing_fields_are_listed_without_input(client: TestClient) -> None:
    response = client.post("/patients", json={"phone": FAKE_PHONE})

    errors = field_errors(response)
    assert set(errors) == {"name", "birth_date", "sex", "terms_accepted"}
    assert_no_pii(response.text)


def test_malformed_json_returns_422_envelope_without_echo(client: TestClient) -> None:
    raw = f'{{"phone": "{FAKE_PHONE}", "name": "{FAKE_NAME}", "birth_date": '.encode()

    response = client.post("/patients", content=raw, headers={"content-type": "application/json"})

    errors = field_errors(response)
    assert list(errors) == ["__root__"]  # e não a posição do byte onde o JSON quebrou
    assert_no_pii(response.text)


def test_empty_body_returns_422_envelope(client: TestClient) -> None:
    response = client.post("/patients", content=b"", headers={"content-type": "application/json"})

    assert field_errors(response) == {"__root__": ["Field required"]}


@pytest.mark.parametrize(
    ("content_type", "raw"),
    [
        (
            "application/x-www-form-urlencoded",
            f"phone={FAKE_PHONE_DIGITS}&name={FAKE_NAME}&birth_date={FAKE_BIRTH_DATE}".encode(),
        ),
        ("text/plain", json.dumps(patient_payload()).encode()),
    ],
)
def test_wrong_content_type_returns_422_without_echo(
    client: TestClient, content_type: str, raw: bytes
) -> None:
    response = client.post("/patients", content=raw, headers={"content-type": content_type})

    assert list(field_errors(response)) == ["__root__"]
    assert_no_pii(response.text)


# -----------------------------------------------------------------------------
# Caminhos de erro fora do body: path, query, rota, método, exceção não tratada
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [FAKE_PHONE, FAKE_NAME, FAKE_BIRTH_DATE, "not-a-uuid"])
def test_path_and_query_validation_errors_never_echo_a_fragment_of_the_input(
    client: TestClient, raw: str
) -> None:
    responses = [
        client.get(f"/patients/{raw}"),
        client.post(f"/patients/{raw}/consent/revoke"),
        client.get("/events", params={"patient_id": raw}),
    ]

    for response in responses:
        assert list(field_errors(response)) == ["patient_id"]
        assert_no_pii(response.text)
        assert raw not in response.text
        # Pydantic cita o primeiro caractere inválido entre crases ("found `+` at 1").
        assert f"`{raw[0]}`" not in response.text


def test_unknown_event_name_filter_is_rejected_without_echo(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    response = client.get(
        "/events", params={"patient_id": patient["id"], "event_name": f"evento_{FAKE_NAME}"}
    )

    assert list(field_errors(response)) == ["event_name"]
    assert_no_pii(response.text)


def test_unknown_route_returns_error_envelope_with_request_id(client: TestClient) -> None:
    response = client.get(f"/rota-inexistente/{FAKE_PHONE_DIGITS}")

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "HTTP_ERROR"
    assert body["request_id"] == response.headers["X-Request-ID"]
    assert_no_pii(response.text)


def test_method_not_allowed_returns_envelope_and_allow_header(client: TestClient) -> None:
    response = client.delete(f"/patients/{UNKNOWN_ID}")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "HTTP_ERROR"
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert "GET" in response.headers["allow"]


def test_unhandled_exception_returns_envelope_with_request_id_and_correlated_log(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("falha simulada")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    assert response.json()["error"] == {
        "code": "INTERNAL_ERROR",
        "message": "Erro interno",
        "details": None,
    }
    request_id = response.headers["X-Request-ID"]
    assert response.json()["request_id"] == request_id
    assert len(request_id) == 36  # uuid4 gerado no servidor
    assert "falha simulada" not in response.text

    events = [r.msg for r in caplog.records if isinstance(r.msg, dict)]
    failure = next(e for e in events if e["event"] == "unhandled_exception")
    assert failure["request_id"] == request_id  # liga a linha de log ao request
    assert failure["error_type"] == "RuntimeError"
    access = next(e for e in events if e["event"] == "request_failed")
    assert access["request_id"] == request_id


# -----------------------------------------------------------------------------
# Middleware: request_id sempre gerado no servidor
# -----------------------------------------------------------------------------


def test_client_request_id_is_never_propagated(client: TestClient) -> None:
    """Um valor do cliente iria parar em logs e na trilha imutável: o servidor gera o seu."""
    created = client.post(
        "/patients", json=patient_payload(), headers={"X-Request-ID": "req-Abc_123.z"}
    )
    server_id = created.headers["X-Request-ID"]
    assert server_id != "req-Abc_123.z" and len(server_id) == 36

    trail = client.get("/events", params={"patient_id": created.json()["id"]}).json()["data"]
    assert {e["correlation_id"] for e in trail} == {server_id}

    error = client.get(f"/patients/{UNKNOWN_ID}", headers={"X-Request-ID": "req-Abc_123.z"})
    assert error.json()["request_id"] == error.headers["X-Request-ID"] != "req-Abc_123.z"


@pytest.mark.parametrize(
    "unsafe",
    [
        pytest.param(FAKE_PHONE_DIGITS, id="phone_digits"),  # iria parar na trilha
        pytest.param(FAKE_PHONE, id="phone_formatted"),
        pytest.param("p" + FAKE_PHONE_DIGITS, id="phone_with_prefix"),
        pytest.param(FAKE_NAME, id="name"),
        pytest.param(FAKE_BIRTH_DATE, id="birth_date"),
        pytest.param("x" * 5000, id="too_long"),  # seria ecoado no header e no envelope
        pytest.param("<script>", id="markup"),
    ],
)
def test_injected_request_id_never_reaches_trail_or_response(
    client: TestClient, unsafe: str
) -> None:
    created = client.post("/patients", json=patient_payload(), headers={"X-Request-ID": unsafe})

    assert created.status_code == 201, created.text
    generated = created.headers["X-Request-ID"]
    assert generated != unsafe
    assert len(generated) == 36  # uuid4

    trail = client.get("/events", params={"patient_id": created.json()["id"]}).json()
    assert {e["correlation_id"] for e in trail["data"]} == {generated}
    assert_no_pii(json.dumps(trail))


def test_request_log_never_carries_pii_from_the_path(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    client.get(f"/patients/{FAKE_PHONE_DIGITS}")
    client.get(f"/patients/{FAKE_PHONE}")

    rendered = [repr(record.msg) for record in caplog.records]
    assert any("request_completed" in line for line in rendered)
    for line in rendered:
        assert FAKE_PHONE_DIGITS not in line and FAKE_PHONE not in line


# -----------------------------------------------------------------------------
# Event store: guarda de PII em todo o envelope, imutabilidade, filtro
# -----------------------------------------------------------------------------


def test_store_refuses_phone_like_integers_in_properties(container: Container) -> None:
    with pytest.raises(PIIGuardViolationError) as exc:
        container.events.append(EventName.PATIENT_CREATED, "hash", {"contact": PHONE_LIKE_INT})
    assert exc.value.details["violations"] == ["properties.contact (phone_like_value)"]

    with pytest.raises(PIIGuardViolationError):
        container.events.append(
            EventName.PATIENT_CREATED, "hash", {"nested": {"items": [1, PHONE_LIKE_INT]}}
        )
    assert len(container.events) == 0


def test_store_accepts_small_and_large_non_phone_numbers(container: Container) -> None:
    event = container.events.append(
        EventName.PROTOCOL_COMPLETED,
        "hash",
        {"score": 27, "remaining_hours": 71.98, "big_counter": 10**16, "flag": True},
    )
    assert dict(event.properties) == {
        "score": 27,
        "remaining_hours": 71.98,
        "big_counter": 10**16,
        "flag": True,
    }


def test_store_refuses_phone_like_patient_id_hash(container: Container) -> None:
    with pytest.raises(PIIGuardViolationError) as exc:
        container.events.append(EventName.PATIENT_CREATED, FAKE_PHONE_DIGITS, {})
    assert exc.value.details["violations"] == ["patient_id_hash (phone_like_value)"]
    assert FAKE_PHONE_DIGITS not in str(exc.value.details)
    assert len(container.events) == 0


def test_event_properties_are_read_only_even_without_explicit_properties() -> None:
    event = Event(
        event_id=uuid4(),
        occurred_at=FROZEN_NOW,
        event_name=EventName.PATIENT_CREATED,
        patient_id_hash="hash",
    )
    with pytest.raises(TypeError):
        event.properties["x"] = 1  # type: ignore[index]


def test_trail_filter_by_absent_event_name_returns_empty_list(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient(terms_accepted=False)
    response = client.get(
        "/events", params={"patient_id": patient["id"], "event_name": "terms_accepted"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "patient_id": patient["id"],
        "patient_id_hash": patient["phone_hash"],
        "total": 0,
        "data": [],
    }


def test_trail_of_one_patient_never_leaks_into_another(
    client: TestClient, create_patient: CreatePatient
) -> None:
    first = create_patient()
    second = create_patient(phone="+55 11 90000-0002", terms_accepted=False)

    first_trail = client.get("/events", params={"patient_id": first["id"]}).json()
    second_trail = client.get("/events", params={"patient_id": second["id"]}).json()

    assert first_trail["total"] == 2 and second_trail["total"] == 1
    assert {e["patient_id_hash"] for e in first_trail["data"]} == {first["phone_hash"]}
    assert {e["patient_id_hash"] for e in second_trail["data"]} == {second["phone_hash"]}


# -----------------------------------------------------------------------------
# Consentimento: máquina de estados e apagamento de PII
# -----------------------------------------------------------------------------


def test_full_consent_lifecycle_emits_one_event_per_transition_with_previous_status(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient(terms_accepted=False)
    for action in ("accept", "pause", "resume", "revoke"):
        response = client.post(f"/patients/{patient['id']}/consent/{action}")
        assert response.status_code == 200, response.text

    trail = client.get("/events", params={"patient_id": patient["id"]}).json()["data"]
    assert [e["event_name"] for e in trail] == [
        "patient_created",
        "terms_accepted",
        "consent_paused",
        "consent_resumed",
        "consent_revoked",
    ]
    assert trail[1]["properties"] == {"source": "consent_endpoint"}
    assert trail[2]["properties"] == {"previous_status": "accepted"}
    assert trail[3]["properties"] == {"previous_status": "paused"}
    assert trail[4]["properties"]["previous_status"] == "accepted"
    assert_no_pii(json.dumps(trail))


@pytest.mark.parametrize(
    ("setup", "expected_previous"),
    [([], "pending"), (["accept"], "accepted"), (["accept", "pause"], "paused")],
)
def test_revoke_erases_pii_from_every_non_terminal_state(
    client: TestClient, create_patient: CreatePatient, setup: list[str], expected_previous: str
) -> None:
    patient = create_patient(terms_accepted=False)
    for action in setup:
        assert client.post(f"/patients/{patient['id']}/consent/{action}").status_code == 200

    response = client.post(f"/patients/{patient['id']}/consent/revoke")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["consent_status"] == "revoked"
    assert body["name"] is None and body["birth_date"] is None
    assert body["phone_hash"] == patient["phone_hash"]
    assert_no_pii(response.text)
    revoked = client.get("/events", params={"patient_id": patient["id"]}).json()["data"][-1]
    assert revoked["properties"] == {
        "previous_status": expected_previous,
        "erased_fields": ["phone", "name", "birth_date"],
    }
    assert_no_pii(client.get(f"/patients/{patient['id']}").text)


def test_invalid_transition_leaves_patient_and_trail_untouched(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    before_trail = client.get("/events", params={"patient_id": patient["id"]}).json()["data"]

    response = client.post(f"/patients/{patient['id']}/consent/resume")

    assert response.status_code == 409
    assert response.json()["error"]["details"] == {"from": "accepted", "action": "resume"}
    after = client.get(f"/patients/{patient['id']}").json()
    assert after == patient  # nem status nem consent_updated_at mudaram
    assert (
        client.get("/events", params={"patient_id": patient["id"]}).json()["data"] == before_trail
    )


def test_unknown_consent_action_is_rejected_before_lookup_without_echo(
    client: TestClient,
) -> None:
    response = client.post(f"/patients/{UNKNOWN_ID}/consent/apagar_{FAKE_NAME}")

    assert list(field_errors(response)) == ["action"]
    assert_no_pii(response.text)


# -----------------------------------------------------------------------------
# Hashing e configuração
# -----------------------------------------------------------------------------


def test_phone_hash_depends_on_the_configured_salt(clock: FixedClock) -> None:
    hashes: list[str] = []
    for salt in ("salt-a-0123456789abcdef", "salt-b-0123456789abcdef"):
        settings = Settings(
            PHONE_HASH_SALT=salt,
            APP_ENV=AppEnv.TESTING,
            LOG_FORMAT="json",
            _env_file=None,  # type: ignore[call-arg]
        )
        with TestClient(create_app(settings=settings, clock=clock)) as client:
            hashes.append(client.post("/patients", json=patient_payload()).json()["phone_hash"])

    assert len(hashes[0]) == len(hashes[1]) == 64
    assert hashes[0] != hashes[1]


def test_missing_salt_fails_boot_with_friendly_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # sem .env por perto
    monkeypatch.delenv("PHONE_HASH_SALT", raising=False)

    with pytest.raises(RuntimeError) as exc:
        load_settings()

    assert "PHONE_HASH_SALT" in str(exc.value)
    assert "make setup" in str(exc.value)


def test_short_salt_fails_boot_without_leaking_the_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PHONE_HASH_SALT", "segredo-curto")

    with pytest.raises(RuntimeError) as exc:
        load_settings()

    assert "PHONE_HASH_SALT" in str(exc.value)
    assert "16" in str(exc.value)  # diz o que está errado…
    printed = "".join(traceback.format_exception(exc.value))
    assert "segredo-curto" not in printed  # …sem imprimir o segredo no traceback do boot


def test_env_example_alone_does_not_boot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    shutil.copy(Path(__file__).parents[2] / ".env.example", tmp_path / ".env")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PHONE_HASH_SALT", raising=False)

    with pytest.raises(RuntimeError, match="PHONE_HASH_SALT"):
        load_settings()


# -----------------------------------------------------------------------------
# Container: artefatos inconsistentes derrubam o boot
# -----------------------------------------------------------------------------


def test_boot_fails_when_a_template_has_no_journey_plan(
    settings: Settings, clock: FixedClock, tmp_path: Path
) -> None:
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    source = settings.PROTOCOL_TEMPLATES_DIR / "phq9.json"
    shutil.copy(source, templates_dir / "phq9.json")
    orphan = json.loads(source.read_text(encoding="utf-8"))
    orphan["template_id"] = "phq9_sem_plano"
    (templates_dir / "orphan.json").write_text(json.dumps(orphan), encoding="utf-8")
    broken = settings.model_copy(update={"PROTOCOL_TEMPLATES_DIR": templates_dir})

    with pytest.raises(ConfigurationError, match="phq9_sem_plano"):
        create_app(settings=broken, clock=clock)


def test_boot_fails_when_templates_dir_is_missing(
    settings: Settings, clock: FixedClock, tmp_path: Path
) -> None:
    broken = settings.model_copy(update={"PROTOCOL_TEMPLATES_DIR": tmp_path / "nao-existe"})

    with pytest.raises(ConfigurationError):
        create_app(settings=broken, clock=clock)


# -----------------------------------------------------------------------------
# Logging: redação também para números com cara de telefone
# -----------------------------------------------------------------------------


def test_redaction_masks_phone_like_integers(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    get_logger("careless").info(
        "oops", contact=PHONE_LIKE_INT, nested={"items": [PHONE_LIKE_INT]}, score=27
    )

    record = next(
        r.msg for r in caplog.records if isinstance(r.msg, dict) and r.msg["event"] == "oops"
    )
    assert record["contact"] == "[redacted]"
    assert record["nested"] == {"items": ["[redacted]"]}
    assert record["score"] == 27
