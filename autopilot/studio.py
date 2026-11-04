"""Headless driver for LucyLab Story Studio: same API calls the UI makes in "AI lọc content · Người que" mode.

Usage:
  python autopilot/studio.py produce autopilot/nemo/runs/<run>     # script.md + state.json(title) -> private YouTube upload
  python autopilot/studio.py publish autopilot/nemo/runs/<run> 2026-10-20T18:00   # owner approved: schedule it
  python autopilot/studio.py refresh autopilot/nemo/runs/<run>     # re-apply edited packaging.json to the private upload
  python autopilot/studio.py check                                   # backend / gpt2api / YouTube readiness

`produce` pauses twice for Claude: after subtitles (write packaging.json) and after thumbnail candidates (pick one).

Every step stores its result in <run>/state.json, so re-running `produce` resumes after a failure.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

BASE = os.environ.get("STUDIO_URL", "http://localhost:9000")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))  # thumbnails reuse the backend's image client and project paths
LLM_TIMEOUT = 1000  # codex CLI calls may take up to 900 s each


def q(segment: str) -> str:
    return quote(segment, safe="")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def api(method: str, path: str, timeout: float = 120, **kw) -> dict:
    r = requests.request(method, BASE + path, timeout=timeout, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
    return r.json()


def sse(path: str, body: dict, label: str) -> dict:
    """POST an SSE endpoint, log progress, return the final `done` event; raise on `error`."""
    done = None
    with requests.post(BASE + path, json=body, stream=True, timeout=(10, 1800)) as r:
        if r.status_code >= 400:
            raise RuntimeError(f"{label}: HTTP {r.status_code}: {r.text[:500]}")
        last = 0.0
        for raw in r.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data: "):
                continue
            data = raw[6:]
            if data == "[DONE]":
                break
            ev = json.loads(data)
            kind = ev.get("type") or ""
            if kind == "error":
                raise RuntimeError(f"{label}: {ev.get('message') or ev.get('error')}")
            if kind == "done":
                done = ev
                for warning in ev.get("warnings", []):  # e.g. thumbnail/captions not set; the video itself is up
                    log(f"{label}: WARNING {warning}")
            elif kind.endswith("_error") or ev.get("success") is False:  # e.g. one failed TTS segment
                log(f"{label}: {kind} {ev.get('scene_id') or ev.get('reference_id') or ev.get('index') or ''} "
                    f"{ev.get('message') or ev.get('error') or ''}")
            elif time.time() - last > 30:
                last = time.time()
                pct = ev.get("percent") or ev.get("completed")
                log(f"{label}: {kind} {pct if pct is not None else ''} {ev.get('message', '')}".rstrip())
    if done is None:
        raise RuntimeError(f"{label}: stream ended without done")
    return done


# ── readiness ────────────────────────────────────────────────────────────────

def ensure_backend() -> None:
    try:
        api("GET", "/api/config", timeout=5)
        return
    except Exception:
        pass
    log("Backend down, starting uvicorn on :9000")
    cmd = [sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]
    try:  # leave the caller's job object so the server outlives this script
        subprocess.Popen(cmd, cwd=ROOT / "backend",
                         creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_BREAKAWAY_FROM_JOB)
    except OSError:
        subprocess.Popen(cmd, cwd=ROOT / "backend", creationflags=subprocess.CREATE_NEW_CONSOLE)
    for _ in range(60):
        time.sleep(2)
        try:
            api("GET", "/api/config", timeout=5)
            return
        except Exception:
            pass
    raise RuntimeError("Backend did not come up on :9000")


def ensure_gpt2api() -> None:
    api("POST", "/api/gpt2api/runtime/start", timeout=300)
    for _ in range(60):
        try:
            if api("GET", "/api/gpt-accounts", timeout=30).get("pool", {}).get("usableCount", 0) > 0:
                return
        except Exception as exc:
            log(f"gpt2api not ready: {exc}")
        time.sleep(4)
    raise RuntimeError("gpt2api has no usable image account (log in to ChatGPT in gpt2api admin)")


def youtube_status() -> dict:
    return api("GET", "/api/youtube/auth/status", timeout=60)


def youtube_token_dir() -> Path:
    from tts_service import OUTPUT_DIR
    return OUTPUT_DIR / "_shared" / "youtube"  # youtube_publish_service.PUBLISH_DIR


def use_channel(profile: dict, token_dir: Path) -> None:
    """The backend holds one YouTube token (token.json). Keep a copy per channel and swap in this profile's
    channel before any upload, so a video never lands on the other channel."""
    want = profile["youtube"]["channel_id"]
    token, saved = token_dir / "token.json", token_dir / f"token.{want}.json"
    current = (youtube_status().get("channel") or {}).get("id")
    if current and current != want:
        shutil.copyfile(token, token_dir / f"token.{current}.json")  # a just-connected channel survives the swap
    if current != want and saved.exists():
        shutil.copyfile(saved, token)
        current = (youtube_status().get("channel") or {}).get("id")
    if current != want:
        raise RuntimeError(f"YouTube is connected to {current}, not {profile['channel_name']} ({want}): "
                           "owner connects this channel once in the UI (Publish > Connect)")
    shutil.copyfile(token, saved)  # keep the copy current (refreshed access token)


# ── state helpers ────────────────────────────────────────────────────────────

class Run:
    def __init__(self, run_dir: Path):
        self.dir = run_dir
        self.path = run_dir / "state.json"
        self.state = json.loads(self.path.read_text(encoding="utf-8"))
        self.profile = json.loads((run_dir.parents[1] / "profile.json").read_text(encoding="utf-8"))

    def save(self) -> None:
        self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def step(self, key: str, fn):
        if key not in self.state:
            log(f"── {key}")
            self.state[key] = fn()
            self.save()
        return self.state[key]


def clean_project_name(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", value)).strip()


def safe_stem(value: str) -> str:  # App.jsx cleanFileStem (JS \w is ASCII)
    return re.sub(r"[^\w-]+", "_", clean_project_name(value), flags=re.ASCII).strip("_") or "story"


def project_video_name(project: str) -> str:  # VideoUpload.jsx projectVideoName
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", project).strip()
    stem = re.sub(r"[^\w.-]+", "_", stem).strip("_-.")
    return f"{stem or 'video'}.mp4"


def unique_project(base: str) -> str:
    taken = set(api("GET", "/api/projects"))
    name, n = base, 2
    while name in taken:
        name, n = f"{base} - {n}", n + 1
    return name


def session_payload(run: Run, stage: str) -> dict:
    s, p = run.state, run.profile
    bp = {k: v for k, v in s["blueprint"].items() if k not in ("narration_units", "scenes")}
    locale = p["locale"]
    return {
        "title": s["title"][:80],
        "story_title": s["title"],
        "stage": stage,
        "story_data": {
            "storyTitle": s["title"], "model": bp.get("model", ""), "text": bp["tts_text"],
            "language": {"source_locale": locale, "target_locale": locale, "review_before_tts": True,
                         "detected_source_locale": locale},
            "source_script": s["blueprint"].get("source_script", ""), "content_mode": "stick_figure",
            "stick_figure": bp, "source_text": bp["tts_text"], "localization": None, "narration_locale": locale,
        },
        "approved_story": bp["tts_text"],
        "current_project": s.get("project"),
        "audio_file": s.get("audio", {}).get("file"),
        "audio_duration": s.get("audio", {}).get("duration"),
        "video_order": [],
        "video_settings": {**p["video_settings"], "outputName": project_video_name(s["project"])},
        "output_video_file": s.get("video"),
        "subtitle_settings": p["subtitle_settings"],
        "subtitle_outputs": s.get("subtitled", {}),
    }


# ── pipeline ─────────────────────────────────────────────────────────────────

def make_audio(run: Run) -> dict:
    s = run.state
    done = sse("/api/generate", {"text": s["blueprint"]["tts_text"], "output_name": safe_stem(s["title"]),
                                 "project": s["project"], "locale": run.profile["locale"]}, "tts")
    errors = done.get("errors") or 0
    if not done.get("success") or (len(errors) if isinstance(errors, list) else errors):
        raise RuntimeError(f"TTS incomplete: {done.get('reason') or errors}")
    dur = api("GET", "/api/audio/duration", params={"project": s["project"], "filename": done["output_file"]})
    return {"file": done["output_file"], "duration": dur["duration_sec"]}


def apply_style(run: Run) -> None:
    """style.txt (one art style per video, no colour names) replaces the style the extractor copied from the script."""
    f = run.dir / "style.txt"
    if not f.exists():
        return
    style = f.read_text(encoding="utf-8").strip()
    run.state["blueprint"]["visual_style"] = style
    for r in run.state["blueprint"].get("references", []):
        if r.get("type") == "style":
            r["prompt"] = r["description"] = style
    run.save()


def make_plan(run: Run) -> bool:
    s, proj = run.state, run.state["project"]
    bp = {k: v for k, v in s["blueprint"].items() if k not in ("narration_units", "scenes")}
    api("POST", f"/api/scenes/{q(proj)}/stick-figure/visual-plan", timeout=2 * LLM_TIMEOUT, json={
        "project": proj, "audio_file": s["audio"]["file"], "story_text": bp["tts_text"],
        "story_title": s["title"], "visual_style": bp.get("visual_style", ""),
        "target_scene_sec": 12, "min_scene_sec": 7, "max_scene_sec": 18, "max_scenes": 140,
        "visual_plan_instruction": "", "content_blueprint": bp, "auto_prepare_references": False,
        "output_locale": run.profile["locale"],
    })
    api("POST", f"/api/scenes/{q(proj)}/stick-figure/visual-plan/approve", json={"auto_prepare_references": False})
    return True


def missing(plan: dict, key: str) -> list[str]:
    return [x["id"] for x in plan.get(key, []) if not x.get("image_file") or x.get("status") == "failed"]


def make_images(run: Run) -> int:
    proj = run.state["project"]
    for attempt in range(2):
        sse(f"/api/scenes/{q(proj)}/director/references/generate",
            {"reference_ids": None, "retry_failed": attempt > 0, "mode": "parallel", "concurrency": 3}, "references")
        if not missing(api("GET", f"/api/scenes/{q(proj)}"), "references"):
            break
    for attempt in range(4):
        sse("/api/scenes/generate", {"project": proj, "scene_ids": None, "retry_failed": attempt > 0,
                                     "concurrency": 3}, "scenes")
        plan = api("GET", f"/api/scenes/{q(proj)}")
        left = missing(plan, "scenes")
        if not left:
            return len(plan["scenes"])
        log(f"{len(left)} scenes still missing, retrying")
    # compose would silently drop these scenes and drift against the audio
    raise RuntimeError(f"Scenes without image: {left}")


def make_video(run: Run) -> str:
    s, vs = run.state, run.profile["video_settings"]
    rv = vs["sceneReveal"]
    done = sse("/api/scenes/compose", {
        "project": s["project"], "audio_file": s["audio"]["file"], "output_name": project_video_name(s["project"]),
        "video_encoder": vs["videoEncoder"], "quality": vs["quality"], "overwrite_mode": vs["overwriteMode"],
        "scene_reveal": {"enabled": rv["enabled"], "effect": rv["effect"], "duration_sec": rv["durationSec"],
                         "pattern": rv["pattern"], "actor": rv["actor"]},
    }, "compose")
    return done["output_file"]


def make_subtitled(run: Run) -> dict:
    s, st = run.state, run.profile["subtitle_settings"]
    modes = [m for m in ("16x9", "9x16") if st["renderTargets"].get(m)]
    if not st["burnSubtitles"] and modes == ["16x9"] and not st["backgroundMusicEnabled"]:
        return {"16x9": s["video"]}  # same passthrough as SubtitleConfig.jsx
    done = sse("/api/subtitles/render", {
        "project": s["project"], "input_video": s["video"], "audio_file": s["audio"]["file"],
        "story_text": s["blueprint"]["tts_text"], "style": st, "video_encoder": run.profile["video_settings"]["videoEncoder"],
        "quality": "high", "overwrite_mode": "version",
        "background_music": st["backgroundMusicFile"] if st["backgroundMusicEnabled"] else "",
        "background_music_volume": st["backgroundMusicVolume"] / 100 if st["backgroundMusicEnabled"] else 0,
        "render_modes": modes, "vertical_video_mode": st["verticalVideoMode"], "burn_subtitles": st["burnSubtitles"],
    }, "subtitles")
    return done["outputs"]


# ── packaging: Claude writes <run>/packaging.json (playbook "Đóng gói"), Codex draws the thumbnail ──

def check_packaging(pkg: dict) -> None:
    """Fail before upload on what the YouTube API would reject."""
    tags = pkg["tags"]
    tags_len = sum(len(t) + (2 if " " in t else 0) for t in tags) + max(len(tags) - 1, 0)  # quoted multi-word tags
    versions = [pkg] + list(pkg.get("localizations", {}).values())
    texts = [v.get(k, "") for v in versions for k in ("title", "description")]
    problems = [msg for bad, msg in (
        (any(not 0 < len(v.get("title", "")) <= 100 for v in versions), "every title must be 1-100 chars"),
        (any(len(v.get("description", "").encode("utf-8")) > 5000 for v in versions), "description > 5000 bytes"),
        (tags_len > 500, f"tags use {tags_len}/500 chars"),
        (any(c in t for t in texts for c in "<>"), "< and > are not allowed"),
    ) if bad]
    if problems:
        raise RuntimeError(f"packaging.json: {'; '.join(problems)}")


def fetch_srt(run: Run) -> Path:
    """Narration-timed SRT; also what Claude reads to write description chapters."""
    path = run.dir / "captions.srt"
    if not path.exists():
        r = requests.get(BASE + "/api/subtitles/srt", timeout=120, params={
            "project": run.state["project"], "audio_file": run.state["audio"]["file"], "max_chars": 84, "max_words": 16})
        r.raise_for_status()
        path.write_bytes(r.content)
    return path


def make_thumbnail_candidates(run: Run, pkg: dict) -> None:
    from image_generation_service import generate_image_file  # backend's gpt2api (Codex image) client
    from tts_service import get_project_dir
    style_dir = get_project_dir(run.state["project"]) / "scenes" / "references" / "style"
    style = [str(p) for p in sorted(style_dir.glob("*.png"))[:1]]  # same look as the video's scenes
    prompts = pkg["thumbnail_prompts"]
    (run.dir / "thumbnails").mkdir(exist_ok=True)
    for i, prompt in enumerate(prompts, 1):
        target = run.dir / "thumbnails" / f"cand_{i}.png"
        if target.exists():  # delete a candidate to redraw it
            continue
        log(f"thumbnail candidate {i}/{len(prompts)}")
        try:
            generate_image_file(prompt, target, reference_images=style)
        except Exception as exc:
            log(f"thumbnail candidate {i} failed: {exc}")


def youtube_metadata(run: Run, pkg: dict) -> dict:
    """Stage the chosen thumbnail (1280x720 JPEG) and SRT in the project; return the publish item metadata."""
    from PIL import Image, ImageOps
    from tts_service import get_project_dir
    s, yt = run.state, run.profile["youtube"]
    proj_dir = get_project_dir(s["project"])
    thumb_dir = proj_dir / "thumbnails" / "manual"  # thumbnail_service._thumb_dir
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb = f"autopilot_{Path(pkg['thumbnail']).stem}.jpg"
    ImageOps.fit(Image.open(run.dir / pkg["thumbnail"]).convert("RGB"), (1280, 720)).save(thumb_dir / thumb, "JPEG", quality=92)
    api("POST", f"/api/thumbnails/{q(s['project'])}/selected", json={"filename": thumb})
    caption = f"{safe_stem(s['title'])}.{yt['language']}.srt"
    (proj_dir / "captions").mkdir(exist_ok=True)
    (proj_dir / "captions" / caption).write_bytes(fetch_srt(run).read_bytes())
    return {"title": pkg["title"], "description": pkg["description"], "tags": pkg["tags"],
            "language": yt["language"], "category_id": yt["category_id"], "caption_file": caption,
            "localizations": pkg.get("localizations")}


def upload_private(run: Run, pkg: dict) -> dict:
    s = run.state
    meta = youtube_metadata(run, pkg)
    try:  # a run that died after the upload already has a video id; reuse it instead of uploading twice
        plan = api("GET", f"/api/publish/youtube/plan/{q(s['project'])}")
    except RuntimeError:
        plan = {}
    item = next((i for i in plan.get("items", []) if i["kind"] == "long" and i.get("youtube_video_id")), None)
    if item:
        item = next(i for i in api("PATCH", f"/api/publish/youtube/plan/{q(s['project'])}/items/{item['id']}", json=meta)["items"]
                    if i["id"] == item["id"])
    else:
        plan = api("POST", "/api/publish/youtube/plan/create", timeout=300, json={
            "project": s["project"], "story_title": s["title"], "story_text": s["blueprint"]["tts_text"],
            "long_video": {"filename": s["subtitled"]["16x9"]}, "short_videos": [], "long_metadata": meta,
            "timezone": run.profile.get("timezone", "Asia/Ho_Chi_Minh"),
        })
        item = next(i for i in plan["items"] if i["kind"] == "long")
    done = sse(f"/api/publish/youtube/items/{item['id']}/upload",
               {"project": s["project"], "item_id": item["id"], "mode": "private"}, "upload")
    return {"item_id": item["id"], "video_id": done["youtube_video_id"], "url": done["youtube_url"],
            "title": item["title"]}


def load_packaging(run: Run) -> dict | None:
    path = run.dir / "packaging.json"
    if not path.exists():
        return None
    pkg = json.loads(path.read_text(encoding="utf-8"))
    check_packaging(pkg)
    return pkg


def produce(run_dir: Path) -> None:
    run = Run(run_dir)
    s, p = run.state, run.profile
    ensure_backend()
    run.step("blueprint", lambda: api("POST", "/api/story/stick-figure/extract", timeout=LLM_TIMEOUT,
                                      json={"source_script": (run_dir / "script.md").read_text(encoding="utf-8")}))
    if not s["blueprint"].get("tts_text", "").strip():
        raise RuntimeError("Extract returned empty tts_text")
    run.step("project", lambda: unique_project(clean_project_name(f"{s['title']} - {p['locale']}")))
    run.step("session_id", lambda: api("POST", "/api/story-sessions", json=session_payload(run, "STORY_EDITING"))["id"])
    run.step("audio", lambda: make_audio(run))
    if "plan_approved" not in s:
        apply_style(run)
    run.step("plan_approved", lambda: make_plan(run))
    ensure_gpt2api()
    run.step("scene_count", lambda: make_images(run))
    run.step("video", lambda: make_video(run))
    run.step("subtitled", lambda: make_subtitled(run))
    fetch_srt(run)
    pkg = load_packaging(run)
    if not pkg:
        log("PAUSE: write packaging.json (playbook 'Đóng gói', chapters from captions.srt), then re-run produce")
        return
    if not pkg.get("thumbnail"):
        make_thumbnail_candidates(run, pkg)
        log("PAUSE: review thumbnails/cand_*.png, set packaging.json \"thumbnail\", then re-run produce")
        return
    api("PATCH", f"/api/story-sessions/{s['session_id']}", json=session_payload(run, "DONE"))
    if youtube_status().get("connected"):
        use_channel(p, youtube_token_dir())
        run.step("youtube", lambda: upload_private(run, pkg))
        log(f"Uploaded PRIVATE for review: {s['youtube']['url']}")
    else:
        log("YouTube not connected: skipped upload")
    log(f"Done: {s['project']} / {s['subtitled']['16x9']}")


def publish(run_dir: Path, at: str) -> None:
    """Only after the owner approved this exact video. `at` is local time (profile timezone), ISO format."""
    run = Run(run_dir)
    s = run.state
    yt = s["youtube"]
    use_channel(run.profile, youtube_token_dir())
    api("PATCH", f"/api/publish/youtube/plan/{q(s['project'])}/items/{yt['item_id']}", json={"scheduled_at": at})
    sse(f"/api/publish/youtube/items/{yt['item_id']}/upload",
        {"project": s["project"], "item_id": yt["item_id"], "mode": "scheduled"}, "schedule")
    s["published_at"] = at
    run.save()
    log(f"Scheduled {yt['url']} for {at}")


def refresh(run_dir: Path) -> None:
    """Re-apply packaging.json to the uploaded video. Before `publish`: text, thumbnail, captions; after: text only."""
    run = Run(run_dir)
    s, yt = run.state, run.state["youtube"]
    use_channel(run.profile, youtube_token_dir())
    meta = youtube_metadata(run, load_packaging(run))
    api("PATCH", f"/api/publish/youtube/plan/{q(s['project'])}/items/{yt['item_id']}", json=meta)
    # After `publish` keep the schedule (or public status): a private-mode update would clear publishAt.
    mode = "scheduled" if s.get("published_at") else "private"
    sse(f"/api/publish/youtube/items/{yt['item_id']}/upload",
        {"project": s["project"], "item_id": yt["item_id"], "mode": mode}, "refresh")
    log(f"Refreshed {yt['url']}")


def check() -> None:
    ensure_backend()
    log("backend ok")
    ensure_gpt2api()
    log("gpt2api ok")
    log(f"youtube: {youtube_status()}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "produce":
        produce(Path(sys.argv[2]).resolve())
    elif cmd == "publish":
        publish(Path(sys.argv[2]).resolve(), sys.argv[3])
    elif cmd == "refresh":
        refresh(Path(sys.argv[2]).resolve())
    elif cmd == "check":
        check()
    else:
        print(__doc__)
