"""Monta o `EligibilityContext` a partir do estado atual dos repositórios."""

from datetime import datetime

from app.core.clock import Clock
from app.features.events.models import EventName
from app.features.events.store import EventStore
from app.features.followups.checks import EligibilityContext
from app.features.journeys.repository import JourneyRepository
from app.features.patients.models import Patient
from app.features.protocols.repository import ProtocolSessionRepository


def build_context(
    patient: Patient,
    sessions: ProtocolSessionRepository,
    journeys: JourneyRepository,
    events: EventStore,
    clock: Clock,
) -> EligibilityContext:
    """Fotografia do paciente para o motor de regras."""
    latest = journeys.latest_for_patient(patient.id)

    def last_event_at(event_name: EventName) -> datetime | None:
        event = events.last_by_name(
            patient.phone_hash, event_name, trail_id=patient.trail_start_event_id
        )
        return event.occurred_at if event is not None else None

    return EligibilityContext(
        now=clock.now(),
        consent_status=patient.consent_status,
        has_completed_protocol=sessions.has_completed(patient.id),
        latest_journey_status=latest.status if latest is not None else None,
        active_tasks_count=len(latest.active_tasks) if latest is not None else 0,
        last_event_at=last_event_at,
    )
