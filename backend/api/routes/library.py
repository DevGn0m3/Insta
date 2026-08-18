"""
Library API Routes
Endpoints for browsing the archived content: posts, authors,
collections, favorites, timeline, and individual post details.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from backend.models import CollectionCreate, PostType, SearchQuery
from backend.repositories.post_repository import PostRepository
from backend.repositories.media_repository import AuthorRepository, MediaRepository, TagRepository
from backend.database.connection import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/library", tags=["library"])


# ── Posts ─────────────────────────────────────────────────────────────────────

@router.get("/posts")
async def list_posts(
    page:        int                = Query(1, ge=1),
    per_page:    int                = Query(50, ge=1, le=200),
    sort_by:     str                = Query("downloaded_at"),
    sort_dir:    str                = Query("desc"),
    post_type:   Optional[PostType] = Query(None),
    is_favorite: Optional[bool]     = Query(None),
    author:      Optional[str]      = Query(None),  # también sirve como filtro de dominio
) -> dict[str, Any]:
    """
    List posts with full filter support: post_type, is_favorite, author/domain,
    plus sorting and pagination.
    """
    repo = PostRepository()
    q = SearchQuery(
        page=page,
        per_page=per_page,
        sort_by=sort_by,
        sort_dir=sort_dir,
        post_type=post_type,
        is_favorite=is_favorite,
        author=author,
    )
    total, posts = await repo.search(q)
    return {
        "total": total, "page": page, "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "posts": posts,
    }


@router.get("/domains")
async def list_domains() -> list[dict[str, Any]]:
    """
    Dominios de origen para posts que NO son de Instagram (para el filtro
    de dominio en la Biblioteca). El username del autor ya ES el dominio
    para sitios genéricos/noticias/LinkedIn (así los guarda download_manager
    cuando el extractor no reporta un autor explícito).
    """
    async with get_db() as db:
        cur = await db.execute(
            """
            SELECT a.username AS domain, COUNT(p.id) AS post_count
            FROM authors a
            JOIN posts p ON p.author_id = a.id
            WHERE a.username LIKE '%.%'
              AND a.username NOT LIKE '%instagram.com%'
            GROUP BY a.username
            ORDER BY post_count DESC
            """
        )
        return [dict(r) for r in await cur.fetchall()]


@router.get("/posts/recent")
async def recent_posts(limit: int = Query(20, ge=1, le=100)) -> list[dict]:
    repo = PostRepository()
    return await repo.get_recent(limit)


@router.get("/posts/timeline")
async def timeline(
    page:     int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    repo = PostRepository()
    total, posts = await repo.get_timeline(page, per_page)
    return {
        "total": total, "page": page, "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "posts": posts,
    }


@router.get("/posts/{post_id}")
async def get_post(post_id: int) -> dict[str, Any]:
    repo = PostRepository()
    post = await repo.get_post_full(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


@router.patch("/posts/{post_id}/favorite")
async def toggle_favorite(post_id: int, payload: dict[str, bool]) -> dict[str, Any]:
    repo = PostRepository()
    ok = await repo.set_favorite(post_id, payload.get("is_favorite", True))
    if not ok:
        raise HTTPException(status_code=404, detail="Post not found")
    return {"post_id": post_id, "is_favorite": payload.get("is_favorite", True)}


@router.patch("/posts/{post_id}/notes")
async def update_notes(post_id: int, payload: dict[str, str]) -> dict[str, Any]:
    repo = PostRepository()
    await repo.update(post_id, {"notes": payload.get("notes", "")})
    return {"post_id": post_id, "notes": payload.get("notes", "")}


@router.delete("/posts/{post_id}")
async def delete_post(post_id: int) -> dict[str, Any]:
    """
    Borra la card completa: registro del post en la DB (cascada elimina
    media_files/post_tags vía ON DELETE CASCADE) Y los archivos físicos
    asociados (media + thumbnails) del disco.
    """
    async with get_db() as db:
        cur = await db.execute(
            "SELECT file_path, thumbnail_path FROM media_files WHERE post_id = ?",
            (post_id,),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    repo = PostRepository()
    ok = await repo.delete(post_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Post not found")

    deleted_files = 0
    for row in rows:
        for key in ("file_path", "thumbnail_path"):
            p = row.get(key)
            if p:
                try:
                    Path(p).unlink(missing_ok=True)
                    deleted_files += 1
                except Exception as exc:
                    logger.warning("No se pudo borrar %s: %s", p, exc)

    return {"status": "deleted", "post_id": post_id, "files_deleted": deleted_files}


@router.delete("/media/{media_id}")
async def delete_media(media_id: int) -> dict[str, Any]:
    """
    Borra UN archivo individual dentro de una card: su registro en
    media_files y sus archivos físicos (original + thumbnail). NO toca
    la card (post) ni el resto de sus archivos.
    """
    async with get_db() as db:
        cur = await db.execute(
            "SELECT id, post_id, file_path, thumbnail_path FROM media_files WHERE id = ?",
            (media_id,),
        )
        row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Media not found")
        row = dict(row)

        await db.execute("DELETE FROM media_files WHERE id = ?", (media_id,))
        # Reflejar el conteo real de archivos restantes en la card
        await db.execute(
            "UPDATE posts SET media_count = (SELECT COUNT(*) FROM media_files WHERE post_id = ?) WHERE id = ?",
            (row["post_id"], row["post_id"]),
        )

    for key in ("file_path", "thumbnail_path"):
        p = row.get(key)
        if p:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("No se pudo borrar %s: %s", p, exc)

    return {"status": "deleted", "media_id": media_id, "post_id": row["post_id"]}


# ── Authors ───────────────────────────────────────────────────────────────────

@router.get("/authors")
async def list_authors() -> list[dict]:
    repo = AuthorRepository()
    return await repo.get_all_with_post_count()


@router.get("/authors/{username}/posts")
async def author_posts(
    username: str,
    page:     int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    repo = PostRepository()
    total, posts = await repo.get_by_author(username, page, per_page)
    return {
        "total": total, "page": page, "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "posts": posts,
        "author": username,
    }


# ── Tags ──────────────────────────────────────────────────────────────────────

@router.get("/tags")
async def list_tags(
    tag_type: Optional[str] = Query(None),
    limit:    int           = Query(100, ge=1, le=500),
) -> list[dict]:
    repo = TagRepository()
    return await repo.get_top_tags(tag_type=tag_type, limit=limit)


@router.get("/posts/{post_id}/tags")
async def get_post_tags(post_id: int) -> list[dict]:
    async with get_db() as db:
        cur = await db.execute(
            """
            SELECT t.id, t.name, t.tag_type, pt.confidence, pt.source
            FROM post_tags pt JOIN tags t ON t.id = pt.tag_id
            WHERE pt.post_id = ?
            ORDER BY pt.confidence DESC NULLS LAST
            """,
            (post_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


@router.post("/posts/{post_id}/tags")
async def add_manual_tag(post_id: int, payload: dict[str, str]) -> dict[str, Any]:
    tag_name = payload.get("name", "").strip().lower()
    if not tag_name:
        raise HTTPException(status_code=400, detail="Tag name required")
    tag_repo = TagRepository()
    post_repo = PostRepository()
    tag_id = await tag_repo.get_or_create(tag_name, "manual")
    await post_repo.add_tag(post_id, tag_id, "manual")
    return {"post_id": post_id, "tag": tag_name, "status": "added"}


# ── Collections ───────────────────────────────────────────────────────────────

@router.get("/collections")
async def list_collections() -> list[dict]:
    async with get_db() as db:
        cur = await db.execute(
            """
            SELECT c.*, COUNT(cp.post_id) AS post_count
            FROM collections c
            LEFT JOIN collection_posts cp ON cp.collection_id = c.id
            GROUP BY c.id
            ORDER BY c.updated_at DESC
            """
        )
        return [dict(r) for r in await cur.fetchall()]


@router.post("/collections")
async def create_collection(payload: CollectionCreate) -> dict[str, Any]:
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO collections (name, description) VALUES (?, ?)",
            (payload.name, payload.description),
        )
        return {"id": cur.lastrowid, "name": payload.name}


@router.post("/collections/{collection_id}/posts/{post_id}")
async def add_to_collection(collection_id: int, post_id: int) -> dict[str, str]:
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO collection_posts (collection_id, post_id)
            VALUES (?, ?)
            ON CONFLICT(collection_id, post_id) DO NOTHING
            """,
            (collection_id, post_id),
        )
        await db.execute(
            "UPDATE collections SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
            (collection_id,),
        )
    return {"status": "added"}


