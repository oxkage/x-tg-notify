#!/usr/bin/env python3
"""
x-notify debug CLI — test individual components without running the full bot.

Usage:
  python3 debug.py tweet <tweet_id>         Fetch tweet by ID, show data + media URLs
  python3 debug.py send <tweet_id>          Fetch tweet → forward to Telegram (full pipeline)
  python3 debug.py send-text <tweet_id>     Fetch tweet → send text only (no media)
  python3 debug.py notifs                   Show current X notifications
  python3 debug.py timeline <username>      Fetch timeline for a user
  python3 debug.py test-tg                  Send test message to Telegram
  python3 debug.py test-video <url>         Test sending a video URL to Telegram
  python3 debug.py test-photo <url>         Test sending a photo URL to Telegram
  python3 debug.py state                    Show seen state + watchlist + settings
  python3 debug.py reset                    Reset seen state (mark all as unseen)
  python3 debug.py raw <tweet_id>           Show raw API response for a tweet
"""
import json, sys, os, time, asyncio, urllib.parse
from pathlib import Path

BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"
WATCHLIST_FILE = BASE_DIR / "watchlist.json"
SEEN_FILE = BASE_DIR / "seen_state.json"
SETTINGS_FILE = BASE_DIR / "settings.json"

# ── Load env ──────────────────────────────────────────────────────────
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

# ── Colors ────────────────────────────────────────────────────────────
class C:
    R = "\033[0;31m"; G = "\033[0;32m"; Y = "\033[0;33m"
    B = "\033[0;34m"; M = "\033[0;35m"; C = "\033[0;36m"
    W = "\033[0;37m"; BOLD = "\033[1m"; DIM = "\033[2m"; NC = "\033[0m"

def ok(msg):  print(f"  {C.G}✓ {msg}{C.NC}")
def fail(msg): print(f"  {C.R}✗ {msg}{C.NC}")
def info(msg): print(f"  {C.C}ℹ {msg}{C.NC}")
def warn(msg): print(f"  {C.Y}⚠ {msg}{C.NC}")

# ── X API (copied from bot.py for standalone use) ─────────────────────
BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
USER_TWEETS_QUERY_ID = "V7H0Ap3_Hh2FyS75OCDO3Q"
USER_BY_SCREEN_NAME_QUERY_ID = "1VOOyvKkiI3FMmkeDNxM9A"
NOTIF_QUERY_ID = "ZhJlpN0aKcSkccG5Pz1MEw"

TWEET_FEATURES = {
    "rweb_video_screen_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
}

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

from curl_cffi import requests as cffi
from x_client_transaction import ClientTransaction
from x_client_transaction.utils import generate_headers, get_ondemand_file_url
import bs4

_x_session = None
_ct = None
_ct_time = 0

def get_x_session():
    global _x_session
    if _x_session is None:
        _x_session = cffi.Session(impersonate="chrome136")
        _x_session.cookies.set("auth_token", AUTH_TOKEN, domain=".x.com")
        _x_session.cookies.set("ct0", CT0, domain=".x.com")
        _x_session.cookies.set("lang", "en", domain=".x.com")
    return _x_session

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
        except Exception as e:
            print(f"  {C.Y}⚠ CT refresh failed: {e}{C.NC}")
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
            print(f"  {C.Y}⚠ CT header failed: {e}{C.NC}")
    return h

# ── Resolve user ──────────────────────────────────────────────────────
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

