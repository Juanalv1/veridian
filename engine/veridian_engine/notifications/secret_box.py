"""Webhook signing secret: generation and encryption at rest.

The engine needs the plain secret to sign, so it is stored encrypted with a server key
(`VERIDIAN_SECRET_KEY`), not hashed. Encryption uses Fernet from `cryptography`
(pinned in `engine/requirements.txt`); if it is missing, encrypt/decrypt raise
`SecretEncryptionUnavailable` instead of storing the secret in clear.
"""

from __future__ import annotations

import os
import secrets

KEY_ENV = "VERIDIAN_SECRET_KEY"
SECRET_PREFIX = "whsec_"


class SecretEncryptionError(RuntimeError):
    pass


class SecretKeyMissing(SecretEncryptionError):
    pass


class SecretEncryptionUnavailable(SecretEncryptionError):
    pass


def generate_secret() -> str:
    return SECRET_PREFIX + secrets.token_urlsafe(32)


def _fernet():
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        raise SecretKeyMissing(f"{KEY_ENV} is not set")
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise SecretEncryptionUnavailable(
            "the 'cryptography' package is required to encrypt webhook secrets"
        ) from None
    try:
        return Fernet(key.encode("ascii"))
    except ValueError:
        raise SecretKeyMissing(f"{KEY_ENV} is not a valid Fernet key") from None


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
