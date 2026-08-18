from __future__ import annotations
import asyncio, logging, re, urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
import httpx
from backend.config import config

logger = logging.getLogger(__name__)

@dataclass
class PageResult:
    url:str; source_type:str; title:str=""; author:str=""
    description:str=""; content_text:str=""
    media_files:list=field(default_factory=list)
    metadata:dict=field(default_factory=dict)
    shortcode:str=""; posted_at:Optional[str]=None
    location:Optional[str]=None; tags:list=field(default_factory=list)
    is_instagram:bool=False

class URLClassifier:
    INSTAGRAM_RE = re.compile(r"instagram\.com/(p|reel|tv|stories)/([A-Za-z0-9_\-]+)")
    # Perfil de Instagram (instagram.com/usuario, sin /p/ /reel/ etc.) — NO es
    # un post individual. El extractor de posts no puede procesarlo, y sin
    # sesión Instagram solo muestra el muro de login — no vale la pena
    # capturarlo como si fuera contenido real.
    INSTAGRAM_PROFILE_RE = re.compile(
        r"instagram\.com/(?!p/|reel/|tv/|stories/|explore/|accounts/|direct/)"
        r"([A-Za-z0-9_.]+)/?(?:\?.*)?$"
    )
    YOUTUBE_RE   = re.compile(r"(youtube\.com/(watch|shorts|live)|youtu\.be/)")
    TWITTER_RE   = re.compile(r"(twitter\.com|x\.com)/\w+/status/\d+")
    TIKTOK_RE    = re.compile(r"tiktok\.com/")
    GITHUB_RE    = re.compile(r"github\.com/")
    ML_RE        = re.compile(r"mercadolibre\.com")
    LINKEDIN_RE  = re.compile(r"linkedin\.com/")
    NEWS_RE      = re.compile(r"(infobae\.com|clarin\.com|lanacion\.com|cronista\.com|ambito\.com|perfil\.com|pagina12\.com|telam\.com|reddit\.com|medium\.com)")

    @classmethod
    def classify(cls, url):
        if cls.INSTAGRAM_RE.search(url): return "instagram"
        if cls.INSTAGRAM_PROFILE_RE.search(url): return "instagram_profile"
        if cls.YOUTUBE_RE.search(url):   return "youtube"
        if cls.TWITTER_RE.search(url):   return "twitter"
        if cls.TIKTOK_RE.search(url):    return "tiktok"
        if cls.GITHUB_RE.search(url):    return "github"
        if cls.ML_RE.search(url):        return "mercadolibre"
        if cls.LINKEDIN_RE.search(url):  return "linkedin"
        if cls.NEWS_RE.search(url):      return "news"
        return "generic"

    @classmethod
    def is_valid_http(cls, url):
        try:
            p = urlparse(url.strip())
            return p.scheme in ("http","https") and bool(p.netloc)
        except: return False

def _is_real_image(path):
    if not path.exists() or path.stat().st_size < 64: return False
    try:
        with open(path,"rb") as f: h = f.read(16)
        if h[:2]==b'\xff\xd8': return True
        if h[:8]==b'\x89PNG\r\n\x1a\n': return True
        if h[:6] in (b'GIF87a',b'GIF89a'): return True
        if h[:4]==b'RIFF' and h[8:12]==b'WEBP': return True
        if h[:2]==b'BM': return True
        if h[4:8]==b'ftyp': return True
        return False
    except: return False

def _is_real_video(path):
    if not path.exists() or path.stat().st_size < 64: return False
    try:
        with open(path,"rb") as f: h = f.read(12)
        if h[4:8]==b'ftyp': return True
        if h[4:8] in (b'mdat',b'moov',b'free'): return True
        if h[:4]==b'\x1a\x45\xdf\xa3': return True
        return False
    except: return False


def _truncate_text(text: str) -> str:
    """
    Recorta el texto scrapeado según los límites configurables en
    ⚙️ Configuración (max_content_chars / max_content_lines), en vez de
    los límites fijos que había antes (10000/15000/5000 hardcodeados).
    """
    from backend.services import settings_service
    max_chars = int(settings_service.get("max_content_chars"))
    max_lines = int(settings_service.get("max_content_lines"))
    lines = text.split("\n")[:max_lines]
    return "\n".join(lines)[:max_chars]

