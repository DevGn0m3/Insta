"""
Instagram Client v6.0
Integración multi-método:
  1. API Móvil Nativa de Instagram (i.instagram.com/api/v1/media/{media_id}/info/) - Máxima estabilidad con sessionid
  2. Extractor HTML directo de página / Embed
  3. GraphQL POST Fallback
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from backend.config import config

logger = logging.getLogger(__name__)

SHORTCODE_RE = re.compile(r"instagram\.com/(?:p|reel|tv|stories)/([A-Za-z0-9_\-]+)")
IG_APP_ID = "936619743392459"
GRAPHQL_URL = "https://www.instagram.com/graphql/query"
DOC_ID = "8845758582119845"


class InstagramFetchError(RuntimeError):
    def __init__(
        self,
        message: str,
        reason: str = "unknown",
        retry_after_s: Optional[float] = None,
    ):
        super().__init__(message)
        self.reason = reason
        self.retry_after_s = retry_after_s


@dataclass
class PostMetadata:
    shortcode: str
    author: str
    full_name: str
    is_private: bool
    is_verified: bool
    post_type: str
    caption: str
    hashtags: list
    mentions: list
    location_name: Optional[str]
    location_lat: Optional[float]
    location_lng: Optional[float]
    like_count: int
    comment_count: int
    media_count: int
    posted_at: Optional[datetime]
    original_url: str
    media_items: list
    profile_pic_url: str
    raw: dict


def shortcode_to_media_id(shortcode: str) -> int:
    """Convierte el shortcode de Instagram (alfanumérico) al Media ID numérico."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    media_id = 0
    for char in shortcode:
        media_id = media_id * 64 + alphabet.index(char)
    return media_id


def _mobile_headers(cookies: Optional[dict] = None) -> dict:
    csrf = (cookies or {}).get("csrftoken", "")
    return {
        "User-Agent": "Instagram 275.0.0.27.98 Android (33/13; 420dpi; 1080x2400; samsung; SM-G991B; o1s; exynos2100; es_ES; 458229239)",
        "Accept": "*/*",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "X-IG-App-ID": IG_APP_ID,
        "X-CSRFToken": csrf,
    }


def _browser_headers(cookies: Optional[dict] = None) -> dict:
    csrf = (cookies or {}).get("csrftoken", "")
    return {
        "accept": "*/*",
        "accept-language": "es-ES,es;q=0.9,en;q=0.8",
        "cache-control": "no-cache",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://www.instagram.com",
        "pragma": "no-cache",
        "referer": "https://www.instagram.com/",
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "x-asbd-id": "129477",
        "x-csrftoken": csrf,
        "x-ig-app-id": IG_APP_ID,
        "x-ig-www-claim": "0",
        "x-requested-with": "XMLHttpRequest",
    }


def _retry_after_seconds(resp: httpx.Response) -> Optional[float]:
    raw = resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(1.0, min(float(raw), 3600.0))
    except (TypeError, ValueError):
        return None


def _response_evidence(resp: httpx.Response) -> str:
    body = (resp.text or "")[:8000].lower()
    try:
        final_url = str(resp.url or "").lower()
    except RuntimeError:
        final_url = ""
    if re.search(r'"(?:is_private|private)"\s*:\s*true', body):
        return "private_signal"
    if "checkpoint" in body or "challenge_required" in body or "challenge" in final_url:
        return "checkpoint"
    if "login" in final_url or "/accounts/login" in body:
        return "login_redirect"
    if any(marker in body for marker in (
        "please wait a few minutes", "rate limit", "too many requests",
        "temporarily blocked", "try again later",
    )):
        return "rate_signal"
    if resp.status_code >= 500:
        return "server_error"
    if resp.status_code in (401, 403, 404, 429):
        return f"http_{resp.status_code}"
    return "status_only"


