"""
test_api_topics_conversations_audit.py
---------------------------------------
The read-mostly admin endpoints against a real database: topic CRUD,
conversation listing and detail, and the paged audit log.

    pytest tests/test_api_topics_conversations_audit.py -v
"""
from tests.helpers import SYSTEM_A_KEY, SYSTEM_B_KEY, send_event


async def test_topic_crud(admin, app_env):
    codes = [t["code"] for t in (await admin.get("/api/v1/topics")).json()]
    assert codes == ["INFRA", "PATIENT_ADMIN", "SALES", "SPM"]

    created = await admin.post("/api/v1/topics", json={"code": "LAB", "name": "Laboratory"})
    assert created.status_code == 201
    assert created.json()["active"] is True
    assert (await admin.post("/api/v1/topics", json={"code": "LAB", "name": "Again"})).status_code == 409

    updated = await admin.patch("/api/v1/topics/LAB", json={"description": "Expected metadata: sample_id"})
    assert updated.json()["description"] == "Expected metadata: sample_id"
    assert (await admin.get("/api/v1/topics/LAB")).json()["name"] == "Laboratory"


async def test_topic_errors(admin, client):
    assert (await admin.get("/api/v1/topics/NOPE")).status_code == 404
    assert (await admin.patch("/api/v1/topics/NOPE", json={"name": "x"})).status_code == 404
    assert (await admin.patch("/api/v1/topics/INFRA", json={})).status_code == 400


async def _two_conversations(client):
    infra = await send_event(client, SYSTEM_A_KEY, external_ref="A-1", status="new", topic_code="INFRA", subject="Printer")
    spm = await send_event(client, SYSTEM_B_KEY, external_ref="B-1", status="new", topic_code="SPM", subject="Project")
    return infra.json()["conversation_id"], spm.json()["conversation_id"]


async def test_conversation_list_and_filters(admin):
    infra_id, spm_id = await _two_conversations(admin)

    all_ids = {c["conversation_id"] for c in (await admin.get("/api/v1/conversations")).json()}
    assert all_ids == {infra_id, spm_id}

    by_system = (await admin.get("/api/v1/conversations?system_code=system_a")).json()
    assert [c["conversation_id"] for c in by_system] == [infra_id]
    by_topic = (await admin.get("/api/v1/conversations?topic_code=SPM")).json()
    assert [c["conversation_id"] for c in by_topic] == [spm_id]


async def test_conversation_detail(admin):
    infra_id, _ = await _two_conversations(admin)
    await send_event(admin, SYSTEM_B_KEY, conversation_id=infra_id, external_ref="B-7", status="in_progress")

    c = (await admin.get(f"/api/v1/conversations/{infra_id}")).json()
    assert c["subject"] == "Printer"
    assert c["topic_code"] == "INFRA"
    assert [(p["system_code"], p["external_ref"], p["local_status"]) for p in c["participants"]] == [
        ("system_a", "A-1", "new"), ("system_b", "B-7", "in_progress"),
    ]
    assert (await admin.get("/api/v1/conversations/00000000-0000-0000-0000-000000000000")).status_code == 404


async def test_audit_paging_and_filters(admin):
    infra_id, _ = await _two_conversations(admin)

    first = (await admin.get("/api/v1/audit?limit=2")).json()
    assert len(first["items"]) == 2 and first["has_more"] is True
    second = (await admin.get("/api/v1/audit?limit=2&offset=2")).json()
    assert {i["id"] for i in first["items"]}.isdisjoint({i["id"] for i in second["items"]})
    # Newest first.
    assert first["items"][0]["id"] > first["items"][1]["id"] > second["items"][0]["id"]

    conv = (await admin.get(f"/api/v1/audit?conversation_id={infra_id}&limit=500")).json()["items"]
    assert {i["event_type"] for i in conv} == {"conversation_created", "event_received", "fanout_scheduled"}
    by_system = (await admin.get("/api/v1/audit?system_code=system_b&limit=500")).json()["items"]
    assert by_system and all(i["system_code"] == "system_b" for i in by_system)


async def test_read_endpoints_require_a_session(client):
    for path in ("/api/v1/topics", "/api/v1/conversations", "/api/v1/audit", "/api/v1/deliveries"):
        assert (await client.get(path)).status_code == 401, path
