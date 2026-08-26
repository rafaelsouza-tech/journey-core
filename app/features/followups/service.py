"""Avaliação de elegibilidade: decide, registra o evento, não envia nada."""

from uuid import UUID

from app.core.clock import Clock
from app.core.logging import get_logger
from app.features.events.models import Event, EventName
from app.features.events.store import EventStore
from app.features.followups.context import build_context
from app.features.followups.engine import evaluate, trace_as_json
from app.features.followups.models import Decision, RuleSet
from app.features.journeys.repository import JourneyRepository
from app.features.patients.service import PatientService
from app.features.protocols.repository import ProtocolSessionRepository

logger = get_logger(__name__)


class FollowupService:
    """Aplica o conjunto de regras ao paciente e emite `followup_eligible` ou `followup_skipped`."""

    def __init__(
        self,
        rules: RuleSet,
        patients: PatientService,
        sessions: ProtocolSessionRepository,
        journeys: JourneyRepository,
        events: EventStore,
        clock: Clock,
    ) -> None:
        self._rules = rules
        self._patients = patients
        self._sessions = sessions
        self._journeys = journeys
        self._events = events
        self._clock = clock

    def evaluate(self, patient_id: UUID) -> tuple[Decision, Event]:
        """
        Decide e registra. Nenhuma mensagem é enviada — só a decisão e o evento.

        Raises:
            PatientNotFoundError
        """
        patient = self._patients.get(patient_id)
        context = build_context(patient, self._sessions, self._journeys, self._events, self._clock)
        decision = evaluate(context, self._rules)

        properties = {
            "template_key": decision.template_key,
            "rules_version": decision.rules_version,
            "trace": trace_as_json(decision),
        }
        if decision.eligible:
            event = self._events.append(EventName.FOLLOWUP_ELIGIBLE, patient.phone_hash, properties)
        else:
            event = self._events.append(
                EventName.FOLLOWUP_SKIPPED,
                patient.phone_hash,
                {"reason": decision.reason, **properties},
            )

        logger.info(
            "followup_evaluated",
            patient_id=str(patient.id),
            eligible=decision.eligible,
            reason=decision.reason.value if decision.reason else None,
        )
        return decision, event
