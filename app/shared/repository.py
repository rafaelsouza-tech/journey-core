"""
Repositório in-memory genérico.

Um `dict` por agregado, chaveado pelo `id`. A interface (add/get/save/all) é a
mesma que um repositório Firestore exporia — trocar a persistência não toca os services.
"""

from collections.abc import Callable
from typing import Protocol
from uuid import UUID


class HasId(Protocol):
    """Qualquer entidade com identificador `id: UUID`."""

    @property
    def id(self) -> UUID: ...


class InMemoryRepository[T: HasId]:
    """Armazena entidades em memória, preservando ordem de inserção."""

    def __init__(self) -> None:
        self._items: dict[UUID, T] = {}

    def add(self, entity: T) -> T:
        """Insere uma entidade nova. Falha se o id já existir."""
        if entity.id in self._items:
            raise KeyError(f"entidade {entity.id} já existe")
        self._items[entity.id] = entity
        return entity

    def get(self, entity_id: UUID) -> T | None:
        """Busca por id; `None` se não existir."""
        return self._items.get(entity_id)

    def save(self, entity: T) -> T:
        """Persiste o estado atual de uma entidade existente."""
        self._items[entity.id] = entity
        return entity

    def all(self) -> list[T]:
        """Todas as entidades, na ordem de inserção."""
        return list(self._items.values())

    def filter(self, predicate: Callable[[T], bool]) -> list[T]:
        """Entidades que satisfazem o predicado, na ordem de inserção."""
        return [item for item in self._items.values() if predicate(item)]
