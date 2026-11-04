"""Subtitle preview/render helpers using ASS + FFmpeg."""
import json
import math
import re
import subprocess
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Generator

from video_service import _encoder_args, _parse_ffmpeg_time, get_video_duration, validate_video_integrity


DEFAULT_SUBTITLE_FONT = "JetBrains Mono NL SemiBold"

DEFAULT_STYLE = {
    "fontFamily": DEFAULT_SUBTITLE_FONT,
    "fontSize16x9": 54,
    "fontSize9x16": 72,
    "textColor": "#FFFFFF",
    "outlineColor": "#000000",
    "outlineWidth": 3,
    "backgroundEnabled": False,
    "backgroundColor": "#000000",
    "backgroundOpacity": 0.55,
    "bold": True,
    "align": "center",
    "position": "bottom",
    "marginX": 90,
    "marginY": 150,
    "maxWidthPct": 82,
    "lineHeight": 1.12,
    "maxCharsPerLine16x9": 42,
    "maxLines16x9": 2,
    "maxCueChars16x9": 110,
    "maxCueWords16x9": 24,
    "maxCharsPerLine9x16": 24,
    "maxLines9x16": 2,
    "maxCueChars9x16": 60,
    "maxCueWords9x16": 18,
}

DEFAULT_SUBTITLE_LAYOUT = {"x": 50.0, "y": 86.0, "widthPct": 82.0, "locked": False}

MAX_SUBTITLE_LINES = 2
MAX_CHARS_PER_LINE = {
    "16x9": 42,
    "9x16": 24,
}
MAX_CUE_CHARS = 60
MAX_CUE_WORDS = 18
MODE_CUE_LIMITS = {
    "16x9": {"max_chars": 110, "max_words": 24},
    "9x16": {"max_chars": MAX_CUE_CHARS, "max_words": MAX_CUE_WORDS},
}


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - math.floor(seconds)) * 100))
    if centis >= 100:
        secs += 1
        centis = 0
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _hex_to_ass(value: str, alpha: int = 0) -> str:
    value = (value or "#FFFFFF").strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if not re.match(r"^[0-9a-fA-F]{6}$", value):
        value = "FFFFFF"
    rr, gg, bb = value[0:2], value[2:4], value[4:6]
    alpha = max(0, min(255, int(alpha)))
    return f"&H{alpha:02X}{bb}{gg}{rr}"


def _escape_ass_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
    )


def split_text_blocks_by_words(text: str, max_chars: int, max_lines: int = MAX_SUBTITLE_LINES) -> list[str]:
    words = re.sub(r"\s+", " ", text or "").strip().split(" ")
    blocks: list[str] = []
    lines: list[str] = []
    current = ""
    for word in words:
        if not word:
            continue
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            if len(lines) >= max_lines:
                blocks.append("\\N".join(_escape_ass_text(line) for line in lines))
                lines = []
            current = word
    if current:
        lines.append(current)
    if lines:
        blocks.append("\\N".join(_escape_ass_text(line) for line in lines))
    return blocks


def _cue_word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def _cue_fits(text: str, max_chars: int, max_words: int) -> bool:
    return len(text) <= max_chars and _cue_word_count(text) <= max_words


def _split_sentences_for_cues(text: str) -> list[str]:
    """Keep terminal punctuation with its sentence for natural subtitle units."""
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return []
    return [
        item.strip()
        for item in re.split(r"(?<=[.!?…。！？])\s+|(?<=[。！？])", normalized)
        if item.strip()
    ]


