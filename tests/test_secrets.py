"""
test_secrets.py
----------------
Unit tests for secrets.py's secret_ref resolution: the local-mode
os.environ.get(secret_ref.upper()) lookup, and the Secret Manager path
with a faked client (no real GCP call is ever made). The uppercasing
behavior is here specifically because of a real bug found and fixed in
this project: .env conventionally uses SCREAMING_SNAKE_CASE, but
secret_ref values are lowercase-with-underscores (Secret Manager's own
convention) - without an explicit .upper(), the two never matched.

    pytest tests/test_secrets.py -v
"""
from types import SimpleNamespace

import pytest

import app.services.secrets as secrets_module


@pytest.fixture(autouse=True)
def _clear_secret_cache():
    """resolve_secret is @lru_cache'd - a result cached by one test would leak into the next otherwise."""
    secrets_module.resolve_secret.cache_clear()
    yield
    secrets_module.resolve_secret.cache_clear()


def _use_environment(monkeypatch, environment: str):
    monkeypatch.setattr(secrets_module, "get_settings", lambda: SimpleNamespace(environment=environment))


def test_local_mode_resolves_uppercased_env_var(monkeypatch):
    _use_environment(monkeypatch, "local")
    monkeypatch.setenv("SYSTEM_TEST_ONE_KEY", "resolved-value-1")

    assert secrets_module.resolve_secret("system_test_one_key") == "resolved-value-1"


def test_local_mode_lowercase_env_var_is_not_matched(monkeypatch):
    """The exact bug that was found and fixed: env var names are case-sensitive, so a
    secret_ref only ever resolves against its UPPERCASED form, never its own literal case."""
    _use_environment(monkeypatch, "local")
    monkeypatch.delenv("SYSTEM_TEST_TWO_KEY", raising=False)
    monkeypatch.setenv("system_test_two_key", "should-not-be-found")

    assert secrets_module.resolve_secret("system_test_two_key") is None


def test_local_mode_missing_env_var_returns_none(monkeypatch):
    _use_environment(monkeypatch, "local")
    monkeypatch.delenv("SYSTEM_TEST_THREE_KEY", raising=False)

    assert secrets_module.resolve_secret("system_test_three_key") is None


def test_production_mode_without_project_returns_none(monkeypatch):
    _use_environment(monkeypatch, "production")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    assert secrets_module.resolve_secret("system_test_four_key") is None


def test_production_mode_resolves_via_secret_manager(monkeypatch):
    _use_environment(monkeypatch, "production")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")

    class _FakeClient:
        def access_secret_version(self, request):
            assert request["name"] == "projects/test-project/secrets/system_test_five_key/versions/latest"
            return SimpleNamespace(payload=SimpleNamespace(data=b"resolved-from-secret-manager"))

    monkeypatch.setattr(secrets_module, "_get_secret_manager_client", lambda: _FakeClient())

    assert secrets_module.resolve_secret("system_test_five_key") == "resolved-from-secret-manager"


def test_production_mode_secret_manager_failure_returns_none(monkeypatch):
    _use_environment(monkeypatch, "production")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")

    class _FailingClient:
        def access_secret_version(self, request):
            raise RuntimeError("simulated Secret Manager outage")

    monkeypatch.setattr(secrets_module, "_get_secret_manager_client", lambda: _FailingClient())

    assert secrets_module.resolve_secret("system_test_six_key") is None
