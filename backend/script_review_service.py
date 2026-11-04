"""Script review agents for story drafts."""
import concurrent.futures
import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Generator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from story_service import generate_text, generate_text_openai_compatible, _parse_json_response
from tts_service import OUTPUT_DIR, load_config


router = APIRouter()

STORY_SESSIONS_DIR = OUTPUT_DIR / "_story_sessions"
SAFE_SESSION_ID = re.compile(r"^[a-f0-9-]{20,80}$", re.IGNORECASE)
SEVERITY_ORDER = {"critical": 0, "important": 1, "minor": 2}
SPECIALIST_IDS = ("spoken_naturalness", "logic_continuity", "tension_pacing", "retention_hooks", "opening_hook")

AUTO_RUN_LOCK = threading.Lock()
AUTO_RUN_WORKERS: set[str] = set()


class ScriptReviewRunRequest(BaseModel):
    session_id: str = ""
    story_text: str
    story_prompt: str = ""
    story_title: str = ""
    model: str = ""
    reviewers: list[str] | None = None


class ScriptReviewSaveRequest(BaseModel):
    review: dict


class ScriptReviewReconcileRequest(BaseModel):
    story_text: str
    review: dict


class ScriptReviewRewriteRequest(BaseModel):
    story_text: str
    story_prompt: str = ""
    story_title: str = ""
    model: str = ""
    review: dict


class ScriptReviewPlanRequest(BaseModel):
    story_text: str
    story_prompt: str = ""
    story_title: str = ""
    model: str = ""
    review: dict


class ScriptReviewPostAuditRequest(BaseModel):
    story_text: str
    story_prompt: str = ""
    story_title: str = ""
    model: str = ""
    review: dict
    applied_comment_ids: list[str] = []


class ScriptReviewApplyRequest(BaseModel):
    story_text: str
    story_prompt: str = ""
    story_title: str = ""
    model: str = ""
    review: dict


class ScriptReviewAutoRunRequest(BaseModel):
    max_iterations: int = 5
    story_text: str = ""
    review: dict | None = None


@dataclass(frozen=True)
class ReviewerSpec:
    id: str
    label: str
    focus: str
    categories: tuple[str, ...]


REVIEWERS = {
    "spoken_naturalness": ReviewerSpec(
        id="spoken_naturalness",
        label="Văn nói",
        focus=(
            "Bạn là reviewer chuyên văn nói/audio. Chỉ bắt các đoạn nghe gượng khi đọc thành lời, "
            "câu quá văn viết, thoại không tự nhiên, nhịp câu khó nghe, hoặc đoạn cần rewrite cho mượt."
        ),
        categories=("văn nói", "thoại", "nhịp đọc", "tự nhiên"),
    ),
    "logic_continuity": ReviewerSpec(
        id="logic_continuity",
        label="Logic",
        focus=(
            "Bạn là reviewer logic truyện. Bắt mâu thuẫn, động cơ nhân vật yếu, nguyên nhân-kết quả thiếu, "
            "chi tiết xuất hiện rồi mất, hành động vô lý, hoặc thông tin làm người nghe thấy cấn."
        ),
        categories=("logic", "động cơ", "mâu thuẫn", "liên tục"),
    ),
    "tension_pacing": ReviewerSpec(
        id="tension_pacing",
        label="Kịch tính",
        focus=(
            "Bạn là reviewer kịch tính/cao trào. Bắt đoạn bị đều, thiếu escalation, cao trào chưa đủ lực, "
            "lộ bí mật quá sớm, hoặc cần đổi thứ tự cảnh để suspense mạnh hơn."
        ),
        categories=("kịch tính", "cao trào", "pacing", "suspense"),
    ),
    "retention_hooks": ReviewerSpec(
        id="retention_hooks",
        label="Giữ chân",
        focus=(
            "Bạn là reviewer retention cho video/audio YouTube. Bắt hook yếu, đoạn giữa dễ rơi người nghe, "
            "thiếu câu hỏi kéo tiếp, thiếu cliffhanger, hoặc chuyển cảnh làm mất tò mò."
        ),
        categories=("hook", "retention", "curiosity gap", "cliffhanger"),
    ),
    "opening_hook": ReviewerSpec(
        id="opening_hook",
        label="Mở màn",
        focus=(
            "Bạn là reviewer cold-open, CHỈ soi 20-30 giây đầu (đoạn mở màn). "
            "Bắt các lỗi: 2-3 câu đầu KHÔNG có sự việc bất thường/bí ẩn/câu hỏi gây tò mò; "
            "mở đầu bằng lời chào kênh, giới thiệu bản thân, mô tả bối cảnh/thời tiết đời thường dài, "
            "hoặc giải thích trước nội dung sắp kể; vào truyện quá chậm khiến người nghe lướt đi. "
            "Mỗi comment phải kèm rewrite mở màn mạnh hơn."
        ),
        categories=("mở màn", "cold open", "hook", "tò mò"),
    ),
    "editor_in_chief": ReviewerSpec(
        id="editor_in_chief",
        label="Tổng biên tập",
        focus=(
            "Bạn là tổng biên tập. Đọc kết quả của các reviewer khác, loại trùng, xếp ưu tiên, "
            "giữ lại các comment thật sự đáng sửa để câu chuyện hay hơn."
        ),
        categories=("ưu tiên", "tổng thể", "biên tập"),
    ),
    "post_rewrite_audit": ReviewerSpec(
        id="post_rewrite_audit",
        label="Đối soát",
        focus="Kiểm tra mạch truyện sau khi một nhóm rewrite đã được áp dụng.",
        categories=("đối soát", "mạch truyện", "hậu rewrite"),
    ),
}


SPECIALIST_SYSTEM = """Bạn review kịch bản truyện audio tiếng Việt.
Mục tiêu là giúp truyện tự nhiên hơn khi đọc, logic hơn, kịch tính hơn, và giữ chân người nghe tốt hơn.
Không khen chung chung. Không rewrite toàn bộ truyện. Chỉ comment những chỗ đáng sửa.
Mỗi comment phải bám vào một quote ngắn có thật trong đoạn được đưa vào.
Return JSON hợp lệ, không markdown, không giải thích ngoài JSON.
Schema:
{
  "comments": [
    {
      "quote": "đoạn text gốc ngắn, copy y nguyên",
      "category": "một nhãn ngắn",
      "severity": "critical|important|minor",
      "issue": "vấn đề cụ thể",
      "why_it_matters": "vì sao làm truyện yếu đi",
      "suggestion": "hướng xử lý",
      "rewrite": "BẮT BUỘC. Bản thay thế trực tiếp cho quote, không được để rỗng."
    }
  ],
  "global_notes": ["nhận xét tổng thể ngắn"],
  "scores": {"naturalness": 1-10, "logic": 1-10, "tension": 1-10, "retention": 1-10, "hook": 1-10}
}
"""


