"""SEO packaging manifest used exclusively by long-form stick-figure projects.

The generic YouTube metadata and thumbnail systems deliberately do not import
this module.  They opt in only after verifying that the stored scene plan is a
``stick_figure`` plan, keeping existing story and Shorts pipelines unchanged.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from story_service import generate_text, generate_text_openai_compatible
from tts_service import PROJECT_TITLE_VALIDATOR, get_project_dir, load_config


router = APIRouter()
_SKILL_PATH = Path(__file__).resolve().parents[1] / "skill-youtube-nguoi-que-packaging-v2.md"


class PackagingRequest(BaseModel):
    story_title: str
    story_text: str
    target_locale: str = "vi-VN"
    model: str = ""


class PackagingTitleRequest(BaseModel):
    title: str


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _project_dir(project: str) -> Path:
    if not project or not PROJECT_TITLE_VALIDATOR.match(project):
        raise HTTPException(400, "Invalid project name")
    path = get_project_dir(project)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _scene_plan(project: str) -> dict:
    path = _project_dir(project) / "scenes" / "scene_plan.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def is_stick_figure_project(project: str) -> bool:
    """Strict scope guard: a manifest exists only for an approved stick plan."""
    return str(_scene_plan(project).get("mode") or "").strip().lower() == "stick_figure"


def _manifest_path(project: str) -> Path:
    return _project_dir(project) / "stick_figure_packaging.json"


def _read_manifest(project: str) -> dict:
    path = _manifest_path(project)
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def load_stick_figure_packaging(project: str) -> dict | None:
    """Return a manifest only when its project is still a stick-figure plan."""
    if not is_stick_figure_project(project):
        return None
    value = _read_manifest(project)
    return value or None


def stick_figure_packaging_is_current(manifest: dict, story_title: str, story_text: str, target_locale: str = "") -> bool:
    """Prevent an older script/locale package leaking into a later render."""
    if not isinstance(manifest, dict) or manifest.get("status") != "ready":
        return False
    locale = re.sub(r"[^A-Za-z0-9_-]", "", target_locale or manifest.get("target_locale") or "vi-VN")[:35] or "vi-VN"
    title = re.sub(r"\s+", " ", story_title or "").strip()[:160]
    story = re.sub(r"\s+", " ", story_text or "").strip()
    if not title or not story:
        return False
    try:
        expected = _hash(title, story, locale, _skill_text())
    except RuntimeError:
        return False
    return manifest.get("source_hash") == expected


def _skill_text() -> str:
    try:
        return _SKILL_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Không đọc được skill YouTube người que: {exc}") from exc


def _hash(*parts: str) -> str:
    return hashlib.sha256("\n\0".join(parts).encode("utf-8")).hexdigest()


def _youtube_llm(system: str, user: str, model_override: str = "", max_tokens: int = 5000) -> str:
    cfg = load_config()
    provider = str(cfg.get("youtube_metadata_provider") or "default").strip().lower()
    configured_model = str(cfg.get("youtube_metadata_model") or "").strip()
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
    return generate_text(
        system,
        user,
        (model_override or configured_model or cfg.get("default_story_model") or "claude-sonnet-4-6").strip(),
        cfg.get("anthropic_api_key", ""),
        cfg.get("ollama_url", "http://localhost:11434"),
        max_tokens=max_tokens,
        cli_timeout_sec=90,
    )


def _json_object(raw: str) -> dict:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text
        text = re.sub(r"^\s*json\s*", "", text, flags=re.I)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Packaging AI did not return a JSON object")
    return data


def _normalise_titles(raw: object, fallback: str) -> list[dict]:
    seen, items = set(), []
    for index, item in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(item, dict):
            continue
        text = re.sub(r"\s+", " ", str(item.get("text") or item.get("title") or "")).strip()[:110]
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        kind = str(item.get("kind") or ("balanced" if index == 0 else "curiosity")).strip()[:32]
        items.append({"kind": kind, "text": text, "recommended": bool(item.get("recommended"))})
    if not items:
        items = [{"kind": "balanced", "text": fallback[:110] or "Video người que", "recommended": True}]
    if not any(item["recommended"] for item in items):
        items[0]["recommended"] = True
    return items[:3]


def _normalise_hashtags(raw: object) -> list[str]:
    values, seen = [], set()
    for value in raw if isinstance(raw, list) else []:
        tag = re.sub(r"\s+", "", str(value or "").strip())
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = "#" + tag
        if len(tag) <= 1 or tag.casefold() in seen:
            continue
        seen.add(tag.casefold())
        values.append(tag[:80])
    return values[:5]


def _normalise_briefs(raw: object, locale: str) -> list[dict]:
    result = []
    for index, item in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(item, dict):
            continue
        headline = re.sub(r"\s+", " ", str(item.get("headline") or "")).strip()[:48]
        focus = re.sub(r"\s+", " ", str(item.get("visual_focus") or item.get("subject") or "")).strip()[:750]
        if not headline and not focus:
            continue
        result.append({
            "id": f"pack_{index + 1}",
            "name": str(item.get("name") or f"Packaging concept {index + 1}").strip()[:80],
            "headline": headline,
            "core_mystery": str(item.get("core_mystery") or "").strip()[:500],
            "visual_focus": focus,
            "composition": str(item.get("composition") or "one oversized hero subject with one supporting clue").strip()[:500],
            "emotion": str(item.get("emotion") or "curiosity").strip()[:180],
            "palette": str(item.get("palette") or "high-contrast, controlled vivid color accents").strip()[:220],
            "overlay_text": str(item.get("overlay_text") or headline).strip()[:48],
            "locale": locale,
        })
    return result[:3]


def _build_manifest(project: str, body: PackagingRequest) -> dict:
    if not is_stick_figure_project(project):
        raise HTTPException(409, "SEO packaging chỉ áp dụng cho pipeline Người que")
    title = re.sub(r"\s+", " ", body.story_title or project).strip()[:160]
    story = re.sub(r"\s+", " ", body.story_text or "").strip()
    if not story:
        raise HTTPException(400, "Thiếu kịch bản để tạo gói SEO Người que")
    locale = re.sub(r"[^A-Za-z0-9_-]", "", body.target_locale or "vi-VN")[:35] or "vi-VN"
    skill = _skill_text()
    source_hash = _hash(title, story, locale, skill)
    existing = _read_manifest(project)
    if existing.get("source_hash") == source_hash and existing.get("status") == "ready":
        return existing
    system = (
        "You produce YouTube packaging for a long-form 16:9 stick-figure explainer. "
        "Follow the supplied production skill as binding policy. Write every user-facing field in the target output locale. "
        "Stay truthful to the supplied story: do not turn uncertain claims into facts, invent evidence, reveal the final resolution, or add unrelated tags. "
        "Return strict JSON only."
    )
    schema = '''{
  "core_promise":"...","central_question":"...","strongest_clue":"...","emotion":"...","keywords":["..."],
  "titles":[{"kind":"balanced|search|curiosity","text":"...","recommended":true}],
  "description":"180-350 word natural mini-trailer, no markdown heading, no hashtags in this field",
  "hashtags":["#..."],
  "thumbnail_concepts":[{"name":"...","headline":"2-4 words","core_mystery":"...","visual_focus":"one large subject/action/evidence","composition":"...","emotion":"...","palette":"...","overlay_text":"2-4 words"}]
}'''
    user = (
        f"Target output locale: {locale}\nStory title: {title}\n\n"
        f"FULL NARRATION:\n{story[:50000]}\n\n"
        f"PACKAGING SKILL (binding):\n{skill[:30000]}\n\n"
        "Produce exactly three genuinely different thumbnail concepts, three title options (one recommended), "
        "one description, and 3-5 hashtags. The thumbnail concepts must each use a clear oversized subject, "
        "a visible action/evidence clue, high contrast and controlled vivid colors; do not use generic dark horror styling.\n"
        f"Required JSON schema:\n{schema}"
    )
    raw = _youtube_llm(system, user, body.model, max_tokens=5600)
    try:
        data = _json_object(raw)
    except Exception:
        repair = _youtube_llm(
            "Repair this response to valid JSON only. Preserve meaning and obey the exact schema.",
            f"Schema:\n{schema}\n\nResponse:\n{raw[:22000]}",
            body.model,
            max_tokens=4200,
        )
        data = _json_object(repair)
    titles = _normalise_titles(data.get("titles"), title)
    selected = next((item["text"] for item in titles if item["recommended"]), titles[0]["text"])
    description = str(data.get("description") or "").strip()[:6000]
    hashtags = _normalise_hashtags(data.get("hashtags"))
    manifest = {
        "project": project,
        "scope": "stick_figure_long_form",
        "status": "ready",
        "source_hash": source_hash,
        "skill_hash": hashlib.sha256(skill.encode("utf-8")).hexdigest(),
        "target_locale": locale,
        "story_title": title,
        "core_promise": str(data.get("core_promise") or "").strip()[:600],
        "central_question": str(data.get("central_question") or "").strip()[:500],
        "strongest_clue": str(data.get("strongest_clue") or "").strip()[:500],
        "emotion": str(data.get("emotion") or "curiosity").strip()[:180],
        "keywords": [str(item).strip()[:80] for item in (data.get("keywords") or []) if str(item).strip()][:6],
        "titles": titles,
        "selected_title": selected,
        "description": description,
        "hashtags": hashtags,
        "description_with_hashtags": (description + ("\n\n" + " ".join(hashtags) if hashtags else "")).strip(),
        "thumbnail_concepts": _normalise_briefs(data.get("thumbnail_concepts"), locale),
        "created_at": _now(),
        "updated_at": _now(),
    }
    _manifest_path(project).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def ensure_stick_figure_packaging(project: str, story_title: str, story_text: str, target_locale: str, model: str = "") -> dict | None:
    if not is_stick_figure_project(project):
        return None
    return _build_manifest(project, PackagingRequest(story_title=story_title, story_text=story_text, target_locale=target_locale, model=model))


@router.get("/api/stick-figure/packaging/{project}")
def get_packaging(project: str):
    manifest = load_stick_figure_packaging(project)
    if manifest is None:
        raise HTTPException(404, "Project này không dùng SEO packaging Người que")
    return manifest


@router.post("/api/stick-figure/packaging/{project}/generate")
def generate_packaging(project: str, body: PackagingRequest):
    return _build_manifest(project, body)


@router.patch("/api/stick-figure/packaging/{project}/selected-title")
def select_packaging_title(project: str, body: PackagingTitleRequest):
    manifest = load_stick_figure_packaging(project)
    if manifest is None:
        raise HTTPException(404, "Project này không dùng SEO packaging Người que")
    allowed = {str(item.get("text") or "") for item in manifest.get("titles") or []}
    selected = re.sub(r"\s+", " ", body.title or "").strip()
    if selected not in allowed:
        raise HTTPException(400, "Title không thuộc gói SEO hiện tại")
    manifest["selected_title"] = selected
    manifest["updated_at"] = _now()
    _manifest_path(project).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
