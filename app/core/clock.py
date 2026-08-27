"""
Relógio injetável.

Toda leitura de "agora" passa por aqui. Em produção, `SystemClock`; nos testes,
`FixedClock` — o que torna o cooldown de 72h determinístico sem congelar o tempo
do processo inteiro.
"""

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Porta de leitura de tempo (sempre timezone-aware, UTC)."""

    def now(self) -> datetime:
        """Retorna o instante atual em UTC."""
        ...


class SystemClock:
    """Relógio real do sistema."""

    def now(self) -> datetime:
        """Retorna `datetime.now(UTC)`."""
        return datetime.now(tz=UTC)


class FixedClock:
    """Relógio controlado manualmente (testes e demonstrações)."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = _ensure_utc(start or datetime(2026, 1, 1, 12, 0, tzinfo=UTC))

    def now(self) -> datetime:
        """Retorna o instante fixado."""
        return self._now

    def advance(self, *, hours: float = 0, minutes: float = 0, seconds: float = 0) -> datetime:
        """Avança o relógio e retorna o novo instante."""
        self._now += timedelta(hours=hours, minutes=minutes, seconds=seconds)
        return self._now


def _ensure_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)
