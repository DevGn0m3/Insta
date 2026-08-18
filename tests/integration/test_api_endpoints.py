"""
Integration Tests — API Endpoints
Tests the FastAPI routes end-to-end using httpx AsyncClient against
an isolated test database, validating the full request/response cycle.
"""

import pytest
from httpx import AsyncClient, ASGITransport

from backend.main import app
from backend.repositories.media_repository import AuthorRepository
from backend.repositories.post_repository import PostRepository


@pytest.fixture
async def client(temp_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health_endpoint_returns_200(client):
    response = await client.get("/api/stats/library")
    assert response.status_code == 200
    data = response.json()
    assert "total_posts" in data


@pytest.mark.asyncio
async def test_list_posts_empty_library(client):
    response = await client.get("/api/library/posts")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["posts"] == []


@pytest.mark.asyncio
async def test_get_nonexistent_post_returns_404(client):
    response = await client.get("/api/library/posts/99999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_search_with_no_results(client):
    response = await client.get("/api/search?q=nonexistentterm12345")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_post_favorite_toggle(temp_db, client):
    # Seed a post directly via repositories
    author_repo = AuthorRepository()
    post_repo = PostRepository()

    author_id = await author_repo.upsert_author({
        "username": "integtest", "full_name": "", "is_private": 0,
        "is_verified": 0, "profile_pic_url": None,
        "last_updated_at": "2025-01-01T00:00:00Z",
    })
    post_id = await post_repo.insert({
        "shortcode": "INTEG123",
        "author_id": author_id,
        "post_type": "image",
        "caption": "",
        "hashtags": "[]",
        "mentions": "[]",
        "media_count": 1,
        "original_url": "https://www.instagram.com/p/INTEG123/",
    })

    response = await client.patch(
        f"/api/library/posts/{post_id}/favorite",
        json={"is_favorite": True},
    )
    assert response.status_code == 200
    assert response.json()["is_favorite"] is True


@pytest.mark.asyncio
async def test_enqueue_download_creates_task(client):
    response = await client.post(
        "/api/downloads",
        json={"url": "https://www.instagram.com/p/NEWURL123/", "priority": 5},
    )
    assert response.status_code == 202
    data = response.json()
    assert "task_id" in data
    assert data["status"] == "queued"


@pytest.mark.asyncio
async def test_enqueue_batch_downloads(client):
    response = await client.post(
        "/api/downloads/batch",
        json={
            "urls": [
                "https://www.instagram.com/p/BATCH1/",
                "https://www.instagram.com/p/BATCH2/",
            ],
            "priority": 5,
        },
    )
    assert response.status_code == 202
    data = response.json()
    assert data["queued"] == 2


@pytest.mark.asyncio
async def test_search_suggestions_endpoint(client):
    response = await client.get("/api/search/suggestions?q=te")
    assert response.status_code == 200
    data = response.json()
    assert "tags" in data
    assert "authors" in data
    assert "hashtags" in data


@pytest.mark.asyncio
async def test_collections_crud_flow(client):
    create_resp = await client.post(
        "/api/library/collections",
        json={"name": "Test Collection", "description": "A test"},
    )
    assert create_resp.status_code == 200
    collection_id = create_resp.json()["id"]

    list_resp = await client.get("/api/library/collections")
    assert list_resp.status_code == 200
    names = [c["name"] for c in list_resp.json()]
    assert "Test Collection" in names
