"""
test_migration_009.py
----------------------
migrations/009_overall_status_follows_latest_event.sql against a real
database: conversations whose overall_status was left at its creation
value (before 0.14.0) get the status and time of their latest event, and
re-running it changes nothing.

    pytest tests/test_migration_009.py -v
"""
from pathlib import Path

import psycopg

from tests.helpers import SYSTEM_A_KEY, SYSTEM_B_KEY, query, send_event

MIGRATION = Path(__file__).parent.parent / "migrations" / "009_overall_status_follows_latest_event.sql"


def _run_migration(database_url: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(MIGRATION.read_text())


async def test_backfills_status_and_time_of_the_latest_event(client, app_env):
    first = await send_event(client, SYSTEM_A_KEY, external_ref="A-1", status="new", topic_code="INFRA")
    cid = first.json()["conversation_id"]
    await send_event(client, SYSTEM_B_KEY, conversation_id=cid, external_ref="B-1", status="resolved")
    untouched = await send_event(client, SYSTEM_A_KEY, external_ref="A-2", status="new", topic_code="INFRA")

    # Simulate a pre-0.14.0 row: status never updated, updated_at long ago.
    with psycopg.connect(app_env, autocommit=True) as conn:
        conn.execute("ALTER TABLE conversations DISABLE TRIGGER trg_conversations_updated_at")
        conn.execute(
            "UPDATE conversations SET overall_status = 'new', updated_at = '2020-01-01' WHERE conversation_id = %(c)s",
            {"c": cid},
        )
        conn.execute("ALTER TABLE conversations ENABLE TRIGGER trg_conversations_updated_at")

    _run_migration(app_env)

    row = (await query(app_env, "SELECT overall_status, updated_at FROM conversations WHERE conversation_id = %(c)s", {"c": cid}))[0]
    last_event = (await query(app_env, """
        SELECT max(created_at) AS at FROM audit_log
        WHERE event_type = 'event_received' AND conversation_id = %(c)s
    """, {"c": cid}))[0]["at"]
    assert row["overall_status"] == "resolved"
    assert row["updated_at"] == last_event

    other = (await query(app_env, "SELECT overall_status FROM conversations WHERE conversation_id = %(c)s",
                         {"c": untouched.json()["conversation_id"]}))[0]
    assert other["overall_status"] == "new"

    snapshot = await query(app_env, "SELECT conversation_id, overall_status, updated_at FROM conversations ORDER BY 1")
    _run_migration(app_env)
    assert await query(app_env, "SELECT conversation_id, overall_status, updated_at FROM conversations ORDER BY 1") == snapshot
