import io
import platform
import time
from pathlib import Path
from typing import Literal

import psutil
import pyautogui
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse, StreamingResponse

app = FastAPI(title="HidLink")

HERE = Path(__file__).parent

# ---------------------------------------------------------------------------
# Windows-only system helpers
# ---------------------------------------------------------------------------

def _windows_lock():
    import ctypes
    ctypes.windll.user32.LockWorkStation()


def _windows_sleep():
    import ctypes
    ctypes.windll.powrprof.SetSuspendState(False, True, False)


def _windows_volume(direction: Literal["up", "down"]):
    import ctypes
    VK_VOLUME_UP = 0xAF
    VK_VOLUME_DOWN = 0xAE
    key = VK_VOLUME_UP if direction == "up" else VK_VOLUME_DOWN
    ctypes.windll.user32.keybd_event(key, 0, 0, 0)
    ctypes.windll.user32.keybd_event(key, 0, 2, 0)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    html = (HERE / "templates" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@app.get("/api/stats")
async def stats():
    return {
        "cpu": psutil.cpu_percent(interval=0),
        "ram": psutil.virtual_memory().percent,
    }


@app.get("/api/screenshot")
async def screenshot():
    try:
        img = pyautogui.screenshot()
    except Exception as exc:
        return Response(
            content=f'{{"error":"Screenshot failed: {exc}"}}',
            status_code=503,
            media_type="application/json",
        )

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=65, optimize=True)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/api/command/{action}")
async def command(action: str):
    if platform.system() != "Windows":
        return {"status": "error", "message": "Only Windows is supported"}

    try:
        match action:
            case "lock":
                _windows_lock()
            case "sleep":
                _windows_sleep()
            case "volume_up":
                _windows_volume("up")
            case "volume_down":
                _windows_volume("down")
            case _:
                return {"status": "error", "message": f"Unknown action: {action}"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}

    return {"status": "ok", "action": action}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
