"""
Base Repository
Generic async CRUD operations. All concrete repositories extend this class.
Implements the Repository Pattern for clean separation of data access logic.
"""

from __future__ import annotations

import logging
from typing import Any, Generic, Optional, TypeVar

import aiosqlite

from backend.database.connection import get_db

logger = logging.getLogger(__name__)

T = TypeVar("T")


class BaseRepository(Generic[T]):
    """
    Abstract base providing common database operations.
    Subclasses define table_name, id_column, and mapping logic.
    """

    table_name: str = ""
    id_column:  str = "id"

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _row_to_dict(self, row: aiosqlite.Row) -> dict[str, Any]:
        return dict(row)

    def _build_where_clause(
        self, filters: dict[str, Any]
    ) -> tuple[str, list[Any]]:
        """Build a WHERE clause from a dict of {column: value}."""
        if not filters:
            return "", []
        parts = [f"{col} = ?" for col in filters]
        return "WHERE " + " AND ".join(parts), list(filters.values())

    # ── Core CRUD ─────────────────────────────────────────────────────────────

    async def get_by_id(self, record_id: int) -> Optional[dict[str, Any]]:
        async with get_db() as db:
            cursor = await db.execute(
                f"SELECT * FROM {self.table_name} WHERE {self.id_column} = ?",
                (record_id,),
            )
            row = await cursor.fetchone()
            return self._row_to_dict(row) if row else None

    async def get_all(
        self,
        limit: int = 100,
        offset: int = 0,
        order_by: str = "id",
        order_dir: str = "DESC",
    ) -> list[dict[str, Any]]:
        sql = (
            f"SELECT * FROM {self.table_name} "
            f"ORDER BY {order_by} {order_dir} "
            f"LIMIT ? OFFSET ?"
        )
        async with get_db() as db:
            cursor = await db.execute(sql, (limit, offset))
            rows = await cursor.fetchall()
            return [self._row_to_dict(r) for r in rows]

    async def count(self, filters: dict[str, Any] | None = None) -> int:
        where_clause, params = self._build_where_clause(filters or {})
        sql = f"SELECT COUNT(*) FROM {self.table_name} {where_clause}"
        async with get_db() as db:
            cursor = await db.execute(sql, params)
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def insert(self, data: dict[str, Any]) -> int:
        """Insert a row and return the new rowid."""
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" * len(data))
        sql = f"INSERT INTO {self.table_name} ({cols}) VALUES ({placeholders})"
        async with get_db() as db:
            cursor = await db.execute(sql, list(data.values()))
            return cursor.lastrowid

    async def update(
        self, record_id: int, data: dict[str, Any]
    ) -> bool:
        """Update specific columns for a row. Returns True if a row was modified."""
        if not data:
            return False
        set_clause = ", ".join(f"{col} = ?" for col in data)
        sql = (
            f"UPDATE {self.table_name} SET {set_clause} "
            f"WHERE {self.id_column} = ?"
        )
        async with get_db() as db:
            cursor = await db.execute(sql, [*data.values(), record_id])
            return cursor.rowcount > 0

    async def delete(self, record_id: int) -> bool:
        async with get_db() as db:
            cursor = await db.execute(
                f"DELETE FROM {self.table_name} WHERE {self.id_column} = ?",
                (record_id,),
            )
            return cursor.rowcount > 0

    async def exists(self, filters: dict[str, Any]) -> bool:
        where_clause, params = self._build_where_clause(filters)
        sql = f"SELECT 1 FROM {self.table_name} {where_clause} LIMIT 1"
        async with get_db() as db:
            cursor = await db.execute(sql, params)
            return await cursor.fetchone() is not None

    async def upsert(
        self,
        data: dict[str, Any],
        conflict_columns: list[str],
        update_columns: list[str] | None = None,
    ) -> int:
        """
        INSERT OR REPLACE with partial update on conflict.
        Returns the rowid of the inserted/updated row.
        """
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" * len(data))
        conflict_clause = ", ".join(conflict_columns)

        if update_columns:
            updates = ", ".join(f"{c} = excluded.{c}" for c in update_columns)
            on_conflict = f"ON CONFLICT({conflict_clause}) DO UPDATE SET {updates}"
        else:
            on_conflict = f"ON CONFLICT({conflict_clause}) DO NOTHING"

        sql = (
            f"INSERT INTO {self.table_name} ({cols}) VALUES ({placeholders}) "
            f"{on_conflict}"
        )
        async with get_db() as db:
            cursor = await db.execute(sql, list(data.values()))
            # If DO NOTHING fired, fetch the existing row id
            if cursor.lastrowid == 0:
                where, params = self._build_where_clause(
                    {c: data[c] for c in conflict_columns}
                )
                cur2 = await db.execute(
                    f"SELECT {self.id_column} FROM {self.table_name} {where}",
                    params,
                )
                row = await cur2.fetchone()
                return row[0] if row else 0
            return cursor.lastrowid
