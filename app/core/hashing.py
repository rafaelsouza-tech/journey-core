"""
Pseudonimização do telefone.

O telefone só existe em claro no cadastro operacional; em eventos e logs circula
apenas `phone_hash`. Usamos HMAC-SHA256 com salt secreto (o "SHA-256 com salt"
na construção correta — sem concatenação ingênua), sobre o telefone normalizado
para dígitos, de modo que `+55 (11) 91111-2222` e `5511911112222` gerem o mesmo hash.
"""

import hashlib
import hmac
import re

PHONE_MIN_DIGITS = 10
PHONE_MAX_DIGITS = 15  # limite do padrão E.164

# Só dígitos ASCII e os separadores usuais (`+` apenas à frente). Letras ou dígitos de
# outros alfabetos não são "formatação": são entrada inválida, não algo a descartar.
_PHONE_FORMAT = re.compile(r"^\+?[0-9\s().\-]+$")
_SEPARATORS = re.compile(r"[^0-9]")


def normalize_phone(phone: str) -> str:
    """
    Reduz o telefone a dígitos e valida formato e tamanho (10–15, padrão E.164).

    Raises:
        ValueError: se houver caracteres além de dígitos e separadores, ou se o telefone
            não tiver entre 10 e 15 dígitos. A mensagem nunca inclui o valor recebido.
    """
    if not _PHONE_FORMAT.fullmatch(phone):
        raise ValueError("telefone deve conter apenas dígitos e os separadores + - ( ) . e espaço")
    digits = _SEPARATORS.sub("", phone)
    if not PHONE_MIN_DIGITS <= len(digits) <= PHONE_MAX_DIGITS:
        raise ValueError(f"telefone deve ter entre {PHONE_MIN_DIGITS} e {PHONE_MAX_DIGITS} dígitos")
    return digits


def hash_phone(phone: str, salt: str) -> str:
    """Retorna o HMAC-SHA256 (hex) do telefone normalizado, usando o salt como chave."""
    digits = normalize_phone(phone)
    return hmac.new(salt.encode("utf-8"), digits.encode("utf-8"), hashlib.sha256).hexdigest()
