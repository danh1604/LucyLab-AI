"""Shorts Campaign Builder — turn one long video into a teaser + climax Short pair.

YouTube counts a video >= 3:00 as a long video, not a Short, so every cut is clamped
below SHORT_MAX_SEC. Related Video / End Screen cannot be set via the Data API, so we
only store the target long video + emit a Studio checklist.
"""
import json
import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from tts_service import load_config
from video_service import get_video_duration
from youtube_publish_service import (
    _cfg_defaults,
    _generate_metadata,
    _generate_youtube_text,
    _load_plan,
    _project_dir,
    _remote_schedule,
    _local_schedule,
    _save_plan,
    schedule_campaign_triplet,
)

router = APIRouter()

# The teaser is deliberately deterministic: it is always the opening 2:55 of
# the vertical video.  The climax is the only cut selected by AI, with enough
# room to form a complete mini-story while remaining safely below 3 minutes.
TEASER_DURATION_SEC = 175
CLIMAX_MIN_SEC = 150
CLIMAX_MAX_SEC = 175
SHORT_MAX_SEC = CLIMAX_MAX_SEC
SHORT_MIN_SEC = CLIMAX_MIN_SEC

_SEGMENT_SYSTEM = (
    "You are a YouTube Shorts editor. Work in the same language and genre as the supplied narrated story. "
    "Pick ONE self-contained climax clip to promote the full video. Return strict JSON only:\n"
    '{"segments":[{"role":"climax","start_sec":0,"end_sec":40,"reason":"..."}]}\n'
    "Rules:\n"
    f"- The teaser is already fixed by the app to the opening {TEASER_DURATION_SEC} seconds. Do not select or return a teaser.\n"
    "- 'climax': a tense moment from the middle/late part, cut BEFORE the ending/reveal is spoiled.\n"
    f"- The climax MUST be self-contained and last between {CLIMAX_MIN_SEC} and {CLIMAX_MAX_SEC} seconds.\n"
    "- Start at the beginning of a spoken sentence/scene, never halfway through a sentence.\n"
    "- End immediately AFTER a complete subtitle cue. Prefer a question, an ellipsis, a frightening sound, "
    "or a new danger is revealed; never end mid-sentence, mid-dialogue, or mid-action.\n"
    "- The timestamp at end_sec must match the END of a subtitle cue that has completed its thought.\n"
    "- Use the subtitle timestamps provided. Never exceed the video duration."
)


class ShortsCampaignRequest(BaseModel):
    project: str
    long_item_id: str = ""
    vertical_filename: str = ""
    model: str = ""
    role: str = ""


def _cues_digest(cues: list[dict]) -> str:
    """Full timestamped SRT cue list for selecting teaser and climax precisely."""
    lines = []
    for cue in cues:
        start = float(cue.get("start_sec", 0) or 0)
        end = float(cue.get("end_sec", start) or start)
        text = str(cue.get("text", "")).strip()
        if text:
            lines.append(f"[{start:.1f}-{end:.1f}] {text}")
    return "\n".join(lines)


def _valid_segment(segment: object) -> dict | None:
    """Keep only a real time range before storing it as a rejected Short."""
    if not isinstance(segment, dict):
        return None
    try:
        start = round(float(segment.get("start_sec", 0) or 0), 2)
        end = round(float(segment.get("end_sec", 0) or 0), 2)
    except (TypeError, ValueError):
        return None
    return {"start_sec": start, "end_sec": end} if end > start else None


def _substantially_overlaps_rejected(start: float, end: float, rejected_segments: list[dict]) -> bool:
    """A retry must not silently pick the same narrative beat again."""
    length = max(0.01, end - start)
    for rejected in rejected_segments:
        old = _valid_segment(rejected)
        if not old:
            continue
        old_start, old_end = old["start_sec"], old["end_sec"]
        overlap = max(0.0, min(end, old_end) - max(start, old_start))
        # A slightly shifted cut of the same scene is still a rejected cut.
        if overlap / min(length, old_end - old_start) >= 0.25:
            return True
        if abs(start - old_start) <= 8 and abs(end - old_end) <= 8:
            return True
    return False


def _rejected_segments_for_role(plan: dict, long_item_id: str, role: str) -> list[dict]:
    history = plan.get("shorts_rejected_segments")
    if not isinstance(history, list):
        return []
    return [
        record["segment"]
        for record in history
        if isinstance(record, dict)
        and record.get("long_item_id") == long_item_id
        and record.get("role") == role
        and _valid_segment(record.get("segment"))
    ]


