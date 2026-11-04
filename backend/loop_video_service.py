"""Per-project assets for the single-video loop workflow."""
import json
import random
import re
import time
import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from image_generation_service import generate_image_events, image_provider_config
from scene_image_service import _call_director_llm
from tts_service import PROJECT_TITLE_VALIDATOR, get_project_dir, load_config
from video_service import get_video_duration


router = APIRouter()

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
SAFE_PROJECT = PROJECT_TITLE_VALIDATOR
SAFE_FILE = re.compile(r"^[\w.-]+$", re.ASCII)

DEFAULT_LOOP_STYLE = (
    "A peaceful old Vietnamese mountain town at blue hour, a cozy wooden tea shop with warm lantern light, "
    "stone-paved alley, traditional tiled-roof houses, distant mountains, warm orange and cool blue cinematic lighting, "
    "nostalgic lo-fi atmosphere, detailed anime background art, semi-realistic hand-painted digital illustration, "
    "wide establishing shot, 16:9."
)

RANDOM_SUBJECTS = [
    "a rain-washed hillside alley with a small tea stall and glowing paper lanterns",
    "an old tiled-roof riverside market at twilight, still water reflecting lantern light",
    "a quiet railway platform in a misty mountain town after a gentle rain",
    "a mossy stone bridge above a narrow stream beside old wooden houses",
    "a moonlit courtyard with a flowering tree, a ceramic water jar, and warm windows",
    "a foggy mountain pass with a roadside shrine and a single warm lantern",
    "an empty lakeside pier with distant town lights and a calm blue evening sky",
    "a quiet old bookshop facade with potted plants, rain puddles, and warm indoor light",
]


class LoopImageGenerateRequest(BaseModel):
    project: str
    style: str = DEFAULT_LOOP_STYLE
    subject: str = ""
    mode: str = "random"


class LoopPromptRequest(BaseModel):
    project: str
    filename: str


def _project_dir(project: str) -> Path:
    if not project or not SAFE_PROJECT.match(project):
        raise HTTPException(400, "Invalid project name")
    path = get_project_dir(project)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _root(project: str) -> Path:
    root = _project_dir(project) / "loop_assets"
    (root / "videos").mkdir(parents=True, exist_ok=True)
    (root / "images").mkdir(parents=True, exist_ok=True)
    return root


def _manifest_path(project: str) -> Path:
    return _root(project) / "assets.json"


