"""Endpoints do motor de protocolo."""

from uuid import UUID

from fastapi import APIRouter, status

from app.features.protocols.dependencies import ProtocolServiceDep
from app.features.protocols.schemas import (
    AnswerRequest,
    ProtocolStepResponse,
    StartProtocolRequest,
)

router = APIRouter(tags=["Protocolos"])


@router.post(
    "/patients/{patient_id}/protocols",
    response_model=ProtocolStepResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Iniciar sessão de protocolo",
    description=(
        "Inicia uma sessão a partir de `template_id` (ex.: `phq9`) e devolve a primeira pergunta. "
        "Exige consentimento ativo (403 `CONSENT_REQUIRED`). "
        "Uma sessão em andamento por template (409)."
    ),
)
async def start_protocol(
    patient_id: UUID, data: StartProtocolRequest, service: ProtocolServiceDep
) -> ProtocolStepResponse:
    session, template = service.start(patient_id, data.template_id)
    return ProtocolStepResponse.from_session(session, template)


@router.get(
    "/protocol-sessions/{session_id}",
    response_model=ProtocolStepResponse,
    summary="Consultar sessão",
)
async def get_session(session_id: UUID, service: ProtocolServiceDep) -> ProtocolStepResponse:
    session, template = service.get(session_id)
    return ProtocolStepResponse.from_session(session, template)


@router.post(
    "/protocol-sessions/{session_id}/answers",
    response_model=ProtocolStepResponse,
    summary="Responder a próxima pergunta",
    description=(
        "Registra a resposta e devolve **a próxima pergunta ou o resultado final** "
        "(`score`, `status`, `ended_by_skip`). `question_id` precisa ser a pergunta esperada "
        "(409 `UNEXPECTED_QUESTION` protege contra entrega duplicada/fora de ordem). "
        "Ao concluir, a jornada é criada e `journey_id` vem preenchido."
    ),
)
async def answer(
    session_id: UUID, data: AnswerRequest, service: ProtocolServiceDep
) -> ProtocolStepResponse:
    session, template = service.answer(session_id, data.question_id, data.value)
    return ProtocolStepResponse.from_session(session, template)
