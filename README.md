<div align="center">

# 🤖 x-tg-notify

**Real-time X (Twitter) notification forwarder to Telegram**

Monitor specific X accounts and receive instant Telegram alerts when they post — with photos, clickable links, and inline buttons.

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

</div>

---

## 📌 Features

- **🔔 Real-time notifications** — polls X notifications every 5 seconds
- **📸 Photo support** — forwards tweets with images as Telegram photos
- **🔗 Inline button** — "Go to Post" button links directly to the tweet
- **👤 Clickable usernames** — `@username` links to X profile
- **➕ Add/Remove users** — Telegram bot commands to manage watched accounts
- **🔄 Auto-follow** — automatically follows users when added + enables post notifications
- **🔁 Dedup system** — never forwards the same tweet twice (persists across restarts)
- **⚡ Low-latency** — matches X web client's own polling interval (~28s), we do 5s

---

## 🏗️ How It Works

```
┌──────────────┐    NotificationsTimeline     ┌──────────────┐
│   X (Twitter)│ ◄──── GraphQL Poll (5s) ──── │  x-tg-notify │
│   API        │ ──── bell_icon notif ───────► │    Bot       │
└──────────────┘                               │              │
                                               │  ┌──────────┐│
┌──────────────┐    sendMessage / sendPhoto    │  │ Watchlist ││
│   Telegram   │ ◄──────────────────────────── │  │  .json    ││
│   Bot API    │ ────── /add, /remove ───────► │  └──────────┘│
└──────────────┘                               └──────────────┘
```

1. Bot polls `NotificationsTimeline` GraphQL endpoint every 5 seconds
2. When a `bell_icon` notification appears (user posted), it fetches the latest tweet
3. Tweet is forwarded to Telegram with photo (if any) + "Go to Post" button
4. Dedup via tweet ID — same tweet never forwarded twice

---

## 📦 Dependencies

All dependencies are installable via pip. **No external scripts or local files required.**

