"""
Modelos do motor de protocolo.

- `ProtocolTemplate` (Pydantic): schema do JSON — perguntas, escala, scoring e skip rules.
  É a ÚNICA fonte das perguntas; o serviço nunca conhece um template específico.
- `ProtocolSession` (dataclass): estado de uma aplicação do protocolo a um paciente.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Formato dos identificadores do template (template_id, ids de perguntas e de regras).
# Reutilizado pelos contratos da API: o que não casa é 422 antes de chegar ao serviço.
IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_]*$"

# -----------------------------------------------------------------------------
# Template (schema do JSON)
# -----------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ScaleOption(_Strict):
    value: int
    label: str = Field(min_length=1)


class Scale(_Strict):
    options: list[ScaleOption] = Field(min_length=2)

    @property
    def allowed_values(self) -> list[int]:
        return [option.value for option in self.options]

    @property
    def max_value(self) -> int:
        return max(self.allowed_values)

    @model_validator(mode="after")
    def _unique_values(self) -> "Scale":
        if len(set(self.allowed_values)) != len(self.options):
            raise ValueError("valores da escala devem ser únicos")
        return self


class Scoring(_Strict):
    method: Literal["sum"] = "sum"


class Question(_Strict):
    id: str = Field(pattern=IDENTIFIER_PATTERN)
    order: int = Field(ge=1)
    text: str = Field(min_length=1)


class SumOf(_Strict):
    sum_of: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> "SumOf":
        if len(set(self.sum_of)) != len(self.sum_of):
            raise ValueError(
                "ids em sum_of devem ser únicos (repetir contaria a resposta duas vezes)"
            )
        return self


class AnswerRef(_Strict):
    answer: str


Operand = int | SumOf | AnswerRef
ConditionOperator = Literal["lt", "lte", "gt", "gte", "eq", "ne"]


class Condition(_Strict):
    op: ConditionOperator
    left: Operand
    right: Operand

    def referenced_questions(self) -> set[str]:
        refs: set[str] = set()
        for operand in (self.left, self.right):
            if isinstance(operand, SumOf):
                refs.update(operand.sum_of)
            elif isinstance(operand, AnswerRef):
                refs.add(operand.answer)
        return refs


class SkipAction(StrEnum):
    END_BLOCK = "end_block"


class SkipRule(_Strict):
    id: str = Field(pattern=IDENTIFIER_PATTERN)
    description: str | None = None
    after_question: str
    condition: Condition
    action: SkipAction


class ProtocolTemplate(_Strict):
    """Template de protocolo carregado do JSON."""

    template_id: str = Field(pattern=IDENTIFIER_PATTERN)
    version: int = Field(ge=1)
    name: str = Field(min_length=1)
    description: str | None = None
    intro: str = Field(min_length=1)
    scale: Scale
    scoring: Scoring = Scoring()
    questions: list[Question] = Field(min_length=1)
    skip_rules: list[SkipRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> "ProtocolTemplate":
        ids = [question.id for question in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError("ids de perguntas devem ser únicos")
        orders = sorted(question.order for question in self.questions)
        if orders != list(range(1, len(orders) + 1)):
            raise ValueError("orders das perguntas devem ser 1..n sem lacunas")
        order_of = {question.id: question.order for question in self.questions}
        rule_ids = [rule.id for rule in self.skip_rules]
        if len(set(rule_ids)) != len(rule_ids):
            raise ValueError("ids de skip_rules devem ser únicos")
        for rule in self.skip_rules:
            if rule.after_question not in order_of:
                raise ValueError(f"skip_rule '{rule.id}' aponta para pergunta inexistente")
            for ref in rule.condition.referenced_questions():
                if ref not in order_of:
                    raise ValueError(
                        f"skip_rule '{rule.id}' referencia pergunta inexistente '{ref}'"
                    )
                if order_of[ref] > order_of[rule.after_question]:
                    raise ValueError(
                        f"skip_rule '{rule.id}' usa '{ref}', que ainda não foi respondida"
                    )
        return self

    @property
    def ordered_questions(self) -> list[Question]:
        return sorted(self.questions, key=lambda question: question.order)

    @property
    def max_score(self) -> int:
        return self.scale.max_value * len(self.questions)

    def get_question(self, question_id: str) -> Question | None:
        return next((q for q in self.questions if q.id == question_id), None)


# -----------------------------------------------------------------------------
# Sessão (estado)
# -----------------------------------------------------------------------------


class SessionStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass
class ProtocolSession:
    """Aplicação de um template a um paciente. Pina `template_version` no início."""

    id: UUID
    patient_id: UUID
    template_id: str
    template_version: int
    status: SessionStatus
    started_at: datetime
    answers: dict[str, int] = field(default_factory=dict)
    score: int | None = None
    ended_by_skip: bool = False
    skip_rule_id: str | None = None
    completed_at: datetime | None = None
    journey_id: UUID | None = None

    @property
    def is_completed(self) -> bool:
        return self.status is SessionStatus.COMPLETED
