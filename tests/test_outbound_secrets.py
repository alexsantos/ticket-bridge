"""
test_outbound_secrets.py
------------------------
Unit tests for outbound secrets set from the frontend (CLAUDE.md Decision
13): encryption at rest (app/services/secret_store.py) and which secret a
delivery actually uses (sync_service.resolve_outbound_secret) - including
that a configured-but-unavailable secret fails the delivery instead of
sending it without auth.

    pytest tests/test_outbound_secrets.py -v
"""
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

import app.services.secrets as secrets_module
from app.services import secret_store
from app.services.dispatcher import DeliveryError
from app.services.sync_service import resolve_outbound_secret

KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(secret_store, "get_settings", lambda: SimpleNamespace(secrets_encryption_key=KEY))
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    secrets_module._cache.clear()
    yield
    secrets_module._cache.clear()


def _use_key(monkeypatch, key: str):
    monkeypatch.setattr(secret_store, "get_settings", lambda: SimpleNamespace(secrets_encryption_key=key))


def test_encrypt_decrypt_roundtrip_and_ciphertext_hides_value():
    token = secret_store.encrypt("s3cret-value")

    assert "s3cret-value" not in token
    assert secret_store.decrypt(token) == "s3cret-value"


def test_encrypt_without_key_refuses_instead_of_storing_plaintext(monkeypatch):
    _use_key(monkeypatch, "")
    with pytest.raises(secret_store.SecretStoreError, match="SECRETS_ENCRYPTION_KEY is not set"):
        secret_store.encrypt("value")


def test_invalid_key_is_reported_clearly(monkeypatch):
    _use_key(monkeypatch, "not-a-fernet-key")
    with pytest.raises(secret_store.SecretStoreError, match="not a valid key"):
        secret_store.encrypt("value")


def test_decrypt_with_a_different_key_fails_clearly(monkeypatch):
    token = secret_store.encrypt("value")
    _use_key(monkeypatch, Fernet.generate_key().decode())
    with pytest.raises(secret_store.SecretStoreError, match="could not be decrypted"):
        secret_store.decrypt(token)


def test_stored_secret_wins_over_secret_ref(monkeypatch):
    monkeypatch.setenv("SOME_REF", "from-env")
    system = {"outbound_secret_encrypted": secret_store.encrypt("stored"), "auth_config": {"secret_ref": "some_ref"}}

    assert resolve_outbound_secret(system) == "stored"


def test_secret_ref_used_when_nothing_stored(monkeypatch):
    monkeypatch.setenv("SOME_REF", "from-env")

    assert resolve_outbound_secret({"outbound_secret_encrypted": None, "auth_config": {"secret_ref": "some_ref"}}) == "from-env"


def test_no_secret_configured_means_no_auth():
    assert resolve_outbound_secret({"outbound_secret_encrypted": None, "auth_config": {}}) is None


def test_unresolvable_secret_ref_fails_delivery_instead_of_sending_without_auth(monkeypatch):
    monkeypatch.delenv("MISSING_REF", raising=False)
    with pytest.raises(DeliveryError, match="could not be resolved"):
        resolve_outbound_secret({"outbound_secret_encrypted": None, "auth_config": {"secret_ref": "missing_ref"}})


def test_undecryptable_stored_secret_fails_delivery(monkeypatch):
    token = secret_store.encrypt("value")
    _use_key(monkeypatch, Fernet.generate_key().decode())
    with pytest.raises(DeliveryError, match="could not be decrypted"):
        resolve_outbound_secret({"outbound_secret_encrypted": token, "auth_config": {}})
