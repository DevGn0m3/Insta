/**
 * Search View Component
 * Advanced search with instant suggestions, filters, and results grid.
 */

class SearchView {
  constructor(container) {
    this._container = container;
    this._debounceTimer = null;
    this._currentQuery = {};
    this._page = 1;
  }

  async init() {
    this._render();
    this._bindEvents();
  }

  _render() {
    this._container.innerHTML = `
      <div class="section-header">
        <div>
          <div class="section-title">Búsqueda avanzada</div>
          <div class="section-subtitle">Busca por texto, etiquetas, autor, fecha, colores y más</div>
        </div>
      </div>

      <div style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:14px;padding:20px;margin-bottom:24px;">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px;">
          <div class="form-row">
            <label class="form-label">Texto libre</label>
            <input type="text" id="srchQ" class="form-input" placeholder="Buscar en captions, hashtags..." />
          </div>
          <div class="form-row">
            <label class="form-label">Autor</label>
            <input type="text" id="srchAuthor" class="form-input" placeholder="@username" />
          </div>
          <div class="form-row">
            <label class="form-label">Tipo de contenido</label>
            <select id="srchType" class="form-select">
              <option value="">Todos</option>
              <option value="image">Imagen</option>
              <option value="video">Video</option>
              <option value="carousel">Carrusel</option>
              <option value="reel">Reel</option>
            </select>
          </div>
          <div class="form-row">
            <label class="form-label">Etiquetas IA (separadas por coma)</label>
            <input type="text" id="srchTags" class="form-input" placeholder="perro, playa, comida..." />
          </div>
          <div class="form-row">
            <label class="form-label">Desde</label>
            <input type="date" id="srchDateFrom" class="form-input" />
          </div>
          <div class="form-row">
            <label class="form-label">Hasta</label>
            <input type="date" id="srchDateTo" class="form-input" />
          </div>
        </div>

        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;">
          <label style="display:flex;align-items:center;gap:6px;font-size:0.84rem;cursor:pointer;color:var(--text-secondary)">
            <input type="checkbox" id="srchFavorites" /> Solo favoritos
          </label>
          <label style="display:flex;align-items:center;gap:6px;font-size:0.84rem;cursor:pointer;color:var(--text-secondary)">
            <input type="checkbox" id="srchHasOcr" /> Con texto OCR
          </label>
          <div class="ml-auto flex gap-8">
            <button class="btn-secondary" id="srchClear">Limpiar</button>
            <button class="btn-primary" id="srchBtn">🔍 Buscar</button>
          </div>
        </div>
      </div>

      <!-- OCR Search -->
      <div style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:14px;padding:16px;margin-bottom:24px;">
        <div class="form-row" style="flex-direction:row;align-items:center;gap:10px;">
          <label class="form-label" style="white-space:nowrap;margin:0">📝 Buscar en texto OCR:</label>
          <input type="text" id="srchOcrQ" class="form-input" placeholder="Texto extraído de imágenes..." style="flex:1" />
          <button class="btn-secondary" id="srchOcrBtn" style="white-space:nowrap">Buscar OCR</button>
        </div>
      </div>

      <div id="srchResults"></div>
      <div id="srchPagination" class="pagination"></div>
    `;
  }

