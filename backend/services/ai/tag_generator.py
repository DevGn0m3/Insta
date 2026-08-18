from __future__ import annotations
import asyncio, logging
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional
from backend.services.ai.vision_analyzer import VisionAnalyzer
from backend.services.ai.ocr_service import OCRService
from backend.utils.logger import TaskLogger

logger = logging.getLogger(__name__)
BroadcastFn = Callable[[dict], Coroutine[Any, Any, None]]
_vision_analyzer: Optional[VisionAnalyzer]=None
_ocr_service: Optional[OCRService]=None
_ai_initialized=False
IMAGE_EXTENSIONS = {'.jpg','.jpeg','.png','.webp','.gif','.bmp','.tiff','.heic','.avif'}

async def initialize_ai() -> None:
    global _vision_analyzer,_ocr_service,_ai_initialized
    _vision_analyzer=VisionAnalyzer(); _ocr_service=OCRService()
    v=await _vision_analyzer.initialize(); o=await _ocr_service.initialize()
    _ai_initialized = v or o
    logger.info("AI init (lazy) — vision=%s ocr=%s", v, o)

class TagGeneratorService:
    def __init__(self, post_repo, media_repo, tag_repo) -> None:
        self._post_repo=post_repo; self._media_repo=media_repo; self._tag_repo=tag_repo

    async def analyze_post(self, post_id, task_id, broadcast, tlog: TaskLogger) -> None:
        if not _ai_initialized: return
        media_rows = await self._media_repo.get_by_post(post_id)
        image_files = [r for r in media_rows if r["file_type"]=="image"
                       and Path(r["file_path"]).suffix.lower() in IMAGE_EXTENSIONS
                       and Path(r["file_path"]).exists()]
        if not image_files:
            await tlog.info("Sin imágenes para IA."); return
        total = len(image_files)
        try:
            for i, media in enumerate(image_files):
                fp = Path(media["file_path"]); media_id = media["id"]
                if _vision_analyzer and _vision_analyzer._initialized:
                    tags = await _vision_analyzer.classify_image(fp)
                    for t in tags:
                        tag_id = await self._tag_repo.get_or_create(t["label"], "ai")
                        await self._post_repo.add_tag(post_id, tag_id, "ai", t["confidence"])
                        from backend.database.connection import get_db
                        async with get_db() as db:
                            await db.execute(
                                "INSERT INTO media_tags(media_id,tag_id,confidence,source) VALUES(?,?,?,'ai') ON CONFLICT DO NOTHING",
                                (media_id, tag_id, t["confidence"]))
                    colors = await _vision_analyzer.extract_color_palette(fp)
                    if colors:
                        await self._tag_repo.save_color_palette(media_id, colors)
                        for c in colors:
                            cid = await self._tag_repo.get_or_create(c["name"], "color")
                            await self._post_repo.add_tag(post_id, cid, "color")
                if _ocr_service and _ocr_service._initialized:
                    ocr = await _ocr_service.extract_text(fp)
                    if ocr and ocr.get("text"):
                        await self._tag_repo.save_ocr(media_id, ocr["text"], ocr["confidence"], ocr["language"])
                await broadcast({"type":"task_progress","task_id":task_id,"progress":75.0+((i+1)/total)*25.0})
        finally:
            if _vision_analyzer: _vision_analyzer._unload()
            if _ocr_service: _ocr_service._unload()
            import gc; gc.collect()
        await tlog.info(f"IA completada ({total} imágenes).")
        await broadcast({"type":"ai_completed","task_id":task_id,"post_id":post_id})
