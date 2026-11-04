"""Local hand-drawn reveal assets for scene-image video rendering.

The module deliberately keeps the visual effect deterministic and offline.  It
creates small, job-scoped PNG sequences that FFmpeg combines with the selected
scene image; source scene images are never modified.
"""
from __future__ import annotations

import hashlib
import math
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageOps


FPS = 30
FRAME_WIDTH = 1920
FRAME_HEIGHT = 1080
ASSET_FPS = 12
MASK_WIDTH = 960
MASK_HEIGHT = 540
PAPER_COLOR = "#F8F1E4"
# The final three output frames of every reveal bypass the mask and show the
# original image.  This avoids a terminal partial-mask frame surviving when a
# compact 12 fps mask sequence is resampled to the 30 fps video timeline.
SETTLE_FRAMES = 3

_EFFECTS = {"brush", "pencil"}
_PATTERNS = {"auto", "left_to_right", "right_to_left", "top_to_bottom", "diagonal", "scribble"}
_ACTORS = {"none", "hand", "pencil", "brush"}


class RevealConfigError(ValueError):
    """The reveal payload is syntactically valid JSON but semantically invalid."""


@dataclass(frozen=True)
class SceneRevealAssets:
    effect: str
    animation_frames: int
    settle_frames: int
    line_frames: int
    asset_fps: int = ASSET_FPS
    mask_pattern: Path | None = None
    color_mask_pattern: Path | None = None
    actor_pattern: Path | None = None
    line_art_path: Path | None = None


def default_scene_reveal() -> dict[str, Any]:
    return {
        "enabled": False,
        "effect": "brush",
        "duration_sec": 1.2,
        "pattern": "auto",
        "actor": "none",
    }


def normalize_scene_reveal(value: Any) -> dict[str, Any]:
    """Return a complete, safe config.  Invalid requested values fail loudly."""
    raw = value if isinstance(value, dict) else {}
    result = default_scene_reveal()
    result["enabled"] = bool(raw.get("enabled", result["enabled"]))
    for key, allowed in (("effect", _EFFECTS), ("pattern", _PATTERNS), ("actor", _ACTORS)):
        requested = str(raw.get(key, result[key]) or result[key]).strip().lower()
        if requested not in allowed:
            raise RevealConfigError(f"Invalid scene reveal {key}: {requested}")
        result[key] = requested
    try:
        duration = float(raw.get("duration_sec", raw.get("durationSec", result["duration_sec"])))
    except (TypeError, ValueError) as exc:
        raise RevealConfigError("Scene reveal duration must be a number") from exc
    if not 0.4 <= duration <= 3.0:
        raise RevealConfigError("Scene reveal duration must be between 0.4 and 3 seconds")
    result["duration_sec"] = round(duration, 3)
    return result


