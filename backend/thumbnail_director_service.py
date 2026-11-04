"""Persistent Thumbnail Director jobs, vision review, and channel profiles."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import threading
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import BytesIO
from pathlib import Path

import requests
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from image_generation_service import generate_image_events, image_provider_config
from story_service import generate_text, generate_text_openai_compatible
from thumbnail_service import (
    FORMULAS,
    StandaloneThumbnailRequest,
    _build_safe_llm_thumbnail_prompt,
    _finalize_ai_thumbnail,
    _image_dimensions,
    _load_history,
    _normalize_upscale_mode,
    _project_dir,
    _resolve_reference_path,
    _resolve_thumb,
    _save_history,
    _template_reference_path,
    _title_key,
    _thumb_dir,
    _unique_output_path,
    _upscale_existing_thumbnail,
    _write_history,
    _session_id,
)
from tts_service import get_project_dir, load_config
from stick_figure_packaging_service import ensure_stick_figure_packaging


router = APIRouter()

TERMINAL_RUN_STATES = {"awaiting_approval", "completed", "completed_with_errors", "failed", "cancelled", "interrupted"}
TERMINAL_CANDIDATE_STATES = {"succeeded", "needs_review", "failed", "cancelled"}
SAFE_ID = re.compile(r"^[a-zA-Z0-9_.-]+$")
_STATE_LOCK = threading.RLock()
_ACTIVE_THREADS: dict[str, threading.Thread] = {}
_CANCEL_EVENTS: dict[str, threading.Event] = {}
_ATOMIC_REPLACE_ATTEMPTS = 9
_ATOMIC_REPLACE_DELAY_SEC = 0.05
LAYOUT_FREE = "free"
LAYOUT_CHANNEL_FORMAT = "channel_format"
LAYOUT_MODES = {LAYOUT_FREE, LAYOUT_CHANNEL_FORMAT}
CHANNEL_HEADLINE_MAX_CHARS = 32


class DirectorRunRequest(BaseModel):
    project: str
    story_title: str
    story_text: str
    session_id: str = ""
    target_locale: str = "vi-VN"
    channel_name: str = ""
    headline_seed: str = ""
    hook: str = ""
    genre_label: str = ""
    formula: str = "mystery"
    profile_id: str = ""
    lock_headline: bool = False
    concurrency: int = 6
    reference_image: str = ""
    model: str = ""
    vision_enabled: bool = True
    upscale_mode: str = "2k"
    layout_mode: str = LAYOUT_FREE


class CandidateRetryRequest(BaseModel):
    mode: str = "original"


class ConceptApprovalRequest(BaseModel):
    concept_ids: list[str] = []


class ConceptRegenerateRequest(BaseModel):
    keep_concept_ids: list[str] = []


class CustomConceptRequest(BaseModel):
    headline: str


class CandidateUpscaleRequest(BaseModel):
    mode: str = "2k"


class SelectedThumbnailRequest(BaseModel):
    filename: str


class ChannelProfileRequest(BaseModel):
    channel_name: str


class ProfileReferenceRequest(BaseModel):
    project: str
    filename: str


class ProfilePrimaryRequest(BaseModel):
    reference_id: str


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _director_root(project: str) -> Path:
    root = _project_dir(project) / "thumbnails" / "director"
    (root / "runs").mkdir(parents=True, exist_ok=True)
    return root


def _run_path(project: str, run_id: str) -> Path:
    if not SAFE_ID.match(run_id or ""):
        raise HTTPException(400, "Invalid Director run id")
    return _director_root(project) / "runs" / f"{run_id}.json"


def _profile_root() -> Path:
    root = get_project_dir("") / "_thumbnail_profiles"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _profile_dir(profile_id: str) -> Path:
    if not SAFE_ID.match(profile_id or ""):
        raise HTTPException(400, "Invalid thumbnail profile id")
    path = _profile_root() / profile_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "references").mkdir(parents=True, exist_ok=True)
    return path


def _profile_path(profile_id: str) -> Path:
    return _profile_dir(profile_id) / "profile.json"


def _concept_memory_item(concept: dict) -> dict:
    """Keep only the semantic fields needed to prevent rejected ideas returning."""
    return {
        "headline": str(concept.get("headline") or "").strip()[:160],
        "mystery": str(concept.get("core_mystery") or "").strip()[:600],
        "focus": str(concept.get("visual_focus") or "").strip()[:600],
        "composition": str(concept.get("composition") or "").strip()[:400],
    }


def _merge_concept_memory(*groups: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen = set()
    for group in groups:
        for raw in group or []:
            if not isinstance(raw, dict):
                continue
            item = _concept_memory_item(raw)
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if not any(item.values()) or key in seen:
                continue
            seen.add(key)
            merged.append(item)
    # A long-running story can be revised many times; retain enough negative
    # examples for variety without turning the Director prompt into the story.
    return merged[-36:]


def _prior_rejected_concepts(project: str, session_id: str, story_title: str) -> list[dict]:
    remembered: list[dict] = []
    title_key = _title_key(story_title)
    for path in (_director_root(project) / "runs").glob("*.json"):
        run = _read_json(path, {})
        if not isinstance(run, dict):
            continue
        same_session = bool(session_id and run.get("session_id") == session_id)
        same_legacy_story = not session_id and _title_key(run.get("story_title")) == title_key
        if same_session or same_legacy_story:
            remembered.extend(run.get("rejected_concepts") or [])
    return _merge_concept_memory(remembered)


def _atomic_json(path: Path, value: dict | list) -> None:
    """Persist Director state without failing on a brief Windows file lock.

    Thumbnail candidates finish concurrently while the browser polls the run.
    On Windows, Defender, Explorer, or an overlapping read can temporarily deny
    replacing the destination file even though the JSON temp file is complete.
    Keep the temp file and retry the atomic replace instead of failing the whole
    six-thumbnail job.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex[:6]}.tmp")
    try:
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        for attempt in range(_ATOMIC_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                return
            except OSError as exc:
                # WinError 5 = access denied, 32/33 = file or region locked.
                # Other OS errors are not expected to resolve by waiting.
                retryable = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {5, 32, 33}
                if not retryable or attempt == _ATOMIC_REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(min(0.5, _ATOMIC_REPLACE_DELAY_SEC * (attempt + 1)))
    finally:
        # A successful os.replace has already moved tmp.  If all retries fail,
        # do not leave a stale state file that could be mistaken for a run.
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _has_active_run_thread(run_id: str) -> bool:
    """Return whether the Director run or any of its candidate jobs is still live.

    A full Director run uses ``run_id`` as its thread key; retries and upscales
    use child keys such as ``run_id:candidate_1``.  Treating only the parent
    thread as active caused a retry to be marked interrupted as soon as the UI
    opened its SSE stream.
    """
    prefix = f"{run_id}:"
    return any(
        (key == run_id or key.startswith(prefix)) and thread.is_alive()
        for key, thread in list(_ACTIVE_THREADS.items())
    )


def _read_run(project: str, run_id: str, *, normalize: bool = True) -> dict:
    path = _run_path(project, run_id)
    if not path.exists():
        raise HTTPException(404, "Thumbnail Director run not found")
    run = _read_json(path, {})
    if not isinstance(run, dict) or not run:
        raise HTTPException(500, "Thumbnail Director run is unreadable")
    if normalize and run.get("status") not in TERMINAL_RUN_STATES:
        if not _has_active_run_thread(run_id):
            run["status"] = "interrupted"
            run["error"] = run.get("error") or "Backend restarted while this Director run was active. Retry failed items to continue."
            run["updated_at"] = _now()
            _atomic_json(path, run)
    return run


def _write_run(run: dict) -> dict:
    # Every writer (candidate workers, ranking, retry, and polling recovery)
    # shares this lock. RLock permits _mutate_run to call this safely.
    with _STATE_LOCK:
        run["updated_at"] = _now()
        _atomic_json(_run_path(run["project"], run["id"]), run)
    return run


def _mutate_run(project: str, run_id: str, fn) -> dict:
    with _STATE_LOCK:
        run = _read_run(project, run_id, normalize=False)
        fn(run)
        return _write_run(run)


def _profile_id(channel_name: str) -> str:
    text = unicodedata.normalize("NFKD", channel_name or "").encode("ascii", "ignore").decode("ascii")
    stem = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:42] or "channel"
    digest = hashlib.sha1((channel_name or "channel").strip().lower().encode("utf-8")).hexdigest()[:8]
    return f"{stem}-{digest}"


def _load_profile(profile_id: str) -> dict:
    path = _profile_path(profile_id)
    if not path.exists():
        raise HTTPException(404, "Thumbnail channel profile not found")
    data = _read_json(path, {})
    if not isinstance(data, dict) or not data:
        raise HTTPException(500, "Thumbnail channel profile is unreadable")
    return data


