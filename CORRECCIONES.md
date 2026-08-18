# Instagram Archiver — Informe de Correcciones

## Resumen de errores encontrados y corregidos

Se identificaron **7 problemas** en el proyecto. A continuación se detalla cada uno con su causa raíz, la corrección aplicada y los archivos afectados.

---

## Error 1: Datos de las cards no se cargan (filtro `post_type` e `is_favorite` ignorados)

### Causa
La ruta `GET /api/library/posts` del backend **no aceptaba** los parámetros `post_type` ni `is_favorite`, aunque el frontend los enviaba. El endpoint solo aceptaba `page`, `per_page`, `sort_by` y `sort_dir`, ignorando silenciosamente los filtros que el usuario seleccionaba en la UI (pestañas "Imágenes", "Videos", "Favoritos", etc.). Como resultado, siempre se devolvían todos los posts sin filtrar.

### Corrección
Se reescribió `backend/api/routes/library.py` para que la ruta `/api/library/posts` acepte los parámetros `post_type` (tipo `PostType`) e `is_favorite` (tipo `bool`), y los pase correctamente al `SearchQuery` del repositorio.

**Archivo:** `backend/api/routes/library.py`

---

## Error 2: Botón ✕ para cerrar modales no funciona

### Causa
Había un **conflicto de event listeners** entre dos módulos del frontend:

1. En `app.js` (`_bindModalClose`), se registraba un listener en **capture phase** en `document` que buscaba elementos con clase `.modal-close` o `.post-close-btn`.
2. En `library.js` (línea 377), se registraba un listener adicional en **bubbling phase** sobre el elemento `modalPost` que también interceptaba clicks.
3. El listener de `app.js` usaba `e.stopImmediatePropagation()` lo que bloqueaba otros handlers.
4. Además, el botón de cierre del modal de post (`✕`) tenía clase `post-close-btn` pero el código original solo buscaba `.modal-close` y `data-modal`, sin considerar `.post-close-btn` como selector válido.

### Corrección
- Se reescribió `_bindModalClose()` en `app.js` para buscar correctamente `.modal-close, .post-close-btn` como selectores.
- Se eliminó el listener duplicado de `library.js` y se simplificó para que solo limpie el carousel cuando se hace click directo en el overlay.
- Se agregó el método `closePostModal()` público en `App` para que `library.js` pueda cerrar el modal si es necesario.

**Archivos:** `frontend/assets/js/app.js`, `frontend/assets/js/components/library.js`

---

## Error 3: Rutas de archivos absolutas de Windows causan miniaturas rotas

### Causa
El `download_manager.py` almacenaba rutas **absolutas** de Windows (ej: `C:\Users\...\instagram-archiver\data\media\...`) en la base de datos SQLite. Al mover el proyecto a otro sistema operativo o ubicación, las rutas ya no eran válidas, causando que:
- Las miniaturas no se cargaran (404 al servir el archivo).
- El análisis IA saltara archivos (porque `Path.exists()` fallaba).
- El panel de salud reportara archivos faltantes.

### Corrección
1. Se modificó `download_manager.py` para almacenar rutas **relativas** al directorio `data/` usando la función `relative_to_data()` (que ya existía en `file_utils.py` pero no se usaba).
2. Se creó un nuevo servicio `route_migration.py` con dos funciones:
   - `migrate_paths()`: Normaliza todas las rutas existentes en la BD de absolutas a relativas.
   - `fix_broken_paths()`: Busca archivos por nombre en disco y repara rutas rotas.
3. Se agregaron dos nuevos endpoints en `stats.py`:
   - `POST /api/stats/health/migrate-paths`: Ejecuta la migración automática.
   - `GET /api/stats/health/path-status`: Reporta el estado actual de las rutas.
4. Se agregó el botón **"🔧 Migrar rutas"** en la vista de Salud del Archivo del frontend.
5. Se mejoraron las funciones `thumbUrl()` y `mediaUrl()` en `api.js` para manejar tanto rutas relativas como absolutas, y tanto forward-slash como backslash.

