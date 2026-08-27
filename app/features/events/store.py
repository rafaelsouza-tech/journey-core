"""
Event Store append-only.

A porta `EventStore` só expõe `append` e leituras: não existe update nem delete
na interface — a imutabilidade é do contrato, não apenas da implementação.

Cada evento pertence a uma **trilha**: a do cadastro que o originou, identificada
pelo `event_id` do seu `patient_created`. Um mesmo telefone (mesmo hash) pode ter
tido mais de um cadastro (revogação seguida de novo cadastro); cada um enxerga só
a própria história. O `trail_id` é índice interno — não faz parte do envelope.
"""

from collections import defaultdict
from collections.abc import Mapping
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.core.clock import Clock
from app.core.logging import current_request_id, get_logger
from app.features.events.models import Event, EventName
from app.features.events.pii_guard import validate_patient_id_hash, validate_properties
from app.shared.serialization import freeze, json_safe

logger = get_logger(__name__)


class EventStore(Protocol):
    """Porta do Event Store."""

    def append(
        self,
        event_name: EventName,
        patient_id_hash: str,
        properties: Mapping[str, Any] | None = None,
        *,
        trail_id: UUID | None = None,
    ) -> Event:
        """Grava um evento na trilha `trail_id`; sem `trail_id`, o evento inicia uma trilha nova."""
        ...

    def list_by_patient_hash(
        self,
        patient_id_hash: str,
        event_name: EventName | None = None,
        *,
        trail_id: UUID | None = None,
    ) -> list[Event]:
        """Eventos do hash em ordem de gravação; com `trail_id`, só os daquela trilha."""
        ...

    def last_by_name(
        self, patient_id_hash: str, event_name: EventName, *, trail_id: UUID | None = None
    ) -> Event | None:
        """Último evento de um nome para o hash (ou para a trilha), se houver."""
        ...

    def __len__(self) -> int: ...


class InMemoryEventStore:
    """Implementação em memória: lista global + índices por hash e por trilha."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._events: list[Event] = []
        self._by_hash: dict[str, list[Event]] = defaultdict(list)
        self._by_trail: dict[tuple[str, UUID], list[Event]] = defaultdict(list)

    def append(
        self,
        event_name: EventName,
        patient_id_hash: str,
        properties: Mapping[str, Any] | None = None,
        *,
        trail_id: UUID | None = None,
    ) -> Event:
        """Valida (PII no hash e nas properties), congela em profundidade e grava o evento."""
        safe_properties = json_safe(properties or {})
        validate_patient_id_hash(event_name, patient_id_hash)
        validate_properties(event_name, safe_properties)
        event = Event(
            event_id=uuid4(),
            occurred_at=self._clock.now(),
            event_name=event_name,
            patient_id_hash=patient_id_hash,
            properties=freeze(safe_properties),
            correlation_id=current_request_id(),
        )
        self._events.append(event)
        self._by_hash[patient_id_hash].append(event)
        self._by_trail[(patient_id_hash, trail_id or event.event_id)].append(event)
        logger.info("event_appended", event_name=str(event_name), event_id=str(event.event_id))
        return event

    def list_by_patient_hash(
        self,
        patient_id_hash: str,
        event_name: EventName | None = None,
        *,
        trail_id: UUID | None = None,
    ) -> list[Event]:
        """Eventos do hash (ou da trilha), opcionalmente filtrados por nome."""
        events = self._select(patient_id_hash, trail_id)
        if event_name is None:
            return events
        return [event for event in events if event.event_name == event_name]

    def last_by_name(
        self, patient_id_hash: str, event_name: EventName, *, trail_id: UUID | None = None
    ) -> Event | None:
        """Último evento de um nome para o hash (ou para a trilha)."""
        for event in reversed(self._select(patient_id_hash, trail_id)):
            if event.event_name == event_name:
                return event
        return None

    def _select(self, patient_id_hash: str, trail_id: UUID | None) -> list[Event]:
        if trail_id is None:
            return list(self._by_hash.get(patient_id_hash, []))
        return list(self._by_trail.get((patient_id_hash, trail_id), []))

    def __len__(self) -> int:
        return len(self._events)
