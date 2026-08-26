"""Endpoints de jornadas e tarefas."""

from uuid import UUID

from fastapi import APIRouter

from app.features.journeys.dependencies import JourneyServiceDep
from app.features.journeys.schemas import JourneyListResponse, JourneyResponse

router = APIRouter(tags=["Jornadas"])


@router.get(
    "/journeys/{journey_id}",
    response_model=JourneyResponse,
    summary="Consultar jornada",
)
async def get_journey(journey_id: UUID, service: JourneyServiceDep) -> JourneyResponse:
    return JourneyResponse.model_validate(service.get(journey_id))


@router.get(
    "/patients/{patient_id}/journeys",
    response_model=JourneyListResponse,
    summary="Jornadas do paciente",
)
async def list_patient_journeys(
    patient_id: UUID, service: JourneyServiceDep
) -> JourneyListResponse:
    journeys = service.list_for_patient(patient_id)
    return JourneyListResponse(
        patient_id=patient_id,
        total=len(journeys),
        data=[JourneyResponse.model_validate(journey) for journey in journeys],
    )


@router.post(
    "/journeys/{journey_id}/tasks/{task_id}/complete",
    response_model=JourneyResponse,
    summary="Concluir tarefa",
    description=(
        "Marca a tarefa como `concluida` e emite `task_completed`. Quando for a última, a jornada "
        "passa a `concluida` (`journey_completed`). Tarefa já concluída → 409 (sem evento duplicado)."
    ),
)
async def complete_task(
    journey_id: UUID, task_id: UUID, service: JourneyServiceDep
) -> JourneyResponse:
    return JourneyResponse.model_validate(service.complete_task(journey_id, task_id))
