"""
Hardening de jornadas e tarefas.

Cada teste prova uma borda: jornada só nasce de protocolo concluído (estrutural), contrato
de status/objetivo/tarefas, conclusão de tarefa com eventos exatos e relógio, tarefa
inexistente ou de outra jornada, repetição, consentimento pausado/revogado, listagem por
paciente, segunda jornada (a mais recente) e plano inválido/ausente derrubando o boot.
"""

import json
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import APP_DIR, Settings
from app.container import Container
from app.core.clock import FixedClock
from app.core.exceptions import ConfigurationError
from app.features.journeys.models import JourneyStatus, TaskStatus
from app.main import create_app
from tests.conftest import (
    FAKE_NAME,
    FAKE_PHONE,
    FROZEN_NOW,
    assert_no_pii,
    patient_payload,
    run_protocol,
)

pytestmark = pytest.mark.integration

UNKNOWN_ID = "00000000-0000-0000-0000-000000000000"
STATUS_LITERALS = ["em_andamento", "concluida"]
JOURNEY_KEYS = {
    "id",
    "patient_id",
    "source_session_id",
    "template_id",
    "plan_version",
    "status",
    "objective",
    "tasks",
    "created_at",
    "completed_at",
}
TASK_KEYS = {"id", "key", "title", "status", "completed_at"}
JOURNEY_ROUTES = {
    ("/journeys/{journey_id}", "get"),
    ("/patients/{patient_id}/journeys", "get"),
    ("/journeys/{journey_id}/tasks/{task_id}/complete", "post"),
}


def _trail(client: TestClient, patient_id: str) -> list[dict[str, Any]]:
    response = client.get("/events", params={"patient_id": patient_id})
    assert response.status_code == 200, response.text
    data: list[dict[str, Any]] = response.json()["data"]
    return data


def _event_names(client: TestClient, patient_id: str) -> list[str]:
    return [event["event_name"] for event in _trail(client, patient_id)]


def _get_journey(client: TestClient, journey_id: str) -> dict[str, Any]:
    response = client.get(f"/journeys/{journey_id}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _list_journeys(client: TestClient, patient_id: str) -> dict[str, Any]:
    response = client.get(f"/patients/{patient_id}/journeys")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _task_ids(client: TestClient, journey_id: str) -> list[str]:
    return [task["id"] for task in _get_journey(client, journey_id)["tasks"]]


def _complete(client: TestClient, journey_id: str, task_id: str) -> Any:
    return client.post(f"/journeys/{journey_id}/tasks/{task_id}/complete")


def _complete_all(client: TestClient, journey_id: str) -> dict[str, Any]:
    """Conclui todas as tarefas em ordem e devolve a última resposta."""
    body: dict[str, Any] = {}
    for task_id in _task_ids(client, journey_id):
        response = _complete(client, journey_id, task_id)
        assert response.status_code == 200, response.text
        body = response.json()
    return body


def _write_json(directory: Path, name: str, payload: Any) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    (directory / name).write_text(content, encoding="utf-8")


def _boot_with_plans(settings: Settings, clock: FixedClock, plans_dir: Path) -> FastAPI:
    return create_app(
        settings=settings.model_copy(update={"JOURNEY_PLANS_DIR": plans_dir}), clock=clock
    )


def _valid_plan() -> dict[str, Any]:
    return {
        "template_id": "phq9",
        "version": 1,
        "objective": "Objetivo de teste",
        "tasks": [{"key": "primeira", "title": "Primeira"}, {"key": "segunda", "title": "Segunda"}],
    }


@pytest.fixture
def single_task_client(
    settings: Settings, clock: FixedClock, tmp_path: Path
) -> Iterator[TestClient]:
    """App com o template padrão e um plano do PHQ-9 reduzido a uma única tarefa."""
    plan = _valid_plan() | {"version": 7, "tasks": [{"key": "unica", "title": "Tarefa única"}]}
    _write_json(tmp_path / "plans", "phq9.json", plan)
    with TestClient(_boot_with_plans(settings, clock, tmp_path / "plans")) as test_client:
        yield test_client


# -----------------------------------------------------------------------------
# Criação: só via protocolo concluído, com o contrato do enunciado
# -----------------------------------------------------------------------------


def test_the_only_writing_route_of_journeys_is_task_completion(client: TestClient) -> None:
    """Estrutural: não existe rota que crie ou altere jornada além de concluir tarefa."""
    paths: dict[str, dict[str, Any]] = client.get("/openapi.json").json()["paths"]

    routes = {
        (path, method)
        for path, methods in paths.items()
        if "journeys" in path
        for method in methods
    }

    assert routes == JOURNEY_ROUTES


def test_journey_logic_has_no_template_specific_branching() -> None:
    """O plano é a única fonte do objetivo e das tarefas: nada de `if template_id == ...`."""
    journeys_dir = APP_DIR / "features" / "journeys"

    for module in ("service.py", "loader.py", "repository.py", "models.py"):
        source = (journeys_dir / module).read_text(encoding="utf-8")
        assert "phq" not in source.lower(), module
        assert re.search(r"""template_id\s*(==|!=|in)\s*[\(\["']""", source) is None, module


def test_status_literals_are_exactly_the_ones_from_the_specification(client: TestClient) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]

    assert [status.value for status in JourneyStatus] == STATUS_LITERALS
    assert [status.value for status in TaskStatus] == STATUS_LITERALS
    assert schemas["JourneyStatus"]["enum"] == STATUS_LITERALS
    assert schemas["TaskStatus"]["enum"] == STATUS_LITERALS