class _YtdlpLogger:
    def debug(self, msg):
        if not msg.startswith('[debug]'): logger.debug("yt-dlp: %s", msg)
    def info(self, msg): logger.debug("yt-dlp: %s", msg)
    def warning(self, msg): logger.warning("yt-dlp: %s", msg)
    def error(self, msg): logger.error("yt-dlp: %s", msg)

def _ytdlp_download(url, dest_dir, opts_extra=None):
    import yt_dlp
    opts = {
        "outtmpl": str(Path(dest_dir).resolve()/"%(id)s.%(ext)s"),
        "quiet": True, "no_warnings": True, "ignoreerrors": False,
        "logger": _YtdlpLogger(),
    }
    if opts_extra: opts.update(opts_extra)
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=True)

class BaseExtractor:
    def __init__(self, dest_dir):
        self.dest_dir = Path(dest_dir).resolve()
        self.dest_dir.mkdir(parents=True, exist_ok=True)

    async def extract(self, url): raise NotImplementedError

    async def _download_file(self, url, dest, referer=""):
        dest = Path(dest).resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        headers = {"User-Agent": config.downloader.user_agent,
                   "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"}
        if referer: headers["Referer"] = referer
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
                async with client.stream("GET", url, headers=headers) as resp:
                    resp.raise_for_status()
                    ct = resp.headers.get("content-type","")
                    if "text/html" in ct or "text/plain" in ct: return None
                    with open(dest,"wb") as f:
                        async for chunk in resp.aiter_bytes(65536): f.write(chunk)
            if not _is_real_image(dest) and not _is_real_video(dest):
                dest.unlink(missing_ok=True); return None
            return dest
        except Exception as exc:
            logger.warning("Download falló %s: %s", url[:80], exc)
            dest.unlink(missing_ok=True); return None

    async def _fetch_html(self, url):
        headers = {"User-Agent": config.downloader.user_agent,
                   "Accept": "text/html,*/*;q=0.9",
                   "Accept-Language": "es-AR,es;q=0.9,en;q=0.8"}
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text

    def _collect_media(self, info):
        media_files = []
        entries = info.get("entries") or [info]
        idx = 0
        for entry in entries:
            eid = entry.get("id", info.get("id",""))
            for ext in ["mp4","mkv","webm","m4v"]:
                c = self.dest_dir/f"{eid}.{ext}"
                if not c.exists(): c = self.dest_dir/f"{info.get('id','')}.{ext}"
                if c.exists() and _is_real_video(c):
                    media_files.append({"path":str(c),"file_type":"video","mime_type":"video/mp4",
                                        "duration_s":entry.get("duration"),"carousel_index":idx})
                    idx += 1; break
        for ext in ["jpg","jpeg","png","webp"]:
            c = self.dest_dir/f"{info.get('id','')}.{ext}"
            if c.exists() and _is_real_image(c):
                media_files.append({"path":str(c),"file_type":"thumbnail",
                                    "mime_type":f"image/{ext}","carousel_index":None})
                break
        return media_files

    def _download_og_image(self, html, filename="preview.jpg"):
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for prop in ["og:image","twitter:image"]:
                tag = soup.find("meta",property=prop) or soup.find("meta",attrs={"name":prop})
                if tag and tag.get("content"):
                    dest = self.dest_dir/filename
                    try:
                        urllib.request.urlretrieve(tag["content"], str(dest))
                        if _is_real_image(dest):
                            return [{"path":str(dest),"file_type":"image","mime_type":"image/jpeg","carousel_index":0}]
                        dest.unlink(missing_ok=True)
                    except: pass
        except: pass
        return []

    # Patrones de páginas de challenge/anti-bot — si el título o el body
    # contienen alguno de estos, la página NO es contenido real y no se
    # guarda como screenshot válido.
    _ANTIBOT_PATTERNS = [
        "checking your browser", "verify you are human", "just a moment",
        "attention required", "are you a robot", "unusual traffic",
        "please verify you are a human", "cf-browser-verification",
        "ddos protection by", "access denied", "captcha",
        "one more step", "verificando tu navegador", "comprobando tu navegador",
    ]

    async def _capture_screenshot(self, url: str) -> dict:
        """
        Captura la página con un navegador headless (Playwright) y extrae
        título + descripción real desde el DOM ya renderizado. A diferencia
        de httpx + BeautifulSoup, esto funciona en sitios que necesitan
        JavaScript para mostrar contenido (Udemy, Cluely, apps web, etc.)
        que antes quedaban con "Sin archivos media".

        Retorna: {"screenshot_path": Path|None, "title": str, "description": str,
                   "blocked": bool, "block_reason": str|None}

        Si Playwright no está instalado, la página falla al cargar, o se
        detecta una pantalla de challenge/CAPTCHA, screenshot_path queda en
        None (no se guarda como si fuera contenido real) y blocked/block_reason
        indican el motivo para que download_manager lo registre como error
        en vez de un post vacío exitoso.
        """
        from backend.services import settings_service

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning(
                "Playwright no instalado — sin captura de pantalla. "
                "Instalar con: pip install playwright && playwright install chromium"
            )
            return {"screenshot_path": None, "title": "", "description": "",
                    "blocked": False, "block_reason": None}

        wait_s   = float(settings_service.get("screenshot_wait_seconds"))
        nav_to_s = float(settings_service.get("navigation_timeout_s"))

        dest = self.dest_dir / "screenshot.png"
        title = ""
        description = ""
        try:
            async with async_playwright() as p:
                # Flags estándar para reducir el fingerprint trivial de
                # automatización (NO es evasión de CAPTCHA, solo evita que
                # el sitio detecte "esto es obviamente un bot headless"
                # por chequeos superficiales como navigator.webdriver).
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                page = await browser.new_page(
                    viewport={"width": 1280, "height": 800},
                    user_agent=config.downloader.user_agent,
                )
                try:
                    await page.goto(url, timeout=nav_to_s * 1000, wait_until="domcontentloaded")
                    try:
                        await page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass

                    # Espera configurable antes de capturar (default 5s) —
                    # da tiempo a contenido que carga async y a que un
                    # posible challenge anti-bot muestre su resultado final.
                    if wait_s > 0:
                        await page.wait_for_timeout(wait_s * 1000)

                    title = await page.title()
                    description = await page.evaluate(
                        """() => {
                            const og = document.querySelector('meta[property="og:description"]');
                            if (og && og.content) return og.content;
                            const md = document.querySelector('meta[name="description"]');
                            if (md && md.content) return md.content;
                            return '';
                        }"""
                    ) or ""

                    # FIX: antes se chequeaba el patrón anti-bot y RECIÉN
                    # DESPUÉS se tomaba el screenshot — dejaba una ventana
                    # (por más chica que sea, son dos llamadas async
                    # separadas) donde Cloudflare/challenge podía inyectar
                    # su pantalla justo entre el chequeo y la captura,
                    # pasando el chequeo limpio pero guardando igual el
                    # challenge como si fuera contenido real (caso real:
                    # greasyfork.org guardado con "Just a moment...").
                    #
                    # Ahora se captura PRIMERO y se valida DESPUÉS, leyendo
                    # el título/body en el mismo instante que el screenshot
                    # ya tomado — sin gap posible entre lo que se ve y lo
                    # que se guarda.
                    await page.screenshot(path=str(dest), full_page=True, timeout=15000)

                    title = await page.title()  # releer por si cambió durante el wait
                    body_snippet = await page.evaluate(
                        "() => document.body ? document.body.innerText.slice(0, 2000) : ''"
                    ) or ""
                    haystack = f"{title} {body_snippet}".lower()
                    block_reason = next(
                        (p for p in self._ANTIBOT_PATTERNS if p in haystack), None
                    )

                    if block_reason:
                        dest.unlink(missing_ok=True)
                        logger.warning(
                            "Página de challenge/anti-bot detectada en %s (%r) — no se guarda screenshot",
                            url[:80], block_reason,
                        )
                        return {"screenshot_path": None, "title": title, "description": description,
                                "blocked": True, "block_reason": block_reason}
                finally:
                    if not browser.is_connected():
                        pass
                    else:
                        await browser.close()
        except Exception as exc:
            logger.warning("Screenshot falló para %s: %s", url[:80], exc)
            dest.unlink(missing_ok=True)
            return {"screenshot_path": None, "title": title, "description": description,
                    "blocked": False, "block_reason": str(exc)}

        if not dest.exists() or not _is_real_image(dest):
            return {"screenshot_path": None, "title": title, "description": description,
                    "blocked": False, "block_reason": None}

        return {"screenshot_path": dest, "title": title.strip(), "description": description.strip(),
                "blocked": False, "block_reason": None}


