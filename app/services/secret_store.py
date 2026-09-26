"""
secret_store.py
---------------
Encrypts/decrypts outbound secrets entered in the frontend
(systems.outbound_secret_encrypted) - see CLAUDE.md Decision 13.

Fernet (AES-128-CBC + HMAC-SHA256, from `cryptography`) keyed by
SECRETS_ENCRYPTION_KEY: a database dump or backup alone doesn't reveal the
secrets, only the database *plus* the host's environment does. The key is
set once per deployment and never changes through the app; losing it means
re-entering every stored secret (the ciphertext can't be recovered).

Generate a key with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class SecretStoreError(Exception):
    """Encryption isn't configured, or a stored value can't be decrypted. The message is safe to show."""


def _fernet() -> Fernet:
    key = get_settings().secrets_encryption_key
    if not key:
        raise SecretStoreError(
            "SECRETS_ENCRYPTION_KEY is not set - the bridge can't store or read outbound secrets. "
            "See README.md, 'Outbound secrets'."
        )
    try:
        return Fernet(key)
    except ValueError as exc:
        raise SecretStoreError(
            "SECRETS_ENCRYPTION_KEY is not a valid key (expected 32 url-safe base64-encoded bytes)."
        ) from exc


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretStoreError(
            "A stored outbound secret could not be decrypted - SECRETS_ENCRYPTION_KEY has probably "
            "changed since it was saved. Re-enter the secret in the system's settings."
        ) from exc
