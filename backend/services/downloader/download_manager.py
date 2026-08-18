from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
import mimetypes
from pathlib import Path
import random
import re
import time
from typing import Any, Callable, Coroutine, Optional, Set

import httpx

from backend.config import config
from backend.models import TaskStatus
from backend.repositories.media_repository import (
    AuthorRepository,
    MediaRepository,
    TagRepository,
)
from backend.repositories.post_repository import PostRepository
from backend.repositories.task_repository import DownloadTaskRepository as TaskRepository
from backend.services.downloader.instagram_client import InstagramClient, InstagramFetchError
from backend.services.downloader.universal_downloader import UniversalDownloader
from backend.utils.logger import TaskLogger

logger = logging.getLogger(__name__)

BroadcastFn = Callable[[dict], Coroutine[Any, Any, None]]
_active_locks: dict[str, asyncio.Lock] = {}
_locks_mutex = asyncio.Lock()

RATE_LIMIT_COOLDOWN_S = 300.0


# ---------------------------------------------------------------------------
# Utilidades internas (sin dependencias a módulos inexistentes)
# ---------------------------------------------------------------------------

def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def get_media_dir(shortcode: str) -> Path:
    d = config.data_dir / "media" / shortcode
    d.mkdir(parents=True, exist_ok=True)
    return d


