"""
Logger
Configures structured logging with file rotation, console output,
and a context-aware adapter for per-task log entries.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from backend.config import config


def setup_logging() -> None:
    """Initialize root logger with rotating file handler and console handler."""
    log_dir: Path = config.logs_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if config.debug else logging.INFO)

    # Rotating file: 10 MB × 5 files
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "archiver.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    # Error-only file
    error_handler = logging.handlers.RotatingFileHandler(
        log_dir / "errors.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)

    # Console
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    root.addHandler(file_handler)
    root.addHandler(error_handler)
    root.addHandler(console_handler)

    # Silence noisy third-party loggers
    for noisy in ("urllib3", "httpx", "httpcore", "PIL", "aiosqlite"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


class TaskLogger:
    """
    Wrapper that writes log entries both to the standard logger
    and to the download_logs table via the task repository.
    """

    def __init__(self, task_id: int, task_repo) -> None:
        self._task_id = task_id
        self._repo = task_repo
        self._logger = logging.getLogger(f"task.{task_id}")

    async def info(self, message: str, details: str | None = None) -> None:
        self._logger.info("[task=%d] %s", self._task_id, message)
        await self._repo.add_log(self._task_id, "info", message, details)

    async def warning(self, message: str, details: str | None = None) -> None:
        self._logger.warning("[task=%d] %s", self._task_id, message)
        await self._repo.add_log(self._task_id, "warning", message, details)

    async def error(self, message: str, details: str | None = None) -> None:
        self._logger.error("[task=%d] %s", self._task_id, message)
        await self._repo.add_log(self._task_id, "error", message, details)

    async def debug(self, message: str, details: str | None = None) -> None:
        self._logger.debug("[task=%d] %s", self._task_id, message)
        if config.debug:
            await self._repo.add_log(self._task_id, "debug", message, details)
