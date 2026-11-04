"""Standalone thumbnail generation for post-production."""
import base64
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from image_generation_service import generate_image_events, image_provider_config
from story_service import generate_text, generate_text_openai_compatible
from tts_service import PROJECT_TITLE_VALIDATOR, get_project_dir, load_config


router = APIRouter()

UPSCALE_TARGETS = {
    "2k": (2560, 1440),
    "4k": (3840, 2160),
}
REALESRGAN_ROOT = Path(__file__).resolve().parent.parent / "tools" / "realesrgan"
REALESRGAN_MODEL = "realesrgan-x4plus"
REALESRGAN_MODEL_SCALE = 4
_UPSCALE_LOCK = threading.Lock()

SAFE_PROJECT = PROJECT_TITLE_VALIDATOR
SAFE_IMAGE_FILE = re.compile(r"^[\w .,()\-]+\.jpe?g$", re.IGNORECASE)
SAFE_REFERENCE_FILE = re.compile(r"^[\w .()\-]+\.(png|jpe?g|webp)$", re.IGNORECASE)
SAFE_SESSION_ID = re.compile(r"^[a-zA-Z0-9_-]{1,100}$")
APPROVED_THUMBNAIL_REFERENCE_PROJECT = "Người được chọn"
APPROVED_THUMBNAIL_REFERENCE_NAMES = (
    "thumbnail_frame_option_a.png",
    "thumbnail_frame_option_a.jpg",
    "thumbnail_frame_option_a.jpeg",
)

FORMULAS = {
    "mystery": {
        "label": "BÍ ẨN",
        "prompt": "an unanswered question, one clear focal clue, and a strong curiosity gap",
    },
    "warning": {
        "label": "ĐỪNG XEM MỘT MÌNH",
        "prompt": "a high-stakes warning, a clear consequence, and immediate curiosity",
    },
    "secret": {
        "label": "SỰ THẬT BỊ GIẤU",
        "prompt": "a hidden fact, a surprising clue, and an incomplete reveal",
    },
    "entity": {
        "label": "THỨ Ở SAU LƯNG",
        "prompt": "an unseen factor affecting the subject, a clear point of view, and an unresolved question",
    },
}


class StandaloneThumbnailRequest(BaseModel):
    project: str
    title: str
    session_id: str = ""
    channel_name: str = ""
    genre_label: str = ""
    hook: str = ""
    context: str = ""
    formula: str = "mystery"
    output_name: str = ""
    reference_image: str = ""
    model: str = ""
    prompt_mode: str = "original"
    safe_prompt: str = ""
    safe_level: int = 0
    upscale_mode: str = "2k"


class ThumbnailSafePromptRequest(StandaloneThumbnailRequest):
    mode: str = "rule"
    level: int = 1


class ThumbnailUpscaleRequest(BaseModel):
    filename: str
    mode: str = "2k"


def _project_dir(project: str) -> Path:
    if not project or not SAFE_PROJECT.match(project):
        raise HTTPException(400, "Invalid project name")
    path = get_project_dir(project)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _thumb_dir(project: str) -> Path:
    path = _project_dir(project) / "thumbnails" / "manual"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _history_path(project: str) -> Path:
    return _thumb_dir(project) / "history.json"


def _reference_dir(project: str) -> Path:
    path = _project_dir(project) / "thumbnails" / "template_concepts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _template_reference_path(project: str) -> Path | None:
    concept_dirs = [_reference_dir(project)]
    if project != APPROVED_THUMBNAIL_REFERENCE_PROJECT:
        concept_dirs.append(get_project_dir(APPROVED_THUMBNAIL_REFERENCE_PROJECT) / "thumbnails" / "template_concepts")
    for concept_dir in concept_dirs:
        for name in APPROVED_THUMBNAIL_REFERENCE_NAMES:
            path = concept_dir / name
            if path.exists() and path.is_file():
                return path
    return None


def _resolve_reference_path(project: str, reference_image: str = "") -> Path | None:
    token = (reference_image or "").strip()
    if not token or token == "default":
        return _template_reference_path(project)
    if token.startswith("generated:"):
        generated = _resolve_thumb(project, token.split(":", 1)[1].strip())
        if generated.exists() and generated.is_file():
            return generated
        raise HTTPException(404, "Generated thumbnail reference not found")
    if SAFE_REFERENCE_FILE.match(token):
        custom = _reference_dir(project) / token
        if custom.exists() and custom.is_file():
            return custom
    if SAFE_IMAGE_FILE.match(token):
        generated = _resolve_thumb(project, token)
        if generated.exists() and generated.is_file():
            return generated
    raise HTTPException(404, "Thumbnail reference image not found")


