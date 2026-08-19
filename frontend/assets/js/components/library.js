/**
 * Library View Component
 */

// Función para regenerar todos los previews faltantes en masa
window.handleRegenerateAllThumbs = async function(e) {
  const btn = e ? e.currentTarget : document.getElementById('btnRegenAll');
  if (!confirm('¿Deseas escanear y generar todas las miniaturas faltantes de tu biblioteca?')) return;
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '⏳ Procesando...';
    btn.style.opacity = '0.7';
  }
  try {
    const res = await fetch('/api/library/posts/regenerate-all-thumbnails', { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      const msg = `¡Completado! Se generaron ${data.updated || data.updated_thumbnails || 0} miniaturas nuevas.`;
      if (typeof showToast === 'function') showToast(msg, 'success');
      else alert(msg);
      if (window._currentLibraryView) window._currentLibraryView._loadPosts();
    } else {
      alert('Error: ' + (data.detail || 'No se pudo completar'));
    }
  } catch (err) {
    alert('Error al comunicar con el servidor: ' + err.message);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '🪄 Regenerar previews';
      btn.style.opacity = '1';
    }
  }
};

// Funciones globales para acciones rápidas
window.handleRegenerateThumb = async function(e, postId) {
  if (e) { e.stopPropagation(); e.preventDefault(); }
  const btn = e ? e.currentTarget : null;
  if (btn) { btn.style.opacity = '0.5'; btn.disabled = true; }
  try {
    const res = await LibraryAPI.regenerateThumbnails(postId);
    if (typeof showToast === 'function') {
      showToast('Miniatura regenerada con éxito', 'success');
    } else {
      alert('Miniatura regenerada con éxito.');
    }
    if (window._currentLibraryView) {
      window._currentLibraryView._loadPosts();
    }
  } catch (err) {
    const msg = err.detail || err.message || 'No se pudo regenerar. Prueba con Redescargar.';
    if (typeof showToast === 'function') {
      showToast(msg, 'error');
    } else {
      alert(msg);
    }
  } finally {
    if (btn) { btn.style.opacity = '1'; btn.disabled = false; }
  }
};

window.handleRedownloadPost = async function(e, postId) {
  if (e) { e.stopPropagation(); e.preventDefault(); }
  if (!confirm('¿Deseas volver a descargar este post desde Instagram? Se borrarán los datos viejos y se iniciará una nueva descarga limpia.')) return;
  const btn = e ? e.currentTarget : null;
  if (btn) { btn.style.opacity = '0.5'; btn.disabled = true; }
  try {
    await LibraryAPI.redownloadPost(postId);
    if (typeof showToast === 'function') {
      showToast('¡Descarga encolada! Mira el progreso en Descargas.', 'success');
    } else {
      alert('¡Descarga encolada de nuevo!');
    }
    const modal = document.getElementById('modalPost');
    if (modal) modal.style.setProperty('display', 'none', 'important');
    if (window._currentLibraryView) {
      window._currentLibraryView._loadPosts();
    }
  } catch (err) {
    const msg = err.detail || err.message || 'Error al reencolar descarga';
    if (typeof showToast === 'function') {
      showToast(msg, 'error');
    } else {
      alert(msg);
    }
  } finally {
    if (btn) { btn.style.opacity = '1'; btn.disabled = false; }
  }
};

class LibraryView {
  constructor(container) {
    this._container = container;
    this._page = 1; this._perPage = 50; this._totalPages = 1;
    this._filter = 'all'; this._sortBy = 'downloaded_at'; this._sortDir = 'desc';
    this._domain = '';
    this._activeCarousel = null; this._posts = [];
    window._currentLibraryView = this;
  }

  async init() { this._render(); await this._loadDomains(); await this._loadPosts(); this._bindEvents(); }

