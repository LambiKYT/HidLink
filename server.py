import asyncio
import io
import json
import os
import platform
import shutil
import sqlite3
import string
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import psutil
import pyautogui
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv(Path(__file__).parent / ".env")

HERE = Path(__file__).parent
STATIC_DIR = HERE / "static"
CONFIG_PATH = HERE / "config.json"
DB_PATH = HERE / "db.sqlite"
ERROR_LOG = HERE / "error.log"


def _write_error(context: str, detail: str = "") -> None:
    try:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        with ERROR_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"[{now}] {context}: {detail}\n")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Settings (from .env)
# ---------------------------------------------------------------------------
VALID_PIN = os.getenv("HIDLINK_PIN", "0000")
PORT = int(os.getenv("HIDLINK_PORT", "8000"))
HOST = os.getenv("HIDLINK_HOST", "0.0.0.0")
SESSION_TTL = int(os.getenv("HIDLINK_SESSION_TTL", "21600"))
MAX_PIN_ATTEMPTS = int(os.getenv("HIDLINK_MAX_PIN_ATTEMPTS", "5"))
PIN_LOCKOUT = int(os.getenv("HIDLINK_PIN_LOCKOUT", "60"))
MAX_UPLOAD_BYTES = int(os.getenv("HIDLINK_MAX_UPLOAD_MB", "100")) * 1024 * 1024
TERMINAL_TIMEOUT = int(os.getenv("HIDLINK_TERMINAL_TIMEOUT", "15"))
MACRO_TIMEOUT = int(os.getenv("HIDLINK_MACRO_TIMEOUT", "30"))

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    assert _conn is not None
    return _conn


def _init_db() -> None:
    global _conn
    _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode=WAL")
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
    """)
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS auth_attempts (
            ip TEXT PRIMARY KEY,
            count INTEGER NOT NULL DEFAULT 0,
            lockout_until REAL NOT NULL DEFAULT 0
        )
    """)
    _conn.commit()


def _cleanup_sessions() -> None:
    _db().execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
    _db().commit()


