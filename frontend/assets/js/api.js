/**
 * API Client
 * Centralizes all HTTP calls to the FastAPI backend.
 * Handles errors, JSON parsing, and provides typed helpers.
 */

const API_BASE = '';

class ApiError extends Error {
  constructor(status, message, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function apiFetch(path, options = {}) {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options
  });
  if (!res.ok) {
    let detail = '';
    try { const j = await res.json(); detail = j.detail || ''; } catch {}
    throw new ApiError(res.status, `HTTP ${res.status}: ${res.statusText}`, detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

// ── Downloads ──────────────────────────────────────────────────────────────

const DownloadsAPI = {
  enqueue:       (url, priority = 5)    => apiFetch('/api/downloads', { method: 'POST', body: JSON.stringify({ url, priority }) }),
  enqueueBatch:  (urls, priority = 5)   => apiFetch('/api/downloads/batch', { method: 'POST', body: JSON.stringify({ urls, priority }) }),
  getQueue:      ()                     => apiFetch('/api/downloads/queue'),
  getHistory:    (limit = 50)           => apiFetch(`/api/downloads/history?limit=${limit}`),
  getTask:       (id)                   => apiFetch(`/api/downloads/${id}`),
  getTaskLogs:   (id)                   => apiFetch(`/api/downloads/${id}/logs`),
  pauseTask:     (id)                   => apiFetch(`/api/downloads/${id}/pause`,  { method: 'POST' }),
  resumeTask:    (id)                   => apiFetch(`/api/downloads/${id}/resume`, { method: 'POST' }),
  cancelTask:    (id)                   => apiFetch(`/api/downloads/${id}/cancel`, { method: 'POST' }),
  igStatus:      ()                     => apiFetch('/api/downloads/instagram/status'),
  igBrowserProbe:(url)                  => apiFetch('/api/downloads/instagram/browser-probe', { method:'POST', body:JSON.stringify({url}) }),
  igLogin:       (username, password, method = 'password', sessionid = '') => {
    const cleanSessionid = String(sessionid || '').trim();
    const effectiveMethod = cleanSessionid ? 'sessionid' : method;
    return apiFetch('/api/downloads/instagram/login', {
      method: 'POST',
      body: JSON.stringify({
        username,
        password: effectiveMethod === 'password' ? password : '',
        method: effectiveMethod,
        sessionid: cleanSessionid
      })
    });
  },
  getEventHistory: (limit = 100)        => apiFetch(`/api/downloads/history/events?limit=${limit}`)
};

// ── Library ────────────────────────────────────────────────────────────────

const LibraryAPI = {
  getPosts:       (params = {})         => apiFetch('/api/library/posts?' + new URLSearchParams(params)),
  getRecent:      (limit = 20)          => apiFetch(`/api/library/posts/recent?limit=${limit}`),
  getTimeline:    (page = 1, pp = 50)   => apiFetch(`/api/library/posts/timeline?page=${page}&per_page=${pp}`),
  getPost:        (id)                  => apiFetch(`/api/library/posts/${id}`),
  toggleFavorite: (id, val)             => apiFetch(`/api/library/posts/${id}/favorite`, { method: 'PATCH', body: JSON.stringify({ is_favorite: val }) }),
  updateNotes:    (id, notes)           => apiFetch(`/api/library/posts/${id}/notes`, { method: 'PATCH', body: JSON.stringify({ notes }) }),
  deletePost:     (id)                  => apiFetch(`/api/library/posts/${id}`, { method: 'DELETE' }),

  regenerateThumbnails: (postId)        => apiFetch(`/api/library/posts/${postId}/regenerate-thumbnails`, { method: 'POST' }),
  redownloadPost:       (postId)        => apiFetch(`/api/library/posts/${postId}/redownload`, { method: 'POST' }),

  getAuthors:     ()                    => apiFetch('/api/library/authors'),
  getAuthorPosts: (username, page, pp)  => apiFetch(`/api/library/authors/${encodeURIComponent(username)}/posts?page=${page}&per_page=${pp}`),

  getTags:        (type, limit = 100)   => apiFetch(`/api/library/tags?${type ? 'tag_type=' + type + '&' : ''}limit=${limit}`),
  getPostTags:    (id)                  => apiFetch(`/api/library/posts/${id}/tags`),
  addTag:         (id, name)            => apiFetch(`/api/library/posts/${id}/tags`, { method: 'POST', body: JSON.stringify({ name }) }),

  getCollections:     ()                => apiFetch('/api/library/collections'),
  createCollection:   (name, desc)      => apiFetch('/api/library/collections', { method: 'POST', body: JSON.stringify({ name, description: desc }) }),
  addToCollection:    (colId, postId)   => apiFetch(`/api/library/collections/${colId}/posts/${postId}`, { method: 'POST' }),
  removeFromCollection: (colId, postId) => apiFetch(`/api/library/collections/${colId}/posts/${postId}`, { method: 'DELETE' }),
  getCollectionPosts: (colId, page, pp) => apiFetch(`/api/library/collections/${colId}/posts?page=${page}&per_page=${pp}`),

  getFavorites:   (page = 1, pp = 50)   => apiFetch(`/api/library/favorites?page=${page}&per_page=${pp}`)
};

// ── Search ─────────────────────────────────────────────────────────────────

const SearchAPI = {
  search:       (params = {})  => apiFetch('/api/search?' + new URLSearchParams(
    Object.fromEntries(Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== ''))
  )),
  suggestions:  (q)            => apiFetch(`/api/search/suggestions?q=${encodeURIComponent(q)}`),
  byColor:      (hex)          => apiFetch(`/api/search/by-color?hex_color=${encodeURIComponent(hex)}`),
  byOcr:        (q)            => apiFetch(`/api/search/by-ocr?q=${encodeURIComponent(q)}`)
};

// ── Stats ──────────────────────────────────────────────────────────────────

const StatsAPI = {
  library:       ()   => apiFetch('/api/stats/library'),
  system:        ()   => apiFetch('/api/stats/system'),
  health:        ()   => apiFetch('/api/stats/health'),
  duplicates:    ()   => apiFetch('/api/stats/duplicates'),
  reindexFts:    ()   => apiFetch('/api/stats/health/reindex-fts', { method: 'POST' }),
  queueSummary:  ()   => apiFetch('/api/stats/queue/summary')
};

// ── Utilities ──────────────────────────────────────────────────────────────

function humanSize(bytes) {
  if (!bytes) return '0 B';
  const units = ['B','KB','MB','GB','TB'];
  let i = 0;
  while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
  return `${bytes.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function humanSpeed(bps) {
  if (!bps) return '—';
  return humanSize(bps) + '/s';
}

function humanEta(seconds) {
  if (!seconds) return '—';
  const s = Math.floor(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60), ss = s % 60;
  if (m < 60) return `${m}m ${ss}s`;
  const h = Math.floor(m / 60), mm = m % 60;
  return `${h}h ${mm}m`;
}

);
}

);
}

function postTypeLabel(type) {
  const map = { image: '🖼️ Imagen', video: '🎬 Video', carousel: '🎠 Carrusel', reel: '🎥 Reel', unknown: '❓ Desconocido' };
  return map[type] || type;
}

function postTypeIcon(type) {
  const map = { image: '🖼️', video: '🎬', carousel: '🎠', reel: '🎥', unknown: '❓' };
  return map[type] || '📄';
}

function thumbUrl(path) {
  if (!path) return null;
  if (path.startsWith('http')) return path;

  let normalized = path.replace(/\\/g, '/');
  normalized = normalized.replace(/^[A-Z]:\//i, '/');

  if (normalized.startsWith('/thumbnails/')) return normalized;
  if (normalized.startsWith('thumbnails/')) return '/' + normalized;

  const thumbIdx = normalized.indexOf('/thumbnails/');
  if (thumbIdx !== -1) return normalized.substring(thumbIdx);

  const parts = normalized.split('/');
  const name = parts[parts.length - 1];
  if (name && name.length > 4) return `/thumbnails/${name}`;

  return null;
}

function mediaUrl(path) {
  if (!path) return null;
  if (path.startsWith('http')) return path;

  let normalized = path.replace(/\\/g, '/');
  normalized = normalized.replace(/^[A-Z]:\//i, '/');

  if (normalized.startsWith('/media/')) return normalized;
  if (normalized.startsWith('media/')) return '/' + normalized;

  const mediaIdx = normalized.indexOf('/media/');
  if (mediaIdx !== -1) return normalized.substring(mediaIdx);

  const parts = normalized.split('/');
  const name = parts[parts.length - 1];
  if (name && name.length > 4) return `/media/${name}`;

  return null;
}

function parseSafeDate(val) {
  if (!val) return null;
  // Si es un timestamp numérico (ej: 1718723456)
  if (typeof val === 'number') {
    // Si viene en segundos (10 dígitos), convertir a milisegundos
    return new Date(val < 1e11 ? val * 1000 : val);
  }
  if (typeof val === 'string') {
    const trimmed = val.trim();
    if (!trimmed || trimmed === 'null' || trimmed === 'None') return null;
    // Si es un número en string
    if (/^\d+$/.test(trimmed)) {
      const num = parseInt(trimmed, 10);
      return new Date(num < 1e11 ? num * 1000 : num);
    }
    // Parseo estándar ISO / SQL
    const d = new Date(trimmed.replace(' ', 'T'));
    if (!isNaN(d.getTime())) return d;
  }
  const d = new Date(val);
  return !isNaN(d.getTime()) ? d : null;
}

function formatDate(iso) {
  const d = parseSafeDate(iso);
  if (!d) return '—';
  try {
    return d.toLocaleDateString('es-AR', {
      year: 'numeric', month: 'short', day: 'numeric'
    });
  } catch {
    return '—';
  }
}

function formatDateTime(iso) {
  const d = parseSafeDate(iso);
  if (!d) return '—';
  try {
    return d.toLocaleString('es-AR', {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit'
    });
  } catch {
    return '—';
  }
}