def _ensure_profile(channel_name: str, profile_id: str = "") -> dict:
    if profile_id:
        return _load_profile(profile_id)
    profile_id = _profile_id(channel_name or "Kênh mặc định")
    path = _profile_path(profile_id)
    if path.exists():
        return _load_profile(profile_id)
    profile = {
        "id": profile_id,
        "channel_name": (channel_name or "Kênh mặc định").strip(),
        "style_brief": "",
        "palette": [],
        "primary_reference_id": "",
        "references": [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    _atomic_json(path, profile)
    return profile


def _save_profile(profile: dict) -> dict:
    profile["updated_at"] = _now()
    _atomic_json(_profile_path(profile["id"]), profile)
    return profile


def _profile_reference_path(profile: dict, reference_id: str) -> Path | None:
    item = next((x for x in profile.get("references", []) if x.get("id") == reference_id), None)
    if not item:
        return None
    path = _profile_dir(profile["id"]) / "references" / str(item.get("filename") or "")
    return path if path.exists() and path.is_file() else None


def _load_scene_plan(project: str) -> dict:
    path = _project_dir(project) / "scenes" / "scene_plan.json"
    data = _read_json(path, {}) if path.exists() else {}
    return data if isinstance(data, dict) else {}


def _director_llm(system: str, user: str, max_tokens: int = 5000) -> str:
    cfg = load_config()
    provider = str(cfg.get("visual_director_provider") or "default").strip().lower()
    model = str(cfg.get("visual_director_model") or cfg.get("default_story_model") or "claude-sonnet-4-6").strip()
    if provider == "custom":
        return generate_text_openai_compatible(
            system,
            user,
            base_url=cfg.get("visual_director_base_url") or "https://api.openai.com",
            path=cfg.get("visual_director_path") or "/v1/chat/completions",
            model=model or "gpt-4o-mini",
            api_key=cfg.get("visual_director_api_key") or "",
            api_key_path=cfg.get("visual_director_api_key_path") or "",
            max_tokens=max_tokens,
        )
    return generate_text(
        system,
        user,
        model,
        cfg.get("anthropic_api_key", ""),
        cfg.get("ollama_url", "http://localhost:11434"),
        max_tokens=max_tokens,
    )


def _json_object(text: str) -> dict:
    raw = (text or "").strip()
    if raw.startswith("```"):
        pieces = raw.split("```")
        raw = pieces[1] if len(pieces) > 1 else raw
        if raw.lstrip().lower().startswith("json"):
            raw = raw.lstrip()[4:]
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def _story_evidence(story_text: str) -> str:
    clean = re.sub(r"\s+", " ", (story_text or "").strip())
    if len(clean) <= 24000:
        return clean
    system = (
        "You are a story analyst preparing evidence for a YouTube thumbnail director. "
        "Extract visually strong, curiosity-building beats without revealing the ending. Return concise plain text."
    )
    notes = []
    for index, offset in enumerate(range(0, len(clean), 12000), start=1):
        chunk = clean[offset:offset + 12000]
        notes.append(_director_llm(system, f"Story part {index}:\n{chunk}", max_tokens=1200))
    return "\n\n".join(f"PART {i + 1}: {note}" for i, note in enumerate(notes))[:24000]


def _scene_context(plan: dict) -> tuple[str, list[dict]]:
    if not plan:
        return "No Visual Director scene plan is available.", []
    bible = plan.get("visual_bible") if isinstance(plan.get("visual_bible"), dict) else {}
    refs = plan.get("references") if isinstance(plan.get("references"), list) else []
    scenes = plan.get("scenes") if isinstance(plan.get("scenes"), list) else []
    reference_summary = [
        {
            "id": ref.get("id"),
            "type": ref.get("type"),
            "name": ref.get("name"),
            "description": ref.get("description"),
            "status": ref.get("status"),
        }
        for ref in refs
    ]
    scene_summary = []
    for scene in scenes:
        storyboard = scene.get("storyboard") if isinstance(scene.get("storyboard"), dict) else {}
        scene_summary.append({
            "id": scene.get("id"),
            "text": str(scene.get("text") or "")[:420],
            "summary": storyboard.get("visual_summary") or storyboard.get("shot") or "",
            "reference_ids": scene.get("reference_ids") or [],
            "has_image": bool(scene.get("image_file")),
        })
    payload = {
        "visual_bible": bible,
        "references": reference_summary,
        "scenes": scene_summary[:160],
    }
    return json.dumps(payload, ensure_ascii=False), refs


def _visual_direction(scene_plan: dict) -> str:
    """Turn the Visual Director plan into non-negotiable thumbnail art direction."""
    bible = scene_plan.get("visual_bible") if isinstance(scene_plan.get("visual_bible"), dict) else {}
    mode = str(scene_plan.get("mode") or "").strip().lower()
    style = str(bible.get("visual_style") or "").strip()
    palette = bible.get("palette") if isinstance(bible.get("palette"), list) else []
    continuity = bible.get("continuity_rules") if isinstance(bible.get("continuity_rules"), list) else []
    parts = [
        "Derive the thumbnail's genre, emotional register, visual medium, and palette from the story evidence and Visual Director plan. "
        "A click formula defines the curiosity structure only; it must not impose a genre or mood.",
    ]
    if style:
        parts.append(f"Required visual style from the project: {style}")
    if palette:
        parts.append("Preferred palette from the project: " + ", ".join(str(item) for item in palette[:8]))
    if continuity:
        parts.append("Continuity requirements: " + " ".join(str(item) for item in continuity[:5]))
    if mode == "stick_figure":
        parts.append(
            "This is a stick-figure explainer project. The finished thumbnail MUST remain a clean 2D stick-figure / infographic illustration "
            "with the project's characters, props, and locations. Do not replace it with photoreal people, X-ray horror art, ghost imagery, "
            "or a generic dark cinematic poster unless those elements are explicitly part of the supplied project style."
        )
    return "\n".join(parts)


def _normalize_concepts(data: dict, body: DirectorRunRequest) -> list[dict]:
    raw = data.get("concepts") if isinstance(data.get("concepts"), list) else []
    concepts = []
    for index in range(3):
        item = raw[index] if index < len(raw) and isinstance(raw[index], dict) else {}
        headline = re.sub(r"\s+", " ", str(item.get("headline") or body.headline_seed or body.story_title)).strip()
        if body.lock_headline:
            headline = re.sub(r"\s+", " ", (body.headline_seed or body.story_title).strip())[:70]
        elif body.layout_mode == LAYOUT_CHANNEL_FORMAT:
            # The final channel-format title is composited by us, not guessed
            # by the image model. Keep a short fallback until headline QA.
            headline = _fit_channel_headline(headline)
        else:
            headline = headline[:70]
        variants = item.get("variants") if isinstance(item.get("variants"), list) else []
        while len(variants) < 2:
            variants.append("alternate framing and focal emphasis" if variants else "strong, story-appropriate hero composition")
        concepts.append({
            "id": f"concept_{index + 1}",
            "name": str(item.get("name") or f"Concept {index + 1}").strip()[:80],
            "headline": headline,
            "core_mystery": str(item.get("core_mystery") or "").strip()[:500],
            "visual_focus": str(item.get("visual_focus") or "").strip()[:700],
            "composition": str(item.get("composition") or "").strip()[:500],
            "emotion": str(item.get("emotion") or "curiosity with a tone appropriate to the story").strip()[:180],
            "palette": str(item.get("palette") or "a palette appropriate to the story and visual style").strip()[:220],
            "spoiler_boundary": str(item.get("spoiler_boundary") or "Do not reveal the ending or final truth.").strip()[:420],
            "reference_ids": [str(x) for x in (item.get("reference_ids") or []) if str(x).strip()][:2],
            "scene_id": str(item.get("scene_id") or "").strip(),
            "image_prompt": str(item.get("image_prompt") or item.get("prompt") or "").strip()[:2400],
            "variants": [str(variants[0])[:350], str(variants[1])[:350]],
        })
    return concepts


def _normalize_headline(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:70]


def _fit_channel_headline(value: object) -> str:
    """Keep an emergency channel-format headline inside the fixed title panel."""
    text = _normalize_headline(value)
    if len(text) <= CHANNEL_HEADLINE_MAX_CHARS:
        return text
    words = text.split()
    if len(words) > 1:
        chosen: list[str] = []
        for word in words:
            candidate = " ".join([*chosen, word])
            if chosen and len(candidate) > CHANNEL_HEADLINE_MAX_CHARS:
                break
            chosen.append(word)
        if chosen:
            return " ".join(chosen)
    return text[:CHANNEL_HEADLINE_MAX_CHARS].rstrip(" ,.;:!?-")


def _channel_format_rules(headline: str = "") -> str:
    """Binding visual system for repeatable channel thumbnails."""
    return (
        "MANDATORY CHANNEL FORMAT — this layout is non-negotiable for every concept and retry. "
        "Use a 16:9 editorial two-zone composition: the LEFT 38% is a calm warm cream-and-gray paper title panel "
        "(muted #D9D5CD / #B8B9B6 paper texture, no story imagery), and the RIGHT 62% is the single story illustration. "
        "Use a vertical organic deckled torn-paper seam at their boundary, with a very subtle 3–5% feathered blur/ink bleed into the right image; "
        "never use a hard straight divider, generic collage, or conventional split-screen. "
        "Leave the left title panel completely blank and clean: it is a protected text-safe area for a deterministic headline overlay added after generation. "
        "Do not draw letters, words, speech bubbles, labels, watermark, badges, logos, or faux text anywhere in the image. "
        "All three concepts must preserve these panel proportions, colors, typography placement, and torn-paper transition; "
        "they may differ in their short curiosity headline and in the story subject, action, camera, and visual metaphor on the right. "
        "Make the single hero subject or decisive object on the RIGHT deliberately oversized and unmistakable at phone size: "
        "it should occupy roughly 60–80% of the right illustration area, with a close or medium-close crop when appropriate. "
        "Never make the key subject a tiny distant figure, a small prop lost in scenery, or one equal element in a busy infographic. "
    )


def _thumbnail_scale_rules(layout_mode: str) -> str:
    """Non-negotiable phone-size hierarchy for every generated thumbnail."""
    if layout_mode == LAYOUT_CHANNEL_FORMAT:
        return (
            "Scale hierarchy is mandatory: the local overlay supplies the large headline on the protected left panel; "
            "make the one story-defining subject on the right large, foregrounded, and high contrast. "
            "Prefer one oversized subject over many small details."
        )
    return (
        "Scale hierarchy is mandatory: make the exact main headline the largest readable element, bold and high contrast, "
        "using no more than 2–3 short lines and roughly 25–35% of the frame height. "
        "Make one story-defining person, object, creature, or event visibly oversized (roughly 45–65% of frame height or width) "
        "and clearly separated from the background. Prefer one huge focal subject over many small competing details; "
        "never bury the focal subject in a wide distant scene."
    )


def _layout_director_instruction(body: DirectorRunRequest) -> str:
    if body.layout_mode != LAYOUT_CHANNEL_FORMAT:
        return "Layout mode: adaptive. You may choose the composition that best serves each concept. " + _thumbnail_scale_rules(body.layout_mode)
    return _channel_format_rules(body.headline_seed or body.story_title) + _thumbnail_scale_rules(body.layout_mode)


def _headline_qa(concepts: list[dict], body: DirectorRunRequest) -> tuple[list[dict], dict]:
    """Validate and, when allowed, localize Director headlines before image prompts.

    The existing Visual Director configuration is deliberately reused here so a
    thumbnail cannot receive a second, independently configured language model.
    """
    original = [
        {"id": concept.get("id"), "headline": _normalize_headline(concept.get("headline"))}
        for concept in concepts
    ]
    target_locale = (body.target_locale or "vi-VN").strip() or "vi-VN"
    if len(original) != 3 or any(not item["id"] or not item["headline"] for item in original):
        raise ValueError("Headline QA requires exactly three non-empty concepts")
    if body.lock_headline:
        for concept, previous in zip(concepts, original):
            concept["headline"] = previous["headline"]
            concept["headline_qa"] = {"status": "skipped_locked", "original": previous["headline"]}
        return concepts, {
            "status": "skipped_locked",
            "target_locale": target_locale,
            "items": [{**item, "status": "skipped_locked"} for item in original],
            "checked_at": _now(),
        }

    system = (
        "You are a strict multilingual YouTube thumbnail headline editor. "
        "Return JSON only. Correct headlines before they are rendered into images."
    )
    schema = '{"headlines":[{"id":"concept_1","headline":"..."}]}'
    user = json.dumps({
        "target_locale": target_locale,
        "story_title": body.story_title,
        "headline_seed": body.headline_seed or body.story_title,
        "concepts": [
            {
                "id": concept.get("id"),
                "headline": concept.get("headline"),
                "core_mystery": concept.get("core_mystery"),
                "visual_focus": concept.get("visual_focus"),
                "spoiler_boundary": concept.get("spoiler_boundary"),
            }
            for concept in concepts
        ],
        "requirements": [
            "Return exactly one item for every supplied concept id, in the same order.",
            "Write every headline in target_locale, not in the language of the instructions.",
            "Make every headline grammatical and semantically complete; never leave an implied or missing verb.",
            (
                f"This is a fixed channel-format title panel: use a distinct, self-contained 2-5 word headline, maximum {CHANNEL_HEADLINE_MAX_CHARS} Unicode characters. "
                "It must fit fully without clipping, ellipsis, or an unreadable extra line."
                if body.layout_mode == LAYOUT_CHANNEL_FORMAT
                else "Keep it concise and phone-readable (maximum 70 Unicode characters)."
            ),
            "Preserve each concept's distinct curiosity angle and do not reveal the ending.",
            "Do not add commentary, scores, markdown, or any fields outside the schema.",
        ],
        "schema": schema,
    }, ensure_ascii=False)
    raw = _director_llm(system, user, max_tokens=2200)
    try:
        data = _json_object(raw)
    except Exception as exc:
        raise ValueError(f"Headline QA returned invalid JSON: {exc}") from exc
    rows = data.get("headlines") if isinstance(data.get("headlines"), list) else []
    expected_ids = [item["id"] for item in original]
    if len(rows) != len(expected_ids):
        raise ValueError("Headline QA did not return exactly three headlines")
    revised: list[str] = []
    for expected_id, row in zip(expected_ids, rows):
        if not isinstance(row, dict) or str(row.get("id") or "") != expected_id:
            raise ValueError("Headline QA returned concept IDs out of order or missing")
        headline = _normalize_headline(row.get("headline"))
        if not headline:
            raise ValueError(f"Headline QA returned an empty headline for {expected_id}")
        if body.layout_mode == LAYOUT_CHANNEL_FORMAT and len(headline) > CHANNEL_HEADLINE_MAX_CHARS:
            raise ValueError(f"Headline QA returned an overlong channel-format headline for {expected_id}")
        revised.append(headline)
    if len({item.casefold() for item in revised}) != len(revised):
        raise ValueError("Headline QA returned duplicate headlines")

    items = []
    for concept, previous, headline in zip(concepts, original, revised):
        status = "corrected" if headline != previous["headline"] else "unchanged"
        concept["headline"] = headline
        concept["headline_qa"] = {"status": status, "original": previous["headline"]}
        items.append({"id": concept["id"], "original": previous["headline"], "headline": headline, "status": status})
    return concepts, {"status": "completed", "target_locale": target_locale, "items": items, "checked_at": _now()}


def _packaging_thumbnail_briefs(packaging: dict | None) -> list[dict]:
    if not isinstance(packaging, dict):
        return []
    return [
        {
            "id": item.get("id"), "name": item.get("name"), "headline": item.get("headline"),
            "mystery": item.get("core_mystery"), "focus": item.get("visual_focus"),
            "composition": item.get("composition"), "emotion": item.get("emotion"),
            "palette": item.get("palette"), "overlay_text": item.get("overlay_text"),
        }
        for item in (packaging.get("thumbnail_concepts") or []) if isinstance(item, dict)
    ][:3]


def _plan_concepts(body: DirectorRunRequest, scene_plan: dict, profile: dict, excluded_concepts: list[dict] | None = None, packaging: dict | None = None) -> list[dict]:
    evidence = _story_evidence(body.story_text)
    scene_json, _ = _scene_context(scene_plan)
    formula = FORMULAS.get(body.formula, FORMULAS["mystery"])
    direction = _visual_direction(scene_plan)
    stick_packaging = _packaging_thumbnail_briefs(packaging) if str(scene_plan.get("mode") or "").lower() == "stick_figure" else []
    excluded_summary = [
        {"headline": item.get("headline"), "mystery": item.get("core_mystery"), "focus": item.get("visual_focus")}
        for item in (excluded_concepts or [])
        if isinstance(item, dict)
    ]
    system = (
        "You are an elite, general-purpose YouTube Thumbnail Director. "
        "Read the supplied story and visual plan before deciding the subject, genre, emotional intensity, visual medium, palette, and composition. "
        "Use creative judgment, not fixed templates. Never presume horror, suspense, darkness, a specific locale, or photorealism. "
        "Create three genuinely different high-CTR concepts that are truthful, readable at phone size, visually coherent with the project, and do not spoil the ending. "
        "Return strict JSON only."
    )
    schema = (
        '{"concepts":[{"name":"...","headline":"3-7 words in the target output locale","core_mystery":"...",'
        '"visual_focus":"...","composition":"...","emotion":"...","palette":"...",'
        '"spoiler_boundary":"...","reference_ids":["..."],"scene_id":"...",'
        '"image_prompt":"complete final 16:9 thumbnail prompt","variants":["variation A","variation B"]}]}'
    )
    user = f"""Create exactly 3 concepts and exactly 2 variants per concept.
Story title: {body.story_title}
Headline seed: {body.headline_seed or body.story_title}
Target output locale: {body.target_locale}
Lock headline: {body.lock_headline}
Thumbnail layout mode: {body.layout_mode}
Channel label (exact if non-empty): {body.channel_name}
Optional hook (must be absent if empty): {body.hook}
Optional genre footer (must be absent if empty): {body.genre_label}
Click direction: {formula['prompt']}
Channel style profile: {json.dumps(profile, ensure_ascii=False)[:5000]}
Concepts already approved and kept (do not repeat their headline, mystery, or visual thesis):
{json.dumps(excluded_summary, ensure_ascii=False)[:2400]}

Story evidence from the full story:
{evidence}

Available Visual Director data:
{scene_json[:30000]}

Project visual direction (binding):
{direction}

Stick-figure SEO packaging brief (binding only when present):
{json.dumps(stick_packaging, ensure_ascii=False)[:6000] if stick_packaging else 'None. Use the normal Director process.'}

Rules:
- Each concept must use a different curiosity angle and visual thesis.
- If rejected concepts are supplied, they are a hard negative brief: do not recycle their topic framing, headline meaning, core mystery, hero subject, or composition with superficial wording changes.
- Infer the suitable genre and visual treatment from the evidence. A mystery subject may be documentary, educational, comedic, historical, dramatic, or horror depending on the actual story; do not default it to horror.
- Treat a stated project visual style and continuity rule as a hard requirement, including a stick-figure / illustrated style when present.
- When a stick-figure SEO packaging brief is present, produce one concept from each supplied brief in the same order. Preserve its promised clue, colour direction and headline intent; improve only the final image prompt and phone-size composition. Do not replace it with generic horror styling.
- {_layout_director_instruction(body)}
- Headlines must be natural in the target output locale, short, correctly spelled, and not reveal the resolution.
- Design every concept for an extremely small mobile feed: headline and hero subject must be intentionally oversized, not merely readable.
- Do not invent hook or genre text when those fields are empty.
- image_prompt requests a polished COMPLETE thumbnail inspired by the style reference, including exact required text.
- reference_ids and scene_id may only use IDs listed above.
- No gore, random text, watermark, cheap badge, collage, or split-screen.

JSON schema:
{schema}"""
    raw = _director_llm(system, user, max_tokens=6000)
    try:
        data = _json_object(raw)
    except Exception:
        repair = _director_llm(
            "Repair the supplied response into valid JSON. Return JSON only and preserve the content.",
            f"Required schema: {schema}\n\nResponse:\n{raw[:18000]}",
            max_tokens=5000,
        )
        data = _json_object(repair)
    concepts = _normalize_concepts(data, body)
    # The packaging skill owns the three high-level promises for this special
    # mode.  Keep the Director responsible for turning them into image-ready
    # prompts, but prevent it from silently replacing the approved SEO hooks.
    if stick_packaging:
        for concept, brief in zip(concepts, stick_packaging):
            for target, source in (("headline", "headline"), ("core_mystery", "mystery"), ("visual_focus", "focus"), ("composition", "composition"), ("emotion", "emotion"), ("palette", "palette")):
                value = str(brief.get(source) or "").strip()
                if value:
                    concept[target] = value[:(70 if target == "headline" else 700)]
            concept["packaging_brief_id"] = brief.get("id") or ""
    return concepts


def _expand_custom_concept(body: DirectorRunRequest, scene_plan: dict, profile: dict, headline: str, excluded_concepts: list[dict]) -> dict:
    """Expand a user-owned headline into the same Director schema as AI concepts."""
    evidence = _story_evidence(body.story_text)
    scene_json, _ = _scene_context(scene_plan)
    direction = _visual_direction(scene_plan)
    schema = ('{"name":"...","core_mystery":"...","visual_focus":"...","composition":"...",'
              '"emotion":"...","palette":"...","spoiler_boundary":"...","reference_ids":["..."],'
              '"scene_id":"...","image_prompt":"...","variants":["...","..."]}')
    system = (
        "You are an elite YouTube Thumbnail Director. Expand the user's exact headline into one detailed, "
        "high-CTR visual concept grounded in the supplied story. The headline is locked and must never be rewritten. Return JSON only."
    )
    user = f"""User-locked headline (it will be rendered exactly as written): {headline}
Target locale: {body.target_locale}
Layout mode: {body.layout_mode}
Project visual direction (binding): {direction}
Story evidence:
{evidence}
Visual Director data:
{scene_json[:24000]}
Do not overlap these rejected concepts in meaning, mystery, hero subject, or composition:
{json.dumps(excluded_concepts, ensure_ascii=False)[:3200]}
Return one detailed concept. Do not output a headline field and do not add readable text other than the locked headline in image_prompt.
Schema: {schema}"""
    raw = _director_llm(system, user, max_tokens=3200)
    try:
        data = _json_object(raw)
    except Exception:
        repair = _director_llm("Repair this into the required JSON object only.", f"Schema: {schema}\nResponse: {raw[:12000]}", max_tokens=2600)
        data = _json_object(repair)
    normalized = _normalize_concepts({"concepts": [{**data, "headline": headline}]}, body)[0]
    normalized.update({
        "id": f"concept_custom_{uuid.uuid4().hex[:8]}",
        "headline": headline,
        "headline_locked": True,
        "source": "user_headline",
    })
    return normalized


def _scene_reference_path(project: str, plan: dict, reference_id: str) -> Path | None:
    ref = next((x for x in plan.get("references", []) if x.get("id") == reference_id), None)
    if not ref:
        return None
    filename = str(ref.get("image_file") or "").strip()
    if not filename:
        return None
    root = _project_dir(project) / "scenes" / "references"
    for path in root.rglob(filename):
        if path.exists() and path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _scene_image_path(project: str, plan: dict, scene_id: str) -> Path | None:
    scene = next((x for x in plan.get("scenes", []) if x.get("id") == scene_id), None)
    filename = str((scene or {}).get("image_file") or "").strip()
    path = _project_dir(project) / "scenes" / "images" / filename
    return path if filename and path.exists() and path.is_file() else None


def _style_reference(body: DirectorRunRequest, profile: dict) -> Path | None:
    if body.reference_image:
        try:
            return _resolve_reference_path(body.project, body.reference_image)
        except HTTPException:
            pass
    primary = str(profile.get("primary_reference_id") or "")
    if primary:
        path = _profile_reference_path(profile, primary)
        if path:
            return path
    return _template_reference_path(body.project)


def _candidate_references(body: DirectorRunRequest, concept: dict, scene_plan: dict, profile: dict) -> list[str]:
    refs: list[Path] = []
    style = _style_reference(body, profile)
    if style:
        refs.append(style)
    for reference_id in concept.get("reference_ids") or []:
        path = _scene_reference_path(body.project, scene_plan, reference_id)
        if path and path not in refs:
            refs.append(path)
    scene_path = _scene_image_path(body.project, scene_plan, concept.get("scene_id") or "")
    if scene_path and scene_path not in refs:
        refs.append(scene_path)
    return [str(path) for path in refs[:3]]


def _candidate_prompt(body: DirectorRunRequest, concept: dict, variant_index: int, references: list[str], visual_direction: str = "") -> str:
    formula = FORMULAS.get(body.formula, FORMULAS["mystery"])
    role_lines = []
    if references:
        role_lines.append(
            "Reference image 1 controls channel visual language and typography mood; do not copy its story subject."
            if body.layout_mode == LAYOUT_CHANNEL_FORMAT
            else "Reference image 1 controls channel layout, typography mood, and brand identity; do not copy its story subject."
        )
    if len(references) >= 2:
        role_lines.append("Reference image 2 controls character identity or key story subject.")
    if len(references) >= 3:
        role_lines.append("Reference image 3 controls setting or scene continuity.")
    if body.layout_mode == LAYOUT_CHANNEL_FORMAT:
        text_rules = [
            "Do not render any headline or readable text. The exact headline is composited locally into the protected left panel after generation.",
            "Keep the entire left panel free of letters, speech bubbles, logos, labels, or faux text.",
        ]
    else:
        text_rules = [
            f"Render the exact main headline in {body.target_locale}: {concept['headline']}",
            "If any earlier director wording conflicts with this headline, this exact headline rule wins.",
        ]
    if body.layout_mode != LAYOUT_CHANNEL_FORMAT:
        if body.channel_name.strip():
            text_rules.append(f"Render the exact channel label: {body.channel_name.strip()}")
        if body.hook.strip():
            text_rules.append(f"Render the exact optional hook: {body.hook.strip()}")
        else:
            text_rules.append("Do not add a hook or substitute hook text.")
        if body.genre_label.strip():
            text_rules.append(f"Render the exact genre footer: {body.genre_label.strip()}")
        else:
            text_rules.append("Do not add a genre footer or substitute footer text.")
    return (
        "Create a polished COMPLETE 16:9 YouTube thumbnail, not a background-only image. "
        "Use a single clear focal composition with strong phone-size readability. "
        f"{_thumbnail_scale_rules(body.layout_mode)} "
        f"Concept: {concept.get('name')}. Core mystery: {concept.get('core_mystery')}. "
        f"Visual focus: {concept.get('visual_focus')}. Composition: {concept.get('composition')}. "
        f"Emotion: {concept.get('emotion')}. Palette: {concept.get('palette')}. "
        f"Variant direction: {(concept.get('variants') or [''])[variant_index]}. "
        f"Click formula: {formula['prompt']}. Spoiler boundary: {concept.get('spoiler_boundary')}. "
        f"Director image prompt: {concept.get('image_prompt')}. "
        f"Project visual direction (binding): {visual_direction}. "
        f"{_layout_director_instruction(body)} "
        + " ".join(role_lines + text_rules)
        + " Preserve the supplied text exactly. No random extra text, watermark, gore, collage, split-screen, or cheap badge."
    )


def _safe_candidate_prompt(run: dict, candidate: dict, level: int) -> str:
    """Safety fallback that preserves the Director's story-derived art direction."""
    concept = candidate.get("concept") if isinstance(candidate.get("concept"), dict) else {}
    visual_direction = str(candidate.get("visual_direction") or "").strip()
    safety_notes = {
        1: "Remove only graphic or provider-sensitive detail.",
        2: "Represent any injury, threat, or conflict indirectly with a relevant prop, diagram, posture, or setting.",
        3: "Use a clean, non-graphic symbolic or explanatory treatment of the same story clue.",
        4: "Use a restrained, non-graphic visual metaphor while retaining the story's real genre and art style.",
        5: "Use one simple, family-safe symbolic clue while preserving the title and the project visual language.",
    }
    headline_rule = (
        "Do not render any readable text. Leave the protected left title panel blank; the exact headline is composited locally after generation. "
        if run.get("layout_mode") == LAYOUT_CHANNEL_FORMAT
        else f"Render the exact main headline: {candidate.get('headline') or run.get('story_title')}. "
    )
    metadata_rule = (
        "Do not render a channel label, hook, genre footer, or any other readable text. "
        if run.get("layout_mode") == LAYOUT_CHANNEL_FORMAT
        else (
            f"Channel label: {run.get('channel_name') or 'none; do not add one'}. "
            f"Optional hook: {run.get('hook') or 'none; do not add one'}. "
            f"Genre footer: {run.get('genre_label') or 'none; do not add one'}. "
        )
    )
    return (
        "Create a complete, family-safe 16:9 YouTube thumbnail. "
        "Preserve the supplied project's actual genre, art medium, palette, characters, and visual language; never substitute horror, dark realism, or a generic suspense poster by default. "
        f"Story-derived concept: {concept.get('name')}. Core curiosity: {concept.get('core_mystery')}. "
        f"Visual focus: {concept.get('visual_focus')}. Composition: {concept.get('composition')}. "
        f"Emotion: {concept.get('emotion')}. Palette: {concept.get('palette')}. "
        f"Project visual direction (binding): {visual_direction}. "
        f"{_thumbnail_scale_rules(run.get('layout_mode') or '')} "
        f"Safety adaptation level {level}: {safety_notes[level]} "
        + headline_rule
        + metadata_rule
        + (_channel_format_rules(candidate.get("headline") or run.get("story_title") or "") if run.get("layout_mode") == LAYOUT_CHANNEL_FORMAT else "")
        + " "
        "Preserve the supplied text exactly. No watermark, random extra text, gore, collage, or split-screen."
    )


def _read_key(value: str, path_value: str) -> str:
    direct = (value or "").strip()
    if direct:
        return direct
    path = (path_value or "").strip().strip('"')
    try:
        return Path(path).read_text(encoding="utf-8").strip() if path else ""
    except OSError:
        return ""


def _vision_endpoint(base_url: str, path: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    route = (path or "/v1/chat/completions").strip()
    if not route.startswith("/"):
        route = "/" + route
    if base.lower().endswith(route.lower()):
        return base
    return base + route


def _vision_ready(cfg: dict) -> bool:
    return bool((cfg.get("thumbnail_vision_base_url") or "").strip() and (cfg.get("thumbnail_vision_model") or "").strip())


VISION_SCORE_FIELDS = (
    "text_fidelity",
    "small_view_readability",
    "visual_intrigue",
    "story_relevance",
    "brand_consistency",
    "spoiler_safety",
)


def _invalid_vision_review(message: str) -> dict:
    return {
        "status": "invalid",
        "message": message,
        "retry_needed": False,
    }


def _validate_vision_review(review: dict) -> dict:
    if not isinstance(review.get("required_text_ok"), bool):
        return _invalid_vision_review("Vision response is missing required_text_ok.")
    scores = review.get("scores")
    if not isinstance(scores, dict):
        return _invalid_vision_review("Vision response is missing its score rubric.")
    normalized_scores = {}
    for key in VISION_SCORE_FIELDS:
        try:
            value = float(scores[key])
        except (KeyError, TypeError, ValueError):
            return _invalid_vision_review(f"Vision response is missing a valid {key} score.")
        if not 0 <= value <= 10:
            return _invalid_vision_review(f"Vision response returned an out-of-range {key} score.")
        normalized_scores[key] = value
    review["scores"] = normalized_scores
    return review


def _vision_image_url(path: Path) -> str:
    from PIL import Image

    image = Image.open(path).convert("RGB")
    image.thumbnail((1280, 720))
    stream = BytesIO()
    image.save(stream, "JPEG", quality=82, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(stream.getvalue()).decode("ascii")


def _vision_text(data: dict) -> str:
    choices = data.get("choices") if isinstance(data, dict) else None
    if choices:
        content = (choices[0].get("message") or {}).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(str(x.get("text") or "") for x in content if isinstance(x, dict))
    output = data.get("output_text") if isinstance(data, dict) else ""
    return str(output or "")


def _vision_review(path: Path, body: DirectorRunRequest, concept: dict) -> dict:
    cfg = load_config()
    if not _vision_ready(cfg):
        return {"status": "unconfigured", "message": "Vision API chưa được cấu hình."}
    expected = {
        "headline": concept.get("headline") or "",
        "channel_name": body.channel_name.strip(),
        "hook": body.hook.strip(),
        "genre_label": body.genre_label.strip(),
    }
    rubric = {
        "expected_text": expected,
        "response_schema": {
            "observed_text": {"headline": "", "channel_name": "", "hook": "", "genre_label": "", "extra": []},
            "required_text_ok": True,
            "text_issues": [],
            "scores": {
                "text_fidelity": 0,
                "small_view_readability": 0,
                "visual_intrigue": 0,
                "story_relevance": 0,
                "brand_consistency": 0,
                "spoiler_safety": 0,
            },
            "correction_instructions": "",
            "summary": "",
        },
    }
    prompt = (
        "Inspect this YouTube thumbnail. OCR every visible word and score each rubric field from 0 to 10. "
        "Required non-empty text must match exactly in its supplied language. Empty hook or genre means that module must be absent. "
        "Judge readability at small phone size, curiosity, cinematic brand consistency, relevance to the supplied concept, and spoiler safety. "
        "Return strict JSON only.\n"
        + json.dumps({"rubric": rubric, "concept": concept}, ensure_ascii=False)
    )
    endpoint = _vision_endpoint(cfg.get("thumbnail_vision_base_url") or "", cfg.get("thumbnail_vision_path") or "/v1/chat/completions")
    key = _read_key(cfg.get("thumbnail_vision_api_key") or "", cfg.get("thumbnail_vision_api_key_path") or "")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request_body = {
        "model": cfg.get("thumbnail_vision_model"),
        "messages": [
            {"role": "system", "content": "You are a strict thumbnail OCR and visual quality evaluator. Return JSON only."},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": _vision_image_url(path), "detail": "high"}},
            ]},
        ],
        "temperature": 0,
        "max_tokens": 1600,
    }
    response = requests.post(endpoint, headers=headers, json=request_body, timeout=180)
    if not response.ok:
        raise RuntimeError(f"Vision API failed ({response.status_code}) at {endpoint}: {(response.text or '')[:700]}")
    review = _json_object(_vision_text(response.json()))
    review = _validate_vision_review(review)
    if review.get("status") == "invalid":
        return review
    scores = review["scores"]
    weights = {
        "text_fidelity": 0.30,
        "small_view_readability": 0.25,
        "visual_intrigue": 0.20,
        "story_relevance": 0.10,
        "brand_consistency": 0.10,
        "spoiler_safety": 0.05,
    }
    total = sum(max(0.0, min(10.0, float(scores.get(key) or 0))) * weight for key, weight in weights.items()) * 10
    issues = review.get("text_issues") if isinstance(review.get("text_issues"), list) else []
    if review.get("required_text_ok") is False:
        total -= 30
    observed = review.get("observed_text") if isinstance(review.get("observed_text"), dict) else {}
    if observed.get("extra"):
        total -= 20
    review["status"] = "completed"
    review["score"] = round(max(0, min(100, total)), 1)
    review["text_issues"] = [str(x) for x in issues]
    review["retry_needed"] = review.get("required_text_ok") is False
    return review


def _director_filename_slug(value: str, fallback: str = "thumbnail") -> str:
    text = unicodedata.normalize("NFKD", str(value or "").replace("đ", "d").replace("Đ", "D"))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:58].strip("-") or fallback


def _candidate_by_id(run: dict, candidate_id: str) -> dict | None:
    return next((x for x in run.get("candidates", []) if x.get("id") == candidate_id), None)


def _update_candidate(project: str, run_id: str, candidate_id: str, patch: dict) -> dict:
    def mutate(run):
        candidate = _candidate_by_id(run, candidate_id)
        if candidate:
            candidate.update(patch)
    return _mutate_run(project, run_id, mutate)


def _save_candidate_history(run: dict, candidate: dict, output: Path, prompt: str, references: list[str], vision: dict) -> None:
    item = {
        "filename": output.name,
        "saved_path": str(output),
        "url": f"/api/thumbnails/{run['project']}/files/{output.name}",
        "title": candidate.get("headline") or run.get("story_title"),
        "session_id": run.get("session_id") or "",
        "hook": run.get("hook") or "",
        "channel_name": run.get("channel_name") or "",
        "genre_label": run.get("genre_label") or "",
        "formula": run.get("formula") or "mystery",
        "created_at": _now(),
        "prompt": prompt,
        "prompt_mode": candidate.get("prompt_mode") or "director",
        "safe_level": candidate.get("safe_level") or 0,
        "reference_attached": bool(references),
        "reference_files": references,
        "vision": vision,
        "director_run_id": run["id"],
        "director_candidate_id": candidate["id"],
        "source_filename": candidate.get("source_filename") or "",
        "upscale_mode": candidate.get("upscale_mode") or "original",
        "upscale_engine": candidate.get("upscale_engine") or "provider",
        "upscale": candidate.get("upscale") or {},
        "width": candidate.get("width") or 0,
        "height": candidate.get("height") or 0,
        "size_bytes": output.stat().st_size,
    }
    with _STATE_LOCK:
        _save_history(run["project"], item)


def _channel_overlay_font(size: int):
    from PIL import ImageFont

    candidates = (
        Path(r"C:\Windows\Fonts\NotoSans-Bold.ttf"),
        Path(r"C:\Windows\Fonts\malgunbd.ttf"),
        Path(r"C:\Windows\Fonts\arialbd.ttf"),
    )
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _wrap_channel_headline(draw, text: str, font, max_width: int) -> list[str]:
    text = _fit_channel_headline(text)
    # Space-separated languages wrap by word; CJK without spaces wraps by glyph.
    words = text.split() if " " in text else list(text)
    separator = " " if " " in text else ""
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current}{separator if current else ''}{word}"
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [text]


