from __future__ import annotations
import asyncio, gc, logging
from pathlib import Path
from backend.config import config

logger = logging.getLogger(__name__)

class VisionAnalyzer:
    def __init__(self) -> None:
        self._model=None; self._preprocess=None; self._device="cpu"
        self._labels=config.ai.label_categories; self._initialized=False

    async def initialize(self) -> bool:
        self._initialized = True
        logger.info("VisionAnalyzer listo (lazy)")
        return True

    def _ensure_loaded(self) -> bool:
        if self._model is not None: return True
        try:
            import clip
            self._model, self._preprocess = clip.load(config.ai.clip_model, device=self._device, jit=False)
            self._model.eval()
            return True
        except Exception as exc:
            logger.warning("CLIP no disponible: %s", exc); return False

    def _unload(self) -> None:
        if self._model is not None:
            del self._model; del self._preprocess
            self._model=None; self._preprocess=None
            gc.collect()
            try:
                import torch; torch.cuda.empty_cache()
            except: pass

    async def classify_image(self, image_path: Path) -> list[dict]:
        if not self._initialized: return []
        try:
            return await asyncio.get_event_loop().run_in_executor(None, self._run_clip, image_path)
        except Exception as exc:
            logger.error("CLIP falló %s: %s", image_path.name, exc); return []

    def _run_clip(self, image_path: Path) -> list[dict]:
        if not self._ensure_loaded(): return []
        import clip, torch
        from PIL import Image
        image = self._preprocess(Image.open(image_path).convert("RGB")).unsqueeze(0)
        text_tokens = clip.tokenize(self._labels)
        with torch.no_grad():
            img_f = self._model.encode_image(image); txt_f = self._model.encode_text(text_tokens)
            img_f /= img_f.norm(dim=-1,keepdim=True); txt_f /= txt_f.norm(dim=-1,keepdim=True)
            sim = (100.0*img_f@txt_f.T).softmax(dim=-1)
            vals, idxs = sim[0].topk(config.ai.max_tags_per_image)
        results = [{"label":self._labels[i],"confidence":round(s,4)} for s,i in zip(vals.tolist(),idxs.tolist()) if s>=config.ai.min_tag_confidence]
        return sorted(results, key=lambda x:x["confidence"], reverse=True)

    async def extract_color_palette(self, image_path: Path, n_colors: int = 5) -> list[dict]:
        try:
            return await asyncio.get_event_loop().run_in_executor(None, self._run_color, image_path, n_colors)
        except Exception as exc:
            logger.error("Color extraction falló: %s", exc); return []

    def _run_color(self, image_path: Path, n_colors: int) -> list[dict]:
        from PIL import Image
        import numpy as np
        with Image.open(image_path) as img:
            img = img.convert("RGB").resize((100,100))
            pixels = np.array(img).reshape(-1,3).astype(float)
        centers = pixels[np.random.choice(len(pixels), n_colors, replace=False)].copy()
        for _ in range(15):
            dists = np.linalg.norm(pixels[:,None]-centers[None], axis=2)
            labels = np.argmin(dists, axis=1)
            new_c = np.array([pixels[labels==k].mean(axis=0) if (labels==k).any() else centers[k] for k in range(n_colors)])
            if np.allclose(centers,new_c,atol=1): break
            centers = new_c
        counts = np.bincount(labels, minlength=n_colors); total = len(pixels); palette=[]
        for k in np.argsort(-counts):
            if not counts[k]: continue
            r,g,b = map(int, centers[k])
            palette.append({"hex":f"#{r:02x}{g:02x}{b:02x}","pct":round(counts[k]/total*100,1),"name":self._color_name(r,g,b)})
        return palette[:n_colors]

    @staticmethod
    def _color_name(r,g,b) -> str:
        l=(max(r,g,b)+min(r,g,b))/2/255
        if l<0.15: return "negro"
        if l>0.85: return "blanco"
        if (max(r,g,b)-min(r,g,b))/(max(r,g,b)+1e-6)<0.15: return "gris"
        if r>g and r>b: return "naranja" if g>b*1.3 else "rojo"
        if g>r and g>b: return "verde"
        if b>r and b>g: return "azul"
        if r>b*1.2 and g>b*1.2: return "amarillo"
        return "mixto"
