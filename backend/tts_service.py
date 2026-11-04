"""TTS service logic extracted from lucylab_tts.py"""
import re
import os
import json
import time
import shutil
import subprocess
import hashlib
import requests
from pathlib import Path
from typing import Generator
from concurrent.futures import ThreadPoolExecutor, as_completed
from tts_providers import SynthesisRequest, TTSProviderError, resolve_tts_profile

BASE_DIR = Path(__file__).parent.parent
OUTPUT_DIR = BASE_DIR / "output_audio"
TRASH_DIR = OUTPUT_DIR / "_trash"
TEMP_DIR = BASE_DIR / "temp_audio"
CONFIG_FILE = BASE_DIR / "config.json"
SAFE_PROJECT_NAME = re.compile(r'^(?!\.{1,2}$)[^\x00-\x1f<>:"/\\|?*]+$')
PROJECT_STORAGE_DIR = OUTPUT_DIR / "_projects"
PROJECT_METADATA_FILE = ".project.json"
_WINDOWS_RESERVED_PROJECT_NAMES = {
    "CON", "PRN", "AUX", "NUL", "CLOCK$",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
LUCYLAB_API = "https://api.lucylab.io/json-rpc"

# Long requests are less reliable with the narration provider: it can repeat or
# skip phrases, and a single 40-second timing anchor makes subtitles visibly
# drift. Keep narration requests short enough to be individually dependable.
TTS_TIMING_MIN_CHARS = 120
TTS_TIMING_MAX_CHARS = 260

# Narrative structure stays in the editor for reviewers, but Markdown headings
# and standalone chapter labels are not narration and must never reach
# TTS/timing/subtitle generation.
_MARKDOWN_HEADING_RE = re.compile(r"^\s*#")
_CHAPTER_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:chương|chapter|phần|hồi)\s+"
    r"(?:\d+|[ivxlcdm]+|một|hai|ba|bốn|năm|sáu|bảy|tám|chín|mười)"
    r"(?:\s*[:.\-–—]\s*.*)?\s*$",
    re.IGNORECASE,
)


def is_project_title(project: str) -> bool:
    """Validate a user-visible title without treating it as a filesystem path."""
    if not isinstance(project, str):
        return False
    title = project.strip()
    return bool(title and title not in {".", ".."} and "\x00" not in title and "/" not in title and "\\" not in title)


class _ProjectTitleValidator:
    """Compatibility adapter for existing `.match()` validation call sites."""
    def match(self, value: str) -> bool:
        return is_project_title(value)


PROJECT_TITLE_VALIDATOR = _ProjectTitleValidator()


def _legacy_project_directory_name(title: str) -> bool:
    if not SAFE_PROJECT_NAME.fullmatch(title) or title.startswith("_") or title[-1:] in {" ", "."}:
        return False
    return title.split(".", 1)[0].upper() not in _WINDOWS_RESERVED_PROJECT_NAMES


def _managed_project_dir(title: str) -> Path:
    digest = hashlib.sha256(title.encode("utf-8")).hexdigest()[:24]
    return PROJECT_STORAGE_DIR / digest


def _project_metadata_path(project_dir: Path) -> Path:
    return project_dir / PROJECT_METADATA_FILE


def write_project_title(project_dir: Path, title: str) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    _project_metadata_path(project_dir).write_text(json.dumps({"title": title}, ensure_ascii=False), encoding="utf-8")


def project_uses_managed_storage(title: str) -> bool:
    return not _legacy_project_directory_name(title)


def get_project_dir(project: str) -> Path:
    if not project:
        return OUTPUT_DIR
    title = str(project).strip()
    if not is_project_title(title):
        raise ValueError("Invalid project title")
    return OUTPUT_DIR / title if _legacy_project_directory_name(title) else _managed_project_dir(title)


