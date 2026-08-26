import json
from pathlib import Path

import pytest

from app.config import Settings
from app.features.protocols.engine import (
    apply_answer,
    compute_score,
    evaluate_condition,
    next_question,
    triggered_skip_rule,
)
from app.features.protocols.models import Condition, ProtocolTemplate

pytestmark = pytest.mark.unit

TEMPLATES_DIR = Settings.model_fields["PROTOCOL_TEMPLATES_DIR"].default


@pytest.fixture(scope="module")
def phq9() -> ProtocolTemplate:
    raw = json.loads(Path(TEMPLATES_DIR, "phq9.json").read_text(encoding="utf-8"))
    return ProtocolTemplate.model_validate(raw)


def test_phq2_skip_when_sum_of_first_two_is_below_3(phq9: ProtocolTemplate) -> None:
    answers, first = apply_answer(phq9, {}, "q1", 1)
    assert not first.completed and first.next_question is not None
    assert first.next_question.id == "q2"

    answers, outcome = apply_answer(phq9, answers, "q2", 1)

    assert outcome.completed is True
    assert outcome.ended_by_skip is True
    assert outcome.skip_rule_id == "phq2_gate"
    assert outcome.score == 2  # pontuação parcial: só a soma do que foi respondido
    assert outcome.next_question is None
    assert answers == {"q1": 1, "q2": 1}


@pytest.mark.parametrize(("q1", "q2"), [(2, 1), (1, 2), (3, 0), (0, 3), (3, 3)])
def test_continues_to_q3_when_sum_is_3_or_more(phq9: ProtocolTemplate, q1: int, q2: int) -> None:
    answers, _ = apply_answer(phq9, {}, "q1", q1)
    _, outcome = apply_answer(phq9, answers, "q2", q2)

    assert outcome.completed is False
    assert outcome.ended_by_skip is False
    assert outcome.next_question is not None
    assert outcome.next_question.id == "q3"


def test_skip_rule_is_only_evaluated_after_its_anchor_question(phq9: ProtocolTemplate) -> None:
    assert triggered_skip_rule(phq9, {"q1": 0}, "q1") is None


def test_full_run_scores_the_plain_sum(phq9: ProtocolTemplate) -> None:
    values = [2, 1, 0, 3, 1, 2, 0, 1, 3]
    answers: dict[str, int] = {}
    outcome = None
    for value in values:
        question = next_question(phq9, answers)
        assert question is not None
        answers, outcome = apply_answer(phq9, answers, question.id, value)

    assert outcome is not None
    assert outcome.completed is True
    assert outcome.ended_by_skip is False
    assert outcome.score == sum(values) == 13
    assert compute_score(phq9, answers) == 13
    assert phq9.max_score == 27


@pytest.mark.parametrize(
    ("op", "left", "right", "expected"),
    [
        ("lt", 2, 3, True),
        ("lt", 3, 3, False),
        ("lte", 3, 3, True),
        ("gt", 4, 3, True),
        ("gte", 3, 3, True),
        ("eq", 3, 3, True),
        ("ne", 3, 3, False),
    ],
)
def test_condition_operators(op: str, left: int, right: int, expected: bool) -> None:
    condition = Condition.model_validate({"op": op, "left": left, "right": right})
    assert evaluate_condition(condition, {}) is expected


def test_condition_operands_sum_of_and_answer_ref() -> None:
    answers = {"a": 1, "b": 2, "c": 5}
    sum_cond = Condition.model_validate({"op": "eq", "left": {"sum_of": ["a", "b"]}, "right": 3})
    ref_cond = Condition.model_validate(
        {"op": "gt", "left": {"answer": "c"}, "right": {"answer": "b"}}
    )
    assert evaluate_condition(sum_cond, answers)
    assert evaluate_condition(ref_cond, answers)


def test_engine_is_generic_and_works_with_any_template() -> None:
    """Um template fictício de 3 perguntas com regra própria roda no mesmo interpretador."""
    template = ProtocolTemplate.model_validate(
        {
            "template_id": "mini",
            "version": 1,
            "name": "Mini",
            "intro": "Pergunta-guia",
            "scale": {"options": [{"value": 0, "label": "não"}, {"value": 1, "label": "sim"}]},
            "questions": [
                {"id": "a", "order": 1, "text": "A?"},
                {"id": "b", "order": 2, "text": "B?"},
                {"id": "c", "order": 3, "text": "C?"},
            ],
            "skip_rules": [
                {
                    "id": "stop_if_a_is_no",
                    "after_question": "a",
                    "condition": {"op": "eq", "left": {"answer": "a"}, "right": 0},
                    "action": "end_block",
                }
            ],
        }
    )
    _, skipped = apply_answer(template, {}, "a", 0)
    assert skipped.completed and skipped.ended_by_skip and skipped.skip_rule_id == "stop_if_a_is_no"

    answers, continued = apply_answer(template, {}, "a", 1)
    assert not continued.completed and continued.next_question is not None
    assert continued.next_question.id == "b"
    answers, _ = apply_answer(template, answers, "b", 1)
    _, last = apply_answer(template, answers, "c", 0)
    assert last.completed and not last.ended_by_skip and last.score == 2
