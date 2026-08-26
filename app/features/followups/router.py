"""Endpoint do motor de elegibilidade."""

from fastapi import APIRouter

from app.features.followups.dependencies import FollowupServiceDep
from app.features.followups.engine import trace_as_json
from app.features.followups.schemas import DecisionResponse, EvaluateRequest, RuleResultResponse

router = APIRouter(prefix="/followups", tags=["Follow-ups"])


@router.post(
    "/evaluate",
    response_model=DecisionResponse,
    summary="Avaliar elegibilidade de follow-up",
    description=(
        "Aplica as regras declarativas (`rules/default.yaml`) ao paciente e emite "
        "`followup_eligible` (com `template_key`) ou `followup_skipped` (com `reason` tipado). "
        "**Todas** as regras entram no `trace`, com valor observado e esperado — a decisão é "
        "explicável. Nenhuma mensagem é enviada."
    ),
)
async def evaluate_followup(data: EvaluateRequest, service: FollowupServiceDep) -> DecisionResponse:
    decision, event = service.evaluate(data.patient_id)
    return DecisionResponse(
        patient_id=data.patient_id,
        eligible=decision.eligible,
        reason=decision.reason,
        template_key=decision.template_key,
        rules_version=decision.rules_version,
        evaluated_at=decision.evaluated_at,
        event_id=event.event_id,
        trace=[RuleResultResponse.model_validate(item) for item in trace_as_json(decision)],
    )
