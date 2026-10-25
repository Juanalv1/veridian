import sys

import pytest

from veridian_engine.notifications.secret_box import (
    KEY_ENV,
    SecretEncryptionUnavailable,
    SecretKeyMissing,
    decrypt_secret,
    encrypt_secret,
    generate_secret,
)


def test_key_env_name_is_documented():
    assert KEY_ENV == "VERIDIAN_SECRET_KEY"


def test_generated_secrets_are_random_and_prefixed():
    a, b = generate_secret(), generate_secret()
    assert a != b
    assert a.startswith("whsec_") and len(a) > 40


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    with pytest.raises(SecretKeyMissing):
        encrypt_secret("whsec_x")
    with pytest.raises(SecretKeyMissing):
        decrypt_secret("token")


def test_missing_dependency_raises_a_clear_error(monkeypatch):
    monkeypatch.setenv(KEY_ENV, "k" * 44)
    monkeypatch.setitem(sys.modules, "cryptography", None)
    monkeypatch.setitem(sys.modules, "cryptography.fernet", None)
    with pytest.raises(SecretEncryptionUnavailable):
        encrypt_secret("whsec_x")


def test_roundtrip_when_cryptography_is_installed(monkeypatch):
    fernet = pytest.importorskip("cryptography.fernet")
    monkeypatch.setenv(KEY_ENV, fernet.Fernet.generate_key().decode())
    token = encrypt_secret("whsec_x")
    assert "whsec_x" not in token
    assert decrypt_secret(token) == "whsec_x"
