"""Repositório de pacientes (in-memory) com índice por `phone_hash`."""

from uuid import UUID

from app.features.patients.models import Patient
from app.shared.repository import InMemoryRepository


class PatientRepository(InMemoryRepository[Patient]):
    """Pacientes por id e por hash de telefone."""

    def __init__(self) -> None:
        super().__init__()
        self._by_hash: dict[str, UUID] = {}

    def add(self, entity: Patient) -> Patient:
        """Insere e indexa pelo hash."""
        super().add(entity)
        self._by_hash[entity.phone_hash] = entity.id
        return entity

    def get_by_phone_hash(self, phone_hash: str) -> Patient | None:
        """Paciente com o hash informado, se existir."""
        patient_id = self._by_hash.get(phone_hash)
        return self.get(patient_id) if patient_id is not None else None
