#!/usr/bin/env python3
"""
x-tg-notify — Real-time X notification forwarder to Telegram bot.
Self-contained, no external CLI dependencies.
"""
import json, sys, time, asyncio, urllib.parse, logging
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("x-notify")

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
USER_ID = "1753466847415218176"

def load_watchlist():
    if WATCHLIST_FILE.exists():
        return json.loads(WATCHLIST_FILE.read_text())
    return {}

def save_watchlist(wl):
    WATCHLIST_FILE.write_text(json.dumps(wl, indent=2, ensure_ascii=False))

def load_seen():
    if SEEN_FILE.exists():
        return json.loads(SEEN_FILE.read_text())
    return {"seen_tweet_ids": {}}

def save_seen(state):
    SEEN_FILE.write_text(json.dumps(state))

# ── X API (self-contained, no twitter-cli) ──────────────────────────
from curl_cffi import requests as cffi
from x_client_transaction import ClientTransaction
from x_client_transaction.utils import generate_headers, get_ondemand_file_url
import bs4

# Shared session with Chrome TLS impersonation
_x_session = None

def get_x_session():
    global _x_session
    if _x_session is None:
        _x_session = cffi.Session(impersonate="chrome136")
        _x_session.cookies.set("auth_token", AUTH_TOKEN, domain=".x.com")
        _x_session.cookies.set("ct0", CT0, domain=".x.com")
        _x_session.cookies.set("twid", f"u%3D{USER_ID}", domain=".x.com")
        _x_session.cookies.set("lang", "en", domain=".x.com")
    return _x_session

_ct = None

def get_ct():
    global _ct
    if _ct is None:
        session = get_x_session()
        ct_headers = generate_headers()
        home = session.get("https://x.com", headers=ct_headers, timeout=10)
        home_resp = bs4.BeautifulSoup(home.content, "html.parser")
        ondemand_url = get_ondemand_file_url(response=home_resp)
        ondemand = session.get(ondemand_url, headers=ct_headers, timeout=10)
        _ct = ClientTransaction(home_page_response=home_resp, ondemand_file_response=ondemand.text)
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
        except: pass
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
    return r.status_code == 200

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
    if r.status_code != 200: return []
    try:
        timeline = r.json()["data"]["viewer_v2"]["user_results"]["result"]["notification_timeline"]["timeline"]
    except (KeyError, TypeError): return []
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
            })
    return notifs

def fetch_user_tweets(user_id: str, count=5) -> list[dict]:
    """Fetch latest tweets via GraphQL (no CLI dependency)."""
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

            # Media
            photo_url = None
            media_list = []
            for m in (y for x in (legacy.get("extended_entities", {}).get("media", []),) for y in x):
                if m.get("media_url_https"):
                    media_list.append({"type": m.get("type", ""), "url": m["media_url_https"]})
                    if not photo_url and m.get("type") == "photo":
                        photo_url = m["media_url_https"]

            # Author
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

async def tg_send(text, chat_id=TG_CHAT, photo=None, button_url=None, button_text="🔗 Go to Post"):
    async with httpx.AsyncClient() as c:
        reply_markup = None
        if button_url:
            reply_markup = json.dumps({"inline_keyboard": [[{"text": button_text, "url": button_url}]]})
        if photo:
            data = {"chat_id": chat_id, "caption": text, "parse_mode": "HTML", "photo": photo}
            if reply_markup: data["reply_markup"] = reply_markup
            await c.post(f"{TG_API}/sendPhoto", data=data, timeout=15)
        else:
            payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
            if reply_markup: payload["reply_markup"] = reply_markup
            await c.post(f"{TG_API}/sendMessage", json=payload, timeout=10)

async def tg_get_updates(offset=0):
    async with httpx.AsyncClient() as c:
        try:
            r = await c.get(f"{TG_API}/getUpdates", params={"offset": offset, "timeout": 1}, timeout=5)
            if r.status_code == 200: return r.json().get("result", [])
            elif r.status_code == 409: log.warning("409 Conflict"); await asyncio.sleep(5)
        except Exception as e: log.warning(f"TG error: {e}")
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
            await tg_send(f"❌ Failed to follow @{uname}.", chat_id); return
        await asyncio.sleep(1)
        notif_ok = enable_notifs(uid)
        watchlist[uname] = {"id": uid, "name": name, "added": datetime.now().isoformat()}
        save_watchlist(watchlist)
        icon = "🔔" if notif_ok else "🔕"
        await tg_send(f"✅ Added <b>{name}</b>\n👤 <a href=\"https://x.com/{uname}\">@{uname}</a>\n• Followed ✅\n• Notifications {icon}", chat_id, button_url=f"https://x.com/{uname}", button_text="👤 View Profile")

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
        await tg_send("🤖 <b>X Notify Bot</b>\n\n/add @username — Follow + notify ON\n/remove @username — Unfollow + notify OFF\n/list — Show watched users\n\nNew posts forwarded every 5s.", chat_id)

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

    # Seed current tweets
    for uname, info in watchlist.items():
        uid = info.get("id", "")
        if uid:
            tweets = fetch_user_tweets(uid, 10)
            if tweets:
                for t in tweets:
                        if not t.get("is_retweet"):
                            seen_tweet_ids[uname] = t["id"]
                            break
                log.info(f"Seeded @{uname}: {tweets[0]['id']}")
    save_seen({"seen_tweet_ids": seen_tweet_ids})

    last_tweet_fetch = {}  # {username: timestamp} cooldown for UserTweets
    last_update_id = 0
    last_poll = 0

    while True:
        now = time.time()

        try:
            updates = await tg_get_updates(last_update_id + 1)
            for u in updates:
                last_update_id = u["update_id"]
                msg = u.get("message", {})
                text = msg.get("text", "")
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if text.startswith("/"):
                    await handle_cmd(text, chat_id, watchlist)
        except Exception as e:
            log.warning(f"TG error: {e}")

        if now - last_poll >= POLL_INTERVAL:
            last_poll = now
            try:
                notifs = fetch_notifications()
                for n in notifs:
                    icon, msg = n["icon"], n["message"]
                    if "login" in msg.lower() or "temporary label" in msg.lower(): continue
                    if icon != "bell_icon": continue

                    matched = None
                    for uname, info in watchlist.items():
                        if uname.lower() in msg.lower() or info["name"][:8].lower() in msg.lower():
                            matched = uname; break
                    if not matched: continue

                    uid = watchlist[matched].get("id", "")
                    if not uid: continue

                    # Cooldown: skip if fetched within last 30s
                    now_ts = time.time()
                    if now_ts - last_tweet_fetch.get(matched, 0) < 30:
                        continue
                    last_tweet_fetch[matched] = now_ts

                    tweets = fetch_user_tweets(uid, 10)
                    if not tweets: continue

                    # Compare: if current tweet ID > stored ID, it's new
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
                        caption = (f"🔔 <b>New Post</b>\n"
                                   f"👤 <a href=\"{profile_url}\">{name}</a> (@{matched})\n\n"
                                   f"{best['text'][:400]}\n\n"
                                   f"🕐 {best['created']}")
                        await tg_send(caption, photo=best.get("photo_url"), button_url=tweet_url)
                        log.info(f"Forwarded @{matched} {best['id']}")

            except Exception as e:
                log.warning(f"Poll error: {e}")

        await asyncio.sleep(0.5)

if __name__ == "__main__":
    asyncio.run(main())
