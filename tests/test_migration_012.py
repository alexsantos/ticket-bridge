"""
test_migration_012.py
----------------------
migrations/012_conversation_ref.sql against a real database: conversations
that existed before it get numbers in the order they were created, without
counting as new activity (updated_at unchanged), and conversations created
afterwards carry on from the highest number.

    pytest tests/test_migration_012.py -v
"""
from pathlib import Path

import psycopg

from tests.helpers import SYSTEM_A_KEY, query, send_event

MIGRATION = Path(__file__).parent.parent / "migrations" / "012_conversation_ref.sql"


async def test_numbers_existing_conversations_in_creation_order(client, app_env):
    for ref, topic in (("A-1", "INFRA"), ("A-2", "PATIENT_ADMIN"), ("A-3", "INFRA")):
        resp = await send_event(client, SYSTEM_A_KEY, external_ref=ref, status="new", topic_code=topic)
        assert resp.status_code == 200, resp.text

    # Simulate a pre-012 database: creation times out of UUID order, and the new columns gone.
    with psycopg.connect(app_env, autocommit=True) as conn:
        conn.execute("ALTER TABLE conversations DISABLE TRIGGER trg_conversations_updated_at")
        conn.execute(
            """
            UPDATE conversations c SET created_at = '2020-01-01'::timestamptz + make_interval(days => p.n::int),
                                       updated_at = '2021-01-01'
            FROM (SELECT conversation_id, 4 - row_number() OVER (ORDER BY created_at) AS n FROM conversations) p
            WHERE p.conversation_id = c.conversation_id
            """
        )
        conn.execute("ALTER TABLE conversations ENABLE TRIGGER trg_conversations_updated_at")
        conn.execute("ALTER TABLE conversations DROP COLUMN conversation_ref, DROP COLUMN conversation_number")
        conn.execute(MIGRATION.read_text())

    rows = await query(
        app_env, "SELECT conversation_ref, updated_at::date::text AS updated FROM conversations ORDER BY created_at"
    )
    # Creation order was reversed above, so A-3's conversation is now the oldest.
    assert [r["conversation_ref"] for r in rows] == ["INFRA-1", "PATIENT_ADMIN-2", "INFRA-3"]
    assert {r["updated"] for r in rows} == {"2021-01-01"}

    resp = await send_event(client, SYSTEM_A_KEY, external_ref="A-4", status="new", topic_code="INFRA")
    assert resp.json()["conversation_ref"] == "INFRA-4"