  _bindEvents() {
    document.getElementById('srchBtn')?.addEventListener('click', () => this._doSearch(1));
    document.getElementById('srchClear')?.addEventListener('click', () => this._clearSearch());
    document.getElementById('srchOcrBtn')?.addEventListener('click', () => this._doOcrSearch());

    // Enter key triggers search
    ['srchQ','srchAuthor','srchTags'].forEach(id => {
      document.getElementById(id)?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') this._doSearch(1);
      });
    });

    document.getElementById('srchOcrQ')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') this._doOcrSearch();
    });
  }

  async _doSearch(page = 1) {
    this._page = page;
    const q        = document.getElementById('srchQ')?.value.trim();
    const author   = document.getElementById('srchAuthor')?.value.trim();
    const postType = document.getElementById('srchType')?.value;
    const tagsRaw  = document.getElementById('srchTags')?.value.trim();
    const dateFrom = document.getElementById('srchDateFrom')?.value;
    const dateTo   = document.getElementById('srchDateTo')?.value;
    const favOnly  = document.getElementById('srchFavorites')?.checked;
    const hasOcr   = document.getElementById('srchHasOcr')?.checked;

    const params = {
      page,
      per_page: 50,
      sort_by: 'downloaded_at',
      sort_dir: 'desc',
    };
    if (q)        params.q         = q;
    if (author)   params.author    = author;
    if (postType) params.post_type = postType;
    if (tagsRaw)  params.tags      = tagsRaw.split(',').map(t => t.trim()).join(',');
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo)   params.date_to   = dateTo;
    if (favOnly)  params.is_favorite = true;
    if (hasOcr)   params.has_ocr   = true;

    this._currentQuery = params;
    await this._fetchAndRender(params);
  }

  async _doOcrSearch() {
    const q = document.getElementById('srchOcrQ')?.value.trim();
    if (!q) return;
    const resultsEl = document.getElementById('srchResults');
    resultsEl.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';
    try {
      const results = await SearchAPI.byOcr(q);
      this._renderResults(results, results.length, 1, 1, resultsEl);
    } catch (err) {
      resultsEl.innerHTML = `<div class="empty-state"><div class="empty-title">Error: ${err.message}</div></div>`;
    }
  }

  async _fetchAndRender(params) {
    const resultsEl = document.getElementById('srchResults');
    resultsEl.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';
    try {
      const data = await SearchAPI.search(params);
      this._renderResults(data.posts, data.total, data.page, data.pages, resultsEl);
      this._renderPagination(data.total, data.pages, data.page);
    } catch (err) {
      resultsEl.innerHTML = `<div class="empty-state">
        <div class="empty-icon">⚠️</div>
        <div class="empty-title">Error en la búsqueda</div>
        <div class="empty-desc">${err.message}</div>
      </div>`;
    }
  }

  _renderResults(posts, total, page, pages, container) {
    if (!posts.length) {
      container.innerHTML = `<div class="empty-state">
        <div class="empty-icon">🔍</div>
        <div class="empty-title">Sin resultados</div>
        <div class="empty-desc">Probá con otros términos o filtros</div>
      </div>`;
      return;
    }

    container.innerHTML = `
      <div style="font-size:0.84rem;color:var(--text-muted);margin-bottom:14px">
        ${total.toLocaleString()} resultado${total !== 1 ? 's' : ''}
        ${pages > 1 ? ` · Página ${page} de ${pages}` : ''}
      </div>
      <div class="post-grid" id="srchGrid"></div>
    `;

    const grid = container.querySelector('#srchGrid');
    posts.forEach(post => {
      const card = App.libraryView._buildCard(post);
      grid.appendChild(card);
    });
  }

  _renderPagination(total, pages, currentPage) {
    const pag = document.getElementById('srchPagination');
    if (!pag || pages <= 1) { if (pag) pag.innerHTML = ''; return; }

    let html = `<button class="page-btn" ${currentPage <= 1 ? 'disabled' : ''} data-page="${currentPage - 1}">‹</button>`;
    for (let p = Math.max(1, currentPage - 2); p <= Math.min(pages, currentPage + 2); p++) {
      html += `<button class="page-btn ${p === currentPage ? 'active' : ''}" data-page="${p}">${p}</button>`;
    }
    html += `<button class="page-btn" ${currentPage >= pages ? 'disabled' : ''} data-page="${currentPage + 1}">›</button>`;
    pag.innerHTML = html;

    pag.querySelectorAll('[data-page]').forEach(btn => {
      btn.addEventListener('click', () => this._doSearch(parseInt(btn.dataset.page)));
    });
  }

  _clearSearch() {
    ['srchQ','srchAuthor','srchTags','srchDateFrom','srchDateTo','srchOcrQ'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = '';
    });
    ['srchFavorites','srchHasOcr'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.checked = false;
    });
    document.getElementById('srchType').value = '';
    document.getElementById('srchResults').innerHTML = '';
    document.getElementById('srchPagination').innerHTML = '';
  }
}