class YouTubeExtractor(BaseExtractor):
    async def extract(self, url):
        return await asyncio.get_event_loop().run_in_executor(None, self._run, url)
    def _run(self, url):
        try: import yt_dlp
        except ImportError:
            return PageResult(url=url, source_type="youtube", title="YouTube", metadata={"error":"yt-dlp no instalado"})
        try:
            info = _ytdlp_download(url, self.dest_dir, {
                "format":"bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
                "writethumbnail":True,"merge_output_format":"mp4"})
        except Exception as exc:
            return PageResult(url=url, source_type="youtube", title="YouTube (error)", metadata={"error":str(exc)})
        media_files = self._collect_media(info)
        ud = info.get("upload_date","")
        posted_at = f"{ud[:4]}-{ud[4:6]}-{ud[6:]}T00:00:00Z" if ud and len(ud)==8 else None
        return PageResult(url=url, source_type="youtube", title=info.get("title",""),
                          author=info.get("uploader",""), description=(info.get("description","") or "")[:500],
                          media_files=media_files, tags=(info.get("tags",[]) or [])[:20],
                          posted_at=posted_at, metadata={"video_id":info.get("id",""),"duration":info.get("duration")})


class TikTokExtractor(BaseExtractor):
    async def extract(self, url):
        return await asyncio.get_event_loop().run_in_executor(None, self._run, url)
    def _run(self, url):
        try: import yt_dlp
        except ImportError:
            return PageResult(url=url, source_type="tiktok", title="TikTok", metadata={"error":"yt-dlp no instalado"})
        try:
            info = _ytdlp_download(url, self.dest_dir, {"format":"best[ext=mp4]/best","writethumbnail":True})
        except Exception as exc:
            return PageResult(url=url, source_type="tiktok", title="TikTok (error)", metadata={"error":str(exc)})
        media_files = self._collect_media(info)
        author = (info.get("uploader_id","") or info.get("uploader","") or "").lstrip("@")
        ud = info.get("upload_date","")
        posted_at = f"{ud[:4]}-{ud[4:6]}-{ud[6:]}T00:00:00Z" if ud and len(ud)==8 else None
        return PageResult(url=url, source_type="tiktok",
                          title=info.get("title","") or (info.get("description","") or "")[:100],
                          author=author, description=(info.get("description","") or "")[:500],
                          media_files=media_files, tags=(info.get("tags",[]) or [])[:20],
                          posted_at=posted_at, metadata={"video_id":info.get("id","")})


