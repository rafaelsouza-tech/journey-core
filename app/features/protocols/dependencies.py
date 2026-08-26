"""Injeção de dependências da feature de protocolos."""

from typing import Annotated

from fastapi import Depends

from app.container import ContainerDep
from app.features.journeys.dependencies import get_journey_service
from app.features.patients.dependencies import get_patient_service
from app.features.protocols.service import ProtocolService


def get_protocol_service(container: ContainerDep) -> ProtocolService:
    """Monta o serviço de protocolos a partir do container."""
    return ProtocolService(
        templates=container.templates,
        sessions=container.sessions,
        patients=get_patient_service(container),
        journeys=get_journey_service(container),
        events=container.events,
        clock=container.clock,
    )


ProtocolServiceDep = Annotated[ProtocolService, Depends(get_protocol_service)]