def _image_data_url(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _slug(value: str, fallback: str = "thumbnail") -> str:
    name = (value or "").strip().replace("\\", "_").replace("/", "_")
    name = re.sub(r'[<>:"|?*\x00-\x1f]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = fallback
    return name[:90].strip(" .") or fallback


def _unique_output_path(project: str, requested: str, title: str) -> Path:
    base = _slug(requested or title or "thumbnail")
    if base.lower().endswith((".jpg", ".jpeg")):
        base = re.sub(r"\.jpe?g$", "", base, flags=re.IGNORECASE).strip(" .")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = _thumb_dir(project) / f"{base}_{stamp}.jpg"
    if not path.exists():
        return path
    return _thumb_dir(project) / f"{base}_{stamp}_{uuid.uuid4().hex[:6]}.jpg"


def _resolve_thumb(project: str, filename: str) -> Path:
    if not filename or not SAFE_IMAGE_FILE.match(filename):
        raise HTTPException(400, "Invalid thumbnail filename")
    path = _thumb_dir(project) / filename
    try:
        path.relative_to(_thumb_dir(project))
    except ValueError:
        raise HTTPException(400, "Invalid thumbnail path")
    return path


def _load_history(project: str) -> list[dict]:
    path = _history_path(project)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_history(project: str, item: dict) -> None:
    history = [item] + [x for x in _load_history(project) if x.get("filename") != item.get("filename")]
    _history_path(project).write_text(json.dumps(history[:80], ensure_ascii=False, indent=2), encoding="utf-8")


def _write_history(project: str, history: list[dict]) -> None:
    _history_path(project).write_text(json.dumps(history[:80], ensure_ascii=False, indent=2), encoding="utf-8")


def _title_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"\s+", " ", text)


def _session_id(value: str) -> str:
    token = str(value or "").strip()
    if token and not SAFE_SESSION_ID.match(token):
        raise HTTPException(400, "Invalid session id")
    return token


def _migrate_legacy_quick_items(project: str, session_id: str, story_title: str) -> list[dict]:
    history = _load_history(project)
    target_title = _title_key(story_title)
    if not session_id or not target_title:
        return history
    changed = False
    for item in history:
        if item.get("session_id") or item.get("director_run_id"):
            continue
        if _title_key(item.get("title")) == target_title:
            item["session_id"] = session_id
            changed = True
    if changed:
        _write_history(project, history)
    return history


SENSITIVE_TERMS = (
    "blood", "gore", "corpse", "dead body", "dead", "death", "murder", "kill", "knife", "weapon",
    "violence", "violent", "injury", "wound", "attack", "assault", "crime scene", "body",
    "máu", "xác", "xác chết", "chết", "giết", "dao", "vũ khí", "bạo lực", "tấn công",
    "thi thể", "án mạng", "sát hại", "đâm", "chém", "tử vong",
)

TERM_REPLACEMENTS = {
    "horror": "suspense mystery",
    "gore": "dark mystery",
    "blood": "deep red lighting accent",
    "corpse": "still covered shape",
    "dead body": "covered mysterious shape",
    "dead": "silent",
    "death": "ominous past event",
    "murder": "dark secret",
    "kill": "threaten",
    "knife": "old metal object",
    "weapon": "old object",
    "violence": "tension",
    "violent": "tense",
    "injury": "distress",
    "wound": "shadow mark",
    "attack": "confrontation",
    "assault": "conflict",
    "crime scene": "mysterious room",
    "body": "covered shape",
    "máu": "ánh đỏ",
    "xác chết": "bóng phủ vải",
    "xác": "bóng phủ vải",
    "chết": "im lặng",
    "giết": "đe dọa",
    "dao": "vật kim loại cũ",
    "vũ khí": "đồ vật cũ",
    "bạo lực": "căng thẳng",
    "tấn công": "đối đầu",
    "thi thể": "bóng phủ vải",
    "án mạng": "bí mật cũ",
    "sát hại": "đe dọa",
    "đâm": "va chạm",
    "chém": "xô xát",
    "tử vong": "biến mất",
}


def _sanitize_visual_text(value: str, *, level: int = 1, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    if not text:
        return ""
    lowered_level = max(1, min(5, int(level or 1)))
    for term, replacement in sorted(TERM_REPLACEMENTS.items(), key=lambda item: len(item[0]), reverse=True):
        text = re.sub(re.escape(term), replacement, text, flags=re.IGNORECASE)
    if lowered_level >= 3:
        parts = [p.strip(" ,;:-") for p in re.split(r"(?<=[.!?。！？])\s+|\n+", text) if p.strip()]
        safe_parts = []
        for part in parts:
            folded = part.lower()
            if any(term in folded for term in SENSITIVE_TERMS):
                continue
            safe_parts.append(part)
        text = " ".join(safe_parts[:2]).strip()
    return text[:limit].rsplit(" ", 1)[0].strip(" ,;:-") if len(text) > limit else text


def _font(size: int, bold: bool = True):
    from PIL import ImageFont

    names = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/ARIALNB.TTF" if bold else "C:/Windows/Fonts/ARIALN.TTF",
    ]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _display_font(size: int):
    from PIL import ImageFont

    names = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/impact.ttf",
        "C:/Windows/Fonts/ARIALNB.TTF",
    ]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return _font(size)


def _text_width(draw, text: str, font) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _wrap(draw, text: str, font, max_width: int) -> list[str]:
    words = [x for x in (text or "").split() if x]
    if not words:
        return []
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        if not current or _text_width(draw, test, font) <= max_width:
            current = test
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _fit_lines(draw, text: str, max_width: int, max_lines: int, start: int, minimum: int, bold: bool = True):
    clean = re.sub(r"\s+", " ", (text or "").strip()).upper()
    for size in range(start, minimum - 1, -4):
        font = _font(size, bold=bold)
        lines = _wrap(draw, clean, font, max_width)
        if len(lines) <= max_lines:
            return font, lines
    font = _font(minimum, bold=bold)
    lines = _wrap(draw, clean, font, max_width)[:max_lines]
    if lines:
        while _text_width(draw, f"{lines[-1]}...", font) > max_width and len(lines[-1]) > 4:
            lines[-1] = " ".join(lines[-1].split()[:-1]) or lines[-1][:-2]
        lines[-1] = f"{lines[-1].rstrip('.')}..."
    return font, lines


