"""Middleware de contexto de requisição: request_id + log de acesso sem body."""

import re
import time
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.logging import bind_context, clear_context, get_logger
from app.core.pii import is_phone_like

logger = get_logger("api.request")

REQUEST_ID_HEADER = "X-Request-ID"

# O request_id vindo do cliente vai para logs, envelope de erro e `correlation_id` da
# trilha: só se propaga se for curto e sem espaços/markup — e sem cara de telefone.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def incoming_request_id(request: Request) -> str | None:
    """Request id do header, se for seguro propagá-lo; senão `None` (gera-se um novo)."""
    value = request.headers.get(REQUEST_ID_HEADER)
    if value is None or not _SAFE_REQUEST_ID.fullmatch(value) or is_phone_like(value):
        return None
    return value


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Gera (ou propaga) um `request_id`, liga-o ao contexto de log e o devolve no header.

    Loga apenas método, rota, status e duração — nunca body ou query string.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = incoming_request_id(request) or str(uuid4())
        request.state.request_id = request_id
        bind_context(request_id=request_id, method=request.method, path=request.url.path)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            # A resposta 500 é montada fora deste middleware; aqui fica a linha de acesso.
            logger.error(
                "request_failed", error_type=type(exc).__name__, duration_ms=_elapsed_ms(started)
            )
            raise
        else:
            log = logger.warning if response.status_code >= 400 else logger.info
            log(
                "request_completed",
                status_code=response.status_code,
                duration_ms=_elapsed_ms(started),
            )
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            clear_context()


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
