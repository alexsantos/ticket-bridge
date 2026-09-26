"""
test_secrets.py
----------------
Unit tests for secrets.py's secret_ref resolution: the
os.environ.get(secret_ref.upper()) lookup, and the Secret Manager path
with a faked client (no real GCP call is ever made). The uppercasing
behavior is here specifically because of a real bug found and fixed in
this project: .env conventionally uses SCREAMING_SNAKE_CASE, but
secret_ref values are lowercase-with-underscores (Secret Manager's own
convention) - without an explicit .upper(), the two never matched.

The lookup no longer depends on ENVIRONMENT (CLAUDE.md Decision 13): it
used to read env vars only when ENVIRONMENT=local, which silently broke
.env secrets on a self-hosted VM running with ENVIRONMENT=production.

    pytest tests/test_secrets.py -v
"""
from types import SimpleNamespace

import pytest

import app.services.secrets as secrets_module


@pytest.fixture(autouse=True)
def _clear_secret_cache():
    """Resolved secrets are cached per process - a result cached by one test would leak into the next."""
    secrets_module._cache.clear()
    yield
    secrets_module._cache.clear()


def _fake_secret_manager(monkeypatch, *, value: bytes | None = None, fail: bool = False):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")

    class _FakeClient:
        def access_secret_version(self, request):
            if fail:
                raise RuntimeError("simulated Secret Manager outage")
            assert request["name"].startswith("projects/test-project/secrets/")
            return SimpleNamespace(payload=SimpleNamespace(data=value))

    monkeypatch.setattr(secrets_module, "_get_secret_manager_client", lambda: _FakeClient())


def test_resolves_uppercased_env_var(monkeypatch):
    monkeypatch.setenv("SYSTEM_TEST_ONE_KEY", "resolved-value-1")

    assert secrets_module.resolve_secret("system_test_one_key") == "resolved-value-1"


def test_env_var_is_used_whatever_environment_is_set_to(monkeypatch):
    """The VM bug: ENVIRONMENT=production used to skip env vars entirely."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.setenv("SYSTEM_TEST_TWO_KEY", "from-dot-env")

    assert secrets_module.resolve_secret("system_test_two_key") == "from-dot-env"


def test_lowercase_env_var_is_not_matched(monkeypatch):
    """Env var names are case-sensitive, so a secret_ref only ever resolves against
    its UPPERCASED form, never its own literal case."""
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("SYSTEM_TEST_THREE_KEY", raising=False)
    monkeypatch.setenv("system_test_three_key", "should-not-be-found")

    assert secrets_module.resolve_secret("system_test_three_key") is None


def test_missing_everywhere_returns_none(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("SYSTEM_TEST_FOUR_KEY", raising=False)

    assert secrets_module.resolve_secret("system_test_four_key") is None


def test_falls_back_to_secret_manager(monkeypatch):
    monkeypatch.delenv("SYSTEM_TEST_FIVE_KEY", raising=False)
    _fake_secret_manager(monkeypatch, value=b"resolved-from-secret-manager")

    assert secrets_module.resolve_secret("system_test_five_key") == "resolved-from-secret-manager"


def test_env_var_wins_over_secret_manager(monkeypatch):
    monkeypatch.setenv("SYSTEM_TEST_SIX_KEY", "from-env")
    _fake_secret_manager(monkeypatch, value=b"from-secret-manager")

    assert secrets_module.resolve_secret("system_test_six_key") == "from-env"


def test_secret_manager_failure_returns_none_and_is_not_cached(monkeypatch):
    """A transient outage must not leave the secret "missing" for the life of the process."""
    monkeypatch.delenv("SYSTEM_TEST_SEVEN_KEY", raising=False)
    _fake_secret_manager(monkeypatch, fail=True)
    assert secrets_module.resolve_secret("system_test_seven_key") is None

    _fake_secret_manager(monkeypatch, value=b"back-online")
    assert secrets_module.resolve_secret("system_test_seven_key") == "back-online"
