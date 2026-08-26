"""Injeção de dependências da feature de pacientes."""

from typing import Annotated

from fastapi import Depends

from app.container import ContainerDep
from app.features.patients.service import PatientService


def get_patient_service(container: ContainerDep) -> PatientService:
    """Monta o serviço de pacientes a partir do container."""
    return PatientService(
        container.patients,
        container.events,
        container.clock,
        container.settings.PHONE_HASH_SALT,
    )


PatientServiceDep = Annotated[PatientService, Depends(get_patient_service)]
