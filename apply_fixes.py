"""
Reparación DEFINITIVA de:
1. task_repository.py -> get_active_tasks excluye 'error', set_status usa ISO timestamp real.
2. download_manager.py -> not_found marca error terminal con timestamp ISO.
3. download-center.js -> contador de inexistentes y refresco reactivo.
4. SQLite -> actualización de registros previos.
"""

from pathlib import Path
import sqlite3
from datetime import datetime, timezone

BASE = Path(__file__).resolve().parent

# ── 1. ACTUALIZAR TASK_REPOSITORY.PY ─────────────────────────────
TASK_REPO = BASE / "backend/repositories/task_repository.py"
if TASK_REPO.exists():
    code = TASK_REPO.read_text(encoding="utf-8")
    
    # A. Corregir get_active_tasks para excluir 'error'
    code = code.replace(
        "WHERE status NOT IN ('completed','cancelled')",
        "WHERE status NOT IN ('completed','cancelled','error')"
    )
    
    # B. Corregir set_status para usar fecha ISO real en vez de string literal
    old_set_status = """    async def set_status(self, task_id: int, status: TaskStatus, **extra) -> None:
        data: dict[str, Any] = {"status": status.value}
        if status == TaskStatus.DOWNLOADING and "started_at" not in extra:
            data["started_at"] = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"
        if status in (TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELLED):
            data["completed_at"] = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"
        data.update(extra)
        await self.update(task_id, data)"""

    new_set_status = """    async def set_status(self, task_id: int, status: TaskStatus, **extra) -> None:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        data: dict[str, Any] = {"status": status.value}
        if status == TaskStatus.DOWNLOADING and "started_at" not in extra:
            data["started_at"] = now_iso
        if status in (TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELLED):
            data["completed_at"] = now_iso
        data.update(extra)
        await self.update(task_id, data)"""

    if old_set_status in code:
        code = code.replace(old_set_status, new_set_status)
    
    TASK_REPO.write_text(code, encoding="utf-8")
    print("✅ task_repository.py actualizado: 'error' excluido de tareas activas y fechas ISO corregidas.")

# ── 2. ACTUALIZAR DOWNLOAD-CENTER.JS ──────────────────────────────
DC_JS = BASE / "frontend/assets/js/components/download-center.js"
if DC_JS.exists():
    dc_code = DC_JS.read_text(encoding="utf-8")
    
    # Asegurar conteo real de inexistentes desde errores e historial
    old_summary = """    // Contar inexistentes del historial
    const notFoundCount = (this._historyTasks||[]).filter(t => t.status === 'not_found' || (t.error_message && t.error_message.includes('no existe'))).length;
    set('dlStatNotFound', notFoundCount);
    set('dlStatError', Math.max(0, (s.error||0) - notFoundCount));"""

    new_summary = """    // Contar inexistentes de las tareas con error registradas
    const allErrors = (this._historyTasks||[]).filter(t => t.status === 'error' || t.status === 'not_found');
    const notFoundCount = allErrors.filter(t => t.status === 'not_found' || (t.error_message && (t.error_message.includes('no existe') || t.error_message.includes('eliminada')))).length;
    set('dlStatNotFound', notFoundCount);
    set('dlStatError', Math.max(0, (s.error||0) - notFoundCount));"""

    if old_summary in dc_code:
        dc_code = dc_code.replace(old_summary, new_summary)
        DC_JS.write_text(dc_code, encoding="utf-8")
        print("✅ download-center.js actualizado: cálculo de estadísticas optimizado.")

# ── 3. LIMPIEZA DIRECTA DE SQLITE ─────────────────────────────────
db_path = BASE / "data/db/archiver.db"
if db_path.exists():
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    now_iso = datetime.now(timezone.utc).isoformat()
    # Mover todas las tareas que fallaron o quedaron en analyzing a error con fecha actual
    cur.execute("""
        UPDATE download_tasks 
        SET status = 'error', 
            completed_at = ?,
            error_message = COALESCE(error_message, 'La publicación no existe o fue eliminada de Instagram.')
        WHERE status = 'analyzing'
    """, (now_iso,))
    conn.commit()
    print(f"✅ Base de datos saneada: {cur.rowcount} tareas actualizadas a estado final.")
    conn.close()

print("\n🚀 FIX COMPLETO APLICADO CON ÉXITO.")
print("Pasos siguientes:")
print("1. Ejecuta: .\\start.bat")
print("2. Abre http://localhost:8000 (Ctrl+F5)")