def _cover_resize(image, size: tuple[int, int]):
    image = image.convert("RGB")
    target_w, target_h = size
    src_w, src_h = image.size
    scale = max(target_w / src_w, target_h / src_h)
    new_size = (int(src_w * scale + 0.5), int(src_h * scale + 0.5))
    resized = image.resize(new_size)
    left = (new_size[0] - target_w) // 2
    top = (new_size[1] - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def _fold_text(value: str) -> str:
    text = unicodedata.normalize("NFD", value or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.upper()


def _draw_dashed_line(draw, start: tuple[int, int], end: tuple[int, int], fill, width: int = 1, dash: int = 10, gap: int = 8):
    x1, y1 = start
    x2, y2 = end
    if x1 == x2:
        y = min(y1, y2)
        stop = max(y1, y2)
        while y < stop:
            draw.line((x1, y, x2, min(stop, y + dash)), fill=fill, width=width)
            y += dash + gap
        return
    x = min(x1, x2)
    stop = max(x1, x2)
    while x < stop:
        draw.line((x, y1, min(stop, x + dash), y2), fill=fill, width=width)
        x += dash + gap


def _draw_dashed_rect(draw, box: tuple[int, int, int, int], fill, width: int = 1, dash: int = 10, gap: int = 8):
    left, top, right, bottom = box
    _draw_dashed_line(draw, (left, top), (right, top), fill, width, dash, gap)
    _draw_dashed_line(draw, (right, top), (right, bottom), fill, width, dash, gap)
    _draw_dashed_line(draw, (right, bottom), (left, bottom), fill, width, dash, gap)
    _draw_dashed_line(draw, (left, bottom), (left, top), fill, width, dash, gap)


def _preferred_title_lines(draw, text: str, font, max_width: int) -> list[str]:
    words = [word for word in re.sub(r"\s+", " ", text.strip()).split(" ") if word]
    if len(words) >= 5:
        return [" ".join(words[:2]), " ".join(words[2:])]
    return _wrap(draw, text, font, max_width)[:2]


def _fit_title(draw, text: str, max_width: int):
    clean = re.sub(r"\s+", " ", (text or "").strip()).upper()
    for size in range(118, 54, -4):
        font = _display_font(size)
        lines = _preferred_title_lines(draw, clean, font, max_width)
        if lines and len(lines) <= 2 and all(_text_width(draw, line, font) <= max_width for line in lines):
            return font, lines
    font = _display_font(54)
    return font, _preferred_title_lines(draw, clean, font, max_width)[:2]


def _draw_word_line(draw, xy: tuple[int, int], line: str, font, *, max_width: int):
    x, y = xy
    space = _text_width(draw, " ", font)
    words = [word for word in line.split(" ") if word]
    total = sum(_text_width(draw, word, font) for word in words) + max(0, len(words) - 1) * space
    if total > max_width and words and font.size > 44:
        scaled = max(44, int(font.size * max_width / max(total, 1)))
        return _draw_word_line(draw, xy, line, _display_font(scaled), max_width=max_width)
    used_font = font
    for word in words:
        folded = _fold_text(word)
        fill = (190, 24, 33, 255) if "CHUOC" in folded else (238, 230, 211, 255)
        draw.text((x + 4, y + 5), word, font=used_font, fill=(0, 0, 0, 225), stroke_width=5, stroke_fill=(0, 0, 0, 220))
        draw.text((x, y), word, font=used_font, fill=fill, stroke_width=1, stroke_fill=(70, 10, 12, 185))
        x += _text_width(draw, word, used_font) + space
    return used_font


def _draw_pill(draw, xy, text: str, font, fill, text_fill, pad_x: int = 18):
    x, y = xy
    width = _text_width(draw, text, font) + pad_x * 2
    height = 42
    draw.rounded_rectangle((x, y, x + width, y + height), radius=11, fill=fill)
    draw.text((x + pad_x, y + 10), text, font=font, fill=text_fill)
    return x + width


def _overlay_template(raw_path: Path, out_path: Path, body: StandaloneThumbnailRequest) -> Path:
    from PIL import Image, ImageDraw, ImageFilter, ImageEnhance

    size = (1280, 720)
    img = _cover_resize(Image.open(raw_path), size)
    img = ImageEnhance.Contrast(img).enhance(1.16)
    img = ImageEnhance.Color(img).enhance(0.82)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=112))
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Option A frame: fixed code-owned structure, generated image stays background-only.
    for x in range(0, 720):
        alpha = int(235 * (1 - (x / 720) ** 1.55))
        draw.line((x, 0, x, size[1]), fill=(0, 0, 0, alpha))
    for x in range(720, size[0]):
        alpha = int(72 * ((x - 720) / 560) ** 1.35)
        draw.line((x, 0, x, size[1]), fill=(0, 0, 0, alpha))
    for y in range(0, 190):
        alpha = int(95 * (1 - y / 190))
        draw.line((0, y, size[0], y), fill=(0, 0, 0, alpha))
    for y in range(430, 720):
        alpha = int(165 * ((y - 430) / 290))
        draw.line((0, y, size[0], y), fill=(0, 0, 0, alpha))
    _draw_dashed_rect(draw, (20, 20, 1260, 700), fill=(219, 204, 179, 82), width=1, dash=12, gap=8)
    draw.line((42, 26, 42, 118), fill=(201, 24, 34, 225), width=2)
    draw.line((42, 140, 42, 410), fill=(201, 24, 34, 225), width=2)

    channel = re.sub(r"\s+", " ", (body.channel_name or "").strip()).upper()[:28]
    formula = FORMULAS.get(body.formula, FORMULAS["mystery"])
    small_font = _font(22, bold=False)
    if channel:
        draw.text((58, 52), channel, font=small_font, fill=(225, 214, 194, 238))
        channel_w = min(360, _text_width(draw, channel, small_font))
        draw.line((58, 84, 58 + channel_w + 18, 84), fill=(211, 199, 177, 170), width=2)

    hook = re.sub(r"\s+", " ", (body.hook or "").strip()).upper()
    if hook:
        draw.text((90, 176), "HOOK", font=_font(19, bold=False), fill=(219, 34, 43, 235))
        _draw_dashed_rect(draw, (90, 210, 540, 268), fill=(205, 28, 36, 160), width=1, dash=10, gap=8)
        hook_font, hook_lines = _fit_lines(draw, hook, 406, 1, 25, 19)
        if hook_lines:
            draw.text((112, 226), hook_lines[0], font=hook_font, fill=(238, 222, 186, 245))

    title_font, title_lines = _fit_title(draw, body.title, 690)
    y = 312 if hook else 270
    for line in title_lines:
        used_font = _draw_word_line(draw, (72, y), line, title_font, max_width=690)
        y += int(used_font.size * 0.93)

    accent_y = min(520, y + 16)
    draw.line((72, accent_y, 530, accent_y), fill=(198, 21, 31, 230), width=4)
    draw.polygon([(452, accent_y), (468, accent_y - 12), (484, accent_y), (476, accent_y), (468, accent_y - 6), (460, accent_y)], fill=(198, 21, 31, 240))
    draw.text((88, 645), "AUDIO STORY", font=_font(18, bold=False), fill=(225, 214, 194, 165))

    hook = re.sub(r"\s+", " ", (body.hook or "CÀNG NGHE CÀNG LẠNH").strip()).upper()[:54]
    formula_label = re.sub(r"\s+", " ", formula["label"]).upper()
    formula_font, formula_lines = _fit_lines(draw, formula_label, 210, 1, 22, 16)
    if formula_lines:
        fx = 1195
        fy = 646
        draw.text((fx, fy), formula_lines[0], font=formula_font, fill=(219, 34, 43, 235), anchor="ra")
        line_w = min(170, _text_width(draw, formula_lines[0], formula_font) + 26)
        draw.line((fx - line_w, fy + 30, fx, fy + 30), fill=(201, 24, 34, 195), width=2)

    result = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    result.save(out_path, "JPEG", quality=92, optimize=True)
    return out_path