class TwitterExtractor(BaseExtractor):
    async def extract(self, url):
        normalized = url.replace("x.com/","twitter.com/")
        try:
            result = await asyncio.get_event_loop().run_in_executor(None, self._ytdlp, normalized)
            if result and result.media_files: return result
        except Exception as exc:
            logger.info("Twitter sin media (%s), saltando.", exc)
        # Sin media = retornar vacío para que download_manager lo saltee
        return PageResult(url=url, source_type="twitter", title="", author="",
                          description="", content_text="", media_files=[],
                          metadata={"has_media":False})

    def _ytdlp(self, url):
        info = _ytdlp_download(url, self.dest_dir, {"format":"best[ext=mp4]/best"})
        media_files = self._collect_media(info)
        for i, thumb in enumerate((info.get("thumbnails") or [])[:4]):
            tu = thumb.get("url","")
            if not tu: continue
            dest = self.dest_dir/f"{info.get('id','tweet')}_img{i}.jpg"
            try:
                urllib.request.urlretrieve(tu, str(dest))
                if _is_real_image(dest):
                    media_files.append({"path":str(dest),"file_type":"image","mime_type":"image/jpeg","carousel_index":i})
                else: dest.unlink(missing_ok=True)
            except: pass
        return PageResult(url=url, source_type="twitter",
                          title=info.get("title","") or (info.get("description","") or "")[:100],
                          author=(info.get("uploader_id","") or "").lstrip("@"),
                          media_files=media_files, metadata={"tweet_id":info.get("id","")})


