"""
Detecção de PII em estruturas de dados.

Usado por duas fronteiras: o Event Store (recusa properties com PII) e o logging
(redige campos sensíveis). As funções nunca devolvem o valor ofensor — só o caminho
e o tipo da violação — para não vazar PII na própria mensagem de erro.
"""

import re
from collections.abc import Mapping
from typing import Any

# Chaves que, por nome exato, carregam PII.
FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "phone",
        "telefone",
        "celular",
        "name",
        "nome",
        "full_name",
        "first_name",
        "last_name",
        "patient_name",
        "birth_date",
        "birthdate",
        "date_of_birth",
        "data_nascimento",
        "dob",
        "cpf",
        "email",
    }
)
# Radicais que tornam qualquer chave suspeita (ex.: `patient_phone`, `nascimento_titular`).
# `name` fica fora de propósito: `event_name`/`template_name` são legítimos.
FORBIDDEN_KEY_STEMS: tuple[str, ...] = (
    "phone",
    "telefone",
    "nome_",
    "birth",
    "nasc",
    "cpf",
    "email",
)

# Sequência de 10–15 dígitos, com separadores usuais de telefone, delimitada por
# não-alfanuméricos (evita casar trechos de UUIDs e hashes hexadecimais).
_PHONE_CANDIDATE = re.compile(r"(?<![\w.\-])\+?\d[\d\s().\-]{8,}\d(?![\w.\-])")
_ISO_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")
_NON_DIGITS = re.compile(r"\D")

PHONE_MIN_DIGITS = 10
PHONE_MAX_DIGITS = 15  # limite do padrão E.164


def is_forbidden_key(key: str) -> bool:
    """Chave de dicionário que, por nome, carrega PII (ex.: `phone`, `patient_phone`, `dob`)."""
    lowered = key.lower()
    return lowered in FORBIDDEN_KEYS or any(stem in lowered for stem in FORBIDDEN_KEY_STEMS)


def looks_like_phone(text: str) -> bool:
    """Texto que contém algo com cara de telefone (10–15 dígitos com separadores)."""
    for match in _PHONE_CANDIDATE.finditer(text):
        candidate = match.group()
        if _ISO_DATE_PREFIX.match(candidate):
            continue  # datas/timestamps ISO não são telefones
        digits = _NON_DIGITS.sub("", candidate)
        if PHONE_MIN_DIGITS <= len(digits) <= PHONE_MAX_DIGITS:
            return True
    return False


def is_phone_like(value: Any) -> bool:
    """
    Valor escalar com cara de telefone.

    Texto: ver `looks_like_phone`. Inteiro (não booleano) com 10–15 dígitos: um telefone
    convertido para número escaparia da detecção textual.
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return PHONE_MIN_DIGITS <= len(str(abs(value))) <= PHONE_MAX_DIGITS
    return isinstance(value, str) and looks_like_phone(value)


def find_pii(data: Any, path: str = "$") -> list[str]:
    """
    Percorre recursivamente `data` e devolve as violações encontradas.

    Cada item tem o formato `"<caminho> (<tipo>)"`, ex.: `"$.contact (forbidden_key)"`.
    O valor ofensor nunca aparece no retorno.
    """
    violations: list[str] = []
    if isinstance(data, Mapping):
        for key, value in data.items():
            key_str = str(key)
            child = f"{path}.{key_str}"
            if is_forbidden_key(key_str):
                violations.append(f"{child} (forbidden_key)")
                continue
            violations.extend(find_pii(value, child))
    elif isinstance(data, list | tuple | set | frozenset):
        for index, item in enumerate(data):
            violations.extend(find_pii(item, f"{path}[{index}]"))
    elif is_phone_like(data):
        violations.append(f"{path} (phone_like_value)")
    return violations


def redact(data: Any) -> Any:
    """Devolve uma cópia de `data` com chaves proibidas e valores com cara de telefone substituídos."""
    if isinstance(data, Mapping):
        return {
            key: ("[redacted]" if is_forbidden_key(str(key)) else redact(value))
            for key, value in data.items()
        }
    if isinstance(data, list | tuple):
        return [redact(item) for item in data]
    if is_phone_like(data):
        return "[redacted]"
    return data
