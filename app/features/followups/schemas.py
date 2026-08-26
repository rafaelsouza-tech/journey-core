"""Contratos de API do motor de elegibilidade."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.features.followups.models import SkipReason
from app.shared.schemas import BaseSchema


class EvaluateRequest(BaseSchema):
    patient_id: UUID


class RuleResultResponse(BaseSchema):
    rule_id: str
    check: str
    params: dict[str, Any]
    observed: Any
    expected: dict[str, Any]
    passed: bool
    details: dict[str, Any]


class DecisionResponse(BaseSchema):
    patient_id: UUID
    eligible: bool
    reason: SkipReason | None = Field(description="Motivo tipado quando não elegível")
    template_key: str = Field(description="Template de follow-up que seria disparado")
    rules_version: int
    evaluated_at: datetime
    event_id: UUID = Field(description="Evento followup_eligible/followup_skipped emitido")
    trace: list[RuleResultResponse] = Field(description="Todas as regras avaliadas, em ordem")