def test_journey_contract_mirrors_the_plan_with_id_title_and_status_per_task(
    client: TestClient, container: Container, completed_patient: dict[str, Any]
) -> None:
    plan = container.plans.get("phq9")
    response = client.get(f"/journeys/{completed_patient['journey_id']}")

    assert response.status_code == 200
    journey = response.json()
    assert set(journey) == JOURNEY_KEYS
    assert journey["status"] == "em_andamento"
    assert journey["objective"] == plan.objective
    assert journey["plan_version"] == plan.version
    assert journey["completed_at"] is None
    assert datetime.fromisoformat(journey["created_at"]) == FROZEN_NOW
    assert all(set(task) == TASK_KEYS for task in journey["tasks"])
    assert [task["title"] for task in journey["tasks"]] == [task.title for task in plan.tasks]
    assert [task["key"] for task in journey["tasks"]] == [task.key for task in plan.tasks]
    assert all(task["status"] == "em_andamento" for task in journey["tasks"])
    assert all(task["completed_at"] is None for task in journey["tasks"])
    task_ids = {UUID(task["id"]) for task in journey["tasks"]}
    assert len(task_ids) == len(plan.tasks)
    assert_no_pii(response.text)


def test_journey_and_session_are_linked_both_ways_and_journey_created_is_exact(
    client: TestClient, create_patient: Any
) -> None:
    patient = create_patient()
    step = run_protocol(client, patient["id"], [1, 1])
    journey = _get_journey(client, step["journey_id"])
    session = client.get(f"/protocol-sessions/{step['session_id']}").json()

    assert session["journey_id"] == journey["id"]
    assert journey["source_session_id"] == session["session_id"]
    assert journey["patient_id"] == patient["id"]
    created = next(e for e in _trail(client, patient["id"]) if e["event_name"] == "journey_created")
    assert created["patient_id_hash"] == patient["phone_hash"]
    assert created["properties"] == {
        "journey_id": journey["id"],
        "source_session_id": session["session_id"],
        "template_id": "phq9",
        "plan_version": journey["plan_version"],
        "task_count": len(journey["tasks"]),
    }


def test_same_plan_is_applied_whether_the_protocol_ends_by_skip_or_runs_to_the_end(
    client: TestClient, create_patient: Any
) -> None:
    """Um plano por template, independente do score: plano por faixa seria decisão clínica."""
    by_skip = create_patient()
    to_the_end = create_patient(phone="+55 11 90000-0002")
    skipped = run_protocol(client, by_skip["id"], [0, 0])
    finished = run_protocol(client, to_the_end["id"], [3] * 9)

    assert skipped["result"]["score"] == 0 and skipped["result"]["ended_by_skip"] is True
    assert finished["result"]["score"] == 27 and finished["result"]["ended_by_skip"] is False
    first = _get_journey(client, skipped["journey_id"])
    second = _get_journey(client, finished["journey_id"])
    assert first["objective"] == second["objective"]
    assert [t["title"] for t in first["tasks"]] == [t["title"] for t in second["tasks"]]
    assert first["status"] == second["status"] == "em_andamento"


