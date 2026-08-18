"""
Migrations
Lightweight schema versioning system.
Currently the schema.sql file is idempotent (CREATE TABLE IF NOT EXISTS),
so initial setup just runs it directly via connection.initialize_database().

This module exists to support FUTURE incremental migrations beyond v1.0.0,
without needing to touch the core schema.sql file.

Usage pattern for future migrations:
    1. Add a new function below named migrate_v1_to_v2()
    2. Register it in MIGRATIONS list
    3. Bump SCHEMA_VERSION
    4. run_pending_migrations() will apply it automatically on next startup
"""

from __future__ import annotations

import logging
from typing import Callable

from backend.database.connection import get_db

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


async def _ensure_version_table() -> None:
    async with get_db() as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            )
            """
        )
        cursor = await db.execute("SELECT MAX(version) FROM schema_version")
        row = await cursor.fetchone()
        if row[0] is None:
            await db.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )


async def get_current_version() -> int:
    async with get_db() as db:
        cursor = await db.execute("SELECT MAX(version) FROM schema_version")
        row = await cursor.fetchone()
        return row[0] if row and row[0] is not None else 0


# Registry of migration functions: {target_version: migration_fn}
MIGRATIONS: dict[int, Callable] = {
    # Example future migration:
    # 2: migrate_v1_to_v2,
}


async def run_pending_migrations() -> None:
    """
    Apply any migrations needed to reach SCHEMA_VERSION.
    Safe to call on every startup — no-ops if already up to date.
    """
    await _ensure_version_table()
    current = await get_current_version()

    if current >= SCHEMA_VERSION:
        logger.debug("Schema up to date (v%d)", current)
        return

    for version in range(current + 1, SCHEMA_VERSION + 1):
        migration_fn = MIGRATIONS.get(version)
        if migration_fn:
            logger.info("Applying migration to v%d...", version)
            await migration_fn()
            async with get_db() as db:
                await db.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", (version,)
                )
            logger.info("Migration to v%d applied successfully", version)
