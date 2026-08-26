"""
Motor de regras: avalia TODAS as regras e devolve uma decisão com trace completo.

`eligible` só é verdadeiro se todas passarem; `reason` é o da primeira regra que
falha (a ordem do YAML é a prioridade). Nenhuma regra é hardcoded aqui.
"""

from collections.abc import Mapping
from typing import Any

from app.features.followups.checks import CHECKS, EligibilityContext
from app.features.followups.models import Decision, Rule, RuleResult, RuleSet, SkipReason
from app.shared.serialization import json_safe


def _evaluate_rule(rule: Rule, ctx: EligibilityContext) -> tuple[RuleResult, SkipReason | None]:
    observed, details = CHECKS[rule.check].fn(ctx, rule.params)
    details = dict(details)

    if observed is None:
        passed = rule.if_absent == "pass"
        details["absent"] = True
    else:
        passed = rule.expect.evaluate(observed)
        if (
            not passed
            and rule.expect.operator in {"gte", "gt"}
            and isinstance(observed, int | float)
        ):
            details["remaining"] = round(float(rule.expect.threshold) - float(observed), 2)

    reason = None if passed else rule.reason_by_value.get(str(observed), rule.reason)
    result = RuleResult(
        rule_id=rule.id,
        check=rule.check,
        params=json_safe(rule.params),
        observed=json_safe(observed),
        expected=rule.expect.as_dict(),
        passed=passed,
        details=json_safe(details),
    )
    return result, reason


def evaluate(ctx: EligibilityContext, ruleset: RuleSet) -> Decision:
    """Aplica o conjunto de regras ao contexto."""
    trace: list[RuleResult] = []
    first_reason: SkipReason | None = None
    for rule in ruleset.rules:
        result, reason = _evaluate_rule(rule, ctx)
        trace.append(result)
        if reason is not None and first_reason is None:
            first_reason = reason
    return Decision(
        eligible=first_reason is None,
        reason=first_reason,
        template_key=ruleset.template_key,
        rules_version=ruleset.version,
        evaluated_at=ctx.now,
        trace=trace,
    )


def trace_as_json(decision: Decision) -> list[Mapping[str, Any]]:
    """Trace serializável para properties de evento e resposta da API."""
    return [
        {
            "rule_id": item.rule_id,
            "check": item.check,
            "params": item.params,
            "observed": item.observed,
            "expected": item.expected,
            "passed": item.passed,
            "details": item.details,
        }
        for item in decision.trace
    ]
