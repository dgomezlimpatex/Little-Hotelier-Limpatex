"""
╔══════════════════════════════════════════════════════════════════╗
║         LITTLE HOTELIER → LIMPATEX  — Sincronizador            ║
║                      by Limpatex / Daniel                       ║
╚══════════════════════════════════════════════════════════════════╝

DEPENDENCIAS:
  pip install requests python-dotenv beautifulsoup4 lxml

CONFIGURACIÓN (.env):
  LH_SESSION_TOKEN=<copia de tu cookie lh_session_token>
  LH_PROPERTY_UUID=8d0c221e-3a40-4eda-8c15-deb2450cd307
  LH_REGION=emea
  APP_URL=https://gestionlimpatex.vercel.app
  APP_API_KEY=<tu api key si la app lo requiere>

CÓMO OBTENER LH_SESSION_TOKEN (una sola vez, válido 20 años):
  1. Inicia sesión en platform.littlehotelier.com
  2. F12 → Application → Cookies → platform.littlehotelier.com
  3. Copia el valor de la cookie "lh_session_token"
  4. Pégalo en el .env

USO:
  python little_hotelier_sync.py           # ejecución única
  python little_hotelier_sync.py --debug   # ver datos sin enviar
  python little_hotelier_sync.py --loop    # bucle cada hora
"""

import argparse
import os
import json
import re
import smtplib
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────
LH_SESSION_TOKEN = os.getenv("LH_SESSION_TOKEN", "")
LH_EMAIL         = os.getenv("LH_EMAIL", "")
LH_PASSWORD      = os.getenv("LH_PASSWORD", "")
LH_PROPERTY_UUID = os.getenv("LH_PROPERTY_UUID", "8d0c221e-3a40-4eda-8c15-deb2450cd307")
LH_REGION        = os.getenv("LH_REGION", "emea")
LH_BASE_URL      = "https://platform.littlehotelier.com"
ENV_PATH         = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
COOKIE_JAR_PATH  = os.getenv(
    "LH_COOKIES_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "lh_cookies.json"),
)
LH_BROWSER_PROFILE_DIR = os.getenv("LH_BROWSER_PROFILE_DIR", "").strip()
LH_HEADLESS = os.getenv("LH_HEADLESS", "1").strip().lower() not in ("0", "false", "no")

APP_URL     = os.getenv("APP_URL", "https://gestionlimpatex.vercel.app")
APP_API_KEY = os.getenv("APP_API_KEY", "")

DAYS_BACK      = int(os.getenv("DAYS_BACK", "7"))       # dias hacia atras
DAYS_AHEAD     = int(os.getenv("DAYS_AHEAD", "30"))     # dias hacia adelante
LOOP_INTERVAL  = int(os.getenv("LOOP_INTERVAL", "3600"))  # segundos legacy

STATE_PATH = os.getenv(
    "LH_STATE_PATH",
    os.path.join(os.path.dirname(COOKIE_JAR_PATH) or os.path.dirname(os.path.abspath(__file__)), "lh_sync_state.json"),
)
DEBUG_DIR = os.getenv("LH_DEBUG_DIR", os.path.join(os.path.dirname(COOKIE_JAR_PATH) or os.path.dirname(os.path.abspath(__file__)), "debug"))

RUN_TIMEZONE = os.getenv("RUN_TIMEZONE", "Europe/Madrid")
RUN_AT_HOURS = [x.strip() for x in os.getenv("RUN_AT_HOURS", "09:00,14:00,20:00").split(",") if x.strip()]
RUN_ON_START = os.getenv("RUN_ON_START", "0").strip().lower() in ("1", "true", "yes")
SCHEDULER_POLL_SECONDS = int(os.getenv("SCHEDULER_POLL_SECONDS", os.getenv("LOOP_INTERVAL", "60")))

ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", "dgomezlimpatex@gmail.com")
ALERT_EMAIL_FROM = os.getenv("ALERT_EMAIL_FROM", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "1").strip().lower() not in ("0", "false", "no")
ALERT_COOLDOWN_MINUTES = int(os.getenv("ALERT_COOLDOWN_MINUTES", "180"))
ALERT_AFTER_HOURS_WITHOUT_SUCCESS = int(os.getenv("ALERT_AFTER_HOURS_WITHOUT_SUCCESS", "12"))
ALERT_ON_ZERO_RESERVATIONS = os.getenv("ALERT_ON_ZERO_RESERVATIONS", "0").strip().lower() in ("1", "true", "yes")

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_STATE_TABLE = os.getenv("SUPABASE_STATE_TABLE", "private_lh_integration_state")
LH_SECRET_STORE_ENABLED = os.getenv("LH_SECRET_STORE_ENABLED", "0").strip().lower() in ("1", "true", "yes")


class SyncStatus(str, Enum):
    OK = "ok"
    NO_RESERVATIONS = "no_reservations"
    AUTH_EXPIRED = "auth_expired"
    AUTO_LOGIN_FAILED = "auto_login_failed"
    HTTP_ERROR = "http_error"
    NETWORK_ERROR = "network_error"
    PARSE_ERROR = "parse_error"
    SEND_ERROR = "send_error"
    CONFIG_ERROR = "config_error"
    UNKNOWN_ERROR = "unknown_error"


@dataclass
class FetchResult:
    status: SyncStatus
    reservations: list[dict] = field(default_factory=list)
    message: str = ""
    http_status: int | None = None
    final_url: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

# ─────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("sync_log.txt", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# ESTADO, ALERTAS Y PERSISTENCIA OPCIONAL
# ─────────────────────────────────────────────────────────────────
def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_sync_state() -> dict:
    try:
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log.warning(f"⚠️  No se pudo leer estado de sync: {e}")
    return {}


