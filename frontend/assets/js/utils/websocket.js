/**
 * WebSocket Client
 * Manages the persistent connection to the backend WebSocket.
 * Auto-reconnects on disconnect with exponential backoff.
 * Dispatches typed events to registered listeners.
 */

class ArchiveWebSocket {
  constructor() {
    this._ws         = null;
    this._listeners  = {};       // { eventType: [fn, ...] }
    this._reconnectDelay = 1500;
    this._reconnectTimer = null;
    this._intentionalClose = false;
    this._pingInterval = null;
  }

  connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const url   = `${proto}://${location.host}/ws`;

    this._intentionalClose = false;
    this._ws = new WebSocket(url);

    this._ws.onopen = () => {
      console.log('[WS] Connected');
      this._reconnectDelay = 1500;
      this._updateStatus('connected');
      this._pingInterval = setInterval(() => {
        if (this._ws && this._ws.readyState === WebSocket.OPEN) {
          this._ws.send('ping');
        }
      }, 25000);
    };

    this._ws.onmessage = (e) => {
      if (e.data === 'pong') return;
      try {
        const event = JSON.parse(e.data);
        this._dispatch(event.type, event);
        this._dispatch('*', event);  // wildcard listener
      } catch (err) {
        console.warn('[WS] Parse error:', err);
      }
    };

    this._ws.onclose = () => {
      clearInterval(this._pingInterval);
      this._updateStatus('disconnected');
      if (!this._intentionalClose) {
        console.log(`[WS] Disconnected. Reconnecting in ${this._reconnectDelay}ms...`);
        this._reconnectTimer = setTimeout(() => {
          this._reconnectDelay = Math.min(this._reconnectDelay * 1.5, 30000);
          this.connect();
        }, this._reconnectDelay);
      }
    };

    this._ws.onerror = () => {
      this._updateStatus('connecting');
    };
  }

  disconnect() {
    this._intentionalClose = true;
    clearTimeout(this._reconnectTimer);
    clearInterval(this._pingInterval);
    if (this._ws) this._ws.close();
  }

  on(eventType, fn) {
    if (!this._listeners[eventType]) this._listeners[eventType] = [];
    this._listeners[eventType].push(fn);
    return () => this.off(eventType, fn);  // returns unsubscribe fn
  }

  off(eventType, fn) {
    if (!this._listeners[eventType]) return;
    this._listeners[eventType] = this._listeners[eventType].filter(f => f !== fn);
  }

  _dispatch(type, event) {
    const fns = this._listeners[type] || [];
    fns.forEach(fn => { try { fn(event); } catch (e) { console.error('[WS] Listener error:', e); } });
  }

  _updateStatus(state) {
    const dot = document.getElementById('wsStatus');
    if (!dot) return;
    dot.className = 'connection-dot ' + state;
    dot.title = { connected: 'Conectado', disconnected: 'Desconectado', connecting: 'Conectando...' }[state] || state;
  }
}

// Singleton
const ws = new ArchiveWebSocket();
