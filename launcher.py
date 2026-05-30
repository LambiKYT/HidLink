"""
HidLink Launcher v3.3
─────────────────────
Launches server.py + tunnel (localtunnel or cloudflared).
Reads settings from .env.
"""

from __future__ import annotations

import ctypes
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).parent
SERVER_SCRIPT = HERE / "server.py"
ENV_FILE = HERE / ".env"
LOG_FILE = HERE / "launcher.log"
ERROR_LOG = HERE / "error.log"
MAX_LOG_LINES = 500


def _read_env(key: str, default: str = "") -> str:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text("utf-8").splitlines():
            line = line.strip()
            if line.startswith(key + "=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        _rotate_log()
    except Exception as exc:
        _write_error("launcher_log", str(exc))


def _write_error(context: str, detail: str) -> None:
    try:
        with ERROR_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {context}: {detail}\n")
    except Exception:
        print(f"FATAL: cannot write to error.log: {detail}")


def _rotate_log() -> None:
    try:
        lines = LOG_FILE.read_text("utf-8").splitlines()
        if len(lines) > MAX_LOG_LINES:
            LOG_FILE.write_text("\n".join(lines[-MAX_LOG_LINES:]) + "\n", encoding="utf-8")
    except Exception as exc:
        _write_error("rotate_log", str(exc))


def copy_clipboard(text: str) -> None:
    GMEM_MOVABLE = 0x0002
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    encoded = (text + "\0").encode("utf-16-le")
    handle = kernel32.GlobalAlloc(GMEM_MOVABLE, len(encoded))
    if not handle:
        return
    ptr = kernel32.GlobalLock(handle)
    if ptr:
        ctypes.memmove(ptr, encoded, len(encoded))
        kernel32.GlobalUnlock(handle)
    user32.OpenClipboard(None)
    user32.EmptyClipboard()
    user32.SetClipboardData(13, handle)
    user32.CloseClipboard()


def print_url_box(url: str) -> None:
    pad = 4
    line = "\u2500" * (len(url) + pad * 2)
    print()
    print(f"  \u250c{line}\u2510")
    print(f"  \u2502  {'URL':^{len(url)+pad*2-4}}  \u2502")
    print(f"  \u2502{' ' * pad}{url}{' ' * pad}\u2502")
    print(f"  \u2514{line}\u2518")
    print()


def _launch_localtunnel(port: int, server_proc: subprocess.Popen) -> str | None:
    SUBDOMAIN = "hidlink-pc-control"
    FIXED_URL = f"https://{SUBDOMAIN}.loca.lt"
    tunnel_url = ""
    tunnel_url_event = threading.Event()

    log("Запуск localtunnel ...")
    try:
        tunnel_proc = subprocess.Popen(
            ["npx", "localtunnel", "--port", str(port), "--subdomain", SUBDOMAIN],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except FileNotFoundError:
        log("npx не найден. Установите Node.js или запустите туннель вручную.")
        return None

    URL_REGEX = re.compile(r"https?://[\w.-]+(?:loca\.lt|localtunnel\.me)")

    def _reader():
        nonlocal tunnel_url
        start = time.time()
        while time.time() - start < 30:
            line = tunnel_proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            log(f"[localtunnel] {line}")
            m = URL_REGEX.search(line)
            if m:
                tunnel_url = m.group(0)
                log(f"\U0001f517 Tunnel URL: {tunnel_url}")
                tunnel_url_event.set()
                break
        if not tunnel_url:
            tunnel_url = FIXED_URL
            tunnel_url_event.set()

    threading.Thread(target=_reader, daemon=True).start()
    tunnel_url_event.wait(timeout=35)
    return tunnel_url or FIXED_URL


def _launch_cloudflare(port: int, server_proc: subprocess.Popen) -> str | None:
    token = _read_env("CLOUDFLARE_TOKEN", "")
    tunnel_url = ""
    tunnel_url_event = threading.Event()

    if token:
        log("Запуск Cloudflare Tunnel (persistent) ...")
        try:
            tunnel_proc = subprocess.Popen(
                ["cloudflared", "tunnel", "run", "--token", token],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except FileNotFoundError:
            log("cloudflared не найден. Установите cloudflared или смените TUNNEL_MODE.")
            return None
        log("Персистентный туннель запущен (URL задаётся в Cloudflare Dashboard).")
        log(f"Сервер доступен локально: http://localhost:{port}")
        return None
    else:
        log("Запуск Cloudflare Tunnel (quick — trycloudflare.com) ...")
        try:
            tunnel_proc = subprocess.Popen(
                ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except FileNotFoundError:
            log("cloudflared не найден. Установите cloudflared или смените TUNNEL_MODE.")
            return None

    URL_REGEX = re.compile(r"https?://[a-z0-9-]+\.trycloudflare\.com")

    def _reader():
        nonlocal tunnel_url
        start = time.time()
        while time.time() - start < 30:
            line = tunnel_proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            log(f"[cloudflared] {line}")
            m = URL_REGEX.search(line)
            if m:
                tunnel_url = m.group(0)
                log(f"\U0001f517 Tunnel URL: {tunnel_url}")
                tunnel_url_event.set()
                break
        if not tunnel_url:
            tunnel_url_event.set()

    threading.Thread(target=_reader, daemon=True).start()
    tunnel_url_event.wait(timeout=35)
    return tunnel_url


def run() -> None:
    PORT = int(_read_env("HIDLINK_PORT", "8000"))
    TUNNEL_MODE = _read_env("TUNNEL_MODE", "localtunnel")

    log("HidLink v3.3 Launcher")
    log(f"TUNNEL_MODE={TUNNEL_MODE}, PORT={PORT}")
    log("Запуск server.py ...")

    server_proc = subprocess.Popen(
        [sys.executable, str(SERVER_SCRIPT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    log(f"server.py PID={server_proc.pid}")
    time.sleep(2)

    if server_proc.poll() is not None:
        log("server.py завершился с ошибкой при запуске!")
        return

    tunnel_url = None
    if TUNNEL_MODE == "cloudflare":
        tunnel_url = _launch_cloudflare(PORT, server_proc)
    else:
        tunnel_url = _launch_localtunnel(PORT, server_proc)

    if tunnel_url:
        print_url_box(tunnel_url)
        copy_clipboard(tunnel_url)
        log("Ссылка скопирована в буфер обмена!")
    else:
        log(f"Туннель не запущен. Сервер доступен локально: http://localhost:{PORT}")

    log("HidLink запущен. Нажмите Ctrl+C для остановки.")
    try:
        server_proc.wait()
    except KeyboardInterrupt:
        log("Получен Ctrl+C, завершаем процессы...")

    server_proc.terminate()
    try:
        server_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server_proc.kill()

    log("HidLink остановлен.")


if __name__ == "__main__":
    run()
