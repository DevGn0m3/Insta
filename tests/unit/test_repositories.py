"""
Unit Tests — Repositories
Tests CRUD operations and queries against an isolated test database.
"""

import pytest

from backend.repositories.media_repository import AuthorRepository, TagRepository
from backend.repositories.post_repository import PostRepository
from backend.repositories.task_repository import DownloadTaskRepository
from backend.models import TaskStatus


@pytest.mark.asyncio
async def test_author_upsert_creates_and_updates(temp_db):
    repo = AuthorRepository()

    author_id = await repo.upsert_author({
        "username": "testuser",
        "full_name": "Original Name",
        "is_private": 0,
        "is_verified": 0,
        "profile_pic_url": None,
        "last_updated_at": "2025-01-01T00:00:00Z",
    })
    assert author_id > 0

    # Upsert again with updated name — should not create a duplicate row
    author_id_2 = await repo.upsert_author({
        "username": "testuser",
        "full_name": "Updated Name",
        "is_private": 0,
        "is_verified": 1,
        "profile_pic_url": None,
        "last_updated_at": "2025-01-02T00:00:00Z",
    })
    assert author_id_2 == author_id

    fetched = await repo.get_by_username("testuser")
    assert fetched["full_name"] == "Updated Name"
    assert fetched["is_verified"] == 1


@pytest.mark.asyncio
async def test_post_creation_and_retrieval(temp_db):
    author_repo = AuthorRepository()
    post_repo = PostRepository()

    author_id = await author_repo.upsert_author({
        "username": "poster", "full_name": "", "is_private": 0,
        "is_verified": 0, "profile_pic_url": None,
        "last_updated_at": "2025-01-01T00:00:00Z",
    })

    post_id = await post_repo.insert({
        "shortcode": "XYZ789",
        "author_id": author_id,
        "post_type": "image",
        "caption": "Test caption",
        "hashtags": "[]",
        "mentions": "[]",
        "media_count": 1,
        "original_url": "https://www.instagram.com/p/XYZ789/",
    })
    assert post_id > 0

    fetched = await post_repo.get_by_shortcode("XYZ789")
    assert fetched is not None
    assert fetched["caption"] == "Test caption"


@pytest.mark.asyncio
async def test_tag_get_or_create_is_idempotent(temp_db):
    tag_repo = TagRepository()

    id1 = await tag_repo.get_or_create("perro", "ai")
    id2 = await tag_repo.get_or_create("perro", "ai")
    id3 = await tag_repo.get_or_create("PERRO", "ai")  # Case-insensitive

    assert id1 == id2 == id3


@pytest.mark.asyncio
async def test_download_task_enqueue_deduplicates(temp_db):
    task_repo = DownloadTaskRepository()

    url = "https://www.instagram.com/p/SAME123/"
    task_id_1 = await task_repo.enqueue(url)
    task_id_2 = await task_repo.enqueue(url)  # Same URL while still queued

    assert task_id_1 == task_id_2


@pytest.mark.asyncio
async def test_task_status_transitions(temp_db):
    task_repo = DownloadTaskRepository()

    task_id = await task_repo.enqueue("https://www.instagram.com/p/STATUS123/")
    await task_repo.set_status(task_id, TaskStatus.DOWNLOADING)

    task = await task_repo.get_by_id(task_id)
    assert task["status"] == "downloading"

    await task_repo.set_status(task_id, TaskStatus.COMPLETED, progress_pct=100.0)
    task = await task_repo.get_by_id(task_id)
    assert task["status"] == "completed"
    assert task["progress_pct"] == 100.0
    assert task["completed_at"] is not None


@pytest.mark.asyncio
async def test_queue_summary_counts_correctly(temp_db):
    task_repo = DownloadTaskRepository()

    await task_repo.enqueue("https://www.instagram.com/p/A1/")
    await task_repo.enqueue("https://www.instagram.com/p/A2/")
    task3 = await task_repo.enqueue("https://www.instagram.com/p/A3/")
    await task_repo.set_status(task3, TaskStatus.COMPLETED)

    summary = await task_repo.get_queue_summary()
    assert summary.get("queued") == 2
    assert summary.get("completed") == 1