| Package | Purpose |
|---------|---------|
| [`curl_cffi`](https://github.com/yifeikong/curl_cffi) | Chrome TLS fingerprint impersonation |
| [`x_client_transaction`](https://github.com/iSarabjitDhiman/XClientTransaction) | X anti-detection (`x-client-transaction-id` header) |

| [`httpx`](https://github.com/encode/httpx) | Async HTTP client for Telegram API |
| [`beautifulsoup4`](https://www.crummy.com/software/BeautifulSoup/) | HTML parsing for X page initialization |

---

## 🚀 Quick Start

### 1. Clone
```bash
git clone https://github.com/oxkage/x-tg-notify.git
cd x-tg-notify
```

### 2. Run
```bash
python3 bot.py
```

On first run, the **setup wizard** launches automatically — it will:
- ✅ Check Python 3.10+
- ✅ Create virtual environment
- ✅ Install all dependencies
- ✅ Guide you through getting each credential (step-by-step)
- ✅ Validate Telegram connection (sends test message)
- ✅ Verify X credential format
- ✅ Save everything to `.env`

### 3. Add accounts via Telegram
Send `/add @username` to your bot — it follows them and enables notifications.

### Re-run setup
```bash
python3 bot.py --setup
```

### Manual setup (alternative)
If you prefer editing `.env` directly:
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit with your credentials
python3 bot.py
```

---

## 🔑 Getting Credentials

The setup wizard walks you through this interactively. For reference:

### X Auth Token & CT0
1. Open [x.com](https://x.com) in your browser and log in
2. Open DevTools (F12) → Application → Cookies → `x.com`
3. Copy `auth_token` value (40-char hex)
4. Copy `ct0` value (long hex string)

### Telegram Bot Token
1. Open [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the instructions
3. Copy the token (format: `123456:ABC-DEF...`)

### Telegram Chat ID
1. Send any message to your bot
2. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Find `"chat":{"id":XXXXX}` — that number is your Chat ID

---

## 🎮 Bot Commands

Send these to your Telegram bot:

| Command | Description |
|---------|-------------|
| `/add @username` | Follow user + enable post notifications |
| `/remove @username` | Disable notifications + unfollow |
| `/list` | Show all watched users |
| `/help` | Show help message |

### Example

```
You: /add elonmusk
Bot: ⏳ Resolving @elonmusk...
Bot: ✅ Added Elon Musk
     👤 @elonmusk
     • Followed ✅
     • Notifications 🔔
     [👤 View Profile]  [🔕 Stop Notify]
```

When `@elonmusk` posts:

```
Bot: 🔔 New Post
     👤 Elon Musk (@elonmusk)

     📝 The thing about gravity is...

     🕐 Mar 27, 2026 · 14:30 UTC

     [🔗 Go to Post]  [🔕 Stop Notify]
```
---

## 📁 File Structure

```
x-tg-notify/
├── bot.py              # Main bot (setup wizard + runtime)
├── requirements.txt    # Python dependencies
├── .env.example        # Credential template with instructions
├── .env                # Your credentials (git-ignored, auto-created by wizard)
├── venv/               # Virtual environment (auto-created by wizard)
├── watchlist.json      # Watched users (auto-created)
├── seen_state.json     # Dedup state (auto-created)
└── README.md
```

---

## ⚙️ Configuration

### Polling Interval

Default: **5 seconds**. Change in `bot.py`:

```python
POLL_INTERVAL = 5  # seconds
```

### Rate Limits

X's `NotificationsTimeline` endpoint has a rate limit of **1500 requests per ~16 minutes**.

| Interval | Requests/16min | % of Limit |
|----------|---------------|------------|
| 5s | 192 | 13% ✅ |
| 10s | 96 | 6% ✅ |
| 30s | 32 | 2% ✅ |

5s polling uses ~13% of the rate limit — safe for continuous operation.

---

## 🔧 Technical Details

### API Endpoints Used

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `x.com/i/api/graphql/ZhJlpN0aKcSkccG5Pz1MEw/NotificationsTimeline` | GET | Poll notifications |
| `x.com/i/api/graphql/1VOOyvKkiI3FMmkeDNxM9A/UserByScreenName` | GET | Resolve username to ID |
| `x.com/i/api/1.1/friendships/create.json` | POST | Follow user |
| `x.com/i/api/1.1/friendships/destroy.json` | POST | Unfollow user |
| `x.com/i/api/1.1/friendships/update.json` | POST | Enable/disable notifications |
| `api.telegram.org/bot{token}/sendMessage` | POST | Send Telegram text |
| `api.telegram.org/bot{token}/sendPhoto` | POST | Send Telegram photo |

### Anti-Detection

- Uses `curl_cffi` with Chrome TLS fingerprint impersonation
- `x-client-transaction-id` header generated per request
- Full cookie set: `auth_token`, `ct0`, `twid`, `lang`
- Matches X web client's feature flags exactly

### Dedup System

Two-layer dedup:

1. **Tweet ID tracking** — `seen_tweet_ids[username] = "tweet_id"` — prevents same tweet forwarded twice
2. **Persistent state** — `seen_state.json` survives restarts — no re-forwarding of old tweets on restart

---

## 🛡️ Security Notes

- **Never commit `.env`** — it contains your auth credentials
- `auth_token` and `ct0` are session cookies — they expire when you log out of X
- If the bot stops working, re-copy fresh cookies from your browser
- The bot uses your X account to follow users — be mindful of X's follow limits

---

## 🐛 Troubleshooting

### Bot says "409 Conflict"

Another bot instance is running. Kill it:
```bash
pkill -f "python3 bot.py"
```

### Bot says "Unauthorized" on notifications

Your `auth_token` may be expired. Get fresh cookies from your browser.

### Bot not forwarding new posts

1. Check that the user is in your watchlist: `/list`
2. Verify the bot is running and polling (check logs)
3. Make sure you enabled notifications for the user (`/add` does this automatically)

### Bot forwards old tweets on restart

This shouldn't happen — `seen_state.json` persists tweet IDs. If it does:
```bash
rm seen_state.json
python3 bot.py  # Will re-seed on first run
```

---

## 📄 License

MIT License — see [LICENSE](LICENSE) file.

---

## 🙏 Credits

- [curl_cffi](https://github.com/yifeikong/curl_cffi) — TLS fingerprint impersonation
- [XClientTransaction](https://github.com/iSarabjitDhiman/XClientTransaction) — X anti-detection headers


---

<div align="center">

**Built with 🪄 by [Kage](https://github.com/oxkage)**

</div>
