"""
File Utilities
Helpers for path generation, hash computation, MIME detection,
human-readable sizes, and safe filename sanitization.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
import unicodedata
from pathlib import Path

from backend.config import config


def compute_sha256(file_path: Path, chunk_size: int = 1048576) -> str:
    """Compute SHA-256 hash of a file in streaming chunks."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def human_size(num_bytes: int | None) -> str:
    if num_bytes is None:
        return "—"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"


def sanitize_filename(name: str, max_length: int = 200) -> str:
    """
    Produce a filesystem-safe filename by:
    1. Normalizing unicode to ASCII equivalents where possible
    2. Stripping characters not allowed on Windows/Linux/macOS
    3. Collapsing whitespace and truncating
    """
    # Normalize unicode (e.g. accented → base char)
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    # Remove disallowed characters
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", "_", name.strip())
    name = name[:max_length]
    return name or "unnamed"


def get_media_dir(shortcode: str) -> Path:
    """
    Return (and create) the directory for a post's media files.
    Structure: media/<first2chars>/<shortcode>/
    Spreading across subdirectories avoids large flat directories.
    """
    bucket = shortcode[:2] if len(shortcode) >= 2 else "xx"
    path = config.media_dir / bucket / shortcode
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_thumbnail_path(sha256: str, extension: str = ".webp") -> Path:
    """
    Deterministic thumbnail path based on file hash.
    Structure: thumbnails/<first2>/<next2>/<hash><ext>
    """
    path = config.thumbnails_dir / sha256[:2] / sha256[2:4]
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{sha256}{extension}"


def detect_mime(file_path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(file_path))
    return mime or "application/octet-stream"


def is_video(file_path: Path) -> bool:
    mime = detect_mime(file_path)
    return mime.startswith("video/") if mime else file_path.suffix.lower() in {
        ".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"
    }


def is_image(file_path: Path) -> bool:
    mime = detect_mime(file_path)
    return mime.startswith("image/") if mime else file_path.suffix.lower() in {
        ".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".avif"
    }


def relative_to_data(path: Path) -> str:
    """Return path relative to DATA_DIR as a forward-slash string."""
    try:
        return path.relative_to(config.data_dir).as_posix()
    except ValueError:
        return path.as_posix()
