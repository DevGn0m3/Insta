-- Instagram Archiver - Complete Database Schema
-- SQLite with FTS5, WAL mode, and full normalization
-- Version: 1.0.0

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA temp_store = MEMORY;

-- ─── Authors ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS authors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    full_name       TEXT,
    bio             TEXT,
    profile_pic_url TEXT,
    profile_pic_path TEXT,
    is_private      INTEGER NOT NULL DEFAULT 0,
    is_verified     INTEGER NOT NULL DEFAULT 0,
    follower_count  INTEGER,
    following_count INTEGER,
    post_count      INTEGER,
    external_url    TEXT,
    first_seen_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_updated_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_authors_username ON authors(username);

-- ─── Posts ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS posts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    shortcode           TEXT    NOT NULL UNIQUE,
    author_id           INTEGER NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
    post_type           TEXT    NOT NULL CHECK(post_type IN ('image','video','carousel','reel','unknown')),
    caption             TEXT,
    hashtags            TEXT,   -- JSON array of hashtag strings
    mentions            TEXT,   -- JSON array of mentioned usernames
    location_name       TEXT,
    location_lat        REAL,
    location_lng        REAL,
    like_count          INTEGER,
    comment_count       INTEGER,
    media_count         INTEGER NOT NULL DEFAULT 1,
    original_url        TEXT    NOT NULL,
    posted_at           TEXT,   -- ISO8601 from Instagram
    downloaded_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_verified_at    TEXT,
    integrity_ok        INTEGER NOT NULL DEFAULT 1,
    is_favorite         INTEGER NOT NULL DEFAULT 0,
    notes               TEXT,
    raw_metadata        TEXT    -- Full JSON from instaloader
);

CREATE INDEX IF NOT EXISTS idx_posts_shortcode    ON posts(shortcode);
CREATE INDEX IF NOT EXISTS idx_posts_author_id    ON posts(author_id);
CREATE INDEX IF NOT EXISTS idx_posts_post_type    ON posts(post_type);
CREATE INDEX IF NOT EXISTS idx_posts_posted_at    ON posts(posted_at);
CREATE INDEX IF NOT EXISTS idx_posts_downloaded_at ON posts(downloaded_at);
CREATE INDEX IF NOT EXISTS idx_posts_is_favorite  ON posts(is_favorite);

-- ─── Media Files ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS media_files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id         INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    file_path       TEXT    NOT NULL UNIQUE,
    file_name       TEXT    NOT NULL,
    file_type       TEXT    NOT NULL CHECK(file_type IN ('image','video','thumbnail','profile_pic')),
    mime_type       TEXT,
    file_size_bytes INTEGER,
    width_px        INTEGER,
    height_px       INTEGER,
    duration_s      REAL,           -- For videos/reels
    carousel_index  INTEGER,        -- Position in carousel (NULL if not carousel)
    sha256_hash     TEXT,
    thumbnail_path  TEXT,
    is_original     INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_media_post_id    ON media_files(post_id);
CREATE INDEX IF NOT EXISTS idx_media_file_type  ON media_files(file_type);
CREATE INDEX IF NOT EXISTS idx_media_sha256     ON media_files(sha256_hash);

-- ─── Tags ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    tag_type    TEXT    NOT NULL CHECK(tag_type IN ('ai','hashtag','manual','ocr','color','object','brand')),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_tags_name     ON tags(name);
CREATE INDEX IF NOT EXISTS idx_tags_tag_type ON tags(tag_type);

