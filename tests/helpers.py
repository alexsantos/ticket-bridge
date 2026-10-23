"""
helpers.py
----------
Shared constants and small helpers for the integration tests (fixtures
live in conftest.py).
"""
import httpx
import psycopg
from cryptography.fernet import Fernet
from psycopg.rows import dict_row

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "ChangeMe-Immediately!"  # seeded by migrations/005_admin_authentication.sql
SYSTEM_A_KEY = "dev-key-system-a"         # seeded by migrations/002_seed_example.sql
SYSTEM_B_KEY = "dev-key-system-b"
ENCRYPTION_KEY = Fernet.generate_key().decode()


async def send_event(client, key: str, **event) -> httpx.Response:
    """POST /api/v1/events as the system owning `key`."""
    return await client.post("/api/v1/events", json=event, headers={"X-API-Key": key})


async def query(database_url: str, sql: str, params: dict | None = None) -> list[dict]:
    """Reads rows straight from the test database, for assertions."""
    async with await psycopg.AsyncConnection.connect(database_url, row_factory=dict_row) as conn:
        cur = await conn.execute(sql, params or {})
        return await cur.fetchall() if cur.description else []