def _remember_rejected_segments(plan: dict, long_item_id: str, role: str, previous: list[dict]) -> None:
    """Persist discarded cuts so each subsequent retry has negative context."""
    if not role:
        return
    history = plan.setdefault("shorts_rejected_segments", [])
    if not isinstance(history, list):
        history = []
        plan["shorts_rejected_segments"] = history
    known = {
        (record.get("long_item_id"), record.get("role"), segment["start_sec"], segment["end_sec"])
        for record in history
        if isinstance(record, dict)
        for segment in [_valid_segment(record.get("segment"))]
        if segment
    }
    for item in previous:
        segment = _valid_segment(item.get("segment"))
        if not segment:
            continue
        key = (long_item_id, role, segment["start_sec"], segment["end_sec"])
        if key not in known:
            history.append({"long_item_id": long_item_id, "role": role, "segment": segment})
            known.add(key)


def _clamp_segment(start: float, end: float, duration: float) -> tuple[float, float]:
    """Force an AI-selected climax inside its 2:30–2:55 duration bounds."""
    if 0 < duration < SHORT_MIN_SEC:
        # A source shorter than 2:30 cannot satisfy the floor; preserve the
        # requested beat rather than silently turning every cut into 0–end.
        return round(max(0.0, min(start, duration)), 2), round(max(0.0, min(end, duration)), 2)
    start = max(0.0, min(start, max(0.0, duration - SHORT_MIN_SEC)))
    end = min(end, duration) if duration > 0 else end
    if end - start > SHORT_MAX_SEC:
        end = start + SHORT_MAX_SEC  # keep the opening (the hook), trim the tail
    minimum = min(SHORT_MIN_SEC, duration) if duration > 0 else SHORT_MIN_SEC
    if end - start < minimum:
        # Prefer extending forward so the chosen hook remains the opening.  At
        # the end of a video, move the window back rather than returning a
        # too-short clip.
        end = min(duration or start + minimum, start + minimum)
        if end - start < minimum:
            start = max(0.0, end - minimum)
    return round(start, 2), round(end, 2)


def _fixed_teaser_segment(duration: float) -> dict:
    """The campaign teaser is always the first 2:55, never AI-selected."""
    end = min(TEASER_DURATION_SEC, duration) if duration > 0 else TEASER_DURATION_SEC
    return {
        "role": "teaser",
        "start_sec": 0.0,
        "end_sec": round(max(0.0, end), 2),
        "clamped": duration > TEASER_DURATION_SEC,
        "cue_aligned": False,
    }


def _usable_cues(cues: list[dict], duration: float) -> list[dict]:
    """Return ordered, non-empty subtitle cues that lie inside the rendered video."""
    result = []
    for cue in cues:
        start = max(0.0, float(cue.get("start_sec", 0) or 0))
        end = min(duration, float(cue.get("end_sec", start) or start))
        text = str(cue.get("text", "")).strip()
        if text and end > start:
            result.append({"start_sec": start, "end_sec": end, "text": text})
    return sorted(result, key=lambda cue: (cue["start_sec"], cue["end_sec"]))


def _ends_a_thought(text: str) -> bool:
    """Subtitles can have closing quotes/brackets after their real terminal mark."""
    return text.rstrip().rstrip('”’\"\'»)]} ').endswith((".", "!", "?", "…"))


def _cliffhanger_bonus(text: str) -> int:
    ending = text.rstrip().rstrip('”’\"\'»)]} ')
    if ending.endswith(("?", "…")):
        return 4
    if ending.endswith("!"):
        return 2
    return 0


