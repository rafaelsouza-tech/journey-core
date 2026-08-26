"""Carga e validação do conjunto de regras (YAML) contra o vocabulário de checks."""

from pathlib import Path

from app.core.exceptions import ConfigurationError
from app.features.events.models import EventName
from app.features.followups.checks import CHECKS
from app.features.followups.models import RuleSet
from app.shared.declarative import load_document


def load_ruleset(path: Path) -> RuleSet:
    """
    Lê o YAML e garante que cada regra usa um check conhecido com os params exigidos.

    Raises:
        ConfigurationError
    """
    ruleset = load_document(path, RuleSet)
    for rule in ruleset.rules:
        spec = CHECKS.get(rule.check)
        if spec is None:
            raise ConfigurationError(
                f"regra '{rule.id}': check desconhecido '{rule.check}' "
                f"(disponíveis: {', '.join(sorted(CHECKS))})"
            )
        missing = [name for name in spec.required_params if name not in rule.params]
        if missing:
            raise ConfigurationError(
                f"regra '{rule.id}': params obrigatórios ausentes: {', '.join(missing)}"
            )
        if "event_name" in rule.params:
            try:
                EventName(rule.params["event_name"])
            except ValueError as exc:
                raise ConfigurationError(
                    f"regra '{rule.id}': event_name desconhecido '{rule.params['event_name']}'"
                ) from exc
    return ruleset
