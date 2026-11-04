"""Shared image generation helpers for scene frames and thumbnails."""
import base64
import mimetypes
import json
import math
import os
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Callable, Generator
from urllib.parse import urlparse

import requests
from PIL import Image, ImageOps

from tts_service import load_config
from gpt2api_runtime_service import ensure_gpt2api_ready


GPT2API_KEY_PATH = ""
# gpt2api's Codex image2 route defaults its main model to gpt-5.5, which
# ChatGPT accounts now reject; override it per request via params.main_model.
GPT2API_IMAGE_MAIN_MODEL = "gpt-6.1-sol"


def image_provider_config(cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    provider = (cfg.get("image_generation_provider") or "gpt2api").strip().lower()
    if provider not in {"gpt2api", "custom"}:
        provider = "gpt2api"

    if provider == "custom":
        api_key = _resolve_key(
            cfg.get("custom_image_api_key") or os.getenv("CUSTOM_IMAGE_API_KEY") or "",
            cfg.get("custom_image_api_key_path") or "",
        )
        base_url, endpoint = _normalize_image_endpoint(
            cfg.get("custom_image_base_url")
            or os.getenv("CUSTOM_IMAGE_BASE_URL")
            or "https://api.openai.com",
            cfg.get("custom_image_path")
            or os.getenv("CUSTOM_IMAGE_PATH")
            or "/v1/images/edits",
        )
        return {
            "provider": "custom",
            "base_url": base_url,
            "endpoint": endpoint,
            "asset_base_url": _origin_from_url(base_url),
            "api_key": api_key,
            "model": cfg.get("custom_image_model") or os.getenv("CUSTOM_IMAGE_MODEL") or "gpt-image-1",
            "size": cfg.get("custom_image_size") or os.getenv("CUSTOM_IMAGE_SIZE") or "1536x1024",
            "resolution": "",
        }

    api_key = _resolve_key(
        cfg.get("scene_gpt2api_api_key")
        or os.getenv("GPT2API_API_KEY")
        or cfg.get("gpt2api_api_key")
        or "",
        cfg.get("scene_gpt2api_api_key_path") or GPT2API_KEY_PATH,
    )
    base_url, endpoint = _normalize_image_endpoint(
        cfg.get("scene_gpt2api_base_url")
        or os.getenv("GPT2API_BASE_URL")
        or cfg.get("gpt2api_base_url")
        or "http://127.0.0.1:17200/v1"
    )
    return {
        "provider": "gpt2api",
        "base_url": base_url,
        "endpoint": endpoint,
        "asset_base_url": (
            cfg.get("scene_gpt2api_asset_base_url")
            or os.getenv("GPT2API_ASSET_BASE_URL")
            or "http://127.0.0.1:17080"
        ).strip().rstrip("/"),
        "api_key": api_key,
        "model": cfg.get("scene_gpt2api_image_model") or cfg.get("gpt2api_image_model") or "gpt-image-2",
        "size": cfg.get("scene_gpt2api_image_size") or "1792x1024",
        "resolution": cfg.get("scene_gpt2api_image_resolution") or "1K",
        "main_model": cfg.get("scene_gpt2api_main_model") or GPT2API_IMAGE_MAIN_MODEL,
        "gpt2api_autostart": cfg.get("gpt2api_autostart", True),
        "gpt2api_start_script": cfg.get("gpt2api_start_script") or "",
        # Keep the admin endpoint when passing this normalized provider config
        # into the lifecycle checker. Without it, WSL's IPv6 admin route was
        # replaced by the legacy IPv4 default and every image job waited for
        # startup until timing out despite the OpenAI route being live.
        "gpt2api_admin_base_url": cfg.get("gpt2api_admin_base_url") or "http://127.0.0.1:17188",
        "startup_wait_sec": cfg.get("gpt2api_startup_wait_sec") or 240,
    }


def generate_image_file(
    prompt: str,
    target: Path,
    cfg: dict | None = None,
    *,
    size: str | None = None,
    model: str | None = None,
    reference_images: list[str] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> Path:
    for _ in generate_image_events(prompt, target, cfg, size=size, model=model, reference_images=reference_images, cancel_check=cancel_check):
        pass
    return target


def generate_image_events(
    prompt: str,
    target: Path,
    cfg: dict | None = None,
    *,
    size: str | None = None,
    model: str | None = None,
    reference_images: list[str] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> Generator[dict, None, None]:
    provider_cfg = dict(cfg or image_provider_config())
    if size:
        provider_cfg["size"] = size
    if model:
        provider_cfg["model"] = model

    target.parent.mkdir(parents=True, exist_ok=True)
    yield {
        "type": "provider",
        "provider": provider_cfg["provider"],
        "base_url": provider_cfg["base_url"],
        "model": provider_cfg["model"],
        "size": provider_cfg.get("size", ""),
    }

    if provider_cfg["provider"] == "custom":
        yield {"type": "progress", "progress": 1, "status": "requesting"}
        image_value = _generate_custom_image(provider_cfg, prompt, reference_images or [])
        yield {"type": "progress", "progress": 100, "status": "succeeded"}
    else:
        ensure_gpt2api_ready(provider_cfg, wait_seconds=float(provider_cfg.get("startup_wait_sec") or 240))
        image_value = None
        for event in _generate_gpt2api_image_events(provider_cfg, prompt, cancel_check, reference_images or []):
            if event.get("type") == "image":
                image_value = event.get("value")
            else:
                yield event
        if not image_value:
            raise RuntimeError("Image task completed without image data")

    _save_image_value(image_value, target, provider_cfg)
    yield {"type": "saved", "path": str(target), "size_bytes": target.stat().st_size}


def _generate_gpt2api_image(cfg: dict, prompt: str, cancel_check: Callable[[], bool] | None) -> str:
    image_value = ""
    for event in _generate_gpt2api_image_events(cfg, prompt, cancel_check):
        if event.get("type") == "image":
            image_value = str(event.get("value") or "")
    if not image_value:
        raise RuntimeError("Image task completed without image data")
    return image_value


def _generate_gpt2api_image_events(cfg: dict, prompt: str, cancel_check: Callable[[], bool] | None, reference_images: list[str] | None = None) -> Generator[dict, None, None]:
    body = {
        "model": cfg["model"],
        "prompt": prompt,
        "resolution": cfg.get("resolution") or "1K",
        "params": {
            "resolution": cfg.get("resolution") or "1K",
            "size_tier": cfg.get("resolution") or "1K",
            # A scene/reference worker is one independent HTTP request.  Tell
            # the local gpt2api pool that these jobs may reuse its one OAuth
            # account concurrently instead of serializing them behind a lock.
            "allow_parallel_account_reuse": True,
            "main_model": cfg.get("main_model") or GPT2API_IMAGE_MAIN_MODEL,
        },
        "size": cfg.get("size") or "1792x1024",
        "n": 1,
        "async": True,
    }
    refs = [_reference_json_value(ref) for ref in (reference_images or []) if str(ref).strip()]
    refs = [ref for ref in refs if ref]
    # The Codex Responses route receives every data URL in the JSON request.
    # Six full-resolution PNG references previously produced a 13 MB request
    # and repeatedly left its HTTP/2 stream at 5% until it timed out. Keep all
    # user-selected references, but serialize image references as one compact
    # visual board for the transport layer only.
    refs = _compact_gpt2api_reference_images(refs)
    if refs:
        if len(refs) == 1:
            body["image"] = refs[0]
        else:
            body["images"] = refs
            body["ref_assets"] = refs
    res = requests.post(cfg["endpoint"], headers=_headers(cfg), json=body, timeout=90)
    data = _read_json(res)
    if not res.ok:
        if res.status_code == 404:
            raise RuntimeError(
                "gpt2api OpenAI route /v1/images/generations đang trả 404. "
                "Thường là container gpt2api-openai khởi động trước MySQL/Redis nên không mount route. "
                "Hãy restart gpt2api-openai rồi thử lại."
            )
        raise RuntimeError(_error_message(data, f"Image task failed ({res.status_code}) at {cfg['endpoint']}"))

    direct = _first_image_candidate(data)
    if direct:
        yield {"type": "progress", "progress": 100, "status": "succeeded"}
        yield {"type": "image", "value": direct}
        return

    task_id = str(data.get("task_id") or data.get("id") or "").strip()
    if not task_id:
        raise RuntimeError("Image task response did not include task_id")
    yield {"type": "task", "task_id": task_id}

    timeout_at = time.time() + 15 * 60
    last_progress = 0
    succeeded_without_image_at = 0.0
    while time.time() < timeout_at:
        if cancel_check and cancel_check():
            raise RuntimeError("Image generation cancelled")
        poll_url = f"{cfg['base_url'].rstrip('/')}/images/generations/{task_id}"
        poll = requests.get(poll_url, headers=_headers(cfg), timeout=60)
        payload = _read_json(poll)
        if not poll.ok:
            if poll.status_code == 404:
                raise RuntimeError(
                    "gpt2api image task route đang trả 404. "
                    "Hãy restart gpt2api-openai để mount lại /v1/images/generations."
            )
            raise RuntimeError(_error_message(payload, f"Image task poll failed ({poll.status_code})"))
        status = str(payload.get("status") or "").lower()
        try:
            last_progress = int(float(payload.get("progress") or last_progress or 0))
        except Exception:
            last_progress = last_progress or 0
        yield {
            "type": "progress",
            "task_id": task_id,
            "status": status or "running",
            "progress": max(0, min(99, last_progress)),
        }
        if status in {"succeeded", "success", "completed"}:
            image_value = _first_image_candidate(payload)
            if not image_value:
                if not succeeded_without_image_at:
                    succeeded_without_image_at = time.time()
                if time.time() - succeeded_without_image_at < 60:
                    yield {"type": "progress", "task_id": task_id, "status": "finalizing", "progress": 99}
                    time.sleep(2)
                    continue
                raise RuntimeError("Image task succeeded but returned no image URL")
            yield {"type": "progress", "task_id": task_id, "status": "succeeded", "progress": 100}
            yield {"type": "image", "value": image_value}
            return
        succeeded_without_image_at = 0.0
        if status in {"failed", "refunded"}:
            raise RuntimeError(_error_message(payload, "Image generation failed"))
        time.sleep(3)
    raise RuntimeError("Image task timed out")


def _reference_json_value(ref: str) -> str:
    raw = str(ref or "").strip()
    if not raw:
        return ""
    if raw.startswith(("data:", "http://", "https://")):
        return raw
    path = Path(raw).expanduser()
    if not path.exists() or not path.is_file():
        return raw
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _compact_gpt2api_reference_images(references: list[str]) -> list[str]:
    """Keep all reference images while keeping Codex request bodies small.

    gpt2api's Codex route accepts image data URLs, but embedding several raw
    PNGs makes a single HTTP/2 request excessively large.  When every input
    is a readable image, one reference stays as a resized JPEG and multiple
    references become a contact sheet.  URLs or unreadable values are passed
    through unchanged rather than silently dropping a user reference.
    """
    if not references:
        return []

    loaded: list[Image.Image] = []
    for reference in references:
        image = _decode_reference_image(reference)
        if image is None:
            return references
        loaded.append(image)

    try:
        if len(loaded) == 1:
            return [_reference_image_data_url(loaded[0], max_edge=1536)]
        return [_reference_contact_sheet_data_url(loaded)]
    except Exception:
        # Image optimization must never prevent the generation request from
        # being sent. Fall back to the original provider-compatible values.
        return references
    finally:
        for image in loaded:
            try:
                image.close()
            except Exception:
                pass


def _decode_reference_image(value: str) -> Image.Image | None:
    raw = str(value or "").strip()
    if not raw.startswith("data:"):
        return None
    header, separator, payload = raw.partition(",")
    if not separator or not payload or ";base64" not in header.lower():
        return None
    try:
        data = base64.b64decode(payload, validate=True)
        image = Image.open(BytesIO(data))
        image.load()
        return image.convert("RGB")
    except Exception:
        return None


def _reference_image_data_url(image: Image.Image, *, max_edge: int) -> str:
    prepared = ImageOps.contain(image.convert("RGB"), (max_edge, max_edge), Image.Resampling.LANCZOS)
    try:
        return _jpeg_data_url_under_limit(prepared)
    finally:
        prepared.close()


def _reference_contact_sheet_data_url(images: list[Image.Image]) -> str:
    count = len(images)
    columns = min(4, max(2, math.ceil(math.sqrt(count))))
    rows = math.ceil(count / columns)
    # 640px per tile keeps six references legible while a full board remains
    # well below the payload size that makes the Codex HTTP/2 stream unstable.
    tile_w, tile_h, gap = 640, 480, 12
    board = Image.new("RGB", (columns * tile_w + (columns - 1) * gap, rows * tile_h + (rows - 1) * gap), "#f5f1e8")
    try:
        for index, image in enumerate(images):
            tile = ImageOps.contain(image.convert("RGB"), (tile_w, tile_h), Image.Resampling.LANCZOS)
            try:
                x = (index % columns) * (tile_w + gap) + (tile_w - tile.width) // 2
                y = (index // columns) * (tile_h + gap) + (tile_h - tile.height) // 2
                board.paste(tile, (x, y))
            finally:
                tile.close()
        return _jpeg_data_url_under_limit(board)
    finally:
        board.close()


def _jpeg_data_url_under_limit(image: Image.Image, *, max_bytes: int = 1_600_000) -> str:
    """Encode a high-quality reference, reducing quality only when needed."""
    encoded = b""
    for quality in (88, 82, 76, 70):
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
        encoded = buffer.getvalue()
        if len(encoded) <= max_bytes:
            break
    return "data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii")


def _generate_custom_image(cfg: dict, prompt: str, reference_images: list[str] | None = None) -> str:
    refs = [str(ref).strip() for ref in (reference_images or []) if str(ref).strip()]
    body = {
        "model": cfg["model"],
        "prompt": prompt,
        "size": cfg.get("size") or "1536x1024",
        "n": 1,
        "response_format": "b64_json",
    }
    endpoint = _custom_image_endpoint(cfg.get("endpoint", ""), "edits" if refs else "generations")
    if refs:
        res, data = _post_custom_image_edit(cfg, endpoint, body, refs)
    else:
        res = requests.post(endpoint, headers=_headers(cfg), json=body, timeout=240)
        data = _read_json(res)
    if not res.ok and "response_format" in _error_message(data, "").lower():
        body.pop("response_format", None)
        if refs:
            res, data = _post_custom_image_edit(cfg, endpoint, body, refs)
        else:
            res = requests.post(endpoint, headers=_headers(cfg), json=body, timeout=240)
            data = _read_json(res)
    if not res.ok:
        raise RuntimeError(_error_message(data, f"Custom image request failed ({res.status_code}) at {endpoint}"))

    image_value = _first_image_candidate(data)
    if not image_value:
        task_id = _custom_async_task_id(data)
        if task_id:
            return _poll_custom_task(cfg, task_id)
        request_id = str(data.get("id") or "").strip()
        suffix = f" Response id: {request_id}." if request_id else ""
        raise RuntimeError(
            "Custom image response did not include image data. "
            "If this provider is async, it must return task_id or a queued/running status before LucyLab can poll it."
            f"{suffix}"
        )
    return image_value


def _post_custom_image_edit(cfg: dict, endpoint: str, body: dict, reference_images: list[str]) -> tuple[requests.Response, dict]:
    files, handles = _reference_upload_files(reference_images)
    if not files:
        raise RuntimeError("Custom image edits requires a readable reference image file")
    try:
        res = requests.post(endpoint, headers=_headers(cfg, json_content=False), data=body, files=files, timeout=240)
        return res, _read_json(res)
    finally:
        for handle in handles:
            try:
                handle.close()
            except OSError:
                pass


def _custom_image_endpoint(endpoint: str, kind: str) -> str:
    wanted = "edits" if kind == "edits" else "generations"
    raw = (endpoint or "").strip().rstrip("/")
    if not raw:
        return f"/v1/images/{wanted}"
    return re.sub(r"/images/(generations|edits)$", f"/images/{wanted}", raw, flags=re.IGNORECASE)


def _reference_upload_files(reference_images: list[str]) -> tuple[list[tuple[str, tuple[str, object, str]]], list[object]]:
    files: list[tuple[str, tuple[str, object, str]]] = []
    handles: list[object] = []
    for idx, ref in enumerate(reference_images, start=1):
        upload = _open_reference_upload(ref, idx)
        if not upload:
            continue
        filename, handle, mime = upload
        files.append(("image", (filename, handle, mime)))
        handles.append(handle)
    return files, handles


def _open_reference_upload(ref: str, index: int) -> tuple[str, object, str] | None:
    raw = (ref or "").strip()
    if not raw:
        return None
    if raw.startswith("data:"):
        header, _, payload = raw.partition(",")
        if not payload:
            return None
        mime_match = re.search(r"data:([^;,]+)", header, re.IGNORECASE)
        mime = mime_match.group(1) if mime_match else "image/png"
        handle = BytesIO(base64.b64decode(payload))
        return f"reference_{index}.{_extension_for_mime(mime)}", handle, mime
    if raw.startswith(("http://", "https://")):
        res = requests.get(raw, timeout=180)
        if not res.ok or not res.content:
            return None
        mime = (res.headers.get("content-type") or "image/png").split(";", 1)[0].strip() or "image/png"
        name = Path(urlparse(raw).path).name or f"reference_{index}.{_extension_for_mime(mime)}"
        return name, BytesIO(res.content), mime

    path = Path(raw).expanduser()
    if not path.exists() or not path.is_file():
        return None
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return path.name, path.open("rb"), mime


def _extension_for_mime(mime: str) -> str:
    if mime == "image/jpeg":
        return "jpg"
    if mime == "image/webp":
        return "webp"
    return "png"


def _poll_custom_task(cfg: dict, task_id: str) -> str:
    timeout_at = time.time() + 15 * 60
    succeeded_without_image_at = 0.0
    while time.time() < timeout_at:
        poll_url = f"{cfg['base_url'].rstrip('/')}/images/generations/{task_id}"
        res = requests.get(poll_url, headers=_headers(cfg), timeout=60)
        data = _read_json(res)
        if not res.ok:
            raise RuntimeError(_error_message(data, f"Image task poll failed ({res.status_code})"))
        status = str(data.get("status") or "").lower()
        if status in {"succeeded", "success", "completed"}:
            image_value = _first_image_candidate(data)
            if image_value:
                return image_value
            if not succeeded_without_image_at:
                succeeded_without_image_at = time.time()
            if time.time() - succeeded_without_image_at < 60:
                time.sleep(2)
                continue
            raise RuntimeError("Image task succeeded but returned no image data")
        succeeded_without_image_at = 0.0
        if status in {"failed", "refunded"}:
            raise RuntimeError(_error_message(data, "Image generation failed"))
        time.sleep(3)
    raise RuntimeError("Image task timed out")


def _save_image_value(image_value: str, target: Path, cfg: dict) -> None:
    if image_value.startswith("data:"):
        comma = image_value.find(",")
        if comma <= 0 or not image_value[comma + 1:].strip():
            raise RuntimeError("Invalid data URL returned by image provider")
        target.write_bytes(base64.b64decode(image_value[comma + 1:]))
        _ensure_image_file(target)
        return

    if image_value.startswith(("http://", "https://")):
        url = image_value
    elif image_value.startswith("/"):
        asset_base = (cfg.get("asset_base_url") or cfg.get("base_url") or "").rstrip("/")
        url = f"{asset_base}{image_value}"
    else:
        target.write_bytes(base64.b64decode(image_value))
        _ensure_image_file(target)
        return

    res = requests.get(url, headers=_image_download_headers(url, cfg), timeout=180)
    if res.status_code == 414:
        res = requests.get(url, headers={}, timeout=180)
    if not res.ok:
        raise RuntimeError(
            f"Cannot download generated image ({res.status_code}) "
            f"from {_origin_from_url(url) or 'provider'}; url length {len(url)}. "
            f"{_response_excerpt(res)}"
        )
    if not res.content:
        raise RuntimeError("Image provider returned an empty image download")
    target.write_bytes(res.content)
    _ensure_image_file(target)


def _ensure_image_file(target: Path) -> None:
    if not target.exists() or target.stat().st_size <= 0:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError("Image provider returned an empty image file")


def _normalize_image_endpoint(value: str, endpoint_path: str | None = None) -> tuple[str, str]:
    base = (value or "").strip().rstrip("/")
    if base and not re.match(r"^https?://", base, re.IGNORECASE):
        if base.startswith("/"):
            raise RuntimeError(
                f"Custom image base URL is relative ({base}). "
                "Use a full URL like https://api.example.com/v1."
            )
        scheme = "http" if base.startswith(("127.", "localhost")) else "https"
        base = f"{scheme}://{base}"
    if endpoint_path:
        path = _normalize_endpoint_path(endpoint_path)
        if base.lower().endswith(path.lower()):
            return base[: -len(path)].rstrip("/"), base
        parsed = urlparse(base)
        base_path = parsed.path.rstrip("/")
        join_path = path
        if base_path and path.lower().startswith(f"{base_path.lower()}/"):
            join_path = path[len(base_path):]
        return base, f"{base}{join_path}"
    suffix = "/images/generations"
    if base.endswith(suffix):
        return base[: -len(suffix)], base
    return base, f"{base}{suffix}"


def _normalize_endpoint_path(value: str) -> str:
    path = (value or "").strip()
    if not path:
        return "/v1/images/generations"
    if re.match(r"^https?://", path, re.IGNORECASE):
        parsed = urlparse(path)
        path = parsed.path or "/v1/images/generations"
    if not path.startswith("/"):
        path = f"/{path}"
    return path.rstrip("/") or "/v1/images/generations"


def _origin_from_url(value: str) -> str:
    parsed = urlparse(value or "")
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _resolve_key(value: str, key_path: str) -> str:
    key = (value or "").strip()
    if key:
        return key
    if not key_path:
        return ""
    try:
        return Path(key_path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _headers(cfg: dict, json_content: bool = True) -> dict:
    headers = {}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    if json_content:
        headers["Content-Type"] = "application/json"
    return headers


def _image_download_headers(url: str, cfg: dict) -> dict:
    provider_origin = _origin_from_url(cfg.get("base_url") or cfg.get("endpoint") or "")
    asset_origin = _origin_from_url(cfg.get("asset_base_url") or "")
    url_origin = _origin_from_url(url)
    if url_origin and url_origin not in {provider_origin, asset_origin}:
        return {}
    return _headers(cfg, json_content=False)


def _response_excerpt(res: requests.Response) -> str:
    content_type = res.headers.get("content-type", "")
    if content_type.startswith("image/"):
        return ""
    text = (res.text or "").strip()
    if not text:
        return ""
    if "<html" in text.lower() or "<!doctype html" in text.lower():
        return _compact_provider_error(text, "", res.status_code)
    return re.sub(r"\s+", " ", text)[:240]


def _read_json(res: requests.Response) -> dict:
    try:
        return res.json()
    except ValueError:
        return {
            "raw": res.text,
            "status_code": getattr(res, "status_code", None),
            "content_type": res.headers.get("content-type", ""),
        }


def _error_message(data: dict, fallback: str) -> str:
    err = data.get("error")
    if isinstance(err, dict) and err.get("message"):
        return str(err["message"])
    if isinstance(err, str) and err:
        return err
    if data.get("raw"):
        return _compact_provider_error(str(data["raw"]), fallback, data.get("status_code"))
    return fallback


def _compact_provider_error(raw: str, fallback: str, status_code=None) -> str:
    text = raw or ""
    lower = text.lower()
    if "<html" in lower or "<!doctype html" in lower:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
        title = _strip_html(title_match.group(1)) if title_match else ""
        if "524" in lower or "a timeout occurred" in lower:
            return (
                f"{fallback}. Cloudflare 524 timeout from image provider"
                f"{f' ({title})' if title else ''}. The provider accepted the request but its origin did not answer in time."
            )
        if title:
            return f"{fallback}. Provider returned HTML instead of JSON: {title}"
        return f"{fallback}. Provider returned HTML instead of JSON."
    clean = re.sub(r"\s+", " ", text).strip()
    if status_code and str(status_code) not in fallback:
        clean = f"HTTP {status_code}: {clean}"
    if len(clean) > 700:
        return clean[:700].rstrip() + "..."
    return clean or fallback


def _strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value or "")).strip()


def _custom_async_task_id(data: dict) -> str:
    for key in ("task_id", "taskId", "job_id", "jobId", "generation_id", "generationId"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    status = str(data.get("status") or data.get("state") or "").lower()
    if status in {"queued", "pending", "running", "processing", "generating", "in_progress"}:
        value = data.get("id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _first_image_candidate(data: dict) -> str:
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    rows = result.get("data") if isinstance(result.get("data"), list) else data.get("data")
    if not isinstance(rows, list):
        rows = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate = _image_candidate_from_mapping(row)
        if candidate:
            return candidate

    candidate = _image_candidate_from_mapping(data)
    if candidate:
        return candidate
    if result:
        candidate = _image_candidate_from_mapping(result)
        if candidate:
            return candidate

    for key in ("output", "outputs", "images", "result"):
        candidate = _image_candidate_from_value(data.get(key))
        if candidate:
            return candidate
    return ""


def _image_candidate_from_mapping(data: dict) -> str:
    image_keys = (
        "url",
        "image_url",
        "imageUrl",
        "b64_json",
        "base64",
        "image",
        "data_url",
        "download_url",
        "downloadUrl",
        "output_url",
        "outputUrl",
        "file_url",
        "fileUrl",
    )
    for key in image_keys:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        allow_plain_base64 = key in {"b64_json", "base64", "image", "data_url"}
        candidate = _image_candidate_from_string(value, allow_plain_base64=allow_plain_base64)
        if candidate:
            return candidate
    return ""


def _image_candidate_from_value(value) -> str:
    if isinstance(value, dict):
        candidate = _image_candidate_from_mapping(value)
        if candidate:
            return candidate
        for nested in value.values():
            candidate = _image_candidate_from_value(nested)
            if candidate:
                return candidate
    if isinstance(value, list):
        for item in value:
            candidate = _image_candidate_from_value(item)
            if candidate:
                return candidate
    return ""


def _image_candidate_from_string(value: str, *, allow_plain_base64: bool = False) -> str:
    raw = value.strip()
    if raw.startswith("data:"):
        comma = raw.find(",")
        if comma <= 0 or not raw[comma + 1:].strip():
            return ""
        return raw
    data_url = _base64_image_data_url(raw)
    if data_url:
        return data_url
    if _looks_like_generation_poll_url(raw):
        return ""
    if raw.startswith(("http://", "https://", "/")):
        return raw
    if allow_plain_base64:
        return f"data:image/png;base64,{raw}"
    return ""


def _base64_image_data_url(value: str) -> str:
    compact = re.sub(r"\s+", "", value or "")
    if len(compact) < 100:
        return ""
    mime = ""
    if compact.startswith("/9j/"):
        mime = "image/jpeg"
    elif compact.startswith("iVBOR"):
        mime = "image/png"
    elif compact.startswith("UklGR"):
        mime = "image/webp"
    elif compact.startswith("R0lGOD"):
        mime = "image/gif"
    if not mime:
        return ""
    return f"data:{mime};base64,{compact}"


def _looks_like_generation_poll_url(value: str) -> bool:
    path = urlparse(value).path if value.startswith(("http://", "https://")) else value
    return bool(re.search(r"/images/generations/[^/?#]+/?$", path or "", re.IGNORECASE))


def config_for_public_response(cfg: dict | None = None) -> dict:
    provider_cfg = image_provider_config(cfg)
    return {
        "provider": provider_cfg["provider"],
        "base_url": provider_cfg["base_url"],
        "model": provider_cfg["model"],
        "size": provider_cfg.get("size", ""),
        "has_api_key": bool(provider_cfg.get("api_key")),
    }