def _split_long_sentence_at_pauses(sentence: str, max_chars: int, max_words: int) -> list[str]:
    """Split an oversized sentence at a comma/semicolon/colon where possible.

    Word-level splitting is deliberately the final fallback only. This prevents a
    cue from ending halfway through a normal sentence unless that sentence simply
    cannot fit on screen.
    """
    tokens = re.findall(r"\S+", sentence or "")
    chunks: list[str] = []
    start = 0
    while start < len(tokens):
        end = start
        last_pause = -1
        while end < len(tokens):
            candidate = " ".join(tokens[start:end + 1])
            if end > start and not _cue_fits(candidate, max_chars, max_words):
                break
            if re.search(r"[,;:—–]$", tokens[end]):
                last_pause = end + 1
            end += 1

        if end >= len(tokens):
            chunks.append(" ".join(tokens[start:]).strip())
            break

        # Prefer the final natural pause that still fits. If there is none,
        # retain all fitting words rather than dropping or reordering speech.
        cut = last_pause if last_pause > start else max(start + 1, end)
        chunks.append(" ".join(tokens[start:cut]).strip())
        start = cut
    return [chunk for chunk in chunks if chunk]


def split_text_into_cue_blocks(text: str, max_chars: int = MAX_CUE_CHARS, max_words: int = MAX_CUE_WORDS) -> list[str]:
    """Create readable subtitle cues without breaking ordinary sentences.

    A complete sentence remains one cue whenever it fits. Short sentences are
    paired with the following sentence. Only an oversized sentence is split,
    preferring commas and other natural pauses.
    """
    blocks: list[str] = []
    pending = ""
    short_threshold = max(18, int(max_chars * 0.48))

    def flush_pending() -> None:
        nonlocal pending
        if pending:
            blocks.append(pending)
            pending = ""

    for sentence in _split_sentences_for_cues(text):
        pieces = [sentence] if _cue_fits(sentence, max_chars, max_words) else _split_long_sentence_at_pauses(sentence, max_chars, max_words)
        for piece in pieces:
            if pending:
                combined = f"{pending} {piece}"
                if _cue_fits(combined, max_chars, max_words):
                    pending = combined
                    # A completed, readable sentence should not be held back
                    # merely to fill the box. Only short fragments are paired.
                    if len(pending) >= short_threshold:
                        flush_pending()
                    continue
                flush_pending()

            if _cue_fits(piece, max_chars, max_words) and len(piece) < short_threshold:
                pending = piece
            else:
                blocks.append(piece)
    flush_pending()
    return blocks