def save_sync_state(**updates) -> dict:
    state = load_sync_state()
    state.update(updates)
    state["updated_at"] = _utc_now_iso()
    state_dir = os.path.dirname(STATE_PATH)
    if state_dir:
        os.makedirs(state_dir, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    return state


def supabase_enabled() -> bool:
    return bool(LH_SECRET_STORE_ENABLED and SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY)


def _supabase_headers(prefer: str | None = None) -> dict:
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def save_remote_state(key: str, value: dict) -> bool:
    if not supabase_enabled():
        return False
    try:
        url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_STATE_TABLE}"
        payload = {"key": key, "value": value, "updated_at": _utc_now_iso()}
        resp = requests.post(
            url,
            headers=_supabase_headers("resolution=merge-duplicates"),
            params={"on_conflict": "key"},
            json=payload,
            timeout=20,
        )
        if resp.status_code not in (200, 201, 204):
            log.warning(f"⚠️  Supabase no guardó {key}: HTTP {resp.status_code} — {resp.text[:200]}")
            return False
        return True
    except requests.RequestException as e:
        log.warning(f"⚠️  Error guardando {key} en Supabase: {e}")
        return False


def load_remote_state(key: str) -> dict | None:
    if not supabase_enabled():
        return None
    try:
        url = f"{SUPABASE_URL}/rest/v1/{SUPABASE_STATE_TABLE}"
        resp = requests.get(
            url,
            headers=_supabase_headers(),
            params={"key": f"eq.{key}", "select": "value", "limit": "1"},
            timeout=20,
        )
        if resp.status_code != 200:
            log.warning(f"⚠️  Supabase no leyó {key}: HTTP {resp.status_code} — {resp.text[:200]}")
            return None
        rows = resp.json()
        if not rows:
            return None
        value = rows[0].get("value")
        return value if isinstance(value, dict) else None
    except Exception as e:
        log.warning(f"⚠️  Error leyendo {key} desde Supabase: {e}")
        return None


def _alert_key_is_cooling_down(alert_key: str | None, state: dict) -> bool:
    if not alert_key or not ALERT_COOLDOWN_MINUTES:
        return False
    if state.get("last_alert_key") != alert_key or not state.get("last_alert_at"):
        return False
    try:
        last_alert = datetime.fromisoformat(state["last_alert_at"])
        if last_alert.tzinfo is None:
            last_alert = last_alert.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - last_alert.astimezone(timezone.utc)
        return age.total_seconds() < ALERT_COOLDOWN_MINUTES * 60
    except Exception:
        return False


def send_alert(subject: str, body: str, alert_key: str | None = None, force: bool = False) -> bool:
    state = load_sync_state()
    if not force and _alert_key_is_cooling_down(alert_key, state):
        log.info(f"🔕  Alerta omitida por cooldown: {alert_key}")
        return False
    if not ALERT_EMAIL_TO:
        log.warning("⚠️  ALERT_EMAIL_TO no configurado; no se envía alerta")
        return False

    sent = False
    if RESEND_API_KEY:
        sent = _send_alert_resend(subject, body)
    elif SMTP_HOST and SMTP_USER and SMTP_PASSWORD and ALERT_EMAIL_FROM:
        sent = _send_alert_smtp(subject, body)
    else:
        log.warning("⚠️  Email no configurado; define RESEND_API_KEY o SMTP_*")
        return False

    if sent:
        save_sync_state(last_alert_at=_utc_now_iso(), last_alert_key=alert_key or subject)
    return sent


def _send_alert_resend(subject: str, body: str) -> bool:
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": ALERT_EMAIL_FROM, "to": [ALERT_EMAIL_TO], "subject": subject, "text": body},
            timeout=20,
        )
        if resp.status_code not in (200, 201, 202):
            log.error(f"❌  Resend no envió alerta: HTTP {resp.status_code} — {resp.text[:200]}")
            return False
        log.info(f"📧  Alerta enviada a {ALERT_EMAIL_TO}")
        return True
    except requests.RequestException as e:
        log.error(f"❌  Error enviando alerta por Resend: {e}")
        return False


def _send_alert_smtp(subject: str, body: str) -> bool:
    try:
        msg = EmailMessage()
        msg["From"] = ALERT_EMAIL_FROM
        msg["To"] = ALERT_EMAIL_TO
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
            if SMTP_USE_TLS:
                smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(msg)
        log.info(f"📧  Alerta enviada a {ALERT_EMAIL_TO}")
        return True
    except Exception as e:
        log.error(f"❌  Error enviando alerta SMTP: {e}")
        return False


def build_alert_body(summary: dict) -> str:
    state = load_sync_state()
    return "\n".join([
        "La sincronización automática Little Hotelier → Limpatex no ha podido completarse.",
        "",
        f"Estado: {summary.get('status')}",
        f"Motivo: {summary.get('message') or 'Sin detalle'}",
        f"Última sincronización correcta: {state.get('last_success_at', 'No registrada')}",
        f"Reservas encontradas: {summary.get('reservations_found', 0)}",
        f"Enviadas OK: {summary.get('sent_ok', 0)}",
        f"Errores envío: {summary.get('send_errors', 0)}",
        "",
        "Acción recomendada: revisar logs de Render. Si la sesión caducó y el login automático falla, ejecutar login manual local y actualizar cookies.",
    ])


def should_run_now(now: datetime, state: dict) -> tuple[bool, str]:
    try:
        tz = ZoneInfo(RUN_TIMEZONE)
    except ZoneInfoNotFoundError:
        log.warning(f"⚠️  Zona horaria inválida {RUN_TIMEZONE!r}; usando UTC")
        tz = timezone.utc
    local_now = now.astimezone(tz)
    hhmm = local_now.strftime("%H:%M")
    slot_key = f"{local_now.strftime('%Y-%m-%d')}T{hhmm}"
    return (hhmm in RUN_AT_HOURS and state.get("last_run_slot") != slot_key), slot_key


