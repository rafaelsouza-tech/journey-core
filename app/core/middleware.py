"""Middleware de contexto de requisição: request_id + log de acesso sem body."""

import time
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.routing import Route

from app.core.logging import bind_context, clear_context, get_logger

logger = get_logger("api.request")

REQUEST_ID_HEADER = "X-Request-ID"
UNMATCHED_ROUTE = "<unmatched>"


def route_template(request: Request) -> str:
    """Rota casada (`/patients/{patient_id}`), nunca o path bruto — que pode carregar PII."""
    route = request.scope.get("route")
    return route.path if isinstance(route, Route) else UNMATCHED_ROUTE


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Gera o `request_id`, liga-o ao contexto de log e o devolve no header `X-Request-ID`.

    O id é sempre gerado no servidor: um valor vindo do cliente iria parar em logs e no
    `correlation_id` da trilha imutável. Loga apenas método, rota, status e duração —
    nunca body, query string ou path bruto.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid4())
        request.state.request_id = request_id
        bind_context(request_id=request_id, method=request.method)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            # A resposta 500 é montada fora deste middleware; aqui fica a linha de acesso.
            logger.error(
                "request_failed",
                route=route_template(request),
                error_type=type(exc).__name__,
                duration_ms=_elapsed_ms(started),
            )
            raise
        else:
            log = logger.warning if response.status_code >= 400 else logger.info
            log(
                "request_completed",
                route=route_template(request),
                status_code=response.status_code,
                duration_ms=_elapsed_ms(started),
            )
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            clear_context()


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
