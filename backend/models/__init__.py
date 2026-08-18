"""
Domain Models
Pydantic v2 models for all database entities.
Used for validation, serialization, and API responses.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, computed_field


# ─── Enums ────────────────────────────────────────────────────────────────────

class PostType(str, Enum):
    IMAGE    = "image"
    VIDEO    = "video"
    CAROUSEL = "carousel"
    REEL     = "reel"
    UNKNOWN  = "unknown"


class FileType(str, Enum):
    IMAGE       = "image"
    VIDEO       = "video"
    THUMBNAIL   = "thumbnail"
    PROFILE_PIC = "profile_pic"


class TagType(str, Enum):
    AI       = "ai"
    HASHTAG  = "hashtag"
    MANUAL   = "manual"
    OCR      = "ocr"
    COLOR    = "color"
    OBJECT   = "object"
    BRAND    = "brand"


class TagSource(str, Enum):
    AI      = "ai"
    HASHTAG = "hashtag"
    MANUAL  = "manual"
    OCR     = "ocr"
    COLOR   = "color"


class TaskStatus(str, Enum):
    QUEUED               = "queued"
    ANALYZING            = "analyzing"
    DOWNLOADING          = "downloading"
    PROCESSING_AI        = "processing_ai"
    GENERATING_THUMBNAILS = "generating_thumbnails"
    SAVING               = "saving"
    COMPLETED            = "completed"
    ERROR                = "error"
    PAUSED               = "paused"
    CANCELLED            = "cancelled"


class LogLevel(str, Enum):
    INFO    = "info"
    WARNING = "warning"
    ERROR   = "error"
    DEBUG   = "debug"


# ─── Author ───────────────────────────────────────────────────────────────────

class AuthorBase(BaseModel):
    username:        str
    full_name:       Optional[str] = None
    bio:             Optional[str] = None
    profile_pic_url: Optional[str] = None
    is_private:      bool = False
    is_verified:     bool = False
    follower_count:  Optional[int] = None
    following_count: Optional[int] = None
    post_count:      Optional[int] = None
    external_url:    Optional[str] = None


class AuthorCreate(AuthorBase):
    pass


class Author(AuthorBase):
    id:               int
    profile_pic_path: Optional[str] = None
    first_seen_at:    datetime
    last_updated_at:  datetime

    model_config = {"from_attributes": True}


# ─── Media File ───────────────────────────────────────────────────────────────

class MediaFileBase(BaseModel):
    post_id:         int
    file_path:       str
    file_name:       str
    file_type:       FileType
    mime_type:       Optional[str] = None
    file_size_bytes: Optional[int] = None
    width_px:        Optional[int] = None
    height_px:       Optional[int] = None
    duration_s:      Optional[float] = None
    carousel_index:  Optional[int] = None
    sha256_hash:     Optional[str] = None
    thumbnail_path:  Optional[str] = None
    is_original:     bool = True


class MediaFileCreate(MediaFileBase):
    pass


class MediaFile(MediaFileBase):
    id:         int
    created_at: datetime

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def url_path(self) -> str:
        """Relative URL path for serving the file."""
        return f"/media/{self.file_name}"

    @computed_field
    @property
    def thumbnail_url(self) -> Optional[str]:
        if self.thumbnail_path:
            from pathlib import Path
            return f"/thumbnails/{Path(self.thumbnail_path).name}"
        return None


# ─── Tag ──────────────────────────────────────────────────────────────────────

class TagBase(BaseModel):
    name:     str
    tag_type: TagType


class TagCreate(TagBase):
    pass


class Tag(TagBase):
    id:         int
    created_at: datetime

    model_config = {"from_attributes": True}


class PostTagAssociation(BaseModel):
    tag_id:     int
    confidence: Optional[float] = None
    source:     TagSource
    name:       Optional[str] = None      # Populated on joins


# ─── Post ─────────────────────────────────────────────────────────────────────

class PostBase(BaseModel):
    shortcode:     str
    author_id:     int
    post_type:     PostType
    caption:       Optional[str] = None
    hashtags:      Optional[list[str]] = None
    mentions:      Optional[list[str]] = None
    location_name: Optional[str] = None
    location_lat:  Optional[float] = None
    location_lng:  Optional[float] = None
    like_count:    Optional[int] = None
    comment_count: Optional[int] = None
    media_count:   int = 1
    original_url:  str
    posted_at:     Optional[datetime] = None

    @field_validator("hashtags", "mentions", mode="before")
    @classmethod
    def parse_json_list(cls, v: Any) -> Optional[list[str]]:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return []
        return v


class PostCreate(PostBase):
    raw_metadata: Optional[dict] = None


class Post(PostBase):
    id:               int
    downloaded_at:    datetime
    last_verified_at: Optional[datetime] = None
    integrity_ok:     bool = True
    is_favorite:      bool = False
    notes:            Optional[str] = None
    raw_metadata:     Optional[str] = None

    # Populated via joins
    author:      Optional[Author] = None
    media_files: list[MediaFile] = Field(default_factory=list)
    tags:        list[PostTagAssociation] = Field(default_factory=list)

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def cover_thumbnail(self) -> Optional[str]:
        """URL of the first media file's thumbnail."""
        for f in self.media_files:
            if f.thumbnail_url:
                return f.thumbnail_url
            if f.file_type == FileType.IMAGE:
                return f.url_path
        return None


