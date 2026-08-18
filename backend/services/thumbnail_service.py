from __future__ import annotations
import asyncio, logging
from pathlib import Path
from typing import Optional
from backend.config import config
from backend.utils.file_utils import compute_sha256, get_thumbnail_path, is_video, is_image

logger = logging.getLogger(__name__)

class ThumbnailService:
    def __init__(self) -> None:
        self._cfg = config.thumbnail

    async def generate(self, source_path: Path) -> Optional[Path]:
        if not source_path.exists(): return None
        if not is_image(source_path) and not is_video(source_path):
            logger.debug("Skip thumbnail no-media: %s", source_path.name)
            return None
        if is_video(source_path):
            return await asyncio.get_event_loop().run_in_executor(None, self._video_thumb, source_path)
        return await asyncio.get_event_loop().run_in_executor(None, self._image_thumb, source_path)

    def _image_thumb(self, source_path: Path) -> Optional[Path]:
        try:
            from PIL import Image, ImageOps
            sha = compute_sha256(source_path)
            thumb_path = get_thumbnail_path(sha, ".webp")
            if thumb_path.exists(): return thumb_path
            with Image.open(source_path) as img:
                img = ImageOps.exif_transpose(img)
                if img.mode not in ("RGB","RGBA"): img = img.convert("RGB")
                w,h = self._cfg.width, self._cfg.height
                ir = img.width/img.height; tr = w/h
                if ir > tr: nh=h; nw=int(img.width*h/img.height)
                else: nw=w; nh=int(img.height*w/img.width)
                img = img.resize((nw,nh), Image.LANCZOS)
                left=(img.width-w)//2; top=(img.height-h)//2
                img = img.crop((left,top,left+w,top+h))
                img.save(thumb_path, format="WEBP", quality=self._cfg.quality, method=4)
            return thumb_path
        except Exception as exc:
            logger.error("Image thumb failed %s: %s", source_path.name, exc)
            return None

    def _video_thumb(self, source_path: Path) -> Optional[Path]:
        try:
            sha = compute_sha256(source_path)
            thumb_path = get_thumbnail_path(sha, ".webp")
            if thumb_path.exists(): return thumb_path
            try:
                import cv2
                cap = cv2.VideoCapture(str(source_path))
                cap.set(cv2.CAP_PROP_POS_MSEC, 1000)
                ret, frame = cap.read(); cap.release()
                if ret:
                    from PIL import Image
                    img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    img.thumbnail((self._cfg.width, self._cfg.height), Image.LANCZOS)
                    img.save(thumb_path, format="WEBP", quality=self._cfg.quality)
                    return thumb_path
            except ImportError: pass
            self._placeholder(thumb_path)
            return thumb_path
        except Exception as exc:
            logger.error("Video thumb failed %s: %s", source_path.name, exc)
            return None

    def _placeholder(self, dest: Path) -> None:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (self._cfg.width, self._cfg.height), color=(30,30,30))
        draw = ImageDraw.Draw(img)
        cx,cy = self._cfg.width//2, self._cfg.height//2; size=40
        draw.polygon([(cx-size//2,cy-size//2),(cx-size//2,cy+size//2),(cx+size//2,cy)], fill=(180,180,180))
        img.save(dest, format="WEBP", quality=self._cfg.quality)
