"""Orquestração de sessões de protocolo: iniciar, responder, concluir e disparar a jornada."""

from uuid import UUID, uuid4

from app.core.clock import Clock
from app.core.exceptions import (
    ConfigurationError,
    InvalidAnswerValueError,
    SessionAlreadyCompletedError,
    SessionInProgressError,
    SessionNotFoundError,
    UnexpectedQuestionError,
)
from app.core.logging import get_logger
from app.features.events.models import EventName
from app.features.events.store import EventStore
from app.features.journeys.service import JourneyService
from app.features.patients.models import Patient
from app.features.patients.service import PatientService, require_active_consent
from app.features.protocols.engine import StepOutcome, apply_answer, next_question
from app.features.protocols.loader import TemplateRegistry
from app.features.protocols.models import ProtocolSession, ProtocolTemplate, SessionStatus
from app.features.protocols.repository import ProtocolSessionRepository

logger = get_logger(__name__)


class ProtocolService:
    """Casos de uso do protocolo. O interpretador (engine) é puro; aqui ficam estado e eventos."""

    def __init__(
        self,
        templates: TemplateRegistry,
        sessions: ProtocolSessionRepository,
        patients: PatientService,
        journeys: JourneyService,
        events: EventStore,
        clock: Clock,
    ) -> None:
        self._templates = templates
        self._sessions = sessions
        self._patients = patients
        self._journeys = journeys
        self._events = events
        self._clock = clock

    def start(self, patient_id: UUID, template_id: str) -> tuple[ProtocolSession, ProtocolTemplate]:
        """
        Inicia uma sessão. Exige consentimento ativo; uma sessão em andamento por template.

        Raises:
            PatientNotFoundError, ConsentRequiredError, TemplateNotFoundError,
            SessionInProgressError
        """
        patient = self._patients.get(patient_id)
        require_active_consent(patient)
        template = self._templates.get(template_id)

        existing = self._sessions.find_in_progress(patient_id, template_id)
        if existing is not None:
            raise SessionInProgressError(existing.id, template_id)

        session = ProtocolSession(
            id=uuid4(),
            patient_id=patient_id,
            template_id=template.template_id,
            template_version=template.version,
            status=SessionStatus.IN_PROGRESS,
            started_at=self._clock.now(),
        )
        self._sessions.add(session)
        self._events.append(
            EventName.PROTOCOL_STARTED,
            patient.phone_hash,
            {
                "session_id": session.id,
                "template_id": template.template_id,
                "template_version": template.version,
            },
            trail_id=patient.trail_start_event_id,
        )
        logger.info("protocol_started", session_id=str(session.id), template_id=template_id)
        return session, template

    def get(self, session_id: UUID) -> tuple[ProtocolSession, ProtocolTemplate]:
        """
        Sessão + template na versão pinada no início.

        Se o template carregado tiver outra versão, a sessão não segue com perguntas e
        pontuação de um template diferente do que ela declara: falha explicitamente.

        Raises:
            SessionNotFoundError, ConfigurationError
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        template = self._templates.get(session.template_id)
        if template.version != session.template_version:
            raise ConfigurationError(
                f"sessão pinada na versão {session.template_version} do template "
                f"'{session.template_id}', mas a versão carregada é {template.version}"
            )
        return session, template

    def answer(
        self, session_id: UUID, question_id: str, value: int
    ) -> tuple[ProtocolSession, ProtocolTemplate]:
        """
        Registra a resposta da próxima pergunta esperada. Ao concluir (por skip ou pelo fim),
        emite `protocol_completed` e cria a jornada.

        Raises:
            SessionNotFoundError, SessionAlreadyCompletedError, ConsentRequiredError,
            UnexpectedQuestionError, InvalidAnswerValueError
        """
        session, template = self.get(session_id)
        if session.is_completed:
            raise SessionAlreadyCompletedError(session.id)

        patient = self._patients.get(session.patient_id)
        require_active_consent(patient)

        expected = next_question(template, session.answers)
        if expected is None or expected.id != question_id:
            raise UnexpectedQuestionError(expected.id if expected else "<none>", question_id)
        if value not in template.scale.allowed_values:
            raise InvalidAnswerValueError(template.scale.allowed_values)

        session.answers, outcome = apply_answer(template, session.answers, question_id, value)
        if outcome.completed:
            self._complete(session, template, patient, outcome)
        self._sessions.save(session)
        return session, template

    def _complete(
        self,
        session: ProtocolSession,
        template: ProtocolTemplate,
        patient: Patient,
        outcome: StepOutcome,
    ) -> None:
        """Marca a conclusão, emite `protocol_completed` e cria a jornada."""
        session.status = SessionStatus.COMPLETED
        session.score = outcome.score
        session.ended_by_skip = outcome.ended_by_skip
        session.skip_rule_id = outcome.skip_rule_id
        session.completed_at = self._clock.now()

        # Minimização: o evento carrega o resultado, não as respostas individuais.
        self._events.append(
            EventName.PROTOCOL_COMPLETED,
            patient.phone_hash,
            {
                "session_id": session.id,
                "template_id": template.template_id,
                "template_version": template.version,
                "score": session.score,
                "max_score": template.max_score,
                "ended_by_skip": session.ended_by_skip,
                "skip_rule_id": session.skip_rule_id,
                "answered_count": len(session.answers),
                "total_questions": len(template.questions),
            },
            trail_id=patient.trail_start_event_id,
        )
        # Sem o score no log: resultado clínico fica só no evento, não em plataformas de log.
        logger.info(
            "protocol_completed",
            session_id=str(session.id),
            ended_by_skip=session.ended_by_skip,
            answered_count=len(session.answers),
        )

        journey = self._journeys.create_for_completed_protocol(
            patient, session_id=session.id, template_id=template.template_id
        )
        session.journey_id = journey.id