class PostSummary(BaseModel):
    """Lightweight post representation for library grid views."""
    id:            int
    shortcode:     str
    post_type:     PostType
    media_count:   int
    posted_at:     Optional[datetime]
    downloaded_at: datetime
    is_favorite:   bool
    author_username: str
    cover_thumbnail: Optional[str]
    caption_preview: Optional[str]

    @field_validator("caption_preview", mode="before")
    @classmethod
    def truncate_caption(cls, v: Any) -> Optional[str]:
        if isinstance(v, str) and len(v) > 120:
            return v[:117] + "..."
        return v


# ─── Download Task ────────────────────────────────────────────────────────────

class DownloadTaskCreate(BaseModel):
    url:      str
    priority: int = Field(default=5, ge=1, le=10)


class DownloadTask(BaseModel):
    id:               int
    url:              str
    shortcode:        Optional[str] = None
    status:           TaskStatus
    priority:         int
    attempt_count:    int
    max_attempts:     int
    error_message:    Optional[str] = None
    progress_pct:     float
    bytes_total:      Optional[int] = None
    bytes_downloaded: Optional[int] = None
    speed_bps:        Optional[float] = None
    eta_seconds:      Optional[float] = None
    post_id:          Optional[int] = None
    created_at:       datetime
    started_at:       Optional[datetime] = None
    completed_at:     Optional[datetime] = None

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def speed_human(self) -> Optional[str]:
        if self.speed_bps is None:
            return None
        bps = self.speed_bps
        for unit in ["B/s", "KB/s", "MB/s", "GB/s"]:
            if bps < 1024:
                return f"{bps:.1f} {unit}"
            bps /= 1024
        return f"{bps:.1f} TB/s"

    @computed_field
    @property
    def eta_human(self) -> Optional[str]:
        if self.eta_seconds is None:
            return None
        s = int(self.eta_seconds)
        if s < 60:
            return f"{s}s"
        m, s = divmod(s, 60)
        if m < 60:
            return f"{m}m {s}s"
        h, m = divmod(m, 60)
        return f"{h}h {m}m"


class DownloadLog(BaseModel):
    id:        int
    task_id:   int
    level:     LogLevel
    message:   str
    details:   Optional[str] = None
    logged_at: datetime

    model_config = {"from_attributes": True}


# ─── Collection ───────────────────────────────────────────────────────────────

class CollectionCreate(BaseModel):
    name:        str = Field(min_length=1, max_length=200)
    description: Optional[str] = None


class Collection(CollectionCreate):
    id:           int
    cover_post_id: Optional[int] = None
    created_at:   datetime
    updated_at:   datetime
    post_count:   int = 0

    model_config = {"from_attributes": True}


# ─── Search ───────────────────────────────────────────────────────────────────

class SearchQuery(BaseModel):
    q:           Optional[str] = None
    author:      Optional[str] = None
    post_type:   Optional[PostType] = None
    tags:        Optional[list[str]] = None
    date_from:   Optional[datetime] = None
    date_to:     Optional[datetime] = None
    is_favorite: Optional[bool] = None
    has_ocr:     Optional[bool] = None
    page:        int = Field(default=1, ge=1)
    per_page:    int = Field(default=50, ge=1, le=200)
    sort_by:     str = Field(default="downloaded_at")
    sort_dir:    str = Field(default="desc", pattern="^(asc|desc)$")


class SearchResult(BaseModel):
    total:    int
    page:     int
    per_page: int
    pages:    int
    posts:    list[PostSummary]


# ─── Stats ────────────────────────────────────────────────────────────────────

class LibraryStats(BaseModel):
    total_posts:    int = 0
    total_authors:  int = 0
    total_images:   int = 0
    total_videos:   int = 0
    total_carousels: int = 0
    total_reels:    int = 0
    total_ai_tags:  int = 0
    total_ocr_texts: int = 0
    total_size_bytes: int = 0

    @computed_field
    @property
    def total_size_human(self) -> str:
        b = self.total_size_bytes
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if b < 1024:
                return f"{b:.2f} {unit}"
            b /= 1024
        return f"{b:.2f} PB"
