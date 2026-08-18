from __future__ import annotations
import shutil, time, platform
from pathlib import Path
from typing import Any
from fastapi import APIRouter
from backend.config import config
from backend.database.connection import get_db, get_db_stats
from backend.repositories.post_repository import PostRepository
from backend.repositories.media_repository import MediaRepository
from backend.repositories.task_repository import DownloadTaskRepository

router = APIRouter(prefix="/api/stats", tags=["stats"])
_start = time.monotonic()

@router.get("/library")
async def library_stats() -> dict[str, Any]:
    repo  = PostRepository()
    stats = await repo.get_library_stats()
    disk  = shutil.disk_usage(config.data_dir)
    stats.update({"disk_total_bytes":disk.total,"disk_used_bytes":disk.used,
                  "disk_free_bytes":disk.free,"disk_free_pct":round(disk.free/disk.total*100,1)})
    try: stats.update(await get_db_stats())
    except: pass
    return stats

@router.get("/system")
async def system_stats() -> dict[str, Any]:
    stats: dict[str, Any] = {"uptime_seconds":int(time.monotonic()-_start),
                             "platform":platform.system(),"python_version":platform.python_version()}
    try:
        import psutil
        mem = psutil.virtual_memory()
        stats.update({"cpu_pct":psutil.cpu_percent(interval=0.2),"ram_used_bytes":mem.used,
                      "ram_total_bytes":mem.total,"ram_pct":mem.percent})
    except ImportError: stats["psutil_available"] = False
    return stats

@router.get("/health")
async def library_health() -> dict[str, Any]:
    async with get_db() as db:
        cur    = await db.execute("SELECT * FROM v_library_health")
        health = dict(await cur.fetchone())
        missing = 0
        cur2 = await db.execute("SELECT file_path FROM media_files WHERE file_path IS NOT NULL")
        for row in await cur2.fetchall():
            if row[0] and not Path(row[0]).exists(): missing += 1
        health["missing_physical_files"] = missing
        c2 = await db.execute("SELECT COUNT(*) FROM media_files WHERE thumbnail_path IS NULL AND file_type='image'")
        health["missing_thumbnails"] = (await c2.fetchone())[0]
        c3 = await db.execute("SELECT COUNT(*) FROM download_tasks WHERE status='error'")
        health["failed_tasks"] = (await c3.fetchone())[0]
        c4 = await db.execute("SELECT COUNT(*) FROM posts_fts")
        c5 = await db.execute("SELECT COUNT(*) FROM posts")
        fts = (await c4.fetchone())[0]; total = (await c5.fetchone())[0]
        health["fts_posts_indexed"] = fts; health["total_posts_in_table"] = total
        health["fts_in_sync"] = fts == total
        c6 = await db.execute("""SELECT COUNT(*) FROM posts p
            WHERE NOT EXISTS (SELECT 1 FROM media_files m WHERE m.post_id=p.id)""")
        health["empty_posts"] = (await c6.fetchone())[0]
        disk = shutil.disk_usage(config.data_dir)
        health["disk_free_bytes"] = disk.free
        avg_c = await db.execute("SELECT AVG(file_size_bytes) FROM media_files WHERE file_size_bytes>0")
        avg = (await avg_c.fetchone())[0] or 0
        ppd_c = await db.execute("""SELECT COUNT(*)/MAX(1,CAST(
            (julianday('now')-julianday(MIN(downloaded_at))) AS INTEGER)) FROM posts""")
        ppd = (await ppd_c.fetchone())[0] or 0
        appp_c = await db.execute("SELECT AVG(media_count) FROM posts")
        appp = (await appp_c.fetchone())[0] or 1
        bpd = ppd*appp*avg
        health["estimated_days_remaining"] = int(disk.free/bpd) if bpd>0 else None
    return health

