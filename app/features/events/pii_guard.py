"""Guarda de PII na fronteira do Event Store."""

from collections.abc import Mapping
from typing import Any

from app.core.exceptions import PIIGuardViolationError
from app.core.pii import find_pii, is_phone_like


def validate_patient_id_hash(event_name: str, patient_id_hash: str) -> None:
    """
    Recusa um `patient_id_hash` com cara de telefone: a trilha carrega o hash, nunca o número.

    Raises:
        PIIGuardViolationError: sem o valor recebido.
    """
    if is_phone_like(patient_id_hash):
        raise PIIGuardViolationError(event_name, ["patient_id_hash (phone_like_value)"])


def validate_properties(event_name: str, properties: Mapping[str, Any]) -> None:
    """
    Recusa `properties` que contenham chaves proibidas ou valores com cara de telefone.

    Raises:
        PIIGuardViolationError: com os caminhos violados (nunca os valores).
    """
    violations = find_pii(properties, path="properties")
    if violations:
        raise PIIGuardViolationError(event_name, violations)