REWRITE_SYSTEM = """You are a Vietnamese audio-drama line editor.
For every supplied review item, write one concrete, non-empty replacement that can directly replace its exact quote.
Keep the same local point of view, facts, tense, and continuity. Fix the stated issue while preserving the surrounding story.
Do not return advice, placeholders, brackets, explanations, or a full-story rewrite.
Return valid JSON only:
{"rewrites":[{"id":"comment id","rewrite":"direct replacement text"}]}
"""


MERGE_PLAN_SYSTEM = """You are the editor-in-chief for a Vietnamese audio-drama script.
You receive actionable review comments with direct rewrites. Build a safe application plan.
Resolve duplicate, overlapping, or semantically conflicting rewrites before they reach the writer.
Choose the strongest coherent change in each conflicted area. Keep only changes that work together across the story.
For every selected item, return a direct replacement for that item's exact quote. Do not combine unrelated locations into one rewrite.
Return valid JSON only:
{
  "summary":"short editorial rationale",
  "entries":[
    {"comment_id":"id", "action":"apply|skip", "rewrite":"direct replacement when apply", "sequence":1, "reason":"short reason"}
  ]
}
"""


POST_REWRITE_AUDIT_SYSTEM = """You are a final continuity editor for a Vietnamese audio-drama script.
Review the revised full story after a batch of edits has been applied. Identify only meaningful new or remaining problems in logic, continuity, spoken naturalness, tension, pacing, or retention.
Do not repeat issues that were already fixed. Every returned comment must target an exact quote in the revised story and include a non-empty direct replacement.
Return valid JSON only:
{
  "comments":[{
    "quote":"exact short quote from revised story",
    "category":"short label",
    "severity":"critical|important|minor",
    "issue":"specific problem",
    "why_it_matters":"why it hurts the story",
    "suggestion":"brief direction",
    "rewrite":"direct replacement for quote"
  }],
  "summary":"short audit result"
}
"""


EDITOR_SYSTEM = """Bạn là Tổng biên tập cho đội review kịch bản truyện audio tiếng Việt.
Input gồm truyện gốc và kết quả JSON từ các reviewer chuyên môn. Nhiệm vụ:
- Loại comment trùng hoặc quá nhỏ.
- Giữ comment có ảnh hưởng thật đến độ tự nhiên, logic, kịch tính, retention.
- Xếp priority cho các lỗi quan trọng nhất.
- Viết summary ngắn về chiến lược sửa.
Return JSON hợp lệ, không markdown.
Schema:
{
  "keep_comment_ids": ["id comment nên giữ"],
  "priority_comment_ids": ["id comment nên sửa trước"],
  "summary": {
    "overall_score": 1-10,
    "main_problems": ["..."],
    "fix_strategy": ["..."]
  }
}
"""


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _hash_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()[:16]


def _session_path(session_id: str) -> Path:
    if not session_id or not SAFE_SESSION_ID.match(session_id):
        raise HTTPException(400, "Invalid session id")
    return STORY_SESSIONS_DIR / f"{session_id}.json"