class InstagramClient:
    def __init__(self) -> None:
        self._logged_in = False
        self._loader = None
        self._session_cookies: Optional[dict] = None
        self._username: Optional[str] = None
        self._session_state: str = "no_session"
        self._last_fetch_reason: str = ""
        self._last_fetch_evidence: str = ""
        self._last_request_time: float = 0.0
        self._last_login_error: str = ""
        self._browser_context = None
        self._browser_probe_page = None
        self._last_browser_probe: dict = {}

    def _session_file(self) -> Path:
        from backend.config import config
        return config.data_dir / "instagram_session.json"

    def _has_session_cookie(self) -> bool:
        return bool(self._session_cookies and self._session_cookies.get("sessionid"))

    @staticmethod
    def _safe_error(exc: Exception, *secrets: str) -> str:
        text = str(exc)
        for secret in secrets:
            if secret:
                text = text.replace(secret, "[redacted]")
        return text[:2000]

    def _record_fetch_state(
        self,
        reason: str,
        shortcode: str,
        method: str,
        http_status: Optional[int] = None,
    ) -> None:
        self._last_fetch_reason = reason
        status_suffix = f" http_status={http_status}" if http_status is not None else ""
        username = self._username or "desconocido"
        if reason == "invalid_session":
            self._logged_in = False
            self._session_state = "possibly_expired"
            logger.warning(
                "[IG_SESSION] state=possibly_expired reason=invalid_session method=%s shortcode=%s username=%s%s",
                method, shortcode, username, status_suffix,
            )
        elif reason == "access_denied":
            logger.warning(
                "[IG_FETCH] state=%s reason=access_denied method=%s shortcode=%s username=%s auth=%s%s",
                self._session_state, method, shortcode, username,
                "session_cookie_present" if self._has_session_cookie() else "anonymous",
                status_suffix,
            )
        elif reason == "rate_limited":
            logger.warning(
                "[IG_FETCH] state=%s reason=rate_limited method=%s shortcode=%s%s",
                self._session_state, method, shortcode, status_suffix,
            )
        else:
            logger.info(
                "[IG_FETCH] state=%s reason=%s method=%s shortcode=%s%s",
                self._session_state, reason, method, shortcode, status_suffix,
            )

    async def initialize(self) -> None:
        session_file = self._session_file()
        if session_file.exists():
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
                cookies = data.get("cookies") or {}
                self._session_cookies = cookies if cookies.get("sessionid") else None
                self._username = data.get("username")
                self._logged_in = self._has_session_cookie()
                if self._logged_in:
                    self._session_state = "active"
                    logger.info(
                        "[IG_SESSION] state=active source=persisted username=%s",
                        self._username or "desconocido",
                    )
            except Exception as exc:
                self._session_cookies = None
                self._username = None
                self._logged_in = False
                self._session_state = "no_session"
                logger.warning("[IG_SESSION] state=no_session restore_failed error=%s", exc)
        if not self._logged_in:
            self._session_state = "no_session"
            logger.info("[IG_SESSION] state=no_session mode=anonymous")

    @staticmethod
    def extract_shortcode(url: str) -> Optional[str]:
        url = url.strip().rstrip("/")
        m = SHORTCODE_RE.search(url)
        if m:
            return m.group(1)
        if re.fullmatch(r"[A-Za-z0-9_\-]{6,15}", url):
            return url
        return None

    async def _human_delay(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        min_config = config.downloader.min_delay_between_requests_s
        max_config = config.downloader.max_delay_between_requests_s
        try:
            from backend.services.settings_service import get as get_setting
            min_config = float(get_setting("min_delay_between_requests_s"))
            max_config = float(get_setting("max_delay_between_requests_s"))
        except Exception:
            pass
        min_delay_s = max(5.0, min_config)
        max_delay_s = max(min_delay_s, 10.0, max_config)
        min_delay = random.uniform(min_delay_s, max_delay_s)
        if elapsed < min_delay:
            await asyncio.sleep(min_delay - elapsed)
        self._last_request_time = time.monotonic()

    async def fetch_post_metadata(self, url: str) -> PostMetadata:
        shortcode = self.extract_shortcode(url)
        if not shortcode:
            raise ValueError(f"No se puede extraer shortcode de: {url}")

        self._last_fetch_reason = ""
        self._last_fetch_evidence = ""
        await self._human_delay()

        # 1. Intentar API Móvil Oficial (la más confiable y resistente)
        try:
            data = await self._mobile_api_fetch(shortcode)
            if data:
                self._last_fetch_reason = ""
                return self._parse_graphql(data, url, shortcode)
        except Exception as exc:
            logger.debug("Mobile API fetch falló: %s", exc)

        # 2. Intentar HTML directo
        try:
            data = await self._html_page_fetch(shortcode)
            if data:
                self._last_fetch_reason = ""
                return self._parse_graphql(data, url, shortcode)
        except Exception as exc:
            logger.debug("HTML fetch falló: %s", exc)

        # 3. Intentar GraphQL
        try:
            data = await self._graphql_fetch(shortcode)
            if data:
                self._last_fetch_reason = ""
                return self._parse_graphql(data, url, shortcode)
        except Exception as exc:
            logger.debug("GraphQL falló: %s", exc)

        final_reason = self._last_fetch_reason or "access_denied"
        raise InstagramFetchError(
            f"Instagram rechazó la solicitud para el post {shortcode}.",
            reason=final_reason
        )

    async def _mobile_api_fetch(self, shortcode: str) -> Optional[dict]:
        try:
            media_id = shortcode_to_media_id(shortcode)
        except Exception:
            return None

        url = f"https://i.instagram.com/api/v1/media/{media_id}/info/"
        headers = _mobile_headers(self._session_cookies)

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=25,
            headers=headers,
            cookies=self._session_cookies,
        ) as client:
            resp = await client.get(url)

        self._last_request_time = time.monotonic()
        self._last_fetch_evidence = _response_evidence(resp)

        if resp.status_code == 200:
            try:
                data = resp.json()
                items = data.get("items", [])
                if items:
                    logger.info("[IG_HTTP] method=mobile_api shortcode=%s media_id=%s status=200", shortcode, media_id)
                    return self._normalize_mobile_item(items[0], shortcode)
            except Exception as exc:
                logger.debug("Error parseando respuesta mobile: %s", exc)
        else:
            logger.info("[IG_HTTP] method=mobile_api shortcode=%s status=%s", shortcode, resp.status_code)

        return None

    def _normalize_mobile_item(self, item: dict, shortcode: str) -> dict:
        user = item.get("user", {})
        carousel = item.get("carousel_media", [])
        caption_text = (item.get("caption") or {}).get("text", "")
        
        media_items = []
        if carousel:
            for idx, c in enumerate(carousel):
                is_vid = c.get("media_type") == 2
                candidates = (c.get("video_versions") if is_vid else (c.get("image_versions2") or {}).get("candidates")) or []
                url = candidates[0].get("url") if candidates else ""
                media_items.append({
                    "url": url,
                    "is_video": is_vid,
                    "width": candidates[0].get("width") if candidates else 1080,
                    "height": candidates[0].get("height") if candidates else 1080,
                })
        else:
            is_vid = item.get("media_type") == 2
            candidates = (item.get("video_versions") if is_vid else (item.get("image_versions2") or {}).get("candidates")) or []
            url = candidates[0].get("url") if candidates else ""
            media_items.append({
                "url": url,
                "is_video": is_vid,
                "width": candidates[0].get("width") if candidates else 1080,
                "height": candidates[0].get("height") if candidates else 1080,
            })

        children_edges = [
            {
                "node": {
                    "is_video": m["is_video"],
                    "display_url": m["url"],
                    "video_url": m["url"] if m["is_video"] else None,
                    "dimensions": {"width": m["width"], "height": m["height"]},
                }
            }
            for m in media_items
        ] if len(media_items) > 1 else None

        return {
            "shortcode": shortcode,
            "display_url": media_items[0]["url"] if media_items else "",
            "is_video": item.get("media_type") == 2,
            "video_url": media_items[0]["url"] if (item.get("media_type") == 2 and media_items) else None,
            "owner": {
                "username": user.get("username", "instagram_user"),
                "full_name": user.get("full_name", user.get("username", "")),
                "is_private": user.get("is_private", False),
                "is_verified": user.get("is_verified", False),
                "profile_pic_url": user.get("profile_pic_url", ""),
            },
            "edge_media_to_caption": {"edges": [{"node": {"text": caption_text}}]},
            "edge_media_to_comment": {"count": item.get("comment_count", 0)},
            "edge_liked_by": {"count": item.get("like_count", 0)},
            "taken_at_timestamp": item.get("taken_at", int(time.time())),
            "dimensions": {"width": 1080, "height": 1080},
            "edge_sidecar_to_children": {"edges": children_edges} if children_edges else None,
        }

    async def _html_page_fetch(self, shortcode: str) -> Optional[dict]:
        url = f"https://www.instagram.com/p/{shortcode}/"
        headers = _browser_headers(self._session_cookies)
        headers["accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=25,
            headers=headers,
            cookies=self._session_cookies,
        ) as client:
            resp = await client.get(url)

        self._last_request_time = time.monotonic()
        if resp.status_code == 200:
            patterns = [
                r'<script type="application/json" data-sjs>(\{.*?"xdt_shortcode_media".*?\})</script>',
                r'<script type="application/json" data-sjs>(\{.*?"shortcode_media".*?\})</script>',
            ]
            for pat in patterns:
                m = re.search(pat, resp.text)
                if m:
                    try:
                        data = json.loads(m.group(1))
                        media = (
                            data.get("data", {}).get("xdt_shortcode_media")
                            or data.get("data", {}).get("shortcode_media")
                        )
                        if media:
                            return media
                    except Exception:
                        pass
        return None

    async def _graphql_fetch(self, shortcode: str) -> Optional[dict]:
        headers = _browser_headers(self._session_cookies)
        payload = {
            "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": "PolarisPostActionLoadPostQueryQuery",
            "variables": json.dumps({
                "shortcode": shortcode,
                "fetch_tagged_user_count": None,
                "hoisted_comment_id": None,
                "hoisted_reply_id": None,
            }),
            "server_timestamps": "true",
            "doc_id": DOC_ID,
        }
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=25,
            headers=headers,
            cookies=self._session_cookies,
        ) as client:
            resp = await client.post(GRAPHQL_URL, data=payload)

        self._last_request_time = time.monotonic()
        self._last_fetch_evidence = _response_evidence(resp)

        if resp.status_code == 200 and "application/json" in resp.headers.get("content-type", ""):
            body = resp.json()
            return body.get("data", {}).get("xdt_shortcode_media") or body.get("data", {}).get("shortcode_media")
        return None

    def _parse_graphql(self, media: dict, url: str, shortcode: str) -> PostMetadata:
        owner = media.get("owner", {})
        caption_edges = media.get("edge_media_to_caption", {}).get("edges", [])
        caption = caption_edges[0]["node"]["text"] if caption_edges else ""
        hashtags = re.findall(r"#([A-Za-z0-9_ñáéíóú]+)", caption)
        mentions = re.findall(r"@([A-Za-z0-9_.]+)", caption)
        timestamp = media.get("taken_at_timestamp")
        posted_at = datetime.fromtimestamp(timestamp, tz=timezone.utc) if timestamp else None

        sidecar = media.get("edge_sidecar_to_children", {})
        media_items = []
        if sidecar and sidecar.get("edges"):
            for edge in sidecar["edges"]:
                node = edge.get("node", {})
                is_vid = node.get("is_video", False)
                media_items.append({
                    "url": node.get("video_url") if is_vid else node.get("display_url"),
                    "is_video": is_vid,
                    "width": node.get("dimensions", {}).get("width"),
                    "height": node.get("dimensions", {}).get("height"),
                })
        else:
            is_vid = media.get("is_video", False)
            media_items.append({
                "url": media.get("video_url") if is_vid else media.get("display_url"),
                "is_video": is_vid,
                "width": media.get("dimensions", {}).get("width"),
                "height": media.get("dimensions", {}).get("height"),
            })

        post_type = "carousel" if len(media_items) > 1 else ("video" if media.get("is_video") else "image")

        return PostMetadata(
            shortcode=shortcode,
            author=owner.get("username", "instagram_user"),
            full_name=owner.get("full_name", ""),
            is_private=owner.get("is_private", False),
            is_verified=owner.get("is_verified", False),
            post_type=post_type,
            caption=caption,
            hashtags=hashtags,
            mentions=mentions,
            location_name=media.get("location", {}).get("name") if media.get("location") else None,
            location_lat=media.get("location", {}).get("lat") if media.get("location") else None,
            location_lng=media.get("location", {}).get("lng") if media.get("location") else None,
            like_count=media.get("edge_liked_by", {}).get("count", 0),
            comment_count=media.get("edge_media_to_comment", {}).get("count", 0),
            media_count=len(media_items),
            posted_at=posted_at,
            original_url=url,
            media_items=media_items,
            profile_pic_url=owner.get("profile_pic_url", ""),
            raw=media,
        )

    async def login_with_sessionid(self, username: str, sessionid: str) -> bool:
        sessionid = sessionid.strip()
        username = username.strip().lstrip("@")
        cookies = {"sessionid": sessionid, "ds_user_id": username}
        self._session_cookies = cookies
        self._username = username
        self._logged_in = True
        self._session_state = "active"
        try:
            self._session_file().write_text(
                json.dumps({"cookies": cookies, "username": self._username}),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("No se pudo persistir la sesión: %s", exc)
        logger.info("[IG_SESSION] state=active method=sessionid username=%s", self._username)
        return True

    async def check_login_status(self) -> dict:
        return {
            "logged_in": self._logged_in and self._session_state == "active",
            "username": self._username,
            "state": self._session_state,
            "method": "API Móvil + Web Session",
        }