def _snap_segment_to_cues(
    start: float, end: float, duration: float, cues: list[dict]
) -> tuple[float, float, bool]:
    """Align a proposed window to spoken boundaries without violating Short limits.

    The model remains responsible for narrative selection.  This deterministic pass
    prevents duration clamping from chopping the selected narration in the middle of
    a subtitle cue or sentence.
    """
    usable = _usable_cues(cues, duration)
    start, end = _clamp_segment(start, end, duration)
    if not usable:
        return start, end, False

    minimum = min(SHORT_MIN_SEC, duration)
    # A start boundary is safest after a cue that has completed a thought.  Limit
    # the move so a model-selected dramatic beat is not silently replaced by a
    # distant scene.
    start_candidates = []
    for index, cue in enumerate(usable):
        natural_start = index == 0 or _ends_a_thought(usable[index - 1]["text"])
        candidate = cue["start_sec"]
        # On a video shorter than the desired climax floor, do not collapse
        # every candidate to 0: retain the nearest natural sentence start.
        start_limit = duration - minimum if duration >= SHORT_MIN_SEC else duration
        if natural_start and candidate <= start_limit:
            start_candidates.append(candidate)
    if start_candidates:
        nearby = [point for point in start_candidates if abs(point - start) <= 12]
        start = min(nearby or start_candidates, key=lambda point: abs(point - start))

    if duration < SHORT_MIN_SEC:
        # There is no valid 2:30 window in a short source. Still finish on a
        # spoken cue so this exceptional path never cuts a sentence in half.
        end_candidates = [cue for cue in usable if cue["end_sec"] >= start]
        if end_candidates:
            complete = [cue for cue in end_candidates if _ends_a_thought(cue["text"])]
            chosen = min(complete or end_candidates, key=lambda cue: abs(cue["end_sec"] - end))
            return round(start, 2), round(chosen["end_sec"], 2), True
        return _clamp_segment(start, end, duration) + (False,)

    latest_end = min(duration, start + SHORT_MAX_SEC)
    earliest_end = start + minimum
    target_end = min(max(end, earliest_end), latest_end)
    end_candidates = [cue for cue in usable if earliest_end <= cue["end_sec"] <= latest_end]
    if not end_candidates:
        # This only occurs with malformed/sparse subtitles.  Keeping a cue edge is
        # still better than cutting inside speech; the duration guard remains intact.
        return _clamp_segment(start, end, duration) + (False,)

    complete = [cue for cue in end_candidates if _ends_a_thought(cue["text"])]
    pool = complete or end_candidates
    # Complete thoughts dominate, then prefer the model's chosen timing.  Among
    # equally close endings, questions/ellipses make stronger unresolved exits.
    chosen = min(
        pool,
        key=lambda cue: (
            abs(cue["end_sec"] - target_end),
            -_cliffhanger_bonus(cue["text"]),
        ),
    )
    return round(start, 2), round(chosen["end_sec"], 2), True


def _different_segment(
    start: float,
    end: float,
    duration: float,
    cues: list[dict],
    rejected_segments: list[dict],
) -> tuple[float, float, bool]:
    """Choose the nearest cue-safe alternative when an LLM repeats a rejected cut."""
    length = min(SHORT_MAX_SEC, max(SHORT_MIN_SEC, end - start))
    usable = _usable_cues(cues, duration)
    candidates = [0.0]
    candidates.extend(
        cue["start_sec"] for index, cue in enumerate(usable)
        if (index == 0 or _ends_a_thought(usable[index - 1]["text"]))
        and cue["start_sec"] <= max(0.0, duration - min(SHORT_MIN_SEC, duration))
    )
    if not usable:
        candidates.extend(duration * point for point in (0.2, 0.4, 0.6, 0.8))

    alternatives = []
    for candidate in candidates:
        candidate_start, candidate_end, cue_aligned = _snap_segment_to_cues(
            candidate, candidate + length, duration, cues
        )
        if not _substantially_overlaps_rejected(candidate_start, candidate_end, rejected_segments):
            alternatives.append((candidate_start, candidate_end, cue_aligned))
    if not alternatives:
        return start, end, False
    return min(alternatives, key=lambda segment: abs(segment[0] - start))