@router.get("/health/empty-posts")
async def list_empty_posts() -> dict[str, Any]:
    async with get_db() as db:
        cur = await db.execute("""
            SELECT p.id, p.shortcode, p.original_url, p.post_type, p.downloaded_at, a.username
            FROM posts p JOIN authors a ON a.id=p.author_id
            WHERE NOT EXISTS (SELECT 1 FROM media_files m WHERE m.post_id=p.id)
            ORDER BY p.downloaded_at DESC LIMIT 500""")
        posts = [dict(r) for r in await cur.fetchall()]
    return {"count":len(posts),"posts":posts}

@router.post("/health/fix-empty-posts")
async def fix_empty_posts() -> dict[str, Any]:
    from backend.main import download_manager
    async with get_db() as db:
        cur = await db.execute("""SELECT p.id, p.original_url FROM posts p
            WHERE NOT EXISTS (SELECT 1 FROM media_files m WHERE m.post_id=p.id)""")
        empty = [dict(r) for r in await cur.fetchall()]
        if not empty: return {"status":"ok","fixed":0,"message":"No hay posts vacíos."}
        for post in empty:
            await db.execute("""UPDATE download_tasks SET status='cancelled'
                WHERE url=? AND status NOT IN ('completed')""", (post["original_url"],))
            await db.execute("DELETE FROM post_tags WHERE post_id=?", (post["id"],))
            await db.execute("DELETE FROM posts WHERE id=?", (post["id"],))
    urls = [p["original_url"] for p in empty]
    result = await download_manager.enqueue_batch(urls, priority=5)
    return {"status":"ok","deleted":len(empty),"requeued":len(result["queued"]),
            "message":f"{len(empty)} posts vacíos eliminados y {len(result['queued'])} URLs re-encoladas."}

@router.get("/duplicates")
async def find_duplicates() -> list[dict]:
    return await MediaRepository().find_duplicates()

def _is_real_image(path: Path) -> bool:
    """Magic bytes check — mismo criterio que universal_downloader.py."""
    if not path.exists() or path.stat().st_size < 64:
        return False
    try:
        with open(path, "rb") as f:
            h = f.read(16)
        if h[:2] == b'\xff\xd8': return True
        if h[:8] == b'\x89PNG\r\n\x1a\n': return True
        if h[:6] in (b'GIF87a', b'GIF89a'): return True
        if h[:4] == b'RIFF' and h[8:12] == b'WEBP': return True
        if h[:2] == b'BM': return True
        if h[4:8] == b'ftyp': return True
        return False
    except Exception:
        return False


