#!/usr/bin/env python3
"""
x-tg-notify — Real-time X notification forwarder to Telegram bot.
Run: python3 bot.py          (auto-setup on first run)
     python3 bot.py --setup  (re-run setup wizard)
"""
import json, os, sys, time, asyncio, urllib.parse, logging, shutil, subprocess, re
from datetime import datetime
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"
WATCHLIST_FILE = BASE_DIR / "watchlist.json"
SEEN_FILE = BASE_DIR / "seen_state.json"
SETTINGS_FILE = BASE_DIR / "settings.json"
PID_FILE = BASE_DIR / ".bot.pid"

# ── Single Instance Lock ────────────────────────────────────────────
def acquire_lock():
    """Prevent multiple bot instances from running simultaneously."""
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            # Check if process is still alive
            os.kill(old_pid, 0)
            print(f"❌ Bot already running (PID {old_pid}). Kill it first or delete .bot.pid")
            sys.exit(1)
        except (ProcessLookupError, ValueError):
            pass  # Stale PID file — safe to continue
    PID_FILE.write_text(str(os.getpid()))

def release_lock():
    """Clean up PID file on exit."""
    try:
        if PID_FILE.exists() and PID_FILE.read_text().strip() == str(os.getpid()):
            PID_FILE.unlink()
    except Exception:
        pass

BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

# ── GraphQL Query IDs ────────────────────────────────────────────────
# X rotates these every few weeks. We ship known-good defaults, cache the
# live values to query_ids.json, refresh on staleness, and self-heal on 404.
# All List ops + UserTweets + UserByScreenName live in one main.<hash>.js
# bundle reachable in 2 requests; NotificationsTimeline is lazy-loaded so it
# keeps its hardcoded default (the notif path is a cheap optional secondary).
QUERY_IDS_FILE = BASE_DIR / "query_ids.json"
DEFAULT_QUERY_IDS = {
    "NotificationsTimeline": "ZhJlpN0aKcSkccG5Pz1MEw",
    "UserTweets": "V7H0Ap3_Hh2FyS75OCDO3Q",
    "UserByScreenName": "1VOOyvKkiI3FMmkeDNxM9A",
    "ListLatestTweetsTimeline": "27HKUy8ulrflZ9Tole038g",
    "ListAddMember": "E5PskyoAL2YZ6PsJDebfWg",
    "ListRemoveMember": "LNR-rIOOs8to-dcgUhLndw",
    "ListMembers": "H_0zFfjp73xGZrJpY-C2IQ",
}
# Ops that should trigger a scrape if missing/stale (all live in main.js).
SCRAPEABLE_OPS = ["UserTweets", "UserByScreenName", "ListLatestTweetsTimeline",
                  "ListAddMember", "ListRemoveMember", "ListMembers"]
QUERY_IDS = dict(DEFAULT_QUERY_IDS)
_qid_scraped_at = 0.0

def load_query_ids():
    """Load cached query IDs, merged over defaults."""
    global QUERY_IDS, _qid_scraped_at
    QUERY_IDS = dict(DEFAULT_QUERY_IDS)
    if QUERY_IDS_FILE.exists():
        try:
            cached = json.loads(QUERY_IDS_FILE.read_text())
            _qid_scraped_at = cached.pop("_scraped_at", 0.0)
            QUERY_IDS.update({k: v for k, v in cached.items() if v})
        except Exception:
            pass
    return QUERY_IDS

def save_query_ids():
    data = dict(QUERY_IDS)
    data["_scraped_at"] = _qid_scraped_at
    _atomic_write(QUERY_IDS_FILE, json.dumps(data, indent=2))

def qid(name: str) -> str:
    return QUERY_IDS.get(name, DEFAULT_QUERY_IDS.get(name, ""))

NOTIF_FEATURES = {
    "rweb_video_screen_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": False,
    "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
}

TWEET_FEATURES = {
    "rweb_video_screen_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
}

POLL_INTERVAL = 5
TIMELINE_INTERVAL = 60
LIST_POLL_INTERVAL = 3   # primary: poll the List timeline every N seconds (flat cost)
REQUIRED_ENV = ["X_AUTH_TOKEN", "X_CT0", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]

# ── Runtime Stats ──────────────────────────────────────────────────
START_TIME = time.time()
FORWARDED_COUNT = 0
AUTH_EXPIRED_SENT = False  # prevent spam

# ── Terminal Colors ─────────────────────────────────────────────────
class C:
    R = "\033[0;31m"; G = "\033[0;32m"; Y = "\033[0;33m"
    B = "\033[0;34m"; M = "\033[0;35m"; C = "\033[0;36m"
    W = "\033[0;37m"; BOLD = "\033[1m"; DIM = "\033[2m"; NC = "\033[0m"

def banner():
    print(f"""
{C.C}╔══════════════════════════════════════════════╗
║        🤖 {C.BOLD}x-tg-notify{C.NC}{C.C} — Setup Wizard         ║
╚══════════════════════════════════════════════╝{C.NC}""")

def step(n, total, title):
    print(f"\n{C.BOLD}Step {n}/{total}: {title}{C.NC}")

def hint(text):
    for line in text.strip().split("\n"):
        print(f"  {C.Y}{line.strip()}{C.NC}")

def ok(msg):
    print(f"  {C.G}✓ {msg}{C.NC}")

def fail(msg):
    print(f"  {C.R}✗ {msg}{C.NC}")

def warn(msg):
    print(f"  {C.Y}⚠ {msg}{C.NC}")

def prompt_input(label, required=True):
    while True:
        val = input(f"  {C.W}▌ {C.NC}").strip()
        if val or not required:
            return val
        print(f"  {C.R}Required — cannot be empty{C.NC}")

