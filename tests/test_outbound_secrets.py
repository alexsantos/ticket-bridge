"""
test_outbound_secrets.py
------------------------
Unit tests for outbound secrets (CLAUDE.md Decisions 13-14): encryption
at rest (app/services/secret_store.py), which secret a delivery uses
(sync_service.resolve_outbound_secret) - including that a leftover
pre-0.8.0 secret_ref or an undecryptable secret fails the delivery
instead of sending it without auth - and that the API no longer accepts
secret_ref at all.

    pytest tests/test_outbound_secrets.py -v
"""
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.schemas import SystemCreate, SystemUpdate
from app.services import secret_store
from app.services.dispatcher import DeliveryError
from app.services.sync_service import resolve_outbound_secret

KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(secret_store, "get_settings", lambda: SimpleNamespace(secrets_encryption_key=KEY))


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


def test_stored_secret_is_used():
    system = {"outbound_secret_encrypted": secret_store.encrypt("stored"), "auth_config": {"header": "X-API-Key"}}

    assert resolve_outbound_secret(system) == "stored"


def test_no_secret_configured_means_no_auth():
    assert resolve_outbound_secret({"outbound_secret_encrypted": None, "auth_config": {}}) is None


def test_leftover_secret_ref_fails_delivery_instead_of_sending_without_auth():
    """A pre-0.8.0 system expected auth that can no longer be looked up."""
    with pytest.raises(DeliveryError, match="no longer supported"):
        resolve_outbound_secret({"outbound_secret_encrypted": None, "auth_config": {"secret_ref": "old_ref"}})


def test_stored_secret_is_used_even_with_a_leftover_secret_ref():
    system = {"outbound_secret_encrypted": secret_store.encrypt("stored"), "auth_config": {"secret_ref": "old_ref"}}

    assert resolve_outbound_secret(system) == "stored"


@pytest.mark.parametrize("model", [SystemCreate, SystemUpdate])
def test_api_rejects_secret_ref(model):
    fields = {"code": "x", "name": "x", "base_url": "http://x"} if model is SystemCreate else {}
    with pytest.raises(ValidationError, match="secret_ref is no longer supported"):
        model(**fields, auth_config={"secret_ref": "some_ref"})


def test_api_rejects_unknown_auth_config_keys():
    with pytest.raises(ValidationError, match="Unknown auth_config keys"):
        SystemUpdate(auth_config={"header": "X-API-Key", "passwrod": "typo"})
    assert SystemUpdate(auth_config={"header": "Authorization", "value_prefix": "Bearer "}).auth_config


def test_undecryptable_stored_secret_fails_delivery(monkeypatch):
    token = secret_store.encrypt("value")
    _use_key(monkeypatch, Fernet.generate_key().decode())
    with pytest.raises(DeliveryError, match="could not be decrypted"):
        resolve_outbound_secret({"outbound_secret_encrypted": token, "auth_config": {}})
