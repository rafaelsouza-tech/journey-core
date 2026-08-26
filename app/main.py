"""
Entrypoint da API (app factory).

    uvicorn app.main:create_app --factory

Sem instância global no import: cada `create_app()` monta um container próprio,
o que mantém os testes isolados e a aplicação sem estado compartilhado escondido.
"""

from fastapi import FastAPI

from app import __version__
from app.config import Settings, load_settings
from app.container import build_container
from app.core.clock import Clock
from app.core.handlers import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.features.events.router import router as events_router
from app.features.followups.router import router as followups_router
from app.features.journeys.router import router as journeys_router
from app.features.patients.router import router as patients_router
from app.features.protocols.router import router as protocols_router

logger = get_logger(__name__)

API_DESCRIPTION = """
Núcleo determinístico de uma jornada de saúde: **consentimento**, **protocolo clínico
data-driven** (PHQ-9 com skip logic PHQ-2), **jornada com tarefas**, **trilha de eventos
append-only sem PII** e **elegibilidade de follow-up** por regras declarativas.

Nenhum endpoint envia mensagem, chama LLM ou decide com IA — só regras e dados.
"""

TAGS_METADATA = [
    {"name": "Pacientes", "description": "Cadastro e ciclo de vida do consentimento."},
    {
        "name": "Protocolos",
        "description": "Sessões de protocolo clínico guiadas por template JSON.",
    },
    {"name": "Jornadas", "description": "Plano de ação criado ao concluir o protocolo."},
    {"name": "Follow-ups", "description": "Motor de elegibilidade com regras declarativas."},
    {"name": "Eventos", "description": "Trilha append-only do paciente (pseudonimizada)."},
    {"name": "Saúde", "description": "Liveness."},
]


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    """Monta a aplicação. `settings`/`clock` injetáveis para testes."""
    settings = settings or load_settings()
    configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)

    app = FastAPI(
        title=settings.APP_NAME,
        version=__version__,
        description=API_DESCRIPTION,
        openapi_tags=TAGS_METADATA,
        docs_url="/docs" if settings.DOCS_ENABLED else None,
        redoc_url="/redoc" if settings.DOCS_ENABLED else None,
        openapi_url="/openapi.json" if settings.DOCS_ENABLED else None,
    )
    app.state.container = build_container(settings, clock)

    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)

    app.include_router(patients_router)
    app.include_router(protocols_router)
    app.include_router(journeys_router)
    app.include_router(followups_router)
    app.include_router(events_router)

    @app.get("/health", tags=["Saúde"], summary="Liveness")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "env": settings.APP_ENV.value}

    logger.info("application_ready", env=settings.APP_ENV.value, version=__version__)
    return app