def _composite_channel_headline(output: Path, headline: str) -> None:
    """Render reliable, legible text after image generation for Format kÃªnh.

    Image models are intentionally asked for a blank title panel. This avoids
    fabricated/cropped glyphs and makes the channel's text hierarchy exact.
    """
    from PIL import Image, ImageDraw

    with Image.open(output) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    width, height = image.size
    left = round(width * 0.055)
    panel_right = round(width * 0.35)
    max_width = max(80, panel_right - left)
    max_height = max(80, round(height * 0.62))
    font_size = max(26, round(width * 0.060))
    min_font_size = max(18, round(width * 0.026))
    selected_font = _channel_overlay_font(font_size)
    lines = _wrap_channel_headline(draw, headline, selected_font, max_width)
    line_height = round(font_size * 1.12)
    while font_size > min_font_size and (len(lines) > 4 or len(lines) * line_height > max_height):
        font_size -= 2
        selected_font = _channel_overlay_font(font_size)
        lines = _wrap_channel_headline(draw, headline, selected_font, max_width)
        line_height = round(font_size * 1.12)
    total_height = len(lines) * line_height
    top = max(round(height * 0.18), round((height - total_height) / 2))
    draw.multiline_text(
        (left, top), "\n".join(lines), font=selected_font, fill="#20242B",
        spacing=max(2, round(font_size * 0.12)), align="left",
    )
    temp = output.with_name(f".{output.stem}.headline-{uuid.uuid4().hex}{output.suffix}")
    try:
        if output.suffix.lower() in {".jpg", ".jpeg"}:
            image.save(temp, "JPEG", quality=95, subsampling=0, optimize=True)
        else:
            image.save(temp, "PNG", optimize=True)
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)


