"""
Interpretador genérico do template.

Funções puras sobre (template, respostas). Nada aqui conhece um protocolo
específico: a skip logic é avaliada a partir das `skip_rules` do JSON com um
mini-vocabulário de operandos (`sum_of`, `answer`, literal) e operadores
(`lt/lte/gt/gte/eq/ne`).
"""

import operator
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from app.features.protocols.models import (
    AnswerRef,
    Condition,
    Operand,
    ProtocolTemplate,
    Question,
    SkipAction,
    SkipRule,
    SumOf,
)

_OPERATORS: dict[str, Callable[[int, int], bool]] = {
    "lt": operator.lt,
    "lte": operator.le,
    "gt": operator.gt,
    "gte": operator.ge,
    "eq": operator.eq,
    "ne": operator.ne,
}


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """Resultado de aplicar uma resposta."""

    completed: bool
    ended_by_skip: bool
    skip_rule_id: str | None
    next_question: Question | None
    score: int


def resolve_operand(operand: Operand, answers: Mapping[str, int]) -> int:
    """Valor numérico de um operando dado o conjunto de respostas."""
    if isinstance(operand, int):
        return operand
    if isinstance(operand, SumOf):
        return sum(answers[question_id] for question_id in operand.sum_of)
    if isinstance(operand, AnswerRef):
        return answers[operand.answer]
    raise TypeError(f"operando não suportado: {operand!r}")  # pragma: no cover


def evaluate_condition(condition: Condition, answers: Mapping[str, int]) -> bool:
    """Avalia `left <op> right`."""
    left = resolve_operand(condition.left, answers)
    right = resolve_operand(condition.right, answers)
    return _OPERATORS[condition.op](left, right)


def triggered_skip_rule(
    template: ProtocolTemplate, answers: Mapping[str, int], answered_question_id: str
) -> SkipRule | None:
    """Primeira skip rule ancorada na pergunta recém-respondida cuja condição é verdadeira."""
    for rule in template.skip_rules:
        if rule.after_question == answered_question_id and evaluate_condition(
            rule.condition, answers
        ):
            return rule
    return None


def compute_score(template: ProtocolTemplate, answers: Mapping[str, int]) -> int:
    """Pontuação pelo método do template (só `sum` — sem índices compostos)."""
    if template.scoring.method == "sum":
        return sum(answers.values())
    raise ValueError(
        f"método de scoring desconhecido: {template.scoring.method}"
    )  # pragma: no cover


def next_question(template: ProtocolTemplate, answers: Mapping[str, int]) -> Question | None:
    """Primeira pergunta, em ordem, ainda sem resposta."""
    return next((q for q in template.ordered_questions if q.id not in answers), None)


def apply_answer(
    template: ProtocolTemplate, answers: Mapping[str, int], question_id: str, value: int
) -> tuple[dict[str, int], StepOutcome]:
    """
    Registra uma resposta e decide o próximo passo. Puro: devolve novas respostas + outcome.

    Pressupõe que `question_id` é a próxima pergunta esperada e `value` está na escala
    (o serviço valida isso e levanta os erros tipados).
    """
    updated = {**answers, question_id: value}
    rule = triggered_skip_rule(template, updated, question_id)
    if rule is not None and rule.action is SkipAction.END_BLOCK:
        return updated, StepOutcome(
            completed=True,
            ended_by_skip=True,
            skip_rule_id=rule.id,
            next_question=None,
            score=compute_score(template, updated),
        )
    following = next_question(template, updated)
    return updated, StepOutcome(
        completed=following is None,
        ended_by_skip=False,
        skip_rule_id=None,
        next_question=following,
        score=compute_score(template, updated),
    )
