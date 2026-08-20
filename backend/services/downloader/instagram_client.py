"""
Instagram API Client - Detección real de PolarisErrorRoute y Posts Inexistentes.
"""

import re
import json
import urllib.parse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List
import httpx

logger = logging.getLogger(__name__)


class InstagramFetchError(Exception):
    def __init__(self, message: str, reason: str = "fetch_failed", retry_after_s: Optional[float] = None):
        super().__init__(message)
        self.reason = reason
        self.retry_after_s = retry_after_s


class PostMetadata:
    def __init__(
        self,
        shortcode: str,
        author: str,
        full_name: str = "",
        is_private: bool = False,
        is_verified: bool = False,
        profile_pic_url: Optional[str] = None,
        post_type: str = "image",
        caption: str = "",
        hashtags: Optional[List[str]] = None,
        mentions: Optional[List[str]] = None,
        location_name: Optional[str] = None,
        location_lat: Optional[float] = None,
        location_lng: Optional[float] = None,
        like_count: int = 0,
        comment_count: int = 0,
        media_items: Optional[List[Dict[str, Any]]] = None,
        original_url: str = "",
        posted_at: Optional[datetime] = None,
        raw: Optional[Dict[str, Any]] = None,
    ):
        self.shortcode = shortcode
        self.author = author or "instagram_user"
        self.full_name = full_name or self.author
        self.is_private = is_private
        self.is_verified = is_verified
        self.profile_pic_url = profile_pic_url
        self.post_type = post_type
        self.caption = caption or ""
        self.hashtags = hashtags or []
        self.mentions = mentions or []
        self.location_name = location_name
        self.location_lat = location_lat
        self.location_lng = location_lng
        self.like_count = like_count
        self.comment_count = comment_count
        self.media_items = media_items or []
        self.media_count = len(self.media_items)
        self.original_url = original_url
        self.posted_at = posted_at or datetime.now(timezone.utc)
        self.raw = raw or {}


