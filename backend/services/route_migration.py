"""
Route Migration Service
Fixes Windows absolute paths stored in the database by converting them
to relative paths based on the data directory. Handles both file_path
and thumbnail_path columns in media_files.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from backend.config import config
from backend.database.connection import get_db

logger = logging.getLogger(__name__)


def _normalize_path(raw: str) -> str:
    """
    Normalize a stored path to a relative forward-slash path from the data dir.
    Handles:
    - Windows absolute paths: C:\\Users\\...\\instagram-archiver\\data\\media\\...
    - Windows paths with mixed separators: C:/Users/.../data/media/...
    - Already relative forward-slash paths: media/ab/shortcode/file.jpg
    - Paths starting with /media/ or /thumbnails/
    """
    if not raw:
        return raw

    # Normalize backslashes to forward slashes
    normalized = raw.replace("\\", "/")

    # If it already looks relative (starts with media/ or thumbnails/), keep as-is
    if normalized.startswith(("media/", "thumbnails/")):
        return normalized

    # Try to find /data/ in the path and extract everything after it
    data_idx = normalized.lower().rfind("/data/")
    if data_idx != -1:
        after_data = normalized[data_idx + 6:]  # skip "/data/"
        # Remove leading "instagram-archiver/data/" prefix variations
        return after_data

    # Try to find /media/ or /thumbnails/ anywhere in the path
    for prefix in ["/media/", "/thumbnails/"]:
        idx = normalized.find(prefix)
        if idx != -1:
            return normalized[idx + 1:]  # return without leading slash

    # Last resort: try to extract just the relative portion after the project name
    patterns = [r"/instagram-archiver/(.+)", r"/Instagram.*?/(.+)", r"[A-Z]:/.+/(.+?/media/.+)", r"[A-Z]:/.+/(.+?/thumbnails/.+)"]
    for pattern in patterns:
        m = re.search(pattern, normalized, re.IGNORECASE)
        if m:
            return m.group(1)

    # If nothing matched, return as-is (might already be OK on current system)
    return normalized


def _is_valid_path(db_path: str) -> bool:
    """Check if the stored path resolves to an existing file on disk."""
    if not db_path:
        return False
    p = config.data_dir / db_path
    return p.exists()


async def migrate_paths() -> dict:
    """
    Main migration function. Scans all media_files rows, normalizes paths,
    and verifies they exist on disk. Returns a summary dict.
    """
    migrated = 0
    fixed = 0
    broken = 0
    total = 0

    async with get_db() as db:
        cur = await db.execute("SELECT id, file_path, thumbnail_path FROM media_files")
        rows = [dict(r) for r in await cur.fetchall()]
        total = len(rows)

        for row in rows:
            file_path = row["file_path"]
            thumb_path = row["thumbnail_path"]

            new_file = _normalize_path(file_path)
            new_thumb = _normalize_path(thumb_path) if thumb_path else thumb_path

            changed = False
            if new_file != file_path:
                await db.execute(
                    "UPDATE media_files SET file_path = ? WHERE id = ?",
                    (new_file, row["id"]),
                )
                changed = True
                migrated += 1

            if thumb_path and new_thumb != thumb_path:
                await db.execute(
                    "UPDATE media_files SET thumbnail_path = ? WHERE id = ?",
                    (new_thumb, row["id"]),
                )
                changed = True
                migrated += 1

            if changed:
                # Verify the new path exists
                if not _is_valid_path(new_file):
                    broken += 1
                    logger.warning("Broken path after migration: id=%d, path=%s", row["id"], new_file)
                else:
                    fixed += 1

        await db.commit()

    return {
        "total": total,
        "migrated": migrated,
        "verified_ok": fixed,
        "still_broken": broken,
    }


async def fix_broken_paths() -> dict:
    """
    Attempt to fix broken paths by searching for files with matching names
    in the media/thumbnails directories.
    """
    async with get_db() as db:
        cur = await db.execute(
            "SELECT id, file_path, file_name, file_type FROM media_files"
        )
        rows = [dict(r) for r in await cur.fetchall()]

    fixed = 0
    for row in rows:
        fp = Path(row["file_path"])
        if _is_valid_path(row["file_path"]):
            continue

        # Try to find the file by name in the media directory
        file_name = row["file_name"]
        found = False

        if row["file_type"] == "thumbnail":
            # Search in thumbnails
            for root in [config.thumbnails_dir]:
                for match in root.rglob(file_name):
                    rel = match.relative_to(config.data_dir).as_posix()
                    await _update_media_row(db, row["id"], rel)
                    fixed += 1
                    found = True
                    break
                if found:
                    break
        else:
            # Search in media
            for root in [config.media_dir]:
                for match in root.rglob(file_name):
                    rel = match.relative_to(config.data_dir).as_posix()
                    await _update_media_row(db, row["id"], rel)
                    fixed += 1
                    found = True
                    break
                if found:
                    break

        if not found:
            logger.debug("Could not fix path for id=%d name=%s", row["id"], file_name)

    await db.commit()
    return {"fixed": fixed}


async def _update_media_row(db, media_id: int, new_path: str):
    await db.execute(
        "UPDATE media_files SET file_path = ? WHERE id = ?",
        (new_path, media_id),
    )
