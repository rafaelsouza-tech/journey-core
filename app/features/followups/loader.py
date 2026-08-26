"""Carga e validação do conjunto de regras (YAML) contra o vocabulário de checks."""

from pathlib import Path

from app.core.exceptions import ConfigurationError
from app.features.events.models import EventName
from app.features.followups.checks import CHECKS
from app.features.followups.models import ORDERING_OPERATORS, RuleSet
from app.shared.declarative import load_document


def _rule_error(path: Path, rule_id: str, problem: str) -> ConfigurationError:
    """Erro de configuração apontando arquivo e regra — sem valores de paciente, só do YAML."""
    return ConfigurationError(f"{path.name}: regra '{rule_id}': {problem}")


def load_ruleset(path: Path) -> RuleSet:
    """
    Lê o YAML e garante que cada regra usa um check conhecido, com os params exigidos
    e um comparador compatível com o tipo do valor observado.

    Raises:
        ConfigurationError
    """
    ruleset = load_document(path, RuleSet)
    for rule in ruleset.rules:
        spec = CHECKS.get(rule.check)
        if spec is None:
            raise _rule_error(
                path,
                rule.id,
                f"check desconhecido '{rule.check}' (disponíveis: {', '.join(sorted(CHECKS))})",
            )
        missing = [name for name in spec.required_params if name not in rule.params]
        if missing:
            raise _rule_error(path, rule.id, f"params obrigatórios ausentes: {', '.join(missing)}")
        if "event_name" in rule.params:
            try:
                EventName(rule.params["event_name"])
            except ValueError as exc:
                raise _rule_error(
                    path, rule.id, f"event_name desconhecido '{rule.params['event_name']}'"
                ) from exc
        if rule.expect.operator in ORDERING_OPERATORS and not spec.numeric:
            # Sem isto a app sobe e a comparação (ex.: "accepted" >= 1) estoura em todo request.
            raise _rule_error(
                path,
                rule.id,
                f"comparador '{rule.expect.operator}' exige um check numérico "
                f"('{rule.check}' não é)",
            )
    return ruleset
