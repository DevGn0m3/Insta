"""
Settings Service — configuración runtime editable desde la UI (⚙️ Configuración).

No reemplaza backend/config.py (eso sigue siendo los defaults de arranque),
sino que agrega una capa de overrides persistidos en data/settings.json que
el usuario puede modificar sin tocar código ni reiniciar el servidor para
la mayoría de los valores.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from backend.config import config

logger = logging.getLogger(__name__)

SETTINGS_FILE: Path = config.data_dir / "settings.json"

# Valores por defecto — se usan si el usuario nunca guardó nada, o si
# guardó parcialmente (solo algunas claves).
DEFAULTS: dict[str, Any] = {
    # Texto scrapeado de sitios genéricos/noticias
    "max_content_chars": 10000,
    "max_content_lines": 300,

    # Screenshots (Playwright)
    "screenshot_wait_seconds": 5.0,
    "navigation_timeout_s": 20.0,

    # Red / descargas
    "request_timeout_s": config.downloader.request_timeout_s,

    # Reintentos
    "max_retries": config.downloader.max_retries,
    "retry_base_delay_s": config.downloader.retry_base_delay_s,
    "retry_max_delay_s": config.downloader.retry_max_delay_s,

    # Control de carga — mismo mecanismo que ya usa la app (asyncio.Semaphore)
    "generic_concurrency": 1,

    # Throttle conservador entre requests de Instagram; no es un bypass.
    "min_delay_between_requests_s": max(20.0, config.downloader.min_delay_between_requests_s),
    "max_delay_between_requests_s": max(30.0, config.downloader.max_delay_between_requests_s),
}

# Rangos válidos — evita que la UI mande valores absurdos que rompan algo
BOUNDS: dict[str, tuple[float, float]] = {
    "max_content_chars":            (500, 200000),
    "max_content_lines":            (10, 5000),
    "screenshot_wait_seconds":      (0, 60),
    "navigation_timeout_s":         (5, 120),
    "request_timeout_s":            (5, 300),
    "max_retries":                  (0, 10),
    "retry_base_delay_s":           (0.5, 300),
    "retry_max_delay_s":            (5, 3600),
    "generic_concurrency":          (1, 10),
    "min_delay_between_requests_s": (20, 120),
    "max_delay_between_requests_s": (20, 180),
}

_cache: dict[str, Any] | None = None


def _load() -> None:
    global _cache
    _cache = dict(DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            for k, v in saved.items():
                if k in DEFAULTS:
                    _cache[k] = v
            # Saneamos configuraciones antiguas que usaban intervalos muy
            # bajos para Instagram antes de activar el throttle conservador.
            _cache["min_delay_between_requests_s"] = max(
                20.0, float(_cache.get("min_delay_between_requests_s", 20.0))
            )
            _cache["max_delay_between_requests_s"] = max(
                30.0, float(_cache.get("max_delay_between_requests_s", 30.0))
            )
            if _cache["max_delay_between_requests_s"] < _cache["min_delay_between_requests_s"]:
                _cache["max_delay_between_requests_s"] = _cache["min_delay_between_requests_s"]
        except Exception as exc:
            logger.warning("No se pudo leer settings.json (%s), usando defaults", exc)


def get_settings() -> dict[str, Any]:
    if _cache is None:
        _load()
    return dict(_cache)


def get(key: str) -> Any:
    """Acceso rápido a un solo valor, con fallback a DEFAULTS si falta."""
    if _cache is None:
        _load()
    return _cache.get(key, DEFAULTS.get(key))


def update_settings(partial: dict[str, Any]) -> dict[str, Any]:
    if _cache is None:
        _load()
    errors = []
    for k, v in partial.items():
        if k not in DEFAULTS:
            continue
        try:
            v = float(v) if not isinstance(v, bool) else v
        except (TypeError, ValueError):
            errors.append(f"{k}: valor inválido")
            continue
        lo, hi = BOUNDS.get(k, (None, None))
        if lo is not None and not (lo <= v <= hi):
            errors.append(f"{k}: debe estar entre {lo} y {hi}")
            continue
        # Los enteros quedan como enteros (concurrencia, reintentos, líneas)
        if k in ("max_content_chars", "max_content_lines", "max_retries", "generic_concurrency"):
            v = int(v)
        _cache[k] = v

    SETTINGS_FILE.write_text(json.dumps(_cache, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"settings": dict(_cache), "errors": errors}


def reset_to_defaults() -> dict[str, Any]:
    global _cache
    _cache = dict(DEFAULTS)
    SETTINGS_FILE.write_text(json.dumps(_cache, indent=2, ensure_ascii=False), encoding="utf-8")
    return dict(_cache)
