# 📦 Instagram Archiver

Aplicación de escritorio (servidor local) para archivar publicaciones de
Instagram para uso personal: imágenes, carruseles, videos y reels en su
máxima calidad, con biblioteca web navegable, búsqueda instantánea y
etiquetado automático mediante IA.

---

## 🏗️ Arquitectura

### Decisiones técnicas y justificación

| Capa | Tecnología | Justificación |
|------|-----------|----------------|
| Backend | **Python + FastAPI** | Ecosistema maduro para scraping (`instaloader`) e IA (`torch`, `CLIP`, `EasyOCR`); `asyncio` nativo para concurrencia sin bloqueos; WebSockets integrados para tiempo real. |
| Frontend | **HTML/CSS/JS vanilla + Alpine-style reactivity manual** | Cero build step, carga instantánea, sin dependencias pesadas. Ideal para una app local que abre `index.html` directamente. |
| Base de datos | **SQLite + FTS5** | Cero configuración, un solo archivo portable, rendimiento excelente hasta cientos de miles de filas con los índices correctos. FTS5 da búsqueda full-text nativa sin necesitar Elasticsearch. |
| IA — Visión | **CLIP (OpenAI, ViT-B/32)** | Clasificación zero-shot sin necesidad de entrenar modelos, funciona razonablemente en CPU. |
| IA — OCR | **EasyOCR** | Soporta español e inglés out-of-the-box, sin GPU. |
| Miniaturas | **Pillow + OpenCV (video)** | Generación rápida de WebP, extracción de keyframes de video. |

### Por qué NO Elasticsearch / PostgreSQL (por ahora)

Con una escala de **miles de publicaciones** (no millones), SQLite + FTS5
ofrece:
- Cero latencia de red (todo en el mismo proceso)
- Cero mantenimiento (sin servicios adicionales corriendo)
- Migración a PostgreSQL es posible más adelante si la escala lo justifica

### Estructura de carpetas

```
instagram-archiver/
├── backend/
│   ├── main.py                  # Entry point FastAPI
│   ├── config.py                # Configuración centralizada
│   ├── database/
│   │   ├── connection.py        # Pool de conexiones async + pragmas
│   │   ├── migrations.py        # Sistema de versionado de esquema
│   │   └── schema.sql           # DDL completo (tablas, índices, FTS, triggers)
│   ├── models/__init__.py       # Modelos Pydantic (validación + serialización)
│   ├── repositories/            # Repository Pattern — acceso a datos
│   ├── services/
│   │   ├── downloader/          # Cliente Instagram + Download Manager
│   │   ├── ai/                  # CLIP (visión) + EasyOCR + orquestador
│   │   └── thumbnail_service.py
│   ├── api/
│   │   ├── routes/               # Endpoints REST
│   │   └── websocket.py          # Tiempo real
│   └── utils/                    # Logging, comportamiento humano, archivos
├── frontend/
│   ├── index.html
│   └── assets/{css,js}/
├── tests/{unit,integration}/
├── data/                          # Generado en runtime (DB, media, thumbnails, logs)
├── requirements.txt
└── start.bat                      # Arranque en Windows
```

---

## 🚀 Instalación (Windows)

### Requisitos previos

