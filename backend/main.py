"""
Instagram Archiver - Main Application
FastAPI entry point. Registers all routes, static file serving,
WebSocket endpoint, and manages application lifecycle.
"""

from __future__ import annotations
import warnings
warnings.filterwarnings("ignore", message=r".*pin_memory.*argument is set as true but no accelerator is found.*", category=UserWarning)

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from backend.config import config
from backend.database.connection import initialize_database
from backend.api.websocket import ws_manager
from backend.api.routes import downloads, library, search, stats, settings
from backend.services.ai.tag_generator import initialize_ai
from backend.services.downloader.download_manager import DownloadManager
from backend.utils.logger import setup_logging

logger = logging.getLogger(__name__)

# ── Application singleton (imported by route modules) ─────────────────────────
download_manager: DownloadManager = None  # type: ignore


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle manager."""
    global download_manager

    setup_logging()
    logger.info("=" * 60)
    logger.info("Instagram Archiver v%s starting...", config.app_version)
    logger.info("Data directory: %s", config.data_dir)

    # Initialize database
    await initialize_database()
    logger.info("Database ready")

    # Initialize AI models (non-blocking — runs in background)
    import asyncio
    asyncio.create_task(initialize_ai())

    # Initialize and start download manager
    download_manager = DownloadManager(broadcast_fn=ws_manager.broadcast)
    await download_manager.start()
    logger.info("Download manager ready")

    logger.info("Server running at http://%s:%d", config.server.host, config.server.port)
    logger.info("Open your browser at: http://127.0.0.1:%d", config.server.port)

    yield  # App is running

    # Shutdown
    await download_manager.stop()
    logger.info("Instagram Archiver stopped")


# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title=config.app_name,
    version=config.app_version,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.server.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API Routes ────────────────────────────────────────────────────────────────
app.include_router(downloads.router)
app.include_router(library.router)
app.include_router(search.router)
app.include_router(stats.router)
app.include_router(settings.router)


# ── WebSocket ─────────────────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        summary = await download_manager.get_queue_summary()
        await websocket.send_json({"type": "connected", "queue_summary": summary})
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# ── Static file serving ───────────────────────────────────────────────────────
app.mount(
    "/media",
    StaticFiles(directory=str(config.media_dir), html=False),
    name="media",
)

app.mount(
    "/thumbnails",
    StaticFiles(directory=str(config.thumbnails_dir), html=False),
    name="thumbnails",
)

frontend_dir = config.frontend_dir
if frontend_dir.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(frontend_dir / "assets")),
        name="assets",
    )


# ── SPA catch-all: serve index.html for all non-API routes ───────────────────
@app.get("/")
@app.get("/{full_path:path}")
async def serve_spa(full_path: str = ""):
    if full_path.startswith(("api/", "ws", "media/", "thumbnails/", "assets/")):
        from fastapi import HTTPException
        raise HTTPException(status_code=404)
    index = frontend_dir / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "Frontend not found. Place frontend files in /frontend/"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=config.server.host,
        port=config.server.port,
        reload=config.server.reload,
        workers=config.server.workers,
        log_level="info",
    )