def normalize_scene_reveal_override(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    mode = str(raw.get("mode", "inherit") or "inherit").strip().lower()
    if mode not in {"inherit", "off", "custom"}:
        raise RevealConfigError("Scene reveal override mode must be inherit, off, or custom")
    if mode != "custom":
        return {"mode": mode}
    config = normalize_scene_reveal(raw)
    config["mode"] = "custom"
    config["enabled"] = True
    return config


def resolve_scene_reveal(global_config: Any, scene: dict[str, Any]) -> dict[str, Any]:
    result = normalize_scene_reveal(global_config)
    override = scene.get("reveal_override") if isinstance(scene, dict) else None
    if not isinstance(override, dict):
        return result
    normalized = normalize_scene_reveal_override(override)
    if normalized["mode"] == "inherit":
        return result
    if normalized["mode"] == "off":
        result["enabled"] = False
        return result
    normalized.pop("mode", None)
    return normalized


def reveal_is_enabled(config: dict[str, Any]) -> bool:
    return bool(config.get("enabled"))


def scene_frame_counts(scenes: list[dict[str, Any]], audio_duration: float) -> list[int]:
    """Allocate CFR frames from cumulative boundaries without shortening audio."""
    target_total = max(1, math.ceil(max(0.0, float(audio_duration)) * FPS))
    counts: list[int] = []
    previous_frame = 0
    cumulative_seconds = 0.0
    for index, scene in enumerate(scenes):
        fallback_duration = max(0.04, float(scene.get("duration_sec") or 0.04))
        cumulative_seconds += fallback_duration
        if index == len(scenes) - 1:
            end_frame = target_total
        else:
            try:
                boundary = float(scene.get("end_sec"))
            except (TypeError, ValueError):
                boundary = cumulative_seconds
            end_frame = math.ceil(max(0.0, boundary) * FPS)
            # Every scene must have a visible frame, while retaining enough
            # frames for all remaining scenes.
            minimum_remaining = len(scenes) - index - 1
            end_frame = max(previous_frame + 1, min(end_frame, target_total - minimum_remaining))
        counts.append(max(1, end_frame - previous_frame))
        previous_frame = end_frame
    return counts


def reveal_frame_count(config: dict[str, Any], scene_frames: int) -> int:
    requested = max(1, round(float(config["duration_sec"]) * FPS))
    return max(1, min(requested, max(1, math.floor(scene_frames * 0.45))))


def split_reveal_frames(total_reveal_frames: int) -> tuple[int, int]:
    """Split an effect into animated frames and a full-colour settle state.

    Very short scenes skip animation instead of risking a one-frame partial
    reveal.  The caller renders those scenes as a normal static image.
    """
    total = max(0, int(total_reveal_frames))
    if total <= SETTLE_FRAMES + 1:
        return 0, total
    return total - SETTLE_FRAMES, SETTLE_FRAMES


def prepare_scene_reveal_assets(
    root: Path,
    *,
    project: str,
    scene_id: str,
    image_path: Path,
    config: dict[str, Any],
    animation_frames: int,
    settle_frames: int,
) -> SceneRevealAssets:
    """Create only the transient assets required for one scene render."""
    if animation_frames < 2:
        raise RevealConfigError("Scene reveal animation requires at least two frames")
    effect = str(config["effect"])
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", scene_id or "scene").strip("_") or "scene"
    asset_dir = root / safe_id
    asset_dir.mkdir(parents=True, exist_ok=True)
    seed = _stable_seed(project, scene_id, effect, config["pattern"])

    if effect == "brush":
        mask_plan = _build_stroke_plan(config["pattern"], seed, MASK_WIDTH, MASK_HEIGHT)
        actor_plan = _build_stroke_plan(config["pattern"], seed, FRAME_WIDTH, FRAME_HEIGHT)
        asset_frames = _asset_frame_count(animation_frames)
        mask_pattern = asset_dir / "mask_%04d.png"
        _write_mask_sequence(asset_dir, "mask", mask_plan, asset_frames, MASK_WIDTH, MASK_HEIGHT)
        actor_pattern = _write_actor_sequence(
            asset_dir, "actor", actor_plan, asset_frames, str(config["actor"]),
        )
        return SceneRevealAssets(
            effect="brush",
            animation_frames=animation_frames,
            settle_frames=settle_frames,
            line_frames=0,
            mask_pattern=mask_pattern,
            actor_pattern=actor_pattern,
        )

    line_frames = max(1, min(animation_frames - 1, round(animation_frames * 0.45)))
    color_frames = max(1, animation_frames - line_frames)
    line_mask_plan = _build_stroke_plan(config["pattern"], seed, MASK_WIDTH, MASK_HEIGHT)
    color_mask_plan = _build_stroke_plan(config["pattern"], seed ^ 0xA5A5A5A5, MASK_WIDTH, MASK_HEIGHT)
    line_actor_plan = _build_stroke_plan(config["pattern"], seed, FRAME_WIDTH, FRAME_HEIGHT)
    color_actor_plan = _build_stroke_plan(config["pattern"], seed ^ 0xA5A5A5A5, FRAME_WIDTH, FRAME_HEIGHT)
    line_art_path = asset_dir / "line_art.png"
    _write_line_art(image_path, line_art_path)
    _write_mask_sequence(asset_dir, "line_mask", line_mask_plan, _asset_frame_count(line_frames), MASK_WIDTH, MASK_HEIGHT)
    _write_mask_sequence(asset_dir, "color_mask", color_mask_plan, _asset_frame_count(color_frames), MASK_WIDTH, MASK_HEIGHT)
    actor_pattern = _write_pencil_actor_sequence(
        asset_dir,
        line_actor_plan,
        color_actor_plan,
        _asset_frame_count(line_frames),
        _asset_frame_count(color_frames),
        str(config["actor"]),
    )
    return SceneRevealAssets(
        effect="pencil",
        animation_frames=animation_frames,
        settle_frames=settle_frames,
        line_frames=line_frames,
        mask_pattern=asset_dir / "line_mask_%04d.png",
        color_mask_pattern=asset_dir / "color_mask_%04d.png",
        actor_pattern=actor_pattern,
        line_art_path=line_art_path,
    )


def _stable_seed(*parts: object) -> int:
    text = "|".join(str(part or "") for part in parts)
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def _asset_frame_count(output_frames: int) -> int:
    return max(1, math.ceil(output_frames / FPS * ASSET_FPS))


def _build_stroke_plan(pattern: str, seed: int, width: int, height: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    selected = pattern
    if selected == "auto":
        selected = rng.choice(["left_to_right", "right_to_left", "top_to_bottom", "diagonal", "scribble"])
    strokes: list[dict[str, Any]] = []
    if selected == "top_to_bottom":
        count = 10
        spacing = width / (count - 1)
        for index in range(count):
            x = index * spacing
            strokes.append(_wavy_stroke((x, -100), (x, height + 100), max(100, int(spacing * 1.5)), rng))
    else:
        count = 9
        spacing = height / (count - 1)
        levels = [index * spacing for index in range(count)]
        if selected == "scribble":
            rng.shuffle(levels)
        for level in levels:
            slope = (height / width) * rng.uniform(-0.24, 0.24) if selected == "diagonal" else rng.uniform(-0.035, 0.035)
            start = (-110, level - slope * width / 2)
            end = (width + 110, level + slope * width / 2)
            if selected == "right_to_left":
                start, end = end, start
            strokes.append(_wavy_stroke(start, end, max(110, int(spacing * 1.55)), rng))
    for index, stroke in enumerate(strokes):
        stroke["start"] = index / len(strokes)
        stroke["end"] = (index + 1) / len(strokes)
    return strokes


def _wavy_stroke(start: tuple[float, float], end: tuple[float, float], width: int, rng: random.Random) -> dict[str, Any]:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = max(1.0, math.hypot(dx, dy))
    normal = (-dy / length, dx / length)
    points: list[tuple[float, float]] = []
    for index in range(8):
        progress = index / 7
        jitter = 0.0 if index in {0, 7} else rng.uniform(-0.18, 0.18) * width
        points.append((
            start[0] + dx * progress + normal[0] * jitter,
            start[1] + dy * progress + normal[1] * jitter,
        ))
    return {"points": points, "width": width}


def _partial_polyline(points: list[tuple[float, float]], fraction: float) -> list[tuple[float, float]]:
    if fraction >= 1:
        return points
    if fraction <= 0 or len(points) < 2:
        return points[:1]
    lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:])]
    wanted = sum(lengths) * fraction
    consumed = 0.0
    result = [points[0]]
    for segment, (start, end) in zip(lengths, zip(points, points[1:])):
        if consumed + segment <= wanted:
            result.append(end)
            consumed += segment
            continue
        local = (wanted - consumed) / max(segment, 0.0001)
        result.append((start[0] + (end[0] - start[0]) * local, start[1] + (end[1] - start[1]) * local))
        break
    return result


