"""Contratos de API do motor de protocolo."""

from uuid import UUID

from pydantic import Field

from app.features.protocols.models import ProtocolSession, ProtocolTemplate, SessionStatus
from app.shared.schemas import BaseSchema


class StartProtocolRequest(BaseSchema):
    template_id: str = Field(examples=["phq9"])


class AnswerRequest(BaseSchema):
    question_id: str = Field(
        description="Id da pergunta que está sendo respondida (a próxima esperada)",
        examples=["q1"],
    )
    value: int = Field(description="Valor na escala do template", examples=[1])


class ScaleOptionResponse(BaseSchema):
    value: int
    label: str


class QuestionResponse(BaseSchema):
    id: str
    order: int
    text: str
    intro: str = Field(description="Pergunta-guia do template")
    options: list[ScaleOptionResponse]


class ProgressResponse(BaseSchema):
    answered: int
    total: int


class ProtocolResultResponse(BaseSchema):
    score: int = Field(description="Soma das respostas dadas (parcial quando encerrado por skip)")
    max_score: int
    ended_by_skip: bool
    skip_rule_id: str | None
    answered_questions: list[str]


class ProtocolStepResponse(BaseSchema):
    """Mesmo shape para início, resposta e consulta: próxima pergunta OU resultado final."""

    session_id: UUID
    patient_id: UUID
    template_id: str
    template_version: int
    status: SessionStatus
    progress: ProgressResponse
    next_question: QuestionResponse | None
    result: ProtocolResultResponse | None
    journey_id: UUID | None

    @classmethod
    def from_session(
        cls, session: ProtocolSession, template: ProtocolTemplate
    ) -> "ProtocolStepResponse":
        from app.features.protocols.engine import next_question

        pending = None if session.is_completed else next_question(template, session.answers)
        options = [
            ScaleOptionResponse(value=o.value, label=o.label) for o in template.scale.options
        ]
        return cls(
            session_id=session.id,
            patient_id=session.patient_id,
            template_id=session.template_id,
            template_version=session.template_version,
            status=session.status,
            progress=ProgressResponse(answered=len(session.answers), total=len(template.questions)),
            next_question=(
                QuestionResponse(
                    id=pending.id,
                    order=pending.order,
                    text=pending.text,
                    intro=template.intro,
                    options=options,
                )
                if pending is not None
                else None
            ),
            result=(
                ProtocolResultResponse(
                    score=session.score if session.score is not None else 0,
                    max_score=template.max_score,
                    ended_by_skip=session.ended_by_skip,
                    skip_rule_id=session.skip_rule_id,
                    answered_questions=list(session.answers),
                )
                if session.is_completed
                else None
            ),
            journey_id=session.journey_id,
        )
