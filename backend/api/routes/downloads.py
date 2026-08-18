from __future__ import annotations
import asyncio, logging, shutil
from typing import Any
from fastapi import APIRouter, HTTPException, status
from backend.models import DownloadTaskCreate
from backend.repositories.task_repository import DownloadTaskRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/downloads", tags=["downloads"])

@router.post("", status_code=202)
async def enqueue_url(payload: DownloadTaskCreate) -> dict[str, Any]:
    from backend.main import download_manager
    try:
        task_id = await download_manager.enqueue_url(payload.url, payload.priority)
        return {"task_id": task_id, "status": "queued", "url": payload.url}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/batch", status_code=202)
async def enqueue_batch(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.main import download_manager
    urls = payload.get("urls", [])
    priority = int(payload.get("priority", 5))
    if not urls:
        raise HTTPException(status_code=400, detail="No URLs")
    result = await download_manager.enqueue_batch(urls, priority)
    return {
        "queued": len(result["queued"]), "rejected": len(result["rejected"]),
        "rejected_urls": result["rejected"], "task_ids": result["queued"],
        "message": f"{len(result['queued'])} encoladas." + (f" {len(result['rejected'])} ignoradas." if result["rejected"] else ""),
    }

@router.get("/queue")
async def get_queue() -> dict[str, Any]:
    from backend.main import download_manager
    return {"summary": await download_manager.get_queue_summary(), "tasks": await download_manager.get_active_tasks()}

@router.get("/history")
async def get_history(limit: int = 50) -> list[dict]:
    return await DownloadTaskRepository().get_recent_completed(limit=min(limit, 200))

@router.get("/errors")
async def get_errors(limit: int = 1000) -> list[dict]:
    """Todas las descargas con error, sin compartir el límite de /history."""
    return await DownloadTaskRepository().get_errors(limit=min(limit, 5000))

@router.get("/history/events")
async def get_event_history(limit: int = 100) -> list[dict]:
    return await DownloadTaskRepository().get_recent_history(limit=min(limit, 500))

@router.get("/{task_id}/logs")
async def get_task_logs(task_id: int) -> list[dict]:
    return await DownloadTaskRepository().get_logs(task_id)

@router.get("/{task_id}")
async def get_task(task_id: int) -> dict[str, Any]:
    task = await DownloadTaskRepository().get_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return task

@router.post("/{task_id}/pause")
async def pause_task(task_id: int) -> dict:
    from backend.main import download_manager
    await download_manager.pause_task(task_id)
    return {"status": "paused"}

@router.post("/{task_id}/resume")
async def resume_task(task_id: int) -> dict:
    from backend.main import download_manager
    await download_manager.resume_task(task_id)
    return {"status": "queued"}

@router.post("/{task_id}/cancel")
async def cancel_task(task_id: int) -> dict:
    from backend.main import download_manager
    await download_manager.cancel_task(task_id)
    return {"status": "cancelled"}

@router.get("/instagram/status")
async def instagram_status() -> dict[str, Any]:
    from backend.main import download_manager
    return await download_manager.get_instagram_status()

@router.post("/instagram/browser-probe")
async def instagram_browser_probe(payload: dict[str, Any]) -> dict[str, Any]:
    from backend.main import download_manager
    url = str(payload.get("url", "")).strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL de Instagram requerida")
    try:
        return await download_manager.instagram_browser_probe(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

@router.post("/instagram/browser-close")
async def instagram_browser_close() -> dict[str, str]:
    from backend.main import download_manager
    await download_manager.close_instagram_browser_probe()
    return {"status": "ok"}

@router.post("/instagram/login")
async def instagram_login(payload: dict[str, str]) -> dict[str, Any]:
    from backend.main import download_manager

    username = payload.get("username", "").strip()
    password = payload.get("password", "")
    method = payload.get("method", "password").strip().lower()
    sessionid = payload.get("sessionid", "").strip()

    # Si el usuario pegó un sessionid, priorizarlo aunque un frontend antiguo
    # haya omitido o enviado mal el campo `method`.
    if sessionid:
        method = "sessionid"

    logger.info("Solicitud de login de Instagram para %s usando método=%s", username, method)

    if method not in {"password", "sessionid"}:
        raise HTTPException(status_code=400, detail="Método de login inválido")
    if method == "password" and not username:
        raise HTTPException(status_code=400, detail="Usuario requerido")
    if method == "password" and not password:
        raise HTTPException(status_code=400, detail="Contraseña requerida")
    if method == "sessionid" and not sessionid:
        raise HTTPException(status_code=400, detail="sessionid requerido")

    if method == "sessionid":
        ok = await download_manager.login_instagram_session(username, sessionid)
    else:
        ok = await download_manager.login_instagram(username, password)

    if not ok:
        detail = (
            getattr(download_manager._instagram, "_last_login_error", None)
            or "Login fallido."
        )
        raise HTTPException(status_code=401, detail=detail)

    session_status = await download_manager.get_instagram_status()
    return {
        "logged_in": session_status.get("logged_in", True),
        "username": session_status.get("username"),
        "method": method,
        "state": session_status.get("state", "active"),
        "message": session_status.get("message", "Sesión activa"),
        "last_fetch_reason": session_status.get("last_fetch_reason"),
        "last_fetch_evidence": session_status.get("last_fetch_evidence"),
    }

@router.post("/instagram/logout")
async def instagram_logout() -> dict[str, Any]:
    from backend.main import download_manager
    await download_manager._instagram.logout()
    download_manager._semaphore = asyncio.Semaphore(1)
    return {"status": "ok", "logged_in": False}

@router.post("/pause-all")
async def pause_all() -> dict[str, Any]:
    from backend.database.connection import get_db
    async with get_db() as db:
        cur = await db.execute(
            "UPDATE download_tasks SET status='paused' WHERE status IN ('queued','downloading','analyzing')"
        )
        paused = cur.rowcount
    return {"status": "ok", "paused": paused, "message": f"{paused} tareas pausadas."}

@router.post("/resume-queue")
async def resume_queue() -> dict[str, Any]:
    from backend.main import download_manager
    count = await download_manager.resume_interrupted()
    return {"status": "ok", "resumed": count, "message": f"{count} tareas reanudadas."}

@router.post("/cancel-all")
async def cancel_all() -> dict[str, Any]:
    from backend.main import download_manager
    count = await download_manager.cancel_all_queued()
    return {"status": "ok", "cancelled": count, "message": f"{count} tareas canceladas."}

@router.post("/clear-history")
async def clear_history() -> dict[str, Any]:
    from backend.database.connection import get_db
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM download_tasks WHERE status IN ('completed','cancelled','error')"
        )
        deleted = cur.rowcount
    return {"status": "ok", "deleted": deleted, "message": f"{deleted} tareas eliminadas."}

@router.post("/reset")
async def reset_all() -> dict[str, Any]:
    from backend.config import config
    from backend.database.connection import initialize_database
    errors = []
    for folder in [config.media_dir, config.thumbnails_dir]:
        try:
            if folder.exists(): shutil.rmtree(folder)
            folder.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            errors.append(str(e))
    try:
        db = config.database.path
        for p in [db, db.parent/(db.name+"-wal"), db.parent/(db.name+"-shm")]:
            if p.exists(): p.unlink()
    except Exception as e:
        errors.append(str(e))
    try:
        for f in config.logs_dir.glob("*.log*"): f.unlink(missing_ok=True)
    except Exception as e:
        errors.append(str(e))
    try:
        await initialize_database()
    except Exception as e:
        errors.append(str(e))
    try:
        from backend.main import download_manager
        download_manager._paused_tasks    = set()
        download_manager._cancelled_tasks = set()
        download_manager._rate_limited_until = 0.0
    except Exception as e:
        errors.append(str(e))
    return {"status": "ok" if not errors else "partial", "message": "Reset completo.", "errors": errors}