def _verify_token(token: str | None) -> str:
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")
    now = time.time()
    row = _db().execute(
        "SELECT expires_at FROM sessions WHERE token = ?", (token,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if row["expires_at"] < now:
        _db().execute("DELETE FROM sessions WHERE token = ?", (token,))
        _db().commit()
        raise HTTPException(status_code=401, detail="Session expired")
    return token


def _check_rate_limit(ip: str) -> bool:
    now = time.time()
    row = _db().execute(
        "SELECT count, lockout_until FROM auth_attempts WHERE ip = ?", (ip,)
    ).fetchone()
    if row and row["lockout_until"] > now:
        return False
    if row and row["lockout_until"] <= now and row["count"] >= MAX_PIN_ATTEMPTS:
        return True
    return True


def _record_attempt(ip: str, success: bool) -> None:
    now = time.time()
    row = _db().execute(
        "SELECT count FROM auth_attempts WHERE ip = ?", (ip,)
    ).fetchone()
    if success:
        _db().execute("DELETE FROM auth_attempts WHERE ip = ?", (ip,))
    elif row:
        new_count = row["count"] + 1
        lockout = now + PIN_LOCKOUT if new_count >= MAX_PIN_ATTEMPTS else 0
        _db().execute(
            "UPDATE auth_attempts SET count = ?, lockout_until = ? WHERE ip = ?",
            (new_count, lockout, ip),
        )
    else:
        _db().execute(
            "INSERT INTO auth_attempts (ip, count, lockout_until) VALUES (?, 1, 0)",
            (ip,),
        )
    _db().commit()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text("utf-8"))
        except Exception as exc:
            _write_error("load_config", str(exc))
    return {"macros": []}


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

def _is_safe_path(base_dir: Path, requested: Path) -> bool:
    try:
        base = base_dir.resolve()
        target = requested.resolve()
        common = Path(os.path.commonpath([base, target]))
        return common == base or common == target
    except (ValueError, OSError):
        return False


# ---------------------------------------------------------------------------
# Windows helpers (blocking — run in threads)
# ---------------------------------------------------------------------------

def _win_lock():
    import ctypes
    ctypes.windll.user32.LockWorkStation()


def _win_sleep():
    import ctypes
    ctypes.windll.powrprof.SetSuspendState(False, True, False)


def _win_vkey(code: int):
    import ctypes
    ctypes.windll.user32.keybd_event(code, 0, 0, 0)
    ctypes.windll.user32.keybd_event(code, 0, 2, 0)


VK = {
    "volume_up": 0xAF,
    "volume_down": 0xAE,
    "media_next": 0xB0,
    "media_prev": 0xB1,
    "play_pause": 0xB3,
}


def _clipboard_get() -> str:
    import ctypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(13)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            size = kernel32.GlobalSize(handle)
            raw = ctypes.string_at(ptr, size)
            text = raw.decode("utf-16-le")
            return text.rstrip("\x00")
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _clipboard_set(text: str):
    import ctypes
    GMEM_MOVABLE = 0x0002
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    encoded = (text + "\0").encode("utf-16-le")
    handle = kernel32.GlobalAlloc(GMEM_MOVABLE, len(encoded))
    if not handle:
        return
    ptr = kernel32.GlobalLock(handle)
    if ptr:
        ctypes.memmove(ptr, encoded, len(encoded))
        kernel32.GlobalUnlock(handle)
    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(handle)
        return
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(13, handle):
            kernel32.GlobalFree(handle)
    finally:
        user32.CloseClipboard()


def _temperatures() -> dict:
    result = {"cpu": None}
    try:
        r = subprocess.run(
            ["wmic", "/namespace:\\\\root\\wmi", "PATH",
             "MSAcpi_ThermalZoneTemperature", "get", "CurrentTemperature"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in r.stdout.strip().splitlines()[1:]:
            line = line.strip()
            if line.isdigit():
                result["cpu"] = round((int(line) - 2732) / 10, 1)
                break
    except Exception as exc:
        _write_error("cpu_temperature", str(exc))
    return result


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class TokenBody(BaseModel):
    token: str | None = None
    model_config = {"extra": "allow"}


class PinBody(BaseModel):
    pin: str


class TextBody(TokenBody):
    text: str | None = None


class CommandBody(TokenBody):
    command: str


class PathBody(TokenBody):
    path: str


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):
    _init_db()
    _cleanup_sessions()
    psutil.cpu_percent(interval=0)
    yield
    if _conn:
        _conn.close()


app = FastAPI(title="HidLink v3.3", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:"
    )
    return response


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Public routes
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    return HTMLResponse((HERE / "templates" / "index.html").read_text("utf-8"))


@app.post("/api/verify-pin")
async def verify_pin(body: PinBody, request: Request):
    ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Too many attempts, try later")
    if body.pin != VALID_PIN:
        _record_attempt(ip, False)
        raise HTTPException(status_code=403, detail="Wrong PIN")
    _record_attempt(ip, True)
    token = uuid.uuid4().hex
    now = time.time()
    _db().execute(
        "INSERT INTO sessions (token, created_at, expires_at) VALUES (?, ?, ?)",
        (token, now, now + SESSION_TTL),
    )
    _db().commit()
    return {"token": token}


# -----------------------------------------------------------------------
# Monitoring
# -----------------------------------------------------------------------

@app.post("/api/stats")
async def stats(body: TokenBody):
    _verify_token(body.token)
    cpu = psutil.cpu_percent(interval=0)
    ram = psutil.virtual_memory().percent
    disks = {}
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if os.path.isdir(drive):
            try:
                u = psutil.disk_usage(drive)
                disks[letter] = {"total": u.total, "used": u.used, "percent": u.percent}
            except Exception:
                disks[letter] = None
    return {"cpu": cpu, "ram": ram, "disks": disks}


@app.post("/api/processes")
async def processes(body: TokenBody):
    _verify_token(body.token)
    procs = []
    for p in sorted(psutil.process_iter(["pid", "name", "cpu_percent"]),
                    key=lambda p: p.info.get("cpu_percent") or 0, reverse=True)[:10]:
        try:
            info = p.info
            procs.append({"pid": info["pid"], "name": info["name"] or "?",
                          "cpu": info["cpu_percent"] or 0})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return {"processes": procs}


@app.post("/api/hardware")
async def hardware(body: TokenBody):
    _verify_token(body.token)
    return await asyncio.to_thread(_temperatures)


# -----------------------------------------------------------------------
# Screenshot
# -----------------------------------------------------------------------

@app.post("/api/screenshot")
async def screenshot(body: TokenBody):
    _verify_token(body.token)
    try:
        img = await asyncio.to_thread(pyautogui.screenshot)
    except Exception as exc:
        return {"error": f"Screenshot failed: {exc}"}
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=65, optimize=True)
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/jpeg",
                             headers={"Cache-Control": "no-cache"})


# -----------------------------------------------------------------------
# Clipboard
# -----------------------------------------------------------------------

@app.post("/api/clipboard")
async def clipboard(body: TextBody):
    _verify_token(body.token)
    if body.text is not None:
        await asyncio.to_thread(_clipboard_set, body.text)
        return {"status": "ok"}
    text = await asyncio.to_thread(_clipboard_get)
    return {"text": text}


# -----------------------------------------------------------------------
# Terminal
# -----------------------------------------------------------------------

@app.post("/api/terminal")
async def terminal(body: CommandBody):
    _verify_token(body.token)
    try:
        proc = await asyncio.create_subprocess_shell(
            body.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=TERMINAL_TIMEOUT
        )
        return {
            "stdout": (stdout.decode(errors="replace") or "")[-3000:],
            "stderr": (stderr.decode(errors="replace") or "")[-1000:],
            "returncode": proc.returncode,
        }
    except asyncio.TimeoutExpired:
        try:
            proc.kill()
        except Exception as exc:
            _write_error("terminal_kill", str(exc))
        return {"stdout": "", "stderr": f"Command timed out ({TERMINAL_TIMEOUT}s)", "returncode": -1}
    except Exception as exc:
        return {"stdout": "", "stderr": str(exc), "returncode": -1}


