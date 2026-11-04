import base64
import json
import math
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel

from story_service import generate_text, generate_text_openai_compatible, _parse_json_response
from subtitle_service import build_subtitle_cues, load_timing_file
from tts_service import OUTPUT_DIR, PROJECT_TITLE_VALIDATOR, get_project_dir, load_config, save_config, channel_profile_block
from video_service import get_video_duration
from image_generation_service import generate_image_file, image_provider_config
from stick_figure_packaging_service import load_stick_figure_packaging, stick_figure_packaging_is_current


SAFE_NAME = PROJECT_TITLE_VALIDATOR
SAFE_VIDEO = re.compile(r"^[\w.\- ]+\.mp4$", re.IGNORECASE)
PUBLISH_DIR = OUTPUT_DIR / "_shared" / "youtube"
TOKEN_PATH = PUBLISH_DIR / "token.json"
STATE_PATH = PUBLISH_DIR / "oauth_state.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"]


class PublishSource(BaseModel):
    filename: str = ""
    path: str = ""


class PublishPlanCreateRequest(BaseModel):
    project: str
    story_title: str
    story_text: str = ""
    model: str = ""
    long_video: PublishSource | None = None
    short_videos: list[PublishSource] = []
    timezone: str = "Asia/Ho_Chi_Minh"
    long_metadata: "PublishItemUpdateRequest | None" = None  # caller-written metadata; skips the LLM


class PublishItemUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    thumbnail_text: str | None = None
    scheduled_at: str | None = None
    status: str | None = None
    language: str | None = None  # BCP-47, sets defaultLanguage + defaultAudioLanguage
    category_id: str | None = None
    caption_file: str | None = None  # bare .srt name inside <project>/captions
    localizations: dict[str, dict[str, str]] | None = None  # {"en": {"title": ..., "description": ...}}


PublishPlanCreateRequest.model_rebuild()
SAFE_CAPTION = re.compile(r"^[\w.\- ]+\.srt$", re.IGNORECASE)


class PublishMetadataRequest(BaseModel):
    project: str
    item_id: str
    model: str = ""


class PublishThumbnailRequest(BaseModel):
    project: str
    item_id: str
    model: str = ""


class VideoDescriptionRequest(BaseModel):
    project: str = ""
    story_title: str = ""
    story_text: str = ""
    model: str = ""


class VideoDescriptionSaveRequest(BaseModel):
    project: str
    description: str = ""


class PublishUploadRequest(BaseModel):
    project: str
    item_id: str
    mode: str = "scheduled"


def _cfg_defaults(cfg: dict) -> dict:
    cfg.setdefault("youtube_client_secrets_path", "")
    cfg.setdefault("youtube_timezone", "Asia/Ho_Chi_Minh")
    cfg.setdefault("youtube_long_slots", ["20:00"])
    cfg.setdefault("youtube_short_slots", ["12:00", "21:30"])
    cfg.setdefault("youtube_min_gap_hours", 6)
    cfg.setdefault("youtube_max_long_per_day", 1)
    cfg.setdefault("youtube_max_shorts_per_day", 2)
    cfg.setdefault("youtube_campaign_mode", False)
    cfg.setdefault("youtube_campaign_long_slots", ["20:00"])
    cfg.setdefault("youtube_campaign_short_slots", ["12:00", "20:00"])
    cfg.setdefault("youtube_metadata_provider", "default")
    cfg.setdefault("youtube_metadata_base_url", "https://api.openai.com")
    cfg.setdefault("youtube_metadata_path", "/v1/chat/completions")
    cfg.setdefault("youtube_metadata_model", "")
    cfg.setdefault("youtube_metadata_api_key", "")
    cfg.setdefault("youtube_metadata_api_key_path", "")
    cfg.setdefault("gpt2api_base_url", "http://127.0.0.1:7860/v1/images/generations")
    cfg.setdefault("gpt2api_image_model", "")
    cfg.setdefault("image_generation_provider", "gpt2api")
    cfg.setdefault("custom_image_base_url", "https://api.openai.com")
    cfg.setdefault("custom_image_path", "/v1/images/edits")
    cfg.setdefault("custom_image_api_key", "")
    cfg.setdefault("custom_image_api_key_path", "")
    cfg.setdefault("custom_image_model", "gpt-image-1")
    cfg.setdefault("custom_image_size", "1536x1024")
    cfg.setdefault("thumbnail_image_size", "1280x720")
    return cfg


def _generate_youtube_text(cfg: dict, system: str, user: str, model_override: str = "", max_tokens: int = 1600) -> str:
    """Use the dedicated YouTube metadata provider for descriptions and campaign copy."""
    provider = (cfg.get("youtube_metadata_provider") or "default").strip().lower()
    configured_model = (cfg.get("youtube_metadata_model") or "").strip()
    if provider == "custom":
        return generate_text_openai_compatible(
            system,
            user,
            base_url=cfg.get("youtube_metadata_base_url") or "https://api.openai.com",
            path=cfg.get("youtube_metadata_path") or "/v1/chat/completions",
            model=(model_override or configured_model or "gpt-4o-mini").strip(),
            api_key=cfg.get("youtube_metadata_api_key") or "",
            api_key_path=cfg.get("youtube_metadata_api_key_path") or "",
            max_tokens=max_tokens,
        )
    model = (model_override or configured_model or cfg.get("default_story_model") or "claude-sonnet-4-6").strip()
    return generate_text(
        system,
        user,
        model,
        cfg.get("anthropic_api_key", ""),
        cfg.get("ollama_url", "http://localhost:11434"),
        max_tokens,
        cli_timeout_sec=75,
    )


def _project_dir(project: str) -> Path:
    if not project or not SAFE_NAME.match(project):
        raise HTTPException(400, "Invalid project name")
    path = get_project_dir(project)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _plan_path(project: str) -> Path:
    return _project_dir(project) / "publish_plan.json"


def _post_production_path(project: str) -> Path:
    return _project_dir(project) / "post_production.json"


