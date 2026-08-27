"""Entidade Paciente e máquina de estados do consentimento."""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID


class Sex(StrEnum):
    """Sexo declarado no cadastro."""

    FEMALE = "female"
    MALE = "male"
    OTHER = "other"


class ConsentStatus(StrEnum):
    """
    Estado do consentimento.

    `pending` → nunca aceitou; `accepted` → ativo; `paused` → tratamento suspenso
    (LGPD art. 18, restrição); `revoked` → terminal, cadastro operacional apagado.
    """

    PENDING = "pending"
    ACCEPTED = "accepted"
    PAUSED = "paused"
    REVOKED = "revoked"


class ConsentAction(StrEnum):
    """Ações do ciclo de vida do consentimento (ver `CONSENT_TRANSITIONS`)."""

    ACCEPT = "accept"
    PAUSE = "pause"
    RESUME = "resume"
    REVOKE = "revoke"


# Tabela de transições: (estado atual, ação) → novo estado. O que não está aqui é inválido.
CONSENT_TRANSITIONS: dict[tuple[ConsentStatus, ConsentAction], ConsentStatus] = {
    (ConsentStatus.PENDING, ConsentAction.ACCEPT): ConsentStatus.ACCEPTED,
    (ConsentStatus.ACCEPTED, ConsentAction.PAUSE): ConsentStatus.PAUSED,
    (ConsentStatus.PAUSED, ConsentAction.RESUME): ConsentStatus.ACCEPTED,
    (ConsentStatus.PENDING, ConsentAction.REVOKE): ConsentStatus.REVOKED,
    (ConsentStatus.ACCEPTED, ConsentAction.REVOKE): ConsentStatus.REVOKED,
    (ConsentStatus.PAUSED, ConsentAction.REVOKE): ConsentStatus.REVOKED,
}

PII_FIELDS: tuple[str, ...] = ("phone", "name", "birth_date")


@dataclass
class Patient:
    """Cadastro operacional. O telefone em claro só existe aqui — e some no `revoke`."""

    id: UUID
    phone_hash: str
    phone: str | None
    name: str | None
    birth_date: date | None
    sex: Sex
    consent_status: ConsentStatus
    terms_accepted_at: datetime | None
    consent_updated_at: datetime
    created_at: datetime
    # Evento `patient_created` deste cadastro: identifica a trilha do paciente. Um mesmo
    # telefone pode ter tido outro cadastro (revogado) — este não enxerga a história daquele.
    trail_start_event_id: UUID

    @property
    def has_active_consent(self) -> bool:
        """Consentimento aceito e não pausado/revogado."""
        return self.consent_status is ConsentStatus.ACCEPTED

    def erase_pii(self) -> list[str]:
        """Apaga os campos de PII do cadastro e devolve os nomes apagados."""
        erased: list[str] = []
        for field_name in PII_FIELDS:
            if getattr(self, field_name) is not None:
                setattr(self, field_name, None)
                erased.append(field_name)
        return erased