def _build_prompt(body: StandaloneThumbnailRequest) -> str:
    formula = FORMULAS.get(body.formula, FORMULAS["mystery"])
    context = re.sub(r"\s+", " ", (body.context or "").strip())[:1600]
    channel = re.sub(r"\s+", " ", (body.channel_name or "").strip())
    genre = re.sub(r"\s+", " ", (body.genre_label or "").strip())
    hook = re.sub(r"\s+", " ", (body.hook or "").strip())
    optional_hook = f"Optional hook text: {hook}. " if hook else "Do not add any hook text; leave the hook module absent or empty. "
    optional_channel = (
        f"Include the exact channel label '{channel}' as a small refined label in the upper-left corner, matching the reference style. "
        if channel
        else "Channel label is optional; if omitted, use no channel label. "
    )
    optional_genre = (
        f"Include the exact category/genre footer text '{genre}' below the main title, replacing the reference's lower-left footer line. "
        if genre
        else "Do not add a category/genre footer line below the main title. "
    )
    return (
        "Create a complete 16:9 YouTube thumbnail, not a background-only image. "
        "Infer the subject, genre, emotional intensity, palette, visual medium, and composition from the supplied story context and optional genre. "
        "Do not assume horror, suspense, dark lighting, a specific country, or photorealism unless the story or reference explicitly calls for it. "
        "Use the attached reference thumbnail as the primary layout and brand-style reference when provided, while adapting its subject, mood, and imagery truthfully to this story. "
        "Make the result a polished final thumbnail inspired by that reference, not an exact copy. "
        "Use readable text in the same language as the supplied title and preserve its exact spelling. Do not omit required provided text. No watermark, no random extra text, no gore. "
        f"Click formula: {formula['prompt']}. "
        f"Main title text: {body.title}. "
        f"{optional_channel}{optional_genre}{optional_hook}"
        f"Story context: {context}"
    )