def _load_manifest(project: str) -> dict:
    path = _manifest_path(project)
    if not path.exists():
        return {"project": project, "images": [], "updated_at": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("project", project)
            data.setdefault("images", [])
            return data
    except (OSError, ValueError):
        pass
    return {"project": project, "images": [], "updated_at": ""}


def _save_manifest(project: str, data: dict) -> dict:
    data["project"] = project
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    path = _manifest_path(project)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return data


def _safe_filename(filename: str, allowed: set[str]) -> str:
    name = Path(filename or "").name
    if not SAFE_FILE.match(name) or Path(name).suffix.lower() not in allowed:
        raise HTTPException(400, "Invalid asset filename")
    return name


def _image_record(project: str, filename: str) -> dict:
    manifest = _load_manifest(project)
    return next((item for item in manifest.get("images", []) if item.get("filename") == filename), {})


def _loop_provider_config() -> dict:
    provider_cfg = dict(image_provider_config(load_config()))
    # gpt2api exposes image quality as a resolution tier. Loop sources are kept
    # at 16:9 2K so the user can generate a clean motion clip from the download.
    if provider_cfg.get("provider") == "gpt2api":
        provider_cfg["resolution"] = "2K"
        provider_cfg["size"] = "1792x1024"
    return provider_cfg


def _image_prompt(style: str, subject: str) -> str:
    return (
        "Create one premium 16:9 environmental background image intended as the source for a calm seamless looping video.\n\n"
        f"STYLE LOCK (preserve this visual language):\n{style.strip()}\n\n"
        f"SCENE SUBJECT: {subject.strip()}\n\n"
        "Composition requirements:\n"
        "- Wide establishing shot, clean readable depth, detailed foreground and distant atmosphere.\n"
        "- A peaceful atmospheric setting with natural light, gentle weather, and visual areas suitable for subtle motion.\n"
        "- No people, no human figures, no faces, no hands, no human silhouettes, and no human-shaped shadows.\n"
        "- Allowed subjects are scenery, architecture, objects, plants, water, weather, and animals only.\n"
        "- No text, letters, captions, logos, UI, watermark, or border.\n"
        "- Preserve 16:9 framing. Create a high-detail polished final image."
    )


@router.get("/api/loop-assets/{project}")
def list_loop_assets(project: str):
    root = _root(project)
    cfg = load_config()
    ffprobe = cfg.get("ffprobe_path", "ffprobe")
    videos = []
    for path in sorted((root / "videos").iterdir()):
        if path.is_file() and path.suffix.lower() in VIDEO_EXTS:
            videos.append({
                "filename": path.name,
                "duration_sec": get_video_duration(path, ffprobe),
                "size_mb": round(path.stat().st_size / (1024 * 1024), 2),
            })
    manifest = _load_manifest(project)
    images = [item for item in manifest.get("images", []) if (root / "images" / str(item.get("filename") or "")).is_file()]
    provider_cfg = _loop_provider_config()
    return {
        "videos": videos,
        "images": images,
        "provider": {
            "provider": provider_cfg.get("provider"),
            "base_url": provider_cfg.get("base_url"),
            "model": provider_cfg.get("model"),
            "size": provider_cfg.get("size"),
            "resolution": provider_cfg.get("resolution"),
            "has_api_key": bool(provider_cfg.get("api_key")),
        },
    }


@router.post("/api/loop-assets/upload/{project}")
async def upload_loop_video(project: str, file: UploadFile = File(...)):
    root = _root(project)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in VIDEO_EXTS:
        raise HTTPException(400, "Only MP4, MOV, MKV, AVI, and WEBM loop videos are supported")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(file.filename or "loop").stem).strip("_") or "loop"
    filename = f"{int(time.time())}_{stem}{ext}"
    target = root / "videos" / filename
    async with aiofiles.open(target, "wb") as out:
        await out.write(await file.read())
    duration = get_video_duration(target, load_config().get("ffprobe_path", "ffprobe"))
    if duration <= 0:
        target.unlink(missing_ok=True)
        raise HTTPException(400, "Cannot read loop video duration")
    return {"filename": filename, "duration_sec": duration, "size_mb": round(target.stat().st_size / (1024 * 1024), 2)}


@router.get("/api/loop-assets/video/{project}/{filename}")
def serve_loop_video(project: str, filename: str, request: Request):
    path = _root(project) / "videos" / _safe_filename(filename, VIDEO_EXTS)
    if not path.is_file():
        raise HTTPException(404, "Loop video not found")
    return FileResponse(str(path), media_type="video/mp4", headers={"Content-Disposition": f'inline; filename="{path.name}"', "Accept-Ranges": "bytes"})


@router.get("/api/loop-assets/image/{project}/{filename}")
def serve_loop_image(project: str, filename: str):
    path = _root(project) / "images" / _safe_filename(filename, IMAGE_EXTS)
    if not path.is_file():
        raise HTTPException(404, "Loop image not found")
    return FileResponse(str(path), media_type="image/png", headers={"Content-Disposition": f'inline; filename="{path.name}"'})


@router.delete("/api/loop-assets/{project}/video/{filename}")
def delete_loop_video(project: str, filename: str):
    path = _root(project) / "videos" / _safe_filename(filename, VIDEO_EXTS)
    if not path.exists():
        raise HTTPException(404, "Loop video not found")
    path.unlink()
    return {"ok": True}


