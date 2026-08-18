/**
 * Theme Manager
 * Persists and toggles dark/light theme using localStorage.
 */

const ThemeManager = {
  init() {
    const saved = localStorage.getItem('archiver-theme') || 'dark';
    this.apply(saved);
    document.getElementById('themeToggle')?.addEventListener('click', () => this.toggle());
  },
  apply(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    const btn = document.getElementById('themeToggle');
    if (btn) btn.textContent = theme === 'dark' ? '🌙' : '☀️';
    localStorage.setItem('archiver-theme', theme);
  },
  toggle() {
    const current = document.documentElement.getAttribute('data-theme');
    this.apply(current === 'dark' ? 'light' : 'dark');
  },
};
