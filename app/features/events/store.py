"""
Event Store append-only.

A porta `EventStore` só expõe `append` e leituras: não existe update nem delete
na interface — a imutabilidade é do contrato, não apenas da implementação.
"""

from collections import defaultdict
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Protocol
from uuid import uuid4

from app.core.clock import Clock
from app.core.logging import current_request_id, get_logger
from app.features.events.models import Event, EventName
from app.features.events.pii_guard import validate_properties
from app.shared.serialization import json_safe

logger = get_logger(__name__)


class EventStore(Protocol):
    """Porta do Event Store."""

    def append(
        self,
        event_name: EventName,
        patient_id_hash: str,
        properties: Mapping[str, Any] | None = None,
    ) -> Event:
        """Grava um evento e o devolve."""
        ...

    def list_by_patient_hash(
        self, patient_id_hash: str, event_name: EventName | None = None
    ) -> list[Event]:
        """Trilha do paciente em ordem de gravação, opcionalmente filtrada por nome."""
        ...

    def last_by_name(self, patient_id_hash: str, event_name: EventName) -> Event | None:
        """Último evento de um nome para o paciente, se houver."""
        ...

    def __len__(self) -> int: ...


class InMemoryEventStore:
    """Implementação em memória: lista global + índice por `patient_id_hash`."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._events: list[Event] = []
        self._by_hash: dict[str, list[Event]] = defaultdict(list)

    def append(
        self,
        event_name: EventName,
        patient_id_hash: str,
        properties: Mapping[str, Any] | None = None,
    ) -> Event:
        """Valida (PII), congela e grava o evento."""
        safe_properties = json_safe(properties or {})
        validate_properties(event_name, safe_properties)
        event = Event(
            event_id=uuid4(),
            occurred_at=self._clock.now(),
            event_name=event_name,
            patient_id_hash=patient_id_hash,
            properties=MappingProxyType(safe_properties),
            correlation_id=current_request_id(),
        )
        self._events.append(event)
        self._by_hash[patient_id_hash].append(event)
        logger.info("event_appended", event_name=str(event_name), event_id=str(event.event_id))
        return event

    def list_by_patient_hash(
        self, patient_id_hash: str, event_name: EventName | None = None
    ) -> list[Event]:
        """Trilha do paciente, opcionalmente filtrada por nome."""
        events = self._by_hash.get(patient_id_hash, [])
        if event_name is None:
            return list(events)
        return [event for event in events if event.event_name == event_name]

    def last_by_name(self, patient_id_hash: str, event_name: EventName) -> Event | None:
        """Último evento de um nome para o paciente."""
        for event in reversed(self._by_hash.get(patient_id_hash, [])):
            if event.event_name == event_name:
                return event
        return None

    def __len__(self) -> int:
        return len(self._events)