@router.delete("/api/loop-assets/{project}/image/{filename}")
def delete_loop_image(project: str, filename: str):
    name = _safe_filename(filename, IMAGE_EXTS)
    path = _root(project) / "images" / name
    if not path.exists():
        raise HTTPException(404, "Loop image not found")
    path.unlink()
    manifest = _load_manifest(project)
    manifest["images"] = [item for item in manifest.get("images", []) if item.get("filename") != name]
    _save_manifest(project, manifest)
    return {"ok": True}


@router.post("/api/loop-assets/generate")
def generate_loop_image(body: LoopImageGenerateRequest):
    root = _root(body.project)
    style = (body.style or DEFAULT_LOOP_STYLE).strip()
    if not style:
        raise HTTPException(400, "Loop style is required")
    mode = (body.mode or "random").strip().lower()
    subject = (body.subject or "").strip()
    if mode == "random":
        subject = random.choice(RANDOM_SUBJECTS)
    if not subject:
        raise HTTPException(400, "Describe the landscape or object to generate")
    filename = f"loop_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.png"
    target = root / "images" / filename
    prompt = _image_prompt(style, subject)
    provider_cfg = _loop_provider_config()

    def stream():
        try:
            yield f"data: {json.dumps({'type': 'start', 'subject': subject, 'prompt': prompt}, ensure_ascii=False)}\n\n"
            for event in generate_image_events(prompt, target, provider_cfg):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            item = {
                "filename": filename,
                "subject": subject,
                "style": style,
                "image_prompt": prompt,
                "loop_prompt": "",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "width": 1792 if provider_cfg.get("provider") == "gpt2api" else 0,
                "height": 1024 if provider_cfg.get("provider") == "gpt2api" else 0,
                "provider": provider_cfg.get("provider"),
                "model": provider_cfg.get("model"),
                "resolution": provider_cfg.get("resolution") or provider_cfg.get("size"),
            }
            manifest = _load_manifest(body.project)
            manifest["images"] = [item, *[row for row in manifest.get("images", []) if row.get("filename") != filename]]
            _save_manifest(body.project, manifest)
            yield f"data: {json.dumps({'type': 'done', 'image': item}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            target.unlink(missing_ok=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/loop-assets/loop-prompt")
def generate_loop_prompt(body: LoopPromptRequest):
    filename = _safe_filename(body.filename, IMAGE_EXTS)
    item = _image_record(body.project, filename)
    if not item:
        raise HTTPException(404, "Loop image metadata not found")
    system = (
        "You write image-to-video prompts. Return ONLY one English prompt, no markdown and no preface. "
        "It must request a perfectly seamless 8-10 second 24fps loop with locked composition, no cuts, no new objects, "
        "no people, and only calm subtle environmental movement."
    )
    user = (
        "Write a production-ready loop animation prompt for this generated still. Keep its exact composition, architecture, "
        "lighting, colors, and painterly style unchanged.\n\n"
        f"Original scene subject: {item.get('subject', '')}\n"
        f"Original style: {item.get('style', '')}\n"
        "Include: a clear 'Animate this image into a perfectly seamless looping video' opening; 4-7 subtle movements suited "
        "to the scene; locked/static camera or push-in under 2%; strict final-frame-equals-first-frame requirements; 8-10 seconds; 24 fps."
    )
    try:
        prompt = _call_director_llm(system, user, max_tokens=1000).strip()
    except Exception as exc:
        raise HTTPException(502, f"Loop prompt LLM failed: {exc}")
    if not prompt:
        raise HTTPException(502, "Loop prompt LLM returned an empty response")
    manifest = _load_manifest(body.project)
    for row in manifest.get("images", []):
        if row.get("filename") == filename:
            row["loop_prompt"] = prompt
            row["loop_prompt_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            item = row
            break
    _save_manifest(body.project, manifest)
    return {"image": item, "loop_prompt": prompt}
