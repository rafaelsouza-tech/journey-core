"""Injeção de dependências da feature de jornadas."""

from typing import Annotated

from fastapi import Depends

from app.container import ContainerDep
from app.features.journeys.service import JourneyService
from app.features.patients.dependencies import get_patient_service


def get_journey_service(container: ContainerDep) -> JourneyService:
    """Monta o serviço de jornadas a partir do container."""
    return JourneyService(
        plans=container.plans,
        journeys=container.journeys,
        patients=get_patient_service(container),
        events=container.events,
        clock=container.clock,
    )


JourneyServiceDep = Annotated[JourneyService, Depends(get_journey_service)]
