"""
Database Connection Manager
Handles SQLite connection lifecycle, WAL mode, migrations, and provides
a thread-safe async context manager for all database operations.
"""

import sqlite3
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Any

import aiosqlite

from backend.config import config

logger = logging.getLogger(__name__)


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """Apply performance and safety pragmas to a raw SQLite connection."""
    db_cfg = config.database
    conn.execute(f"PRAGMA journal_mode = {'WAL' if db_cfg.wal_mode else 'DELETE'}")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute(f"PRAGMA cache_size = -{db_cfg.cache_size_kb}")
    conn.execute(f"PRAGMA mmap_size = {db_cfg.mmap_size_bytes}")
    conn.execute(f"PRAGMA busy_timeout = {db_cfg.busy_timeout_ms}")
    conn.execute("PRAGMA auto_vacuum = INCREMENTAL")


async def _apply_pragmas_async(conn: aiosqlite.Connection) -> None:
    """Apply pragmas to an async aiosqlite connection."""
    db_cfg = config.database
    await conn.execute(f"PRAGMA journal_mode = {'WAL' if db_cfg.wal_mode else 'DELETE'}")
    await conn.execute("PRAGMA synchronous = NORMAL")
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.execute("PRAGMA temp_store = MEMORY")
    await conn.execute(f"PRAGMA cache_size = -{db_cfg.cache_size_kb}")
    await conn.execute(f"PRAGMA mmap_size = {db_cfg.mmap_size_bytes}")
    await conn.execute(f"PRAGMA busy_timeout = {db_cfg.busy_timeout_ms}")
    await conn.execute("PRAGMA auto_vacuum = INCREMENTAL")


async def initialize_database() -> None:
    """
    Run schema migrations on startup.
    Creates all tables and indexes if they do not exist.
    Safe to call multiple times (idempotent).
    """
    schema_path = Path(__file__).parent / "schema.sql"
    schema_sql = schema_path.read_text(encoding="utf-8")

    async with aiosqlite.connect(config.database.path) as conn:
        await _apply_pragmas_async(conn)
        await conn.executescript(schema_sql)
        await conn.commit()

    logger.info("Database initialized at %s", config.database.path)


@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """
    Async context manager that yields a configured aiosqlite connection.

    Usage:
        async with get_db() as db:
            rows = await db.execute_fetchall("SELECT * FROM posts")
    """
    conn = await aiosqlite.connect(config.database.path)
    conn.row_factory = aiosqlite.Row
    try:
        await _apply_pragmas_async(conn)
        yield conn
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await conn.close()


async def execute_many(sql: str, params_list: list[tuple[Any, ...]]) -> int:
    """Bulk insert/update with a single connection. Returns rowcount."""
    async with get_db() as db:
        cursor = await db.executemany(sql, params_list)
        return cursor.rowcount


async def fetchall(sql: str, params: tuple = ()) -> list[dict]:
    """Convenience: run a SELECT and return list of plain dicts."""
    async with get_db() as db:
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def fetchone(sql: str, params: tuple = ()) -> dict | None:
    """Convenience: run a SELECT and return single plain dict or None."""
    async with get_db() as db:
        cursor = await db.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row else None


async def get_db_stats() -> dict:
    """Return basic database health statistics."""
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM v_library_health")
        row = await cursor.fetchone()
        stats = dict(row) if row else {}

        # Page info
        page_cur = await db.execute("PRAGMA page_count")
        page_row = await page_cur.fetchone()
        page_size_cur = await db.execute("PRAGMA page_size")
        page_size_row = await page_size_cur.fetchone()

        if page_row and page_size_row:
            stats["db_size_bytes"] = page_row[0] * page_size_row[0]

        return stats