def _generate_attempt(
    run: dict,
    candidate: dict,
    prompt: str,
    references: list[str],
    cancel_event: threading.Event,
    attempt_kind: str,
) -> tuple[Path, list[dict]]:
    cfg = load_config()
    provider_cfg = image_provider_config(cfg)
    headline = run.get("headline_seed") or candidate.get("headline") or run.get("story_title")
    requested = f"{_director_filename_slug(headline)}_{candidate['id']}_{uuid.uuid4().hex[:6]}"
    output = _unique_output_path(run["project"], requested, requested)
    raw = output.with_suffix(".raw.png")
    events = []
    last_error = None
    for infrastructure_attempt in range(2):
        try:
            for event in generate_image_events(
                prompt,
                raw,
                dict(provider_cfg),
                size=cfg.get("thumbnail_image_size") or provider_cfg.get("size") or "1280x720",
                model=run.get("model") or None,
                reference_images=references,
                cancel_check=cancel_event.is_set,
            ):
                events.append(event)
                if event.get("type") == "progress":
                    _update_candidate(run["project"], run["id"], candidate["id"], {
                        "status": "generating",
                        "progress": max(2, min(88, int(event.get("progress") or 2))),
                        "provider_status": event.get("status") or "running",
                    })
            break
        except Exception as exc:
            last_error = exc
            if cancel_event.is_set() or infrastructure_attempt >= 1:
                raise
            _update_candidate(run["project"], run["id"], candidate["id"], {
                "provider_status": "retrying_provider",
                "error": str(exc)[:800],
            })
            time.sleep(2.5)
    if last_error and not raw.exists():
        raise last_error
    if cancel_event.is_set():
        raise RuntimeError("Cancelled")
    _finalize_ai_thumbnail(raw, output, target_size=None)
    if run.get("layout_mode") == LAYOUT_CHANNEL_FORMAT:
        _composite_channel_headline(output, candidate.get("headline") or run.get("story_title") or "")
    try:
        raw.unlink()
    except OSError:
        pass
    source_width, source_height = _image_dimensions(output)
    upscale = {
        "mode": "original",
        "engine": "provider",
        "width": source_width,
        "height": source_height,
        "source_width": source_width,
        "source_height": source_height,
        "elapsed_sec": 0,
        "warning": "",
    }
    candidate.update({
        "source_filename": "",
        "upscale_mode": "original",
        "upscale_engine": upscale["engine"],
        "upscale": upscale,
        "width": upscale["width"],
        "height": upscale["height"],
    })
    _update_candidate(run["project"], run["id"], candidate["id"], {
        "source_filename": candidate["source_filename"],
        "upscale_mode": candidate["upscale_mode"],
        "upscale_engine": candidate["upscale_engine"],
        "upscale": candidate["upscale"],
        "width": candidate["width"],
        "height": candidate["height"],
    })
    return output, events