- **Python 3.11 o superior** — [descargar aquí](https://www.python.org/downloads/)
  - ⚠️ Durante la instalación, marcar la casilla **"Add Python to PATH"**
- Conexión a internet para la primera instalación de dependencias

### Pasos

1. Descomprimí el archivo ZIP en una carpeta de tu elección (ej: `C:\InstagramArchiver`)
2. Hacé doble click en **`start.bat`**
3. La primera vez tardará varios minutos: crea el entorno virtual e instala
   todas las dependencias (FastAPI, instaloader, PyTorch, EasyOCR, etc.)
4. Cuando veas el mensaje `La aplicacion estara disponible en: http://127.0.0.1:8000`,
   abrí tu navegador en esa dirección

### Instalación manual (alternativa)

```bash
cd instagram-archiver
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python -m backend.main
```

---

## 📖 Guía de uso

### Agregar contenido para archivar

1. Click en **"+ Agregar URL"**
2. Tres modos disponibles:
   - **URL única**: pegá un link de Instagram
   - **Múltiples URLs**: pegá una lista, una URL por línea
   - **Importar archivo**: arrastrá un `.txt` con URLs

### Autenticación con Instagram (opcional, recomendado)

Para contenido privado o para reducir bloqueos por límite de tasa:

1. Click en **"🔑 Instagram"** (dentro de Centro de Descargas)
2. Ingresá tu usuario y contraseña
3. La contraseña **no se almacena** — solo se guarda la cookie de sesión
   localmente en `data/.instagram_session`

> **Comportamiento humano simulado**: la aplicación introduce delays
> aleatorios entre descargas (no instantáneos) y pausas largas cada ~15
> publicaciones, simulando un patrón de navegación humano normal. Esto
> reduce sustancialmente el riesgo de que Instagram marque tu cuenta como
> sospechosa. Cuanto más alto el volumen de descargas, más conservador
> conviene ser — podés ajustar estos tiempos en `backend/config.py`
> (`DownloaderConfig`).

### Navegación

- **Biblioteca**: grid principal, filtros por tipo, favoritos
- **Centro de Descargas**: progreso en tiempo real, logs, pausa/reanuda/cancela
- **Buscar**: búsqueda avanzada con filtros combinables + búsqueda OCR
- **Autores**: vista agrupada por cuenta de origen
- **Colecciones**: carpetas virtuales para organizar contenido
- **Favoritos**: publicaciones marcadas con ❤️
- **Etiquetas**: nube de etiquetas IA, hashtags, colores
- **Salud del Archivo**: integridad, duplicados, proyección de espacio

---

## 🩺 Centro de Salud del Archivo

Verifica continuamente:
- Archivos físicos faltantes (movidos/borrados fuera de la app)
- Hashes SHA-256 para detectar duplicados exactos
- Metadatos incompletos
- Sincronización del índice de búsqueda (FTS5)
- Proyección de espacio en disco según ritmo de descarga actual

---

## ⚙️ Configuración avanzada

Editá `backend/config.py` para ajustar:

| Parámetro | Ubicación | Por defecto | Descripción |
|-----------|-----------|-------------|--------------|
| `max_concurrent_downloads` | `DownloaderConfig` | 3 | Descargas simultáneas |
| `min/max_delay_between_posts_s` | `DownloaderConfig` | 3-8s | Delay humano entre posts |
| `session_pause_every_n_posts` | `DownloaderConfig` | 15 | Pausa larga cada N posts |
| `max_retries` | `DownloaderConfig` | 5 | Reintentos ante error |
| `clip_model` | `AIConfig` | ViT-B/32 | Modelo CLIP (más liviano = más rápido en CPU) |
| `ocr_languages` | `AIConfig` | es, en | Idiomas para EasyOCR |

---

## 🧪 Tests

```bash
pip install pytest pytest-asyncio
pytest
```

Cobertura:
- **Unitarios**: repositorios, utilidades de archivos, parsing de URLs,
  simulador de comportamiento humano
- **Integración**: endpoints completos vía httpx AsyncClient contra DB aislada

---

## 🔧 Mantenimiento

### Backup de la base de datos

La base de datos vive en `data/db/archiver.db`. Para respaldar todo el
archivo (metadatos + contenido):

```
data/
├── db/archiver.db        ← copiar este archivo
├── media/                ← copiar esta carpeta completa
└── thumbnails/           ← se puede regenerar, no es crítico respaldar
```

### Reindexar búsqueda

Si la búsqueda se desincroniza (poco probable, pero puede pasar tras un
cierre abrupto): **Salud del Archivo → Reindexar ahora**.

### Migración a otro disco

1. Detené la aplicación
2. Movés la carpeta `data/` completa al nuevo destino
3. Editás `backend/config.py` → `DATA_DIR` apuntando a la nueva ubicación

### Logs

- `data/logs/archiver.log` — log completo (rotación 10MB × 5 archivos)
- `data/logs/errors.log` — solo errores

---

## ⚠️ Notas legales y de uso responsable

Esta herramienta está diseñada exclusivamente para **archivado personal**
de contenido al que el usuario tiene acceso legítimo. Cada usuario es
responsable de cumplir con los Términos de Servicio de Instagram y las
leyes de propiedad intelectual aplicables en su jurisdicción al usar esta
herramienta.
