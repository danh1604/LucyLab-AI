"""Local gpt2api lifecycle helpers used by image generation."""
import os
import subprocess
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlparse


_START_LOCK = threading.Lock()
_START_PROCESS: subprocess.Popen | None = None
_STARTED_AT = 0.0


def _local_endpoint_ready(base_url: str) -> bool:
    """Check the OpenAI route itself, not merely whether its TCP port is open."""
    parsed = urlparse((base_url or "http://127.0.0.1:17200/v1").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    models_url = f"{(base_url or '').rstrip('/')}/models"
    try:
        request = Request(models_url, method="GET")
        with urlopen(request, timeout=2.5) as response:
            return 200 <= response.status < 500
    except HTTPError as exc:
        # A configured API key is intentionally not sent by this lifecycle
        # probe; 401 therefore proves that the OpenAI route is present.
        return exc.code not in {404, 502, 503, 504}
    except (URLError, OSError, ValueError):
        return False


def _admin_routes_ready(cfg: dict) -> bool:
    """Confirm the account-management routes exist before reporting ready."""
    admin_base_url = str(cfg.get("gpt2api_admin_base_url") or "http://127.0.0.1:17188").strip().rstrip("/")
    accounts_url = f"{admin_base_url}/admin/api/v1/accounts?provider=gpt&page=1&page_size=1"
    try:
        request = Request(accounts_url, method="GET")
        with urlopen(request, timeout=2.5) as response:
            return 200 <= response.status < 500
    except HTTPError as exc:
        # This endpoint normally returns 401 without the admin session; that
        # is a healthy route.  A degraded admin process returns 404 instead.
        return exc.code not in {404, 502, 503, 504}
    except (URLError, OSError, ValueError):
        return False


def _gpt2api_ready(cfg: dict, base_url: str) -> bool:
    return _local_endpoint_ready(base_url) and _admin_routes_ready(cfg)


def _start_script_path(cfg: dict) -> Path | None:
    configured = str(cfg.get("gpt2api_start_script") or "").strip().strip('"')
    candidates = [Path(configured)] if configured else []
    key_path = str(cfg.get("scene_gpt2api_api_key_path") or "").strip().strip('"')
    if key_path:
        key_file = Path(key_path)
        candidates.append(key_file.parent.parent / "start-gpt2api.ps1")
        candidates.append(key_file.parent.parent / "start-gpt2api.bat")
    # Also look relative to project root
    from tts_service import BASE_DIR
    candidates.append(BASE_DIR / "start-gpt2api.bat")
    candidates.append(BASE_DIR / "start-gpt2api.ps1")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def start_gpt2api(cfg: dict) -> dict:
    """Start local gpt2api once, returning immediately while Docker boots."""
    global _START_PROCESS, _STARTED_AT
    base_url = str(cfg.get("base_url") or cfg.get("scene_gpt2api_base_url") or "http://127.0.0.1:17200/v1")
    if _gpt2api_ready(cfg, base_url):
        return {"status": "ready", "base_url": base_url}
    if not bool(cfg.get("gpt2api_autostart", True)):
        raise RuntimeError("gpt2api chưa chạy và tự khởi động đang tắt trong cấu hình.")

    with _START_LOCK:
        if _gpt2api_ready(cfg, base_url):
            return {"status": "ready", "base_url": base_url}
        if _START_PROCESS is not None and _START_PROCESS.poll() is None:
            return {"status": "starting", "base_url": base_url, "started_at": _STARTED_AT}
        script = _start_script_path(cfg)
        if not script:
            raise RuntimeError(
                "Không tìm thấy start-gpt2api.bat/ps1. Đặt gpt2api_start_script hoặc chạy setup-gpt2api.bat trước."
            )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        if script.suffix.lower() == ".bat":
            cmd = ["cmd.exe", "/c", str(script)]
        else:
            cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
        _START_PROCESS = subprocess.Popen(
            cmd,
            cwd=str(script.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        _STARTED_AT = time.time()
        return {"status": "starting", "base_url": base_url, "script": str(script), "started_at": _STARTED_AT}


def ensure_gpt2api_ready(cfg: dict, wait_seconds: float = 240) -> None:
    """Start gpt2api if needed and wait only at the moment an image job needs it."""
    result = start_gpt2api(cfg)
    if result["status"] == "ready":
        return
    base_url = str(result["base_url"])
    deadline = time.monotonic() + max(5.0, float(wait_seconds))
    while time.monotonic() < deadline:
        if _gpt2api_ready(cfg, base_url):
            return
        time.sleep(1)
    raise RuntimeError(f"gpt2api chưa sẵn sàng tại {base_url} sau {int(wait_seconds)} giây.")
