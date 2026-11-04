"""Scene-image video generation service for LucyLab."""
import base64
import concurrent.futures
import json
import math
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Generator

import requests
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from subtitle_service import build_subtitle_cues, load_timing_file
from story_service import generate_text, generate_text_openai_compatible, _parse_json_response
from tts_service import OUTPUT_DIR, TEMP_DIR, get_project_dir, load_config, split_text_for_timing
from video_service import get_video_duration, _encoder_args, _parse_ffmpeg_time, _progress_event, validate_video_integrity
from image_generation_service import generate_image_events, image_provider_config
from scene_reveal_renderer import (
    FPS as REVEAL_FPS,
    FRAME_HEIGHT as REVEAL_HEIGHT,
    FRAME_WIDTH as REVEAL_WIDTH,
    PAPER_COLOR,
    RevealConfigError,
    SceneRevealAssets,
    normalize_scene_reveal,
    normalize_scene_reveal_override,
    prepare_scene_reveal_assets,
    resolve_scene_reveal,
    reveal_frame_count,
    reveal_is_enabled,
    scene_frame_counts,
    split_reveal_frames,
)


router = APIRouter(prefix="/api/scenes", tags=["scene-images"])

# Legacy scene plans use ``scene_001`` while Visual Plan V2 uses ``vb_001``.
# Both names are generated internally and must be accepted when selecting or
# recovering a scene image from disk.
_SAFE_IMAGE = re.compile(r"^(?:scene|vb)_\d{3,4}(?:_[A-Za-z0-9_-]+)?\.(png|jpg|jpeg|webp)$", re.IGNORECASE)
_SAFE_REF_IMAGE = re.compile(r"^[A-Za-z0-9_-]+(?:_[0-9]{8}_[0-9]{6}_[A-Za-z0-9_-]+)?\.(png|jpg|jpeg|webp)$", re.IGNORECASE)
_cancelled: set[str] = set()
_ACTIVE_SCENE_GENERATION_PROJECTS: set[str] = set()
_ACTIVE_SCENE_GENERATION_LOCK = threading.RLock()
_PLAN_IO_LOCK = threading.RLock()


class ScenePlanRequest(BaseModel):
    project: str
    audio_file: str
    story_text: str = ""
    story_title: str = ""
    visual_style: str = ""
    target_scene_sec: float = 12
    min_scene_sec: float = 7
    max_scene_sec: float = 18
    max_scenes: int = 120


class SceneRevealConfig(BaseModel):
    enabled: bool = False
    effect: str = "brush"
    duration_sec: float = 1.2
    pattern: str = "auto"
    actor: str = "none"


class SceneRevealOverride(BaseModel):
    mode: str = "inherit"
    enabled: bool | None = None
    effect: str | None = None
    duration_sec: float | None = None
    pattern: str | None = None
    actor: str | None = None


class ScenePatchRequest(BaseModel):
    text: str | None = None
    prompt: str | None = None
    director_prompt: str | None = None
    safe_prompt: str | None = None
    safe_rule_prompt: str | None = None
    safe_llm_prompt: str | None = None
    active_prompt_mode: str | None = None
    start_sec: float | None = None
    end_sec: float | None = None
    reveal_override: SceneRevealOverride | None = None


class SceneGenerateRequest(BaseModel):
    project: str
    scene_ids: list[str] | None = None
    retry_failed: bool = False
    concurrency: int = 1


class SceneSafePromptRequest(BaseModel):
    scene_ids: list[str] | None = None
    only_failed: bool = True
    mode: str = "rule"


class SceneSelectImageRequest(BaseModel):
    image_file: str


class DirectorPrepareRequest(BaseModel):
    audio_file: str
    story_text: str = ""
    story_title: str = ""
    visual_style: str = ""
    target_scene_sec: float = 12
    min_scene_sec: float = 7
    max_scene_sec: float = 18
    max_scenes: int = 120
    use_visual_plan: bool = True
    visual_plan_instruction: str = ""


class StickFigureVisualPlanRequest(DirectorPrepareRequest):
    content_blueprint: dict
    style_reference_id: str = ""
    auto_prepare_references: bool = False
    output_locale: str = "vi-VN"


class StickFigureVisualPlanApproveRequest(BaseModel):
    auto_prepare_references: bool = False


class DirectorReferenceGenerateRequest(BaseModel):
    reference_ids: list[str] | None = None
    retry_failed: bool = False
    mode: str = "sequential"
    concurrency: int | None = None


class DirectorReferenceSelectRequest(BaseModel):
    image_file: str


class DirectorRegeneratePromptRequest(BaseModel):
    note: str = ""


class SceneComposeRequest(BaseModel):
    project: str
    audio_file: str
    output_name: str = "final_video_scene.mp4"
    video_encoder: str = "nvidia_nvenc"
    quality: str = "high"
    overwrite_mode: str = "version"
    scene_reveal: SceneRevealConfig = Field(default_factory=SceneRevealConfig)


def _require_project(project: str) -> Path:
    """Resolve a project created by the app without imposing a name whitelist.

    Project titles are user-facing text and can be Vietnamese, Korean, or any
    other locale.  The old Windows-character regex rejected otherwise valid
    in-memory/session names before the visual pipeline even started.  Path
    containment is the security boundary that matters here.
    """
    name = str(project or "").strip()
    if not name:
        raise HTTPException(400, "Project is required")
    path = get_project_dir(name)
    try:
        path.resolve().relative_to(OUTPUT_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(403, "Access denied") from exc
    return path


def _scene_dir(project: str) -> Path:
    path = _require_project(project) / "scenes"
    path.mkdir(parents=True, exist_ok=True)
    (path / "images").mkdir(parents=True, exist_ok=True)
    (path / "references").mkdir(parents=True, exist_ok=True)
    return path


def _reference_dir(project: str, ref_type: str = "") -> Path:
    root = _scene_dir(project) / "references"
    ref_type = _safe_stem(ref_type or "misc", "misc")
    path = root / ref_type
    path.mkdir(parents=True, exist_ok=True)
    return path


def _plan_path(project: str) -> Path:
    return _scene_dir(project) / "scene_plan.json"


def _plan_backup_path(project: str) -> Path:
    """Last complete plan snapshot, used only to recover an interrupted write."""
    return _plan_path(project).with_name("scene_plan.last-good.json")


def _read_plan(project: str) -> dict:
    path = _plan_path(project)
    if not path.exists():
        raise HTTPException(404, "Scene plan not found")
    with _PLAN_IO_LOCK:
        try:
            with open(path, encoding="utf-8") as f:
                plan = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            # A process from an older hot-reload generation could still have
            # been writing the old file format.  Never silently turn that into
            # an empty plan: restore the last verified snapshot when present.
            backup = _plan_backup_path(project)
            try:
                with open(backup, encoding="utf-8") as f:
                    plan = json.load(f)
            except (OSError, json.JSONDecodeError):
                raise HTTPException(
                    409,
                    "Scene plan was interrupted before it finished saving. "
                    "Its generated images are still kept on disk; recreate the Visual Plan to reconnect them.",
                ) from exc
            _write_plan(project, plan)
    if _normalize_plan(project, plan):
        _write_plan(project, plan)
    return plan


def _write_plan(project: str, plan: dict) -> dict:
    path = _plan_path(project)
    with _PLAN_IO_LOCK:
        plan["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        # Scene workers and UI refreshes can overlap. Replacing a completed
        # temp file prevents readers from ever seeing an empty/partial plan.
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
        # Keep one known-good JSON document. It lets a later interrupted
        # legacy worker write be recovered without losing links to images.
        backup_path = _plan_backup_path(project)
        backup_temp = backup_path.with_name(f".{backup_path.name}.{uuid.uuid4().hex}.tmp")
        shutil.copyfile(path, backup_temp)
        os.replace(backup_temp, backup_path)
    return plan


def _assign_stick_plan_asset_scope(plan: dict, *, reset_generated_assets: bool) -> str:
    """Bind generated assets to this Visual Plan revision, never to shared IDs."""
    scope = _asset_scope(plan.get("asset_scope")) or _new_plan_asset_scope()
    plan["asset_scope"] = scope
    for ref in plan.get("references") or []:
        if not isinstance(ref, dict):
            continue
        ref["asset_scope"] = scope
        is_uploaded_style = ref.get("id") == "style_source" and ref.get("source") == "uploaded"
        if reset_generated_assets and not is_uploaded_style:
            ref["status"] = "pending"
            ref["image_file"] = ""
            ref["image_history"] = []
            ref["error"] = ""
            ref["progress"] = 0
            for key in ("task_id", "task_status", "last_candidate_image_file", "started_at", "finished_at"):
                ref.pop(key, None)
    for scene in plan.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        scene["asset_scope"] = scope
        if reset_generated_assets:
            scene["status"] = "pending"
            scene["image_file"] = ""
            scene["image_history"] = []
            scene["error"] = ""
            scene["progress"] = 0
            for key in ("task_id", "task_status", "last_candidate_image_file", "last_generation_error", "started_at", "finished_at", "attempt"):
                scene.pop(key, None)
    return scope


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _new_plan_asset_scope() -> str:
    """Namespace generated assets so a rebuilt Visual Plan never adopts an old one."""
    return f"vp_{uuid.uuid4().hex[:12]}"


def _asset_scope(value: object) -> str:
    scope = _safe_stem(str(value or ""), "")
    return scope if scope.startswith("vp_") else ""


def _new_scene_image_filename(scene: dict) -> str:
    scope = _asset_scope(scene.get("asset_scope"))
    prefix = f"{scene['id']}_{scope}" if scope else str(scene["id"])
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.png"


def _image_file_valid(project: str, filename: str) -> bool:
    if not filename or not _SAFE_IMAGE.match(filename):
        return False
    path = _scene_dir(project) / "images" / filename
    return path.exists() and path.stat().st_size > 0


def _history_items(scene: dict) -> list[dict]:
    history = scene.get("image_history")
    if not isinstance(history, list):
        history = []
        scene["image_history"] = history
    return history


def _history_has_file(scene: dict, filename: str) -> bool:
    return any(item.get("image_file") == filename for item in _history_items(scene))


def _add_image_history(project: str, scene: dict, filename: str, *, selected: bool, meta: dict | None = None) -> dict:
    if not _image_file_valid(project, filename):
        raise RuntimeError(f"Scene image is missing or empty: {filename}")
    path = _scene_dir(project) / "images" / filename
    history = _history_items(scene)
    existing = next((item for item in history if item.get("image_file") == filename), None)
    item = existing or {}
    item.update({
        "id": Path(filename).stem,
        "image_file": filename,
        "created_at": item.get("created_at") or _now_iso(),
        "size_bytes": path.stat().st_size,
    })
    if meta:
        item.update({k: v for k, v in meta.items() if v not in (None, "")})
    if existing is None:
        history.append(item)
    if selected:
        for other in history:
            other["selected"] = other.get("image_file") == filename
        scene["image_file"] = filename
        scene["status"] = "succeeded"
        scene["progress"] = 100
        scene["task_status"] = "succeeded"
    return item


def _ensure_current_image_in_history(project: str, scene: dict, *, selected: bool = True) -> bool:
    filename = str(scene.get("image_file") or "")
    if not filename or not _image_file_valid(project, filename):
        return False
    existed = _history_has_file(scene, filename)
    _add_image_history(
        project,
        scene,
        filename,
        selected=selected,
        meta={
            "source": "current",
            "prompt": scene.get("prompt", ""),
            "task_id": scene.get("task_id", ""),
        },
    )
    return not existed


def _recover_scene_variants(project: str, scene: dict) -> bool:
    scene_id = str(scene.get("id") or "")
    if not scene_id:
        return False
    changed = False
    images_dir = _scene_dir(project) / "images"
    scope = _asset_scope(scene.get("asset_scope"))
    pattern = f"{scene_id}_{scope}_*" if scope else f"{scene_id}_*"
    for path in sorted(images_dir.glob(pattern)):
        filename = path.name
        if not _SAFE_IMAGE.match(filename) or path.stat().st_size <= 0:
            continue
        if _history_has_file(scene, filename):
            continue
        _add_image_history(project, scene, filename, selected=False, meta={
            "source": "recovered",
            "prompt": scene.get("prompt", ""),
        })
        changed = True
    return changed


def _select_scene_image(project: str, scene: dict, filename: str) -> None:
    if not _SAFE_IMAGE.match(filename):
        raise HTTPException(400, "Invalid image filename")
    if not _image_file_valid(project, filename):
        raise HTTPException(404, "Scene image not found")
    _add_image_history(project, scene, filename, selected=True, meta={"source": "selected"})
    scene["error"] = ""
    scene["finished_at"] = _now_iso()


def _scene_image_path(project: str, scene: dict) -> tuple[str, Path] | tuple[str, None]:
    candidates: list[str] = []
    if scene.get("image_file"):
        candidates.append(str(scene["image_file"]))
    for item in scene.get("image_history") or []:
        if isinstance(item, dict) and item.get("selected") and item.get("image_file"):
            candidates.append(str(item["image_file"]))
    if scene.get("id") and not _asset_scope(scene.get("asset_scope")):
        candidates.append(f"{scene['id']}.png")
    for item in scene.get("image_history") or []:
        if isinstance(item, dict) and item.get("image_file"):
            candidates.append(str(item["image_file"]))
    seen: set[str] = set()
    for filename in candidates:
        if filename in seen or not _SAFE_IMAGE.match(filename):
            continue
        seen.add(filename)
        path = _scene_dir(project) / "images" / filename
        if path.exists() and path.stat().st_size > 0:
            return filename, path
    return "", None


def _scene_has_valid_image(project: str, scene: dict) -> bool:
    _, path = _scene_image_path(project, scene)
    return path is not None


_ORPHANED_TASK_MAX_AGE_SEC = 10 * 60


def _mark_orphaned_running_tasks(project: str, plan: dict) -> bool:
    """Close stale in-memory tasks without making provider calls during reads.

    Loading a scene plan must be fast and read-only from the provider's point
    of view.  After a restart, quota exhaustion, or a hot-reload, old tasks can
    remain marked ``running`` even though no local worker owns them anymore.
    Polling every such task from GET /api/scenes can block the UI for minutes.
    Keep live projects untouched; otherwise mark sufficiently old orphaned
    tasks failed so the user can retry them explicitly.
    """
    with _ACTIVE_SCENE_GENERATION_LOCK:
        if project in _ACTIVE_SCENE_GENERATION_PROJECTS:
            return False

    now = time.time()
    changed = False
    items = list(plan.get("scenes") or []) + list(plan.get("references") or [])
    for item in items:
        if not isinstance(item, dict) or item.get("status") != "running":
            continue
        stamp = item.get("started_at") or item.get("last_progress_at") or ""
        try:
            started = time.mktime(time.strptime(str(stamp), "%Y-%m-%dT%H:%M:%S"))
        except (TypeError, ValueError, OverflowError):
            # Missing timestamps are only considered orphaned when a task id
            # exists; a freshly queued local item has no task id yet.
            started = 0 if item.get("task_id") else now
        if now - started < _ORPHANED_TASK_MAX_AGE_SEC:
            continue
        item["status"] = "failed"
        item["task_status"] = "failed"
        item["progress"] = 0
        item["finished_at"] = _now_iso()
        item["error"] = item.get("error") or "Tác vụ cũ đã bị gián đoạn hoặc hết hạn; hãy Retry khi provider sẵn sàng."
        changed = True
    return changed


def _normalize_plan(project: str, plan: dict) -> bool:
    changed = False
    for ref in plan.get("references", []):
        if isinstance(ref, dict):
            changed = _recover_reference_variants(project, ref) or changed
    for scene in plan.get("scenes", []):
        changed = _recover_scene_variants(project, scene) or changed
        filename, path = _scene_image_path(project, scene)
        if path is not None:
            history_changed = not _history_has_file(scene, filename)
            _add_image_history(project, scene, filename, selected=True, meta={
                "source": "normalized",
                "prompt": scene.get("prompt", ""),
                "task_id": scene.get("task_id", ""),
            })
            if history_changed or scene.get("status") != "succeeded" or scene.get("image_file") != filename or scene.get("progress") != 100:
                scene["status"] = "succeeded"
                scene["image_file"] = filename
                scene["error"] = ""
                scene["progress"] = 100
                scene["task_status"] = "succeeded"
                changed = True
            if scene.get("error"):
                scene["last_generation_error"] = scene.get("last_generation_error") or scene.get("error")
                scene["error"] = ""
                changed = True
            continue

        if scene.get("image_file"):
            bad_path = _scene_dir(project) / "images" / str(scene["image_file"])
            if bad_path.exists() and bad_path.stat().st_size <= 0:
                try:
                    bad_path.unlink(missing_ok=True)
                except OSError:
                    pass
                scene["error"] = "Generated image file was empty"
                scene["image_file"] = ""
                scene["status"] = "failed"
                scene["progress"] = 0
                changed = True
            elif scene.get("status") == "succeeded":
                scene["error"] = "Generated image file is missing"
                scene["image_file"] = ""
                scene["status"] = "failed"
                scene["progress"] = 0
                changed = True

    # Do not contact the image provider while the UI is merely loading a plan.
    # Provider polling belongs to an explicit generation/retry flow.
    return _mark_orphaned_running_tasks(project, plan) or changed


def _reconcile_running_tasks(project: str, plan: dict) -> bool:
    # The in-process worker already owns task polling and retries. A parallel
    # reconciliation request from the UI can receive a transient provider
    # "task not found" response and incorrectly overwrite the live worker's
    # scene with failed. Reconciliation is only for recovery after a restart.
    with _ACTIVE_SCENE_GENERATION_LOCK:
        if project in _ACTIVE_SCENE_GENERATION_PROJECTS:
            return False
    running = [s for s in plan.get("scenes", []) if s.get("status") == "running" and s.get("task_id")]
    if not running:
        return False
    try:
        cfg = _scene_cfg()
    except Exception:
        return False
    if cfg.get("provider") == "custom":
        return False

    changed = False
    now = time.time()
    for scene in running:
        try:
            last_poll = float(scene.get("last_task_poll_ts") or 0)
        except Exception:
            last_poll = 0
        if now - last_poll < 4:
            continue
        scene["last_task_poll_ts"] = round(now, 3)
        try:
            changed = _reconcile_scene_task(project, scene, cfg) or changed
        except Exception as exc:
            scene["status"] = "failed"
            scene["task_status"] = "failed"
            scene["progress"] = 0
            scene["error"] = str(exc)
            scene["finished_at"] = _now_iso()
            changed = True
    return changed


def _reconcile_scene_task(project: str, scene: dict, cfg: dict) -> bool:
    task_id = str(scene.get("task_id") or "").strip()
    if not task_id:
        return False
    url = f"{cfg['base_url'].rstrip('/')}/images/generations/{task_id}"
    res = requests.get(url, headers=_auth_headers(cfg), timeout=30)
    data = _read_response_json(res)
    if not res.ok:
        raise RuntimeError(_error_message(data, f"Image task poll failed ({res.status_code})"))

    status = str(data.get("status") or "").lower()
    progress = _coerce_progress(data.get("progress"), scene.get("progress", 0))
    changed = False
    if scene.get("task_status") != (status or "running"):
        scene["task_status"] = status or "running"
        changed = True
    if scene.get("progress") != progress:
        scene["progress"] = progress
        changed = True

    if status in {"succeeded", "success", "completed"}:
        had_valid_image = _scene_has_valid_image(project, scene)
        image_value = _first_image_candidate(data)
        if not image_value:
            now = time.time()
            first_missing = float(scene.get("image_missing_since_ts") or now)
            scene["image_missing_since_ts"] = first_missing
            scene["task_status"] = "finalizing"
            scene["progress"] = 99
            if now - first_missing < 60:
                return True
            raise RuntimeError("Image task succeeded but returned no usable image")
        scene.pop("image_missing_since_ts", None)
        filename = _save_image(project, scene, image_value, cfg, filename=_new_scene_image_filename(scene))
        select_new_image = not had_valid_image
        _add_image_history(project, scene, filename, selected=select_new_image, meta={
            "source": "reconciled_task",
            "prompt": scene.get("prompt", ""),
            "provider": cfg.get("provider", "gpt2api"),
            "model": cfg.get("image_model") or cfg.get("model", ""),
            "task_id": task_id,
        })
        scene["status"] = "succeeded"
        scene["task_status"] = "succeeded"
        scene["progress"] = 100
        if select_new_image:
            scene["image_file"] = filename
        scene["last_candidate_image_file"] = filename
        scene["error"] = ""
        scene["last_generation_error"] = ""
        scene["finished_at"] = _now_iso()
        return True
    if status in {"failed", "refunded"}:
        scene["status"] = "failed"
        scene["task_status"] = status
        scene["progress"] = 0
        scene["error"] = _error_message(data, "Image generation failed")
        scene["finished_at"] = _now_iso()
        return True
    return changed


def _coerce_progress(value, fallback=0) -> int:
    try:
        progress = int(float(value if value is not None else fallback or 0))
    except Exception:
        progress = int(fallback or 0)
    return max(0, min(99, progress))


def _coerce_concurrency(value, total: int) -> int:
    try:
        concurrency = int(value or 1)
    except Exception:
        concurrency = 1
    # The caller controls the number of independent image requests.  Do not
    # impose a product-level ceiling here: the provider may still reject or
    # throttle a burst, but LucyLab must actually submit the requested jobs.
    concurrency = max(1, concurrency)
    if total > 0:
        concurrency = min(concurrency, total)
    return concurrency


def _is_fatal_image_error(message: str) -> bool:
    lower = (message or "").lower()
    return any(term in lower for term in (
        "invalid_api_key",
        "unauthorized",
        "scope_not_allowed",
        "billing",
        "usage_limit_reached",
        "usage limit has been reached",
        "quota remaining. wait",
    ))


def _retry_delay_for_image_error(message: str, attempt: int) -> float:
    lower = (message or "").lower()
    if (
        "no available" in lower
        or "暂无可用账号" in lower
        or "pick account" in lower
        or "http 429" in lower
        or "too many requests" in lower
        or "rate limit" in lower
    ):
        return min(60.0, 15.0 * attempt)
    if "http 500" in lower or "gpt image2 500" in lower:
        return min(30.0, 8.0 * attempt)
    if (
        "context deadline exceeded" in lower
        or "stream read" in lower
        or "internal_error" in lower
        or "cloudflare 524" in lower
        or "timeout occurred" in lower
        or "http 524" in lower
        or "generated image (414)" in lower
    ):
        return min(20.0, 5.0 * attempt)
    return min(12.0, 2.0 * attempt)


def _sleep_with_cancel(project: str, seconds: float) -> None:
    until = time.time() + max(0.0, seconds)
    while time.time() < until:
        if project in _cancelled:
            raise RuntimeError("Scene image generation cancelled")
        time.sleep(min(0.5, max(0.0, until - time.time())))


def _generation_targets(project: str, scenes: list[dict], wanted: set[str], retry_failed: bool) -> list[dict]:
    if wanted:
        return [s for s in scenes if s.get("id") in wanted]

    targets: list[dict] = []
    for scene in scenes:
        valid_image = _scene_has_valid_image(project, scene)
        status = scene.get("status")
        if retry_failed:
            if status in {"failed", "running", "cancelled"} or (status == "succeeded" and not valid_image):
                targets.append(scene)
            continue
        if status != "succeeded" or not valid_image:
            targets.append(scene)
    return targets


def _mark_generation_kind(targets: list[dict], retry_failed: bool) -> str:
    """Record user intent; an image filename is not reliable retry evidence."""
    kind = "retry" if retry_failed else "initial"
    for scene in targets:
        scene["generation_kind"] = kind
    return kind


def _safe_stem(value: str, fallback: str = "output") -> str:
    stem = re.sub(r"[^\w.-]+", "_", (value or "").strip(), flags=re.UNICODE).strip("._-")
    return stem or fallback


def _reference_filename(ref: dict) -> str:
    ref_id = _safe_stem(str(ref.get("id") or "ref"), "ref")
    scope = _asset_scope(ref.get("asset_scope"))
    prefix = f"{ref_id}_{scope}" if scope else ref_id
    return f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.png"


def _reference_path(project: str, ref: dict, filename: str) -> Path:
    if not _SAFE_REF_IMAGE.match(filename):
        raise RuntimeError("Invalid reference image filename")
    return _reference_dir(project, str(ref.get("type") or "misc")) / filename


def _reference_history(ref: dict) -> list[dict]:
    history = ref.get("image_history")
    if not isinstance(history, list):
        history = []
        ref["image_history"] = history
    return history


def _reference_file_valid(project: str, ref: dict, filename: str) -> bool:
    if not filename or not _SAFE_REF_IMAGE.match(filename):
        return False
    path = _reference_path(project, ref, filename)
    return path.exists() and path.stat().st_size > 0


def _add_reference_history(project: str, ref: dict, filename: str, *, selected: bool, meta: dict | None = None) -> dict:
    if not _reference_file_valid(project, ref, filename):
        raise RuntimeError(f"Reference image is missing or empty: {filename}")
    path = _reference_path(project, ref, filename)
    history = _reference_history(ref)
    existing = next((item for item in history if item.get("image_file") == filename), None)
    item = existing or {}
    item.update({
        "id": Path(filename).stem,
        "image_file": filename,
        "created_at": item.get("created_at") or _now_iso(),
        "size_bytes": path.stat().st_size,
    })
    if meta:
        item.update({k: v for k, v in meta.items() if v not in (None, "")})
    if existing is None:
        history.append(item)
    if selected:
        for other in history:
            other["selected"] = other.get("image_file") == filename
        ref["image_file"] = filename
        ref["status"] = "succeeded"
        ref["error"] = ""
    return item


def _recover_reference_variants(project: str, ref: dict) -> bool:
    """Reconnect reference assets after recovering interrupted plan metadata."""
    ref_id = str(ref.get("id") or "")
    if not ref_id:
        return False
    changed = False
    root = _scene_dir(project) / "references"
    scope = _asset_scope(ref.get("asset_scope"))
    pattern = f"{ref_id}_{scope}_*" if scope else f"{ref_id}_*"
    for path in sorted(root.rglob(pattern)):
        filename = path.name
        if not path.is_file() or not _SAFE_REF_IMAGE.match(filename) or path.stat().st_size <= 0:
            continue
        # The saved folder is authoritative if a later planner refined the
        # reference type (for example misc -> prop).
        saved_type = path.parent.name
        if saved_type and ref.get("type") != saved_type:
            ref["type"] = saved_type
            changed = True
        if _history_has_file(ref, filename):
            continue
        _add_reference_history(project, ref, filename, selected=False, meta={"source": "recovered"})
        changed = True
    if ref.get("image_file") and not _reference_file_valid(project, ref, str(ref["image_file"])):
        ref["image_file"] = ""
        changed = True
    if not ref.get("image_file"):
        candidate = next(
            (item for item in reversed(_reference_history(ref))
             if _reference_file_valid(project, ref, str(item.get("image_file") or ""))),
            None,
        )
        if candidate:
            _add_reference_history(project, ref, str(candidate["image_file"]), selected=True, meta={"source": "recovered"})
            changed = True
    return changed


def _find_reference(plan: dict, reference_id: str) -> dict | None:
    return next((item for item in plan.get("references") or [] if item.get("id") == reference_id), None)


def _scene_reference_images(project: str, scene: dict, plan: dict) -> list[str]:
    images: list[str] = []
    for reference_id in scene.get("reference_ids") or []:
        ref = _find_reference(plan, str(reference_id))
        if not ref:
            continue
        filename = str(ref.get("image_file") or "")
        if not filename or not _reference_file_valid(project, ref, filename):
            continue
        images.append(str(_reference_path(project, ref, filename)))
    # The provider receives every selected reference.  A stick-figure explainer
    # may need a style sheet, character turns, props, maps, and a storyboard
    # frame at once; silently truncating that visual vocabulary breaks continuity.
    return images


def _reference_targets(plan: dict, wanted: set[str], retry_failed: bool) -> list[dict]:
    refs = plan.get("references") if isinstance(plan.get("references"), list) else []
    if wanted:
        return [ref for ref in refs if ref.get("id") in wanted]
    targets = []
    for ref in refs:
        has_image = bool(ref.get("image_file"))
        if retry_failed:
            if ref.get("status") in {"failed", "running"} or not has_image:
                targets.append(ref)
            continue
        if not has_image:
            targets.append(ref)
    return targets


def _unique_output_path(output_dir: Path, output_name: str, overwrite_mode: str) -> Path:
    name = output_name.strip() or "final_video_scene.mp4"
    if not name.lower().endswith(".mp4"):
        name += ".mp4"
    path = output_dir / f"{_safe_stem(Path(name).stem, 'final_video_scene')}.mp4"
    if overwrite_mode == "overwrite":
        return path
    candidate = path
    i = 2
    while candidate.exists():
        candidate = output_dir / f"{path.stem}_{i}.mp4"
        i += 1
    return candidate


def _audio_duration(project_dir: Path, audio_file: str, ffprobe_path: str) -> float:
    audio_path = project_dir / audio_file
    if not audio_path.exists():
        raise HTTPException(404, "Audio file not found")
    duration = get_video_duration(audio_path, ffprobe_path)
    if duration <= 0:
        raise HTTPException(400, "Cannot read audio duration")
    return duration


def _fallback_cues(story_text: str, total_duration: float) -> list[dict]:
    chunks = split_text_for_timing(story_text or "", 380) or [story_text or ""]
    weights = [max(1, len(c)) for c in chunks]
    total_weight = sum(weights) or 1
    cursor = 0.0
    cues: list[dict] = []
    for i, (chunk, weight) in enumerate(zip(chunks, weights), start=1):
        duration = total_duration * (weight / total_weight)
        end = total_duration if i == len(chunks) else min(total_duration, cursor + duration)
        cues.append({
            "text": chunk.strip(),
            "start_sec": round(cursor, 3),
            "end_sec": round(end, 3),
            "duration_sec": round(max(0.01, end - cursor), 3),
        })
        cursor = end
    return cues


def _load_cues(project_dir: Path, audio_file: str, story_text: str, duration: float) -> tuple[list[dict], str]:
    timing = load_timing_file(project_dir, audio_file)
    if timing:
        cues = build_subtitle_cues(timing)
        if cues:
            return cues, "timing_metadata"
    return _fallback_cues(story_text, duration), "fallback_story_split"


def _group_cues(cues: list[dict], target: float, minimum: float, maximum: float, max_scenes: int) -> list[dict]:
    target = min(30.0, max(5.0, float(target or 12)))
    minimum = min(target, max(3.0, float(minimum or 7)))
    maximum = max(target, min(45.0, float(maximum or 18)))
    max_scenes = min(250, max(1, int(max_scenes or 120)))

    scenes: list[dict] = []
    current: list[dict] = []
    start = None
    for cue in cues:
        if start is None:
            start = float(cue.get("start_sec", 0))
        current.append(cue)
        end = float(cue.get("end_sec", cue.get("start_sec", 0)))
        duration = end - start
        text_len = len(" ".join(c.get("text", "") for c in current))
        should_close = duration >= target or duration >= maximum or text_len >= 950
        if should_close and duration >= minimum and len(scenes) < max_scenes - 1:
            scenes.append(_scene_from_cues(len(scenes) + 1, current, start, end))
            current = []
            start = None

    if current:
        end = float(current[-1].get("end_sec", current[-1].get("start_sec", 0)))
        scenes.append(_scene_from_cues(len(scenes) + 1, current, start or 0.0, end))

    if len(scenes) > max_scenes:
        tail = scenes[max_scenes - 1:]
        merged_text = " ".join(s.get("text", "") for s in tail)
        scenes = scenes[:max_scenes - 1]
        scenes.append({
            **tail[0],
            "id": f"scene_{max_scenes:03d}",
            "index": max_scenes,
            "end_sec": tail[-1]["end_sec"],
            "duration_sec": round(tail[-1]["end_sec"] - tail[0]["start_sec"], 3),
            "text": merged_text,
        })
    return scenes


def _scene_from_cues(index: int, cues: list[dict], start: float, end: float) -> dict:
    return {
        "id": f"scene_{index:03d}",
        "index": index,
        "start_sec": round(start, 3),
        "end_sec": round(end, 3),
        "duration_sec": round(max(0.01, end - start), 3),
        "text": " ".join(c.get("text", "").strip() for c in cues if c.get("text", "").strip()),
        "status": "pending",
        "task_id": "",
        "image_file": "",
        "error": "",
        "prompt": "",
    }


_DIRECTOR_VISUAL_PLAN_SYSTEM = """You are the Visual Plan editor for a narrated video.
Return strict JSON only:
{"beats":[{"cue_ids":["d_0001"],"visual_moment":"specific visible moment to hold on screen","camera":"...","composition":"...","emotion":"...","hold_reason":"optional"}]}

You decide how to group consecutive timed narration cues into visual beats. Every cue_id must appear exactly once and in the original order. Group only when the consecutive narration truly shares one visual event, location, evidence, or emotional beat. Split when the subject, action, location, evidence, point of view, or emotional turn changes. Do not mechanically follow sentence length and do not reveal an event before its narration reaches it.

The target duration is only a pacing hint, not a fixed rule. Make short beats for reveals or cuts and longer beats only when one strong visual can carry the narration. Keep the plan coherent, cinematic, and economical: no duplicate visual variations. The optional instruction can influence grouping, pacing, camera, composition, and mood only; never change narration order or wording."""


def _director_visual_plan_scenes(
    cues: list[dict],
    *,
    target_scene_sec: float,
    min_scene_sec: float,
    max_scene_sec: float,
    max_scenes: int,
    story_title: str,
    visual_style: str,
    instruction: str = "",
) -> tuple[list[dict], str]:
    """Use the Director LLM to group real audio cues into semantic visual beats.

    Invalid or partial LLM output falls back to the existing deterministic
    grouping so the visual timeline can never omit or reorder audio.
    """
    if not cues:
        return [], "visual_plan_empty_cues"
    units = [
        {
            "id": f"d_{index:04d}",
            "start_sec": round(float(cue.get("start_sec", 0) or 0), 3),
            "end_sec": round(float(cue.get("end_sec", cue.get("start_sec", 0)) or 0), 3),
            "text": str(cue.get("text") or "").strip(),
        }
        for index, cue in enumerate(cues, start=1)
    ]
    payload = {
        "story_title": story_title or "Untitled story",
        "visual_style": visual_style,
        "pacing_hint": {
            "target_scene_sec": round(float(target_scene_sec or 12), 2),
            "minimum_scene_sec": round(float(min_scene_sec or 7), 2),
            "maximum_scene_sec": round(float(max_scene_sec or 18), 2),
            "maximum_beats": min(250, max(1, int(max_scenes or 120))),
        },
        "visual_plan_instruction": str(instruction or "").strip()[:2000],
        "cues": units,
    }
    try:
        raw = _call_director_llm(
            _DIRECTOR_VISUAL_PLAN_SYSTEM,
            json.dumps(payload, ensure_ascii=False),
            max_tokens=9000,
        )
        data = _parse_director_json(raw, json.dumps(payload, ensure_ascii=False))
        raw_beats = data.get("beats") if isinstance(data, dict) else None
        if not isinstance(raw_beats, list) or not raw_beats:
            raise ValueError("Visual Plan returned no beats")
        if len(raw_beats) > int(payload["pacing_hint"]["maximum_beats"]):
            raise ValueError("Visual Plan exceeded maximum beats")
        by_id = {unit["id"]: unit for unit in units}
        expected_ids = [unit["id"] for unit in units]
        cursor = 0
        scenes: list[dict] = []
        for raw in raw_beats:
            if not isinstance(raw, dict):
                raise ValueError("Visual Plan contained an invalid beat")
            raw_cue_ids = [str(item) for item in raw.get("cue_ids", [])]
            if any(item not in by_id for item in raw_cue_ids):
                raise ValueError("Visual Plan referenced an unknown cue")
            cue_ids = raw_cue_ids
            if not cue_ids or cue_ids != expected_ids[cursor:cursor + len(cue_ids)]:
                raise ValueError("Visual Plan does not cover cues in exact order")
            selected = [by_id[item] for item in cue_ids]
            scene = _scene_from_cues(
                len(scenes) + 1,
                selected,
                float(selected[0]["start_sec"]),
                float(selected[-1]["end_sec"]),
            )
            scene["visual_plan"] = {
                "visual_moment": str(raw.get("visual_moment") or "").strip(),
                "camera": str(raw.get("camera") or "").strip(),
                "composition": str(raw.get("composition") or "").strip(),
                "emotion": str(raw.get("emotion") or "").strip(),
                "hold_reason": str(raw.get("hold_reason") or "").strip(),
                "cue_ids": cue_ids,
            }
            scenes.append(scene)
            cursor += len(cue_ids)
        if cursor != len(expected_ids):
            raise ValueError("Visual Plan did not cover every cue")
        return scenes, "visual_plan_ai"
    except Exception as exc:
        return _group_cues(cues, target_scene_sec, min_scene_sec, max_scene_sec, max_scenes), f"visual_plan_fallback: {str(exc)[:180]}"


def _build_story_bible(story_title: str, story_text: str, visual_style: str) -> str:
    excerpt = re.sub(r"\s+", " ", (story_text or "").strip())[:1400]
    style = visual_style.strip() or "cinematic Vietnamese horror, realistic digital painting, tense lighting"
    return (
        f"Title: {story_title or 'Untitled story'}\n"
        f"Visual style: {style}\n"
        "Continuity rules: keep characters, locations, era, mood, props, and color palette consistent. "
        "No text, captions, subtitles, logo, watermark, or UI inside the image.\n"
        f"Story context: {excerpt}"
    )


def _build_scene_prompt(scene: dict, story_bible: str, visual_style: str) -> str:
    style = visual_style.strip() or "cinematic horror illustration, realistic digital painting, dramatic lighting"
    text = re.sub(r"\s+", " ", scene.get("text", "")).strip()
    return (
        "Create one 16:9 cinematic story frame for a narrated horror video.\n"
        f"Style: {style}.\n"
        f"Story bible:\n{story_bible}\n"
        f"Scene beat ({scene['start_sec']}s-{scene['end_sec']}s): {text}\n"
        "Use a single clear visual moment from this beat. Maintain visual continuity with the story. "
        "No readable text, no subtitles, no speech bubbles, no watermark, no logo. "
        "Composition must be clean enough for later subtitles."
    )


DIRECTOR_SYSTEM = """You are a senior visual director for Vietnamese narrated horror videos.
Use creative judgment. Do not mechanically illustrate every sentence.
Design cinematic, coherent, emotionally effective visuals that keep characters, locations, props, mood, palette, and camera language consistent.
Return valid JSON only. No markdown."""


def _story_excerpt_for_director(story_text: str, limit: int = 18000) -> str:
    text = re.sub(r"\s+", " ", (story_text or "").strip())
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.58)].rsplit(" ", 1)[0]
    tail = text[-int(limit * 0.38):].split(" ", 1)[-1]
    return f"{head}\n\n...[middle omitted for planning context]...\n\n{tail}"