def _load_post_production(project: str) -> dict:
    path = _post_production_path(project)
    if not path.exists():
        return {"project": project, "video_description": "", "updated_at": ""}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"project": project, "video_description": "", "updated_at": ""}
    except Exception:
        return {"project": project, "video_description": "", "updated_at": ""}


def _save_post_production(project: str, data: dict) -> dict:
    data = {**data, "project": project, "updated_at": datetime.now().isoformat(timespec="seconds")}
    path = _post_production_path(project)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def _load_plan(project: str) -> dict:
    path = _plan_path(project)
    if not path.exists():
        return {"project": project, "items": [], "created_at": "", "updated_at": ""}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_plan(plan: dict) -> dict:
    plan["updated_at"] = datetime.now().isoformat(timespec="seconds")
    path = _plan_path(plan["project"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    return plan


def _resolve_source(project: str, source: PublishSource) -> Path:
    project_dir = _project_dir(project)
    if source.path:
        path = Path(source.path.strip().strip('"')).expanduser()
        if not path.is_absolute():
            path = (project_dir / path).resolve()
    elif source.filename:
        if not SAFE_VIDEO.match(source.filename):
            raise HTTPException(400, "Invalid video filename")
        path = project_dir / source.filename
    else:
        raise HTTPException(400, "Missing source video")
    if not path.exists() or path.suffix.lower() != ".mp4":
        raise HTTPException(404, f"Video not found: {path}")
    return path.resolve()


def _parse_iso(value: str, tz: ZoneInfo) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _slot_datetime(day: datetime, hhmm: str, tz: ZoneInfo) -> datetime:
    hour, minute = [int(x) for x in hhmm.split(":", 1)]
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)


def _counts_for_day(existing: list[dict], day: datetime) -> tuple[int, int]:
    long_count = 0
    short_count = 0
    for item in existing:
        dt = item["dt"]
        if dt.date() != day.date():
            continue
        if item.get("kind") == "short":
            short_count += 1
        else:
            long_count += 1
    return long_count, short_count


def _has_gap(existing: list[dict], candidate: datetime, min_gap_hours: float) -> bool:
    gap = timedelta(hours=min_gap_hours)
    return all(abs(item["dt"] - candidate) >= gap for item in existing)


def _next_weekday(now: datetime, weekday: int, slot: str, tz: ZoneInfo) -> datetime:
    """Next occurrence of `weekday` (Mon=0) at slot time, strictly after now+1h."""
    ahead = (weekday - now.weekday()) % 7
    candidate = _slot_datetime(now + timedelta(days=ahead), slot, tz)
    if candidate <= now + timedelta(hours=1):
        candidate = _slot_datetime(now + timedelta(days=ahead + 7), slot, tz)
    return candidate


def _schedule_items_campaign(kinds: list[str], cfg: dict, remote_existing: list[dict]) -> list[str]:
    """Fixed weekly campaign: 2 longs + up to 4 shorts, shorts flanking each long.

    Pattern per week (Mon=0): Mon teaser · Tue long1 · Wed climax ·
    Fri teaser · Sat long2 · Sun climax. Overflow items roll to the next week.
    """
    tz = ZoneInfo(cfg.get("youtube_timezone") or "Asia/Ho_Chi_Minh")
    long_slots = cfg.get("youtube_campaign_long_slots") or ["20:00"]
    short_slots = cfg.get("youtube_campaign_short_slots") or ["12:00", "20:00"]
    long_slot = long_slots[0]
    teaser_slot = short_slots[0]
    climax_slot = short_slots[1] if len(short_slots) > 1 else short_slots[0]
    # (weekday, slot) sequence for shorts and longs, in publish order.
    short_beats = [(0, teaser_slot), (2, climax_slot), (4, teaser_slot), (6, climax_slot)]
    long_beats = [(1, long_slot), (5, long_slot)]
    now = datetime.now(tz)
    short_i = 0
    long_i = 0
    min_gap = float(cfg.get("youtube_min_gap_hours") or 6)
    existing = list(remote_existing)
    result = []
    for kind in kinds:
        if kind == "short":
            beat = short_beats[short_i % len(short_beats)]
            week = short_i // len(short_beats)
            short_i += 1
        else:
            beat = long_beats[long_i % len(long_beats)]
            week = long_i // len(long_beats)
            long_i += 1
        placed = _next_weekday(now, beat[0], beat[1], tz) + timedelta(weeks=week)
        while not _has_gap(existing, placed, min_gap):
            placed += timedelta(weeks=1)
        existing.append({"dt": placed, "kind": kind})
        result.append(placed.isoformat())
    return result


def _schedule_items(kinds: list[str], cfg: dict, remote_existing: list[dict]) -> list[str]:
    if cfg.get("youtube_campaign_mode"):
        return _schedule_items_campaign(kinds, cfg, remote_existing)
    tz = ZoneInfo(cfg.get("youtube_timezone") or "Asia/Ho_Chi_Minh")
    min_gap = float(cfg.get("youtube_min_gap_hours") or 6)
    max_long = int(cfg.get("youtube_max_long_per_day") or 1)
    max_short = int(cfg.get("youtube_max_shorts_per_day") or 2)
    long_slots = cfg.get("youtube_long_slots") or ["20:00"]
    short_slots = cfg.get("youtube_short_slots") or ["12:00", "21:30"]
    existing = list(remote_existing)
    now = datetime.now(tz)
    result = []
    for kind in kinds:
        slots = short_slots if kind == "short" else long_slots
        placed = None
        for day_offset in range(0, 180):
            day = now + timedelta(days=day_offset)
            long_count, short_count = _counts_for_day(existing, day)
            if kind == "short" and short_count >= max_short:
                continue
            if kind != "short" and long_count >= max_long:
                continue
            for slot in slots:
                candidate = _slot_datetime(day, slot, tz)
                if candidate <= now + timedelta(hours=1):
                    continue
                if not _has_gap(existing, candidate, min_gap):
                    continue
                placed = candidate
                break
            if placed:
                break
        if not placed:
            placed = now + timedelta(days=len(result) + 1, hours=2)
        existing.append({"dt": placed, "kind": kind})
        result.append(placed.isoformat())
    return result


def schedule_campaign_triplet(long_item: dict, short_items: list[dict], cfg: dict, existing: list[dict]) -> None:
    """Schedule a teaser/long/climax set as one conflict-free campaign unit.

    The generated Shorts are created after the long-item plan, so they cannot use the
    generic flat scheduler. Keep their timestamps tied to their parent long video.
    """
    tz = ZoneInfo(cfg.get("youtube_timezone") or "Asia/Ho_Chi_Minh")
    now = datetime.now(tz)
    minimum = now + timedelta(hours=1)
    min_gap = float(cfg.get("youtube_min_gap_hours") or 6)
    long_slot = (cfg.get("youtube_campaign_long_slots") or ["20:00"])[0]
    short_slots = cfg.get("youtube_campaign_short_slots") or ["12:00", "20:00"]
    teaser_slot = short_slots[0]
    climax_slot = short_slots[1] if len(short_slots) > 1 else short_slots[0]

    preferred = None
    if long_item.get("scheduled_at"):
        try:
            preferred = _parse_iso(long_item["scheduled_at"], tz)
        except (TypeError, ValueError):
            preferred = None

    candidates: list[datetime] = []
    if preferred and preferred > minimum:
        candidates.append(preferred)
    # Preserve the configured Tue/Sat cadence when the existing long slot cannot form a full triplet.
    for day_offset in range(0, 180):
        day = now + timedelta(days=day_offset)
        if day.weekday() in {1, 5}:
            candidate = _slot_datetime(day, long_slot, tz)
            if candidate > minimum and all(abs(candidate - item) > timedelta(minutes=1) for item in candidates):
                candidates.append(candidate)

    occupied = list(existing)
    for long_at in candidates:
        teaser_at = _slot_datetime(long_at - timedelta(days=1), teaser_slot, tz)
        climax_at = _slot_datetime(long_at + timedelta(days=1), climax_slot, tz)
        triplet = [(teaser_at, "short"), (long_at, "long"), (climax_at, "short")]
        if any(at <= minimum for at, _ in triplet):
            continue
        test_occupied = list(occupied)
        if not all(_has_gap(test_occupied + [{"dt": before, "kind": before_kind} for before, before_kind in triplet[:index]], at, min_gap)
                   for index, (at, _) in enumerate(triplet)):
            continue
        long_item["scheduled_at"] = long_at.isoformat()
        long_item["timezone"] = tz.key
        for item in short_items:
            item["scheduled_at"] = (teaser_at if item.get("role") == "teaser" else climax_at).isoformat()
            item["timezone"] = tz.key
        return
    raise RuntimeError("Could not find a conflict-free teaser/long/climax schedule in the next 180 days")


def _youtube_deps():
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import Flow
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except Exception as e:
        raise HTTPException(500, f"Missing YouTube dependencies. Run pip install -r backend/requirements.txt. {e}")
    return Credentials, Flow, build, MediaFileUpload


def _credentials():
    Credentials, _, _, _ = _youtube_deps()
    if not TOKEN_PATH.exists():
        raise HTTPException(401, "YouTube is not connected")
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        from google.auth.transport.requests import Request as GoogleRequest
        creds.refresh(GoogleRequest())
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    if not creds or not creds.valid:
        raise HTTPException(401, "YouTube token is invalid")
    return creds


def _youtube_service():
    _, _, build, _ = _youtube_deps()
    return build("youtube", "v3", credentials=_credentials())


def _remote_schedule(cfg: dict) -> list[dict]:
    try:
        yt = _youtube_service()
        search = yt.search().list(part="id", forMine=True, type="video", order="date", maxResults=50).execute()
        ids = [x["id"]["videoId"] for x in search.get("items", []) if x.get("id", {}).get("videoId")]
        if not ids:
            return []
        videos = yt.videos().list(part="snippet,status", id=",".join(ids), maxResults=50).execute()
        tz = ZoneInfo(cfg.get("youtube_timezone") or "Asia/Ho_Chi_Minh")
        existing = []
        for item in videos.get("items", []):
            snippet = item.get("snippet", {})
            status = item.get("status", {})
            raw = status.get("publishAt") or snippet.get("publishedAt")
            if not raw:
                continue
            title = snippet.get("title", "")
            kind = "short" if "#short" in title.lower() else "long"
            existing.append({"dt": _parse_iso(raw, tz), "kind": kind})
        return existing
    except HTTPException:
        return []
    except Exception:
        return []


def _local_schedule(project: str, cfg: dict) -> list[dict]:
    tz = ZoneInfo(cfg.get("youtube_timezone") or "Asia/Ho_Chi_Minh")
    existing = []
    for path in OUTPUT_DIR.glob("*/publish_plan.json"):
        # Rebuilding or extending this project must not reserve its own old draft slots.
        if path.parent.name == project:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                plan = json.load(f)
            for item in plan.get("items", []):
                if item.get("scheduled_at") and item.get("status") in {"approved", "uploading", "scheduled"}:
                    existing.append({"dt": _parse_iso(item["scheduled_at"], tz), "kind": item.get("kind", "long")})
        except Exception:
            continue
    return existing


def _clean_json_text(text: str) -> dict:
    parsed = _parse_json_response(text)
    if isinstance(parsed, dict):
        return parsed
    return {}


def _default_metadata(story_title: str, kind: str, index: int = 0) -> dict:
    # No "đoạn N"/P1/P2 patterns — those signal "watch other parts first" and hurt clicks.
    if kind == "short":
        title = story_title
        description = f"{story_title}\n\nXem trọn câu chuyện trong video đầy đủ trên kênh.\n\n#Shorts #truyenma #kechuyen"
    else:
        title = story_title
        description = f"Truyện audio: {story_title}\n\n#truyenma #kechuyen #audiostory"
    return {
        "title": title[:95],
        "description": description,
        "tags": ["truyen ma", "ke chuyen", "audio story"],
        "thumbnail_text": "",
    }


def _strip_banned_title_patterns(title: str, cfg: dict) -> str:
    """Backstop: remove banned literals the LLM was told to avoid but may still emit."""
    # ponytail: literal substring strip is the reliable backstop; add regex only if too blunt.
    for pat in re.split(r"[,\n]", cfg.get("channel_banned_title_patterns") or ""):
        pat = pat.strip()
        if pat:
            title = re.sub(re.escape(pat), " ", title, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", title).strip(" -|·—").strip()


def _banned_hashtags(cfg: dict) -> set[str]:
    return {
        item.strip().lstrip("#").casefold()
        for item in re.split(r"[,\n]", cfg.get("channel_banned_hashtags") or "")
        if item.strip().lstrip("#")
    }


def _strip_banned_hashtags(description: str, tags: list[str], cfg: dict) -> tuple[str, list[str]]:
    """Apply the channel's hashtag ban deterministically after LLM generation."""
    banned = _banned_hashtags(cfg)
    if not banned:
        return description, tags
    filtered_tags = [tag for tag in tags if str(tag).strip().lstrip("#").casefold() not in banned]
    for tag in banned:
        description = re.sub(rf"(?<![\w#])#{re.escape(tag)}\b", "", description, flags=re.IGNORECASE)
    description = re.sub(r"[ \t]+\n", "\n", description)
    description = re.sub(r"\n{3,}", "\n\n", description).strip()
    return description, filtered_tags


def _item_context(plan: dict, item: dict) -> str:
    text = plan.get("story_text") or ""
    if item.get("kind") != "short":
        return text[:12000]
    cues = plan.get("subtitle_cues") or []
    if not cues:
        return text[:3000]
    segment = item.get("segment") or {}
    if segment:
        start_sec = float(segment.get("start_sec", 0) or 0)
        end_sec = float(segment.get("end_sec", start_sec) or start_sec)
        segment_cues = [
            cue for cue in cues
            if float(cue.get("end_sec", 0) or 0) > start_sec
            and float(cue.get("start_sec", 0) or 0) < end_sec
        ]
        if segment_cues:
            return " ".join(cue.get("text", "") for cue in segment_cues)[:5000]
    idx = max(0, int(item.get("index") or 1) - 1)
    total = max(1, int(plan.get("short_count") or 1))
    start = math.floor((len(cues) / total) * idx)
    end = math.ceil((len(cues) / total) * (idx + 1))
    return " ".join(c.get("text", "") for c in cues[start:end])[:5000]


_TITLE_FORMULA_RULES = (
    "Title rules (use the same language as the supplied story, <=95 chars, MUST start with the hook):\n"
    "- Use one of these formulas: (1) Địa điểm + điều phi lý, (2) Lời cảnh báo + hậu quả gây tò mò, "
    "(3) Nhân vật + việc làm ban đêm + phát hiện, (4) Vật quen thuộc + hành vi bất thường.\n"
    "- The mystery/curiosity must appear first. NO technical prefixes (e.g. [TRUYỆN AUDIO], ||, FULL, "
    "channel name, the words 'audio'/'full' at the start).\n"
    "- Do NOT write the whole title in ALL CAPS.\n"
)

_SHORT_META_RULES = (
    "This is a Short. The title must describe ONE self-contained situation that makes sense to a viewer "
    "who has NOT seen any other part. NEVER use 'P1', 'P2', 'P5', 'phần cuối', 'đoạn 3', or part numbers.\n"
    "End the description with one cliffhanger sentence + a call-to-action pointing viewers to the full long video on the channel.\n"
)


def _generate_metadata(plan: dict, item: dict, model: str) -> dict:
    cfg = _cfg_defaults(load_config())
    model_override = (model or "").strip()
    is_short = item.get("kind") == "short"
    system = (
        "You are a YouTube growth copywriter. Write every field in the same language as the supplied story. "
        "Return strict JSON only with keys title, description, tags, thumbnail_text. "
        "Tags must be 5-12 short strings in that language. "
        "thumbnail_text is 2-4 BIG words in that language for the thumbnail overlay (readable on a TV from across the room), "
        "not the full title.\n"
        + _TITLE_FORMULA_RULES
        + (_SHORT_META_RULES if is_short else "")
    )
    profile = channel_profile_block(cfg)
    if profile:
        system = f"{system}\n\n{profile}"
    user = (
        f"Story title: {plan.get('story_title')}\n"
        f"Video kind: {item.get('kind')}\n"
        f"Content excerpt:\n{_item_context(plan, item)}"
    )
    fallback = _default_metadata(plan.get("story_title") or plan.get("project"), item.get("kind", "long"), item.get("index", 0))
    generation_error = ""
    try:
        data = _clean_json_text(_generate_youtube_text(
            cfg,
            system,
            user,
            model_override,
            1600,
        ))
    except Exception as exc:
        data = {}
        generation_error = str(exc)
    title = str(data.get("title") or fallback["title"]).strip()
    title = _strip_banned_title_patterns(title, cfg) or fallback["title"]
    title = title[:95]
    description = str(data.get("description") or fallback["description"]).strip()
    tags = data.get("tags") if isinstance(data.get("tags"), list) else fallback["tags"]
    tags = [str(t).strip()[:50] for t in tags if str(t).strip()][:12]
    description, tags = _strip_banned_hashtags(description, tags, cfg)
    thumbnail_text = str(data.get("thumbnail_text") or "").strip()[:40]
    if is_short and "shorts" not in _banned_hashtags(cfg) and "#shorts" not in f"{title} {description}".lower():
        description = f"{description}\n\n#Shorts"
    return {
        "title": title,
        "description": description,
        "tags": tags,
        "thumbnail_text": thumbnail_text,
        "metadata_fallback": bool(generation_error),
    }


def _stick_figure_long_metadata(project: str, story_title: str = "", story_text: str = "") -> dict | None:
    """Opt in to the dedicated package only for an already-valid stick plan."""
    try:
        packaging = load_stick_figure_packaging(project)
    except Exception:
        packaging = None
    if not isinstance(packaging, dict) or packaging.get("scope") != "stick_figure_long_form":
        return None
    if story_title and story_text and not stick_figure_packaging_is_current(packaging, story_title, story_text):
        return None
    title = str(packaging.get("selected_title") or "").strip()
    description = str(packaging.get("description_with_hashtags") or packaging.get("description") or "").strip()
    if not title or not description:
        return None
    concepts = packaging.get("thumbnail_concepts") if isinstance(packaging.get("thumbnail_concepts"), list) else []
    return {
        "title": title[:110],
        "description": description[:6000],
        # The skill intentionally does not create Studio tags unless asked.
        "tags": [],
        "thumbnail_text": str((concepts[0] if concepts else {}).get("overlay_text") or "").strip()[:48],
        "metadata_fallback": False,
        "metadata_source": "stick_figure_packaging",
    }


def _fallback_video_description(title: str) -> str:
    return (
        f"{title}\n\n"
        "Một câu chuyện kinh dị với những dấu hiệu bất thường dần xuất hiện trong bóng tối. "
        "Khi sự thật bị che giấu quá lâu, mỗi lựa chọn đều có thể dẫn nhân vật đến gần hơn với điều không nên chạm tới.\n\n"
        "Đeo tai nghe, tắt bớt đèn và cùng bước vào câu chuyện đêm nay.\n\n"
        "#truyenma #truyenkinhdi #truyenaudio #kechuyen #datthoai"
    )


def _generate_video_description_text(body: VideoDescriptionRequest) -> str:
    cfg = _cfg_defaults(load_config())
    model_override = (body.model or "").strip()
    title = (body.story_title or body.project or "Truyen audio").strip()
    story = re.sub(r"\s+", " ", (body.story_text or "").strip())
    if not story:
        raise HTTPException(400, "Missing story text")
    teaser_len = max(1200, int(len(story) * 0.68))
    excerpt = story[: min(9000, teaser_len)]
    system = (
        "You are a YouTube copywriter for narrated story videos. Write in the same language as the supplied story. "
        "Write attractive video descriptions that make viewers curious without spoiling the ending, twist, culprit, secret explanation, or final reveal. "
        "Use natural wording, no clickbait lies, no markdown table."
    )
    user = (
        f"Story title: {title}\n"
        f"Non-ending excerpt for teaser only:\n{excerpt}\n\n"
        "Write one YouTube video description. Requirements:\n"
        "- 2 to 4 short paragraphs.\n"
        "- Hook viewers with mystery and atmosphere.\n"
        "- Do not reveal the plot twist, ending, final secret, culprit, or resolution.\n"
        "- Mention this is an audio story/narrated story if natural.\n"
        "- End with 5-8 relevant YouTube hashtags in the story language.\n"
        "- Return only the description text."
    )
    text = _generate_youtube_text(
        cfg,
        system,
        user,
        model_override,
        1200,
    )
    text = str(text or "").strip()
    if not text:
        raise HTTPException(500, "AI returned an empty description")
    return text[:4000].strip()


def _thumbnail_path(project: str, item_id: str) -> Path:
    d = _project_dir(project) / "thumbnails"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{item_id}.jpg"


def _selected_thumbnail_path(project: str) -> Path | None:
    state_path = _project_dir(project) / "thumbnails" / "selected_thumbnail.json"
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        path = Path(data.get("path") or "")
        project_root = _project_dir(project).resolve()
        if path.exists() and path.is_file() and str(path.resolve()).startswith(str(project_root)):
            return path
    except Exception:
        return None
    return None


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = test
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines[:3]


def _overlay_thumbnail_text(image_path: Path, title: str, out_path: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    img = Image.open(image_path).convert("RGB").resize((1280, 720))
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, 420, 1280, 720), fill=(0, 0, 0, 150))
    # Short punchy overlay text (2-4 words) gets a bigger font so it reads on a TV from across the room.
    text = (title or "").strip()
    font_size = 108 if len(text.split()) <= 4 else 76
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
    lines = _wrap_text(draw, text, font, 1120)
    line_h = font_size + 12
    # Center the block vertically within the bottom band (420-720) so a big single line isn't clipped.
    y = max(440, 570 - (len(lines) * line_h) // 2)
    for line in lines:
        draw.text((646, y + 4), line, font=font, fill=(0, 0, 0, 210), anchor="ma")
        draw.text((640, y), line, font=font, fill=(245, 245, 245, 255), anchor="ma", stroke_width=2, stroke_fill=(130, 0, 0, 255))
        y += line_h
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    img.save(out_path, "JPEG", quality=88, optimize=True)
    return out_path


def _fallback_thumbnail(title: str, out_path: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    img = Image.new("RGB", (1280, 720), "#08070b")
    draw = ImageDraw.Draw(img)
    for i in range(0, 720, 8):
        shade = int(10 + i * 0.07)
        draw.rectangle((0, i, 1280, i + 8), fill=(shade, 6, 10 + int(i * 0.04)))
    for x in range(0, 1280, 80):
        draw.line((x, 0, x - 300, 720), fill=(70, 0, 20), width=2)
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    tmp = out_path.with_suffix(".base.jpg")
    img.save(tmp, "JPEG", quality=92)
    try:
        return _overlay_thumbnail_text(tmp, title, out_path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _generate_thumbnail(plan: dict, item: dict, model: str) -> Path:
    cfg = _cfg_defaults(load_config())
    out = _thumbnail_path(plan["project"], item["id"])
    prompt = (
        "Cinematic YouTube thumbnail background that matches the supplied story's actual genre and visual language, "
        "with a clear curiosity-building focal point, no gore, no text, no logo. "
        f"Story: {plan.get('story_title')}. Context: {_item_context(plan, item)[:1200]}"
    )
    try:
        provider_cfg = image_provider_config(cfg)
        raw = out.with_suffix(".raw.png")
        generate_image_file(
            prompt,
            raw,
            provider_cfg,
            model=model or None,
            size=cfg.get("thumbnail_image_size") or provider_cfg.get("size") or "1280x720",
        )
        overlay_text = item.get("thumbnail_text") or plan.get("story_title") or item.get("title", "")
        try:
            return _overlay_thumbnail_text(raw, overlay_text, out)
        finally:
            try:
                raw.unlink()
            except OSError:
                pass
    except Exception:
        pass
    overlay_text = item.get("thumbnail_text") or plan.get("story_title") or item.get("title", "")
    return _fallback_thumbnail(overlay_text, out)


def _build_plan(body: PublishPlanCreateRequest) -> dict:
    cfg = _cfg_defaults(load_config())
    project_dir = _project_dir(body.project)
    now = datetime.now().isoformat(timespec="seconds")
    items = []
    kinds = []
    if body.long_video:
        path = _resolve_source(body.project, body.long_video)
        metadata_context = {
            "project": body.project,
            "story_title": body.story_title,
            "story_text": body.story_text,
            "subtitle_cues": [],
        }
        meta = (
            {"title": body.story_title[:95], **body.long_metadata.model_dump(exclude_none=True, exclude={"status", "scheduled_at"})}
            if body.long_metadata
            else _stick_figure_long_metadata(body.project, body.story_title, body.story_text)
            or _generate_metadata(metadata_context, {"kind": "long", "index": 0}, body.model)
        )
        long_item = {
            "id": str(uuid.uuid4()),
            "kind": "long",
            "index": 0,
            "source_path": str(path),
            "source_file": path.name,
            "aspect": "16:9",
            "duration_sec": get_video_duration(path, cfg.get("ffprobe_path", "ffprobe")),
            "status": "draft",
            **meta,
        }
        selected_thumbnail = _selected_thumbnail_path(body.project)
        if selected_thumbnail:
            long_item["thumbnail_path"] = str(selected_thumbnail)
            long_item["thumbnail_url"] = f"/api/publish/youtube/thumbnail/{body.project}/{long_item['id']}"
        items.append(long_item)
        kinds.append("long")
    for i, src in enumerate(body.short_videos, 1):
        path = _resolve_source(body.project, src)
        meta = _default_metadata(body.story_title, "short", i)
        items.append({
            "id": str(uuid.uuid4()),
            "kind": "short",
            "index": i,
            "source_path": str(path),
            "source_file": path.name,
            "aspect": "9:16",
            "duration_sec": get_video_duration(path, cfg.get("ffprobe_path", "ffprobe")),
            "status": "draft",
            **meta,
        })
        kinds.append("short")
    existing = _remote_schedule(cfg) + _local_schedule(body.project, cfg)
    schedule = _schedule_items(kinds, cfg, existing)
    for item, scheduled_at in zip(items, schedule):
        item["scheduled_at"] = scheduled_at
        item["timezone"] = body.timezone or cfg.get("youtube_timezone", "Asia/Ho_Chi_Minh")
    timing = None
    subtitle_cues = []
    try:
        audio_files = sorted(project_dir.glob("*_FULL.mp3"))
        if audio_files:
            timing = load_timing_file(project_dir, audio_files[-1].name)
        if timing:
            subtitle_cues = build_subtitle_cues(timing)
    except Exception:
        pass
    plan = {
        "project": body.project,
        "story_title": body.story_title,
        "story_text": body.story_text,
        "subtitle_cues": subtitle_cues,
        "short_count": len(body.short_videos),
        "items": items,
        "created_at": now,
        "updated_at": now,
    }
    return _save_plan(plan)


UPLOAD_MODES = {"scheduled", "unlisted", "private"}  # private = no publishAt, held for review


def _youtube_thumbnail_jpeg(path: Path) -> Path:
    """YouTube thumbnails must be JPEG/PNG under 2 MB; director candidates can be 4K PNGs."""
    if path.suffix.lower() in {".jpg", ".jpeg"} and path.stat().st_size < 2_000_000:
        return path
    from PIL import Image
    out = path.with_name(f"{path.stem}_youtube.jpg")
    img = Image.open(path).convert("RGB")
    img.thumbnail((1280, 720))
    img.save(out, "JPEG", quality=90)
    return out


def _upload_item(plan: dict, item: dict, mode: str = "scheduled"):
    _, _, _, MediaFileUpload = _youtube_deps()
    yt = _youtube_service()
    if mode not in UPLOAD_MODES:
        raise RuntimeError("Invalid upload mode")
    if mode == "unlisted" and item.get("kind") != "long":
        raise RuntimeError("Only a long video can be uploaded as an unlisted Related Video target")
    path = Path(item["source_path"])
    if not path.exists():
        raise RuntimeError(f"Source video missing: {path}")
    publish_at = None
    if mode == "scheduled":
        if not item.get("scheduled_at"):
            raise RuntimeError("Set a publish time before scheduling this video")
        publish_at = _parse_iso(item["scheduled_at"], ZoneInfo(item.get("timezone") or "Asia/Ho_Chi_Minh")).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    status = {
        "privacyStatus": "unlisted" if mode == "unlisted" else "private",
        "selfDeclaredMadeForKids": False,
    }
    if publish_at:
        status["publishAt"] = publish_at
    snippet = {
        "title": item["title"],
        "description": item.get("description", ""),
        "tags": item.get("tags", []),
        "categoryId": item.get("category_id") or "24",
    }
    if item.get("language"):
        snippet["defaultLanguage"] = snippet["defaultAudioLanguage"] = item["language"]
    body = {"snippet": snippet, "status": status}
    parts = "snippet,status"
    if item.get("localizations") and item.get("language"):
        body["localizations"] = item["localizations"]
        parts += ",localizations"
    video_id = item.get("youtube_video_id")
    if video_id:
        # videos.update resets every omitted status field (embeddable, publicStatsViewable, ...) to false; keep YouTube's values
        current = yt.videos().list(part="status", id=video_id).execute()["items"][0]["status"]
        kept = {k: current[k] for k in ("embeddable", "publicStatsViewable", "license", "containsSyntheticMedia") if k in current}
        body["status"] = {**kept, **status}
        yt.videos().update(part=parts, body={"id": video_id, **body}).execute()
        if mode != "private":
            yield _upload_done(video_id, mode, [])  # publishing a reviewed video: keep thumbnail/captions edited in Studio
            return
    else:
        media = MediaFileUpload(str(path), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4")
        request = yt.videos().insert(part=parts, body=body, media_body=media)
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                yield {"type": "progress", "percent": int(status.progress() * 90), "message": "Uploading video"}
        video_id = response["id"]
        # The video is on YouTube now; persist its id before the extras (a retry would re-upload).
        yield {"type": "uploaded", "percent": 90, "youtube_video_id": video_id}
    warnings = []
    if item.get("kind") == "long" and item.get("thumbnail_path"):
        thumb = Path(item["thumbnail_path"])
        if thumb.exists():
            try:
                yt.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(_youtube_thumbnail_jpeg(thumb)), mimetype="image/jpeg")).execute()
            except Exception as exc:
                warnings.append(f"Thumbnail not set: {exc}")
    if item.get("caption_file"):
        try:
            _set_caption(yt, video_id, plan["project"], item, MediaFileUpload)
        except Exception as exc:
            warnings.append(f"Captions not set: {exc}")
    if item.get("localizations") and not item.get("language"):
        warnings.append("Localizations skipped: set the item language first")
    for warning in warnings:
        yield {"type": "progress", "percent": 95, "message": warning}
    yield _upload_done(video_id, mode, warnings)


def _upload_done(video_id: str, mode: str, warnings: list[str]) -> dict:
    return {
        "type": "done",
        "percent": 100,
        "youtube_video_id": video_id,
        "youtube_url": f"https://youtu.be/{video_id}",
        "mode": mode,
        "warnings": warnings,
    }


def _set_caption(yt, video_id: str, project: str, item: dict, MediaFileUpload) -> None:
    """Upload <project>/captions/<caption_file> as the video's track; replace our earlier track on re-runs."""
    name = item["caption_file"]
    if not SAFE_CAPTION.match(name):
        raise RuntimeError("Invalid caption file name")
    srt = _project_dir(project) / "captions" / name
    if not srt.exists():
        raise RuntimeError(f"Caption file missing: {srt}")
    language = item.get("language")
    if not language:
        raise RuntimeError("Set the item language before adding captions")
    media = MediaFileUpload(str(srt), mimetype="application/octet-stream")
    tracks = yt.captions().list(part="snippet", videoId=video_id).execute().get("items", [])
    mine = next((t for t in tracks if t["snippet"].get("language") == language and t["snippet"].get("trackKind") != "asr"), None)
    if mine:
        yt.captions().update(part="snippet", body={"id": mine["id"], "snippet": {"isDraft": False}}, media_body=media).execute()
    else:
        yt.captions().insert(part="snippet", body={"snippet": {"videoId": video_id, "language": language, "name": "", "isDraft": False}}, media_body=media).execute()


router = APIRouter()


@router.get("/api/youtube/auth/status")
def youtube_auth_status():
    cfg = _cfg_defaults(load_config())
    connected = False
    channel = None
    try:
        if TOKEN_PATH.exists():
            yt = _youtube_service()
            channels = yt.channels().list(part="snippet", mine=True).execute()
            item = (channels.get("items") or [None])[0]
            connected = bool(item)
            if item:
                channel = {"id": item["id"], "title": item["snippet"]["title"]}
    except Exception:
        connected = False
    return {
        "connected": connected,
        "channel": channel,
        "client_secrets_path": cfg.get("youtube_client_secrets_path", ""),
        "timezone": cfg.get("youtube_timezone", "Asia/Ho_Chi_Minh"),
    }


@router.post("/api/youtube/auth/start")
def youtube_auth_start(request: Request):
    cfg = _cfg_defaults(load_config())
    secrets = cfg.get("youtube_client_secrets_path", "").strip().strip('"')
    if not secrets or not Path(secrets).exists():
        raise HTTPException(400, "Set youtube_client_secrets_path in config first")
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    _, Flow, _, _ = _youtube_deps()
    redirect_uri = str(request.base_url).rstrip("/") + "/api/youtube/auth/callback"
    flow = Flow.from_client_secrets_file(secrets, scopes=SCOPES, redirect_uri=redirect_uri)
    auth_url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent")
    PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({"state": state, "redirect_uri": redirect_uri, "secrets": secrets}), encoding="utf-8")
    return {"auth_url": auth_url}


@router.get("/api/youtube/auth/callback")
def youtube_auth_callback(state: str, code: str):
    if not STATE_PATH.exists():
        raise HTTPException(400, "OAuth state missing")
    data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if state != data.get("state"):
        raise HTTPException(400, "OAuth state mismatch")
    _, Flow, _, _ = _youtube_deps()
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    flow = Flow.from_client_secrets_file(data["secrets"], scopes=SCOPES, redirect_uri=data["redirect_uri"], state=state)
    flow.fetch_token(code=code)
    PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(flow.credentials.to_json(), encoding="utf-8")
    return HTMLResponse("<html><body><h3>YouTube connected. You can close this tab.</h3></body></html>")


@router.post("/api/youtube/auth/logout")
def youtube_auth_logout():
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()
    return {"ok": True}


@router.post("/api/publish/youtube/plan/create")
def create_publish_plan(body: PublishPlanCreateRequest):
    return _build_plan(body)


@router.get("/api/publish/youtube/plan/{project}")
def get_publish_plan(project: str):
    return _load_plan(project)


@router.patch("/api/publish/youtube/plan/{project}/items/{item_id}")
def update_publish_item(project: str, item_id: str, body: PublishItemUpdateRequest):
    plan = _load_plan(project)
    for item in plan.get("items", []):
        if item["id"] == item_id:
            for key, value in body.model_dump(exclude_none=True).items():
                item[key] = value
            return _save_plan(plan)
    raise HTTPException(404, "Publish item not found")


@router.post("/api/publish/youtube/metadata/generate")
def generate_publish_metadata(body: PublishMetadataRequest):
    plan = _load_plan(body.project)
    for item in plan.get("items", []):
        if item["id"] == body.item_id:
            packaged = _stick_figure_long_metadata(body.project, plan.get("story_title", ""), plan.get("story_text", "")) if item.get("kind") == "long" else None
            item.update(packaged or _generate_metadata(plan, item, body.model))
            return _save_plan(plan)
    raise HTTPException(404, "Publish item not found")


@router.post("/api/post-production/video-description/generate")
def generate_post_video_description(body: VideoDescriptionRequest):
    warning = ""
    packaged = _stick_figure_long_metadata(body.project, body.story_title, body.story_text) if body.project else None
    if packaged:
        description = packaged["description"]
    else:
        try:
            description = _generate_video_description_text(body)
        except HTTPException:
            raise
        except Exception:
            description = _fallback_video_description((body.story_title or body.project or "Truyện audio").strip())
            warning = "LLM không phản hồi kịp thời; đã tạo bản mô tả nháp để bạn chỉnh sửa."
    if body.project:
        meta = _load_post_production(body.project)
        _save_post_production(body.project, {**meta, "video_description": description})
    return {"description": description, "warning": warning}


@router.get("/api/post-production/video-description/{project}")
def get_post_video_description(project: str):
    meta = _load_post_production(project)
    return {"project": project, "description": meta.get("video_description", ""), "updated_at": meta.get("updated_at", "")}


@router.patch("/api/post-production/video-description")
def save_post_video_description(body: VideoDescriptionSaveRequest):
    meta = _load_post_production(body.project)
    saved = _save_post_production(body.project, {**meta, "video_description": body.description})
    return {"project": body.project, "description": saved.get("video_description", ""), "updated_at": saved.get("updated_at", "")}


@router.post("/api/publish/youtube/thumbnail/generate")
def generate_publish_thumbnail(body: PublishThumbnailRequest):
    plan = _load_plan(body.project)
    for item in plan.get("items", []):
        if item["id"] == body.item_id:
            if item.get("kind") != "long":
                raise HTTPException(400, "Only long videos need thumbnails")
            path = _selected_thumbnail_path(body.project) or _generate_thumbnail(plan, item, body.model)
            item["thumbnail_path"] = str(path)
            item["thumbnail_url"] = f"/api/publish/youtube/thumbnail/{body.project}/{item['id']}"
            return _save_plan(plan)
    raise HTTPException(404, "Publish item not found")


@router.get("/api/publish/youtube/thumbnail/{project}/{item_id}")
def serve_publish_thumbnail(project: str, item_id: str):
    path = None
    try:
        plan = _load_plan(project)
        item = next((entry for entry in plan.get("items", []) if entry.get("id") == item_id), None)
        candidate = Path((item or {}).get("thumbnail_path") or "")
        if candidate.exists() and candidate.is_file() and str(candidate.resolve()).startswith(str(_project_dir(project).resolve())):
            path = candidate
    except Exception:
        path = None
    path = path or _thumbnail_path(project, item_id)
    if not path.exists():
        raise HTTPException(404, "Thumbnail not found")
    return FileResponse(str(path), media_type="image/jpeg")


def _inline_video_response(path: Path, request: Request):
    size = path.stat().st_size
    headers = {"Accept-Ranges": "bytes", "Content-Disposition": f'inline; filename="{path.name}"'}
    range_header = request.headers.get("range", "").strip()
    if not range_header:
        return FileResponse(str(path), media_type="video/mp4", headers=headers)
    match = re.match(r"^bytes=(\d*)-(\d*)$", range_header)
    if not match:
        return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
    start_raw, end_raw = match.groups()
    try:
        if start_raw:
            start = int(start_raw)
            end = min(int(end_raw) if end_raw else size - 1, size - 1)
        else:
            start = max(0, size - int(end_raw))
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
            while remaining:
                chunk = video.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(stream_range(), status_code=206, media_type="video/mp4", headers={
        **headers,
        "Content-Range": f"bytes {start}-{end}/{size}",
        "Content-Length": str(length),
    })


@router.get("/api/publish/youtube/video/{project}/{item_id}")
def serve_publish_video(project: str, item_id: str, request: Request):
    plan = _load_plan(project)
    item = next((entry for entry in plan.get("items", []) if entry.get("id") == item_id), None)
    if not item:
        raise HTTPException(404, "Publish item not found")
    path = Path(item.get("source_path") or "")
    project_dir = _project_dir(project).resolve()
    try:
        path = path.resolve()
    except OSError:
        raise HTTPException(404, "Video file not found")
    if not path.is_file() or not str(path).startswith(str(project_dir)):
        raise HTTPException(404, "Video file not found")
    return _inline_video_response(path, request)


@router.post("/api/publish/youtube/items/{item_id}/approve")
def approve_publish_item(item_id: str, body: PublishUploadRequest):
    plan = _load_plan(body.project)
    for item in plan.get("items", []):
        if item["id"] == item_id:
            item["status"] = "approved"
            return _save_plan(plan)
    raise HTTPException(404, "Publish item not found")


@router.post("/api/publish/youtube/items/{item_id}/upload")
def upload_publish_item(item_id: str, body: PublishUploadRequest):
    def event_stream():
        plan = _load_plan(body.project)
        target = None
        for item in plan.get("items", []):
            if item["id"] == item_id:
                target = item
                break
        if not target:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Publish item not found'})}\n\n"
            yield "data: [DONE]\n\n"
            return
        mode = body.mode if body.mode in UPLOAD_MODES else "scheduled"
        if target.get("kind") == "short" and target.get("long_item_id"):
            parent = next((item for item in plan.get("items", []) if item.get("id") == target["long_item_id"]), None)
            if not parent or not parent.get("youtube_video_id") or parent.get("status") != "unlisted":
                yield f"data: {json.dumps({'type': 'error', 'message': 'Upload the long video as unlisted first. Keep it unlisted until this Short is linked to it in YouTube Studio.'}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                return
        target["status"] = "uploading"
        _save_plan(plan)
        try:
            for ev in _upload_item(plan, target, mode):
                if ev.get("type") == "uploaded":
                    target["youtube_video_id"] = ev["youtube_video_id"]
                    _save_plan(plan)
                if ev.get("type") == "done":
                    target["status"] = mode
                    target["youtube_video_id"] = ev["youtube_video_id"]
                    target["youtube_url"] = ev["youtube_url"]
                    target["error"] = ""
                    _save_plan(plan)
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:
            target["status"] = "failed"
            target["error"] = str(e)
            _save_plan(plan)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
