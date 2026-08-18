from __future__ import annotations
import asyncio, hashlib, json, logging, time
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional
import httpx
from backend.config import config
from backend.models import TaskStatus
from backend.repositories.post_repository import PostRepository
from backend.repositories.media_repository import AuthorRepository, MediaRepository, TagRepository
from backend.repositories.task_repository import DownloadTaskRepository
from backend.services.downloader.universal_downloader import UniversalDownloader, URLClassifier
from backend.services.downloader.instagram_client import InstagramClient
from backend.services.thumbnail_service import ThumbnailService
from backend.utils.file_utils import compute_sha256, detect_mime, get_media_dir
from backend.utils.human_behavior import HumanBehaviorSimulator
from backend.utils.logger import TaskLogger

logger = logging.getLogger(__name__)
BroadcastFn = Callable[[dict], Coroutine[Any, Any, None]]
RATE_LIMIT_COOLDOWN_S = 360

_shortcode_locks: dict[str, asyncio.Lock] = {}
_shortcode_locks_mutex = asyncio.Lock()

async def _get_lock(key: str) -> asyncio.Lock:
    async with _shortcode_locks_mutex:
        if key not in _shortcode_locks:
            _shortcode_locks[key] = asyncio.Lock()
        return _shortcode_locks[key]

