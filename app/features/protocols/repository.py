"""Repositório de sessões de protocolo."""

from uuid import UUID

from app.features.protocols.models import ProtocolSession, SessionStatus
from app.shared.repository import InMemoryRepository


class ProtocolSessionRepository(InMemoryRepository[ProtocolSession]):
    """Sessões por paciente/template."""

    def find_in_progress(self, patient_id: UUID, template_id: str) -> ProtocolSession | None:
        """Sessão em andamento do paciente para o template, se houver."""
        return next(
            (
                session
                for session in self.all()
                if session.patient_id == patient_id
                and session.template_id == template_id
                and session.status is SessionStatus.IN_PROGRESS
            ),
            None,
        )

    def has_completed(self, patient_id: UUID) -> bool:
        """Se o paciente concluiu ao menos um protocolo."""
        return any(
            session.patient_id == patient_id and session.status is SessionStatus.COMPLETED
            for session in self.all()
        )
