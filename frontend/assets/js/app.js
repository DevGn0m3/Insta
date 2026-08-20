// ── Compatibility hook required by start.bat ──────────────────────────────
function syncInstagramLoginFields() {
  const selectedMethod = document.querySelector('input[name="igLoginMethod"]:checked')?.value || 'sessionid';
  const sessionFields = document.getElementById('igSessionFields');
  if (sessionFields) sessionFields.hidden = false;
}

// ── Theme Manager ─────────────────────────────────────────────────────────
const ThemeManager = {
  init() {
    const saved = localStorage.getItem('theme') || 'dark';
    this.setTheme(saved);
  },
  setTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  },
  toggle() {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    this.setTheme(current === 'dark' ? 'light' : 'dark');
  }
};

// ── WebSocket Manager ─────────────────────────────────────────────────────
const ws = {
  _socket: null,
  _listeners: new Map(),
  _reconnectTimer: null,

  connect() {
    if (this._socket && this._socket.readyState === WebSocket.OPEN) return;
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${window.location.host}/ws`;
    
    try {
      this._socket = new WebSocket(url);
      
      this._socket.onopen = () => {
        console.info('[WS] Connected');
        if (this._reconnectTimer) {
          clearTimeout(this._reconnectTimer);
          this._reconnectTimer = null;
        }
      };

      this._socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          const type = data.type || '*';
          const handlers = this._listeners.get(type) || [];
          const wildcard = this._listeners.get('*') || [];
          [...handlers, ...wildcard].forEach(fn => fn(data));
        } catch (e) {
          console.debug('[WS] Parse error:', e);
        }
      };

      this._socket.onclose = () => {
        if (!this._reconnectTimer) {
          this._reconnectTimer = setTimeout(() => this.connect(), 3000);
        }
      };

      this._socket.onerror = () => {
        if (this._socket) this._socket.close();
      };
    } catch (e) {
      console.debug('[WS] Connection failed:', e);
      if (!this._reconnectTimer) {
        this._reconnectTimer = setTimeout(() => this.connect(), 3000);
      }
    }
  },

  on(type, callback) {
    if (!this._listeners.has(type)) {
      this._listeners.set(type, []);
    }
    this._listeners.get(type).push(callback);
  }
};

// ── Helpers Globales de Formato ───────────────────────────────────────────
function humanSize(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function humanSpeed(bps) {
  if (!bps || bps <= 0) return '—';
  return humanSize(bps) + '/s';
}

function humanEta(seconds) {
  if (!seconds || seconds <= 0) return '';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s}s`;
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function mediaUrl(path) {
  if (!path) return '';
  if (path.startsWith('http://') || path.startsWith('https://')) return path;
  return '/' + path.replace(/\\/g, '/').replace(/^\/+/, '');
}

function formatDateTime(val) {
  if (!val) return '—';
  try {
    const d = new Date(val);
    return isNaN(d.getTime()) ? '—' : d.toLocaleString('es-AR');
  } catch {
    return '—';
  }
}

// ── Main App Controller ───────────────────────────────────────────────────
const App = {
  currentView: 'library',
  libraryView: null,
  downloadCenterView: null,
  searchView: null,
  _fileContent: null,

  async init() {
    ThemeManager.init();
    ws.connect();
    
    if (typeof LibraryView === 'function') {
      this.libraryView = new LibraryView(document.getElementById('view-library'));
      await this.libraryView.init();
    }
    if (typeof DownloadCenterView === 'function') {
      this.downloadCenterView = new DownloadCenterView(document.getElementById('view-downloads'));
    }
    if (typeof SearchView === 'function') {
      this.searchView = new SearchView(document.getElementById('view-search'));
    }

    this._bindNavigation();
    this._bindModalClose();
    this._bindAddUrlModal();
    this._bindUrlSubmission();
    this._bindQuickSearch();
    this._bindSidebarToggle();
    this._bindWsBadges();
    this._updateIgStatus();
    setInterval(() => this._updateIgStatus(), 30000);
    this._loadSidebarStats();
    setInterval(() => this._loadSidebarStats(), 20000);
  },

  _bindNavigation() {
    document.querySelectorAll('.nav-item').forEach(item => {
      item.addEventListener('click', (e) => {
        e.preventDefault();
        this.navigate(item.dataset.view);
      });
    });
  },

  async navigate(viewName) {
    if (this.currentView === viewName) return;
    document.querySelectorAll('.nav-item').forEach(i => i.classList.toggle('active', i.dataset.view === viewName));
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.getElementById('view-' + viewName)?.classList.add('active');
    
    const titles = {
      library: 'Biblioteca',
      downloads: 'Centro de Descargas',
      search: 'Buscar',
      authors: 'Autores',
      collections: 'Colecciones',
      favorites: 'Favoritos',
      tags: 'Etiquetas',
      health: 'Salud del Archivo',
      config: 'Configuración'
    };
    const titleEl = document.getElementById('pageTitle');
    if (titleEl) titleEl.textContent = titles[viewName] || viewName;
    
    this.currentView = viewName;
    switch (viewName) {
      case 'downloads':
        if (this.downloadCenterView) {
          if (!this.downloadCenterView._initialized) {
            await this.downloadCenterView.init();
            this.downloadCenterView._initialized = true;
          } else {
            await this.downloadCenterView._loadInitialData();
          }
        }
        break;
      case 'search':
        if (this.searchView && !this.searchView._initialized) {
          await this.searchView.init();
          this.searchView._initialized = true;
        }
        break;
      case 'authors':     await this._renderAuthorsView();     break;
      case 'collections': await this._renderCollectionsView(); break;
      case 'favorites':   await this._renderFavoritesView();   break;
      case 'tags':        await this._renderTagsView();        break;
      case 'health':      await this._renderHealthView();      break;
      case 'config':      await this._renderConfigView();      break;
      case 'library':     if (this.libraryView) await this.libraryView.refresh(); break;
    }
  },

  _bindModalClose() {
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('.modal-close');
      if (btn) {
        e.preventDefault();
        e.stopImmediatePropagation();
        const id = btn.dataset.modal;
        if (id) this._closeModal(id);
        return;
      }
      if (e.target.classList.contains('modal-overlay')) {
        e.stopImmediatePropagation();
        this._closeModal(e.target.id);
      }
    }, true);

    const postOverlay = document.getElementById('modalPost');
    if (postOverlay) {
      postOverlay.addEventListener('click', (e) => {
        if (e.target === postOverlay || e.target.closest('.modal-close, .post-close-btn')) {
          this._closeModal('modalPost');
        }
      }, true);
    }

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        document.querySelectorAll('.modal-overlay').forEach(o => {
          if (o.style.display !== 'none') this._closeModal(o.id);
        });
      }
    });
  },

  _closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (!modal) return;
    modal.style.setProperty('display', 'none', 'important');
    if (modalId === 'modalAddUrl') this._resetAddUrlModal();
    if (modalId === 'modalPost') {
      if (this.libraryView?._activeCarousel) {
        this.libraryView._activeCarousel.destroy();
        this.libraryView._activeCarousel = null;
      }
    }
  },

  closePostModal() { this._closeModal('modalPost'); },

  _bindAddUrlModal() {
    document.getElementById('addUrlBtn')?.addEventListener('click', () => {
      this._resetAddUrlModal();
      const m = document.getElementById('modalAddUrl');
      if (m) m.style.display = '';
    });

    document.querySelectorAll('#modalAddUrl .tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const body = btn.closest('.modal-body');
        if (!body) return;
        body.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        body.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById('tab-' + btn.dataset.tab)?.classList.add('active');
      });
    });

    this._bindDropZone();

    // ── Login limpio con sessionid ──
    document.getElementById('loginBtn')?.addEventListener('click', async () => {
      const sessionid = document.getElementById('igSessionId')?.value.trim() || '';
      const statusEl = document.getElementById('loginStatus');

      if (!sessionid) {
        if (statusEl) statusEl.textContent = '⚠️ Pegá el valor de sessionid.';
        return;
      }

      if (statusEl) statusEl.textContent = 'Guardando sessionid...';
      try {
        const result = await DownloadsAPI.igLogin('', '', 'sessionid', sessionid);
        const uname = result?.username || 'Thegn0m3';
        if (statusEl) statusEl.textContent = `✅ Conectado como @${uname}`;
        await this._updateIgStatus();
        showToast('Sesión de Instagram guardada', 'success');
        setTimeout(() => {
          const m = document.getElementById('modalLogin');
          if (m) m.style.display = 'none';
          if (statusEl) statusEl.textContent = '';
          const inp = document.getElementById('igSessionId');
          if (inp) inp.value = '';
        }, 1200);
      } catch (err) {
        if (statusEl) statusEl.textContent = `❌ ${err.detail || err.message || 'Error guardando sesión'}`;
      }
    });

    document.getElementById('igLogoutBtn')?.addEventListener('click', async () => {
      if (!confirm('¿Cerrar la sesión de Instagram?')) return;
      try {
        await fetch('/api/downloads/instagram/logout', { method: 'POST' });
        showToast('Sesión cerrada', 'info');
        await this._updateIgStatus();
        const m = document.getElementById('modalLogin');
        if (m) m.style.display = 'none';
      } catch (err) {
        showToast('Error: ' + err.message, 'error');
      }
    });
  },

  _resetAddUrlModal() {
    const m = document.getElementById('modalAddUrl');
    if (m) m.style.display = 'none';
    ['singleUrl', 'batchUrls'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    const fi = document.getElementById('fileInput');
    if (fi) fi.value = '';
    const fp = document.getElementById('filePreview');
    if (fp) fp.style.display = 'none';
    this._fileContent = null;
    const dz = document.getElementById('dropZone');
    if (dz) {
      dz.classList.remove('drag-over');
      dz.innerHTML = '<span class="drop-icon">📄</span><p>Arrastrá un archivo .txt aquí</p><p class="drop-hint">o hacé click para seleccionar</p><input type="file" id="fileInput" accept=".txt" style="display:none" />';
      this._bindDropZone();
    }
    const body = document.querySelector('#modalAddUrl .modal-body');
    if (body) {
      body.querySelectorAll('.tab-btn').forEach((b, i) => b.classList.toggle('active', i === 0));
      body.querySelectorAll('.tab-content').forEach((c, i) => c.classList.toggle('active', i === 0));
    }
    const pr = document.getElementById('downloadPriority');
    if (pr) pr.value = '5';
  },

  _bindDropZone() {
    const dz = document.getElementById('dropZone');
    const fi = document.getElementById('fileInput');
    if (!dz || !fi) return;
    dz.onclick = () => fi.click();
    dz.ondragover = (e) => { e.preventDefault(); dz.classList.add('drag-over'); };
    dz.ondragleave = () => dz.classList.remove('drag-over');
    dz.ondrop = (e) => {
      e.preventDefault();
      dz.classList.remove('drag-over');
      if (e.dataTransfer.files.length) this._handleFileSelect(e.dataTransfer.files[0]);
    };
    fi.onchange = (e) => {
      if (e.target.files.length) this._handleFileSelect(e.target.files[0]);
    };
  },

  _handleFileSelect(file) {
    if (!file.name.toLowerCase().endsWith('.txt')) {
      showToast('Solo archivos .txt', 'warning');
      return;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
      this._fileContent = e.target.result;
      const urls = this._fileContent.split('\n').map(l => l.trim()).filter(l => l.startsWith('http'));
      const fp = document.getElementById('filePreview');
      if (fp) fp.style.display = '';
      const info = document.querySelector('#filePreview .file-preview-info');
      if (info) info.textContent = '📄 ' + file.name + ' — ' + urls.length + ' URLs';
      const dz = document.getElementById('dropZone');
      if (dz) dz.innerHTML = '<span class="drop-icon">✅</span><p style="font-weight:600">' + file.name + '</p><p class="drop-hint">' + urls.length + ' URLs listas</p>';
    };
    reader.readAsText(file);
  },

  _bindUrlSubmission() {
    document.getElementById('startDownloadBtn')?.addEventListener('click', async () => {
      const activeTab = document.querySelector('#modalAddUrl .tab-btn.active')?.dataset?.tab || 'single';
      const priority = parseInt(document.getElementById('downloadPriority')?.value) || 5;
      let urls = [];
      if (activeTab === 'single') {
        const u = document.getElementById('singleUrl')?.value.trim();
        if (u) urls = [u];
      } else if (activeTab === 'batch') {
        urls = (document.getElementById('batchUrls')?.value || '').split('\n').map(l => l.trim()).filter(Boolean);
      } else if (activeTab === 'file') {
        if (!this._fileContent) {
          showToast('Seleccioná un archivo .txt', 'warning');
          return;
        }
        urls = this._fileContent.split('\n').map(l => l.trim()).filter(Boolean);
      }
      if (!urls.length) {
        showToast('Ingresá al menos una URL', 'warning');
        return;
      }
      const btn = document.getElementById('startDownloadBtn');
      btn.disabled = true;
      btn.textContent = 'Encolando...';
      try {
        let msg = '';
        if (urls.length === 1) {
          await DownloadsAPI.enqueue(urls[0], priority);
          msg = '1 URL encolada';
        } else {
          const res = await DownloadsAPI.enqueueBatch(urls, priority);
          msg = res.message || res.queued + ' URLs encoladas';
          if (res.rejected > 0) showToast('⚠️ ' + res.rejected + ' URLs ignoradas', 'warning');
        }
        showToast('✅ ' + msg, 'success');
        this._closeModal('modalAddUrl');
        this.navigate('downloads');
      } catch (err) {
        showToast('Error: ' + (err.detail || err.message), 'error');
      } finally {
        btn.disabled = false;
        btn.textContent = '⬇ Iniciar descarga';
      }
    });
  },

  _bindQuickSearch() {
    const input = document.getElementById('quickSearch');
    const box = document.getElementById('searchSuggestions');
    let timer;
    input?.addEventListener('input', () => {
      clearTimeout(timer);
      const q = input.value.trim();
      if (q.length < 2) {
        if (box) box.style.display = 'none';
        return;
      }
      timer = setTimeout(async () => {
        try {
          this._renderSuggestions(await SearchAPI.suggestions(q), box);
        } catch {}
      }, 250);
    });
    input?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        if (box) box.style.display = 'none';
        const q = input.value.trim();
        input.value = '';
        this.navigate('search');
        setTimeout(() => {
          const sq = document.getElementById('srchQ');
          if (sq) sq.value = q;
          document.getElementById('srchBtn')?.click();
        }, 150);
      }
      if (e.key === 'Escape' && box) box.style.display = 'none';
    });
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.quick-search-wrap') && box) box.style.display = 'none';
    });
  },

  _renderSuggestions(results, box) {
    if (!box) return;
    const sections = [];
    if (results.hashtags?.length) sections.push('<div class="suggestion-group-title">Hashtags</div>' + results.hashtags.map(t => `<div class="suggestion-item" data-type="tag" data-value="${t.name}"><span class="sug-icon">#</span>${t.name}<span class="sug-count">${t.cnt}</span></div>`).join(''));
    if (results.tags?.length) sections.push('<div class="suggestion-group-title">Etiquetas IA</div>' + results.tags.map(t => `<div class="suggestion-item" data-type="tag" data-value="${t.name}"><span class="sug-icon">🏷️</span>${t.name}<span class="sug-count">${t.cnt}</span></div>`).join(''));
    if (results.authors?.length) sections.push('<div class="suggestion-group-title">Autores</div>' + results.authors.map(a => `<div class="suggestion-item" data-type="author" data-value="${a.username}"><span class="sug-icon">👤</span>@${a.username}</div>`).join(''));
    box.innerHTML = sections.length ? sections.join('') : '<div class="suggestion-item" style="cursor:default;color:var(--text-muted)">Sin sugerencias</div>';
    box.style.display = '';
    box.querySelectorAll('.suggestion-item[data-type]').forEach(item => {
      item.addEventListener('click', () => {
        box.style.display = 'none';
        const qs = document.getElementById('quickSearch');
        if (qs) qs.value = '';
        this.navigate('search');
        setTimeout(() => {
          if (item.dataset.type === 'tag') {
            const st = document.getElementById('srchTags');
            if (st) st.value = item.dataset.value;
          } else {
            const sa = document.getElementById('srchAuthor');
            if (sa) sa.value = item.dataset.value;
          }
          document.getElementById('srchBtn')?.click();
        }, 150);
      });
    });
  },

  _bindSidebarToggle() {
    document.getElementById('sidebarToggle')?.addEventListener('click', () => {
      document.getElementById('sidebar')?.classList.toggle('collapsed');
    });
  },

  async _loadSidebarStats() {
    try {
      const s = await StatsAPI.library();
      const pe = document.getElementById('stat-posts');
      if (pe) pe.textContent = (s.total_posts || 0).toLocaleString();
      const se = document.getElementById('stat-size');
      if (se) se.textContent = humanSize(s.total_size_bytes || 0);
    } catch {}
  },

  _bindWsBadges() {
    const u = async () => {
      try {
        const s = await StatsAPI.queueSummary();
        const n = (s.downloading || 0) + (s.analyzing || 0) + (s.processing_ai || 0) + (s.queued || 0);
        const b = document.getElementById('badge-active');
        if (!b) return;
        if (n > 0) {
          b.textContent = n;
          b.style.display = '';
        } else {
          b.style.display = 'none';
        }
      } catch {}
    };
    ws.on('*', u);
    u();
  },

  async _updateIgStatus() {
    const wrap = document.getElementById('igStatusWrap');
    const dot = document.getElementById('igStatusDot');
    const label = document.getElementById('igStatusLabel');
    if (!wrap || !dot || !label) return;

    dot.classList.remove('connected', 'warning', 'loading');
    dot.classList.add('loading');
    label.textContent = 'Instagram: verificando...';

    try {
      const status = await DownloadsAPI.igStatus();
      const state = status?.state || (status?.logged_in ? 'active' : 'no_session');
      const username = status?.username ? `@${status.username}` : '';
      dot.classList.remove('loading');

      const activeBox = document.getElementById('igActiveSessionBox');
      const activeText = document.getElementById('igActiveSessionText');

      if (state === 'active' && status?.logged_in) {
        dot.classList.add('connected');
        label.textContent = username ? `Instagram: ${username}` : 'Instagram: activa';
        wrap.title = status.message || 'Instagram: sesión activa';
        if (activeBox) activeBox.style.display = '';
        if (activeText) activeText.textContent = `✅ Sesión activa como ${username || '@Thegn0m3'}`;
      } else {
        label.textContent = 'Instagram: sin sesión';
        wrap.title = 'Instagram: sin sesión configurada';
        if (activeBox) activeBox.style.display = 'none';
      }
    } catch (err) {
      dot.classList.remove('loading');
      dot.classList.add('warning');
      label.textContent = 'Instagram: sin verificar';
    }
  },

  async _renderAuthorsView() {
    const c = document.getElementById('view-authors');
    if (!c) return;
    c.innerHTML = '<div class="section-header"><div class="section-title">Autores</div></div><div class="author-grid" id="authorsGrid"><div class="loading-center"><div class="spinner"></div></div></div>';
    try {
      const authors = await LibraryAPI.getAuthors();
      const grid = document.getElementById('authorsGrid');
      if (!grid) return;
      if (!authors.length) {
        grid.innerHTML = '<div class="empty-state"><div class="empty-icon">👤</div><div class="empty-title">Sin autores aún</div></div>';
        return;
      }
      grid.innerHTML = '';
      authors.forEach(a => {
        const card = document.createElement('div');
        card.className = 'author-card';
        card.innerHTML = (a.profile_pic_path ? `<img class="author-avatar-large" src="${mediaUrl(a.profile_pic_path)}" onerror="this.outerHTML=\'<div class=author-avatar-placeholder>👤</div>\'" />` : '<div class="author-avatar-placeholder">👤</div>') + `<div class="author-username">@${a.username}</div><div class="author-post-count">${a.archived_posts} publicación(es)</div>`;
        card.addEventListener('click', () => {
          this.navigate('search');
          setTimeout(() => {
            const sa = document.getElementById('srchAuthor');
            if (sa) sa.value = a.username;
            document.getElementById('srchBtn')?.click();
          }, 150);
        });
        grid.appendChild(card);
      });
    } catch (err) {
      const grid = document.getElementById('authorsGrid');
      if (grid) grid.innerHTML = `<div class="empty-state"><div class="empty-title">Error: ${err.message}</div></div>`;
    }
  },

  async _renderCollectionsView() {
    const c = document.getElementById('view-collections');
    if (!c) return;
    c.innerHTML = '<div class="section-header"><div class="section-title">Colecciones</div><button class="btn-primary" id="newCollectionBtn">+ Nueva</button></div><div class="collection-grid" id="collectionsGrid"><div class="loading-center"><div class="spinner"></div></div></div>';
    document.getElementById('newCollectionBtn')?.addEventListener('click', async () => {
      const n = prompt('Nombre:');
      if (n?.trim()) {
        await LibraryAPI.createCollection(n.trim(), '').catch(() => {});
        this._renderCollectionsView();
      }
    });
    try {
      const cols = await LibraryAPI.getCollections();
      const grid = document.getElementById('collectionsGrid');
      if (!grid) return;
      if (!cols.length) {
        grid.innerHTML = '<div class="empty-state"><div class="empty-icon">📁</div><div class="empty-title">Sin colecciones</div></div>';
        return;
      }
      grid.innerHTML = '';
      cols.forEach(col => {
        const card = document.createElement('div');
        card.className = 'collection-card';
        card.innerHTML = `<div class="collection-cover">📁</div><div class="collection-info"><div class="collection-name">${escapeHtml(col.name)}</div><div class="collection-count">${col.post_count} publicación(es)</div></div>`;
        grid.appendChild(card);
      });
    } catch (err) {
      const grid = document.getElementById('collectionsGrid');
      if (grid) grid.innerHTML = `<div class="empty-state"><div class="empty-title">Error: ${err.message}</div></div>`;
    }
  },

  async _renderFavoritesView() {
    const c = document.getElementById('view-favorites');
    if (!c) return;
    c.innerHTML = '<div class="section-header"><div class="section-title">Favoritos</div></div><div class="post-grid" id="favGrid"><div class="loading-center"><div class="spinner"></div></div></div>';
    try {
      const data = await LibraryAPI.getFavorites();
      const grid = document.getElementById('favGrid');
      if (!grid) return;
      if (!data.posts.length) {
        grid.innerHTML = '<div class="empty-state"><div class="empty-icon">❤️</div><div class="empty-title">Sin favoritos aún</div></div>';
        return;
      }
      grid.innerHTML = '';
      data.posts.forEach(p => {
        if (this.libraryView?._buildCard) grid.appendChild(this.libraryView._buildCard(p));
      });
    } catch (err) {
      const grid = document.getElementById('favGrid');
      if (grid) grid.innerHTML = `<div class="empty-state"><div class="empty-title">Error: ${err.message}</div></div>`;
    }
  },

  async _renderTagsView() {
    const c = document.getElementById('view-tags');
    if (!c) return;
    c.innerHTML = '<div class="section-header"><div class="section-title">Etiquetas</div></div><div class="filter-bar" id="tagTypeFilter"><span class="filter-chip active" data-type="">Todas</span><span class="filter-chip" data-type="ai">🤖 IA</span><span class="filter-chip" data-type="hashtag">#️⃣ Hashtags</span><span class="filter-chip" data-type="color">🎨 Colores</span><span class="filter-chip" data-type="manual">✋ Manuales</span></div><div class="tags-cloud" id="tagsCloud"><div class="loading-center"><div class="spinner"></div></div></div>';
    const loadTags = async (type) => {
      const cloud = document.getElementById('tagsCloud');
      if (!cloud) return;
      cloud.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';
      try {
        const tags = await LibraryAPI.getTags(type || null, 150);
        if (!tags.length) {
          cloud.innerHTML = '<div class="empty-state"><div class="empty-icon">🏷️</div><div class="empty-title">Sin etiquetas</div></div>';
          return;
        }
        cloud.innerHTML = '';
        tags.forEach(t => {
          const el = document.createElement('div');
          el.className = 'tag-cloud-item';
          el.innerHTML = `${escapeHtml(t.name)} <span class="tag-count">${t.usage_count}</span>`;
          el.addEventListener('click', () => {
            this.navigate('search');
            setTimeout(() => {
              const st = document.getElementById('srchTags');
              if (st) st.value = t.name;
              document.getElementById('srchBtn')?.click();
            }, 150);
          });
          cloud.appendChild(el);
        });
      } catch (err) {
        cloud.innerHTML = `<div class="empty-state"><div class="empty-title">Error: ${err.message}</div></div>`;
      }
    };
    document.getElementById('tagTypeFilter')?.addEventListener('click', (e) => {
      const chip = e.target.closest('.filter-chip');
      if (!chip) return;
      document.querySelectorAll('#tagTypeFilter .filter-chip').forEach(cc => cc.classList.remove('active'));
      chip.classList.add('active');
      loadTags(chip.dataset.type);
    });
    await loadTags('');
  },

  async _renderHealthView() {
    const c = document.getElementById('view-health');
    if (!c) return;
    c.innerHTML = '<div class="section-header"><div><div class="section-title">Salud del Archivo</div></div><div class="flex gap-8"><button class="btn-secondary" id="fixEmptyBtn">🔧 Reparar vacíos</button><button class="btn-secondary" id="regenThumbsBtn">🖼️ Regenerar miniaturas</button><button class="btn-danger" id="resetBtn">🗑️ Reset completo</button><button class="btn-secondary" id="healthRefresh">↻ Verificar</button></div></div><div id="healthContent"><div class="loading-center"><div class="spinner"></div></div></div>';
    
    document.getElementById('fixEmptyBtn')?.addEventListener('click', async () => {
      const btn = document.getElementById('fixEmptyBtn');
      if (btn) { btn.disabled = true; btn.textContent = '🔧 Reparando...'; }
      try {
        const res = await fetch('/api/stats/health/fix-empty-posts', { method: 'POST' });
        const d = await res.json();
        showToast(d.message, 'success', 6000);
        await load();
      } catch (err) {
        showToast('Error: ' + err.message, 'error');
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = '🔧 Reparar vacíos'; }
      }
    });

    document.getElementById('regenThumbsBtn')?.addEventListener('click', async () => {
      const btn = document.getElementById('regenThumbsBtn');
      if (btn) { btn.disabled = true; btn.textContent = '🖼️ Regenerando...'; }
      try {
        const res = await fetch('/api/stats/health/regenerate-thumbnails', { method: 'POST' });
        const d = await res.json();
        showToast(d.message, 'success', 6000);
        await load();
      } catch (err) {
        showToast('Error: ' + err.message, 'error');
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = '🖼️ Regenerar miniaturas'; }
      }
    });

    document.getElementById('resetBtn')?.addEventListener('click', async () => {
      if (!confirm('⚠️ Borrará TODO.\n¿Confirmar?')) return;
      if (!confirm('¿Estás seguro?')) return;
      const btn = document.getElementById('resetBtn');
      if (btn) { btn.disabled = true; btn.textContent = '⏳ Reseteando...'; }
      try {
        await fetch('/api/downloads/reset', { method: 'POST' });
        showToast('✅ Reset completo', 'success');
        setTimeout(() => location.reload(), 1800);
      } catch (err) {
        showToast('Error: ' + err.message, 'error');
        if (btn) { btn.disabled = false; btn.textContent = '🗑️ Reset completo'; }
      }
    });

    const load = async () => {
      const content = document.getElementById('healthContent');
      if (!content) return;
      content.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';
      try {
        const h = await StatsAPI.health();
        content.innerHTML = `<div class="disk-meter"><div class="disk-meter-header"><span>Espacio en disco</span><span>${humanSize(h.disk_free_bytes)} libres</span></div><div class="disk-bar-wrap"><div class="disk-bar ${h.disk_free_bytes < 5e9 ? 'full' : h.disk_free_bytes < 20e9 ? 'warn' : ''}" style="width:${Math.min(99, Math.max(1, 100 - Math.round((h.disk_free_bytes || 0) / 1e11)))}%"></div></div>${h.estimated_days_remaining != null ? `<div class="text-sm text-muted">📈 ${h.estimated_days_remaining} días restantes</div>` : ''}</div><div class="health-grid"><div class="health-card"><div class="health-card-title">📭 Posts vacíos</div><div class="health-card-value ${(h.empty_posts || 0) > 0 ? 'health-status-warn' : 'health-status-ok'}">${h.empty_posts || 0}</div><div class="health-detail">sin archivos</div>${(h.empty_posts || 0) > 0 ? '<button class="btn-secondary text-sm" onclick="document.getElementById(\'fixEmptyBtn\').click()" style="margin-top:8px;width:100%">🔧 Reparar</button>' : ''}</div><div class="health-card"><div class="health-card-title">🔒 Archivos faltantes</div><div class="health-card-value ${h.missing_physical_files > 0 ? 'health-status-error' : 'health-status-ok'}">${h.missing_physical_files > 0 ? h.missing_physical_files : '✓ OK'}</div></div><div class="health-card"><div class="health-card-title">🖼️ Miniaturas</div><div class="health-card-value ${(h.missing_thumbnails || 0) > 0 ? 'health-status-warn' : 'health-status-ok'}">${h.missing_thumbnails || 0}</div>${(h.missing_thumbnails || 0) > 0 ? '<button class="btn-secondary text-sm" onclick="document.getElementById(\'regenThumbsBtn\').click()" style="margin-top:8px;width:100%">🖼️ Regenerar</button>' : ''}</div><div class="health-card"><div class="health-card-title">🔍 FTS5</div><div class="health-card-value ${h.fts_in_sync ? 'health-status-ok' : 'health-status-warn'}">${h.fts_in_sync ? '✓ OK' : '⚠️ Desincronizado'}</div>${!h.fts_in_sync ? '<button class="btn-secondary text-sm" id="reindexBtn" style="margin-top:8px;width:100%">Reindexar</button>' : ''}</div><div class="health-card"><div class="health-card-title">❌ Tareas fallidas</div><div class="health-card-value ${(h.failed_tasks || 0) > 0 ? 'health-status-error' : 'health-status-ok'}">${h.failed_tasks || 0}</div></div><div class="health-card"><div class="health-card-title">💾 Total</div><div class="health-card-value health-status-ok">${humanSize(h.total_size_bytes)}</div><div class="health-detail">${h.total_posts || 0} publicaciones</div></div></div><div class="section-header" style="margin-top:24px;margin-bottom:12px"><div class="section-title" style="font-size:.92rem">Duplicados</div></div><div id="dupesContainer"><div class="loading-center"><div class="spinner"></div></div></div>`;
        document.getElementById('reindexBtn')?.addEventListener('click', async () => {
          showToast('Reindexando...', 'info');
          await StatsAPI.reindexFts();
          showToast('✓ Reindexado', 'success');
          load();
        });
        const dupes = await StatsAPI.duplicates();
        const dc = document.getElementById('dupesContainer');
        if (dc) {
          dc.innerHTML = !dupes.length ? '<div class="text-sm text-muted">✓ No hay duplicados.</div>' : `<div class="text-sm text-muted" style="margin-bottom:10px">${dupes.length} grupo(s)</div>` + dupes.slice(0, 20).map(d => `<div style="padding:8px 12px;background:var(--bg-card);border:1px solid var(--border-color);border-radius:8px;margin-bottom:6px;font-size:.82rem;font-family:monospace">${d.sha256_hash.substring(0, 16)}… · ${d.cnt} copias</div>`).join('');
        }
      } catch (err) {
        content.innerHTML = `<div class="empty-state"><div class="empty-icon">⚠️</div><div class="empty-title">Error: ${err.message}</div></div>`;
      }
    };
    document.getElementById('healthRefresh')?.addEventListener('click', load);
    await load();
  },

  async _renderConfigView() {
    const c = document.getElementById('view-config');
    if (!c) return;
    c.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';
    let s;
    try {
      s = await fetch('/api/settings').then(r => r.json());
    } catch (err) {
      c.innerHTML = `<div class="empty-state"><div class="empty-title">Error: ${err.message}</div></div>`;
      return;
    }

    const field = (key, label, hint, step) => `
      <div class="form-row" style="margin-bottom:14px">
        <label class="form-label">${label}</label>
        <input type="number" step="${step || 1}" class="form-input" id="cfg_${key}" value="${s[key]}" style="max-width:220px" />
        ${hint ? `<div class="form-hint">${hint}</div>` : ''}
      </div>`;

    c.innerHTML = `
      <div class="section-header">
        <div><div class="section-title">Configuración</div>
        <div class="section-subtitle">Parámetros del proceso de descarga y captura</div></div>
        <div class="flex gap-8">
          <button class="btn-secondary" id="cfgResetBtn">↺ Restaurar defaults</button>
          <button class="btn-primary" id="cfgSaveBtn">💾 Guardar</button>
        </div>
      </div>
      <div style="max-width:640px">
        <div class="section-header" style="margin-bottom:8px"><div class="section-title" style="font-size:.92rem">Texto scrapeado</div></div>
        ${field('max_content_chars', 'Máx. caracteres de texto por página', 'Límite de longitud del texto extraído de artículos/páginas.')}
        ${field('max_content_lines', 'Máx. líneas de texto por página')}

        <div class="section-header" style="margin-bottom:8px;margin-top:20px"><div class="section-title" style="font-size:.92rem">Screenshots (Playwright)</div></div>
        ${field('screenshot_wait_seconds', 'Espera antes de capturar (segundos)', 'Tiempo que se espera después de cargar la página, antes de tomar el screenshot. Default: 5s.', '0.5')}
        ${field('navigation_timeout_s', 'Timeout de navegación (segundos)', 'Cuánto esperar a que la página cargue antes de abortar.')}

        <div class="section-header" style="margin-bottom:8px;margin-top:20px"><div class="section-title" style="font-size:.92rem">Red y reintentos</div></div>
        ${field('request_timeout_s', 'Timeout de descarga (segundos)')}
        ${field('max_retries', 'Cantidad máxima de reintentos')}
        ${field('retry_base_delay_s', 'Delay base entre reintentos (segundos)', 'Crece exponencialmente en cada intento, hasta el máximo de abajo.', '0.5')}
        ${field('retry_max_delay_s', 'Delay máximo entre reintentos (segundos)')}

        <div class="section-header" style="margin-bottom:8px;margin-top:20px"><div class="section-title" style="font-size:.92rem">Uso de recursos</div></div>
        ${field('generic_concurrency', 'Descargas simultáneas (sitios no-Instagram)', 'Cuántas páginas se procesan en paralelo. Mismo mecanismo de concurrencia que ya usa la app (asyncio.Semaphore) — subir este número aumenta uso de CPU/red.')}

        <div class="section-header" style="margin-bottom:8px;margin-top:20px"><div class="section-title" style="font-size:.92rem">Instagram — throttle conservador</div></div>
        ${field('min_delay_between_requests_s', 'Intervalo mínimo entre requests de Instagram (segundos)', 'Mínimo permitido: 20 segundos. Se aplica de forma conservadora y no evita las restricciones de Instagram.', '20')}
        ${field('max_delay_between_requests_s', 'Intervalo máximo entre requests de Instagram (segundos)', 'Debe ser igual o mayor al mínimo. El servidor puede imponer un cooldown superior ante HTTP 429.', '30')}
      </div>`;

    document.getElementById('cfgSaveBtn')?.addEventListener('click', async () => {
      const btn = document.getElementById('cfgSaveBtn');
      if (btn) { btn.disabled = true; btn.textContent = '💾 Guardando...'; }
      const payload = {};
      Object.keys(s).forEach(k => {
        const el = document.getElementById('cfg_' + k);
        if (el) payload[k] = parseFloat(el.value);
      });
      try {
        const res = await fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        const d = await res.json();
        if (d.errors?.length) showToast('⚠️ ' + d.errors.join(', '), 'warning', 6000);
        else showToast('✅ Configuración guardada', 'success');
      } catch (err) {
        showToast('Error: ' + err.message, 'error');
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = '💾 Guardar'; }
      }
    });

    document.getElementById('cfgResetBtn')?.addEventListener('click', async () => {
      if (!confirm('¿Restaurar todos los valores por defecto?')) return;
      try {
        await fetch('/api/settings/reset', { method: 'POST' });
        showToast('Restaurado a valores por defecto', 'success');
        await this._renderConfigView();
      } catch (err) {
        showToast('Error: ' + err.message, 'error');
      }
    });
  }
};

function showToast(message, type, duration) {
  type = type || 'info';
  duration = duration || 3500;
  const container = document.getElementById('toastContainer');
  if (!container) return;
  const icons = { success: '✅', error: '❌', warning: '⚠️', info: 'ℹ️' };
  const toast = document.createElement('div');
  toast.className = 'toast ' + type;
  toast.innerHTML = '<span>' + (icons[type] || '') + '</span><span>' + escapeHtml(String(message)) + '</span>';
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('toast-out');
    setTimeout(() => toast.remove(), 260);
  }, duration);
}

document.addEventListener('DOMContentLoaded', () => {
  App.init();
});