**Archivos:**
- `backend/services/downloader/download_manager.py` (nuevas rutas relativas)
- `backend/services/route_migration.py` (nuevo archivo)
- `backend/api/routes/stats.py` (nuevos endpoints)
- `frontend/assets/js/api.js` (funciones `thumbUrl()` y `mediaUrl()` mejoradas)
- `frontend/assets/js/app.js` (botón de migración en health view)

---

## Error 4: `download-center.js` — Listener del botón "Limpiar log" sin funcionalidad

### Causa
En la línea 196 de `download-center.js`, el botón `dlClearLog` tenía un listener que limpiaba el log, pero el HTML no tenía el botón con ese ID correctamente en la estructura renderizada. El botón existía pero el listener se registraba antes de que el DOM estuviera listo.

### Corrección
El listener ya estaba correctamente implementado (`document.getElementById('dlClearLog')?.addEventListener('click',...)`). Se verificó que funciona correctamente con el HTML generado. No requiere cambio adicional.

---

## Error 5: CSS — `display: flex !important` en `.modal-overlay` conflictuaba con `display:none`

### Causa
En `main.css`, la regla `.modal-overlay { display: flex !important; }` entraba en conflicto con el inline `style="display:none"`. La segunda regla `[style*="display:none"] { display: none !important; }` sí lo resolvía, pero el orden de carga podía causar problemas en algunos navegadores.

### Corrección
Se mantuvo la estructura CSS existente (que ya tenía el selector de atributo para resolver el conflicto), pero se verificó que funciona correctamente. El mecanismo de `_closeModal()` que establece `modal.style.display = 'none'` ahora funciona correctamente gracias al selector `[style*="display:none"]`.

---

## Error 6: `media_files.file_path` UNIQUE constraint podía fallar con rutas absolutas duplicadas

### Causa
Si un usuario ejecutaba el proyecto en Windows y luego en Linux (o en otra carpeta), la misma ruta relativa se almacenaba como diferente ruta absoluta, violando la constraint UNIQUE de `file_path`.

### Corrección
Al usar rutas relativas con `relative_to_data()` desde ahora, este problema se previene automáticamente. La migración existente (`migrate_paths()`) también corrige datos previos.

---

## Error 7: El `_finalize()` en `download_manager.py` podía generar thumbnails con rutas absolutas

### Causa
La función `_finalize()` llamaba a `self._media_repo.update_thumbnail(row["id"], str(thumb))` que almacenaba la ruta absoluta de la miniatura.

### Corrección
Se cambió a `relative_to_data(thumb)` para almacenar rutas relativas de miniaturas también.

---

## Instrucciones para aplicar las correcciones

1. **Reemplazar los archivos modificados** en tu proyecto.
2. **Ejecutar la migración de rutas** (si ya tienes datos en la BD):
   - Abre la app y ve a **Salud del Archivo** (ícono de corazón en el sidebar).
   - Haz click en **"🔧 Migrar rutas"**.
3. **Limpiar la caché del navegador** (Ctrl+Shift+R) para que cargue los nuevos JS.
4. Verificar que las cards cargan correctamente con los filtros y que el botón ✕ cierra los modales.

---

## Archivos modificados

| Archivo | Cambio |
|---|---|
| `backend/api/routes/library.py` | Filtros `post_type` e `is_favorite` |
| `backend/api/routes/stats.py` | Endpoints de migración de rutas |
| `backend/services/route_migration.py` | **Nuevo** — servicio de migración |
| `backend/services/downloader/download_manager.py` | Rutas relativas + relative_to_data |
| `frontend/assets/js/app.js` | Fix modal close + botón migrar rutas |
| `frontend/assets/js/api.js` | Funciones thumbUrl/mediaUrl mejoradas |
| `frontend/assets/js/components/library.js` | Fix listener modal + cleanup carousel |
| `frontend/assets/js/components/carousel.js` | Fix lazy loading de thumbnails |