def list_projects() -> list[str]:
    OUTPUT_DIR.mkdir(exist_ok=True)
    titles = [d.name for d in OUTPUT_DIR.iterdir() if d.is_dir() and not d.name.startswith("_")]
    if PROJECT_STORAGE_DIR.is_dir():
        for project_dir in PROJECT_STORAGE_DIR.iterdir():
            if not project_dir.is_dir():
                continue
            try:
                title = json.loads(_project_metadata_path(project_dir).read_text(encoding="utf-8")).get("title")
            except (OSError, json.JSONDecodeError):
                title = ""
            if is_project_title(title):
                titles.append(title)
    return sorted(set(titles), key=str.casefold)


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    return {
        "jwt_token": "",
        "voice_id": "",
        "speed": 1.0,
        "max_chars": 2500,
        "workers": 3,
        "delay": 1.0,
        "sentence_gap": 0.3,
        "optimize_text": False,
        "trim_silence": False,
        "silence_db": -35.0,
    }


def save_config(config: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def channel_profile_block(cfg: dict) -> str:
    """Format the Channel Strategy Profile for prompt injection. Empty -> ''."""
    fields = [
        ("Channel positioning", cfg.get("channel_positioning")),
        ("Target audience", cfg.get("channel_audience")),
        ("Content pillars", cfg.get("channel_pillars")),
        ("Banned title patterns (never use)", cfg.get("channel_banned_title_patterns")),
        ("Banned hashtags (never use)", cfg.get("channel_banned_hashtags")),
    ]
    parts = [f"{label}:\n{str(v).strip()}" for label, v in fields if str(v or "").strip()]
    if not parts:
        return ""
    return "CHANNEL STRATEGY PROFILE (follow strictly):\n" + "\n\n".join(parts)


def optimize_text_for_tts(text: str) -> str:
    text = re.sub(r'([.!?…])\s*\n+', r'\1 ', text)
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()


def strip_chapter_headings_for_tts(text: str) -> str:
    """Remove Markdown headings/chapter labels while preserving narration."""
    if not text:
        return ""
    kept = [
        line for line in text.splitlines()
        if not _MARKDOWN_HEADING_RE.match(line)
        and not _CHAPTER_HEADING_RE.match(line)
    ]
    # Avoid turning a removed heading into an audible/visual blank gap.
    cleaned = "\n".join(kept)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def split_text(text: str, max_chars: int = 2500) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    segments: list[str] = []
    current = ""
    sentences = re.split(r"(?<=[.!?…。！？])\s+|(?<=[。！？])", text)

    for sentence in sentences:
        if len(current) + len(sentence) <= max_chars:
            current += (" " if current else "") + sentence
        else:
            if current.strip():
                segments.append(current.strip())
            if len(sentence) > max_chars:
                segments.extend(_split_long(sentence, max_chars))
                current = ""
            else:
                current = sentence

    if current.strip():
        segments.append(current.strip())

    return segments


def split_text_for_timing(text: str, max_chars: int = 2500) -> list[str]:
    """Split text into short speech units so subtitle timing can follow narration."""
    unit_limit = max(120, min(int(max_chars or 2500), 900))
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    sentences = re.split(r"(?<=[.!?…。！？])\s+|(?<=[。！？])", normalized)
    units: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > unit_limit:
            if current:
                units.append(current.strip())
                current = ""
            units.extend(_split_long(sentence, unit_limit))
            continue
        next_text = f"{current} {sentence}".strip() if current else sentence
        if len(next_text) <= unit_limit:
            current = next_text
        else:
            if current:
                units.append(current.strip())
            current = sentence
    if current:
        units.append(current.strip())
    return units


def _split_long_at_natural_pauses(sentence: str, max_chars: int) -> list[str]:
    """Split an oversized narration sentence at a pause before a hard word cut."""
    words = sentence.split()
    if not words:
        return []

    parts: list[str] = []
    start = 0
    pause_suffixes = (",", ";", ":", "…", "—", "–")
    while start < len(words):
        end = start
        last_pause = -1
        while end < len(words):
            candidate = " ".join(words[start:end + 1])
            if len(candidate) > max_chars and end > start:
                break
            if words[end].endswith(pause_suffixes):
                last_pause = end + 1
            end += 1

        if end >= len(words):
            parts.append(" ".join(words[start:]).strip())
            break

        # Prefer the latest comma/semicolon/colon pause inside this cue. If
        # none exists, retain the normal word boundary instead of truncating.
        cut = last_pause if last_pause > start else max(start + 1, end)
        parts.append(" ".join(words[start:cut]).strip())
        start = cut
    return [part for part in parts if part]


def split_text_for_tts_timing(text: str, max_chars: int = TTS_TIMING_MAX_CHARS) -> list[str]:
    """Return short, natural narration units used as both TTS and timing anchors.

    ``max_chars`` remains configurable, but is intentionally capped. This
    prevents an accidental legacy setting such as 5,000 characters from sending
    a 40-second narration request to the provider.
    """
    unit_limit = max(TTS_TIMING_MIN_CHARS, min(int(max_chars or TTS_TIMING_MAX_CHARS), TTS_TIMING_MAX_CHARS))
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []

    sentences = re.split(r"(?<=[.!?…。！？])\s+|(?<=[。！？])", normalized)
    units: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > unit_limit:
            if current:
                units.append(current.strip())
                current = ""
            units.extend(_split_long_at_natural_pauses(sentence, unit_limit))
            continue
        next_text = f"{current} {sentence}".strip() if current else sentence
        if len(next_text) <= unit_limit:
            current = next_text
        else:
            if current:
                units.append(current.strip())
            current = sentence
    if current:
        units.append(current.strip())
    return units


def _split_long(sentence: str, max_chars: int) -> list[str]:
    parts: list[str] = []
    words = sentence.split()
    current = ""
    for word in words:
        if len(current) + len(word) + 1 <= max_chars:
            current += (" " if current else "") + word
        else:
            if current:
                parts.append(current.strip())
            current = word
    if current.strip():
        parts.append(current.strip())
    return parts


def call_tts_api(text: str, voice_id: str, speed: float, jwt_token: str) -> dict:
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Content-Type": "application/json",
        "Origin": "https://www.vivibe.app",
        "Referer": "https://www.vivibe.app/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    payload = {
        "method": "tts",
        "input": {
            "text": text,
            "userVoiceId": voice_id,
            "speed": speed,
            "blockVersion": 0,
        },
    }
    try:
        r = requests.post(LUCYLAB_API, json=payload, headers=headers, timeout=60)
        if r.status_code == 200:
            data = r.json()
            if "error" in data:
                return {"error": data["error"].get("message", str(data["error"]))}
            if "result" in data and "url" in data["result"]:
                return {
                    "url": data["result"]["url"],
                    "credits_used": data["result"].get("creditsUsed", 0),
                }
            return {"error": "No URL in response"}
        elif r.status_code in (401, 403):
            return {"error": "JWT_EXPIRED"}
        return {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def download_audio(url: str, filepath: Path) -> bool:
    try:
        r = requests.get(url, timeout=120, stream=True)
        if r.status_code == 200:
            with open(filepath, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            return filepath.stat().st_size > 500
    except Exception:
        pass
    return False


def _create_silence(duration_s: float, filepath: Path, ffmpeg_path: str = "ffmpeg") -> bool:
    """Git-compatible silence asset used by the original audio merge path."""
    try:
        subprocess.run([
            ffmpeg_path, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-t", str(duration_s), str(filepath),
        ], capture_output=True, check=True)
        return filepath.exists()
    except Exception:
        return False


def get_media_duration(path: Path, ffprobe_path: str = "ffprobe", ffmpeg_path: str = "ffmpeg") -> float:
    if not path.exists() or path.stat().st_size <= 0:
        return 0.0
    try:
        result = subprocess.run(
            [
                ffprobe_path, "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        data = json.loads(result.stdout or "{}")
        duration = data.get("format", {}).get("duration")
        if duration:
            return float(duration)
    except Exception:
        pass
    try:
        result = subprocess.run(
            [
                ffprobe_path, "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        data = json.loads(result.stdout or "{}")
        for stream in data.get("streams", []):
            duration = stream.get("duration")
            if duration:
                return float(duration)
    except Exception:
        pass
    try:
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-i", str(path), "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr or "")
        if match:
            hours, minutes, seconds = match.groups()
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except Exception:
        pass
    return 0.0


def trim_audio_segment(input_path: Path, output_path: Path, silence_db: float = -35.0, ffmpeg_path: str = "ffmpeg") -> tuple[bool, str]:
    """Git-compatible trim behavior for provider WAV segments."""
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        af_filter = (
            f"silenceremove=start_periods=1:start_duration=0.05:start_threshold={silence_db}dB:"
            f"stop_periods=-1:stop_duration=0.15:stop_threshold={silence_db}dB"
        )
        cmd = [
            ffmpeg_path, "-y",
            "-i", str(input_path),
            "-af", af_filter,
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "FFmpeg trim failed")[-500:]
        if not output_path.exists() or output_path.stat().st_size <= 500:
            return False, "Trim produced an empty audio segment"
        return True, ""
    except Exception as e:
        return False, str(e)


def normalize_audio_segment_for_merge(input_path: Path, output_path: Path, ffmpeg_path: str = "ffmpeg") -> tuple[bool, str]:
    """Convert non-Vivibe provider audio to one WAV format for safe concat.

    Vivibe retains its original legacy path. Azure and ElevenLabs may return
    different sample rates/codecs, so normalize only their temporary segments.
    """
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run([
            ffmpeg_path, "-y", "-i", str(input_path),
            "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(output_path),
        ], capture_output=True, text=True)
        if result.returncode != 0:
            return False, (result.stderr or result.stdout or "FFmpeg normalization failed")[-500:]
        if not output_path.exists() or output_path.stat().st_size <= 500:
            return False, "Normalization produced an empty audio segment"
        return True, ""
    except Exception as exc:
        return False, str(exc)


def normalize_provider_results_for_merge(results: list[dict], provider_id: str, ffmpeg_path: str) -> list[dict]:
    """Normalize only adapters whose native output is not the Vivibe legacy WAV."""
    if provider_id == "vivibe":
        return results
    normalized: list[dict] = []
    for result in results:
        source = Path(result["filepath"])
        target = source.with_name(f"{source.stem}_canonical.wav")
        ok, error = normalize_audio_segment_for_merge(source, target, ffmpeg_path)
        if not ok:
            raise RuntimeError(f"{provider_id}: không thể chuẩn hóa segment {result.get('index')}: {error}")
        next_result = dict(result)
        next_result["provider_source_filepath"] = str(source)
        next_result["provider_source_filename"] = source.name
        next_result["filepath"] = str(target)
        next_result["filename"] = target.name
        next_result["pcm_normalized"] = True
        normalized.append(next_result)
    return normalized


def prepare_segments_for_merge(
    results: list[dict],
    trim_silence: bool,
    silence_db: float,
    ffprobe_path: str = "ffprobe",
    ffmpeg_path: str = "ffmpeg",
) -> tuple[list[dict], list[str]]:
    prepared: list[dict] = []
    trim_failed = 0
    duration_failed = 0
    trim_error_samples: list[str] = []
    for result in results:
        original_path = Path(result["filepath"])
        original_duration = get_media_duration(original_path, ffprobe_path, ffmpeg_path)
        if original_duration <= 0:
            duration_failed += 1
        used_path = original_path
        trim_ok = False
        trimmed_duration = 0.0

        if trim_silence:
            trimmed_path = original_path.with_name(f"{original_path.stem}_trimmed{original_path.suffix}")
            trim_ok, trim_error = trim_audio_segment(original_path, trimmed_path, silence_db, ffmpeg_path)
            if trim_ok:
                trimmed_duration = get_media_duration(trimmed_path, ffprobe_path, ffmpeg_path)
                if trimmed_duration > 0:
                    used_path = trimmed_path
                else:
                    trim_ok = False
                    trim_error = "Could not read trimmed segment duration"
            if not trim_ok:
                trim_failed += 1
                if trim_error and len(trim_error_samples) < 3:
                    trim_error_samples.append(f"segment {result.get('index')}: {trim_error}")

        used_duration = get_media_duration(used_path, ffprobe_path, ffmpeg_path)
        if used_duration <= 0 and original_duration > 0:
            used_duration = original_duration

        updated = dict(result)
        updated["original_filepath"] = str(original_path)
        updated["original_filename"] = original_path.name
        updated["original_duration_sec"] = round(original_duration, 3) if original_duration > 0 else 0
        updated["filepath"] = str(used_path)
        updated["filename"] = used_path.name
        updated["used_audio_segment_file"] = used_path.name
        updated["used_duration_sec"] = round(used_duration, 3) if used_duration > 0 else 0
        updated["trimmed_duration_sec"] = round(trimmed_duration, 3) if trim_silence and trim_ok else None
        updated["trim_applied"] = bool(trim_silence and trim_ok)
        updated["join_fade_applied"] = False
        prepared.append(updated)
    warnings: list[str] = []
    if trim_failed:
        warning = f"Trim was skipped for {trim_failed} segment{'s' if trim_failed != 1 else ''}; using original audio."
        if trim_error_samples:
            warning += " Samples: " + " | ".join(trim_error_samples)
        warnings.append(warning)
    if duration_failed:
        warnings.append(
            f"Could not read duration for {duration_failed} segment{'s' if duration_failed != 1 else ''}; "
            "timing will be rebuilt from final audio duration if needed."
        )
    return prepared, warnings


def write_timing_metadata(
    project_dir: Path,
    output_name: str,
    source_text: str,
    results: list[dict],
    requested_gap_sec: float,
    effective_gap_sec: float,
    trim_silence: bool,
    trim_mode: str = "none",
    warnings: list[str] | None = None,
    ffprobe_path: str = "ffprobe",
    final_audio_duration: float = 0.0,
    processing: dict | None = None,
    ffmpeg_path: str = "ffmpeg",
) -> dict:
    captions = []
    cursor = 0.0
    durations: list[float] = []
    invalid_indexes: list[int] = []

    for result in results:
        filepath = Path(result["filepath"])
        duration = float(result.get("used_duration_sec") or 0)
        if duration <= 0:
            duration = get_media_duration(filepath, ffprobe_path, ffmpeg_path)
        if duration <= 0:
            invalid_indexes.append(len(durations))
        durations.append(duration if duration > 0 else 0.0)

    if invalid_indexes and final_audio_duration > 0:
        gap_total = max(0, len(results) - 1) * max(0.0, effective_gap_sec)
        usable_duration = max(0.01, final_audio_duration - gap_total)
        known_duration = sum(d for d in durations if d > 0)
        remaining = max(0.01, usable_duration - known_duration)
        weights = [max(1, len(str(results[i].get("text", "")))) for i in invalid_indexes]
        total_weight = sum(weights) or 1
        for idx, weight in zip(invalid_indexes, weights):
            durations[idx] = remaining * (weight / total_weight)

    for idx, result in enumerate(results):
        duration = durations[idx] if idx < len(durations) else 0
        if duration <= 0:
            duration = 0.01
        start = cursor
        end = start + duration
        captions.append({
            "index": len(captions) + 1,
            # TTS can use a spoken number-normalized string, while captions
            # must keep the creator's original script (for example 900.000).
            "text": result.get("display_text", result.get("text", "")),
            "start_sec": round(start, 3),
            "end_sec": round(end, 3),
            "duration_sec": round(duration, 3),
            "audio_segment_file": result.get("filename", ""),
            "used_audio_segment_file": result.get("used_audio_segment_file", result.get("filename", "")),
            "original_audio_segment_file": result.get("original_filename", result.get("filename", "")),
            "original_duration_sec": result.get("original_duration_sec", 0),
            "trimmed_duration_sec": result.get("trimmed_duration_sec"),
            "trim_applied": result.get("trim_applied", False),
            "trim_rejected": result.get("trim_rejected", False),
            "trim_rejection_reason": result.get("trim_rejection_reason", ""),
            "join_fade_applied": result.get("join_fade_applied", False),
            "pcm_normalized": result.get("pcm_normalized", False),
            "join_gap_after_sec": effective_gap_sec if idx < len(results) - 1 else 0.0,
        })
        cursor = end + (effective_gap_sec if idx < len(results) - 1 else 0.0)

    warnings = warnings or []
    if invalid_indexes and final_audio_duration > 0:
        warnings.append(
            f"Rebuilt timing for {len(invalid_indexes)} segment{'s' if len(invalid_indexes) != 1 else ''} "
            "from final audio duration because segment duration was unavailable."
        )
    metadata = {
        "version": 2,
        "source_text": source_text,
        "output_name": output_name,
        "audio_file": f"{output_name}_FULL.mp3",
        # gap_sec remains for old readers; it now means the real gap in the
        # file, not merely the value selected in the UI.
        "gap_sec": effective_gap_sec,
        "requested_gap_sec": requested_gap_sec,
        "effective_gap_sec": effective_gap_sec,
        "join_guard_sec": 0,
        "join_fade_sec": 0,
        "trim_silence": trim_silence,
        "trim_mode": trim_mode,
        "processing": processing or {},
        "warning": " ".join(warnings),
        "warnings": warnings,
        "total_duration_sec": round(captions[-1]["end_sec"], 3) if captions else 0,
        "captions": captions,
    }
    timing_path = project_dir / f"{output_name}_timing.json"
    with open(timing_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    return metadata


def merge_wav_files(
    wav_files: list[Path],
    output_file: Path,
    gap_s: float = 0.0,
    trim_silence: bool = False,
    silence_db: float = -35.0,
    ffmpeg_path: str = "ffmpeg",
) -> bool:
    """Use the original FFmpeg concat merge path from the Git baseline.

    The previous concat-demuxer method joined unlike WAV streams (provider
    audio versus a hard-coded silence WAV), which inserted 80–90ms of padding
    per boundary and made trimmed narration sound choppy.
    """
    if not wav_files:
        return False
    # Restore the baseline merge behaviour exactly: preserve provider WAVs and
    # let FFmpeg concatenate them with the same 44.1 kHz stereo silence asset.
    # Do not normalize/downmix the voice before encoding the final MP3.
    TEMP_DIR.mkdir(exist_ok=True)
    files_to_merge = list(wav_files)
    if gap_s > 0:
        silence_path = TEMP_DIR / f"silence_{gap_s:.3f}s.wav"
        if _create_silence(gap_s, silence_path, ffmpeg_path):
            files_to_merge = []
            for index, wav_path in enumerate(wav_files):
                files_to_merge.append(wav_path)
                if index < len(wav_files) - 1:
                    files_to_merge.append(silence_path)
    list_file = TEMP_DIR / "merge_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for filepath in files_to_merge:
            escaped = str(filepath.resolve()).replace("\\", "/")
            f.write(f"file '{escaped}'\n")
    try:
        subprocess.run([
            ffmpeg_path, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-acodec", "libmp3lame", "-b:a", "192k", str(output_file),
        ], capture_output=True, check=True)
        return output_file.exists() and output_file.stat().st_size > 0
    except Exception:
        return False

def process_text_streaming(
    text: str,
    output_name: str,
    config: dict,
    project: str = "",
    locale: str = "vi-VN",
) -> Generator[dict, None, None]:
    """Yields progress events as dicts."""
    display_text = strip_chapter_headings_for_tts(text)
    speech_text = display_text
    if config.get("optimize_text"):
        speech_text = optimize_text_for_tts(speech_text)
    display_segments = split_text_for_tts_timing(display_text, config.get("max_chars", TTS_TIMING_MAX_CHARS))
    # Keep timing/caption units stable even if legacy optimization is enabled.
    speech_seed_segments = (
        [optimize_text_for_tts(segment) for segment in display_segments]
        if config.get("optimize_text") else list(display_segments)
    )
    if speech_text and not display_segments:
        display_segments = split_text_for_tts_timing(speech_text, config.get("max_chars", TTS_TIMING_MAX_CHARS))
        speech_seed_segments = list(display_segments)

    yield {"type": "normalizing_numbers", "phase": "start"}
    # Imported lazily to avoid the language service's config dependency creating
    # an import cycle during backend startup.
    from language_service import normalize_number_expressions_for_tts
    speech_segments, number_speech = normalize_number_expressions_for_tts(speech_seed_segments, locale)
    yield {
        "type": "normalizing_numbers",
        "phase": "done",
        "replacement_count": number_speech.get("replacement_count", 0),
        "provider": number_speech.get("provider", "not-used"),
        "model": number_speech.get("model", "not-used"),
    }

    total = len(speech_segments)

    yield {"type": "start", "total": total, "output_name": output_name}

    project_dir = get_project_dir(project)
    project_dir.mkdir(parents=True, exist_ok=True)
    seg_dir = project_dir / f"{output_name}_segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(exist_ok=True)

    provider, provider_settings, profile = resolve_tts_profile(config, locale)
    voice_id = str(profile["voice_id"])
    speed = float(profile.get("speed", 1.0))
    workers = min(int(config.get("workers", 3)), int(provider.max_parallel_requests))
    delay = float(config.get("delay", 1.0))

    results: list[dict] = []
    errors: list[dict] = []
    tasks = [
        (i + 1, spoken_segment, display_segments[i] if i < len(display_segments) else spoken_segment)
        for i, spoken_segment in enumerate(speech_segments)
    ]

    import queue
    import threading

    q: queue.Queue = queue.Queue()

    def worker(task):
        idx, seg_text, display_segment = task
        try:
            synthesis = provider.synthesize(SynthesisRequest(
                text=seg_text,
                locale=locale,
                voice_id=voice_id,
                speed=speed,
                profile=profile,
                provider_settings=provider_settings,
            ))
        except TTSProviderError as exc:
            q.put({"index": idx, "success": False, "error": str(exc)})
            return
        except Exception as exc:
            q.put({"index": idx, "success": False, "error": f"Provider error: {exc}"})
            return
        filename = f"{output_name}_{idx:04d}.{synthesis.extension.lstrip('.')}"
        filepath = seg_dir / filename
        try:
            filepath.write_bytes(synthesis.audio)
        except OSError as exc:
            q.put({"index": idx, "success": False, "error": f"Không lưu được audio segment: {exc}"})
            return
        if filepath.exists() and filepath.stat().st_size > 500:
            q.put({
                "index": idx,
                "success": True,
                "filepath": str(filepath),
                "filename": filename,
                "usage": synthesis.usage,
                "provider_metadata": synthesis.metadata,
                "text_length": len(seg_text),
                "text_preview": seg_text[:80],
                "text": seg_text,
                "display_text": display_segment,
            })
        else:
            q.put({"index": idx, "success": False, "error": "Download failed"})
        time.sleep(delay)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(worker, t) for t in tasks]
        completed = 0
        while completed < total:
            try:
                result = q.get(timeout=120)
            except queue.Empty:
                break
            completed += 1
            idx = result["index"]
            if result["success"]:
                results.append(result)
                yield {
                    "type": "progress",
                    "index": idx,
                    "success": True,
                    "filename": result["filename"],
                    "text_preview": result.get("text_preview", ""),
                    "completed": completed,
                    "total": total,
                }
            else:
                error = result.get("error", "Unknown")
                errors.append(result)
                yield {"type": "progress", "index": idx, "success": False, "error": error, "completed": completed, "total": total}

    results.sort(key=lambda x: x["index"])

    if results:
        yield {"type": "merging", "count": len(results)}
        merged_mp3 = project_dir / f"{output_name}_FULL.mp3"
        requested_gap_sec = max(0.0, float(config.get("sentence_gap", 0.0)))
        gap_s = requested_gap_sec
        trim_silence = bool(config.get("trim_silence", False))
        silence_db = float(config.get("silence_db", -35.0))
        ffprobe_path = config.get("ffprobe_path", "ffprobe")
        ffmpeg_path = config.get("ffmpeg_path", "ffmpeg")
        results = normalize_provider_results_for_merge(results, provider.provider_id, ffmpeg_path)
        prepared_results, timing_warnings = prepare_segments_for_merge(
            results,
            trim_silence,
            silence_db,
            ffprobe_path,
            ffmpeg_path,
        )
        wav_files = [Path(r["filepath"]) for r in prepared_results]
        trim_mode = "segment" if trim_silence else "none"
        ok = merge_wav_files(wav_files, merged_mp3, gap_s=gap_s, trim_silence=trim_silence, silence_db=silence_db, ffmpeg_path=ffmpeg_path)
        size_mb = merged_mp3.stat().st_size / (1024 * 1024) if ok and merged_mp3.exists() else 0
        final_duration = get_media_duration(merged_mp3, ffprobe_path, ffmpeg_path) if ok and merged_mp3.exists() else 0
        timing = write_timing_metadata(
            project_dir,
            output_name,
            display_text,
            prepared_results,
            requested_gap_sec,
            gap_s,
            trim_silence,
            trim_mode,
            timing_warnings,
            ffprobe_path,
            final_duration,
            {
                "voice_id": voice_id,
                "provider": provider.provider_id,
                "speed": speed,
                "provider_profile": {
                    key: value for key, value in profile.items()
                    if key not in {"api_key", "jwt_token", "token", "secret"}
                },
                "trim_silence": trim_silence,
                "silence_db": silence_db,
                "requested_gap_sec": requested_gap_sec,
                "effective_gap_sec": gap_s,
                "join_fade_sec": 0,
                "audio_merge_format": "legacy_ffmpeg_concat",
                "segment_count": len(prepared_results),
                "number_speech": number_speech,
            },
            ffmpeg_path,
        )

        # cleanup temp and segments
        try:
            shutil.rmtree(TEMP_DIR)
            TEMP_DIR.mkdir(exist_ok=True)
        except Exception:
            pass
        try:
            shutil.rmtree(seg_dir)
        except Exception:
            pass

        yield {
            "type": "done",
            "success": ok,
            "output_file": f"{output_name}_FULL.mp3" if ok else None,
            "size_mb": round(size_mb, 2),
            "results": len(results),
            "errors": len(errors),
            "timing_file": f"{output_name}_timing.json",
            "timing_warning": timing.get("warning", ""),
        }
    else:
        yield {"type": "done", "success": False, "reason": "No successful segments", "results": 0, "errors": len(errors)}


def list_outputs(project: str = "") -> list[dict]:
    d = get_project_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(d.iterdir()):
        if p.is_file() and p.suffix in (".mp3", ".wav"):
            stat = p.stat()
            items.append({
                "name": p.name,
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "modified": stat.st_mtime,
            })
    return items


def list_trash() -> list[dict]:
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(TRASH_DIR.iterdir()):
        if p.is_file() and p.suffix in (".mp3", ".wav"):
            stat = p.stat()
            items.append({
                "name": p.name,
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "modified": stat.st_mtime,
            })
    return items