def _is_real_video(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 64:
        return False
    try:
        with open(path, "rb") as f:
            h = f.read(12)
        if h[4:8] == b'ftyp': return True
        if h[4:8] in (b'mdat', b'moov', b'free'): return True
        if h[:4] == b'\x1a\x45\xdf\xa3': return True
        return False
    except Exception:
        return False


@router.post("/health/regenerate-thumbnails")
async def regenerate_thumbnails() -> dict[str, Any]:
    """
    Busca todos los archivos de imagen/video sin thumbnail_path (o con thumbnail
    apuntando a un archivo que ya no existe en disco) y regenera la miniatura.

    FIX 1: antes de intentar generar la miniatura, valida que el archivo fuente
    sea realmente una imagen/video (magic bytes). Los archivos corruptos que
    quedaron de antes de la validación en universal_downloader.py (HTML
    guardado como .jpg) ya no spamean errores en el log en loop infinito —
    se detectan, se borran del disco y de la base, y se cuentan aparte.

    FIX 2: antes, si el archivo ORIGINAL (no solo la miniatura) ya no existía
    en disco, la fila se saltaba en silencio sin arreglar ni limpiar nada —
    quedaba la referencia colgada mostrando el ícono de imagen rota para
    siempre (caso real: productos de MercadoLibre cuyo archivo original se
    había borrado en alguna limpieza anterior). Ahora esas filas también se
    detectan y se eliminan de la base, igual que los corruptos.
    """
    from backend.services.thumbnail_service import ThumbnailService
    thumbnail_service = ThumbnailService()

    async with get_db() as db:
        cur = await db.execute("""
            SELECT id, post_id, file_path, file_type, thumbnail_path
            FROM media_files
            WHERE file_type IN ('image','video')
        """)
        rows = [dict(r) for r in await cur.fetchall()]

    to_process = []
    orphaned = []  # archivo original ya no existe en disco
    for row in rows:
        if not Path(row["file_path"]).exists():
            orphaned.append(row)
            continue
        thumb_path = row.get("thumbnail_path")
        needs_regen = (not thumb_path) or (not Path(thumb_path).exists())
        if needs_regen:
            to_process.append(row)

    if not to_process and not orphaned:
        return {"status":"ok","regenerated":0,"corrupt_removed":0,"orphaned_removed":0,
                "message":"No hay miniaturas faltantes."}

    regenerated = 0
    corrupt_removed = 0
    orphaned_removed = 0
    failed = 0
    affected_posts = set()

    async with get_db() as db:
        # Archivos originales que ya no existen en disco — limpiar referencia
        for row in orphaned:
            await db.execute("DELETE FROM media_files WHERE id = ?", (row["id"],))
            affected_posts.add(row["post_id"])
            orphaned_removed += 1

        for row in to_process:
            fp = Path(row["file_path"])
            is_valid = _is_real_image(fp) if row["file_type"] == "image" else _is_real_video(fp)

            if not is_valid:
                # Archivo corrupto (HTML guardado como imagen) — limpiar
                try:
                    fp.unlink(missing_ok=True)
                except Exception:
                    pass
                await db.execute("DELETE FROM media_files WHERE id = ?", (row["id"],))
                affected_posts.add(row["post_id"])
                corrupt_removed += 1
                continue

            try:
                thumb = await thumbnail_service.generate(fp)
                if thumb:
                    await db.execute(
                        "UPDATE media_files SET thumbnail_path = ? WHERE id = ?",
                        (str(thumb), row["id"]),
                    )
                    regenerated += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

        # Posts que quedaron sin ningún archivo tras limpiar corruptos/huérfanos
        # -> borrarlos para que puedan re-encolarse limpios
        empty_urls = []
        for post_id in affected_posts:
            cur = await db.execute("SELECT COUNT(*) FROM media_files WHERE post_id = ?", (post_id,))
            count = (await cur.fetchone())[0]
            if count == 0:
                cur2 = await db.execute("SELECT original_url FROM posts WHERE id = ?", (post_id,))
                r = await cur2.fetchone()
                if r:
                    empty_urls.append(r[0])
                await db.execute("DELETE FROM post_tags WHERE post_id = ?", (post_id,))
                await db.execute("DELETE FROM posts WHERE id = ?", (post_id,))

    requeued = 0
    if empty_urls:
        from backend.main import download_manager
        result = await download_manager.enqueue_batch(empty_urls, priority=5)
        requeued = len(result["queued"])

    parts = [f"{regenerated} miniaturas regeneradas"]
    if corrupt_removed:
        parts.append(f"{corrupt_removed} archivos corruptos eliminados")
    if orphaned_removed:
        parts.append(f"{orphaned_removed} referencias huérfanas eliminadas")
    if requeued:
        parts.append(f"{requeued} posts re-encolados")
    if failed:
        parts.append(f"{failed} fallaron")

    return {
        "status": "ok",
        "regenerated": regenerated,
        "corrupt_removed": corrupt_removed,
        "orphaned_removed": orphaned_removed,
        "requeued": requeued,
        "failed": failed,
        "message": ", ".join(parts) + ".",
    }


@router.post("/health/reindex-fts")
async def reindex_fts() -> dict[str, str]:
    async with get_db() as db:
        await db.executescript("""
            INSERT INTO posts_fts(posts_fts) VALUES('rebuild');
            INSERT INTO ocr_fts(ocr_fts) VALUES('rebuild');
            INSERT INTO tags_fts(tags_fts) VALUES('rebuild');""")
    return {"status":"reindexed"}

@router.get("/queue/summary")
async def queue_summary() -> dict[str, Any]:
    return await DownloadTaskRepository().get_queue_summary()
