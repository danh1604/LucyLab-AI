"""FastAPI backend for LucyLab TTS UI"""
import json
import re
import shutil
import asyncio
import aiofiles
import uuid
import math
import subprocess
from urllib.parse import quote
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from tts_service import (
    load_config,
    save_config,
    channel_profile_block,
    split_text,
    split_text_for_timing,
    split_text_for_tts_timing,
    strip_chapter_headings_for_tts,
    process_text_streaming,
    list_outputs,
    list_trash,
    list_projects,
    get_project_dir,
    BASE_DIR,
    OUTPUT_DIR,
    TRASH_DIR,
    TEMP_DIR,
    PROJECT_TITLE_VALIDATOR,
    project_uses_managed_storage,
    write_project_title,
)
from tts_providers import SynthesisRequest, TTSProviderError, TTS_PROVIDERS, resolve_tts_profile
from story_service import (
    analyze_prompt,
    interview_turn,
    stream_interview,
    stream_story,
    get_ollama_models,
    get_codex_models,
    get_opencode_models,
    requires_anthropic_key,
    ANTHROPIC_MODELS,
    generate_text,
    generate_text_openai_compatible,
)
from video_service import get_video_duration, compose_video, compose_loop_video, _encoder_args, _parse_ffmpeg_time
from subtitle_service import build_subtitle_cues, load_timing_file, render_subtitle_videos, subtitle_cues_to_srt
from youtube_publish_service import router as youtube_publish_router
from scene_image_service import router as scene_image_router
from thumbnail_service import router as thumbnail_router
from thumbnail_director_service import router as thumbnail_director_router
from script_review_service import router as script_review_router
from shorts_campaign_service import router as shorts_campaign_router
from loop_video_service import router as loop_video_router
from language_service import router as language_router
from stick_figure_packaging_service import router as stick_figure_packaging_router
from gpt2api_admin_service import (
    delete_gpt_account,
    disable_gpt_account,
    enable_gpt_account,
    get_codex_login_session_status,
    import_gpt_accounts,
    import_local_codex_account,
    list_gpt_accounts,
    probe_gpt_account,
    select_gpt_account,
    start_codex_login_session,
)
from gpt2api_runtime_service import start_gpt2api

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}
_SAFE_VIDEO = re.compile(r'^[\w\- ]+\.(mp4|mov|mkv|avi|webm)$', re.IGNORECASE)

