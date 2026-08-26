"""Injeção de dependências da feature de follow-ups."""

from typing import Annotated

from fastapi import Depends

from app.container import ContainerDep
from app.features.followups.service import FollowupService
from app.features.patients.dependencies import get_patient_service


def get_followup_service(container: ContainerDep) -> FollowupService:
    """Monta o serviço de follow-ups a partir do container."""
    return FollowupService(
        rules=container.rules,
        patients=get_patient_service(container),
        sessions=container.sessions,
        journeys=container.journeys,
        events=container.events,
        clock=container.clock,
    )


FollowupServiceDep = Annotated[FollowupService, Depends(get_followup_service)]