-- ─── Post Tags (M:N) ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS post_tags (
    post_id     INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    confidence  REAL,           -- AI confidence score (0-1), NULL for manual/hashtag
    source      TEXT    NOT NULL CHECK(source IN ('ai','hashtag','manual','ocr','color')),
    PRIMARY KEY (post_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_post_tags_post_id ON post_tags(post_id);
CREATE INDEX IF NOT EXISTS idx_post_tags_tag_id  ON post_tags(tag_id);

-- ─── Media Tags (M:N) ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS media_tags (
    media_id    INTEGER NOT NULL REFERENCES media_files(id) ON DELETE CASCADE,
    tag_id      INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    confidence  REAL,
    source      TEXT    NOT NULL CHECK(source IN ('ai','ocr','color','manual')),
    PRIMARY KEY (media_id, tag_id)
);

-- ─── OCR Results ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ocr_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id    INTEGER NOT NULL UNIQUE REFERENCES media_files(id) ON DELETE CASCADE,
    text        TEXT,
    confidence  REAL,
    language    TEXT,
    processed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ─── Color Palette ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS color_palettes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    media_id    INTEGER NOT NULL REFERENCES media_files(id) ON DELETE CASCADE,
    hex_color   TEXT    NOT NULL,
    percentage  REAL    NOT NULL,
    color_name  TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_color_media_id ON color_palettes(media_id);
CREATE INDEX IF NOT EXISTS idx_color_hex      ON color_palettes(hex_color);

-- ─── Collections ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS collections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT,
    cover_post_id INTEGER REFERENCES posts(id) ON DELETE SET NULL,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS collection_posts (
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    post_id       INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    added_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    sort_order    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (collection_id, post_id)
);

-- ─── Download Queue ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS download_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT    NOT NULL,
    shortcode       TEXT,
    status          TEXT    NOT NULL DEFAULT 'queued'
                    CHECK(status IN ('queued','analyzing','downloading','processing_ai',
                                     'generating_thumbnails','saving','completed','error',
                                     'paused','cancelled')),
    priority        INTEGER NOT NULL DEFAULT 5,
    attempt_count   INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 5,
    error_message   TEXT,
    error_details   TEXT,   -- Full traceback JSON
    progress_pct    REAL    NOT NULL DEFAULT 0,
    bytes_total     INTEGER,
    bytes_downloaded INTEGER,
    speed_bps       REAL,
    eta_seconds     REAL,
    post_id         INTEGER REFERENCES posts(id) ON DELETE SET NULL,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    started_at      TEXT,
    completed_at    TEXT,
    next_retry_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_status     ON download_tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON download_tasks(created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_shortcode  ON download_tasks(shortcode);

-- ─── Download Log (detailed per-task event log) ───────────────────────────────
CREATE TABLE IF NOT EXISTS download_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL REFERENCES download_tasks(id) ON DELETE CASCADE,
    level       TEXT    NOT NULL CHECK(level IN ('info','warning','error','debug')),
    message     TEXT    NOT NULL,
    details     TEXT,
    logged_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_dllog_task_id  ON download_logs(task_id);
CREATE INDEX IF NOT EXISTS idx_dllog_level    ON download_logs(level);

-- ─── App Event History ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT    NOT NULL,
    entity_type TEXT,   -- 'post', 'task', 'media', etc.
    entity_id   INTEGER,
    message     TEXT    NOT NULL,
    details     TEXT,
    occurred_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_history_occurred_at ON app_history(occurred_at);
CREATE INDEX IF NOT EXISTS idx_history_event_type  ON app_history(event_type);

-- ─── Statistics (pre-aggregated for dashboard) ────────────────────────────────
CREATE TABLE IF NOT EXISTS stats_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    total_posts         INTEGER NOT NULL DEFAULT 0,
    total_authors       INTEGER NOT NULL DEFAULT 0,
    total_images        INTEGER NOT NULL DEFAULT 0,
    total_videos        INTEGER NOT NULL DEFAULT 0,
    total_carousels     INTEGER NOT NULL DEFAULT 0,
    total_reels         INTEGER NOT NULL DEFAULT 0,
    total_ai_tags       INTEGER NOT NULL DEFAULT 0,
    total_ocr_texts     INTEGER NOT NULL DEFAULT 0,
    total_size_bytes    INTEGER NOT NULL DEFAULT 0,
    snapshot_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ─── Full-Text Search Virtual Tables ─────────────────────────────────────────
CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
    shortcode,
    caption,
    hashtags,
    location_name,
    content='posts',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE IF NOT EXISTS ocr_fts USING fts5(
    text,
    content='ocr_results',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE IF NOT EXISTS tags_fts USING fts5(
    name,
    content='tags',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- ─── FTS Triggers (keep FTS in sync with base tables) ─────────────────────────
CREATE TRIGGER IF NOT EXISTS posts_fts_insert AFTER INSERT ON posts BEGIN
    INSERT INTO posts_fts(rowid, shortcode, caption, hashtags, location_name)
    VALUES (new.id, new.shortcode, new.caption, new.hashtags, new.location_name);
END;

CREATE TRIGGER IF NOT EXISTS posts_fts_update AFTER UPDATE ON posts BEGIN
    INSERT INTO posts_fts(posts_fts, rowid, shortcode, caption, hashtags, location_name)
    VALUES ('delete', old.id, old.shortcode, old.caption, old.hashtags, old.location_name);
    INSERT INTO posts_fts(rowid, shortcode, caption, hashtags, location_name)
    VALUES (new.id, new.shortcode, new.caption, new.hashtags, new.location_name);
END;

CREATE TRIGGER IF NOT EXISTS posts_fts_delete AFTER DELETE ON posts BEGIN
    INSERT INTO posts_fts(posts_fts, rowid, shortcode, caption, hashtags, location_name)
    VALUES ('delete', old.id, old.shortcode, old.caption, old.hashtags, old.location_name);
END;

CREATE TRIGGER IF NOT EXISTS ocr_fts_insert AFTER INSERT ON ocr_results BEGIN
    INSERT INTO ocr_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS ocr_fts_update AFTER UPDATE ON ocr_results BEGIN
    INSERT INTO ocr_fts(ocr_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO ocr_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS ocr_fts_delete AFTER DELETE ON ocr_results BEGIN
    INSERT INTO ocr_fts(ocr_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

-- ─── Integrity Check View ─────────────────────────────────────────────────────
CREATE VIEW IF NOT EXISTS v_library_health AS
SELECT
    (SELECT COUNT(*) FROM posts)                                AS total_posts,
    (SELECT COUNT(*) FROM posts WHERE integrity_ok = 0)        AS corrupt_posts,
    (SELECT COUNT(*) FROM media_files)                         AS total_files,
    (SELECT COUNT(*) FROM media_files WHERE sha256_hash IS NULL) AS unverified_files,
    (SELECT COUNT(*) FROM posts WHERE raw_metadata IS NULL)    AS missing_metadata,
    (SELECT SUM(file_size_bytes) FROM media_files)             AS total_size_bytes,
    (SELECT COUNT(*) FROM download_tasks WHERE status = 'error') AS failed_tasks;
