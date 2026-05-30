# HidLink

Remote PC management from your phone via browser — no app install required.

HidLink turns your Windows machine into a personal cloud server with a real-time dashboard. Monitor CPU/RAM/disk, view screenshots, browse files, run terminal commands, control system functions, and automate tasks — all through a glassmorphism PWA that looks native on mobile.

---

## Features

- **Real‑time monitoring** — CPU, RAM, disk usage, top processes, CPU temperatures
- **Screenshots** — View your desktop live from your phone
- **File manager** — Browse, download, and upload files securely
- **Terminal** — Execute CMD commands remotely with time‑limited sessions
- **System control** — Lock, sleep, kill processes, adjust volume
- **Clipboard** — Read and write clipboard text remotely
- **Macros** — Pre‑configured one‑click command sequences
- **PWA** — Add to home screen, works offline (cached UI)
- **Tunnel support** — localtunnel (zero‑config) or Cloudflare Tunnel (persistent)
- **Brute‑force protection** — Rate‑limited auth with SQLite‑backed session management

---

## Stack

| Component  | Technology |
|-----------|-----------|
| Backend   | Python 3.12+, FastAPI, Uvicorn |
| Frontend  | HTML / CSS (Glassmorphism) / Vanilla JS |
| Charts    | Chart.js 4.x (self‑hosted, offline) |
| System    | psutil, pyautogui, ctypes |
| Auth      | Token sessions (SQLite, persistent) |
| Tunnel    | localtunnel (default) / Cloudflare Tunnel |
| PWA       | Manifest + Service Worker (offline cache) |

---

## Quick Start

```bash
pip install -r requirements.txt
python launcher.py
```

Open the printed URL on your phone and enter PIN **0000**.

[Full install guide →](INSTALL.md)

---

## Tunnel Modes

| Variable                | Mode | Setup |
|-------------------------|------|-------|
| `TUNNEL_MODE=localtunnel` | **Zero‑config** (default) | No extra install — uses `npx localtunnel` |
| `TUNNEL_MODE=cloudflare`  | **Persistent URL** | Install [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) and optionally set `CLOUDFLARE_TOKEN` |

---

## Security

- PIN is read from `.env` — never hardcoded
- Session tokens stored in SQLite with configurable TTL
- Brute‑force lockout: 5 failed attempts → 60s block per IP
- All requests require token except `/api/verify-pin`
- Path traversal prevented via `os.path.commonpath` verification
- CORS restricted to localhost and tunnel origin
- Security headers: CSP, X‑Frame‑Options, X‑Content‑Type‑Options, Referrer‑Policy
- EOL in terminal: LF and null‑byte sanitisation

---

## License

MIT © 2026 HidLink
