"""Bordas do motor de elegibilidade: prioridade das regras, cooldown na borda, eventos e loader."""

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import AppEnv, Settings
from app.container import Container
from app.core.clock import FixedClock
from app.core.exceptions import ConfigurationError
from app.features.followups.models import SkipReason
from app.main import create_app
from tests.conftest import (
    FAKE_PHONE,
    TEST_SALT,
    CreatePatient,
    assert_no_pii,
    patient_payload,
    run_protocol,
    trail,
)

pytestmark = pytest.mark.integration

RULE_IDS = ["consent", "protocol_completed", "journey_active", "active_task", "cooldown"]
CHECKS = [
    "consent_status",
    "has_completed_protocol",
    "latest_journey_status",
    "active_tasks_count",
    "hours_since_last_event",
]
TRACE_KEYS = {"rule_id", "check", "params", "observed", "expected", "passed", "details"}
MISSING_ID = "00000000-0000-0000-0000-000000000000"


def _evaluate(client: TestClient, patient_id: str) -> Any:
    return client.post("/followups/evaluate", json={"patient_id": patient_id})


def _decide(client: TestClient, patient_id: str) -> dict[str, Any]:
    response = _evaluate(client, patient_id)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _event_by_id(client: TestClient, patient_id: str, event_id: str) -> dict[str, Any]:
    return next(event for event in trail(client, patient_id) if event["event_id"] == event_id)


def _instant(value: str) -> datetime:
    """Compara instantes, não strings: `+00:00` e `Z` são o mesmo momento."""
    return datetime.fromisoformat(value)


def _complete_all_tasks(client: TestClient, journey_id: str) -> None:
    for task in client.get(f"/journeys/{journey_id}").json()["tasks"]:
        response = client.post(f"/journeys/{journey_id}/tasks/{task['id']}/complete")
        assert response.status_code == 200, response.text


# -----------------------------------------------------------------------------
# As cinco regras do enunciado, a prioridade e o trace completo
# -----------------------------------------------------------------------------


