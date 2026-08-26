"""Endpoints de pacientes e consentimento."""

from uuid import UUID

from fastapi import APIRouter, status

from app.features.patients.dependencies import PatientServiceDep
from app.features.patients.models import ConsentAction
from app.features.patients.schemas import PatientCreateRequest, PatientResponse

router = APIRouter(prefix="/patients", tags=["Pacientes"])


@router.post(
    "",
    response_model=PatientResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Cadastrar paciente",
    description=(
        "Cria o paciente e emite `patient_created` (e `terms_accepted` quando `terms_accepted=true`). "
        "O telefone é persistido como HMAC-SHA256 e **não** retorna em nenhuma resposta."
    ),
)
async def create_patient(data: PatientCreateRequest, service: PatientServiceDep) -> PatientResponse:
    return PatientResponse.model_validate(service.create(data))


@router.get(
    "/{patient_id}",
    response_model=PatientResponse,
    summary="Consultar paciente",
)
async def get_patient(patient_id: UUID, service: PatientServiceDep) -> PatientResponse:
    return PatientResponse.model_validate(service.get(patient_id))


@router.post(
    "/{patient_id}/consent/{action}",
    response_model=PatientResponse,
    summary="Ciclo de vida do consentimento",
    description=(
        "`accept` (pending→accepted) · `pause` (accepted→paused) · `resume` (paused→accepted) · "
        "`revoke` (qualquer→revoked, terminal). **`revoke` apaga telefone, nome e nascimento do "
        "cadastro**; a trilha de eventos permanece, pseudonimizada pelo hash. Transição inválida → 409."
    ),
)
async def apply_consent_action(
    patient_id: UUID, action: ConsentAction, service: PatientServiceDep
) -> PatientResponse:
    return PatientResponse.model_validate(service.apply_consent_action(patient_id, action))
