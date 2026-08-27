"""Serviço de consulta da trilha de eventos."""

from uuid import UUID

from app.features.events.models import Event, EventName
from app.features.events.store import EventStore
from app.features.patients.service import PatientService


class EventService:
    """Resolve o id interno do paciente para o hash e lista a trilha daquele cadastro."""

    def __init__(self, patients: PatientService, store: EventStore) -> None:
        self._patients = patients
        self._store = store

    def list_for_patient(
        self, patient_id: UUID, event_name: EventName | None = None
    ) -> tuple[str, list[Event]]:
        """
        Trilha do cadastro: do seu `patient_created` em diante.

        Returns:
            Tupla `(patient_id_hash, eventos)`.

        Raises:
            PatientNotFoundError: se o id interno não existir.
        """
        patient = self._patients.get(patient_id)
        events = self._store.list_by_patient_hash(
            patient.phone_hash, event_name, trail_id=patient.trail_start_event_id
        )
        return patient.phone_hash, events
