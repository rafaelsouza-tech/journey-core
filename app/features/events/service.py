"""Serviço de consulta da trilha de eventos."""

from uuid import UUID

from app.core.exceptions import PatientNotFoundError
from app.features.events.models import Event, EventName
from app.features.events.store import EventStore
from app.features.patients.repository import PatientRepository


class EventService:
    """Resolve o id interno do paciente para o hash e lista a trilha."""

    def __init__(self, patients: PatientRepository, store: EventStore) -> None:
        self._patients = patients
        self._store = store

    def list_for_patient(
        self, patient_id: UUID, event_name: EventName | None = None
    ) -> tuple[str, list[Event]]:
        """
        Trilha do paciente.

        Returns:
            Tupla `(patient_id_hash, eventos)`.

        Raises:
            PatientNotFoundError: se o id interno não existir.
        """
        patient = self._patients.get(patient_id)
        if patient is None:
            raise PatientNotFoundError(patient_id)
        return patient.phone_hash, self._store.list_by_patient_hash(patient.phone_hash, event_name)
