"""
Instagram Client v5.0
Usa el endpoint GraphQL público de Instagram para posts públicos.
Si hay login real (usuario/contraseña vía instaloader), aplica las
cookies de sesión resultantes a cada request — permite acceder también
a contenido que requiere estar logueado. Sin login, funciona en modo
anónimo (solo posts públicos).
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from backend.config import config

logger = logging.getLogger(__name__)


class InstagramFetchError(RuntimeError):
    """
    Error de fetch con motivo clasificado, para que download_manager pueda
    decidir si reintentar, aplicar cooldown global, o marcar error inmediato
    sin gastar los 5 intentos en un post que nunca va a estar disponible.

    reason:
      - "not_found"   -> post borrado/no existe (404, media null sin error) — FATAL, no reintentar
      - "private"     -> la respuesta JSON confirma que el post es privado — FATAL
      - "access_denied" -> HTTP 403 sin prueba suficiente de privacidad; puede ser bloqueo temporal/WAF
      - "invalid_session" -> 401 o sesión invalidada — requiere importar otro sessionid
      - "redirect_to_login" -> Instagram redirigió al login — sesión posiblemente expirada
      - "rate_limited"-> 429 / "please wait" — reintentar con cooldown global
      - "unknown"     -> error ambiguo (timeout, JSON inválido, etc.) — reintentar normal
    """
    def __init__(
        self,
        message: str,
        reason: str = "unknown",
        retry_after_s: Optional[float] = None,
    ):
        super().__init__(message)
        self.reason = reason
        self.retry_after_s = retry_after_s


SHORTCODE_RE = re.compile(
    r"instagram\.com/(?:p|reel|tv|stories)/([A-Za-z0-9_\-]+)"
)

# App ID público de Instagram web — no requiere autenticación
IG_APP_ID = "936619743392459"

# GraphQL doc_id para PolarisPostActionLoadPostQueryQuery
# Este es el endpoint más estable para posts públicos
GRAPHQL_URL = "https://www.instagram.com/graphql/query"
DOC_ID      = "8845758582119845"

# Endpoint alternativo más simple (no GraphQL) — fallback
POST_INFO_URL = "https://www.instagram.com/p/{shortcode}/?__a=1&__d=dis"


@dataclass
class PostMetadata:
    shortcode:      str
    author:         str
    full_name:      str
    is_private:     bool
    is_verified:    bool
    post_type:      str
    caption:        str
    hashtags:       list
    mentions:       list
    location_name:  Optional[str]
    location_lat:   Optional[float]
    location_lng:   Optional[float]
    like_count:     int
    comment_count:  int
    media_count:    int
    posted_at:      Optional[datetime]
    original_url:   str
    media_items:    list
    profile_pic_url: str
    raw:            dict


def _retry_after_seconds(resp: httpx.Response) -> Optional[float]:
    """Lee Retry-After numérico sin confiar en valores ausentes o inválidos."""
    raw = resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(1.0, min(float(raw), 3600.0))
    except (TypeError, ValueError):
        return None


def _response_evidence(resp: httpx.Response) -> str:
    """Resume evidencia de la respuesta sin registrar su cuerpo ni secretos."""
    body = (resp.text or "")[:8000].lower()
    try:
        final_url = str(resp.url or "").lower()
    except RuntimeError:
        # httpx puede crear respuestas sintéticas sin request asociado.
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


def _browser_headers() -> dict:
    """Headers de cliente web declarados explícitamente; no ocultan automatización."""
    return {
        "accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "accept-language":           "es-AR,es;q=0.9,en;q=0.8",
        "cache-control":             "no-cache",
        "dnt":                       "1",
        "pragma":                    "no-cache",
        "sec-ch-ua":                 '"Google Chrome";v="124", "Chromium";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile":          "?0",
        "sec-ch-ua-platform":        '"Windows"',
        "sec-fetch-dest":            "document",
        "sec-fetch-mode":            "navigate",
        "sec-fetch-site":            "none",
        "sec-fetch-user":            "?1",
        "upgrade-insecure-requests": "1",
        "user-agent":                (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "x-ig-app-id":               IG_APP_ID,
        "x-requested-with":          "XMLHttpRequest",
    }


class InstagramClient:

    def __init__(self) -> None:
        self._logged_in = False
        self._loader    = None
        # FIX: antes login() era un no-op (siempre devolvía True sin hacer
        # nada) y check_login_status() siempre reportaba logged_in=True,
        # aunque NINGÚN request llevara cookies de sesión. El botón de
        # login en la UI no tenía efecto real — todos los fetches eran
        # 100% anónimos, por eso Instagram seguía bloqueando como si nunca
        # te hubieras logueado. Ahora sí se guarda una sesión real.
        self._session_cookies: Optional[dict] = None
        self._username: Optional[str] = None
        # Estado observable por la API/UI. Nunca contiene cookies ni sessionid.
        self._session_state: str = "no_session"
        self._last_fetch_reason: str = ""
        self._last_fetch_evidence: str = ""
        # Track request times para aplicar delays inteligentes
        self._last_request_time: float = 0.0
        self._last_login_error: str = ""
        # Contexto independiente y persistente para una comprobación manual
        # con navegador visible. No se mezcla con _session_cookies ni con
        # instagram_session.json; el usuario autoriza la sesión en esa ventana.
        self._browser_context = None
        self._browser_probe_page = None
        self._last_browser_probe: dict = {}

    def _session_file(self) -> Path:
        from backend.config import config
        return config.data_dir / "instagram_session.json"

    def _has_session_cookie(self) -> bool:
        """Indica si hay un sessionid cargado, sin exponer su valor."""
        return bool(self._session_cookies and self._session_cookies.get("sessionid"))

    @staticmethod
    def _safe_error(exc: Exception, *secrets: str) -> str:
        """Devuelve un error apto para logs/API, ocultando secretos conocidos."""
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
        """Actualiza el estado visible y deja un log accionable sin secretos."""
        self._last_fetch_reason = reason
        status_suffix = f" http_status={http_status}" if http_status is not None else ""
        username = self._username or "desconocido"

        if reason == "invalid_session":
            self._logged_in = False
            self._session_state = "possibly_expired"
            logger.warning(
                "[IG_SESSION] state=possibly_expired reason=invalid_session "
                "method=%s shortcode=%s username=%s%s",
                method, shortcode, username, status_suffix,
            )
        elif reason == "redirect_to_login":
            if self._has_session_cookie():
                self._session_state = "possibly_expired"
                logger.warning(
                    "[IG_SESSION] state=possibly_expired reason=redirect_to_login "
                    "method=%s shortcode=%s username=%s%s",
                    method, shortcode, username, status_suffix,
                )
            else:
                self._session_state = "no_session"
                logger.info(
                    "[IG_SESSION] state=no_session reason=redirect_to_login "
                    "method=%s shortcode=%s%s",
                    method, shortcode, status_suffix,
                )
        elif reason == "private":
            if not self._has_session_cookie():
                self._session_state = "no_session"
                logger.warning(
                    "[IG_FETCH] state=no_session reason=private method=%s "
                    "shortcode=%s auth=anonymous%s",
                    method, shortcode, status_suffix,
                )
            else:
                logger.warning(
                    "[IG_FETCH] state=%s reason=private method=%s shortcode=%s "
                    "username=%s auth=session_cookie_present%s",
                    self._session_state, method, shortcode, username, status_suffix,
                )
        elif reason == "access_denied":
            logger.warning(
                "[IG_FETCH] state=%s reason=access_denied method=%s shortcode=%s "
                "username=%s auth=%s%s",
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
        # Intentar restaurar una sesión guardada de un login anterior
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
            logger.info(
                "[IG_SESSION] state=no_session mode=anonymous "
                "Instagram Client usa GraphQL público; posts privados no son accesibles"
            )

    @staticmethod
    def extract_shortcode(url: str) -> Optional[str]:
        url = url.strip().rstrip("/")
        m = SHORTCODE_RE.search(url)
        if m:
            return m.group(1)
        if re.fullmatch(r"[A-Za-z0-9_\-]{6,15}", url):
            return url
        return None

    async def fetch_post_metadata(self, url: str) -> PostMetadata:
        shortcode = self.extract_shortcode(url)
        if not shortcode:
            raise ValueError(f"No se puede extraer shortcode de: {url}")

        self._last_fetch_reason = ""
        self._last_fetch_evidence = ""
        if not self._has_session_cookie():
            self._session_state = "no_session"
            logger.info(
                "[IG_FETCH] state=no_session mode=anonymous shortcode=%s",
                shortcode,
            )
        else:
            logger.debug(
                "[IG_FETCH] state=%s mode=session username=%s shortcode=%s",
                self._session_state, self._username or "desconocido", shortcode,
            )

        # Throttle conservador entre solicitudes al mismo cliente.
        await self._human_delay()

        # Trackeamos el motivo MÁS específico visto entre ambos intentos.
        # Prioridad: invalid_session/redirect > rate_limited > access_denied
        # > private > not_found > unknown. Un 403 del endpoint no prueba por sí
        # solo que el post sea privado, especialmente si había una sesión válida.
        reasons_seen: list[str] = []

        # Método 1: GraphQL endpoint público
        try:
            data = await self._graphql_fetch(shortcode)
            if data:
                self._last_fetch_reason = ""
                return self._parse_graphql(data, url, shortcode)
            # Un body JSON sin media no confirma privacidad ni eliminación.
            # Solo un HTTP 404 explícito se clasifica como not_found.
            reasons_seen.append("unknown")
        except InstagramFetchError as exc:
            reasons_seen.append(exc.reason)
            logger.debug("GraphQL falló (%s): %s", exc.reason, exc)
            # No insistir cuando la respuesta confirma autenticación inválida,
            # redirección, privacidad o rate limit. Un 403 se prueba una sola vez
            # con el fallback porque puede ser específico del endpoint GraphQL.
            if exc.reason in {
                "invalid_session", "redirect_to_login", "private", "rate_limited",
            }:
                self._last_fetch_reason = exc.reason
                raise
        except Exception as exc:
            reasons_seen.append("unknown")
            logger.debug("GraphQL falló: %s", exc)

        # El fallback es una segunda solicitud real: usa el mismo throttle
        # conservador que la primera, nunca un delay fijo de pocos segundos.
        await self._human_delay()

        # Método 2: endpoint ?__a=1 (más simple, menos info)
        try:
            data = await self._simple_fetch(shortcode)
            if data:
                self._last_fetch_reason = ""
                return self._parse_simple(data, url, shortcode)
            # Un body JSON sin media tampoco confirma la causa.
            reasons_seen.append("unknown")
        except InstagramFetchError as exc:
            reasons_seen.append(exc.reason)
            logger.debug("Simple fetch falló (%s): %s", exc.reason, exc)
        except Exception as exc:
            reasons_seen.append("unknown")
            logger.debug("Simple fetch falló: %s", exc)

        # Elegir el motivo más informativo de los dos intentos.
        priority = [
            "invalid_session", "redirect_to_login", "rate_limited",
            "access_denied", "private", "not_found", "unknown",
        ]
        final_reason = next((r for r in priority if r in reasons_seen), "unknown")
        # El fallback puede devolver 404 después de un 403. Conservamos el
        # motivo final más informativo para el endpoint de estado y la UI.
        self._last_fetch_reason = final_reason

        has_session = self._has_session_cookie()
        messages = {
            "rate_limited": (
                f"Instagram está aplicando rate limit para el post {shortcode}. "
                "Se reintentará automáticamente tras el cooldown."
            ),
            "invalid_session": (
                "La sesión de Instagram expiró o fue invalidada. "
                "Volvé a importar un sessionid válido."
            ),
            "redirect_to_login": (
                "Instagram redirigió la solicitud al login. "
                + (
                    "La sesión cargada posiblemente expiró o requiere verificación."
                    if has_session else
                    "No hay un sessionid cargado; el post puede requerir iniciar sesión."
                )
            ),
            "access_denied": (
                f"Instagram rechazó la solicitud para el post {shortcode} (HTTP 403). "
                + (
                    "La sesión está activa y fue enviada; este 403 no demuestra que el post sea privado. "
                    "Puede tratarse de un bloqueo temporal del endpoint, una restricción de Instagram o una respuesta WAF."
                    if has_session else
                    "No hay un sessionid cargado; la solicitud se realizó en modo anónimo."
                )
            ),
            "private": (
                f"Instagram confirmó que el post {shortcode} es privado y no permite accederlo con esta sesión."
            ),
            "not_found": (
                f"El post {shortcode} no existe o fue eliminado."
            ),
            "unknown": (
                f"No se pudo obtener el post {shortcode} (motivo no determinado). "
                "Se reintentará."
            ),
        }
        raise InstagramFetchError(messages[final_reason], reason=final_reason)

    async def _human_delay(self) -> None:
        """Aplica un intervalo conservador entre requests al mismo cliente."""
        elapsed = time.monotonic() - self._last_request_time
        min_config = config.downloader.min_delay_between_requests_s
        max_config = config.downloader.max_delay_between_requests_s
        try:
            # La UI puede guardar overrides; nunca permitimos que reduzcan
            # el throttle Instagram por debajo del mínimo conservador.
            from backend.services.settings_service import get as get_setting
            min_config = float(get_setting("min_delay_between_requests_s"))
            max_config = float(get_setting("max_delay_between_requests_s"))
        except Exception:
            pass
        min_delay_s = max(20.0, min_config)
        max_delay_s = max(min_delay_s, 30.0, max_config)
        min_delay = random.uniform(min_delay_s, max_delay_s)
        if elapsed < min_delay:
            await asyncio.sleep(min_delay - elapsed)
        self._last_request_time = time.monotonic()

    async def _graphql_fetch(self, shortcode: str) -> Optional[dict]:
        """Fetch via GraphQL POST — devuelve datos completos del post."""
        headers = _browser_headers()
        headers["content-type"] = "application/x-www-form-urlencoded"
        headers["referer"]      = f"https://www.instagram.com/p/{shortcode}/"
        headers["x-fb-friendly-name"] = "PolarisPostActionLoadPostQueryQuery"

        payload = {
            "fb_api_caller_class":    "RelayModern",
            "fb_api_req_friendly_name": "PolarisPostActionLoadPostQueryQuery",
            "variables": json.dumps({
                "shortcode":              shortcode,
                "fetch_tagged_user_count": None,
                "hoisted_comment_id":     None,
                "hoisted_reply_id":       None,
            }),
            "server_timestamps": "true",
            "doc_id":            DOC_ID,
        }

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30,
            headers=headers,
            cookies=self._session_cookies,  # FIX: antes nunca se pasaban
        ) as client:
            resp = await client.post(GRAPHQL_URL, data=payload)
        self._last_request_time = time.monotonic()
        self._last_fetch_evidence = _response_evidence(resp)
        logger.info(
            "[IG_HTTP] method=graphql shortcode=%s status=%s content_type=%s evidence=%s auth=%s",
            shortcode,
            resp.status_code,
            resp.headers.get("content-type", "unknown").split(";", 1)[0],
            _response_evidence(resp),
            "session_cookie_present" if self._has_session_cookie() else "anonymous",
        )

        if resp.status_code == 429:
            retry_after_s = _retry_after_seconds(resp)
            self._record_fetch_state("rate_limited", shortcode, "graphql", resp.status_code)
            raise InstagramFetchError(
                "Instagram indicó demasiadas solicitudes; se respetará Retry-After.",
                reason="rate_limited",
                retry_after_s=retry_after_s,
            )
        if resp.status_code == 401:
            self._record_fetch_state("invalid_session", shortcode, "graphql", resp.status_code)
            raise InstagramFetchError(
                "La sesión de Instagram expiró o fue invalidada.",
                reason="invalid_session",
            )
        if resp.status_code == 403:
            evidence = _response_evidence(resp)
            if evidence == "private_signal":
                self._record_fetch_state("private", shortcode, "graphql", resp.status_code)
                raise InstagramFetchError(
                    "Instagram indicó que el contenido es privado.", reason="private"
                )
            self._record_fetch_state("access_denied", shortcode, "graphql", resp.status_code)
            raise InstagramFetchError(
                "Instagram rechazó la solicitud (HTTP 403); no confirma privacidad.",
                reason="access_denied",
            )
        if resp.status_code == 404:
            self._record_fetch_state("not_found", shortcode, "graphql", resp.status_code)
            raise InstagramFetchError("Post no encontrado", reason="not_found")
        # Nunca clasificar un HTTP 500/503 como rate limit solo porque la
        # respuesta no sea JSON. El rate limit se confirma por 429 o por una
        # señal textual inequívoca en una respuesta HTML/JSON.
        if resp.status_code != 200:
            self._record_fetch_state("unknown", shortcode, "graphql", resp.status_code)
            raise InstagramFetchError(f"HTTP {resp.status_code}", reason="unknown")

        # Si Instagram redirige a la página de login, httpx sigue el redirect
        # (follow_redirects=True) y devuelve 200 pero con HTML, no JSON.
        ct = resp.headers.get("content-type", "")
        if "application/json" not in ct:
            final_url = str(resp.url).lower()
            body_hint = (resp.text or "")[:4000].lower()
            if "login" in final_url:
                reason = "redirect_to_login"
                message = "Instagram redirigió a login"
            elif any(marker in body_hint for marker in (
                "please wait a few minutes", "rate limit", "too many requests",
                "temporarily blocked", "try again later",
            )):
                reason = "rate_limited"
                message = "Instagram indicó una limitación temporal"
            else:
                reason = "unknown"
                message = "Instagram devolvió una respuesta no JSON"
            self._record_fetch_state(reason, shortcode, "graphql", resp.status_code)
            raise InstagramFetchError(message, reason=reason)

        try:
            body = resp.json()
        except Exception:
            raise InstagramFetchError("Respuesta no es JSON válido", reason="unknown")

        media = (
            body.get("data", {})
                .get("xdt_shortcode_media")
            or body.get("data", {})
                .get("shortcode_media")
        )
        return media

    async def _simple_fetch(self, shortcode: str) -> Optional[dict]:
        """Fetch via ?__a=1 — más simple pero menos datos."""
        url = POST_INFO_URL.format(shortcode=shortcode)
        headers = _browser_headers()
        headers["referer"] = f"https://www.instagram.com/p/{shortcode}/"

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30,
            headers=headers,
            cookies=self._session_cookies,  # FIX: antes nunca se pasaban
        ) as client:
            resp = await client.get(url)
        self._last_request_time = time.monotonic()
        self._last_fetch_evidence = _response_evidence(resp)
        logger.info(
            "[IG_HTTP] method=simple shortcode=%s status=%s content_type=%s evidence=%s auth=%s",
            shortcode,
            resp.status_code,
            resp.headers.get("content-type", "unknown").split(";", 1)[0],
            _response_evidence(resp),
            "session_cookie_present" if self._has_session_cookie() else "anonymous",
        )

        if resp.status_code == 429:
            retry_after_s = _retry_after_seconds(resp)
            self._record_fetch_state("rate_limited", shortcode, "simple", resp.status_code)
            raise InstagramFetchError(
                "Instagram indicó demasiadas solicitudes; se respetará Retry-After.",
                reason="rate_limited",
                retry_after_s=retry_after_s,
            )
        if resp.status_code == 401:
            self._record_fetch_state("invalid_session", shortcode, "simple", resp.status_code)
            raise InstagramFetchError(
                "La sesión de Instagram expiró o fue invalidada.",
                reason="invalid_session",
            )
        if resp.status_code == 403:
            evidence = _response_evidence(resp)
            if evidence == "private_signal":
                self._record_fetch_state("private", shortcode, "simple", resp.status_code)
                raise InstagramFetchError(
                    "Instagram indicó que el contenido es privado.", reason="private"
                )
            self._record_fetch_state("access_denied", shortcode, "simple", resp.status_code)
            raise InstagramFetchError(
                "Instagram rechazó la solicitud (HTTP 403); no confirma privacidad.",
                reason="access_denied",
            )
        if resp.status_code == 404:
            self._record_fetch_state("not_found", shortcode, "simple", resp.status_code)
            raise InstagramFetchError("Post no encontrado", reason="not_found")
        # Un HTTP 500/503 no es evidencia suficiente de rate limit. Se
        # registra como unknown y queda sujeto al backoff normal.
        if resp.status_code != 200:
            self._record_fetch_state("unknown", shortcode, "simple", resp.status_code)
            raise InstagramFetchError(f"HTTP {resp.status_code}", reason="unknown")

        ct = resp.headers.get("content-type", "")
        if "application/json" not in ct:
            final_url = str(resp.url).lower()
            body_hint = (resp.text or "")[:4000].lower()
            if "login" in final_url:
                reason = "redirect_to_login"
                message = "Instagram redirigió a login"
            elif any(marker in body_hint for marker in (
                "please wait a few minutes", "rate limit", "too many requests",
                "temporarily blocked", "try again later",
            )):
                reason = "rate_limited"
                message = "Instagram indicó una limitación temporal"
            else:
                reason = "unknown"
                message = "Instagram devolvió una respuesta no JSON"
            self._record_fetch_state(reason, shortcode, "simple", resp.status_code)
            raise InstagramFetchError(message, reason=reason)

        try:
            body = resp.json()
        except Exception:
            raise InstagramFetchError("Respuesta no es JSON válido", reason="unknown")

        return (
            body.get("graphql", {}).get("shortcode_media")
            or body.get("items", [{}])[0].get("media")
        )

    def _parse_graphql(self, media: dict, url: str, shortcode: str) -> PostMetadata:
        """Parsea la respuesta del GraphQL endpoint."""
        caption_edges = (
            media.get("edge_media_to_caption", {}).get("edges", [])
        )
        caption = caption_edges[0]["node"]["text"] if caption_edges else \
                  media.get("accessibility_caption", "") or ""

        hashtags = re.findall(r"#(\w+)", caption)
        mentions = re.findall(r"@(\w+)", caption)

        # Tipo de post
        typename = media.get("__typename", "")
        if typename == "XDTGraphSidecar" or media.get("edge_sidecar_to_children"):
            post_type = "carousel"
        elif media.get("is_video"):
            post_type = "reel" if "reel" in url.lower() else "video"
        else:
            post_type = "image"

        # Media items
        media_items = []
        sidecar = media.get("edge_sidecar_to_children", {}).get("edges", [])
        if sidecar:
            for edge in sidecar:
                node     = edge["node"]
                is_video = node.get("is_video", False)
                # Mejor resolución disponible
                resources = node.get("display_resources", [])
                img_url   = resources[-1]["src"] if resources else node.get("display_url", "")
                media_items.append({
                    "url":      node.get("video_url", "") if is_video else img_url,
                    "is_video": is_video,
                    "width":    resources[-1].get("config_width") if resources else None,
                    "height":   resources[-1].get("config_height") if resources else None,
                })
        else:
            is_video  = media.get("is_video", False)
            resources = media.get("display_resources", [])
            img_url   = resources[-1]["src"] if resources else media.get("display_url", "")
            media_items.append({
                "url":      media.get("video_url", "") if is_video else img_url,
                "is_video": is_video,
                "width":    resources[-1].get("config_width") if resources else None,
                "height":   resources[-1].get("config_height") if resources else None,
            })

        # Fecha
        posted_at = None
        ts = media.get("taken_at_timestamp")
        if ts:
            try:
                posted_at = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            except Exception:
                pass

        # Autor
        owner     = media.get("owner", {})
        author    = owner.get("username", "unknown")
        full_name = owner.get("full_name", "") or author

        # Likes / comments
        likes    = (media.get("edge_media_preview_like", {}).get("count")
                    or media.get("edge_liked_by", {}).get("count", 0))
        comments = (media.get("edge_media_to_comment", {}).get("count")
                    or media.get("edge_media_to_parent_comment", {}).get("count", 0))

        # Ubicación
        loc       = media.get("location") or {}
        loc_name  = loc.get("name")

        return PostMetadata(
            shortcode=shortcode,
            author=author,
            full_name=full_name,
            is_private=owner.get("is_private", False),
            is_verified=owner.get("is_verified", False),
            post_type=post_type,
            caption=caption,
            hashtags=hashtags,
            mentions=mentions,
            location_name=loc_name,
            location_lat=None,
            location_lng=None,
            like_count=likes or 0,
            comment_count=comments or 0,
            media_count=len(media_items),
            posted_at=posted_at,
            original_url=url,
            media_items=media_items,
            profile_pic_url=owner.get("profile_pic_url", ""),
            raw={"source": "graphql-public", "shortcode": shortcode,
                 "typename": typename},
        )

    def _parse_simple(self, media: dict, url: str, shortcode: str) -> PostMetadata:
        """Parsea la respuesta del endpoint simple ?__a=1."""
        caption = (
            media.get("edge_media_to_caption", {})
                 .get("edges", [{}])[0]
                 .get("node", {})
                 .get("text", "")
            or media.get("caption", {}).get("text", "")
            or ""
        )
        hashtags = re.findall(r"#(\w+)", caption)
        mentions = re.findall(r"@(\w+)", caption)

        is_video  = media.get("is_video", False)
        post_type = "reel" if "reel" in url.lower() and is_video else \
                    "video" if is_video else \
                    "carousel" if media.get("edge_sidecar_to_children") else "image"

        resources = media.get("display_resources", [])
        img_url   = resources[-1]["src"] if resources else media.get("display_url", "")
        media_items = [{
            "url":      media.get("video_url", "") if is_video else img_url,
            "is_video": is_video,
            "width":    None, "height": None,
        }]

        owner  = media.get("owner", {})
        ts     = media.get("taken_at_timestamp") or media.get("taken_at")
        posted_at = None
        if ts:
            try:
                posted_at = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            except Exception:
                pass

        return PostMetadata(
            shortcode=shortcode,
            author=owner.get("username", "unknown"),
            full_name=owner.get("full_name", ""),
            is_private=owner.get("is_private", False),
            is_verified=owner.get("is_verified", False),
            post_type=post_type,
            caption=caption, hashtags=hashtags, mentions=mentions,
            location_name=None, location_lat=None, location_lng=None,
            like_count=media.get("edge_media_preview_like", {}).get("count", 0),
            comment_count=media.get("edge_media_to_comment", {}).get("count", 0),
            media_count=len(media_items),
            posted_at=posted_at,
            original_url=url,
            media_items=media_items,
            profile_pic_url="",
            raw={"source": "simple-public", "shortcode": shortcode},
        )

    async def login(self, username: str, password: str) -> bool:
        """
        Login real usando instaloader (que hace las llamadas correctas al
        endpoint de auth de Instagram, maneja checkpoints básicos, etc.).
        Extrae las cookies de sesión resultantes y las guarda para que
        _graphql_fetch/_simple_fetch las usen en cada request — antes esto
        no pasaba nunca, por eso "estar logueado" no tenía ningún efecto.
        """
        self._last_login_error = ""
        import instaloader

        def _do_login():
            loader = instaloader.Instaloader(
                quiet=True, download_pictures=False, download_videos=False,
                save_metadata=False, download_comments=False,
            )
            loader.login(username, password)  # lanza excepción si falla
            # instaloader envuelve un requests.Session en context._session
            jar = loader.context._session.cookies
            return {c.name: c.value for c in jar}

        try:
            cookies = await asyncio.get_event_loop().run_in_executor(None, _do_login)
        except Exception as exc:
            safe_error = self._safe_error(exc, password)
            self._last_login_error = safe_error
            logger.error(
                "[IG_SESSION] state=login_failed method=password username=%s error=%s",
                username, safe_error,
            )
            return False

        if not cookies or "sessionid" not in cookies:
            self._last_login_error = "Instagram no devolvió una sesión válida."
            logger.error(
                "[IG_SESSION] state=login_failed method=password username=%s "
                "reason=no_session_cookie",
                username,
            )
            return False

        self._session_cookies = cookies
        self._username = username
        self._logged_in = True
        self._session_state = "active"
        self._last_fetch_reason = ""
        self._last_fetch_evidence = ""
        self._last_login_error = ""

        try:
            self._session_file().write_text(
                json.dumps({"cookies": cookies, "username": username}),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("No se pudo persistir la sesión de Instagram: %s", exc)

        logger.info("[IG_SESSION] state=active method=password username=%s", username)
        return True

    async def login_with_sessionid(self, username: str, sessionid: str) -> bool:
        """Inicia sesión usando un sessionid de una sesión propia ya verificada."""
        self._last_login_error = ""
        import instaloader

        def _do_login():
            loader = instaloader.Instaloader(
                quiet=True, download_pictures=False, download_videos=False,
                save_metadata=False, download_comments=False,
            )
            loader.context._session.cookies.set(
                "sessionid", sessionid, domain=".instagram.com", path="/"
            )
            verified_username = loader.test_login()
            if not verified_username:
                raise RuntimeError("El sessionid es inválido, expiró o Instagram lo rechazó.")

            jar = loader.context._session.cookies
            cookies = {c.name: c.value for c in jar}
            cookies["sessionid"] = sessionid
            return verified_username, cookies

        try:
            verified_username, cookies = await asyncio.get_event_loop().run_in_executor(
                None, _do_login
            )
        except Exception as exc:
            safe_error = self._safe_error(exc, sessionid)
            self._last_login_error = safe_error
            logger.error(
                "[IG_SESSION] state=login_failed method=sessionid username=%s error=%s",
                username, safe_error,
            )
            return False

        self._session_cookies = cookies
        self._username = verified_username or username
        self._logged_in = True
        self._session_state = "active"
        self._last_fetch_reason = ""
        self._last_fetch_evidence = ""
        self._last_login_error = ""

        try:
            self._session_file().write_text(
                json.dumps({"cookies": cookies, "username": self._username}),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("No se pudo persistir la sesión de Instagram: %s", exc)

        logger.info(
            "[IG_SESSION] state=active method=sessionid username=%s",
            self._username,
        )
        return True

    async def browser_probe(self, url: str) -> dict:
        """Abre una URL de Instagram en un navegador visible y persistente.

        Es una comprobación manual/autorizada, no un reemplazo silencioso del
        cliente HTTP: el usuario puede iniciar sesión en la ventana abierta y
        volver a pulsar el botón para repetir la prueba. No se exportan cookies,
        cuerpos HTML ni tokens al backend o a los logs.
        """
        from urllib.parse import urlparse
        parsed = urlparse((url or '').strip())
        host = (parsed.hostname or '').lower().removeprefix('www.')
        if parsed.scheme not in {'http', 'https'} or host not in {'instagram.com', 'm.instagram.com'}:
            raise ValueError('La URL debe pertenecer a instagram.com.')

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            result = {
                'state': 'unavailable', 'blocked': False,
                'message': 'Playwright no está instalado en el entorno del servidor.',
            }
            self._last_browser_probe = result
            return result

        try:
            if self._browser_context is None:
                profile_dir = config.data_dir / 'instagram_browser_profile'
                profile_dir.mkdir(parents=True, exist_ok=True)
                self._browser_playwright = await async_playwright().start()
                self._browser_context = await self._browser_playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=False,
                    viewport={'width': 1280, 'height': 900},
                )
            page = self._browser_probe_page
            if page is None or page.is_closed():
                page = self._browser_context.pages[0] if self._browser_context.pages else await self._browser_context.new_page()
                self._browser_probe_page = page
            await page.goto(url.strip(), wait_until='domcontentloaded', timeout=60000)
            try:
                await page.wait_for_load_state('networkidle', timeout=8000)
            except Exception:
                pass
            await page.wait_for_timeout(1500)
            title = (await page.title())[:160]
            body = (await page.locator('body').inner_text(timeout=5000))[:4000].lower()
            haystack = f'{title.lower()} {body}'

            if any(x in haystack for x in ('checking your browser', 'verify you are human', 'unusual traffic', 'captcha', 'challenge', 'checkpoint')):
                state, message = 'blocked', 'Instagram mostró una pantalla de verificación o bloqueo en el navegador.'
            elif any(x in haystack for x in ('log in', 'iniciar sesión', 'inicia sesión', 'accounts/login')):
                state, message = 'login_required', 'El navegador no tiene una sesión de Instagram activa para esta prueba.'
            elif any(x in haystack for x in ('this account is private', 'cuenta es privada', 'private account')):
                state, message = 'private_signal', 'La página mostró una señal explícita de cuenta privada.'
            elif any(x in haystack for x in ("page isn't available", 'link may be broken', 'página no está disponible')):
                state, message = 'not_found', 'Instagram indicó que la publicación no está disponible.'
            else:
                state, message = 'browser_loaded', 'La publicación cargó en el navegador autorizado; el endpoint HTTP puede ser el problema.'

            result = {
                'state': state, 'blocked': state == 'blocked',
                'message': message, 'title': title,
                'browser_context_open': True,
            }
            self._last_browser_probe = result
            logger.info('[IG_BROWSER] state=%s host=%s title_present=%s', state, host, bool(title))
            return result
        except Exception as exc:
            safe_error = self._safe_error(exc)
            result = {
                'state': 'browser_error', 'blocked': False,
                'message': f'Falló la comprobación en navegador: {safe_error[:300]}',
                'browser_context_open': bool(self._browser_context),
            }
            self._last_browser_probe = result
            logger.warning('[IG_BROWSER] state=browser_error host=%s error=%s', host, safe_error[:300])
            return result

    async def close_browser_probe(self) -> None:
        context = self._browser_context
        self._browser_context = None
        self._browser_probe_page = None
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        playwright = getattr(self, '_browser_playwright', None)
        self._browser_playwright = None
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                pass

    async def logout(self) -> None:
        previous_username = self._username
        self._session_cookies = None
        self._username = None
        self._logged_in = False
        self._session_state = "no_session"
        self._last_fetch_reason = ""
        self._last_fetch_evidence = ""
        self._last_login_error = ""
        logger.info(
            "[IG_SESSION] state=no_session reason=logout previous_username=%s",
            previous_username or "desconocido",
        )
        try:
            self._session_file().unlink(missing_ok=True)
        except Exception:
            pass

    async def check_login_status(self) -> dict:
        logged_in = self._logged_in and self._session_state == "active"
        if self._session_state == "active":
            method = "sesión autenticada (cookies)"
            message = f"Sesión activa como @{self._username}" if self._username else "Sesión activa"
            if self._last_fetch_reason == "access_denied":
                message += " — Instagram rechazó la última solicitud (HTTP 403); no confirma que el post sea privado"
            elif self._last_fetch_reason == "private":
                message += " — la última respuesta confirmó contenido privado"
        elif self._session_state == "possibly_expired":
            method = "sesión cargada, posiblemente expirada"
            message = (
                f"Sesión posiblemente expirada (@{self._username})"
                if self._username else "Sesión posiblemente expirada"
            )
        else:
            method = "GraphQL público sin sesión — posts privados no accesibles"
            message = "Sin sesión; se usa acceso público"

        return {
            "logged_in": logged_in,
            "username": self._username if self._session_state in {"active", "possibly_expired"} else None,
            "method": method,
            "state": self._session_state,
            "message": message,
            "last_fetch_reason": self._last_fetch_reason or None,
            "last_fetch_evidence": self._last_fetch_evidence or None,
            "last_browser_probe": self._last_browser_probe or None,
        }

    @property
    def is_logged_in(self) -> bool:
        return self._logged_in