@router.delete("/collections/{collection_id}/posts/{post_id}")
async def remove_from_collection(collection_id: int, post_id: int) -> dict[str, str]:
    async with get_db() as db:
        await db.execute(
            "DELETE FROM collection_posts WHERE collection_id = ? AND post_id = ?",
            (collection_id, post_id),
        )
    return {"status": "removed"}


@router.get("/collections/{collection_id}/posts")
async def collection_posts(
    collection_id: int,
    page:     int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    offset = (page - 1) * per_page
    async with get_db() as db:
        count_cur = await db.execute(
            "SELECT COUNT(*) FROM collection_posts WHERE collection_id = ?",
            (collection_id,),
        )
        total = (await count_cur.fetchone())[0]
        cur = await db.execute(
            """
            SELECT p.id, p.shortcode, p.post_type, p.media_count,
                   p.posted_at, p.downloaded_at, p.is_favorite, p.caption,
                   a.username AS author_username,
                   (SELECT mf.thumbnail_path FROM media_files mf
                    WHERE mf.post_id = p.id
                    ORDER BY mf.carousel_index, mf.id LIMIT 1
                   ) AS cover_thumbnail
            FROM collection_posts cp
            JOIN posts p ON p.id = cp.post_id
            JOIN authors a ON a.id = p.author_id
            WHERE cp.collection_id = ?
            ORDER BY cp.sort_order, cp.added_at DESC
            LIMIT ? OFFSET ?
            """,
            (collection_id, per_page, offset),
        )
        posts = [dict(r) for r in await cur.fetchall()]
    return {"total": total, "page": page, "per_page": per_page, "posts": posts}


# ── Favorites ─────────────────────────────────────────────────────────────────

@router.get("/favorites")
async def list_favorites(
    page:     int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    repo = PostRepository()
    q = SearchQuery(is_favorite=True, page=page, per_page=per_page)
    total, posts = await repo.search(q)
    return {
        "total": total, "page": page, "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "posts": posts,
    }
