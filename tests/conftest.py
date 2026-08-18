"""
Pytest Configuration & Shared Fixtures
Provides an isolated temporary database for each test to avoid
polluting the real archiver.db.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend import config as config_module
from backend.database.connection import initialize_database


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def temp_db(tmp_path, monkeypatch):
    """Provides a fresh, isolated SQLite database for each test."""
    test_db_path = tmp_path / "test_archiver.db"
    monkeypatch.setattr(config_module.config.database, "path", test_db_path)
    await initialize_database()
    yield test_db_path


@pytest.fixture
def sample_post_metadata():
    """A representative PostMetadata-like dict for testing the pipeline."""
    return {
        "shortcode": "ABC123xyz",
        "author": "testuser",
        "full_name": "Test User",
        "is_private": False,
        "is_verified": False,
        "post_type": "image",
        "caption": "Hello world #test #archive",
        "hashtags": ["test", "archive"],
        "mentions": [],
        "location_name": None,
        "location_lat": None,
        "location_lng": None,
        "like_count": 42,
        "comment_count": 3,
        "media_count": 1,
        "posted_at": None,
        "original_url": "https://www.instagram.com/p/ABC123xyz/",
        "media_items": [{"url": "https://example.com/img.jpg", "is_video": False}],
        "profile_pic_url": "",
        "raw": {},
    }
