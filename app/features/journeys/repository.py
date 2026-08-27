"""Repositório de jornadas."""

from uuid import UUID

from app.features.journeys.models import Journey
from app.shared.repository import InMemoryRepository


class JourneyRepository(InMemoryRepository[Journey]):
    """Jornadas por paciente."""

    def list_by_patient(self, patient_id: UUID) -> list[Journey]:
        """Jornadas do paciente, na ordem de criação."""
        return self.filter(lambda journey: journey.patient_id == patient_id)

    def latest_for_patient(self, patient_id: UUID) -> Journey | None:
        """Jornada mais recente do paciente (a última criada)."""
        journeys = self.list_by_patient(patient_id)
        return journeys[-1] if journeys else None
