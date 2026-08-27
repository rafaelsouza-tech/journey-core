"""Contratos de API de jornadas."""

from datetime import datetime
from uuid import UUID

from app.features.journeys.models import JourneyStatus, TaskStatus
from app.shared.schemas import BaseSchema


class TaskResponse(BaseSchema):
    """Tarefa da jornada (id, título e status literal do enunciado)."""

    id: UUID
    key: str
    title: str
    status: TaskStatus
    completed_at: datetime | None


class JourneyResponse(BaseSchema):
    """Jornada com objetivo e tarefas."""

    id: UUID
    patient_id: UUID
    source_session_id: UUID
    template_id: str
    plan_version: int
    status: JourneyStatus
    objective: str
    tasks: list[TaskResponse]
    created_at: datetime
    completed_at: datetime | None


class JourneyListResponse(BaseSchema):
    """Jornadas de um paciente."""

    patient_id: UUID
    total: int
    data: list[JourneyResponse]