def _director_cfg() -> dict:
    cfg = load_config()
    cfg.setdefault("visual_director_provider", "default")
    cfg.setdefault("visual_director_model", "")
    cfg.setdefault("visual_director_base_url", "https://api.openai.com")
    cfg.setdefault("visual_director_path", "/v1/chat/completions")
    cfg.setdefault("visual_director_api_key", "")
    cfg.setdefault("visual_director_api_key_path", "")
    return cfg


def _call_director_llm(system: str, user: str, max_tokens: int = 5000) -> str:
    cfg = _director_cfg()
    provider = (cfg.get("visual_director_provider") or "default").strip().lower()
    configured_model = (cfg.get("visual_director_model") or "").strip()
    if provider == "custom":
        return generate_text_openai_compatible(
            system,
            user,
            base_url=cfg.get("visual_director_base_url") or "https://api.openai.com",
            path=cfg.get("visual_director_path") or "/v1/chat/completions",
            model=configured_model or "gpt-4o-mini",
            api_key=cfg.get("visual_director_api_key") or "",
            api_key_path=cfg.get("visual_director_api_key_path") or "",
            max_tokens=max_tokens,
        )
    return generate_text(
        system,
        user,
        configured_model or cfg.get("default_story_model") or "claude-sonnet-4-6",
        cfg.get("anthropic_api_key", ""),
        cfg.get("ollama_url", "http://localhost:11434"),
        max_tokens=max_tokens,
    )


def _parse_director_json(raw: str, repair_user: str = "") -> dict:
    try:
        parsed = _parse_json_response(raw)
        if isinstance(parsed, dict) and parsed and "complete" not in parsed:
            return parsed
    except Exception:
        pass
    text = (raw or "").strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.lower().startswith("json"):
            text = text.split("\n", 1)[-1]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            pass
    if repair_user:
        repaired = _call_director_llm(
            "Repair the following response into valid JSON matching the requested schema. Return JSON only.",
            f"Original request:\n{repair_user[:6000]}\n\nBroken response:\n{raw[:12000]}",
            max_tokens=4000,
        )
        return _parse_director_json(repaired, "")
    raise RuntimeError("Visual Director returned invalid JSON")


def _director_json(user: str, max_tokens: int = 6000) -> dict:
    raw = _call_director_llm(DIRECTOR_SYSTEM, user, max_tokens=max_tokens)
    return _parse_director_json(raw, user)


def _fallback_visual_bible(plan: dict, story_text: str) -> dict:
    style = plan.get("visual_style") or "cinematic Vietnamese horror, realistic digital painting, dramatic light"
    return {
        "logline": f"Cinematic narrated horror story: {plan.get('story_title') or 'Untitled story'}",
        "visual_style": style,
        "palette": ["deep shadows", "warm practical light", "rainy cool blue", "aged Vietnamese interiors"],
        "continuity_rules": [
            "Keep character wardrobe and facial features consistent.",
            "Keep locations grounded in old Vietnamese architecture and practical lighting.",
            "Leave clean negative space for subtitles.",
        ],
        "characters": [
            {
                "id": "char_001",
                "name": "Main protagonist",
                "description": "Vietnamese adult protagonist, tired expression, simple dark clothing, tense posture.",
            }
        ],
        "locations": [
            {
                "id": "loc_001",
                "name": "Main story location",
                "description": "Old Vietnamese interior at night, rain outside, worn walls, wooden furniture, warm oil-lamp glow.",
            }
        ],
        "props": [],
        "policy_notes": ["Use suspense, distance, shadow, symbolic props, and atmosphere instead of explicit violence."],
        "story_excerpt": _story_excerpt_for_director(story_text, 900),
    }


def _generate_visual_bible(plan: dict, story_text: str) -> dict:
    user = f"""Create a visual bible for this narrated horror story.
Return JSON:
{{
  "logline": "...",
  "visual_style": "...",
  "palette": ["..."],
  "continuity_rules": ["..."],
  "characters": [{{"id":"char_001","name":"...","description":"fixed visual description","wardrobe":"...","recognition_features":"..."}}],
  "locations": [{{"id":"loc_001","name":"...","description":"fixed visual description","lighting":"...","props":"..."}}],
  "props": [{{"id":"prop_001","name":"...","description":"..."}}],
  "policy_notes": ["..."]
}}

Story title: {plan.get('story_title') or 'Untitled story'}
Requested style: {plan.get('visual_style') or ''}
Story:
{_story_excerpt_for_director(story_text)}
"""
    try:
        data = _director_json(user, max_tokens=7000)
    except Exception:
        return _fallback_visual_bible(plan, story_text)
    if not isinstance(data, dict):
        return _fallback_visual_bible(plan, story_text)
    data.setdefault("visual_style", plan.get("visual_style") or "")
    data.setdefault("continuity_rules", [])
    data.setdefault("characters", [])
    data.setdefault("locations", [])
    data.setdefault("props", [])
    data.setdefault("policy_notes", [])
    return data


def _normalize_reference_type(value: str) -> str:
    raw = (value or "").strip().lower()
    if raw in {"character", "characters", "char", "person"}:
        return "character"
    if raw in {"location", "locations", "place", "setting"}:
        return "location"
    if raw in {"style", "mood", "style/mood", "style_mood"}:
        return "style"
    if raw in {"prop", "props", "object", "asset"}:
        return "prop"
    if raw in {"storyboard", "storyboard_frame", "frame", "beat"}:
        return "storyboard"
    return "misc"


def _reference_prefix(ref_type: str) -> str:
    return {"character": "char", "location": "loc", "style": "style", "prop": "prop", "storyboard": "board"}.get(ref_type, "ref")


def _normalize_references(data: dict, visual_bible: dict, plan: dict) -> list[dict]:
    raw_refs = data.get("references") if isinstance(data, dict) else []
    if not isinstance(raw_refs, list):
        raw_refs = []
    result: list[dict] = []
    seen: set[str] = set()
    counters = {"character": 0, "location": 0, "style": 0, "misc": 0}
    for raw in raw_refs:
        if not isinstance(raw, dict):
            continue
        ref_type = _normalize_reference_type(str(raw.get("type") or raw.get("kind") or "misc"))
        counters[ref_type] = counters.get(ref_type, 0) + 1
        raw_id = _safe_stem(str(raw.get("id") or ""), "")
        ref_id = raw_id or f"{_reference_prefix(ref_type)}_{counters[ref_type]:03d}"
        if ref_id in seen:
            ref_id = f"{ref_id}_{counters[ref_type]:03d}"
        seen.add(ref_id)
        name = str(raw.get("name") or ref_id).strip()
        description = str(raw.get("description") or raw.get("visual_description") or "").strip()
        prompt = str(raw.get("prompt") or "").strip()
        if not prompt:
            prompt = _build_reference_prompt(ref_type, name, description, visual_bible, plan)
        result.append({
            "id": ref_id,
            "type": ref_type,
            "name": name,
            "description": description,
            "prompt": prompt,
            "status": raw.get("status") or "pending",
            "image_file": raw.get("image_file") or "",
            "image_history": raw.get("image_history") if isinstance(raw.get("image_history"), list) else [],
            "error": raw.get("error") or "",
            "created_at": raw.get("created_at") or _now_iso(),
        })
    if result:
        return result

    fallback = _fallback_visual_bible(plan, "")
    char = (visual_bible.get("characters") or fallback["characters"])[0]
    loc = (visual_bible.get("locations") or fallback["locations"])[0]
    return [
        {
            "id": str(char.get("id") or "char_001"),
            "type": "character",
            "name": str(char.get("name") or "Main protagonist"),
            "description": str(char.get("description") or ""),
            "prompt": _build_reference_prompt("character", str(char.get("name") or "Main protagonist"), str(char.get("description") or ""), visual_bible, plan),
            "status": "pending",
            "image_file": "",
            "image_history": [],
            "error": "",
            "created_at": _now_iso(),
        },
        {
            "id": str(loc.get("id") or "loc_001"),
            "type": "location",
            "name": str(loc.get("name") or "Main location"),
            "description": str(loc.get("description") or ""),
            "prompt": _build_reference_prompt("location", str(loc.get("name") or "Main location"), str(loc.get("description") or ""), visual_bible, plan),
            "status": "pending",
            "image_file": "",
            "image_history": [],
            "error": "",
            "created_at": _now_iso(),
        },
    ]


def _build_reference_prompt(
    ref_type: str,
    name: str,
    description: str,
    visual_bible: dict,
    plan: dict,
    *,
    allow_text: bool = False,
    on_screen_text: str = "",
) -> str:
    style = visual_bible.get("visual_style") or plan.get("visual_style") or "cinematic Vietnamese horror, realistic digital painting"
    project_color_bible = visual_bible.get("project_color_bible") if isinstance(visual_bible.get("project_color_bible"), dict) else None
    if ref_type == "character":
        subject = f"character reference sheet for {name}: {description}"
        instruction = "Show the character clearly, consistent face, hair, body shape, and wardrobe. Neutral cinematic pose, no text."
    elif ref_type == "location":
        subject = f"environment reference for {name}: {description}"
        instruction = "Show the location clearly with layout, lighting, props, texture, and atmosphere. No people unless necessary, no text."
    elif ref_type == "prop":
        subject = f"prop reference for {name}: {description}"
        instruction = "Show the object clearly from a useful explanatory angle, with consistent material, scale, and silhouette."
    elif ref_type == "storyboard":
        subject = f"storyboard reference frame for {name}: {description}"
        instruction = "Create a readable 2D storyboard frame that locks composition, subject placement, and visual storytelling for this exact beat."
    else:
        subject = f"style and mood reference for {name}: {description}"
        instruction = "Show the visual language, palette, lighting, atmosphere, lens feel, and subtitle-safe composition. No text."
    output_locale = _stick_output_locale(plan.get("output_locale"))
    text_rule = (
        f'Include ONLY this exact, legible on-screen text for {output_locale}: "{on_screen_text}". '
        "Treat it as intentional editorial typography, not a watermark."
        if allow_text and on_screen_text else "Do not include any readable text, captions, subtitles, logos, signs, labels, or watermarks."
    )
    colour_rule = ""
    if project_color_bible:
        usage = "adaptive_focus"
        colour_rule = _stick_color_usage_instruction(project_color_bible, usage) + " "
        if ref_type == "character":
            colour_rule += "Keep the reference background quiet; use colour only as a stable identity cue on wardrobe or props. "
        elif ref_type == "style":
            colour_rule += "Demonstrate the complete shared palette clearly so later assets can match it. "
    return (
        f"Create one 16:9 reference image for a narrated video.\n"
        f"Story: {plan.get('story_title') or 'Untitled story'}\n"
        f"Style: {style}\n"
        f"Subject: {subject}\n"
        f"{instruction}\n"
        f"{colour_rule}\n"
        "If a style-sheet image is provided as a reference input, match its line work, palette, character proportions, and editorial layout closely.\n"
        f"{text_rule}\n"
        "Avoid watermarks, logos, gore, explicit injury, and distorted anatomy."
    )


def _generate_reference_plan(plan: dict, visual_bible: dict) -> list[dict]:
    user = f"""Create the minimum useful reference image plan for this story.
Return JSON:
{{
  "references": [
    {{"id":"char_001","type":"character","name":"...","description":"...","prompt":"..."}},
    {{"id":"loc_001","type":"location","name":"...","description":"...","prompt":"..."}},
    {{"id":"style_001","type":"style","name":"...","description":"...","prompt":"..."}}
  ]
}}
Use only important recurring characters and locations. Usually 2-6 references total.
Each prompt must create a standalone 16:9 reference image and must avoid readable text/watermark/subtitles.

Story title: {plan.get('story_title') or 'Untitled story'}
Visual bible:
{json.dumps(visual_bible, ensure_ascii=False)}
"""
    try:
        data = _director_json(user, max_tokens=6000)
    except Exception:
        data = {}
    return _normalize_references(data, visual_bible, plan)


def _reference_summary(references: list[dict]) -> str:
    rows = []
    for ref in references:
        rows.append({
            "id": ref.get("id"),
            "type": ref.get("type"),
            "name": ref.get("name"),
            "description": ref.get("description"),
        })
    return json.dumps(rows, ensure_ascii=False)


def _scene_batch_for_director(scenes: list[dict]) -> list[dict]:
    return [
        {
            "id": s.get("id"),
            "time": f"{s.get('start_sec')}s-{s.get('end_sec')}s",
            "duration_sec": s.get("duration_sec"),
            "narration": s.get("text", ""),
            "visual_plan": s.get("visual_plan") if isinstance(s.get("visual_plan"), dict) else {},
        }
        for s in scenes
    ]


def _build_director_prompt(scene: dict, storyboard: dict, visual_bible: dict, references: list[dict], plan: dict) -> str:
    ref_ids = set(storyboard.get("reference_ids") or scene.get("reference_ids") or [])
    ref_lines = [
        f"{ref.get('id')}: {ref.get('name')} - {ref.get('description')}"
        for ref in references
        if ref.get("id") in ref_ids
    ]
    style = visual_bible.get("visual_style") or plan.get("visual_style") or "cinematic Vietnamese horror, realistic digital painting"
    return (
        "Create one 16:9 cinematic frame for a narrated Vietnamese horror video.\n"
        f"Story: {plan.get('story_title') or 'Untitled story'}\n"
        f"Style: {style}\n"
        f"Scene time: {scene.get('start_sec')}s-{scene.get('end_sec')}s\n"
        f"Visual moment: {storyboard.get('visual_moment') or storyboard.get('visual_goal') or scene.get('text', '')}\n"
        f"Characters: {', '.join(storyboard.get('characters') or []) or 'as required by the scene'}\n"
        f"Location: {storyboard.get('location') or 'story location'}\n"
        f"Camera: {storyboard.get('camera') or storyboard.get('shot') or 'cinematic medium-wide shot'}\n"
        f"Composition: {storyboard.get('composition') or 'clean cinematic composition with room for subtitles'}\n"
        f"Mood/emotion: {storyboard.get('emotion') or storyboard.get('mood') or 'tense suspense'}\n"
        f"Continuity references: {'; '.join(ref_lines) if ref_lines else 'follow the visual bible and story continuity'}\n"
        "Keep a clean subtitle-safe area. Avoid readable text, captions, subtitles, logos, watermarks, gore, explicit injuries, and distorted anatomy."
    )