  _render() {
    this._container.innerHTML = `
      <div class="section-header">
        <div>
          <div class="section-title">Biblioteca</div>
          <div class="section-subtitle" id="libSubtitle">Cargando...</div>
        </div>
        <div class="flex gap-8" style="align-items:center;">
          <!-- Botón de regeneración masiva -->
          <button id="btnRegenAll" onclick="handleRegenerateAllThumbs(event)" class="btn-secondary" title="Generar miniaturas para todos los posts que aún no tengan preview" style="font-size:.82rem; padding:6px 12px; display:flex; align-items:center; gap:6px; background:rgba(99,102,241,0.15); border:1px solid rgba(99,102,241,0.4); color:#a5b4fc; border-radius:8px; cursor:pointer;">
            🪄 Regenerar previews
          </button>
          
          <select id="libDomainFilter" class="form-select" style="width:auto;padding:6px 10px;font-size:.82rem;">
            <option value="">🌐 Todos los dominios</option>
          </select>
          <select id="libSort" class="form-select" style="width:auto;padding:6px 10px;font-size:.82rem;">
            <option value="downloaded_at|desc">Más reciente</option>
            <option value="downloaded_at|asc">Más antiguo</option>
            <option value="posted_at|desc">Fecha post ↓</option>
            <option value="posted_at|asc">Fecha post ↑</option>
            <option value="like_count|desc">Más likes</option>
          </select>
          <div class="flex gap-8" style="border:1px solid var(--border-color);border-radius:8px;overflow:hidden;">
            <button class="btn-icon" id="viewGrid" title="Cuadrícula" style="border-radius:0">⊞</button>
            <button class="btn-icon" id="viewLarge" title="Grande" style="border-radius:0">⊟</button>
          </div>
        </div>
      </div>
      <div class="filter-bar" id="libFilters">
        <span class="filter-chip active" data-filter="all">Todos</span>
        <span class="filter-chip" data-filter="image">🖼️ Imágenes</span>
        <span class="filter-chip" data-filter="video">🎬 Videos</span>
        <span class="filter-chip" data-filter="carousel">🎠 Carruseles</span>
        <span class="filter-chip" data-filter="reel">🎥 Reels</span>
        <span class="filter-chip" data-filter="favorites">❤️ Favoritos</span>
      </div>
      <div class="post-grid" id="libGrid"></div>
      <div id="libPagination" class="pagination"></div>`;
    this._gridEl = this._container.querySelector('#libGrid');
  }

  async _loadDomains() {
    try {
      const domains = await fetch('/api/library/domains').then(r=>r.json());
      const sel = this._container.querySelector('#libDomainFilter');
      if (!sel || !domains?.length) return;
      domains.forEach(d => {
        const opt = document.createElement('option');
        opt.value = d.domain; opt.textContent = `${d.domain} (${d.post_count})`;
        sel.appendChild(opt);
      });
    } catch {}
  }

  async _loadPosts() {
    this._gridEl.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';
    try {
      const params = { page:this._page, per_page:this._perPage, sort_by:this._sortBy, sort_dir:this._sortDir };
      if (this._filter === 'favorites') params.is_favorite = true;
      else if (this._filter !== 'all') params.post_type = this._filter;
      if (this._domain) params.author = this._domain;
      const data = await LibraryAPI.getPosts(params);
      this._posts = data.posts || [];
      this._totalPages = data.pages || 1;
      const sub = this._container.querySelector('#libSubtitle');
      if (sub) sub.textContent = `${(data.total||0).toLocaleString()} publicaciones archivadas`;
      this._renderGrid();
      this._renderPagination(data.total, data.pages);
    } catch(err) {
      this._gridEl.innerHTML = `<div class="empty-state"><div class="empty-icon">⚠️</div><div class="empty-title">Error</div><div class="empty-desc">${err.message}</div></div>`;
    }
  }

