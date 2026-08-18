"""
Unit Tests — Utilities
Tests file utilities, human behavior simulator, and Instagram URL parsing.
"""

import asyncio
import time

import pytest

from backend.services.downloader.instagram_client import InstagramClient
from backend.utils.file_utils import human_size, sanitize_filename
from backend.utils.human_behavior import HumanBehaviorSimulator


class TestShortcodeExtraction:

    def test_extracts_from_post_url(self):
        url = "https://www.instagram.com/p/ABC123xyz/"
        assert InstagramClient.extract_shortcode(url) == "ABC123xyz"

    def test_extracts_from_reel_url(self):
        url = "https://www.instagram.com/reel/XYZ789abc/"
        assert InstagramClient.extract_shortcode(url) == "XYZ789abc"

    def test_extracts_from_tv_url(self):
        url = "https://www.instagram.com/tv/DEF456ghi/"
        assert InstagramClient.extract_shortcode(url) == "DEF456ghi"

    def test_extracts_with_query_params(self):
        url = "https://www.instagram.com/p/ABC123xyz/?utm_source=ig_web"
        assert InstagramClient.extract_shortcode(url) == "ABC123xyz"

    def test_extracts_bare_shortcode(self):
        assert InstagramClient.extract_shortcode("ABC123xyz") == "ABC123xyz"

    def test_invalid_url_returns_none(self):
        assert InstagramClient.extract_shortcode("https://google.com") is None


class TestFileUtils:

    def test_human_size_bytes(self):
        assert human_size(500) == "500.0 B"

    def test_human_size_megabytes(self):
        assert "MB" in human_size(5 * 1024 * 1024)

    def test_human_size_none(self):
        assert human_size(None) == "—"

    def test_sanitize_filename_removes_invalid_chars(self):
        result = sanitize_filename('test<>:"/\\|?*.jpg')
        for ch in '<>:"/\\|?*':
            assert ch not in result

    def test_sanitize_filename_handles_unicode(self):
        result = sanitize_filename("café_münchen")
        assert result  # Should not crash, should produce something usable

    def test_sanitize_filename_truncates(self):
        long_name = "a" * 300
        result = sanitize_filename(long_name, max_length=50)
        assert len(result) <= 50


class TestHumanBehaviorSimulator:

    @pytest.mark.asyncio
    async def test_wait_between_requests_respects_bounds(self):
        sim = HumanBehaviorSimulator()
        start = time.monotonic()
        await sim.wait_between_requests()
        elapsed = time.monotonic() - start
        assert sim._cfg.min_delay_between_requests_s <= elapsed <= sim._cfg.max_delay_between_requests_s + 0.5

    @pytest.mark.asyncio
    async def test_session_tracks_posts_processed(self):
        sim = HumanBehaviorSimulator()
        sim._cfg.session_pause_every_n_posts = 100  # Avoid triggering long pause in test
        sim._cfg.min_delay_between_posts_s = 0.01
        sim._cfg.max_delay_between_posts_s = 0.02

        await sim.wait_between_posts()
        await sim.wait_between_posts()

        assert sim.posts_processed == 2

    def test_reset_session_clears_counters(self):
        sim = HumanBehaviorSimulator()
        sim._session.posts_processed = 10
        sim.reset_session()
        assert sim.posts_processed == 0