def _draw_plan(mask: Image.Image, plan: list[dict[str, Any]], progress: float) -> tuple[tuple[float, float], tuple[float, float]]:
    draw = ImageDraw.Draw(mask)
    actor_point = (mask.width / 2, mask.height / 2)
    actor_tangent = (1.0, 0.0)
    for stroke in plan:
        start = float(stroke["start"])
        end = float(stroke["end"])
        fraction = min(1.0, max(0.0, (progress - start) / max(0.0001, end - start)))
        if fraction <= 0:
            continue
        points = _partial_polyline(stroke["points"], fraction)
        width = int(stroke["width"])
        if len(points) > 1:
            draw.line(points, fill=255, width=width, joint="curve")
        if points:
            tip = points[-1]
            radius = width / 2
            draw.ellipse((tip[0] - radius, tip[1] - radius, tip[0] + radius, tip[1] + radius), fill=255)
            actor_point = tip
            if len(points) > 1:
                actor_tangent = (tip[0] - points[-2][0], tip[1] - points[-2][1])
    return actor_point, actor_tangent


def _write_mask_sequence(directory: Path, prefix: str, plan: list[dict[str, Any]], frames: int, width: int, height: int) -> None:
    for frame in range(frames):
        progress = (frame + 1) / frames
        # Keep a full-white mask for two compact-source frames.  The renderer
        # still has an explicit full-colour settle segment, but this prevents
        # its visible final animated frame from ending on a narrow uncovered
        # strip when FFmpeg converts 12 fps masks to 30 fps.
        if frame >= frames - min(2, frames):
            mask = Image.new("L", (width, height), 255)
        else:
            mask = Image.new("L", (width, height), 0)
            _draw_plan(mask, plan, progress)
        mask.save(directory / f"{prefix}_{frame + 1:04d}.png", compress_level=1)