def relative_to_data(path: Path) -> str:
    try:
        return str(path.relative_to(config.data_dir)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


class URLClassifier:
    @staticmethod
    def classify(url: str) -> str:
        u = url.lower()
        if "instagram.com" in u:
            return "instagram"
        if "youtube.com" in u or "youtu.be" in u:
            return "youtube"
        if "tiktok.com" in u:
            return "tiktok"
        if "twitter.com" in u or "x.com" in u:
            return "twitter"
        if "pinterest.com" in u or "pin.it" in u:
            return "pinterest"
        if "reddit.com" in u:
            return "reddit"
        return "universal"


class HumanBehavior:
    async def wait_between_requests(self) -> None:
        await asyncio.sleep(random.uniform(1.2, 2.5))

    async def wait_between_posts(self) -> None:
        await asyncio.sleep(random.uniform(2.0, 4.0))


class ThumbnailGenerator:
    async def generate(self, file_path: Path) -> Optional[Path]:
        try:
            from PIL import Image
            thumb_dir = config.data_dir / "thumbnails"
            thumb_dir.mkdir(parents=True, exist_ok=True)
            thumb_path = thumb_dir / f"thumb_{file_path.stem}.jpg"
            if thumb_path.exists():
                return thumb_path

            if file_path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                with Image.open(file_path) as img:
                    img.thumbnail((320, 320))
                    img.convert("RGB").save(thumb_path, "JPEG", quality=80)
                return thumb_path
        except Exception:
            pass
        return None


async def _get_lock(key: str) -> asyncio.Lock:
    async with _locks_mutex:
        if key not in _active_locks:
            _active_locks[key] = asyncio.Lock()
        return _active_locks[key]


# ---------------------------------------------------------------------------
# Download Manager
# ---------------------------------------------------------------------------

class DownloadManager:
    def __init__(
        self,
        task_repo: Optional[TaskRepository] = None,
        post_repo: Optional[PostRepository] = None,
        media_repo: Optional[MediaRepository] = None,
        author_repo: Optional[AuthorRepository] = None,
        tag_repo: Optional[TagRepository] = None,
        broadcast_fn: Optional[BroadcastFn] = None,
    ) -> None:
        self._task_repo = task_repo or TaskRepository()
        self._post_repo = post_repo or PostRepository()
        self._media_repo = media_repo or MediaRepository()
        self._author_repo = author_repo or AuthorRepository()
        self._tag_repo = tag_repo or TagRepository()
        self._broadcast = broadcast_fn or self._noop_broadcast
        self._instagram = InstagramClient()
        self._thumbnails = ThumbnailGenerator()
        self._human = HumanBehavior()
        self._semaphore = asyncio.Semaphore(1)
        max_tasks = getattr(config.downloader, "max_concurrent_tasks", getattr(config.downloader, "max_concurrent_downloads", 2))
        self._generic_sem = asyncio.Semaphore(max_tasks)
        self._running = False
        self._worker_task: Optional[asyncio.Task] = None
        self._paused_tasks: Set[int] = set()
        self._cancelled_tasks: Set[int] = set()
        self._rate_limited_until: float = 0.0
        self._instagram_max_concurrent: int = 1

    async def start(self) -> None:
        self._running = True
        await self._instagram.initialize()
        self._worker_task = asyncio.create_task(self._queue_worker())
        logger.info("Download manager started")

    async def stop(self) -> None:
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        logger.info("Download manager stopped")

    async def enqueue(self, urls: list[str], priority: int = 0) -> dict:
        clean_urls = []
        rejected = []
        for raw in urls:
            u = raw.strip()
            if not u or u.startswith("#"):
                continue
            if not (u.startswith("http://") or u.startswith("https://")):
                u = "https://" + u
            clean_urls.append(u)

        if not clean_urls:
            return {"queued": [], "rejected": rejected}

        queued = await self._task_repo.enqueue_many(clean_urls, priority)
        for task_id, url in zip(queued, clean_urls):
            await self._broadcast({"type": "task_queued", "task_id": task_id, "url": url})
        return {"queued": queued, "rejected": rejected}

    async def resume_interrupted(self) -> int:
        interrupted = await self._task_repo.get_pending_on_startup()
        count = 0
        for t in interrupted:
            if t["status"] not in ("completed", "cancelled", "error"):
                await self._task_repo.set_status(t["id"], TaskStatus.QUEUED)
                count += 1
        return count

    async def cancel_all_queued(self) -> int:
        from backend.database.connection import get_db
        async with get_db() as db:
            cur = await db.execute(
                "SELECT id FROM download_tasks WHERE status IN ('queued','paused','analyzing','downloading')"
            )
            ids = [r[0] for r in await cur.fetchall()]
            if ids:
                await db.execute(
                    "UPDATE download_tasks SET status='cancelled' WHERE id IN (%s)" % ",".join("?" * len(ids)),
                    ids,
                )
        return len(ids)

    async def pause_task(self, task_id: int) -> None:
        self._paused_tasks.add(task_id)
        await self._task_repo.set_status(task_id, TaskStatus.PAUSED)
        await self._broadcast({"type": "task_paused", "task_id": task_id})

    async def resume_task(self, task_id: int) -> None:
        self._paused_tasks.discard(task_id)
        await self._task_repo.set_status(task_id, TaskStatus.QUEUED)
        await self._broadcast({"type": "task_resumed", "task_id": task_id})

    async def cancel_task(self, task_id: int) -> None:
        self._cancelled_tasks.add(task_id)
        await self._task_repo.set_status(task_id, TaskStatus.CANCELLED)
        await self._broadcast({"type": "task_cancelled", "task_id": task_id})

    async def get_queue_summary(self) -> dict:
        return await self._task_repo.get_queue_summary()

    async def get_active_tasks(self) -> list[dict]:
        return await self._task_repo.get_active_tasks()

    async def get_instagram_status(self) -> dict:
        return await self._instagram.check_login_status()

    async def login_instagram_session(self, username: str, sessionid: str) -> bool:
        ok = await self._instagram.login_with_sessionid(username, sessionid)
        if ok:
            self._instagram_max_concurrent = 1
            self._semaphore = asyncio.Semaphore(1)
        return ok

    async def _queue_worker(self) -> None:
        ig_active: set[asyncio.Task] = set()
        generic_active: set[asyncio.Task] = set()

        while self._running:
            for pool in (ig_active, generic_active):
                done = {t for t in pool if t.done()}
                for t in done:
                    pool.discard(t)
                    try:
                        t.result()
                    except Exception:
                        pass

            from backend.services import settings_service
            try:
                generic_max = int(settings_service.get("generic_concurrency"))
            except Exception:
                generic_max = 2

            while len(generic_active) < generic_max:
                queued = await self._task_repo.get_next_queued(url_filter="non_instagram")
                if not queued:
                    break
                if queued["id"] in self._paused_tasks or queued["id"] in self._cancelled_tasks:
                    continue
                task = asyncio.create_task(self._process_task(queued["id"], queued["url"]))
                generic_active.add(task)

            pending_generic = await self._task_repo.count_queued(url_filter="non_instagram")
            remaining = self._rate_limited_until - time.monotonic()
            if remaining <= 0 and pending_generic == 0:
                ig_max = self._instagram_max_concurrent
                while len(ig_active) < ig_max:
                    queued = await self._task_repo.get_next_queued(url_filter="instagram")
                    if not queued:
                        break
                    if queued["id"] in self._paused_tasks or queued["id"] in self._cancelled_tasks:
                        continue
                    task = asyncio.create_task(self._process_task(queued["id"], queued["url"]))
                    ig_active.add(task)

            await asyncio.sleep(2.0)

    async def _process_task(self, task_id: int, url: str) -> None:
        source_type = URLClassifier.classify(url)
        logger.info("[CLASSIFY] task=%s url=%s -> source_type=%s", task_id, url, source_type)

        sem = self._generic_sem if source_type != "instagram" else self._semaphore
        async with sem:
            tlog = TaskLogger(task_id, self._task_repo)
            try:
                if source_type == "instagram":
                    await self._run_instagram_pipeline(task_id, url, tlog)
                else:
                    await self._run_universal_pipeline(task_id, url, tlog, source_type)
            except asyncio.CancelledError:
                await self._task_repo.set_status(task_id, TaskStatus.CANCELLED)
            except Exception as exc:
                import traceback
                await tlog.error(f"Error: {exc}", traceback.format_exc())
                logger.error("Task %d failed: %s\n%s", task_id, exc, traceback.format_exc())

                if isinstance(exc, InstagramFetchError):
                    ig_state = getattr(self._instagram, "_session_state", "unknown")
                    logger.warning("[IG_TASK] task=%s state=%s reason=%s url=%s", task_id, ig_state, exc.reason, url)
                    is_rate = exc.reason == "rate_limited"
                    is_fatal = exc.reason in ("private", "not_found", "invalid_session")
                    retry_after_s = exc.retry_after_s
                else:
                    msg = str(exc).lower()
                    is_rate = any(w in msg for w in ("rate limit", "too many requests", "429", "throttl"))
                    is_fatal = any(w in msg for w in ("privad", "private", "no existe", "404", "login_required"))
                    retry_after_s = None

                if is_rate:
                    cooldown = retry_after_s or RATE_LIMIT_COOLDOWN_S
                    self._rate_limited_until = time.monotonic() + cooldown
                    logger.warning("[RATE_LIMIT] Cooldown de %.0fs activado", cooldown)

                task_data = await self._task_repo.get_by_id(task_id)
                attempt = task_data["retry_count"] if task_data else 0
                max_retries = config.downloader.max_retries
                base_delay = config.downloader.retry_delay_base_s
                max_delay = config.downloader.retry_delay_max_s

                if not is_fatal and attempt < max_retries:
                    delay = (retry_after_s or RATE_LIMIT_COOLDOWN_S) if is_rate else min(base_delay * (2 ** attempt), max_delay)
                    await self._task_repo.increment_attempt(task_id, delay)
                    await tlog.warning(f"Reintentando en {delay:.0f}s (intento {attempt+1})")
                else:
                    await self._task_repo.set_status(task_id, TaskStatus.ERROR, error_message=str(exc))
                    await self._broadcast({"type": "task_error", "task_id": task_id, "error": str(exc)})

    async def _run_instagram_pipeline(self, task_id: int, url: str, tlog: TaskLogger) -> None:
        await self._task_repo.set_status(task_id, TaskStatus.ANALYZING)
        await tlog.info("Instagram — analizando URL...")
        shortcode = InstagramClient.extract_shortcode(url)
        if not shortcode:
            raise ValueError(f"No se puede extraer shortcode de: {url}")

        lock = await _get_lock(shortcode)
        async with lock:
            existing = await self._post_repo.get_by_shortcode(shortcode)
            if existing:
                await tlog.info("Ya archivado, saltando.")
                await self._task_repo.set_status(task_id, TaskStatus.COMPLETED, progress_pct=100.0, post_id=existing["id"])
                await self._broadcast({"type": "task_completed", "task_id": task_id, "post_id": existing["id"], "skipped": True})
                return

            await tlog.info("Obteniendo metadatos de Instagram...")
            metadata = await self._instagram.fetch_post_metadata(url)
            await self._task_repo.update(task_id, {"shortcode": metadata.shortcode})

            author_id = await self._author_repo.upsert_author({
                "username": metadata.author,
                "full_name": metadata.full_name,
                "is_private": 1 if metadata.is_private else 0,
                "is_verified": 1 if metadata.is_verified else 0,
                "profile_pic_url": metadata.profile_pic_url,
                "last_updated_at": datetime.now(timezone.utc).isoformat(),
            })

            post_id = await self._post_repo.insert({
                "shortcode": metadata.shortcode,
                "author_id": author_id,
                "post_type": metadata.post_type,
                "caption": metadata.caption,
                "hashtags": json.dumps(metadata.hashtags),
                "mentions": json.dumps(metadata.mentions),
                "location_name": metadata.location_name,
                "location_lat": metadata.location_lat,
                "location_lng": metadata.location_lng,
                "like_count": metadata.like_count,
                "comment_count": metadata.comment_count,
                "media_count": metadata.media_count,
                "original_url": metadata.original_url,
                "posted_at": metadata.posted_at.isoformat() if metadata.posted_at else None,
                "raw_metadata": json.dumps(metadata.raw),
            })
            await self._task_repo.update(task_id, {"post_id": post_id})

            for ht in metadata.hashtags:
                tag_id = await self._tag_repo.get_or_create(ht, "hashtag")
                await self._post_repo.add_tag(post_id, tag_id, "hashtag")

        await self._task_repo.set_status(task_id, TaskStatus.DOWNLOADING)
        await tlog.info(f"Descargando {metadata.media_count} archivo(s)...")
        media_dir = get_media_dir(metadata.shortcode)

        for idx, item in enumerate(metadata.media_items):
            if task_id in self._cancelled_tasks:
                raise asyncio.CancelledError()
            ext = ".mp4" if item["is_video"] else ".jpg"
            dest_file = media_dir / f"{metadata.shortcode}_{idx:02d}{ext}"
            fp = await self._stream_download(task_id, item["url"], dest_file, metadata.media_count, idx)
            sha256 = await asyncio.get_event_loop().run_in_executor(None, compute_sha256, fp)
            dup = await self._media_repo.get_by_hash(sha256)
            if dup:
                fp.unlink(missing_ok=True)
                fp = Path(dup["file_path"])
                if not fp.is_absolute():
                    fp = config.data_dir / fp
            stat = fp.stat()

            try:
                await self._media_repo.insert({
                    "post_id": post_id,
                    "file_path": relative_to_data(fp),
                    "file_name": fp.name,
                    "file_type": "video" if item["is_video"] else "image",
                    "mime_type": detect_mime(fp),
                    "file_size_bytes": stat.st_size,
                    "width_px": item.get("width"),
                    "height_px": item.get("height"),
                    "carousel_index": idx,
                    "sha256_hash": sha256,
                    "is_original": 1,
                })
            except Exception as e:
                if "UNIQUE" in str(e):
                    pass
                else:
                    raise

            progress = ((idx + 1) / metadata.media_count) * 65.0
            await self._task_repo.update_progress(task_id, progress)
            await self._human.wait_between_requests()

        await self._finalize(task_id, post_id, tlog)
        await self._human.wait_between_posts()

    async def _run_universal_pipeline(self, task_id: int, url: str, tlog: TaskLogger, source_type: str) -> None:
        await self._task_repo.set_status(task_id, TaskStatus.ANALYZING)
        await tlog.info(f"{source_type.upper()} — analizando...")
        shortcode = hashlib.md5(url.encode()).hexdigest()[:12]
        lock = await _get_lock(shortcode)

        async with lock:
            existing = await self._post_repo.get_by_shortcode(shortcode)
            if existing:
                await tlog.info("Ya archivado, saltando.")
                await self._task_repo.set_status(task_id, TaskStatus.COMPLETED, progress_pct=100.0, post_id=existing["id"])
                await self._broadcast({"type": "task_completed", "task_id": task_id, "post_id": existing["id"], "skipped": True})
                return

            dest_dir = get_media_dir(shortcode)
            result = await UniversalDownloader(dest_dir).extract(url)

            has_content = bool(
                result.media_files
                or (result.content_text and len(result.content_text.strip()) > 30)
            )
            if not has_content:
                await tlog.info(f"Sin contenido extraíble de {source_type}, saltando.")
                await self._task_repo.set_status(task_id, TaskStatus.COMPLETED, progress_pct=100.0)
                await self._broadcast({"type": "task_completed", "task_id": task_id, "post_id": None, "skipped": True})
                return

            from urllib.parse import urlparse as _urlparse
            domain = _urlparse(url).netloc
            author_id = await self._author_repo.upsert_author({
                "username": (result.author or domain)[:100],
                "full_name": result.author or domain,
                "is_private": 0,
                "is_verified": 0,
                "profile_pic_url": None,
                "last_updated_at": datetime.now(timezone.utc).isoformat(),
            })

            has_video = any(m.get("file_type") == "video" for m in result.media_files)
            post_type = "video" if has_video else ("image" if result.media_files else "unknown")
            caption = result.description or result.content_text[:500] or result.title
            post_id = await self._post_repo.insert({
                "shortcode": shortcode,
                "author_id": author_id,
                "post_type": post_type,
                "caption": caption,
                "hashtags": json.dumps(result.tags),
                "mentions": json.dumps([]),
                "media_count": max(len(result.media_files), 1),
                "original_url": url,
                "posted_at": result.posted_at,
                "raw_metadata": json.dumps({"source_type": source_type, "title": result.title, "domain": domain, **result.metadata}),
            })
            await self._task_repo.update(task_id, {"post_id": post_id, "shortcode": shortcode})

            for tag in result.tags[:20]:
                tag_id = await self._tag_repo.get_or_create(tag, "hashtag")
                await self._post_repo.add_tag(post_id, tag_id, "hashtag")

        await self._task_repo.set_status(task_id, TaskStatus.DOWNLOADING)
        total = max(len(result.media_files), 1)

        for idx, mf in enumerate(result.media_files):
            fp = Path(mf["path"]).resolve()
            if not fp.exists():
                continue
            sha256 = await asyncio.get_event_loop().run_in_executor(None, compute_sha256, fp)
            dup = await self._media_repo.get_by_hash(sha256)
            if dup:
                fp.unlink(missing_ok=True)
                fp = Path(dup["file_path"])
                if not fp.is_absolute():
                    fp = config.data_dir / fp
            stat = fp.stat()

            try:
                await self._media_repo.insert({
                    "post_id": post_id,
                    "file_path": relative_to_data(fp),
                    "file_name": fp.name,
                    "file_type": mf.get("file_type", "image"),
                    "mime_type": mf.get("mime_type", detect_mime(fp)),
                    "file_size_bytes": stat.st_size,
                    "width_px": mf.get("width"),
                    "height_px": mf.get("height"),
                    "duration_s": mf.get("duration_s"),
                    "carousel_index": mf.get("carousel_index", idx),
                    "sha256_hash": sha256,
                    "is_original": 1,
                })
            except Exception as e:
                if "UNIQUE" in str(e):
                    continue
                raise

            progress = ((idx + 1) / total) * 65.0
            await self._task_repo.update_progress(task_id, progress)

        if not result.media_files and result.content_text:
            txt = get_media_dir(shortcode) / f"{shortcode}_content.txt"
            txt.write_text(f"{result.title}\n\n{result.content_text}", encoding="utf-8")
            sha256 = await asyncio.get_event_loop().run_in_executor(None, compute_sha256, txt)
            try:
                await self._media_repo.insert({
                    "post_id": post_id,
                    "file_path": relative_to_data(txt),
                    "file_name": txt.name,
                    "file_type": "image",
                    "mime_type": "text/plain",
                    "file_size_bytes": txt.stat().st_size,
                    "carousel_index": 0,
                    "sha256_hash": sha256,
                    "is_original": 1,
                })
            except Exception:
                pass

        await self._finalize(task_id, post_id, tlog)

    async def _finalize(self, task_id: int, post_id: int, tlog: TaskLogger) -> None:
        await self._task_repo.set_status(task_id, TaskStatus.GENERATING_THUMBNAILS)
        await tlog.info("Generando miniaturas...")
        media_rows = await self._media_repo.get_by_post(post_id)

        for i, row in enumerate(media_rows):
            fp = Path(row["file_path"])
            if not fp.is_absolute():
                fp = config.data_dir / fp
            if fp.exists() and row["file_type"] in ("image", "video"):
                thumb = await self._thumbnails.generate(fp)
                if thumb:
                    await self._media_repo.update_thumbnail(row["id"], relative_to_data(thumb))
            progress = 65.0 + ((i + 1) / max(len(media_rows), 1)) * 15.0
            await self._task_repo.update_progress(task_id, progress)

        await self._task_repo.set_status(task_id, TaskStatus.PROCESSING_AI)
        asyncio.create_task(self._run_ai_analysis(post_id, task_id, tlog))
        await self._task_repo.set_status(task_id, TaskStatus.COMPLETED, progress_pct=100.0, post_id=post_id)
        await tlog.info("¡Finalizado!")
        await self._broadcast({"type": "task_completed", "task_id": task_id, "post_id": post_id})

    async def _stream_download(self, task_id: int, url: str, dest: Path, total_items: int = 1, index: int = 0) -> Path:
        dest = Path(dest).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        headers = {"User-Agent": config.downloader.user_agent, "Referer": "https://www.instagram.com/"}
        start = time.monotonic()
        written = 0
        last_broadcast = 0.0

        async with httpx.AsyncClient(follow_redirects=True, timeout=config.downloader.request_timeout_s) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                resp.raise_for_status()
                total_bytes = int(resp.headers.get("content-length", 0))
                with open(dest, "wb") as f:
                    async for chunk in resp.aiter_bytes(config.downloader.chunk_size_bytes):
                        if task_id in self._cancelled_tasks:
                            dest.unlink(missing_ok=True)
                            raise asyncio.CancelledError()
                        f.write(chunk)
                        written += len(chunk)

                        now = time.monotonic()
                        if now - last_broadcast >= 0.5:
                            elapsed = now - start
                            speed_bps = written / elapsed if elapsed > 0 else 0
                            await self._broadcast({
                                "type": "file_progress",
                                "task_id": task_id,
                                "bytes_downloaded": written,
                                "bytes_total": total_bytes,
                                "speed_bps": speed_bps,
                            })
                            last_broadcast = now
        return dest

    async def _run_ai_analysis(self, post_id: int, task_id: int, tlog: TaskLogger) -> None:
        try:
            from backend.services.ai.tag_generator import TagGeneratorService
            await TagGeneratorService(self._post_repo, self._media_repo, self._tag_repo).analyze_post(post_id, task_id, self._broadcast, tlog)
        except Exception as exc:
            await tlog.error(f"Error IA: {exc}")

    @staticmethod
    async def _noop_broadcast(event: dict) -> None:
        pass