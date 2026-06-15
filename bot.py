#!/usr/bin/env python3
"""
x-tg-notify — Real-time X notification forwarder to Telegram bot.
Run: python3 bot.py          (auto-setup on first run)
     python3 bot.py --setup  (re-run setup wizard)
"""
import json, os, sys, time, asyncio, urllib.parse, logging, shutil
from datetime import datetime
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"
WATCHLIST_FILE = BASE_DIR / "watchlist.json"
SEEN_FILE = BASE_DIR / "seen_state.json"

BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
NOTIF_QUERY_ID = "ZhJlpN0aKcSkccG5Pz1MEw"
USER_TWEETS_QUERY_ID = "V7H0Ap3_Hh2FyS75OCDO3Q"
USER_BY_SCREEN_NAME_QUERY_ID = "1VOOyvKkiI3FMmkeDNxM9A"

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
REQUIRED_ENV = ["X_AUTH_TOKEN", "X_CT0", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]

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
    url = f"https://x.com/i/api/graphql/{USER_BY_SCREEN_NAME_QUERY_ID}/UserByScreenName?variables={urllib.parse.quote(v)}&features={urllib.parse.quote(f)}"
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
    url = f"https://x.com/i/api/graphql/{NOTIF_QUERY_ID}/NotificationsTimeline?variables={urllib.parse.quote(v)}&features={urllib.parse.quote(f)}"
    r = get_x_session().get(url, headers=xh(url), timeout=15)
    if r.status_code == 200:
        _rate.mark_ok("notif")
    elif r.status_code == 429:
        _rate.mark_fail("notif")
        return []
    else:
        return []
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

def fetch_user_tweets(user_id: str, count=5) -> list[dict]:
    v = json.dumps({
        "userId": user_id, "count": count,
        "includePromotedContent": False,
        "withQuickPromoteEligibilityTweetFields": True,
        "withVoice": True, "withV2Timeline": True,
    }, separators=(",", ":"))
    f = json.dumps(TWEET_FEATURES, separators=(",", ":"))
    url = f"https://x.com/i/api/graphql/{USER_TWEETS_QUERY_ID}/UserTweets?variables={urllib.parse.quote(v)}&features={urllib.parse.quote(f)}"
    r = get_x_session().get(url, headers=xh(url), timeout=15)
    if r.status_code == 200:
        _rate.mark_ok("tweets")
    elif r.status_code == 429:
        _rate.mark_fail("tweets")
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
            if content.get("entryType") != "TimelineTimelineItem": continue
            item = content.get("itemContent", {})
            tweet_result = item.get("tweet_results", {}).get("result", {})
            if tweet_result.get("__typename") == "TweetWithVisibilityResults":
                tweet_result = tweet_result.get("tweet", {})
            legacy = tweet_result.get("legacy", {})
            if not legacy.get("full_text"): continue

            media_list = []
            photo_url = None
            for m in legacy.get("extended_entities", {}).get("media", []):
                url_m = m.get("media_url_https")
                if not url_m: continue
                mtype = m.get("type", "")
                media_list.append({"type": mtype, "url": url_m})
                if not photo_url and mtype == "photo":
                    photo_url = url_m

            user_result = tweet_result.get("core", {}).get("user_results", {}).get("result", {})
            u_legacy = user_result.get("legacy", {}) if user_result else {}

            is_rt = bool(legacy.get("retweeted_status_result"))
            if is_rt:
                rt_result = legacy["retweeted_status_result"].get("result", {})
                if rt_result.get("__typename") == "TweetWithVisibilityResults":
                    rt_result = rt_result.get("tweet", {})
                rt_legacy = rt_result.get("legacy", {})
                rt_user = rt_result.get("core", {}).get("user_results", {}).get("result", {}).get("legacy", {})
                text = rt_legacy.get("full_text", legacy.get("full_text", ""))
                user_screen = rt_user.get("screen_name", "")
                user_name = rt_user.get("name", "")
                if not media_list:
                    for m in rt_legacy.get("extended_entities", {}).get("media", []):
                        url_m = m.get("media_url_https")
                        if not url_m: continue
                        mtype = m.get("type", "")
                        media_list.append({"type": mtype, "url": url_m})
                        if not photo_url and mtype == "photo":
                            photo_url = url_m
            else:
                text = legacy.get("full_text", "")
                user_screen = u_legacy.get("screen_name", "")
                user_name = u_legacy.get("name", "")

            tweets.append({
                "id": legacy.get("id_str", ""),
                "text": text,
                "user": user_screen,
                "user_name": user_name,
                "created": legacy.get("created_at", ""),
                "is_retweet": is_rt,
                "photo_url": photo_url,
                "media": media_list,
            })
    return tweets

# ── Telegram ───────────────────────────────────────────────────────
import httpx
TG_API = f"https://api.telegram.org/bot{TG_TOKEN}"
_tg_client: httpx.AsyncClient | None = None