def _normalize_storyboard_item(raw: dict, scene: dict, visual_bible: dict, references: list[dict], plan: dict) -> dict:
    valid_refs = {r.get("id") for r in references}
    ref_ids = [str(x) for x in (raw.get("reference_ids") or raw.get("references") or []) if str(x) in valid_refs]
    item = {
        "scene_id": scene.get("id"),
        "visual_moment": str(raw.get("visual_moment") or raw.get("visual_goal") or "").strip(),
        "characters": [str(x) for x in (raw.get("characters") or []) if str(x).strip()],
        "location": str(raw.get("location") or "").strip(),
        "camera": str(raw.get("camera") or raw.get("shot") or "").strip(),
        "composition": str(raw.get("composition") or "").strip(),
        "emotion": str(raw.get("emotion") or raw.get("mood") or "").strip(),
        "continuity_notes": str(raw.get("continuity_notes") or "").strip(),
        "subtitle_safe_area": str(raw.get("subtitle_safe_area") or "leave lower/center area readable for subtitles").strip(),
        "reference_ids": ref_ids,
    }
    prompt = str(raw.get("prompt") or raw.get("director_prompt") or "").strip()
    item["prompt"] = prompt or _build_director_prompt(scene, item, visual_bible, references, plan)
    return item


def _generate_storyboard_batch(plan: dict, scenes: list[dict], visual_bible: dict, references: list[dict]) -> list[dict]:
    user = f"""Create storyboard metadata and final image prompts for the scene batch.
Return JSON:
{{
  "scenes": [
    {{
      "scene_id": "scene_001",
      "visual_moment": "the best cinematic moment to show, not a literal transcript",
      "characters": ["names or ids"],
      "location": "...",
      "camera": "...",
      "composition": "...",
      "emotion": "...",
      "continuity_notes": "...",
      "subtitle_safe_area": "...",
      "reference_ids": ["char_001","loc_001"],
      "prompt": "final image prompt"
    }}
  ]
}}
Use creative visual direction. Preserve timeline and story continuity. Use provided reference IDs when useful.
Every prompt must be 16:9, cinematic, no readable text, no subtitles, no watermark, clean subtitle space.

Story title: {plan.get('story_title') or 'Untitled story'}
Visual bible:
{json.dumps(visual_bible, ensure_ascii=False)}

Available references:
{_reference_summary(references)}

Scene batch:
{json.dumps(_scene_batch_for_director(scenes), ensure_ascii=False)}
"""
    try:
        data = _director_json(user, max_tokens=8000)
        raw_items = data.get("scenes") if isinstance(data, dict) else []
        if not isinstance(raw_items, list):
            raw_items = []
    except Exception:
        raw_items = []
    by_id = {str(item.get("scene_id") or item.get("id") or ""): item for item in raw_items if isinstance(item, dict)}
    normalized = []
    for scene in scenes:
        raw = by_id.get(str(scene.get("id"))) or {}
        normalized.append(_normalize_storyboard_item(raw, scene, visual_bible, references, plan))
    return normalized


_SENSITIVE_SCENE_TERMS = (
    "blood", "knife", "corpse", "dead body", "murder", "kill", "killed", "violence", "violent",
    "mau", "dao", "chet", "giet", "xac", "thi the", "bat dong", "dam", "danh", "guc",
    "bao luc", "hanh hung", "vet thuong", "tu vong", "tan sat", "ac dang phai chet",
)