  _renderGrid() {
    if (!this._posts.length) {
      this._gridEl.innerHTML = `<div class="empty-state" style="grid-column:1/-1">
        <div class="empty-icon">🖼️</div><div class="empty-title">No hay publicaciones</div>
        <div class="empty-desc">Agregá URLs desde "+ Agregar URL"</div>
        <button class="btn-primary" onclick="document.getElementById('addUrlBtn').click()">+ Agregar URL</button>
      </div>`; return;
    }
    this._gridEl.innerHTML = '';
    this._posts.forEach(post => this._gridEl.appendChild(this._buildCard(post)));
  }

  _buildCard(post) {
    const card = document.createElement('div');
    card.className = 'post-card'; card.dataset.postId = post.id;
    card.style.position = 'relative';
    const thumb = thumbUrl(post.cover_thumbnail);
    card.innerHTML = `
      <!-- Botones flotantes en la Card -->
      <div class="card-quick-actions" style="position:absolute; top:8px; right:8px; display:flex; gap:5px; z-index:40;" onclick="event.stopPropagation();">
        <button type="button" onclick="handleRegenerateThumb(event, ${post.id})" title="Regenerar miniatura (preview)" style="background:rgba(20,20,25,0.85); color:#60a5fa; border:1px solid rgba(255,255,255,0.15); border-radius:6px; padding:4px 7px; cursor:pointer; font-size:12px; backdrop-filter:blur(6px); display:flex; align-items:center; justify-content:center;">
          🔄
        </button>
        <button type="button" onclick="handleRedownloadPost(event, ${post.id})" title="Volver a descargar post completo" style="background:rgba(20,20,25,0.85); color:#fbbf24; border:1px solid rgba(255,255,255,0.15); border-radius:6px; padding:4px 7px; cursor:pointer; font-size:12px; backdrop-filter:blur(6px); display:flex; align-items:center; justify-content:center;">
          📥
        </button>
      </div>

      ${thumb ? `<img class="post-card-thumb" src="${thumb}" alt="thumb" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">` : ''}
      <div class="post-card-thumb-placeholder" style="${thumb?'display:none':''}">
        ${postTypeIcon(post.post_type)}
      </div>
      <div class="post-card-overlay">
        <span class="post-type-badge ${post.post_type}">${post.post_type}</span>
        <button class="post-card-fav ${post.is_favorite?'active':''}" data-post-id="${post.id}">${post.is_favorite?'❤️':'🤍'}</button>
      </div>
      ${post.media_count>1?`<div class="post-media-count">⊞ ${post.media_count}</div>`:''}
      <div class="post-card-info">
        <div class="post-card-author">@${post.author_username||''}</div>
        ${post.caption?`<div class="post-card-caption">${escapeHtml((post.caption||'').substring(0,100))}</div>`:''}
        <div class="post-card-meta"><span>${formatDate(post.posted_at||post.downloaded_at)}</span></div>
      </div>`;
      
    card.addEventListener('click', (e) => { 
      if (e.target.closest('.post-card-fav') || e.target.closest('.card-quick-actions')) return; 
      this._openPostViewer(post.id); 
    });
    
    card.querySelector('.post-card-fav').addEventListener('click', async(e) => {
      e.stopPropagation();
      const newVal = !post.is_favorite; post.is_favorite = newVal;
      const btn = e.currentTarget; btn.textContent = newVal?'❤️':'🤍'; btn.classList.toggle('active',newVal);
      await LibraryAPI.toggleFavorite(post.id, newVal).catch(()=>{});
    });
    return card;
  }

  async _openPostViewer(postId) {
    const modal = document.getElementById('modalPost');
    const content = document.getElementById('postViewerContent');
    content.innerHTML = '<div class="loading-center" style="height:300px"><div class="spinner"></div></div>';
    modal.style.display = '';
    try {
      const post = await LibraryAPI.getPost(postId);
      this._renderPostViewer(post, content);
    } catch(err) {
      content.innerHTML = `<div class="empty-state"><div class="empty-title">Error: ${err.message}</div></div>`;
    }
  }

  _renderPostViewer(post, container) {
    if (this._activeCarousel) { this._activeCarousel.destroy(); this._activeCarousel = null; }
    const tags = post.tags || [];
    const hashtagTags = tags.filter(t=>t.source==='hashtag');
    const aiTags      = tags.filter(t=>t.source==='ai');
    const colorTags   = tags.filter(t=>t.source==='color');

    container.innerHTML = `
      <div class="post-viewer">
        <div class="post-viewer-media" id="pvMedia" style="position:relative"></div>
        <div class="post-viewer-sidebar">
          <div class="post-viewer-header">
            <div class="author-avatar-placeholder" style="width:38px;height:38px;font-size:1rem;">👤</div>
            <div class="author-info">
              <div class="author-name">@${post.username||post.author_username||''}</div>
              <div class="post-date">${formatDateTime(post.posted_at||post.downloaded_at)}</div>
            </div>
            <div class="ml-auto flex gap-8">
              <button class="btn-icon pv-fav ${post.is_favorite?'active':''}">${post.is_favorite?'❤️':'🤍'}</button>
              <a href="${post.original_url}" target="_blank" class="btn-icon" title="Ver original">🔗</a>
            </div>
          </div>
          <div class="post-viewer-body">
            ${post.caption?`<div class="post-viewer-caption">${escapeHtml(post.caption)}</div>`:''}
            ${hashtagTags.length?`<div><div class="text-xs text-muted font-bold" style="margin-bottom:6px">HASHTAGS</div><div class="post-tags-wrap">${hashtagTags.map(t=>`<span class="tag-pill hashtag">#${t.name}</span>`).join('')}</div></div>`:''}
            ${aiTags.length?`<div><div class="text-xs text-muted font-bold" style="margin-bottom:6px">ETIQUETAS IA</div><div class="post-tags-wrap">${aiTags.map(t=>`<span class="tag-pill ai" title="${((t.confidence||0)*100).toFixed(0)}%">${t.name}</span>`).join('')}</div></div>`:''}
            ${colorTags.length?`<div><div class="text-xs text-muted font-bold" style="margin-bottom:6px">COLORES</div><div class="post-tags-wrap">${colorTags.map(t=>`<span class="tag-pill">${t.name}</span>`).join('')}</div></div>`:''}
            <table class="post-meta-table">
              <tr><td>Tipo</td><td>${postTypeLabel(post.post_type)}</td></tr>
              <tr><td>Autor</td><td>@${post.username||post.author_username||'—'}</td></tr>
              <tr><td>Publicado</td><td>${formatDateTime(post.posted_at)}</td></tr>
              <tr><td>Archivado</td><td>${formatDateTime(post.downloaded_at)}</td></tr>
              ${post.location_name?`<tr><td>Ubicación</td><td>📍 ${escapeHtml(post.location_name)}</td></tr>`:''}
              ${post.like_count!=null?`<tr><td>Likes</td><td>❤️ ${post.like_count.toLocaleString()}</td></tr>`:''}
              <tr><td>Archivos</td><td>${(post.media_files||[]).length}</td></tr>
            </table>
          </div>
          <div class="post-viewer-actions" style="display:flex; flex-wrap:wrap; gap:8px;">
            <button class="btn-secondary" onclick="navigator.clipboard.writeText('${post.original_url}')">📋 Copiar URL</button>
            <button class="btn-secondary" id="pvAddTag">🏷️ Etiqueta</button>
            <button class="btn-secondary" onclick="handleRegenerateThumb(event, ${post.id})" title="Regenerar miniatura">🔄 Miniatura</button>
            <button class="btn-secondary" onclick="handleRedownloadPost(event, ${post.id})" style="color:var(--color-warning)" title="Volver a descargar">📥 Redescargar</button>
            <button class="btn-secondary" id="pvDeleteImg" title="Eliminar imagen actual" style="color:var(--color-warning)">🗑️ Imagen</button>
            <button class="btn-secondary" id="pvDeletePost" title="Eliminar toda la card" style="color:var(--color-error)">🗑️ Card</button>
          </div>
        </div>
      </div>`;

    const mediaContainer = container.querySelector('#pvMedia');
    const mediaFiles = (post.media_files||[]).filter(f=>f.file_type!=='thumbnail'&&f.file_type!=='document');
    if (mediaFiles.length > 1) {
      this._activeCarousel = new Carousel(mediaContainer, mediaFiles);
    } else if (mediaFiles.length === 1) {
      const f = mediaFiles[0];
      if (f.file_type==='video') {
        mediaContainer.innerHTML = `<video controls style="max-width:100%;max-height:100%;object-fit:contain;" src="${mediaUrl(f.file_path)}" preload="metadata"></video>`;
      } else {
        mediaContainer.innerHTML = `<img src="${mediaUrl(f.file_path)}" alt="post" style="max-width:100%;max-height:100%;object-fit:contain;border-radius:4px;cursor:zoom-in;" />`;
      }
    } else {
      mediaContainer.innerHTML = `<div class="empty-state"><div class="empty-icon">🖼️</div><div class="empty-title">Sin archivos media</div></div>`;
    }

    mediaContainer.addEventListener('click', (e) => {
      const img = e.target.closest('img');
      if (img && img.src) this._openLightbox(img.src);
    });

    container.querySelector('.pv-fav')?.addEventListener('click', async(e) => {
      const newVal = !post.is_favorite; post.is_favorite = newVal;
      e.currentTarget.textContent = newVal?'❤️':'🤍'; e.currentTarget.classList.toggle('active',newVal);
      await LibraryAPI.toggleFavorite(post.id, newVal).catch(()=>{});
      const gridCard = document.querySelector(`[data-post-id="${post.id}"] .post-card-fav`);
      if (gridCard) { gridCard.textContent = newVal?'❤️':'🤍'; gridCard.classList.toggle('active',newVal); }
    });

    container.querySelector('#pvAddTag')?.addEventListener('click', async() => {
      const name = prompt('Nombre de la etiqueta:');
      if (name?.trim()) { await LibraryAPI.addTag(post.id, name.trim()).catch(()=>{}); if(typeof showToast==='function') showToast('Etiqueta agregada','success'); }
    });

    container.querySelector('#pvDeleteImg')?.addEventListener('click', async () => {
      if (!mediaFiles.length) { if(typeof showToast==='function') showToast('No hay imágenes para eliminar','warning'); return; }
      const idx = this._activeCarousel ? this._activeCarousel._current : 0;
      const target = mediaFiles[idx];
      if (!target?.id) { if(typeof showToast==='function') showToast('No se pudo identificar el archivo','error'); return; }
      if (!confirm('¿Eliminar esta imagen? Esta acción no se puede deshacer.')) return;
      try {
        await fetch(`/api/library/media/${target.id}`, { method:'DELETE' });
        if(typeof showToast==='function') showToast('Imagen eliminada','success');
        const refreshed = await LibraryAPI.getPost(post.id);
        this._renderPostViewer(refreshed, container);
      } catch(err) { if(typeof showToast==='function') showToast('Error: '+err.message,'error'); }
    });

    container.querySelector('#pvDeletePost')?.addEventListener('click', async () => {
      if (!confirm('¿Eliminar esta card completa? Se borrarán todos sus archivos del disco. Esta acción no se puede deshacer.')) return;
      try {
        await fetch(`/api/library/posts/${post.id}`, { method:'DELETE' });
        if(typeof showToast==='function') showToast('Card eliminada','success');
        document.getElementById('modalPost').style.setProperty('display','none','important');
        const card = document.querySelector(`.post-card[data-post-id="${post.id}"]`);
        if (card) card.remove();
        await this._loadPosts();
      } catch(err) { if(typeof showToast==='function') showToast('Error: '+err.message,'error'); }
    });
  }

  _openLightbox(src) {
    let lb = document.getElementById('libLightbox');
    if (!lb) {
      lb = document.createElement('div');
      lb.id = 'libLightbox';
      lb.style.cssText = 'position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.92);display:flex;align-items:center;justify-content:center;cursor:zoom-out;padding:40px;';
      lb.innerHTML = `<img id="libLightboxImg" style="max-width:100%;max-height:100%;object-fit:contain;border-radius:4px;" />
                       <button id="libLightboxClose" style="position:absolute;top:16px;right:16px;background:var(--bg-secondary);border:1px solid var(--border-color);border-radius:50%;width:38px;height:38px;color:var(--text-primary);font-size:1.1rem;cursor:pointer;">✕</button>`;
      document.body.appendChild(lb);
      lb.addEventListener('click', (e) => { if (e.target === lb || e.target.id==='libLightboxClose') lb.style.display='none'; });
      document.addEventListener('keydown', (e) => { if (e.key==='Escape' && lb.style.display!=='none') lb.style.display='none'; });
    }
    lb.querySelector('#libLightboxImg').src = src;
    lb.style.display = 'flex';
  }

  _renderPagination(total, pages) {
    const pag = this._container.querySelector('#libPagination');
    if (!pag || pages<=1) { if(pag) pag.innerHTML=''; return; }
    let html = `<button class="page-btn" ${this._page<=1?'disabled':''} data-page="${this._page-1}">‹</button>`;
    const range = this._pageRange(this._page, pages);
    range.forEach(p => {
      if(p==='...') html+=`<span class="page-btn" style="cursor:default">…</span>`;
      else html+=`<button class="page-btn ${p===this._page?'active':''}" data-page="${p}">${p}</button>`;
    });
    html += `<button class="page-btn" ${this._page>=pages?'disabled':''} data-page="${this._page+1}">›</button>`;
    pag.innerHTML = html;
    pag.querySelectorAll('[data-page]').forEach(btn => {
      btn.addEventListener('click', async() => {
        this._page = parseInt(btn.dataset.page);
        await this._loadPosts();
        this._container.querySelector('#libGrid').scrollIntoView({behavior:'smooth'});
      });
    });
  }

  _pageRange(current, total) {
    if(total<=7) return Array.from({length:total},(_,i)=>i+1);
    if(current<=4) return [1,2,3,4,5,'...',total];
    if(current>=total-3) return [1,'...',total-4,total-3,total-2,total-1,total];
    return [1,'...',current-1,current,current+1,'...',total];
  }

  _bindEvents() {
    this._container.querySelector('#libFilters')?.addEventListener('click', async(e) => {
      const chip = e.target.closest('.filter-chip'); if(!chip) return;
      this._container.querySelectorAll('.filter-chip').forEach(c=>c.classList.remove('active'));
      chip.classList.add('active'); this._filter = chip.dataset.filter; this._page = 1;
      await this._loadPosts();
    });
    this._container.querySelector('#libSort')?.addEventListener('change', async(e) => {
      const [sort_by,sort_dir] = e.target.value.split('|');
      this._sortBy=sort_by; this._sortDir=sort_dir; this._page=1; await this._loadPosts();
    });
    this._container.querySelector('#libDomainFilter')?.addEventListener('change', async(e) => {
      this._domain = e.target.value; this._page = 1; await this._loadPosts();
    });
    this._container.querySelector('#viewGrid')?.addEventListener('click', ()=>this._gridEl.className='post-grid');
    this._container.querySelector('#viewLarge')?.addEventListener('click', ()=>this._gridEl.className='post-grid view-large');
    if (typeof ws !== 'undefined' && ws.on) {
      ws.on('task_completed', ()=>{ if(this._page===1) this._loadPosts(); });
    }
  }

  async refresh() { this._page=1; await this._loadPosts(); }
}

function escapeHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}