class LinkedInExtractor(BaseExtractor):
    async def extract(self, url):
        # Método principal: screenshot real con navegador headless
        shot = await self._capture_screenshot(url)
        if shot.get("blocked"):
            raise RuntimeError(f"Página bloqueada por protección anti-bot ({shot['block_reason']})")
        if shot["screenshot_path"]:
            return PageResult(
                url=url, source_type="linkedin",
                title=shot["title"] or urlparse(url).path,
                description=shot["description"],
                media_files=[{"path":str(shot["screenshot_path"]),"file_type":"image",
                             "mime_type":"image/png","carousel_index":0}],
                metadata={"domain":"linkedin.com","capture_method":"screenshot"},
            )
        # Fallback: og:image + meta tags vía httpx si Playwright no está disponible/falla
        try:
            html = await self._fetch_html(url)
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            title = ""
            for prop in ["og:title","twitter:title"]:
                tag = soup.find("meta",property=prop) or soup.find("meta",attrs={"name":prop})
                if tag and tag.get("content"): title=tag["content"]; break
            desc = ""
            for prop in ["og:description","description"]:
                tag = soup.find("meta",property=prop) or soup.find("meta",attrs={"name":prop})
                if tag and tag.get("content"): desc=tag["content"]; break
            media_files = self._download_og_image(html, "linkedin_preview.jpg")
            return PageResult(url=url, source_type="linkedin", title=title or urlparse(url).path,
                              description=desc, media_files=media_files,
                              metadata={"domain":"linkedin.com","capture_method":"og_image"})
        except Exception as exc:
            return PageResult(url=url, source_type="linkedin", title="LinkedIn",
                              description="", content_text=f"LinkedIn URL: {url}", metadata={"error":str(exc)})


class GitHubExtractor(BaseExtractor):
    REPO_RE = re.compile(r"github\.com/([^/?#]+)/([^/?#]+)")
    async def extract(self, url):
        m = self.REPO_RE.search(url)
        if not m: return await GenericExtractor(self.dest_dir).extract(url)
        owner, repo = m.group(1), m.group(2).rstrip("/")
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=20,
                headers={"User-Agent":config.downloader.user_agent,
                         "Accept":"application/vnd.github.v3+json"}) as client:
                resp = await client.get(f"https://api.github.com/repos/{owner}/{repo}")
            if resp.status_code != 200:
                return PageResult(url=url, source_type="github", title=f"{owner}/{repo}")
            data = resp.json()
            readme = ""
            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                    r2 = await client.get(f"https://api.github.com/repos/{owner}/{repo}/readme",
                                          headers={"Accept":"application/vnd.github.v3.raw"})
                    if r2.status_code == 200: readme = r2.text[:5000]
            except: pass
            # Imagen social del repo
            media_files = []
            og_url = f"https://opengraph.githubassets.com/1/{owner}/{repo}"
            dest = self.dest_dir/"github_og.png"
            try:
                urllib.request.urlretrieve(og_url, str(dest))
                if _is_real_image(dest):
                    media_files.append({"path":str(dest),"file_type":"image","mime_type":"image/png","carousel_index":0})
                else: dest.unlink(missing_ok=True)
            except: pass
            return PageResult(url=url, source_type="github",
                              title=data.get("full_name",f"{owner}/{repo}"), author=owner,
                              description=data.get("description",""), content_text=readme,
                              tags=data.get("topics",[]), posted_at=data.get("created_at"),
                              media_files=media_files,
                              metadata={"stars":data.get("stargazers_count",0),"language":data.get("language","")})
        except Exception as exc:
            return PageResult(url=url, source_type="github", title=f"{owner}/{repo}", metadata={"error":str(exc)})


