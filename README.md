<div align="center">

# x-tg-notify

**Real-time X (Twitter) notification forwarder to Telegram**

[![Python](https://img.shields.io/badge/Python-3.10+-3776ab?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-44cc11?style=flat-square)](LICENSE)

Monitor X accounts and receive instant Telegram alerts when they post — with photos, videos, clickable links, and inline action buttons.

[Features](#features) · [Quick Start](#quick-start) · [Commands](#commands) · [Configuration](#configuration) · [Debug CLI](#debug-cli)

</div>

---

## Features

- **Real-time forwarding** — polls X notifications every 5 seconds, forwards new posts instantly
- **Rich messages** — structured formatting with headings, dividers, and embedded media
- **Photo + Video + GIF** — media types individually toggleable via `/settings`
- **Inline buttons** — "Go to Post" links directly to the tweet, "Stop Notify" removes the account
- **Auto-follow** — `/add` automatically follows the user and enables post notifications
- **Deduplication** — tweet ID tracking persists across restarts, never forwards the same tweet twice
- **Anti-detection** — Chrome TLS fingerprint impersonation + per-request transaction headers
- **Setup wizard** — interactive first-run guides you through credentials, creates venv, validates connections
- **Rate limit aware** — exponential backoff on 429s, stays within X API limits
- **Single instance lock** — PID file prevents multiple bot instances running simultaneously
- **Timeline fallback** — catches tweets missed by notification polling (every 60 seconds)

---

## Quick Start

### 1. Clone and run

```bash
git clone https://github.com/oxkage/x-tg-notify.git
cd x-tg-notify
python3 bot.py
```

On first run, the **setup wizard** launches automatically:

1. Checks Python 3.10+
2. Creates virtual environment and installs dependencies
3. Walks you through getting each credential (step-by-step with instructions)
4. Validates Telegram connection (sends test message)
5. Verifies X credential format
6. Saves everything to `.env`

### 2. Add accounts

Send `/add @username` to your Telegram bot — it follows them and enables notifications.

### 3. Done

New posts arrive in your Telegram chat with photos, timestamps, and inline buttons.

> [!TIP]
> Re-run the setup wizard anytime with `python3 bot.py --setup`

---

## Commands

| Command | Description |
|---------|-------------|
| `/add @username` | Follow user + enable post notifications |
| `/remove @username` | Disable notifications + unfollow |
| `/list` | Show all watched users |
| `/status` | Bot status + stats |
| `/settings` | Toggle media types (photo/video/gif) |
| `/help` | Show help message |

**Example — adding an account:**

```
You:    /add elonmusk
Bot:    ⏳ Resolving @elonmusk...
Bot:    ✅ Added Elon Musk
        👤 @elonmusk
        • Followed ✅
        • Notifications 🔔
        [👤 View Profile]  [🔕 Stop Notify]
```

**Example — forwarded post:**

```
Bot:    ## 🔔 New Post

        **Elon Musk** ([@elonmusk](https://x.com/elonmusk))

        The thing about gravity is...

        📸 [embedded photo]

        ---
        🕐 Mar 27, 2026 · 14:30 UTC

        [🔗 Go to Post]  [🔕 Stop Notify]
```

---

## Getting Credentials

The setup wizard handles this interactively. For reference:

<details>
<summary><b>X Auth Token & CT0</b></summary>

1. Open [x.com](https://x.com) in your browser and log in
2. Open DevTools (F12) → Application → Cookies → `x.com`
3. Copy `auth_token` (40-char hex)
4. Copy `ct0` (long hex string)

> [!WARNING]
> These are session cookies — they expire when you log out of X. If the bot stops working, re-copy fresh cookies.
</details>

<details>
<summary><b>Telegram Bot Token</b></summary>

1. Open [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the instructions
3. Copy the token (format: `123456:ABC-DEF...`)
</details>

<details>
<summary><b>Telegram Chat ID</b></summary>

1. Send any message to your bot
2. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Find `"chat":{"id":XXXXX}` — that number is your Chat ID
</details>

---

## Configuration

### Media Settings

Toggle individual media types via `/settings` in Telegram:

| Setting | Default | Description |
|---------|---------|-------------|
| `photo_enabled` | ON | Send tweet photos |
| `video_enabled` | OFF | Send tweet videos |
| `gif_enabled` | ON | Send tweet GIFs |

Posts are always forwarded — toggles only control media attachment.

### Polling interval

Default: **5 seconds**. Change in `bot.py`:

```python
POLL_INTERVAL = 5  # seconds
```

### Rate limits

X's `NotificationsTimeline` endpoint allows ~1500 requests per 16 minutes.

| Interval | Requests / 16min | % of Limit |
|----------|-----------------|------------|
| 5s | 192 | 13% |
| 10s | 96 | 6% |
| 30s | 32 | 2% |

5s polling uses ~13% of the rate limit — safe for continuous operation.

---

## Debug CLI

Test individual components without running the full bot:

```bash
source venv/bin/activate

# Fetch a tweet by ID
python3 debug.py tweet <tweet_id>

# Send a tweet to Telegram (full pipeline)
python3 debug.py send <tweet_id>

# Send text only (no media)
python3 debug.py send-text <tweet_id>

# Test Telegram connection
python3 debug.py test-tg

# Test video/photo sending
python3 debug.py test-video <url>
python3 debug.py test-photo <url>

# Show current notifications
python3 debug.py notifs

# Fetch user timeline
python3 debug.py timeline <username>

# Show state (seen tweets, watchlist, settings)
python3 debug.py state

# Clear seen state
python3 debug.py reset

# Show raw API response
python3 debug.py raw <tweet_id>
```

---

## Architecture

```
X (Twitter) API                    x-tg-notify Bot
┌─────────────────┐                ┌─────────────────┐
│ Notifications   │◄── poll 5s ───│ Poll loop       │
│ Timeline (GQL)  │── bell_icon ──►│                 │
├─────────────────┤                │  ┌───────────┐  │
│ UserTweets (GQL)│◄── fetch ─────│  │ Watchlist  │  │
│                 │── tweet data ──►│  │ (JSON)    │  │
└─────────────────┘                │  └───────────┘  │
                                   │        │        │
Telegram Bot API                   │        ▼        │
┌─────────────────┐                │  ┌───────────┐  │
│ sendRichMessage │◄── forward ───│  │ Dedup     │  │
│ sendMessage     │                │  │ (tweet ID)│  │
│ callbackQuery   │── /add, etc ──►│  └───────────┘  │
└─────────────────┘                └─────────────────┘
```

1. Bot polls `NotificationsTimeline` GraphQL endpoint every 5 seconds
2. On `bell_icon` notification (new post), fetches latest tweets via `UserTweets`
3. Compares tweet ID against stored last-seen ID (snowflake: greater = newer)
4. Forwards to Telegram with rich formatting, embedded media, and inline buttons
5. Timeline fallback (every 60s) catches tweets missed by notification polling

---

## File Structure

```
x-tg-notify/
├── bot.py              # Setup wizard + bot runtime
├── debug.py            # Debug CLI for testing
├── requirements.txt    # Python dependencies
├── .env.example        # Credential template
├── .env                # Your credentials (git-ignored)
├── settings.json       # Media toggle settings (git-ignored)
├── venv/               # Virtual environment (auto-created)
├── watchlist.json      # Watched users (auto-created)
├── seen_state.json     # Dedup state (auto-created)
└── .bot.pid            # Single instance lock (auto-created)
```

---

## Troubleshooting

**409 Conflict** — Another bot instance is running. The PID lock should prevent this, but if it happens:

```bash
pkill -f "python3 bot.py"
rm .bot.pid
python3 bot.py
```

**Unauthorized on notifications** — Your `auth_token` expired. Re-copy fresh cookies from your browser.

**Not forwarding new posts** — Check `/list` to verify the user is watched, and ensure the bot is running.

**Forwards old tweets on restart** — This shouldn't happen (`seen_state.json` persists). If it does:

```bash
rm seen_state.json
python3 bot.py  # Re-seeds on first run
```

---

## Credits

- [curl_cffi](https://github.com/yifeikong/curl_cffi) — TLS fingerprint impersonation
- [XClientTransaction](https://github.com/iSarabjitDhiman/XClientTransaction) — X anti-detection headers
