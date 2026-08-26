"""Endpoints da trilha de eventos."""

from uuid import UUID

from fastapi import APIRouter, Query

from app.features.events.dependencies import EventServiceDep
from app.features.events.models import EventName
from app.features.events.schemas import EventListResponse, EventResponse

router = APIRouter(prefix="/events", tags=["Eventos"])


@router.get(
    "",
    response_model=EventListResponse,
    summary="Trilha de eventos do paciente",
    description=(
        "Lista os eventos do paciente em ordem cronológica. A consulta usa o **id interno**; "
        "a trilha persistida carrega apenas `patient_id_hash` — nunca telefone, nome ou nascimento."
    ),
)
async def list_events(
    service: EventServiceDep,
    patient_id: UUID = Query(description="Id interno do paciente"),
    event_name: EventName | None = Query(default=None, description="Filtro opcional por nome"),
) -> EventListResponse:
    patient_hash, events = service.list_for_patient(patient_id, event_name)
    return EventListResponse(
        patient_id=patient_id,
        patient_id_hash=patient_hash,
        total=len(events),
        data=[EventResponse.model_validate(event) for event in events],
    )
