"""Fixtures compartilhadas: app isolada por teste, relógio fixo e helpers de fluxo."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import AppEnv, Settings
from app.container import Container
from app.core.clock import FixedClock
from app.main import create_app

TEST_SALT = "test-salt-0123456789abcdef-not-a-secret"
FROZEN_NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)

# Dados fictícios — servem também como "agulha" nas asserções de ausência de PII.
FAKE_PHONE = "+55 11 90000-0001"
FAKE_PHONE_DIGITS = "5511900000001"
FAKE_NAME = "Paciente Exemplo"
FAKE_BIRTH_DATE = "1990-05-20"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        PHONE_HASH_SALT=TEST_SALT,
        APP_ENV=AppEnv.TESTING,
        LOG_FORMAT="json",
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(FROZEN_NOW)


@pytest.fixture
def app(settings: Settings, clock: FixedClock) -> FastAPI:
    return create_app(settings=settings, clock=clock)


@pytest.fixture
def container(app: FastAPI) -> Container:
    container: Container = app.state.container
    return container


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def patient_payload(**overrides: Any) -> dict[str, Any]:
    """Payload válido de cadastro, com overrides."""
    payload: dict[str, Any] = {
        "phone": FAKE_PHONE,
        "name": FAKE_NAME,
        "birth_date": FAKE_BIRTH_DATE,
        "sex": "female",
        "terms_accepted": True,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def create_patient(client: TestClient) -> Callable[..., dict[str, Any]]:
    """Cria um paciente via API e devolve o JSON da resposta."""

    def _create(**overrides: Any) -> dict[str, Any]:
        response = client.post("/patients", json=patient_payload(**overrides))
        assert response.status_code == 201, response.text
        body: dict[str, Any] = response.json()
        return body

    return _create


PII_NEEDLES: tuple[str, ...] = (FAKE_PHONE, FAKE_PHONE_DIGITS, FAKE_NAME, FAKE_BIRTH_DATE)


def assert_no_pii(text: str) -> None:
    """Falha se qualquer dado fictício de PII aparecer no texto."""
    for needle in PII_NEEDLES:
        assert needle not in text, f"PII vazou: {needle!r}"


# -----------------------------------------------------------------------------
# Helpers de fluxo (protocolo / jornada)
# -----------------------------------------------------------------------------


def start_protocol(
    client: TestClient, patient_id: str, template_id: str = "phq9"
) -> dict[str, Any]:
    response = client.post(f"/patients/{patient_id}/protocols", json={"template_id": template_id})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def answer(client: TestClient, session_id: str, question_id: str, value: int) -> Any:
    return client.post(
        f"/protocol-sessions/{session_id}/answers",
        json={"question_id": question_id, "value": value},
    )


def run_protocol(client: TestClient, patient_id: str, values: list[int]) -> dict[str, Any]:
    """Inicia o PHQ-9 e responde `values` em ordem; devolve o último passo."""
    step = start_protocol(client, patient_id)
    for value in values:
        assert step["next_question"] is not None, "protocolo já concluído"
        response = answer(client, step["session_id"], step["next_question"]["id"], value)
        assert response.status_code == 200, response.text
        step = response.json()
    return step


@pytest.fixture
def completed_patient(
    client: TestClient, create_patient: Callable[..., dict[str, Any]]
) -> dict[str, Any]:
    """Paciente com PHQ-9 concluído por skip e jornada criada."""
    patient = create_patient()
    step = run_protocol(client, patient["id"], [1, 1])
    assert step["status"] == "completed"
    return {"patient": patient, "step": step, "journey_id": step["journey_id"]}
