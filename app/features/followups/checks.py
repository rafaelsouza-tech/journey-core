"""
Vocabulário de checks: funções puras sobre o `EligibilityContext`.

Cada check devolve `(observed, details)`. `observed` é comparado com `expect` pelo
engine; `details` vai para o trace. Adicionar uma regra nova ao YAML só exige
código se precisar de um check que ainda não existe aqui.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.features.events.models import EventName
from app.features.journeys.models import JourneyStatus
from app.features.patients.models import ConsentStatus

# Resolução do tempo decorrido no trace (0.01h). O valor é truncado, nunca arredondado:
# arredondar faria 71h59m59s virar 72.0 e liberaria o cooldown antes da hora.
HUNDREDTH_OF_HOUR = timedelta(seconds=36)


@dataclass(frozen=True, slots=True)
class EligibilityContext:
    """Fotografia do paciente no instante da avaliação."""

    now: datetime
    consent_status: ConsentStatus
    has_completed_protocol: bool
    latest_journey_status: JourneyStatus | None
    active_tasks_count: int
    last_event_at: Callable[[EventName], datetime | None]


CheckFn = Callable[[EligibilityContext, Mapping[str, Any]], tuple[Any, dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class CheckSpec:
    """Entrada do vocabulário: a função, os params exigidos e se o observado é numérico."""

    fn: CheckFn
    required_params: tuple[str, ...] = ()
    numeric: bool = False  # só checks numéricos aceitam gt/gte/lt/lte (validado no load)


def _consent_status(ctx: EligibilityContext, _: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
    return ctx.consent_status.value, {}


def _has_completed_protocol(
    ctx: EligibilityContext, _: Mapping[str, Any]
) -> tuple[Any, dict[str, Any]]:
    return ctx.has_completed_protocol, {}


def _latest_journey_status(
    ctx: EligibilityContext, _: Mapping[str, Any]
) -> tuple[Any, dict[str, Any]]:
    status = ctx.latest_journey_status
    return (status.value if status is not None else None), {}


def _active_tasks_count(
    ctx: EligibilityContext, _: Mapping[str, Any]
) -> tuple[Any, dict[str, Any]]:
    return ctx.active_tasks_count, {}


def _hours_since_last_event(
    ctx: EligibilityContext, params: Mapping[str, Any]
) -> tuple[Any, dict[str, Any]]:
    event_name = EventName(params["event_name"])
    last = ctx.last_event_at(event_name)
    if last is None:
        return None, {"event_name": event_name.value, "last_event_at": None, "unit": "hours"}
    hours = ((ctx.now - last) // HUNDREDTH_OF_HOUR) / 100  # inteiro: sem ruído de ponto flutuante
    return hours, {
        "event_name": event_name.value,
        "last_event_at": last.isoformat(),
        "unit": "hours",
    }


CHECKS: dict[str, CheckSpec] = {
    "consent_status": CheckSpec(_consent_status),
    "has_completed_protocol": CheckSpec(_has_completed_protocol),
    "latest_journey_status": CheckSpec(_latest_journey_status),
    "active_tasks_count": CheckSpec(_active_tasks_count, numeric=True),
    "hours_since_last_event": CheckSpec(
        _hours_since_last_event, required_params=("event_name",), numeric=True
    ),
}