def _write_actor_sequence(directory: Path, prefix: str, plan: list[dict[str, Any]], frames: int, actor: str) -> Path | None:
    if actor == "none":
        return None
    for frame in range(frames):
        progress = (frame + 1) / frames
        mask = Image.new("L", (FRAME_WIDTH, FRAME_HEIGHT), 0)
        point, tangent = _draw_plan(mask, plan, progress)
        _write_actor_frame(directory / f"{prefix}_{frame + 1:04d}.png", actor, point, tangent, frame, frames)
    return directory / f"{prefix}_%04d.png"


def _write_pencil_actor_sequence(
    directory: Path,
    line_plan: list[dict[str, Any]],
    color_plan: list[dict[str, Any]],
    line_frames: int,
    color_frames: int,
    actor: str,
) -> Path | None:
    if actor == "none":
        return None
    total = line_frames + color_frames
    for frame in range(total):
        if frame < line_frames:
            plan, local_frame, local_total = line_plan, frame, line_frames
        else:
            plan, local_frame, local_total = color_plan, frame - line_frames, color_frames
        mask = Image.new("L", (FRAME_WIDTH, FRAME_HEIGHT), 0)
        point, tangent = _draw_plan(mask, plan, (local_frame + 1) / local_total)
        _write_actor_frame(directory / f"actor_{frame + 1:04d}.png", actor, point, tangent, frame, total)
    return directory / "actor_%04d.png"


def _write_actor_frame(
    target: Path,
    actor: str,
    point: tuple[float, float],
    tangent: tuple[float, float],
    frame: int,
    total: int,
) -> None:
    canvas = Image.new("RGBA", (FRAME_WIDTH, FRAME_HEIGHT), (0, 0, 0, 0))
    tile = _actor_tile(actor)
    angle = -math.degrees(math.atan2(tangent[1], tangent[0]))
    tile = tile.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
    fade = min(1.0, (frame + 1) / 4, (total - frame) / 5)
    if fade < 1:
        alpha = tile.getchannel("A").point(lambda value: round(value * fade))
        tile.putalpha(alpha)
    canvas.alpha_composite(tile, (round(point[0] - tile.width * 0.5), round(point[1] - tile.height * 0.52)))
    canvas.save(target, compress_level=1)


def _actor_tile(actor: str) -> Image.Image:
    size = 230
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    if actor == "brush":
        draw.rounded_rectangle((38, 92, 185, 128), radius=16, fill=(100, 59, 34, 255))
        draw.rounded_rectangle((148, 92, 188, 128), radius=8, fill=(188, 153, 94, 255))
        draw.polygon([(185, 88), (217, 98), (217, 122), (185, 132)], fill=(49, 35, 27, 255))
    else:
        draw.polygon([(24, 96), (180, 72), (199, 116), (42, 141)], fill=(239, 181, 77, 255))
        draw.polygon([(180, 72), (218, 94), (199, 116)], fill=(197, 168, 127, 255))
        draw.polygon([(218, 94), (226, 101), (199, 116)], fill=(43, 42, 43, 255))
        if actor == "hand":
            draw.ellipse((24, 108, 121, 189), fill=(224, 169, 129, 255))
            draw.rounded_rectangle((72, 130, 157, 172), radius=20, fill=(224, 169, 129, 255))
            draw.ellipse((97, 91, 133, 139), fill=(235, 181, 141, 255))
    return tile


def _write_line_art(image_path: Path, target: Path) -> None:
    with Image.open(image_path) as source:
        rgb = source.convert("RGB")
    grayscale = ImageOps.grayscale(rgb)
    edges = grayscale.filter(ImageFilter.FIND_EDGES)
    edges = ImageOps.autocontrast(edges, cutoff=2)
    alpha = edges.point(lambda value: 0 if value < 36 else min(220, (value - 36) * 3))
    line_art = Image.new("RGB", rgb.size, PAPER_COLOR)
    ink = Image.new("RGB", rgb.size, (78, 53, 37))
    line_art.paste(ink, mask=alpha)
    line_art.save(target, optimize=True)