def _run_candidate(project: str, run_id: str, candidate_id: str, cancel_event: threading.Event, override_prompt: str = "") -> None:
    run = _read_run(project, run_id, normalize=False)
    candidate = _candidate_by_id(run, candidate_id)
    if not candidate:
        return
    request_body = DirectorRunRequest(**run["request"])
    references = list(candidate.get("reference_files") or [])
    prompt = override_prompt or candidate.get("prompt") or ""
    candidate["prompt_mode"] = candidate.get("prompt_mode") or "director"
    _update_candidate(project, run_id, candidate_id, {"status": "generating", "progress": 2, "error": ""})
    try:
        output, _ = _generate_attempt(run, candidate, prompt, references, cancel_event, "initial")
        _update_candidate(project, run_id, candidate_id, {
            "status": "evaluating",
            "progress": 92,
            "filename": output.name,
            "saved_path": str(output),
        })
        if request_body.vision_enabled:
            try:
                vision = _vision_review(output, request_body, candidate["concept"])
            except Exception as exc:
                vision = {"status": "failed", "message": str(exc)[:900], "retry_needed": False}
        else:
            vision = {"status": "skipped", "message": "VLM evaluation was disabled for this run.", "retry_needed": False}
        attempts = list(candidate.get("attempts") or [])
        attempts.append({"kind": "initial", "filename": output.name, "prompt": prompt, "vision": vision, "created_at": _now()})
        final_output = output
        final_prompt = prompt

        if vision.get("retry_needed") and int(candidate.get("ocr_retry_count") or 0) < 1 and not cancel_event.is_set():
            correction = str(vision.get("correction_instructions") or "Correct every required text exactly in its supplied language.").strip()
            retry_prompt = (
                f"Edit the attached thumbnail while preserving composition, subject, lighting, and style. {correction} "
                f"The exact headline must be: {candidate['headline']}. "
                f"The exact channel label is: {run.get('channel_name') or 'none; do not add one'}. "
                f"The exact optional hook is: {run.get('hook') or 'none; do not add one'}. "
                f"The exact genre footer is: {run.get('genre_label') or 'none; do not add one'}. "
                "Remove random extra text. Preserve the required text exactly."
            )
            retry_refs = [str(output)] + [x for x in references if x != str(output)]
            _update_candidate(project, run_id, candidate_id, {
                "status": "ocr_retry",
                "progress": 94,
                "ocr_retry_count": 1,
                "vision": vision,
            })
            try:
                retry_output, _ = _generate_attempt(run, candidate, retry_prompt, retry_refs[:3], cancel_event, "ocr_retry")
                if request_body.vision_enabled:
                    try:
                        retry_vision = _vision_review(retry_output, request_body, candidate["concept"])
                    except Exception as exc:
                        retry_vision = {"status": "failed", "message": str(exc)[:900], "retry_needed": False}
                else:
                    retry_vision = {"status": "skipped", "message": "VLM evaluation was disabled for this run.", "retry_needed": False}
                attempts.append({"kind": "ocr_retry", "filename": retry_output.name, "prompt": retry_prompt, "vision": retry_vision, "created_at": _now()})
                final_output = retry_output
                final_prompt = retry_prompt
                vision = retry_vision
            except Exception as exc:
                attempts.append({"kind": "ocr_retry", "error": str(exc)[:900], "prompt": retry_prompt, "created_at": _now()})
                vision["retry_error"] = str(exc)[:900]

        status = "needs_review" if vision.get("retry_needed") else "succeeded"
        if vision.get("status") in {"unconfigured", "failed"}:
            status = "succeeded"
        _save_candidate_history(run, candidate, final_output, final_prompt, references, vision)
        _update_candidate(project, run_id, candidate_id, {
            "status": status,
            "progress": 100,
            "filename": final_output.name,
            "saved_path": str(final_output),
            "vision": vision,
            "score": vision.get("score"),
            "attempts": attempts,
            "error": "",
        })
    except Exception as exc:
        status = "cancelled" if cancel_event.is_set() or str(exc).lower() == "cancelled" else "failed"
        _update_candidate(project, run_id, candidate_id, {"status": status, "progress": 100, "error": str(exc)[:1200]})


