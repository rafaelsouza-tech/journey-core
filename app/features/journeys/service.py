"""Regras de negócio da jornada: criação a partir do protocolo concluído e conclusão de tarefas."""

from uuid import UUID, uuid4

from app.core.clock import Clock
from app.core.exceptions import JourneyNotFoundError, TaskAlreadyCompletedError, TaskNotFoundError
from app.core.logging import get_logger
from app.features.events.models import EventName
from app.features.events.store import EventStore
from app.features.journeys.loader import PlanRegistry
from app.features.journeys.models import Journey, JourneyStatus, Task, TaskStatus
from app.features.journeys.repository import JourneyRepository
from app.features.patients.models import Patient
from app.features.patients.service import PatientService, require_active_consent
from app.features.protocols.models import ProtocolSession

logger = get_logger(__name__)


class JourneyService:
    """Jornadas nascem só aqui, e só a partir de um protocolo concluído — não há endpoint de criação."""

    def __init__(
        self,
        plans: PlanRegistry,
        journeys: JourneyRepository,
        patients: PatientService,
        events: EventStore,
        clock: Clock,
    ) -> None:
        self._plans = plans
        self._journeys = journeys
        self._patients = patients
        self._events = events
        self._clock = clock

    def create_for_completed_protocol(self, patient: Patient, session: ProtocolSession) -> Journey:
        """Instancia o plano do template como jornada do paciente e emite `journey_created`."""
        plan = self._plans.get(session.template_id)
        journey = Journey(
            id=uuid4(),
            patient_id=patient.id,
            source_session_id=session.id,
            template_id=session.template_id,
            plan_version=plan.version,
            objective=plan.objective,
            created_at=self._clock.now(),
            tasks=[Task(id=uuid4(), key=task.key, title=task.title) for task in plan.tasks],
        )
        self._journeys.add(journey)
        self._events.append(
            EventName.JOURNEY_CREATED,
            patient.phone_hash,
            {
                "journey_id": journey.id,
                "source_session_id": session.id,
                "template_id": session.template_id,
                "plan_version": plan.version,
                "task_count": len(journey.tasks),
            },
        )
        logger.info("journey_created", journey_id=str(journey.id), task_count=len(journey.tasks))
        return journey

    def get(self, journey_id: UUID) -> Journey:
        """
        Jornada pelo id.

        Raises:
            JourneyNotFoundError
        """
        journey = self._journeys.get(journey_id)
        if journey is None:
            raise JourneyNotFoundError(journey_id)
        return journey

    def list_for_patient(self, patient_id: UUID) -> list[Journey]:
        """Jornadas do paciente (valida existência do paciente)."""
        self._patients.get(patient_id)
        return self._journeys.list_by_patient(patient_id)

    def complete_task(self, journey_id: UUID, task_id: UUID) -> Journey:
        """
        Marca a tarefa como concluída; quando for a última, conclui a jornada.

        Raises:
            JourneyNotFoundError, TaskNotFoundError, ConsentRequiredError, TaskAlreadyCompletedError
        """
        journey = self.get(journey_id)
        task = journey.find_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)

        patient = self._patients.get(journey.patient_id)
        require_active_consent(patient)

        if not task.is_active:
            raise TaskAlreadyCompletedError(task.id)

        now = self._clock.now()
        task.status = TaskStatus.CONCLUIDA
        task.completed_at = now
        remaining = len(journey.active_tasks)
        self._events.append(
            EventName.TASK_COMPLETED,
            patient.phone_hash,
            {
                "journey_id": journey.id,
                "task_id": task.id,
                "task_key": task.key,
                "remaining_tasks": remaining,
            },
        )

        if remaining == 0:
            journey.status = JourneyStatus.CONCLUIDA
            journey.completed_at = now
            self._events.append(
                EventName.JOURNEY_COMPLETED, patient.phone_hash, {"journey_id": journey.id}
            )

        self._journeys.save(journey)
        logger.info("task_completed", journey_id=str(journey.id), remaining_tasks=remaining)
        return journey
