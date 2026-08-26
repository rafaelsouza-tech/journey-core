"""
Raiz de composição.

Tudo que tem estado (repositórios, event store, registries, relógio) nasce aqui,
uma vez por aplicação, e fica em `app.state.container`. Os `dependencies.py` de
cada feature montam seus services a partir dele — os services em si são puros.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from app.config import Settings
from app.core.clock import Clock, SystemClock
from app.core.exceptions import ConfigurationError
from app.features.events.store import EventStore, InMemoryEventStore
from app.features.followups.loader import load_ruleset
from app.features.followups.models import RuleSet
from app.features.journeys.loader import PlanRegistry
from app.features.journeys.repository import JourneyRepository
from app.features.patients.repository import PatientRepository
from app.features.protocols.loader import TemplateRegistry
from app.features.protocols.repository import ProtocolSessionRepository


@dataclass(slots=True)
class Container:
    """Dependências com estado da aplicação."""

    settings: Settings
    clock: Clock
    events: EventStore
    patients: PatientRepository
    sessions: ProtocolSessionRepository
    journeys: JourneyRepository
    templates: TemplateRegistry
    plans: PlanRegistry
    rules: RuleSet


def build_container(settings: Settings, clock: Clock | None = None) -> Container:
    """Constrói o container. `clock` injetável para testes determinísticos."""
    clock = clock or SystemClock()
    templates = TemplateRegistry.load_from_dir(settings.PROTOCOL_TEMPLATES_DIR)
    plans = PlanRegistry.load_from_dir(settings.JOURNEY_PLANS_DIR)
    _ensure_every_template_has_a_plan(templates, plans)
    rules = load_ruleset(settings.FOLLOWUP_RULES_PATH)
    return Container(
        settings=settings,
        clock=clock,
        events=InMemoryEventStore(clock),
        patients=PatientRepository(),
        sessions=ProtocolSessionRepository(),
        journeys=JourneyRepository(),
        templates=templates,
        plans=plans,
        rules=rules,
    )


def _ensure_every_template_has_a_plan(templates: TemplateRegistry, plans: PlanRegistry) -> None:
    """Falha no boot — não no meio de um protocolo — se faltar plano para algum template."""
    missing = sorted(set(templates.ids()) - set(plans.ids()))
    if missing:
        raise ConfigurationError(f"templates sem plano de jornada: {', '.join(missing)}")


def get_container(request: Request) -> Container:
    """Dependência FastAPI: container da aplicação em curso."""
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]
