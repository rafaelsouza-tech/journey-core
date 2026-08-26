"""
Handlers de exceção → envelope `ErrorResponse`.

Regra de ouro: nenhuma resposta de erro ecoa a entrada do cliente. O handler de
validação devolve só campo + mensagem (nunca `input`/`ctx` do Pydantic).
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import JourneyCoreError
from app.core.logging import get_logger
from app.shared.schemas import ErrorDetail, ErrorResponse

logger = get_logger(__name__)


def _envelope(
    request: Request, status_code: int, code: str, message: str, details: Any = None
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=message, details=details),
        request_id=getattr(request.state, "request_id", None),
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


async def journey_core_error_handler(request: Request, exc: JourneyCoreError) -> JSONResponse:
    """Exceções de domínio: status/código/details vêm da própria exceção."""
    if exc.status_code >= 500:
        logger.error("domain_error", error_code=exc.error_code, details=exc.details)
    return _envelope(request, exc.status_code, exc.error_code, exc.message, exc.details or None)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Erros de validação do Pydantic sem ecoar o valor recebido."""
    field_errors: dict[str, list[str]] = {}
    for error in exc.errors():
        loc = [str(part) for part in error.get("loc", ()) if part not in ("body", "query", "path")]
        field = ".".join(loc) or "__root__"
        field_errors.setdefault(field, []).append(str(error.get("msg", "inválido")))
    return _envelope(
        request,
        422,
        "VALIDATION_ERROR",
        "Falha de validação da requisição",
        {"field_errors": field_errors},
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Exceções HTTP do próprio framework (404 de rota, 405 etc.)."""
    return _envelope(request, exc.status_code, "HTTP_ERROR", str(exc.detail))


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Qualquer outra exceção: loga com stack e devolve 500 genérico."""
    logger.exception("unhandled_exception", error_type=type(exc).__name__)
    return _envelope(request, 500, "INTERNAL_ERROR", "Erro interno")


def register_exception_handlers(app: FastAPI) -> None:
    """Registra todos os handlers na aplicação."""
    app.add_exception_handler(JourneyCoreError, journey_core_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)
