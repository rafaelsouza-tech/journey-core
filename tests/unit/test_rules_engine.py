from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.core.exceptions import ConfigurationError
from app.features.events.models import EventName
from app.features.followups.checks import CHECKS, EligibilityContext
from app.features.followups.engine import evaluate
from app.features.followups.loader import load_ruleset
from app.features.followups.models import Expectation, RuleSet, SkipReason
from app.features.journeys.models import JourneyStatus
from app.features.patients.models import ConsentStatus

pytestmark = pytest.mark.unit

RULES_PATH = Settings.model_fields["FOLLOWUP_RULES_PATH"].default
NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def ruleset() -> RuleSet:
    return load_ruleset(RULES_PATH)


def make_context(
    *,
    consent: ConsentStatus = ConsentStatus.ACCEPTED,
    completed: bool = True,
    journey: JourneyStatus | None = JourneyStatus.EM_ANDAMENTO,
    active_tasks: int = 3,
    last_eligible_hours_ago: float | None = None,
) -> EligibilityContext:
    last_at = (
        NOW - timedelta(hours=last_eligible_hours_ago)
        if last_eligible_hours_ago is not None
        else None
    )

    def last_event_at(name: EventName) -> datetime | None:
        return last_at if name is EventName.FOLLOWUP_ELIGIBLE else None

    return EligibilityContext(
        now=NOW,
        consent_status=consent,
        has_completed_protocol=completed,
        latest_journey_status=journey,
        active_tasks_count=active_tasks,
        last_event_at=last_event_at,
    )


def test_default_ruleset_matches_the_specification(ruleset: RuleSet) -> None:
    assert ruleset.template_key == "checkin_adesao"
    assert [rule.id for rule in ruleset.rules] == [
        "consent",
        "protocol_completed",
        "journey_active",
        "active_task",
        "cooldown",
    ]
    cooldown = ruleset.rules[-1]
    assert cooldown.check == "hours_since_last_event"
    assert cooldown.params == {"event_name": "followup_eligible"}
    assert cooldown.expect.as_dict() == {"gte": 72}
    assert cooldown.if_absent == "pass"
    assert {rule.reason for rule in ruleset.rules} <= set(SkipReason)


def test_all_rules_pass_makes_patient_eligible_with_full_trace(ruleset: RuleSet) -> None:
    decision = evaluate(make_context(), ruleset)

    assert decision.eligible is True
    assert decision.reason is None
    assert decision.template_key == "checkin_adesao"
    assert [item.rule_id for item in decision.trace] == [rule.id for rule in ruleset.rules]
    assert all(item.passed for item in decision.trace)
    assert decision.trace[-1].details["absent"] is True  # nunca disparou → cooldown passa


@pytest.mark.parametrize(
    ("consent", "expected_reason"),
    [
        (ConsentStatus.PENDING, SkipReason.MISSING_CONSENT),
        (ConsentStatus.PAUSED, SkipReason.CONSENT_PAUSED),
        (ConsentStatus.REVOKED, SkipReason.CONSENT_REVOKED),
    ],
)
def test_consent_reason_is_mapped_by_observed_value(
    ruleset: RuleSet, consent: ConsentStatus, expected_reason: SkipReason
) -> None:
    decision = evaluate(make_context(consent=consent), ruleset)

    assert decision.eligible is False
    assert decision.reason is expected_reason
    assert decision.trace[0].observed == consent.value


def test_reason_is_the_first_failing_rule_in_yaml_order(ruleset: RuleSet) -> None:
    decision = evaluate(
        make_context(consent=ConsentStatus.PENDING, completed=False, journey=None, active_tasks=0),
        ruleset,
    )

    assert decision.reason is SkipReason.MISSING_CONSENT
    assert [item.passed for item in decision.trace] == [False, False, False, False, True]


def test_all_rules_are_evaluated_even_after_a_failure(ruleset: RuleSet) -> None:
    decision = evaluate(make_context(completed=False, last_eligible_hours_ago=1), ruleset)

    assert len(decision.trace) == 5
    assert decision.reason is SkipReason.PROTOCOL_NOT_COMPLETED
    assert decision.trace[-1].passed is False  # cooldown também aparece como falho