class MercadoLibreExtractor(BaseExtractor):
    async def extract(self, url):
        clean = url.split("#")[0].split("?")[0]
        title, price, media_files = "", "", []
        try:
            html = await self._fetch_html(clean)
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            t = soup.find("h1", class_=re.compile(r"ui-pdp-title"))
            if t: title = t.get_text(strip=True)
            if not title:
                og = soup.find("meta", property="og:title")
                if og: title = og.get("content","")
            p = soup.find("span", class_=re.compile(r"andes-money-amount__fraction"))
            if p: price = p.get_text(strip=True)
            img_urls = []
            for img in soup.find_all("img"):
                src = img.get("data-zoom") or img.get("data-src") or img.get("src","")
                if src and "mlstatic.com" in src and src.startswith("http"):
                    src = re.sub(r"-[A-Z]\.jpg","-O.jpg",src); img_urls.append(src)
            img_urls = list(dict.fromkeys(img_urls))[:12]
            tasks = [self._download_file(iu, self.dest_dir/f"ml_{i:02d}.jpg", referer=clean) for i,iu in enumerate(img_urls)]
            paths = await asyncio.gather(*tasks, return_exceptions=True)
            for i,path in enumerate(paths):
                if isinstance(path,Path) and path and path.exists():
                    media_files.append({"path":str(path),"file_type":"image","mime_type":"image/jpeg","carousel_index":i})
        except Exception as exc:
            logger.debug("MercadoLibre scrape estático falló para %s: %s", url[:80], exc)

        # FIX: el scraping estático (httpx+BeautifulSoup) dejó de encontrar
        # imágenes — MercadoLibre las carga por JS en el carrusel del
        # producto. Antes esto pasaba silencioso: el título del producto
        # siempre está presente, así que la card se creaba igual con 0
        # archivos. Ahora, si no se encontró ninguna imagen por scraping
        # estático, se cae al mismo mecanismo de screenshot real (Playwright)
        # que ya usan News/Generic/LinkedIn — al menos queda una captura
        # visual real del producto en vez de una card vacía.
        if not media_files:
            shot = await self._capture_screenshot(url)
            if shot.get("blocked"):
                raise RuntimeError(f"Página bloqueada por protección anti-bot ({shot['block_reason']})")
            if shot["screenshot_path"]:
                media_files = [{"path":str(shot["screenshot_path"]),"file_type":"image",
                               "mime_type":"image/png","carousel_index":0}]
                title = title or shot["title"] or "Producto MercadoLibre"

        return PageResult(url=url, source_type="mercadolibre", title=title or "Producto MercadoLibre",
                          description=f"Precio: ${price}" if price else "", media_files=media_files,
                          metadata={"price":price})


class NewsExtractor(BaseExtractor):
    async def extract(self, url):
        # Screenshot real primero — funciona incluso si el sitio necesita JS
        # y da una imagen de portada consistente para la card.
        shot = await self._capture_screenshot(url)
        if shot.get("blocked"):
            raise RuntimeError(f"Página bloqueada por protección anti-bot ({shot['block_reason']})")

        # El texto completo del artículo (para búsqueda full-text) sigue
        # viniendo de httpx + readability, aparte de la descripción corta
        # que ya trae el screenshot desde el meta tag.
        text = ""
        fallback_title = ""
        fallback_desc = ""
        fallback_media = []
        try:
            html = await self._fetch_html(url)
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            if not shot["title"]:
                for prop in ["og:title","twitter:title"]:
                    tag = soup.find("meta",property=prop) or soup.find("meta",attrs={"name":prop})
                    if tag and tag.get("content"): fallback_title=tag["content"]; break
                if not fallback_title and soup.find("h1"):
                    fallback_title = soup.find("h1").get_text(strip=True)
            if not shot["description"]:
                for prop in ["og:description","description"]:
                    tag = soup.find("meta",property=prop) or soup.find("meta",attrs={"name":prop})
                    if tag and tag.get("content"): fallback_desc=tag["content"]; break
            try:
                from readability import Document
                text = _truncate_text(BeautifulSoup(Document(html).summary(),"html.parser").get_text("\n",strip=True))
            except ImportError:
                for tag in soup.find_all(["article","main"]):
                    t = tag.get_text("\n",strip=True)
                    if len(t) > len(text): text = t
                text = _truncate_text(text)
            if not shot["screenshot_path"]:
                fallback_media = self._download_og_image(html, "news_preview.jpg")
        except Exception as exc:
            logger.debug("Fallback fetch falló para %s: %s", url[:80], exc)

        title = (shot["title"] or fallback_title or urlparse(url).netloc)[:300]
        description = (shot["description"] or fallback_desc)[:500]
        media_files = (
            [{"path":str(shot["screenshot_path"]),"file_type":"image",
              "mime_type":"image/png","carousel_index":0}]
            if shot["screenshot_path"] else fallback_media
        )

        return PageResult(url=url, source_type="news", title=title,
                          description=description, content_text=text, media_files=media_files,
                          metadata={"domain":urlparse(url).netloc,
                                    "capture_method":"screenshot" if shot["screenshot_path"] else "og_image"})