def _rank_run(project: str, run_id: str) -> dict:
    def mutate(run):
        ranked = [x for x in run.get("candidates", []) if isinstance(x.get("score"), (int, float))]
        ranked.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
        for index, candidate in enumerate(ranked, start=1):
            candidate["rank"] = index
        run["recommended_candidate_id"] = ranked[0]["id"] if ranked else ""
        failed = [x for x in run.get("candidates", []) if x.get("status") in {"failed", "cancelled"}]
        review = [x for x in run.get("candidates", []) if x.get("status") == "needs_review"]
        if run.get("cancel_requested"):
            run["status"] = "cancelled"
        elif failed or review:
            run["status"] = "completed_with_errors"
        else:
            run["status"] = "completed"
        run["progress"] = 100
    return _mutate_run(project, run_id, mutate)


def _build_director_candidates(body: DirectorRunRequest, run: dict, scene_plan: dict, profile: dict, concepts: list[dict]) -> list[dict]:
    visual_direction = _visual_direction(scene_plan)
    candidates = []
    for concept_index, concept in enumerate(concepts, start=1):
        references = _candidate_references(body, concept, scene_plan, profile)
        for variant_index in range(2):
            candidate_id = f"c{concept_index}v{variant_index + 1}"
            candidates.append({
                "id": candidate_id,
                "concept_id": concept["id"],
                "variant": variant_index + 1,
                "headline": concept["headline"],
                "concept": concept,
                "prompt": _candidate_prompt(body, concept, variant_index, references, visual_direction),
                "prompt_mode": "director",
                "safe_level": 0,
                "visual_direction": visual_direction,
                "reference_files": references,
                "status": "queued",
                "progress": 0,
                "attempts": [],
                "ocr_retry_count": 0,
                "upscale_mode": "original",
                "upscale_engine": "provider",
                "width": 0,
                "height": 0,
                "error": "",
            })
    return candidates


def _refresh_director_candidate_prompt(project: str, run: dict, candidate: dict) -> str:
    """Rebuild a legacy candidate with the current Director prompt rules.

    Retries must not keep an obsolete serialized prompt forever: they preserve
    the approved concept, its variant and its original references, while
    inheriting any later prompt-quality improvements.
    """
    concept = candidate.get("concept") if isinstance(candidate.get("concept"), dict) else {}
    if not concept:
        raise ValueError("Thumbnail candidate has no reusable concept")
    request_data = run.get("request") if isinstance(run.get("request"), dict) else {}
    body = DirectorRunRequest(**request_data)
    body.project = project
    variant_index = max(0, int(candidate.get("variant") or 1) - 1)
    references = [str(path) for path in (candidate.get("reference_files") or []) if str(path).strip()]
    scene_plan: dict | None = None
    if not references or not str(candidate.get("visual_direction") or "").strip():
        scene_plan = _load_scene_plan(project)
    if not references:
        profile = _ensure_profile(body.channel_name, run.get("profile_id") or body.profile_id)
        references = _candidate_references(body, concept, scene_plan or {}, profile)
        candidate["reference_files"] = references
    visual_direction = str(candidate.get("visual_direction") or "").strip()
    if not visual_direction:
        visual_direction = _visual_direction(scene_plan or {})
        candidate["visual_direction"] = visual_direction
    prompt = _candidate_prompt(body, concept, variant_index, references, visual_direction)
    candidate["prompt"] = prompt
    candidate["prompt_mode"] = "director"
    return prompt


def _run_director_job(project: str, run_id: str, body: DirectorRunRequest, cancel_event: threading.Event) -> None:
    """Plan and QA concepts only. Image generation starts after user approval."""
    try:
        _mutate_run(project, run_id, lambda run: run.update({"status": "planning", "phase": "Đang phân tích toàn bộ truyện", "progress": 3}))
        scene_plan = _load_scene_plan(project)
        profile = _ensure_profile(body.channel_name, body.profile_id)
        remembered = _read_run(project, run_id, normalize=False).get("rejected_concepts") or []
        packaging = None
        packaging_error = ""
        if str(scene_plan.get("mode") or "").strip().lower() == "stick_figure":
            _mutate_run(project, run_id, lambda run: run.update({"phase": "Packaging stick-figure SEO", "progress": 4}))
            try:
                packaging = ensure_stick_figure_packaging(project, body.story_title, body.story_text, body.target_locale, body.model)
            except Exception as exc:
                # Packaging improves this special mode but does not make its
                # already-established concept workflow unavailable.
                packaging_error = str(exc)[:900]
        concepts = _plan_concepts(body, scene_plan, profile, excluded_concepts=remembered, packaging=packaging)
        _mutate_run(project, run_id, lambda run: run.update({
            "status": "planning",
            "phase": "Đang kiểm headline theo ngôn ngữ đầu ra",
            "progress": 5,
            "concepts": concepts,
            "packaging": packaging or {},
            "packaging_error": packaging_error,
            "headline_qa": {"status": "running", "target_locale": body.target_locale},
        }))
        concepts, headline_qa = _headline_qa(concepts, body)
        _mutate_run(project, run_id, lambda run: run.update({
            "status": "awaiting_approval",
            "phase": "Duyệt concept trước khi tạo ảnh",
            "progress": 100,
            "profile_id": profile["id"],
            "concepts": concepts,
            "headline_qa": headline_qa,
            "candidates": [],
            "vision_enabled": body.vision_enabled,
            "vision_configured": bool(body.vision_enabled and _vision_ready(load_config())),
            "upscale_mode": _normalize_upscale_mode(body.upscale_mode),
        }))
    except Exception as exc:
        def fail(run):
            run.update({"status": "failed", "progress": 100, "error": str(exc)[:1600]})
            if isinstance(run.get("headline_qa"), dict) and run["headline_qa"].get("status") == "running":
                run["headline_qa"] = {"status": "failed", "target_locale": body.target_locale, "error": str(exc)[:800], "checked_at": _now()}
        _mutate_run(project, run_id, fail)
    finally:
        _ACTIVE_THREADS.pop(run_id, None)
        _CANCEL_EVENTS.pop(run_id, None)


def _run_approved_generation(project: str, run_id: str, body: DirectorRunRequest, concept_ids: list[str], cancel_event: threading.Event) -> None:
    try:
        scene_plan = _load_scene_plan(project)
        profile = _ensure_profile(body.channel_name, body.profile_id)
        run = _read_run(project, run_id, normalize=False)
        wanted = set(concept_ids)
        concepts = [item for item in run.get("concepts", []) if item.get("id") in wanted]
        if not concepts:
            raise ValueError("Choose at least one approved thumbnail concept")
        candidates = _build_director_candidates(body, run, scene_plan, profile, concepts)
        _mutate_run(project, run_id, lambda item: item.update({
            "status": "generating", "phase": f"Đang tạo {len(candidates)} thumbnail", "progress": 8,
            "approved_concept_ids": [concept["id"] for concept in concepts],
            "candidates": candidates, "error": "", "cancel_requested": False,
        }))
        with ThreadPoolExecutor(max_workers=max(1, min(len(candidates), body.concurrency))) as pool:
            futures = [pool.submit(_run_candidate, project, run_id, candidate["id"], cancel_event) for candidate in candidates]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception:
                    pass
                snapshot = _read_run(project, run_id, normalize=False)
                done = sum(1 for x in snapshot.get("candidates", []) if x.get("status") in TERMINAL_CANDIDATE_STATES)
                _mutate_run(project, run_id, lambda run, count=done: run.update({
                    "progress": min(98, 8 + round((count / max(1, len(candidates))) * 90)),
                    "phase": f"Đã xử lý {count}/{len(candidates)} thumbnail",
                }))
        _rank_run(project, run_id)
    except Exception as exc:
        def fail(run):
            run.update({"status": "failed", "progress": 100, "error": str(exc)[:1600]})
            if isinstance(run.get("headline_qa"), dict) and run["headline_qa"].get("status") == "running":
                run["headline_qa"] = {
                    "status": "failed",
                    "target_locale": body.target_locale,
                    "error": str(exc)[:800],
                    "checked_at": _now(),
                }
        _mutate_run(project, run_id, fail)
    finally:
        _ACTIVE_THREADS.pop(run_id, None)
        _CANCEL_EVENTS.pop(run_id, None)


def _run_concept_regeneration(project: str, run_id: str, body: DirectorRunRequest, keep_concept_ids: list[str]) -> None:
    try:
        run = _read_run(project, run_id, normalize=False)
        current = [item for item in run.get("concepts", []) if isinstance(item, dict)]
        keep = set(keep_concept_ids)
        kept = [item for item in current if item.get("id") in keep]
        replace_slots = [item for item in current if item.get("id") not in keep]
        if not replace_slots:
            raise ValueError("Unselect at least one concept to regenerate it")
        scene_plan = _load_scene_plan(project)
        profile = _ensure_profile(body.channel_name, body.profile_id)
        rejected = _merge_concept_memory(run.get("rejected_concepts") or [], replace_slots)
        fresh = _plan_concepts(body, scene_plan, profile, excluded_concepts=_merge_concept_memory(kept, rejected))
        fresh, fresh_qa = _headline_qa(fresh, body)
        replacement_by_id = {}
        for slot, replacement in zip(replace_slots, fresh):
            replacement = dict(replacement)
            replacement["id"] = slot.get("id")
            replacement_by_id[replacement["id"]] = replacement
        merged = [replacement_by_id.get(item.get("id"), item) for item in current]
        _mutate_run(project, run_id, lambda item: item.update({
            "status": "awaiting_approval", "phase": "Đã tạo concept mới, chờ bạn duyệt", "progress": 100,
            "concepts": merged, "candidates": [], "approved_concept_ids": [x.get("id") for x in kept],
            "rejected_concepts": rejected,
            "headline_qa": {**fresh_qa, "kept_concept_ids": [x.get("id") for x in kept]}, "error": "",
        }))
    except Exception as exc:
        _mutate_run(project, run_id, lambda item: item.update({
            "status": "awaiting_approval", "phase": "Không thể tạo lại concept", "progress": 100, "error": str(exc)[:1200],
        }))
    finally:
        _ACTIVE_THREADS.pop(run_id, None)


