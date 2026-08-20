"""
Download Task Repository
Manages the persistent download queue and per-task event logs.
"""

from __future__ import annotations

from typing import Any, Optional
from backend.database.connection import get_db
from backend.models import TaskStatus
from backend.repositories.base import BaseRepository


class DownloadTaskRepository(BaseRepository):
    table_name = "download_tasks"

    async def enqueue(self, url: str, priority: int = 5) -> int:
        """Add a URL to the queue. Returns new task id."""
        async with get_db() as db:
            cur = await db.execute(
                """
                SELECT id FROM download_tasks
                WHERE url = ? AND status IN ('queued','paused','analyzing','downloading')
                LIMIT 1
                """,
                (url,),
            )
            existing = await cur.fetchone()
            if existing:
                return existing[0]

            ins = await db.execute(
                "INSERT INTO download_tasks (url, priority) VALUES (?, ?)",
                (url, priority),
            )
            return ins.lastrowid

    async def enqueue_many(self, urls: list[str], priority: int = 5) -> list[int]:
        """
        FIX: encolar un .txt grande con N URLs una por una (N transacciones
        separadas, cada una cediendo el control al event loop) le daba al
        worker de la cola tiempo de sobra para empezar a descargar ANTES de
        que terminara de insertarse el resto del batch — mezclando URLs
        de sitios distintos de forma impredecible en vez de completar el
        encolado primero. Ahora se hace en UNA sola transacción atómica.
        """
        if not urls:
            return []
        async with get_db() as db:
            placeholders = ",".join("?" * len(urls))
            cur = await db.execute(
                f"""
                SELECT url FROM download_tasks
                WHERE url IN ({placeholders})
                  AND status IN ('queued','paused','analyzing','downloading')
                """,
                urls,
            )
            already_queued = {r[0] for r in await cur.fetchall()}
            new_urls = [u for u in urls if u not in already_queued]
            if new_urls:
                await db.executemany(
                    "INSERT INTO download_tasks (url, priority) VALUES (?, ?)",
                    [(u, priority) for u in new_urls],
                )
            # IDs de todas las tareas relevantes (nuevas + ya existentes)
            cur2 = await db.execute(
                f"SELECT id FROM download_tasks WHERE url IN ({placeholders})",
                urls,
            )
            return [r[0] for r in await cur2.fetchall()]

    async def count_queued(self, url_filter: Optional[str] = None) -> int:
        """Cuenta tareas 'queued' pendientes, opcionalmente filtradas por tipo de URL."""
        url_clause = ""
        if url_filter == "instagram":
            url_clause = "AND url LIKE '%instagram.com%'"
        elif url_filter == "non_instagram":
            url_clause = "AND url NOT LIKE '%instagram.com%'"
        async with get_db() as db:
            cur = await db.execute(
                f"""
                SELECT COUNT(*) FROM download_tasks
                WHERE status = 'queued'
                  AND (next_retry_at IS NULL OR datetime(next_retry_at) <= datetime('now'))
                  {url_clause}
                """
            )
            return (await cur.fetchone())[0]

    async def get_next_queued(
        self, url_filter: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        """
        Atomically claim and fetch highest-priority queued task.

        FIX (race condition): antes esto era un simple SELECT. Cuando el
        worker llamaba a este método varias veces seguidas en un mismo tick
        (p.ej. con concurrencia > 1), dos llamadas podían leer la MISMA fila
        con status='queued' antes de que la primera terminara de marcarla
        como 'analyzing' -> la misma tarea se procesaba dos veces en paralelo,
        duplicando requests a Instagram y acelerando el rate-limit.

        Ahora el UPDATE + SELECT van en una sola sentencia atómica (SQLite
        serializa los writes sobre la misma conexión), así que dos llamadas
        concurrentes nunca pueden reclamar la misma fila.

        url_filter:
          - "instagram"     -> solo tareas cuya URL contiene instagram.com
          - "non_instagram" -> solo tareas cuya URL NO contiene instagram.com
          - None            -> sin filtro (cualquier tipo)

        Esto permite que el worker siga procesando sitios genéricos aunque
        Instagram esté en cooldown, en vez de bloquear la cola entera.
        """
        url_clause = ""
        if url_filter == "instagram":
            url_clause = "AND url LIKE '%instagram.com%'"
        elif url_filter == "non_instagram":
            url_clause = "AND url NOT LIKE '%instagram.com%'"

        async with get_db() as db:
            cur = await db.execute(
                f"""
                UPDATE download_tasks
                SET status = 'analyzing'
                WHERE id = (
                    SELECT id FROM download_tasks
                    WHERE status = 'queued'
                      AND (
                        next_retry_at IS NULL
                        OR datetime(next_retry_at) <= datetime('now')
                      )
                      {url_clause}
                    ORDER BY priority DESC, created_at ASC
                    LIMIT 1
                )
                RETURNING *
                """
            )
            row = await cur.fetchone()
            return dict(row) if row else None

    async def get_pending_on_startup(self) -> list[dict[str, Any]]:
        """Return tasks that were interrupted (downloading/analyzing) on previous run."""
        async with get_db() as db:
            cur = await db.execute(
                """
                SELECT * FROM download_tasks
                WHERE status IN ('queued','paused','analyzing','downloading',
                                  'processing_ai','generating_thumbnails','saving')
                ORDER BY priority DESC, created_at ASC
                """
            )
            return [dict(r) for r in await cur.fetchall()]

    async def set_status(self, task_id: int, status: TaskStatus, **extra) -> None:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        data: dict[str, Any] = {"status": status.value}
        if status == TaskStatus.DOWNLOADING and "started_at" not in extra:
            data["started_at"] = now_iso
        if status in (TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELLED):
            data["completed_at"] = now_iso
        data.update(extra)
        await self.update(task_id, data)

    async def update_progress(
        self,
        task_id: int,
        progress_pct: float,
        bytes_downloaded: int | None = None,
        bytes_total: int | None = None,
        speed_bps: float | None = None,
        eta_seconds: float | None = None,
    ) -> None:
        data: dict[str, Any] = {"progress_pct": round(progress_pct, 1)}
        if bytes_downloaded is not None:
            data["bytes_downloaded"] = bytes_downloaded
        if bytes_total is not None:
            data["bytes_total"] = bytes_total
        if speed_bps is not None:
            data["speed_bps"] = speed_bps
        if eta_seconds is not None:
            data["eta_seconds"] = eta_seconds
        await self.update(task_id, data)

    async def increment_attempt(self, task_id: int, next_retry_delay_s: float) -> None:
        # FIX: usar el mismo formato ISO8601 (T...Z) que get_next_queued(),
        # antes se usaba datetime('now', '+N seconds') que genera 'YYYY-MM-DD HH:MM:SS'
        # (con espacio en vez de T). Al comparar como texto, el espacio (0x20)
        # ordena antes que 'T' (0x54), así que next_retry_at siempre parecía
        # "en el pasado" y el reintento se disparaba de inmediato en vez de
        # esperar el delay configurado — esto causaba requests cada pocos
        # segundos en vez de cada 360s, empeorando el rate-limit de Instagram.
        async with get_db() as db:
            await db.execute(
                """
                UPDATE download_tasks
                SET attempt_count = attempt_count + 1,
                    next_retry_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ? || ' seconds'),
                    status = 'queued'
                WHERE id = ?
                """,
                (str(int(next_retry_delay_s)), task_id),
            )

    async def get_queue_summary(self) -> dict[str, int]:
        async with get_db() as db:
            cur = await db.execute(
                """
                SELECT status, COUNT(*) AS cnt
                FROM download_tasks
                GROUP BY status
                """
            )
            rows = await cur.fetchall()
            return {r["status"]: r["cnt"] for r in rows}

    async def get_active_tasks(self) -> list[dict[str, Any]]:
        async with get_db() as db:
            cur = await db.execute(
                """
                SELECT * FROM download_tasks
                WHERE status NOT IN ('completed','cancelled','error')
                ORDER BY
                    CASE status
                        WHEN 'downloading' THEN 0
                        WHEN 'analyzing'   THEN 1
                        WHEN 'processing_ai' THEN 2
                        WHEN 'generating_thumbnails' THEN 3
                        WHEN 'saving'      THEN 4
                        WHEN 'queued'      THEN 5
                        WHEN 'paused'      THEN 6
                        ELSE 7
                    END,
                    priority DESC, created_at ASC
                """
            )
            return [dict(r) for r in await cur.fetchall()]

    async def get_recent_completed(self, limit: int = 20) -> list[dict[str, Any]]:
        async with get_db() as db:
            cur = await db.execute(
                """
                SELECT * FROM download_tasks
                WHERE status IN ('completed','error','cancelled')
                ORDER BY completed_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def get_errors(self, limit: int = 1000) -> list[dict[str, Any]]:
        """
        FIX: el panel de Estadísticas pedía los "N más recientes" de
        get_recent_completed (capado a 200 en la ruta) y filtraba errores
        del lado del cliente. Con muchos más completados que errores
        recientes, los 200 más recientes podían ser TODOS 'completed' y
        el filtro nunca encontraba ningún error, aunque hubiera miles
        acumulados más atrás en el tiempo. Este método filtra por
        status='error' directamente en la consulta SQL, sin ese problema.
        """
        async with get_db() as db:
            cur = await db.execute(
                """
                SELECT * FROM download_tasks
                WHERE status = 'error'
                ORDER BY completed_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(r) for r in await cur.fetchall()]

    # ── Task Logs ─────────────────────────────────────────────────────────────

    async def add_log(
        self,
        task_id: int,
        level: str,
        message: str,
        details: str | None = None,
    ) -> None:
        async with get_db() as db:
            await db.execute(
                "INSERT INTO download_logs (task_id, level, message, details) VALUES (?,?,?,?)",
                (task_id, level, message, details),
            )
            await db.execute(
                "INSERT INTO app_history (event_type, entity_type, entity_id, message) VALUES (?,?,?,?)",
                (level, "task", task_id, message),
            )

    async def get_logs(self, task_id: int) -> list[dict[str, Any]]:
        async with get_db() as db:
            cur = await db.execute(
                "SELECT * FROM download_logs WHERE task_id = ? ORDER BY logged_at ASC",
                (task_id,),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def get_recent_history(self, limit: int = 100) -> list[dict[str, Any]]:
        async with get_db() as db:
            cur = await db.execute(
                "SELECT * FROM app_history ORDER BY occurred_at DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in await cur.fetchall()]
