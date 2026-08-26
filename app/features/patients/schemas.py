"""Contratos de API de pacientes."""

from datetime import UTC, date, datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.core.hashing import normalize_phone
from app.features.patients.models import ConsentStatus, Sex
from app.shared.schemas import BaseSchema


class PatientCreateRequest(BaseSchema):
    """Cadastro de paciente."""

    phone: str = Field(
        description="Telefone com DDI/DDD (10–15 dígitos). Persistido apenas como hash.",
        examples=["+55 11 90000-0000"],
    )
    name: str = Field(min_length=2, max_length=120, examples=["Maria Exemplo"])
    birth_date: date = Field(examples=["1990-05-20"])
    sex: Sex = Field(examples=["female"])
    terms_accepted: bool = Field(description="Aceite dos termos (LGPD)", examples=[True])

    @field_validator("phone")
    @classmethod
    def _normalize_phone(cls, value: str) -> str:
        # A mensagem de erro de normalize_phone nunca inclui o valor recebido.
        return normalize_phone(value)

    @field_validator("birth_date")
    @classmethod
    def _birth_date_in_past(cls, value: date) -> date:
        if value > datetime.now(tz=UTC).date():
            raise ValueError("data de nascimento não pode estar no futuro")
        return value


class PatientResponse(BaseSchema):
    """Cadastro como exposto na API — **nunca** inclui o telefone em claro."""

    id: UUID
    phone_hash: str = Field(description="HMAC-SHA256 do telefone (correlaciona com /events)")
    name: str | None = Field(description="Nulo após revogação do consentimento")
    birth_date: date | None = Field(description="Nulo após revogação do consentimento")
    sex: Sex
    consent_status: ConsentStatus
    terms_accepted_at: datetime | None
    consent_updated_at: datetime
    created_at: datetime