async def get_tg() -> httpx.AsyncClient:
    global _tg_client
    if _tg_client is None or _tg_client.is_closed:
        _tg_client = httpx.AsyncClient(timeout=15)
    return _tg_client

async def tg_send(text, chat_id=TG_CHAT, photo=None, button_url=None, button_text="🔗 Go to Post",
                  buttons=None):
    c = await get_tg()
    reply_markup = None
    if buttons:
        reply_markup = json.dumps({"inline_keyboard": [buttons]})
    elif button_url:
        reply_markup = json.dumps({"inline_keyboard": [[{"text": button_text, "url": button_url}]]})
    try:
        if photo:
            data = {"chat_id": chat_id, "caption": text, "parse_mode": "HTML", "photo": photo}
            if reply_markup: data["reply_markup"] = reply_markup
            await c.post(f"{TG_API}/sendPhoto", data=data)
        else:
            payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
            if reply_markup: payload["reply_markup"] = reply_markup
            await c.post(f"{TG_API}/sendMessage", json=payload)
    except Exception as e:
        log.warning(f"tg_send failed: {e}")

async def tg_answer_callback(callback_id, text="", show_alert=False):
    c = await get_tg()
    try:
        await c.post(f"{TG_API}/answerCallbackQuery", json={
            "callback_query_id": callback_id, "text": text, "show_alert": show_alert
        })
    except Exception as e:
        log.warning(f"tg_answer_callback failed: {e}")

async def tg_get_updates(offset=0):
    c = await get_tg()
    try:
        r = await c.get(f"{TG_API}/getUpdates", params={"offset": offset, "timeout": 3})
        if r.status_code == 200:
            return r.json().get("result", [])
        elif r.status_code == 409:
            log.warning("409 Conflict — another instance running?")
            await asyncio.sleep(5)
    except Exception as e:
        log.warning(f"TG getUpdates error: {e}")
    return []

# ── Commands ────────────────────────────────────────────────────────
async def handle_cmd(text, chat_id, watchlist):
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
        watchlist[uname] = {"id": uid, "name": name, "added": datetime.now().isoformat()}
        save_watchlist(watchlist)
        icon = "🔔" if notif_ok else "🔕"
        await tg_send(
            f"✅ Added <b>{name}</b>\n"
            f"👤 <a href=\"https://x.com/{uname}\">@{uname}</a>\n"
            f"• Followed ✅\n• Notifications {icon}",
            chat_id,
            buttons=[
                {"text": "👤 View Profile", "url": f"https://x.com/{uname}"},
                {"text": "🔕 Stop Notify", "callback_data": f"stop:{uname}"},
            ]
        )

    elif cmd == "/remove" and len(parts) >= 2:
        uname = parts[1].lstrip("@")
        if uname not in watchlist:
            await tg_send(f"⚠️ @{uname} not in watchlist.", chat_id); return
        uid = watchlist[uname]["id"]; name = watchlist[uname]["name"]
        disable_notifs(uid); await asyncio.sleep(0.5)
        unfollow_user(uid)
        del watchlist[uname]; save_watchlist(watchlist)
        await tg_send(f"✅ Removed <b>{name}</b> (@{uname})\n• Notifications OFF 🔕\n• Unfollowed", chat_id)

    elif cmd == "/list":
        if not watchlist:
            await tg_send("📋 Watchlist empty.\nUse /add @username.", chat_id); return
        lines = ["📋 <b>Watchlist:</b>"]
        for i, (u, info) in enumerate(watchlist.items(), 1):
            lines.append(f"  {i}. <b>{info['name']}</b> — <a href=\"https://x.com/{u}\">@{u}</a>")
        await tg_send("\n".join(lines), chat_id)

    elif cmd in ("/start", "/help"):
        await tg_send(
            "🤖 <b>X Notify Bot</b>\n\n"
            "/add @username — Follow + notify ON\n"
            "/remove @username — Unfollow + notify OFF\n"
            "/list — Show watched users\n\n"
            "🔔 New posts forwarded with inline buttons:\n"
            "• 🔗 Go to Post\n"
            "• 🔕 Stop Notify — remove from watchlist",
            chat_id
        )

