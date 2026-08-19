#!/usr/bin/env python3
"""
fix_dates.py - Repara el formateo de fechas en frontend/assets/js/api.js y components
para evitar que aparezca 'Invalid Date'.
"""
from pathlib import Path
import re

print("🛠️ Reparando funciones de fecha en el Frontend...")

api_file = Path("frontend/assets/js/api.js")
if api_file.exists():
    text = api_file.read_text(encoding="utf-8")

    # Función robusta de formateo de fecha
    robust_date_helpers = """
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
    if (/^\\d+$/.test(trimmed)) {
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
"""

    # Reemplazar las funciones formatDate y formatDateTime existentes
    pattern = r'function formatDate\([^)]*\)\s*\{[\s\S]*?function formatDateTime\([^)]*\)\s*\{[\s\S]*?\}\s*\}'
    if re.search(pattern, text):
        text = re.sub(pattern, robust_date_helpers.strip(), text, count=1)
        api_file.write_text(text, encoding="utf-8")
        print("✅ frontend/assets/js/api.js actualizado con parseo seguro de fechas.")
    else:
        # Si no coincidió el bloque exacto, reemplazar individualmente
        text = re.sub(r'function formatDate\([^)]*\)\s*\{[\s\S]*?\}', '', text)
        text = re.sub(r'function formatDateTime\([^)]*\)\s*\{[\s\S]*?\}', '', text)
        text += "\n" + robust_date_helpers
        api_file.write_text(text, encoding="utf-8")
        print("✅ Funciones de fecha añadidas limpiamente a frontend/assets/js/api.js.")

# También revisar download-center.js por si tiene formateadores propios
dc_file = Path("frontend/assets/js/components/download-center.js")
if dc_file.exists():
    dc_text = dc_file.read_text(encoding="utf-8")
    # Reemplazar posibles formateos directos new Date(...) que fallen
    dc_text = dc_text.replace("new Date(task.created_at).toLocaleString()", "formatDateTime(task.created_at || task.downloaded_at)")
    dc_text = dc_text.replace("new Date(item.created_at).toLocaleString()", "formatDateTime(item.created_at || item.downloaded_at)")
    dc_file.write_text(dc_text, encoding="utf-8")
    print("✅ frontend/assets/js/components/download-center.js sincronizado.")

print("\n🎉 Listo. Recarga con Ctrl + F5 y las fechas se mostrarán correctamente en todas las descargas.")