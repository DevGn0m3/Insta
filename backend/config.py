"""
Instagram Archiver - Global Configuration
Centralizes all configurable parameters for the application.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import List


# ─── Base Paths ───────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
MEDIA_DIR = DATA_DIR / "media"
THUMBNAILS_DIR = DATA_DIR / "thumbnails"
DB_DIR = DATA_DIR / "db"
LOGS_DIR = DATA_DIR / "logs"
FRONTEND_DIR = BASE_DIR / "frontend"

# Ensure directories exist
for _dir in [MEDIA_DIR, THUMBNAILS_DIR, DB_DIR, LOGS_DIR]:
    _dir.mkdir(parents=True, exist_ok=True)


@dataclass
class DatabaseConfig:
    path: Path = DB_DIR / "archiver.db"
    wal_mode: bool = True
    cache_size_kb: int = 65536          # 64 MB SQLite cache
    mmap_size_bytes: int = 268435456    # 256 MB mmap
    busy_timeout_ms: int = 30000


@dataclass
class DownloaderConfig:
    max_concurrent_downloads: int = 3
    max_retries: int = 3
    retry_base_delay_s: float = 2.0     # Exponential backoff base
    retry_max_delay_s: float = 600
    retry_jitter_max_s: float = 5.0     # Random jitter to avoid thundering herd
    request_timeout_s: int = 60
    chunk_size_bytes: int = 1048576     # 1 MB chunks for streaming downloads

    # Instagram request pacing (seconds). This is a conservative throttle,
    # not a mechanism to bypass platform protections.
    min_delay_between_requests_s: float = 20.0
    max_delay_between_requests_s: float = 30.0
    min_delay_between_posts_s: float = 30.0
    max_delay_between_posts_s: float = 60.0
    session_pause_every_n_posts: int = 10   # Pause after N posts
    session_pause_min_s: float = 60.0
    session_pause_max_s: float = 180.0

    # instaloader settings
    instagram_session_file: Path = DATA_DIR / ".instagram_session"
    download_videos: bool = True
    download_video_thumbnails: bool = True
    download_geotags: bool = True
    download_comments: bool = False     # Resource intensive, opt-in
    compress_json: bool = False
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )


@dataclass
class ThumbnailConfig:
    width: int = 480
    height: int = 480
    quality: int = 85
    format: str = "WEBP"
    fit_mode: str = "cover"             # cover | contain


@dataclass
class AIConfig:
    enabled: bool = True
    clip_model: str = "ViT-B/32"        # Smaller model for CPU
    ocr_languages: List[str] = field(default_factory=lambda: ["en", "es"])
    batch_size: int = 4                 # Small batches for CPU
    ai_queue_workers: int = 1           # Single worker to avoid CPU overload
    min_tag_confidence: float = 0.20    # Minimum CLIP confidence to keep tag
    max_tags_per_image: int = 15

    # Predefined label categories for CLIP zero-shot classification
    label_categories: List[str] = field(default_factory=lambda: [
        "automobile", "motorcycle", "bicycle", "airplane", "boat",
        "person", "portrait", "group of people", "selfie",
        "computer", "electronics", "smartphone", "gaming",
        "anime", "art", "illustration", "painting",
        "travel", "landscape", "nature", "sunset", "sky",
        "sports", "fitness", "workout",
        "fashion", "clothing", "accessories",
        "music", "concert", "festival",
        "text", "document", "screenshot",
        "meme", "humor", "Infosec"
    ])


@dataclass
class SearchConfig:
    fts_tokenizer: str = "unicode61"    # SQLite FTS5 tokenizer
    max_results_per_page: int = 50
    snippet_tokens: int = 30


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8000
    workers: int = 1                    # Single worker for SQLite compatibility
    reload: bool = False
    cors_origins: List[str] = field(default_factory=lambda: [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "null",                         # For file:// protocol during dev
    ])


@dataclass
class AppConfig:
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    downloader: DownloaderConfig = field(default_factory=DownloaderConfig)
    thumbnail: ThumbnailConfig = field(default_factory=ThumbnailConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    server: ServerConfig = field(default_factory=ServerConfig)

    # Paths
    base_dir: Path = BASE_DIR
    data_dir: Path = DATA_DIR
    media_dir: Path = MEDIA_DIR
    thumbnails_dir: Path = THUMBNAILS_DIR
    db_dir: Path = DB_DIR
    logs_dir: Path = LOGS_DIR
    frontend_dir: Path = FRONTEND_DIR

    # App metadata
    app_name: str = "Instagram Archiver"
    app_version: str = "1.0.0"
    debug: bool = False


# Singleton instance
config = AppConfig()
