"""Contratos de API da trilha de eventos."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.features.events.models import EventName
from app.shared.schemas import BaseSchema


class EventResponse(BaseSchema):
    """Envelope de evento como exposto na API."""

    event_id: UUID
    occurred_at: datetime
    event_name: EventName
    patient_id_hash: str = Field(description="HMAC-SHA256 do telefone; nunca o telefone")
    properties: dict[str, Any] = Field(default_factory=dict)
    schema_version: int
    correlation_id: str | None = Field(default=None, description="request_id que originou o evento")


class EventListResponse(BaseSchema):
    """Trilha de um paciente."""

    patient_id: UUID
    patient_id_hash: str
    total: int
    data: list[EventResponse]
