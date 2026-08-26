"""Plano de jornada (JSON) e entidades Jornada/Tarefa."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlanTask(BaseModel):
    # `str_strip_whitespace`: um título só de espaços não passa pelo `min_length`.
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)


class JourneyPlan(BaseModel):
    """Plano declarativo por `template_id`: objetivo + tarefas iniciais."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    template_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: int = Field(ge=1)
    objective: str = Field(min_length=1)
    tasks: list[PlanTask] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_keys(self) -> "JourneyPlan":
        keys = [task.key for task in self.tasks]
        if len(set(keys)) != len(keys):
            raise ValueError("keys das tarefas devem ser únicas")
        return self


class JourneyStatus(StrEnum):
    """Valores literais do enunciado."""

    EM_ANDAMENTO = "em_andamento"
    CONCLUIDA = "concluida"


class TaskStatus(StrEnum):
    EM_ANDAMENTO = "em_andamento"
    CONCLUIDA = "concluida"


@dataclass
class Task:
    id: UUID
    key: str
    title: str
    status: TaskStatus = TaskStatus.EM_ANDAMENTO
    completed_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.status is TaskStatus.EM_ANDAMENTO


@dataclass
class Journey:
    id: UUID
    patient_id: UUID
    source_session_id: UUID
    template_id: str
    plan_version: int
    objective: str
    created_at: datetime
    status: JourneyStatus = JourneyStatus.EM_ANDAMENTO
    tasks: list[Task] = field(default_factory=list)
    completed_at: datetime | None = None

    @property
    def active_tasks(self) -> list[Task]:
        return [task for task in self.tasks if task.is_active]

    @property
    def is_active(self) -> bool:
        return self.status is JourneyStatus.EM_ANDAMENTO

    def find_task(self, task_id: UUID) -> Task | None:
        return next((task for task in self.tasks if task.id == task_id), None)
