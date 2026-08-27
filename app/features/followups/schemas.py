"""Contratos de API do motor de elegibilidade."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.features.followups.models import Decision, SkipReason
from app.shared.schemas import BaseSchema


class EvaluateRequest(BaseSchema):
    """Paciente a avaliar."""

    patient_id: UUID


class RuleResultResponse(BaseSchema):
    """Uma linha do trace: regra, observado, esperado e resultado."""

    rule_id: str
    check: str
    params: dict[str, Any]
    observed: Any
    expected: dict[str, Any]
    passed: bool
    details: dict[str, Any]


class DecisionResponse(BaseSchema):
    """Decisão de elegibilidade — e o porquê."""

    patient_id: UUID
    eligible: bool
    reason: SkipReason | None = Field(description="Motivo tipado quando não elegível")
    template_key: str = Field(description="Template de follow-up que seria disparado")
    rules_version: int
    evaluated_at: datetime
    event_id: UUID = Field(description="Evento followup_eligible/followup_skipped emitido")
    trace: list[RuleResultResponse] = Field(description="Todas as regras avaliadas, em ordem")

    @classmethod
    def from_decision(
        cls, patient_id: UUID, decision: Decision, event_id: UUID
    ) -> "DecisionResponse":
        """Monta a resposta a partir da decisão do motor e do evento registrado."""
        return cls(
            patient_id=patient_id,
            eligible=decision.eligible,
            reason=decision.reason,
            template_key=decision.template_key,
            rules_version=decision.rules_version,
            evaluated_at=decision.evaluated_at,
            event_id=event_id,
            trace=[RuleResultResponse.model_validate(item) for item in decision.trace],
        )
