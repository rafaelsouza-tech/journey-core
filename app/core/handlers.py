"""
Handlers de exceção → envelope `ErrorResponse`.

Regra de ouro: nenhuma resposta de erro ecoa a entrada do cliente. O handler de
validação devolve só campo + mensagem (nunca `input`/`ctx` do Pydantic) — e limpa
da mensagem os trechos da entrada que o próprio Pydantic cita.
"""

import re
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import JourneyCoreError
from app.core.logging import get_logger
from app.core.middleware import REQUEST_ID_HEADER
from app.core.pii import is_phone_like
from app.shared.schemas import ErrorDetail, ErrorResponse

logger = get_logger(__name__)

# Trechos da entrada que o Pydantic cita entre crases (ex.: "found `+` at 1").
_QUOTED_FRAGMENT = re.compile(r"`[^`]*`")
_LOCATION_PREFIXES = ("body", "query", "path")


def _envelope(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message, details=details), request_id=request_id
    )
    response = JSONResponse(
        status_code=status_code, content=body.model_dump(mode="json"), headers=headers
    )
    if request_id is not None:
        # Também nos erros tratados fora do middleware (exceções não previstas).
        response.headers[REQUEST_ID_HEADER] = request_id
    return response


def _safe_field(error: Mapping[str, Any]) -> str:
    """Nome do campo a partir do `loc`; JSON malformado é erro do corpo inteiro."""
    if error.get("type") == "json_invalid":
        return "__root__"  # o `loc` traz a posição do byte, que não é um campo
    loc = [str(part) for part in error.get("loc", ()) if part not in _LOCATION_PREFIXES]
    return ".".join(loc) or "__root__"


def _safe_message(error: Mapping[str, Any]) -> str:
    """Mensagem do Pydantic sem trechos citados da entrada nem nada com cara de telefone."""
    message = _QUOTED_FRAGMENT.sub("`…`", str(error.get("msg", "inválido")))
    return "[redacted]" if is_phone_like(message) else message


async def journey_core_error_handler(request: Request, exc: JourneyCoreError) -> JSONResponse:
    """Exceções de domínio: status/código/details vêm da própria exceção."""
    if exc.status_code >= 500:
        logger.error("domain_error", error_code=exc.error_code, details=exc.details)
    return _envelope(request, exc.status_code, exc.error_code, exc.message, exc.details or None)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Erros de validação do Pydantic (body, query ou path) sem ecoar o valor recebido."""
    field_errors: dict[str, list[str]] = {}
    for error in exc.errors():
        field_errors.setdefault(_safe_field(error), []).append(_safe_message(error))
    return _envelope(
        request,
        422,
        "VALIDATION_ERROR",
        "Falha de validação da requisição",
        {"field_errors": field_errors},
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Exceções HTTP do próprio framework (404 de rota, 405 com `Allow` etc.)."""
    return _envelope(request, exc.status_code, "HTTP_ERROR", str(exc.detail), headers=exc.headers)


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Qualquer outra exceção: loga com stack e devolve 500 genérico."""
    # Roda fora do middleware (ServerErrorMiddleware), com o contexto de log já limpo:
    # o request_id vai explícito — é o caso em que a correlação mais importa.
    logger.exception(
        "unhandled_exception",
        error_type=type(exc).__name__,
        request_id=getattr(request.state, "request_id", None),
        method=request.method,
        path=request.url.path,
    )
    return _envelope(request, 500, "INTERNAL_ERROR", "Erro interno")


def register_exception_handlers(app: FastAPI) -> None:
    """Registra todos os handlers na aplicação."""
    app.add_exception_handler(JourneyCoreError, journey_core_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
