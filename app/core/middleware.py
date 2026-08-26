"""Middleware de contexto de requisição: request_id + log de acesso sem body."""

import time
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.logging import bind_context, clear_context, get_logger

logger = get_logger("api.request")

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Gera (ou propaga) um `request_id`, liga-o ao contexto de log e o devolve no header.

    Loga apenas método, rota, status e duração — nunca body ou query string.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid4())
        request.state.request_id = request_id
        bind_context(request_id=request_id, method=request.method, path=request.url.path)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            log = logger.warning if response.status_code >= 400 else logger.info
            log("request_completed", status_code=response.status_code, duration_ms=duration_ms)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            clear_context()