class DownloadManager:
    def __init__(self, broadcast_fn: Optional[BroadcastFn] = None) -> None:
        self._broadcast = broadcast_fn or self._noop_broadcast
        self._task_repo   = DownloadTaskRepository()
        self._post_repo   = PostRepository()
        self._author_repo = AuthorRepository()
        self._media_repo  = MediaRepository()
        self._tag_repo    = TagRepository()
        self._instagram   = InstagramClient()
        self._thumbnails  = ThumbnailService()
        self._human       = HumanBehaviorSimulator()
        self._semaphore   = asyncio.Semaphore(1)
        self._generic_sem = asyncio.Semaphore(1)
        # FIX: Semaphore._value es la cantidad de permisos DISPONIBLES en
        # este instante (baja cuando hay tareas activas usándolo), NO la
        # capacidad máxima configurada. Leerlo como "max" en _queue_worker
        # podía devolver 0 mientras había una tarea corriendo, bloqueando
        # la creación de nuevas tareas genéricas aunque hubiera cupo real.
        # Guardamos la capacidad máxima aparte, de forma explícita.
        self._instagram_max_concurrent = 1
        self._generic_max_concurrent   = 1
        self._running     = False
        self._paused_tasks:    set[int] = set()
        self._cancelled_tasks: set[int] = set()
        self._worker_task: Optional[asyncio.Task] = None
        self._rate_limited_until: float = 0.0
        self._ema_speed_bps: float = 0.0

    async def start(self) -> None:
        await self._instagram.initialize()
        # FIX: check_login_status() del cliente público (v4) siempre reporta
        # logged_in=True aunque no haya sesión real, porque opera sin login.
        # Confiar en ese flag disparaba concurrencia alta (varias tareas de
        # Instagram en paralelo) contra un endpoint que ya está sin auth y
        # rate-limitado -- empeorando los bloqueos. Ahora arrancamos siempre
        # en concurrencia 1 y solo subimos si hubo un login() real y exitoso
        # con usuario/contraseña (ver login_instagram()).
        self._semaphore = asyncio.Semaphore(1)
        self._running = True
        self._worker_task = asyncio.create_task(self._queue_worker())
        interrupted = await self._task_repo.get_pending_on_startup()
        if interrupted:
            logger.info("%d tareas interrumpidas. Usá 'Reanudar cola'.", len(interrupted))
            await self._broadcast({"type":"startup_interrupted","count":len(interrupted),
                "message":f"Hay {len(interrupted)} tareas de sesiones anteriores. Usá 'Reanudar cola'."})
        logger.info("Download manager started")

    async def stop(self) -> None:
        self._running = False
        if self._worker_task: self._worker_task.cancel()

    async def enqueue_url(self, url: str, priority: int = 5) -> int:
        url = url.strip()
        if not URLClassifier.is_valid_http(url): raise ValueError(f"URL no válida: '{url}'")
        task_id = await self._task_repo.enqueue(url, priority)
        await self._broadcast({"type":"task_queued","task_id":task_id,"url":url})
        return task_id

    async def enqueue_batch(self, urls: list[str], priority: int = 5) -> dict:
        clean_urls, rejected = [], []
        for url in urls:
            url = url.strip()
            if not url: continue
            if not URLClassifier.is_valid_http(url): rejected.append(url); continue
            clean_urls.append(url)

        # FIX: inserción atómica en una sola transacción — antes cada URL
        # se insertaba con su propio await/commit, dándole tiempo al worker
        # de la cola (que sondea cada 2s) para empezar a procesar ANTES de
        # que terminara de encolarse el resto del batch.
        queued = await self._task_repo.enqueue_many(clean_urls, priority)
        for task_id, url in zip(queued, clean_urls):
            await self._broadcast({"type":"task_queued","task_id":task_id,"url":url})
        return {"queued":queued,"rejected":rejected}

    async def resume_interrupted(self) -> int:
        interrupted = await self._task_repo.get_pending_on_startup()
        count = 0
        for t in interrupted:
            if t["status"] not in ("completed","cancelled","error"):
                await self._task_repo.set_status(t["id"], TaskStatus.QUEUED); count += 1
        return count

    async def cancel_all_queued(self) -> int:
        from backend.database.connection import get_db
        async with get_db() as db:
            cur = await db.execute(
                "SELECT id FROM download_tasks WHERE status IN ('queued','paused','analyzing','downloading')")
            ids = [r[0] for r in await cur.fetchall()]
            if ids:
                await db.execute(
                    "UPDATE download_tasks SET status='cancelled' WHERE id IN (%s)" % ",".join("?"*len(ids)), ids)
        return len(ids)

    async def pause_task(self, task_id: int) -> None:
        self._paused_tasks.add(task_id)
        await self._task_repo.set_status(task_id, TaskStatus.PAUSED)
        await self._broadcast({"type":"task_paused","task_id":task_id})

    async def resume_task(self, task_id: int) -> None:
        self._paused_tasks.discard(task_id)
        await self._task_repo.set_status(task_id, TaskStatus.QUEUED)
        await self._broadcast({"type":"task_resumed","task_id":task_id})

    async def cancel_task(self, task_id: int) -> None:
        self._cancelled_tasks.add(task_id)
        await self._task_repo.set_status(task_id, TaskStatus.CANCELLED)
        await self._broadcast({"type":"task_cancelled","task_id":task_id})

    async def get_queue_summary(self) -> dict: return await self._task_repo.get_queue_summary()
    async def get_active_tasks(self) -> list[dict]: return await self._task_repo.get_active_tasks()
    async def get_instagram_status(self) -> dict: return await self._instagram.check_login_status()
    async def instagram_browser_probe(self, url: str) -> dict: return await self._instagram.browser_probe(url)
    async def close_instagram_browser_probe(self) -> None: await self._instagram.close_browser_probe()

    async def _finish_instagram_login(self, ok: bool) -> bool:
        if ok:
            # Instagram permanece serializado: el login válido no habilita
            # varias solicitudes paralelas contra endpoints restrictivos.
            self._instagram_max_concurrent = 1
            self._semaphore = asyncio.Semaphore(1)
            logger.info("[IG_RATE] concurrency=1 reason=conservative_pacing")
        return ok

    async def login_instagram(self, username: str, password: str) -> bool:
        ok = await self._instagram.login(username, password)
        return await self._finish_instagram_login(ok)

    async def login_instagram_session(self, username: str, sessionid: str) -> bool:
        ok = await self._instagram.login_with_sessionid(username, sessionid)
        return await self._finish_instagram_login(ok)

    async def _queue_worker(self) -> None:
        """
        FIX: antes, cuando Instagram entraba en cooldown (self._rate_limited_until
        en el futuro), el `continue` cortaba el loop COMPLETO sin procesar nada,
        ni siquiera sitios que no son Instagram (GitHub, noticias, MercadoLibre...).
        Ahora se mantienen dos pools de tareas activas separados: uno para
        Instagram (respeta el cooldown) y otro para todo lo demás (nunca se
        detiene por el cooldown de Instagram).
        """
        ig_active: set[asyncio.Task] = set()
        generic_active: set[asyncio.Task] = set()

        while self._running:
            for pool in (ig_active, generic_active):
                done = {t for t in pool if t.done()}
                for t in done:
                    pool.discard(t)
                    try: t.result()
                    except: pass

            # Sitios genéricos: SIEMPRE se procesan, sin importar el cooldown de Instagram
            from backend.services import settings_service
            generic_max = int(settings_service.get("generic_concurrency"))
            while len(generic_active) < generic_max:
                queued = await self._task_repo.get_next_queued(url_filter="non_instagram")
                if not queued: break
                if queued["id"] in self._paused_tasks or queued["id"] in self._cancelled_tasks: continue
                task = asyncio.create_task(self._process_task(queued["id"], queued["url"]))
                generic_active.add(task)

            # Instagram: respeta el cooldown global. Además, FIX: no arranca
            # ninguna tarea de Instagram mientras queden sitios no-Instagram
            # sin procesar — esto asegura que el .txt con URLs mixtas se
            # procese primero por los sitios más rápidos/confiables, dejando
            # Instagram (más lento por los delays anti rate-limit) para el
            # final, en vez de mezclarse en paralelo desde el arranque.
            pending_generic = await self._task_repo.count_queued(url_filter="non_instagram")
            remaining = self._rate_limited_until - time.monotonic()
            if remaining <= 0 and pending_generic == 0:
                ig_max = self._instagram_max_concurrent
                while len(ig_active) < ig_max:
                    queued = await self._task_repo.get_next_queued(url_filter="instagram")
                    if not queued: break
                    if queued["id"] in self._paused_tasks or queued["id"] in self._cancelled_tasks: continue
                    task = asyncio.create_task(self._process_task(queued["id"], queued["url"]))
                    ig_active.add(task)

            await asyncio.sleep(2.0)

    async def _process_task(self, task_id: int, url: str) -> None:
        source_type = URLClassifier.classify(url)
        logger.info("[CLASSIFY] task=%s url=%s -> source_type=%s", task_id, url, source_type)
        # Defensa contra una copia/version vieja que pudiera etiquetar como
        # Twitter una URL que no tiene /status/<id> en twitter.com o x.com.
        if source_type == "twitter" and not URLClassifier.TWITTER_RE.search(url):
            logger.warning("[CLASSIFY] clasificación Twitter inválida para %s; usando generic", url)
            source_type = "generic"
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

                # FIX: si el error viene del cliente de Instagram con un motivo
                # ya clasificado (InstagramFetchError.reason), usarlo directamente
                # en vez de adivinar por texto — así "privado"/"no existe" se
                # marcan como fatal de una y no gastan los 5 reintentos, y solo
                # "rate_limited" real dispara el cooldown global. Un "access_denied"
                # queda separado de "private": HTTP 403 no confirma privacidad.
                from backend.services.downloader.instagram_client import InstagramFetchError
                if isinstance(exc, InstagramFetchError):
                    ig_state = getattr(self._instagram, "_session_state", "unknown")
                    logger.warning(
                        "[IG_TASK] task=%s state=%s reason=%s url=%s",
                        task_id, ig_state, exc.reason, url,
                    )
                    is_rate  = exc.reason == "rate_limited"
                    is_fatal = exc.reason in (
                        "private", "access_denied", "not_found", "invalid_session",
                        "redirect_to_login",
                    )
                else:
                    err_str  = str(exc).lower()
                    is_rate  = any(k in err_str for k in ["401","429","please wait","rate limit","too many"])
                    is_fatal = any(k in err_str for k in [
                        "unsupported url","no video formats","no video could be found",
                        "private","not found","does not exist","deleted","suspended","unavailable"
                    ])

                retry_after_s = getattr(exc, "retry_after_s", None) if is_rate else None
                if is_rate:
                    cooldown_s = retry_after_s or RATE_LIMIT_COOLDOWN_S
                    self._rate_limited_until = max(
                        self._rate_limited_until,
                        time.monotonic() + cooldown_s,
                    )
                    logger.warning(
                        "[IG_RATE] cooldown_s=%.0f source=%s task=%s",
                        cooldown_s,
                        "retry-after" if retry_after_s else "default",
                        task_id,
                    )
                task = await self._task_repo.get_by_id(task_id)
                attempt = task["attempt_count"] if task else 0
                from backend.services import settings_service
                max_retries    = int(settings_service.get("max_retries"))
                base_delay     = float(settings_service.get("retry_base_delay_s"))
                max_delay      = float(settings_service.get("retry_max_delay_s"))
                if not is_fatal and attempt < max_retries:
                    delay = (retry_after_s or RATE_LIMIT_COOLDOWN_S) if is_rate else min(
                        base_delay*(2**attempt), max_delay)
                    await self._task_repo.increment_attempt(task_id, delay)
                    await tlog.warning(f"Reintentando en {delay:.0f}s (intento {attempt+1})")
                else:
                    await self._task_repo.set_status(task_id, TaskStatus.ERROR, error_message=str(exc))
                    await self._broadcast({"type":"task_error","task_id":task_id,"error":str(exc)})

    async def _run_instagram_pipeline(self, task_id: int, url: str, tlog: TaskLogger) -> None:
        await self._task_repo.set_status(task_id, TaskStatus.ANALYZING)
        await tlog.info("Instagram — analizando URL...")
        shortcode = InstagramClient.extract_shortcode(url)
        if not shortcode: raise ValueError(f"No se puede extraer shortcode de: {url}")
        lock = await _get_lock(shortcode)
        async with lock:
            existing = await self._post_repo.get_by_shortcode(shortcode)
            if existing:
                await tlog.info("Ya archivado, saltando.")
                await self._task_repo.set_status(task_id, TaskStatus.COMPLETED, progress_pct=100.0, post_id=existing["id"])
                await self._broadcast({"type":"task_completed","task_id":task_id,"post_id":existing["id"],"skipped":True})
                return
            await tlog.info("Obteniendo metadatos de Instagram...")
            ig_status = await self._instagram.check_login_status()
            ig_state_label = ig_status.get("state", "unknown")
            ig_user_suffix = f" (@{ig_status['username']})" if ig_status.get("username") else ""
            await tlog.info(f"Instagram — estado de sesión: {ig_state_label}{ig_user_suffix}")
            metadata = await self._instagram.fetch_post_metadata(url)
            await self._task_repo.update(task_id, {"shortcode": metadata.shortcode})
            author_id = await self._author_repo.upsert_author({
                "username":metadata.author,"full_name":metadata.full_name,
                "is_private":1 if metadata.is_private else 0,
                "is_verified":1 if metadata.is_verified else 0,
                "profile_pic_url":metadata.profile_pic_url,
                "last_updated_at":"strftime('%Y-%m-%dT%H:%M:%SZ','now')",
            })
            post_id = await self._post_repo.insert({
                "shortcode":metadata.shortcode,"author_id":author_id,
                "post_type":metadata.post_type,"caption":metadata.caption,
                "hashtags":json.dumps(metadata.hashtags),"mentions":json.dumps(metadata.mentions),
                "location_name":metadata.location_name,"location_lat":metadata.location_lat,
                "location_lng":metadata.location_lng,"like_count":metadata.like_count,
                "comment_count":metadata.comment_count,"media_count":metadata.media_count,
                "original_url":metadata.original_url,
                "posted_at":metadata.posted_at.isoformat() if metadata.posted_at else None,
                "raw_metadata":json.dumps(metadata.raw),
            })
            await self._task_repo.update(task_id, {"post_id":post_id})
            for ht in metadata.hashtags:
                tag_id = await self._tag_repo.get_or_create(ht, "hashtag")
                await self._post_repo.add_tag(post_id, tag_id, "hashtag")
        await self._task_repo.set_status(task_id, TaskStatus.DOWNLOADING)
        await tlog.info(f"Descargando {metadata.media_count} archivo(s)...")
        media_dir = get_media_dir(metadata.shortcode)
        for idx, item in enumerate(metadata.media_items):
            if task_id in self._cancelled_tasks: raise asyncio.CancelledError()
            ext = ".mp4" if item["is_video"] else ".jpg"
            fp = await self._stream_download(task_id, item["url"], media_dir/f"{metadata.shortcode}_{idx:02d}{ext}", metadata.media_count, idx)
            sha256 = await asyncio.get_event_loop().run_in_executor(None, compute_sha256, fp)
            dup = await self._media_repo.get_by_hash(sha256)
            if dup: fp.unlink(missing_ok=True); fp = Path(dup["file_path"])
            stat = fp.stat()
            await self._media_repo.insert({
                "post_id":post_id,"file_path":str(fp),"file_name":fp.name,
                "file_type":"video" if item["is_video"] else "image",
                "mime_type":detect_mime(fp),"file_size_bytes":stat.st_size,
                "width_px":item.get("width"),"height_px":item.get("height"),
                "carousel_index":idx,"sha256_hash":sha256,"is_original":1,
            })
            progress = ((idx+1)/metadata.media_count)*65.0
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
                await self._broadcast({"type":"task_completed","task_id":task_id,"post_id":existing["id"],"skipped":True})
                return
            dest_dir = get_media_dir(shortcode)
            result = await UniversalDownloader(dest_dir).extract(url)

            # FIX: no crear post si no hay nada útil que mostrar
            # FIX: "title" solo (sin media_files ni texto real) no cuenta
            # como contenido archivable — MercadoLibre, por ejemplo, siempre
            # tiene título (nombre del producto) aunque el scraping de
            # imágenes falle, y eso colaba cards vacías. Ahora se exige
            # media real o texto sustancial (>30 caracteres), no solo un
            # título de una línea.
            has_content = bool(
                result.media_files
                or (result.content_text and len(result.content_text.strip()) > 30)
            )
            if not has_content:
                await tlog.info(f"Sin contenido extraíble de {source_type}, saltando.")
                await self._task_repo.set_status(task_id, TaskStatus.COMPLETED, progress_pct=100.0)
                await self._broadcast({"type":"task_completed","task_id":task_id,"post_id":None,"skipped":True})
                return

            from urllib.parse import urlparse as _urlparse
            domain = _urlparse(url).netloc
            author_id = await self._author_repo.upsert_author({
                "username":(result.author or domain)[:100],"full_name":result.author or domain,
                "is_private":0,"is_verified":0,"profile_pic_url":None,
                "last_updated_at":"strftime('%Y-%m-%dT%H:%M:%SZ','now')",
            })
            has_video = any(m.get("file_type")=="video" for m in result.media_files)
            post_type = "video" if has_video else ("image" if result.media_files else "unknown")
            caption = result.description or result.content_text[:500] or result.title
            post_id = await self._post_repo.insert({
                "shortcode":shortcode,"author_id":author_id,"post_type":post_type,
                "caption":caption,"hashtags":json.dumps(result.tags),"mentions":json.dumps([]),
                "media_count":max(len(result.media_files),1),"original_url":url,
                "posted_at":result.posted_at,
                "raw_metadata":json.dumps({"source_type":source_type,"title":result.title,
                                           "domain":domain,**result.metadata}),
            })
            await self._task_repo.update(task_id, {"post_id":post_id,"shortcode":shortcode})
            for tag in result.tags[:20]:
                tag_id = await self._tag_repo.get_or_create(tag, "hashtag")
                await self._post_repo.add_tag(post_id, tag_id, "hashtag")
        await self._task_repo.set_status(task_id, TaskStatus.DOWNLOADING)
        total = max(len(result.media_files), 1)
        for idx, mf in enumerate(result.media_files):
            fp = Path(mf["path"]).resolve()
            if not fp.exists(): continue
            sha256 = await asyncio.get_event_loop().run_in_executor(None, compute_sha256, fp)
            dup = await self._media_repo.get_by_hash(sha256)
            if dup: fp.unlink(missing_ok=True); fp = Path(dup["file_path"])
            stat = fp.stat()
            try:
                await self._media_repo.insert({
                    "post_id":post_id,"file_path":str(fp),"file_name":fp.name,
                    "file_type":mf.get("file_type","image"),"mime_type":mf.get("mime_type",detect_mime(fp)),
                    "file_size_bytes":stat.st_size,"width_px":mf.get("width"),"height_px":mf.get("height"),
                    "duration_s":mf.get("duration_s"),"carousel_index":mf.get("carousel_index",idx),
                    "sha256_hash":sha256,"is_original":1,
                })
            except Exception as e:
                if "UNIQUE" in str(e): continue
                raise
            progress = ((idx+1)/total)*65.0
            await self._task_repo.update_progress(task_id, progress)

        if not result.media_files and result.content_text:
            txt = get_media_dir(shortcode)/f"{shortcode}_content.txt"
            txt.write_text(f"{result.title}\n\n{result.content_text}", encoding="utf-8")
            sha256 = await asyncio.get_event_loop().run_in_executor(None, compute_sha256, txt)
            try:
                await self._media_repo.insert({
                    "post_id":post_id,"file_path":str(txt),"file_name":txt.name,
                    "file_type":"image","mime_type":"text/plain",
                    "file_size_bytes":txt.stat().st_size,"carousel_index":0,
                    "sha256_hash":sha256,"is_original":1,
                })
            except: pass

        await self._finalize(task_id, post_id, tlog)

    async def _finalize(self, task_id: int, post_id: int, tlog: TaskLogger) -> None:
        await self._task_repo.set_status(task_id, TaskStatus.GENERATING_THUMBNAILS)
        await tlog.info("Generando miniaturas...")
        media_rows = await self._media_repo.get_by_post(post_id)
        for i, row in enumerate(media_rows):
            fp = Path(row["file_path"])
            if fp.exists() and row["file_type"] in ("image","video"):
                thumb = await self._thumbnails.generate(fp)
                if thumb: await self._media_repo.update_thumbnail(row["id"], str(thumb))
            progress = 65.0+((i+1)/max(len(media_rows),1))*15.0
            await self._task_repo.update_progress(task_id, progress)
        await self._task_repo.set_status(task_id, TaskStatus.PROCESSING_AI)
        asyncio.create_task(self._run_ai_analysis(post_id, task_id, tlog))
        await self._task_repo.set_status(task_id, TaskStatus.COMPLETED, progress_pct=100.0, post_id=post_id)
        await tlog.info("¡Finalizado!")
        await self._broadcast({"type":"task_completed","task_id":task_id,"post_id":post_id})

    async def _stream_download(self, task_id: int, url: str, dest: Path, total_items: int=1, index: int=0) -> Path:
        dest = Path(dest).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        headers = {"User-Agent":config.downloader.user_agent,"Referer":"https://www.instagram.com/"}
        start = time.monotonic(); written = 0
        last_broadcast = 0.0
        async with httpx.AsyncClient(follow_redirects=True, timeout=config.downloader.request_timeout_s) as client:
            async with client.stream("GET", url, headers=headers) as resp:
                resp.raise_for_status()
                total_bytes = int(resp.headers.get("content-length", 0))
                with open(dest,"wb") as f:
                    async for chunk in resp.aiter_bytes(config.downloader.chunk_size_bytes):
                        if task_id in self._cancelled_tasks: dest.unlink(missing_ok=True); raise asyncio.CancelledError()
                        f.write(chunk); written += len(chunk)
                        # FIX: dlStatSpeed en el frontend quedaba siempre en "—"
                        # porque este broadcast se había perdido en una reescritura
                        # anterior. Se limita a como máximo 1 emisión cada 0.5s
                        # para no saturar el WebSocket en descargas rápidas.
                        now = time.monotonic()
                        if now - last_broadcast >= 0.5:
                            elapsed = now - start
                            speed_bps = written / elapsed if elapsed > 0 else 0
                            await self._broadcast({
                                "type": "file_progress", "task_id": task_id,
                                "bytes_downloaded": written, "bytes_total": total_bytes,
                                "speed_bps": speed_bps,
                            })
                            last_broadcast = now
        return dest

    async def _run_ai_analysis(self, post_id: int, task_id: int, tlog: TaskLogger) -> None:
        try:
            from backend.services.ai.tag_generator import TagGeneratorService
            await TagGeneratorService(self._post_repo, self._media_repo, self._tag_repo).analyze_post(post_id, task_id, self._broadcast, tlog)
        except Exception as exc: await tlog.error(f"Error IA: {exc}")

    @staticmethod
    async def _noop_broadcast(event: dict) -> None: pass
