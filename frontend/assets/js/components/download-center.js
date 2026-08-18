class DownloadCenterView {
  constructor(container) { this._container=container; this._tasks=new Map(); this._historyTasks=[]; }

  async init() { this._render(); await this._loadInitialData(); this._bindControls(); this._bindWsEvents(); this._startStatsPoll(); }

  _render() {
    this._container.innerHTML = `
      <div class="section-header">
        <div><div class="section-title">Centro de Descargas</div></div>
        <div class="flex gap-8" style="flex-wrap:wrap">
          <button class="btn-secondary" id="dlRefresh">↻ Actualizar</button>
          <button class="btn-secondary" id="dlResumeQueue" style="display:none">▶️ Reanudar cola</button>
          <button class="btn-secondary" id="dlPauseAll">⏸ Pausar todo</button>
          <button class="btn-secondary" id="dlCancelAll">⏹ Cancelar todo</button>
          <button class="btn-secondary" id="dlClearHistory">🧹 Limpiar historial</button>
          <button class="btn-secondary" id="dlLoginBtn">🔑 Instagram</button>
          <button class="btn-secondary" id="dlIgBrowserProbe" title="Abrir una comprobación visible con navegador autorizado">🌐 Probar en navegador</button>
          <button class="btn-secondary" id="dlStatsBtn">📊 Estadísticas</button>
          <button class="btn-secondary" id="dlRetryErrors" title="Reintentar todas las descargas con error">🔁 Reintentar errores</button>
        </div>
      </div>
      <div id="dlStatsPanel" style="display:none;background:var(--bg-card);border:1px solid var(--border-color);border-radius:12px;padding:16px;margin-bottom:16px;max-height:400px;overflow-y:auto"></div>
      <div id="dlInterruptedBanner" style="display:none;background:rgba(234,179,8,.12);border:1px solid var(--color-warning);border-radius:10px;padding:12px 16px;margin-bottom:16px;font-size:.88rem;color:var(--color-warning)"></div>
      <div class="dl-center-header">
        <div class="dl-stat-card"><div class="dl-stat-label">⬇️ Activas</div><div class="dl-stat-value accent" id="dlStatActive">0</div></div>
        <div class="dl-stat-card"><div class="dl-stat-label">⏳ En cola</div><div class="dl-stat-value" id="dlStatQueued">0</div></div>
        <div class="dl-stat-card"><div class="dl-stat-label">✅ Completadas</div><div class="dl-stat-value success" id="dlStatDone">0</div></div>
        <div class="dl-stat-card"><div class="dl-stat-label">❌ Errores</div><div class="dl-stat-value error" id="dlStatError">0</div></div>
        <div class="dl-stat-card"><div class="dl-stat-label">⏸️ Pausadas</div><div class="dl-stat-value warning" id="dlStatPaused">0</div></div>
        <div class="dl-stat-card"><div class="dl-stat-label">⚡ Velocidad</div><div class="dl-stat-value" id="dlStatSpeed">—</div></div>
      </div>
      <div class="stats-grid" id="dlLibStats" style="margin-bottom:24px"></div>
      <div class="section-header" style="margin-bottom:12px;align-items:center;gap:12px;flex-wrap:wrap">
        <div class="section-title" style="font-size:.92rem">Tareas activas</div>
        <div class="dl-status-filter-wrap">
          <label for="dlActiveStatusFilter" class="dl-status-filter-label">Filtrar por estado</label>
          <select id="dlActiveStatusFilter" class="dl-status-filter" aria-label="Filtrar tareas activas e historial por estado">
            <option value="all">Activas</option>
            <option value="queued">En cola</option>
            <option value="analyzing">Analizando</option>
            <option value="downloading">Descargando</option>
            <option value="processing_ai">Procesando con IA</option>
            <option value="generating_thumbnails">Generando miniaturas</option>
            <option value="saving">Guardando</option>
            <option value="paused">Pausadas</option>
            <option value="completed">Completadas</option>
            <option value="error">Con error</option>
            <option value="cancelled">Canceladas</option>
          </select>
          <span id="dlActiveFilterCount" class="dl-status-filter-count"></span>
        </div>
      </div>
      <div class="dl-tasks-list" id="dlActiveTasks"></div>
      <div class="section-header" style="margin-top:28px;margin-bottom:12px"><div class="section-title" style="font-size:.92rem">Historial reciente</div></div>
      <div class="dl-tasks-list" id="dlHistoryTasks"></div>
      <div class="section-header" style="margin-top:28px;margin-bottom:12px">
        <div class="section-title" style="font-size:.92rem">Registro de eventos</div>
        <button class="btn-secondary text-sm" id="dlClearLog" style="padding:4px 10px">Limpiar</button>
      </div>
      <div id="dlEventLog" style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:12px;padding:12px 16px;font-size:.78rem;font-family:monospace;max-height:300px;overflow-y:auto;color:var(--text-secondary);line-height:1.8;"></div>`;
  }

  async _loadInitialData() {
    try {
      const [q,h,s] = await Promise.all([DownloadsAPI.getQueue(), DownloadsAPI.getHistory(200), StatsAPI.library()]);
      this._tasks.clear();
      (q.tasks||[]).forEach(t=>this._tasks.set(t.id,t));
      this._historyTasks = Array.isArray(h) ? h : [];
      this._updateSummary(q.summary||{});
      this._renderActiveTasks();
      this._renderHistoryTasks((h||[]).slice(0,20));
      this._renderLibStats(s);
    } catch(err) { this._appendLog('Error cargando: '+err.message,'error'); }
  }

  _updateSummary(s) {
    const set=(id,v)=>{const e=document.getElementById(id);if(e)e.textContent=v;};
    set('dlStatActive',(s.downloading||0)+(s.analyzing||0)+(s.processing_ai||0));
    set('dlStatQueued',s.queued||0); set('dlStatDone',s.completed||0);
    set('dlStatError',s.error||0);   set('dlStatPaused',s.paused||0);
    const rb=document.getElementById('dlResumeQueue');
    if(rb) rb.style.display=((s.paused||0)+(s.queued||0))>0?'':'none';
  }

  _renderActiveTasks() {
    const el=document.getElementById('dlActiveTasks'); if(!el) return;
    const terminal=new Set(['completed','cancelled','error']);
    const active=[...this._tasks.values()].filter(t=>!terminal.has(t.status));
    // El selector también puede mostrar estados históricos. El mapa evita
    // duplicar una tarea que todavía aparece en /queue y /history durante una
    // actualización de estado.
    const allById=new Map();
    (this._historyTasks||[]).forEach(t=>allById.set(t.id,t));
    [...this._tasks.values()].forEach(t=>allById.set(t.id,t));
    const filter=document.getElementById('dlActiveStatusFilter')?.value || 'all';
    const visible=filter==='all' ? active : [...allById.values()].filter(t=>t.status===filter);
    const count=document.getElementById('dlActiveFilterCount');
    if(count) count.textContent=filter==='all' ? `${active.length} activas` : `${visible.length} encontradas`;
    if(!visible.length){
      const selected=document.getElementById('dlActiveStatusFilter')?.selectedOptions?.[0]?.textContent || 'ese estado';
      const title=filter==='all' ? 'Sin tareas activas' : `Sin tareas ${escapeHtml(selected.toLowerCase())}`;
      el.innerHTML=`<div class="empty-state" style="padding:32px"><div class="empty-icon">${filter==='all'?'😴':'🔎'}</div><div class="empty-title">${title}</div><div class="empty-desc">${filter==='all'?'Agregá URLs con "+ Agregar URL"':'Probá otro estado o actualizá la lista.'}</div></div>`;
      return;
    }
    el.innerHTML='';
    const order=['downloading','analyzing','processing_ai','generating_thumbnails','saving','queued','paused','error','completed','cancelled'];
    [...visible].sort((a,b)=>(order.indexOf(a.status)<0?99:order.indexOf(a.status))-(order.indexOf(b.status)<0?99:order.indexOf(b.status))).forEach(t=>el.appendChild(this._buildCard(t)));
  }

  _renderHistoryTasks(tasks) {
    const el=document.getElementById('dlHistoryTasks'); if(!el) return;
    if(!tasks.length){el.innerHTML='<div class="text-muted text-sm" style="padding:12px">Sin historial.</div>';return;}
    el.innerHTML=''; tasks.slice(0,20).forEach(t=>el.appendChild(this._buildCard(t)));
  }

  _buildCard(task) {
    const labels={queued:'En cola',analyzing:'Analizando...',downloading:'Descargando...',
      processing_ai:'IA...',generating_thumbnails:'Miniaturas...',saving:'Guardando...',
      completed:'Completado',error:'Error',paused:'Pausado',cancelled:'Cancelado'};
    const pct=Math.round(task.progress_pct||0);
    const shortUrl=(task.url||'').replace(/https?:\/\//,'').substring(0,55);
    const card=document.createElement('div');
    card.className='dl-task-card status-'+task.status; card.id='task-card-'+task.id;
    card.innerHTML=`
      <div class="dl-task-header">
        <div class="dl-task-url" title="${task.url||''}">🔗 ${shortUrl}${(task.url||'').length>55?'…':''}</div>
        <span class="dl-task-status-badge status-badge-${task.status}">${labels[task.status]||task.status}</span>
      </div>
      ${task.error_message?`<div style="font-size:.8rem;color:var(--color-error);margin-bottom:8px;padding:6px 10px;background:rgba(239,68,68,.1);border-radius:6px;">⚠️ ${escapeHtml(task.error_message)}</div>`:''}
      <div class="progress-bar-wrap" style="margin-bottom:6px">
        <div class="progress-bar ${task.status==='error'?'error':task.status==='completed'?'success':''} ${['downloading','processing_ai','generating_thumbnails'].includes(task.status)?'animated':''}" style="width:${pct}%"></div>
      </div>
      <div style="display:flex;justify-content:space-between;font-size:.76rem;color:var(--text-muted);margin-bottom:8px">
        <span id="pct-${task.id}">${pct}%</span>
        <span>${task.speed_bps?humanSpeed(task.speed_bps):''}</span>
        <span>${task.eta_seconds?'⏱ '+humanEta(task.eta_seconds):''}</span>
      </div>
      <div class="dl-task-meta">
        ${task.attempt_count>1?`<span>🔄 Intento ${task.attempt_count}/${task.max_attempts}</span>`:''}
        ${task.bytes_downloaded?`<span>📦 ${humanSize(task.bytes_downloaded)}${task.bytes_total?' / '+humanSize(task.bytes_total):''}</span>`:''}
        <span>🕐 ${formatDateTime(task.started_at||task.created_at)}</span>
      </div>
      <div class="dl-task-actions">
        ${['queued','downloading'].includes(task.status)?`<button class="btn-icon" data-action="pause" data-id="${task.id}" title="Pausar">⏸</button>`:''}
        ${task.status==='paused'?`<button class="btn-icon" data-action="resume" data-id="${task.id}" title="Reanudar">▶️</button>`:''}
        ${!['completed','cancelled'].includes(task.status)?`<button class="btn-icon" data-action="cancel" data-id="${task.id}" title="Cancelar" style="color:var(--color-error)">✕</button>`:''}
        <button class="btn-icon" data-action="logs" data-id="${task.id}" title="Ver logs">📋</button>
        ${task.post_id?`<button class="btn-icon" data-action="view" data-post-id="${task.post_id}" title="Ver en biblioteca">👁</button>`:''}
      </div>`;
    card.addEventListener('click',async(e)=>{
      const btn=e.target.closest('[data-action]'); if(!btn) return;
      const a=btn.dataset.action, id=parseInt(btn.dataset.id);
      if(a==='pause')  {await DownloadsAPI.pauseTask(id);  showToast('Pausada','info');   await this._loadInitialData();}
      if(a==='resume') {await DownloadsAPI.resumeTask(id); showToast('Reanudada','info'); await this._loadInitialData();}
      if(a==='cancel') {if(confirm('¿Cancelar esta tarea?')){await DownloadsAPI.cancelTask(id); await this._loadInitialData();}}
      if(a==='logs')   {await this._showLogs(id);}
      if(a==='view')   {App.navigate('library'); App.libraryView._openPostViewer(parseInt(btn.dataset.postId));}
    });
    return card;
  }

  async _showLogs(taskId) {
    const logs=await DownloadsAPI.getTaskLogs(taskId).catch(()=>[]);
    const el=document.getElementById('dlEventLog'); if(!el) return;
    const colors={info:'#60a5fa',warning:'#eab308',error:'#ef4444',debug:'#6b7280'};
    el.innerHTML=logs.length?logs.map(l=>{
      const t=(l.logged_at||'').split('T')[1]?.split('.')[0]||'';
      return `<div><span style="color:var(--text-muted)">${t}</span> <span style="color:${colors[l.level]||'inherit'}">[${l.level.toUpperCase()}]</span> ${escapeHtml(l.message)}</div>`;
    }).join(''):'<div style="color:var(--text-muted)">Sin logs</div>';
    el.scrollTop=el.scrollHeight; el.scrollIntoView({behavior:'smooth'});
  }

  _renderLibStats(s) {
    const el=document.getElementById('dlLibStats'); if(!el||!s) return;
    el.innerHTML=[
      {icon:'📸',label:'Publicaciones',value:(s.total_posts||0).toLocaleString()},
      {icon:'👤',label:'Autores',       value:(s.total_authors||0).toLocaleString()},
      {icon:'🖼️',label:'Imágenes',      value:(s.total_images||0).toLocaleString()},
      {icon:'🎬',label:'Videos',        value:(s.total_videos||0).toLocaleString()},
      {icon:'🎠',label:'Carruseles',    value:(s.total_carousels||0).toLocaleString()},
      {icon:'🏷️',label:'Etiquetas IA',  value:(s.total_ai_tags||0).toLocaleString()},
      {icon:'📝',label:'Textos OCR',    value:(s.total_ocr_texts||0).toLocaleString()},
      {icon:'💾',label:'Espacio usado', value:humanSize(s.total_size_bytes||0)},
    ].map(i=>`<div class="dl-stat-card"><div class="dl-stat-label">${i.icon} ${i.label}</div><div class="dl-stat-value" style="font-size:1.1rem">${i.value}</div></div>`).join('');
  }

  _appendLog(msg,level) {
    const el=document.getElementById('dlEventLog'); if(!el) return;
    const colors={info:'#60a5fa',warning:'#eab308',error:'#ef4444',success:'#22c55e'};
    const now=new Date().toTimeString().split(' ')[0];
    const d=document.createElement('div');
    d.innerHTML=`<span style="color:var(--text-muted)">${now}</span> <span style="color:${colors[level||'info']||'inherit'}">${escapeHtml(String(msg))}</span>`;
    el.appendChild(d); el.scrollTop=el.scrollHeight;
    while(el.children.length>200) el.removeChild(el.firstChild);
  }

  _bindControls() {
    // Actualizar
    document.getElementById('dlRefresh')?.addEventListener('click',async()=>{
      const b=document.getElementById('dlRefresh'); b.disabled=true; b.textContent='↻ Actualizando...';
      await this._loadInitialData(); b.disabled=false; b.textContent='↻ Actualizar'; showToast('Actualizado ✓','success');
    });

    // Reanudar cola interrumpida
    document.getElementById('dlResumeQueue')?.addEventListener('click',async()=>{
      if(!confirm('¿Reanudar todas las tareas pausadas?')) return;
      try { const r=await fetch('/api/downloads/resume-queue',{method:'POST'}); const d=await r.json(); showToast(d.message,'success'); await this._loadInitialData(); }
      catch(e){showToast('Error: '+e.message,'error');}
    });

    // Pausar todo
    document.getElementById('dlPauseAll')?.addEventListener('click',async()=>{
      if(!confirm('¿Pausar todas las tareas activas?')) return;
      try { const r=await fetch('/api/downloads/pause-all',{method:'POST'}); const d=await r.json(); showToast(d.message,'warning'); await this._loadInitialData(); }
      catch(e){showToast('Error: '+e.message,'error');}
    });

    // Cancelar todo
    document.getElementById('dlCancelAll')?.addEventListener('click',async()=>{
      if(!confirm('¿Cancelar todas las tareas en cola?\nLos archivos ya descargados se conservan.')) return;
      try { const r=await fetch('/api/downloads/cancel-all',{method:'POST'}); const d=await r.json(); showToast(d.message,'warning'); await this._loadInitialData(); }
      catch(e){showToast('Error: '+e.message,'error');}
    });

    // Limpiar historial
    document.getElementById('dlClearHistory')?.addEventListener('click',async()=>{
      if(!confirm('¿Eliminar del historial las tareas completadas, canceladas y con error?\nLos archivos descargados NO se borran.')) return;
      try { const r=await fetch('/api/downloads/clear-history',{method:'POST'}); const d=await r.json(); showToast(d.message,'success'); await this._loadInitialData(); }
      catch(e){showToast('Error: '+e.message,'error');}
    });

    // Filtrar tareas activas por estado
    document.getElementById('dlActiveStatusFilter')?.addEventListener('change',()=>this._renderActiveTasks());

    // Limpiar log de eventos
    document.getElementById('dlClearLog')?.addEventListener('click',()=>{const e=document.getElementById('dlEventLog');if(e)e.innerHTML='';});

    // Comprobación visible con navegador autorizado. No comparte cookies ni
    // intenta evadir controles: abre un perfil Playwright persistente para
    // que el usuario pueda iniciar sesión manualmente si hace falta.
    document.getElementById('dlIgBrowserProbe')?.addEventListener('click', async () => {
      const url = window.prompt('Pegá una URL de publicación de Instagram para probar en el navegador:');
      if (!url) return;
      const btn = document.getElementById('dlIgBrowserProbe');
      if (btn) { btn.disabled = true; btn.textContent = '🌐 Abriendo...'; }
      try {
        const result = await DownloadsAPI.igBrowserProbe(url.trim());
        const text = result.message || `Resultado: ${result.state || result.status || 'desconocido'}`;
        showToast(text, result.blocked || result.state === 'login_required' ? 'warning' : 'info', 10000);
        this._appendLog('[Instagram navegador] '+text, result.blocked ? 'warning' : 'info');
      } catch (err) {
        showToast('No se pudo abrir la comprobación: '+(err.detail||err.message), 'error', 8000);
      } finally {
        if (btn) { btn.disabled = false; btn.textContent = '🌐 Probar en navegador'; }
      }
    });

    // Login Instagram
    document.getElementById('dlLoginBtn')?.addEventListener('click', () => {
      document.getElementById('modalLogin').style.display = '';
      window.syncInstagramLoginFields?.();
    });

    // Estadísticas de errores
    document.getElementById('dlRetryErrors')?.addEventListener('click', async () => {
      if (!confirm('¿Reintentar TODAS las descargas con error? Se resetea el contador de intentos y vuelven a la cola.')) return;
      const btn = document.getElementById('dlRetryErrors');
      btn.disabled = true; btn.textContent = '🔁 Reintentando...';
      try {
        const res = await fetch('/api/downloads/errors/retry-all', {method:'POST'});
        const d = await res.json();
        showToast(d.message, 'success', 5000);
        await this._loadInitialData();
      } catch (err) { showToast('Error: '+err.message, 'error'); }
      finally { btn.disabled = false; btn.textContent = '🔁 Reintentar errores'; }
    });

    document.getElementById('dlStatsBtn')?.addEventListener('click', async () => {
      const panel = document.getElementById('dlStatsPanel');
      const willShow = panel.style.display === 'none';
      panel.style.display = willShow ? '' : 'none';
      if (willShow) await this._renderStatsPanel();
    });
  }

  async _renderStatsPanel() {
    const panel = document.getElementById('dlStatsPanel');
    panel.innerHTML = '<div class="loading-center"><div class="spinner"></div></div>';
    try {
      // FIX: antes usaba getHistory(500) filtrado del lado del cliente —
      // ese endpoint devuelve los "más recientes" capados a 200 en el
      // backend, y si las descargas exitosas fueron más recientes que
      // los errores, el filtro nunca encontraba ninguno aunque hubiera
      // miles acumulados. Ahora pega directo a /api/downloads/errors,
      // que filtra por status='error' en la consulta SQL.
      const failed = await fetch('/api/downloads/errors?limit=1000').then(r=>r.json());
      if (!failed.length) {
        panel.innerHTML = '<div class="text-sm text-muted">✓ Sin descargas con error.</div>';
        return;
      }
      const byReason = {};
      failed.forEach(t => {
        const key = (t.error_message||'Desconocido').split('.')[0].substring(0,60);
        byReason[key] = (byReason[key]||0) + 1;
      });
      const summary = Object.entries(byReason).sort((a,b)=>b[1]-a[1]).slice(0,8)
        .map(([reason,count]) => `<div style="display:flex;justify-content:space-between;padding:4px 0;font-size:.82rem"><span>${escapeHtml(reason)}</span><span class="text-muted">${count}</span></div>`)
        .join('');
      const rows = failed.slice(0,150).map(t => `
        <div style="padding:10px 12px;background:var(--bg-hover);border-radius:8px;margin-bottom:6px;font-size:.8rem">
          <div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start">
            <div style="font-weight:600;word-break:break-all;flex:1">${escapeHtml((t.url||'').substring(0,90))}</div>
            <button class="btn-icon" data-task-id="${t.id}" title="Ver detalle / respuesta completa" style="flex-shrink:0">🔍</button>
          </div>
          <div style="color:var(--color-error);margin-top:4px">${escapeHtml(t.error_message||'Error desconocido')}</div>
          <div style="color:var(--text-muted);margin-top:4px;display:flex;gap:12px">
            <span>Intentos: ${t.attempt_count||1}</span>
            <span>${formatDateTime(t.completed_at||t.created_at)}</span>
          </div>
          <div class="err-detail" id="errDetail-${t.id}" style="display:none;margin-top:8px;padding:8px 10px;background:var(--bg-card);border:1px solid var(--border-color);border-radius:6px;font-family:monospace;font-size:.74rem;white-space:pre-wrap;word-break:break-all;max-height:280px;overflow-y:auto"></div>
        </div>`).join('');
      panel.innerHTML = `
        <div style="font-weight:700;margin-bottom:8px">📊 ${failed.length} descarga(s) con error</div>
        <div style="margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid var(--border-color)">
          <div style="font-size:.78rem;color:var(--text-muted);margin-bottom:6px">Motivos más frecuentes</div>
          ${summary}
        </div>
        <div style="font-size:.78rem;color:var(--text-muted);margin-bottom:8px">Detalle (últimas ${Math.min(failed.length,150)}) — 🔍 muestra el traceback/respuesta completa</div>
        ${rows}`;

      // Botón 🔍 por fila: trae los logs guardados de esa tarea (incluye
      // el traceback completo, que ya se guarda en download_logs.details
      // pero antes no se mostraba en ningún lado accesible).
      panel.querySelectorAll('[data-task-id]').forEach(btn => {
        btn.addEventListener('click', async () => {
          const taskId = btn.dataset.taskId;
          const box = document.getElementById('errDetail-'+taskId);
          const willShow = box.style.display === 'none';
          box.style.display = willShow ? '' : 'none';
          if (!willShow || box.dataset.loaded) return;
          box.textContent = 'Cargando...';
          try {
            const logs = await DownloadsAPI.getTaskLogs(taskId);
            const errorLogs = logs.filter(l => l.level === 'error' && l.details);
            box.textContent = errorLogs.length
              ? errorLogs.map(l => `[${l.logged_at}] ${l.message}\n\n${l.details}`).join('\n\n---\n\n')
              : (logs.map(l => `[${l.level}] ${l.message}`).join('\n') || 'Sin logs guardados para esta tarea.');
            box.dataset.loaded = '1';
          } catch (err) {
            box.textContent = 'Error cargando logs: ' + err.message;
          }
        });
      });
    } catch (err) {
      panel.innerHTML = `<div class="text-sm" style="color:var(--color-error)">Error: ${err.message}</div>`;
    }
  }

  _bindWsEvents() {
    ws.on('startup_interrupted',(e)=>{
      const b=document.getElementById('dlInterruptedBanner'); if(b){b.style.display='';b.innerHTML=`⚠️ <strong>${e.message}</strong> — Usá "▶️ Reanudar cola" para continuar.`;}
      const r=document.getElementById('dlResumeQueue'); if(r) r.style.display='';
    });
    ws.on('task_queued',   (e)=>{this._appendLog('URL agregada: '+(e.url||''),'info'); this._loadInitialData();});
    ws.on('task_status',   (e)=>{this._appendLog('['+e.task_id+'] '+(e.message||e.status),'info'); this._loadInitialData();});
    ws.on('task_progress', (e)=>{
      const p=document.getElementById('pct-'+e.task_id); if(p) p.textContent=Math.round(e.progress||0)+'%';
      const bar=document.querySelector('#task-card-'+e.task_id+' .progress-bar'); if(bar) bar.style.width=Math.round(e.progress||0)+'%';
    });
    // FIX: dlStatSpeed quedaba siempre en "—" porque nunca se conectó a
    // ningún evento. file_progress (restaurado en el backend) trae
    // speed_bps mientras hay una descarga de archivo activa.
    ws.on('file_progress', (e)=>{
      const el=document.getElementById('dlStatSpeed'); if(!el) return;
      el.textContent = e.speed_bps ? humanSpeed(e.speed_bps) : '—';
      clearTimeout(this._speedResetTimer);
      // Si no llega otra actualización en 3s, volver a "—" (descarga terminó)
      this._speedResetTimer = setTimeout(()=>{ if(el) el.textContent='—'; }, 3000);
    });
    ws.on('task_completed',(e)=>{this._appendLog('✅ Finalizado post '+e.post_id,'success'); this._tasks.delete(e.task_id); setTimeout(()=>this._loadInitialData(),400);});
    ws.on('task_error',   (e)=>{this._appendLog('❌ Error tarea '+e.task_id+': '+e.error,'error'); this._loadInitialData();});
    ws.on('task_retry',   (e)=>{this._appendLog('🔄 Reintento en '+e.delay+'s'+(e.is_rate_limit?' (rate limit)':''),'warning');});
    ws.on('rate_limited', (e)=>{this._appendLog('⏳ '+e.message,'warning'); showToast(e.message,'warning',8000);});
    ws.on('ai_completed', (e)=>{this._appendLog('🤖 IA completada post '+e.post_id,'success');});
  }

  _startStatsPoll() {
    setInterval(async()=>{
      try {
        const [s,q]=await Promise.all([StatsAPI.library(),StatsAPI.queueSummary()]);
        this._renderLibStats(s); this._updateSummary(q);
        const pe=document.getElementById('stat-posts'); if(pe) pe.textContent=(s.total_posts||0).toLocaleString();
        const se=document.getElementById('stat-size');  if(se) se.textContent=humanSize(s.total_size_bytes||0);
      } catch {}
    },30000);
  }
}