# ── Setup Wizard ───────────────────────────────────────────────────
def setup_wizard():
    """Interactive first-run setup. Returns True if setup completed."""
    banner()

    # ── Check Python version ──
    v = sys.version_info
    if v < (3, 10):
        fail(f"Python 3.10+ required (you have {v.major}.{v.minor})")
        return False
    ok(f"Python {v.major}.{v.minor}.{v.micro}")

    # ── Check/install venv + deps ──
    venv_dir = BASE_DIR / "venv"
    venv_python = venv_dir / "bin" / "python3"
    requirements = BASE_DIR / "requirements.txt"

    if not venv_dir.exists():
        print(f"\n  {C.DIM}Creating virtual environment...{C.NC}")
        import subprocess
        r = subprocess.run([sys.executable, "-m", "venv", str(venv_dir)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"Failed to create venv: {r.stderr[:200]}")
            return False
        ok("Virtual environment created")

    if not venv_python.exists():
        fail("venv python3 not found")
        return False

    # Install deps if needed
    if requirements.exists():
        print(f"  {C.DIM}Installing dependencies...{C.NC}")
        import subprocess
        pip = venv_dir / "bin" / "pip"
        r = subprocess.run([str(pip), "install", "-q", "-r", str(requirements)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            warn(f"pip install had warnings: {r.stderr[:150]}")
        else:
            ok("Dependencies installed")

    # ── Collect credentials ──
    print(f"\n{C.BOLD}{'━' * 44}")
    print(f"  Telegram Setup")
    print(f"{'━' * 44}{C.NC}")

    step(1, 4, "Telegram Bot Token")
    hint("1. Open @BotFather on Telegram")
    hint("2. Send /newbot, follow instructions")
    hint("3. Copy the token (format: 123456:ABC-DEF...)")
    tg_token = prompt_input("Bot Token:")

    step(2, 4, "Telegram Chat ID")
    hint("1. Send any message to your bot")
    hint("2. Open this URL in browser (replace <TOKEN>):")
    hint(f"   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates")
    hint('3. Find "chat":{"id":XXXXX} — paste that number')
    tg_chat = prompt_input("Chat ID:")

    print(f"\n{C.BOLD}{'━' * 44}")
    print(f"  X (Twitter) Setup")
    print(f"{'━' * 44}{C.NC}")

    step(3, 4, "X Auth Token")
    hint("1. Open https://x.com in browser, log in")
    hint("2. Press F12 → Application → Cookies → x.com")
    hint('3. Copy "auth_token" value (40-char hex)')
    x_auth = prompt_input("Auth Token:")

    step(4, 4, "X CT0")
    hint("1. Same cookie page as above")
    hint('2. Copy "ct0" value (long hex string)')
    x_ct0 = prompt_input("CT0:")

    # ── Validate ──
    print(f"\n{C.BOLD}{'━' * 44}")
    print(f"  Validation")
    print(f"{'━' * 44}{C.NC}")

    all_ok = True

    # Telegram
    print(f"\n  {C.DIM}Testing Telegram connection...{C.NC}")
    import httpx
    try:
        r = httpx.get(f"https://api.telegram.org/bot{tg_token}/getMe", timeout=10)
        if r.status_code == 200:
            bot = r.json().get("result", {})
            username = bot.get("username", "?")
            ok(f"Bot connected: @{username}")

            # Test send
            r2 = httpx.post(f"https://api.telegram.org/bot{tg_token}/sendMessage",
                            data={"chat_id": tg_chat,
                                  "text": "✅ x-tg-notify setup complete!\nYou can now run: python3 bot.py"},
                            timeout=10)
            if r2.status_code == 200:
                ok("Test message sent successfully")
            else:
                warn(f"Could not send test message (chat_id might be wrong)")
        else:
            fail(f"Invalid bot token (HTTP {r.status_code})")
            all_ok = False
    except Exception as e:
        fail(f"Telegram connection failed: {e}")
        all_ok = False

    # X credentials format
    print(f"\n  {C.DIM}Checking X credentials format...{C.NC}")
    if len(x_auth) >= 30 and all(c in "0123456789abcdef" for c in x_auth.lower()):
        ok(f"Auth token format OK ({len(x_auth)} chars)")
    else:
        warn(f"Auth token looks unusual ({len(x_auth)} chars) — double-check")
    if len(x_ct0) >= 30:
        ok(f"CT0 format OK ({len(x_ct0)} chars)")
    else:
        warn(f"CT0 looks too short ({len(x_ct0)} chars) — double-check")

    # X API full test (optional, non-blocking)
    print(f"\n  {C.DIM}Testing X API (optional)...{C.NC}")
    try:
        from curl_cffi import requests as cffi
        session = cffi.Session(impersonate="chrome136")
        session.cookies.set("auth_token", x_auth, domain=".x.com")
        session.cookies.set("ct0", x_ct0, domain=".x.com")
        session.cookies.set("lang", "en", domain=".x.com")
        r = session.get("https://x.com", timeout=10)
        if r.status_code == 200:
            ok("X homepage accessible")
        else:
            warn(f"X returned HTTP {r.status_code} — cookies may be expired")
    except ImportError:
        warn("curl_cffi not installed yet — X API test skipped")
    except Exception as e:
        warn(f"X API test failed: {e}")

    # ── Write .env ──
    print(f"\n  {C.DIM}Saving credentials...{C.NC}")
    env_content = f"""# x-tg-notify credentials
# Generated by setup wizard — run 'python3 bot.py --setup' to reconfigure

# X / Twitter Auth (from browser cookies)
X_AUTH_TOKEN={x_auth}
X_CT0={x_ct0}

# Telegram
TELEGRAM_BOT_TOKEN={tg_token}
TELEGRAM_CHAT_ID={tg_chat}
"""
    _atomic_write(ENV_FILE, env_content)
    ok(f"Credentials saved to .env")

    # ── Done ──
    print(f"""
{C.G}╔══════════════════════════════════════════════╗
║           ✅ Setup Complete!                  ║
╚══════════════════════════════════════════════╝{C.NC}

  Start the bot:  {C.BOLD}python3 bot.py{C.NC}
  Re-run setup:   {C.BOLD}python3 bot.py --setup{C.NC}
  Add accounts:   {C.BOLD}/add @username{C.NC} (via Telegram)
""")
    return all_ok

# ── Env Validation ─────────────────────────────────────────────────
def validate_env():
    """Check all required env vars are present."""
    missing = [v for v in REQUIRED_ENV if not ENV.get(v)]
    if missing:
        print(f"""
{C.R}╔══════════════════════════════════════════════╗
║         ⚠️  Configuration Missing             ║
╚══════════════════════════════════════════════╝{C.NC}

  Missing: {C.R}{', '.join(missing)}{C.NC}

  Run setup wizard:  {C.BOLD}python3 bot.py --setup{C.NC}
""")
        sys.exit(1)

def check_deps():
    """Verify all required packages are importable."""
    missing = []
    try:
        import curl_cffi  # noqa
    except ImportError:
        missing.append("curl_cffi")
    try:
        import x_client_transaction  # noqa
    except ImportError:
        missing.append("x-client-transaction-id")
    try:
        import httpx  # noqa
    except ImportError:
        missing.append("httpx")
    try:
        import bs4  # noqa
    except ImportError:
        missing.append("beautifulsoup4")
    if missing:
        print(f"""
{C.R}╔══════════════════════════════════════════════╗
║           ❌ Missing Dependencies            ║
╚══════════════════════════════════════════════╝{C.NC}

  Missing: {C.R}{', '.join(missing)}{C.NC}

  Install with:  {C.BOLD}pip install -r requirements.txt{C.NC}
  Or re-run:     {C.BOLD}python3 bot.py --setup{C.NC}
""")
        sys.exit(1)

# ── Atomic File I/O ─────────────────────────────────────────────────
def _atomic_write(path: Path, data: str):
    """Write to temp file then rename — prevents corruption."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(data)
    os.rename(tmp, path)

# ── Env ─────────────────────────────────────────────────────────────
def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"): continue
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env

ENV = load_env()
AUTH_TOKEN = ENV.get("X_AUTH_TOKEN", "")
CT0 = ENV.get("X_CT0", "")
TG_TOKEN = ENV.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = ENV.get("TELEGRAM_CHAT_ID", "")
# Optional: X List ID used for flat-cost timeline polling (the primary fetch path).
# When set, the bot polls this one List instead of N per-user timelines.
LIST_ID = ENV.get("X_LIST_ID", "").strip()

def load_watchlist():
    if WATCHLIST_FILE.exists():
        return json.loads(WATCHLIST_FILE.read_text())
    return {}

def save_watchlist(wl):
    _atomic_write(WATCHLIST_FILE, json.dumps(wl, indent=2, ensure_ascii=False))

def load_seen():
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text())
    return {"seen_tweet_ids": {}}

def save_seen(state):
    _atomic_write(SEEN_FILE, json.dumps(state))

DEFAULT_SETTINGS = {
    "photo_enabled": True,
    "video_enabled": False,
    "gif_enabled": True,
}

def load_settings():
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text())
            settings = DEFAULT_SETTINGS.copy()
            settings.update(saved)
            return settings
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()

def save_settings(settings):
    _atomic_write(SETTINGS_FILE, json.dumps(settings, indent=2))

# ── Rate Limiter ────────────────────────────────────────────────────
class RateLimiter:
    def __init__(self):
        self._last: dict[str, float] = {}
        self._backoff: dict[str, float] = {}

    def wait_time(self, key: str, base_interval: float) -> float:
        now = time.time()
        last = self._last.get(key, 0)
        backoff = self._backoff.get(key, 0)
        return max(0, base_interval - (now - last) + backoff)

    def mark_ok(self, key: str):
        self._last[key] = time.time()
        self._backoff[key] = 0

    def mark_fail(self, key: str, max_backoff: float = 60):
        self._last[key] = time.time()  # anchor backoff timer to the moment of failure
        self._backoff[key] = min(self._backoff.get(key, 0) * 2 + 1, max_backoff)
        log.warning(f"Rate limit backoff for {key}: {self._backoff[key]:.0f}s")

_rate = RateLimiter()

# ── Time Formatting ────────────────────────────────────────────────
def format_tweet_time(raw: str) -> str:
    """Parse X's raw created_at into neat format.
    e.g. 'Wed Mar 27 14:30:00 +0000 2026' → 'Mar 27, 2026 · 14:30 UTC'
    """
    try:
        dt = datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
        tz = dt.strftime("%Z") or "UTC"
        return dt.strftime(f"%b %d, %Y · %H:%M {tz}")
    except Exception:
        return raw

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("x-notify")
# Silence httpx per-request INFO spam (it drowned the real signal in bot.log).
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ── X API ──────────────────────────────────────────────────────────
from curl_cffi import requests as cffi
from x_client_transaction import ClientTransaction
from x_client_transaction.utils import generate_headers, get_ondemand_file_url
import bs4

_x_session = None

def get_x_session():
    global _x_session
    if _x_session is None:
        _x_session = cffi.Session(impersonate="chrome136")
        _x_session.cookies.set("auth_token", AUTH_TOKEN, domain=".x.com")
        _x_session.cookies.set("ct0", CT0, domain=".x.com")
        _x_session.cookies.set("lang", "en", domain=".x.com")
    return _x_session

_ct = None
_ct_time = 0

def get_ct():
    global _ct, _ct_time
    if _ct is None or (time.time() - _ct_time) > 2700:
        try:
            session = get_x_session()
            ct_headers = generate_headers()
            home = session.get("https://x.com", headers=ct_headers, timeout=10)
            home_resp = bs4.BeautifulSoup(home.content, "html.parser")
            ondemand_url = get_ondemand_file_url(response=home_resp)
            ondemand = session.get(ondemand_url, headers=ct_headers, timeout=10)
            _ct = ClientTransaction(home_page_response=home_resp, ondemand_file_response=ondemand.text)
            _ct_time = time.time()
            log.info("ClientTransaction refreshed")
        except Exception as e:
            log.error(f"CT refresh failed: {e}")
    return _ct

QID_MAX_AGE = 86400  # re-scrape query IDs if cache older than 24h

def scrape_query_ids(force=False) -> bool:
    """Scrape live GraphQL query IDs from x.com's main.<hash>.js bundle.
    Returns True if any ID changed. All scrapeable ops live in one bundle.
    """
    global QUERY_IDS, _qid_scraped_at
    if not force and (time.time() - _qid_scraped_at) < QID_MAX_AGE:
        return False
    try:
        s = get_x_session()
        ct_headers = generate_headers()
        home = s.get("https://x.com", headers=ct_headers, timeout=15)
        html = home.text
        # main bundle URL is embedded directly in the HTML
        m = re.search(r'https://abs\.twimg\.com/responsive-web/[^"\s]+?/main\.[a-f0-9]+\.js', html)
        if not m:
            log.warning("scrape_query_ids: main.js URL not found in HTML")
            return False
        main_js = s.get(m.group(0), headers=ct_headers, timeout=20).text
        changed = False
        for op in SCRAPEABLE_OPS:
            mm = re.search(r'queryId:"([\w-]{16,})",operationName:"' + op + '"', main_js)
            if not mm:
                mm = re.search(r'operationName:"' + op + r'",[^}]*?queryId:"([\w-]{16,})"', main_js)
            if mm and mm.group(1) != QUERY_IDS.get(op):
                QUERY_IDS[op] = mm.group(1)
                changed = True
        _qid_scraped_at = time.time()
        save_query_ids()
        if changed:
            log.info(f"Query IDs refreshed from main.js: { {k: QUERY_IDS[k] for k in SCRAPEABLE_OPS} }")
        return changed
    except Exception as e:
        log.warning(f"scrape_query_ids failed: {e}")
        return False


def xh(url="", method="GET"):
    h = {
        "Authorization": f"Bearer {BEARER}",
        "X-Csrf-Token": CT0,
        "X-Twitter-Active-User": "yes",
        "X-Twitter-Auth-Type": "OAuth2Session",
    }
    if url:
        try:
            ct = get_ct()
            path = urllib.parse.urlparse(url).path
            h["X-Client-Transaction-Id"] = ct.generate_transaction_id(method=method, path=path)
        except Exception as e:
            log.warning(f"CT header failed: {e}")
    return h

# ── X API Functions ────────────────────────────────────────────────
def resolve_user(username: str) -> dict | None:
    username = username.lstrip("@")
    v = json.dumps({"screen_name": username}, separators=(",", ":"))
    f = json.dumps({"responsive_web_graphql_exclude_directive_enabled": True}, separators=(",", ":"))
    url = f"https://x.com/i/api/graphql/{qid('UserByScreenName')}/UserByScreenName?variables={urllib.parse.quote(v)}&features={urllib.parse.quote(f)}"
    r = get_x_session().get(url, headers=xh(url), timeout=15)
    if r.status_code == 200:
        result = r.json().get("data", {}).get("user", {}).get("result", {})
        if result:
            legacy = result.get("legacy", {})
            return {"id": result["rest_id"], "name": legacy.get("name", ""), "screen_name": legacy.get("screen_name", username)}
    return None

def follow_user(uid: str) -> bool:
    url = "https://x.com/i/api/1.1/friendships/create.json"
    h = xh(url, "POST"); h["Content-Type"] = "application/x-www-form-urlencoded"
    r = get_x_session().post(url, headers=h, timeout=15, data=f"user_id={uid}&include_profile_interstitial_type=1")
    if r.status_code == 200:
        src = r.json().get("relationship", {}).get("source", {})
        following = src.get("following")
        if following is None:
            return _check_following(uid)
        return bool(following)
    return False

def _check_following(uid: str) -> bool:
    try:
        url = "https://x.com/i/api/1.1/friendships/show.json"
        h = xh(url, "GET")
        r = get_x_session().get(url, headers=h, params={"target_id": uid}, timeout=15)
        if r.status_code == 200:
            return r.json().get("relationship", {}).get("source", {}).get("following", False)
    except Exception:
        pass
    return False

def unfollow_user(uid: str) -> bool:
    url = "https://x.com/i/api/1.1/friendships/destroy.json"
    h = xh(url, "POST"); h["Content-Type"] = "application/x-www-form-urlencoded"
    r = get_x_session().post(url, headers=h, timeout=15, data=f"user_id={uid}&include_profile_interstitial_type=1")
    return r.status_code == 200

def enable_notifs(uid: str) -> bool:
    url = "https://x.com/i/api/1.1/friendships/update.json"
    h = xh(url, "POST"); h["Content-Type"] = "application/x-www-form-urlencoded"
    r = get_x_session().post(url, headers=h, timeout=15, data=f"id={uid}&device=True")
    if r.status_code == 200:
        return r.json().get("relationship", {}).get("source", {}).get("notifications_enabled", False)
    return False

def disable_notifs(uid: str) -> bool:
    url = "https://x.com/i/api/1.1/friendships/update.json"
    h = xh(url, "POST"); h["Content-Type"] = "application/x-www-form-urlencoded"
    r = get_x_session().post(url, headers=h, timeout=15, data=f"id={uid}&device=False")
    return r.status_code == 200

def fetch_notifications(count=40) -> list[dict]:
    v = json.dumps({"timeline_type": "All", "count": count}, separators=(",", ":"))
    f = json.dumps(NOTIF_FEATURES, separators=(",", ":"))
    url = f"https://x.com/i/api/graphql/{qid('NotificationsTimeline')}/NotificationsTimeline?variables={urllib.parse.quote(v)}&features={urllib.parse.quote(f)}"
    r = get_x_session().get(url, headers=xh(url), timeout=15)
    if r.status_code == 429:
        _rate.mark_fail("notif")
        return []
    elif r.status_code in (401, 403):
        log.error(f"X API auth failed ({r.status_code}) — token may be expired")
        if not AUTH_EXPIRED_SENT:
            asyncio.get_event_loop().create_task(tg_send_token_refresh_guidance())
        return []
    elif r.status_code != 200:
        log.warning(f"Notifications fetch failed: {r.status_code}")
        return []
    _rate.mark_ok("notif")
    try:
        timeline = r.json()["data"]["viewer_v2"]["user_results"]["result"]["notification_timeline"]["timeline"]
    except (KeyError, TypeError):
        return []
    notifs = []
    for inst in timeline.get("instructions", []):
        for entry in inst.get("entries", []):
            content = entry.get("content", {})
            if content.get("entryType") != "TimelineTimelineItem": continue
            item = content.get("itemContent", {})
            if item.get("__typename") != "TimelineNotification": continue
            notifs.append({
                "id": entry.get("entryId", ""),
                "icon": item.get("notification_icon", ""),
                "message": item.get("rich_message", {}).get("text", ""),
                "url": item.get("notification_url", {}).get("url", ""),
                "user_id": item.get("target_user", {}).get("id", ""),
            })
    return notifs

def _extract_tweet(tweet_result: dict) -> dict | None:
    """Parse a single GraphQL tweet_result into our normalized tweet dict.
    Shared by UserTweets and ListLatestTweetsTimeline. Adds reply detection:
    a tweet is a reply when it replies to SOMEONE ELSE; replying to your own
    tweet (self-thread) is treated as a post per spec.
    """
    if tweet_result.get("__typename") == "TweetWithVisibilityResults":
        tweet_result = tweet_result.get("tweet", {})
    legacy = tweet_result.get("legacy", {})
    if not legacy.get("full_text"):
        return None

    author_id = legacy.get("user_id_str", "")
    media_list = []
    photo_url = video_url = gif_url = None

    def _scan_media(src_legacy):
        nonlocal photo_url, video_url, gif_url
        for m in src_legacy.get("extended_entities", {}).get("media", []):
            url_m = m.get("media_url_https")
            if not url_m:
                continue
            mtype = m.get("type", "")
            media_list.append({"type": mtype, "url": url_m})
            if not photo_url and mtype == "photo":
                photo_url = url_m
            elif not video_url and mtype == "video":
                variants = m.get("video_info", {}).get("variants", [])
                best_v = max((v for v in variants if v.get("content_type") == "video/mp4"),
                             key=lambda v: v.get("bitrate", 0), default=None)
                if best_v and best_v.get("url"):
                    video_url = best_v["url"]  # KEEP ?tag= auth params
            elif not gif_url and mtype == "animated_gif":
                variants = m.get("video_info", {}).get("variants", [])
                if variants and variants[0].get("url"):
                    gif_url = variants[0]["url"]

    _scan_media(legacy)

    user_result = tweet_result.get("core", {}).get("user_results", {}).get("result", {})
    u_legacy = user_result.get("legacy", {}) if user_result else {}
    # X moved screen_name/name into user_results.result.core (nested); fall back to legacy.
    u_core = user_result.get("core", {}) if user_result else {}

    is_rt = bool(legacy.get("retweeted_status_result"))
    is_quote = bool(legacy.get("is_quote_status")) and bool(legacy.get("quoted_status_result"))
    quote_text = quote_user = ""
    if is_quote:
        q_result = legacy["quoted_status_result"].get("result", {})
        if q_result.get("__typename") == "TweetWithVisibilityResults":
            q_result = q_result.get("tweet", {})
        q_legacy = q_result.get("legacy", {})
        q_user_res = q_result.get("core", {}).get("user_results", {}).get("result", {})
        quote_text = q_legacy.get("full_text", "")
        quote_user = (q_user_res.get("core", {}).get("screen_name")
                      or q_user_res.get("legacy", {}).get("screen_name", ""))

    # Reply detection (only for non-RTs). Self-thread = post.
    reply_to_uid = legacy.get("in_reply_to_user_id_str", "")
    is_reply = (not is_rt) and bool(legacy.get("in_reply_to_status_id_str")) \
               and reply_to_uid and reply_to_uid != author_id

    if is_rt:
        rt_result = legacy["retweeted_status_result"].get("result", {})
        if rt_result.get("__typename") == "TweetWithVisibilityResults":
            rt_result = rt_result.get("tweet", {})
        rt_legacy = rt_result.get("legacy", {})
        rt_user_res = rt_result.get("core", {}).get("user_results", {}).get("result", {})
        rt_core = rt_user_res.get("core", {})
        rt_legacy_u = rt_user_res.get("legacy", {})
        text = rt_legacy.get("full_text", legacy.get("full_text", ""))
        user_screen = rt_core.get("screen_name") or rt_legacy_u.get("screen_name", "")
        user_name = rt_core.get("name") or rt_legacy_u.get("name", "")
        if not media_list:
            _scan_media(rt_legacy)
    else:
        text = legacy.get("full_text", "")
        user_screen = u_core.get("screen_name") or u_legacy.get("screen_name", "")
        user_name = u_core.get("name") or u_legacy.get("name", "")

    return {
        "id": legacy.get("id_str", ""),
        "text": text,
        "user": user_screen,
        "user_name": user_name,
        "author_id": author_id,
        "created": legacy.get("created_at", ""),
        "is_retweet": is_rt,
        "is_quote": is_quote,
        "is_reply": is_reply,
        "quote_text": quote_text,
        "quote_user": quote_user,
        "photo_url": photo_url,
        "video_url": video_url,
        "gif_url": gif_url,
        "media": media_list,
    }

def fetch_user_tweets(user_id: str, count=5, rl_key: str = "tweets") -> list[dict]:
    v = json.dumps({
        "userId": user_id, "count": count,
        "includePromotedContent": False,
        "withQuickPromoteEligibilityTweetFields": True,
        "withVoice": True, "withV2Timeline": True,
    }, separators=(",", ":"))
    f = json.dumps(TWEET_FEATURES, separators=(",", ":"))
    url = f"https://x.com/i/api/graphql/{qid('UserTweets')}/UserTweets?variables={urllib.parse.quote(v)}&features={urllib.parse.quote(f)}"
    r = get_x_session().get(url, headers=xh(url), timeout=15)
    if r.status_code == 200:
        _rate.mark_ok(rl_key)
    elif r.status_code == 429:
        _rate.mark_fail(rl_key)
        return []
    elif r.status_code == 404:
        log.warning("UserTweets 404 — query id may be stale, triggering re-scrape")
        scrape_query_ids(force=True)
        return []
    else:
        log.warning(f"UserTweets failed: {r.status_code}")
        return []

    data = r.json()
    instructions = data.get("data", {}).get("user", {}).get("result", {}).get("timeline_v2", {}).get("timeline", {}).get("instructions", [])
    tweets = []
    for inst in instructions:
        for entry in inst.get("entries", []):
            content = entry.get("content", {})
            if content.get("entryType") != "TimelineTimelineItem":
                continue
            tweet_result = content.get("itemContent", {}).get("tweet_results", {}).get("result", {})
            t = _extract_tweet(tweet_result)
            if t:
                tweets.append(t)
    return tweets

def fetch_list_timeline(list_id: str, count=40, rl_key: str = "list") -> list[dict]:
    """Poll ListLatestTweetsTimeline — one request returns newest tweets across
    ALL list members, time-sorted. Flat cost regardless of member count."""
    v = json.dumps({"listId": list_id, "count": count}, separators=(",", ":"))
    f = json.dumps(TWEET_FEATURES, separators=(",", ":"))
    url = f"https://x.com/i/api/graphql/{qid('ListLatestTweetsTimeline')}/ListLatestTweetsTimeline?variables={urllib.parse.quote(v)}&features={urllib.parse.quote(f)}"
    r = get_x_session().get(url, headers=xh(url), timeout=15)
    if r.status_code == 200:
        _rate.mark_ok(rl_key)
    elif r.status_code == 429:
        _rate.mark_fail(rl_key)
        return []
    elif r.status_code == 404:
        log.warning("ListLatestTweetsTimeline 404 — query id stale, triggering re-scrape")
        scrape_query_ids(force=True)
        return []
    elif r.status_code in (401, 403):
        log.error(f"List timeline auth failed ({r.status_code})")
        if not AUTH_EXPIRED_SENT:
            asyncio.get_event_loop().create_task(tg_send_token_refresh_guidance())
        return []
    else:
        log.warning(f"ListLatestTweetsTimeline failed: {r.status_code}")
        return []

    data = r.json()
    instructions = data.get("data", {}).get("list", {}).get("tweets_timeline", {}).get("timeline", {}).get("instructions", [])
    tweets = []
    for inst in instructions:
        for entry in inst.get("entries", []):
            content = entry.get("content", {})
            if content.get("entryType") != "TimelineTimelineItem":
                continue
            tweet_result = content.get("itemContent", {}).get("tweet_results", {}).get("result", {})
            t = _extract_tweet(tweet_result)
            if t:
                tweets.append(t)
    return tweets

def list_add_member(list_id: str, user_id: str) -> bool:
    url = f"https://x.com/i/api/graphql/{qid('ListAddMember')}/ListAddMember"
    h = xh(url, "POST"); h["Content-Type"] = "application/json"
    payload = {"variables": {"listId": list_id, "userId": str(user_id)}, "queryId": qid("ListAddMember")}
    r = get_x_session().post(url, headers=h, timeout=15, data=json.dumps(payload))
    if r.status_code == 200 and r.json().get("data", {}).get("list"):
        return True
    if r.status_code == 404:
        log.warning("ListAddMember 404 — query id stale, re-scraping")
        scrape_query_ids(force=True)
    else:
        log.warning(f"ListAddMember failed: {r.status_code} {r.text[:120]}")
    return False

def list_remove_member(list_id: str, user_id: str) -> bool:
    url = f"https://x.com/i/api/graphql/{qid('ListRemoveMember')}/ListRemoveMember"
    h = xh(url, "POST"); h["Content-Type"] = "application/json"
    payload = {"variables": {"listId": list_id, "userId": str(user_id)}, "queryId": qid("ListRemoveMember")}
    r = get_x_session().post(url, headers=h, timeout=15, data=json.dumps(payload))
    if r.status_code == 200 and r.json().get("data", {}).get("list"):
        return True
    if r.status_code == 404:
        scrape_query_ids(force=True)
    else:
        log.warning(f"ListRemoveMember failed: {r.status_code} {r.text[:120]}")
    return False

# ── Telegram ───────────────────────────────────────────────────────
import httpx
TG_API = f"https://api.telegram.org/bot{TG_TOKEN}"
_tg_client: httpx.AsyncClient | None = None

async def get_tg() -> httpx.AsyncClient:
    global _tg_client
    if _tg_client is None or _tg_client.is_closed:
        _tg_client = httpx.AsyncClient(timeout=15)
    return _tg_client

async def tg_send(text, chat_id=TG_CHAT, photo=None, video=None, animation=None,
                  button_url=None, button_text="🔗 Go to Post", buttons=None, buttons_grid=None):
    """Send rich formatted message with fallback."""
    await tg_send_rich(text, chat_id=chat_id, photo=photo, video=video,
                       animation=animation, button_url=button_url,
                       button_text=button_text, buttons=buttons, buttons_grid=buttons_grid)

async def tg_send_rich(text, chat_id=TG_CHAT, photo=None, video=None, animation=None,
                       button_url=None, button_text="🔗 Go to Post", buttons=None, buttons_grid=None):
    """Send rich formatted message with embedded media using sendRichMessage API."""
    c = await get_tg()
    reply_markup = None
    if buttons_grid:
        # Pre-built list of rows (list[list[button]]).
        reply_markup = json.dumps({"inline_keyboard": buttons_grid})
    elif buttons:
        reply_markup = json.dumps({"inline_keyboard": [buttons]})
    elif button_url:
        reply_markup = json.dumps({"inline_keyboard": [[{"text": button_text, "url": button_url}]]})

    # Build rich markdown with embedded media
    md = text
    if photo:
        md += f"\n\n![]({photo})"
    elif video:
        md += f"\n\n![]({video})"
    elif animation:
        md += f"\n\n![]({animation})"

    payload = {
        "chat_id": chat_id,
        "rich_message": json.dumps({"markdown": md}),
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        r = await c.post(f"{TG_API}/sendRichMessage", data=payload)
        if r.status_code == 200:
            return
        # Fallback to legacy if rich message fails
        log.warning(f"sendRichMessage failed ({r.status_code}), falling back to legacy")
        await _tg_send_legacy(text, chat_id=chat_id, photo=photo, video=video,
                              animation=animation, reply_markup=reply_markup)
    except Exception as e:
        log.warning(f"tg_send_rich failed: {e}")
        await _tg_send_legacy(text, chat_id=chat_id, photo=photo, video=video,
                              animation=animation, reply_markup=reply_markup)

async def _tg_send_legacy(text, chat_id=TG_CHAT, photo=None, video=None, animation=None,
                          reply_markup=None):
    """Legacy sendMessage/sendPhoto/sendVideo fallback."""
    c = await get_tg()
    try:
        if photo:
            data = {"chat_id": chat_id, "caption": text, "parse_mode": "HTML", "photo": photo}
            if reply_markup: data["reply_markup"] = reply_markup
            await c.post(f"{TG_API}/sendPhoto", data=data)
        elif video:
            data = {"chat_id": chat_id, "caption": text, "parse_mode": "HTML", "video": video}
            if reply_markup: data["reply_markup"] = reply_markup
            await c.post(f"{TG_API}/sendVideo", data=data)
        elif animation:
            data = {"chat_id": chat_id, "caption": text, "parse_mode": "HTML", "animation": animation}
            if reply_markup: data["reply_markup"] = reply_markup
            await c.post(f"{TG_API}/sendAnimation", data=data)
        else:
            payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
            if reply_markup: payload["reply_markup"] = reply_markup
            await c.post(f"{TG_API}/sendMessage", json=payload)
    except Exception as e:
        log.warning(f"tg_send_legacy failed: {e}")

async def tg_set_commands():
    """Register the bot command menu (the '/' button + Menu in Telegram)."""
    c = await get_tg()
    commands = [
        {"command": "add", "description": "Follow a user + add to List"},
        {"command": "remove", "description": "Unfollow + remove from List"},
        {"command": "filter", "description": "Toggle posts/replies for a user"},
        {"command": "list", "description": "Show watched users + filters"},
        {"command": "status", "description": "Bot status + stats"},
        {"command": "settings", "description": "Toggle media types"},
        {"command": "help", "description": "Show help"},
    ]
    try:
        r = await c.post(f"{TG_API}/setMyCommands", json={"commands": commands})
        if r.status_code == 200 and r.json().get("ok"):
            log.info("Telegram command menu registered")
        else:
            log.warning(f"setMyCommands failed: {r.status_code} {r.text[:120]}")
    except Exception as e:
        log.warning(f"setMyCommands error: {e}")

async def tg_answer_callback(callback_id, text="", show_alert=False):
    c = await get_tg()
    try:
        await c.post(f"{TG_API}/answerCallbackQuery", json={
            "callback_query_id": callback_id, "text": text, "show_alert": show_alert
        })
    except Exception as e:
        log.warning(f"tg_answer_callback failed: {e}")

async def tg_send_token_refresh_guidance():
    """Send token refresh instructions when X auth expires."""
    global AUTH_EXPIRED_SENT
    if AUTH_EXPIRED_SENT:
        return
    AUTH_EXPIRED_SENT = True
    msg = (
        "## ⚠️ X Auth Token Expired\n\n"
        "Your session cookies have expired. The bot cannot fetch notifications.\n\n"
        "**How to fix:**\n"
        "1. Open https://x.com in your browser\n"
        "2. Log in to your account\n"
        "3. Press F12 → Application → Cookies → x.com\n"
        '4. Copy new "auth_token" (40-char hex)\n'
        '5. Copy new "ct0" (long hex)\n\n'
        "Then run: `python3 bot.py --setup`\n"
        "Or edit .env directly with the new values.\n\n"
        "_This message won't repeat until restart._"
    )
    await tg_send(msg)

def format_uptime(seconds: float) -> str:
    """Format seconds into human-readable uptime."""
    d = int(seconds // 86400)
    h = int((seconds % 86400) // 3600)
    m = int((seconds % 3600) // 60)
    if d > 0:
        return f"{d}d {h}h {m}m"
    elif h > 0:
        return f"{h}h {m}m"
    else:
        return f"{m}m"

async def tg_get_updates(offset=0, timeout=3):
    c = await get_tg()
    try:
        # HTTP read timeout must exceed the long-poll timeout.
        r = await c.get(f"{TG_API}/getUpdates",
                        params={"offset": offset, "timeout": timeout},
                        timeout=timeout + 10)
        if r.status_code == 200:
            return r.json().get("result", [])
        elif r.status_code == 409:
            log.warning("409 Conflict — another instance running?")
            await asyncio.sleep(5)
    except Exception as e:
        log.warning(f"TG getUpdates error: {e}")
    return []

# ── Commands ────────────────────────────────────────────────────────
async def handle_callback(cb, watchlist, settings, seen_tweet_ids, rebuild_id_map, list_active):
    """Handle an inline-button callback_query."""
    cb_data = cb.get("data", "")
    cb_id = cb.get("id", "")
    cb_chat = str(cb.get("message", {}).get("chat", {}).get("id", ""))
    cb_msg_id = cb.get("message", {}).get("message_id")

    if cb_data.startswith("stop:"):
        uname = cb_data.split(":", 1)[1]
        if uname in watchlist:
            uid = watchlist[uname]["id"]
            name = watchlist[uname]["name"]
            disable_notifs(uid); await asyncio.sleep(0.5)
            unfollow_user(uid)
            if list_active and LIST_ID:
                list_remove_member(LIST_ID, uid)
            del watchlist[uname]; save_watchlist(watchlist)
            rebuild_id_map()
            await tg_answer_callback(cb_id, f"🔕 Stopped @{uname}")
            await tg_send(f"🔕 Stopped <b>{name}</b> (@{uname})\n• Unfollowed\n• Removed from List", cb_chat)
            log.info(f"Stop notify: @{uname}")
        else:
            await tg_answer_callback(cb_id, f"@{uname} not in watchlist", show_alert=True)
        return

    if cb_data.startswith("filt:"):
        # filt:<posts|replies>:<uname>
        _, which, uname = cb_data.split(":", 2)
        if uname not in watchlist:
            await tg_answer_callback(cb_id, f"@{uname} not in watchlist", show_alert=True)
            return
        info = ensure_filter_flags(watchlist[uname])
        info[which] = not bool(info.get(which, which == "posts"))
        save_watchlist(watchlist)
        state = "ON 🟢" if info[which] else "OFF 🔴"
        label = "Posts" if which == "posts" else "Replies"
        await tg_answer_callback(cb_id, f"{label}: {state}")
        # Rebuild the filter keyboard in place.
        c = await get_tg()
        try:
            await c.post(f"{TG_API}/editMessageReplyMarkup", json={
                "chat_id": cb_chat,
                "message_id": cb_msg_id,
                "reply_markup": {"inline_keyboard": build_filter_buttons(uname, info)},
            })
        except Exception as e:
            log.warning(f"edit filter markup failed: {e}")
        return

    if cb_data.startswith("toggle_"):
        media_type = cb_data.split("_", 1)[1]
        key = f"{media_type}_enabled"
        settings[key] = not settings.get(key, False)
        save_settings(settings)
        emoji = "📷" if media_type == "photo" else "🎬" if media_type == "video" else "🎞"
        state = "ON 🟢" if settings[key] else "OFF 🔴"
        await tg_answer_callback(cb_id, f"{emoji} {media_type.title()}: {state}")
        photo_on = settings.get("photo_enabled", True)
        video_on = settings.get("video_enabled", False)
        gif_on = settings.get("gif_enabled", True)
        buttons = [
            [{"text": f"📷 Photo: {'ON 🟢' if photo_on else 'OFF 🔴'}", "callback_data": "toggle_photo"}],
            [{"text": f"🎬 Video: {'ON 🟢' if video_on else 'OFF 🔴'}", "callback_data": "toggle_video"}],
            [{"text": f"🎞 GIF: {'ON 🟢' if gif_on else 'OFF 🔴'}", "callback_data": "toggle_gif"}],
        ]
        c = await get_tg()
        try:
            await c.post(f"{TG_API}/editMessageReplyMarkup", json={
                "chat_id": cb_chat, "message_id": cb_msg_id,
                "reply_markup": {"inline_keyboard": buttons},
            })
        except Exception as e:
            log.warning(f"edit media markup failed: {e}")
        return

async def handle_cmd(text, chat_id, watchlist, settings, seen_tweet_ids=None, list_active=False):
    parts = text.strip().split()
    cmd = parts[0].lower()

    if cmd == "/add" and len(parts) >= 2:
        uname = parts[1].lstrip("@")
        if uname in watchlist:
            await tg_send(f"⚠️ @{uname} already in watchlist.", chat_id); return
        await tg_send(f"⏳ Resolving @{uname}...", chat_id)
        user = resolve_user(uname)
        if not user:
            await tg_send(f"❌ @{uname} not found.", chat_id); return
        uid, name = user["id"], user["name"]

        if not follow_user(uid):
            await asyncio.sleep(2)
            if not follow_user(uid):
                await tg_send(f"❌ Failed to follow @{uname}.", chat_id); return

        await asyncio.sleep(1)
        notif_ok = enable_notifs(uid)

        # Add to the X List (primary polling path) when configured.
        list_ok = False
        if list_active and LIST_ID:
            list_ok = list_add_member(LIST_ID, uid)

        # New entries default to posts ON, replies OFF.
        watchlist[uname] = ensure_filter_flags(
            {"id": uid, "name": name, "added": datetime.now().isoformat()}
        )
        save_watchlist(watchlist)

        # Seed seen_state so we DON'T flood old posts. Baseline = newest existing tweet.
        if seen_tweet_ids is not None:
            try:
                existing = fetch_user_tweets(uid, 10, rl_key=f"tweets:{uname}")
                baseline = "0"
                for t in existing:
                    if t.get("is_retweet"):
                        continue
                    if t["id"] > baseline:
                        baseline = t["id"]
                # If we couldn't read the timeline (rate-limited/empty), use a snowflake
                # floor for "now" instead of "0" so we never dump the whole back-catalogue.
                # Twitter snowflake = ((ms_since_twitter_epoch) << 22); epoch = 1288834974657.
                if baseline == "0":
                    now_ms = int(time.time() * 1000)
                    baseline = str((now_ms - 1288834974657) << 22)
                seen_tweet_ids[uname] = baseline
                save_seen({"seen_tweet_ids": seen_tweet_ids})
                log.info(f"Seeded @{uname} on add: {baseline}")
            except Exception as e:
                log.warning(f"Seed-on-add failed for @{uname}: {e}")
                now_ms = int(time.time() * 1000)
                seen_tweet_ids[uname] = str((now_ms - 1288834974657) << 22)  # snowflake floor = now

        icon = "🔔" if notif_ok else "🔕"
        list_line = ""
        if list_active:
            list_line = f"\n• List {'✅' if list_ok else '⚠️ failed'}"
        await tg_send(
            f"✅ Added <b>{name}</b>\n"
            f"👤 <a href=\"https://x.com/{uname}\">@{uname}</a>\n"
            f"• Followed ✅\n• Notifications {icon}{list_line}\n\n"
            f"Pick what to forward:",
            chat_id,
            buttons_grid=build_filter_buttons(uname, watchlist[uname]),
        )

    elif cmd == "/remove" and len(parts) >= 2:
        uname = parts[1].lstrip("@")
        if uname not in watchlist:
            await tg_send(f"⚠️ @{uname} not in watchlist.", chat_id); return
        uid = watchlist[uname]["id"]; name = watchlist[uname]["name"]
        disable_notifs(uid); await asyncio.sleep(0.5)
        unfollow_user(uid)
        if list_active and LIST_ID:
            list_remove_member(LIST_ID, uid)
        del watchlist[uname]; save_watchlist(watchlist)
        await tg_send(f"✅ Removed <b>{name}</b> (@{uname})\n• Notifications OFF 🔕\n• Unfollowed\n• Removed from List", chat_id)

    elif cmd == "/list":
        if not watchlist:
            await tg_send("📋 Watchlist empty.\nUse /add @username.", chat_id); return
        lines = ["📋 <b>Watchlist:</b>"]
        for i, (u, info) in enumerate(watchlist.items(), 1):
            ensure_filter_flags(info)
            p = "📝" if info.get("posts", True) else "▫️"
            rp = "💬" if info.get("replies", False) else "▫️"
            lines.append(f"  {i}. <b>{info['name']}</b> — <a href=\"https://x.com/{u}\">@{u}</a>  {p}{rp}")
        lines.append("\n<i>📝=posts 💬=replies. Use /filter @user to change.</i>")
        await tg_send("\n".join(lines), chat_id)

    elif cmd == "/filter" and len(parts) >= 2:
        uname = parts[1].lstrip("@")
        if uname not in watchlist:
            await tg_send(f"⚠️ @{uname} not in watchlist.", chat_id); return
        info = ensure_filter_flags(watchlist[uname])
        await tg_send(
            f"🎛 <b>Filter for @{uname}</b>\n\nToggle what gets forwarded:",
            chat_id,
            buttons_grid=build_filter_buttons(uname, info),
        )

    elif cmd == "/status":
        uptime = format_uptime(time.time() - START_TIME)
        wl_count = len(watchlist)
        wl_names = ", ".join(f"@{u}" for u in watchlist.keys()) or "none"
        
        # Rate limit info
        notif_backoff = _rate._backoff.get("notif", 0)
        tweet_backoff = max(_rate._backoff.get(f"tweets:{u}", 0) for u in watchlist) if watchlist else 0
        
        status = (
            f"📊 <b>Bot Status</b>\n\n"
            f"⏱ Uptime: {uptime}\n"
            f"📤 Forwarded: {FORWARDED_COUNT} posts\n"
            f"📋 Watching: {wl_count} accounts\n"
            f"👤 {wl_names}\n\n"
            f"⚡ Rate Limits:\n"
            f"• Notif backoff: {notif_backoff:.0f}s\n"
            f"• Tweet backoff: {tweet_backoff:.0f}s\n\n"
            f"🔑 Auth: {'⚠️ EXPIRED' if AUTH_EXPIRED_SENT else '✅ OK'}"
        )
        await tg_send(status, chat_id)

    elif cmd == "/settings":
        photo_on = settings.get("photo_enabled", True)
        video_on = settings.get("video_enabled", False)
        gif_on = settings.get("gif_enabled", True)
        buttons = [
            [{"text": f"📷 Photo: {'ON 🟢' if photo_on else 'OFF 🔴'}", "callback_data": "toggle_photo"}],
            [{"text": f"🎬 Video: {'ON 🟢' if video_on else 'OFF 🔴'}", "callback_data": "toggle_video"}],
            [{"text": f"🎞 GIF: {'ON 🟢' if gif_on else 'OFF 🔴'}", "callback_data": "toggle_gif"}],
        ]
        keyboard = json.dumps({"inline_keyboard": buttons})
        c = await get_tg()
        await c.post(f"{TG_API}/sendMessage", json={
            "chat_id": chat_id,
            "text": f"⚙️ <b>Settings</b>\n\n"
                    f"Posts always sent. Toggle media types:",
            "parse_mode": "HTML",
            "reply_markup": keyboard
        })

    elif cmd in ("/start", "/help", "/"):
        await tg_send(
            "## 🤖 X Notify Bot\n\n"
            "**Commands:**\n\n"
            "| Command | Description |\n"
            "|---------|-------------|\n"
            "| `/add @username` | Follow + add to List + notify |\n"
            "| `/remove @username` | Unfollow + remove from List |\n"
            "| `/filter @username` | Toggle posts/replies for a user |\n"
            "| `/list` | Show watched users + filters |\n"
            "| `/status` | Bot status + stats |\n"
            "| `/settings` | Toggle media types |\n"
            "| `/help` | Show this message |\n\n"
            "---\n\n"
            "**Per-user filters:** at `/add` (or `/filter`) toggle 📝 Posts / 💬 Replies. "
            "Default: posts ON, replies OFF. Self-threads count as posts.\n\n"
            "Media toggles (`/settings`) only control attachments, never block a post.",
            chat_id
        )

# ── Main ────────────────────────────────────────────────────────────
def ensure_filter_flags(info: dict) -> dict:
    """Backfill per-user filter flags on a watchlist entry. Default posts ON,
    replies OFF (low-noise default)."""
    if "posts" not in info:
        info["posts"] = True
    if "replies" not in info:
        info["replies"] = False
    return info

def passes_filter(tweet: dict, info: dict) -> bool:
    """Apply a watched user's post/reply filter. Retweets & quotes follow the
    'posts' flag (they're not replies). Self-threads are posts (handled in
    _extract_tweet). A reply only forwards when replies flag is ON."""
    if tweet.get("is_reply"):
        return bool(info.get("replies", False))
    return bool(info.get("posts", True))

def build_filter_buttons(uname: str, info: dict) -> list:
    """Inline keyboard rows for toggling a user's post/reply filters."""
    posts_on = bool(info.get("posts", True))
    replies_on = bool(info.get("replies", False))
    return [
        [
            {"text": f"📝 Posts: {'ON 🟢' if posts_on else 'OFF 🔴'}", "callback_data": f"filt:posts:{uname}"},
            {"text": f"💬 Replies: {'ON 🟢' if replies_on else 'OFF 🔴'}", "callback_data": f"filt:replies:{uname}"},
        ],
        [
            {"text": "👤 View Profile", "url": f"https://x.com/{uname}"},
            {"text": "🔕 Stop Notify", "callback_data": f"stop:{uname}"},
        ],
    ]

async def forward_tweet(tweet: dict, uname: str, info: dict, settings: dict):
    """Render + send one tweet to Telegram, honoring media settings."""
    global FORWARDED_COUNT
    name = info.get("name", uname)
    tid = tweet["id"]
    tweet_url = f"https://x.com/{uname}/status/{tid}"
    profile_url = f"https://x.com/{uname}"
    post_time = format_tweet_time(tweet.get("created", ""))

    tag = ""
    if tweet.get("is_reply"):
        tag = " 💬"
    elif tweet.get("is_retweet"):
        tag = " 🔁"
    elif tweet.get("is_quote"):
        tag = " ↪️"

    quote_block = ""
    if tweet.get("is_quote") and tweet.get("quote_text"):
        q_user = tweet.get("quote_user", "")
        q_text = tweet.get("quote_text", "")[:200]
        quote_block = f"\n\n> ↪️ **@{q_user}**: {q_text}"

    tweet_text = tweet["text"][:400]
    if len(tweet.get("text", "")) > 200:
        tweet_text = f"> {tweet['text'][:150]}...\n>\n> (click Go to Post for full text)"

    caption = (
        f"## 🔔 New Post{tag}\n\n"
        f"**{name}** ([@{uname}]({profile_url}))\n\n"
        f"{tweet_text}{quote_block}\n\n"
        f"---\n🕐 {post_time}"
    )
    buttons = [
        {"text": "🔗 Go to Post", "url": tweet_url},
        {"text": "🔕 Stop Notify", "callback_data": f"stop:{uname}"},
    ]

    photo_enabled = settings.get("photo_enabled", True)
    video_enabled = settings.get("video_enabled", False)
    gif_enabled = settings.get("gif_enabled", True)
    if video_enabled and tweet.get("video_url"):
        await tg_send(caption, video=tweet["video_url"], buttons=buttons)
    elif gif_enabled and tweet.get("gif_url"):
        await tg_send(caption, animation=tweet["gif_url"], buttons=buttons)
    elif photo_enabled and tweet.get("photo_url"):
        await tg_send(caption, photo=tweet["photo_url"], buttons=buttons)
    else:
        await tg_send(caption, buttons=buttons)
    FORWARDED_COUNT += 1
    log.info(f"Forwarded @{uname} {tid} (reply={tweet.get('is_reply')})")

async def main():
    global FORWARDED_COUNT, AUTH_EXPIRED_SENT
    log.info("Starting x-tg-notify...")
    get_x_session()
    get_ct()
    log.info("X session ready")

    # Refresh GraphQL query IDs from x.com (cached; scrapes if stale/missing).
    load_query_ids()
    scrape_query_ids()

    watchlist = load_watchlist()
    # Backfill per-user post/reply filter flags on existing entries.
    for _u, _i in watchlist.items():
        ensure_filter_flags(_i)
    save_watchlist(watchlist)
    log.info(f"Watchlist: {list(watchlist.keys())}")
    settings = load_settings()
    log.info(f"Settings: {settings}")

    state = load_seen()
    seen_tweet_ids = state.get("seen_tweet_ids", {})

    # id -> uname map for fast author matching in the List timeline.
    id_to_uname: dict[str, str] = {}
    def rebuild_id_map():
        id_to_uname.clear()
        for un, inf in watchlist.items():
            uid = inf.get("id", "")
            if uid:
                id_to_uname[str(uid)] = un
    rebuild_id_map()

    # ── Migrate existing watchlist members into the X List ──────────
    list_active = bool(LIST_ID)
    if list_active:
        log.info(f"List mode ON (list_id={LIST_ID}); migrating {len(watchlist)} members")
        for uname, info in watchlist.items():
            uid = info.get("id", "")
            if uid:
                ok = list_add_member(LIST_ID, uid)
                if ok:
                    log.info(f"List+ @{uname}")
                await asyncio.sleep(0.5)
    else:
        log.warning("X_LIST_ID not set — falling back to per-user timeline polling (does not scale).")

    # ── Seed seen_state so we don't flood old posts on startup ──────
    if list_active:
        tweets = fetch_list_timeline(LIST_ID, count=40, rl_key="list")
        # Newest tweet id per author becomes the baseline.
        for t in tweets:
            au = id_to_uname.get(t.get("author_id", ""))
            if not au:
                continue
            if t["id"] > seen_tweet_ids.get(au, "0"):
                seen_tweet_ids[au] = t["id"]
        # Any watched user with no tweets in the window gets a snowflake floor.
        now_ms = int(time.time() * 1000)
        floor = str((now_ms - 1288834974657) << 22)
        for uname in watchlist:
            seen_tweet_ids.setdefault(uname, floor)
        log.info(f"Seeded {len(seen_tweet_ids)} users from list timeline")
    else:
        for uname, info in watchlist.items():
            uid = info.get("id", "")
            if not uid:
                continue
            tweets = fetch_user_tweets(uid, 10, rl_key=f"tweets:{uname}")
            for t in tweets:
                if not t.get("is_retweet"):
                    seen_tweet_ids[uname] = t["id"]
                    break
    save_seen({"seen_tweet_ids": seen_tweet_ids})

    await tg_set_commands()
    await tg_send("🤖 X Notify Bot started!\nUse /help for commands.")

    # ── Task 1: Telegram command loop (own long-poll, never blocks X) ─
    async def tg_command_loop():
        nonlocal watchlist, settings, seen_tweet_ids
        last_update_id = 0
        while True:
            try:
                updates = await tg_get_updates(last_update_id + 1, timeout=25)
                for u in updates:
                    last_update_id = u["update_id"]
                    cb = u.get("callback_query")
                    if cb:
                        await handle_callback(cb, watchlist, settings, seen_tweet_ids, rebuild_id_map, list_active)
                        continue
                    msg = u.get("message", {})
                    text = msg.get("text", "")
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    if text.startswith("/"):
                        await handle_cmd(text, chat_id, watchlist, settings, seen_tweet_ids,
                                         list_active=list_active)
                        rebuild_id_map()
            except Exception as e:
                log.warning(f"TG loop error: {e}")
                await asyncio.sleep(2)

    # ── Task 2: X poll loop (List timeline primary; per-user fallback) ─
    async def x_poll_loop():
        nonlocal seen_tweet_ids
        last_notif_poll = 0.0
        while True:
            now = time.time()
            try:
                if list_active:
                    wait = _rate.wait_time("list", LIST_POLL_INTERVAL)
                    if wait > 0:
                        await asyncio.sleep(wait)
                    tweets = fetch_list_timeline(LIST_ID, count=40, rl_key="list")
                    # Oldest-first so multiple new tweets forward in order.
                    new = []
                    for t in tweets:
                        au = id_to_uname.get(t.get("author_id", ""))
                        if not au or au not in watchlist:
                            continue
                        if t["id"] <= seen_tweet_ids.get(au, "0"):
                            continue
                        new.append((au, t))
                    new.sort(key=lambda x: x[1]["id"])
                    dirty = False
                    for au, t in new:
                        # advance seen regardless (so filtered-out tweets aren't re-checked)
                        if t["id"] > seen_tweet_ids.get(au, "0"):
                            seen_tweet_ids[au] = t["id"]
                            dirty = True
                        if not passes_filter(t, watchlist[au]):
                            log.info(f"Filtered @{au} {t['id']} (reply={t.get('is_reply')})")
                            continue
                        await forward_tweet(t, au, watchlist[au], settings)
                    if dirty:
                        save_seen({"seen_tweet_ids": seen_tweet_ids})
                else:
                    # Fallback: per-user timeline polling (no List configured).
                    for uname, info in list(watchlist.items()):
                        uid = info.get("id", "")
                        if not uid:
                            continue
                        tw_wait = _rate.wait_time(f"tweets:{uname}", 10)
                        if tw_wait > 0:
                            await asyncio.sleep(tw_wait)
                        tweets = fetch_user_tweets(uid, 10, rl_key=f"tweets:{uname}")
                        new = [t for t in tweets if t["id"] > seen_tweet_ids.get(uname, "0")]
                        new.sort(key=lambda t: t["id"])
                        for t in new:
                            if t["id"] > seen_tweet_ids.get(uname, "0"):
                                seen_tweet_ids[uname] = t["id"]
                            if not passes_filter(t, info):
                                continue
                            await forward_tweet(t, uname, info, settings)
                        save_seen({"seen_tweet_ids": seen_tweet_ids})
            except Exception as e:
                log.warning(f"X poll error: {e}")
            await asyncio.sleep(LIST_POLL_INTERVAL if list_active else 1)

    await asyncio.gather(tg_command_loop(), x_poll_loop())

# ── Entry Point ─────────────────────────────────────────────────────
def entry():
    # --setup: force wizard
    if "--setup" in sys.argv:
        if setup_wizard():
            print(f"{C.G}Restart with: python3 bot.py{C.NC}")
        return

    # First run: no .env or missing keys → wizard
    if not ENV_FILE.exists() or not all(ENV.get(v) for v in REQUIRED_ENV):
        setup_wizard()
        # Reload env after wizard
        global ENV, AUTH_TOKEN, CT0, TG_TOKEN, TG_CHAT
        ENV = load_env()
        AUTH_TOKEN = ENV.get("X_AUTH_TOKEN", "")
        CT0 = ENV.get("X_CT0", "")
        TG_TOKEN = ENV.get("TELEGRAM_BOT_TOKEN", "")
        TG_CHAT = ENV.get("TELEGRAM_CHAT_ID", "")

    # Validate
    validate_env()
    check_deps()

    # Single instance lock
    acquire_lock()

    # Run
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down.")
    finally:
        release_lock()

if __name__ == "__main__":
    entry()
