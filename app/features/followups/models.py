"""Schema das regras (YAML), resultado por regra e decisão."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SkipReason(StrEnum):
    """Motivos tipados de recusa. O YAML só pode referenciar valores daqui."""

    MISSING_CONSENT = "missing_consent"
    CONSENT_PAUSED = "consent_paused"
    CONSENT_REVOKED = "consent_revoked"
    PROTOCOL_NOT_COMPLETED = "protocol_not_completed"
    NO_ACTIVE_JOURNEY = "no_active_journey"
    NO_ACTIVE_TASK = "no_active_task"
    COOLDOWN = "cooldown"


# Comparadores que exigem um observado numérico (o loader recusa em checks textuais).
ORDERING_OPERATORS: frozenset[str] = frozenset({"gt", "gte", "lt", "lte"})


class Expectation(BaseModel):
    """Exatamente um comparador: equals | not_equals | in | gt | gte | lt | lte."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    equals: Any | None = None
    not_equals: Any | None = None
    in_: list[Any] | None = Field(default=None, alias="in")
    gt: float | None = None
    gte: float | None = None
    lt: float | None = None
    lte: float | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "Expectation":
        if len(self.as_dict()) != 1:
            raise ValueError("expect deve ter exatamente um comparador")
        return self

    def as_dict(self) -> dict[str, Any]:
        """Representação `{comparador: valor}` (usada no trace)."""
        raw = self.model_dump(by_alias=True, exclude_none=True)
        return dict(raw)

    @property
    def operator(self) -> str:
        return next(iter(self.as_dict()))

    @property
    def threshold(self) -> Any:
        return next(iter(self.as_dict().values()))

    def evaluate(self, observed: Any) -> bool:
        """Compara o valor observado com o esperado."""
        op, expected = self.operator, self.threshold
        match op:
            case "equals":
                return bool(observed == expected)
            case "not_equals":
                return bool(observed != expected)
            case "in":
                return observed in expected
            case "gt":
                return bool(observed > expected)
            case "gte":
                return bool(observed >= expected)
            case "lt":
                return bool(observed < expected)
            case "lte":
                return bool(observed <= expected)
        raise ValueError(f"comparador desconhecido: {op}")  # pragma: no cover


class Rule(BaseModel):
    """Uma regra declarativa."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    description: str | None = None
    check: str = Field(description="Nome de um check do vocabulário (checks.py)")
    params: dict[str, Any] = Field(default_factory=dict)
    expect: Expectation
    reason: SkipReason
    reason_by_value: dict[str, SkipReason] = Field(default_factory=dict)
    if_absent: Literal["pass", "fail"] = Field(
        default="fail", description="Resultado quando o check não tem observação (null)"
    )


class RuleSet(BaseModel):
    """Conjunto de regras carregado do YAML."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    template_key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    rules: list[Rule] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> "RuleSet":
        ids = [rule.id for rule in self.rules]
        if len(set(ids)) != len(ids):
            raise ValueError("ids de regras devem ser únicos")
        return self


@dataclass(frozen=True, slots=True)
class RuleResult:
    """Uma linha do trace: o que foi observado, o que era esperado e se passou."""

    rule_id: str
    check: str
    params: dict[str, Any]
    observed: Any
    expected: dict[str, Any]
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Decision:
    """Decisão determinística e explicável."""

    eligible: bool
    reason: SkipReason | None
    template_key: str
    rules_version: int
    evaluated_at: datetime
    trace: list[RuleResult]
