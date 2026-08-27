"""Bordas do motor de protocolo: skip logic exaustiva, estado da sessão, erros sem eco e loader."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import AppEnv, Settings
from app.container import Container
from app.core.clock import FixedClock
from app.core.exceptions import ConfigurationError
from app.features.protocols.loader import TemplateRegistry
from app.main import create_app
from tests.conftest import (
    FAKE_PHONE,
    TEST_SALT,
    CreatePatient,
    answer,
    assert_no_pii,
    event_names,
    patient_payload,
    run_protocol,
    start_protocol,
    trail,
)

pytestmark = pytest.mark.integration

PHQ9_TOTAL = 9
PHQ9_MAX_SCORE = 27
PROTOCOL_COMPLETED_KEYS = {
    "session_id",
    "template_id",
    "template_version",
    "score",
    "max_score",
    "ended_by_skip",
    "skip_rule_id",
    "answered_count",
    "total_questions",
}


def _get_step(client: TestClient, session_id: str) -> dict[str, Any]:
    response = client.get(f"/protocol-sessions/{session_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _journeys_total(client: TestClient, patient_id: str) -> int:
    total: int = client.get(f"/patients/{patient_id}/journeys").json()["total"]
    return total


# -----------------------------------------------------------------------------
# Skip logic PHQ-2 — todas as combinações das duas primeiras respostas
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(("q1", "q2"), [(q1, q2) for q1 in range(4) for q2 in range(4)])
def test_phq2_gate_is_evaluated_for_every_pair_of_first_answers(
    client: TestClient, create_patient: CreatePatient, q1: int, q2: int
) -> None:
    patient = create_patient()
    step = run_protocol(client, patient["id"], [q1, q2])
    names = event_names(client, patient["id"])

    if q1 + q2 < 3:
        assert step["status"] == "completed"
        assert step["next_question"] is None
        assert step["progress"] == {"answered": 2, "total": PHQ9_TOTAL}
        assert step["result"] == {
            "score": q1 + q2,
            "max_score": PHQ9_MAX_SCORE,
            "ended_by_skip": True,
            "skip_rule_id": "phq2_gate",
            "answered_questions": ["q1", "q2"],
        }
        assert step["journey_id"] is not None
        completed = next(
            e for e in trail(client, patient["id"]) if e["event_name"] == "protocol_completed"
        )
        assert completed["properties"]["score"] == q1 + q2
        assert completed["properties"]["answered_count"] == 2
        assert completed["properties"]["ended_by_skip"] is True
        assert names[-2:] == ["protocol_completed", "journey_created"]
    else:
        assert step["status"] == "in_progress"
        assert step["next_question"]["id"] == "q3"
        assert step["progress"] == {"answered": 2, "total": PHQ9_TOTAL}
        assert step["result"] is None and step["journey_id"] is None
        assert "protocol_completed" not in names
        assert _journeys_total(client, patient["id"]) == 0


# -----------------------------------------------------------------------------
# Progresso, resultado final e jornada só após a conclusão
# -----------------------------------------------------------------------------


def test_progress_and_next_question_advance_one_step_at_a_time_until_the_end(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    values = [3, 0, 2, 1, 3, 0, 1, 2, 3]
    step = start_protocol(client, patient["id"])
    session_id = step["session_id"]

    for index, value in enumerate(values, start=1):
        assert step["status"] == "in_progress"
        assert step["progress"] == {"answered": index - 1, "total": PHQ9_TOTAL}
        assert step["next_question"]["id"] == f"q{index}"
        assert step["next_question"]["order"] == index
        assert step["result"] is None and step["journey_id"] is None
        assert _get_step(client, session_id) == step  # consulta devolve o mesmo estado
        assert _journeys_total(client, patient["id"]) == 0
        response = answer(client, session_id, f"q{index}", value)
        assert response.status_code == 200, response.text
        step = response.json()

    assert step["status"] == "completed"
    assert step["progress"] == {"answered": PHQ9_TOTAL, "total": PHQ9_TOTAL}
    assert step["next_question"] is None
    assert step["result"] == {
        "score": sum(values),
        "max_score": PHQ9_MAX_SCORE,
        "ended_by_skip": False,
        "skip_rule_id": None,
        "answered_questions": [f"q{i}" for i in range(1, PHQ9_TOTAL + 1)],
    }
    assert step["journey_id"] is not None
    assert _get_step(client, session_id) == step
    journeys = client.get(f"/patients/{patient['id']}/journeys").json()
    assert journeys["total"] == 1
    assert journeys["data"][0]["id"] == step["journey_id"]
    assert journeys["data"][0]["source_session_id"] == session_id


def test_each_completed_session_creates_its_own_journey(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    first = run_protocol(client, patient["id"], [1, 1])
    second = run_protocol(client, patient["id"], [0, 0])

    assert first["session_id"] != second["session_id"]
    assert first["journey_id"] != second["journey_id"]
    assert _journeys_total(client, patient["id"]) == 2
    names = event_names(client, patient["id"])
    assert names.count("protocol_completed") == 2 and names.count("journey_created") == 2


# -----------------------------------------------------------------------------
# Minimização: eventos só no início e no fim, sem respostas cruas
# -----------------------------------------------------------------------------


def test_protocol_events_are_minimal_and_emitted_only_at_start_and_end(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    values = [3, 0, 3, 0, 3, 0, 3, 0, 3]
    step = run_protocol(client, patient["id"], values)
    events = trail(client, patient["id"])
    names = [event["event_name"] for event in events]

    assert names.count("protocol_started") == 1 and names.count("protocol_completed") == 1
    # Responder não gera evento: nada entre o início e o fim.
    assert names[names.index("protocol_started") + 1] == "protocol_completed"

    started = next(e for e in events if e["event_name"] == "protocol_started")
    assert started["properties"] == {
        "session_id": step["session_id"],
        "template_id": "phq9",
        "template_version": 1,
    }
    completed = next(e for e in events if e["event_name"] == "protocol_completed")
    assert set(completed["properties"]) == PROTOCOL_COMPLETED_KEYS
    assert completed["properties"] == {
        "session_id": step["session_id"],
        "template_id": "phq9",
        "template_version": 1,
        "score": sum(values),
        "max_score": PHQ9_MAX_SCORE,
        "ended_by_skip": False,
        "skip_rule_id": None,
        "answered_count": PHQ9_TOTAL,
        "total_questions": PHQ9_TOTAL,
    }
    for event in (started, completed):
        assert all(not isinstance(v, list | dict) for v in event["properties"].values())
    assert_no_pii(client.get("/events", params={"patient_id": patient["id"]}).text)


# -----------------------------------------------------------------------------
# Versão do template pinada na sessão
# -----------------------------------------------------------------------------


def test_session_reports_the_pinned_template_version_in_responses_and_events(
    client: TestClient, container: Container, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    pinned = container.templates.get("phq9").version
    step = run_protocol(client, patient["id"], [1, 1])

    assert step["template_version"] == pinned
    assert _get_step(client, step["session_id"])["template_version"] == pinned
    versions = {
        e["event_name"]: e["properties"]["template_version"]
        for e in trail(client, patient["id"])
        if e["event_name"] in {"protocol_started", "protocol_completed"}
    }
    assert versions == {"protocol_started": pinned, "protocol_completed": pinned}


def test_session_refuses_to_continue_on_a_template_with_a_different_version(
    client: TestClient, container: Container, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    step = run_protocol(client, patient["id"], [2])
    session_id = step["session_id"]
    original = container.templates
    names_before = event_names(client, patient["id"])

    # Simula a troca do JSON por uma versão nova enquanto a sessão está em curso.
    container.templates = TemplateRegistry([original.get("phq9").model_copy(update={"version": 2})])
    fetched = client.get(f"/protocol-sessions/{session_id}")
    blocked = answer(client, session_id, "q2", 1)

    assert fetched.status_code == 500
    assert fetched.json()["error"]["code"] == "CONFIGURATION_ERROR"
    assert blocked.status_code == 500
    assert blocked.json()["error"]["code"] == "CONFIGURATION_ERROR"
    assert_no_pii(fetched.text + blocked.text)
    assert event_names(client, patient["id"]) == names_before

    # Com a versão pinada de volta, a sessão segue de onde parou.
    container.templates = original
    assert _get_step(client, session_id) == step
    assert answer(client, session_id, "q2", 1).json()["next_question"]["id"] == "q3"


# -----------------------------------------------------------------------------
# Consentimento pausado/revogado no meio da sessão
# -----------------------------------------------------------------------------


def test_paused_consent_blocks_answers_without_touching_the_session_and_resume_continues(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    step = run_protocol(client, patient["id"], [2])
    session_id = step["session_id"]
    assert client.post(f"/patients/{patient['id']}/consent/pause").status_code == 200

    blocked = answer(client, session_id, "q2", 1)

    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "CONSENT_REQUIRED"
    assert blocked.json()["error"]["details"] == {"consent_status": "paused"}
    assert _get_step(client, session_id) == step
    names = event_names(client, patient["id"])
    assert names[-1] == "consent_paused" and "protocol_completed" not in names

    assert client.post(f"/patients/{patient['id']}/consent/resume").status_code == 200
    resumed = answer(client, session_id, "q2", 1)
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "in_progress"
    assert resumed.json()["progress"] == {"answered": 2, "total": PHQ9_TOTAL}
    assert resumed.json()["next_question"]["id"] == "q3"


def test_revoked_patient_with_an_open_session_is_refused_by_consent_not_by_conflict(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    step = start_protocol(client, patient["id"])
    assert client.post(f"/patients/{patient['id']}/consent/revoke").status_code == 200

    restart = client.post(f"/patients/{patient['id']}/protocols", json={"template_id": "phq9"})
    blocked = answer(client, step["session_id"], "q1", 0)

    assert restart.status_code == 403
    assert restart.json()["error"]["code"] == "CONSENT_REQUIRED"
    assert restart.json()["error"]["details"] == {"consent_status": "revoked"}
    assert blocked.status_code == 403
    # A sessão pseudonimizada continua consultável e intacta (só ids e contagens).
    assert _get_step(client, step["session_id"]) == step
    assert_no_pii(client.get(f"/protocol-sessions/{step['session_id']}").text)


# -----------------------------------------------------------------------------
# Respostas rejeitadas não mudam o estado
# -----------------------------------------------------------------------------


def test_rejected_answers_leave_the_session_untouched(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()
    step = run_protocol(client, patient["id"], [2])
    session_id = step["session_id"]

    out_of_order_and_out_of_scale = answer(client, session_id, "q3", 9)
    unknown_question = answer(client, session_id, "q99", 1)
    duplicated = answer(client, session_id, "q1", 1)
    out_of_scale = answer(client, session_id, "q2", 4)

    # Fora de ordem prevalece sobre valor inválido: nada é gravado.
    assert out_of_order_and_out_of_scale.status_code == 409
    assert out_of_order_and_out_of_scale.json()["error"]["details"] == {
        "expected": "q2",
        "received": "q3",
    }
    assert unknown_question.status_code == 409
    assert unknown_question.json()["error"]["details"] == {"expected": "q2", "received": "q99"}
    assert duplicated.status_code == 409
    assert duplicated.json()["error"]["code"] == "UNEXPECTED_QUESTION"
    assert out_of_scale.status_code == 422
    assert out_of_scale.json()["error"]["code"] == "INVALID_ANSWER_VALUE"
    assert out_of_scale.json()["error"]["details"]["allowed"] == [0, 1, 2, 3]

    assert _get_step(client, session_id) == step
    assert event_names(client, patient["id"])[-1] == "protocol_started"


def test_unknown_patient_and_unknown_session_return_typed_404(client: TestClient) -> None:
    missing = "00000000-0000-0000-0000-000000000000"

    start = client.post(f"/patients/{missing}/protocols", json={"template_id": "phq9"})
    reply = answer(client, missing, "q1", 1)

    assert start.status_code == 404
    assert start.json()["error"]["code"] == "PATIENT_NOT_FOUND"
    assert reply.status_code == 404
    assert reply.json()["error"]["code"] == "SESSION_NOT_FOUND"


# -----------------------------------------------------------------------------
# Entrada malformada: 422 sem eco (ids) e sem coerção (valor)
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("bad_id", [FAKE_PHONE, "Q1", "q 1", "1q", "q-1", ""])
def test_malformed_question_id_is_rejected_without_echoing_the_input(
    client: TestClient, create_patient: CreatePatient, bad_id: str
) -> None:
    patient = create_patient()
    step = start_protocol(client, patient["id"])

    response = answer(client, step["session_id"], bad_id, 1)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "question_id" in response.json()["error"]["details"]["field_errors"]
    if bad_id:
        assert bad_id not in response.text
    assert_no_pii(response.text)
    assert _get_step(client, step["session_id"]) == step


def test_malformed_template_id_is_rejected_without_echoing_the_input(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()

    response = client.post(f"/patients/{patient['id']}/protocols", json={"template_id": FAKE_PHONE})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "template_id" in response.json()["error"]["details"]["field_errors"]
    assert_no_pii(response.text)
    assert "protocol_started" not in event_names(client, patient["id"])


@pytest.mark.parametrize("value", [True, "1", 1.0])
def test_answer_value_must_be_a_json_integer_not_a_coercible_lookalike(
    client: TestClient, create_patient: CreatePatient, value: Any
) -> None:
    patient = create_patient()
    step = start_protocol(client, patient["id"])

    response = answer(client, step["session_id"], "q1", value)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "value" in response.json()["error"]["details"]["field_errors"]
    assert _get_step(client, step["session_id"]) == step


# -----------------------------------------------------------------------------
# Template fictício via API: o serviço é genérico e as sessões são por paciente/template
# -----------------------------------------------------------------------------

MINI_TEMPLATE: dict[str, Any] = {
    "template_id": "mini",
    "version": 3,
    "name": "Mini",
    "intro": "Pergunta-guia do mini",
    "scale": {"options": [{"value": 0, "label": "não"}, {"value": 1, "label": "sim"}]},
    # Fora de ordem de propósito: a API serve pela `order`, não pela posição na lista.
    "questions": [
        {"id": "c", "order": 3, "text": "C?"},
        {"id": "a", "order": 1, "text": "A?"},
        {"id": "b", "order": 2, "text": "B?"},
    ],
    "skip_rules": [
        {
            "id": "stop_if_a_is_no",
            "after_question": "a",
            "condition": {"op": "eq", "left": {"answer": "a"}, "right": 0},
            "action": "end_block",
        }
    ],
}
PLAIN_TEMPLATE: dict[str, Any] = {
    "template_id": "plain",
    "version": 1,
    "name": "Plain",
    "intro": "Pergunta-guia do plain",
    # Escala não contígua: os valores aceitos vêm do template, não de um intervalo.
    "scale": {"options": [{"value": 0, "label": "zero"}, {"value": 2, "label": "dois"}]},
    "questions": [{"id": "x", "order": 1, "text": "X?"}, {"id": "y", "order": 2, "text": "Y?"}],
}
MINI_PLAN: dict[str, Any] = {
    "template_id": "mini",
    "version": 1,
    "objective": "Objetivo do mini",
    "tasks": [{"key": "unica", "title": "Tarefa única"}],
}
PLAIN_PLAN: dict[str, Any] = {
    "template_id": "plain",
    "version": 1,
    "objective": "Objetivo do plain",
    "tasks": [{"key": "p1", "title": "P1"}, {"key": "p2", "title": "P2"}],
}


def _write_json(directory: Path, name: str, payload: Any) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _settings_for(templates_dir: Path, plans_dir: Path | None = None) -> Settings:
    overrides: dict[str, Any] = {"PROTOCOL_TEMPLATES_DIR": templates_dir}
    if plans_dir is not None:
        overrides["JOURNEY_PLANS_DIR"] = plans_dir
    return Settings(
        PHONE_HASH_SALT=TEST_SALT,
        APP_ENV=AppEnv.TESTING,
        LOG_FORMAT="json",
        _env_file=None,  # type: ignore[call-arg]
        **overrides,
    )


@pytest.fixture
def custom_client(tmp_path: Path, clock: FixedClock) -> Iterator[TestClient]:
    """App com dois templates fictícios (e seus planos) carregados de um diretório temporário."""
    _write_json(tmp_path / "templates", "mini.json", MINI_TEMPLATE)
    _write_json(tmp_path / "templates", "plain.json", PLAIN_TEMPLATE)
    _write_json(tmp_path / "plans", "mini.json", MINI_PLAN)
    _write_json(tmp_path / "plans", "plain.json", PLAIN_PLAN)
    app = create_app(
        settings=_settings_for(tmp_path / "templates", tmp_path / "plans"), clock=clock
    )
    with TestClient(app) as test_client:
        yield test_client


def _create_custom_patient(client: TestClient, phone: str = FAKE_PHONE) -> dict[str, Any]:
    response = client.post("/patients", json=patient_payload(phone=phone))
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def test_a_fictitious_template_runs_end_to_end_through_the_same_service(
    custom_client: TestClient,
) -> None:
    patient = _create_custom_patient(custom_client)

    # Caminho com skip: a regra própria do template encerra após a primeira pergunta.
    step = start_protocol(custom_client, patient["id"], template_id="mini")
    assert step["template_version"] == 3
    assert step["progress"] == {"answered": 0, "total": 3}
    assert step["next_question"] == {
        "id": "a",
        "order": 1,
        "text": "A?",
        "intro": "Pergunta-guia do mini",
        "options": [{"value": 0, "label": "não"}, {"value": 1, "label": "sim"}],
    }
    skipped = answer(custom_client, step["session_id"], "a", 0).json()
    assert skipped["status"] == "completed"
    assert skipped["result"] == {
        "score": 0,
        "max_score": 3,
        "ended_by_skip": True,
        "skip_rule_id": "stop_if_a_is_no",
        "answered_questions": ["a"],
    }
    journey = custom_client.get(f"/journeys/{skipped['journey_id']}").json()
    assert journey["objective"] == "Objetivo do mini"
    assert [task["title"] for task in journey["tasks"]] == ["Tarefa única"]
    completed = next(
        e for e in trail(custom_client, patient["id"]) if e["event_name"] == "protocol_completed"
    )
    assert completed["properties"]["template_id"] == "mini"
    assert completed["properties"]["template_version"] == 3
    assert completed["properties"]["max_score"] == 3
    assert completed["properties"]["answered_count"] == 1
    assert completed["properties"]["total_questions"] == 3

    # Caminho completo: a ordem servida é a de `order`, não a da lista do JSON.
    step = run_protocol_by_ids(custom_client, patient["id"], "mini", [("a", 1), ("b", 1)])
    assert step["next_question"]["id"] == "c"
    finished = answer(custom_client, step["session_id"], "c", 0).json()
    assert finished["status"] == "completed"
    assert finished["result"]["score"] == 2 and finished["result"]["ended_by_skip"] is False
    assert finished["result"]["answered_questions"] == ["a", "b", "c"]


def run_protocol_by_ids(
    client: TestClient, patient_id: str, template_id: str, answers: list[tuple[str, int]]
) -> dict[str, Any]:
    """Inicia `template_id` e responde os pares (question_id, value); devolve o último passo."""
    step = start_protocol(client, patient_id, template_id=template_id)
    for question_id, value in answers:
        response = answer(client, step["session_id"], question_id, value)
        assert response.status_code == 200, response.text
        step = response.json()
    return step


def test_allowed_values_come_from_the_template_scale_not_from_a_range(
    custom_client: TestClient,
) -> None:
    patient = _create_custom_patient(custom_client)
    step = start_protocol(custom_client, patient["id"], template_id="plain")

    rejected = answer(custom_client, step["session_id"], "x", 1)
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "INVALID_ANSWER_VALUE"
    assert rejected.json()["error"]["details"]["allowed"] == [0, 2]

    assert answer(custom_client, step["session_id"], "x", 2).json()["next_question"]["id"] == "y"
    finished = answer(custom_client, step["session_id"], "y", 2).json()
    assert finished["status"] == "completed"
    assert finished["result"]["score"] == 4 and finished["result"]["max_score"] == 4
    assert finished["result"]["ended_by_skip"] is False


def test_in_progress_sessions_are_isolated_per_patient_and_per_template(
    custom_client: TestClient,
) -> None:
    patient = _create_custom_patient(custom_client)
    other = _create_custom_patient(custom_client, phone="+55 11 90000-0002")

    mini = start_protocol(custom_client, patient["id"], template_id="mini")
    plain = start_protocol(custom_client, patient["id"], template_id="plain")
    duplicate = custom_client.post(
        f"/patients/{patient['id']}/protocols", json={"template_id": "mini"}
    )
    other_mini = start_protocol(custom_client, other["id"], template_id="mini")

    assert mini["session_id"] != plain["session_id"] != other_mini["session_id"]
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "SESSION_IN_PROGRESS"
    assert duplicate.json()["error"]["details"] == {
        "session_id": mini["session_id"],
        "template_id": "mini",
    }
    assert_no_pii(duplicate.text)


# -----------------------------------------------------------------------------
# Loader: a aplicação não sobe com template inválido
# -----------------------------------------------------------------------------


def _base_template() -> dict[str, Any]:
    return {
        "template_id": "t",
        "version": 1,
        "name": "T",
        "intro": "i",
        "scale": {"options": [{"value": 0, "label": "a"}, {"value": 1, "label": "b"}]},
        "questions": [{"id": "q1", "order": 1, "text": "x"}, {"id": "q2", "order": 2, "text": "y"}],
        "skip_rules": [
            {
                "id": "gate",
                "after_question": "q2",
                "condition": {"op": "lt", "left": {"sum_of": ["q1", "q2"]}, "right": 1},
                "action": "end_block",
            }
        ],
    }


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        (
            lambda t: t["skip_rules"][0]["condition"]["left"].__setitem__("sum_of", ["q1", "q1"]),
            "únicos",
        ),
        (lambda t: t["questions"][0].__setitem__("id", "Q1"), "questions.0.id"),
        (lambda t: t["skip_rules"][0].__setitem__("action", "skip_to"), "skip_rules.0.action"),
        (
            lambda t: t["scale"].__setitem__("options", [{"value": 0, "label": "a"}]),
            "scale.options",
        ),
        (lambda t: t["scale"]["options"][1].__setitem__("value", 0), "escala"),
        (lambda t: t.__setitem__("version", 0), "version"),
        (lambda t: t.__setitem__("questions", []), "questions"),
        (lambda t: t["skip_rules"].append(dict(t["skip_rules"][0])), "ids de skip_rules"),
        (
            lambda t: t["skip_rules"][0]["condition"].__setitem__(
                "left", {"sum_of": ["q1"], "answer": "q2"}
            ),
            "condition.left",
        ),
        (lambda t: t.__setitem__("template_id", "PHQ-9"), "template_id"),
        (lambda t: t.pop("intro"), "intro"),
    ],
)
def test_app_does_not_boot_with_an_invalid_template(
    tmp_path: Path, mutation: Any, expected_fragment: str
) -> None:
    template = _base_template()
    mutation(template)
    _write_json(tmp_path / "templates", "t.json", template)

    with pytest.raises(ConfigurationError) as exc:
        create_app(settings=_settings_for(tmp_path / "templates"))

    assert "t.json" in str(exc.value)
    assert expected_fragment in str(exc.value)


def test_app_does_not_boot_when_the_template_document_is_not_an_object(tmp_path: Path) -> None:
    _write_json(tmp_path / "templates", "t.json", [_base_template()])

    with pytest.raises(ConfigurationError, match=r"t\.json"):
        create_app(settings=_settings_for(tmp_path / "templates"))


def test_a_valid_template_boots_regardless_of_its_file_name(tmp_path: Path) -> None:
    _write_json(tmp_path / "templates", "any-file-name.json", _base_template())
    _write_json(
        tmp_path / "plans",
        "any-plan.json",
        {"template_id": "t", "version": 1, "objective": "o", "tasks": [{"key": "k", "title": "k"}]},
    )

    app = create_app(settings=_settings_for(tmp_path / "templates", tmp_path / "plans"))

    assert app.state.container.templates.ids() == ["t"]