def build_subtitle_cues(
    timing: dict,
    max_chars: int = MAX_CUE_CHARS,
    max_words: int = MAX_CUE_WORDS,
    use_cached: bool = True,
) -> list[dict]:
    if use_cached and timing.get("subtitle_cues"):
        return list(timing.get("subtitle_cues") or [])
    cues: list[dict] = []
    for caption in timing.get("captions", []):
        blocks = split_text_into_cue_blocks(caption.get("text", ""), max_chars=max_chars, max_words=max_words)
        if not blocks:
            continue
        start = float(caption.get("start_sec", 0) or 0)
        end = float(caption.get("end_sec", start) or start)
        duration = max(0.01, end - start)
        weights = [max(1, len(block)) for block in blocks]
        total_weight = sum(weights) or 1
        cursor = start
        for index, (block, weight) in enumerate(zip(blocks, weights)):
            cue_end = end if index == len(blocks) - 1 else min(end, cursor + duration * (weight / total_weight))
            cues.append({
                "index": len(cues) + 1,
                "text": block,
                "start_sec": round(cursor, 3),
                "end_sec": round(cue_end, 3),
                "duration_sec": round(max(0.01, cue_end - cursor), 3),
            })
            cursor = cue_end
    return cues


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, int(round(float(seconds or 0) * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds_part, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds_part:02d},{millis:03d}"


def subtitle_cues_to_srt(cues: list[dict]) -> str:
    """Serialize the exact cue list used for rendering as a UTF-8 SRT file."""
    blocks = []
    for index, cue in enumerate(cues or [], start=1):
        text = str(cue.get("text") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            continue
        start = _srt_time(cue.get("start_sec", 0))
        end = _srt_time(cue.get("end_sec", cue.get("start_sec", 0)))
        blocks.append(f"{index}\r\n{start} --> {end}\r\n{text}")
    return "\ufeff" + "\r\n\r\n".join(blocks) + ("\r\n" if blocks else "")


def wrap_cue_text_for_ass(text: str, max_chars: int, max_lines: int = MAX_SUBTITLE_LINES) -> str:
    blocks = split_text_blocks_by_words(text, max_chars, max_lines=max_lines)
    if not blocks:
        return ""
    if len(blocks) > 1:
        lines = " ".join(block.replace("\\N", " ") for block in blocks).split()
        midpoint = max(1, len(lines) // 2)
        return "\\N".join([
            _escape_ass_text(" ".join(lines[:midpoint])),
            _escape_ass_text(" ".join(lines[midpoint:])),
        ])
    return blocks[0]


def split_caption_for_ass(caption: dict, max_chars: int) -> list[dict]:
    blocks = split_text_blocks_by_words(caption.get("text", ""), max_chars)
    if not blocks:
        return []
    start = float(caption.get("start_sec", 0) or 0)
    end = float(caption.get("end_sec", start) or start)
    duration = max(0.01, end - start)
    plain_weights = [max(1, len(block.replace("\\N", " "))) for block in blocks]
    total_weight = sum(plain_weights) or 1
    cursor = start
    expanded = []
    for index, (block, weight) in enumerate(zip(blocks, plain_weights)):
        if index == len(blocks) - 1:
            block_end = end
        else:
            block_end = min(end, cursor + duration * (weight / total_weight))
        expanded.append({"start_sec": cursor, "end_sec": block_end, "text": block})
        cursor = block_end
    return expanded


def _alignment(position: str, align: str) -> int:
    horizontal = {"left": 1, "center": 2, "right": 3}.get(align, 2)
    vertical_base = {"bottom": 0, "middle": 3, "top": 6}.get(position, 0)
    return vertical_base + horizontal


def _subtitle_layout(style: dict, suffix: str) -> dict:
    raw = style.get(f"subtitleLayout{suffix}") if isinstance(style, dict) else None
    if not isinstance(raw, dict):
        legacy_position = (style or {}).get(f"subtitlePosition{suffix}")
        legacy_position = legacy_position if isinstance(legacy_position, dict) else {}
        raw = {
            "x": legacy_position.get("x", DEFAULT_SUBTITLE_LAYOUT["x"]),
            "y": legacy_position.get("y", DEFAULT_SUBTITLE_LAYOUT["y"]),
            "widthPct": (style or {}).get(f"maxWidthPct{suffix}", (style or {}).get("maxWidthPct", DEFAULT_SUBTITLE_LAYOUT["widthPct"])),
            "locked": False,
        }
    try:
        return {
            "x": max(3.0, min(97.0, float(raw.get("x", DEFAULT_SUBTITLE_LAYOUT["x"])))),
            "y": max(3.0, min(97.0, float(raw.get("y", DEFAULT_SUBTITLE_LAYOUT["y"])))),
            "widthPct": max(30.0, min(95.0, float(raw.get("widthPct", DEFAULT_SUBTITLE_LAYOUT["widthPct"])))),
            "locked": bool(raw.get("locked", False)),
        }
    except (TypeError, ValueError):
        return dict(DEFAULT_SUBTITLE_LAYOUT)


def _style_for_mode(style: dict, mode: str) -> dict:
    merged = {**DEFAULT_STYLE, **(style or {})}
    suffix = "9x16" if mode == "9x16" else "16x9"
    font_size = int(merged.get(f"fontSize{suffix}") or 54)
    width = 1080 if mode == "9x16" else 1920
    layout = _subtitle_layout(style or {}, suffix)
    merged["subtitleLayout"] = layout
    merged["maxWidthPct"] = layout["widthPct"]
    max_width = width * (merged["maxWidthPct"] / 100)
    avg_char_width = max(1.0, font_size * 0.56)
    merged["fontSize"] = font_size
    merged["fontFamily"] = (str(merged.get("fontFamily") or "").strip() or DEFAULT_SUBTITLE_FONT)
    configured_wrap = int(merged.get(f"maxCharsPerLine{suffix}") or MAX_CHARS_PER_LINE.get(mode, 34))
    merged["wrapChars"] = max(8, min(configured_wrap, int(max_width / avg_char_width)))
    merged["maxLines"] = max(1, min(4, int(merged.get(f"maxLines{suffix}") or MAX_SUBTITLE_LINES)))
    defaults = MODE_CUE_LIMITS.get(mode, MODE_CUE_LIMITS["9x16"])
    merged["cueMaxChars"] = max(12, min(180, int(merged.get(f"maxCueChars{suffix}") or defaults["max_chars"])))
    merged["cueMaxWords"] = max(2, min(36, int(merged.get(f"maxCueWords{suffix}") or defaults["max_words"])))
    merged["subtitlePosition"] = {"x": layout["x"], "y": layout["y"]}
    return merged


def _custom_position(mode_style: dict, width: int, height: int) -> tuple[int, int] | None:
    position = mode_style.get("subtitlePosition")
    if not isinstance(position, dict):
        return None
    try:
        x = float(position.get("x"))
        y = float(position.get("y"))
    except (TypeError, ValueError):
        return None
    return (
        int(round(max(0.0, min(100.0, x)) * width / 100)),
        int(round(max(0.0, min(100.0, y)) * height / 100)),
    )


def _ass_round_rect_path(width: int, height: int, radius: int) -> str:
    """ASS vector drawing path for a filled rounded subtitle background."""
    radius = max(0, min(radius, width // 2, height // 2))
    if radius == 0:
        return f"m 0 0 l {width} 0 l {width} {height} l 0 {height}"
    right = width
    bottom = height
    # Cubic curves keep the corners smooth in libass (kappa approximation).
    curve = int(round(radius * 0.5523))
    return (
        f"m {radius} 0 l {right - radius} 0 "
        f"b {right - radius + curve} 0 {right} {radius - curve} {right} {radius} "
        f"l {right} {bottom - radius} "
        f"b {right} {bottom - radius + curve} {right - radius + curve} {bottom} {right - radius} {bottom} "
        f"l {radius} {bottom} "
        f"b {radius - curve} {bottom} 0 {bottom - radius + curve} 0 {bottom - radius} "
        f"l 0 {radius} b 0 {radius - curve} {radius - curve} 0 {radius} 0"
    )


def _estimated_ass_line_width(text: str, font_size: int, latin_em: float, cjk_em: float) -> float:
    """Estimate a libass line width without treating every Unicode glyph as Latin.

    Korean, Japanese and Chinese glyphs are rendered substantially wider than
    the compact Latin estimate used by the preview.  A per-glyph estimate keeps
    the rounded vector background behind the rendered text in multilingual
    videos while preserving the existing tight Latin sizing.
    """
    units = 0.0
    for char in text:
        if unicodedata.combining(char):
            continue
        units += cjk_em if unicodedata.east_asian_width(char) in {"W", "F"} else latin_em
    return units * font_size


def _rounded_background_dialogues(text: str, mode_style: dict, width: int, height: int) -> tuple[str, str]:
    """Return a vector-background dialogue and positioned text dialogue overrides.

    ASS has no rounded-box style.  We therefore draw the background as a vector
    for each cue, then position normal text over it.  The sizing deliberately
    mirrors the preview's CSS width estimate and compact px-2/py-1 padding.
    """
    lines = [line for line in text.split("\\N") if line] or [text]
    font_size = max(12, int(mode_style["fontSize"]))
    line_height = max(font_size, int(round(font_size * float(mode_style.get("lineHeight", 1.12)))))
    # Fonts can fall back when the selected family is unavailable to libass.
    # Korean/Japanese/Chinese fallback glyphs need a wider reserve than Latin.
    outline = max(0, int(mode_style.get("outlineWidth", 3)))
    latin_em = 0.62 if height > width else 0.52
    cjk_em = 0.82 if height > width else 0.78
    text_width = max(
        _estimated_ass_line_width(line, font_size, latin_em, cjk_em)
        for line in lines
    ) + outline * 2
    padding_x = max(8, int(round(font_size * 0.22)) + outline)
    padding_y = max(4, int(round(font_size * 0.12)))
    margin_x = max(0, int(mode_style.get("marginX", 90)))
    margin_y = max(0, int(mode_style.get("marginY", 150)))
    box_width = min(width - margin_x * 2, max(1, int(round(text_width + padding_x * 2))))
    box_height = max(1, len(lines) * line_height + padding_y * 2)
    align = str(mode_style.get("align") or "center")
    position = str(mode_style.get("position") or "bottom")
    custom_position = _custom_position(mode_style, width, height)

    if custom_position:
        box_x = max(0, min(width - box_width, custom_position[0] - box_width // 2))
        box_y = max(0, min(height - box_height, custom_position[1] - box_height // 2))
        text_alignment = 8
        text_x = box_x + box_width // 2
    elif align == "left":
        box_x = margin_x
        text_alignment = 7
        text_x = box_x + padding_x
    elif align == "right":
        box_x = width - margin_x - box_width
        text_alignment = 9
        text_x = box_x + box_width - padding_x
    else:
        box_x = (width - box_width) // 2
        text_alignment = 8
        text_x = box_x + box_width // 2
    if custom_position:
        pass
    elif position == "top":
        box_y = margin_y
    elif position == "middle":
        box_y = (height - box_height) // 2
    else:
        box_y = height - margin_y - box_height
    text_y = box_y + padding_y

    background = _hex_to_ass(mode_style.get("backgroundColor"), 0)
    bgr = background[-6:]
    opacity = max(0.0, min(1.0, float(mode_style.get("backgroundOpacity", 0.55))))
    alpha = int(round((1 - opacity) * 255))
    radius = max(4, int(round(font_size * 0.16)))
    shape = (
        f"{{\\an7\\pos({box_x},{box_y})\\p1\\bord0\\shad0"
        f"\\1c&H{bgr}&\\1a&H{alpha:02X}&}}{_ass_round_rect_path(box_width, box_height, radius)}"
    )
    text_override = f"{{\\an{text_alignment}\\pos({text_x},{text_y})}}{text}"
    return shape, text_override


def create_ass_file(timing: dict, style: dict, path: Path, mode: str, cues: list[dict] | None = None) -> Path:
    mode_style = _style_for_mode(style, mode)
    width, height = (1080, 1920) if mode == "9x16" else (1920, 1080)
    outline = max(0, int(mode_style.get("outlineWidth", 3)))
    # Rounded backgrounds are rendered per cue as vector shapes below. ASS
    # BorderStyle=3 only supports a hard rectangular box and does not match UI preview.
    use_rounded_background = bool(mode_style.get("backgroundEnabled"))
    border_style = 1
    back_alpha = int((1 - float(mode_style.get("backgroundOpacity", 0.55))) * 255)
    align = _alignment(mode_style.get("position"), mode_style.get("align"))
    margin_x = max(0, int(mode_style.get("marginX", 90)))
    margin_y = max(0, int(mode_style.get("marginY", 150)))
    primary = _hex_to_ass(mode_style.get("textColor"), 0)
    outline_color = _hex_to_ass(mode_style.get("outlineColor"), 0)
    back = _hex_to_ass(mode_style.get("backgroundColor"), back_alpha)
    bold = -1 if mode_style.get("bold") else 0
    custom_position = _custom_position(mode_style, width, height)

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 0",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        (
            "Style: Default,"
            f"{mode_style['fontFamily']},{mode_style['fontSize']},"
            f"{primary},{primary},{outline_color},{back},{bold},0,0,0,100,100,0,0,"
            f"{border_style},{outline},0,{align},{margin_x},{margin_x},{margin_y},1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in cues or build_subtitle_cues(timing):
        start = _ass_time(cue.get("start_sec", 0))
        end = _ass_time(cue.get("end_sec", 0))
        text = wrap_cue_text_for_ass(cue.get("text", ""), mode_style["wrapChars"], mode_style["maxLines"])
        if text:
            if use_rounded_background:
                shape, positioned_text = _rounded_background_dialogues(text, mode_style, width, height)
                lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{shape}")
                lines.append(f"Dialogue: 1,{start},{end},Default,,0,0,0,,{positioned_text}")
            else:
                override = f"{{\\an5\\pos({custom_position[0]},{custom_position[1]})}}" if custom_position else ""
                lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{override}{text}")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _filter_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    value = value.replace(":", "\\:").replace("'", "\\'")
    return value


def _run_ffmpeg_progress(
    cmd: list[str],
    output_path: Path,
    total_duration: float,
    percent_start: int,
    percent_span: int,
    label: str,
    message: str = "",
) -> Generator[dict, None, int]:
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
            percent = min(99, percent_start + int(ratio * percent_span))
            size_mb = output_path.stat().st_size / (1024 * 1024) if output_path.exists() else 0
            yield {
                "type": "progress",
                "step": label,
                "message": message or f"Đang render {label}...",
                "percent": percent,
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
            "message": " ".join(stderr_tail[-10:]) or "FFmpeg subtitle render failed",
        }
    return proc.returncode or 0


@lru_cache(maxsize=4)
def _cuda_hybrid_available(ffmpeg_path: str) -> bool:
    """Return whether this FFmpeg can safely use CUDA around a CPU ASS filter."""
    try:
        filters = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        ).stdout
        hwaccels = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-hwaccels"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    required_filters = {"hwdownload", "hwupload_cuda"}
    return "cuda" in hwaccels.lower() and all(item in filters for item in required_filters)


def _cuda_hybrid_filter(video_filter: str) -> str:
    """Use GPU decode/encode while preserving libass on CPU for exact subtitles."""
    return f"hwdownload,format=nv12,{video_filter},format=nv12,hwupload_cuda"


def _validate_final_video(
    ffmpeg_path: str,
    path: Path,
    duration: float,
    percent_start: int,
    percent_span: int,
    mode: str,
) -> Generator[dict, None, bool]:
    """Fully decode the final output and surface real progress instead of freezing UI."""
    if not path.exists() or path.stat().st_size == 0:
        yield {"type": "ffmpeg_failed", "message": f"Final {mode} output is missing or empty"}
        return False
    command = [
        ffmpeg_path, "-v", "error", "-xerror", "-progress", "pipe:2", "-nostats",
        "-i", str(path), "-map", "0", "-f", "null", "-",
    ]
    failed = False
    for event in _run_ffmpeg_progress(
        command,
        path,
        duration,
        percent_start,
        percent_span,
        f"verify_{mode}",
        f"Đang kiểm tra toàn bộ file {mode}...",
    ):
        if event.get("type") == "ffmpeg_failed":
            failed = True
        yield event
    return not failed


def _video_filter_for_mode(ass_path: Path | None, mode: str, vertical_video_mode: str = "crop") -> str:
    """Build mode-specific scaling with an optional subtitle layer."""
    subtitle_filter = f"subtitles='{_filter_path(ass_path)}'" if ass_path else ""
    if mode != "9x16":
        return subtitle_filter
    suffix = f",{subtitle_filter}" if subtitle_filter else ""
    if vertical_video_mode == "fit":
        # Preserve the complete 16:9 frame on a 9:16 canvas rather than zooming it.
        return (
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1{suffix}"
        )
    return (
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,setsar=1{suffix}"
    )


def _render_one(
    ffmpeg_path: str,
    input_path: Path,
    output_path: Path,
    ass_path: Path | None,
    mode: str,
    video_encoder: str,
    quality: str,
    duration: float,
    percent_start: int,
    percent_span: int,
    vertical_video_mode: str = "crop",
    background_music: Path | None = None,
    background_music_volume: float = 0.15,
    use_cuda: bool | None = None,
    burn_subtitles: bool = True,
) -> Generator[dict, None, bool]:
    encoder_args, encoder_label = _encoder_args(video_encoder, quality)
    vf = _video_filter_for_mode(ass_path, mode, vertical_video_mode)
    if use_cuda is None:
        use_cuda = video_encoder == "nvidia_nvenc" and _cuda_hybrid_available(ffmpeg_path)
    if use_cuda and vf:
        vf = _cuda_hybrid_filter(vf)
        encoder_label = "NVIDIA NVENC + CUDA decode (subtitle CPU)" if burn_subtitles else "NVIDIA NVENC + CUDA decode"
    hardware_acceleration = "cuda_hybrid" if use_cuda else ("cpu" if video_encoder == "cpu_x264" else "nvenc_standard")
    use_music = background_music is not None and background_music.is_file()
    cmd = [ffmpeg_path, "-y"]
    if use_cuda:
        cmd.extend(["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"])
    cmd.extend(["-i", str(input_path)])
    if use_music:
        cmd.extend(["-stream_loop", "-1", "-i", str(background_music)])
    if vf:
        cmd.extend(["-vf", vf])
    cmd.extend(["-map", "0:v:0", *encoder_args])
    if use_music:
        volume = max(0.0, min(1.0, float(background_music_volume)))
        audio_mix = (
            f"[0:a]volume=1[narration];[1:a]volume={volume:.4f}[music];"
            "[narration][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
        )
        cmd.extend(["-filter_complex", audio_mix, "-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-shortest"])
    else:
        cmd.extend(["-map", "0:a?", "-c:a", "copy"])
    cmd.extend(["-movflags", "+faststart", "-progress", "pipe:2", "-nostats", str(output_path)])
    yield {
        "type": "step",
        "step": mode,
        "message": f"Render phụ đề{' + nhạc nền' if use_music else ''} {mode} bằng {encoder_label}...",
        "encoder_label": encoder_label,
        "hardware_acceleration": hardware_acceleration,
    }
    events = _run_ffmpeg_progress(cmd, output_path, duration, percent_start, percent_span, mode)
    failed = False
    for event in events:
        if event.get("type") == "ffmpeg_failed":
            failed = True
        yield event
    if failed or not output_path.exists() or output_path.stat().st_size == 0:
        if use_cuda:
            yield {
                "type": "step",
                "step": mode,
                "message": "CUDA hybrid không tương thích với video này, chuyển sang NVENC chuẩn...",
                "encoder_label": "NVIDIA NVENC",
                "hardware_acceleration": "nvenc_standard",
            }
            yield from _render_one(
                ffmpeg_path, input_path, output_path, ass_path, mode, video_encoder, quality,
                duration, percent_start, percent_span, vertical_video_mode,
                background_music, background_music_volume, False, burn_subtitles,
            )
            return
        if video_encoder != "cpu_x264":
            yield {
                "type": "step",
                "step": mode,
                "message": f"{encoder_label} lỗi, chuyển sang CPU libx264...",
                "encoder_label": "CPU libx264",
            }
            yield from _render_one(
                ffmpeg_path,
                input_path,
                output_path,
                ass_path,
                mode,
                "cpu_x264",
                quality,
                duration,
                percent_start,
                percent_span,
                vertical_video_mode,
                background_music,
                background_music_volume,
                False,
                burn_subtitles,
            )
            return
        return False
    return True


def render_subtitle_videos(
    project_dir: Path,
    input_video: Path,
    timing: dict,
    style: dict,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    video_encoder: str = "cpu_x264",
    quality: str = "high",
    overwrite_mode: str = "version",
    background_music: Path | None = None,
    background_music_volume: float = 0.15,
    render_modes: list[str] | None = None,
    vertical_video_mode: str = "crop",
    burn_subtitles: bool = True,
) -> Generator[dict, None, None]:
    yield {"type": "step", "step": "source_verify", "message": "Đang kiểm tra toàn bộ video nguồn trước khi xuất..."}
    input_valid, input_error = validate_video_integrity(ffmpeg_path, input_video)
    if not input_valid:
        yield {"type": "error", "message": f"Video nguồn bị lỗi, cần dựng lại trước khi chèn phụ đề: {input_error}"}
        return
    duration = get_video_duration(input_video, ffprobe_path)
    if duration <= 0:
        duration = float(timing.get("total_duration_sec") or 1)

    requested_modes = render_modes if render_modes is not None else ["16x9", "9x16"]
    modes = [mode for mode in ("16x9", "9x16") if mode in requested_modes]
    if not modes:
        yield {"type": "error", "message": "Choose at least one subtitle output"}
        return

    stem = input_video.stem
    output_kind = "subtitled" if burn_subtitles else "clean"
    outputs = {mode: project_dir / f"{stem}_{mode}_{output_kind}.mp4" for mode in modes}
    if overwrite_mode != "overwrite":
        for key, path in list(outputs.items()):
            candidate, index = path, 2
            while candidate.exists():
                candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
                index += 1
            outputs[key] = candidate

    ass_files: dict[str, Path | None] = {mode: None for mode in modes}
    if burn_subtitles:
        for mode in modes:
            mode_style = _style_for_mode(style, mode)
            cues = build_subtitle_cues(
                timing,
                max_chars=mode_style["cueMaxChars"],
                max_words=mode_style["cueMaxWords"],
                use_cached=False,
            )
            ass_files[mode] = create_ass_file(timing, style, project_dir / f"{stem}_{mode}.ass", mode, cues)

    use_music = background_music is not None and background_music.is_file()
    # The source is fully validated up front.  Each finished output is then
    # fully decoded once.  Keeping validation at the final boundary preserves
    # reliability while avoiding the former subtitle->music intermediate pass.
    work_budget = 92
    mode_budget = max(1, work_budget // len(modes))
    verify_span = max(8, round(mode_budget * 0.28))
    render_span = max(1, mode_budget - verify_span)
    for index, mode in enumerate(modes):
        mode_start = 4 + index * mode_budget
        for event in _render_one(
            ffmpeg_path, input_video, outputs[mode], ass_files[mode], mode,
            video_encoder, quality, duration, mode_start, render_span, vertical_video_mode,
            background_music if use_music else None,
            background_music_volume,
            None,
            burn_subtitles,
        ):
            if isinstance(event, dict):
                yield event
        if not outputs[mode].exists() or outputs[mode].stat().st_size == 0:
            yield {"type": "error", "message": f"Video render failed for {mode}"}
            return
        validation_failed = False
        for event in _validate_final_video(
            ffmpeg_path, outputs[mode], duration,
            mode_start + render_span, verify_span, mode,
        ):
            if event.get("type") == "ffmpeg_failed":
                validation_failed = True
            yield event
        if validation_failed:
            yield {"type": "error", "message": f"Final {mode} video integrity check failed"}
            return

    yield {
        "type": "done",
        "percent": 100,
        "outputs": {mode: path.name for mode, path in outputs.items()},
        "size_mb": {mode: round(path.stat().st_size / (1024 * 1024), 2) for mode, path in outputs.items()},
    }


def load_timing_file(project_dir: Path, audio_file: str) -> dict | None:
    if audio_file.endswith("_FULL.mp3"):
        timing_name = audio_file.replace("_FULL.mp3", "_timing.json")
    else:
        timing_name = f"{Path(audio_file).stem}_timing.json"
    path = project_dir / timing_name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