# ── Fetch tweets by user ID ───────────────────────────────────────────
def fetch_user_tweets(user_id: str, count=10) -> list[dict]:
    v = json.dumps({
        "userId": user_id, "count": count,
        "includePromotedContent": False,
        "withQuickPromoteEligibilityTweetFields": True,
        "withVoice": True, "withV2Timeline": True,
    }, separators=(",", ":"))
    f = json.dumps(TWEET_FEATURES, separators=(",", ":"))
    url = f"https://x.com/i/api/graphql/{USER_TWEETS_QUERY_ID}/UserTweets?variables={urllib.parse.quote(v)}&features={urllib.parse.quote(f)}"
    r = get_x_session().get(url, headers=xh(url), timeout=15)
    if r.status_code != 200:
        print(f"  {C.R}✗ UserTweets HTTP {r.status_code}{C.NC}")
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
            video_url = None
            gif_url = None
            for m in legacy.get("extended_entities", {}).get("media", []):
                url_m = m.get("media_url_https")
                if not url_m: continue
                mtype = m.get("type", "")
                media_list.append({"type": mtype, "url": url_m})
                if not photo_url and mtype == "photo":
                    photo_url = url_m
                elif not video_url and mtype == "video":
                    variants = m.get("video_info", {}).get("variants", [])
                    best_v = max((v for v in variants if v.get("content_type") == "video/mp4"),
                                 key=lambda v: v.get("bitrate", 0), default=None)
                    if best_v:
                        raw_url = best_v.get("url", "")
                        video_url = raw_url if raw_url else None
                elif not gif_url and mtype == "animated_gif":
                    variants = m.get("video_info", {}).get("variants", [])
                    if variants:
                        raw_url = variants[0].get("url", "")
                        gif_url = raw_url if raw_url else None

            user_result = tweet_result.get("core", {}).get("user_results", {}).get("result", {})
            u_legacy = user_result.get("legacy", {}) if user_result else {}

            is_rt = bool(legacy.get("retweeted_status_result"))
            is_quote = bool(legacy.get("is_quote_status")) and legacy.get("quoted_status_result")
            quote_text = ""
            quote_user = ""
            if is_quote:
                q_result = legacy["quoted_status_result"].get("result", {})
                if q_result.get("__typename") == "TweetWithVisibilityResults":
                    q_result = q_result.get("tweet", {})
                q_legacy = q_result.get("legacy", {})
                q_user = q_result.get("core", {}).get("user_results", {}).get("result", {}).get("legacy", {})
                quote_text = q_legacy.get("full_text", "")
                quote_user = q_user.get("screen_name", "")

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
                        elif not video_url and mtype == "video":
                            variants = m.get("video_info", {}).get("variants", [])
                            best_v = max((v for v in variants if v.get("content_type") == "video/mp4"),
                                         key=lambda v: v.get("bitrate", 0), default=None)
                            if best_v:
                                raw_url = best_v.get("url", "")
                                video_url = raw_url if raw_url else None
                        elif not gif_url and mtype == "animated_gif":
                            variants = m.get("video_info", {}).get("variants", [])
                            if variants:
                                raw_url = variants[0].get("url", "")
                                gif_url = raw_url if raw_url else None
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
                "is_quote": is_quote,
                "quote_text": quote_text,
                "quote_user": quote_user,
                "photo_url": photo_url,
                "video_url": video_url,
                "gif_url": gif_url,
                "media": media_list,
            })
    return tweets

# ── Fetch notifications ───────────────────────────────────────────────
def fetch_notifications(count=40) -> list[dict]:
    v = json.dumps({"timeline_type": "All", "count": count}, separators=(",", ":"))
    f = json.dumps(NOTIF_FEATURES, separators=(",", ":"))
    url = f"https://x.com/i/api/graphql/{NOTIF_QUERY_ID}/NotificationsTimeline?variables={urllib.parse.quote(v)}&features={urllib.parse.quote(f)}"
    r = get_x_session().get(url, headers=xh(url), timeout=15)
    print(f"  HTTP {r.status_code}")
    if r.status_code != 200:
        return []
    try:
        timeline = r.json()["data"]["viewer_v2"]["user_results"]["result"]["notification_timeline"]["timeline"]
    except (KeyError, TypeError):
        print(f"  {C.Y}⚠ Could not parse notification timeline{C.NC}")
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