def test_trace_lists_the_five_rules_in_priority_order_even_when_almost_all_fail(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient(terms_accepted=False)

    body = _decide(client, patient["id"])

    assert body["eligible"] is False
    assert body["reason"] == "missing_consent"  # primeira que falha, na ordem do YAML
    assert [item["rule_id"] for item in body["trace"]] == RULE_IDS
    assert [item["check"] for item in body["trace"]] == CHECKS
    assert [item["passed"] for item in body["trace"]] == [False, False, False, False, True]
    assert all(set(item) == TRACE_KEYS for item in body["trace"])
    consent, protocol, journey, tasks, cooldown = body["trace"]
    assert consent["observed"] == "pending" and consent["expected"] == {"equals": "accepted"}
    assert protocol["observed"] is False and protocol["expected"] == {"equals": True}
    assert journey["observed"] is None and journey["details"] == {"absent": True}
    assert tasks["observed"] == 0 and tasks["details"] == {"remaining": 1.0}
    assert cooldown["params"] == {"event_name": "followup_eligible"}
    assert cooldown["observed"] is None and cooldown["passed"] is True  # if_absent: pass
    assert cooldown["details"] == {
        "event_name": "followup_eligible",
        "last_event_at": None,
        "unit": "hours",
        "absent": True,
    }


def test_accepted_patient_without_protocol_fails_on_the_second_rule_first(
    client: TestClient, create_patient: CreatePatient
) -> None:
    patient = create_patient()

    body = _decide(client, patient["id"])

    assert body["reason"] == "protocol_not_completed"
    assert [item["passed"] for item in body["trace"]] == [True, False, False, False, True]


@pytest.mark.parametrize(
    ("action", "observed", "expected_reason"),
    [("pause", "paused", "consent_paused"), ("revoke", "revoked", "consent_revoked")],
)
def test_paused_or_revoked_consent_is_the_only_failing_rule_and_maps_its_own_reason(
    client: TestClient,
    completed_patient: dict[str, Any],
    action: str,
    observed: str,
    expected_reason: str,
) -> None:
    patient_id = completed_patient["patient"]["id"]
    assert client.post(f"/patients/{patient_id}/consent/{action}").status_code == 200

    body = _decide(client, patient_id)

    assert body["eligible"] is False
    assert body["reason"] == expected_reason  # reason_by_value: o valor observado escolhe o reason
    assert [item["passed"] for item in body["trace"]] == [False, True, True, True, True]
    assert body["trace"][0]["observed"] == observed
    skipped = trail(client, patient_id)[-1]
    assert skipped["event_name"] == "followup_skipped"
    assert skipped["properties"]["reason"] == expected_reason
    assert_no_pii(client.get("/events", params={"patient_id": patient_id}).text)


def test_every_typed_reason_is_reachable_from_the_default_ruleset(container: Container) -> None:
    declared = {rule.reason for rule in container.rules.rules}
    declared |= {
        reason for rule in container.rules.rules for reason in rule.reason_by_value.values()
    }

    assert declared == set(SkipReason)  # nenhum reason morto, nenhum reason fora do enum


def test_completed_journey_is_reported_as_no_active_journey_with_the_observed_status(
    client: TestClient, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    _complete_all_tasks(client, completed_patient["journey_id"])

    body = _decide(client, patient_id)

    assert body["reason"] == "no_active_journey"
    journey, tasks = body["trace"][2], body["trace"][3]
    assert journey["observed"] == "concluida" and journey["passed"] is False
    assert tasks["observed"] == 0 and tasks["passed"] is False
    assert tasks["details"] == {"remaining": 1.0}
    assert trail(client, patient_id)[-1]["properties"]["reason"] == "no_active_journey"


# -----------------------------------------------------------------------------
# Cooldown de 72h: borda exata, referência ao último disparo, por paciente
# -----------------------------------------------------------------------------


def test_cooldown_refuses_one_second_before_72h_and_releases_at_exactly_72h(
    client: TestClient, clock: FixedClock, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    first = _decide(client, patient_id)
    assert first["eligible"] is True
    fired_at = _event_by_id(client, patient_id, first["event_id"])["occurred_at"]

    clock.advance(hours=71, minutes=59, seconds=59)
    still = _decide(client, patient_id)

    assert still["eligible"] is False and still["reason"] == "cooldown"
    cooldown = still["trace"][-1]
    assert cooldown["observed"] == 71.99  # nunca arredonda para cima: 71h59m59s não é 72h
    assert cooldown["details"]["remaining"] == 0.01
    assert _instant(cooldown["details"]["last_event_at"]) == _instant(fired_at)

    clock.advance(seconds=1)
    released = _decide(client, patient_id)

    assert released["eligible"] is True
    assert released["trace"][-1]["observed"] == 72.0
    assert "remaining" not in released["trace"][-1]["details"]


def test_cooldown_counts_from_the_last_followup_eligible_not_the_first(
    client: TestClient, clock: FixedClock, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    assert _decide(client, patient_id)["eligible"] is True
    clock.advance(hours=72)
    second = _decide(client, patient_id)
    assert second["eligible"] is True

    clock.advance(hours=1)  # 73h desde o primeiro disparo, 1h desde o segundo
    third = _decide(client, patient_id)

    assert third["reason"] == "cooldown"
    cooldown = third["trace"][-1]
    assert cooldown["observed"] == 1.0
    assert cooldown["details"]["remaining"] == 71.0
    second_fired_at = _event_by_id(client, patient_id, second["event_id"])["occurred_at"]
    assert _instant(cooldown["details"]["last_event_at"]) == _instant(second_fired_at)


def test_followup_skipped_does_not_restart_the_cooldown(
    client: TestClient, clock: FixedClock, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    first = _decide(client, patient_id)
    assert first["eligible"] is True
    clock.advance(hours=1)
    assert _decide(client, patient_id)["reason"] == "cooldown"

    clock.advance(hours=71)  # 72h desde o eligible, 71h desde o skipped
    again = _decide(client, patient_id)

    assert again["eligible"] is True
    cooldown = again["trace"][-1]
    assert cooldown["observed"] == 72.0
    first_fired_at = _event_by_id(client, patient_id, first["event_id"])["occurred_at"]
    assert _instant(cooldown["details"]["last_event_at"]) == _instant(first_fired_at)
    assert [e["event_name"] for e in trail(client, patient_id)][-3:] == [
        "followup_eligible",
        "followup_skipped",
        "followup_eligible",
    ]


def test_cooldown_applies_per_patient(client: TestClient, create_patient: CreatePatient) -> None:
    first = create_patient()
    second = create_patient(phone="+55 11 90000-0002")
    for patient in (first, second):
        assert run_protocol(client, patient["id"], [1, 1])["status"] == "completed"

    assert _decide(client, first["id"])["eligible"] is True
    other = _decide(client, second["id"])
    assert other["eligible"] is True
    assert other["trace"][-1]["details"]["absent"] is True  # o disparo do outro não conta
    assert _decide(client, first["id"])["reason"] == "cooldown"


def test_cooldown_survives_journey_completion_and_a_new_protocol(
    client: TestClient, clock: FixedClock, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    assert _decide(client, patient_id)["eligible"] is True
    _complete_all_tasks(client, completed_patient["journey_id"])
    assert _decide(client, patient_id)["reason"] == "no_active_journey"

    clock.advance(hours=1)
    run_protocol(client, patient_id, [2, 2, 0, 0, 0, 0, 0, 0, 0])
    body = _decide(client, patient_id)

    # A jornada nova reabilita as regras de jornada, mas o cooldown é do paciente.
    assert body["reason"] == "cooldown"
    assert [item["passed"] for item in body["trace"]] == [True, True, True, True, False]
    assert body["trace"][-1]["observed"] == 1.0

    clock.advance(hours=71)
    assert _decide(client, patient_id)["eligible"] is True


# -----------------------------------------------------------------------------
# Eventos: um por avaliação, properties espelhando a resposta, sem PII
# -----------------------------------------------------------------------------


def test_each_evaluation_appends_exactly_one_event_mirroring_the_decision(
    client: TestClient, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    before = len(trail(client, patient_id))

    eligible = _decide(client, patient_id)
    skipped = _decide(client, patient_id)

    events = trail(client, patient_id)
    assert len(events) == before + 2
    eligible_event, skipped_event = events[-2], events[-1]
    assert eligible_event["event_name"] == "followup_eligible"
    assert eligible_event["event_id"] == eligible["event_id"]
    assert eligible_event["properties"] == {
        "template_key": "checkin_adesao",
        "rules_version": 1,
        "trace": eligible["trace"],
    }
    assert skipped_event["event_name"] == "followup_skipped"
    assert skipped_event["event_id"] == skipped["event_id"]
    assert skipped_event["properties"] == {
        "reason": "cooldown",
        "template_key": "checkin_adesao",
        "rules_version": 1,
        "trace": skipped["trace"],
    }
    for event, body in ((eligible_event, eligible), (skipped_event, skipped)):
        assert _instant(event["occurred_at"]) == _instant(body["evaluated_at"])
        assert event["patient_id_hash"] == completed_patient["patient"]["phone_hash"]
    assert_no_pii(client.get("/events", params={"patient_id": patient_id}).text)


def test_evaluation_changes_nothing_besides_the_trail(
    client: TestClient, completed_patient: dict[str, Any]
) -> None:
    patient_id = completed_patient["patient"]["id"]
    journey_id = completed_patient["journey_id"]
    patient_before = client.get(f"/patients/{patient_id}").json()
    journey_before = client.get(f"/journeys/{journey_id}").json()

    _decide(client, patient_id)
    _decide(client, patient_id)

    assert client.get(f"/patients/{patient_id}").json() == patient_before
    assert client.get(f"/journeys/{journey_id}").json() == journey_before


# -----------------------------------------------------------------------------
# Entrada: paciente inexistente e ids malformados, sem eco e sem evento
# -----------------------------------------------------------------------------


def test_unknown_patient_returns_typed_404_and_emits_no_event(
    client: TestClient, container: Container
) -> None:
    before = len(container.events)

    response = _evaluate(client, MISSING_ID)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PATIENT_NOT_FOUND"
    assert len(container.events) == before


@pytest.mark.parametrize(
    "payload", [{"patient_id": FAKE_PHONE}, {"patient_id": "not-a-uuid"}, {"patient_id": ""}, {}]
)
def test_malformed_patient_id_is_rejected_without_echoing_the_input(
    client: TestClient, container: Container, payload: dict[str, Any]
) -> None:
    before = len(container.events)

    response = client.post("/followups/evaluate", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "patient_id" in response.json()["error"]["details"]["field_errors"]
    if payload.get("patient_id"):
        assert payload["patient_id"] not in response.text
    assert_no_pii(response.text)
    assert len(container.events) == before


# -----------------------------------------------------------------------------
# Regras declarativas: um YAML diferente muda a decisão sem código novo
# -----------------------------------------------------------------------------

CUSTOM_RULES = """
version: 7
template_key: checkin_sono
rules:
  - id: consent
    check: consent_status
    expect: { in: [accepted] }
    reason: missing_consent
  - id: recent_task
    description: Só quem concluiu uma tarefa nas últimas 24 horas
    check: hours_since_last_event
    params: { event_name: task_completed }
    expect: { lte: 24 }
    reason: no_active_task
"""


def _write_rules(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _settings_for(rules_path: Path) -> Settings:
    return Settings(
        PHONE_HASH_SALT=TEST_SALT,
        APP_ENV=AppEnv.TESTING,
        LOG_FORMAT="json",
        FOLLOWUP_RULES_PATH=rules_path,
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def custom_client(tmp_path: Path, clock: FixedClock) -> Iterator[TestClient]:
    """App com um conjunto de regras fictício carregado de um arquivo temporário."""
    app = create_app(settings=_settings_for(_write_rules(tmp_path, CUSTOM_RULES)), clock=clock)
    with TestClient(app) as test_client:
        yield test_client


def test_a_custom_ruleset_drives_the_decision_the_template_key_and_the_pinned_version(
    custom_client: TestClient, clock: FixedClock
) -> None:
    patient = custom_client.post("/patients", json=patient_payload()).json()
    journey_id = run_protocol(custom_client, patient["id"], [1, 1])["journey_id"]

    absent = _decide(custom_client, patient["id"])
    assert absent["eligible"] is False and absent["reason"] == "no_active_task"
    assert absent["template_key"] == "checkin_sono" and absent["rules_version"] == 7
    assert [item["rule_id"] for item in absent["trace"]] == ["consent", "recent_task"]
    assert absent["trace"][0]["expected"] == {"in": ["accepted"]}
    recent = absent["trace"][1]
    assert recent["observed"] is None and recent["details"]["absent"] is True  # if_absent: fail
    assert recent["params"] == {"event_name": "task_completed"}
    event = _event_by_id(custom_client, patient["id"], absent["event_id"])
    assert event["event_name"] == "followup_skipped"
    assert event["properties"]["template_key"] == "checkin_sono"
    assert event["properties"]["rules_version"] == 7

    task = custom_client.get(f"/journeys/{journey_id}").json()["tasks"][0]
    assert (
        custom_client.post(f"/journeys/{journey_id}/tasks/{task['id']}/complete").status_code == 200
    )
    fresh = _decide(custom_client, patient["id"])
    assert fresh["eligible"] is True
    assert fresh["trace"][1]["observed"] == 0.0 and fresh["trace"][1]["expected"] == {"lte": 24}

    clock.advance(hours=25)
    stale = _decide(custom_client, patient["id"])
    assert stale["reason"] == "no_active_task"
    assert stale["trace"][1]["observed"] == 25.0
    assert "remaining" not in stale["trace"][1]["details"]


# -----------------------------------------------------------------------------
# Loader: a aplicação não sobe com regras inválidas
# -----------------------------------------------------------------------------

BASE_RULES = """
version: 1
template_key: checkin_adesao
rules:
  - id: consent
    check: consent_status
    expect: { equals: accepted }
    reason: missing_consent
"""


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ("version: [\n", "Sintaxe inválida"),
        ("rules:\n\t- id: x\n", "Sintaxe inválida"),
        ("- not: a-mapping\n", "inválido"),
        ("just text\n", "inválido"),
        (BASE_RULES.replace("version: 1", "version: 0"), "version"),
        (BASE_RULES.replace("checkin_adesao", "Checkin Adesão"), "template_key"),
        (BASE_RULES.replace("template_key: checkin_adesao\n", ""), "template_key"),
        ("version: 1\ntemplate_key: checkin_adesao\nrules: []\n", "rules"),
        (
            BASE_RULES.replace("{ equals: accepted }", "{ equals: accepted, gte: 1 }"),
            "exatamente um",
        ),
        (BASE_RULES.replace("{ equals: accepted }", "{ between: [1, 2] }"), "expect"),
        (BASE_RULES.replace("{ equals: accepted }", "{ gte: many }"), "expect"),
        (BASE_RULES + "    if_absent: maybe\n", "if_absent"),
        (BASE_RULES + "    reason_by_value: { paused: made_up }\n", "reason_by_value"),
        (BASE_RULES + "    cooldown_hours: 72\n", "cooldown_hours"),
        (BASE_RULES.replace("id: consent", "id: Consent"), "id"),
        (BASE_RULES.replace("check: consent_status", "check: phone_number"), "check desconhecido"),
        (BASE_RULES.replace("reason: missing_consent", "reason: not_in_the_mood"), "reason"),
        (
            BASE_RULES.replace("check: consent_status", "check: hours_since_last_event"),
            "params obrigatórios",
        ),
        (
            BASE_RULES.replace(
                "check: consent_status",
                "check: hours_since_last_event\n    params: { event_name: phone_called }",
            ),
            "event_name desconhecido",
        ),
        (BASE_RULES + BASE_RULES.split("rules:\n", 1)[1], "únicos"),
    ],
)
def test_app_does_not_boot_with_an_invalid_ruleset(
    tmp_path: Path, body: str, fragment: str
) -> None:
    with pytest.raises(ConfigurationError) as exc:
        create_app(settings=_settings_for(_write_rules(tmp_path, body)))

    assert "rules.yaml" in str(exc.value)
    assert fragment in str(exc.value)
    assert_no_pii(str(exc.value))


def test_app_does_not_boot_without_the_rules_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="não encontrado"):
        create_app(settings=_settings_for(tmp_path / "missing.yaml"))


def test_ordering_comparator_on_a_non_numeric_check_is_rejected_at_boot(tmp_path: Path) -> None:
    # Sem esta validação a app sobe e toda avaliação viraria 500 ("str" >= 1).
    body = BASE_RULES.replace("{ equals: accepted }", "{ gte: 1 }")

    with pytest.raises(ConfigurationError, match="numérico"):
        create_app(settings=_settings_for(_write_rules(tmp_path, body)))


def test_ordering_comparators_are_accepted_on_numeric_checks(tmp_path: Path) -> None:
    body = BASE_RULES.replace("check: consent_status", "check: active_tasks_count").replace(
        "{ equals: accepted }", "{ gt: 0 }"
    )

    app = create_app(settings=_settings_for(_write_rules(tmp_path, body)))

    assert app.state.container.rules.rules[0].expect.as_dict() == {"gt": 0}
