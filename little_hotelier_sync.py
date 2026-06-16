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

import os
import json
import re
import time
import logging
from datetime import datetime, timedelta, timezone

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
LOOP_INTERVAL  = int(os.getenv("LOOP_INTERVAL", "3600"))  # segundos

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
        """Carga cookies guardadas por el login manual, si existen."""
        try:
            if os.path.exists(COOKIE_JAR_PATH):
                with open(COOKIE_JAR_PATH, "r", encoding="utf-8") as f:
                    cookies = json.load(f)
            else:
                cookies_json = os.getenv("LH_COOKIES_JSON", "").strip()
                if not cookies_json:
                    return 0
                cookies = json.loads(cookies_json)
        except Exception as e:
            log.warning(f"⚠️  No se pudieron leer las cookies de sesión: {e}")
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
            log.info(f"🍪  Cookies de sesión cargadas: {loaded}")
        return loaded

    def _save_cookie_jar(self, cookies: list[dict]) -> int:
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

        cookie_dir = os.path.dirname(COOKIE_JAR_PATH)
        if cookie_dir:
            os.makedirs(cookie_dir, exist_ok=True)
        with open(COOKIE_JAR_PATH, "w", encoding="utf-8") as f:
            json.dump(useful, f, ensure_ascii=False, indent=2)
        log.info(f"💾  Cookies guardadas en {COOKIE_JAR_PATH} ({len(useful)})")
        return len(useful)

    def get_reservations(self, date_from: str, date_to: str) -> list[dict]:
        """
        Descarga y parsea la página HTML de reservas.

        Args:
            date_from:  "YYYY-MM-DD"
            date_to:    "YYYY-MM-DD"

        Returns:
            Lista de reservas normalizadas como dicts.
        """
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
            return []

        if resp.status_code == 401 or "authx.siteminder.com" in resp.url:
            log.warning("⚠️  Sesión expirada")
            # En entornos cloud (Render, etc.) el auto-login falla porque LH
            # devuelve 500 desde IPs de datacenter. Permitir desactivarlo con
            # DISABLE_AUTO_LOGIN=1 para fallar rápido en esos entornos.
            if os.getenv("DISABLE_AUTO_LOGIN") == "1":
                log.error("❌  Auto-login desactivado (DISABLE_AUTO_LOGIN=1).")
                log.error("    Refresca LH_SESSION_TOKEN manualmente con:")
                log.error("    python little_hotelier_sync.py --login")
                return []
            log.info("    Intentando login automático...")
            if self._auto_login():
                # Reintentar con la nueva sesión
                try:
                    resp = self.session.get(url, params=params, timeout=30)
                    log.info(f"🔄  Retry → URL final: {resp.url} | HTTP {resp.status_code}")
                except requests.RequestException as e:
                    log.error(f"❌  Error tras relogin: {e}")
                    return []
            else:
                log.error("❌  No se pudo renovar la sesión. Comprueba LH_EMAIL y LH_PASSWORD en .env")
                return []

        if resp.status_code != 200:
            log.error(f"❌  HTTP {resp.status_code} al obtener reservas")
            return []

        reservations = _parse_html(resp.text)
        log.info(f"📋  {len(reservations)} reservas encontradas")
        return reservations

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

    # Habitación (array para compatibilidad con el schema de Lovable)
    room_td   = row.select_one("td.room_name")
    rooms     = _extract_rooms(room_td)

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
    Envía reservas a gestionlimpatex.vercel.app.

    El endpoint esperado es:  POST /api/reservations
    Ver sección final de este archivo para saber qué añadir en tu app.
    """

    def __init__(self):
        self.base = APP_URL.rstrip("/")
        self.sess = requests.Session()
        self.sess.headers["Content-Type"] = "application/json"
        if APP_API_KEY:
            self.sess.headers["Authorization"] = f"Bearer {APP_API_KEY}"

    def upsert(self, reservation: dict) -> bool:
        url = f"{self.base}/api/reservations"
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
def sync():
    log.info("=" * 60)
    log.info("🚀  Little Hotelier → Limpatex")
    log.info("=" * 60)

    today     = datetime.today()
    date_from = (today - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")
    date_to   = (today + timedelta(days=DAYS_AHEAD)).strftime("%Y-%m-%d")

    lh = LittleHotelierClient()
    reservations = lh.get_reservations(date_from, date_to)
    if not reservations:
        log.info("ℹ️   Sin reservas en el período.")
        return

    with open("reservas_cache.json", "w", encoding="utf-8") as f:
        json.dump(reservations, f, ensure_ascii=False, indent=2)
    log.info("💾  Copia local → reservas_cache.json")

    app = LimpatexAppClient()
    ok, errors = app.send_batch(reservations)

    log.info("─" * 60)
    log.info(f"✅  {ok} ok · {errors} errores")
    log.info("=" * 60)


def list_mode():
    """Muestra todas las reservas en tabla de texto."""
    days = int(next((sys.argv[i+1] for i, a in enumerate(sys.argv) if a == "--days"), DAYS_AHEAD))
    today     = datetime.today()
    date_from = (today - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")
    date_to   = (today + timedelta(days=days)).strftime("%Y-%m-%d")

    lh = LittleHotelierClient()
    reservations = lh.get_reservations(date_from, date_to)

    if not reservations:
        print("Sin reservas en el período.")
        return

    print(f"\n{'#':<4} {'ESTADO':<12} {'HUÉSPED':<25} {'REFERENCIA':<20} {'ENTRADA':<12} {'SALIDA':<12} {'HABITACIÓN':<18} {'CANAL'}")
    print("─" * 120)
    for i, r in enumerate(reservations, 1):
        rooms_str = ", ".join(r.get("rooms", [])) or "-"
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
    today     = datetime.today()
    date_from = (today - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")
    date_to   = (today + timedelta(days=DAYS_AHEAD)).strftime("%Y-%m-%d")

    lh = LittleHotelierClient()
    reservations = lh.get_reservations(date_from, date_to)

    print("\n" + "=" * 60)
    print(f"RESERVAS ({len(reservations)} encontradas):")
    print("=" * 60)
    if reservations:
        print(json.dumps(reservations[0], ensure_ascii=False, indent=2))
        if len(reservations) > 1:
            print(f"\n... y {len(reservations)-1} más.")
    else:
        print("Sin reservas. Revisa LH_SESSION_TOKEN y LH_PROPERTY_UUID.")


# ─────────────────────────────────────────────────────────────────
# PUNTO DE ENTRADA
# ─────────────────────────────────────────────────────────────────
def manual_login_mode():
    """Abre Chrome VISIBLE, espera a que el usuario inicie sesión manualmente
    y guarda el nuevo lh_session_token en .env.

    Uso cuando el token expira: el flujo OAuth con auth0 prompt=none no se
    puede automatizar headless porque Auth0 valida la redirect_uri contra
    una lista registrada que incluye parámetros dinámicos (userDeviceToken)
    imposibles de replicar fuera del navegador real del usuario.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.error("❌  Playwright no instalado. Ejecuta: pip install playwright && python -m playwright install chromium")
        return

    print()
    print("=" * 60)
    print("🌐  MODO LOGIN MANUAL")
    print("=" * 60)
    print()
    print("Se abrirá Chrome. Inicia sesión normalmente en Little Hotelier.")
    print("Cuando veas tu panel de reservas, el script capturará la cookie")
    print("y actualizará automáticamente el .env. (Tiempo máx: 5 minutos)")
    print()
    print("NO cierres la ventana hasta ver el mensaje de éxito.")
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

        if token:
            all_cookies = ctx.cookies()
            cookie_client = LittleHotelierClient()
            cookie_client._save_cookie_jar(all_cookies)
            # Guardar token en .env
            try:
                if os.path.exists(ENV_PATH):
                    with open(ENV_PATH, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    found = False
                    for i, line in enumerate(lines):
                        if line.startswith("LH_SESSION_TOKEN="):
                            lines[i] = f"LH_SESSION_TOKEN={token}\n"
                            found = True
                            break
                    if not found:
                        lines.append(f"LH_SESSION_TOKEN={token}\n")
                    with open(ENV_PATH, "w", encoding="utf-8") as f:
                        f.writelines(lines)
                else:
                    with open(ENV_PATH, "w", encoding="utf-8") as f:
                        f.write(f"LH_SESSION_TOKEN={token}\n")
                print()
                print("=" * 60)
                print("✅  TOKEN GUARDADO en .env")
                print("=" * 60)
                print(f"\n    LH_SESSION_TOKEN={token[:30]}...\n")
                print(f"    Cookies completas guardadas en: {COOKIE_JAR_PATH}")
                print()
                print("Ya puedes ejecutar normalmente:")
                print("    python little_hotelier_sync.py")
                print()
                print("Para Render: además del token, puedes copiar el contenido")
                print("de lh_cookies.json en Environment → LH_COOKIES_JSON.")
                print()
                print(f"Valor completo: {token}")
                print()
            except Exception as e:
                log.error(f"❌  Error guardando token: {e}")
                print(f"\nToken capturado (cópialo manualmente):\n{token}\n")
        else:
            print()
            print("❌  No se detectó login completado tras 5 minutos.")
            print("    ¿Llegaste a ver la página de reservas?")

        browser.close()


if __name__ == "__main__":
    import sys

    if "--login" in sys.argv:
        manual_login_mode()
    elif "--list" in sys.argv:
        list_mode()
    elif "--debug" in sys.argv:
        debug_mode()
    elif "--loop" in sys.argv:
        log.info(f"🔁  Bucle cada {LOOP_INTERVAL // 60} min")
        while True:
            try:
                sync()
            except Exception as e:
                log.exception(f"Error en ciclo: {e}")
            log.info(f"⏳  Próxima sync en {LOOP_INTERVAL // 60} min...")
            time.sleep(LOOP_INTERVAL)
    else:
        sync()


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