class InstagramClient:
    DESKTOP_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Upgrade-Insecure-Requests": "1",
    }

    # Firmas tanto en texto plano como en el bundle de React (Polaris)
    NOT_FOUND_SIGNATURES = [
        "polariserrorroute",
        "polariserrorroot",
        "polarisnullstate",
        "polarisgenericerror",
        "esta página no está disponible",
        "esta pagina no esta disponible",
        "se haya eliminado la página",
        "se haya eliminado la pagina",
        "sorry, this page isn't available",
        "this page isn't available",
        "page not found",
        "content not found",
        "the link you followed may be broken",
    ]

    def __init__(self, session_cookie: Optional[str] = None):
        self.session_cookie = (session_cookie or "").strip()
        self.sessionid = self.session_cookie
        self._username: Optional[str] = None
        self._user_id: Optional[str] = None
        self._session_state = "active" if self.session_cookie else "anonymous"
        self._last_login_error: Optional[str] = None
        self._last_fetch_reason: Optional[str] = None
        self._last_fetch_evidence: Optional[str] = None
        self._config_file = Path("data/instagram_session.json")

        if self.session_cookie:
            self._extract_user_id()

    def _extract_user_id(self):
        try:
            decoded = urllib.parse.unquote(self.session_cookie)
            parts = decoded.split(":")
            if parts and parts[0].isdigit():
                self._user_id = parts[0]
        except Exception:
            pass

    @staticmethod
    def sanitize_url(url: str) -> str:
        m = re.search(r"(?:/p/|/reel/|/reels/|/tv/)([A-Za-z0-9_-]+)", url)
        if m:
            shortcode = m.group(1)
            prefix = "reel" if "/reel/" in url or "/reels/" in url else "p"
            return f"https://www.instagram.com/{prefix}/{shortcode}/"
        return url.split("?")[0]

    @staticmethod
    def extract_shortcode(url: str) -> Optional[str]:
        m = re.search(r"(?:/p/|/reel/|/reels/|/tv/)([A-Za-z0-9_-]+)", url)
        return m.group(1) if m else None

    async def initialize(self):
        try:
            if self._config_file.exists():
                data = json.loads(self._config_file.read_text(encoding="utf-8"))
                sid = data.get("sessionid") or ""
                uname = data.get("username")
                if sid:
                    self.session_cookie = sid.strip()
                    self.sessionid = self.session_cookie
                    self._username = uname or ("Thegn0m3" if "8006278546" in sid else "Usuario")
                    self._session_state = "active"
                    self._extract_user_id()
                    logger.info(f"[IG_SESSION] Sesión activa: @{self._username}")
                    return
        except Exception as e:
            logger.warning(f"[IG_SESSION] Error cargando sesión: {e}")
        logger.info(f"[IG_SESSION] Modo: {self._session_state}")

    async def check_login_status(self) -> Dict[str, Any]:
        has_sid = bool(self.session_cookie)
        display_user = self._username or (f"ID: {self._user_id}" if self._user_id else "Instagram User")
        return {
            "logged_in": has_sid,
            "username": display_user if has_sid else None,
            "state": "active" if has_sid else "anonymous",
            "message": f"Conectado como @{display_user}" if has_sid else "Modo Anónimo / Sin sesión",
            "last_fetch_reason": self._last_fetch_reason,
            "last_fetch_evidence": self._last_fetch_evidence,
        }

    async def login_with_sessionid(self, username: str, sessionid: str) -> bool:
        clean_sid = (sessionid or "").strip()
        if not clean_sid:
            self._last_login_error = "El sessionid no puede estar vacío."
            return False

        self.session_cookie = clean_sid
        self.sessionid = clean_sid
        self._session_state = "active"
        self._extract_user_id()
        self._username = "Thegn0m3" if self._user_id == "8006278546" else (f"Usuario_{self._user_id}" if self._user_id else "Instagram_User")

        try:
            self._config_file.parent.mkdir(parents=True, exist_ok=True)
            self._config_file.write_text(
                json.dumps({
                    "sessionid": self.session_cookie,
                    "username": self._username,
                    "user_id": self._user_id
                }, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[IG_SESSION] Error guardando sesión: {e}")

        logger.info(f"[IG_SESSION] Sesión iniciada para @{self._username}")
        return True

    async def logout(self):
        self.session_cookie = ""
        self.sessionid = ""
        self._username = None
        self._user_id = None
        self._session_state = "anonymous"
        if self._config_file.exists():
            try:
                self._config_file.unlink(missing_ok=True)
            except Exception:
                pass

    def _is_polaris_not_found(self, html_text: str) -> bool:
        low = html_text.lower()
        return any(sig in low for sig in self.NOT_FOUND_SIGNATURES)

    async def fetch_post_metadata(self, url: str) -> PostMetadata:
        shortcode = self.extract_shortcode(url)
        if not shortcode:
            raise InstagramFetchError(f"No se pudo extraer shortcode de: {url}", reason="invalid_url")

        clean_url = self.sanitize_url(url)
        cookies = {"sessionid": self.session_cookie} if self.session_cookie else {}

        # ── 1. Inspección de HTML / Polaris SPA ──
        try:
            async with httpx.AsyncClient(headers=self.DESKTOP_HEADERS, cookies=cookies, timeout=12.0, follow_redirects=True) as client:
                res = await client.get(clean_url)
                if res.status_code == 404 or self._is_polaris_not_found(res.text):
                    logger.info(f"[IG_CHECK] Post {shortcode} detectado como NO DISPONIBLE (PolarisErrorRoute).")
                    self._last_fetch_reason = "not_found"
                    raise InstagramFetchError(
                        f"La publicación {shortcode} no existe o fue eliminada de Instagram.",
                        reason="not_found"
                    )
        except InstagramFetchError:
            raise
        except Exception as e:
            logger.debug(f"[IG_CHECK] Probe status: {e}")

        # ── 2. Extractor Seguro con yt-dlp ──
        try:
            import asyncio
            import json as pyjson
            cmd = ["yt-dlp", "--dump-json", "--no-warnings", "--no-check-certificates", clean_url]
            if self.session_cookie:
                cmd.extend(["--add-header", f"Cookie:sessionid={self.session_cookie}"])

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0 and stdout:
                info = pyjson.loads(stdout.decode(errors="ignore"))
                logger.info(f"[IG_SAFE] Post {shortcode} extraído con éxito")
                return self._parse_ytdlp_item(info, shortcode, clean_url)
            else:
                err_msg = stderr.decode(errors="ignore").lower() if stderr else ""
                if any(w in err_msg for w in ["not found", "deleted", "unavailable", "404", "no existe", "private account", "login required"]):
                    logger.info(f"[IG_SAFE] yt-dlp confirmó que {shortcode} no está disponible.")
                    self._last_fetch_reason = "not_found"
                    raise InstagramFetchError(
                        f"La publicación {shortcode} no está disponible (eliminada o privada).",
                        reason="not_found"
                    )
        except InstagramFetchError:
            raise
        except Exception as e:
            logger.warning(f"[IG_SAFE] yt-dlp fallo: {e}")

        # ── 3. Fallback Embed ──
        try:
            async with httpx.AsyncClient(headers=self.DESKTOP_HEADERS, cookies=cookies, timeout=12.0, follow_redirects=True) as client:
                res = await client.get(f"https://www.instagram.com/p/{shortcode}/embed/captioned/")
                if res.status_code == 404 or self._is_polaris_not_found(res.text):
                    self._last_fetch_reason = "not_found"
                    raise InstagramFetchError(
                        f"La publicación {shortcode} no existe o fue eliminada.",
                        reason="not_found"
                    )
                if res.status_code == 200:
                    html = res.text
                    img_match = re.search(r'class="EmbeddedMediaImage" src="([^"]+)"', html)
                    if img_match:
                        img_url = img_match.group(1).replace("&amp;", "&")
                        return PostMetadata(
                            shortcode=shortcode,
                            author="instagram_user",
                            full_name="Instagram User",
                            post_type="image",
                            caption="",
                            media_items=[{"is_video": False, "url": img_url}],
                            original_url=clean_url,
                            posted_at=datetime.now(timezone.utc),
                        )
        except InstagramFetchError:
            raise
        except Exception as e:
            logger.warning(f"[IG_SAFE] Embed fallo: {e}")

        # En caso de no poder extraerlo:
        self._last_fetch_reason = "not_found"
        raise InstagramFetchError(
            f"La publicación {shortcode} no está disponible en Instagram.",
            reason="not_found"
        )

    def _parse_ytdlp_item(self, info: Dict[str, Any], shortcode: str, url: str) -> PostMetadata:
        media_items = []
        is_video = info.get("ext") == "mp4" or "video" in str(info.get("format", "")).lower()
        post_type = "video" if is_video else "image"

        entries = info.get("entries")
        if entries:
            post_type = "carousel"
            for entry in entries:
                v = entry.get("ext") == "mp4" or "video" in str(entry.get("format", "")).lower()
                m_url = entry.get("url") or entry.get("thumbnail")
                if m_url:
                    media_items.append({
                        "is_video": v,
                        "url": m_url,
                        "width": entry.get("width"),
                        "height": entry.get("height")
                    })
        elif is_video and info.get("url"):
            media_items.append({
                "is_video": True,
                "url": info["url"],
                "width": info.get("width"),
                "height": info.get("height")
            })
        elif info.get("url") or info.get("thumbnail"):
            media_items.append({
                "is_video": False,
                "url": info.get("url") or info.get("thumbnail"),
                "width": info.get("width"),
                "height": info.get("height")
            })

        caption = info.get("description") or info.get("title") or ""
        hashtags = info.get("tags") or re.findall(r"#(\w+)", caption)

        return PostMetadata(
            shortcode=shortcode,
            author=info.get("uploader") or info.get("channel") or "instagram_user",
            full_name=info.get("uploader_id") or info.get("uploader") or "",
            post_type=post_type,
            caption=caption,
            hashtags=hashtags,
            like_count=info.get("like_count", 0),
            comment_count=info.get("comment_count", 0),
            media_items=media_items,
            original_url=url,
            posted_at=datetime.now(timezone.utc),
            raw=info,
        )