def _pick_segments(
    plan: dict,
    duration: float,
    model: str,
    avoid_role: str = "",
    rejected_segments: list[dict] | None = None,
) -> list[dict]:
    cfg = _cfg_defaults(load_config())
    model_override = (model or "").strip()
    cues = plan.get("subtitle_cues") or []
    digest = _cues_digest(cues)
    teaser = _fixed_teaser_segment(duration)
    # The fallback is intentionally only for the AI-selected climax. The teaser
    # must remain exactly the opening 2:55 even after a retry.
    fallback = {
        "role": "climax",
        "start_sec": max(0.0, (duration or 0) * 0.6),
        "end_sec": max(0.0, (duration or 0) * 0.6) + min(CLIMAX_MAX_SEC, duration or CLIMAX_MAX_SEC),
    }
    if avoid_role == "teaser":
        return [teaser]
    if not digest:
        climax = fallback
    else:
        rejected_segments = rejected_segments or []
        rejected_ranges = [segment for segment in (_valid_segment(item) for item in rejected_segments) if segment]
        avoid_note = ""
        if avoid_role and rejected_ranges:
            ranges = ", ".join(
                f"{segment['start_sec']:.1f}s-{segment['end_sec']:.1f}s" for segment in rejected_ranges
            )
            avoid_note = (
                f"\nREJECTED {avoid_role} cuts: {ranges}. The user explicitly rejected these passages. "
                "For that role choose a different scene with no meaningful overlap (maximum 25% overlap); "
                "a small trim or shifted start is not a new Short."
            )
        user = (
            f"Story title: {plan.get('story_title')}\n"
            f"Video duration (seconds): {duration:.1f}\n"
            f"Subtitle cues with timestamps:\n{digest}{avoid_note}"
        )
        try:
            raw = _generate_youtube_text(cfg, _SEGMENT_SYSTEM, user, model_override, 800)
            data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
            candidates = data.get("segments") if isinstance(data.get("segments"), list) else []
            climax = next(
                (segment for segment in candidates if isinstance(segment, dict) and segment.get("role") == "climax"),
                fallback,
            )
        except Exception:
            climax = fallback

    proposed_start, proposed_end = _clamp_segment(
        float(climax.get("start_sec", 0) or 0),
        float(climax.get("end_sec", 0) or 0),
        duration,
    )
    start, end, cue_aligned = _snap_segment_to_cues(proposed_start, proposed_end, duration, cues)
    if avoid_role == "climax" and rejected_ranges and _substantially_overlaps_rejected(start, end, rejected_ranges):
        start, end, cue_aligned = _different_segment(start, end, duration, cues, rejected_ranges)
    climax_result = {
        "role": "climax", "start_sec": start, "end_sec": end,
        "clamped": (end - start) >= CLIMAX_MAX_SEC,
        "cue_aligned": cue_aligned,
    }
    return [teaser, climax_result]