def _run_custom_concept_expand(project: str, run_id: str, body: DirectorRunRequest, headline: str) -> None:
    try:
        run = _read_run(project, run_id, normalize=False)
        scene_plan = _load_scene_plan(project)
        profile = _ensure_profile(body.channel_name, body.profile_id)
        exclusions = _merge_concept_memory(run.get("rejected_concepts") or [], run.get("concepts") or [])
        concept = _expand_custom_concept(body, scene_plan, profile, headline, exclusions)
        _mutate_run(project, run_id, lambda item: item.update({
            "status": "awaiting_approval", "phase": "Đã thêm concept theo ý tưởng của bạn", "progress": 100,
            "concepts": [*(item.get("concepts") or []), concept], "candidates": [], "error": "",
        }))
    except Exception as exc:
        _mutate_run(project, run_id, lambda item: item.update({
            "status": "awaiting_approval", "phase": "Không thể triển khai ý tưởng concept", "progress": 100, "error": str(exc)[:1200],
        }))
    finally:
        _ACTIVE_THREADS.pop(run_id, None)


def _start_run_thread(project: str, run_id: str, body: DirectorRunRequest) -> None:
    cancel_event = threading.Event()
    thread = threading.Thread(target=_run_director_job, args=(project, run_id, body, cancel_event), daemon=True, name=f"thumb-director-{run_id}")
    _CANCEL_EVENTS[run_id] = cancel_event
    _ACTIVE_THREADS[run_id] = thread
    thread.start()


def _start_approved_generation(project: str, run_id: str, body: DirectorRunRequest, concept_ids: list[str]) -> None:
    cancel_event = threading.Event()
    thread = threading.Thread(
        target=_run_approved_generation,
        args=(project, run_id, body, concept_ids, cancel_event),
        daemon=True,
        name=f"thumb-generate-{run_id}",
    )
    _CANCEL_EVENTS[run_id] = cancel_event
    _ACTIVE_THREADS[run_id] = thread
    thread.start()


def _start_concept_regeneration(project: str, run_id: str, body: DirectorRunRequest, keep_concept_ids: list[str]) -> None:
    thread = threading.Thread(
        target=_run_concept_regeneration,
        args=(project, run_id, body, keep_concept_ids),
        daemon=True,
        name=f"thumb-replan-{run_id}",
    )
    _ACTIVE_THREADS[run_id] = thread
    thread.start()


def _start_custom_concept_expand(project: str, run_id: str, body: DirectorRunRequest, headline: str) -> None:
    thread = threading.Thread(
        target=_run_custom_concept_expand,
        args=(project, run_id, body, headline),
        daemon=True,
        name=f"thumb-custom-concept-{run_id}",
    )
    _ACTIVE_THREADS[run_id] = thread
    thread.start()


def _selected_path(project: str) -> Path:
    return _project_dir(project) / "thumbnails" / "selected_thumbnail.json"


def _apply_selected_to_publish_plan(project: str, image_path: Path) -> None:
    plan_path = _project_dir(project) / "publish_plan.json"
    if not plan_path.exists():
        return
    plan = _read_json(plan_path, {})
    changed = False
    for item in plan.get("items", []):
        if item.get("kind") == "long":
            item["thumbnail_path"] = str(image_path)
            item["thumbnail_url"] = f"/api/publish/youtube/thumbnail/{project}/{item.get('id')}"
            changed = True
    if changed:
        plan["updated_at"] = _now()
        _atomic_json(plan_path, plan)


@router.post("/api/thumbnails/director/runs")
def create_director_run(body: DirectorRunRequest):
    if not body.project or not body.story_title.strip() or not body.story_text.strip():
        raise HTTPException(400, "Project, story title, and full story text are required")
    if body.formula not in FORMULAS:
        body.formula = "mystery"
    body.concurrency = max(1, int(body.concurrency or 6))
    body.layout_mode = body.layout_mode if body.layout_mode in LAYOUT_MODES else LAYOUT_FREE
    body.upscale_mode = _normalize_upscale_mode(body.upscale_mode)
    body.session_id = _session_id(body.session_id)
    body.target_locale = re.sub(r"[^A-Za-z0-9_-]", "", (body.target_locale or "").strip())[:35] or "vi-VN"
    body.story_title = re.sub(r"\s+", " ", body.story_title.strip())[:120]
    body.headline_seed = re.sub(r"\s+", " ", (body.headline_seed or body.story_title).strip())[:90]
    body.channel_name = re.sub(r"\s+", " ", body.channel_name.strip())[:60]
    body.hook = re.sub(r"\s+", " ", body.hook.strip())[:80]
    body.genre_label = re.sub(r"\s+", " ", body.genre_label.strip())[:80]
    _project_dir(body.project)
    rejected_concepts = _prior_rejected_concepts(body.project, body.session_id, body.story_title)
    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    run = {
        "id": run_id,
        "project": body.project,
        "session_id": body.session_id,
        "target_locale": body.target_locale,
        "story_title": body.story_title,
        "channel_name": body.channel_name,
        "hook": body.hook,
        "genre_label": body.genre_label,
        "formula": body.formula,
        "layout_mode": body.layout_mode,
        "headline_seed": body.headline_seed,
        "model": body.model,
        "vision_enabled": body.vision_enabled,
        "vision_configured": bool(body.vision_enabled and _vision_ready(load_config())),
        "upscale_mode": body.upscale_mode,
        "request": body.model_copy(),
        "status": "queued",
        "phase": "Đang xếp hàng",
        "progress": 1,
        "headline_qa": {"status": "pending", "target_locale": body.target_locale},
        "concepts": [],
        "rejected_concepts": rejected_concepts,
        "candidates": [],
        "recommended_candidate_id": "",
        "selected_candidate_id": "",
        "cancel_requested": False,
        "error": "",
        "created_at": _now(),
        "updated_at": _now(),
    }
    run["request"] = body.model_dump()
    _write_run(run)
    _start_run_thread(body.project, run_id, body)
    return run


def _migrate_legacy_run_session(project: str, run: dict, session_id: str, story_title: str) -> bool:
    if not session_id or run.get("session_id"):
        return run.get("session_id") == session_id
    if _title_key(run.get("story_title")) != _title_key(story_title):
        return False
    run["session_id"] = session_id
    _write_run(run)
    history = _load_history(project)
    changed = False
    for item in history:
        if item.get("director_run_id") == run.get("id") and not item.get("session_id"):
            item["session_id"] = session_id
            changed = True
    if changed:
        _write_history(project, history)
    return True


@router.get("/api/thumbnails/director/runs/{project}")
def list_director_runs(project: str, session_id: str = "", story_title: str = ""):
    session_id = _session_id(session_id)
    root = _director_root(project) / "runs"
    items = []
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        run = _read_json(path, {})
        if not isinstance(run, dict) or not run:
            continue
        if session_id:
            if run.get("session_id") != session_id and not _migrate_legacy_run_session(project, run, session_id, story_title):
                continue
        items.append(run)
    return {"project": project, "runs": items[:30]}


@router.get("/api/thumbnails/director/runs/{project}/{run_id}")
def get_director_run(project: str, run_id: str):
    return _read_run(project, run_id)


@router.get("/api/thumbnails/director/runs/{project}/{run_id}/events")
def stream_director_run(project: str, run_id: str):
    def events():
        last_signature = ""
        idle_after_terminal = 0
        while True:
            try:
                run = _read_run(project, run_id)
            except HTTPException as exc:
                yield f"data: {json.dumps({'type': 'error', 'message': exc.detail}, ensure_ascii=False)}\n\n"
                break
            signature = json.dumps(run, ensure_ascii=False, sort_keys=True)
            if signature != last_signature:
                yield f"data: {json.dumps({'type': 'snapshot', 'run': run}, ensure_ascii=False)}\n\n"
                last_signature = signature
            if run.get("status") in TERMINAL_RUN_STATES:
                idle_after_terminal += 1
                if idle_after_terminal >= 2:
                    break
            time.sleep(0.55)
        yield "data: [DONE]\n\n"
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/thumbnails/director/runs/{project}/{run_id}/approve")
def approve_director_concepts(project: str, run_id: str, body: ConceptApprovalRequest):
    run = _read_run(project, run_id)
    if run.get("status") != "awaiting_approval":
        raise HTTPException(409, "Concepts are not waiting for approval")
    available = {str(item.get("id") or "") for item in run.get("concepts", [])}
    concept_ids = [item for item in body.concept_ids if item in available]
    if not concept_ids:
        raise HTTPException(400, "Choose at least one thumbnail concept")
    request = DirectorRunRequest(**run["request"])
    _mutate_run(project, run_id, lambda item: item.update({
        "status": "queued", "phase": "Đã chốt concept, đang chuẩn bị tạo ảnh", "progress": 2,
        "approved_concept_ids": concept_ids, "error": "", "cancel_requested": False,
    }))
    _start_approved_generation(project, run_id, request, concept_ids)
    return _read_run(project, run_id, normalize=False)


@router.post("/api/thumbnails/director/runs/{project}/{run_id}/concepts/regenerate")
def regenerate_director_concepts(project: str, run_id: str, body: ConceptRegenerateRequest):
    run = _read_run(project, run_id)
    if run.get("status") != "awaiting_approval":
        raise HTTPException(409, "Concepts are not waiting for approval")
    available = {str(item.get("id") or "") for item in run.get("concepts", [])}
    keep_ids = [item for item in body.keep_concept_ids if item in available]
    if len(keep_ids) >= len(available):
        raise HTTPException(400, "Unselect at least one concept to regenerate it")
    request = DirectorRunRequest(**run["request"])
    _mutate_run(project, run_id, lambda item: item.update({
        "status": "planning", "phase": "Đang tạo lại concept chưa chọn", "progress": 10, "error": "",
    }))
    _start_concept_regeneration(project, run_id, request, keep_ids)
    return _read_run(project, run_id, normalize=False)


@router.post("/api/thumbnails/director/runs/{project}/{run_id}/concepts/custom")
def add_custom_director_concept(project: str, run_id: str, body: CustomConceptRequest):
    run = _read_run(project, run_id)
    if run.get("status") != "awaiting_approval":
        raise HTTPException(409, "Concepts are not waiting for approval")
    headline = re.sub(r"\s+", " ", str(body.headline or "").strip())[:90]
    if not headline:
        raise HTTPException(400, "Enter a concept headline")
    request = DirectorRunRequest(**run["request"])
    _mutate_run(project, run_id, lambda item: item.update({
        "status": "planning", "phase": "AI đang triển khai ý tưởng concept của bạn", "progress": 20, "error": "",
    }))
    _start_custom_concept_expand(project, run_id, request, headline)
    return _read_run(project, run_id, normalize=False)


