import pytest

from app.core.hashing import hash_phone, normalize_phone

pytestmark = pytest.mark.unit


def test_same_phone_in_different_formats_yields_same_hash() -> None:
    salt = "s" * 32
    assert hash_phone("+55 (11) 90000-0001", salt) == hash_phone("5511900000001", salt)


def test_different_salt_yields_different_hash() -> None:
    assert hash_phone("5511900000001", "a" * 32) != hash_phone("5511900000001", "b" * 32)


def test_hash_is_sha256_hex_and_does_not_contain_phone() -> None:
    digest = hash_phone("5511900000001", "x" * 32)
    assert len(digest) == 64
    assert "5511900000001" not in digest


@pytest.mark.parametrize("phone", ["123", "12345678", "1" * 16, "abc"])
def test_normalize_phone_rejects_invalid_lengths_without_echoing_value(phone: str) -> None:
    with pytest.raises(ValueError) as exc:
        normalize_phone(phone)
    assert phone not in str(exc.value)