def _fold_for_filter(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower()


def _looks_sensitive(value: str) -> bool:
    folded = _fold_for_filter(value)
    return any(term in folded for term in _SENSITIVE_SCENE_TERMS)


def _safe_visual_style(visual_style: str) -> str:
    style = (visual_style or "").strip() or "cinematic Vietnamese suspense, realistic digital painting, dramatic light, moody shadows"
    replacements = {
        r"\bhorror\b": "suspense",
        r"\bgore\b": "dark mystery",
        r"\bviolent\b": "tense",
        r"\bviolence\b": "tension",
        r"\bblood\b": "deep red accent",
    }
    for pattern, replacement in replacements.items():
        style = re.sub(pattern, replacement, style, flags=re.IGNORECASE)
    return style


def _safe_scene_idea(scene: dict, level: int) -> str:
    fallback_by_level = {
        1: "An anxious Vietnamese protagonist inside an old pawn shop, rain outside, warm lamp light, old objects on shelves, tense quiet atmosphere.",
        2: "A restrained suspense frame: an old shop interior, rain-streaked doorway, one worried character studying an old keepsake under dim lamp light.",
        3: "A quiet Vietnamese mystery scene with worn walls, vintage props, rain, shadowy corners, and a solitary character in thought.",
        4: "An empty old Vietnamese shop interior at night, rain outside, warm lamp glow, dusty shelves, old ledger and keepsake on a wooden table.",
        5: "Symbolic suspense still life: rain on metal roof, oil lamp glow, aged paper, old keepsake, dark Vietnamese interior, cinematic shadows.",
    }
    if level >= 4:
        return fallback_by_level.get(min(level, 5), fallback_by_level[5])

    text = re.sub(r"\s+", " ", scene.get("text", "")).strip()
    parts = [p.strip(" ,;:-") for p in re.split(r"(?<=[.!?。！？])\s+|\n+", text) if p.strip(" ,;:-")]
    safe_parts = [part for part in parts if not _looks_sensitive(part)]
    idea = " ".join(safe_parts[:2]).strip()
    if not idea or level >= 3:
        return fallback_by_level.get(min(level, 5), fallback_by_level[5])

    limit = 360 if level == 1 else 240
    if len(idea) > limit:
        idea = idea[:limit].rsplit(" ", 1)[0].strip(" ,;:-")
    return idea or fallback_by_level.get(min(level, 5), fallback_by_level[5])


def _build_safe_scene_prompt(scene: dict, plan: dict, level: int) -> str:
    safe_level = max(1, min(5, int(level or 1)))
    style = _safe_visual_style(plan.get("visual_style", ""))
    title = (plan.get("story_title") or "Untitled story").strip()
    scene_idea = _safe_scene_idea(scene, safe_level)
    level_notes = {
        1: "Keep the frame restrained and PG-13. Use lighting, distance, posture, and environment to imply tension.",
        2: "Make the image indirect and symbolic. Focus on atmosphere, expression, old objects, rain, and lamp light.",
        3: "Use a quiet establishing shot. Prioritize location, mood, character isolation, and visual continuity.",
        4: "Use an empty environment with story props only. No physical confrontation or explicit action.",
        5: "Use abstract mood and still-life details only, while matching the story palette.",
    }
    return (
        "Create one 16:9 cinematic Vietnamese suspense story frame for narration.\n"
        f"Style: {style}.\n"
        f"Title: {title}\n"
        "Continuity: keep the same Vietnamese setting, era, muted color palette, rain, warm practical light, worn interiors, and realistic props. "
        "Do not include readable text, captions, subtitles, logo, watermark, or UI.\n"
        f"Safe prompt level {safe_level}: {level_notes[safe_level]}\n"
        f"Scene idea: {scene_idea}\n"
        "Composition must be clean enough for later subtitles."
    )


_SAFE_LLM_SYSTEM = """You rewrite image-generation prompts for cinematic story frames.
Return one safe visual prompt only, no JSON, no markdown, no explanations.
The rewritten prompt must preserve the scene's setting, characters, mood, continuity, and key non-violent props.
It must avoid explicit violence, gore, blood, weapons, corpses, injuries, assault, death descriptions, and direct criminal acts.
Use indirect suspense instead: lighting, composition, distance, facial expression, weather, environment, shadows, symbolic objects, and cinematic atmosphere.
Keep it suitable for a 16:9 narrated video frame with space for subtitles."""


def _clean_llm_safe_prompt(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        cleaned = parts[1] if len(parts) > 1 else cleaned
        if cleaned.lower().startswith(("text", "prompt", "markdown")):
            cleaned = cleaned.split("\n", 1)[-1]
    cleaned = cleaned.strip().strip('"').strip()
    cleaned = re.sub(r"^(safe\s+)?prompt\s*:\s*", "", cleaned, flags=re.IGNORECASE).strip()
    if len(cleaned) > 1400:
        cleaned = cleaned[:1400].rsplit(" ", 1)[0].strip()
    return cleaned


def _build_safe_llm_scene_prompt(scene: dict, plan: dict, level: int, cfg: dict) -> str:
    safe_level = max(1, min(5, int(level or 1)))
    model = (cfg.get("safe_prompt_model") or cfg.get("default_story_model") or "claude-sonnet-4-6").strip()
    api_key = cfg.get("anthropic_api_key", "")
    ollama_url = cfg.get("ollama_url", "http://localhost:11434")
    rule_prompt = _build_safe_scene_prompt(scene, plan, safe_level)
    user_prompt = (
        f"Story title: {plan.get('story_title') or 'Untitled story'}\n"
        f"Visual style: {_safe_visual_style(plan.get('visual_style', ''))}\n"
        f"Scene time: {scene.get('start_sec')}s-{scene.get('end_sec')}s\n"
        f"Scene narration text:\n{scene.get('text', '')}\n\n"
        f"Current original image prompt:\n{scene.get('original_prompt') or scene.get('prompt') or ''}\n\n"
        f"Rule-based safe prompt baseline:\n{rule_prompt}\n\n"
        f"Safety level requested: {safe_level}. "
        "Write a better safe prompt that keeps the scene recognizable while avoiding safety rejection. "
        "Return only the final image prompt."
    )
    try:
        if (cfg.get("safe_prompt_provider") or "default").strip().lower() == "custom":
            rewritten = generate_text_openai_compatible(
                _SAFE_LLM_SYSTEM,
                user_prompt,
                base_url=cfg.get("safe_prompt_base_url") or "https://api.openai.com",
                path=cfg.get("safe_prompt_path") or "/v1/chat/completions",
                model=(cfg.get("safe_prompt_model") or "gpt-4o-mini").strip(),
                api_key=cfg.get("safe_prompt_api_key") or "",
                api_key_path=cfg.get("safe_prompt_api_key_path") or "",
                max_tokens=900,
            )
        else:
            rewritten = generate_text(
                _SAFE_LLM_SYSTEM,
                user_prompt,
                model,
                api_key,
                ollama_url,
                max_tokens=900,
            )
    except Exception as exc:
        raise RuntimeError(f"Safe LLM prompt failed: {exc}")
    cleaned = _clean_llm_safe_prompt(rewritten)
    if not cleaned:
        raise RuntimeError("Safe LLM returned an empty prompt")
    return cleaned


def _active_scene_prompt(scene: dict) -> tuple[str, str, int]:
    mode = str(scene.get("active_prompt_mode") or scene.get("prompt_mode") or "").strip().lower()
    director_prompt = str(scene.get("director_prompt") or "").strip()
    safe_rule_prompt = str(scene.get("safe_rule_prompt") or scene.get("safe_prompt") or "").strip()
    safe_llm_prompt = str(scene.get("safe_llm_prompt") or "").strip()
    try:
        safe_rule_level = int(scene.get("safe_rule_level") or scene.get("safe_level") or 0)
    except (TypeError, ValueError):
        safe_rule_level = 0
    try:
        safe_llm_level = int(scene.get("safe_llm_level") or 0)
    except (TypeError, ValueError):
        safe_llm_level = 0
    if mode in {"safe_llm", "llm"} and safe_llm_prompt and safe_llm_level > 0:
        return safe_llm_prompt, "safe_llm", safe_llm_level
    if mode in {"safe_rule", "safe", "rule"} and safe_rule_prompt and safe_rule_level > 0:
        return safe_rule_prompt, "safe_rule", safe_rule_level
    if mode in {"director", "storyboard"} and director_prompt:
        return director_prompt, "director", 0
    if safe_rule_prompt and safe_rule_level > 0:
        return safe_rule_prompt, "safe_rule", safe_rule_level
    if safe_llm_prompt and safe_llm_level > 0:
        return safe_llm_prompt, "safe_llm", safe_llm_level
    if director_prompt:
        return director_prompt, "director", 0
    return str(scene.get("prompt") or "").strip(), "original", 0


def _scene_cfg() -> dict:
    cfg = image_provider_config(load_config())
    return {
        **cfg,
        "image_model": cfg.get("model", ""),
        "image_size": cfg.get("size", ""),
        "image_resolution": cfg.get("resolution", ""),
    }


def _auth_headers(cfg: dict) -> dict:
    if not cfg.get("api_key"):
        raise RuntimeError("Missing gpt2api API key. Start gpt2api or set scene_gpt2api_api_key.")
    return {"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"}


def _create_image_task(cfg: dict, prompt: str) -> str:
    body = {
        "model": cfg["image_model"],
        "prompt": prompt,
        "resolution": cfg["image_resolution"],
        "params": {"resolution": cfg["image_resolution"], "size_tier": cfg["image_resolution"]},
        "size": cfg["image_size"],
        "n": 1,
        "async": True,
    }
    url = f"{cfg['base_url']}/images/generations"
    res = requests.post(url, headers=_auth_headers(cfg), json=body, timeout=90)
    data = _read_response_json(res)
    if not res.ok:
        raise RuntimeError(_error_message(data, f"Image task failed ({res.status_code}) at {url}"))
    task_id = str(data.get("task_id") or data.get("id") or "").strip()
    if not task_id:
        raise RuntimeError("Image task response did not include task_id")
    return task_id


def _poll_image_task(cfg: dict, task_id: str, project: str, scene_id: str) -> tuple[str, int]:
    timeout_at = time.time() + 15 * 60
    last_progress = 0
    while time.time() < timeout_at:
        if project in _cancelled:
            raise RuntimeError("Scene image generation cancelled")
        url = f"{cfg['base_url']}/images/generations/{task_id}"
        res = requests.get(url, headers=_auth_headers(cfg), timeout=60)
        data = _read_response_json(res)
        if not res.ok:
            raise RuntimeError(_error_message(data, f"Image task poll failed ({res.status_code})"))
        status = str(data.get("status") or "").lower()
        last_progress = int(float(data.get("progress") or last_progress or 0))
        if status in {"succeeded", "success", "completed"}:
            image_url = _first_image_candidate(data)
            if not image_url:
                raise RuntimeError("Image task succeeded but returned no image URL")
            return image_url, 100
        if status in {"failed", "refunded"}:
            raise RuntimeError(_error_message(data, "Image generation failed"))
        time.sleep(3)
    raise RuntimeError(f"Image task timed out for {scene_id}")


def _read_response_json(res: requests.Response) -> dict:
    try:
        return res.json()
    except ValueError:
        return {"raw": res.text}


def _error_message(data: dict, fallback: str) -> str:
    err = data.get("error")
    if isinstance(err, dict) and err.get("message"):
        return str(err["message"])
    if data.get("raw"):
        return str(data["raw"])
    return fallback


def _first_image_candidate(data: dict) -> str:
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    rows = result.get("data") if isinstance(result.get("data"), list) else data.get("data")
    if not isinstance(rows, list):
        rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate = _scene_image_candidate_from_mapping(row)
        if candidate:
            return candidate
    for source in (data, result):
        candidate = _scene_image_candidate_from_mapping(source)
        if candidate:
            return candidate
        for key in ("output", "outputs", "images", "result"):
            candidate = _scene_image_candidate_from_value(source.get(key))
            if candidate:
                return candidate
    return ""


def _scene_image_candidate_from_mapping(data: dict) -> str:
    for key in ("url", "image_url", "imageUrl", "b64_json", "base64", "image", "download_url", "output_url"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        candidate = _scene_image_candidate_from_string(
            value,
            allow_plain_base64=key in {"b64_json", "base64", "image"},
        )
        if candidate:
            return candidate
    return ""


def _scene_image_candidate_from_value(value) -> str:
    if isinstance(value, dict):
        candidate = _scene_image_candidate_from_mapping(value)
        if candidate:
            return candidate
        for nested in value.values():
            candidate = _scene_image_candidate_from_value(nested)
            if candidate:
                return candidate
    if isinstance(value, list):
        for item in value:
            candidate = _scene_image_candidate_from_value(item)
            if candidate:
                return candidate
    return ""


def _scene_image_candidate_from_string(value: str, *, allow_plain_base64: bool = False) -> str:
    raw = value.strip()
    if raw.startswith("data:"):
        comma = raw.find(",")
        if comma <= 0 or not raw[comma + 1:].strip():
            return ""
        return raw
    data_url = _scene_base64_image_data_url(raw)
    if data_url:
        return data_url
    if re.search(r"/images/generations/[^/?#]+/?$", raw, re.IGNORECASE):
        return ""
    if raw.startswith(("http://", "https://", "/")):
        return raw
    if allow_plain_base64:
        return f"data:image/png;base64,{raw}"
    return ""


def _scene_base64_image_data_url(value: str) -> str:
    compact = re.sub(r"\s+", "", value or "")
    if len(compact) < 100:
        return ""
    if compact.startswith("/9j/"):
        return f"data:image/jpeg;base64,{compact}"
    if compact.startswith("iVBOR"):
        return f"data:image/png;base64,{compact}"
    if compact.startswith("UklGR"):
        return f"data:image/webp;base64,{compact}"
    return ""


def _save_image(project: str, scene: dict, image_value: str, cfg: dict, filename: str | None = None) -> str:
    scene_dir = _scene_dir(project)
    filename = filename or f"{scene['id']}.png"
    if not _SAFE_IMAGE.match(filename):
        raise RuntimeError("Invalid scene image filename")
    target = scene_dir / "images" / filename
    image_value = _scene_base64_image_data_url(image_value) or image_value
    if image_value.startswith("data:"):
        comma = image_value.find(",")
        if comma <= 0 or not image_value[comma + 1:].strip():
            raise RuntimeError("Invalid data URL returned by image task")
        target.write_bytes(base64.b64decode(image_value[comma + 1:]))
    else:
        url = f"{cfg.get('asset_base_url', '').rstrip('/')}{image_value}" if image_value.startswith("/") else image_value
        res = requests.get(url, timeout=120)
        if not res.ok:
            raise RuntimeError(f"Cannot download generated image ({res.status_code})")
        if not res.content:
            raise RuntimeError("Image task returned an empty image download")
        target.write_bytes(res.content)
    if not target.exists() or target.stat().st_size <= 0:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError("Image task returned an empty image file")
    return filename


def _create_base_plan(
    *,
    project: str,
    audio_file: str,
    story_text: str,
    story_title: str,
    visual_style: str,
    target_scene_sec: float,
    min_scene_sec: float,
    max_scene_sec: float,
    max_scenes: int,
    mode: str,
) -> dict:
    project_dir = _require_project(project)
    cfg = load_config()
    duration = _audio_duration(project_dir, audio_file, cfg.get("ffprobe_path", "ffprobe"))
    cues, timing_source = _load_cues(project_dir, audio_file, story_text, duration)
    scenes = _group_cues(cues, target_scene_sec, min_scene_sec, max_scene_sec, max_scenes)
    story_bible = _build_story_bible(story_title, story_text, visual_style)
    for scene in scenes:
        scene["prompt"] = _build_scene_prompt(scene, story_bible, visual_style)
        scene["active_prompt_mode"] = "original"
        scene["prompt_mode"] = "original"
    return {
        "project": project,
        "mode": mode,
        "audio_file": audio_file,
        "story_title": story_title,
        "visual_style": visual_style,
        "story_bible": story_bible,
        "timing_source": timing_source,
        "audio_duration_sec": round(duration, 3),
        "settings": {
            "target_scene_sec": target_scene_sec,
            "min_scene_sec": min_scene_sec,
            "max_scene_sec": max_scene_sec,
            "max_scenes": max_scenes,
        },
        "scenes": scenes,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


@router.post("/plan")
def create_scene_plan(body: ScenePlanRequest):
    plan = _create_base_plan(
        project=body.project,
        audio_file=body.audio_file,
        story_text=body.story_text,
        story_title=body.story_title,
        visual_style=body.visual_style,
        target_scene_sec=body.target_scene_sec,
        min_scene_sec=body.min_scene_sec,
        max_scene_sec=body.max_scene_sec,
        max_scenes=body.max_scenes,
        mode="fast",
    )
    return _write_plan(body.project, plan)


@router.post("/{project}/director/prepare")
def prepare_director_plan(project: str, body: DirectorPrepareRequest):
    plan = _create_base_plan(
        project=project,
        audio_file=body.audio_file,
        story_text=body.story_text,
        story_title=body.story_title,
        visual_style=body.visual_style,
        target_scene_sec=body.target_scene_sec,
        min_scene_sec=body.min_scene_sec,
        max_scene_sec=body.max_scene_sec,
        max_scenes=body.max_scenes,
        mode="director",
    )
    if body.use_visual_plan:
        project_dir = _require_project(project)
        duration = float(plan.get("audio_duration_sec") or 0)
        cues, timing_source = _load_cues(project_dir, body.audio_file, body.story_text, duration)
        visual_scenes, visual_plan_status = _director_visual_plan_scenes(
            cues,
            target_scene_sec=body.target_scene_sec,
            min_scene_sec=body.min_scene_sec,
            max_scene_sec=body.max_scene_sec,
            max_scenes=body.max_scenes,
            story_title=body.story_title,
            visual_style=body.visual_style,
            instruction=body.visual_plan_instruction,
        )
        if visual_scenes:
            plan["scenes"] = visual_scenes
            plan["timing_source"] = timing_source
        plan["visual_plan"] = {
            "enabled": True,
            "status": visual_plan_status,
            "instruction": str(body.visual_plan_instruction or "").strip()[:2000],
            "created_at": _now_iso(),
        }
    else:
        plan["visual_plan"] = {"enabled": False, "status": "fixed_duration", "created_at": _now_iso()}
    visual_bible = _generate_visual_bible(plan, body.story_text)
    references = _generate_reference_plan(plan, visual_bible)
    plan["visual_bible"] = visual_bible
    plan["references"] = references
    plan["director"] = {
        "status": "prepared",
        "model": (_director_cfg().get("visual_director_model") or _director_cfg().get("default_story_model") or "").strip(),
        "created_at": _now_iso(),
    }

    storyboard_items: list[dict] = []
    scenes = plan.get("scenes", [])
    batch_size = 10
    for offset in range(0, len(scenes), batch_size):
        batch = scenes[offset:offset + batch_size]
        storyboard_items.extend(_generate_storyboard_batch(plan, batch, visual_bible, references))
    by_id = {item.get("scene_id"): item for item in storyboard_items}
    for scene in scenes:
        storyboard = by_id.get(scene.get("id")) or _normalize_storyboard_item({}, scene, visual_bible, references, plan)
        scene["storyboard"] = storyboard
        scene["director_prompt"] = storyboard.get("prompt") or _build_director_prompt(scene, storyboard, visual_bible, references, plan)
        scene["reference_ids"] = storyboard.get("reference_ids") or []
        scene["active_prompt_mode"] = "director"
        scene["prompt_mode"] = "director"
    plan["storyboard_created_at"] = _now_iso()
    return _write_plan(project, plan)


_STICK_FIGURE_DEFAULT_STYLE = (
    "2D hand-drawn stick-figure historical explainer, clean black ink outlines, warm off-white paper, "
    "muted earth brown and icy blue palette, flat editorial infographic composition"
)


def _stick_style_meta_path(project: str) -> Path:
    return _scene_dir(project) / "stick_figure_style_source.json"


def _safe_reference_id(value: str, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii").lower()
    cleaned = re.sub(r"[^a-z0-9_-]+", "_", normalized).strip("_-")
    return cleaned or fallback


def _stick_reference_record(
    ref_id: str,
    ref_type: str,
    name: str,
    description: str,
    prompt: str,
    *,
    image_file: str = "",
    source: str = "text",
) -> dict:
    record = {
        "id": ref_id,
        "type": _normalize_reference_type(ref_type),
        "name": name or ref_id,
        "description": description,
        "prompt": prompt,
        "status": "succeeded" if image_file else "pending",
        "image_file": image_file,
        "image_history": [],
        "error": "",
        "created_at": _now_iso(),
        "source": source,
    }
    if image_file:
        record["image_history"] = [{
            "id": Path(image_file).stem,
            "image_file": image_file,
            "selected": True,
            "created_at": _now_iso(),
            "source": source,
        }]
    return record


_STICK_TARGET_BEAT_MIN_SEC = 3.0
_STICK_TARGET_BEAT_MAX_SEC = 10.0
_STICK_HARD_BEAT_MAX_SEC = 12.0
_STICK_TARGET_BEAT_SEC = 8.0
_STICK_COLOR_USAGES = {"adaptive_focus", "primary_focus", "accent_focus", "highlight_focus"}


def _stick_project_color_bible(*, has_style_sheet: bool = False) -> dict:
    """One stable palette for every stick asset in a single visual plan."""
    if has_style_sheet:
        return {
            "palette_source": "uploaded_style_sheet",
            "base_background": "the light neutral base from the uploaded style sheet",
            "outline": "the consistent dark outline from the uploaded style sheet",
            "primary": "the uploaded style sheet's dominant story colour",
            "accent": "the uploaded style sheet's supporting accent colour",
            "highlight": "the uploaded style sheet's brightest emphasis colour",
        }
    return {
        "palette_source": "ai_art_direction_pending",
        "base_background": "background tone chosen by AI from the story mood",
        "outline": "outline tone chosen by AI for legibility",
        "primary": "story-derived primary colour chosen by AI",
        "accent": "story-derived accent colour chosen by AI",
        "highlight": "story-derived highlight colour chosen by AI",
    }


def _stick_color_usage_instruction(color_bible: dict, usage: str) -> str:
    usage = usage if usage in _STICK_COLOR_USAGES else "adaptive_focus"
    base = color_bible.get("base_background") or "light neutral base"
    outline = color_bible.get("outline") or "dark outline"
    primary = color_bible.get("primary") or "project primary"
    accent = color_bible.get("accent") or "project accent"
    highlight = color_bible.get("highlight") or "project highlight"
    focus = {
        "adaptive_focus": "the palette role that best serves this beat's emotion and evidence",
        "primary_focus": primary,
        "accent_focus": accent,
        "highlight_focus": highlight,
    }[usage]
    return (
        f"Use only this shared project palette: base={base}; outline={outline}; primary={primary}; accent={accent}; highlight={highlight}. "
        f"This beat uses {focus} as the focal colour; all other palette roles stay supporting. Preserve the project's colour relationships, but make an expressive scene-specific choice rather than defaulting to one colour."
    )


def _normalize_stick_color_usage(value: object) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in _STICK_COLOR_USAGES:
        return raw
    return "adaptive_focus"


def _stick_figure_visual_bible(storyboard: dict, style: str, title: str, project_color_bible: dict | None = None) -> dict:
    raw_refs = storyboard.get("references") if isinstance(storyboard.get("references"), list) else []
    characters, locations, props = [], [], []
    for raw in raw_refs:
        if not isinstance(raw, dict):
            continue
        item = {
            "id": str(raw.get("id") or ""),
            "name": str(raw.get("name") or ""),
            "description": str(raw.get("description") or ""),
        }
        ref_type = _normalize_reference_type(str(raw.get("type") or "misc"))
        if ref_type == "character":
            characters.append(item)
        elif ref_type == "location":
            locations.append(item)
        elif ref_type == "prop":
            props.append(item)
    color_bible = project_color_bible or _stick_project_color_bible()
    return {
        "logline": f"Stick-figure explainer: {title or 'Untitled'}",
        "visual_style": style,
        "palette": [color_bible.get("base_background"), color_bible.get("outline"), color_bible.get("primary"), color_bible.get("accent"), color_bible.get("highlight")],
        "project_color_bible": color_bible,
        "color_system": "The project palette is art-directed by AI once from this story. Preserve its relationships across references, storyboard and final scenes, while choosing the focal palette role freely per beat.",
        "continuity_rules": [
            "Match the uploaded style sheet whenever present.",
            "Keep every recurring stick figure, prop, color cue, and location silhouette consistent.",
            "Use a direct, energetic explanatory composition: one large readable subject, a visible action or clue, and only the explicit text requested by the storyboard.",
            "Preserve the AI art-directed project palette while varying focal role, composition and saturation according to each beat's emotion and evidence.",
        ],
        "characters": characters,
        "locations": locations,
        "props": props,
    }


_STICK_STORYBOARD_PANELS_PER_SHEET = 16
_STICK_STORYBOARD_MAX_SHEETS = 3


def _stick_storyboard_sheets(scenes: list[dict], style: str, story_title: str, project_color_bible: dict | None = None) -> list[dict]:
    """Create a compact contact-sheet storyboard for review, never a per-shot image reference."""
    if not scenes:
        return []
    sheet_count = min(
        _STICK_STORYBOARD_MAX_SHEETS,
        max(1, (len(scenes) + _STICK_STORYBOARD_PANELS_PER_SHEET - 1) // _STICK_STORYBOARD_PANELS_PER_SHEET),
    )
    panels_per_sheet = (len(scenes) + sheet_count - 1) // sheet_count
    sheets = []
    for sheet_index, offset in enumerate(range(0, len(scenes), panels_per_sheet), start=1):
        panel_scenes = scenes[offset:offset + panels_per_sheet]
        panel_lines = []
        for scene in panel_scenes:
            panel_lines.append(
                f"{scene.get('index', 0):02d}. {scene.get('visual') or scene.get('storyboard_caption') or scene.get('text') or ''}"[:420]
            )
        grid = "4-column contact sheet" if len(panel_scenes) > 8 else "clean multi-panel contact sheet"
        description = (
            f"Planning overview {sheet_index}/{sheet_count}, shots {panel_scenes[0].get('index', 1)}-{panel_scenes[-1].get('index', len(scenes))}. "
            + " | ".join(panel_lines)
        )[:3000]
        colour_rule = _stick_color_usage_instruction(project_color_bible or _stick_project_color_bible(), "adaptive_focus")
        prompt = (
            "Create one 16:9 STORYBOARD CONTACT SHEET for production planning, not a polished final video frame.\n"
            f"Story: {story_title or 'Untitled story'}\n"
            f"Style: {style}\n"
            f"Layout: {grid}, {len(panel_scenes)} clearly separated small panels in left-to-right, top-to-bottom order.\n"
            "Every panel is a simple, readable thumbnail of one consecutive narrated shot. Keep recurring characters, props, locations, and palette consistent across panels. "
            f"{colour_rule} Keep colour usage consistent across all panels; never give each panel an unrelated palette or make the contact sheet uniformly dull. "
            "Use visual storytelling, arrows, maps, charts, or evidence icons when a panel calls for them. "
            "This sheet is only for reviewing the overall sequence: do not make any panel look like a final standalone thumbnail and do not use it as a frame reference for final scene generation.\n"
            "Avoid readable captions, subtitles, logos, watermarks, gore, and distorted anatomy. Tiny panel numbers are optional; panel order must remain visually clear.\n"
            "Ordered panel brief:\n"
            + "\n".join(panel_lines)
        )
        sheets.append(_stick_reference_record(
            f"storyboard_sheet_{sheet_index:02d}",
            "storyboard",
            f"Storyboard overview {sheet_index}/{sheet_count}",
            description,
            prompt,
            source="storyboard_contact_sheet",
        ))
    return sheets


def _stick_scene_prompt(scene: dict, style: str, references: list[dict], project_color_bible: dict | None = None) -> str:
    ref_ids = set(scene.get("reference_ids") or [])
    ref_lines = [
        f"{ref.get('id')}: {ref.get('name')} — {ref.get('description')}"
        for ref in references if ref.get("id") in ref_ids
    ]
    overlay = str(scene.get("on_screen_text") or "").strip()
    output_locale = _stick_output_locale(scene.get("output_locale"))
    overlay_language = _normalize_overlay_language(scene.get("overlay_language"))
    text_instruction = (
        f'Allowed readable text is ONLY this exact string: "{overlay}". '
        f"It has passed locale QA for {output_locale} ({overlay_language}); preserve its spelling exactly. "
        "Do not translate it, add a second language, or add any other readable text. Use clean intentional editorial lettering; it is content, never a watermark."
        if overlay else "Do not render any readable text whatsoever: no English labels, signs, infographic captions, speech bubbles, subtitles, logos, or watermarks."
    )
    return (
        "Create one 16:9 frame for a narrated 2D stick-figure explainer video.\n"
        f"Style: {style}\n"
        f"Full narration beat (context only): {scene.get('text') or ''}\n"
        f"Narration meaning available when this frame appears (highest priority): {scene.get('visual_anchor') or scene.get('text') or ''}\n"
        f"Unique visual objective: {scene.get('visual') or scene.get('visual_anchor') or scene.get('text') or ''}\n"
        f"Camera: {scene.get('camera') or 'clear explanatory medium-wide view'}\n"
        f"Composition: {scene.get('composition') or 'clean editorial infographic hierarchy'}\n"
        f"Emotion: {scene.get('emotion') or 'curious and factual'}\n"
        f"Target output locale: {output_locale}\n"
        f"Colour direction: {_stick_color_usage_instruction(project_color_bible or _stick_project_color_bible(), scene.get('color_usage') or scene.get('color_direction'))}\n"
        f"Continuity references: {'; '.join(ref_lines) if ref_lines else 'follow the visual bible'}\n"
        f"{text_instruction}\n"
        "This is one unique shot in a sequence. Depict only the narration meaning available when the shot starts, not a generic montage or a variant of the broader source scene. "
        "Do NOT reveal a later action, outcome, twist, clue, number, or evidence from the full narration beat before it is spoken. "
        "Make the subject, action, evidence, camera, or composition visibly distinct from adjacent narration beats. "
        "Make the frame lively and scroll-stopping: one oversized focal subject, a clear action/evidence cue, strong value contrast, and controlled vivid colour. Avoid dull, evenly brown/grey, empty, or static compositions. "
        "Use only the style and continuity references to preserve recurring characters, location, era, and props. "
        "No watermark, no brand logo, no gore, no distorted anatomy."
    )


def _timed_words(cues: list[dict]) -> list[tuple[float, float]]:
    """Approximate word boundaries from the final, timestamped subtitle cues."""
    timeline: list[tuple[float, float]] = []
    for cue in cues:
        words = re.findall(r"[^\W_]+(?:['’\-][^\W_]+)*", str(cue.get("text") or ""), flags=re.UNICODE)
        if not words:
            continue
        start = float(cue.get("start_sec", 0) or 0)
        end = max(start, float(cue.get("end_sec", start) or start))
        weights = [max(1, len(word)) for word in words]
        total_weight = sum(weights) or 1
        cursor = start
        for index, weight in enumerate(weights):
            word_end = end if index == len(weights) - 1 else min(end, cursor + (end - start) * weight / total_weight)
            timeline.append((round(cursor, 3), round(word_end, 3)))
            cursor = word_end
    return timeline


def _stick_timing_from_audio_cues(scenes: list[dict], cues: list[dict], total_duration: float) -> list[tuple[float, float]] | None:
    """Map ordered scene narration to the actual audio timeline, not LLM estimates."""
    word_timeline = _timed_words(cues)
    narration_words = [
        re.findall(r"[^\W_]+(?:['’\-][^\W_]+)*", str(scene.get("narration") or scene.get("text") or ""), flags=re.UNICODE)
        for scene in scenes
    ]
    narration_total = sum(len(words) for words in narration_words)
    if not word_timeline or not narration_total:
        return None
    audio_per_narration_word = len(word_timeline) / narration_total
    cumulative_words = 0
    result: list[tuple[float, float]] = []
    for index, words in enumerate(narration_words):
        start_index = min(len(word_timeline) - 1, int(round(cumulative_words * audio_per_narration_word)))
        cumulative_words += len(words)
        end_index = min(len(word_timeline) - 1, max(start_index, int(round(cumulative_words * audio_per_narration_word)) - 1))
        start = 0.0 if index == 0 else word_timeline[start_index][0]
        end = total_duration if index == len(scenes) - 1 else word_timeline[end_index][1]
        result.append((round(start, 3), round(max(start + 0.04, end), 3)))
    return result


def _stick_timed_scenes(raw_scenes: list[dict], total_duration: float, cues: list[dict] | None = None) -> list[dict]:
    usable = [
        scene for scene in raw_scenes
        if isinstance(scene, dict) and str(scene.get("narration") or scene.get("text") or "").strip()
    ]
    if not usable:
        raise HTTPException(400, "Blueprint không có narration theo từng cảnh.")
    audio_timing = _stick_timing_from_audio_cues(usable, cues or [], total_duration)
    weights = []
    for scene in usable:
        try:
            hint = float(scene.get("duration_hint_sec") or 0)
        except (TypeError, ValueError):
            hint = 0
        weights.append(max(1.0, hint or len(str(scene.get("narration") or scene.get("text") or "")) / 12.0))
    total_weight = sum(weights) or 1.0
    cursor = 0.0
    result = []
    for index, (raw, weight) in enumerate(zip(usable, weights), start=1):
        if audio_timing:
            start, end = audio_timing[index - 1]
        else:
            start = cursor
            end = total_duration if index == len(usable) else min(total_duration, cursor + total_duration * weight / total_weight)
        scene_id = _safe_reference_id(str(raw.get("id") or ""), f"scene_{index:03d}")
        if not scene_id.startswith("scene_"):
            scene_id = f"scene_{index:03d}_{scene_id}"
        result.append({
            "id": scene_id,
            "index": index,
            "start_sec": round(start, 3),
            "end_sec": round(end, 3),
            "duration_sec": round(max(0.04, end - start), 3),
            "text": str(raw.get("narration") or raw.get("text") or "").strip(),
            "visual": str(raw.get("visual") or "").strip(),
            "parent_visual_context": str(raw.get("parent_visual_context") or "").strip(),
            "camera": str(raw.get("camera") or "").strip(),
            "composition": str(raw.get("composition") or "").strip(),
            "emotion": str(raw.get("emotion") or "").strip(),
            "on_screen_text": str(raw.get("on_screen_text") or "").strip(),
            "transition": str(raw.get("transition") or "cut").strip().lower(),
            "reference_ids": [str(item) for item in (raw.get("reference_ids") or []) if str(item).strip()],
            "storyboard_caption": str(raw.get("storyboard_caption") or raw.get("visual") or "").strip(),
        })
        cursor = end
    return result


_STICK_MAX_WORDS_PER_AUDIO_VISUAL = 18


def _split_stick_audio_visual_beats(narration: str, max_words: int = _STICK_MAX_WORDS_PER_AUDIO_VISUAL) -> list[str]:
    """Split legacy narration beats into sentence-first final image beats."""
    sentences = [
        item.strip()
        for item in re.split(r"(?<=[.!?…])\s+|(?<=[。！？])", narration or "")
        if item.strip()
    ]
    beats: list[str] = []
    for sentence in sentences:
        words = sentence.split()
        while len(words) > max_words:
            natural_pauses = [
                index + 1 for index, word in enumerate(words[:max_words])
                if word.rstrip().endswith((",", ";", ":"))
            ]
            cut = natural_pauses[-1] if natural_pauses else max_words
            beats.append(" ".join(words[:cut]))
            words = words[cut:]
        if words:
            beats.append(" ".join(words))
    return beats


def _retarget_stick_scenes_to_narration(raw_scenes: list[dict]) -> list[dict]:
    """Make stored and new stick-figure shots narration-first before rendering.

    Older blueprints already contain many microbeats, but each one inherited
    the same broad source visual. Retargeting here means pressing Prepare on an
    existing session upgrades it too, without needing to recreate the audio.
    """
    retargeted: list[dict] = []
    for raw in raw_scenes:
        if not isinstance(raw, dict):
            continue
        narration = str(raw.get("narration") or raw.get("text") or "").strip()
        if not narration:
            continue
        parent_visual_context = str(
            raw.get("parent_visual_context") or raw.get("visual") or ""
        ).strip()
        beats = _split_stick_audio_visual_beats(narration)
        try:
            original_hint = float(raw.get("duration_hint_sec") or 0)
        except (TypeError, ValueError):
            original_hint = 0
        beat_word_counts = [max(1, len(beat.split())) for beat in beats]
        total_words = sum(beat_word_counts) or 1
        for beat_index, beat in enumerate(beats, start=1):
            scene = dict(raw)
            if len(beats) > 1:
                scene["id"] = f"{raw.get('id') or 'scene'}_{beat_index:02d}"
                # Dates and labels are a single editorial beat, never repeated
                # after the sentence that introduced them.
                if beat_index > 1:
                    scene["on_screen_text"] = ""
            scene["narration"] = beat
            if original_hint:
                scene["duration_hint_sec"] = round(original_hint * beat_word_counts[beat_index - 1] / total_words, 2)
            scene["parent_visual_context"] = parent_visual_context
            scene["visual"] = f"Illustrate this exact narration idea as one distinct visual beat: {beat}"
            retargeted.append(scene)
    return retargeted


def _retime_stick_figure_plan(project_dir: Path, plan: dict, audio_file: str, duration: float) -> bool:
    """Refresh an existing stick-figure plan from final TTS timing before export."""
    storyboard = plan.get("storyboard") if isinstance(plan.get("storyboard"), dict) else {}
    cues, timing_source = _load_cues(project_dir, audio_file, str(storyboard.get("tts_text") or ""), duration)
    if timing_source != "timing_metadata":
        return False
    scenes = plan.get("scenes") if isinstance(plan.get("scenes"), list) else []
    timed = _stick_timed_scenes(scenes, duration, cues)
    if len(timed) != len(scenes):
        return False
    changed = any(
        abs(float(old.get("start_sec", 0) or 0) - new["start_sec"]) > 0.01
        or abs(float(old.get("end_sec", 0) or 0) - new["end_sec"]) > 0.01
        for old, new in zip(scenes, timed)
    )
    for old, new in zip(scenes, timed):
        old["start_sec"] = new["start_sec"]
        old["end_sec"] = new["end_sec"]
        old["duration_sec"] = new["duration_sec"]
    plan["audio_duration_sec"] = round(duration, 3)
    plan["timing_source"] = "stick_figure_audio_cues"
    return changed


@router.post("/{project}/stick-figure/style-reference")
async def upload_stick_figure_style_reference(project: str, file: UploadFile = File(...)):
    _require_project(project)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(400, "Style reference phải là PNG, JPG hoặc WEBP.")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Style reference trống.")
    if len(content) > 30 * 1024 * 1024:
        raise HTTPException(400, "Style reference tối đa 30 MB.")
    ref_id = "style_source"
    filename = f"{ref_id}_{uuid.uuid4().hex[:10]}{ext}"
    target = _reference_dir(project, "style") / filename
    target.write_bytes(content)
    meta = {"id": ref_id, "type": "style", "filename": filename, "uploaded_at": _now_iso()}
    _stick_style_meta_path(project).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return {"reference_id": ref_id, "image_file": filename, "size_bytes": len(content)}


def _legacy_prepare_stick_figure_plan(project: str, body: dict):
    project_dir = _require_project(project)
    cfg = load_config()
    total_duration = _audio_duration(project_dir, body.audio_file, cfg.get("ffprobe_path", "ffprobe"))
    blueprint = body.storyboard if isinstance(body.storyboard, dict) else {}
    raw_scenes = blueprint.get("scenes") if isinstance(blueprint.get("scenes"), list) else []
    raw_scenes = _retarget_stick_scenes_to_narration(raw_scenes)
    audio_cues, timing_source = _load_cues(
        project_dir, body.audio_file, str(blueprint.get("tts_text") or ""), total_duration,
    )
    scenes = _stick_timed_scenes(
        raw_scenes, total_duration,
        audio_cues if timing_source == "timing_metadata" else None,
    )
    style = (body.visual_style or blueprint.get("visual_style") or _STICK_FIGURE_DEFAULT_STYLE).strip()
    references: list[dict] = []
    source_meta_path = _stick_style_meta_path(project)
    if source_meta_path.exists():
        try:
            source_meta = json.loads(source_meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            source_meta = {}
        filename = str(source_meta.get("filename") or "")
        if filename and _SAFE_REF_IMAGE.match(filename) and (_reference_dir(project, "style") / filename).exists():
            references.append(_stick_reference_record(
                "style_source", "style", "Uploaded style sheet", "User supplied visual style sheet; match its line work, palette, character proportions, and editorial layout.", "", image_file=filename, source="uploaded",
            ))
    project_color_bible = _stick_project_color_bible(has_style_sheet=bool(references))
    visual_bible = _stick_figure_visual_bible(blueprint, style, body.story_title, project_color_bible)
    seen_ids = {ref["id"] for ref in references}
    reference_aliases = {"style_source": "style_source"} if references else {}
    for index, raw in enumerate(blueprint.get("references") or [], start=1):
        if not isinstance(raw, dict):
            continue
        ref_type = _normalize_reference_type(str(raw.get("type") or "misc"))
        raw_ref_id = str(raw.get("id") or "").strip()
        ref_id = _safe_reference_id(raw_ref_id, f"{_reference_prefix(ref_type)}_{index:03d}")
        if ref_id in seen_ids:
            ref_id = f"{ref_id}_{index:03d}"
        seen_ids.add(ref_id)
        if raw_ref_id:
            reference_aliases[raw_ref_id] = ref_id
        reference_aliases[ref_id] = ref_id
        name = str(raw.get("name") or ref_id).strip()
        description = str(raw.get("description") or "").strip()
        prompt = str(raw.get("prompt") or "").strip() or _build_reference_prompt(ref_type, name, description, visual_bible, {"story_title": body.story_title, "visual_style": style})
        references.append(_stick_reference_record(ref_id, ref_type, name, description, prompt))
    storyboard_sheets = _stick_storyboard_sheets(scenes, style, body.story_title, project_color_bible)
    references.extend(storyboard_sheets)
    available_reference_ids = {ref["id"] for ref in references}
    for scene in scenes:
        scene_reference_ids = [reference_aliases.get(str(ref_id), str(ref_id)) for ref_id in scene.get("reference_ids", [])]
        # Contact sheets are for the human review of the whole story only.  Final
        # shots get reusable style/character/location/prop assets, never their
        # own storyboard image, so there is no duplicate scene generation.
        scene["reference_ids"] = list(dict.fromkeys([*scene_reference_ids, "style_source"]))
        scene["reference_ids"] = [ref_id for ref_id in scene["reference_ids"] if ref_id in available_reference_ids]
        scene["storyboard"] = {
            "scene_id": scene["id"], "visual_moment": scene.get("visual") or scene["text"], "camera": scene.get("camera"),
            "composition": scene.get("composition"), "emotion": scene.get("emotion"), "reference_ids": scene["reference_ids"],
            "on_screen_text": scene.get("on_screen_text"), "transition": scene.get("transition"),
        }
        scene["director_prompt"] = _stick_scene_prompt(scene, style, references, project_color_bible)
        scene["prompt"] = scene["director_prompt"]
        scene["active_prompt_mode"] = "director"
        scene["prompt_mode"] = "director"
        scene["status"] = "pending"
    plan = {
        "project": project, "mode": "stick_figure", "audio_file": body.audio_file, "story_title": body.story_title,
        "visual_style": style, "project_color_bible": project_color_bible, "visual_bible": visual_bible, "references": references, "scenes": scenes,
        "storyboard": blueprint,
        "storyboard_sheets": [sheet["id"] for sheet in storyboard_sheets],
        "timing_source": "stick_figure_audio_cues" if timing_source == "timing_metadata" else "stick_figure_duration_hints_scaled_to_audio",
        "audio_duration_sec": round(total_duration, 3),
        "settings": {"target_scene_sec": "AI variable", "min_scene_sec": "AI variable", "max_scene_sec": "AI variable", "max_scenes": len(scenes)},
        "created_at": _now_iso(), "storyboard_created_at": _now_iso(),
    }
    return _write_plan(project, plan)


def _stick_pipeline_call(system: str, user: str, max_tokens: int = 8000) -> str:
    """Use the same provider/model selected for AI lọc content."""
    cfg = load_config()
    provider = str(cfg.get("stick_figure_provider") or "default").strip().lower()
    model = str(cfg.get("stick_figure_model") or cfg.get("default_story_model") or "claude-sonnet-4-6").strip()
    if provider == "custom":
        return generate_text_openai_compatible(
            system, user,
            base_url=cfg.get("stick_figure_base_url") or "https://api.openai.com",
            path=cfg.get("stick_figure_path") or "/v1/chat/completions",
            model=model or "gpt-4o-mini",
            api_key=cfg.get("stick_figure_api_key") or "",
            api_key_path=cfg.get("stick_figure_api_key_path") or "",
            max_tokens=max_tokens,
        )
    return generate_text(
        system, user, model, cfg.get("anthropic_api_key", ""),
        cfg.get("ollama_url", "http://localhost:11434"), max_tokens=max_tokens, cli_timeout_sec=900,
    )


def _stick_pipeline_json(system: str, user: str, max_tokens: int = 8000) -> dict:
    raw = _stick_pipeline_call(system, user, max_tokens=max_tokens)
    data = _parse_json_response(raw)
    if not isinstance(data, dict):
        raise ValueError("AI pipeline returned invalid JSON")
    return data


_OVERLAY_LANGUAGE_MODES = {"target_locale", "source_term", "mixed"}


def _stick_output_locale(value: object) -> str:
    locale = str(value or "vi-VN").strip()
    return locale if re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?", locale) else "vi-VN"


def _normalize_overlay_text(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:80]


def _normalize_overlay_language(value: object) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in _OVERLAY_LANGUAGE_MODES else "target_locale"


_OVERLAY_QA_SYSTEM = """You are a strict multilingual on-screen-text editor for a 2D stick-figure explainer.
Return JSON only: {"overlays":[{"id":"vb_001","text":"...","language":"target_locale|source_term|mixed","reason":"brief"}]}.

For every supplied candidate, return the same id exactly once. The default is target_locale: translate or rewrite explanatory headlines into the requested output locale. Preserve original spelling only when the text is a proper name, official event/place/organization name, internationally standard term, source quotation, or explicitly required original-language label. Use source_term for that exception and mixed only when a short target-language label needs an unchanged proper noun.
Never invent English slogans for a non-English target locale. Keep text concise, grammatical, semantically faithful, and suitable as a single image label. If text is unnecessary, uncertain, or cannot be localized safely, return text as an empty string with target_locale.
"""


def _qa_stick_overlay_text(beats: list[dict], output_locale: str) -> tuple[list[dict], str]:
    candidates = [
        {
            "id": beat.get("id"),
            "text": _normalize_overlay_text(beat.get("on_screen_text")),
            "planner_language": _normalize_overlay_language(beat.get("overlay_language")),
            "planner_reason": str(beat.get("overlay_reason") or "")[:240],
            "narration": str(beat.get("text") or "")[:1200],
            "visual_intent": str(beat.get("visual_intent") or "")[:800],
        }
        for beat in beats if _normalize_overlay_text(beat.get("on_screen_text"))
    ]
    if not candidates:
        return beats, "skipped_no_overlay"
    try:
        payload = json.dumps({"target_locale": output_locale, "candidates": candidates}, ensure_ascii=False)
        data = _stick_pipeline_json(_OVERLAY_QA_SYSTEM, payload, max_tokens=5000)
        rows = data.get("overlays") if isinstance(data.get("overlays"), list) else []
        expected_ids = [str(item["id"]) for item in candidates]
        if len(rows) != len(expected_ids) or [str(item.get("id") or "") for item in rows] != expected_ids:
            raise ValueError("Overlay QA returned missing or out-of-order beat ids")
        reviewed = {str(row["id"]): row for row in rows if isinstance(row, dict)}
        for beat in beats:
            row = reviewed.get(str(beat.get("id") or ""))
            if not row:
                continue
            beat["on_screen_text"] = _normalize_overlay_text(row.get("text"))
            beat["overlay_language"] = _normalize_overlay_language(row.get("language"))
            beat["overlay_reason"] = str(row.get("reason") or "")[:240]
            beat["overlay_qa"] = "passed"
        return beats, "passed"
    except Exception as exc:
        # Never let a transient QA failure create a project-wide English label
        # leak.  Visual imagery remains usable without an overlay.
        for beat in beats:
            if _normalize_overlay_text(beat.get("on_screen_text")):
                beat["on_screen_text"] = ""
                beat["overlay_language"] = "target_locale"
                beat["overlay_reason"] = "Overlay QA fallback: omitted unsafe text"
                beat["overlay_qa"] = "fallback_cleared"
        return beats, f"fallback_cleared: {str(exc)[:180]}"


def _content_narration_units(blueprint: dict) -> list[dict]:
    raw_units = blueprint.get("narration_units") if isinstance(blueprint.get("narration_units"), list) else []
    units = []
    for index, raw in enumerate(raw_units, start=1):
        text = str(raw.get("text") if isinstance(raw, dict) else raw or "").strip()
        if text:
            units.append({"id": str(raw.get("id") or f"n_{index:04d}") if isinstance(raw, dict) else f"n_{index:04d}", "text": text, "order": index})
    if units:
        return units
    text = re.sub(r"\s+", " ", str(blueprint.get("tts_text") or "")).strip()
    sentences = [part.strip() for part in re.split(r"(?<=[.!?…。！？])\s+|(?<=[。！？])", text) if part.strip()]
    parts: list[str] = []
    for sentence in sentences:
        clauses = [part.strip() for part in re.split(r"(?<=[,;:—–])\s+", sentence) if part.strip()]
        pending = ""
        for clause in clauses:
            if pending:
                clause = f"{pending} {clause}"
                pending = ""
            if len(re.findall(r"[^\W_]+", clause, flags=re.UNICODE)) < 4 and clause[-1:] in {",", ";", ":", "—", "–"}:
                pending = clause
            else:
                parts.append(clause)
        if pending:
            if parts:
                parts[-1] = f"{parts[-1]} {pending}"
            else:
                parts.append(pending)
    return [{"id": f"n_{index:04d}", "text": part, "order": index} for index, part in enumerate(parts, start=1)]


def _split_atomic_narration_text(text: str, target_words: int = 22) -> list[str]:
    """Sentence/phrase-first chunks suitable for one short visual beat."""
    # Use ASCII punctuation deliberately.  Some older saved sessions contain
    # UTF-8 punctuation that was serialized with a legacy encoding, while
    # periods/commas remain reliable across Vietnamese, Korean and English.
    clean_sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text or "") if part.strip()]
    clean_result: list[str] = []
    for sentence in clean_sentences or [str(text or "").strip()]:
        clauses = [part.strip() for part in re.split(r"(?<=[,;:])\s+", sentence) if part.strip()]
        for clause in clauses or [sentence]:
            words = clause.split()
            while len(words) > target_words:
                pauses = [i + 1 for i, word in enumerate(words[:target_words]) if word.rstrip().endswith((",", ";", ":"))]
                cut = pauses[-1] if pauses else target_words
                clean_result.append(" ".join(words[:cut]).strip())
                words = words[cut:]
            value = " ".join(words).strip()
            if len(value.split()) < 4 and clean_result:
                clean_result[-1] = f"{clean_result[-1]} {value}".strip()
            elif value:
                clean_result.append(value)
    return [part for part in clean_result if part]

    # Legacy fallback retained below for source compatibility.
    sentences = [part.strip() for part in re.split(r"(?<=[.!?â€¦ã€‚ï¼ï¼Ÿ])\s+|(?<=[ã€‚ï¼ï¼Ÿ])", text or "") if part.strip()]
    result: list[str] = []
    for sentence in sentences or [text.strip()]:
        clauses = [part.strip() for part in re.split(r"(?<=[,;:â€”â€“])\s+", sentence) if part.strip()]
        pending = ""
        for clause in clauses or [sentence]:
            words = clause.split()
            if pending:
                words = (pending + " " + clause).split()
                pending = ""
            while len(words) > target_words:
                pauses = [index + 1 for index, word in enumerate(words[:target_words]) if word.rstrip().endswith((",", ";", ":"))]
                cut = pauses[-1] if pauses else target_words
                result.append(" ".join(words[:cut]).strip())
                words = words[cut:]
            value = " ".join(words).strip()
            if len(value.split()) < 4 and result:
                result[-1] = f"{result[-1]} {value}".strip()
            elif value:
                result.append(value)
    return [part for part in result if part]


def _atomic_narration_units(units: list[dict]) -> list[dict]:
    atomic: list[dict] = []
    for unit in units:
        source_id = str(unit.get("id") or "n").strip()
        pieces = _split_atomic_narration_text(str(unit.get("text") or ""))
        for part_index, text in enumerate(pieces, start=1):
            atomic.append({
                "id": source_id if len(pieces) == 1 else f"{source_id}_p{part_index:02d}",
                "source_unit_id": source_id,
                "text": text,
                "order": len(atomic) + 1,
            })
    return atomic


def _semantic_narration_candidates(units: list[dict]) -> list[dict]:
    """Offer natural narration boundaries without turning each one into a shot.

    The Visual Planner may group these candidates.  Unlike the retired atomic
    path, there is deliberately no word-count split: a long uninterrupted idea
    remains intact until the LLM finds a real semantic boundary.
    """
    candidates: list[dict] = []
    for unit in units:
        source_id = str(unit.get("id") or "n").strip()
        text = str(unit.get("text") or "").strip()
        # Preserve a sentence as one candidate.  Commas are not a mandatory
        # visual cut; they are only considered when a sentence is genuinely
        # very long and has a natural explanatory pause.
        sentences = [part.strip() for part in re.split(r"(?<=[.!?…])\s+|(?<=[。！？])", text) if part.strip()]
        pieces: list[str] = []
        for sentence in sentences or [text]:
            word_count = len(re.findall(r"[^\W_]+", sentence, flags=re.UNICODE))
            if word_count > 42 and re.search(r"[,;:]\s+", sentence):
                clauses = [part.strip() for part in re.split(r"(?<=[,;:])\s+", sentence) if part.strip()]
                pieces.extend(clauses or [sentence])
            else:
                pieces.append(sentence)
        for piece_index, piece in enumerate(pieces, start=1):
            candidates.append({
                "id": source_id if len(pieces) == 1 else f"{source_id}_s{piece_index:02d}",
                "source_unit_id": source_id,
                "text": piece,
                "order": len(candidates) + 1,
            })
    return candidates


def _split_text_into_count(text: str, count: int) -> list[str]:
    if count <= 1:
        return [text.strip()]
    words = str(text or "").split()
    if len(words) <= 1:
        return [text.strip()] * count
    chunks, start = [], 0
    for index in range(1, count + 1):
        remaining_chunks = count - index + 1
        remaining_words = len(words) - start
        target = max(1, round(remaining_words / remaining_chunks))
        if index == count:
            end = len(words)
        else:
            limit = min(len(words) - (remaining_chunks - 1), start + target)
            pause_candidates = [pos for pos in range(start + 1, limit + 1) if words[pos - 1].rstrip().endswith((",", ";", ":"))]
            end = pause_candidates[-1] if pause_candidates else limit
        chunks.append(" ".join(words[start:end]).strip())
        start = end
    return [chunk for chunk in chunks if chunk]


def _enforce_timed_unit_cap(units: list[dict], max_sec: float = _STICK_HARD_BEAT_MAX_SEC) -> list[dict]:
    """Split a single long narration unit before the LLM can group it."""
    result: list[dict] = []
    for unit in units:
        start, end = float(unit["start_sec"]), float(unit["end_sec"])
        duration = max(0.04, end - start)
        count = max(1, math.ceil(duration / max_sec))
        pieces = _split_text_into_count(unit["text"], count)
        weights = [max(1, len(re.findall(r"[^\W_]+", part, flags=re.UNICODE))) for part in pieces]
        total = sum(weights) or 1
        cursor = start
        for index, (part, weight) in enumerate(zip(pieces, weights), start=1):
            piece_end = end if index == len(pieces) else min(end, cursor + duration * weight / total)
            result.append({
                **unit,
                "id": unit["id"] if len(pieces) == 1 else f"{unit['id']}_t{index:02d}",
                "source_unit_id": unit.get("source_unit_id") or unit["id"],
                "text": part,
                "start_sec": round(cursor, 3),
                "end_sec": round(piece_end, 3),
                "order": len(result) + 1,
            })
            cursor = piece_end
    return result


def _timed_narration_units(units: list[dict], cues: list[dict], total_duration: float) -> list[dict]:
    raw_scenes = [{"id": unit["id"], "narration": unit["text"]} for unit in units]
    timed = _stick_timed_scenes(raw_scenes, total_duration, cues)
    return [{**unit, "start_sec": timed[index]["start_sec"], "end_sec": timed[index]["end_sec"]} for index, unit in enumerate(units)]


_STICK_COLOR_DIRECTOR_SYSTEM = """You are the colour art director for one narrated 2D stick-figure explainer.
Return strict JSON only:
{"base_background":"...","outline":"...","primary":"...","accent":"...","highlight":"...","rationale":"..."}

Choose a distinctive, story-appropriate palette from the supplied title, narration and visual style. Do not use a generic house palette and do not default to blue. Let era, setting, topic and emotional arc drive your choice. The palette must remain coherent for this one video while still allowing different beats to emphasize different roles. Give concise visual colour descriptions, not hex codes, brands, watermarks or readable image text."""


def _color_director_value(value: object, fallback: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:180] if text else fallback


def _ai_stick_project_color_bible(title: str, style: str, blueprint: dict, *, has_style_sheet: bool) -> dict:
    """Ask the configured stick LLM once; prompts then preserve its creative direction."""
    if has_style_sheet:
        return _stick_project_color_bible(has_style_sheet=True)
    fallback = _stick_project_color_bible()
    narration = str(blueprint.get("tts_text") or blueprint.get("source_script") or "").strip()[:5000]
    try:
        data = _stick_pipeline_json(
            _STICK_COLOR_DIRECTOR_SYSTEM,
            json.dumps({"story_title": title, "visual_style": style, "narration": narration}, ensure_ascii=False),
            max_tokens=900,
        )
        return {
            "palette_source": "ai_story_art_direction",
            "base_background": _color_director_value(data.get("base_background"), fallback["base_background"]),
            "outline": _color_director_value(data.get("outline"), fallback["outline"]),
            "primary": _color_director_value(data.get("primary"), fallback["primary"]),
            "accent": _color_director_value(data.get("accent"), fallback["accent"]),
            "highlight": _color_director_value(data.get("highlight"), fallback["highlight"]),
            "rationale": _color_director_value(data.get("rationale"), "AI art direction unavailable; image model chooses the story palette."),
        }
    except Exception:
        return {**fallback, "palette_source": "ai_art_direction_fallback", "rationale": "The colour director was unavailable; let the image model derive a coherent palette from the story."}


_VISUAL_PLANNER_SYSTEM = """You are the Visual Beat Planner for a narrated 2D stick-figure explainer.
Return strict JSON only, with this exact envelope:
{"beats":[{"unit_ids":["n_0001"],"visual_anchor_unit_id":"n_0001","visual_anchor":"the opening narration idea this frame may show immediately","visual_intent":"specific visible action/evidence to show","visual_type":"scene|map|diagram|timeline|comparison|statistic","camera":"...","composition":"...","emotion":"...","color_usage":"adaptive_focus|primary_focus|accent_focus|highlight_focus","on_screen_text":"optional short image label","overlay_language":"target_locale|source_term|mixed","overlay_reason":"required only for source_term or mixed","reference_needs":["existing_or_new_snake_case_asset_id"],"hold_reason":"required only above 10 seconds"}]}

The supplied units are candidate narration boundaries, NOT compulsory images. Decide final images from story meaning and audio timing, never from a word-count rule. Group consecutive units when they are one connected visual story: same subject, action, location and evidence. Split only at an independent change of subject, action, discovery, place, time, map/chart, or emotion. A strong story beat has one oversized focal subject and a clear visual question, not a literal illustration of every clause. Normal beats are 5-10 seconds; 3-5 seconds are reserved for an intentional hook, reveal, number or punchline. A beat may last up to 12 seconds only when one strong image continues to explain the same idea; give its hold_reason.
Every narration unit must appear exactly once and in order. A beat's visual_anchor_unit_id MUST be its first unit, and visual_anchor must only describe what the viewer has heard at the start of that beat. Never show a later action, outcome, twist, or evidence early just because it appears later in the beat. Split at a meaningful candidate boundary when that would happen. No beat may exceed 12 seconds. Adjacent beats must have clearly different visual intents; never repeat variants of a broad source scene. Reuse an id from reference_catalog when it fits; otherwise propose a stable snake_case id in reference_needs only for a reusable asset, never a one-off shot.
Use the supplied AI art-directed project_color_bible as the continuity foundation. Select color_usage freely per beat, especially adaptive_focus when the story emotion should decide. Do not default to one house colour. Keep the frame lively with one large focal subject, visible action/evidence, contrast, and controlled saturation. For on_screen_text, default to the supplied target_locale. Leave it empty unless text materially improves the shot. Use source_term only for an unchanged proper name, official name, standard international term, original quotation, or explicitly required original-language label; never invent an English slogan for a non-English target locale.
"""

_VISUAL_CRITIC_SYSTEM = """You are the Visual Plan Critic for a narrated 2D stick-figure explainer.
Return strict JSON only in the form {"beats":[...]}. Return a complete corrected beats list using the same beat fields provided.
Ensure each narration unit appears exactly once in order. Candidates are not mandatory cuts: merge connected clauses into one story beat; split only independent visual changes. Reject literal, generic, repeated, or mechanically sliced beats. Keep normal timing near 5-10 seconds, allow 3-5 seconds only for deliberate impact, and never allow more than 12 seconds. Each visual_anchor_unit_id must equal the first unit_id, and visual_anchor may only show information spoken at the beginning of the beat; repair any early reveal by splitting at a semantic boundary. Preserve the supplied AI art-directed project palette, but allow adaptive_focus, primary_focus, accent_focus or highlight_focus according to the beat; never force one default house colour. Preserve target_locale and reject unneeded or incorrectly localized on_screen_text; proper names and official terms may remain in their original language only with source_term/mixed and a reason.
"""

_REFERENCE_PLANNER_SYSTEM = """You are the Reference Planner for a narrated 2D stick-figure explainer.
Return strict JSON only in the form {"references":[{"id":"stable_snake_case_id","type":"style|character|location|prop|map|diagram","name":"...","description":"..."}]}.
Select reusable references only for recurring characters, locations, props, maps, diagrams, or style. Do not create a reference for a one-off scene. For every requested_reference_id that is genuinely reusable, return that exact id so visual beats can link to it. Keep any applicable continuity catalog ids unchanged.
"""


def _visual_planner_payload(
    units: list[dict],
    style: str,
    title: str,
    project_color_bible: dict | None = None,
    output_locale: str = "vi-VN",
) -> str:
    return json.dumps({
        "story_title": title,
        "visual_style": style,
        "target_locale": _stick_output_locale(output_locale),
        "project_color_bible": project_color_bible or _stick_project_color_bible(),
        "timing_policy": {
            "normal_seconds": "5-10",
            "impact_seconds": "3-5 only for intentional impact",
            "maximum_seconds": _STICK_HARD_BEAT_MAX_SEC,
            "target_seconds": _STICK_TARGET_BEAT_SEC,
            "candidate_boundaries": "semantic choices, never compulsory image cuts",
        },
        "units": units,
    }, ensure_ascii=False)


def _normalize_visual_beats(
    raw_beats: list,
    units: list[dict],
    project_color_bible: dict | None = None,
    output_locale: str = "vi-VN",
) -> list[dict]:
    by_id = {unit["id"]: unit for unit in units}
    expected = [unit["id"] for unit in units]
    result, used = [], []
    for index, raw in enumerate(raw_beats if isinstance(raw_beats, list) else [], start=1):
        if not isinstance(raw, dict):
            continue
        ids = [str(item) for item in (raw.get("unit_ids") or []) if str(item) in by_id]
        if not ids:
            continue
        used.extend(ids)
        selected = [by_id[item] for item in ids]
        anchor_id = str(raw.get("visual_anchor_unit_id") or ids[0]).strip()
        if anchor_id != ids[0]:
            raise ValueError("Visual Plan phải neo visual_anchor vào narration unit đầu của beat")
        # Anchor text itself is deterministic.  A model may choose the visual
        # treatment, but it may never move the first visible idea to a later
        # clause and spoil narration that has not been spoken yet.
        visual_anchor = str(selected[0]["text"]).strip()
        color_usage = _normalize_stick_color_usage(raw.get("color_usage") or raw.get("color_direction"))
        result.append({
            "id": f"vb_{index:03d}", "unit_ids": ids,
            "text": " ".join(unit["text"] for unit in selected),
            "start_sec": selected[0]["start_sec"], "end_sec": selected[-1]["end_sec"],
            "visual_anchor_unit_id": anchor_id,
            "visual_anchor": visual_anchor,
            "visual_intent": str(raw.get("visual_intent") or raw.get("visual_goal") or "").strip(),
            "visual_type": str(raw.get("visual_type") or "scene").strip(),
            "camera": str(raw.get("camera") or "clear explanatory medium-wide view").strip(),
            "composition": str(raw.get("composition") or "clean editorial infographic hierarchy").strip(),
            "emotion": str(raw.get("emotion") or "curious and factual").strip(),
            "color_usage": color_usage,
            "color_direction": _stick_color_usage_instruction(project_color_bible or _stick_project_color_bible(), color_usage),
            "on_screen_text": str(raw.get("on_screen_text") or "").strip(),
            "overlay_language": _normalize_overlay_language(raw.get("overlay_language")),
            "overlay_reason": str(raw.get("overlay_reason") or "")[:240],
            "output_locale": _stick_output_locale(output_locale),
            "reference_needs": [str(item) for item in (raw.get("reference_needs") or []) if str(item).strip()],
            "hold_reason": str(raw.get("hold_reason") or "").strip(),
        })
    if used != expected:
        raise ValueError("Visual Plan không phủ chính xác toàn bộ narration units theo đúng thứ tự")
    for beat in result:
        beat["duration_sec"] = round(max(0.04, beat["end_sec"] - beat["start_sec"]), 3)
        if not beat["visual_intent"]:
            raise ValueError("Visual Plan có beat thiếu visual_intent")
    return result


def _semantic_visual_plan_fallback(
    units: list[dict],
    project_color_bible: dict | None = None,
    output_locale: str = "vi-VN",
) -> list[dict]:
    """Build a valid, readable plan if an LLM omits or reorders unit ids.

    This is deliberately a recovery path, not the normal visual director.  It
    keeps existing semantic narration candidates together around the target
    hold time, so one malformed JSON response cannot turn into an HTTP 500 or
    regress to the old word-count/per-sentence image explosion.
    """
    if not units:
        return []

    groups: list[list[dict]] = []
    current: list[dict] = []
    for unit in units:
        if not current:
            current = [unit]
            continue
        start = float(current[0]["start_sec"])
        current_end = float(current[-1]["end_sec"])
        next_end = float(unit["end_sec"])
        current_duration = current_end - start
        expanded_duration = next_end - start
        # Close on a semantic candidate once the current story beat has a
        # useful hold.  Never enlarge it beyond the hard 12-second ceiling.
        if (
            expanded_duration > _STICK_HARD_BEAT_MAX_SEC + 0.001
            or (
                current_duration >= _STICK_TARGET_BEAT_MIN_SEC
                and abs(current_duration - _STICK_TARGET_BEAT_SEC)
                <= abs(expanded_duration - _STICK_TARGET_BEAT_SEC)
            )
        ):
            groups.append(current)
            current = [unit]
        else:
            current.append(unit)
    if current:
        groups.append(current)

    # A short final thought is usually the close of the preceding idea, not a
    # reason for a flash-frame.  Merge only where the 12-second policy holds.
    if len(groups) > 1:
        final_duration = float(groups[-1][-1]["end_sec"]) - float(groups[-1][0]["start_sec"])
        merged_duration = float(groups[-1][-1]["end_sec"]) - float(groups[-2][0]["start_sec"])
        if final_duration < _STICK_TARGET_BEAT_MIN_SEC and merged_duration <= _STICK_HARD_BEAT_MAX_SEC + 0.001:
            groups[-2].extend(groups[-1])
            groups.pop()

    raw_beats = []
    for group in groups:
        narration = " ".join(str(item.get("text") or "").strip() for item in group).strip()
        anchor = str(group[0].get("text") or "").strip()
        raw_beats.append({
            "unit_ids": [str(item["id"]) for item in group],
            "visual_anchor_unit_id": str(group[0]["id"]),
            "visual_intent": f"Show one clear, lively visual moment for this narration idea: {anchor}"[:900],
            "visual_type": "scene",
            "camera": "clear medium-wide explanatory view",
            "composition": "one large focal subject with visible action and evidence",
            "emotion": "curious and engaging",
            "color_usage": "adaptive_focus",
            "on_screen_text": "",
            "overlay_language": "target_locale",
            "reference_needs": [],
            "hold_reason": "semantic recovery grouping" if len(group) > 1 else "",
        })
    return _normalize_visual_beats(raw_beats, units, project_color_bible, output_locale)


def _visual_density_limit(total_duration: float) -> int:
    """Hard upper bound only: semantic planning remains free to use fewer shots."""
    return max(3, math.ceil(max(1.0, total_duration) / 6.4))


def _split_overlong_semantic_beats(beats: list[dict], units: list[dict], project_color_bible: dict | None = None) -> tuple[list[dict], int]:
    """Last-resort duration guard that cuts only at existing semantic candidates.

    This is intentionally never a word/count splitter. It only runs when the
    planner ignored the approved 12-second ceiling and preserves candidate
    ordering, anchors and continuity metadata.
    """
    by_id = {str(unit.get("id")): unit for unit in units}
    result: list[dict] = []
    split_count = 0
    for beat in beats:
        selected = [by_id[item] for item in beat.get("unit_ids") or [] if item in by_id]
        if not selected:
            continue
        chunks: list[list[dict]] = []
        chunk: list[dict] = []
        for unit in selected:
            if chunk and float(unit["end_sec"]) - float(chunk[0]["start_sec"]) > _STICK_HARD_BEAT_MAX_SEC + 0.001:
                chunks.append(chunk)
                chunk = []
            chunk.append(unit)
        if chunk:
            chunks.append(chunk)
        if len(chunks) > 1:
            split_count += len(chunks) - 1
        for chunk_index, items in enumerate(chunks, start=1):
            first = items[0]
            text = " ".join(str(item.get("text") or "").strip() for item in items).strip()
            is_first = chunk_index == 1
            visual = str(beat.get("visual_intent") or "").strip()
            if not is_first:
                visual = f"Illustrate this next connected narration idea, without revealing later text: {text}"[:900]
            usage = _normalize_stick_color_usage(beat.get("color_usage") or beat.get("color_direction"))
            result.append({
                **beat,
                "id": f"vb_{len(result) + 1:03d}",
                "unit_ids": [str(item["id"]) for item in items],
                "text": text,
                "start_sec": round(float(first["start_sec"]), 3),
                "end_sec": round(float(items[-1]["end_sec"]), 3),
                "duration_sec": round(max(0.04, float(items[-1]["end_sec"]) - float(first["start_sec"])), 3),
                "visual_anchor_unit_id": str(first["id"]),
                "visual_anchor": str(first.get("text") or "").strip(),
                "visual_intent": visual,
                "on_screen_text": str(beat.get("on_screen_text") or "") if is_first else "",
                "overlay_language": _normalize_overlay_language(beat.get("overlay_language")) if is_first else "target_locale",
                "overlay_reason": str(beat.get("overlay_reason") or "")[:240] if is_first else "",
                "color_usage": usage,
                "color_direction": _stick_color_usage_instruction(project_color_bible or _stick_project_color_bible(), usage),
                "hold_reason": str(beat.get("hold_reason") or "") if is_first else "semantic duration guard",
            })
    return result, split_count


def _validate_semantic_visual_beats(beats: list[dict], units: list[dict], total_duration: float) -> list[str]:
    """Return repairable errors; never mechanically split a visual story beat."""
    expected = [str(unit.get("id")) for unit in units]
    actual = [item for beat in beats for item in beat.get("unit_ids") or []]
    errors: list[str] = []
    if actual != expected:
        errors.append("Every semantic narration candidate must be covered exactly once in order.")
    for beat in beats:
        if str(beat.get("visual_anchor_unit_id") or "") != str((beat.get("unit_ids") or [""])[0]):
            errors.append(f"{beat.get('id')}: visual_anchor must be the first narration candidate.")
        if not str(beat.get("visual_anchor") or "").strip():
            errors.append(f"{beat.get('id')}: visual_anchor is required.")
        if float(beat.get("duration_sec") or 0) > _STICK_HARD_BEAT_MAX_SEC + 0.01:
            errors.append(f"{beat.get('id')}: beat exceeds {_STICK_HARD_BEAT_MAX_SEC:.0f} seconds; split only at a semantic boundary.")
    limit = _visual_density_limit(total_duration)
    if len(beats) > limit:
        errors.append(f"Plan is too dense ({len(beats)} beats). Keep at most {limit} for this audio by merging connected ideas.")
    return errors


def _reference_specs_for_plan(
    beats: list[dict],
    blueprint: dict,
    style: str,
    title: str,
    project_color_bible: dict | None = None,
    output_locale: str = "vi-VN",
) -> list[dict]:
    requested_ids = list(dict.fromkeys(
        ref_id
        for beat in beats
        for ref_id in beat["reference_needs"]
        if re.fullmatch(r"[a-z][a-z0-9_]{1,63}", ref_id)
    ))
    user = json.dumps({
        "story_title": title, "visual_style": style, "target_locale": _stick_output_locale(output_locale),
        "project_color_bible": project_color_bible or _stick_project_color_bible(),
        "beats": [{"id": beat["id"], "visual_intent": beat["visual_intent"], "reference_needs": beat["reference_needs"]} for beat in beats],
        "continuity_hints": blueprint.get("references") or [],
        "requested_reference_ids": requested_ids,
    }, ensure_ascii=False)
    try:
        data = _stick_pipeline_json(_REFERENCE_PLANNER_SYSTEM, user, max_tokens=5000)
        raw_refs = data.get("references") if isinstance(data.get("references"), list) else []
    except Exception:
        raw_refs = blueprint.get("references") if isinstance(blueprint.get("references"), list) else []
    refs = []
    seen = set()
    for index, raw in enumerate(raw_refs, start=1):
        if not isinstance(raw, dict):
            continue
        ref_type = _normalize_reference_type(str(raw.get("type") or "prop"))
        ref_id = _safe_reference_id(str(raw.get("id") or ""), f"{_reference_prefix(ref_type)}_{index:03d}")
        if ref_id in seen:
            continue
        seen.add(ref_id)
        name = str(raw.get("name") or ref_id).strip()
        description = str(raw.get("description") or "").strip()
        prompt = _build_reference_prompt(
            ref_type, name, description,
            {"visual_style": style, "project_color_bible": project_color_bible or _stick_project_color_bible()},
            {"story_title": title, "visual_style": style, "output_locale": _stick_output_locale(output_locale)},
        )
        refs.append(_stick_reference_record(ref_id, ref_type, name, description, prompt, source="visual_plan"))
    # Preserve the content-filter's continuity hints even when the dedicated
    # reference planner had a transient failure or omitted a requested asset.
    known_ids = {ref["id"] for ref in refs}
    for index, raw in enumerate(blueprint.get("references") or [], start=1):
        if not isinstance(raw, dict):
            continue
        ref_type = _normalize_reference_type(str(raw.get("type") or "prop"))
        ref_id = _safe_reference_id(str(raw.get("id") or ""), f"{_reference_prefix(ref_type)}_{index:03d}")
        if ref_id in known_ids:
            continue
        name = str(raw.get("name") or ref_id).strip()
        description = str(raw.get("description") or "").strip()
        prompt = _build_reference_prompt(
            ref_type, name, description,
            {"visual_style": style, "project_color_bible": project_color_bible or _stick_project_color_bible()},
            {"story_title": title, "visual_style": style, "output_locale": _stick_output_locale(output_locale)},
        )
        refs.append(_stick_reference_record(ref_id, ref_type, name, description, prompt, source="content_blueprint"))
        known_ids.add(ref_id)
    return refs


_LEGACY_VISUAL_PLANNER_SYSTEM = """You are the Visual Beat Planner for a narrated 2D stick-figure explainer.
Return strict JSON only in this form:
{"beats":[{"unit_ids":["n_0001"],"visual_intent":"specific visible action/evidence to show","visual_type":"scene|map|diagram|timeline|comparison|statistic","camera":"...","composition":"...","emotion":"...","on_screen_text":"optional short text in narration language","reference_needs":["existing_or_new_snake_case_asset_id"],"hold_reason":"optional"}]}

You decide the final number of images from narration meaning and real timing, never from a word-count rule. Group consecutive narration units only when they genuinely share one visual idea. Split when subject, action, evidence, map/chart, statistic, location, camera need, or emotional beat changes. Normal beats should be roughly 3-7 seconds. Longer holds require hold_reason.
Every narration unit must appear exactly once and in order. Adjacent beats must have clearly different visual intents; never repeat variants of a broad source scene. Reuse an id from reference_catalog when it fits; otherwise propose a stable snake_case id in reference_needs only for a reusable asset, never a one-off shot.
The request may contain an optional visual_plan_instruction. Follow it only for grouping or splitting beats, visual type, camera, composition, emotion, and pacing. It must never change narration wording, narration order, unit timing, visual style, reference specifications, or final-image prompting.
"""

_LEGACY_VISUAL_CRITIC_SYSTEM = """You are the Visual Plan Critic for a narrated 2D stick-figure explainer.
Return strict JSON only in the form {"beats":[...]}. Return a complete corrected beats list using the same beat fields provided.
Ensure each narration unit appears exactly once in order, adjacent beats have distinct visual intents, and beats are meaningful visual ideas rather than mechanical word slices. Keep normal beat timing near 3-7 seconds unless hold_reason explains an intentional hold.
The request may contain an optional visual_plan_instruction. Respect it only for grouping or splitting beats, visual type, camera, composition, emotion, and pacing. Do not change narration wording, narration order, unit timing, visual style, reference specifications, or final-image prompting.
"""


def _legacy_visual_bible(storyboard: dict, style: str, title: str) -> dict:
    raw_refs = storyboard.get("references") if isinstance(storyboard.get("references"), list) else []
    characters, locations, props = [], [], []
    for raw in raw_refs:
        if not isinstance(raw, dict):
            continue
        item = {"id": str(raw.get("id") or ""), "name": str(raw.get("name") or ""), "description": str(raw.get("description") or "")}
        ref_type = _normalize_reference_type(str(raw.get("type") or "misc"))
        if ref_type == "character":
            characters.append(item)
        elif ref_type == "location":
            locations.append(item)
        elif ref_type == "prop":
            props.append(item)
    return {
        "logline": f"Stick-figure explainer: {title or 'Untitled'}", "visual_style": style,
        "palette": ["warm paper", "charcoal outline", "muted earth brown", "ice blue accent"],
        "continuity_rules": [
            "Match the uploaded style sheet whenever present.",
            "Keep every recurring stick figure, prop, color cue, and location silhouette consistent.",
            "Use direct infographic composition and only the explicit text requested by the storyboard.",
        ],
        "characters": characters, "locations": locations, "props": props,
    }


def _legacy_normalize_visual_beats(raw_beats: list, units: list[dict]) -> list[dict]:
    by_id = {unit["id"]: unit for unit in units}
    expected = [unit["id"] for unit in units]
    result, used = [], []
    for index, raw in enumerate(raw_beats if isinstance(raw_beats, list) else [], start=1):
        if not isinstance(raw, dict):
            continue
        ids = [str(item) for item in (raw.get("unit_ids") or []) if str(item) in by_id]
        if not ids:
            continue
        used.extend(ids)
        selected = [by_id[item] for item in ids]
        result.append({
            "id": f"vb_{index:03d}", "unit_ids": ids,
            "text": " ".join(unit["text"] for unit in selected),
            "start_sec": selected[0]["start_sec"], "end_sec": selected[-1]["end_sec"],
            "visual_intent": str(raw.get("visual_intent") or raw.get("visual_goal") or "").strip(),
            "visual_type": str(raw.get("visual_type") or "scene").strip(),
            "camera": str(raw.get("camera") or "clear explanatory medium-wide view").strip(),
            "composition": str(raw.get("composition") or "clean editorial infographic hierarchy").strip(),
            "emotion": str(raw.get("emotion") or "curious and factual").strip(),
            "on_screen_text": str(raw.get("on_screen_text") or "").strip(),
            "reference_needs": [str(item) for item in (raw.get("reference_needs") or []) if str(item).strip()],
            "hold_reason": str(raw.get("hold_reason") or "").strip(),
        })
    if used != expected:
        raise ValueError("Visual Plan không phủ chính xác toàn bộ narration units theo đúng thứ tự")
    for beat in result:
        beat["duration_sec"] = round(max(0.04, beat["end_sec"] - beat["start_sec"]), 3)
        if not beat["visual_intent"]:
            raise ValueError("Visual Plan có beat thiếu visual_intent")
    return result


def _legacy_scene_prompt(scene: dict, style: str, references: list[dict]) -> str:
    ref_ids = set(scene.get("reference_ids") or [])
    ref_lines = [f"{ref.get('id')}: {ref.get('name')} — {ref.get('description')}" for ref in references if ref.get("id") in ref_ids]
    overlay = str(scene.get("on_screen_text") or "").strip()
    text_instruction = (
        f'Include this exact, legible on-screen text in the narration language: "{overlay}". Use clean intentional editorial lettering; it is content, never a watermark.'
        if overlay else "Do not add any extra readable text, subtitle, logo, or watermark."
    )
    return (
        "Create one 16:9 frame for a narrated 2D stick-figure explainer video.\n"
        f"Style: {style}\nNarration meaning to illustrate (highest priority): {scene.get('text') or ''}\n"
        f"Unique visual objective: {scene.get('visual') or scene.get('text') or ''}\n"
        f"Camera: {scene.get('camera') or 'clear explanatory medium-wide view'}\n"
        f"Composition: {scene.get('composition') or 'clean editorial infographic hierarchy'}\n"
        f"Emotion: {scene.get('emotion') or 'curious and factual'}\n"
        f"Continuity references: {'; '.join(ref_lines) if ref_lines else 'follow the visual bible'}\n{text_instruction}\n"
        "This is one unique shot in a sequence. Depict the exact narration idea above, not a generic montage or a variant of the broader source scene. "
        "Make the subject, action, evidence, camera, or composition visibly distinct from adjacent narration beats. "
        "Use only the style and continuity references to preserve recurring characters, location, era, and props. No watermark, no brand logo, no gore, no distorted anatomy."
    )


def _legacy_reference_specs_for_plan(beats: list[dict], blueprint: dict, style: str, title: str) -> list[dict]:
    requested_ids = list(dict.fromkeys(ref_id for beat in beats for ref_id in beat["reference_needs"] if re.fullmatch(r"[a-z][a-z0-9_]{1,63}", ref_id)))
    user = json.dumps({"story_title": title, "visual_style": style, "beats": [{"id": beat["id"], "visual_intent": beat["visual_intent"], "reference_needs": beat["reference_needs"]} for beat in beats], "continuity_hints": blueprint.get("references") or [], "requested_reference_ids": requested_ids}, ensure_ascii=False)
    try:
        data = _stick_pipeline_json(_REFERENCE_PLANNER_SYSTEM, user, max_tokens=5000)
        raw_refs = data.get("references") if isinstance(data.get("references"), list) else []
    except Exception:
        raw_refs = blueprint.get("references") if isinstance(blueprint.get("references"), list) else []
    refs, seen = [], set()
    for index, raw in enumerate(raw_refs, start=1):
        if not isinstance(raw, dict):
            continue
        ref_type = _normalize_reference_type(str(raw.get("type") or "prop"))
        ref_id = _safe_reference_id(str(raw.get("id") or ""), f"{_reference_prefix(ref_type)}_{index:03d}")
        if ref_id in seen:
            continue
        seen.add(ref_id)
        name, description = str(raw.get("name") or ref_id).strip(), str(raw.get("description") or "").strip()
        prompt = _build_reference_prompt(ref_type, name, description, {"visual_style": style}, {"story_title": title, "visual_style": style})
        refs.append(_stick_reference_record(ref_id, ref_type, name, description, prompt, source="visual_plan"))
    known_ids = {ref["id"] for ref in refs}
    for index, raw in enumerate(blueprint.get("references") or [], start=1):
        if not isinstance(raw, dict):
            continue
        ref_type = _normalize_reference_type(str(raw.get("type") or "prop"))
        ref_id = _safe_reference_id(str(raw.get("id") or ""), f"{_reference_prefix(ref_type)}_{index:03d}")
        if ref_id in known_ids:
            continue
        name, description = str(raw.get("name") or ref_id).strip(), str(raw.get("description") or "").strip()
        prompt = _build_reference_prompt(ref_type, name, description, {"visual_style": style}, {"story_title": title, "visual_style": style})
        refs.append(_stick_reference_record(ref_id, ref_type, name, description, prompt, source="content_blueprint"))
        known_ids.add(ref_id)
    return refs


def _create_legacy_stick_figure_visual_plan(project: str, body: StickFigureVisualPlanRequest):
    project_dir, cfg = _require_project(project), load_config()
    blueprint = body.content_blueprint if isinstance(body.content_blueprint, dict) else {}
    units = _content_narration_units(blueprint)
    if not units:
        raise HTTPException(400, "Content blueprint không có narration units")
    total_duration = _audio_duration(project_dir, body.audio_file, cfg.get("ffprobe_path", "ffprobe"))
    cues, timing_source = _load_cues(project_dir, body.audio_file, str(blueprint.get("tts_text") or ""), total_duration)
    timed_units = _timed_narration_units(units, cues, total_duration)
    style = (body.visual_style or blueprint.get("visual_style") or _STICK_FIGURE_DEFAULT_STYLE).strip()
    visual_plan_instruction = str(body.visual_plan_instruction or "").strip()[:2000]
    payload = {"story_title": body.story_title, "visual_style": style, "units": timed_units, "reference_catalog": blueprint.get("references") or [], "visual_plan_instruction": visual_plan_instruction}
    planner = _stick_pipeline_json(_LEGACY_VISUAL_PLANNER_SYSTEM, json.dumps(payload, ensure_ascii=False), max_tokens=9000)
    planned_beats = _legacy_normalize_visual_beats(planner.get("beats"), timed_units)
    critic_status = "passed"
    try:
        critic = _stick_pipeline_json(_LEGACY_VISUAL_CRITIC_SYSTEM, json.dumps({"units": timed_units, "beats": planned_beats, "visual_plan_instruction": visual_plan_instruction}, ensure_ascii=False), max_tokens=9000)
        beats = _legacy_normalize_visual_beats(critic.get("beats"), timed_units)
    except Exception as exc:
        beats, critic_status = planned_beats, f"fallback: {str(exc)[:180]}"
    references = _legacy_reference_specs_for_plan(beats, blueprint, style, body.story_title)
    source_meta_path = _stick_style_meta_path(project)
    if source_meta_path.exists():
        try:
            source_meta = json.loads(source_meta_path.read_text(encoding="utf-8"))
            filename = str(source_meta.get("filename") or "")
            if filename and _SAFE_REF_IMAGE.match(filename) and (_reference_dir(project, "style") / filename).exists():
                references.insert(0, _stick_reference_record("style_source", "style", "Uploaded style sheet", "User supplied visual style sheet.", "", image_file=filename, source="uploaded"))
        except (OSError, json.JSONDecodeError):
            pass
    visual_bible = _legacy_visual_bible({"references": references}, style, body.story_title)
    available_refs, scenes = {ref["id"] for ref in references}, []
    for index, beat in enumerate(beats, start=1):
        ref_ids = [ref_id for ref_id in beat["reference_needs"] if ref_id in available_refs]
        if "style_source" in available_refs:
            ref_ids.append("style_source")
        scene = {"id": beat["id"], "index": index, "start_sec": beat["start_sec"], "end_sec": beat["end_sec"], "duration_sec": beat["duration_sec"], "text": beat["text"], "source_unit_ids": beat["unit_ids"], "visual": beat["visual_intent"], "visual_type": beat["visual_type"], "camera": beat["camera"], "composition": beat["composition"], "emotion": beat["emotion"], "on_screen_text": beat["on_screen_text"], "reference_ids": list(dict.fromkeys(ref_ids)), "transition": "cut", "status": "pending", "task_id": "", "image_file": "", "error": "", "storyboard": {"scene_id": beat["id"], "visual_moment": beat["visual_intent"], "reference_ids": ref_ids}}
        scene["director_prompt"] = _legacy_scene_prompt(scene, style, references)
        scene["prompt"] = scene["director_prompt"]
        scene["active_prompt_mode"] = scene["prompt_mode"] = "director"
        scenes.append(scene)
    storyboard_sheets = _stick_storyboard_sheets(scenes, style, body.story_title)
    references.extend(storyboard_sheets)
    plan = {"schema_version": 2, "project": project, "mode": "stick_figure", "approval_status": "draft", "audio_file": body.audio_file, "story_title": body.story_title, "visual_style": style, "visual_plan_instruction": visual_plan_instruction, "output_locale": _stick_output_locale(body.output_locale), "asset_scope": _new_plan_asset_scope(), "visual_bible": visual_bible, "references": references, "scenes": scenes, "content_blueprint": blueprint, "narration_units": timed_units, "coverage": {"total_units": len(timed_units), "covered_units": len({item for beat in beats for item in beat["unit_ids"]}), "critic_status": critic_status}, "storyboard_sheets": [sheet["id"] for sheet in storyboard_sheets], "timing_source": timing_source, "audio_duration_sec": round(total_duration, 3), "settings": {"density": "balanced_3_7_sec", "auto_prepare_references": body.auto_prepare_references, "visual_plan_instruction": visual_plan_instruction}, "created_at": _now_iso(), "visual_plan_created_at": _now_iso()}
    _assign_stick_plan_asset_scope(plan, reset_generated_assets=False)
    return _write_plan(project, plan)


@router.post("/{project}/stick-figure/visual-plan")
def create_stick_figure_visual_plan(project: str, body: StickFigureVisualPlanRequest):
    return _create_legacy_stick_figure_visual_plan(project, body)
    project_dir = _require_project(project)
    cfg = load_config()
    blueprint = body.content_blueprint if isinstance(body.content_blueprint, dict) else {}
    units = _semantic_narration_candidates(_content_narration_units(blueprint))
    if not units:
        raise HTTPException(400, "Content blueprint không có narration units")
    total_duration = _audio_duration(project_dir, body.audio_file, cfg.get("ffprobe_path", "ffprobe"))
    cues, timing_source = _load_cues(project_dir, body.audio_file, str(blueprint.get("tts_text") or ""), total_duration)
    timed_units = _timed_narration_units(units, cues, total_duration)
    style = (body.visual_style or blueprint.get("visual_style") or _STICK_FIGURE_DEFAULT_STYLE).strip()
    output_locale = _stick_output_locale(body.output_locale)
    source_meta_path = _stick_style_meta_path(project)
    has_style_sheet = False
    if source_meta_path.exists():
        try:
            source_meta = json.loads(source_meta_path.read_text(encoding="utf-8"))
            filename = str(source_meta.get("filename") or "")
            has_style_sheet = bool(filename and _SAFE_REF_IMAGE.match(filename) and (_reference_dir(project, "style") / filename).exists())
        except (OSError, json.JSONDecodeError):
            pass
    project_color_bible = _ai_stick_project_color_bible(body.story_title, style, blueprint, has_style_sheet=has_style_sheet)
    planner_payload = json.loads(_visual_planner_payload(timed_units, style, body.story_title, project_color_bible, output_locale))
    planner_payload["reference_catalog"] = blueprint.get("references") or []
    planner_recovery_note = ""
    try:
        planner = _stick_pipeline_json(_VISUAL_PLANNER_SYSTEM, json.dumps(planner_payload, ensure_ascii=False), max_tokens=9000)
        planned_beats = _normalize_visual_beats(planner.get("beats"), timed_units, project_color_bible, output_locale)
    except Exception as planner_error:
        # Models occasionally omit an id, repeat one, or return an incomplete
        # JSON beat list.  Ask the critic once for a complete ordered repair;
        # if that also fails, retain a semantic deterministic recovery plan.
        try:
            repaired_planner = _stick_pipeline_json(
                _VISUAL_CRITIC_SYSTEM,
                json.dumps({
                    "units": timed_units,
                    "beats": planner.get("beats") if isinstance(locals().get("planner"), dict) else [],
                    "project_color_bible": project_color_bible,
                    "target_locale": output_locale,
                    "instruction": "The planner output was invalid. Return a complete corrected ordered beat list that covers every narration unit exactly once.",
                }, ensure_ascii=False),
                max_tokens=9000,
            )
            planned_beats = _normalize_visual_beats(repaired_planner.get("beats"), timed_units, project_color_bible, output_locale)
            planner_recovery_note = "planner_invalid_repaired_by_critic"
        except Exception as recovery_error:
            planned_beats = _semantic_visual_plan_fallback(timed_units, project_color_bible, output_locale)
            planner_recovery_note = f"planner_invalid_semantic_recovery: {str(planner_error)[:110]}; repair: {str(recovery_error)[:110]}"
    critic_status = "passed" if not planner_recovery_note else planner_recovery_note
    try:
        critic = _stick_pipeline_json(_VISUAL_CRITIC_SYSTEM, json.dumps({"units": timed_units, "beats": planned_beats, "project_color_bible": project_color_bible, "target_locale": output_locale}, ensure_ascii=False), max_tokens=9000)
        beats = _normalize_visual_beats(critic.get("beats"), timed_units, project_color_bible, output_locale)
    except Exception as exc:
        beats = planned_beats
        critic_status = f"fallback: {str(exc)[:180]}"
    beats, semantic_guard_splits = _split_overlong_semantic_beats(beats, timed_units, project_color_bible)
    validation_errors = _validate_semantic_visual_beats(beats, timed_units, total_duration)
    semantic_warnings: list[str] = []
    if validation_errors:
        try:
            repair = _stick_pipeline_json(
                _VISUAL_CRITIC_SYSTEM,
                json.dumps({
                    "units": timed_units,
                    "beats": beats,
                    "validation_errors": validation_errors,
                    "project_color_bible": project_color_bible,
                    "target_locale": output_locale,
                    "instruction": "Repair this plan semantically. Do not mechanically slice text or invent a generic shot.",
                }, ensure_ascii=False),
                max_tokens=9000,
            )
            repaired_beats = _normalize_visual_beats(repair.get("beats"), timed_units, project_color_bible, output_locale)
            repaired_errors = _validate_semantic_visual_beats(repaired_beats, timed_units, total_duration)
            if not repaired_errors:
                beats = repaired_beats
                validation_errors = []
                critic_status = f"{critic_status}; repaired_semantic_validation"
            else:
                semantic_warnings = validation_errors + ["Semantic repair was incomplete; preserved the complete original plan."]
                critic_status = f"{critic_status}; repair_incomplete_preserved_original"
        except Exception as exc:
            semantic_warnings = validation_errors + [f"Semantic repair failed; preserved the complete original plan: {str(exc)[:160]}"]
            critic_status = f"{critic_status}; repair_failed_preserved_original"
    beats, overlay_qa_status = _qa_stick_overlay_text(beats, output_locale)
    references = _reference_specs_for_plan(beats, blueprint, style, body.story_title, project_color_bible, output_locale)
    if has_style_sheet:
        try:
            source_meta = json.loads(source_meta_path.read_text(encoding="utf-8"))
            filename = str(source_meta.get("filename") or "")
            if filename and _SAFE_REF_IMAGE.match(filename) and (_reference_dir(project, "style") / filename).exists():
                references.insert(0, _stick_reference_record("style_source", "style", "Uploaded style sheet", "User supplied visual style sheet.", "", image_file=filename, source="uploaded"))
        except (OSError, json.JSONDecodeError):
            pass
    visual_bible = _stick_figure_visual_bible({"references": references}, style, body.story_title, project_color_bible)
    available_refs = {ref["id"] for ref in references}
    scenes = []
    for index, beat in enumerate(beats, start=1):
        ref_ids = [ref_id for ref_id in beat["reference_needs"] if ref_id in available_refs]
        if "style_source" in available_refs:
            ref_ids.append("style_source")
        scene = {
            "id": beat["id"], "index": index, "start_sec": beat["start_sec"], "end_sec": beat["end_sec"], "duration_sec": beat["duration_sec"],
            "text": beat["text"], "source_unit_ids": beat["unit_ids"], "visual": beat["visual_intent"], "visual_type": beat["visual_type"],
            "visual_anchor_unit_id": beat["visual_anchor_unit_id"], "visual_anchor": beat["visual_anchor"], "hold_reason": beat.get("hold_reason", ""),
            "camera": beat["camera"], "composition": beat["composition"], "emotion": beat["emotion"], "on_screen_text": beat["on_screen_text"],
            "overlay_language": beat["overlay_language"], "overlay_reason": beat["overlay_reason"], "overlay_qa": beat.get("overlay_qa", "skipped_no_overlay"), "output_locale": output_locale,
            "color_usage": beat["color_usage"], "color_direction": beat["color_direction"],
            "reference_ids": list(dict.fromkeys(ref_ids)), "transition": "cut", "status": "pending", "task_id": "", "image_file": "", "error": "",
            "storyboard": {"scene_id": beat["id"], "visual_moment": beat["visual_intent"], "reference_ids": ref_ids},
        }
        scene["director_prompt"] = _stick_scene_prompt(scene, style, references, project_color_bible)
        scene["prompt"] = scene["director_prompt"]
        scene["active_prompt_mode"] = "director"
        scene["prompt_mode"] = "director"
        scenes.append(scene)
    storyboard_sheets = _stick_storyboard_sheets(scenes, style, body.story_title, project_color_bible)
    references.extend(storyboard_sheets)
    coverage = {
        "total_units": len(timed_units),
        "covered_units": len({item for beat in beats for item in beat["unit_ids"]}),
        "critic_status": critic_status,
        "overlay_qa_status": overlay_qa_status,
        "max_scene_sec": _STICK_HARD_BEAT_MAX_SEC,
        "target_scene_sec": _STICK_TARGET_BEAT_SEC,
        "max_visual_beats": _visual_density_limit(total_duration),
        "semantic_warnings": semantic_warnings,
        "semantic_guard_splits": semantic_guard_splits,
    }
    plan = {
        "schema_version": 2, "project": project, "mode": "stick_figure", "approval_status": "draft", "audio_file": body.audio_file,
        "story_title": body.story_title, "visual_style": style, "output_locale": output_locale, "asset_scope": _new_plan_asset_scope(), "project_color_bible": project_color_bible, "visual_bible": visual_bible, "references": references, "scenes": scenes,
        "content_blueprint": blueprint, "narration_units": timed_units, "coverage": coverage,
        "storyboard_sheets": [sheet["id"] for sheet in storyboard_sheets], "timing_source": timing_source,
        "audio_duration_sec": round(total_duration, 3), "settings": {"density": "semantic_first_target_8_sec_3_12_sec", "target_scene_sec": _STICK_TARGET_BEAT_SEC, "max_scene_sec": _STICK_HARD_BEAT_MAX_SEC, "auto_prepare_references": body.auto_prepare_references},
        "created_at": _now_iso(), "visual_plan_created_at": _now_iso(),
    }
    _assign_stick_plan_asset_scope(plan, reset_generated_assets=False)
    return _write_plan(project, plan)


@router.post("/{project}/stick-figure/visual-plan/approve")
def approve_stick_figure_visual_plan(project: str, body: StickFigureVisualPlanApproveRequest):
    plan = _read_plan(project)
    if int(plan.get("schema_version") or 0) != 2 or plan.get("mode") != "stick_figure":
        raise HTTPException(409, "Visual Plan V2 chưa sẵn sàng")
    # Legacy V2 plans did not have a namespace. Reading one can recover files
    # named vb_001/style_001 from a prior revision, so clear those links once.
    _assign_stick_plan_asset_scope(plan, reset_generated_assets=not bool(_asset_scope(plan.get("asset_scope"))))
    plan["approval_status"] = "approved"
    plan["approved_at"] = _now_iso()
    plan.setdefault("settings", {})["auto_prepare_references"] = bool(body.auto_prepare_references)
    return _write_plan(project, plan)


@router.get("/{project}")
def get_scene_plan(project: str):
    plan = _read_plan(project)
    if plan.get("mode") != "stick_figure":
        return plan
    if int(plan.get("schema_version") or 0) == 2:
        return plan
    raise HTTPException(410, "Visual Plan Người que V1 đã được ngừng. Hãy tạo session Người que mới bằng Visual Plan V2.")


@router.get("/{project}/image/{filename}")
def serve_scene_image(project: str, filename: str):
    if not _SAFE_IMAGE.match(filename):
        raise HTTPException(400, "Invalid image filename")
    path = _scene_dir(project) / "images" / filename
    if not path.exists():
        raise HTTPException(404, "Scene image not found")
    return FileResponse(str(path))


@router.get("/{project}/director/reference/{filename}")
def serve_director_reference(project: str, filename: str):
    if not _SAFE_REF_IMAGE.match(filename):
        raise HTTPException(400, "Invalid reference filename")
    root = _scene_dir(project) / "references"
    for path in root.rglob(filename):
        if path.exists() and path.stat().st_size > 0:
            return FileResponse(str(path))
    raise HTTPException(404, "Reference image not found")


@router.post("/{project}/safe-prompts")
def apply_safe_prompts(project: str, body: SceneSafePromptRequest):
    plan = _read_plan(project)
    wanted = set(body.scene_ids or [])
    mode = (body.mode or "rule").strip().lower()
    if mode not in {"rule", "llm"}:
        raise HTTPException(400, "Safe prompt mode must be rule or llm")
    cfg = load_config()
    applied = 0
    skipped = 0
    max_level = 0
    for scene in plan.get("scenes", []):
        if wanted and scene.get("id") not in wanted:
            continue
        has_valid_image = _scene_has_valid_image(project, scene)
        is_failed = scene.get("status") == "failed" or bool(scene.get("error"))
        if body.only_failed and (not is_failed or has_valid_image):
            skipped += 1
            continue
        if not scene.get("original_prompt"):
            scene["original_prompt"] = scene.get("prompt", "")
        if mode == "llm":
            try:
                current_level = int(scene.get("safe_llm_level") or 0)
            except (TypeError, ValueError):
                current_level = 0
            safe_level = max(1, min(5, current_level + 1))
            scene["safe_llm_level"] = safe_level
            try:
                scene["safe_llm_prompt"] = _build_safe_llm_scene_prompt(scene, plan, safe_level, cfg)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(400, str(exc)) from exc
            if (cfg.get("safe_prompt_provider") or "default").strip().lower() == "custom":
                scene["safe_llm_model"] = (cfg.get("safe_prompt_model") or "gpt-4o-mini").strip()
            else:
                scene["safe_llm_model"] = (cfg.get("safe_prompt_model") or cfg.get("default_story_model") or "").strip()
            scene["active_prompt_mode"] = "safe_llm"
            scene["prompt_mode"] = "safe_llm"
            task_status = f"safe_llm_lv{safe_level}_ready"
        else:
            try:
                current_level = int(scene.get("safe_rule_level") or scene.get("safe_level") or 0)
            except (TypeError, ValueError):
                current_level = 0
            safe_level = max(1, min(5, current_level + 1))
            safe_rule_prompt = _build_safe_scene_prompt(scene, plan, safe_level)
            scene["safe_rule_level"] = safe_level
            scene["safe_rule_prompt"] = safe_rule_prompt
            scene["safe_prompt"] = safe_rule_prompt
            scene["safe_level"] = safe_level
            scene["active_prompt_mode"] = "safe_rule"
            scene["prompt_mode"] = "safe_rule"
            task_status = f"safe_rule_lv{safe_level}_ready"
        scene["safe_prompt_updated_at"] = _now_iso()
        scene["task_status"] = task_status
        scene["progress"] = 0
        applied += 1
        max_level = max(max_level, safe_level)
    plan["last_safe_prompt_batch"] = {
        "applied": applied,
        "skipped": skipped,
        "max_level": max_level,
        "mode": mode,
        "updated_at": _now_iso(),
    }
    return _write_plan(project, plan)


@router.patch("/{project}/{scene_id}")
def update_scene(project: str, scene_id: str, body: ScenePatchRequest):
    plan = _read_plan(project)
    scene = next((item for item in plan.get("scenes", []) if item.get("id") == scene_id), None)
    if not scene:
        raise HTTPException(404, "Scene not found")
    generation_reset = False
    safe_fields = {"safe_prompt", "safe_rule_prompt", "safe_llm_prompt", "director_prompt", "active_prompt_mode"}
    safe_only_update = any(getattr(body, key) is not None for key in safe_fields) and all(
        getattr(body, key) is None for key in ("text", "prompt", "start_sec", "end_sec")
    )
    reveal_only_update = body.reveal_override is not None and all(
        getattr(body, key) is None for key in (
            "text", "prompt", "director_prompt", "safe_prompt", "safe_rule_prompt",
            "safe_llm_prompt", "active_prompt_mode", "start_sec", "end_sec",
        )
    )
    for key in ("text", "prompt", "director_prompt", "safe_prompt", "safe_rule_prompt", "safe_llm_prompt", "active_prompt_mode", "start_sec", "end_sec"):
        value = getattr(body, key)
        if value is not None:
            scene[key] = value
            if key == "prompt":
                scene["original_prompt"] = value
                scene["safe_prompt"] = ""
                scene["safe_level"] = 0
                scene["safe_rule_prompt"] = ""
                scene["safe_rule_level"] = 0
                scene["safe_llm_prompt"] = ""
                scene["safe_llm_level"] = 0
                scene["active_prompt_mode"] = "original"
                scene["prompt_mode"] = "original"
                generation_reset = True
            elif key == "safe_prompt":
                scene["safe_rule_prompt"] = value
                scene["safe_rule_level"] = max(1, int(scene.get("safe_rule_level") or scene.get("safe_level") or 1))
                scene["safe_level"] = scene["safe_rule_level"]
                scene["active_prompt_mode"] = "safe_rule"
                scene["prompt_mode"] = "safe_rule"
                scene["safe_prompt_updated_at"] = _now_iso()
            elif key == "safe_rule_prompt":
                scene["safe_prompt"] = value
                scene["safe_rule_level"] = max(1, int(scene.get("safe_rule_level") or scene.get("safe_level") or 1))
                scene["safe_level"] = scene["safe_rule_level"]
                scene["active_prompt_mode"] = "safe_rule"
                scene["prompt_mode"] = "safe_rule"
                scene["safe_prompt_updated_at"] = _now_iso()
            elif key == "safe_llm_prompt":
                scene["safe_llm_level"] = max(1, int(scene.get("safe_llm_level") or 1))
                scene["active_prompt_mode"] = "safe_llm"
                scene["prompt_mode"] = "safe_llm"
                scene["safe_prompt_updated_at"] = _now_iso()
            elif key == "director_prompt":
                scene["active_prompt_mode"] = "director"
                scene["prompt_mode"] = "director"
                scene["director_prompt_updated_at"] = _now_iso()
            elif key == "active_prompt_mode":
                active = str(value or "").strip().lower()
                if active not in {"original", "safe_rule", "safe_llm", "director"}:
                    raise HTTPException(400, "Invalid active prompt mode")
                scene["active_prompt_mode"] = active
                scene["prompt_mode"] = active
            elif key in {"text", "start_sec", "end_sec"}:
                generation_reset = True
    if body.start_sec is not None or body.end_sec is not None:
        scene["duration_sec"] = round(max(0.01, float(scene["end_sec"]) - float(scene["start_sec"])), 3)
    if body.reveal_override is not None:
        try:
            scene["reveal_override"] = normalize_scene_reveal_override(
                body.reveal_override.model_dump(exclude_none=True)
            )
        except RevealConfigError as exc:
            raise HTTPException(400, str(exc)) from exc
    if generation_reset or (not safe_only_update and not reveal_only_update):
        scene["status"] = "pending"
        scene["error"] = ""
    return _write_plan(project, plan)


@router.post("/{project}/{scene_id}/select-image")
def select_scene_image(project: str, scene_id: str, body: SceneSelectImageRequest):
    plan = _read_plan(project)
    scene = next((item for item in plan.get("scenes", []) if item.get("id") == scene_id), None)
    if not scene:
        raise HTTPException(404, "Scene not found")
    _select_scene_image(project, scene, body.image_file)
    return _write_plan(project, plan)


@router.post("/{project}/director/references/{reference_id}/select")
def select_director_reference(project: str, reference_id: str, body: DirectorReferenceSelectRequest):
    plan = _read_plan(project)
    ref = _find_reference(plan, reference_id)
    if not ref:
        raise HTTPException(404, "Reference not found")
    if not _reference_file_valid(project, ref, body.image_file):
        raise HTTPException(404, "Reference image not found")
    _add_reference_history(project, ref, body.image_file, selected=True, meta={"source": "selected"})
    return _write_plan(project, plan)


@router.post("/{project}/director/scenes/{scene_id}/regenerate-prompt")
def regenerate_director_scene_prompt(project: str, scene_id: str, body: DirectorRegeneratePromptRequest):
    plan = _read_plan(project)
    scene = next((item for item in plan.get("scenes", []) if item.get("id") == scene_id), None)
    if not scene:
        raise HTTPException(404, "Scene not found")
    visual_bible = plan.get("visual_bible") if isinstance(plan.get("visual_bible"), dict) else _fallback_visual_bible(plan, scene.get("text", ""))
    references = plan.get("references") if isinstance(plan.get("references"), list) else []
    user = f"""Regenerate storyboard metadata and the final image prompt for one scene.
Return JSON:
{{
  "scene_id": "{scene_id}",
  "visual_moment": "...",
  "characters": ["..."],
  "location": "...",
  "camera": "...",
  "composition": "...",
  "emotion": "...",
  "continuity_notes": "...",
  "subtitle_safe_area": "...",
  "reference_ids": ["..."],
  "prompt": "final image prompt"
}}
Use creative visual direction, preserve story continuity, and keep it suitable for a 16:9 narrated video frame.
No readable text, no subtitles, no watermark. User note: {body.note or "(none)"}

Story title: {plan.get('story_title') or 'Untitled story'}
Visual bible:
{json.dumps(visual_bible, ensure_ascii=False)}
Available references:
{_reference_summary(references)}
Scene:
{json.dumps(_scene_batch_for_director([scene])[0], ensure_ascii=False)}
"""
    try:
        data = _director_json(user, max_tokens=4500)
        storyboard = _normalize_storyboard_item(data, scene, visual_bible, references, plan)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    scene["storyboard"] = storyboard
    scene["director_prompt"] = storyboard.get("prompt") or _build_director_prompt(scene, storyboard, visual_bible, references, plan)
    scene["reference_ids"] = storyboard.get("reference_ids") or []
    scene["active_prompt_mode"] = "director"
    scene["prompt_mode"] = "director"
    scene["status"] = "pending" if not _scene_has_valid_image(project, scene) else scene.get("status", "succeeded")
    scene["error"] = ""
    scene["director_prompt_updated_at"] = _now_iso()
    return _write_plan(project, plan)


def _generate_director_reference_worker(
    project: str,
    plan: dict,
    ref: dict,
    index: int,
    total: int,
    cfg: dict,
    plan_lock: threading.Lock,
    completed: dict,
    event_q: queue.Queue,
) -> None:
    """Generate one reference and emit isolated events safe for concurrent workers."""
    def emit(event: dict) -> None:
        event_q.put(event)

    try:
        with plan_lock:
            ref["status"] = "running"
            ref["error"] = ""
            ref["progress"] = 0
            ref["started_at"] = _now_iso()
            _write_plan(project, plan)
            # The uploaded stick-figure style sheet is an actual image input,
            # not merely words in a prompt.  Feed it to every generated
            # reference so character turns, props and storyboard frames inherit
            # the supplied line work and palette.
            style_reference_images = [] if ref.get("id") == "style_source" else _scene_reference_images(
                project, {"reference_ids": ["style_source"]}, plan,
            )
        emit({"type": "reference_start", "reference_id": ref.get("id"), "index": index, "total": total})
        filename = _reference_filename(ref)
        target = _reference_path(project, ref, filename)
        for image_event in generate_image_events(
            ref.get("prompt", ""), target, cfg,
            reference_images=style_reference_images,
            cancel_check=lambda: project in _cancelled,
        ):
            if image_event.get("type") == "provider":
                emit({
                    "type": "reference_provider",
                    "reference_id": ref.get("id"),
                    "provider": image_event.get("provider"),
                    "model": image_event.get("model"),
                    "size": image_event.get("size"),
                })
            elif image_event.get("type") == "task":
                with plan_lock:
                    ref["task_id"] = image_event.get("task_id", "")
                    ref["task_status"] = "running"
                    _write_plan(project, plan)
                emit({"type": "reference_task", "reference_id": ref.get("id"), "task_id": ref.get("task_id", "")})
            elif image_event.get("type") == "progress":
                with plan_lock:
                    ref["progress"] = _coerce_progress(image_event.get("progress"), ref.get("progress", 0))
                    ref["task_status"] = image_event.get("status", "") or "running"
                    _write_plan(project, plan)
                    progress = ref["progress"]
                    status = ref.get("task_status", "running")
                emit({
                    "type": "reference_progress",
                    "reference_id": ref.get("id"),
                    "progress": progress,
                    "status": status,
                    "index": index,
                    "total": total,
                })
            elif image_event.get("type") == "saved":
                with plan_lock:
                    ref["progress"] = 100
                    _write_plan(project, plan)
                emit({"type": "reference_saved", "reference_id": ref.get("id"), "size_bytes": image_event.get("size_bytes", 0)})
        with plan_lock:
            _add_reference_history(project, ref, filename, selected=not ref.get("image_file"), meta={
                "source": "generated",
                "prompt": ref.get("prompt", ""),
                "provider": cfg.get("provider", "gpt2api"),
                "model": cfg.get("image_model") or cfg.get("model", ""),
                "task_id": ref.get("task_id", ""),
            })
            ref["status"] = "succeeded"
            ref["progress"] = 100
            ref["finished_at"] = _now_iso()
            ref["last_candidate_image_file"] = filename
            completed["value"] += 1
            done = completed["value"]
            _write_plan(project, plan)
        emit({
            "type": "reference_done",
            "reference_id": ref.get("id"),
            "image_file": ref.get("image_file") or filename,
            "candidate_file": filename,
            "index": index,
            "total": total,
            "completed": done,
        })
    except Exception as exc:
        with plan_lock:
            ref["status"] = "failed"
            ref["error"] = str(exc)
            ref["progress"] = 0
            ref["finished_at"] = _now_iso()
            completed["value"] += 1
            done = completed["value"]
            _write_plan(project, plan)
        emit({"type": "reference_error", "reference_id": ref.get("id"), "message": str(exc), "index": index, "total": total, "completed": done})


def _director_reference_stream(project: str, body: DirectorReferenceGenerateRequest) -> Generator[str, None, None]:
    try:
        cfg = _scene_cfg()
        plan = _read_plan(project)
        if plan.get("mode") == "stick_figure" and int(plan.get("schema_version") or 0) == 2 and plan.get("approval_status") != "approved":
            yield _sse({"type": "error", "message": "Hãy duyệt Visual Plan trước khi tạo reference."})
            return
        wanted = set(body.reference_ids or [])
        targets = _reference_targets(plan, wanted, body.retry_failed)
        mode = "parallel" if body.mode == "parallel" else "sequential"
        # Preserve the original "parallel" behavior for API callers that do
        # not send a count: start every selected reference independently.
        concurrency = _coerce_concurrency(
            len(targets) if body.concurrency is None else body.concurrency,
            len(targets),
        ) if mode == "parallel" else 1
        yield _sse({
            "type": "start",
            "total": len(targets),
            "reference_ids": [r.get("id") for r in targets],
            "provider": cfg.get("provider", "gpt2api"),
            "model": cfg.get("image_model") or cfg.get("model", ""),
            "mode": mode,
            "concurrency": concurrency,
        })
        event_q: queue.Queue = queue.Queue()
        plan_lock = threading.Lock()
        completed = {"value": 0}
        work = [
            (ref, index)
            for index, ref in enumerate(targets, start=1)
        ]
        if mode == "parallel" and len(work) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [
                    pool.submit(_generate_director_reference_worker, project, plan, ref, index, len(work), cfg, plan_lock, completed, event_q)
                    for ref, index in work
                ]
                while any(not future.done() for future in futures):
                    try:
                        yield _sse(event_q.get(timeout=0.15))
                    except queue.Empty:
                        continue
                for future in futures:
                    future.result()
                while not event_q.empty():
                    yield _sse(event_q.get_nowait())
        else:
            # Keep live SSE progress in sequential mode as well; calling the worker
            # directly would buffer all events until one reference had finished.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                for ref, index in work:
                    future = pool.submit(
                        _generate_director_reference_worker,
                        project, plan, ref, index, len(work), cfg, plan_lock, completed, event_q,
                    )
                    while not future.done():
                        try:
                            yield _sse(event_q.get(timeout=0.15))
                        except queue.Empty:
                            continue
                    future.result()
                    while not event_q.empty():
                        yield _sse(event_q.get_nowait())
        yield _sse({"type": "done", "plan": plan})
    except Exception as exc:
        yield _sse({"type": "error", "message": str(exc)})
    yield "data: [DONE]\n\n"


@router.post("/{project}/director/references/generate")
def generate_director_references(project: str, body: DirectorReferenceGenerateRequest):
    return StreamingResponse(_director_reference_stream(project, body), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.post("/cancel")
def cancel_scene_generation(body: dict):
    project = str(body.get("project") or "")
    if not project:
        return {"ok": False, "cancelled_scenes": 0}

    _require_project(project)
    _cancelled.add(project)

    # Update the persisted plan immediately.  A gpt2api task can still be
    # waiting for its upstream stream when the user presses Stop; without
    # this, the card remains visually "running" until that upstream timeout
    # eventually expires.
    plan = _read_plan(project)
    cancelled_scenes = 0
    for scene in plan.get("scenes", []):
        if scene.get("status") != "running":
            continue
        cancelled_scenes += 1
        scene["status"] = "cancelled"
        scene["task_status"] = "cancelled"
        scene["progress"] = 0
        scene["error"] = ""
        scene["last_generation_error"] = "Đã dừng bởi người dùng"
        scene["finished_at"] = _now_iso()
    if cancelled_scenes:
        _write_plan(project, plan)

    return {"ok": True, "cancelled_scenes": cancelled_scenes}


def _generate_scene_worker(
    *,
    project: str,
    scene: dict,
    index: int,
    total: int,
    cfg: dict,
    plan: dict,
    plan_lock: threading.Lock,
    event_q: queue.Queue,
    completed_counter: dict,
) -> None:
    def emit(event: dict) -> None:
        event_q.put(event)

    def save_plan() -> None:
        _write_plan(project, plan)

    def completed() -> int:
        completed_counter["value"] += 1
        return int(completed_counter["value"])

    if project in _cancelled:
        emit({"type": "cancelled", "message": "Da dung tao anh scene"})
        return

    with plan_lock:
        _ensure_current_image_in_history(project, scene, selected=scene.get("status") == "succeeded")
        had_valid_image = _scene_has_valid_image(project, scene)
        scene["status"] = "running"
        scene["error"] = ""
        scene["progress"] = 0
        scene["task_status"] = "queued"
        scene["started_at"] = _now_iso()
        scene["finished_at"] = ""
        scene["attempt"] = 1
        save_plan()
    emit({"type": "scene_start", "scene_id": scene["id"], "index": index, "total": total, "attempt": 1})

    last_error = ""
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        if project in _cancelled:
            last_error = "Scene image generation cancelled"
            break

        with plan_lock:
            scene["status"] = "running"
            scene["task_status"] = "queued" if attempt == 1 else "retrying"
            scene["progress"] = 0
            scene["attempt"] = attempt
            scene["last_progress_at"] = _now_iso()
            prompt_text, prompt_mode, safe_level = _active_scene_prompt(scene)
            scene["prompt_mode"] = prompt_mode
            reference_images = _scene_reference_images(project, scene, plan)
            if attempt > 1:
                scene["last_generation_error"] = last_error
            save_plan()
        if attempt > 1:
            emit({"type": "scene_attempt", "scene_id": scene["id"], "attempt": attempt, "total_attempts": max_attempts})

        image_file = _new_scene_image_filename(scene)
        target = _scene_dir(project) / "images" / image_file
        try:
            for image_event in generate_image_events(
                prompt_text,
                target,
                cfg,
                reference_images=reference_images,
                cancel_check=lambda: project in _cancelled,
            ):
                if project in _cancelled:
                    raise RuntimeError("Scene image generation cancelled")
                if image_event.get("type") == "provider":
                    emit({
                        "type": "scene_provider",
                        "scene_id": scene["id"],
                        "provider": image_event.get("provider"),
                        "model": image_event.get("model"),
                        "size": image_event.get("size"),
                    })
                elif image_event.get("type") == "task":
                    with plan_lock:
                        scene["task_id"] = image_event.get("task_id", "")
                        scene["task_status"] = "running"
                        scene["last_progress_at"] = _now_iso()
                        save_plan()
                    emit({
                        "type": "scene_task",
                        "scene_id": scene["id"],
                        "task_id": image_event.get("task_id", ""),
                        "index": index,
                        "total": total,
                        "attempt": attempt,
                    })
                elif image_event.get("type") == "progress":
                    with plan_lock:
                        scene_progress = _coerce_progress(image_event.get("progress"), scene.get("progress", 0))
                        scene["progress"] = scene_progress
                        scene["task_status"] = image_event.get("status", "") or "running"
                        scene["last_progress_at"] = _now_iso()
                        save_plan()
                    emit({
                        "type": "scene_progress",
                        "scene_id": scene["id"],
                        "task_id": image_event.get("task_id", scene.get("task_id", "")),
                        "status": image_event.get("status", ""),
                        "progress": scene_progress,
                        "index": index,
                        "total": total,
                        "completed": int(completed_counter["value"]),
                        "attempt": attempt,
                    })
                elif image_event.get("type") == "saved":
                    with plan_lock:
                        scene["progress"] = 100
                        scene["last_progress_at"] = _now_iso()
                        save_plan()
                    emit({
                        "type": "scene_saved",
                        "scene_id": scene["id"],
                        "size_bytes": image_event.get("size_bytes", 0),
                        "index": index,
                        "total": total,
                        "attempt": attempt,
                    })

            select_new_image = not had_valid_image
            with plan_lock:
                _add_image_history(project, scene, image_file, selected=select_new_image, meta={
                    "source": "generated",
                    "prompt": prompt_text,
                    "prompt_mode": prompt_mode,
                    "safe_level": safe_level,
                    "provider": cfg.get("provider", "gpt2api"),
                    "model": cfg.get("image_model") or cfg.get("model", ""),
                    "task_id": scene.get("task_id", ""),
                    "attempt": attempt,
                    "reference_ids": scene.get("reference_ids") or [],
                    "reference_count": len(reference_images),
                })
                scene["status"] = "succeeded"
                if select_new_image:
                    scene["image_file"] = image_file
                scene["error"] = ""
                scene["last_generation_error"] = ""
                scene["last_candidate_image_file"] = image_file
                scene["progress"] = 100
                scene["task_status"] = "succeeded"
                scene["finished_at"] = _now_iso()
                done_count = completed()
                save_plan()
            emit({
                "type": "scene_done",
                "scene_id": scene["id"],
                "image_file": scene.get("image_file") or image_file,
                "candidate_file": image_file,
                "selected": select_new_image,
                "completed": done_count,
                "total": total,
                "attempt": attempt,
            })
            return
        except Exception as exc:
            last_error = str(exc)
            if project in _cancelled:
                break
            if attempt < max_attempts and not _is_fatal_image_error(last_error):
                delay = _retry_delay_for_image_error(last_error, attempt)
                with plan_lock:
                    scene["task_status"] = "retry_wait"
                    scene["last_generation_error"] = last_error
                    scene["retry_after_sec"] = round(delay, 1)
                    scene["progress"] = 0
                    save_plan()
                emit({
                    "type": "scene_retry",
                    "scene_id": scene["id"],
                    "message": last_error,
                    "attempt": attempt,
                    "next_attempt": attempt + 1,
                    "retry_after_sec": round(delay, 1),
                })
                _sleep_with_cancel(project, delay)
                continue
            break

    was_cancelled = project in _cancelled
    with plan_lock:
        has_selected_image = _scene_has_valid_image(project, scene)
        if was_cancelled:
            scene["status"] = "cancelled"
            scene["task_status"] = "cancelled"
            scene["last_generation_error"] = "Đã dừng bởi người dùng"
            scene["error"] = ""
            scene["progress"] = 0
        else:
            scene["status"] = "succeeded" if has_selected_image else "failed"
            scene["last_generation_error"] = last_error
            scene["error"] = "" if has_selected_image else last_error
            scene["progress"] = 100 if has_selected_image else 0
            scene["task_status"] = "failed"
        scene["finished_at"] = _now_iso()
        done_count = completed()
        save_plan()
    if was_cancelled:
        emit({"type": "scene_cancelled", "scene_id": scene["id"], "completed": done_count, "total": total})
    else:
        emit({"type": "scene_error", "scene_id": scene["id"], "message": last_error, "completed": done_count, "total": total})


def _generate_scene_stream(body: SceneGenerateRequest):
    _cancelled.discard(body.project)
    with _ACTIVE_SCENE_GENERATION_LOCK:
        _ACTIVE_SCENE_GENERATION_PROJECTS.add(body.project)
    try:
        cfg = _scene_cfg()
        plan = _read_plan(body.project)
        if plan.get("mode") == "stick_figure" and int(plan.get("schema_version") or 0) == 2 and plan.get("approval_status") != "approved":
            yield _sse({"type": "error", "message": "Hãy duyệt Visual Plan trước khi tạo ảnh."})
            return
        wanted = set(body.scene_ids or [])
        scenes = plan.get("scenes", [])
        targets = _generation_targets(body.project, scenes, wanted, body.retry_failed)
        concurrency = _coerce_concurrency(body.concurrency, len(targets))
        yield _sse({
            "type": "start",
            "total": len(targets),
            "scene_ids": [s.get("id") for s in targets],
            "concurrency": concurrency,
            "max_attempts": 3,
            "provider": cfg.get("provider", "gpt2api"),
            "base_url": cfg["base_url"],
            "model": cfg["image_model"],
        })

        if targets:
            # Persist the requested intent before workers start. The client
            # must not infer retry-vs-initial from a leftover image filename:
            # a failed/missing image can still have one.
            _mark_generation_kind(targets, body.retry_failed)
            _write_plan(body.project, plan)
            plan_lock = threading.Lock()
            event_q: queue.Queue = queue.Queue()
            completed_counter = {"value": 0}
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = {
                    executor.submit(
                        _generate_scene_worker,
                        project=body.project,
                        scene=scene,
                        index=idx,
                        total=len(targets),
                        cfg=cfg,
                        plan=plan,
                        plan_lock=plan_lock,
                        event_q=event_q,
                        completed_counter=completed_counter,
                    )
                    for idx, scene in enumerate(targets, start=1)
                }
                while futures:
                    try:
                        while True:
                            yield _sse(event_q.get_nowait())
                    except queue.Empty:
                        pass
                    done = {future for future in futures if future.done()}
                    for future in done:
                        futures.remove(future)
                        try:
                            future.result()
                        except Exception as exc:
                            yield _sse({"type": "worker_error", "message": str(exc)})
                    if futures:
                        try:
                            yield _sse(event_q.get(timeout=0.5))
                        except queue.Empty:
                            pass
                try:
                    while True:
                        yield _sse(event_q.get_nowait())
                except queue.Empty:
                    pass

        yield _sse({"type": "done", "plan": _read_plan(body.project)})
    except Exception as exc:
        yield _sse({"type": "error", "message": str(exc)})
    finally:
        _cancelled.discard(body.project)
        with _ACTIVE_SCENE_GENERATION_LOCK:
            _ACTIVE_SCENE_GENERATION_PROJECTS.discard(body.project)
        yield "data: [DONE]\n\n"


@router.post("/generate")
def generate_scene_images(body: SceneGenerateRequest):
    _require_project(body.project)
    return StreamingResponse(_generate_scene_stream(body), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def stream():
        _cancelled.discard(body.project)
        try:
            cfg = _scene_cfg()
            plan = _read_plan(body.project)
            wanted = set(body.scene_ids or [])
            scenes = plan.get("scenes", [])
            targets = _generation_targets(body.project, scenes, wanted, body.retry_failed)
            yield _sse({
                "type": "start",
                "total": len(targets),
                "scene_ids": [s.get("id") for s in targets],
                "provider": cfg.get("provider", "gpt2api"),
                "base_url": cfg["base_url"],
                "model": cfg["image_model"],
            })
            for idx, scene in enumerate(targets, start=1):
                if body.project in _cancelled:
                    yield _sse({"type": "cancelled", "message": "Đã dừng tạo ảnh scene"})
                    break
                _ensure_current_image_in_history(body.project, scene, selected=scene.get("status") == "succeeded")
                had_valid_image = _scene_has_valid_image(body.project, scene)
                scene["status"] = "running"
                scene["error"] = ""
                scene["progress"] = 0
                scene["task_status"] = "queued"
                scene["started_at"] = _now_iso()
                scene["finished_at"] = ""
                _write_plan(body.project, plan)
                yield _sse({"type": "scene_start", "scene_id": scene["id"], "index": idx, "total": len(targets)})
                try:
                    image_file = _new_scene_image_filename(scene)
                    target = _scene_dir(body.project) / "images" / image_file
                    for image_event in generate_image_events(
                        scene.get("prompt", ""),
                        target,
                        cfg,
                        cancel_check=lambda: body.project in _cancelled,
                    ):
                        if image_event.get("type") == "provider":
                            yield _sse({
                                "type": "scene_provider",
                                "scene_id": scene["id"],
                                "provider": image_event.get("provider"),
                                "model": image_event.get("model"),
                                "size": image_event.get("size"),
                            })
                        elif image_event.get("type") == "task":
                            scene["task_id"] = image_event.get("task_id", "")
                            scene["task_status"] = "running"
                            scene["last_progress_at"] = _now_iso()
                            _write_plan(body.project, plan)
                            yield _sse({
                                "type": "scene_task",
                                "scene_id": scene["id"],
                                "task_id": image_event.get("task_id", ""),
                                "index": idx,
                                "total": len(targets),
                            })
                        elif image_event.get("type") == "progress":
                            scene_progress = _coerce_progress(image_event.get("progress"), scene.get("progress", 0))
                            scene["progress"] = scene_progress
                            scene["task_status"] = image_event.get("status", "") or "running"
                            scene["last_progress_at"] = _now_iso()
                            _write_plan(body.project, plan)
                            yield _sse({
                                "type": "scene_progress",
                                "scene_id": scene["id"],
                                "task_id": image_event.get("task_id", scene.get("task_id", "")),
                                "status": image_event.get("status", ""),
                                "progress": scene_progress,
                                "index": idx,
                                "total": len(targets),
                            })
                        elif image_event.get("type") == "saved":
                            scene["progress"] = 100
                            scene["last_progress_at"] = _now_iso()
                            _write_plan(body.project, plan)
                            yield _sse({
                                "type": "scene_saved",
                                "scene_id": scene["id"],
                                "size_bytes": image_event.get("size_bytes", 0),
                                "index": idx,
                                "total": len(targets),
                            })
                    select_new_image = not had_valid_image
                    _add_image_history(body.project, scene, image_file, selected=select_new_image, meta={
                        "source": "generated",
                        "prompt": scene.get("prompt", ""),
                        "provider": cfg.get("provider", "gpt2api"),
                        "model": cfg.get("image_model") or cfg.get("model", ""),
                        "task_id": scene.get("task_id", ""),
                    })
                    scene["status"] = "succeeded"
                    if select_new_image:
                        scene["image_file"] = image_file
                    scene["error"] = ""
                    scene["last_generation_error"] = ""
                    scene["last_candidate_image_file"] = image_file
                    scene["progress"] = 100
                    scene["task_status"] = "succeeded"
                    scene["finished_at"] = _now_iso()
                    _write_plan(body.project, plan)
                    yield _sse({
                        "type": "scene_done",
                        "scene_id": scene["id"],
                        "image_file": scene.get("image_file") or image_file,
                        "candidate_file": image_file,
                        "selected": select_new_image,
                        "completed": idx,
                        "total": len(targets),
                    })
                except Exception as exc:
                    has_selected_image = _scene_has_valid_image(body.project, scene)
                    scene["status"] = "succeeded" if has_selected_image else "failed"
                    scene["last_generation_error"] = str(exc)
                    scene["error"] = "" if has_selected_image else str(exc)
                    scene["progress"] = 100 if has_selected_image else 0
                    scene["task_status"] = "failed"
                    scene["finished_at"] = _now_iso()
                    _write_plan(body.project, plan)
                    yield _sse({"type": "scene_error", "scene_id": scene["id"], "message": str(exc), "completed": idx, "total": len(targets)})
            yield _sse({"type": "done", "plan": _read_plan(body.project)})
        except Exception as exc:
            yield _sse({"type": "error", "message": str(exc)})
        finally:
            _cancelled.discard(body.project)
            yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/compose")
def compose_scene_video(body: SceneComposeRequest):
    project_dir = _require_project(body.project)
    cfg = load_config()
    ffmpeg_path = cfg.get("ffmpeg_path", "ffmpeg")
    ffprobe_path = cfg.get("ffprobe_path", "ffprobe")
    audio_path = project_dir / body.audio_file
    if not audio_path.exists():
        raise HTTPException(404, "Audio file not found")
    duration = get_video_duration(audio_path, ffprobe_path)
    if duration <= 0:
        raise HTTPException(400, "Cannot read audio duration")
    plan = _read_plan(body.project)
    if plan.get("mode") == "stick_figure" and _retime_stick_figure_plan(project_dir, plan, body.audio_file, duration):
        _write_plan(body.project, plan)
    images_dir = _scene_dir(body.project) / "images"
    scenes = [s for s in plan.get("scenes", []) if _scene_has_valid_image(body.project, s)]
    if not scenes:
        raise HTTPException(400, "No generated scene images found")
    missing = [s["id"] for s in scenes if not (images_dir / s["image_file"]).exists() or (images_dir / s["image_file"]).stat().st_size <= 0]
    if missing:
        raise HTTPException(404, f"Missing scene image: {missing[0]}")
    output_path = _unique_output_path(project_dir, body.output_name, body.overwrite_mode)
    try:
        global_reveal = normalize_scene_reveal(body.scene_reveal.model_dump())
        scene_reveals = [resolve_scene_reveal(global_reveal, scene) for scene in scenes]
    except RevealConfigError as exc:
        raise HTTPException(400, str(exc)) from exc

    def stream():
        renderer = _compose_scene_reveal_events if any(reveal_is_enabled(item) for item in scene_reveals) else _compose_scene_events
        renderer_args = {
            "scenes": scenes,
            "images_dir": images_dir,
            "audio_path": audio_path,
            "output_path": output_path,
            "audio_duration": duration,
            "ffmpeg_path": ffmpeg_path,
            "video_encoder": body.video_encoder,
            "quality": body.quality,
        }
        if renderer is _compose_scene_reveal_events:
            renderer_args["scene_reveals"] = scene_reveals
            renderer_args["project"] = body.project
        for event in renderer(**renderer_args):
            yield _sse(event)
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _scene_clip_command(
    *,
    ffmpeg_path: str,
    image_path: Path,
    clip_path: Path,
    scene_frames: int,
    config: dict,
    assets: SceneRevealAssets | None,
    video_encoder: str,
    quality: str,
) -> list[str]:
    """Build an encoder-compatible, silent CFR clip for one visual scene."""
    duration = scene_frames / REVEAL_FPS
    encoder_args, _ = _encoder_args(video_encoder, quality)
    base_filter = (
        f"fps={REVEAL_FPS},scale={REVEAL_WIDTH}:{REVEAL_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={REVEAL_WIDTH}:{REVEAL_HEIGHT},setsar=1,format=rgba"
    )
    cmd = [
        ffmpeg_path, "-y",
        "-loop", "1", "-framerate", str(REVEAL_FPS), "-i", str(image_path),
    ]
    if not assets:
        return [
            *cmd,
            "-t", f"{duration:.6f}",
            "-vf", f"{base_filter},format=yuv420p",
            "-r", str(REVEAL_FPS),
            *encoder_args,
            "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart",
            "-progress", "pipe:2", "-nostats", str(clip_path),
        ]

    animation_duration = assets.animation_frames / REVEAL_FPS
    settle_duration = assets.settle_frames / REVEAL_FPS
    hold_frames = scene_frames - assets.animation_frames - assets.settle_frames
    if hold_frames < 0:
        raise ValueError("Reveal timing exceeds the scene frame count")
    hold_duration = hold_frames / REVEAL_FPS
    paper = PAPER_COLOR.lstrip("#")
    if assets.effect == "brush":
        assert assets.mask_pattern is not None
        cmd.extend(["-framerate", str(assets.asset_fps), "-start_number", "1", "-i", str(assets.mask_pattern)])
        actor_index = None
        if assets.actor_pattern:
            actor_index = 2
            cmd.extend(["-framerate", str(assets.asset_fps), "-start_number", "1", "-i", str(assets.actor_pattern)])
        graph = [
            f"[0:v]{base_filter},split=3[drawimage][settleimage][holdimage]",
            f"color=c=0x{paper}:s={REVEAL_WIDTH}x{REVEAL_HEIGHT}:r={REVEAL_FPS}:d={animation_duration:.6f},format=rgba[paper]",
            f"[1:v]fps={REVEAL_FPS},scale={REVEAL_WIDTH}:{REVEAL_HEIGHT}:flags=bilinear,format=gray,trim=duration={animation_duration:.6f},setpts=PTS-STARTPTS[mask]",
            "[drawimage][mask]alphamerge[drawalpha]",
            "[paper][drawalpha]overlay=shortest=1:format=auto[revealbase]",
        ]
    else:
        assert assets.line_art_path is not None and assets.mask_pattern is not None and assets.color_mask_pattern is not None
        cmd.extend([
            "-loop", "1", "-framerate", str(REVEAL_FPS), "-i", str(assets.line_art_path),
            "-framerate", str(assets.asset_fps), "-start_number", "1", "-i", str(assets.mask_pattern),
            "-framerate", str(assets.asset_fps), "-start_number", "1", "-i", str(assets.color_mask_pattern),
        ])
        actor_index = None
        if assets.actor_pattern:
            actor_index = 4
            cmd.extend(["-framerate", str(assets.asset_fps), "-start_number", "1", "-i", str(assets.actor_pattern)])
        line_duration = assets.line_frames / REVEAL_FPS
        color_duration = max(1 / REVEAL_FPS, animation_duration - line_duration)
        graph = [
            f"[0:v]{base_filter},split=3[colorimage][settleimage][holdimage]",
            f"[1:v]{base_filter},split=2[lineimage][linebase]",
            f"color=c=0x{paper}:s={REVEAL_WIDTH}x{REVEAL_HEIGHT}:r={REVEAL_FPS}:d={line_duration:.6f},format=rgba[paperline]",
            f"[2:v]fps={REVEAL_FPS},scale={REVEAL_WIDTH}:{REVEAL_HEIGHT}:flags=bilinear,format=gray,trim=duration={line_duration:.6f},setpts=PTS-STARTPTS[linemask]",
            "[lineimage][linemask]alphamerge[linealpha]",
            "[paperline][linealpha]overlay=shortest=1:format=auto[lineclip]",
            f"[3:v]fps={REVEAL_FPS},scale={REVEAL_WIDTH}:{REVEAL_HEIGHT}:flags=bilinear,format=gray,trim=duration={color_duration:.6f},setpts=PTS-STARTPTS[colormask]",
            "[colorimage][colormask]alphamerge[coloralpha]",
            "[linebase][coloralpha]overlay=shortest=1:format=auto[colorclip]",
            "[lineclip][colorclip]concat=n=2:v=1:a=0[revealbase]",
        ]

    if actor_index is not None:
        graph.extend([
            f"[{actor_index}:v]fps={REVEAL_FPS},format=rgba,trim=duration={animation_duration:.6f},setpts=PTS-STARTPTS[actor]",
            "[revealbase][actor]overlay=shortest=1:format=auto[reveal]",
        ])
    else:
        graph.append("[revealbase]null[reveal]")
    graph.extend([
        f"[settleimage]trim=duration={settle_duration:.6f},setpts=PTS-STARTPTS[settle]",
        f"[holdimage]trim=duration={hold_duration:.6f},setpts=PTS-STARTPTS[hold]",
        "[reveal][settle][hold]concat=n=3:v=1:a=0,format=yuv420p[outv]",
    ])
    return [
        *cmd,
        "-filter_complex", ";".join(graph), "-map", "[outv]",
        "-t", f"{duration:.6f}", "-r", str(REVEAL_FPS),
        *encoder_args,
        "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart",
        "-progress", "pipe:2", "-nostats", str(clip_path),
    ]


def _render_scene_clip_events(
    *,
    ffmpeg_path: str,
    image_path: Path,
    clip_path: Path,
    scene_frames: int,
    config: dict,
    assets: SceneRevealAssets | None,
    video_encoder: str,
    quality: str,
    index: int,
    total: int,
) -> Generator[dict, None, tuple[int, str]]:
    duration = scene_frames / REVEAL_FPS
    cmd = _scene_clip_command(
        ffmpeg_path=ffmpeg_path,
        image_path=image_path,
        clip_path=clip_path,
        scene_frames=scene_frames,
        config=config,
        assets=assets,
        video_encoder=video_encoder,
        quality=quality,
    )
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    assert proc.stderr is not None
    tail: list[str] = []
    last_percent = -1
    for raw in proc.stderr:
        line = raw.strip()
        if not line:
            continue
        tail.append(line)
        tail = tail[-20:]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        elapsed = _parse_ffmpeg_time(key, value)
        if elapsed is None:
            continue
        local = min(1.0, max(0.0, elapsed / max(duration, 0.001)))
        percent = round(8 + ((index - 1 + local) / max(1, total)) * 80)
        if percent == last_percent:
            continue
        last_percent = percent
        yield {
            "type": "progress",
            "percent": percent,
            "elapsed_sec": elapsed,
            "total_sec": duration,
            "message": f"Đang render scene {index}/{total}...",
        }
    proc.wait()
    return proc.returncode or 0, " ".join(tail[-8:])


def _mux_scene_clips_events(
    *,
    concat_list: Path,
    audio_path: Path,
    output_path: Path,
    audio_duration: float,
    ffmpeg_path: str,
    video_encoder: str,
    quality: str,
) -> Generator[dict, None, tuple[int, str, str]]:
    _, encoder_label = _encoder_args(video_encoder, quality)
    cmd = [
        ffmpeg_path, "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-i", str(audio_path),
        "-t", f"{audio_duration:.6f}",
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
        "-progress", "pipe:2", "-nostats", str(output_path),
    ]
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    assert proc.stderr is not None
    tail: list[str] = []
    speed = ""
    for raw in proc.stderr:
        line = raw.strip()
        if not line:
            continue
        tail.append(line)
        tail = tail[-20:]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "speed":
            speed = value
            yield {"type": "speed", "speed": speed, "encoder_label": encoder_label}
        elapsed = _parse_ffmpeg_time(key, value)
        if elapsed is not None:
            yield _progress_event(
                elapsed, audio_duration, output_path, speed, video_encoder, encoder_label,
                message="Đang ghép các scene và audio...", percent_start=90, percent_span=9,
            )
    proc.wait()
    return proc.returncode or 0, " ".join(tail[-8:]), encoder_label


def _compose_scene_reveal_events(
    *,
    project: str,
    scenes: list[dict],
    scene_reveals: list[dict],
    images_dir: Path,
    audio_path: Path,
    output_path: Path,
    audio_duration: float,
    ffmpeg_path: str,
    video_encoder: str,
    quality: str,
) -> Generator[dict, None, None]:
    """Render reveal-enabled scenes into compatible clips, then mux audio once."""
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    job_dir = TEMP_DIR / f"scene_reveal_{uuid.uuid4().hex}"
    concat_list = job_dir / "scene_clips.txt"
    render_path = output_path.with_name(f".{output_path.stem}.render-{uuid.uuid4().hex}{output_path.suffix}")
    frame_counts = scene_frame_counts(scenes, audio_duration)
    attempts = [video_encoder] if video_encoder == "cpu_x264" else [video_encoder, "cpu_x264"]
    last_error = ""
    try:
        for attempt_index, encoder in enumerate(attempts, start=1):
            shutil.rmtree(job_dir, ignore_errors=True)
            clips_dir = job_dir / "clips"
            assets_dir = job_dir / "assets"
            clips_dir.mkdir(parents=True, exist_ok=True)
            assets_dir.mkdir(parents=True, exist_ok=True)
            render_path.unlink(missing_ok=True)
            _, encoder_label = _encoder_args(encoder, quality)
            if attempt_index > 1:
                yield {
                    "type": "step",
                    "step": "fallback",
                    "message": f"Render GPU lỗi, dựng lại toàn bộ scene bằng {encoder_label}...",
                    "encoder": encoder,
                    "encoder_label": encoder_label,
                }
            else:
                yield {
                    "type": "step",
                    "step": "prepare-reveal",
                    "message": f"Đang chuẩn bị hiệu ứng vẽ tay cho {len(scenes)} scene...",
                    "encoder": encoder,
                    "encoder_label": encoder_label,
                }
            clips: list[Path] = []
            failed = False
            for index, (scene, config, frames) in enumerate(zip(scenes, scene_reveals, frame_counts), start=1):
                image_path = (images_dir / scene["image_file"]).resolve()
                clip_path = clips_dir / f"{index:04d}.mp4"
                assets = None
                if reveal_is_enabled(config):
                    try:
                        total_reveal_frames = reveal_frame_count(config, frames)
                        animation_frames, settle_frames = split_reveal_frames(total_reveal_frames)
                        if animation_frames:
                            assets = prepare_scene_reveal_assets(
                                assets_dir,
                                project=project,
                                scene_id=str(scene.get("id") or index),
                                image_path=image_path,
                                config=config,
                                animation_frames=animation_frames,
                                settle_frames=settle_frames,
                            )
                    except Exception as exc:
                        last_error = f"Không thể tạo asset hiệu ứng cho scene {scene.get('id')}: {exc}"
                        failed = True
                        break
                effect_label = "ảnh tĩnh" if assets is None else ("nét cọ" if assets.effect == "brush" else "bút chì rồi màu")
                yield {
                    "type": "step",
                    "step": "render-scene",
                    "message": f"Đang render scene {index}/{len(scenes)} ({effect_label})...",
                    "scene_id": scene.get("id"),
                    "encoder": encoder,
                    "encoder_label": encoder_label,
                }
                render = _render_scene_clip_events(
                    ffmpeg_path=ffmpeg_path,
                    image_path=image_path,
                    clip_path=clip_path,
                    scene_frames=frames,
                    config=config,
                    assets=assets,
                    video_encoder=encoder,
                    quality=quality,
                    index=index,
                    total=len(scenes),
                )
                while True:
                    try:
                        yield next(render)
                    except StopIteration as stop:
                        return_code, ffmpeg_error = stop.value
                        break
                if return_code != 0 or not clip_path.exists() or clip_path.stat().st_size <= 0:
                    last_error = f"Render scene {scene.get('id')} thất bại: {ffmpeg_error}"
                    failed = True
                    break
                clips.append(clip_path)
            if failed:
                continue
            with open(concat_list, "w", encoding="utf-8") as handle:
                for clip in clips:
                    handle.write(f"file '{str(clip.resolve()).replace(chr(92), '/')}'\n")
            yield {"type": "step", "step": "mux-scenes", "message": "Đang ghép scene và audio...", "encoder": encoder, "encoder_label": encoder_label}
            mux = _mux_scene_clips_events(
                concat_list=concat_list,
                audio_path=audio_path,
                output_path=render_path,
                audio_duration=audio_duration,
                ffmpeg_path=ffmpeg_path,
                video_encoder=encoder,
                quality=quality,
            )
            while True:
                try:
                    yield next(mux)
                except StopIteration as stop:
                    return_code, ffmpeg_error, encoder_label = stop.value
                    break
            if return_code != 0:
                last_error = f"Ghép scene thất bại: {ffmpeg_error}"
                continue
            yield {"type": "step", "step": "verify", "message": "Đang kiểm tra toàn bộ file video..."}
            valid, integrity_error = validate_video_integrity(ffmpeg_path, render_path)
            if not valid:
                last_error = f"Integrity check failed: {integrity_error}"
                continue
            final_path = output_path
            try:
                os.replace(render_path, output_path)
            except PermissionError:
                final_path = output_path.with_name(f"{output_path.stem}_rendered_{time.strftime('%Y%m%d_%H%M%S')}{output_path.suffix}")
                os.replace(render_path, final_path)
                yield {"type": "step", "step": "output-versioned", "message": "File cũ đang được mở, đã lưu video mới với tên khác."}
            size_mb = final_path.stat().st_size / (1024 * 1024)
            yield {
                "type": "done",
                "output_file": final_path.name,
                "size_mb": round(size_mb, 2),
                "encoder": encoder,
                "encoder_label": encoder_label,
            }
            return
        yield {"type": "error", "message": f"Không thể xuất video hiệu ứng vẽ tay ổn định: {last_error or 'Lỗi không xác định'}", "encoder": attempts[-1]}
    except Exception as exc:
        yield {"type": "error", "message": f"Không thể bắt đầu render hiệu ứng vẽ tay: {exc}"}
    finally:
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
        except OSError:
            pass
        try:
            render_path.unlink(missing_ok=True)
        except OSError:
            pass


def _scene_encode_attempt(
    concat_list: Path,
    audio_path: Path,
    output_path: Path,
    audio_duration: float,
    ffmpeg_path: str,
    video_encoder: str,
    quality: str,
    scene_count: int,
) -> Generator[dict, None, tuple[int, str, str]]:
    encoder_args, encoder_label = _encoder_args(video_encoder, quality)
    yield {
        "type": "step",
        "step": "compose-scenes",
        "message": f"Đang dựng video từ {scene_count} ảnh bằng {encoder_label}...",
        "encoder": video_encoder,
        "encoder_label": encoder_label,
    }
    # Scene image concat is VFR because each still has a different narration
    # duration.  Output-level ``-r 30`` rewrites those timestamps and can make
    # the video track end early; with ``-shortest`` that also cuts spoken audio.
    # Convert to CFR inside the filter graph so timing stays on the concat
    # timeline before encoding.
    vf = "fps=30,scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1,format=yuv420p"
    cmd = [
        ffmpeg_path, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-i", str(audio_path),
        "-t", str(audio_duration),
        "-vf", vf,
        *encoder_args,
        "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        "-movflags", "+faststart",
        "-progress", "pipe:2",
        "-nostats",
        str(output_path),
    ]
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    assert proc.stderr is not None
    stderr_tail: list[str] = []
    speed = ""
    for raw in proc.stderr:
        line = raw.strip()
        if not line:
            continue
        stderr_tail.append(line)
        stderr_tail = stderr_tail[-20:]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "speed":
            speed = value
            yield {"type": "speed", "speed": speed, "encoder_label": encoder_label}
        elapsed = _parse_ffmpeg_time(key, value)
        if elapsed is not None:
            yield _progress_event(
                elapsed, audio_duration, output_path, speed, video_encoder, encoder_label,
                message="Đang xuất video ảnh theo cảnh...", percent_start=0, percent_span=99,
            )
    proc.wait()
    return proc.returncode or 0, " ".join(stderr_tail[-8:]), encoder_label


def _compose_scene_events(
    scenes: list[dict],
    images_dir: Path,
    audio_path: Path,
    output_path: Path,
    audio_duration: float,
    ffmpeg_path: str,
    video_encoder: str,
    quality: str,
) -> Generator[dict, None, None]:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    concat_list = TEMP_DIR / f"scene_images_{int(time.time())}.txt"
    # Rendering to a sibling temporary file avoids Windows failing before the
    # first SSE event when the browser still has the prior MP4 open.
    render_path = output_path.with_name(
        f".{output_path.stem}.render-{uuid.uuid4().hex}{output_path.suffix}"
    )
    try:
        yield {
            "type": "step",
            "step": "prepare",
            "message": "Dang chuan bi danh sach canh de xuat video...",
        }
        with open(concat_list, "w", encoding="utf-8") as f:
            for scene in scenes:
                image_path = (images_dir / scene["image_file"]).resolve()
                duration = max(0.04, float(scene.get("duration_sec") or 0.04))
                f.write(f"file '{str(image_path).replace(chr(92), '/')}'\n")
                f.write(f"duration {duration:.3f}\n")
            last = (images_dir / scenes[-1]["image_file"]).resolve()
            f.write(f"file '{str(last).replace(chr(92), '/')}'\n")

        attempts = [video_encoder]
        if video_encoder != "cpu_x264":
            attempts.extend([video_encoder, "cpu_x264"])
        last_error = ""
        for attempt_index, encoder in enumerate(attempts, start=1):
            render_path.unlink(missing_ok=True)
            if attempt_index > 1:
                label = _encoder_args(encoder, quality)[1]
                yield {
                    "type": "step",
                    "step": "retry" if encoder == video_encoder else "fallback",
                    "message": (
                        f"GPU export không vượt kiểm tra, thử lại bằng {label}..."
                        if encoder == video_encoder else f"GPU tiếp tục tạo file lỗi, chuyển sang {label}..."
                    ),
                    "encoder": encoder,
                    "encoder_label": label,
                }
            render = _scene_encode_attempt(
                concat_list, audio_path, render_path, audio_duration, ffmpeg_path, encoder, quality, len(scenes),
            )
            while True:
                try:
                    yield next(render)
                except StopIteration as stop:
                    return_code, ffmpeg_error, encoder_label = stop.value
                    break
            if return_code == 0:
                yield {"type": "step", "step": "verify", "message": "Đang kiểm tra toàn bộ file video..."}
                valid, integrity_error = validate_video_integrity(ffmpeg_path, render_path)
                if valid:
                    final_path = output_path
                    try:
                        os.replace(render_path, output_path)
                    except PermissionError:
                        final_path = output_path.with_name(
                            f"{output_path.stem}_rendered_{time.strftime('%Y%m%d_%H%M%S')}{output_path.suffix}"
                        )
                        os.replace(render_path, final_path)
                        yield {
                            "type": "step",
                            "step": "output-versioned",
                            "message": "File cu dang duoc mo, da luu ban video moi voi ten khac.",
                        }
                    size_mb = final_path.stat().st_size / (1024 * 1024)
                    yield {
                        "type": "done",
                        "output_file": final_path.name,
                        "size_mb": round(size_mb, 2),
                        "encoder": encoder,
                        "encoder_label": encoder_label,
                    }
                    return
                last_error = f"Integrity check failed: {integrity_error}"
            else:
                last_error = f"FFmpeg export failed: {ffmpeg_error}"
        yield {"type": "error", "message": f"Không thể tạo video ổn định: {last_error}", "encoder": attempts[-1]}
    except Exception as exc:
        yield {"type": "error", "message": f"Không thể bat dau xuat video: {exc}"}
    finally:
        try:
            concat_list.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            render_path.unlink(missing_ok=True)
        except OSError:
            pass


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
