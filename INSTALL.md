# HidLink — Install Guide

## Prerequisites

- **OS**: Windows 10 or 11
- **Python**: 3.12 or higher ([python.org](https://python.org))
- **Node.js** (optional, for localtunnel) — [nodejs.org](https://nodejs.org)

---

## Step 1 — Clone & enter

```bash
git clone https://github.com/yourname/hidlink.git
cd hidlink
```

## Step 2 — Configure `.env`

```bash
notepad .env
```

Adjust any settings (the defaults work out of the box):

| Variable            | Default     | Description |
|---------------------|-------------|-------------|
| `PIN`               | `0000`      | Auth PIN (change this!) |
| `PORT`              | `8000`      | Local server port |
| `SESSION_TTL`       | `3600`      | Session expiry in seconds |
| `RATE_LIMIT_ATTEMPTS` | `5`       | Failed attempts before lockout |
| `RATE_LIMIT_WINDOW`   | `60`      | Lockout duration in seconds |
| `TERMINAL_TIMEOUT`    | `30`      | Max command execution time |
| `TUNNEL_MODE`         | `localtunnel` | `localtunnel` or `cloudflare` |
| `CLOUDFLARE_TOKEN`    | *(empty)* | Required for persistent Cloudflare tunnel |

## Step 3 — Install Python dependencies

```bash
pip install -r requirements.txt
```

## Step 4 — Start the server

```bash
python launcher.py
```

You'll see:

```
[HidLink] Local:      http://127.0.0.1:8000
[HidLink] Public URL: https://some-string.loca.lt
```

Open the **Public URL** on your phone.

## Step 5 — Login

Enter the PIN from your `.env` file (default: `0000`).

---

## Manual start (without launcher)

```bash
python server.py
```

In a separate terminal:

```bash
npx localtunnel --port 8000
```

---

## Cloudflare Tunnel (persistent URL)

1. Install [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
2. Set `TUNNEL_MODE=cloudflare` in `.env`
3. (Optional) Set `CLOUDFLARE_TOKEN=your-token` for a named tunnel
4. Run `python launcher.py`

Without a token, launcher creates a quick `trycloudflare.com` tunnel (random URL each time).

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `pyautogui` screenshot fails on locked screen | Run server as Administrator |
| Tunnel not starting | Ensure `node --version` works (for localtunnel) |
| `npx` not recognised | Install Node.js from nodejs.org |
| Port conflict | Change `PORT` in `.env` |
| Blank page on phone | Clear browser cache / hard refresh |

Check `error.log` and `launcher.log` in the project root for diagnostics.
