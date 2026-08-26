"""Registro de planos de jornada carregados do diretório de JSONs."""

from pathlib import Path

from app.core.exceptions import ConfigurationError
from app.features.journeys.models import JourneyPlan
from app.shared.declarative import load_documents


class PlanRegistry:
    """Planos indexados por `template_id`."""

    def __init__(self, plans: list[JourneyPlan]) -> None:
        self._plans: dict[str, JourneyPlan] = {}
        for plan in plans:
            if plan.template_id in self._plans:
                raise ConfigurationError(f"plano duplicado para template: {plan.template_id}")
            self._plans[plan.template_id] = plan

    @classmethod
    def load_from_dir(cls, directory: Path) -> "PlanRegistry":
        """Carrega e valida todos os `*.json` do diretório."""
        return cls(load_documents(directory, JourneyPlan))

    def get(self, template_id: str) -> JourneyPlan:
        """
        Plano do template.

        Raises:
            ConfigurationError: template sem plano (validado no boot; aqui é defesa).
        """
        plan = self._plans.get(template_id)
        if plan is None:
            raise ConfigurationError(f"nenhum plano de jornada para o template '{template_id}'")
        return plan

    def ids(self) -> list[str]:
        return list(self._plans)