# ── Telegram send ─────────────────────────────────────────────────────
async def tg_send_test(text, photo=None, video=None, animation=None, buttons=None):
    """Send to Telegram with rich message + inline buttons."""
    import httpx
    TG_API = f"https://api.telegram.org/bot{TG_TOKEN}"
    async with httpx.AsyncClient(timeout=15) as c:
        try:
            # Build rich markdown with embedded media
            md = text
            if photo:
                md += f"\n\n![]({photo})"
            elif video:
                md += f"\n\n![]({video})"
            elif animation:
                md += f"\n\n![]({animation})"
            payload = {"chat_id": TG_CHAT, "rich_message": json.dumps({"markdown": md})}
            if buttons:
                payload["reply_markup"] = buttons
            r = await c.post(f"{TG_API}/sendRichMessage", data=payload)
            result = r.json()
            if result.get("ok"):
                ok(f"Rich message sent! msg_id={result['result']['message_id']}")
                return
            # Fallback to legacy
            if photo:
                data = {"chat_id": TG_CHAT, "caption": text, "parse_mode": "HTML", "photo": photo}
                if buttons: data["reply_markup"] = buttons
                r = await c.post(f"{TG_API}/sendPhoto", data=data)
            elif video:
                data = {"chat_id": TG_CHAT, "caption": text, "parse_mode": "HTML", "video": video}
                if buttons: data["reply_markup"] = buttons
                r = await c.post(f"{TG_API}/sendVideo", data=data)
            elif animation:
                data = {"chat_id": TG_CHAT, "caption": text, "parse_mode": "HTML", "animation": animation}
                if buttons: data["reply_markup"] = buttons
                r = await c.post(f"{TG_API}/sendAnimation", data=data)
            else:
                payload = {"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
                if buttons: payload["reply_markup"] = buttons
                r = await c.post(f"{TG_API}/sendMessage", json=payload)
            result = r.json()
            if result.get("ok"):
                ok(f"Legacy sent! msg_id={result['result']['message_id']}")
            else:
                fail(f"Telegram error: {result.get('description', 'unknown')}")
                print(f"    Full response: {json.dumps(result, indent=2)}")
        except Exception as e:
            fail(f"Telegram exception: {e}")

# ── Commands ──────────────────────────────────────────────────────────
def cmd_tweet(tweet_id: str):
    """Fetch a single tweet by ID from the watched user's timeline."""
    print(f"\n{C.BOLD}── Fetching tweet {tweet_id} ──{C.NC}\n")

    # Try to find which user it belongs to
    watchlist = json.loads(WATCHLIST_FILE.read_text()) if WATCHLIST_FILE.exists() else {}
    if not watchlist:
        fail("No watchlist.json found")
        return

    for uname, winfo in watchlist.items():
        uid = winfo.get("id", "")
        if not uid: continue
        print(f"  {C.C}ℹ Checking @{uname} timeline...{C.NC}")
        tweets = fetch_user_tweets(uid, 20)
        for t in tweets:
            if t["id"] == tweet_id:
                print_tweet(t)
                return

    # Not found in watchlist — try fetching user tweets for all known users
    fail(f"Tweet {tweet_id} not found in watchlist timelines")
    info("Try: python3 debug.py raw <tweet_id> to see raw data")

def cmd_send(tweet_id: str, media=True):
    """Fetch tweet + send to Telegram."""
    print(f"\n{C.BOLD}── Sending tweet {tweet_id} to Telegram ──{C.NC}\n")

    watchlist = json.loads(WATCHLIST_FILE.read_text()) if WATCHLIST_FILE.exists() else {}
    settings = json.loads(SETTINGS_FILE.read_text()) if SETTINGS_FILE.exists() else {}

    found = None
    matched_uname = None
    for uname, winfo in watchlist.items():
        uid = winfo.get("id", "")
        if not uid: continue
        print(f"  {C.C}ℹ Checking @{uname}...{C.NC}")
        tweets = fetch_user_tweets(uid, 20)
        for t in tweets:
            if t["id"] == tweet_id:
                found = t
                matched_uname = uname
                break
        if found: break

    if not found:
        fail(f"Tweet {tweet_id} not found")
        return

    print_tweet(found)

    # Build caption
    from datetime import datetime
    def format_tweet_time(raw):
        try:
            dt = datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
            return dt.strftime(f"%b %d, %Y · %H:%M %Z")
        except: return raw

    name = watchlist.get(matched_uname, {}).get("name", found.get("user_name", ""))
    tweet_url = f"https://x.com/{matched_uname}/status/{found['id']}"
    profile_url = f"https://x.com/{matched_uname}"
    post_time = format_tweet_time(found.get("created", ""))

    rt_prefix = ""
    if found.get("is_retweet"):
        rt_name = found.get("user_name", "")
        rt_user = found.get("user", "")
        rt_prefix = f"\n> 🔁 **RT by** [{rt_name}](https://x.com/{rt_user})\n"
    quote_block = ""
    if found.get("is_quote") and found.get("quote_text"):
        q_user = found.get("quote_user", "")
        q_text = found.get("quote_text", "")[:200]
        quote_block = f"\n\n> ↪️ **@{q_user}**: {q_text}"
    tweet_text = found["text"][:400]
    if len(found.get("text", "")) > 200:
        tweet_text = f"> {found['text'][:150]}...\n>\n> (click Go to Post for full text)"
    caption = (
        f"## 🔔 New Post\n\n"
        f"**{name}** ([@{matched_uname}]({profile_url})){rt_prefix}\n\n"
        f"{tweet_text}{quote_block}\n\n"
        f"---\n🕐 {post_time}"
    )

    # Determine media
    if not media:
        info("Sending text only (--text flag)")
        asyncio.run(tg_send_test(caption))
        return

    photo_enabled = settings.get("photo_enabled", True)
    video_enabled = settings.get("video_enabled", False)
    gif_enabled = settings.get("gif_enabled", True)

    media_url = None
    media_type = None
    if video_enabled and found.get("video_url"):
        media_url = found["video_url"]
        media_type = "video"
    elif gif_enabled and found.get("gif_url"):
        media_url = found["gif_url"]
        media_type = "gif"
    elif photo_enabled and found.get("photo_url"):
        media_url = found["photo_url"]
        media_type = "photo"

    if media_type:
        info(f"Sending with {media_type}: {media_url[:80]}...")
    else:
        info("No media (or media type disabled in settings)")

    buttons = json.dumps({"inline_keyboard": [[
        {"text": "🔗 Go to Post", "url": tweet_url},
        {"text": "🔕 Stop Notify", "callback_data": f"stop:{matched_uname}"},
    ]]})

    if media_type == "video":
        asyncio.run(tg_send_test(caption, video=media_url, buttons=buttons))
    elif media_type == "gif":
        asyncio.run(tg_send_test(caption, animation=media_url, buttons=buttons))
    elif media_type == "photo":
        asyncio.run(tg_send_test(caption, photo=media_url, buttons=buttons))
    else:
        asyncio.run(tg_send_test(caption, buttons=buttons))

def cmd_notifs():
    """Show current notifications."""
    print(f"\n{C.BOLD}── X Notifications ──{C.NC}\n")
    notifs = fetch_notifications()
    if not notifs:
        info("No notifications (empty or rate limited)")
        return
    print(f"\n  {C.BOLD}Found {len(notifs)} notifications:{C.NC}\n")
    for i, n in enumerate(notifs, 1):
        icon = "🔔" if n["icon"] == "bell_icon" else n["icon"]
        print(f"  {i}. {icon} {n['message'][:60]}")
        print(f"     user_id={n['user_id']}  url={n['url'][:60]}")
        print()

def cmd_timeline(username: str):
    """Fetch timeline for a user."""
    print(f"\n{C.BOLD}── Timeline for @{username} ──{C.NC}\n")
    user = resolve_user(username)
    if not user:
        fail(f"User @{username} not found")
        return
    info(f"Resolved: {user['name']} (id={user['id']})")
    tweets = fetch_user_tweets(user["id"], 10)
    if not tweets:
        info("No tweets found")
        return
    print(f"\n  {C.BOLD}Found {len(tweets)} tweets:{C.NC}\n")
    for t in tweets:
        print_tweet(t)
        print()

def cmd_test_tg():
    """Send test message to Telegram."""
    print(f"\n{C.BOLD}── Testing Telegram ──{C.NC}\n")
    info(f"Chat ID: {TG_CHAT}")
    info(f"Bot token: ...{TG_TOKEN[-8:]}")
    asyncio.run(tg_send_test("🔧 <b>Debug test</b>\nIf you see this, Telegram is working!"))

def cmd_test_video(url: str):
    """Test sending a video URL to Telegram."""
    print(f"\n{C.BOLD}── Testing video send ──{C.NC}\n")
    info(f"URL: {url[:80]}...")
    asyncio.run(tg_send_test("🎬 <b>Video test</b>", video=url))

def cmd_test_photo(url: str):
    """Test sending a photo URL to Telegram."""
    print(f"\n{C.BOLD}── Testing photo send ──{C.NC}\n")
    info(f"URL: {url[:80]}...")
    asyncio.run(tg_send_test("📸 <b>Photo test</b>", photo=url))

def cmd_state():
    """Show current state."""
    print(f"\n{C.BOLD}── State ──{C.NC}\n")

    # Seen state
    seen = json.loads(SEEN_FILE.read_text()) if SEEN_FILE.exists() else {}
    seen_ids = seen.get("seen_tweet_ids", {})
    print(f"  {C.BOLD}Seen tweet IDs:{C.NC}")
    if seen_ids:
        for k, v in seen_ids.items():
            print(f"    {k}: {v}")
    else:
        print(f"    {C.DIM}(empty){C.NC}")

    # Watchlist
    wl = json.loads(WATCHLIST_FILE.read_text()) if WATCHLIST_FILE.exists() else {}
    print(f"\n  {C.BOLD}Watchlist:{C.NC}")
    if wl:
        for k, v in wl.items():
            print(f"    @{k} (id={v.get('id', '?')}, name={v.get('name', '?')})")
    else:
        print(f"    {C.DIM}(empty){C.NC}")

    # Settings
    settings = json.loads(SETTINGS_FILE.read_text()) if SETTINGS_FILE.exists() else {}
    print(f"\n  {C.BOLD}Settings:{C.NC}")
    print(f"    photo_enabled: {settings.get('photo_enabled', True)}")
    print(f"    video_enabled: {settings.get('video_enabled', False)}")
    print(f"    gif_enabled: {settings.get('gif_enabled', True)}")

def cmd_reset():
    """Reset seen state."""
    print(f"\n{C.BOLD}── Resetting seen state ──{C.NC}\n")
    _atomic_write(SEEN_FILE, json.dumps({"seen_tweet_ids": {}}))
    ok("Seen state cleared")

def cmd_raw(tweet_id: str):
    """Show raw API response for a tweet."""
    print(f"\n{C.BOLD}── Raw tweet data {tweet_id} ──{C.NC}\n")
    watchlist = json.loads(WATCHLIST_FILE.read_text()) if WATCHLIST_FILE.exists() else {}
    for uname, winfo in watchlist.items():
        uid = winfo.get("id", "")
        if not uid: continue
        v = json.dumps({
            "userId": uid, "count": 20,
            "includePromotedContent": False,
            "withQuickPromoteEligibilityTweetFields": True,
            "withVoice": True, "withV2Timeline": True,
        }, separators=(",", ":"))
        f = json.dumps(TWEET_FEATURES, separators=(",", ":"))
        url = f"https://x.com/i/api/graphql/{USER_TWEETS_QUERY_ID}/UserTweets?variables={urllib.parse.quote(v)}&features={urllib.parse.quote(f)}"
        r = get_x_session().get(url, headers=xh(url), timeout=15)
        if r.status_code != 200:
            continue
        data = r.json()
        instructions = data.get("data", {}).get("user", {}).get("result", {}).get("timeline_v2", {}).get("timeline", {}).get("instructions", [])
        for inst in instructions:
            for entry in inst.get("entries", []):
                content = entry.get("content", {})
                if content.get("entryType") != "TimelineTimelineItem": continue
                item = content.get("itemContent", {})
                tweet_result = item.get("tweet_results", {}).get("result", {})
                if tweet_result.get("__typename") == "TweetWithVisibilityResults":
                    tweet_result = tweet_result.get("tweet", {})
                legacy = tweet_result.get("legacy", {})
                if legacy.get("id_str") == tweet_id:
                    print(json.dumps(tweet_result, indent=2, default=str))
                    return
    fail(f"Tweet {tweet_id} not found in watchlist timelines")

# ── Helpers ───────────────────────────────────────────────────────────
def _atomic_write(path, data):
    tmp = Path(path).with_suffix(".tmp")
    tmp.write_text(data)
    os.rename(tmp, path)

def print_tweet(t):
    """Pretty-print a tweet dict."""
    media_info = []
    if t.get("photo_url"): media_info.append(f"photo: {t['photo_url'][:60]}")
    if t.get("video_url"): media_info.append(f"video: {t['video_url'][:60]}")
    if t.get("gif_url"):   media_info.append(f"gif: {t['gif_url'][:60]}")

    rt_tag = " 🔁RT" if t.get("is_retweet") else ""
    q_tag = f" ↪️QT @{t.get('quote_user', '')}" if t.get("is_quote") else ""

    print(f"  {C.BOLD}Tweet {t['id']}{C.NC}{rt_tag}{q_tag}")
    print(f"  @{t.get('user', '?')} ({t.get('user_name', '?')})")
    print(f"  {t.get('created', '?')}")
    print(f"  {C.DIM}{t.get('text', '')[:120]}{C.NC}")
    if media_info:
        for m in media_info:
            print(f"  {C.M}📎 {m}{C.NC}")
    if t.get("is_quote") and t.get("quote_text"):
        print(f"  {C.C}↪️ @{t.get('quote_user', '')}: {t['quote_text'][:80]}{C.NC}")

# ── Main ──────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()
    args = sys.argv[2:]

    if not AUTH_TOKEN or not CT0 or not TG_TOKEN or not TG_CHAT:
        fail("Missing credentials — run python3 bot.py --setup first")
        return

    # Init X session
    get_x_session()
    get_ct()

    if cmd == "tweet" and args:
        cmd_tweet(args[0])
    elif cmd == "send" and args:
        cmd_send(args[0])
    elif cmd == "send-text" and args:
        cmd_send(args[0], media=False)
    elif cmd == "notifs":
        cmd_notifs()
    elif cmd == "timeline" and args:
        cmd_timeline(args[0])
    elif cmd == "test-tg":
        cmd_test_tg()
    elif cmd == "test-video" and args:
        cmd_test_video(args[0])
    elif cmd == "test-photo" and args:
        cmd_test_photo(args[0])
    elif cmd == "state":
        cmd_state()
    elif cmd == "reset":
        cmd_reset()
    elif cmd == "raw" and args:
        cmd_raw(args[0])
    else:
        print(__doc__)

if __name__ == "__main__":
    main()
