"""Envelope de evento e taxonomia."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID


class EventName(StrEnum):
    """
    Taxonomia de eventos.

    Os oito primeiros são a taxonomia mínima do enunciado, com os nomes literais.
    Os prefixados com `consent_` e `journey_completed` são extensões desta implementação.
    """

    PATIENT_CREATED = "patient_created"
    TERMS_ACCEPTED = "terms_accepted"
    PROTOCOL_STARTED = "protocol_started"
    PROTOCOL_COMPLETED = "protocol_completed"
    JOURNEY_CREATED = "journey_created"
    TASK_COMPLETED = "task_completed"
    FOLLOWUP_ELIGIBLE = "followup_eligible"
    FOLLOWUP_SKIPPED = "followup_skipped"
    # extensões
    CONSENT_PAUSED = "consent_paused"
    CONSENT_RESUMED = "consent_resumed"
    CONSENT_REVOKED = "consent_revoked"
    JOURNEY_COMPLETED = "journey_completed"


EVENT_SCHEMA_VERSION = 1


def _empty_properties() -> Mapping[str, Any]:
    """Default somente leitura — um `dict` vazio seria mutável mesmo num dataclass frozen."""
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Event:
    """
    Evento imutável.

    `properties` é um `Mapping` somente leitura (MappingProxyType) com valores
    JSON-seguros e sem PII — garantido pelo store na hora do `append`.
    """

    event_id: UUID
    occurred_at: datetime
    event_name: EventName
    patient_id_hash: str
    properties: Mapping[str, Any] = field(default_factory=_empty_properties)
    schema_version: int = EVENT_SCHEMA_VERSION
    correlation_id: str | None = None