@pytest.mark.parametrize(
    ("kwargs", "expected_reason"),
    [
        ({"completed": False}, SkipReason.PROTOCOL_NOT_COMPLETED),
        ({"journey": None}, SkipReason.NO_ACTIVE_JOURNEY),
        ({"journey": JourneyStatus.CONCLUIDA, "active_tasks": 0}, SkipReason.NO_ACTIVE_JOURNEY),
        ({"active_tasks": 0}, SkipReason.NO_ACTIVE_TASK),
        ({"last_eligible_hours_ago": 10}, SkipReason.COOLDOWN),
    ],
)
def test_each_rule_yields_its_typed_reason(
    ruleset: RuleSet, kwargs: dict[str, Any], expected_reason: SkipReason
) -> None:
    decision = evaluate(make_context(**kwargs), ruleset)

    assert decision.eligible is False
    assert decision.reason is expected_reason


@pytest.mark.parametrize(
    ("hours_ago", "eligible"), [(0, False), (71.99, False), (72, True), (100, True)]
)
def test_cooldown_boundary_is_72_hours_inclusive(
    ruleset: RuleSet, hours_ago: float, eligible: bool
) -> None:
    decision = evaluate(make_context(last_eligible_hours_ago=hours_ago), ruleset)

    assert decision.eligible is eligible
    cooldown = decision.trace[-1]
    assert cooldown.observed == pytest.approx(hours_ago, abs=0.01)
    if not eligible:
        assert cooldown.details["remaining"] == pytest.approx(72 - hours_ago, abs=0.01)
        assert cooldown.details["last_event_at"] is not None


@pytest.mark.parametrize(
    ("expectation", "observed", "expected"),
    [
        ({"equals": "a"}, "a", True),
        ({"not_equals": "a"}, "a", False),
        ({"in": [1, 2]}, 2, True),
        ({"gt": 1}, 1, False),
        ({"gte": 1}, 1, True),
        ({"lt": 5}, 4, True),
        ({"lte": 5}, 6, False),
    ],
)
def test_expectation_operators(expectation: dict[str, Any], observed: Any, expected: bool) -> None:
    assert Expectation.model_validate(expectation).evaluate(observed) is expected


def test_expectation_requires_exactly_one_operator() -> None:
    with pytest.raises(ValueError, match="exatamente um"):
        Expectation.model_validate({"gte": 1, "lte": 2})
    with pytest.raises(ValueError, match="exatamente um"):
        Expectation.model_validate({})


def _write_rules(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(body, encoding="utf-8")
    return path


BASE = """
version: 1
template_key: checkin_adesao
rules:
  - id: r1
    check: {check}
    {params}
    expect: {{ equals: accepted }}
    reason: {reason}
"""


@pytest.mark.parametrize(
    ("check", "params", "reason", "fragment"),
    [
        ("consent_status", "", "missing_consent", None),
        ("unknown_check", "", "missing_consent", "check desconhecido"),
        ("hours_since_last_event", "", "cooldown", "params obrigatórios"),
        (
            "hours_since_last_event",
            "params: { event_name: not_an_event }",
            "cooldown",
            "event_name desconhecido",
        ),
        ("consent_status", "", "made_up_reason", "reason"),
    ],
)
def test_loader_validates_checks_params_and_reasons(
    tmp_path: Path, check: str, params: str, reason: str, fragment: str | None
) -> None:
    path = _write_rules(tmp_path, BASE.format(check=check, params=params, reason=reason))
    if fragment is None:
        assert load_ruleset(path).rules[0].check == check
        return
    with pytest.raises(ConfigurationError) as exc:
        load_ruleset(path)
    assert fragment in str(exc.value)


def test_loader_rejects_duplicate_rule_ids(tmp_path: Path) -> None:
    body = """
version: 1
template_key: checkin_adesao
rules:
  - { id: r, check: consent_status, expect: { equals: accepted }, reason: missing_consent }
  - { id: r, check: consent_status, expect: { equals: accepted }, reason: missing_consent }
"""
    with pytest.raises(ConfigurationError, match="únicos"):
        load_ruleset(_write_rules(tmp_path, body))


def test_check_vocabulary_is_closed() -> None:
    assert set(CHECKS) == {
        "consent_status",
        "has_completed_protocol",
        "latest_journey_status",
        "active_tasks_count",
        "hours_since_last_event",
    }