# ── Main ────────────────────────────────────────────────────────────
async def main():
    log.info("Starting x-tg-notify...")
    get_x_session()
    get_ct()
    log.info("X session ready")
    watchlist = load_watchlist()
    log.info(f"Watchlist: {list(watchlist.keys())}")
    await tg_send("🤖 X Notify Bot started!\nUse /help for commands.")

    state = load_seen()
    seen_tweet_ids = state.get("seen_tweet_ids", {})

    for uname, info in watchlist.items():
        uid = info.get("id", "")
        if uid:
            wait = _rate.wait_time(f"seed:{uname}", 1)
            if wait > 0: await asyncio.sleep(wait)
            tweets = fetch_user_tweets(uid, 10)
            if tweets:
                for t in tweets:
                    if not t.get("is_retweet"):
                        seen_tweet_ids[uname] = t["id"]
                        break
                _rate.mark_ok(f"seed:{uname}")
                log.info(f"Seeded @{uname}: {tweets[0]['id']}")
    save_seen({"seen_tweet_ids": seen_tweet_ids})

    id_to_uname: dict[str, str] = {}
    for uname, info in watchlist.items():
        uid = info.get("id", "")
        if uid:
            id_to_uname[uid] = uname

    last_tweet_fetch: dict[str, float] = {}
    last_update_id = 0
    last_poll = 0

    while True:
        now = time.time()

        try:
            updates = await tg_get_updates(last_update_id + 1)
            for u in updates:
                last_update_id = u["update_id"]

                cb = u.get("callback_query")
                if cb:
                    cb_data = cb.get("data", "")
                    cb_id = cb.get("id", "")
                    cb_chat = str(cb.get("message", {}).get("chat", {}).get("id", ""))
                    if cb_data.startswith("stop:"):
                        uname = cb_data.split(":", 1)[1]
                        if uname in watchlist:
                            uid = watchlist[uname]["id"]
                            name = watchlist[uname]["name"]
                            disable_notifs(uid); await asyncio.sleep(0.5)
                            unfollow_user(uid)
                            del watchlist[uname]; save_watchlist(watchlist)
                            id_to_uname.pop(uid, None)
                            await tg_answer_callback(cb_id, f"🔕 Stopped notifications for @{uname}")
                            await tg_send(f"🔕 Stopped notifications for <b>{name}</b> (@{uname})\n• Unfollowed", cb_chat)
                            log.info(f"Stop notify: @{uname}")
                        else:
                            await tg_answer_callback(cb_id, f"@{uname} not in watchlist", show_alert=True)
                        continue

                msg = u.get("message", {})
                text = msg.get("text", "")
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if text.startswith("/"):
                    await handle_cmd(text, chat_id, watchlist)
                    id_to_uname = {}
                    for un, info in watchlist.items():
                        uid = info.get("id", "")
                        if uid:
                            id_to_uname[uid] = un

        except Exception as e:
            log.warning(f"TG update error: {e}")

        if now - last_poll >= POLL_INTERVAL:
            last_poll = now
            wait = _rate.wait_time("notif", POLL_INTERVAL)
            if wait > 0:
                await asyncio.sleep(wait)

            try:
                notifs = fetch_notifications()
                for n in notifs:
                    icon, msg_text = n["icon"], n["message"]
                    if "login" in msg_text.lower() or "temporary label" in msg_text.lower():
                        continue
                    if icon != "bell_icon":
                        continue

                    notif_uid = n.get("user_id", "")
                    matched = id_to_uname.get(notif_uid) if notif_uid else None
                    if not matched:
                        for uname, info in watchlist.items():
                            if uname.lower() in msg_text.lower() or info["name"][:8].lower() in msg_text.lower():
                                matched = uname; break
                    if not matched:
                        continue

                    uid = watchlist[matched].get("id", "")
                    if not uid:
                        continue

                    now_ts = time.time()
                    if now_ts - last_tweet_fetch.get(matched, 0) < 30:
                        continue
                    last_tweet_fetch[matched] = now_ts

                    tw_wait = _rate.wait_time(f"tweets:{matched}", 5)
                    if tw_wait > 0:
                        await asyncio.sleep(tw_wait)

                    tweets = fetch_user_tweets(uid, 10)
                    if not tweets:
                        continue

                    last_seen = seen_tweet_ids.get(matched, "0")
                    best = None
                    for t in tweets:
                        if t.get("is_retweet"): continue
                        if t["id"] > last_seen:
                            if best is None or t["id"] > best["id"]:
                                best = t

                    if best:
                        seen_tweet_ids[matched] = best["id"]
                        save_seen({"seen_tweet_ids": seen_tweet_ids})
                        name = watchlist[matched]["name"]
                        tweet_url = f"https://x.com/{matched}/status/{best['id']}"
                        profile_url = f"https://x.com/{matched}"
                        post_time = format_tweet_time(best.get("created", ""))

                        rt_prefix = f"🔁 RT by <a href=\"{profile_url}\">{name}</a>\n" if best.get("is_retweet") else ""
                        caption = (
                            f"🔔 <b>New Post</b>\n"
                            f"👤 <a href=\"{profile_url}\">{name}</a> (@{matched})\n\n"
                            f"{rt_prefix}{best['text'][:400]}\n\n"
                            f"🕐 {post_time}"
                        )

                        buttons = [
                            {"text": "🔗 Go to Post", "url": tweet_url},
                            {"text": "🔕 Stop Notify", "callback_data": f"stop:{matched}"},
                        ]
                        await tg_send(caption, photo=best.get("photo_url"), buttons=buttons)
                        _rate.mark_ok(f"tweets:{matched}")
                        log.info(f"Forwarded @{matched} {best['id']}")

            except Exception as e:
                log.warning(f"Poll error: {e}")

        await asyncio.sleep(0.5)

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

    # Run
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down.")

if __name__ == "__main__":
    entry()