app = FastAPI(title="LucyLab TTS API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(youtube_publish_router)
app.include_router(scene_image_router)
app.include_router(thumbnail_router)
app.include_router(thumbnail_director_router)
app.include_router(script_review_router)
app.include_router(shorts_campaign_router)
app.include_router(loop_video_router)
app.include_router(language_router)
app.include_router(stick_figure_packaging_router)

_SAFE_NAME = PROJECT_TITLE_VALIDATOR
_SAFE_FILE = re.compile(r'^[\w\- ]+\.mp3$')
STORY_SESSIONS_DIR = OUTPUT_DIR / "_story_sessions"
VIDEO_CUTTER_DIR = OUTPUT_DIR / "_video_cutter"
VIDEO_CUTTER_UPLOADS = VIDEO_CUTTER_DIR / "uploads"
VIDEO_CUTTER_OUTPUTS = VIDEO_CUTTER_DIR / "outputs"
AUDIO_CUTTER_DIR = OUTPUT_DIR / "_audio_cutter"
AUDIO_CUTTER_UPLOADS = AUDIO_CUTTER_DIR / "uploads"
AUDIO_CUTTER_OUTPUTS = AUDIO_CUTTER_DIR / "outputs"
SHARED_DIR = OUTPUT_DIR / "_shared"
OUTRO_DIR = SHARED_DIR / "outros"


class ConfigUpdate(BaseModel):
    jwt_token: str | None = None
    voice_id: str | None = None
    voice_presets: list[dict] | None = None
    speed: float | None = None
    max_chars: int | None = None
    workers: int | None = None
    delay: float | None = None
    sentence_gap: float | None = None
    optimize_text: bool | None = None
    trim_silence: bool | None = None
    silence_db: float | None = None
    anthropic_api_key: str | None = None
    default_story_model: str | None = None
    youtube_metadata_provider: str | None = None
    youtube_metadata_base_url: str | None = None
    youtube_metadata_path: str | None = None
    youtube_metadata_model: str | None = None
    youtube_metadata_api_key: str | None = None
    youtube_metadata_api_key_path: str | None = None
    safe_prompt_model: str | None = None
    safe_prompt_provider: str | None = None
    safe_prompt_base_url: str | None = None
    safe_prompt_path: str | None = None
    safe_prompt_api_key: str | None = None
    safe_prompt_api_key_path: str | None = None
    script_review_model: str | None = None
    script_review_provider: str | None = None
    script_review_base_url: str | None = None
    script_review_path: str | None = None
    script_review_api_key: str | None = None
    script_review_api_key_path: str | None = None
    script_review_max_comments_per_agent: int | None = None
    script_review_min_severity: str | None = None
    visual_director_model: str | None = None
    visual_director_provider: str | None = None
    visual_director_base_url: str | None = None
    visual_director_path: str | None = None
    visual_director_api_key: str | None = None
    visual_director_api_key_path: str | None = None
    stick_figure_model: str | None = None
    stick_figure_provider: str | None = None
    stick_figure_base_url: str | None = None
    stick_figure_path: str | None = None
    stick_figure_api_key: str | None = None
    stick_figure_api_key_path: str | None = None
    thumbnail_vision_base_url: str | None = None
    thumbnail_vision_path: str | None = None
    thumbnail_vision_model: str | None = None
    thumbnail_vision_api_key: str | None = None
    thumbnail_vision_api_key_path: str | None = None
    ffmpeg_path: str | None = None
    ffprobe_path: str | None = None
    ollama_url: str | None = None
    outro_video_path: str | None = None
    youtube_client_secrets_path: str | None = None
    youtube_timezone: str | None = None
    youtube_long_slots: list[str] | None = None
    youtube_short_slots: list[str] | None = None
    youtube_min_gap_hours: float | None = None
    youtube_max_long_per_day: int | None = None
    youtube_max_shorts_per_day: int | None = None
    youtube_campaign_mode: bool | None = None
    youtube_campaign_long_slots: list[str] | None = None
    youtube_campaign_short_slots: list[str] | None = None
    gpt2api_base_url: str | None = None
    gpt2api_image_model: str | None = None
    image_generation_provider: str | None = None
    scene_gpt2api_base_url: str | None = None
    scene_gpt2api_api_key: str | None = None
    scene_gpt2api_api_key_path: str | None = None
    scene_gpt2api_asset_base_url: str | None = None
    scene_gpt2api_image_model: str | None = None
    scene_gpt2api_image_resolution: str | None = None
    scene_gpt2api_image_size: str | None = None
    gpt2api_autostart: bool | None = None
    gpt2api_start_script: str | None = None
    gpt2api_startup_wait_sec: int | None = None
    gpt2api_admin_base_url: str | None = None
    gpt2api_admin_user: str | None = None
    gpt2api_admin_password: str | None = None
    gpt2api_codex_auth_path: str | None = None
    gpt2api_codex_client_id: str | None = None
    gpt2api_account_name_prefix: str | None = None
    custom_image_base_url: str | None = None
    custom_image_path: str | None = None
    custom_image_api_key: str | None = None
    custom_image_api_key_path: str | None = None
    custom_image_model: str | None = None
    custom_image_size: str | None = None
    thumbnail_image_size: str | None = None
    realesrgan_path: str | None = None
    channel_positioning: str | None = None
    channel_audience: str | None = None
    channel_pillars: str | None = None
    channel_banned_title_patterns: str | None = None
    channel_banned_hashtags: str | None = None
    language_agent_provider: str | None = None
    language_agent_base_url: str | None = None
    language_agent_path: str | None = None
    language_agent_model: str | None = None
    language_agent_api_key: str | None = None
    language_agent_api_key_path: str | None = None
    tts_language_profiles: dict | None = None
    tts_provider_settings: dict | None = None


class GenerateRequest(BaseModel):
    text: str
    output_name: str | None = None
    project: str = ""
    locale: str = ""


class TTSProviderTestRequest(BaseModel):
    locale: str = "vi-VN"
    profile: dict | None = None


class GptAccountImportRequest(BaseModel):
    text: str = ""


class StorySessionUpdate(BaseModel):
    title: str | None = None
    story_title: str | None = None
    stage: str | None = None
    story_data: dict | None = None
    approved_story: str | None = None
    current_project: str | None = None
    audio_file: str | None = None
    audio_duration: float | None = None
    video_order: list[str] | None = None
    video_settings: dict | None = None
    output_video_file: str | None = None
    subtitle_settings: dict | None = None
    subtitle_outputs: dict | None = None
    subtitle_timing_file: str | None = None


class VideoCutRequest(BaseModel):
    upload_id: str
    base_name: str
    minutes: int = 0
    seconds: int = 0
    output_dir: str | None = None


class VideoCutProjectRequest(BaseModel):
    project: str
    filename: str


class AudioCutRequest(BaseModel):
    upload_id: str
    start_sec: float = 0
    end_sec: float = 0
    output_name: str = "audio_cut"
    output_format: str = "mp3"


class AppendOutroRequest(BaseModel):
    project: str
    input_video: str
    outro_path: str = ""
    output_name: str = "with_outro.mp4"
    output_dir: str = ""
    overwrite_mode: str = "version"
    video_encoder: str = "nvidia_nvenc"
    quality: str = "high"


class SubtitleRenderRequest(BaseModel):
    project: str
    input_video: str
    audio_file: str
    story_text: str = ""
    style: dict = {}
    video_encoder: str = "nvidia_nvenc"
    quality: str = "high"
    overwrite_mode: str = "version"
    background_music: str = ""
    background_music_volume: float = 0.15
    render_modes: list[str] | None = None
    vertical_video_mode: str = "crop"
    burn_subtitles: bool = True


def _story_session_path(session_id: str) -> Path:
    if not re.match(r"^[a-f0-9\-]{36}$", session_id):
        raise HTTPException(400, "Invalid session id")
    return STORY_SESSIONS_DIR / f"{session_id}.json"


def _load_story_session(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_story_session(data: dict) -> dict:
    STORY_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    path = _story_session_path(data["id"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def _dir_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            pass
    return total


def _format_size(num_bytes: int) -> str:
    value = float(max(0, num_bytes))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def _space_key(path: Path) -> str:
    resolved = path.resolve()
    return (resolved.anchor or str(resolved)).lower()


def _check_free_space(requirements: list[tuple[Path, int]]) -> str:
    by_volume: dict[str, dict] = {}
    for path, bytes_needed in requirements:
        path.mkdir(parents=True, exist_ok=True)
        key = _space_key(path)
        entry = by_volume.setdefault(key, {"path": path, "needed": 0})
        entry["needed"] += int(max(0, bytes_needed))

    for entry in by_volume.values():
        path = entry["path"]
        needed = entry["needed"]
        free = shutil.disk_usage(path).free
        if free < needed:
            return (
                f"Không đủ dung lượng ổ đĩa cho bước này. "
                f"Cần khoảng {_format_size(needed)}, hiện còn {_format_size(free)} tại {path}."
            )
    return ""


def _delete_project_dir(project: str) -> dict:
    if not project or not _SAFE_NAME.match(project):
        raise HTTPException(400, "Invalid project name")
    path = get_project_dir(project)
    if not path.exists() or not path.is_dir():
        raise HTTPException(404, "Project not found")
    try:
        path.resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError:
        raise HTTPException(403, "Access denied")
    size_bytes = _dir_size_bytes(path)
    shutil.rmtree(path)
    return {
        "ok": True,
        "project": project,
        "deleted_bytes": size_bytes,
        "deleted_gb": round(size_bytes / (1024 ** 3), 2),
    }


def _delete_story_sessions_for_project(project: str) -> int:
    if not project:
        return 0
    deleted = 0
    STORY_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    for path in STORY_SESSIONS_DIR.glob("*.json"):
        try:
            data = _load_story_session(path)
            if data.get("current_project") == project:
                path.unlink()
                deleted += 1
        except Exception:
            continue
    return deleted


def _story_sessions_for_project(project: str) -> list[tuple[Path, dict]]:
    """Return every session currently claiming a project folder."""
    if not project:
        return []
    STORY_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    matches: list[tuple[Path, dict]] = []
    for path in STORY_SESSIONS_DIR.glob("*.json"):
        try:
            data = _load_story_session(path)
        except Exception:
            continue
        if data.get("current_project") == project:
            matches.append((path, data))
    return matches


def _require_exclusive_project_session(project: str, session_id: str = "") -> list[tuple[Path, dict]]:
    matches = _story_sessions_for_project(project)
    if session_id:
        shared = any(data.get("id") != session_id for _, data in matches)
    else:
        shared = len(matches) > 1
    if shared:
        raise HTTPException(
            409,
            "Project folder is linked to multiple story sessions. Deletion was blocked to protect the other story; create or restore a separate project folder first.",
        )
    return matches


def _isolated_project_name(data: dict, source_project: str) -> str:
    base = str(data.get("story_title") or data.get("title") or "").strip()
    if not base or not _SAFE_NAME.match(base):
        base = f"{source_project} - {str(data.get('id') or '')[:8]}"
    candidate = base
    suffix = 2
    while (OUTPUT_DIR / candidate).exists() or _story_sessions_for_project(candidate):
        candidate = f"{base} - {suffix}"
        suffix += 1
    return candidate


def _copy_story_session_media(source: Path, target: Path, data: dict) -> list[str]:
    """Copy only media explicitly owned by a session, never a whole project."""
    target.mkdir(parents=True, exist_ok=True)
    names: set[str] = set()

    def add_file(value) -> None:
        token = str(value or "").strip()
        if token:
            names.add(Path(token).name)

    add_file(data.get("audio_file"))
    add_file(data.get("subtitle_timing_file"))
    add_file(data.get("output_video_file"))
    for item in data.get("video_order") or []:
        add_file(item)

    def collect_output_names(value) -> None:
        if isinstance(value, dict):
            for child in value.values():
                collect_output_names(child)
        elif isinstance(value, list):
            for child in value:
                collect_output_names(child)
        elif isinstance(value, str):
            add_file(value)

    collect_output_names(data.get("subtitle_outputs") or {})
    audio_name = Path(str(data.get("audio_file") or "")).name
    if audio_name:
        names.add(f"{Path(audio_name).stem}_timing.json")

    copied: list[str] = []
    for name in sorted(names):
        source_file = source / name
        if source_file.is_file():
            shutil.copy2(source_file, target / name)
            copied.append(name)

    # Manual thumbnail history records the session id, so it can be copied
    # without bringing unrelated thumbnails from the shared legacy folder.
    history_path = source / "thumbnails" / "manual" / "history.json"
    try:
        history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
    except Exception:
        history = []
    owned_thumbnails = [item for item in history if isinstance(item, dict) and item.get("session_id") == data.get("id")]
    if owned_thumbnails:
        thumbnail_target = target / "thumbnails" / "manual"
        thumbnail_target.mkdir(parents=True, exist_ok=True)
        for item in owned_thumbnails:
            filename = Path(str(item.get("filename") or "")).name
            source_file = history_path.parent / filename
            if filename and source_file.is_file():
                shutil.copy2(source_file, thumbnail_target / filename)
                copied.append(f"thumbnails/manual/{filename}")
        (thumbnail_target / "history.json").write_text(json.dumps(owned_thumbnails, ensure_ascii=False, indent=2), encoding="utf-8")
    return copied


def _repair_story_session_project_isolation(path: Path, data: dict) -> dict:
    """Give a legacy session its own project without cloning unrelated media."""
    source_project = str(data.get("current_project") or "")
    if not source_project:
        raise HTTPException(400, "Session does not have a project folder")
    matches = _story_sessions_for_project(source_project)
    if len(matches) < 2:
        return data

    # Keep the oldest session on the existing folder. Later sessions were
    # created while the old project state leaked into a new story.
    owner_path, owner = min(matches, key=lambda item: (item[1].get("created_at") or "", item[1].get("id") or ""))
    if owner.get("id") == data.get("id"):
        raise HTTPException(409, "This is the original project owner. Open the other shared session to repair it first.")

    source = OUTPUT_DIR / source_project
    if not source.exists() or not source.is_dir():
        raise HTTPException(404, "Shared project folder was not found")
    target_project = _isolated_project_name(data, source_project)
    target = OUTPUT_DIR / target_project
    try:
        copied = _copy_story_session_media(source, target, data)
    except Exception as exc:
        raise HTTPException(500, f"Could not copy session media safely: {exc}") from exc

    data["current_project"] = target_project
    data["project_isolation_repaired_at"] = datetime.now().isoformat(timespec="seconds")
    data["project_isolation_copied_files"] = copied
    _save_story_session(data)
    return data


# ── Config ────────────────────────────────────────────────────────────────────

_CONFIG_DEFAULTS = {
    "voice_presets": [],
    "anthropic_api_key": "",
    "default_story_model": "claude-sonnet-4-6",
    "youtube_metadata_provider": "default",
    "youtube_metadata_base_url": "https://api.openai.com",
    "youtube_metadata_path": "/v1/chat/completions",
    "youtube_metadata_model": "",
    "youtube_metadata_api_key": "",
    "youtube_metadata_api_key_path": "",
    "safe_prompt_model": "",
    "safe_prompt_provider": "default",
    "safe_prompt_base_url": "https://api.openai.com",
    "safe_prompt_path": "/v1/chat/completions",
    "safe_prompt_api_key": "",
    "safe_prompt_api_key_path": "",
    "script_review_model": "",
    "script_review_provider": "default",
    "script_review_base_url": "https://api.openai.com",
    "script_review_path": "/v1/chat/completions",
    "script_review_api_key": "",
    "script_review_api_key_path": "",
    "script_review_max_comments_per_agent": 12,
    "script_review_min_severity": "important",
    "visual_director_model": "",
    "visual_director_provider": "default",
    "visual_director_base_url": "https://api.openai.com",
    "visual_director_path": "/v1/chat/completions",
    "visual_director_api_key": "",
    "visual_director_api_key_path": "",
    "stick_figure_model": "",
    "stick_figure_provider": "default",
    "stick_figure_base_url": "https://api.openai.com",
    "stick_figure_path": "/v1/chat/completions",
    "stick_figure_api_key": "",
    "stick_figure_api_key_path": "",
    "thumbnail_vision_base_url": "https://api.openai.com",
    "thumbnail_vision_path": "/v1/chat/completions",
    "thumbnail_vision_model": "",
    "thumbnail_vision_api_key": "",
    "thumbnail_vision_api_key_path": "",
    "ffmpeg_path": "ffmpeg",
    "ffprobe_path": "ffprobe",
    "ollama_url": "http://localhost:11434",
    "outro_video_path": "",
    "youtube_client_secrets_path": "",
    "youtube_timezone": "Asia/Ho_Chi_Minh",
    "youtube_long_slots": ["20:00"],
    "youtube_short_slots": ["12:00", "21:30"],
    "youtube_min_gap_hours": 6,
    "youtube_max_long_per_day": 1,
    "youtube_max_shorts_per_day": 2,
    "youtube_campaign_mode": False,
    "youtube_campaign_long_slots": ["20:00"],
    "youtube_campaign_short_slots": ["12:00", "20:00"],
    "gpt2api_base_url": "http://127.0.0.1:17200/v1/images/generations",
    "gpt2api_image_model": "",
    "image_generation_provider": "gpt2api",
    "scene_gpt2api_base_url": "http://127.0.0.1:17200/v1",
    "scene_gpt2api_api_key": "",
    "scene_gpt2api_api_key_path": str(BASE_DIR / "gpt2api" / ".local" / "openai-compatible-api-key.txt"),
    "scene_gpt2api_asset_base_url": "http://127.0.0.1:17080",
    "scene_gpt2api_image_model": "gpt-image-2",
    "scene_gpt2api_image_resolution": "1K",
    "scene_gpt2api_image_size": "1792x1024",
    "gpt2api_autostart": True,
    "gpt2api_start_script": "",
    "gpt2api_startup_wait_sec": 240,
    "gpt2api_admin_base_url": "http://127.0.0.1:17188",
    "gpt2api_admin_user": "admin",
    "gpt2api_admin_password": "admin123",
    "gpt2api_codex_auth_path": str(Path.home() / ".codex" / "auth.json"),
    "gpt2api_codex_client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
    "gpt2api_account_name_prefix": "lucylab-codex",
    "custom_image_base_url": "https://api.openai.com",
    "custom_image_path": "/v1/images/edits",
    "custom_image_api_key": "",
    "custom_image_api_key_path": "",
    "custom_image_model": "gpt-image-1",
    "custom_image_size": "1536x1024",
    "thumbnail_image_size": "1792x1024",
    "realesrgan_path": str(BASE_DIR.parent / "tools" / "realesrgan" / "realesrgan-ncnn-vulkan.exe"),
    "channel_positioning": "",
    "channel_audience": "",
    "channel_pillars": "",
    "channel_banned_title_patterns": "",
    "channel_banned_hashtags": "",
    "language_agent_provider": "default",
    "language_agent_base_url": "https://api.openai.com",
    "language_agent_path": "/v1/chat/completions",
    "language_agent_model": "",
    "language_agent_api_key": "",
    "language_agent_api_key_path": "",
    "tts_language_profiles": {},
    "tts_provider_settings": {},
    "visual_pipeline_v2_migrated": False,
}


@app.get("/api/config")
def get_config():
    cfg = load_config()
    # Apply defaults for new fields
    for k, v in _CONFIG_DEFAULTS.items():
        cfg.setdefault(k, v)
    token = cfg.get("jwt_token", "")
    cfg["jwt_token_masked"] = f"{token[:20]}...{token[-8:]}" if len(token) > 30 else token
    ak = cfg.get("anthropic_api_key", "")
    cfg["anthropic_key_masked"] = f"{ak[:8]}...{ak[-4:]}" if len(ak) > 14 else ("***" if ak else "")
    return cfg


@app.post("/api/config")
def update_config(body: ConfigUpdate):
    cfg = load_config()
    for field, value in body.model_dump(exclude_none=True).items():
        cfg[field] = value
    save_config(cfg)
    return {"ok": True}


def _is_legacy_stick_session(data: dict) -> bool:
    story_data = data.get("story_data") if isinstance(data.get("story_data"), dict) else {}
    return story_data.get("content_mode") == "stick_figure" or bool(story_data.get("stick_figure"))


def _purge_legacy_stick_figure_data() -> None:
    """One-time destructive migration authorized for the discontinued V1 flow."""
    cfg = load_config()
    if cfg.get("visual_pipeline_v2_migrated"):
        return
    legacy_projects: set[str] = set()
    STORY_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    for session_path in list(STORY_SESSIONS_DIR.glob("*.json")):
        try:
            session = _load_story_session(session_path)
        except Exception:
            continue
        if _is_legacy_stick_session(session):
            project = str(session.get("current_project") or "")
            if project:
                legacy_projects.add(project)
            session_path.unlink(missing_ok=True)
    for project_path in OUTPUT_DIR.iterdir() if OUTPUT_DIR.exists() else []:
        if not project_path.is_dir() or project_path.name.startswith("_"):
            continue
        plan_path = project_path / "scenes" / "scene_plan.json"
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {}
        except (OSError, json.JSONDecodeError):
            plan = {}
        if plan.get("mode") == "stick_figure":
            legacy_projects.add(project_path.name)
    for project in legacy_projects:
        matches = _story_sessions_for_project(project)
        if not matches:
            project_path = OUTPUT_DIR / project
            if project_path.exists() and project_path.is_dir():
                shutil.rmtree(project_path)
    cfg["visual_pipeline_v2_migrated"] = True
    save_config(cfg)


@app.on_event("startup")
def run_visual_pipeline_v2_migration() -> None:
    _purge_legacy_stick_figure_data()


@app.post("/api/gpt2api/runtime/start")
def start_gpt2api_runtime():
    try:
        return start_gpt2api(load_config())
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


def _gpt_account_call(fn, *args):
    try:
        return fn(load_config(), *args)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/gpt-accounts")
def api_list_gpt_accounts():
    return _gpt_account_call(list_gpt_accounts)


@app.post("/api/gpt-accounts/import")
def api_import_gpt_accounts(body: GptAccountImportRequest):
    return _gpt_account_call(import_gpt_accounts, body.text)


@app.post("/api/gpt-accounts/import-codex")
def api_import_local_codex_account():
    return _gpt_account_call(import_local_codex_account)


@app.post("/api/gpt-accounts/codex-login/start")
def api_start_codex_login():
    return _gpt_account_call(start_codex_login_session)


@app.get("/api/gpt-accounts/codex-login/{session_id}")
def api_get_codex_login(session_id: str):
    try:
        return get_codex_login_session_status(session_id)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/gpt-accounts/{account_id}/enable")
def api_enable_gpt_account(account_id: int):
    return _gpt_account_call(enable_gpt_account, account_id)


@app.post("/api/gpt-accounts/{account_id}/disable")
def api_disable_gpt_account(account_id: int):
    return _gpt_account_call(disable_gpt_account, account_id)


@app.post("/api/gpt-accounts/{account_id}/select")
def api_select_gpt_account(account_id: int):
    return _gpt_account_call(select_gpt_account, account_id)


@app.post("/api/gpt-accounts/{account_id}/probe")
def api_probe_gpt_account(account_id: int):
    return _gpt_account_call(probe_gpt_account, account_id)


@app.delete("/api/gpt-accounts/{account_id}")
def api_delete_gpt_account(account_id: int):
    return _gpt_account_call(delete_gpt_account, account_id)


@app.post("/api/system/select-folder")
def select_folder_dialog(body: dict):
    title = body.get("title") or "Select output folder"
    initial_dir = (body.get("initial_dir") or "").strip().strip('"')
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        options = {"title": title}
        if initial_dir:
            initial_path = Path(initial_dir).expanduser()
            if initial_path.exists() and initial_path.is_dir():
                options["initialdir"] = str(initial_path)
        selected = filedialog.askdirectory(**options)
        root.destroy()
        return {"path": selected or ""}
    except Exception as e:
        raise HTTPException(500, f"Cannot open folder picker: {e}")


# ── Story Sessions ───────────────────────────────────────────────────────────

@app.get("/api/story-sessions")
def list_story_sessions():
    STORY_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for path in STORY_SESSIONS_DIR.glob("*.json"):
        try:
            data = _load_story_session(path)
            story_data = data.get("story_data") or {}
            text = story_data.get("text") or data.get("approved_story") or ""
            prompt = story_data.get("prompt") or ""
            items.append({
                "id": data["id"],
                "title": data.get("title") or prompt[:60] or "Chưa đặt tên",
                "stage": data.get("stage", "STORY_START"),
                "model": story_data.get("model", ""),
                "chars": len(text),
                "current_project": data.get("current_project", ""),
                "created_at": data.get("created_at", ""),
                "updated_at": data.get("updated_at", ""),
            })
        except Exception:
            continue
    return sorted(items, key=lambda x: x.get("updated_at", ""), reverse=True)


@app.post("/api/story-sessions")
def create_story_session(body: StorySessionUpdate):
    now = datetime.now().isoformat(timespec="seconds")
    story_data = body.story_data or {}
    title = body.title or (story_data.get("prompt") or "Truyện mới")[:80]
    data = {
        "id": str(uuid.uuid4()),
        "title": title,
        "story_title": body.story_title or story_data.get("storyTitle") or title,
        "stage": body.stage or "STORY_START",
        "story_data": story_data,
        "approved_story": body.approved_story or "",
        "current_project": body.current_project or "",
        "audio_file": body.audio_file,
        "audio_duration": body.audio_duration or 0,
        "video_order": body.video_order or [],
        "video_settings": body.video_settings or {},
        "output_video_file": body.output_video_file,
        "subtitle_settings": body.subtitle_settings or {},
        "subtitle_outputs": body.subtitle_outputs or {},
        "subtitle_timing_file": body.subtitle_timing_file,
        "created_at": now,
        "updated_at": now,
    }
    return _save_story_session(data)


@app.get("/api/story-sessions/{session_id}")
def get_story_session(session_id: str):
    path = _story_session_path(session_id)
    if not path.exists():
        raise HTTPException(404, "Session not found")
    return _load_story_session(path)


@app.post("/api/story-sessions/{session_id}/repair-project-isolation")
def repair_story_session_project_isolation(session_id: str):
    path = _story_session_path(session_id)
    if not path.exists():
        raise HTTPException(404, "Session not found")
    data = _load_story_session(path)
    repaired = _repair_story_session_project_isolation(path, data)
    return {
        "ok": True,
        "session_id": repaired["id"],
        "current_project": repaired.get("current_project") or "",
    }


@app.patch("/api/story-sessions/{session_id}")
def update_story_session(session_id: str, body: StorySessionUpdate):
    path = _story_session_path(session_id)
    if not path.exists():
        raise HTTPException(404, "Session not found")
    data = _load_story_session(path)
    for field, value in body.model_dump(exclude_none=True).items():
        data[field] = value
    return _save_story_session(data)


@app.delete("/api/story-sessions/{session_id}")
def delete_story_session(session_id: str):
    path = _story_session_path(session_id)
    if not path.exists():
        raise HTTPException(404, "Session not found")
    path.unlink()
    return {"ok": True}


@app.delete("/api/story-sessions/{session_id}/project")
def delete_story_session_project(session_id: str):
    path = _story_session_path(session_id)
    if not path.exists():
        raise HTTPException(404, "Session not found")
    data = _load_story_session(path)
    project = data.get("current_project") or ""
    _require_exclusive_project_session(project, session_id)
    result = _delete_project_dir(project)
    path.unlink()
    result["deleted_sessions"] = 1
    result["session_id"] = session_id
    return result


# ── Projects ──────────────────────────────────────────────────────────────────

@app.get("/api/projects")
def get_projects():
    return list_projects()


@app.post("/api/projects")
def create_project(body: dict):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Invalid project name")
    if not _SAFE_NAME.match(name):
        raise HTTPException(400, "Project name cannot contain a path separator")
    path = get_project_dir(name)
    if path.exists():
        raise HTTPException(409, "Project already exists")
    path.mkdir(parents=True)
    if project_uses_managed_storage(name):
        write_project_title(path, name)
    return {"ok": True, "name": name}


@app.patch("/api/projects/{name}")
def rename_project(name: str, body: dict):
    new_name = (body.get("new_name") or "").strip()
    if not new_name:
        raise HTTPException(400, "Invalid project name")
    if not _SAFE_NAME.match(new_name):
        raise HTTPException(400, "Project name cannot contain a path separator")
    src = get_project_dir(name)
    dst = get_project_dir(new_name)
    if not src.exists() or not src.is_dir():
        raise HTTPException(404, "Project not found")
    if not str(src.resolve()).startswith(str(OUTPUT_DIR.resolve())):
        raise HTTPException(403, "Access denied")
    if dst != src and dst.exists():
        raise HTTPException(409, "A project with that name already exists")
    if dst != src:
        src.rename(dst)
    if project_uses_managed_storage(new_name):
        write_project_title(dst, new_name)
    for session_path, session in _story_sessions_for_project(name):
        session["current_project"] = new_name
        _save_story_session(session)
    return {"ok": True, "new_name": new_name}


@app.delete("/api/projects/{name}")
def delete_project(name: str):
    _require_exclusive_project_session(name)
    result = _delete_project_dir(name)
    result["deleted_sessions"] = _delete_story_sessions_for_project(name)
    return result


@app.get("/api/projects/{name}/size")
def project_size(name: str):
    if not name or not _SAFE_NAME.match(name):
        raise HTTPException(400, "Invalid project name")
    path = get_project_dir(name)
    if not path.exists() or not path.is_dir():
        raise HTTPException(404, "Project not found")
    if not str(path.resolve()).startswith(str(OUTPUT_DIR.resolve())):
        raise HTTPException(403, "Access denied")
    size_bytes = _dir_size_bytes(path)
    return {
        "project": name,
        "size_bytes": size_bytes,
        "size_gb": round(size_bytes / (1024 ** 3), 2),
        "size_mb": round(size_bytes / (1024 ** 2), 2),
    }


# ── Outputs ───────────────────────────────────────────────────────────────────

@app.post("/api/outputs/move")
def move_outputs(body: dict):
    files = body.get("files") or []
    from_project = body.get("from_project", "")
    to_project = body.get("to_project", "")
    src_dir = get_project_dir(from_project)
    dst_dir = get_project_dir(to_project)
    dst_dir.mkdir(parents=True, exist_ok=True)
    moved, errors = [], []
    for filename in files:
        src = src_dir / filename
        if not src.exists():
            errors.append(filename); continue
        if not str(src.resolve()).startswith(str(OUTPUT_DIR.resolve())):
            errors.append(filename); continue
        dst = dst_dir / filename
        if dst.exists():
            stem, suffix, i = dst.stem, dst.suffix, 1
            while dst.exists():
                dst = dst_dir / f"{stem}_{i}{suffix}"
                i += 1
        src.rename(dst)
        moved.append(filename)
    return {"ok": True, "moved": moved, "errors": errors}


@app.get("/api/outputs")
def get_outputs(project: str = ""):
    return list_outputs(project)


@app.get("/api/outputs/{filename}")
def download_output(filename: str, request: Request, project: str = ""):
    path = get_project_dir(project) / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "File not found")
    if not str(path.resolve()).startswith(str(OUTPUT_DIR.resolve())):
        raise HTTPException(403, "Access denied")
    media_type = "audio/mpeg" if filename.endswith(".mp3") else "audio/wav"
    return _range_audio_response(path, media_type, request.headers.get("range"))


def _range_audio_response(path: Path, media_type: str, range_header: str | None):
    """Serve audio with HTTP byte ranges so browser players can seek."""
    size = path.stat().st_size
    # Audio is commonly regenerated under the same project/filename. Never let
    # a browser replay an older MP3 from cache while the UI shows fresh timing.
    common_headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store, max-age=0"}
    if not range_header:
        return FileResponse(str(path), media_type=media_type, headers=common_headers)

    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
    if not match:
        raise HTTPException(416, "Invalid audio range", headers={"Content-Range": f"bytes */{size}"})
    start_raw, end_raw = match.groups()
    try:
        if start_raw:
            start = int(start_raw)
            end = int(end_raw) if end_raw else size - 1
        elif end_raw:
            length = int(end_raw)
            start = max(0, size - length)
            end = size - 1
        else:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(416, "Invalid audio range", headers={"Content-Range": f"bytes */{size}"}) from exc

    if start < 0 or start >= size or end < start:
        raise HTTPException(416, "Audio range is outside the file", headers={"Content-Range": f"bytes */{size}"})
    end = min(end, size - 1)
    length = end - start + 1

    def stream_range():
        with open(path, "rb") as audio:
            audio.seek(start)
            remaining = length
            while remaining > 0:
                chunk = audio.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        stream_range(),
        status_code=206,
        media_type=media_type,
        headers={
            **common_headers,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(length),
        },
    )


@app.patch("/api/outputs/{filename}")
def rename_output(filename: str, body: dict, project: str = ""):
    new_name = (body.get("new_name") or "").strip()
    if not new_name:
        raise HTTPException(400, "new_name is required")
    if not _SAFE_FILE.match(new_name):
        raise HTTPException(400, "Invalid filename")
    project_dir = get_project_dir(project)
    src = project_dir / filename
    dst = project_dir / new_name
    if not src.exists():
        raise HTTPException(404, "File not found")
    if not str(src.resolve()).startswith(str(OUTPUT_DIR.resolve())):
        raise HTTPException(403, "Access denied")
    if dst.exists():
        raise HTTPException(409, "A file with that name already exists")
    src.rename(dst)
    return {"ok": True, "new_name": new_name}


@app.delete("/api/outputs/{filename}")
def delete_output(filename: str, project: str = ""):
    path = get_project_dir(project) / filename
    if not path.exists():
        raise HTTPException(404, "File not found")
    if not str(path.resolve()).startswith(str(OUTPUT_DIR.resolve())):
        raise HTTPException(403, "Access denied")
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    dest = TRASH_DIR / filename
    if dest.exists():
        stem, suffix, i = dest.stem, dest.suffix, 1
        while dest.exists():
            dest = TRASH_DIR / f"{stem}_{i}{suffix}"
            i += 1
    path.rename(dest)
    return {"ok": True}


# ── Trash ─────────────────────────────────────────────────────────────────────

@app.get("/api/trash")
def get_trash():
    return list_trash()


@app.post("/api/trash/{filename}/restore")
def restore_output(filename: str, project: str = ""):
    path = TRASH_DIR / filename
    if not path.exists():
        raise HTTPException(404, "File not found in trash")
    dest = get_project_dir(project) / filename
    if dest.exists():
        raise HTTPException(409, "A file with that name already exists")
    get_project_dir(project).mkdir(parents=True, exist_ok=True)
    path.rename(dest)
    return {"ok": True}


@app.delete("/api/trash/{filename}")
def permanent_delete(filename: str):
    path = TRASH_DIR / filename
    if not path.exists():
        raise HTTPException(404, "File not found in trash")
    if not str(path.resolve()).startswith(str(TRASH_DIR.resolve())):
        raise HTTPException(403, "Access denied")
    path.unlink()
    return {"ok": True}


# ── Generate ──────────────────────────────────────────────────────────────────

@app.post("/api/preview-split")
async def preview_split(body: GenerateRequest):
    cfg = load_config()
    max_chars = cfg.get("max_chars", 2500)
    segments = split_text_for_tts_timing(strip_chapter_headings_for_tts(body.text), max_chars)
    return {
        "total": len(segments),
        "segments": [
            {"index": i + 1, "length": len(s), "preview": s[:100]}
            for i, s in enumerate(segments)
        ],
    }


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    if not file.filename.endswith(".txt"):
        raise HTTPException(400, "Only .txt files are supported")
    content = await file.read()
    text = content.decode("utf-8", errors="replace")
    return {"text": text, "length": len(text), "filename": file.filename}


@app.get("/api/generate")
def generate_sse(text: str, output_name: str | None = None, project: str = "", locale: str = ""):
    return _generate_streaming_response(text, output_name, project, locale)


@app.post("/api/generate")
def generate_sse_post(body: GenerateRequest):
    return _generate_streaming_response(body.text, body.output_name, body.project, body.locale)


def _generate_streaming_response(text: str, output_name: str | None = None, project: str = "", locale: str = ""):
    cfg = load_config()
    locale = (locale or "vi-VN").strip() or "vi-VN"
    try:
        # Provider-specific validation lives behind the TTS abstraction.
        resolve_tts_profile(cfg, locale)
    except TTSProviderError as exc:
        raise HTTPException(400, str(exc)) from exc

    name = output_name or datetime.now().strftime("tts_%Y%m%d_%H%M%S")
    name = "".join(c if c.isalnum() or c in "_-" else "_" for c in name)

    def event_stream():
        try:
            for event in process_text_streaming(text, name, cfg, project, locale):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _tts_provider_settings_from_config(config: dict, provider_id: str) -> dict:
    if provider_id == "vivibe":
        return {"jwt_token": config.get("jwt_token", "")}
    all_settings = config.get("tts_provider_settings") or {}
    return dict(all_settings.get(provider_id) or {}) if isinstance(all_settings, dict) else {}


@app.get("/api/tts/providers")
def list_tts_providers():
    return {"providers": TTS_PROVIDERS.definitions()}


@app.get("/api/tts/providers/{provider_id}/voices")
def list_tts_provider_voices(provider_id: str, locale: str = ""):
    try:
        provider = TTS_PROVIDERS.get(provider_id)
        settings = _tts_provider_settings_from_config(load_config(), provider_id)
        return {"voices": provider.list_voices(locale or "vi-VN", settings)}
    except TTSProviderError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/tts/profile")
def get_tts_profile(locale: str = "vi-VN"):
    locale = (locale or "vi-VN").strip() or "vi-VN"
    try:
        provider, _, profile = resolve_tts_profile(load_config(), locale)
        return {"configured": True, "locale": locale, "provider": provider.provider_id, "provider_label": provider.label, "profile": profile}
    except TTSProviderError as exc:
        return {"configured": False, "locale": locale, "error": str(exc)}


@app.post("/api/tts/providers/{provider_id}/test")
def test_tts_provider(provider_id: str, body: TTSProviderTestRequest):
    try:
        provider = TTS_PROVIDERS.get(provider_id)
        locale = (body.locale or "vi-VN").strip() or "vi-VN"
        profile = {**provider.default_profile(locale), **(body.profile or {}), "provider": provider_id}
        settings = _tts_provider_settings_from_config(load_config(), provider_id)
        provider.validate(settings, profile)
        samples = {
            "ko-KR": "안녕하세요. 이것은 LucyLab 음성 테스트입니다.",
            "vi-VN": "Xin chào. Đây là bản nghe thử giọng đọc của LucyLab.",
        }
        result = provider.synthesize(SynthesisRequest(
            text=samples.get(locale, "Hello. This is a LucyLab voice preview."),
            locale=locale,
            voice_id=str(profile.get("voice_id") or ""),
            speed=float(profile.get("speed", 1.0)),
            profile=profile,
            provider_settings=settings,
        ))
        return Response(content=result.audio, media_type=result.content_type, headers={"Cache-Control": "no-store"})
    except TTSProviderError as exc:
        raise HTTPException(400, str(exc)) from exc


# ── Story ─────────────────────────────────────────────────────────────────────

class AnalyzePromptRequest(BaseModel):
    prompt: str
    model: str = "claude-sonnet-4-6"


class InterviewRequest(BaseModel):
    history: list[dict]
    user_message: str
    model: str = "claude-sonnet-4-6"


class GenerateStoryRequest(BaseModel):
    prompt: str
    interview_history: list[dict] = []
    model: str = "claude-sonnet-4-6"


class StickFigureExtractRequest(BaseModel):
    source_script: str
    model: str = "claude-sonnet-4-6"


_STICK_FIGURE_EXTRACT_SYSTEM = """You are a documentary explainer producer.
Turn a supplied, structured video script in any language into a narration-first content blueprint for a 2D stick-figure explainer. Preserve the source language at this stage.

Return strict JSON only:
{
  "tts_text": "only the natural narration in the source language to be spoken, with no headings, labels, image directions, timing notes, markdown, SEO, or production notes",
  "visual_style": "one concise, reusable style prompt",
  "references": [
    {"id":"style_001","type":"style|character|location|prop","name":"...","description":"...","prompt":"..."}
  ]
}

Rules:
- Preserve all narration facts and their order in tts_text. Do not include headings, labels, image directions, timing notes, markdown, SEO, or production notes.
- Image directions from labels and production notes are non-binding context only. Do not create final shots, scene counts, duration hints, prompts, or storyboards: those are decided later from final narration and actual audio timing.
- References are reusable continuity hints only. Include recurring characters, key locations, props, and style sources when useful; do not impose an artificial maximum.
"""

def _stick_narration_units(text: str) -> list[dict]:
    """Create small semantic narration boundaries, never final visual shots.

    Planner V2 can freely combine these adjacent clauses into one image. Keeping
    natural pause boundaries gives it enough resolution to choose distinct
    visuals when a single long spoken sentence changes idea midway through.
    """
    normalized = re.sub(r"\s+", " ", text or "").strip()
    sentences = [
        part.strip()
        for part in re.split(r"(?<=[.!?…。！？])\s+|(?<=[。！？])", normalized)
        if part.strip()
    ]
    units: list[str] = []
    for sentence in sentences:
        parts = [part.strip() for part in re.split(r"(?<=[,;:—–])\s+", sentence) if part.strip()]
        # Do not turn an isolated introductory phrase into its own beat. This
        # is only a candidate boundary; the planner still decides all shots.
        pending = ""
        for part in parts:
            if pending:
                part = f"{pending} {part}"
                pending = ""
            if len(re.findall(r"[^\W_]+", part, flags=re.UNICODE)) < 4 and part[-1:] in {",", ";", ":", "—", "–"}:
                pending = part
            else:
                units.append(part)
        if pending:
            if units:
                units[-1] = f"{units[-1]} {pending}"
            else:
                units.append(pending)
    return [{"id": f"n_{index:04d}", "text": unit, "order": index} for index, unit in enumerate(units, start=1)]


def _stick_figure_json(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("AI did not return a JSON blueprint")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("AI blueprint must be a JSON object")
    narration = str(data.get("tts_text") or "").strip()
    if not narration:
        raise ValueError("AI blueprint is missing TTS narration")
    data["tts_text"] = narration
    data["references"] = data.get("references") if isinstance(data.get("references"), list) else []
    data["narration_units"] = _stick_narration_units(narration)
    data.pop("scenes", None)
    data["schema_version"] = 2
    return data


def _stick_figure_cfg() -> dict:
    cfg = load_config()
    cfg.setdefault("stick_figure_model", "")
    cfg.setdefault("stick_figure_provider", "default")
    cfg.setdefault("stick_figure_base_url", "https://api.openai.com")
    cfg.setdefault("stick_figure_path", "/v1/chat/completions")
    cfg.setdefault("stick_figure_api_key", "")
    cfg.setdefault("stick_figure_api_key_path", "")
    return cfg


def _call_stick_figure_llm(source: str, requested_model: str) -> tuple[str, str, str]:
    cfg = _stick_figure_cfg()
    provider = (cfg.get("stick_figure_provider") or "default").strip().lower()
    configured_model = (cfg.get("stick_figure_model") or "").strip()
    if provider == "custom":
        model = configured_model or "gpt-4o-mini"
        raw = generate_text_openai_compatible(
            _STICK_FIGURE_EXTRACT_SYSTEM,
            source,
            base_url=cfg.get("stick_figure_base_url") or "https://api.openai.com",
            path=cfg.get("stick_figure_path") or "/v1/chat/completions",
            model=model,
            api_key=cfg.get("stick_figure_api_key") or "",
            api_key_path=cfg.get("stick_figure_api_key_path") or "",
            max_tokens=12000,
        )
        return raw, "custom", model

    model = (configured_model or requested_model or cfg.get("default_story_model") or "claude-sonnet-4-6").strip()
    api_key = cfg.get("anthropic_api_key", "")
    if not api_key and requires_anthropic_key(model):
        raise HTTPException(400, "Chưa cấu hình Anthropic API key. Vào Settings → AI Story để thêm.")
    raw = generate_text(
        _STICK_FIGURE_EXTRACT_SYSTEM,
        source,
        model,
        api_key,
        cfg.get("ollama_url", "http://localhost:11434"),
        max_tokens=12000,
        cli_timeout_sec=900,
    )
    return raw, "default", model



@app.get("/api/models")
def get_models():
    cfg = load_config()
    ollama_url = cfg.get("ollama_url", "http://localhost:11434")
    ollama = get_ollama_models(ollama_url)
    codex = get_codex_models()
    opencode = get_opencode_models()
    return {
        "anthropic": ANTHROPIC_MODELS,
        "ollama": ollama,
        "codex": codex,
        "opencode": opencode,
    }


def _story_cfg():
    cfg = load_config()
    return cfg.get("anthropic_api_key", ""), cfg.get("ollama_url", "http://localhost:11434")


def _clean_font_name(name: str) -> str:
    name = re.sub(r"\s*\((TrueType|OpenType|Raster|Vector)\)\s*$", "", name or "", flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", name).strip()


def _list_system_fonts() -> list[str]:
    fonts: dict[str, str] = {}

    def add_font(value: str):
        cleaned = _clean_font_name(value)
        if not cleaned:
            return
        key = cleaned.casefold()
        if key not in fonts or fonts[key].isupper():
            fonts[key] = cleaned

    for default in ("Arial", "Tahoma", "Verdana", "Times New Roman"):
        add_font(default)

    try:
        import winreg
        registry_paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
        ]
        for root, path in registry_paths:
            try:
                with winreg.OpenKey(root, path) as key:
                    count = winreg.QueryInfoKey(key)[1]
                    for i in range(count):
                        value_name, _, _ = winreg.EnumValue(key, i)
                        add_font(value_name)
            except OSError:
                pass
    except Exception:
        pass

    fonts_dir = Path(r"C:\Windows\Fonts")
    if len(fonts) < 20 and fonts_dir.exists():
        for suffix in ("*.ttf", "*.otf", "*.ttc"):
            for path in fonts_dir.glob(suffix):
                add_font(path.stem)
    return sorted(fonts.values(), key=lambda item: item.casefold())


@app.get("/api/fonts")
def list_fonts():
    return {"fonts": _list_system_fonts()}


@app.post("/api/story/analyze-prompt")
def story_analyze_prompt(body: AnalyzePromptRequest):
    api_key, ollama_url = _story_cfg()
    if not api_key and requires_anthropic_key(body.model):
        raise HTTPException(400, "Chưa cấu hình Anthropic API key. Vào Settings → AI Story để thêm.")
    cfg = load_config()
    model = body.model or cfg.get("default_story_model", "claude-sonnet-4-6")
    return analyze_prompt(body.prompt, model, api_key, ollama_url)


def _with_channel_profile(prompt: str, cfg: dict) -> str:
    """Prepend the Channel Strategy Profile so story output follows channel positioning."""
    block = channel_profile_block(cfg)
    return f"{block}\n\n{prompt}" if block else prompt


@app.post("/api/story/interview")
def story_interview(body: InterviewRequest):
    api_key, ollama_url = _story_cfg()
    if not api_key and requires_anthropic_key(body.model):
        raise HTTPException(400, "Chưa cấu hình Anthropic API key. Vào Settings → AI Story để thêm.")
    cfg = load_config()
    model = body.model or cfg.get("default_story_model", "claude-sonnet-4-6")
    prompt = _with_channel_profile(body.user_message, cfg) if not body.history else body.user_message
    return interview_turn(body.history, prompt, model, api_key, ollama_url)


@app.post("/api/story/interview-stream")
def story_interview_stream(body: InterviewRequest):
    api_key, ollama_url = _story_cfg()
    if not api_key and requires_anthropic_key(body.model):
        raise HTTPException(400, "Chưa cấu hình Anthropic API key. Vào Settings → AI Story để thêm.")
    cfg = load_config()
    model = body.model or cfg.get("default_story_model", "claude-sonnet-4-6")
    user_message = _with_channel_profile(body.user_message, cfg) if not body.history else body.user_message

    def event_stream():
        for event in stream_interview(body.history, user_message, model, api_key, ollama_url):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/story/generate")
def story_generate(body: GenerateStoryRequest):
    api_key, ollama_url = _story_cfg()
    if not api_key and requires_anthropic_key(body.model):
        raise HTTPException(400, "Chưa cấu hình Anthropic API key. Vào Settings → AI Story để thêm.")
    cfg = load_config()
    model = body.model or cfg.get("default_story_model", "claude-sonnet-4-6")
    prompt = _with_channel_profile(body.prompt, cfg)

    def event_stream():
        for event in stream_story(prompt, body.interview_history, model, api_key, ollama_url):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/story/stick-figure/extract")
def extract_stick_figure_script(body: StickFigureExtractRequest):
    source = (body.source_script or "").strip()
    if len(source) < 80:
        raise HTTPException(400, "Kịch bản cần dài ít nhất 80 ký tự để AI tách narration và storyboard.")
    try:
        raw, provider, model = _call_stick_figure_llm(source, body.model)
        blueprint = _stick_figure_json(raw)
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"Không thể tách kịch bản người que: {exc}") from exc
    blueprint["source_script"] = source
    blueprint["mode"] = "stick_figure"
    blueprint["provider"] = provider
    blueprint["model"] = model
    return blueprint


# ── Video ──────────────────────────────────────────────────────────────────────

def _get_video_dir(project: str) -> Path:
    project_dir = get_project_dir(project)
    video_dir = project_dir / "videos"
    video_dir.mkdir(parents=True, exist_ok=True)
    return video_dir


@app.post("/api/video/upload/{project}")
async def upload_video(project: str, file: UploadFile = File(...)):
    if not _SAFE_NAME.match(project):
        raise HTTPException(400, "Invalid project name")
    ext = Path(file.filename).suffix.lower()
    if ext not in VIDEO_EXTS:
        raise HTTPException(400, f"Định dạng không hỗ trợ. Chấp nhận: {', '.join(VIDEO_EXTS)}")

    video_dir = _get_video_dir(project)
    # Prefix with order index to preserve upload order
    existing = sorted(p for p in video_dir.iterdir() if p.suffix.lower() in VIDEO_EXTS)
    idx = len(existing) + 1
    safe_stem = re.sub(r'[^\w\-]', '_', Path(file.filename).stem)
    filename = f"{idx:03d}_{safe_stem}{ext}"
    dest = video_dir / filename

    content = await file.read()
    async with aiofiles.open(dest, "wb") as f:
        await f.write(content)

    cfg = load_config()
    ffprobe = cfg.get("ffprobe_path", "ffprobe")
    duration = get_video_duration(dest, ffprobe)
    return {"filename": filename, "duration_sec": duration, "size_mb": round(dest.stat().st_size / (1024 * 1024), 2)}


@app.get("/api/video/list/{project}")
def list_videos(project: str):
    if not _SAFE_NAME.match(project):
        raise HTTPException(400, "Invalid project name")
    video_dir = _get_video_dir(project)
    cfg = load_config()
    ffprobe = cfg.get("ffprobe_path", "ffprobe")
    items = []
    for p in sorted(video_dir.iterdir()):
        if p.suffix.lower() in VIDEO_EXTS:
            items.append({
                "filename": p.name,
                "duration_sec": get_video_duration(p, ffprobe),
                "size_mb": round(p.stat().st_size / (1024 * 1024), 2),
            })
    return items


@app.delete("/api/video/{project}/{filename}")
def delete_video(project: str, filename: str):
    if not _SAFE_NAME.match(project):
        raise HTTPException(400, "Invalid project name")
    video_dir = _get_video_dir(project)
    path = video_dir / filename
    if not path.exists():
        raise HTTPException(404, "Video not found")
    if not str(path.resolve()).startswith(str(video_dir.resolve())):
        raise HTTPException(403, "Access denied")
    path.unlink()
    return {"ok": True}


@app.get("/api/video/file/{project}/{filename}")
def serve_video(project: str, filename: str, request: Request):
    if not _SAFE_NAME.match(project):
        raise HTTPException(400, "Invalid project name")
    video_dir = _get_video_dir(project)
    path = video_dir / filename
    if not path.exists():
        raise HTTPException(404, "File not found")
    return _video_file_response(path, request)


class ComposeRequest(BaseModel):
    project: str
    audio_file: str
    video_order: list[str] = []  # filenames in desired order; empty = use upload order
    output_name: str | None = None
    video_encoder: str = "nvidia_nvenc"
    quality: str = "high"
    overwrite_mode: str = "version"
    video_mode: str = "background"
    loop_video: str = ""


@app.post("/api/video/compose")
def video_compose(body: ComposeRequest):
    if not _SAFE_NAME.match(body.project):
        raise HTTPException(400, "Invalid project name")

    project_dir = get_project_dir(body.project)
    video_dir = _get_video_dir(body.project)
    audio_path = project_dir / body.audio_file
    if not audio_path.exists():
        raise HTTPException(404, "Audio file not found")

    # Resolve the source. Loop mode owns one short clip stored separately from
    # the existing multi-background upload list.
    loop_video_path = None
    if body.video_mode == "loop":
        loop_name = Path(body.loop_video or "").name
        if not _SAFE_VIDEO.match(loop_name):
            raise HTTPException(400, "Invalid loop video")
        loop_video_path = project_dir / "loop_assets" / "videos" / loop_name
        if not loop_video_path.is_file():
            raise HTTPException(404, "Loop video not found")
        video_paths = []
    elif body.video_order:
        video_paths = [video_dir / fn for fn in body.video_order]
    else:
        video_paths = sorted(p for p in video_dir.iterdir() if p.suffix.lower() in VIDEO_EXTS)

    for vp in video_paths:
        if not vp.exists():
            raise HTTPException(404, f"Video không tìm thấy: {vp.name}")

    cfg = load_config()
    ffmpeg_path = cfg.get("ffmpeg_path", "ffmpeg")
    ffprobe_path = cfg.get("ffprobe_path", "ffprobe")

    # Get audio duration via ffprobe
    audio_duration = get_video_duration(audio_path, ffprobe_path)
    if audio_duration <= 0:
        raise HTTPException(400, "Không đọc được độ dài audio")

    output_name = body.output_name or "final_video.mp4"
    if not output_name.lower().endswith(".mp4"):
        output_name += ".mp4"
    output_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in output_name)
    if not output_name or output_name.startswith("."):
        output_name = "final_video.mp4"

    output_path = project_dir / output_name
    if body.overwrite_mode != "overwrite":
        stem, suffix, i = output_path.stem, output_path.suffix, 2
        while output_path.exists():
            output_path = project_dir / f"{stem}_{i}{suffix}"
            i += 1

    def event_stream():
        if loop_video_path:
            for event in compose_loop_video(
                loop_video=loop_video_path,
                audio_path=audio_path,
                output_path=output_path,
                audio_duration=audio_duration,
                ffmpeg_path=ffmpeg_path,
                video_encoder=body.video_encoder,
                quality=body.quality,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return
        for event in compose_video(
            video_paths=video_paths,
            audio_path=audio_path,
            output_path=output_path,
            audio_duration=audio_duration,
            temp_dir=TEMP_DIR,
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
            video_encoder=body.video_encoder,
            quality=body.quality,
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/output-video/{project}")
def serve_output_video(project: str, request: Request, filename: str = "final_video.mp4"):
    if not _SAFE_NAME.match(project):
        raise HTTPException(400, "Invalid project name")
    if not re.match(r"^[\w.\- ]+\.mp4$", filename, re.IGNORECASE):
        raise HTTPException(400, "Invalid filename")
    path = get_project_dir(project) / filename
    if not path.exists():
        raise HTTPException(404, "Output video not found")
    return _video_file_response(path, request)


# ── Subtitles ────────────────────────────────────────────────────────────────

def _fallback_timing(story_text: str, total_duration: float) -> dict:
    captions = []
    narration_text = strip_chapter_headings_for_tts(story_text or "")
    units = split_text_for_timing(narration_text, 420)
    weights = [max(1, len(u)) for u in units]
    total_weight = sum(weights) or 1
    cursor = 0.0
    for i, (unit, weight) in enumerate(zip(units, weights), start=1):
        duration = max(0.8, total_duration * (weight / total_weight))
        end = min(total_duration, cursor + duration)
        captions.append({
            "index": i,
            "text": unit,
            "start_sec": round(cursor, 3),
            "end_sec": round(end, 3),
            "duration_sec": round(max(0.01, end - cursor), 3),
            "audio_segment_file": "",
        })
        cursor = end
    return {
        "version": 1,
        "source_text": narration_text,
        "warning": "Timing xấp xỉ vì audio này chưa có timing metadata.",
        "total_duration_sec": round(total_duration, 3),
        "captions": captions,
    }


def _timing_duration_is_valid(timing: dict, audio_duration: float) -> bool:
    try:
        timing_duration = float(timing.get("total_duration_sec") or 0)
    except Exception:
        return False
    if audio_duration <= 1:
        return timing_duration > 0
    return audio_duration * 0.5 <= timing_duration <= audio_duration * 1.5


def _timing_or_fallback(project_dir: Path, audio_file: str, story_text: str, ffprobe_path: str) -> dict:
    timing = load_timing_file(project_dir, audio_file)
    audio_path = project_dir / audio_file
    audio_duration = get_video_duration(audio_path, ffprobe_path) if audio_path.exists() else 0
    if timing and _timing_duration_is_valid(timing, audio_duration):
        return timing

    fallback_text = story_text or (timing or {}).get("source_text", "")
    if not fallback_text.strip():
        if timing:
            timing = dict(timing)
            timing["warning"] = "Timing metadata duration does not match audio duration."
            return timing
        raise HTTPException(404, "Timing metadata not found")

    fallback = _fallback_timing(fallback_text, audio_duration)
    if timing:
        fallback["warning"] = (
            f"Timing metadata cũ bị lệch duration ({timing.get('total_duration_sec', 0)}s) "
            f"so với audio thật ({round(audio_duration, 3)}s); đang dùng timing xấp xỉ."
        )
    return fallback


@app.get("/api/subtitles/timing")
def subtitle_timing(project: str, audio_file: str, story_text: str = ""):
    if not _SAFE_NAME.match(project):
        raise HTTPException(400, "Invalid project name")
    project_dir = get_project_dir(project)
    cfg = load_config()
    timing = _timing_or_fallback(project_dir, audio_file, story_text, cfg.get("ffprobe_path", "ffprobe"))
    timing = dict(timing)
    timing["subtitle_cues"] = build_subtitle_cues(timing)
    timing["cue_version"] = 1
    return timing


@app.get("/api/subtitles/srt")
def subtitle_srt(
    project: str,
    audio_file: str,
    mode: str = "16x9",
    max_chars: int = 110,
    max_words: int = 24,
):
    if not _SAFE_NAME.match(project):
        raise HTTPException(400, "Invalid project name")
    if Path(audio_file).name != audio_file or Path(audio_file).suffix.lower() not in AUDIO_EXTS:
        raise HTTPException(400, "Invalid audio file")
    if mode not in {"16x9", "9x16"}:
        raise HTTPException(400, "Invalid subtitle mode")
    project_dir = get_project_dir(project)
    cfg = load_config()
    timing = _timing_or_fallback(project_dir, audio_file, "", cfg.get("ffprobe_path", "ffprobe"))
    cues = build_subtitle_cues(
        timing,
        max_chars=max(12, min(180, int(max_chars or 110))),
        max_words=max(2, min(36, int(max_words or 24))),
        use_cached=False,
    )
    if not cues:
        raise HTTPException(404, "Subtitle cues not found")
    filename = f"{_safe_stem(Path(audio_file).stem, 'subtitles')}_{mode}.srt"
    return Response(
        content=subtitle_cues_to_srt(cues),
        media_type="application/x-subrip; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/subtitles/render")
def subtitle_render(body: SubtitleRenderRequest):
    if not _SAFE_NAME.match(body.project):
        raise HTTPException(400, "Invalid project name")
    if not re.match(r"^[\w.\- ]+\.mp4$", body.input_video, re.IGNORECASE):
        raise HTTPException(400, "Invalid input video")
    project_dir = get_project_dir(body.project)
    input_path = project_dir / body.input_video
    if not input_path.exists() or not str(input_path.resolve()).startswith(str(project_dir.resolve())):
        raise HTTPException(404, "Input video not found")
    if body.vertical_video_mode not in {"crop", "fit"}:
        raise HTTPException(400, "Invalid 9:16 video mode")
    render_modes = body.render_modes if body.render_modes is not None else ["16x9", "9x16"]
    if not render_modes or any(mode not in {"16x9", "9x16"} for mode in render_modes):
        raise HTTPException(400, "Invalid subtitle render modes")

    cfg = load_config()
    ffmpeg_path = cfg.get("ffmpeg_path", "ffmpeg")
    ffprobe_path = cfg.get("ffprobe_path", "ffprobe")
    timing = _timing_or_fallback(project_dir, body.audio_file, body.story_text, ffprobe_path)
    background_music = None
    music_name = Path(body.background_music or "").name
    if music_name:
        if music_name != body.background_music or Path(music_name).suffix.lower() not in AUDIO_EXTS:
            raise HTTPException(400, "Invalid background music file")
        background_music = project_dir / "background_music" / music_name
        if not background_music.is_file():
            raise HTTPException(404, "Background music not found")

    def event_stream():
        try:
            for event in render_subtitle_videos(
                project_dir=project_dir,
                input_video=input_path,
                timing=timing,
                style=body.style,
                ffmpeg_path=ffmpeg_path,
                ffprobe_path=ffprobe_path,
                video_encoder=body.video_encoder,
                quality=body.quality,
                overwrite_mode=body.overwrite_mode,
                background_music=background_music,
                background_music_volume=max(0.0, min(1.0, float(body.background_music_volume or 0))),
                render_modes=render_modes,
                vertical_video_mode=body.vertical_video_mode,
                burn_subtitles=body.burn_subtitles,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/subtitles/background-music/upload")
async def subtitle_background_music_upload(project: str, file: UploadFile = File(...)):
    if not _SAFE_NAME.match(project):
        raise HTTPException(400, "Invalid project name")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in AUDIO_EXTS:
        raise HTTPException(400, f"Unsupported music format. Accepted: {', '.join(sorted(AUDIO_EXTS))}")
    music_dir = get_project_dir(project) / "background_music"
    music_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex[:8]}_{_safe_stem(Path(file.filename or '').stem, 'background_music')}{ext}"
    path = music_dir / filename
    content = await file.read()
    async with aiofiles.open(path, "wb") as handle:
        await handle.write(content)
    cfg = load_config()
    return {
        "filename": filename,
        "duration_sec": get_video_duration(path, cfg.get("ffprobe_path", "ffprobe")),
        "size_mb": round(path.stat().st_size / (1024 * 1024), 2),
    }


@app.get("/api/subtitles/background-music/{project}/{filename}")
def subtitle_background_music_file(project: str, filename: str):
    if not _SAFE_NAME.match(project) or Path(filename).name != filename or Path(filename).suffix.lower() not in AUDIO_EXTS:
        raise HTTPException(400, "Invalid background music")
    path = get_project_dir(project) / "background_music" / filename
    if not path.is_file():
        raise HTTPException(404, "Background music not found")
    media_types = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
        ".opus": "audio/ogg",
    }
    return FileResponse(str(path), media_type=media_types.get(path.suffix.lower(), "application/octet-stream"), filename=filename)


# ── Metadata ───────────────────────────────────────────────────────────────────

def _safe_stem(value: str, fallback: str = "video") -> str:
    stem = re.sub(r"[^\w\-]+", "_", value.strip(), flags=re.UNICODE).strip("_")
    return stem or fallback


def _video_cutter_meta_path(upload_id: str) -> Path:
    if not re.match(r"^[a-f0-9\-]{36}$", upload_id):
        raise HTTPException(400, "Invalid upload id")
    return VIDEO_CUTTER_UPLOADS / f"{upload_id}.json"


def _audio_cutter_meta_path(upload_id: str) -> Path:
    if not re.match(r"^[a-f0-9\-]{36}$", upload_id):
        raise HTTPException(400, "Invalid upload id")
    return AUDIO_CUTTER_UPLOADS / f"{upload_id}.json"


def _resolve_video_cutter_output_dir(output_dir: str | None) -> Path:
    if not output_dir or not output_dir.strip():
        path = VIDEO_CUTTER_OUTPUTS
    else:
        raw = output_dir.strip().strip('"')
        if "\x00" in raw:
            raise HTTPException(400, "Invalid output folder")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (BASE_DIR / path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise HTTPException(400, "Output folder is not a directory")
    return path


def _resolve_required_output_dir(output_dir: str | None) -> Path:
    if not output_dir or not output_dir.strip():
        raise HTTPException(400, "Output folder is required")
    return _resolve_video_cutter_output_dir(output_dir)


def _project_video_path(project: str, filename: str) -> Path:
    if not _SAFE_NAME.match(project):
        raise HTTPException(400, "Invalid project name")
    if not re.match(r"^[\w.\- ]+\.mp4$", filename, re.IGNORECASE):
        raise HTTPException(400, "Invalid video filename")
    project_dir = get_project_dir(project)
    path = project_dir / filename
    try:
        path.resolve().relative_to(project_dir.resolve())
    except ValueError:
        raise HTTPException(403, "Access denied")
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "Video not found")
    return path


def _unique_output_path(output_dir: Path, output_name: str, overwrite_mode: str = "version") -> Path:
    name = output_name.strip() or "output.mp4"
    if not name.lower().endswith(".mp4"):
        name += ".mp4"
    stem = _safe_stem(Path(name).stem, "output")
    path = output_dir / f"{stem}.mp4"
    if overwrite_mode == "overwrite":
        return path
    candidate = path
    i = 2
    while candidate.exists():
        candidate = output_dir / f"{stem}_{i}.mp4"
        i += 1
    return candidate


def _unique_audio_output_path(output_name: str, output_format: str) -> Path:
    fmt = output_format.lower().lstrip(".")
    if fmt not in {"mp3", "wav", "m4a"}:
        raise HTTPException(400, "Output format must be MP3, WAV, or M4A")
    stem = _safe_stem(Path(output_name).stem, "audio_cut")
    AUDIO_CUTTER_OUTPUTS.mkdir(parents=True, exist_ok=True)
    candidate = AUDIO_CUTTER_OUTPUTS / f"{stem}.{fmt}"
    index = 2
    while candidate.exists():
        candidate = AUDIO_CUTTER_OUTPUTS / f"{stem}_{index}.{fmt}"
        index += 1
    return candidate


def _video_stream_info(path: Path, ffprobe: str) -> dict:
    result = subprocess.run(
        [
            ffprobe, "-v", "quiet",
            "-print_format", "json",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    data = json.loads(result.stdout or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    fps_raw = video.get("avg_frame_rate") or video.get("r_frame_rate") or "30/1"
    try:
        num, den = fps_raw.split("/")
        fps = float(num) / max(1.0, float(den))
    except Exception:
        fps = 30.0
    return {
        "width": int(video.get("width") or 1920),
        "height": int(video.get("height") or 1080),
        "fps": max(1.0, min(120.0, fps or 30.0)),
        "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
    }


def _has_audio(path: Path, ffprobe: str) -> bool:
    try:
        return bool(_video_stream_info(path, ffprobe).get("has_audio"))
    except Exception:
        return False


def _run_process_progress(cmd: list[str], output_path: Path, total_duration: float, percent_start: int, percent_span: int, message: str):
    stderr_tail: list[str] = []
    proc = subprocess.Popen(
        cmd,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stderr is not None
    last_speed = ""
    for raw in proc.stderr:
        line = raw.strip()
        if not line:
            continue
        stderr_tail.append(line)
        stderr_tail = stderr_tail[-24:]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "speed":
            last_speed = value
        elapsed = _parse_ffmpeg_time(key, value)
        if elapsed is not None:
            ratio = min(1.0, elapsed / total_duration) if total_duration > 0 else 0
            size_mb = output_path.stat().st_size / (1024 * 1024) if output_path.exists() else 0
            yield {
                "type": "progress",
                "message": message,
                "percent": min(99, percent_start + int(ratio * percent_span)),
                "elapsed_sec": elapsed,
                "total_sec": total_duration,
                "eta_sec": max(0, total_duration - elapsed) if total_duration > 0 else 0,
                "speed": last_speed,
                "size_mb": round(size_mb, 2),
            }
    proc.wait()
    if proc.returncode != 0:
        yield {
            "type": "ffmpeg_failed",
            "message": " ".join(stderr_tail[-10:]) or "FFmpeg failed",
            "returncode": proc.returncode,
        }


def _append_outro_events(
    main_video: Path,
    outro_video: Path,
    output_path: Path,
    temp_dir: Path,
    ffmpeg: str,
    ffprobe: str,
    video_encoder: str,
    quality: str,
):
    main_duration = get_video_duration(main_video, ffprobe)
    outro_duration = get_video_duration(outro_video, ffprobe)
    if main_duration <= 0:
        yield {"type": "error", "message": "Không đọc được độ dài video chính"}
        return
    if outro_duration <= 0:
        yield {"type": "error", "message": "Không đọc được độ dài outro"}
        return

    info = _video_stream_info(main_video, ffprobe)
    width, height, fps = info["width"], info["height"], info["fps"]
    encoder_args, encoder_label = _encoder_args(video_encoder, quality)
    temp_dir.mkdir(parents=True, exist_ok=True)
    normalized_outro = temp_dir / f"outro_normalized_{uuid.uuid4().hex}.mp4"
    main_size = main_video.stat().st_size if main_video.exists() else 0
    outro_size = outro_video.stat().st_size if outro_video.exists() else 0
    total = main_duration + outro_duration
    estimated_output = int(max(main_size + outro_size, main_size * (total / main_duration)) * 1.1) + 256 * 1024 * 1024
    estimated_temp = max(outro_size * 2, 512 * 1024 * 1024)
    space_error = _check_free_space([
        (output_path.parent, estimated_output),
        (temp_dir, estimated_temp),
    ])
    if space_error:
        yield {"type": "error", "message": space_error}
        return

    yield {
        "type": "step",
        "message": f"Chuẩn hóa outro bằng {encoder_label}...",
        "percent": 1,
        "encoder_label": encoder_label,
    }

    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={fps:.3f},setsar=1"
    )
    if _has_audio(outro_video, ffprobe):
        normalize_cmd = [
            ffmpeg, "-y",
            "-i", str(outro_video),
            "-vf", vf,
            *encoder_args,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-progress", "pipe:2",
            "-nostats",
            str(normalized_outro),
        ]
    else:
        normalize_cmd = [
            ffmpeg, "-y",
            "-i", str(outro_video),
            "-f", "lavfi", "-t", str(outro_duration), "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-vf", vf,
            *encoder_args,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-map", "0:v:0", "-map", "1:a:0",
            "-shortest",
            "-progress", "pipe:2",
            "-nostats",
            str(normalized_outro),
        ]
    failed_message = ""
    for event in _run_process_progress(normalize_cmd, normalized_outro, outro_duration, 1, 29, "Đang chuẩn hóa outro..."):
        if event.get("type") == "ffmpeg_failed":
            failed_message = event.get("message", "")
        else:
            yield event
    if failed_message or not normalized_outro.exists() or normalized_outro.stat().st_size == 0:
        if video_encoder != "cpu_x264":
            yield {"type": "step", "message": "GPU encoder lỗi, chuyển sang CPU libx264...", "percent": 5}
            yield from _append_outro_events(main_video, outro_video, output_path, temp_dir, ffmpeg, ffprobe, "cpu_x264", quality)
            return
        yield {"type": "error", "message": failed_message or "Chuẩn hóa outro thất bại"}
        return

    yield {"type": "step", "message": "Đang ghép outro vào cuối video...", "percent": 35}
    concat_filter = (
        f"[0:v]setpts=PTS-STARTPTS,"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={fps:.3f},setsar=1,format=yuv420p[v0];"
        f"[1:v]setpts=PTS-STARTPTS,"
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={fps:.3f},setsar=1,format=yuv420p[v1];"
        "[0:a]asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo[a0];"
        "[1:a]asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo[a1];"
        "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"
    )
    concat_cmd = [
        ffmpeg, "-y",
        "-i", str(main_video),
        "-i", str(normalized_outro),
        "-filter_complex", concat_filter,
        "-map", "[v]",
        "-map", "[a]",
        *encoder_args,
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        "-progress", "pipe:2",
        "-nostats",
        str(output_path),
    ]
    failed_message = ""
    for event in _run_process_progress(concat_cmd, output_path, total, 35, 64, "Đang ghép outro..."):
        if event.get("type") == "ffmpeg_failed":
            failed_message = event.get("message", "")
        else:
            yield event
    if failed_message or not output_path.exists() or output_path.stat().st_size == 0:
        yield {"type": "error", "message": failed_message or "Ghép outro thất bại"}
        return

    actual_duration = get_video_duration(output_path, ffprobe)
    if actual_duration <= 0:
        actual_duration = total
    elif actual_duration < total * 0.8 or actual_duration > total * 1.2:
        yield {
            "type": "error",
            "message": (
                "Output outro có duration không hợp lệ "
                f"({actual_duration:.1f}s, kỳ vọng khoảng {total:.1f}s)."
            ),
        }
        return

    yield {
        "type": "done",
        "percent": 100,
        "filename": output_path.name,
        "saved_path": str(output_path),
        "size_mb": round(output_path.stat().st_size / (1024 * 1024), 2),
        "duration_sec": round(actual_duration, 3),
    }


@app.post("/api/video-cutter/upload")
async def video_cutter_upload(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()
    if ext not in VIDEO_EXTS:
        raise HTTPException(400, f"Định dạng không hỗ trợ. Chấp nhận: {', '.join(sorted(VIDEO_EXTS))}")

    VIDEO_CUTTER_UPLOADS.mkdir(parents=True, exist_ok=True)
    upload_id = str(uuid.uuid4())
    original_stem = _safe_stem(Path(file.filename).stem, "source")
    stored_name = f"{upload_id}_{original_stem}{ext}"
    stored_path = VIDEO_CUTTER_UPLOADS / stored_name

    content = await file.read()
    async with aiofiles.open(stored_path, "wb") as f:
        await f.write(content)

    cfg = load_config()
    ffprobe = cfg.get("ffprobe_path", "ffprobe")
    duration = get_video_duration(stored_path, ffprobe)
    meta = {
        "id": upload_id,
        "original_name": file.filename,
        "stored_name": stored_name,
        "path": str(stored_path),
        "duration_sec": duration,
        "size_mb": round(stored_path.stat().st_size / (1024 * 1024), 2),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    async with aiofiles.open(_video_cutter_meta_path(upload_id), "w", encoding="utf-8") as f:
        await f.write(json.dumps(meta, ensure_ascii=False, indent=2))
    return meta


@app.post("/api/video-cutter/project-video")
async def video_cutter_project_video(body: VideoCutProjectRequest):
    source = _project_video_path(body.project, body.filename)
    cfg = load_config()
    ffprobe = cfg.get("ffprobe_path", "ffprobe")
    duration = get_video_duration(source, ffprobe)
    upload_id = str(uuid.uuid4())
    meta = {
        "id": upload_id,
        "original_name": source.name,
        "stored_name": source.name,
        "path": str(source),
        "duration_sec": duration,
        "size_mb": round(source.stat().st_size / (1024 * 1024), 2),
        "source_project": body.project,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    VIDEO_CUTTER_UPLOADS.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(_video_cutter_meta_path(upload_id), "w", encoding="utf-8") as f:
        await f.write(json.dumps(meta, ensure_ascii=False, indent=2))
    return meta


@app.post("/api/video-cutter/cut")
def video_cutter_cut(body: VideoCutRequest):
    meta_path = _video_cutter_meta_path(body.upload_id)
    if not meta_path.exists():
        raise HTTPException(404, "Upload not found")
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    source = Path(meta["path"])
    try:
        source_resolved = source.resolve()
        allowed = (
            source_resolved.is_relative_to(VIDEO_CUTTER_UPLOADS.resolve()) or
            source_resolved.is_relative_to(OUTPUT_DIR.resolve())
        )
    except Exception:
        allowed = False
    if not source.exists() or not allowed:
        raise HTTPException(404, "Source video not found")

    segment_sec = int(body.minutes) * 60 + int(body.seconds)
    if segment_sec <= 0:
        raise HTTPException(400, "Thời lượng cắt phải lớn hơn 0")

    base = _safe_stem(body.base_name, "video")
    duration = float(meta.get("duration_sec") or 0)
    if duration <= 0:
        cfg = load_config()
        duration = get_video_duration(source, cfg.get("ffprobe_path", "ffprobe"))
    if duration <= 0:
        raise HTTPException(400, "Không đọc được độ dài video")

    output_dir = _resolve_video_cutter_output_dir(body.output_dir)
    default_output_dir = VIDEO_CUTTER_OUTPUTS.resolve()
    use_download_url = output_dir.resolve() == default_output_dir
    total = max(1, math.ceil(duration / segment_sec))
    cfg = load_config()
    ffmpeg = cfg.get("ffmpeg_path", "ffmpeg")

    def event_stream():
        outputs = []
        yield f"data: {json.dumps({'type': 'start', 'total': total, 'duration_sec': duration}, ensure_ascii=False)}\n\n"
        for i in range(total):
            start = i * segment_sec
            length = min(segment_sec, max(0.0, duration - start))
            if length <= 0:
                break
            filename = f"{base}_{i + 1}.mp4"
            dest = output_dir / filename
            yield f"data: {json.dumps({'type': 'segment_start', 'index': i + 1, 'total': total, 'filename': filename, 'start_sec': start, 'duration_sec': length}, ensure_ascii=False)}\n\n"

            cmd = [
                ffmpeg, "-y",
                "-ss", str(start),
                "-i", str(source),
                "-t", str(length),
                "-map", "0",
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                str(dest),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
                fallback = [
                    ffmpeg, "-y",
                    "-ss", str(start),
                    "-i", str(source),
                    "-t", str(length),
                    "-map", "0:v:0",
                    "-map", "0:a?",
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-crf", "23",
                    "-c:a", "aac",
                    "-b:a", "160k",
                    str(dest),
                ]
                result = subprocess.run(fallback, capture_output=True, text=True)
            if result.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
                message = (result.stderr or result.stdout or "FFmpeg split failed")[-1000:]
                yield f"data: {json.dumps({'type': 'error', 'message': message, 'index': i + 1}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                return

            item = {
                "filename": filename,
                "size_mb": round(dest.stat().st_size / (1024 * 1024), 2),
                "duration_sec": length,
                "saved_path": str(dest),
                "output_dir": str(output_dir),
            }
            if use_download_url:
                item["url"] = f"/api/video-cutter/download/{filename}"
            outputs.append(item)
            yield f"data: {json.dumps({'type': 'segment_done', 'index': i + 1, 'total': total, 'percent': int(((i + 1) / total) * 100), 'file': item}, ensure_ascii=False)}\n\n"

        yield f"data: {json.dumps({'type': 'done', 'files': outputs}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/video-cutter/download/{filename}")
def video_cutter_download(filename: str):
    if not re.match(r"^[\w\-]+_\d+\.mp4$", filename, re.IGNORECASE):
        raise HTTPException(400, "Invalid filename")
    path = VIDEO_CUTTER_OUTPUTS / filename
    if not path.exists() or not str(path.resolve()).startswith(str(VIDEO_CUTTER_OUTPUTS.resolve())):
        raise HTTPException(404, "File not found")
    return FileResponse(str(path), media_type="video/mp4", filename=filename)


@app.post("/api/audio-cutter/upload")
async def audio_cutter_upload(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in AUDIO_EXTS:
        raise HTTPException(400, f"Định dạng không hỗ trợ. Chấp nhận: {', '.join(sorted(AUDIO_EXTS))}")

    AUDIO_CUTTER_UPLOADS.mkdir(parents=True, exist_ok=True)
    upload_id = str(uuid.uuid4())
    stored_name = f"{upload_id}_{_safe_stem(Path(file.filename or '').stem, 'audio')}{ext}"
    stored_path = AUDIO_CUTTER_UPLOADS / stored_name
    content = await file.read()
    async with aiofiles.open(stored_path, "wb") as handle:
        await handle.write(content)

    cfg = load_config()
    duration = get_video_duration(stored_path, cfg.get("ffprobe_path", "ffprobe"))
    if duration <= 0:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(400, "Không đọc được độ dài audio")
    meta = {
        "id": upload_id,
        "original_name": file.filename,
        "stored_name": stored_name,
        "path": str(stored_path),
        "duration_sec": duration,
        "size_mb": round(stored_path.stat().st_size / (1024 * 1024), 2),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    async with aiofiles.open(_audio_cutter_meta_path(upload_id), "w", encoding="utf-8") as handle:
        await handle.write(json.dumps(meta, ensure_ascii=False, indent=2))
    return meta


@app.post("/api/audio-cutter/cut")
def audio_cutter_cut(body: AudioCutRequest):
    meta_path = _audio_cutter_meta_path(body.upload_id)
    if not meta_path.exists():
        raise HTTPException(404, "Audio upload not found")
    meta = _load_story_session(meta_path)
    source = Path(meta.get("path") or "")
    try:
        allowed = source.resolve().is_relative_to(AUDIO_CUTTER_UPLOADS.resolve())
    except Exception:
        allowed = False
    if not source.is_file() or not allowed:
        raise HTTPException(404, "Source audio not found")

    duration = float(meta.get("duration_sec") or 0)
    start = max(0.0, float(body.start_sec or 0))
    end = min(duration, float(body.end_sec or 0))
    if duration <= 0 or end <= start:
        raise HTTPException(400, "Khoảng cắt audio không hợp lệ")

    output = _unique_audio_output_path(body.output_name, body.output_format)
    cfg = load_config()
    ffmpeg = cfg.get("ffmpeg_path", "ffmpeg")
    fmt = output.suffix.lower()
    codec_args = ["-c:a", "libmp3lame", "-q:a", "2"]
    if fmt == ".wav":
        codec_args = ["-c:a", "pcm_s16le"]
    elif fmt == ".m4a":
        codec_args = ["-c:a", "aac", "-b:a", "192k"]
    command = [
        ffmpeg, "-y", "-i", str(source), "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
        "-vn", *codec_args, "-movflags", "+faststart", str(output),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0 or not output.exists() or output.stat().st_size <= 0:
        output.unlink(missing_ok=True)
        raise HTTPException(500, (result.stderr or result.stdout or "FFmpeg audio cut failed")[-1000:])
    actual_duration = get_video_duration(output, cfg.get("ffprobe_path", "ffprobe"))
    return {
        "ok": True,
        "filename": output.name,
        "duration_sec": round(actual_duration or (end - start), 3),
        "size_mb": round(output.stat().st_size / (1024 * 1024), 2),
        "url": f"/api/audio-cutter/download/{output.name}",
    }


@app.get("/api/audio-cutter/download/{filename}")
def audio_cutter_download(filename: str):
    if not re.match(r"^[\w\-]+(?:_\d+)?\.(mp3|wav|m4a)$", filename, re.IGNORECASE):
        raise HTTPException(400, "Invalid filename")
    path = AUDIO_CUTTER_OUTPUTS / filename
    try:
        allowed = path.resolve().is_relative_to(AUDIO_CUTTER_OUTPUTS.resolve())
    except Exception:
        allowed = False
    if not path.is_file() or not allowed:
        raise HTTPException(404, "File not found")
    media_type = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(str(path), media_type=media_type, filename=filename)


@app.post("/api/outro/upload")
async def upload_outro(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()
    if ext not in VIDEO_EXTS:
        raise HTTPException(400, f"Định dạng không hỗ trợ. Chấp nhận: {', '.join(sorted(VIDEO_EXTS))}")
    OUTRO_DIR.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(Path(file.filename).stem, "outro")
    path = OUTRO_DIR / f"{stem}{ext}"
    i = 2
    while path.exists():
        path = OUTRO_DIR / f"{stem}_{i}{ext}"
        i += 1
    content = await file.read()
    async with aiofiles.open(path, "wb") as f:
        await f.write(content)
    cfg = load_config()
    cfg["outro_video_path"] = str(path)
    save_config(cfg)
    return {
        "ok": True,
        "filename": path.name,
        "path": str(path),
        "size_mb": round(path.stat().st_size / (1024 * 1024), 2),
        "duration_sec": get_video_duration(path, cfg.get("ffprobe_path", "ffprobe")),
    }


@app.post("/api/outro/append")
def append_outro(body: AppendOutroRequest):
    main_video = _project_video_path(body.project, body.input_video)
    outro_value = (body.outro_path or "").strip().strip('"')
    if not outro_value:
        outro_value = load_config().get("outro_video_path", "")
    if not outro_value:
        raise HTTPException(400, "Outro video is required")
    outro_video = Path(outro_value).expanduser()
    if not outro_video.is_absolute():
        outro_video = (BASE_DIR / outro_video).resolve()
    if not outro_video.exists() or outro_video.suffix.lower() not in VIDEO_EXTS:
        raise HTTPException(404, "Outro video not found")
    output_dir = _resolve_required_output_dir(body.output_dir)
    output_path = _unique_output_path(output_dir, body.output_name, body.overwrite_mode)
    cfg = load_config()
    ffmpeg = cfg.get("ffmpeg_path", "ffmpeg")
    ffprobe = cfg.get("ffprobe_path", "ffprobe")

    def event_stream():
        try:
            for event in _append_outro_events(
                main_video=main_video,
                outro_video=outro_video,
                output_path=output_path,
                temp_dir=TEMP_DIR,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                video_encoder=body.video_encoder,
                quality=body.quality,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") in {"done", "error"}:
                    break
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/audio/duration")
def get_audio_duration(project: str, filename: str):
    path = get_project_dir(project) / filename
    if not path.exists():
        raise HTTPException(404, "File not found")
    if not str(path.resolve()).startswith(str(OUTPUT_DIR.resolve())):
        raise HTTPException(403, "Access denied")
    cfg = load_config()
    ffprobe = cfg.get("ffprobe_path", "ffprobe")
    from video_service import get_video_duration
    dur = get_video_duration(path, ffprobe)
    return {"duration_sec": dur}


@app.get("/api/metadata/{project}")
def get_metadata(project: str):
    if not _SAFE_NAME.match(project):
        raise HTTPException(400, "Invalid project name")
    path = get_project_dir(project) / "metadata.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@app.post("/api/metadata/{project}")
async def save_metadata(project: str, body: dict):
    if not _SAFE_NAME.match(project):
        raise HTTPException(400, "Invalid project name")
    project_dir = get_project_dir(project)
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / "metadata.json"
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(json.dumps(body, ensure_ascii=False, indent=2))
    return {"ok": True}


# ── Frontend SPA ──────────────────────────────────────────────────────────────

FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        return FileResponse(str(FRONTEND_DIST / "index.html"))
# Browser video previews must support byte-range requests. Returning the entire
# MP4 for every seek makes long local renders appear to stop after a few seconds.
_VIDEO_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
_VIDEO_CHUNK_SIZE = 1024 * 1024


def _video_file_response(path: Path, request: Request) -> Response:
    size = path.stat().st_size
    # Header values are Latin-1 encoded by Starlette. A raw Vietnamese filename
    # therefore causes a 500 before the browser can receive the MP4. Keep an
    # ASCII fallback and carry the real name via RFC 5987 UTF-8 encoding.
    encoded_filename = quote(path.name, safe="")
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'inline; filename="video.mp4"; filename*=UTF-8\'\'{encoded_filename}',
    }
    range_header = request.headers.get("range", "").strip()
    if not range_header:
        return FileResponse(str(path), media_type="video/mp4", headers=headers)
    match = _VIDEO_RANGE_RE.match(range_header)
    if not match:
        return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
    start_raw, end_raw = match.groups()
    try:
        if start_raw:
            start = int(start_raw)
            end = min(int(end_raw) if end_raw else size - 1, size - 1)
        else:
            suffix = int(end_raw)
            start = max(0, size - suffix)
            end = size - 1
    except ValueError:
        return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
    if start < 0 or start >= size or end < start:
        return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
    length = end - start + 1

    def stream_range():
        with open(path, "rb") as video:
            video.seek(start)
            remaining = length
            while remaining > 0:
                chunk = video.read(min(_VIDEO_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        stream_range(),
        status_code=206,
        media_type="video/mp4",
        headers={
            **headers,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(length),
        },
    )