def _build_safe_thumbnail_prompt(body: StandaloneThumbnailRequest, level: int) -> str:
    safe_level = max(1, min(5, int(level or 1)))
    formula = FORMULAS.get(body.formula, FORMULAS["mystery"])
    safe_title = _sanitize_visual_text(body.title, level=safe_level, limit=120)
    safe_hook = _sanitize_visual_text(body.hook, level=safe_level, limit=90)
    safe_channel = _sanitize_visual_text(body.channel_name, level=safe_level, limit=60)
    safe_genre = _sanitize_visual_text(body.genre_label, level=safe_level, limit=80)
    safe_context = _sanitize_visual_text(body.context, level=safe_level, limit=900)
    scene = safe_context or "one clear, non-graphic visual clue that truthfully represents the supplied story"

    notes = {
        1: "Keep the original story genre and visual language; remove only graphic or unsafe details.",
        2: "Use indirect, non-graphic storytelling through a relevant setting, expression, prop, or diagram.",
        3: "Favor a clear establishing image, object, or character moment that matches the story's actual tone.",
        4: "Use a symbolic, non-graphic representation of the same story idea.",
        5: "Use a simple symbolic visual clue while preserving the story's real genre and core curiosity gap.",
    }
    return (
        "Create a complete, family-safe 16:9 YouTube thumbnail using the attached reference thumbnail style if provided. "
        "Infer and preserve the story's actual genre, visual medium, palette, and emotional tone from the supplied context; do not turn it into horror or a dark realistic poster by default. "
        "Use the main title text clearly in its supplied language, preserve exact spelling, and follow the reference's relevant layout and brand language. "
        "Do not omit required provided text. "
        "No watermark, no random extra text, no gore, no graphic content. "
        f"Safe level {safe_level}: {notes[safe_level]} "
        f"Click formula: {_sanitize_visual_text(formula['prompt'], level=safe_level, limit=160)}. "
        f"Main title text: {safe_title or body.title}. "
        f"Channel label: {safe_channel if safe_channel else 'none'}. "
        f"{'Place the channel label small in the upper-left corner. ' if safe_channel else ''}"
        f"Category/genre footer text: {safe_genre if safe_genre else 'none, do not invent a footer line'}. "
        f"{'Place this exact genre text below the main title, replacing the reference lower-left footer line. ' if safe_genre else ''}"
        f"Optional hook text: {safe_hook if safe_hook else 'none, do not invent a hook'}. "
        f"Visual scene: {scene}."
    )


SAFE_THUMBNAIL_LLM_SYSTEM = """Rewrite image-generation prompts for complete YouTube thumbnails.
Return one safe visual prompt only, no JSON, no markdown, no explanation.
Keep the supplied story's genre, visual medium, channel consistency, readable title text, mood, and key safe props.
Never introduce horror, suspense, dark settings, or photorealism when the story does not call for them.
Avoid unsafe or graphic visual content through an appropriate non-graphic visual treatment.
The final prompt must request a complete 16:9 final thumbnail using the attached reference thumbnail style when provided."""


def _clean_safe_prompt(value: str) -> str:
    cleaned = (value or "").strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        cleaned = parts[1] if len(parts) > 1 else cleaned
        if cleaned.lower().startswith(("text", "prompt", "markdown")):
            cleaned = cleaned.split("\n", 1)[-1]
    cleaned = re.sub(r"^(safe\s+)?prompt\s*:\s*", "", cleaned.strip().strip('"'), flags=re.IGNORECASE).strip()
    if len(cleaned) > 1800:
        cleaned = cleaned[:1800].rsplit(" ", 1)[0].strip()
    return cleaned


def _build_safe_llm_thumbnail_prompt(body: StandaloneThumbnailRequest, level: int, cfg: dict) -> str:
    safe_level = max(1, min(5, int(level or 1)))
    model = (cfg.get("safe_prompt_model") or cfg.get("default_story_model") or "claude-sonnet-4-6").strip()
    rule_prompt = _build_safe_thumbnail_prompt(body, safe_level)
    user_prompt = (
        f"Thumbnail title: {body.title}\n"
        f"Channel label: {body.channel_name}\n"
        f"Category/genre footer: {body.genre_label}\n"
        f"Hook: {body.hook}\n"
        f"Click formula: {FORMULAS.get(body.formula, FORMULAS['mystery'])['label']}\n"
        f"Story context:\n{body.context[:1800]}\n\n"
        f"Original prompt:\n{_build_prompt(body)}\n\n"
        f"Rule safe baseline:\n{rule_prompt}\n\n"
        f"Safety level requested: {safe_level}. "
        "Write a better safe final-thumbnail prompt that preserves the reference layout, story atmosphere, readable title in its supplied language, and reduces provider safety rejections. "
        "Return only the final image prompt."
    )
    try:
        if (cfg.get("safe_prompt_provider") or "default").strip().lower() == "custom":
            text = generate_text_openai_compatible(
                SAFE_THUMBNAIL_LLM_SYSTEM,
                user_prompt,
                base_url=cfg.get("safe_prompt_base_url") or "https://api.openai.com",
                path=cfg.get("safe_prompt_path") or "/v1/chat/completions",
                model=(cfg.get("safe_prompt_model") or "gpt-4o-mini").strip(),
                api_key=cfg.get("safe_prompt_api_key") or "",
                api_key_path=cfg.get("safe_prompt_api_key_path") or "",
                max_tokens=900,
            )
        else:
            text = generate_text(
                SAFE_THUMBNAIL_LLM_SYSTEM,
                user_prompt,
                model,
                cfg.get("anthropic_api_key", ""),
                cfg.get("ollama_url", "http://localhost:11434"),
                max_tokens=900,
            )
    except Exception as exc:
        raise RuntimeError(f"Safe LLM prompt failed: {exc}")
    cleaned = _clean_safe_prompt(text)
    cleaned = _sanitize_visual_text(cleaned, level=safe_level, limit=1600)
    if not cleaned:
        raise RuntimeError("Safe LLM returned an empty prompt")
    return cleaned


