"""Guarda de PII na fronteira do Event Store."""

from collections.abc import Mapping
from typing import Any

from app.core.exceptions import PIIGuardViolationError
from app.core.pii import find_pii


def validate_properties(event_name: str, properties: Mapping[str, Any]) -> None:
    """
    Recusa `properties` que contenham chaves proibidas ou valores com cara de telefone.

    Raises:
        PIIGuardViolationError: com os caminhos violados (nunca os valores).
    """
    violations = find_pii(properties, path="properties")
    if violations:
        raise PIIGuardViolationError(event_name, violations)
