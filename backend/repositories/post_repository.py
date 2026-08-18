"""
Post Repository
All database operations related to posts, including joins with authors,
media files, and tags. Supports pagination, filtering, and FTS search.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from backend.database.connection import get_db
from backend.models import PostType, SearchQuery
from backend.repositories.base import BaseRepository

logger = logging.getLogger(__name__)


class PostRepository(BaseRepository):
    table_name = "posts"

    async def get_post_full(self, post_id: int) -> Optional[dict[str, Any]]:
        """Fetch a post with author, media files, and tags."""
        async with get_db() as db:
            # Post + author
            cursor = await db.execute(
                """
                SELECT p.*, a.username, a.full_name, a.profile_pic_path,
                       a.is_verified, a.is_private
                FROM posts p
                JOIN authors a ON a.id = p.author_id
                WHERE p.id = ?
                """,
                (post_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            post = dict(row)

            # Media files
            mc = await db.execute(
                "SELECT * FROM media_files WHERE post_id = ? ORDER BY carousel_index, id",
                (post_id,),
            )
            post["media_files"] = [dict(r) for r in await mc.fetchall()]

            # Tags
            tc = await db.execute(
                """
                SELECT t.id, t.name, t.tag_type, pt.confidence, pt.source
                FROM post_tags pt
                JOIN tags t ON t.id = pt.tag_id
                WHERE pt.post_id = ?
                ORDER BY pt.confidence DESC NULLS LAST
                """,
                (post_id,),
            )
            post["tags"] = [dict(r) for r in await tc.fetchall()]

            return post

    async def get_by_shortcode(self, shortcode: str) -> Optional[dict[str, Any]]:
        async with get_db() as db:
            cursor = await db.execute(
                "SELECT * FROM posts WHERE shortcode = ?", (shortcode,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def search(self, query: SearchQuery) -> tuple[int, list[dict[str, Any]]]:
        """
        Full-featured search supporting FTS, filters, and pagination.
        Returns (total_count, posts_list).
        """
        conditions: list[str] = []
        params: list[Any] = []

        # FTS full-text search
        fts_join = ""
        if query.q:
            fts_join = "JOIN posts_fts ON posts_fts.rowid = p.id"
            conditions.append("posts_fts MATCH ?")
            params.append(query.q)

        if query.author:
            conditions.append("a.username LIKE ?")
            params.append(f"%{query.author}%")

        if query.post_type:
            conditions.append("p.post_type = ?")
            params.append(query.post_type.value)

        if query.date_from:
            conditions.append("p.posted_at >= ?")
            params.append(query.date_from.isoformat())

        if query.date_to:
            conditions.append("p.posted_at <= ?")
            params.append(query.date_to.isoformat())

        if query.is_favorite is not None:
            conditions.append("p.is_favorite = ?")
            params.append(1 if query.is_favorite else 0)

        if query.has_ocr is not None:
            if query.has_ocr:
                conditions.append(
                    "EXISTS (SELECT 1 FROM ocr_results o "
                    "JOIN media_files mf ON mf.id = o.media_id "
                    "WHERE mf.post_id = p.id AND o.text IS NOT NULL)"
                )
            else:
                conditions.append(
                    "NOT EXISTS (SELECT 1 FROM ocr_results o "
                    "JOIN media_files mf ON mf.id = o.media_id "
                    "WHERE mf.post_id = p.id)"
                )

        # Tag filter (all specified tags must match)
        if query.tags:
            for tag_name in query.tags:
                conditions.append(
                    """
                    EXISTS (
                        SELECT 1 FROM post_tags pt
                        JOIN tags t ON t.id = pt.tag_id
                        WHERE pt.post_id = p.id AND t.name = ?
                    )
                    """
                )
                params.append(tag_name)

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        allowed_sort = {
            "downloaded_at", "posted_at", "like_count",
            "media_count", "shortcode",
        }
        sort_col = query.sort_by if query.sort_by in allowed_sort else "downloaded_at"
        sort_dir = "ASC" if query.sort_dir == "asc" else "DESC"

        base_sql = f"""
            FROM posts p
            JOIN authors a ON a.id = p.author_id
            {fts_join}
            {where_clause}
        """

        async with get_db() as db:
            count_cur = await db.execute(
                f"SELECT COUNT(DISTINCT p.id) {base_sql}", params
            )
            total = (await count_cur.fetchone())[0]

            offset = (query.page - 1) * query.per_page
            data_sql = f"""
                SELECT DISTINCT
                    p.id, p.shortcode, p.post_type, p.media_count,
                    p.posted_at, p.downloaded_at, p.is_favorite,
                    p.caption,
                    a.username AS author_username,
                    (
                        SELECT mf.thumbnail_path FROM media_files mf
                        WHERE mf.post_id = p.id
                        AND mf.thumbnail_path IS NOT NULL ORDER BY mf.carousel_index, mf.id LIMIT 1
                    ) AS cover_thumbnail
                {base_sql}
                ORDER BY p.{sort_col} {sort_dir}
                LIMIT ? OFFSET ?
            """
            data_cur = await db.execute(data_sql, params + [query.per_page, offset])
            rows = await data_cur.fetchall()
            return total, [dict(r) for r in rows]

    async def set_favorite(self, post_id: int, is_favorite: bool) -> bool:
        return await self.update(post_id, {"is_favorite": 1 if is_favorite else 0})

    async def get_timeline(
        self, page: int = 1, per_page: int = 50
    ) -> tuple[int, list[dict]]:
        offset = (page - 1) * per_page
        async with get_db() as db:
            count_cur = await db.execute("SELECT COUNT(*) FROM posts")
            total = (await count_cur.fetchone())[0]

            cur = await db.execute(
                """
                SELECT p.id, p.shortcode, p.post_type, p.media_count,
                       p.posted_at, p.downloaded_at, p.is_favorite, p.caption,
                       a.username AS author_username,
                       (SELECT mf.thumbnail_path FROM media_files mf
                        WHERE mf.post_id = p.id
                        AND mf.thumbnail_path IS NOT NULL ORDER BY mf.carousel_index, mf.id LIMIT 1
                       ) AS cover_thumbnail
                FROM posts p
                JOIN authors a ON a.id = p.author_id
                ORDER BY p.posted_at DESC NULLS LAST, p.downloaded_at DESC
                LIMIT ? OFFSET ?
                """,
                (per_page, offset),
            )
            rows = await cur.fetchall()
            return total, [dict(r) for r in rows]

    async def get_by_author(
        self, username: str, page: int = 1, per_page: int = 50
    ) -> tuple[int, list[dict]]:
        offset = (page - 1) * per_page
        async with get_db() as db:
            count_cur = await db.execute(
                "SELECT COUNT(*) FROM posts p JOIN authors a ON a.id = p.author_id WHERE a.username = ?",
                (username,),
            )
            total = (await count_cur.fetchone())[0]

            cur = await db.execute(
                """
                SELECT p.id, p.shortcode, p.post_type, p.media_count,
                       p.posted_at, p.downloaded_at, p.is_favorite, p.caption,
                       a.username AS author_username,
                       (SELECT mf.thumbnail_path FROM media_files mf
                        WHERE mf.post_id = p.id
                        AND mf.thumbnail_path IS NOT NULL ORDER BY mf.carousel_index, mf.id LIMIT 1
                       ) AS cover_thumbnail
                FROM posts p
                JOIN authors a ON a.id = p.author_id
                WHERE a.username = ?
                ORDER BY p.posted_at DESC NULLS LAST
                LIMIT ? OFFSET ?
                """,
                (username, per_page, offset),
            )
            rows = await cur.fetchall()
            return total, [dict(r) for r in rows]

    async def get_library_stats(self) -> dict[str, Any]:
        async with get_db() as db:
            cur = await db.execute(
                """
                SELECT
                    COUNT(*)                                        AS total_posts,
                    COUNT(DISTINCT p.author_id)                     AS total_authors,
                    SUM(CASE WHEN p.post_type = 'image'    THEN 1 ELSE 0 END) AS total_images,
                    SUM(CASE WHEN p.post_type = 'video'    THEN 1 ELSE 0 END) AS total_videos,
                    SUM(CASE WHEN p.post_type = 'carousel' THEN 1 ELSE 0 END) AS total_carousels,
                    SUM(CASE WHEN p.post_type = 'reel'     THEN 1 ELSE 0 END) AS total_reels
                FROM posts p
                """
            )
            stats = dict(await cur.fetchone())

            cur2 = await db.execute(
                "SELECT COUNT(*) FROM tags WHERE tag_type = 'ai'"
            )
            stats["total_ai_tags"] = (await cur2.fetchone())[0]

            cur3 = await db.execute(
                "SELECT COUNT(*) FROM ocr_results WHERE text IS NOT NULL"
            )
            stats["total_ocr_texts"] = (await cur3.fetchone())[0]

            cur4 = await db.execute(
                "SELECT COALESCE(SUM(file_size_bytes), 0) FROM media_files"
            )
            stats["total_size_bytes"] = (await cur4.fetchone())[0]

            return stats

    async def add_tag(
        self, post_id: int, tag_id: int, source: str, confidence: float | None = None
    ) -> None:
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO post_tags (post_id, tag_id, confidence, source)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(post_id, tag_id) DO UPDATE SET
                    confidence = excluded.confidence,
                    source = excluded.source
                """,
                (post_id, tag_id, confidence, source),
            )

    async def get_recent(self, limit: int = 20) -> list[dict]:
        async with get_db() as db:
            cur = await db.execute(
                """
                SELECT p.id, p.shortcode, p.post_type, p.media_count,
                       p.posted_at, p.downloaded_at, p.is_favorite, p.caption,
                       a.username AS author_username,
                       (SELECT mf.thumbnail_path FROM media_files mf
                        WHERE mf.post_id = p.id
                        AND mf.thumbnail_path IS NOT NULL ORDER BY mf.carousel_index, mf.id LIMIT 1
                       ) AS cover_thumbnail
                FROM posts p
                JOIN authors a ON a.id = p.author_id
                ORDER BY p.downloaded_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(r) for r in await cur.fetchall()]