@router.post("/api/thumbnails/director/runs/{project}/{run_id}/cancel")
def cancel_director_run(project: str, run_id: str):
    event = _CANCEL_EVENTS.get(run_id)
    if event:
        event.set()
    return _mutate_run(project, run_id, lambda run: run.update({"cancel_requested": True, "phase": "Đang dừng các request còn lại"}))


@router.post("/api/thumbnails/director/runs/{project}/{run_id}/candidates/{candidate_id}/retry")
def retry_director_candidate(project: str, run_id: str, candidate_id: str, body: CandidateRetryRequest):
    run = _read_run(project, run_id)
    candidate = _candidate_by_id(run, candidate_id)
    if not candidate:
        raise HTTPException(404, "Thumbnail candidate not found")
    key = f"{run_id}:{candidate_id}"
    active = _ACTIVE_THREADS.get(key)
    if active and active.is_alive():
        raise HTTPException(409, "This candidate is already running")
    mode = (body.mode or "original").strip().lower()
    try:
        # A normal retry always inherits the latest Director composition and
        # hierarchy rules instead of replaying the historical serialized prompt.
        prompt = _refresh_director_candidate_prompt(project, run, candidate)
    except Exception as exc:
        raise HTTPException(422, f"Không thể dựng lại prompt thumbnail: {exc}") from exc
    if mode in {"safe_rule", "safe_llm"}:
        level = min(5, int(candidate.get("safe_level") or 0) + 1)
        if not candidate.get("visual_direction"):
            candidate["visual_direction"] = _visual_direction(_load_scene_plan(project))
        if mode == "safe_llm":
            concept = candidate.get("concept") if isinstance(candidate.get("concept"), dict) else {}
            request = StandaloneThumbnailRequest(
                project=project,
                title=candidate.get("headline") or run.get("story_title"),
                channel_name=run.get("channel_name") or "",
                genre_label=run.get("genre_label") or "",
                hook=run.get("hook") or "",
                context=(
                    f"{candidate['visual_direction']}\n"
                    f"Concept: {concept.get('name')}. Visual focus: {concept.get('visual_focus')}. "
                    f"Composition: {concept.get('composition')}. Emotion: {concept.get('emotion')}."
                )[:1800],
                formula=run.get("formula") or "mystery",
            )
            prompt = _build_safe_llm_thumbnail_prompt(request, level, load_config())
            prompt += f" Project visual direction (binding): {candidate['visual_direction']}"
            if run.get("layout_mode") == LAYOUT_CHANNEL_FORMAT:
                prompt += " " + _channel_format_rules(candidate.get("headline") or run.get("story_title") or "")
        else:
            prompt = _safe_candidate_prompt(run, candidate, level)
        candidate["safe_level"] = level
        candidate["prompt_mode"] = mode
    else:
        candidate["prompt_mode"] = "director"
    candidate["status"] = "queued"
    candidate["progress"] = 0
    candidate["error"] = ""
    run["status"] = "generating"
    run["phase"] = f"Đang retry {candidate_id}"
    run["progress"] = min(99, int(run.get("progress") or 0))
    _write_run(run)
    cancel_event = threading.Event()

    def job():
        try:
            _run_candidate(project, run_id, candidate_id, cancel_event, prompt)
            _rank_run(project, run_id)
        finally:
            _ACTIVE_THREADS.pop(key, None)

    thread = threading.Thread(target=job, daemon=True, name=f"thumb-retry-{candidate_id}")
    _ACTIVE_THREADS[key] = thread
    thread.start()
    return _read_run(project, run_id, normalize=False)


@router.post("/api/thumbnails/director/runs/{project}/{run_id}/candidates/{candidate_id}/upscale")
def upscale_director_candidate(project: str, run_id: str, candidate_id: str, body: CandidateUpscaleRequest):
    run = _read_run(project, run_id)
    candidate = _candidate_by_id(run, candidate_id)
    if not candidate or not candidate.get("filename"):
        raise HTTPException(404, "Generated thumbnail candidate not found")
    mode = _normalize_upscale_mode(body.mode)
    if mode == "original":
        raise HTTPException(400, "Choose 2K or 4K to increase sharpness")
    key = f"{run_id}:{candidate_id}:upscale"
    active = _ACTIVE_THREADS.get(key)
    if active and active.is_alive():
        raise HTTPException(409, "This candidate is already being upscaled")

    old_filename = candidate["filename"]
    old_status = candidate.get("status") if candidate.get("status") in {"succeeded", "needs_review"} else "succeeded"
    source_filename = candidate.get("source_filename") or old_filename
    try:
        source_path = _resolve_thumb(project, source_filename)
        if not source_path.exists():
            source_filename = old_filename
    except HTTPException:
        source_filename = old_filename

    candidate["status"] = "upscaling"
    candidate["progress"] = 92
    candidate["error"] = ""
    run["status"] = "generating"
    run["phase"] = f"Đang tăng nét {candidate_id} lên {mode.upper()}"
    run["progress"] = min(99, int(run.get("progress") or 0))
    _write_run(run)

    def job():
        try:
            metadata = {
                "title": candidate.get("headline") or run.get("story_title"),
                "hook": run.get("hook") or "",
                "channel_name": run.get("channel_name") or "",
                "genre_label": run.get("genre_label") or "",
                "formula": run.get("formula") or "mystery",
                "prompt": candidate.get("prompt") or "",
                "prompt_mode": candidate.get("prompt_mode") or "director",
                "safe_level": candidate.get("safe_level") or 0,
                "reference_files": candidate.get("reference_files") or [],
                "vision": candidate.get("vision") or {},
                "director_run_id": run_id,
                "director_candidate_id": candidate_id,
            }
            item = _upscale_existing_thumbnail(project, source_filename, mode, metadata)
            attempts = list(candidate.get("attempts") or [])
            attempts.append({
                "kind": f"upscale_{mode}",
                "filename": item["filename"],
                "engine": item.get("upscale_engine"),
                "created_at": _now(),
            })
            _update_candidate(project, run_id, candidate_id, {
                "status": old_status,
                "progress": 100,
                "filename": item["filename"],
                "saved_path": item["saved_path"],
                "source_filename": item.get("source_filename") or source_filename,
                "upscale_mode": item.get("upscale_mode") or mode,
                "upscale_engine": item.get("upscale_engine") or "",
                "upscale": item.get("upscale") or {},
                "width": item.get("width") or 0,
                "height": item.get("height") or 0,
                "attempts": attempts,
                "error": "",
            })
            selected = _read_json(_selected_path(project), {})
            if selected.get("filename") == old_filename:
                selected_path = _resolve_thumb(project, item["filename"])
                selected = {"project": project, "filename": selected_path.name, "path": str(selected_path), "selected_at": _now()}
                _atomic_json(_selected_path(project), selected)
                _apply_selected_to_publish_plan(project, selected_path)
        except Exception as exc:
            _update_candidate(project, run_id, candidate_id, {
                "status": old_status,
                "progress": 100,
                "error": f"Upscale failed: {str(exc)[:1000]}",
            })
        finally:
            _rank_run(project, run_id)
            _ACTIVE_THREADS.pop(key, None)

    thread = threading.Thread(target=job, daemon=True, name=f"thumb-upscale-{candidate_id}")
    _ACTIVE_THREADS[key] = thread
    thread.start()
    return _read_run(project, run_id, normalize=False)


@router.get("/api/thumbnail-profiles")
def list_thumbnail_profiles():
    profiles = []
    for path in _profile_root().glob("*/profile.json"):
        data = _read_json(path, {})
        if isinstance(data, dict) and data:
            profiles.append(data)
    profiles.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return {"profiles": profiles}


@router.post("/api/thumbnail-profiles")
def create_thumbnail_profile(body: ChannelProfileRequest):
    if not body.channel_name.strip():
        raise HTTPException(400, "Channel name is required")
    return _ensure_profile(body.channel_name)


@router.post("/api/thumbnail-profiles/{profile_id}/references")
def add_thumbnail_profile_reference(profile_id: str, body: ProfileReferenceRequest):
    profile = _load_profile(profile_id)
    source = _resolve_thumb(body.project, body.filename)
    if not source.exists():
        raise HTTPException(404, "Thumbnail file not found")
    reference_id = f"ref_{uuid.uuid4().hex[:8]}"
    filename = f"{reference_id}.jpg"
    target = _profile_dir(profile_id) / "references" / filename
    shutil.copy2(source, target)
    item = {
        "id": reference_id,
        "filename": filename,
        "source_project": body.project,
        "source_filename": body.filename,
        "created_at": _now(),
    }
    profile.setdefault("references", []).insert(0, item)
    profile["references"] = profile["references"][:16]
    if not profile.get("primary_reference_id"):
        profile["primary_reference_id"] = reference_id
    return _save_profile(profile)


@router.patch("/api/thumbnail-profiles/{profile_id}/primary")
def set_thumbnail_profile_primary(profile_id: str, body: ProfilePrimaryRequest):
    profile = _load_profile(profile_id)
    if not any(x.get("id") == body.reference_id for x in profile.get("references", [])):
        raise HTTPException(404, "Profile reference not found")
    profile["primary_reference_id"] = body.reference_id
    return _save_profile(profile)


@router.get("/api/thumbnail-profiles/{profile_id}/references/{reference_id}")
def serve_thumbnail_profile_reference(profile_id: str, reference_id: str):
    profile = _load_profile(profile_id)
    path = _profile_reference_path(profile, reference_id)
    if not path:
        raise HTTPException(404, "Profile reference not found")
    return FileResponse(str(path), media_type="image/jpeg", filename=path.name)


@router.post("/api/thumbnails/{project}/selected")
def select_project_thumbnail(project: str, body: SelectedThumbnailRequest):
    path = _resolve_thumb(project, body.filename)
    if not path.exists():
        raise HTTPException(404, "Thumbnail file not found")
    selected = {"project": project, "filename": path.name, "path": str(path), "selected_at": _now()}
    _atomic_json(_selected_path(project), selected)
    _apply_selected_to_publish_plan(project, path)
    return selected


@router.get("/api/thumbnails/{project}/selected")
def get_project_selected_thumbnail(project: str):
    data = _read_json(_selected_path(project), {})
    if not data:
        return {"project": project, "filename": "", "path": ""}
    path = Path(data.get("path") or "")
    if not path.exists():
        return {"project": project, "filename": "", "path": ""}
    return data


@router.post("/api/thumbnail-vision/test")
def test_thumbnail_vision():
    cfg = load_config()
    if not _vision_ready(cfg):
        raise HTTPException(400, "Configure thumbnail Vision base URL and model first")
    reference = _template_reference_path("Người được chọn")
    if not reference:
        raise HTTPException(404, "No thumbnail reference image is available for the Vision test")
    body = DirectorRunRequest(project="Người được chọn", story_title="Vision test", story_text="Vision test")
    concept = {"headline": "", "name": "Connection test", "visual_focus": "", "spoiler_boundary": ""}
    result = _vision_review(reference, body, concept)
    return {"ok": True, "result": result}
