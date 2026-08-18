"""
Human Behavior Simulator
Introduces realistic delays and patterns to avoid automated-behavior detection.
All timing is randomized within configured bounds using a non-uniform distribution
that mimics real human browsing patterns.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field

from backend.config import config

logger = logging.getLogger(__name__)


@dataclass
class SessionState:
    """Tracks per-session activity to adjust timing dynamically."""
    posts_processed: int = 0
    requests_made: int = 0
    session_start: float = field(default_factory=time.monotonic)
    last_request_time: float = field(default_factory=time.monotonic)
    last_long_pause_at: int = 0  # posts_processed count at last long pause


class HumanBehaviorSimulator:
    """
    Simulates human browsing behavior by introducing variable delays,
    occasional longer pauses, and non-uniform request patterns.
    This reduces the risk of automated-access detection.
    """

    def __init__(self) -> None:
        self._cfg = config.downloader
        self._session = SessionState()

    def reset_session(self) -> None:
        self._session = SessionState()

    async def wait_between_requests(self) -> None:
        """Short delay between individual HTTP requests within a post download."""
        delay = self._human_delay(
            self._cfg.min_delay_between_requests_s,
            self._cfg.max_delay_between_requests_s,
        )
        logger.debug("Human delay between requests: %.2fs", delay)
        await asyncio.sleep(delay)
        self._session.requests_made += 1
        self._session.last_request_time = time.monotonic()

    async def wait_between_posts(self) -> None:
        """
        Longer delay between processing different posts.
        Includes occasional long pauses to simulate natural browsing breaks.
        """
        self._session.posts_processed += 1

        # Trigger a long "break" every N posts
        posts_since_pause = (
            self._session.posts_processed - self._session.last_long_pause_at
        )

        if posts_since_pause >= self._cfg.session_pause_every_n_posts:
            pause = self._human_delay(
                self._cfg.session_pause_min_s,
                self._cfg.session_pause_max_s,
            )
            logger.info(
                "Simulating user break after %d posts (%.0fs pause)",
                self._session.posts_processed,
                pause,
            )
            await asyncio.sleep(pause)
            self._session.last_long_pause_at = self._session.posts_processed
        else:
            delay = self._human_delay(
                self._cfg.min_delay_between_posts_s,
                self._cfg.max_delay_between_posts_s,
            )
            logger.debug("Human delay between posts: %.2fs", delay)
            await asyncio.sleep(delay)

    async def wait_before_login(self) -> None:
        """Pause before performing a login action."""
        await asyncio.sleep(self._human_delay(2.0, 5.0))

    async def wait_after_error(self, attempt: int) -> None:
        """
        Exponential backoff with jitter after a failed request.
        Longer waits on repeated failures to avoid hammering a rate-limited endpoint.
        """
        base = self._cfg.retry_base_delay_s
        max_d = self._cfg.retry_max_delay_s
        jitter = random.uniform(0, self._cfg.retry_jitter_max_s)
        delay = min(base * (2 ** (attempt - 1)) + jitter, max_d)
        logger.info("Backoff delay after attempt %d: %.1fs", attempt, delay)
        await asyncio.sleep(delay)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _human_delay(self, min_s: float, max_s: float) -> float:
        """
        Generate a delay using a beta distribution to produce more
        natural-looking timing (clustered toward the middle of the range,
        with occasional short and long values).
        """
        # Beta(2,2) gives a bell-curve shape within [0,1]
        normalized = random.betavariate(2, 2)
        return min_s + normalized * (max_s - min_s)

    @property
    def session_duration_s(self) -> float:
        return time.monotonic() - self._session.session_start

    @property
    def posts_processed(self) -> int:
        return self._session.posts_processed
