"""
test_api_systems.py
--------------------
/api/v1/systems against a real database: CRUD, topic subscriptions, the
write-only outbound secret (Decisions 13-14), auth_config merging,
verify_tls (Decision 15), and the inbound API key lifecycle (Decision 10).

    pytest tests/test_api_systems.py -v
"""
from app.config import get_settings
from app.services import secret_store
from tests.helpers import SYSTEM_A_KEY, query, send_event

NEW_SYSTEM = {"code": "system_c", "name": "Lab", "base_url": "https://lab.example/hook"}


async def test_requires_a_session(client):
    assert (await client.get("/api/v1/systems")).status_code == 401


async def test_list_and_get(admin):
    systems = (await admin.get("/api/v1/systems")).json()
    assert [s["code"] for s in systems] == ["system_a", "system_b"]
    b = (await admin.get("/api/v1/systems/system_b")).json()
    assert b["topics"] == ["INFRA", "PATIENT_ADMIN", "SPM"]
    assert b["auth_header"] == "Authorization"
    assert b["auth_value_prefix"] == "Bearer "
    assert b["verify_tls"] is True
    assert b["has_secret"] is False
    assert "auth_config" not in b
    assert (await admin.get("/api/v1/systems/nope")).status_code == 404


async def test_create_with_topics_and_secret(admin, app_env):
    resp = await admin.post("/api/v1/systems", json={
        **NEW_SYSTEM, "topics": ["INFRA"], "outbound_secret": "s3cret", "verify_tls": False,
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["topics"] == ["INFRA"]
    assert body["has_secret"] is True
    assert body["verify_tls"] is False

    row = (await query(app_env, "SELECT outbound_secret_encrypted FROM systems WHERE code = 'system_c'"))[0]
    assert "s3cret" not in row["outbound_secret_encrypted"]
    assert secret_store.decrypt(row["outbound_secret_encrypted"]) == "s3cret"

    audit = await query(app_env, "SELECT detail FROM audit_log WHERE event_type = 'system_config_created'")
    assert audit[0]["detail"]["outbound_secret_set"] is True
    assert "s3cret" not in str(audit[0]["detail"])


async def test_create_conflicts(admin):
    assert (await admin.post("/api/v1/systems", json={**NEW_SYSTEM, "code": "system_a"})).status_code == 409
    assert (await admin.post("/api/v1/systems", json={**NEW_SYSTEM, "topics": ["NOPE"]})).status_code == 409


async def test_secret_is_refused_without_an_encryption_key(admin, monkeypatch):
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", "")
    get_settings.cache_clear()
    resp = await admin.post("/api/v1/systems", json={**NEW_SYSTEM, "outbound_secret": "s3cret"})
    assert resp.status_code == 400
    assert "SECRETS_ENCRYPTION_KEY" in resp.json()["detail"]


async def test_secret_ref_and_unknown_auth_keys_are_rejected(admin):
    for auth_config in ({"secret_ref": "x"}, {"passwrod": "typo"}):
        resp = await admin.post("/api/v1/systems", json={**NEW_SYSTEM, "auth_config": auth_config})
        assert resp.status_code == 422


async def test_update_merges_auth_config_and_keeps_other_fields(admin):
    resp = await admin.patch("/api/v1/systems/system_b", json={"auth_config": {"header": "X-Token"}})
    body = resp.json()
    assert body["auth_header"] == "X-Token"
    assert body["auth_value_prefix"] == "Bearer "  # merged, not replaced
    assert body["name"] == "Patient Registration & Insurance (example)"


async def test_update_topics_and_tls(admin):
    body = (await admin.patch("/api/v1/systems/system_a", json={"topics": ["SALES"], "verify_tls": False})).json()
    assert body["topics"] == ["SALES"]
    assert body["verify_tls"] is False
    assert (await admin.patch("/api/v1/systems/system_a", json={"topics": []})).json()["topics"] == []
    assert (await admin.patch("/api/v1/systems/system_a", json={"topics": ["NOPE"]})).status_code == 409


async def test_set_and_clear_the_secret(admin):
    assert (await admin.patch("/api/v1/systems/system_a", json={"outbound_secret": "x"})).json()["has_secret"] is True
    assert (await admin.patch("/api/v1/systems/system_a", json={"clear_outbound_secret": True})).json()["has_secret"] is False
    both = await admin.patch("/api/v1/systems/system_a", json={"outbound_secret": "x", "clear_outbound_secret": True})
    assert both.status_code == 400


async def test_update_errors(admin):
    assert (await admin.patch("/api/v1/systems/system_a", json={})).status_code == 400
    assert (await admin.patch("/api/v1/systems/nope", json={"name": "x"})).status_code == 404
    assert (await admin.patch("/api/v1/systems/nope", json={"topics": []})).status_code == 404


async def test_deciding_the_secret_retires_a_legacy_secret_ref(admin, app_env):
    await query(app_env, """UPDATE systems SET auth_config = auth_config || '{"secret_ref": "old"}' WHERE code = 'system_a'""")
    assert (await admin.get("/api/v1/systems/system_a")).json()["has_legacy_secret_ref"] is True

    body = (await admin.patch("/api/v1/systems/system_a", json={"clear_outbound_secret": True})).json()
    assert body["has_legacy_secret_ref"] is False
    assert body["auth_header"] == "X-API-Key"  # the rest of auth_config survives


async def test_api_key_lifecycle(admin):
    keys = (await admin.get("/api/v1/systems/system_a/api-keys")).json()
    assert len(keys) == 1 and keys[0]["active"] is True
    assert "key_hash" not in keys[0]

    created = await admin.post("/api/v1/systems/system_a/api-keys", json={"description": "Production"})
    assert created.status_code == 201
    new_key = created.json()["api_key"]
    ok = await send_event(admin, new_key, external_ref="A-1", status="new", topic_code="INFRA")
    assert ok.status_code == 200

    key_id = created.json()["id"]
    assert (await admin.delete(f"/api/v1/systems/system_a/api-keys/{key_id}")).status_code == 204
    revoked = await send_event(admin, new_key, external_ref="A-2", status="new", topic_code="INFRA")
    assert revoked.status_code == 401
    assert (await admin.delete(f"/api/v1/systems/system_a/api-keys/{key_id}")).status_code == 404

    listed = {k["id"]: k for k in (await admin.get("/api/v1/systems/system_a/api-keys")).json()}
    assert listed[key_id]["active"] is False and listed[key_id]["revoked_at"]
    # The seed key still works.
    assert (await send_event(admin, SYSTEM_A_KEY, external_ref="A-3", status="new", topic_code="INFRA")).status_code == 200


async def test_api_keys_for_an_unknown_system(admin):
    assert (await admin.get("/api/v1/systems/nope/api-keys")).status_code == 404
    assert (await admin.post("/api/v1/systems/nope/api-keys", json={})).status_code == 404
