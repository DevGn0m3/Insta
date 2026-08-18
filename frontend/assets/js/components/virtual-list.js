/**
 * Virtual List
 * Renders only visible DOM nodes for large datasets.
 * Uses IntersectionObserver + sentinel pattern for infinite scroll.
 */

class VirtualGrid {
  /**
   * @param {HTMLElement} container
   * @param {object} options
   *   - itemHeight {number}  approximate row height (px)
   *   - columns   {number}   grid columns
   *   - renderFn  {function} (item) => HTMLElement
   *   - onLoadMore {function} async () => void
   */
  constructor(container, options = {}) {
    this._container  = container;
    this._renderFn   = options.renderFn   || (() => document.createElement('div'));
    this._onLoadMore = options.onLoadMore || null;
    this._items      = [];
    this._renderedIds = new Set();
    this._sentinel   = null;
    this._observer   = null;
    this._loading    = false;
    this._hasMore    = true;
  }

  setItems(items, hasMore = false) {
    this._items   = items;
    this._hasMore = hasMore;
    this._render();
  }

  appendItems(items, hasMore = false) {
    this._items   = [...this._items, ...items];
    this._hasMore = hasMore;
    this._renderNew(items);
    this._attachSentinel();
  }

  clear() {
    this._items = [];
    this._renderedIds.clear();
    this._container.innerHTML = '';
    this._sentinel = null;
    if (this._observer) { this._observer.disconnect(); this._observer = null; }
  }

  _render() {
    this._container.innerHTML = '';
    this._renderedIds.clear();
    this._items.forEach(item => {
      const el = this._renderFn(item);
      if (el) this._container.appendChild(el);
      this._renderedIds.add(item.id);
    });
    this._attachSentinel();
  }

  _renderNew(items) {
    // Remove sentinel before appending
    if (this._sentinel) {
      this._sentinel.remove();
      this._sentinel = null;
    }
    items.forEach(item => {
      if (this._renderedIds.has(item.id)) return;
      const el = this._renderFn(item);
      if (el) this._container.appendChild(el);
      this._renderedIds.add(item.id);
    });
  }

  _attachSentinel() {
    if (!this._hasMore || !this._onLoadMore) return;
    if (this._sentinel) this._sentinel.remove();

    this._sentinel = document.createElement('div');
    this._sentinel.className = 'vlist-sentinel';
    this._sentinel.style.cssText = 'height:40px;width:100%;grid-column:1/-1;display:flex;align-items:center;justify-content:center;';
    this._sentinel.innerHTML = '<div class="spinner"></div>';
    this._container.appendChild(this._sentinel);

    if (this._observer) this._observer.disconnect();
    this._observer = new IntersectionObserver(async (entries) => {
      if (entries[0].isIntersecting && !this._loading) {
        this._loading = true;
        await this._onLoadMore();
        this._loading = false;
      }
    }, { rootMargin: '200px' });

    this._observer.observe(this._sentinel);
  }

  updateItem(id, newData) {
    const idx = this._items.findIndex(i => i.id === id);
    if (idx === -1) return;
    this._items[idx] = { ...this._items[idx], ...newData };
    // Re-render only the affected card
    const existing = this._container.querySelector(`[data-post-id="${id}"]`);
    if (existing) {
      const el = this._renderFn(this._items[idx]);
      if (el) existing.replaceWith(el);
    }
  }
}
