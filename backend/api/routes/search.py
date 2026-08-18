"""
Search API Routes
Full-featured search endpoint combining FTS5, tag filters,
author/date/type filters, and OCR text search.
"""

from __future__ import annotations

from typing import Any, Optional
from fastapi import APIRouter, Query
from backend.models import PostType, SearchQuery
from backend.repositories.post_repository import PostRepository
from backend.repositories.media_repository import TagRepository
from backend.database.connection import get_db

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("")
async def search(
    q:           Optional[str]      = Query(None),
    author:      Optional[str]      = Query(None),
    post_type:   Optional[PostType] = Query(None),
    tags:        Optional[str]      = Query(None),   # comma-separated
    date_from:   Optional[str]      = Query(None),
    date_to:     Optional[str]      = Query(None),
    is_favorite: Optional[bool]     = Query(None),
    has_ocr:     Optional[bool]     = Query(None),
    page:        int                = Query(1, ge=1),
    per_page:    int                = Query(50, ge=1, le=200),
    sort_by:     str                = Query("downloaded_at"),
    sort_dir:    str                = Query("desc"),
) -> dict[str, Any]:

    tag_list = [t.strip() for t in tags.split(",")] if tags else None

    from datetime import datetime
    dt_from = datetime.fromisoformat(date_from) if date_from else None
    dt_to   = datetime.fromisoformat(date_to)   if date_to   else None

    sq = SearchQuery(
        q=q, author=author, post_type=post_type,
        tags=tag_list, date_from=dt_from, date_to=dt_to,
        is_favorite=is_favorite, has_ocr=has_ocr,
        page=page, per_page=per_page,
        sort_by=sort_by, sort_dir=sort_dir,
    )

    repo = PostRepository()
    total, posts = await repo.search(sq)

    return {
        "total":    total,
        "page":     page,
        "per_page": per_page,
        "pages":    (total + per_page - 1) // per_page,
        "posts":    posts,
        "query":    {"q": q, "author": author, "post_type": post_type,
                     "tags": tag_list, "is_favorite": is_favorite},
    }


@router.get("/suggestions")
async def search_suggestions(q: str = Query("", min_length=1)) -> dict[str, Any]:
    """
    Returns autocomplete suggestions: matching tags, hashtags, authors, OCR text.
    Used for instant search-as-you-type.
    """
    q_lower = q.strip().lower()
    results: dict[str, list] = {"tags": [], "authors": [], "hashtags": []}

    async with get_db() as db:
        # Tags (AI + manual)
        cur = await db.execute(
            """
            SELECT t.name, t.tag_type, COUNT(pt.post_id) AS cnt
            FROM tags t LEFT JOIN post_tags pt ON pt.tag_id = t.id
            WHERE t.name LIKE ? AND t.tag_type != 'hashtag'
            GROUP BY t.id ORDER BY cnt DESC LIMIT 8
            """,
            (f"%{q_lower}%",),
        )
        results["tags"] = [dict(r) for r in await cur.fetchall()]

        # Hashtags
        cur2 = await db.execute(
            """
            SELECT t.name, COUNT(pt.post_id) AS cnt
            FROM tags t LEFT JOIN post_tags pt ON pt.tag_id = t.id
            WHERE t.name LIKE ? AND t.tag_type = 'hashtag'
            GROUP BY t.id ORDER BY cnt DESC LIMIT 8
            """,
            (f"%{q_lower}%",),
        )
        results["hashtags"] = [dict(r) for r in await cur2.fetchall()]

        # Authors
        cur3 = await db.execute(
            "SELECT username, full_name FROM authors WHERE username LIKE ? LIMIT 5",
            (f"%{q_lower}%",),
        )
        results["authors"] = [dict(r) for r in await cur3.fetchall()]

    return results


@router.get("/by-color")
async def search_by_color(hex_color: str = Query(...)) -> list[dict]:
    """Find posts containing images with a specific dominant color."""
    hex_color = hex_color.lower().lstrip("#")
    hex_color = f"#{hex_color}"
    async with get_db() as db:
        cur = await db.execute(
            """
            SELECT DISTINCT p.id, p.shortcode, p.post_type, p.downloaded_at,
                   a.username AS author_username,
                   (SELECT mf.thumbnail_path FROM media_files mf
                    WHERE mf.post_id = p.id ORDER BY mf.id LIMIT 1
                   ) AS cover_thumbnail
            FROM color_palettes cp
            JOIN media_files mf ON mf.id = cp.media_id
            JOIN posts p ON p.id = mf.post_id
            JOIN authors a ON a.id = p.author_id
            WHERE cp.hex_color = ?
            ORDER BY p.downloaded_at DESC
            LIMIT 100
            """,
            (hex_color,),
        )
        return [dict(r) for r in await cur.fetchall()]


@router.get("/by-ocr")
async def search_by_ocr(q: str = Query(..., min_length=2)) -> list[dict]:
    """Full-text search within OCR-extracted text."""
    async with get_db() as db:
        cur = await db.execute(
            """
            SELECT DISTINCT p.id, p.shortcode, p.post_type, p.downloaded_at,
                   a.username AS author_username,
                   o.text AS ocr_snippet,
                   (SELECT mf.thumbnail_path FROM media_files mf
                    WHERE mf.post_id = p.id ORDER BY mf.id LIMIT 1
                   ) AS cover_thumbnail
            FROM ocr_fts
            JOIN ocr_results o ON o.id = ocr_fts.rowid
            JOIN media_files mf ON mf.id = o.media_id
            JOIN posts p ON p.id = mf.post_id
            JOIN authors a ON a.id = p.author_id
            WHERE ocr_fts MATCH ?
            ORDER BY rank
            LIMIT 100
            """,
            (q,),
        )
        return [dict(r) for r in await cur.fetchall()]
