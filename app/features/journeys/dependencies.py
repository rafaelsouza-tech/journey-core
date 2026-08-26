"""Injeção de dependências da feature de jornadas."""

from typing import Annotated

from fastapi import Depends

from app.container import Container, ContainerDep
from app.features.journeys.service import JourneyService
from app.features.patients.dependencies import get_patient_service


def get_journey_service(container: Container) -> JourneyService:
    """Monta o serviço de jornadas a partir do container."""
    return JourneyService(
        plans=container.plans,
        journeys=container.journeys,
        patients=get_patient_service(container),
        events=container.events,
        clock=container.clock,
    )


def _journey_service_dep(container: ContainerDep) -> JourneyService:
    return get_journey_service(container)


JourneyServiceDep = Annotated[JourneyService, Depends(_journey_service_dep)]