def _load_session(session_id: str) -> dict:
    path = _session_path(session_id)
    if not path.exists():
        raise HTTPException(404, "Session not found")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_session(data: dict) -> dict:
    data["updated_at"] = _now()
    STORY_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = _session_path(data["id"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def _session_review_payload(session_id: str) -> tuple[dict, dict, dict, str]:
    data = _load_session(session_id)
    story_data = data.get("story_data") if isinstance(data.get("story_data"), dict) else {}
    review = story_data.get("scriptReview") if isinstance(story_data.get("scriptReview"), dict) else {}
    return data, story_data, review, str(story_data.get("text") or "")


def _save_session_review(data: dict, story_data: dict, story_text: str, review: dict) -> dict:
    story_data = dict(story_data)
    story_data["text"] = story_text
    story_data["scriptReview"] = review
    data["story_data"] = story_data
    return _save_session(data)


def _review_cfg() -> dict:
    cfg = load_config()
    cfg.setdefault("script_review_model", "")
    cfg.setdefault("script_review_provider", "default")
    cfg.setdefault("script_review_base_url", "https://api.openai.com")
    cfg.setdefault("script_review_path", "/v1/chat/completions")
    cfg.setdefault("script_review_api_key", "")
    cfg.setdefault("script_review_api_key_path", "")
    cfg.setdefault("script_review_max_comments_per_agent", 12)
    cfg.setdefault("script_review_min_severity", "important")
    return cfg


def _call_reviewer_llm(system: str, user: str, model_override: str = "", max_tokens: int = 5000) -> str:
    cfg = _review_cfg()
    provider = (cfg.get("script_review_provider") or "default").strip().lower()
    configured_model = (cfg.get("script_review_model") or "").strip()
    if provider == "custom":
        model = configured_model or "gpt-4o-mini"
        return generate_text_openai_compatible(
            system,
            user,
            base_url=cfg.get("script_review_base_url") or "https://api.openai.com",
            path=cfg.get("script_review_path") or "/v1/chat/completions",
            model=model,
            api_key=cfg.get("script_review_api_key") or "",
            api_key_path=cfg.get("script_review_api_key_path") or "",
            max_tokens=max_tokens,
        )
    model = (configured_model or model_override or cfg.get("default_story_model") or "claude-sonnet-4-6").strip()
    return generate_text(
        system,
        user,
        model,
        cfg.get("anthropic_api_key", ""),
        cfg.get("ollama_url", "http://localhost:11434"),
        max_tokens=max_tokens,
    )


def _parse_json(text: str) -> dict:
    try:
        return _parse_json_response(text)
    except Exception:
        cleaned = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.IGNORECASE | re.MULTILINE).strip()
        return json.loads(cleaned)


def _fill_missing_rewrites(body: ScriptReviewRunRequest, review: dict) -> tuple[dict, int, int]:
    """Backfill actionable comments emitted by older or non-conforming reviewers."""
    comments = [dict(item) for item in (review.get("comments") or []) if isinstance(item, dict)]
    pending = [
        item for item in comments
        if item.get("status", "open") in {"open", "stale"}
        and str(item.get("quote") or "").strip()
        and not str(item.get("rewrite") or "").strip()
    ]
    filled_ids: set[str] = set()
    for start in range(0, len(pending), 5):
        batch = pending[start:start + 5]
        items = [
            {
                "id": str(item.get("id") or ""),
                "quote": str(item.get("quote") or "")[:500],
                "issue": str(item.get("issue") or "")[:700],
                "suggestion": str(item.get("suggestion") or "")[:900],
                "before": str(item.get("context_before") or "")[-240:],
                "after": str(item.get("context_after") or "")[:240],
            }
            for item in batch
        ]
        prompt = (
            f"Story title: {body.story_title or '(untitled)'}\n"
            f"Original writing brief: {body.story_prompt or '(not provided)'}\n\n"
            f"Review items:\n{json.dumps(items, ensure_ascii=False)}"
        )
        try:
            parsed = _parse_json(_call_reviewer_llm(REWRITE_SYSTEM, prompt, body.model, max_tokens=4500))
        except Exception:
            continue
        rewrites = parsed.get("rewrites") if isinstance(parsed, dict) else []
        by_id = {
            str(item.get("id") or ""): str(item.get("rewrite") or "").strip()[:1600]
            for item in rewrites or []
            if isinstance(item, dict) and str(item.get("rewrite") or "").strip()
        }
        for comment in comments:
            comment_id = str(comment.get("id") or "")
            rewrite = by_id.get(comment_id)
            if rewrite and not str(comment.get("rewrite") or "").strip():
                comment["rewrite"] = rewrite
                filled_ids.add(comment_id)
    review = dict(review)
    review["comments"] = comments
    review["updated_at"] = _now()
    remaining = sum(
        1 for item in comments
        if item.get("status", "open") in {"open", "stale"}
        and str(item.get("quote") or "").strip()
        and not str(item.get("rewrite") or "").strip()
    )
    return review, len(filled_ids), remaining


def _application_candidates(review: dict) -> list[dict]:
    return [
        dict(item) for item in (review.get("comments") or [])
        if isinstance(item, dict)
        and item.get("status", "open") in {"open", "stale"}
        and str(item.get("quote") or "").strip()
        and str(item.get("rewrite") or "").strip()
        and isinstance(item.get("start"), int)
        and isinstance(item.get("end"), int)
        and item["end"] > item["start"]
    ]


def _plan_sequence(value, fallback: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return fallback


def _set_application_plan(
    review: dict,
    body: ScriptReviewRunRequest,
    entries: list[dict],
    status: str,
    summary: str = "",
    excluded_comment_ids: list[str] | None = None,
) -> dict:
    result = dict(review)
    result["application_plan"] = {
        "status": status,
        "source_hash": _hash_text(body.story_text),
        "source_length": len(body.story_text),
        "generated_at": _now(),
        "summary": summary,
        "entries": entries,
        "excluded_comment_ids": excluded_comment_ids or [],
    }
    result["updated_at"] = _now()
    return result


def _build_application_plan(body: ScriptReviewRunRequest, review: dict) -> dict:
    candidates = _application_candidates(review)
    missing = [
        item for item in (review.get("comments") or [])
        if isinstance(item, dict)
        and item.get("status", "open") in {"open", "stale"}
        and str(item.get("quote") or "").strip()
        and not str(item.get("rewrite") or "").strip()
    ]
    if missing:
        return _set_application_plan(review, body, [], "waiting_rewrites", "Some actionable comments still need rewrites.")
    if not candidates:
        return _set_application_plan(review, body, [], "ready", "No actionable rewrites.")

    payload = [
        {
            "id": item["id"],
            "agent": item.get("agent", ""),
            "priority": bool(item.get("priority")),
            "severity": item.get("severity", "important"),
            "start": item["start"],
            "end": item["end"],
            "quote": item["quote"],
            "rewrite": item["rewrite"],
            "issue": item.get("issue", ""),
            "before": item.get("context_before", "")[-180:],
            "after": item.get("context_after", "")[:180],
        }
        for item in candidates
    ]
    if len(candidates) == 1:
        parsed = {"summary": "Only one actionable rewrite.", "entries": [{
            "comment_id": candidates[0]["id"],
            "action": "apply",
            "rewrite": candidates[0]["rewrite"],
            "sequence": 1,
            "reason": "Single actionable rewrite.",
        }]}
    else:
        user = (
            f"Story title: {body.story_title or '(untitled)'}\n"
            f"Original writing brief: {body.story_prompt or '(not provided)'}\n\n"
            f"Candidate rewrites:\n{json.dumps(payload, ensure_ascii=False)}"
        )
        try:
            parsed = _parse_json(_call_reviewer_llm(MERGE_PLAN_SYSTEM, user, body.model, max_tokens=6000))
        except Exception as exc:
            return _set_application_plan(review, body, [], "failed", str(exc)[:500])

    by_id = {str(item["id"]): item for item in candidates}
    raw_entries = parsed.get("entries") if isinstance(parsed, dict) else []
    selected = []
    seen = set()
    raw_entries = [item for item in raw_entries or [] if isinstance(item, dict)]
    for raw in sorted(raw_entries, key=lambda item: _plan_sequence(item.get("sequence"), 10**6)):
        if str(raw.get("action") or "").lower() != "apply":
            continue
        comment_id = str(raw.get("comment_id") or "")
        comment = by_id.get(comment_id)
        if not comment or comment_id in seen:
            continue
        rewrite = str(raw.get("rewrite") or comment["rewrite"]).strip()[:1600]
        if not rewrite:
            continue
        selected.append({
            "comment_id": comment_id,
            "rewrite": rewrite,
            "sequence": _plan_sequence(raw.get("sequence"), len(selected) + 1),
            "priority": bool(comment.get("priority")),
            "start": comment["start"],
            "end": comment["end"],
            "reason": str(raw.get("reason") or "")[:500],
        })
        seen.add(comment_id)

    selected.sort(key=lambda item: (item["sequence"], 0 if item["priority"] else 1, item["start"]))
    non_overlapping = []
    for entry in selected:
        if any(entry["start"] < other["end"] and entry["end"] > other["start"] for other in non_overlapping):
            continue
        non_overlapping.append(entry)
    summary = str(parsed.get("summary") or "")[:1000] if isinstance(parsed, dict) else ""
    selected_ids = {entry["comment_id"] for entry in non_overlapping}
    excluded_ids = [item["id"] for item in candidates if item["id"] not in selected_ids]
    return _set_application_plan(review, body, non_overlapping, "ready", summary, excluded_ids)


def _reconcile_review_for_text(story_text: str, review: dict) -> dict:
    terminal = {"applied", "superseded", "resolved", "ignored"}
    next_comments = []
    for raw in review.get("comments") or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        if item.get("status") in terminal:
            next_comments.append(item)
            continue
        start, end = _locate_quote(story_text, item.get("quote", ""), 0)
        item["start"] = start
        item["end"] = end
        item["status"] = "open" if start >= 0 else "stale"
        before, after = _context(story_text, start, end) if start >= 0 else ("", "")
        item["context_before"] = before
        item["context_after"] = after
        next_comments.append(item)
    result = dict(review)
    result["comments"] = next_comments
    result["source_hash"] = _hash_text(story_text)
    result["source_length"] = len(story_text)
    result["updated_at"] = _now()
    return result


def _apply_application_plan(body: ScriptReviewRunRequest, review: dict) -> tuple[str, dict, dict]:
    review = _reconcile_review_for_text(body.story_text, review)
    plan = review.get("application_plan") if isinstance(review.get("application_plan"), dict) else {}
    if plan.get("status") != "ready" or plan.get("source_hash") != _hash_text(body.story_text):
        review = _build_application_plan(body, review)
        plan = review.get("application_plan") or {}
    if plan.get("status") != "ready":
        return body.story_text, review, {"status": "blocked", "reason": plan.get("summary") or "Application plan is unavailable."}

    comment_by_id = {str(item.get("id")): item for item in review.get("comments") or [] if isinstance(item, dict)}
    entries = [item for item in plan.get("entries") or [] if isinstance(item, dict) and str(item.get("comment_id") or "") in comment_by_id]
    if not entries:
        excluded_ids = {str(item) for item in plan.get("excluded_comment_ids") or []}
        review["comments"] = [
            {**item, "status": "superseded"} if str(item.get("id")) in excluded_ids else item
            for item in review.get("comments") or []
            if isinstance(item, dict)
        ]
        review = _reconcile_review_for_text(body.story_text, review)
        return body.story_text, review, {"status": "no_changes", "applied_ids": [], "stale_ids": [], "excluded_ids": sorted(excluded_ids)}

    original_text = body.story_text
    next_text = body.story_text
    applied_ids: set[str] = set()
    stale_ids: set[str] = set()
    occupied_ranges = []
    entries_by_id = {str(item["comment_id"]): item for item in entries}
    for entry in sorted(entries, key=lambda item: int(item.get("start") or 0), reverse=True):
        comment_id = str(entry["comment_id"])
        comment = comment_by_id[comment_id]
        start, end = int(comment.get("start", -1)), int(comment.get("end", -1))
        if start < 0 or end <= start or any(start < other_end and end > other_start for other_start, other_end in occupied_ranges):
            stale_ids.add(comment_id)
            continue
        if original_text[start:end] != comment.get("quote"):
            stale_ids.add(comment_id)
            continue
        rewrite = str(entry.get("rewrite") or comment.get("rewrite") or "").strip()
        if not rewrite:
            stale_ids.add(comment_id)
            continue
        next_text = next_text[:start] + rewrite + next_text[end:]
        occupied_ranges.append((start, end))
        applied_ids.add(comment_id)

    excluded_ids = {str(item) for item in plan.get("excluded_comment_ids") or []}
    next_comments = []
    for raw in review.get("comments") or []:
        item = dict(raw)
        comment_id = str(item.get("id"))
        if comment_id in applied_ids:
            item["status"] = "applied"
            item["rewrite"] = str(entries_by_id[comment_id].get("rewrite") or item.get("rewrite") or "").strip()
        elif comment_id in stale_ids:
            item["status"] = "stale"
        elif comment_id in excluded_ids:
            item["status"] = "superseded"
        next_comments.append(item)
    review = dict(review)
    review["comments"] = next_comments
    review["application_plan"] = {**plan, "status": "applied" if applied_ids else "ready", "applied_comment_ids": sorted(applied_ids)}
    review = _reconcile_review_for_text(next_text, review)
    return next_text, review, {
        "status": "applied" if applied_ids else "no_changes",
        "applied_ids": sorted(applied_ids),
        "stale_ids": sorted(stale_ids),
        "excluded_ids": sorted(excluded_ids),
    }


def _run_post_rewrite_audit(body: ScriptReviewRunRequest, review: dict, applied_comment_ids: list[str]) -> dict:
    applied = {str(item) for item in applied_comment_ids}
    applied_changes = [
        {
            "id": item.get("id"),
            "quote": item.get("quote"),
            "rewrite": item.get("rewrite"),
            "issue": item.get("issue"),
        }
        for item in (review.get("comments") or [])
        if isinstance(item, dict) and str(item.get("id")) in applied
    ]
    user = (
        f"Story title: {body.story_title or '(untitled)'}\n"
        f"Original writing brief: {body.story_prompt or '(not provided)'}\n\n"
        f"Applied changes:\n{json.dumps(applied_changes, ensure_ascii=False)}\n\n"
        f"Revised full story:\n{body.story_text}"
    )
    try:
        parsed = _parse_json(_call_reviewer_llm(POST_REWRITE_AUDIT_SYSTEM, user, body.model, max_tokens=6000))
    except Exception as exc:
        return {"status": "failed", "error": str(exc)[:500], "comments": []}
    comments = []
    for raw in (parsed.get("comments") or []) if isinstance(parsed, dict) else []:
        if not isinstance(raw, dict):
            continue
        comment = _normalize_comment(raw, "post_rewrite_audit", body.story_text, 0, 0)
        if comment and _passes_min_severity(comment["severity"], "important"):
            comments.append(comment)
    return {
        "status": "done",
        "summary": str(parsed.get("summary") or "")[:1000] if isinstance(parsed, dict) else "",
        "comments": _dedupe_comments(comments)[:10],
    }


def _auto_run_state(review: dict) -> dict:
    state = review.get("auto_review_run") if isinstance(review.get("auto_review_run"), dict) else {}
    return dict(state)


def _set_auto_run_state(review: dict, **updates) -> dict:
    result = dict(review)
    state = _auto_run_state(result)
    state.update(updates)
    state["updated_at"] = _now()
    result["auto_review_run"] = state
    result["updated_at"] = _now()
    return result


def _append_auto_event(review: dict, step: str, message: str, **details) -> dict:
    state = _auto_run_state(review)
    events = list(state.get("events") or [])
    events.append({"at": _now(), "step": step, "message": message, **details})
    return _set_auto_run_state(review, events=events[-40:])


def _auto_stop_requested(session_id: str, run_id: str) -> bool:
    try:
        _, _, review, _ = _session_review_payload(session_id)
        state = _auto_run_state(review)
        return state.get("run_id") == run_id and bool(state.get("stop_requested"))
    except Exception:
        return True


def _save_auto_progress(session_id: str, data: dict, story_data: dict, story_text: str, review: dict) -> dict:
    state = _auto_run_state(review)
    if state.get("status") in {"running", "stopping"}:
        try:
            _, _, current_review, _ = _session_review_payload(session_id)
            current_state = _auto_run_state(current_review)
            if current_state.get("run_id") == state.get("run_id") and current_state.get("stop_requested"):
                review = _set_auto_run_state(review, status="stopping", stop_requested=True)
        except Exception:
            pass
    _save_session_review(data, story_data, story_text, review)
    return review


def _mark_auto_terminal(session_id: str, run_id: str, status: str, message: str, **details) -> None:
    data, story_data, review, story_text = _session_review_payload(session_id)
    state = _auto_run_state(review)
    if state.get("run_id") != run_id:
        return
    review = _set_auto_run_state(review, status=status, current_step="done", completed_at=_now(), **details)
    review = _append_auto_event(review, "done", message, status=status, **details)
    _save_auto_progress(session_id, data, story_data, story_text, review)


def _run_auto_review(session_id: str, run_id: str) -> None:
    try:
        for _ in range(5):
            data, story_data, review, story_text = _session_review_payload(session_id)
            state = _auto_run_state(review)
            if state.get("run_id") != run_id:
                return
            if _auto_stop_requested(session_id, run_id):
                _mark_auto_terminal(session_id, run_id, "stopped", "Stopped before the next step.")
                return
            iteration = int(state.get("iteration") or 0) + 1
            max_iterations = max(1, min(5, int(state.get("max_iterations") or 5)))
            if iteration > max_iterations:
                _mark_auto_terminal(session_id, run_id, "max_iterations", "Reached the automatic review limit.")
                return

            run = ScriptReviewRunRequest(
                session_id=session_id,
                story_text=story_text,
                story_prompt=str(story_data.get("prompt") or ""),
                story_title=str(story_data.get("storyTitle") or data.get("story_title") or data.get("title") or ""),
                model=str(story_data.get("model") or ""),
            )
            review = _set_auto_run_state(review, status="running", iteration=iteration, max_iterations=max_iterations, current_step="prepare")
            review = _append_auto_event(review, "prepare", f"Preparing iteration {iteration}/{max_iterations}.")
            _save_auto_progress(session_id, data, story_data, story_text, review)

            review = _reconcile_review_for_text(story_text, review)
            review, generated, remaining = _fill_missing_rewrites(run, review)
            review = _append_auto_event(review, "rewrites", "Completed missing rewrite pass.", generated=generated, remaining=remaining)
            _save_auto_progress(session_id, data, story_data, story_text, review)
            if _auto_stop_requested(session_id, run_id):
                _mark_auto_terminal(session_id, run_id, "stopped", "Stopped after rewrite preparation.")
                return
            if remaining:
                review = _set_auto_run_state(review, status="blocked", current_step="rewrites", blocked_reason="Some comments still have no rewrite.")
                _save_auto_progress(session_id, data, story_data, story_text, review)
                return

            data, story_data, review, story_text = _session_review_payload(session_id)
            run.story_text = story_text
            review = _set_auto_run_state(review, status="running", current_step="plan")
            review = _build_application_plan(run, review)
            plan_status = (review.get("application_plan") or {}).get("status")
            review = _append_auto_event(review, "plan", "Editor-in-chief completed the application plan.", plan_status=plan_status)
            _save_auto_progress(session_id, data, story_data, story_text, review)
            if _auto_stop_requested(session_id, run_id):
                _mark_auto_terminal(session_id, run_id, "stopped", "Stopped after the application plan.")
                return
            if plan_status != "ready":
                review = _set_auto_run_state(review, status="blocked", current_step="plan", blocked_reason=(review.get("application_plan") or {}).get("summary") or "Plan is unavailable.")
                _save_auto_progress(session_id, data, story_data, story_text, review)
                return

            data, story_data, review, story_text = _session_review_payload(session_id)
            run.story_text = story_text
            review = _set_auto_run_state(review, status="running", current_step="apply")
            next_text, review, applied = _apply_application_plan(run, review)
            review = _append_auto_event(review, "apply", "Applied the safe rewrite plan.", **applied)
            _save_auto_progress(session_id, data, story_data, next_text, review)
            if _auto_stop_requested(session_id, run_id):
                _mark_auto_terminal(session_id, run_id, "stopped", "Stopped after applying the rewrite plan.")
                return
            if applied.get("status") != "applied":
                remaining_open = [item for item in review.get("comments") or [] if isinstance(item, dict) and item.get("status", "open") == "open"]
                if remaining_open:
                    review = _set_auto_run_state(review, status="blocked", current_step="apply", blocked_reason="The plan produced no safe changes.")
                    _save_auto_progress(session_id, data, story_data, next_text, review)
                    return

            data, story_data, review, story_text = _session_review_payload(session_id)
            run.story_text = story_text
            review = _set_auto_run_state(review, status="running", current_step="audit")
            audit = _run_post_rewrite_audit(run, review, applied.get("applied_ids") or [])
            if audit.get("status") != "done":
                review = _set_auto_run_state(review, status="blocked", current_step="audit", blocked_reason=audit.get("error") or "Audit failed.")
                review = _append_auto_event(review, "audit", "Audit failed.", error=audit.get("error", ""))
                _save_auto_progress(session_id, data, story_data, story_text, review)
                return
            review["comments"] = [*(review.get("comments") or []), *(audit.get("comments") or [])]
            review["post_rewrite_audit"] = {
                "status": "done",
                "summary": audit.get("summary", ""),
                "comment_count": len(audit.get("comments") or []),
                "updated_at": _now(),
            }
            review = _reconcile_review_for_text(story_text, review)
            review = _append_auto_event(review, "audit", "Completed post-rewrite audit.", comment_count=len(audit.get("comments") or []))
            _save_auto_progress(session_id, data, story_data, story_text, review)
            if _auto_stop_requested(session_id, run_id):
                _mark_auto_terminal(session_id, run_id, "stopped", "Stopped after completing the audit.")
                return
            if not audit.get("comments"):
                _mark_auto_terminal(session_id, run_id, "passed", "Audit passed with no important remaining comments.")
                return
        _mark_auto_terminal(session_id, run_id, "max_iterations", "Reached the automatic review limit.")
    except Exception as exc:
        try:
            _mark_auto_terminal(session_id, run_id, "blocked", "Automatic review stopped because of an error.", blocked_reason=str(exc)[:800])
        except Exception:
            pass
    finally:
        with AUTO_RUN_LOCK:
            AUTO_RUN_WORKERS.discard(session_id)


def _chunks(text: str, max_chars: int = 9000, overlap: int = 500) -> list[dict]:
    if len(text) <= max_chars:
        return [{"index": 0, "start": 0, "end": len(text), "text": text}]
    chunks = []
    start = 0
    idx = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            split = max(text.rfind("\n\n", start, end), text.rfind(". ", start, end), text.rfind("。", start, end))
            if split > start + max_chars * 0.55:
                end = split + 1
        chunks.append({"index": idx, "start": start, "end": end, "text": text[start:end]})
        if end >= len(text):
            break
        start = max(0, end - overlap)
        idx += 1
    return chunks


def _opening_hook_chunk(text: str, max_chars: int = 650) -> dict:
    """Approximate the first 20-30 seconds without pretending a 9k-char chunk is a cold open."""
    sample = text[:max_chars]
    boundary = max(sample.rfind(mark) for mark in ".!?\n")
    if boundary >= max_chars // 2:
        sample = sample[:boundary + 1]
    return {"index": 0, "start": 0, "end": len(sample), "text": sample}


def _context(text: str, start: int, end: int) -> tuple[str, str]:
    return text[max(0, start - 160):start].strip(), text[end:min(len(text), end + 160)].strip()


def _locate_quote(full_text: str, quote: str, chunk_start: int = 0) -> tuple[int, int]:
    raw = (quote or "").strip()
    if not raw:
        return -1, -1
    idx = full_text.find(raw, max(0, chunk_start - 50))
    if idx < 0:
        idx = full_text.find(raw)
    if idx >= 0:
        return idx, idx + len(raw)
    compact_quote = re.sub(r"\s+", " ", raw)
    if len(compact_quote) < 12:
        return -1, -1
    compact_full = re.sub(r"\s+", " ", full_text)
    compact_idx = compact_full.find(compact_quote)
    if compact_idx < 0:
        return -1, -1
    prefix = compact_full[:compact_idx]
    approx_start = len(prefix)
    window_start = max(0, approx_start - 500)
    window_end = min(len(full_text), approx_start + len(raw) + 500)
    idx = full_text.find(raw[: min(len(raw), 40)], window_start, window_end)
    if idx >= 0:
        return idx, min(len(full_text), idx + len(raw))
    return -1, -1


def _normalize_severity(value: str) -> str:
    raw = (value or "").strip().lower()
    if raw in SEVERITY_ORDER:
        return raw
    if raw in {"high", "severe", "nặng"}:
        return "critical"
    if raw in {"medium", "med", "vừa"}:
        return "important"
    return "minor"


def _passes_min_severity(severity: str, minimum: str) -> bool:
    return SEVERITY_ORDER.get(severity, 2) <= SEVERITY_ORDER.get((minimum or "important").lower(), 1)


def _normalize_comment(item: dict, agent_id: str, full_text: str, chunk_start: int, chunk_index: int) -> dict | None:
    quote = re.sub(r"\s+", " ", str(item.get("quote") or "").strip())
    start, end = _locate_quote(full_text, quote, chunk_start)
    severity = _normalize_severity(str(item.get("severity") or "important"))
    before, after = _context(full_text, start, end) if start >= 0 else ("", "")
    if not quote or len(quote) < 8:
        return None
    return {
        "id": f"c_{uuid.uuid4().hex[:10]}",
        "agent": agent_id,
        "category": str(item.get("category") or "").strip()[:40] or REVIEWERS[agent_id].categories[0],
        "severity": severity,
        "quote": quote[:500],
        "start": start,
        "end": end,
        "context_before": before,
        "context_after": after,
        "issue": str(item.get("issue") or "").strip()[:900],
        "why_it_matters": str(item.get("why_it_matters") or "").strip()[:900],
        "suggestion": str(item.get("suggestion") or "").strip()[:1200],
        "rewrite": str(item.get("rewrite") or "").strip()[:1600],
        "status": "open" if start >= 0 else "stale",
        "chunk_index": chunk_index,
        "priority": False,
    }


def _dedupe_comments(comments: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for item in sorted(comments, key=lambda x: (x.get("start", 10**9), SEVERITY_ORDER.get(x.get("severity"), 2))):
        quote_key = re.sub(r"\W+", "", (item.get("quote") or "").lower())[:80]
        key = (quote_key, item.get("category", "").lower(), item.get("start", -1) // 120 if item.get("start", -1) >= 0 else -1)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _agent_user_prompt(spec: ReviewerSpec, body: ScriptReviewRunRequest, chunk: dict, max_comments: int) -> str:
    return f"""Vai trò reviewer: {spec.label}
Tiêu chí riêng: {spec.focus}

Tên truyện: {body.story_title or "Chưa đặt tên"}
Prompt gốc dùng để tạo/paste kịch bản:
{body.story_prompt or "(không có prompt gốc)"}

Đây là chunk {chunk['index'] + 1}, offset {chunk['start']}..{chunk['end']} trong toàn truyện.
Hãy trả tối đa {max_comments} comment cho chunk này. Ưu tiên lỗi có tác động thật.
Mỗi comment phải có rewrite không rỗng, có thể thay thế trực tiếp quote. Nếu không viết được bản thay thế cụ thể, đừng tạo comment đó.

KỊCH BẢN CHUNK:
{chunk['text']}
"""


def _run_specialist(spec: ReviewerSpec, body: ScriptReviewRunRequest, chunks: list[dict], max_comments: int, min_severity: str) -> dict:
    comments = []
    notes = []
    scores = {}
    # The cold-open reviewer receives only an approximately 20-30 second excerpt.
    scan_chunks = [_opening_hook_chunk(body.story_text)] if spec.id == "opening_hook" else chunks
    for chunk in scan_chunks:
        text = _call_reviewer_llm(
            SPECIALIST_SYSTEM + "\n\n" + spec.focus,
            _agent_user_prompt(spec, body, chunk, max(1, min(max_comments, 8))),
            body.model,
            max_tokens=5000,
        )
        try:
            parsed = _parse_json(text)
        except Exception:
            repaired = _call_reviewer_llm(
                "Bạn sửa output thành JSON hợp lệ theo schema đã yêu cầu. Không thêm markdown.",
                text,
                body.model,
                max_tokens=3000,
            )
            parsed = _parse_json(repaired)
        for raw in parsed.get("comments") or []:
            if not isinstance(raw, dict):
                continue
            comment = _normalize_comment(raw, spec.id, body.story_text, chunk["start"], chunk["index"])
            if comment and _passes_min_severity(comment["severity"], min_severity):
                comments.append(comment)
        notes.extend([str(x) for x in (parsed.get("global_notes") or []) if str(x).strip()])
        if isinstance(parsed.get("scores"), dict):
            scores.update(parsed.get("scores"))
        if len(comments) >= max_comments:
            comments = comments[:max_comments]
            break
    return {
        "agent_id": spec.id,
        "label": spec.label,
        "status": "done",
        "comments": comments[:max_comments],
        "global_notes": notes[:8],
        "scores": scores,
    }


def _run_editor(body: ScriptReviewRunRequest, comments: list[dict], agent_results: list[dict]) -> dict:
    compact_comments = [
        {
            "id": c["id"],
            "agent": c["agent"],
            "severity": c["severity"],
            "quote": c["quote"],
            "issue": c["issue"],
            "suggestion": c["suggestion"],
        }
        for c in comments[:80]
    ]
    user = f"""Tên truyện: {body.story_title or "Chưa đặt tên"}
Prompt gốc:
{body.story_prompt or "(không có)"}

Kịch bản tóm tắt đầu/cuối để hiểu tổng thể:
{body.story_text[:3500]}

...

{body.story_text[-2500:] if len(body.story_text) > 2500 else ""}

Kết quả reviewer chuyên môn:
{json.dumps({"agent_results": agent_results, "comments": compact_comments}, ensure_ascii=False)}
"""
    try:
        raw = _call_reviewer_llm(EDITOR_SYSTEM, user, body.model, max_tokens=5000)
        parsed = _parse_json(raw)
    except Exception as exc:
        return {
            "agent_id": "editor_in_chief",
            "label": REVIEWERS["editor_in_chief"].label,
            "status": "failed",
            "error": str(exc),
            "keep_comment_ids": [c["id"] for c in comments],
            "priority_comment_ids": [c["id"] for c in comments[:8]],
            "summary": {
                "overall_score": 0,
                "main_problems": ["Tổng biên tập lỗi, đang giữ kết quả reviewer chuyên môn."],
                "fix_strategy": [],
            },
        }
    keep = parsed.get("keep_comment_ids") or [c["id"] for c in comments]
    priority = parsed.get("priority_comment_ids") or keep[:8]
    return {
        "agent_id": "editor_in_chief",
        "label": REVIEWERS["editor_in_chief"].label,
        "status": "done",
        "keep_comment_ids": [str(x) for x in keep],
        "priority_comment_ids": [str(x) for x in priority],
        "summary": parsed.get("summary") if isinstance(parsed.get("summary"), dict) else {},
    }


def _save_review_to_session(session_id: str, review: dict) -> None:
    if not session_id:
        return
    data = _load_session(session_id)
    story_data = data.get("story_data") if isinstance(data.get("story_data"), dict) else {}
    story_data["scriptReview"] = review
    data["story_data"] = story_data
    _save_session(data)


def _build_review(body: ScriptReviewRunRequest, status: str = "running") -> dict:
    return {
        "run_id": f"review_{uuid.uuid4().hex[:12]}",
        "source_hash": _hash_text(body.story_text),
        "source_length": len(body.story_text),
        "prompt_hash": _hash_text(body.story_prompt),
        "status": status,
        "reviewers": [
            {"id": REVIEWERS[r].id, "label": REVIEWERS[r].label, "status": "queued"}
            for r in (*SPECIALIST_IDS, "editor_in_chief")
        ],
        "agent_results": [],
        "comments": [],
        "summary": {},
        "created_at": _now(),
        "updated_at": _now(),
    }


def _event_stream(body: ScriptReviewRunRequest) -> Generator[str, None, None]:
    if not body.story_text.strip():
        yield _sse({"type": "error", "message": "Story text is required"})
        yield "data: [DONE]\n\n"
        return
    cfg = _review_cfg()
    max_comments = max(1, min(40, int(cfg.get("script_review_max_comments_per_agent") or 12)))
    min_severity = (cfg.get("script_review_min_severity") or "important").lower()
    wanted = set(body.reviewers or SPECIALIST_IDS)
    active_ids = [rid for rid in SPECIALIST_IDS if rid in wanted and rid in REVIEWERS]
    if not active_ids:
        active_ids = list(SPECIALIST_IDS)
    chunks = _chunks(body.story_text)
    review = _build_review(body)
    review["chunk_count"] = len(chunks)
    yield _sse({"type": "start", "review": review, "chunk_count": len(chunks)})

    agent_results: list[dict] = []
    all_comments: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(active_ids))) as pool:
        futures = {}
        for rid in active_ids:
            spec = REVIEWERS[rid]
            yield _sse({"type": "agent_start", "agent": {"id": spec.id, "label": spec.label}})
            futures[pool.submit(_run_specialist, spec, body, chunks, max_comments, min_severity)] = spec
        for future in concurrent.futures.as_completed(futures):
            spec = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"agent_id": spec.id, "label": spec.label, "status": "failed", "error": str(exc), "comments": []}
            agent_results.append(result)
            all_comments.extend(result.get("comments") or [])
            for comment in result.get("comments") or []:
                yield _sse({"type": "agent_comment", "agent_id": spec.id, "comment": comment})
            yield _sse({"type": "agent_done", "result": result})

    merged = _dedupe_comments(all_comments)
    yield _sse({"type": "editor_start", "agent": {"id": "editor_in_chief", "label": REVIEWERS["editor_in_chief"].label}})
    editor = _run_editor(body, merged, agent_results) if merged else {
        "agent_id": "editor_in_chief",
        "label": REVIEWERS["editor_in_chief"].label,
        "status": "done",
        "keep_comment_ids": [],
        "priority_comment_ids": [],
        "summary": {"overall_score": 8, "main_problems": [], "fix_strategy": ["Không có comment quan trọng từ reviewer."]},
    }
    keep = set(editor.get("keep_comment_ids") or [c["id"] for c in merged])
    priority = set(editor.get("priority_comment_ids") or [])
    final_comments = []
    for comment in merged:
        if comment["id"] not in keep:
            continue
        comment = dict(comment)
        comment["priority"] = comment["id"] in priority
        final_comments.append(comment)
    review.update({
        "status": "done",
        "agent_results": agent_results + [editor],
        "comments": final_comments,
        "summary": editor.get("summary") or {},
        "updated_at": _now(),
    })
    review, _, _ = _fill_missing_rewrites(body, review)
    review = _build_application_plan(body, review)
    final_comments = review["comments"]
    try:
        _save_review_to_session(body.session_id, review)
    except Exception:
        pass
    yield _sse({"type": "summary", "summary": review["summary"], "comments": final_comments})
    yield _sse({"type": "done", "review": review})
    yield "data: [DONE]\n\n"


@router.post("/api/script-review/run")
def run_script_review(body: ScriptReviewRunRequest):
    return StreamingResponse(_event_stream(body), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.patch("/api/story-sessions/{session_id}/review")
def save_script_review(session_id: str, body: ScriptReviewSaveRequest):
    data = _load_session(session_id)
    story_data = data.get("story_data") if isinstance(data.get("story_data"), dict) else {}
    story_data["scriptReview"] = body.review
    data["story_data"] = story_data
    return _save_session(data)


@router.post("/api/script-review/rewrites")
def complete_script_review_rewrites(body: ScriptReviewRewriteRequest):
    run = ScriptReviewRunRequest(
        story_text=body.story_text,
        story_prompt=body.story_prompt,
        story_title=body.story_title,
        model=body.model,
    )
    review, generated, remaining = _fill_missing_rewrites(run, dict(body.review or {}))
    review = _build_application_plan(run, review)
    return {"review": review, "generated": generated, "remaining": remaining}


@router.post("/api/script-review/application-plan")
def build_script_review_application_plan(body: ScriptReviewPlanRequest):
    run = ScriptReviewRunRequest(
        story_text=body.story_text,
        story_prompt=body.story_prompt,
        story_title=body.story_title,
        model=body.model,
    )
    return {"review": _build_application_plan(run, dict(body.review or {}))}


@router.post("/api/script-review/apply-plan")
def apply_script_review_plan(body: ScriptReviewApplyRequest):
    run = ScriptReviewRunRequest(
        story_text=body.story_text,
        story_prompt=body.story_prompt,
        story_title=body.story_title,
        model=body.model,
    )
    story_text, review, result = _apply_application_plan(run, dict(body.review or {}))
    return {"story_text": story_text, "review": review, "result": result}


def _auto_run_response(session_id: str) -> dict:
    data, story_data, review, story_text = _session_review_payload(session_id)
    state = _auto_run_state(review)
    if state.get("status") in {"running", "stopping"}:
        with AUTO_RUN_LOCK:
            alive = session_id in AUTO_RUN_WORKERS
        if not alive:
            review = _set_auto_run_state(review, status="interrupted", current_step="done", interrupted_at=_now())
            review = _append_auto_event(review, "done", "Backend restarted before this workflow completed.", status="interrupted")
            _save_auto_progress(session_id, data, story_data, story_text, review)
            state = _auto_run_state(review)
    return {"story_text": story_text, "review": review, "auto_review_run": state}


@router.post("/api/story-sessions/{session_id}/script-review/auto-run")
def start_script_review_auto_run(session_id: str, body: ScriptReviewAutoRunRequest):
    data, story_data, review, story_text = _session_review_payload(session_id)
    if body.story_text.strip():
        story_text = body.story_text
    if isinstance(body.review, dict):
        review = body.review
    if not story_text.strip():
        raise HTTPException(400, "Story text is required")
    with AUTO_RUN_LOCK:
        if session_id in AUTO_RUN_WORKERS:
            raise HTTPException(409, "Automatic review is already running for this session")
        run_id = f"auto_{uuid.uuid4().hex[:12]}"
        review = _set_auto_run_state(
            review,
            run_id=run_id,
            status="running",
            current_step="queued",
            iteration=0,
            max_iterations=max(1, min(5, int(body.max_iterations or 5))),
            stop_requested=False,
            started_at=_now(),
            completed_at="",
            blocked_reason="",
            source_hash=_hash_text(story_text),
            events=[],
        )
        review = _append_auto_event(review, "queued", "Automatic apply and audit workflow started.")
        _save_auto_progress(session_id, data, story_data, story_text, review)
        AUTO_RUN_WORKERS.add(session_id)
        threading.Thread(target=_run_auto_review, args=(session_id, run_id), daemon=True, name=f"script-review-{session_id[:8]}").start()
    return _auto_run_response(session_id)


@router.get("/api/story-sessions/{session_id}/script-review/auto-run")
def get_script_review_auto_run(session_id: str):
    return _auto_run_response(session_id)


@router.post("/api/story-sessions/{session_id}/script-review/auto-run/stop")
def stop_script_review_auto_run(session_id: str):
    data, story_data, review, story_text = _session_review_payload(session_id)
    state = _auto_run_state(review)
    if state.get("status") not in {"running", "stopping"}:
        raise HTTPException(409, "Automatic review is not running")
    review = _set_auto_run_state(review, status="stopping", stop_requested=True)
    review = _append_auto_event(review, state.get("current_step") or "running", "Stop requested; the current step will finish first.")
    _save_auto_progress(session_id, data, story_data, story_text, review)
    return _auto_run_response(session_id)


@router.post("/api/script-review/post-apply-audit")
def post_apply_script_review_audit(body: ScriptReviewPostAuditRequest):
    run = ScriptReviewRunRequest(
        story_text=body.story_text,
        story_prompt=body.story_prompt,
        story_title=body.story_title,
        model=body.model,
    )
    review = dict(body.review or {})
    audit = _run_post_rewrite_audit(run, review, body.applied_comment_ids)
    if audit["status"] == "done":
        review["comments"] = [*(review.get("comments") or []), *(audit.get("comments") or [])]
    review["post_rewrite_audit"] = {
        "status": audit["status"],
        "summary": audit.get("summary", ""),
        "error": audit.get("error", ""),
        "comment_count": len(audit.get("comments") or []),
        "updated_at": _now(),
    }
    review["source_hash"] = _hash_text(body.story_text)
    review["source_length"] = len(body.story_text)
    review["updated_at"] = _now()
    return {"review": review, "audit": review["post_rewrite_audit"]}


@router.post("/api/script-review/reconcile")
def reconcile_script_review(body: ScriptReviewReconcileRequest):
    return {"review": _reconcile_review_for_text(body.story_text, dict(body.review or {}))}
