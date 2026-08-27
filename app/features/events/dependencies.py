"""Injeção de dependências da feature de eventos."""

from typing import Annotated

from fastapi import Depends

from app.container import ContainerDep
from app.features.events.service import EventService
from app.features.patients.dependencies import get_patient_service


def get_event_service(container: ContainerDep) -> EventService:
    """Monta o serviço de eventos a partir do container."""
    return EventService(get_patient_service(container), container.events)


EventServiceDep = Annotated[EventService, Depends(get_event_service)]
