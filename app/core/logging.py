"""
Logging estruturado (structlog) com redação de PII.

- `request_id` entra em todo log via contextvars (ligado pelo middleware).
- O processor `redact_pii` substitui chaves proibidas e valores com cara de
  telefone antes da renderização — telefone nunca chega a um log, mesmo por engano.
"""

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from app.core.pii import is_forbidden_key, is_phone_like, redact

_PROTECTED_KEYS = frozenset({"event", "level", "logger", "timestamp", "request_id"})
_HANDLER_TAG = "journey_core_handler"


def redact_pii(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Processor structlog: redige PII no dicionário de log."""
    for key in list(event_dict.keys()):
        if key in _PROTECTED_KEYS:
            if is_phone_like(event_dict[key]):
                event_dict[key] = "[redacted]"
            continue
        event_dict[key] = "[redacted]" if is_forbidden_key(key) else redact(event_dict[key])
    return event_dict


def configure_logging(level: str = "INFO", log_format: str = "console") -> None:
    """Configura structlog + stdlib logging. Idempotente."""
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_pii,
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    )

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.set_name(_HANDLER_TAG)
    root = logging.getLogger()
    # Substitui só o handler desta aplicação; preserva outros (ex.: captura de logs em testes).
    for existing in list(root.handlers):
        if existing.get_name() == _HANDLER_TAG:
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Retorna um logger estruturado."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]


def bind_context(**values: Any) -> None:
    """Liga valores ao contexto de log da requisição atual."""
    structlog.contextvars.bind_contextvars(**values)


def clear_context() -> None:
    """Limpa o contexto de log da requisição atual."""
    structlog.contextvars.clear_contextvars()


def current_request_id() -> str | None:
    """`request_id` da requisição em curso, se houver (usado como correlation_id dos eventos)."""
    value = structlog.contextvars.get_contextvars().get("request_id")
    return str(value) if value is not None else None