# -----------------------------------------------------------------------
# File Manager
# -----------------------------------------------------------------------

@app.post("/api/files/list")
async def files_list(body: PathBody):
    _verify_token(body.token)
    requested = Path(body.path)
    if not requested.exists() or not requested.is_dir():
        raise HTTPException(404, "Directory not found")
    if not _is_safe_path(HERE, requested):
        raise HTTPException(403, "Access denied")
    try:
        entries = []
        for child in sorted(requested.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            try:
                stat = child.stat()
                entries.append({
                    "name": child.name,
                    "path": str(child),
                    "is_dir": child.is_dir(),
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                })
            except (PermissionError, OSError):
                pass
        return {
            "path": str(requested),
            "parent": str(requested.parent) if requested.parent != requested else None,
            "entries": entries,
        }
    except PermissionError:
        raise HTTPException(403, "Access denied")


@app.post("/api/files/download")
async def files_download(body: PathBody):
    _verify_token(body.token)
    file = Path(body.path)
    if not file.exists() or not file.is_file():
        raise HTTPException(404, "File not found")
    if not _is_safe_path(HERE, file):
        raise HTTPException(403, "Access denied")
    return FileResponse(str(file))


@app.post("/api/files/upload")
async def files_upload(
    dest: str = Form(...),
    file: UploadFile = File(...),
    token: str = Form(None),
):
    _verify_token(token)
    dest_dir = Path(dest)
    if not dest_dir.is_dir():
        raise HTTPException(404, "Destination directory not found")
    if not _is_safe_path(HERE, dest_dir):
        raise HTTPException(403, "Access denied")
    safe_name = Path(file.filename).name
    if not safe_name:
        raise HTTPException(400, "Invalid filename")
    dest_path = dest_dir / safe_name
    total = 0
    try:
        with dest_path.open("wb") as fh:
            while True:
                chunk = await file.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    fh.close()
                    dest_path.unlink(missing_ok=True)
                    raise HTTPException(413, f"File too large (max {MAX_UPLOAD_BYTES // 1048576} MB)")
                fh.write(chunk)
    except HTTPException:
        raise
    except Exception as exc:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(500, f"Upload failed: {exc}")
    return {"status": "ok", "path": str(dest_path)}


# -----------------------------------------------------------------------
# Macros
# -----------------------------------------------------------------------

@app.post("/api/macros")
async def macros_list(body: TokenBody):
    _verify_token(body.token)
    return _load_config()


@app.post("/api/macros/run/{name}")
async def macros_run(name: str, body: TokenBody):
    _verify_token(body.token)
    cfg = _load_config()
    for m in cfg.get("macros", []):
        if m.get("name") == name:
            cmd = m.get("command", "")
            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=MACRO_TIMEOUT
                )
                return {
                    "status": "ok",
                    "stdout": (stdout.decode(errors="replace") or "")[-2000:],
                    "stderr": (stderr.decode(errors="replace") or "")[-500:],
                    "returncode": proc.returncode,
                }
            except asyncio.TimeoutExpired:
                try:
                    proc.kill()
                except Exception as exc:
                    _write_error("macro_kill", str(exc))
                return {"status": "error", "message": "Command timed out"}
            except Exception as exc:
                return {"status": "error", "message": str(exc)}
    return {"status": "error", "message": f"Macro '{name}' not found"}


# -----------------------------------------------------------------------
# System Commands
# -----------------------------------------------------------------------

@app.post("/api/command/{action}")
async def command(action: str, body: TokenBody):
    _verify_token(body.token)

    if action.startswith("kill/"):
        pid_str = action.split("/", 1)[1]
        try:
            pid = int(pid_str)
            psutil.Process(pid).terminate()
            return {"status": "ok", "action": f"kill {pid}"}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    if platform.system() != "Windows":
        return {"status": "error", "message": "Only Windows is supported"}

    try:
        match action:
            case "lock":          await asyncio.to_thread(_win_lock)
            case "sleep":         await asyncio.to_thread(_win_sleep)
            case "volume_up":     await asyncio.to_thread(_win_vkey, VK["volume_up"])
            case "volume_down":   await asyncio.to_thread(_win_vkey, VK["volume_down"])
            case "media_next":    await asyncio.to_thread(_win_vkey, VK["media_next"])
            case "media_prev":    await asyncio.to_thread(_win_vkey, VK["media_prev"])
            case "play_pause":    await asyncio.to_thread(_win_vkey, VK["play_pause"])
            case _:
                return {"status": "error", "message": f"Unknown action: {action}"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}

    return {"status": "ok", "action": action}


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    print(f"HidLink v3.3 — http://{HOST}:{PORT}")
    print(f"PIN: {VALID_PIN}")
    uvicorn.run("server:app", host=HOST, port=PORT, reload=True)