def _cut_vertical(source: Path, start: float, length: float, dest: Path, ffmpeg: str) -> None:
    """Cut a timed segment from the already-rendered 9:16 video without recropping it."""
    cmd = [
        ffmpeg, "-y", "-ss", str(start), "-i", str(source), "-t", str(length),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        message = (result.stderr or result.stdout or "FFmpeg cut failed")[-600:]
        raise RuntimeError(message)


@router.post("/api/publish/youtube/shorts-campaign")
def build_shorts_campaign(body: ShortsCampaignRequest):
    plan = _load_plan(body.project)
    items = plan.get("items", [])
    long_item = next(
        (it for it in items if it.get("kind") == "long"
         and (not body.long_item_id or it.get("id") == body.long_item_id)),
        None,
    )
    if not long_item:
        raise HTTPException(404, "Không tìm thấy video dài trong publish plan")
    requested_role = (body.role or "").strip().lower()
    if requested_role and requested_role not in {"teaser", "climax"}:
        raise HTTPException(400, "Short role must be teaser or climax")
    project_dir = _project_dir(body.project)
    filename = Path(body.vertical_filename or "").name
    if not filename or filename != body.vertical_filename:
        raise HTTPException(400, "A rendered 9:16 video is required for Shorts campaign")
    source = project_dir / filename
    if not source.exists():
        raise HTTPException(404, "File video dài không tồn tại")

    cfg = _cfg_defaults(load_config())
    ffmpeg = cfg.get("ffmpeg_path", "ffmpeg")
    ffprobe = cfg.get("ffprobe_path", "ffprobe")
    duration = get_video_duration(source, ffprobe)
    if duration <= 0:
        raise HTTPException(400, "Không đọc được độ dài video dài")

    # A role-specific regeneration only replaces that one Short. Keep its old
    # segment as negative context before asking the LLM for a replacement.
    previous = [
        item for item in items
        if item.get("kind") == "short"
        and item.get("long_item_id") == long_item["id"]
        and item.get("role") in {"teaser", "climax"}
        and (not requested_role or item.get("role") == requested_role)
    ]
    rejected_segments = _rejected_segments_for_role(plan, long_item["id"], requested_role)
    if requested_role:
        rejected_segments.extend(
            segment for segment in (_valid_segment(item.get("segment")) for item in previous) if segment
        )

    segments = _pick_segments(
        plan,
        duration,
        body.model,
        avoid_role=requested_role,
        rejected_segments=rejected_segments,
    )
    if requested_role:
        segments = [segment for segment in segments if segment.get("role") == requested_role]
        if not segments:
            raise HTTPException(400, f"Không tạo được phân đoạn {requested_role}")

    out_dir = _project_dir(body.project) / "shorts_campaign"
    out_dir.mkdir(parents=True, exist_ok=True)

    if requested_role and any(item.get("youtube_video_id") for item in previous):
        raise HTTPException(409, "Short đã upload lên YouTube, không thể tạo lại để tránh lệch lịch/video đã đăng")

    created = []
    metadata_fallbacks = 0
    for seg in segments:
        length = round(seg["end_sec"] - seg["start_sec"], 2)
        if length < SHORT_MIN_SEC:
            continue
        item_id = str(uuid.uuid4())
        dest = out_dir / f"{seg['role']}_{item_id[:8]}.mp4"
        _cut_vertical(source, seg["start_sec"], length, dest, ffmpeg)
        item = {
            "id": item_id,
            "kind": "short",
            "index": len(created) + 1,
            "role": seg["role"],
            "source_path": str(dest),
            "source_file": dest.name,
            "aspect": "9:16",
            "duration_sec": length,
            "status": "draft",
            "long_item_id": long_item["id"],
            "segment": {
                "start_sec": seg["start_sec"],
                "end_sec": seg["end_sec"],
                "clamped": seg["clamped"],
                "cue_aligned": seg["cue_aligned"],
            },
        }
        item.update(_generate_metadata(plan, item, body.model))
        metadata_fallbacks += int(bool(item.get("metadata_fallback")))
        created.append(item)

    if not created:
        raise HTTPException(400, "No usable Shorts segments were created")

    # Keep the current item untouched until its replacement was fully rendered.
    # This makes a failed regeneration non-destructive for the existing Short.
    for item in previous:
        try:
            old_path = Path(item.get("source_path") or "").resolve()
            if old_path.is_file() and str(old_path).startswith(str(out_dir.resolve())):
                old_path.unlink()
        except OSError:
            pass
    items[:] = [item for item in items if item not in previous]
    items.extend(created)

    cfg = _cfg_defaults(load_config())
    schedule_campaign_triplet(
        long_item,
        created,
        cfg,
        _remote_schedule(cfg) + _local_schedule(body.project, cfg),
    )
    plan["short_count"] = sum(1 for item in items if item.get("kind") == "short")
    plan["shorts_vertical_source_file"] = source.name
    _remember_rejected_segments(plan, long_item["id"], requested_role, previous)
    plan["items"] = items
    _save_plan(plan)

    # Related Video for Shorts is Studio-only (needs Advanced feature access) — cannot be set via API.
    checklist = [
        "Upload video dài dưới dạng Unlisted trước để làm Related Video target.",
        "Upload từng Short, mở YouTube Studio → chọn Short.",
        "Vào phần 'Related video' và gắn video dài đích (link bên dưới), rồi mới đặt lịch video dài công khai.",
        f"Video dài đích: {long_item.get('title') or long_item.get('story_file') or long_item['id']}",
        "Cần quyền Advanced features của kênh để dùng Related video.",
    ]
    return {
        "project": body.project,
        "long_item_id": long_item["id"],
        "vertical_source_file": source.name,
        "regenerated_role": requested_role or None,
        "created": created,
        "warnings": ([f"{metadata_fallbacks} Shorts dùng metadata nháp vì LLM không phản hồi."] if metadata_fallbacks else []),
        "related_video_checklist": checklist,
        "note": "Related Video và End Screen không thể tự gắn qua YouTube Data API — làm thủ công trong Studio.",
    }


if __name__ == "__main__":
    # ponytail: clamp is the load-bearing rule (Shorts must stay < 3:00) — self-check it.
    assert _clamp_segment(0, 300, 600) == (0.0, float(SHORT_MAX_SEC)), "over-long clip must clamp to 2:58"
    assert _clamp_segment(10, 50, 600) == (10.0, 50.0), "in-range clip unchanged"
    s, e = _clamp_segment(590, 900, 600)
    assert e <= 600 and (e - s) <= SHORT_MAX_SEC, "clip near end stays inside video"
    assert _clamp_segment(0, 0, 120)[1] > 0, "zero-length clip gets a real length"
    print("shorts_campaign_service self-check OK")