def test_patient_without_accepted_terms_has_an_empty_list_and_no_way_to_get_a_journey(
    client: TestClient, create_patient: Any
) -> None:
    patient = create_patient(terms_accepted=False)

    blocked = client.post(f"/patients/{patient['id']}/protocols", json={"template_id": "phq9"})
    listing = _list_journeys(client, patient["id"])

    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "CONSENT_REQUIRED"
    assert listing == {"patient_id": patient["id"], "total": 0, "data": []}
    assert "journey_created" not in _event_names(client, patient["id"])


# -----------------------------------------------------------------------------
# Conclusão de tarefa: relógio, eventos exatos, última tarefa conclui a jornada
# -----------------------------------------------------------------------------


def test_each_completion_stamps_the_clock_and_the_last_one_completes_the_journey(
    client: TestClient, clock: FixedClock, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    journey_id = completed_patient["journey_id"]
    task_ids = _task_ids(client, journey_id)
    stamps: list[datetime] = []

    for index, task_id in enumerate(task_ids):
        clock.advance(hours=1)
        response = _complete(client, journey_id, task_id)
        assert response.status_code == 200, response.text
        body = response.json()
        stamps.append(clock.now())
        completed = body["tasks"][index]
        assert completed["id"] == task_id and completed["status"] == "concluida"
        assert datetime.fromisoformat(completed["completed_at"]) == stamps[-1]
        is_last = index == len(task_ids) - 1
        assert body["status"] == ("concluida" if is_last else "em_andamento")
        assert (body["completed_at"] is not None) is is_last
        assert _get_journey(client, journey_id) == body  # o POST devolve o estado persistido

    journey = _get_journey(client, journey_id)
    assert datetime.fromisoformat(journey["completed_at"]) == stamps[-1]
    assert [datetime.fromisoformat(t["completed_at"]) for t in journey["tasks"]] == stamps
    assert journey["created_at"] != journey["completed_at"]

    trail = _trail(client, patient_id)
    task_events = [e for e in trail if e["event_name"] == "task_completed"]
    assert [e["properties"] for e in task_events] == [
        {
            "journey_id": journey_id,
            "task_id": task["id"],
            "task_key": task["key"],
            "remaining_tasks": remaining,
        }
        for task, remaining in zip(journey["tasks"], [2, 1, 0], strict=True)
    ]
    assert [datetime.fromisoformat(e["occurred_at"]) for e in task_events] == stamps
    assert trail[-1]["event_name"] == "journey_completed"
    assert trail[-1]["properties"] == {"journey_id": journey_id}
    assert datetime.fromisoformat(trail[-1]["occurred_at"]) == stamps[-1]
    assert {e["patient_id_hash"] for e in trail} == {completed_patient["patient"]["phone_hash"]}


def test_journey_completed_is_emitted_once_and_only_with_the_last_task(
    client: TestClient, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    journey_id = completed_patient["journey_id"]
    first, second, last = _task_ids(client, journey_id)

    _complete(client, journey_id, first)
    _complete(client, journey_id, second)
    assert "journey_completed" not in _event_names(client, patient_id)
    assert _get_journey(client, journey_id)["status"] == "em_andamento"

    _complete(client, journey_id, last)
    names = _event_names(client, patient_id)
    assert names.count("journey_completed") == 1
    assert names[-2:] == ["task_completed", "journey_completed"]


def test_completed_journey_rejects_every_task_with_409_and_emits_nothing_more(
    client: TestClient, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    journey_id = completed_patient["journey_id"]
    _complete_all(client, journey_id)
    before = _trail(client, patient_id)
    journey_before = _get_journey(client, journey_id)

    for task_id in _task_ids(client, journey_id):
        response = _complete(client, journey_id, task_id)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "TASK_ALREADY_COMPLETED"
        assert response.json()["error"]["details"] == {"task_id": task_id}
        assert_no_pii(response.text)

    assert _trail(client, patient_id) == before
    assert _get_journey(client, journey_id) == journey_before
    assert before[-1]["event_name"] == "journey_completed"


def test_single_task_plan_completes_the_journey_on_the_first_completion(
    single_task_client: TestClient,
) -> None:
    created = single_task_client.post("/patients", json=patient_payload())
    assert created.status_code == 201, created.text
    patient = created.json()
    step = run_protocol(single_task_client, patient["id"], [1, 1])
    journey = _get_journey(single_task_client, step["journey_id"])
    assert journey["plan_version"] == 7
    assert [t["title"] for t in journey["tasks"]] == ["Tarefa única"]

    response = _complete(single_task_client, journey["id"], journey["tasks"][0]["id"])

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "concluida"
    assert response.json()["tasks"][0]["status"] == "concluida"
    trail = _trail(single_task_client, patient["id"])
    assert [e["event_name"] for e in trail[-3:]] == [
        "journey_created",
        "task_completed",
        "journey_completed",
    ]
    assert trail[-3]["properties"]["task_count"] == 1
    assert trail[-3]["properties"]["plan_version"] == 7
    assert trail[-2]["properties"]["remaining_tasks"] == 0


# -----------------------------------------------------------------------------
# Tarefa inexistente, de outra jornada, jornada inexistente e ids malformados
# -----------------------------------------------------------------------------


def test_task_of_another_patients_journey_is_404_and_leaves_both_journeys_untouched(
    client: TestClient, create_patient: Any
) -> None:
    first = create_patient()
    second = create_patient(phone="+55 11 90000-0002")
    first_journey = run_protocol(client, first["id"], [1, 1])["journey_id"]
    second_journey = run_protocol(client, second["id"], [0, 0])["journey_id"]
    foreign_task = _task_ids(client, second_journey)[0]
    before = (_get_journey(client, first_journey), _get_journey(client, second_journey))

    response = _complete(client, first_journey, foreign_task)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TASK_NOT_FOUND"
    assert response.json()["error"]["details"]["resource_id"] == foreign_task
    assert_no_pii(response.text)
    assert (_get_journey(client, first_journey), _get_journey(client, second_journey)) == before
    assert "task_completed" not in _event_names(client, first["id"])
    assert "task_completed" not in _event_names(client, second["id"])


def test_task_id_is_scoped_to_its_journey_even_within_the_same_patient(
    client: TestClient, create_patient: Any
) -> None:
    patient = create_patient()
    first_journey = run_protocol(client, patient["id"], [1, 1])["journey_id"]
    second_journey = run_protocol(client, patient["id"], [0, 0])["journey_id"]
    task_of_second = _task_ids(client, second_journey)[0]

    wrong = _complete(client, first_journey, task_of_second)
    right = _complete(client, second_journey, task_of_second)

    assert wrong.status_code == 404
    assert wrong.json()["error"]["code"] == "TASK_NOT_FOUND"
    assert right.status_code == 200
    assert _get_journey(client, first_journey)["tasks"][0]["status"] == "em_andamento"
    assert _get_journey(client, second_journey)["tasks"][0]["status"] == "concluida"
    task_events = [e for e in _trail(client, patient["id"]) if e["event_name"] == "task_completed"]
    assert [e["properties"]["journey_id"] for e in task_events] == [second_journey]


def test_unknown_journey_on_completion_is_404_journey_not_found(
    client: TestClient, completed_patient: dict[str, Any]
) -> None:
    journey_id = completed_patient["journey_id"]
    task_id = _task_ids(client, journey_id)[0]

    response = _complete(client, UNKNOWN_ID, task_id)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOURNEY_NOT_FOUND"
    assert response.json()["error"]["details"] == {"resource": "Jornada", "resource_id": UNKNOWN_ID}
    assert _get_journey(client, journey_id)["tasks"][0]["status"] == "em_andamento"
    assert "task_completed" not in _event_names(client, completed_patient["patient"]["id"])


@pytest.mark.parametrize("raw", [FAKE_PHONE, FAKE_NAME, "not-a-uuid"])
def test_malformed_journey_or_task_id_is_422_without_echoing_the_input(
    client: TestClient, completed_patient: dict[str, Any], raw: str
) -> None:
    patient_id = completed_patient["patient"]["id"]
    journey_id = completed_patient["journey_id"]
    names_before = _event_names(client, patient_id)

    only_task = _complete(client, journey_id, raw)
    both = _complete(client, raw, raw)
    get_journey = client.get(f"/journeys/{raw}")
    listing = client.get(f"/patients/{raw}/journeys")

    for response, fields in (
        (only_task, {"task_id"}),
        (both, {"journey_id", "task_id"}),
        (get_journey, {"journey_id"}),
        (listing, {"patient_id"}),
    ):
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
        assert set(response.json()["error"]["details"]["field_errors"]) == fields
        assert raw not in response.text
        assert_no_pii(response.text)
    assert _event_names(client, patient_id) == names_before


# -----------------------------------------------------------------------------
# Consentimento pausado/revogado
# -----------------------------------------------------------------------------


def test_paused_consent_blocks_completion_without_side_effects_and_resume_continues(
    client: TestClient, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    journey_id = completed_patient["journey_id"]
    task_id = _task_ids(client, journey_id)[0]
    before = _get_journey(client, journey_id)
    assert client.post(f"/patients/{patient_id}/consent/pause").status_code == 200

    blocked = _complete(client, journey_id, task_id)

    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "CONSENT_REQUIRED"
    assert blocked.json()["error"]["details"] == {"consent_status": "paused"}
    assert _get_journey(client, journey_id) == before
    names = _event_names(client, patient_id)
    assert names[-1] == "consent_paused" and "task_completed" not in names

    assert client.post(f"/patients/{patient_id}/consent/resume").status_code == 200
    resumed = _complete(client, journey_id, task_id)
    assert resumed.status_code == 200
    assert resumed.json()["tasks"][0]["status"] == "concluida"
    assert _event_names(client, patient_id)[-2:] == ["consent_resumed", "task_completed"]


def test_revoked_consent_blocks_completion_but_the_journey_stays_readable_and_pseudonymized(
    client: TestClient, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    journey_id = completed_patient["journey_id"]
    done, pending, _ = _task_ids(client, journey_id)
    assert _complete(client, journey_id, done).status_code == 200
    before = _get_journey(client, journey_id)
    assert client.post(f"/patients/{patient_id}/consent/revoke").status_code == 200

    blocked = _complete(client, journey_id, pending)
    fetched = client.get(f"/journeys/{journey_id}")
    listing = client.get(f"/patients/{patient_id}/journeys")

    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "CONSENT_REQUIRED"
    assert blocked.json()["error"]["details"] == {"consent_status": "revoked"}
    assert fetched.status_code == 200 and fetched.json() == before
    assert listing.status_code == 200 and listing.json()["data"] == [before]
    assert before["tasks"][0]["status"] == "concluida"  # o que já foi feito permanece
    assert before["status"] == "em_andamento"  # revogar não conclui nem apaga a jornada
    for text in (blocked.text, fetched.text, listing.text):
        assert_no_pii(text)
    names = _event_names(client, patient_id)
    assert names[-1] == "consent_revoked" and names.count("task_completed") == 1


# -----------------------------------------------------------------------------
# Listagem por paciente
# -----------------------------------------------------------------------------


def test_listing_is_isolated_per_patient_and_kept_in_creation_order(
    client: TestClient, create_patient: Any
) -> None:
    first = create_patient()
    second = create_patient(phone="+55 11 90000-0002")
    first_a = run_protocol(client, first["id"], [1, 1])["journey_id"]
    second_only = run_protocol(client, second["id"], [0, 0])["journey_id"]
    first_b = run_protocol(client, first["id"], [2, 2, 0, 0, 0, 0, 0, 0, 0])["journey_id"]

    first_listing = _list_journeys(client, first["id"])
    second_listing = _list_journeys(client, second["id"])

    assert first_listing["patient_id"] == first["id"] and first_listing["total"] == 2
    assert [j["id"] for j in first_listing["data"]] == [first_a, first_b]
    assert {j["patient_id"] for j in first_listing["data"]} == {first["id"]}
    assert second_listing["total"] == 1
    assert [j["id"] for j in second_listing["data"]] == [second_only]
    assert second_listing["data"][0]["patient_id"] == second["id"]
    assert first_listing["data"] == [_get_journey(client, first_a), _get_journey(client, first_b)]
    assert_no_pii(json.dumps(first_listing) + json.dumps(second_listing))


def test_listing_reflects_task_and_journey_status_changes(
    client: TestClient, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    journey_id = completed_patient["journey_id"]
    assert _list_journeys(client, patient_id)["data"][0]["status"] == "em_andamento"

    last = _complete_all(client, journey_id)

    listing = _list_journeys(client, patient_id)
    assert listing["total"] == 1
    assert listing["data"] == [last]
    assert listing["data"][0]["status"] == "concluida"
    assert [t["status"] for t in listing["data"][0]["tasks"]] == ["concluida"] * 3


# -----------------------------------------------------------------------------
# Segunda jornada após novo protocolo: a mais recente é a que vale
# -----------------------------------------------------------------------------


def test_new_protocol_after_a_finished_journey_creates_a_second_one_that_becomes_the_latest(
    client: TestClient, container: Container, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    first_journey = completed_patient["journey_id"]
    _complete_all(client, first_journey)
    assert container.journeys.latest_for_patient(UUID(patient_id)).id == UUID(first_journey)

    step = run_protocol(client, patient_id, [3] * 9)

    second_journey = step["journey_id"]
    assert second_journey not in (None, first_journey)
    latest = container.journeys.latest_for_patient(UUID(patient_id))
    assert latest is not None and str(latest.id) == second_journey
    listing = _list_journeys(client, patient_id)
    assert [(j["id"], j["status"]) for j in listing["data"]] == [
        (first_journey, "concluida"),
        (second_journey, "em_andamento"),
    ]
    assert listing["data"][1]["source_session_id"] == step["session_id"]
    assert all(t["status"] == "em_andamento" for t in listing["data"][1]["tasks"])
    created = [e for e in _trail(client, patient_id) if e["event_name"] == "journey_created"]
    assert [e["properties"]["journey_id"] for e in created] == [first_journey, second_journey]

    # O motor de follow-up enxerga a jornada mais recente, não a concluída.
    decision = client.post("/followups/evaluate", json={"patient_id": patient_id}).json()
    observed = {item["rule_id"]: item["observed"] for item in decision["trace"]}
    assert observed["journey_active"] == "em_andamento"
    assert observed["active_task"] == 3


def test_a_second_journey_coexists_with_an_unfinished_first_one(
    client: TestClient, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    first_journey = completed_patient["journey_id"]
    first_tasks = _task_ids(client, first_journey)
    assert _complete(client, first_journey, first_tasks[0]).status_code == 200

    second_journey = run_protocol(client, patient_id, [0, 0])["journey_id"]
    still_first = _complete(client, first_journey, first_tasks[1])

    assert second_journey != first_journey
    assert still_first.status_code == 200
    first = _get_journey(client, first_journey)
    second = _get_journey(client, second_journey)
    assert [t["status"] for t in first["tasks"]] == ["concluida", "concluida", "em_andamento"]
    assert all(t["status"] == "em_andamento" for t in second["tasks"])
    assert first["status"] == second["status"] == "em_andamento"
    task_events = [e for e in _trail(client, patient_id) if e["event_name"] == "task_completed"]
    assert [e["properties"]["journey_id"] for e in task_events] == [first_journey, first_journey]
    assert [e["properties"]["remaining_tasks"] for e in task_events] == [2, 1]


# -----------------------------------------------------------------------------
# PII: nenhuma superfície de jornada carrega telefone, nome ou nascimento
# -----------------------------------------------------------------------------


def test_journey_responses_errors_and_events_never_carry_pii(
    client: TestClient, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    journey_id = completed_patient["journey_id"]
    task_id = _task_ids(client, journey_id)[0]

    surfaces = [
        client.get(f"/journeys/{journey_id}"),
        client.get(f"/patients/{patient_id}/journeys"),
        _complete(client, journey_id, task_id),
        _complete(client, journey_id, task_id),  # 409
        _complete(client, journey_id, UNKNOWN_ID),  # 404
        _complete(client, UNKNOWN_ID, task_id),  # 404
        client.get("/events", params={"patient_id": patient_id}),
    ]

    assert [r.status_code for r in surfaces] == [200, 200, 200, 409, 404, 404, 200]
    for response in surfaces:
        assert_no_pii(response.text)
    journey_events = [
        e
        for e in _trail(client, patient_id)
        if e["event_name"] in {"journey_created", "task_completed"}
    ]
    assert len(journey_events) == 2
    for event in journey_events:
        # Só ids, contagens e chaves do plano — nada de texto livre nas properties.
        assert all(isinstance(v, int | str) for v in event["properties"].values())
        assert all(not isinstance(v, bool) for v in event["properties"].values())


# -----------------------------------------------------------------------------
# Boot: plano JSON inválido ou ausente derruba a aplicação antes de servir
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        pytest.param(
            lambda p: p["tasks"].append({"key": "primeira", "title": "Repetida"}),
            "únicas",
            id="duplicate_task_key",
        ),
        pytest.param(lambda p: p.__setitem__("tasks", []), "tasks", id="no_tasks"),
        pytest.param(
            lambda p: p.__setitem__("tasks", {"key": "x", "title": "y"}), "tasks", id="tasks_object"
        ),
        pytest.param(lambda p: p["tasks"][0].__setitem__("key", "Sono"), "tasks.0.key", id="key"),
        pytest.param(lambda p: p["tasks"][0].__setitem__("title", ""), "tasks.0.title", id="title"),
        pytest.param(
            lambda p: p["tasks"][0].__setitem__("title", " \t "), "tasks.0.title", id="blank_title"
        ),
        pytest.param(lambda p: p["tasks"][0].pop("title"), "tasks.0.title", id="missing_title"),
        pytest.param(lambda p: p.pop("objective"), "objective", id="missing_objective"),
        pytest.param(lambda p: p.__setitem__("objective", ""), "objective", id="empty_objective"),
        pytest.param(
            lambda p: p.__setitem__("objective", "   "), "objective", id="blank_objective"
        ),
        pytest.param(lambda p: p.__setitem__("version", 0), "version", id="version_zero"),
        pytest.param(lambda p: p.__setitem__("template_id", "PHQ-9"), "template_id", id="tid"),
        pytest.param(lambda p: p.__setitem__("surprise", 1), "surprise", id="unknown_field"),
    ],
)
def test_app_does_not_boot_with_an_invalid_journey_plan(
    settings: Settings, clock: FixedClock, tmp_path: Path, mutation: Any, expected_fragment: str
) -> None:
    plan = _valid_plan()
    mutation(plan)
    _write_json(tmp_path / "plans", "phq9.json", plan)

    with pytest.raises(ConfigurationError) as exc:
        _boot_with_plans(settings, clock, tmp_path / "plans")

    assert exc.value.error_code == "CONFIGURATION_ERROR"
    assert "phq9.json" in str(exc.value)
    assert expected_fragment in str(exc.value)


def test_app_does_not_boot_with_malformed_plan_json_or_a_non_object_document(
    settings: Settings, clock: FixedClock, tmp_path: Path
) -> None:
    _write_json(tmp_path / "broken", "phq9.json", '{"template_id": "phq9", ')
    with pytest.raises(ConfigurationError, match=r"Sintaxe inválida em phq9\.json"):
        _boot_with_plans(settings, clock, tmp_path / "broken")

    _write_json(tmp_path / "list", "phq9.json", [_valid_plan()])
    with pytest.raises(ConfigurationError, match=r"phq9\.json inválido"):
        _boot_with_plans(settings, clock, tmp_path / "list")


def test_app_does_not_boot_when_the_plans_dir_is_missing_or_empty(
    settings: Settings, clock: FixedClock, tmp_path: Path
) -> None:
    with pytest.raises(ConfigurationError, match="não encontrado"):
        _boot_with_plans(settings, clock, tmp_path / "missing")

    (tmp_path / "empty").mkdir()
    (tmp_path / "empty" / "notes.txt").write_text("não é json", encoding="utf-8")
    with pytest.raises(ConfigurationError, match=r"Nenhum arquivo \*\.json"):
        _boot_with_plans(settings, clock, tmp_path / "empty")


def test_app_does_not_boot_with_two_plans_for_the_same_template(
    settings: Settings, clock: FixedClock, tmp_path: Path
) -> None:
    _write_json(tmp_path / "plans", "a.json", _valid_plan())
    _write_json(tmp_path / "plans", "b.json", _valid_plan() | {"version": 2})

    with pytest.raises(ConfigurationError, match=r"duplicado.*phq9"):
        _boot_with_plans(settings, clock, tmp_path / "plans")


def test_app_does_not_boot_when_the_plan_names_a_template_that_does_not_exist(
    settings: Settings, clock: FixedClock, tmp_path: Path
) -> None:
    """Um `template_id` digitado errado deixa o PHQ-9 sem plano: falha no boot, não depois."""
    _write_json(tmp_path / "plans", "phq9.json", _valid_plan() | {"template_id": "phq_9"})

    with pytest.raises(ConfigurationError, match="templates sem plano de jornada: phq9"):
        _boot_with_plans(settings, clock, tmp_path / "plans")
