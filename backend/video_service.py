"""Video composition service using FFmpeg."""
import json
import subprocess
from pathlib import Path
from typing import Generator


def get_video_duration(path: Path, ffprobe_path: str = "ffprobe") -> float:
    """Return video duration in seconds, or 0 on error."""
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
        data = json.loads(result.stdout)
        for stream in data.get("streams", []):
            dur = stream.get("duration")
            if dur:
                return float(dur)

        result2 = subprocess.run(
            [ffprobe_path, "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        fmt = json.loads(result2.stdout).get("format", {})
        if fmt.get("duration"):
            return float(fmt["duration"])
    except Exception:
        pass
    return 0.0


def _encoder_args(video_encoder: str, quality: str) -> tuple[list[str], str]:
    quality = quality if quality in {"fast", "balanced", "high"} else "balanced"
    if video_encoder == "nvidia_nvenc":
        cq = {"fast": "26", "balanced": "23", "high": "20"}[quality]
        preset = {"fast": "p4", "balanced": "p5", "high": "p6"}[quality]
        return ["-c:v", "h264_nvenc", "-preset", preset, "-cq", cq, "-b:v", "0"], "NVIDIA NVENC"
    if video_encoder == "intel_qsv":
        global_quality = {"fast": "26", "balanced": "23", "high": "20"}[quality]
        return ["-c:v", "h264_qsv", "-global_quality", global_quality], "Intel Quick Sync"

    crf = {"fast": "26", "balanced": "23", "high": "20"}[quality]
    preset = {"fast": "veryfast", "balanced": "fast", "high": "slow"}[quality]
    return ["-c:v", "libx264", "-preset", preset, "-crf", crf], "CPU libx264"


def validate_video_integrity(ffmpeg_path: str, path: Path, timeout_sec: int = 600) -> tuple[bool, str]:
    """Fully decode an exported MP4 so a successful encoder exit cannot hide corrupt frames."""
    if not path.exists() or path.stat().st_size == 0:
        return False, "Output file is missing or empty"
    try:
        result = subprocess.run(
            [ffmpeg_path, "-v", "error", "-xerror", "-i", str(path), "-map", "0", "-f", "null", "-"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return False, "Integrity scan timed out"
    except OSError as exc:
        return False, f"Integrity scan could not start: {exc}"
    if result.returncode == 0:
        return True, ""
    detail = (result.stderr or result.stdout or "decoder rejected output").strip().replace("\n", " ")
    return False, detail[-700:]


def _parse_ffmpeg_time(key: str, value: str) -> float | None:
    value = value.strip()
    if not value or value.upper() == "N/A":
        return None
    try:
        if key in {"out_time_ms", "out_time_us"}:
            return max(0.0, int(value) / 1_000_000)
        if key == "out_time":
            parts = value.split(":")
            if len(parts) == 3:
                hours = float(parts[0])
                minutes = float(parts[1])
                seconds = float(parts[2])
                return max(0.0, hours * 3600 + minutes * 60 + seconds)
    except ValueError:
        return None
    return None


def _progress_event(
    elapsed: float,
    total: float,
    output_path: Path,
    speed: str,
    encoder: str = "",
    encoder_label: str = "",
    message: str = "",
    percent_start: int = 0,
    percent_span: int = 99,
) -> dict:
    ratio = min(1.0, elapsed / total) if total > 0 else 0
    pct = min(99, percent_start + int(ratio * percent_span))
    size_mb = output_path.stat().st_size / (1024 * 1024) if output_path.exists() else 0
    event = {
        "type": "progress",
        "percent": pct,
        "elapsed_sec": elapsed,
        "total_sec": total,
        "eta_sec": max(0, total - elapsed) if total > 0 else 0,
        "size_mb": round(size_mb, 2),
        "speed": speed,
        "encoder": encoder,
        "encoder_label": encoder_label,
    }
    if message:
        event["message"] = message
    return event


def _concat_events(
    ffmpeg_path: str,
    concat_list: Path,
    merged_bg: Path,
    total_duration: float,
) -> Generator[dict, None, int]:
    yield {"type": "step", "step": "concat", "message": "Ghep cac video nen lai..."}
    stderr_tail: list[str] = []
    proc = subprocess.Popen(
        [
            ffmpeg_path, "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            "-progress", "pipe:2",
            "-nostats",
            str(merged_bg),
        ],
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
        stderr_tail = stderr_tail[-20:]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "speed":
            last_speed = value
        elapsed = _parse_ffmpeg_time(key, value)
        if elapsed is not None:
            yield _progress_event(
                elapsed,
                total_duration,
                merged_bg,
                last_speed,
                message="Dang ghep video nen...",
                percent_start=0,
                percent_span=15,
            )

    proc.wait()
    if proc.returncode != 0:
        yield {
            "type": "concat_failed",
            "message": f"Ghep video that bai: {' '.join(stderr_tail[-8:])}",
        }
    return proc.returncode or 0


def _encode_events(
    ffmpeg_path: str,
    merged_bg: Path,
    audio_path: Path,
    output_path: Path,
    audio_duration: float,
    video_encoder: str,
    quality: str,
) -> Generator[dict, None, int]:
    encoder_args, encoder_label = _encoder_args(video_encoder, quality)
    yield {
        "type": "step",
        "step": "compose",
        "message": f"Ket hop audio va video bang {encoder_label}...",
        "encoder": video_encoder,
        "encoder_label": encoder_label,
    }

    stderr_tail: list[str] = []
    proc = subprocess.Popen(
        [
            ffmpeg_path, "-y",
            "-i", str(merged_bg),
            "-i", str(audio_path),
            "-t", str(audio_duration),
            *encoder_args,
            "-c:a", "aac", "-b:a", "192k",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-movflags", "+faststart",
            "-progress", "pipe:2",
            "-nostats",
            str(output_path),
        ],
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stderr is not None
    last_speed = ""
    for line in proc.stderr:
        line = line.strip()
        if not line:
            continue
        stderr_tail.append(line)
        stderr_tail = stderr_tail[-20:]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "speed":
            last_speed = value
            yield {"type": "speed", "speed": value, "encoder_label": encoder_label}
        elapsed = _parse_ffmpeg_time(key, value)
        if elapsed is not None:
            yield _progress_event(
                elapsed,
                audio_duration,
                output_path,
                last_speed,
                video_encoder,
                encoder_label,
                message="Dang xuat video...",
                percent_start=15,
                percent_span=84,
            )
    proc.wait()
    if proc.returncode != 0:
        yield {
            "type": "encode_failed",
            "message": f"FFmpeg loi khi xuat video: {' '.join(stderr_tail[-8:])}",
            "encoder": video_encoder,
        }
    return proc.returncode or 0


def compose_video(
    video_paths: list[Path],
    audio_path: Path,
    output_path: Path,
    audio_duration: float,
    temp_dir: Path,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
    video_encoder: str = "cpu_x264",
    quality: str = "high",
) -> Generator[dict, None, None]:
    """
    Compose background videos + audio into final video.
    Yields structured step/progress/done/error events.
    """
    temp_dir.mkdir(parents=True, exist_ok=True)
    concat_list = temp_dir / "video_concat_list.txt"
    merged_bg = temp_dir / "merged_bg.mp4"

    try:
        with open(concat_list, "w", encoding="utf-8") as f:
            for vp in video_paths:
                escaped = str(vp).replace("\\", "/")
                f.write(f"file '{escaped}'\n")
    except Exception as e:
        yield {"type": "error", "message": f"Khong the tao danh sach video: {e}"}
        return

    try:
        total_bg_duration = sum(get_video_duration(vp, ffprobe_path) for vp in video_paths)
        concat = _concat_events(ffmpeg_path, concat_list, merged_bg, total_bg_duration)
        while True:
            try:
                event = next(concat)
                if event.get("type") == "concat_failed":
                    yield {"type": "error", "message": event.get("message", "Ghep video that bai")}
                    return
                yield event
            except StopIteration as stop:
                if stop.value != 0:
                    yield {"type": "error", "message": "Ghep video that bai"}
                    return
                break
        if not merged_bg.exists() or merged_bg.stat().st_size == 0:
            yield {"type": "error", "message": "Ghep video that bai, file nen khong ton tai hoac trong"}
            return
    except Exception as e:
        yield {"type": "error", "message": f"FFmpeg loi khi ghep video: {e}"}
        return

    try:
        selected_encoder = video_encoder
        failed_message = ""
        encode = _encode_events(ffmpeg_path, merged_bg, audio_path, output_path, audio_duration, video_encoder, quality)
        while True:
            try:
                event = next(encode)
                if event.get("type") == "encode_failed":
                    failed_message = event.get("message", "")
                else:
                    yield event
            except StopIteration as stop:
                return_code = stop.value
                break

        if return_code == 0:
            yield {"type": "step", "step": "verify", "message": "Đang kiểm tra toàn bộ file video..."}
            valid, integrity_error = validate_video_integrity(ffmpeg_path, output_path)
            if not valid:
                failed_message = f"GPU output failed integrity check: {integrity_error}"
                return_code = 1
        if return_code != 0:
            if video_encoder != "cpu_x264":
                yield {
                    "type": "step",
                    "step": "fallback",
                    "message": "GPU encoder loi, dang fallback sang CPU libx264...",
                    "encoder": "cpu_x264",
                    "encoder_label": "CPU libx264",
                }
                if output_path.exists():
                    output_path.unlink()
                selected_encoder = "cpu_x264"
                encode = _encode_events(ffmpeg_path, merged_bg, audio_path, output_path, audio_duration, "cpu_x264", quality)
                while True:
                    try:
                        event = next(encode)
                        if event.get("type") == "encode_failed":
                            failed_message = event.get("message", "")
                        else:
                            yield event
                    except StopIteration as stop:
                        return_code = stop.value
                        break
                if return_code == 0:
                    yield {"type": "step", "step": "verify", "message": "Đang kiểm tra file CPU fallback..."}
                    valid, integrity_error = validate_video_integrity(ffmpeg_path, output_path)
                    if not valid:
                        failed_message = f"CPU output failed integrity check: {integrity_error}"
                        return_code = 1
            if return_code != 0:
                yield {"type": "error", "message": failed_message or "FFmpeg loi khi xuat video"}
                return
    except Exception as e:
        yield {"type": "error", "message": f"FFmpeg loi khi xuat video: {e}"}
        return

    if not output_path.exists() or output_path.stat().st_size == 0:
        yield {"type": "error", "message": "Xuat video that bai, file khong ton tai hoac trong"}
        return

    size_mb = output_path.stat().st_size / (1024 * 1024)
    yield {
        "type": "done",
        "output_file": output_path.name,
        "size_mb": round(size_mb, 2),
        "encoder": selected_encoder,
        "encoder_label": _encoder_args(selected_encoder, quality)[1],
    }


def _loop_encode_events(
    ffmpeg_path: str,
    loop_video: Path,
    audio_path: Path,
    output_path: Path,
    audio_duration: float,
    video_encoder: str,
    quality: str,
) -> Generator[dict, None, int]:
    """Render one short source repeatedly until the narration is finished."""
    encoder_args, encoder_label = _encoder_args(video_encoder, quality)
    yield {
        "type": "step",
        "step": "loop-compose",
        "message": f"Dang lap video nen den het audio bang {encoder_label}...",
        "encoder": video_encoder,
        "encoder_label": encoder_label,
    }
    # Normalize any uploaded source to the long-video canvas. The source itself
    # is never altered; FFmpeg repeats it at decode time with -stream_loop.
    vf = "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1,format=yuv420p"
    cmd = [
        ffmpeg_path, "-y", "-stream_loop", "-1", "-i", str(loop_video), "-i", str(audio_path),
        "-t", str(audio_duration), "-vf", vf, "-r", "30",
        *encoder_args,
        "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0", "-movflags", "+faststart",
        "-progress", "pipe:2", "-nostats", str(output_path),
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
                message="Dang xuat video loop...", percent_start=0, percent_span=99,
            )
    proc.wait()
    if proc.returncode != 0:
        yield {
            "type": "encode_failed",
            "message": f"FFmpeg loi khi xuat video loop: {' '.join(stderr_tail[-8:])}",
            "encoder": video_encoder,
        }
    return proc.returncode or 0


def compose_loop_video(
    loop_video: Path,
    audio_path: Path,
    output_path: Path,
    audio_duration: float,
    ffmpeg_path: str = "ffmpeg",
    video_encoder: str = "cpu_x264",
    quality: str = "high",
) -> Generator[dict, None, None]:
    """Compose an audio track over a looped video and verify the exported MP4."""
    selected_encoder = video_encoder
    last_error = ""
    for encoder in [video_encoder, *( ["cpu_x264"] if video_encoder != "cpu_x264" else [])]:
        if encoder != video_encoder:
            yield {
                "type": "step", "step": "fallback",
                "message": "GPU encoder loi, dang fallback sang CPU libx264...",
                "encoder": encoder, "encoder_label": _encoder_args(encoder, quality)[1],
            }
            output_path.unlink(missing_ok=True)
        render = _loop_encode_events(
            ffmpeg_path, loop_video, audio_path, output_path, audio_duration, encoder, quality,
        )
        while True:
            try:
                event = next(render)
                if event.get("type") == "encode_failed":
                    last_error = event.get("message", "")
                else:
                    yield event
            except StopIteration as stop:
                result = stop.value
                break
        if result != 0:
            continue
        yield {"type": "step", "step": "verify", "message": "Dang kiem tra toan bo file video loop..."}
        valid, integrity_error = validate_video_integrity(ffmpeg_path, output_path)
        if not valid:
            last_error = f"Output integrity check failed: {integrity_error}"
            output_path.unlink(missing_ok=True)
            continue
        size_mb = output_path.stat().st_size / (1024 * 1024)
        yield {
            "type": "done", "output_file": output_path.name, "size_mb": round(size_mb, 2),
            "encoder": encoder, "encoder_label": _encoder_args(encoder, quality)[1],
        }
        return
    yield {"type": "error", "message": last_error or "Khong the xuat video loop"}
