from __future__ import annotations
import asyncio, gc, logging
from pathlib import Path
from typing import Optional
from backend.config import config

logger = logging.getLogger(__name__)

class OCRService:
    def __init__(self) -> None:
        self._reader=None; self._initialized=False

    async def initialize(self) -> bool:
        self._initialized = True
        logger.info("OCRService listo (lazy)")
        return True

    def _ensure_loaded(self) -> bool:
        if self._reader is not None: return True
        try:
            import easyocr
            self._reader = easyocr.Reader(config.ai.ocr_languages, gpu=False, verbose=False)
            return True
        except Exception as exc:
            logger.warning("EasyOCR no disponible: %s", exc); return False

    def _unload(self) -> None:
        if self._reader is not None:
            del self._reader; self._reader=None; gc.collect()

    async def extract_text(self, image_path: Path) -> Optional[dict]:
        if not self._initialized: return None
        try:
            return await asyncio.get_event_loop().run_in_executor(None, self._run_ocr, image_path)
        except Exception as exc:
            logger.error("OCR falló %s: %s", image_path.name, exc); return None

    def _run_ocr(self, image_path: Path) -> Optional[dict]:
        if not self._ensure_loaded(): return None
        results = self._reader.readtext(str(image_path), detail=1)
        if not results: return None
        texts, confs = [], []
        for (_bbox,text,conf) in results:
            if conf>0.3 and text.strip(): texts.append(text.strip()); confs.append(conf)
        if not texts: return None
        full = " ".join(texts); avg = sum(confs)/len(confs)
        lang = "es" if any(c in full.lower() for c in "óáéíúñ¿¡") else "en"
        return {"text":full,"confidence":round(avg,4),"language":lang}
