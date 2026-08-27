"""Registro de templates carregados do diretório de JSONs."""

from pathlib import Path

from app.core.exceptions import ConfigurationError, TemplateNotFoundError
from app.features.protocols.models import ProtocolTemplate
from app.shared.declarative import load_documents


class TemplateRegistry:
    """Templates indexados por `template_id`. Falha no boot se o diretório for inválido."""

    def __init__(self, templates: list[ProtocolTemplate]) -> None:
        self._templates: dict[str, ProtocolTemplate] = {}
        for template in templates:
            if template.template_id in self._templates:
                raise ConfigurationError(f"template_id duplicado: {template.template_id}")
            self._templates[template.template_id] = template

    @classmethod
    def load_from_dir(cls, directory: Path) -> "TemplateRegistry":
        """Carrega e valida todos os `*.json` do diretório."""
        return cls(load_documents(directory, ProtocolTemplate))

    def get(self, template_id: str) -> ProtocolTemplate:
        """
        Template pelo id.

        Raises:
            TemplateNotFoundError
        """
        template = self._templates.get(template_id)
        if template is None:
            raise TemplateNotFoundError(template_id)
        return template

    def ids(self) -> list[str]:
        """`template_id`s carregados."""
        return list(self._templates)