# ─────────────────────────────────────────────────────────────────
# CLIENTE LITTLE HOTELIER
# ─────────────────────────────────────────────────────────────────
class LittleHotelierClient:
    """
    Se autentica con la cookie lh_session_token (válida 20 años)
    y descarga la página HTML de reservas de platform.littlehotelier.com.
    """

    RESERVATIONS_URL = (
        "{base}/frontdesk/{region}/{property_uuid}/reservations"
    )

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9",
            "Referer":         LH_BASE_URL,
        })
        self._load_cookie_jar()
        if LH_SESSION_TOKEN:
            self.session.cookies.set(
                "lh_session_token", LH_SESSION_TOKEN,
                domain="platform.littlehotelier.com"
            )

    def _load_cookie_jar(self) -> int:
        """Carga cookies guardadas por login manual/cloud en archivo, env o Supabase."""
        cookies: list[dict] | None = None
        source = ""
        try:
            if os.path.exists(COOKIE_JAR_PATH):
                with open(COOKIE_JAR_PATH, "r", encoding="utf-8") as f:
                    cookies = json.load(f)
                source = COOKIE_JAR_PATH
            else:
                cookies_json = os.getenv("LH_COOKIES_JSON", "").strip()
                if cookies_json:
                    cookies = json.loads(cookies_json)
                    source = "LH_COOKIES_JSON"
                else:
                    remote = load_remote_state("little_hotelier_cookies")
                    if remote and isinstance(remote.get("cookies"), list):
                        cookies = remote["cookies"]
                        source = "Supabase"
                        self._write_cookie_file(cookies)
        except Exception as e:
            log.warning(f"⚠️  No se pudieron leer las cookies de sesión: {e}")
            log.warning("⚠️  Revisa LH_COOKIES_JSON: debe ser el JSON completo de lh_cookies.json, empezando por '[' y terminando por ']'.")
            return 0

        if not cookies:
            return 0

        loaded = 0
        for cookie in cookies:
            name = cookie.get("name")
            value = cookie.get("value")
            domain = (cookie.get("domain") or "platform.littlehotelier.com").lstrip(".")
            path = cookie.get("path") or "/"
            if not name or value is None:
                continue
            if not any(host in domain for host in ("littlehotelier.com", "siteminder.com")):
                continue
            self.session.cookies.set(name, value, domain=domain, path=path)
            loaded += 1
        if loaded:
            log.info(f"🍪  Cookies de sesión cargadas: {loaded} ({source})")
        return loaded

    def _write_cookie_file(self, cookies: list[dict]) -> None:
        cookie_dir = os.path.dirname(COOKIE_JAR_PATH)
        if cookie_dir:
            os.makedirs(cookie_dir, exist_ok=True)
        with open(COOKIE_JAR_PATH, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)

    def _save_cookie_jar(self, cookies: list[dict], source: str = "auto_login") -> int:
        """Guarda cookies útiles de Playwright para reutilizarlas con requests."""
        useful = []
        for cookie in cookies:
            domain = (cookie.get("domain") or "").lstrip(".")
            if not any(host in domain for host in ("littlehotelier.com", "siteminder.com")):
                continue
            useful.append({
                "name": cookie.get("name"),
                "value": cookie.get("value"),
                "domain": domain,
                "path": cookie.get("path") or "/",
                "expires": cookie.get("expires"),
                "httpOnly": cookie.get("httpOnly"),
                "secure": cookie.get("secure"),
                "sameSite": cookie.get("sameSite"),
            })

        if not useful:
            return 0

        self._write_cookie_file(useful)
        save_remote_state(
            "little_hotelier_cookies",
            {"cookies": useful, "saved_at": _utc_now_iso(), "source": source},
        )
        log.info(f"💾  Cookies guardadas en {COOKIE_JAR_PATH} ({len(useful)})")
        return len(useful)

    def get_reservations(self, date_from: str, date_to: str) -> FetchResult:
        """Descarga y parsea la página HTML de reservas con estado explícito."""
        url = self.RESERVATIONS_URL.format(
            base=LH_BASE_URL,
            region=LH_REGION,
            property_uuid=LH_PROPERTY_UUID,
        )

        params = {
            "utf8": "✓",
            "reservation_filter[date_type]":    "CheckIn",
            "reservation_filter[status]":       "",          # vacío = todos los estados
            "reservation_filter[date_from]":    date_from,
            "reservation_filter[date_to]":      date_to,
            "reservation_filter[date_from_display]": _fmt_display(date_from),
            "reservation_filter[date_to_display]":   _fmt_display(date_to),
            "reservation_filter[guest_last_name]":       "",
            "reservation_filter[booking_reference_id]":  "",
            "reservation_filter[invoice_number]":        "",
            "reservation_filter[channel_id]":            "",
            "button": "",
        }

        log.info(f"📥  Obteniendo reservas {date_from} → {date_to} ...")
        try:
            resp = self.session.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            log.error(f"❌  Error de red: {e}")
            return FetchResult(status=SyncStatus.NETWORK_ERROR, message=str(e))

        if resp.status_code == 401 or "authx.siteminder.com" in resp.url:
            log.warning("⚠️  Sesión expirada")
            if os.getenv("DISABLE_AUTO_LOGIN") == "1":
                message = "Sesión expirada y auto-login desactivado (DISABLE_AUTO_LOGIN=1)."
                log.error(f"❌  {message}")
                log.error("    Refresca sesión manualmente con: python little_hotelier_sync.py --login")
                return FetchResult(
                    status=SyncStatus.AUTH_EXPIRED,
                    message=message,
                    http_status=resp.status_code,
                    final_url=resp.url,
                )
            log.info("    Intentando login automático...")
            if self._auto_login():
                try:
                    resp = self.session.get(url, params=params, timeout=30)
                    log.info(f"🔄  Retry → URL final: {resp.url} | HTTP {resp.status_code}")
                except requests.RequestException as e:
                    log.error(f"❌  Error tras relogin: {e}")
                    return FetchResult(status=SyncStatus.NETWORK_ERROR, message=f"Error tras relogin: {e}")
            else:
                message = "Sesión caducada y no se pudo renovar con auto-login."
                log.error(f"❌  {message}")
                return FetchResult(
                    status=SyncStatus.AUTO_LOGIN_FAILED,
                    message=message,
                    http_status=resp.status_code,
                    final_url=resp.url,
                )

        if resp.status_code == 401 or "authx.siteminder.com" in resp.url:
            return FetchResult(
                status=SyncStatus.AUTH_EXPIRED,
                message="La sesión sigue expirada después del reintento de login.",
                http_status=resp.status_code,
                final_url=resp.url,
            )

        if resp.status_code != 200:
            log.error(f"❌  HTTP {resp.status_code} al obtener reservas")
            return FetchResult(
                status=SyncStatus.HTTP_ERROR,
                message=f"HTTP {resp.status_code} al obtener reservas",
                http_status=resp.status_code,
                final_url=resp.url,
            )

        try:
            reservations = _parse_html(resp.text)
        except Exception as e:
            log.exception(f"❌  Error parseando HTML de Little Hotelier: {e}")
            return FetchResult(
                status=SyncStatus.PARSE_ERROR,
                message=str(e),
                http_status=resp.status_code,
                final_url=resp.url,
            )

        log.info(f"📋  {len(reservations)} reservas encontradas")
        if not reservations:
            return FetchResult(
                status=SyncStatus.NO_RESERVATIONS,
                reservations=[],
                message="No se encontraron reservas en el período.",
                http_status=resp.status_code,
                final_url=resp.url,
            )
        return FetchResult(
            status=SyncStatus.OK,
            reservations=reservations,
            message="Reservas obtenidas correctamente.",
            http_status=resp.status_code,
            final_url=resp.url,
        )

    def _auto_login(self) -> bool:
        """Login automático via Playwright (Chrome headless). Guarda el nuevo token en .env."""
        if not LH_EMAIL or not LH_PASSWORD:
            log.error("❌  LH_EMAIL / LH_PASSWORD no configurados en .env")
            return False

        log.info("🔐  Iniciando sesión con Chrome headless (Playwright)...")
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        except ImportError:
            log.error("❌  Playwright no instalado. Ejecuta:\n"
                      "    pip install playwright\n"
                      "    python -m playwright install chromium")
            return False

        try:
            with sync_playwright() as pw:
                browser = None
                launch_args = [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ]
                context_options = {
                    "user_agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "extra_http_headers": {
                        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
                    },
                }
                if LH_BROWSER_PROFILE_DIR:
                    os.makedirs(LH_BROWSER_PROFILE_DIR, exist_ok=True)
                    log.info(f"Browser profile persistente: {LH_BROWSER_PROFILE_DIR}")
                    ctx = pw.chromium.launch_persistent_context(
                        LH_BROWSER_PROFILE_DIR,
                        headless=LH_HEADLESS,
                        args=launch_args,
                        **context_options,
                    )
                else:
                    browser = pw.chromium.launch(headless=LH_HEADLESS, args=launch_args)
                    ctx = browser.new_context(**context_options)
                page = ctx.new_page()

                import urllib.parse

                # 1. Navegar directamente a SiteMinder con redirectUri=/authenticate
                # (esta es la URL que LH tiene registrada en Auth0; usar /reservations
                #  provoca "Callback URL mismatch")
                redirect_uri = urllib.parse.quote(
                    f"{LH_BASE_URL}/frontdesk/{LH_REGION}/authenticate",
                    safe=""
                )
                siteminder_url = (
                    f"https://littlehotelier.authx.siteminder.com/login"
                    f"?redirectUri={redirect_uri}"
                )
                page.goto(siteminder_url, wait_until="domcontentloaded", timeout=30_000)
                log.info(f"🔍  URL SiteMinder: {page.url}")

                # 2. Esperar campo de email/usuario
                page.wait_for_selector(
                    "input[type='email'], input[name='username'], "
                    "input[name='email'], input[type='text']",
                    timeout=15_000
                )

                # 3. Rellenar email
                for selector in ["input[type='email']", "input[name='username']",
                                  "input[name='email']", "input[type='text']"]:
                    if page.locator(selector).count() > 0:
                        page.fill(selector, LH_EMAIL)
                        log.info(f"  ✏️  Email introducido ({selector})")
                        break

                # 4. Pulsar "Siguiente"
                next_btn = page.locator(
                    "button[type='submit'], input[type='submit'], "
                    "button:has-text('Next'), button:has-text('Continue'), "
                    "button:has-text('Siguiente'), button:has-text('Sign in'), "
                    "button:has-text('Log in')"
                ).first
                next_btn.click()
                log.info(f"  🖱️  Botón 'siguiente' pulsado, URL: {page.url}")

                # 5. Esperar campo de contraseña
                page.wait_for_selector("input[type='password']", timeout=15_000)
                page.fill("input[type='password']", LH_PASSWORD)
                log.info("  ✏️  Contraseña introducida")

                # 6. Enviar formulario
                page.locator(
                    "button[type='submit'], input[type='submit'], "
                    "button:has-text('Sign in'), button:has-text('Log in'), "
                    "button:has-text('Iniciar'), button:has-text('Acceder')"
                ).first.click()
                log.info("  🖱️  Formulario enviado")

                # Listener para diagnosticar respuestas HTTP de LH
                def _log_lh_response(response):
                    try:
                        if "platform.littlehotelier.com" in response.url:
                            sc = response.headers.get("set-cookie", "")
                            log.info(
                                f"  📡  LH HTTP {response.status} {response.url[:100]} "
                                f"set-cookie={'sí' if sc else 'no'}"
                            )
                    except Exception:
                        pass
                page.on("response", _log_lh_response)

                # 7. Esperar a que aparezca lh_session_token
                log.info("  ⏳  Esperando cookie lh_session_token...")
                token = None
                deadline = time.time() + 45
                while time.time() < deadline:
                    for c in ctx.cookies():
                        if c["name"] == "lh_session_token":
                            token = c["value"]
                            break
                    if token:
                        log.info(f"  ✅  Cookie detectada (URL: {page.url})")
                        break
                    time.sleep(1)

                if not token:
                    log.error(f"❌  Timeout. URL final: {page.url}")
                    try:
                        title = page.title()
                        body = page.locator("body").inner_text()[:300]
                        log.info(f"  📄  Título: {title!r}")
                        log.info(f"  📄  Cuerpo: {body!r}")
                    except Exception:
                        pass
                    try:
                        cookie_names = [c["name"] for c in ctx.cookies()]
                        log.info(f"  🍪  Cookies presentes: {cookie_names}")
                    except Exception:
                        pass
                    ctx.close()
                    if browser:
                        browser.close()
                    return False

                # 8. Copiar TODAS las cookies de LH al requests session
                all_cookies = ctx.cookies()
                log.info(f"  🍪  Cookies: {[c['name'] for c in all_cookies]}")
                for c in all_cookies:
                    domain = c.get("domain", "").lstrip(".")
                    if "littlehotelier.com" in domain:
                        self.session.cookies.set(c["name"], c["value"], domain=domain)
                self._save_cookie_jar(all_cookies)

                ctx.close()
                if browser:
                    browser.close()
                self._save_token_to_env(token)
                log.info("✅  Sesión renovada — todas las cookies copiadas al cliente")
                return True

        except Exception as e:
            log.error(f"❌  Error en login automático: {e}")
            return False

    def _save_token_to_env(self, token: str):
        """Actualiza el token en memoria y, si es posible, en el archivo .env."""
        global LH_SESSION_TOKEN
        # Siempre actualizar en memoria (funciona en Render y local)
        LH_SESSION_TOKEN = token
        self.session.cookies.set(
            "lh_session_token", token,
            domain="platform.littlehotelier.com"
        )
        save_remote_state(
            "little_hotelier_session_token",
            {"token": token, "saved_at": _utc_now_iso()},
        )
        # Intentar persistir en .env (solo funciona en local)
        try:
            with open(ENV_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
            with open(ENV_PATH, "w", encoding="utf-8") as f:
                for line in lines:
                    if line.startswith("LH_SESSION_TOKEN="):
                        f.write(f"LH_SESSION_TOKEN={token}\n")
                    else:
                        f.write(line)
            log.info("💾  Token guardado en .env")
        except Exception:
            log.info("💾  Token renovado en memoria (entorno cloud — .env no persiste)")


# ─────────────────────────────────────────────────────────────────
# PARSEO HTML
# ─────────────────────────────────────────────────────────────────
def _parse_html(html: str) -> list[dict]:
    """Extrae reservas del HTML de platform.littlehotelier.com/frontdesk."""
    soup = BeautifulSoup(html, "lxml")
    rows = soup.find_all("tr", class_="reservation_room_type")

    reservations = []
    for row in rows:
        try:
            reservations.append(_parse_row(row))
        except Exception as e:
            log.warning(f"  ⚠  Fila ignorada por error de parseo: {e}")

    return reservations


def _extract_rooms(room_td) -> list[str]:
    """Extrae una lista real de habitaciones aunque Little Hotelier las apile."""
    if not room_td:
        return []

    raw_rooms = room_td.get_text("\n", strip=True)
    rooms = []
    for line in raw_rooms.splitlines():
        room = re.sub(r"\(\+\s*\d+\s*M[aá]s\)", "", line, flags=re.IGNORECASE).strip()
        if room and room != "-" and room not in rooms:
            rooms.append(room)

    if len(rooms) > 1:
        return rooms

    # Fallback para HTML/strings antiguos donde quedaron pegadas:
    # "Habitación 6Habitación 1(+1 Más)" -> ["Habitación 6", "Habitación 1"].
    raw_compact = re.sub(r"\(\+\s*\d+\s*M[aá]s\)", "", raw_rooms, flags=re.IGNORECASE)
    matches = re.findall(
        r"Habitaci[oó]n(?:\s+doble\s+sin\s+vistas)?\s+.*?(?=Habitaci[oó]n(?:\s+doble\s+sin\s+vistas)?\s+|$)",
        raw_compact,
        flags=re.IGNORECASE,
    )
    fallback_rooms = []
    for match in matches:
        room = match.strip()
        if room and room not in fallback_rooms:
            fallback_rooms.append(room)
    return fallback_rooms or rooms


def _parse_row(row) -> dict:
    # Estado  (clase CSS en inglés: confirmed, cancelled, etc.)
    status_span = row.select_one("td.status span")
    status      = (status_span.get("class") or ["unknown"])[0] if status_span else "unknown"

    # Nombre del huésped
    name_span  = row.select_one("td.name span.maskContent")
    guest_name = name_span.get_text(strip=True) if name_span else ""

    # Referencia + IDs internos
    ref_a     = row.select_one("a.booking-reference")
    reference = ref_a.get_text(strip=True) if ref_a else ""
    res_id    = ref_a.get("data-reservation-id", "") if ref_a else ""
    res_uuid  = ""
    if ref_a:
        # href: /frontdesk/emea/{prop_uuid}/reservations/{res_uuid}/edit
        m = re.search(r"/reservations/([^/]+)/edit", ref_a.get("href", ""))
        if m:
            res_uuid = m.group(1)

    # Canal / fuente
    src_td  = row.select_one("td.booking_source span")
    channel = src_td.get_text(strip=True) if src_td else ""

    # Huéspedes (adultos / niños / bebés)
    adults = children = infants = 0
    guests_spans = row.select("td.guests span")
    nums = []
    for s in guests_spans:
        txt = s.get_text(strip=True)
        nums.append(int(txt) if txt.isdigit() else 0)
    if len(nums) > 0: adults   = nums[0]
    if len(nums) > 1: children = nums[1]
    if len(nums) > 2: infants  = nums[2]

    # Fechas (formato original DD-MM-YY → YYYY-MM-DD)
    ci_td = row.select_one("td.check_in")
    co_td = row.select_one("td.check_out")
    check_in  = _parse_date(ci_td.get_text(strip=True)) if ci_td else ""
    check_out = _parse_date(co_td.get_text(strip=True)) if co_td else ""

    # Habitación (string legacy + array completo)
    room_td   = row.select_one("td.room_name")
    rooms     = _extract_rooms(room_td)
    room      = ", ".join(rooms) if rooms else ""

    # Total
    total_td = row.select_one("td.total")
    total    = total_td.get_text(strip=True) if total_td else ""

    return {
        "external_id":   res_id,
        "uuid":          res_uuid,
        "reference":     reference,
        "channel":       channel,
        "check_in":      check_in,
        "check_out":     check_out,
        "room":          room,
        "rooms":         rooms,
        "guest_name":    guest_name,
        "adults":        adults,
        "children":      children,
        "infants":       infants,
        "status":        status,
        "total":         total,
        "synced_at":     datetime.now(timezone.utc).isoformat(),
        "source_system": "little_hotelier",
    }


def _parse_date(s: str) -> str:
    """DD-MM-YY  →  YYYY-MM-DD"""
    try:
        d, m, y = s.split("-")
        return f"20{y}-{m}-{d}"
    except Exception:
        return s


def _fmt_display(date_iso: str) -> str:
    """YYYY-MM-DD  →  '19 may 2026'  (para el parámetro display de LH)"""
    try:
        dt = datetime.strptime(date_iso, "%Y-%m-%d")
        months = ["ene","feb","mar","abr","may","jun",
                  "jul","ago","sep","oct","nov","dic"]
        return f"{dt.day} {months[dt.month-1]} {dt.year}"
    except Exception:
        return date_iso


# ─────────────────────────────────────────────────────────────────
# CLIENTE LIMPATEX
# ─────────────────────────────────────────────────────────────────
class LimpatexAppClient:
    """
    Envía reservas al endpoint configurado.

    APP_URL puede ser:
    - dominio/base de la app: https://gestionlimpatex.vercel.app
      → se envía a /api/reservations
    - endpoint completo: https://...supabase.co/functions/v1/little-hotelier-sync
      → se usa tal cual, sin añadir /api/reservations
    """

    def __init__(self):
        self.base = APP_URL.rstrip("/")
        self.endpoint_url = self._resolve_endpoint_url(self.base)
        self.sess = requests.Session()
        self.sess.headers["Content-Type"] = "application/json"
        if APP_API_KEY:
            self.sess.headers["Authorization"] = f"Bearer {APP_API_KEY}"

    @staticmethod
    def _resolve_endpoint_url(app_url: str) -> str:
        if "/functions/v1/" in app_url or app_url.rstrip("/").endswith("/api/reservations"):
            return app_url.rstrip("/")
        return f"{app_url.rstrip('/')}/api/reservations"

    def upsert(self, reservation: dict) -> bool:
        url = self.endpoint_url
        try:
            resp = self.sess.post(url, json=reservation, timeout=15)
            if resp.status_code in (200, 201):
                log.debug(f"  ✓ {reservation['reference']}")
                return True
            if resp.status_code == 409:
                # ya existe → actualizar
                resp = self.sess.put(
                    f"{url}/{reservation['external_id']}",
                    json=reservation, timeout=15
                )
                if resp.status_code in (200, 204):
                    log.debug(f"  ↺ {reservation['reference']} actualizada")
                    return True
            log.warning(f"  ⚠ {reservation['reference']}: HTTP {resp.status_code} — {resp.text[:200]}")
            return False
        except requests.RequestException as e:
            log.error(f"  ❌ {reservation['reference']}: {e}")
            return False

    def send_batch(self, reservations: list[dict]) -> tuple[int, int]:
        ok = errors = 0
        for r in reservations:
            if self.upsert(r):
                ok += 1
            else:
                errors += 1
            time.sleep(0.1)
        return ok, errors


# ─────────────────────────────────────────────────────────────────
# SINCRONIZACIÓN
# ─────────────────────────────────────────────────────────────────
def _date_range(days_ahead: int | None = None) -> tuple[str, str]:
    today = datetime.today()
    date_from = (today - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")
    date_to = (today + timedelta(days=days_ahead if days_ahead is not None else DAYS_AHEAD)).strftime("%Y-%m-%d")
    return date_from, date_to


def _write_reservations_cache(reservations: list[dict]) -> None:
    with open("reservas_cache.json", "w", encoding="utf-8") as f:
        json.dump(reservations, f, ensure_ascii=False, indent=2)
    log.info("💾  Copia local → reservas_cache.json")


def _finalize_summary(summary: dict, alert: bool = False) -> dict:
    summary["finished_at"] = _utc_now_iso()
    try:
        started = datetime.fromisoformat(summary["started_at"])
        finished = datetime.fromisoformat(summary["finished_at"])
        summary["duration_seconds"] = round((finished - started).total_seconds(), 2)
    except Exception:
        summary["duration_seconds"] = None

    status = summary.get("status")
    updates = {
        "last_run_at": summary["finished_at"],
        "last_status": status,
        "last_message": summary.get("message", ""),
        "last_reservations_found": summary.get("reservations_found", 0),
        "last_sent_ok": summary.get("sent_ok", 0),
        "last_send_errors": summary.get("send_errors", 0),
    }
    if status in (SyncStatus.OK.value, SyncStatus.NO_RESERVATIONS.value):
        updates.update({
            "last_success_at": summary["finished_at"],
            "last_error_status": None,
            "last_error_message": None,
        })
    else:
        updates.update({
            "last_error_at": summary["finished_at"],
            "last_error_status": status,
            "last_error_message": summary.get("message", ""),
        })
    save_sync_state(**updates)
    save_remote_state("little_hotelier_sync_state", {**load_sync_state(), "summary": summary})

    if alert:
        send_alert(
            "Error sincronización Little Hotelier",
            build_alert_body(summary),
            alert_key=f"lh_sync:{status}",
        )
    return summary


def sync() -> dict:
    log.info("=" * 60)
    log.info("🚀  Little Hotelier → Limpatex")
    log.info("=" * 60)

    started_at = _utc_now_iso()
    date_from, date_to = _date_range()
    summary = {
        "status": SyncStatus.UNKNOWN_ERROR.value,
        "started_at": started_at,
        "finished_at": None,
        "duration_seconds": None,
        "date_from": date_from,
        "date_to": date_to,
        "reservations_found": 0,
        "sent_ok": 0,
        "send_errors": 0,
        "message": "",
    }

    lh = LittleHotelierClient()
    result = lh.get_reservations(date_from, date_to)
    summary.update({
        "status": result.status.value,
        "message": result.message,
        "http_status": result.http_status,
        "final_url": result.final_url,
        "reservations_found": len(result.reservations),
    })

    if result.status == SyncStatus.NO_RESERVATIONS:
        log.info("ℹ️   Sin reservas en el período.")
        return _finalize_summary(summary, alert=ALERT_ON_ZERO_RESERVATIONS)

    if result.status != SyncStatus.OK:
        log.error(f"❌  Sync abortada: {result.status.value} — {result.message}")
        return _finalize_summary(summary, alert=True)

    reservations = result.reservations
    _write_reservations_cache(reservations)

    app = LimpatexAppClient()
    ok, errors = app.send_batch(reservations)
    summary["sent_ok"] = ok
    summary["send_errors"] = errors

    if errors:
        summary["status"] = SyncStatus.SEND_ERROR.value
        summary["message"] = f"{errors} errores enviando reservas a Limpatex"
        log.error(f"❌  {summary['message']}")
        alert = True
    else:
        summary["status"] = SyncStatus.OK.value
        summary["message"] = f"{ok} reservas sincronizadas correctamente"
        alert = False

    log.info("─" * 60)
    log.info(f"✅  {ok} ok · {errors} errores")
    log.info("=" * 60)
    return _finalize_summary(summary, alert=alert)


def list_mode(days: int | None = None):
    """Muestra todas las reservas en tabla de texto."""
    date_from, date_to = _date_range(days)

    lh = LittleHotelierClient()
    result = lh.get_reservations(date_from, date_to)
    reservations = result.reservations

    if result.status != SyncStatus.OK:
        print(f"Sin reservas o error: {result.status.value} — {result.message}")
        return

    print(f"\n{'#':<4} {'ESTADO':<12} {'HUÉSPED':<25} {'REFERENCIA':<20} {'ENTRADA':<12} {'SALIDA':<12} {'HABITACIÓN':<18} {'CANAL'}")
    print("─" * 120)
    for i, r in enumerate(reservations, 1):
        rooms_str = r.get("room") or ", ".join(r.get("rooms", [])) or "-"
        print(
            f"{i:<4} {r['status']:<12} {r['guest_name'][:24]:<25} "
            f"{r['reference']:<20} {r['check_in']:<12} {r['check_out']:<12} "
            f"{rooms_str[:17]:<18} {r['channel']}"
        )
    print("─" * 120)
    print(f"Total: {len(reservations)} reservas  ({date_from} → {date_to})")


def debug_mode():
    """Descarga y muestra reservas sin enviarlas a la app."""
    log.info("🛠️   MODO DEBUG")
    date_from, date_to = _date_range()

    lh = LittleHotelierClient()
    result = lh.get_reservations(date_from, date_to)
    reservations = result.reservations

    print("\n" + "=" * 60)
    print(f"ESTADO: {result.status.value} — {result.message}")
    print(f"RESERVAS ({len(reservations)} encontradas):")
    print("=" * 60)
    if reservations:
        print(json.dumps(reservations[0], ensure_ascii=False, indent=2))
        if len(reservations) > 1:
            print(f"\n... y {len(reservations)-1} más.")
    else:
        print("Sin reservas. Revisa estado, cookies y LH_PROPERTY_UUID.")


# ─────────────────────────────────────────────────────────────────
# PUNTO DE ENTRADA
# ─────────────────────────────────────────────────────────────────
def manual_login_mode(push_cookies: bool = False):
    """Abre Chrome visible, espera login manual y guarda cookies/token."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("❌  Playwright no instalado. Ejecuta: pip install playwright && python -m playwright install chromium")
        return False

    print()
    print("=" * 60)
    print("🌐  MODO LOGIN MANUAL")
    print("=" * 60)
    print()
    print("Se abrirá Chrome. Inicia sesión normalmente en Little Hotelier.")
    print("Cuando veas tu panel de reservas, el script capturará la cookie")
    print("y actualizará automáticamente el .env. (Tiempo máx: 5 minutos)")
    print()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx     = browser.new_context()
        page    = ctx.new_page()
        page.goto(f"{LH_BASE_URL}/frontdesk/{LH_REGION}/{LH_PROPERTY_UUID}/reservations")

        log.info("⏳  Esperando login (máx 5 min)...")
        deadline = time.time() + 300
        token = None
        while time.time() < deadline:
            for c in ctx.cookies():
                if c["name"] == "lh_session_token":
                    token_candidate = c["value"]
                    if (page.url.startswith(LH_BASE_URL)
                            and "reservations" in page.url
                            and "authx.siteminder.com" not in page.url
                            and "auth0.siteminder.com" not in page.url):
                        token = token_candidate
                        break
            if token:
                break
            time.sleep(2)

        if not token:
            print()
            print("❌  No se detectó login completado tras 5 minutos.")
            print("    ¿Llegaste a ver la página de reservas?")
            browser.close()
            return False

        all_cookies = ctx.cookies()
        cookie_client = LittleHotelierClient()
        cookie_client._save_cookie_jar(all_cookies, source="manual_login")
        cookie_client._save_token_to_env(token)
        if push_cookies:
            save_remote_state(
                "little_hotelier_cookies",
                {"cookies": json.loads(open(COOKIE_JAR_PATH, encoding="utf-8").read()), "saved_at": _utc_now_iso(), "source": "manual_login_push"},
            )
            save_remote_state(
                "little_hotelier_session_token",
                {"token": token, "saved_at": _utc_now_iso(), "source": "manual_login_push"},
            )
        print()
        print("=" * 60)
        print("✅  SESIÓN GUARDADA")
        print("=" * 60)
        print(f"\n    LH_SESSION_TOKEN={token[:30]}...\n")
        print(f"    Cookies completas guardadas en: {COOKIE_JAR_PATH}")
        if push_cookies:
            print("    Push remoto solicitado; revisa logs para confirmar Supabase.")
        print()
        print("Ya puedes ejecutar normalmente:")
        print("    python little_hotelier_sync.py")
        print()
        browser.close()
        return True


def status_mode() -> None:
    state = load_sync_state()
    print(json.dumps(state, ensure_ascii=False, indent=2) if state else "Sin estado registrado todavía.")


def validate_config() -> int:
    checks = []
    def add(ok: bool, msg: str):
        checks.append(ok)
        print(("✅" if ok else "⚠️ ") + " " + msg)

    add(bool(LH_PROPERTY_UUID), "LH_PROPERTY_UUID configurado")
    add(bool(LH_SESSION_TOKEN or os.getenv("LH_COOKIES_JSON") or os.path.exists(COOKIE_JAR_PATH) or supabase_enabled()), "Alguna fuente de sesión/cookies disponible")
    add(bool(APP_URL), "APP_URL configurada")
    add(bool(APP_API_KEY), "APP_API_KEY configurada" if APP_API_KEY else "APP_API_KEY no configurada; solo OK si endpoint no la exige")
    if os.getenv("DISABLE_AUTO_LOGIN") == "1":
        add(True, "Auto-login desactivado explícitamente")
    else:
        add(bool(LH_EMAIL and LH_PASSWORD), "LH_EMAIL/LH_PASSWORD presentes para auto-login" if LH_EMAIL and LH_PASSWORD else "Faltan LH_EMAIL/LH_PASSWORD para auto-login")
    try:
        import playwright  # noqa: F401
        add(True, "Playwright importable")
    except Exception:
        add(False, "Playwright no importable; auto-login no funcionará")
    add(bool(RESEND_API_KEY or (SMTP_HOST and SMTP_USER and SMTP_PASSWORD and ALERT_EMAIL_FROM)), "Email de alertas configurado" if (RESEND_API_KEY or SMTP_HOST) else "Email de alertas no configurado")
    add(supabase_enabled(), "Supabase cookie/state store activo" if supabase_enabled() else "Supabase store desactivado/no configurado")
    state_dir = os.path.dirname(STATE_PATH)
    try:
        if state_dir:
            os.makedirs(state_dir, exist_ok=True)
        add(True, f"STATE_PATH escribible: {STATE_PATH}")
    except Exception as e:
        add(False, f"STATE_PATH no escribible: {e}")
    return 0 if all(checks[:3]) else 1


def debug_login_mode() -> int:
    client = LittleHotelierClient()
    ok = client._auto_login()
    print("✅ Login automático OK" if ok else "❌ Login automático falló")
    return 0 if ok else 1


def run_loop() -> None:
    log.info(f"🔁  Scheduler {RUN_TIMEZONE} slots={RUN_AT_HOURS} poll={SCHEDULER_POLL_SECONDS}s")
    if RUN_ON_START:
        log.info("▶️  RUN_ON_START=1: ejecutando sync inicial")
        sync()
    while True:
        try:
            state = load_sync_state()
            should_run, slot_key = should_run_now(datetime.now(timezone.utc), state)
            if should_run:
                save_sync_state(last_run_slot=slot_key)
                sync()
            else:
                log.info(f"⏳  No toca sync ahora. Próxima comprobación en {SCHEDULER_POLL_SECONDS}s")
        except Exception as e:
            log.exception(f"Error en ciclo scheduler: {e}")
            send_alert("Error scheduler Little Hotelier", str(e), alert_key="lh_scheduler_error")
        time.sleep(SCHEDULER_POLL_SECONDS)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sincronizador Little Hotelier → Limpatex")
    parser.add_argument("--login", action="store_true", help="Abrir navegador visible para renovar cookies manualmente")
    parser.add_argument("--push-cookies", action="store_true", help="Tras --login, subir cookies/token a Supabase si está configurado")
    parser.add_argument("--debug", action="store_true", help="Ver reservas sin enviarlas")
    parser.add_argument("--debug-login", action="store_true", help="Probar solo login automático")
    parser.add_argument("--list", action="store_true", help="Listar reservas")
    parser.add_argument("--loop", action="store_true", help="Ejecutar scheduler continuo")
    parser.add_argument("--validate-config", action="store_true", help="Validar configuración sin imprimir secretos")
    parser.add_argument("--test-alert", action="store_true", help="Enviar email de prueba")
    parser.add_argument("--status", action="store_true", help="Mostrar estado local de sincronización")
    parser.add_argument("--days", type=int, default=DAYS_AHEAD, help="Días hacia adelante para --list")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.login:
        return 0 if manual_login_mode(push_cookies=args.push_cookies) else 1
    if args.validate_config:
        return validate_config()
    if args.test_alert:
        return 0 if send_alert("Prueba alerta Little Hotelier", "Email de prueba del sincronizador Little Hotelier.", force=True) else 1
    if args.status:
        status_mode()
        return 0
    if args.debug_login:
        return debug_login_mode()
    if args.list:
        list_mode(args.days)
        return 0
    if args.debug:
        debug_mode()
        return 0
    if args.loop:
        run_loop()
        return 0
    summary = sync()
    return 0 if summary.get("status") in (SyncStatus.OK.value, SyncStatus.NO_RESERVATIONS.value) else 1


if __name__ == "__main__":
    raise SystemExit(main())


# ─────────────────────────────────────────────────────────────────
# QUÉ ENDPOINT AÑADIR EN TU APP (gestionlimpatex.vercel.app)
# ─────────────────────────────────────────────────────────────────
"""
El script envía un POST a:
  https://gestionlimpatex.vercel.app/api/reservations

con este JSON por cada reserva:

{
  "external_id":   "55998893",          ← ID numérico interno de LH
  "uuid":          "4592b7fd-...",      ← UUID de la reserva en LH
  "reference":     "BDC-6726731254",   ← referencia visible (Booking.com, etc.)
  "channel":       "Booking.com",
  "check_in":      "2026-05-19",
  "check_out":     "2026-05-20",
  "room":          "Habitación 2",
  "guest_name":    "Fisher, Michelle",
  "adults":        1,
  "children":      0,
  "infants":       0,
  "status":        "confirmed",         ← confirmed | cancelled | no_show …
  "total":         "72 €",
  "synced_at":     "2026-05-19T08:00:00Z",
  "source_system": "little_hotelier"
}

En tu app (Lovable / Supabase) necesitas:

1. TABLA en Supabase:
   CREATE TABLE reservations (
     id           BIGSERIAL PRIMARY KEY,
     external_id  TEXT UNIQUE NOT NULL,
     uuid         TEXT,
     reference    TEXT,
     channel      TEXT,
     check_in     DATE,
     check_out    DATE,
     room         TEXT,
     guest_name   TEXT,
     adults       INT,
     children     INT,
     infants      INT,
     status       TEXT,
     total        TEXT,
     synced_at    TIMESTAMPTZ,
     source_system TEXT,
     created_at   TIMESTAMPTZ DEFAULT now()
   );

2. API ROUTE en tu app (Next.js / Vercel):
   // app/api/reservations/route.ts
   export async function POST(req: Request) {
     const data = await req.json();
     const { data: existing } = await supabase
       .from("reservations")
       .select("id")
       .eq("external_id", data.external_id)
       .single();

     if (existing) {
       await supabase.from("reservations").update(data).eq("external_id", data.external_id);
       return Response.json({ updated: true }, { status: 200 });
     }
     await supabase.from("reservations").insert(data);
     return Response.json({ created: true }, { status: 201 });
   }

3. Si usas autenticación en la API, genera un token en Supabase o
   en tu app y ponlo en APP_API_KEY del .env.
"""
