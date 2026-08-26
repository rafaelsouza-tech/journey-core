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
from app.features.events.store import EventStore, InMemoryEventStore
from app.features.patients.repository import PatientRepository


@dataclass(slots=True)
class Container:
    """Dependências com estado da aplicação."""

    settings: Settings
    clock: Clock
    events: EventStore
    patients: PatientRepository


def build_container(settings: Settings, clock: Clock | None = None) -> Container:
    """Constrói o container. `clock` injetável para testes determinísticos."""
    clock = clock or SystemClock()
    return Container(
        settings=settings,
        clock=clock,
        events=InMemoryEventStore(clock),
        patients=PatientRepository(),
    )


def get_container(request: Request) -> Container:
    """Dependência FastAPI: container da aplicação em curso."""
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]