def _active_prompt(body: StandaloneThumbnailRequest) -> tuple[str, str, int]:
    mode = (body.prompt_mode or "original").strip().lower()
    safe_prompt = (body.safe_prompt or "").strip()
    try:
        safe_level = int(body.safe_level or 0)
    except Exception:
        safe_level = 0
    if mode in {"safe", "safe_rule", "safe_llm", "rule", "llm"} and safe_prompt:
        required_text = f" Required exact main title text: {body.title}. "
        if (body.channel_name or "").strip():
            required_text += f"Required exact channel label: {body.channel_name}. Place the channel label small in the upper-left corner. "
        if (body.genre_label or "").strip():
            required_text += f"Required exact category/genre footer text: {body.genre_label}. Place it below the main title. "
        return f"{safe_prompt.strip()} {required_text}".strip(), mode if mode.startswith("safe_") else f"safe_{mode}", max(1, safe_level)
    return _build_prompt(body), "original", 0


def _largest_16x9_size(size: tuple[int, int]) -> tuple[int, int]:
    width, height = max(1, int(size[0])), max(1, int(size[1]))
    target_height = max(1, round(width * 9 / 16))
    if target_height <= height:
        return width, target_height
    return max(1, round(height * 16 / 9)), height


def _image_dimensions(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as image:
        return image.size


def _finalize_ai_thumbnail(
    raw_path: Path,
    out_path: Path,
    target_size: tuple[int, int] | None = (1280, 720),
) -> Path:
    from PIL import Image, ImageEnhance

    with Image.open(raw_path) as source:
        source.load()
        size = target_size or _largest_16x9_size(source.size)
        img = _cover_resize(source.convert("RGB"), size)
    img = ImageEnhance.Contrast(img).enhance(1.04)
    img = ImageEnhance.Color(img).enhance(0.96)
    img.save(out_path, "JPEG", quality=95, subsampling=0, optimize=True)
    return out_path


def _normalize_upscale_mode(value: str) -> str:
    mode = str(value or "2k").strip().lower()
    if mode in {"off", "none", "original", "goc", "gốc"}:
        return "original"
    return mode if mode in UPSCALE_TARGETS else "2k"


def _realesrgan_executable(cfg: dict | None = None) -> Path | None:
    cfg = cfg or load_config()
    configured = str(cfg.get("realesrgan_path") or "").strip().strip('"')
    candidates = [
        Path(configured).expanduser() if configured else None,
        REALESRGAN_ROOT / "realesrgan-ncnn-vulkan.exe",
    ]
    discovered = shutil.which("realesrgan-ncnn-vulkan")
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate and candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def _save_upscaled_jpeg(source_path: Path, target_path: Path, target_size: tuple[int, int]) -> None:
    from PIL import Image, ImageEnhance, ImageFilter

    with Image.open(source_path) as source:
        source.load()
        image = _cover_resize(source.convert("RGB"), target_size)
    image = ImageEnhance.Contrast(image).enhance(1.015)
    image = image.filter(ImageFilter.UnsharpMask(radius=1.0, percent=70, threshold=3))
    target_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(target_path, "JPEG", quality=95, subsampling=0, optimize=True)


def _run_realesrgan(source_path: Path, output_path: Path, mode: str, cfg: dict) -> dict:
    executable = _realesrgan_executable(cfg)
    if not executable:
        raise RuntimeError("Real-ESRGAN executable is not installed")
    model_dir = executable.parent / "models"
    if not model_dir.exists():
        raise RuntimeError("Real-ESRGAN model folder is missing")

    # x4plus must run at its native scale. Scale 2 produces incorrectly
    # stitched tiles in the NCNN build, so delivery sizing happens afterward.
    scale = REALESRGAN_MODEL_SCALE
    with tempfile.TemporaryDirectory(prefix="lucylab_thumb_upscale_") as temp_dir:
        temp_root = Path(temp_dir)
        input_path = temp_root / "input.png"
        ai_output = temp_root / "upscaled.png"
        from PIL import Image

        with Image.open(source_path) as source:
            source.convert("RGB").save(input_path, "PNG", optimize=True)
        command = [
            str(executable),
            "-i", str(input_path),
            "-o", str(ai_output),
            "-s", str(scale),
            "-t", "0",
            "-m", str(model_dir),
            "-n", REALESRGAN_MODEL,
            "-g", "0",
            "-j", "1:2:2",
            "-f", "png",
        ]
        result = subprocess.run(
            command,
            cwd=str(executable.parent),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15 * 60,
        )
        if result.returncode != 0 or not ai_output.exists() or ai_output.stat().st_size <= 0:
            detail = (result.stderr or result.stdout or "Real-ESRGAN failed").strip()
            raise RuntimeError(detail[-1200:])
        _save_upscaled_jpeg(ai_output, output_path, UPSCALE_TARGETS[mode])
    return {"engine": "real-esrgan", "scale": scale, "model": REALESRGAN_MODEL}


def _upscale_output_path(project: str, source: Path, mode: str) -> Path:
    stem = re.sub(r"_(2k|4k)(?:_\d{8}_\d{6})?$", "", source.stem, flags=re.IGNORECASE)
    return _unique_output_path(project, f"{stem}_{mode}", f"{stem}_{mode}")


def _upscale_thumbnail_file(source_path: Path, output_path: Path, mode: str, cfg: dict | None = None) -> dict:
    mode = _normalize_upscale_mode(mode)
    if mode == "original":
        raise ValueError("Upscale mode must be 2k or 4k")
    cfg = cfg or load_config()
    source_width, source_height = _image_dimensions(source_path)
    started = time.monotonic()
    warning = ""
    with _UPSCALE_LOCK:
        try:
            engine = _run_realesrgan(source_path, output_path, mode, cfg)
        except Exception as exc:
            warning = str(exc)[:900]
            _save_upscaled_jpeg(source_path, output_path, UPSCALE_TARGETS[mode])
            engine = {"engine": "lanczos-unsharp", "scale": 1, "model": "fallback"}
    width, height = _image_dimensions(output_path)
    return {
        **engine,
        "mode": mode,
        "width": width,
        "height": height,
        "source_width": source_width,
        "source_height": source_height,
        "elapsed_sec": round(time.monotonic() - started, 2),
        "warning": warning,
    }


def _upscale_existing_thumbnail(project: str, filename: str, mode: str, metadata: dict | None = None) -> dict:
    source = _resolve_thumb(project, filename)
    if not source.exists() or not source.is_file():
        raise HTTPException(404, "Thumbnail not found")
    mode = _normalize_upscale_mode(mode)
    if mode == "original":
        raise HTTPException(400, "Choose 2K or 4K to increase sharpness")
    output = _upscale_output_path(project, source, mode)
    upscale = _upscale_thumbnail_file(source, output, mode)
    source_item = next((item for item in _load_history(project) if item.get("filename") == filename), {})
    base = dict(source_item)
    if metadata:
        base.update(metadata)
    item = {
        **base,
        "filename": output.name,
        "saved_path": str(output),
        "url": f"/api/thumbnails/{project}/files/{output.name}",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_filename": base.get("source_filename") or source.name,
        "upscale_mode": mode,
        "upscale_engine": upscale["engine"],
        "upscale": upscale,
        "width": upscale["width"],
        "height": upscale["height"],
        "size_bytes": output.stat().st_size,
    }
    _save_history(project, item)
    return item


def _generate_events(body: StandaloneThumbnailRequest):
    title = re.sub(r"\s+", " ", body.title.strip())
    if not title:
        raise HTTPException(400, "Thumbnail title is required")
    body.title = title[:95]
    body.hook = re.sub(r"\s+", " ", (body.hook or "").strip())[:80]
    body.channel_name = re.sub(r"\s+", " ", (body.channel_name or "").strip())[:40]
    body.genre_label = re.sub(r"\s+", " ", (body.genre_label or "").strip())[:60]
    if body.formula not in FORMULAS:
        body.formula = "mystery"

    cfg = load_config()
    provider_cfg = image_provider_config(cfg)
    upscale_mode = _normalize_upscale_mode(body.upscale_mode)
    out = _unique_output_path(body.project, body.output_name, body.title)
    raw = out.with_suffix(".raw.png")
    prompt, prompt_mode, safe_level = _active_prompt(body)
    size = cfg.get("thumbnail_image_size") or provider_cfg.get("size") or "1280x720"
    reference_path = _resolve_reference_path(body.project, body.reference_image)
    reference_images: list[str] = []
    if reference_path:
        if provider_cfg.get("provider") == "gpt2api":
            provider_cfg["resolution"] = "1K"
            try:
                reference_images.append(_image_data_url(reference_path))
            except OSError:
                reference_images = []
        elif provider_cfg.get("provider") == "custom":
            reference_images.append(str(reference_path))

    yield {
        "type": "start",
        "status": "requesting",
        "progress": 1,
        "prompt_mode": prompt_mode,
        "safe_level": safe_level,
        "reference_template": str(reference_path) if reference_path else "",
        "reference_image": body.reference_image,
        "reference_attached": bool(reference_images),
    }
    for event in generate_image_events(prompt, raw, provider_cfg, size=size, model=body.model or None, reference_images=reference_images):
        if event.get("type") == "progress":
            yield {
                "type": "progress",
                "status": event.get("status", "running"),
                "progress": max(1, min(94, int(event.get("progress") or 1))),
            }
        elif event.get("type") in {"provider", "task"}:
            yield event

    yield {"type": "progress", "status": "finalizing", "progress": 96}
    _finalize_ai_thumbnail(raw, out, target_size=None)
    try:
        raw.unlink()
    except OSError:
        pass

    source_out = out
    source_width, source_height = _image_dimensions(source_out)
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
    if upscale_mode != "original":
        yield {"type": "progress", "status": "upscaling", "progress": 98, "target": upscale_mode}
        out = _upscale_output_path(body.project, source_out, upscale_mode)
        upscale = _upscale_thumbnail_file(source_out, out, upscale_mode, cfg)

    item = {
        "filename": out.name,
        "saved_path": str(out),
        "url": f"/api/thumbnails/{body.project}/files/{out.name}",
        "title": body.title,
        "session_id": _session_id(body.session_id),
        "hook": body.hook,
        "channel_name": body.channel_name,
        "genre_label": body.genre_label,
        "formula": body.formula,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "prompt": prompt,
        "prompt_mode": prompt_mode,
        "safe_level": safe_level,
        "reference_template": str(reference_path) if reference_path else "",
        "reference_image": body.reference_image,
        "reference_attached": bool(reference_images),
        "source_filename": source_out.name if source_out != out else "",
        "upscale_mode": upscale_mode,
        "upscale_engine": upscale["engine"],
        "upscale": upscale,
        "width": upscale["width"],
        "height": upscale["height"],
        "size_bytes": out.stat().st_size,
    }
    _save_history(body.project, item)
    yield {"type": "done", "status": "done", "progress": 100, "thumbnail": item}


@router.get("/api/thumbnails/{project}/items")
def list_thumbnails(project: str, session_id: str = "", story_title: str = ""):
    session_id = _session_id(session_id)
    history = _migrate_legacy_quick_items(project, session_id, story_title)
    items = []
    for item in history:
        if session_id and item.get("session_id") != session_id:
            continue
        path = _resolve_thumb(project, item.get("filename", ""))
        if path.exists():
            items.append(item)
    return {"project": project, "items": items}


@router.get("/api/thumbnails/{project}/files/{filename}")
def serve_thumbnail(project: str, filename: str):
    path = _resolve_thumb(project, filename)
    if not path.exists():
        raise HTTPException(404, "Thumbnail not found")
    return FileResponse(str(path), media_type="image/jpeg", filename=path.name)


@router.delete("/api/thumbnails/{project}/files/{filename}")
def delete_thumbnail(project: str, filename: str):
    path = _resolve_thumb(project, filename)
    if not path.exists():
        raise HTTPException(404, "Thumbnail not found")
    size_bytes = path.stat().st_size
    path.unlink()
    history = [item for item in _load_history(project) if item.get("filename") != filename]
    _write_history(project, history)
    return {"ok": True, "filename": filename, "deleted_bytes": size_bytes, "items": history}


@router.post("/api/thumbnails/{project}/upscale")
def upscale_thumbnail(project: str, body: ThumbnailUpscaleRequest):
    try:
        return _upscale_existing_thumbnail(project, body.filename, body.mode)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/thumbnails/{project}/template-reference")
def serve_thumbnail_template_reference(project: str):
    path = _template_reference_path(project)
    if not path:
        raise HTTPException(404, "Thumbnail reference not found")
    media_type = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    return FileResponse(str(path), media_type=media_type, filename=path.name)


@router.post("/api/thumbnails/{project}/reference")
async def upload_thumbnail_reference(project: str, file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(400, "Reference must be PNG, JPG, JPEG, or WEBP")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Reference image is empty")
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, "Reference image is too large; keep it under 20MB")
    filename = f"reference_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}{suffix}"
    path = _reference_dir(project) / filename
    path.write_bytes(data)
    return {
        "filename": filename,
        "reference_image": filename,
        "url": f"/api/thumbnails/{project}/reference-files/{filename}",
        "size_bytes": path.stat().st_size,
    }


@router.get("/api/thumbnails/{project}/reference-files/{filename}")
def serve_thumbnail_reference_file(project: str, filename: str):
    if not SAFE_REFERENCE_FILE.match(filename):
        raise HTTPException(400, "Invalid reference filename")
    path = _reference_dir(project) / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "Thumbnail reference not found")
    media_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "image/png")
    return FileResponse(str(path), media_type=media_type, filename=path.name)


