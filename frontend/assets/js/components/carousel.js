/**
 * Carousel Component
 * Instagram-style image/video carousel for multi-media posts.
 * Supports touch/swipe, keyboard navigation, and lazy loading.
 */

class Carousel {
  constructor(container, items) {
    this._container = container;
    this._items     = items;       // [{file_path, file_type, thumbnail_path, ...}]
    this._current   = 0;
    this._total     = items.length;
    this._touchStartX = 0;
    this._render();
    this._bindEvents();
  }

  _render() {
    this._container.innerHTML = `
      <div class="carousel-wrap">
        <div class="carousel-track" id="cTrack"></div>
        ${this._total > 1 ? `
          <button class="carousel-btn prev" id="cPrev" ${this._current === 0 ? 'disabled' : ''}>&#8249;</button>
          <button class="carousel-btn next" id="cNext" ${this._current === this._total - 1 ? 'disabled' : ''}>&#8250;</button>
          <div class="carousel-dots" id="cDots"></div>
          <div class="carousel-counter" id="cCounter">1 / ${this._total}</div>
        ` : ''}
      </div>
    `;

    const track = this._container.querySelector('#cTrack');
    this._items.forEach((item, idx) => {
      const slide = document.createElement('div');
      slide.className = 'carousel-slide';
      slide.dataset.index = idx;

      if (item.file_type === 'video') {
        const video = document.createElement('video');
        video.controls = true;
        video.preload  = 'metadata';
        video.loop     = false;
        video.style.maxWidth = '100%';
        video.style.maxHeight = '100%';
        // Lazy-load: only set src when slide is active
        video.dataset.src = mediaUrl(item.file_path);
        if (idx === 0) video.src = video.dataset.src;
        slide.appendChild(video);
      } else {
        const img = document.createElement('img');
        img.alt   = `Imagen ${idx + 1}`;
        img.style.cssText = 'max-width:100%;max-height:100%;object-fit:contain;border-radius:4px;';
        // Lazy load images except first
        if (idx === 0) {
          img.src = mediaUrl(item.file_path);
        } else {
          img.dataset.src = mediaUrl(item.file_path);
          img.src = thumbUrl(item.thumbnail_path) || item.thumbnail_path;
        }
        slide.appendChild(img);
      }

      track.appendChild(slide);
    });

    // Dots
    if (this._total > 1) {
      const dots = this._container.querySelector('#cDots');
      this._items.forEach((_, idx) => {
        const dot = document.createElement('div');
        dot.className = `carousel-dot${idx === 0 ? ' active' : ''}`;
        dot.dataset.index = idx;
        dots.appendChild(dot);
      });
    }
  }

  _bindEvents() {
    const prev    = this._container.querySelector('#cPrev');
    const next    = this._container.querySelector('#cNext');
    const track   = this._container.querySelector('#cTrack');

    prev?.addEventListener('click', (e) => { e.stopPropagation(); this.prev(); });
    next?.addEventListener('click', (e) => { e.stopPropagation(); this.next(); });

    // Keyboard
    const keyHandler = (e) => {
      if (e.key === 'ArrowLeft')  this.prev();
      if (e.key === 'ArrowRight') this.next();
    };
    document.addEventListener('keydown', keyHandler);
    this._keyHandler = keyHandler;

    // Touch/swipe
    track?.addEventListener('touchstart', (e) => {
      this._touchStartX = e.changedTouches[0].screenX;
    }, { passive: true });

    track?.addEventListener('touchend', (e) => {
      const dx = e.changedTouches[0].screenX - this._touchStartX;
      if (Math.abs(dx) > 40) {
        if (dx < 0) this.next();
        else        this.prev();
      }
    }, { passive: true });

    // Dot clicks
    this._container.querySelector('#cDots')?.addEventListener('click', (e) => {
      const dot = e.target.closest('.carousel-dot');
      if (dot) this.goTo(parseInt(dot.dataset.index));
    });
  }

  goTo(index) {
    if (index < 0 || index >= this._total) return;
    this._current = index;
    const track = this._container.querySelector('#cTrack');
    track.style.transform = `translateX(-${index * 100}%)`;

    // Lazy load the adjacent slides
    [index - 1, index, index + 1].forEach(i => {
      if (i < 0 || i >= this._total) return;
      const slide = track.children[i];
      const lazyEl = slide?.querySelector('[data-src]');
      if (lazyEl && lazyEl.dataset.src) {
        lazyEl.src = lazyEl.dataset.src;
        delete lazyEl.dataset.src;
      }
    });

    // Update dots
    this._container.querySelectorAll('.carousel-dot').forEach((d, i) => {
      d.classList.toggle('active', i === index);
    });

    // Update counter
    const counter = this._container.querySelector('#cCounter');
    if (counter) counter.textContent = `${index + 1} / ${this._total}`;

    // Update buttons
    const prev = this._container.querySelector('#cPrev');
    const next = this._container.querySelector('#cNext');
    if (prev) prev.disabled = index === 0;
    if (next) next.disabled = index === this._total - 1;

    // Pause other videos
    this._container.querySelectorAll('video').forEach((v, i) => {
      if (i !== index) v.pause();
    });
  }

  next() { this.goTo(this._current + 1); }
  prev() { this.goTo(this._current - 1); }

  destroy() {
    document.removeEventListener('keydown', this._keyHandler);
  }
}
