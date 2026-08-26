"""Conversão de estruturas Python para valores JSON-seguros (UUID, datetime, Enum, Path)."""

from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from pathlib import PurePath
from typing import Any
from uuid import UUID


def json_safe(value: Any) -> Any:
    """Devolve uma cópia de `value` contendo apenas tipos serializáveis em JSON."""
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [json_safe(item) for item in value]
    if isinstance(value, Enum):
        return json_safe(value.value)
    if isinstance(value, UUID | PurePath):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value