@router.delete("/api/thumbnails/{project}/reference-files/{filename}")
def delete_thumbnail_reference_file(project: str, filename: str):
    if not SAFE_REFERENCE_FILE.match(filename):
        raise HTTPException(400, "Invalid reference filename")
    if not filename.startswith("reference_"):
        raise HTTPException(400, "Only uploaded reference images can be deleted")
    path = _reference_dir(project) / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(404, "Thumbnail reference not found")
    size_bytes = path.stat().st_size
    path.unlink()
    return {"ok": True, "filename": filename, "deleted_bytes": size_bytes}


@router.post("/api/thumbnails/safe-prompt")
def create_thumbnail_safe_prompt(body: ThumbnailSafePromptRequest):
    try:
        _project_dir(body.project)
        mode = (body.mode or "rule").strip().lower()
        level = max(1, min(5, int(body.level or 1)))
        cfg = load_config()
        if mode in {"llm", "safe_llm"}:
            prompt = _build_safe_llm_thumbnail_prompt(body, level, cfg)
            prompt_mode = "safe_llm"
        else:
            prompt = _build_safe_thumbnail_prompt(body, level)
            prompt_mode = "safe_rule"
        return {
            "project": body.project,
            "prompt": prompt,
            "prompt_mode": prompt_mode,
            "safe_level": level,
            "mode": mode,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/thumbnails/generate")
def generate_thumbnail(body: StandaloneThumbnailRequest):
    def event_stream():
        try:
            for event in _generate_events(body):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
