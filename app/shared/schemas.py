"""Schemas Pydantic compartilhados: base e envelope de erro."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BaseSchema(BaseModel):
    """Base de todos os contratos de API."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        use_enum_values=True,
        str_strip_whitespace=True,
    )


class ErrorDetail(BaseSchema):
    """Detalhe do erro: código estável + mensagem + dados estruturados (sem PII)."""

    code: str = Field(description="Código estável do erro (ex.: CONSENT_REQUIRED)")
    message: str = Field(description="Mensagem legível")
    details: dict[str, Any] | None = Field(default=None, description="Dados estruturados")


class ErrorResponse(BaseSchema):
    """Envelope padrão de erro."""

    success: bool = Field(default=False)
    error: ErrorDetail
    request_id: str | None = Field(default=None, description="Correlação com os logs")
