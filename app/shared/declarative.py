"""Carga e validação de artefatos declarativos (JSON/YAML) com erro de configuração legível."""

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from app.core.exceptions import ConfigurationError


def _summarize(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc']) or '<raiz>'}: {err['msg']}"
        for err in exc.errors()
    )


def load_document[T: BaseModel](path: Path, model: type[T]) -> T:
    """
    Lê um arquivo JSON ou YAML e valida contra `model`.

    Raises:
        ConfigurationError: arquivo ausente, sintaxe inválida ou conteúdo fora do schema.
    """
    if not path.is_file():
        raise ConfigurationError(f"Artefato declarativo não encontrado: {path}")
    try:
        raw: Any = (
            yaml.safe_load(path.read_text(encoding="utf-8"))
            if path.suffix in {".yaml", ".yml"}
            else json.loads(path.read_text(encoding="utf-8"))
        )
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Sintaxe inválida em {path.name}: {exc}") from exc
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise ConfigurationError(f"{path.name} inválido: {_summarize(exc)}") from exc


def load_documents[T: BaseModel](
    directory: Path, model: type[T], pattern: str = "*.json"
) -> list[T]:
    """Carrega todos os arquivos `pattern` de `directory`, em ordem alfabética."""
    if not directory.is_dir():
        raise ConfigurationError(f"Diretório de artefatos não encontrado: {directory}")
    documents = [load_document(path, model) for path in sorted(directory.glob(pattern))]
    if not documents:
        raise ConfigurationError(f"Nenhum arquivo {pattern} em {directory}")
    return documents