class GenericExtractor(BaseExtractor):
    async def extract(self, url):
        # Screenshot real primero — cubre sitios que necesitan JS para
        # renderizar (apps web, landing pages, cursos online, etc.)
        shot = await self._capture_screenshot(url)
        if shot.get("blocked"):
            raise RuntimeError(f"Página bloqueada por protección anti-bot ({shot['block_reason']})")

        title, text = "", ""
        fallback_media = []
        try:
            html = await self._fetch_html(url)
            try:
                from readability import Document; doc=Document(html)
                if not shot["title"]: title=doc.title() or ""
                try:
                    from bs4 import BeautifulSoup
                    text=_truncate_text(BeautifulSoup(doc.summary(),"html.parser").get_text("\n",strip=True))
                except: pass
            except ImportError:
                try:
                    from bs4 import BeautifulSoup; soup=BeautifulSoup(html,"html.parser")
                    if not shot["title"]: title=soup.title.string if soup.title else ""
                    text=_truncate_text(soup.get_text("\n",strip=True))
                except: pass
            if not shot["screenshot_path"]:
                fallback_media = self._download_og_image(html, "preview.jpg")
        except Exception as exc:
            logger.debug("Fallback fetch falló para %s: %s", url[:80], exc)

        final_title = (shot["title"] or title or urlparse(url).netloc)[:300]
        final_desc  = shot["description"][:500] if shot["description"] else ""
        media_files = (
            [{"path":str(shot["screenshot_path"]),"file_type":"image",
              "mime_type":"image/png","carousel_index":0}]
            if shot["screenshot_path"] else fallback_media
        )

        return PageResult(url=url, source_type="generic", title=final_title,
                          description=final_desc, content_text=text, media_files=media_files,
                          metadata={"domain":urlparse(url).netloc,
                                    "capture_method":"screenshot" if shot["screenshot_path"] else "og_image"})


class InstagramProfileExtractor(BaseExtractor):
    """
    Rechaza explícitamente URLs de perfil de Instagram (instagram.com/usuario)
    en vez de capturarlas como si fueran contenido — sin sesión, lo único que
    hay para ver es el muro de login de Instagram, que no aporta nada útil
    archivado y antes quedaba guardado como si fuera una card real.
    """
    async def extract(self, url):
        raise RuntimeError(
            "Unsupported URL: es un perfil de Instagram completo, no un post "
            "individual. Instagram Archiver solo soporta URLs de posts/reels "
            "específicos (instagram.com/p/..., instagram.com/reel/...)."
        )


class UniversalDownloader:
    def __init__(self, dest_dir):
        self.dest_dir = Path(dest_dir).resolve()
    def get_extractor(self, url):
        extractors = {
            "youtube":YouTubeExtractor,"tiktok":TikTokExtractor,
            "twitter":TwitterExtractor,"linkedin":LinkedInExtractor,
            "github":GitHubExtractor,"mercadolibre":MercadoLibreExtractor,
            "news":NewsExtractor,"generic":GenericExtractor,
            "instagram_profile":InstagramProfileExtractor,
        }
        return extractors.get(URLClassifier.classify(url), GenericExtractor)(self.dest_dir)
    async def extract(self, url):
        if not URLClassifier.is_valid_http(url): raise ValueError(f"URL no válida: '{url}'")
        return await self.get_extractor(url).extract(url)
    @staticmethod
    def classify(url): return URLClassifier.classify(url)
