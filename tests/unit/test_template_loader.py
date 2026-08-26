import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings
from app.core.exceptions import ConfigurationError, TemplateNotFoundError
from app.features.protocols.loader import TemplateRegistry

pytestmark = pytest.mark.unit

TEMPLATES_DIR = Settings.model_fields["PROTOCOL_TEMPLATES_DIR"].default

# Transcrição literal da seção 4 do enunciado.
PHQ9_ITEMS = [
    "Pouco interesse ou prazer em fazer as coisas",
    "Sentir-se para baixo, deprimido(a) ou sem esperança",
    "Dificuldade para pegar no sono, continuar dormindo, ou dormir demais",
    "Sentir-se cansado(a) ou com pouca energia",
    "Falta de apetite ou comer demais",
    "Sentir-se mal consigo mesmo(a) — ou que é um fracasso, ou que decepcionou a família ou a si mesmo(a)",
    "Dificuldade para se concentrar nas coisas (ex.: ler ou ver televisão)",
    "Lentidão para se mover ou falar, ou o oposto: inquietação a ponto de movimentar-se mais que o habitual",
    "Pensamentos de que estaria melhor morto(a), ou de se ferir de alguma forma",
]
PHQ9_SCALE = {
    0: "Nenhuma vez",
    1: "Vários dias",
    2: "Mais da metade dos dias",
    3: "Quase todos os dias",
}
PHQ9_INTRO = "Nas últimas duas semanas, com que frequência você foi incomodado por…"


def test_phq9_template_matches_the_specification_literally() -> None:
    registry = TemplateRegistry.load_from_dir(TEMPLATES_DIR)
    phq9 = registry.get("phq9")

    assert [q.text for q in phq9.ordered_questions] == PHQ9_ITEMS
    assert {o.value: o.label for o in phq9.scale.options} == PHQ9_SCALE
    assert phq9.intro == PHQ9_INTRO
    assert phq9.scoring.method == "sum"
    assert phq9.max_score == 27
    assert [rule.id for rule in phq9.skip_rules] == ["phq2_gate"]
    assert phq9.skip_rules[0].after_question == "q2"


def test_unknown_template_raises_typed_404() -> None:
    registry = TemplateRegistry.load_from_dir(TEMPLATES_DIR)
    with pytest.raises(TemplateNotFoundError) as exc:
        registry.get("gad7")
    assert exc.value.status_code == 404


def _write(tmp_path: Path, payload: dict[str, Any] | str, name: str = "t.json") -> Path:
    content = payload if isinstance(payload, str) else json.dumps(payload)
    (tmp_path / name).write_text(content, encoding="utf-8")
    return tmp_path


def _valid_template() -> dict[str, Any]:
    return {
        "template_id": "t",
        "version": 1,
        "name": "T",
        "intro": "i",
        "scale": {"options": [{"value": 0, "label": "a"}, {"value": 1, "label": "b"}]},
        "questions": [{"id": "q1", "order": 1, "text": "x"}, {"id": "q2", "order": 2, "text": "y"}],
        "skip_rules": [],
    }


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        (
            lambda t: t["skip_rules"].append(
                {
                    "id": "r",
                    "after_question": "zz",
                    "condition": {"op": "lt", "left": 1, "right": 2},
                    "action": "end_block",
                }
            ),
            "inexistente",
        ),
        (
            lambda t: t["skip_rules"].append(
                {
                    "id": "r",
                    "after_question": "q1",
                    "condition": {"op": "lt", "left": {"sum_of": ["q1", "q2"]}, "right": 2},
                    "action": "end_block",
                }
            ),
            "ainda não foi respondida",
        ),
        (lambda t: t["questions"].append({"id": "q1", "order": 3, "text": "dup"}), "únicos"),
        (lambda t: t["questions"].__setitem__(1, {"id": "q2", "order": 5, "text": "y"}), "lacunas"),
        (
            lambda t: t.__setitem__(
                "skip_rules",
                [
                    {
                        "id": "r",
                        "after_question": "q1",
                        "condition": {"op": "between", "left": 1, "right": 2},
                        "action": "end_block",
                    }
                ],
            ),
            "op",
        ),
        (lambda t: t.__setitem__("scoring", {"method": "weighted"}), "method"),
        (lambda t: t.__setitem__("surprise", 1), "surprise"),
    ],
)
def test_invalid_templates_fail_fast_with_configuration_error(
    tmp_path: Path, mutation: Any, expected_fragment: str
) -> None:
    template = _valid_template()
    mutation(template)
    with pytest.raises(ConfigurationError) as exc:
        TemplateRegistry.load_from_dir(_write(tmp_path, template))
    assert expected_fragment in str(exc.value)


def test_malformed_json_and_missing_or_empty_dir_fail_fast(tmp_path: Path) -> None:
    (tmp_path / "bad").mkdir()
    with pytest.raises(ConfigurationError, match="Sintaxe"):
        TemplateRegistry.load_from_dir(_write(tmp_path / "bad", "{not json"))
    with pytest.raises(ConfigurationError, match="não encontrado"):
        TemplateRegistry.load_from_dir(tmp_path / "missing")
    (tmp_path / "empty").mkdir()
    with pytest.raises(ConfigurationError, match="Nenhum arquivo"):
        TemplateRegistry.load_from_dir(tmp_path / "empty")


def test_duplicate_template_ids_across_files_fail_fast(tmp_path: Path) -> None:
    _write(tmp_path, _valid_template(), "a.json")
    _write(tmp_path, _valid_template(), "b.json")
    with pytest.raises(ConfigurationError, match="duplicado"):
        TemplateRegistry.load_from_dir(tmp_path)


def test_service_and_engine_have_no_template_specific_branching() -> None:
    """O template é a única fonte: nenhum `if template_id == ...` nem enunciado no código.

    Exemplos de documentação (Swagger) em router/schemas podem citar `phq9`; a lógica não.
    """
    protocols_dir = Path("app/features/protocols")
    logic_modules = ("service.py", "engine.py", "loader.py", "repository.py")
    for module in logic_modules:
        source = (protocols_dir / module).read_text(encoding="utf-8").lower()
        assert "phq" not in source, module
    for module in (*logic_modules, "router.py", "schemas.py"):
        source = (protocols_dir / module).read_text(encoding="utf-8")
        # comparação de template_id com literal = branching por template (proibido)
        assert re.search(r"""template_id\s*(==|!=|in)\s*[\(\["']""", source) is None, module
        for item in PHQ9_ITEMS:
            assert item not in source, module
