"""
Author Repository
"""
from __future__ import annotations
from typing import Any, Optional
from backend.database.connection import get_db
from backend.repositories.base import BaseRepository


class AuthorRepository(BaseRepository):
    table_name = "authors"

    async def get_by_username(self, username: str) -> Optional[dict[str, Any]]:
        async with get_db() as db:
            cur = await db.execute(
                "SELECT * FROM authors WHERE username = ? COLLATE NOCASE", (username,)
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def upsert_author(self, data: dict[str, Any]) -> int:
        update_cols = [
            "full_name", "bio", "profile_pic_url", "is_private", "is_verified",
            "follower_count", "following_count", "post_count", "external_url",
            "last_updated_at",
        ]
        return await self.upsert(data, ["username"], update_cols)

    async def get_all_with_post_count(self) -> list[dict[str, Any]]:
        async with get_db() as db:
            cur = await db.execute(
                """
                SELECT a.*, COUNT(p.id) AS archived_posts
                FROM authors a
                LEFT JOIN posts p ON p.author_id = a.id
                GROUP BY a.id
                ORDER BY archived_posts DESC
                """
            )
            return [dict(r) for r in await cur.fetchall()]


class MediaRepository(BaseRepository):
    table_name = "media_files"

    async def get_by_post(self, post_id: int) -> list[dict[str, Any]]:
        async with get_db() as db:
            cur = await db.execute(
                "SELECT * FROM media_files WHERE post_id = ? ORDER BY carousel_index, id",
                (post_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def get_by_hash(self, sha256: str) -> Optional[dict[str, Any]]:
        async with get_db() as db:
            cur = await db.execute(
                "SELECT * FROM media_files WHERE sha256_hash = ? LIMIT 1", (sha256,)
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def update_thumbnail(self, media_id: int, thumbnail_path: str) -> None:
        await self.update(media_id, {"thumbnail_path": thumbnail_path})

    async def get_missing_thumbnails(self, limit: int = 50) -> list[dict]:
        async with get_db() as db:
            cur = await db.execute(
                """
                SELECT * FROM media_files
                WHERE thumbnail_path IS NULL
                  AND file_type IN ('image', 'video')
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def find_duplicates(self) -> list[dict]:
        async with get_db() as db:
            cur = await db.execute(
                """
                SELECT sha256_hash, COUNT(*) AS cnt, GROUP_CONCAT(id) AS ids
                FROM media_files
                WHERE sha256_hash IS NOT NULL
                GROUP BY sha256_hash
                HAVING cnt > 1
                """
            )
            return [dict(r) for r in await cur.fetchall()]


class TagRepository(BaseRepository):
    table_name = "tags"

    async def get_or_create(self, name: str, tag_type: str) -> int:
        async with get_db() as db:
            cur = await db.execute(
                "SELECT id FROM tags WHERE name = ? COLLATE NOCASE AND tag_type = ?",
                (name.lower().strip(), tag_type),
            )
            row = await cur.fetchone()
            if row:
                return row[0]
            ins = await db.execute(
                "INSERT INTO tags (name, tag_type) VALUES (?, ?)",
                (name.lower().strip(), tag_type),
            )
            return ins.lastrowid

    async def get_top_tags(self, tag_type: str | None = None, limit: int = 50) -> list[dict]:
        async with get_db() as db:
            if tag_type:
                cur = await db.execute(
                    """
                    SELECT t.id, t.name, t.tag_type, COUNT(pt.post_id) AS usage_count
                    FROM tags t
                    LEFT JOIN post_tags pt ON pt.tag_id = t.id
                    WHERE t.tag_type = ?
                    GROUP BY t.id
                    ORDER BY usage_count DESC LIMIT ?
                    """,
                    (tag_type, limit),
                )
            else:
                cur = await db.execute(
                    """
                    SELECT t.id, t.name, t.tag_type, COUNT(pt.post_id) AS usage_count
                    FROM tags t
                    LEFT JOIN post_tags pt ON pt.tag_id = t.id
                    GROUP BY t.id
                    ORDER BY usage_count DESC LIMIT ?
                    """,
                    (limit,),
                )
            return [dict(r) for r in await cur.fetchall()]

    async def save_ocr(self, media_id: int, text: str, confidence: float, language: str) -> None:
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO ocr_results (media_id, text, confidence, language)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(media_id) DO UPDATE SET
                    text = excluded.text,
                    confidence = excluded.confidence,
                    language = excluded.language,
                    processed_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
                """,
                (media_id, text, confidence, language),
            )

    async def save_color_palette(self, media_id: int, colors: list[dict]) -> None:
        async with get_db() as db:
            await db.execute(
                "DELETE FROM color_palettes WHERE media_id = ?", (media_id,)
            )
            for i, c in enumerate(colors):
                await db.execute(
                    """
                    INSERT INTO color_palettes (media_id, hex_color, percentage, color_name, sort_order)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (media_id, c["hex"], c["pct"], c.get("name"), i),
                )
