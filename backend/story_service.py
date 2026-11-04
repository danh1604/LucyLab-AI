"""Story generation service — Anthropic Claude + Ollama providers."""
import json
import os
import requests
import shutil
import subprocess
import tempfile
from typing import Generator
from pathlib import Path
from urllib.parse import urlparse


ANTHROPIC_MODELS = [
    {"id": "claude-sonnet-4-6",         "label": "Claude Sonnet 4.6 (local config)"},
    {"id": "claude-opus-4-8",            "label": "Claude Opus 4.8 (local config)"},
    {"id": "claude-haiku-4-5-20251001",  "label": "Claude Haiku 4.5 (local config)"},
]

CODEX_MODELS = [
    {"id": "codex:gpt-5.5", "label": "Codex GPT-5.5"},
    {"id": "codex:gpt-5.5-pro", "label": "Codex GPT-5.5 Pro"},
    {"id": "codex:gpt-5.4", "label": "Codex GPT-5.4"},
    {"id": "codex:gpt-5.4-mini", "label": "Codex GPT-5.4 Mini"},
    {"id": "codex:gpt-5.3-codex", "label": "Codex GPT-5.3 Codex"},
    {"id": "codex:gpt-5.2-codex", "label": "Codex GPT-5.2 Codex"},
    {"id": "codex:gpt-5.1-codex", "label": "Codex GPT-5.1 Codex"},
    {"id": "codex:gpt-5-codex", "label": "Codex GPT-5 Codex"},
]

OPENCODE_MODEL_PREFIXES = (
    "opencode/",
    "agentrouter/",
    "agentrouter-proxy/",
    "openai/",
    "anthropic/",
    "google/",
    "zai/",
    "minimax-coding-plan/",
)


def _cli_path(name: str) -> str | None:
    return shutil.which(f"{name}.cmd") or shutil.which(name)

_ANALYZE_PROMPT = """You are a professional story editor. Reply in the primary language of the user's prompt. Người dùng cung cấp một đoạn mô tả/hướng dẫn.
Hãy phân tích xem đoạn này đã đủ thông tin để viết một câu chuyện hoàn chỉnh chưa.

"Đủ thông tin" nghĩa là có ít nhất: bối cảnh (thời gian/địa điểm), nhân vật chính, và một cốt truyện cơ bản.
Nếu chỉ có quy tắc phong cách, thể loại chung, hoặc yêu cầu format mà không có nội dung cụ thể → CHƯA ĐỦ.

Trả lời JSON:
{
  "complete": true/false,
  "missing": ["danh sách những gì còn thiếu nếu chưa đủ"]
}

Chỉ trả về JSON, không giải thích thêm."""

_INTERVIEW_SYSTEM = """You are a screenwriter interviewing a client. Always ask and respond in the primary language of the user's latest message. Bạn là một biên kịch/nhà văn đang phỏng vấn khách hàng để hiểu họ muốn viết câu chuyện gì.

Nhiệm vụ: Hỏi từng câu một để khai thác đủ thông tin viết truyện. Tập trung vào:
- Bối cảnh (thời đại, địa điểm, thế giới)
- Nhân vật chính (tên, tính cách, hoàn cảnh)
- Cốt truyện cơ bản (sự kiện chính, xung đột)
- Độ dài mong muốn
- Tone/cảm xúc (buồn, vui, hồi hộp, lãng mạn...)

Mỗi lần hỏi MỘT câu duy nhất, ngắn gọn, thân thiện.
Khi bạn thấy đã đủ thông tin (thường sau 4-6 câu), kết thúc bằng: "Tôi đã hiểu đủ rồi! Bấm 'Viết truyện' để bắt đầu nhé."
Nếu user muốn ngừng sớm hơn, tôn trọng quyết định đó."""

_WRITE_STORY_SYSTEM = """You are a talented narrator. Write the story in the primary language requested by the user; do not default to Vietnamese.

Hãy viết một câu chuyện hoàn chỉnh, hấp dẫn dựa trên thông tin được cung cấp.

MỞ ĐẦU (quan trọng nhất — quyết định người nghe ở lại hay bỏ đi):
- 2-3 câu ĐẦU TIÊN phải là một sự việc bất thường, bí ẩn, hoặc câu hỏi gây tò mò ngay lập tức (cold open).
- TUYỆT ĐỐI KHÔNG mở đầu bằng: lời chào kênh, giới thiệu bản thân, giới thiệu bối cảnh dài dòng, mô tả thời tiết/khung cảnh đời thường kéo dài, hay giải thích trước nội dung sắp kể.
- Chỉ giới thiệu nhân vật, bối cảnh, lý do SAU KHI đã tung ra bí ẩn ở câu mở đầu.

NHỊP KỂ:
- Cứ khoảng mỗi 30-60 giây kể (vài đoạn văn) phải có một tình tiết mới, một dấu hiệu leo thang, hoặc một phát hiện — không để truyện chững lại trên một giọng đều.

VĂN PHONG:
- Văn phong phải mượt mà, đọc tốt khi đọc to thành tiếng (cho audio).
- Không dùng dấu đầu dòng, không dùng tiêu đề chương (trừ khi được yêu cầu).
- Tránh ký tự đặc biệt, emoji, hay ký hiệu khó đọc thành tiếng.
- Câu văn rõ ràng, tự nhiên khi nói.
- Kết truyện có cảm xúc hoặc dư âm gây ám ảnh, đọng lại trong lòng người nghe."""


def _is_ollama(model: str) -> bool:
    """Ollama model IDs contain ':' (e.g. 'minimax-m3:cloud')."""
    return ":" in model and not model.startswith(("codex:", "opencode:"))


def _is_codex(model: str) -> bool:
    return model.startswith("codex:")


def _is_opencode(model: str) -> bool:
    return model.startswith("opencode:")


def _is_claude_cli(model: str) -> bool:
    return model.startswith("claude-") or model in {"sonnet", "opus", "haiku", "fable"}


def requires_anthropic_key(model: str) -> bool:
    return not (_is_claude_cli(model) or _is_ollama(model) or _is_codex(model) or _is_opencode(model))


def get_codex_models() -> list[dict]:
    """Return Codex choices if the Codex CLI is installed."""
    if not _cli_path("codex"):
        return []

    models = list(CODEX_MODELS)
    config_path = os.path.expanduser("~/.codex/config.toml")
    try:
        with open(config_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("model"):
                    current = line.split("=", 1)[1].strip().strip('"')
                    model_id = f"codex:{current}"
                    if current and all(m["id"] != model_id for m in models):
                        models.insert(0, {"id": model_id, "label": f"Codex {current}"})
                    break
    except Exception:
        pass
    return models


def get_opencode_models(limit: int = 160) -> list[dict]:
    """Fetch installed OpenCode model choices from `opencode models`."""
    opencode = _cli_path("opencode")
    if not opencode:
        return []
    try:
        result = subprocess.run(
            [opencode, "models"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []

    models = []
    seen = set()
    for raw in result.stdout.splitlines():
        name = raw.strip()
        if not name or name in seen:
            continue
        if not name.startswith(OPENCODE_MODEL_PREFIXES):
            continue
        seen.add(name)
        models.append({"id": f"opencode:{name}", "label": name})
        if len(models) >= limit:
            break
    return models


def _build_final_prompt(prompt: str, interview_history: list[dict]) -> str:
    if not interview_history:
        return prompt
    parts = []
    for turn in interview_history:
        label = "Nhà văn hỏi" if turn["role"] == "assistant" else "Yêu cầu của tôi"
        parts.append(f"{label}: {turn['content']}")
    return f"Hướng dẫn phong cách/quy tắc:\n{prompt}\n\nThông tin câu chuyện đã thảo luận:\n{chr(10).join(parts)}\n\nHãy viết câu chuyện hoàn chỉnh."


# ── Anthropic ─────────────────────────────────────────────────────────────────

def _get_anthropic_client(api_key: str):
    import anthropic
    return anthropic.Anthropic(api_key=api_key)


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text.strip())
    except Exception:
        return {"complete": False, "missing": ["Không thể phân tích prompt"]}


def _run_codex(prompt: str, model: str, timeout: int = 600) -> str:
    codex = _cli_path("codex")
    if not codex:
        raise RuntimeError("Codex CLI is not installed or not on PATH")
    cli_model = model.removeprefix("codex:")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as tmp:
        out_path = tmp.name
    try:
        result = subprocess.run(
            [
                codex, "exec",
                "--model", cli_model,
                "--sandbox", "read-only",
                "--skip-git-repo-check",
                "--cd", os.getcwd(),
                "--output-last-message", out_path,
            ],
            # Codex reads stdin when no positional prompt is supplied. Keeping long
            # story/director prompts out of argv avoids the Windows command-line limit.
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Codex CLI failed")
        with open(out_path, encoding="utf-8", errors="replace") as f:
            text = f.read().strip()
        return text or result.stdout.strip()
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


def _run_opencode(prompt: str, model: str, timeout: int = 600) -> str:
    opencode = _cli_path("opencode")
    if not opencode:
        raise RuntimeError("OpenCode CLI is not installed or not on PATH")
    cli_model = model.removeprefix("opencode:")
    result = subprocess.run(
        [opencode, "run", "--model", cli_model, "--dir", os.getcwd(), prompt],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "OpenCode CLI failed")
    return result.stdout.strip()


def _run_claude(prompt: str, model: str, timeout: int = 600) -> str:
    claude = _cli_path("claude")
    if not claude:
        raise RuntimeError("Claude CLI is not installed or not on PATH")
    result = subprocess.run(
        [
            claude,
            "-p",
            "--model", model,
            "--permission-mode", "dontAsk",
            "--no-session-persistence",
        ],
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Claude CLI failed")
    return result.stdout.strip()


def _stream_claude(prompt: str, model: str, timeout: int = 600) -> Generator[dict, None, None]:
    claude = _cli_path("claude")
    if not claude:
        raise RuntimeError("Claude CLI is not installed or not on PATH")
    proc = subprocess.Popen(
        [
            claude,
            "-p",
            "--model", model,
            "--permission-mode", "dontAsk",
            "--no-session-persistence",
            "--output-format", "stream-json",
            "--include-partial-messages",
            "--verbose",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdin and proc.stdout
    proc.stdin.write(prompt)
    proc.stdin.close()

    yield {"type": "status", "phase": "starting", "message": "Claude CLI dang khoi dong..."}
    total_chars = 0
    fallback = ""
    try:
        for line in proc.stdout:
            try:
                data = json.loads(line)
            except Exception:
                continue
            if data.get("type") == "result" and data.get("result") and not total_chars:
                fallback = data["result"]
            if data.get("type") == "system" and data.get("subtype") == "status":
                yield {"type": "status", "phase": data.get("status", "working"), "message": "LLM dang xu ly..."}
            event = data.get("event") if data.get("type") == "stream_event" else None
            delta = event.get("delta") if isinstance(event, dict) else None
            if isinstance(delta, dict) and delta.get("type") == "thinking_delta":
                yield {"type": "status", "phase": "thinking", "message": "AI dang suy nghi..."}
            if isinstance(delta, dict) and delta.get("type") == "text_delta":
                text = delta.get("text", "")
                if text:
                    total_chars += len(text)
                    yield {"type": "text", "content": text}
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError("Claude CLI timed out")

    if proc.returncode != 0:
        stderr = proc.stderr.read() if proc.stderr else ""
        raise RuntimeError(stderr.strip() or "Claude CLI failed")
    if fallback and not total_chars:
        total_chars = len(fallback)
        yield {"type": "text", "content": fallback}
    yield {"type": "done", "total_chars": total_chars}


def _run_cli_model(prompt: str, model: str, timeout: int = 600) -> str:
    if _is_claude_cli(model):
        return _run_claude(prompt, model, timeout)
    if _is_codex(model):
        return _run_codex(prompt, model, timeout)
    if _is_opencode(model):
        return _run_opencode(prompt, model, timeout)
    raise RuntimeError("Unsupported CLI model")


def _cli_prompt(system_prompt: str, user_prompt: str, output_rule: str = "") -> str:
    rule = f"\n\n{output_rule.strip()}" if output_rule else ""
    return (
        "You are being used as a backend text generation engine. "
        "Do not edit files, run tools, or mention this wrapper. "
        "Return only the requested content.\n\n"
        f"System instructions:\n{system_prompt}\n\n"
        f"User input:\n{user_prompt}"
        f"{rule}"
    )


def _analyze_prompt_anthropic(prompt: str, model: str, api_key: str) -> dict:
    client = _get_anthropic_client(api_key)
    msg = client.messages.create(
        model=model, max_tokens=256,
        system=_ANALYZE_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_json_response(msg.content[0].text)


def _interview_turn_anthropic(history: list[dict], user_message: str, model: str, api_key: str) -> dict:
    client = _get_anthropic_client(api_key)
    messages = list(history)
    if user_message:
        messages.append({"role": "user", "content": user_message})
    msg = client.messages.create(
        model=model, max_tokens=512,
        system=_INTERVIEW_SYSTEM,
        messages=messages,
    )
    return {"message": msg.content[0].text}


def _stream_interview_anthropic(history: list[dict], user_message: str, model: str, api_key: str) -> Generator[dict, None, None]:
    client = _get_anthropic_client(api_key)
    messages = list(history)
    if user_message:
        messages.append({"role": "user", "content": user_message})
    total_chars = 0
    with client.messages.stream(
        model=model,
        max_tokens=512,
        system=_INTERVIEW_SYSTEM,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            total_chars += len(text)
            yield {"type": "text", "content": text}
    yield {"type": "done", "total_chars": total_chars}


def _stream_story_anthropic(prompt: str, interview_history: list[dict], model: str, api_key: str) -> Generator[dict, None, None]:
    client = _get_anthropic_client(api_key)
    final_prompt = _build_final_prompt(prompt, interview_history)
    total_chars = 0
    with client.messages.stream(
        model=model, max_tokens=8096,
        system=_WRITE_STORY_SYSTEM,
        messages=[{"role": "user", "content": final_prompt}],
    ) as stream:
        for text in stream.text_stream:
            total_chars += len(text)
            yield {"type": "text", "content": text}
    yield {"type": "done", "total_chars": total_chars}


# ── Ollama ────────────────────────────────────────────────────────────────────

def get_ollama_models(ollama_url: str = "http://localhost:11434") -> list[dict]:
    """Fetch available models from Ollama."""
    try:
        r = requests.get(f"{ollama_url}/v1/models", timeout=5)
        data = r.json()
        return [
            {"id": m["id"], "label": m["id"]}
            for m in data.get("data", [])
        ]
    except Exception:
        return []


def _ollama_chat(messages: list[dict], model: str, ollama_url: str, stream: bool = False) -> requests.Response:
    return requests.post(
        f"{ollama_url}/v1/chat/completions",
        json={"model": model, "messages": messages, "stream": stream},
        stream=stream,
        timeout=120,
    )


def _normalize_chat_endpoint(base_url: str, path: str = "/v1/chat/completions") -> tuple[str, str]:
    base = (base_url or "").strip().rstrip("/")
    endpoint_path = (path or "/v1/chat/completions").strip()
    if not endpoint_path.startswith("/"):
        endpoint_path = f"/{endpoint_path}"
    endpoint_path = endpoint_path.rstrip("/") or "/v1/chat/completions"
    if not base:
        base = "https://api.openai.com"
    if not base.startswith(("http://", "https://")):
        scheme = "http" if base.startswith(("127.", "localhost")) else "https"
        base = f"{scheme}://{base}"
    if base.lower().endswith(endpoint_path.lower()):
        return base[: -len(endpoint_path)].rstrip("/"), base
    for suffix in ("/v1/chat/completions", "/chat/completions"):
        if base.lower().endswith(suffix):
            return base[: -len(suffix)].rstrip("/"), base
    parsed = urlparse(base)
    base_path = parsed.path.rstrip("/")
    join_path = endpoint_path
    if base_path and endpoint_path.lower().startswith(f"{base_path.lower()}/"):
        join_path = endpoint_path[len(base_path):]
    return base, f"{base}{join_path}"


def _resolve_key(value: str = "", key_path: str = "") -> str:
    key = (value or "").strip()
    if key:
        return key
    path = (key_path or "").strip().strip('"')
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def generate_text_openai_compatible(
    system_prompt: str,
    user_prompt: str,
    *,
    base_url: str,
    path: str = "/v1/chat/completions",
    model: str,
    api_key: str = "",
    api_key_path: str = "",
    max_tokens: int = 2048,
) -> str:
    """Non-streaming OpenAI-compatible chat completion."""
    _, endpoint = _normalize_chat_endpoint(base_url, path)
    key = _resolve_key(api_key, api_key_path)
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    r = requests.post(endpoint, headers=headers, json=body, timeout=180)
    if not r.ok and "max_tokens" in (r.text or "").lower():
        body["max_completion_tokens"] = body.pop("max_tokens")
        r = requests.post(endpoint, headers=headers, json=body, timeout=180)
    if not r.ok:
        try:
            data = r.json()
            err = data.get("error", {})
            message = err.get("message") if isinstance(err, dict) else err
        except Exception:
            message = r.text
        raise RuntimeError(f"Custom OpenAI-compatible LLM failed ({r.status_code}) at {endpoint}: {str(message)[:700]}")
    data = r.json()
    choices = data.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content).strip()
        if content:
            return str(content).strip()
        text = choices[0].get("text")
        if text:
            return str(text).strip()
    output_text = data.get("output_text")
    if output_text:
        return str(output_text).strip()
    raise RuntimeError("Custom safe prompt LLM returned no text")


def _analyze_prompt_ollama(prompt: str, model: str, ollama_url: str) -> dict:
    messages = [
        {"role": "system", "content": _ANALYZE_PROMPT},
        {"role": "user", "content": prompt},
    ]
    r = _ollama_chat(messages, model, ollama_url, stream=False)
    text = r.json()["choices"][0]["message"]["content"]
    return _parse_json_response(text)


def _interview_turn_ollama(history: list[dict], user_message: str, model: str, ollama_url: str) -> dict:
    messages = [{"role": "system", "content": _INTERVIEW_SYSTEM}] + list(history)
    if user_message:
        messages.append({"role": "user", "content": user_message})
    r = _ollama_chat(messages, model, ollama_url, stream=False)
    text = r.json()["choices"][0]["message"]["content"]
    return {"message": text}


def _stream_interview_ollama(history: list[dict], user_message: str, model: str, ollama_url: str) -> Generator[dict, None, None]:
    messages = [{"role": "system", "content": _INTERVIEW_SYSTEM}] + list(history)
    if user_message:
        messages.append({"role": "user", "content": user_message})
    r = _ollama_chat(messages, model, ollama_url, stream=True)
    total_chars = 0
    for line in r.iter_lines():
        if not line:
            continue
        line = line.decode("utf-8") if isinstance(line, bytes) else line
        if line.startswith("data: "):
            line = line[6:]
        if line == "[DONE]":
            break
        try:
            data = json.loads(line)
            delta = data["choices"][0]["delta"].get("content", "")
            if delta:
                total_chars += len(delta)
                yield {"type": "text", "content": delta}
        except Exception:
            continue
    yield {"type": "done", "total_chars": total_chars}


def _stream_story_ollama(prompt: str, interview_history: list[dict], model: str, ollama_url: str) -> Generator[dict, None, None]:
    final_prompt = _build_final_prompt(prompt, interview_history)
    messages = [
        {"role": "system", "content": _WRITE_STORY_SYSTEM},
        {"role": "user", "content": final_prompt},
    ]
    r = _ollama_chat(messages, model, ollama_url, stream=True)
    total_chars = 0
    for line in r.iter_lines():
        if not line:
            continue
        line = line.decode("utf-8") if isinstance(line, bytes) else line
        if line.startswith("data: "):
            line = line[6:]
        if line == "[DONE]":
            break
        try:
            data = json.loads(line)
            delta = data["choices"][0]["delta"].get("content", "")
            if delta:
                total_chars += len(delta)
                yield {"type": "text", "content": delta}
        except Exception:
            continue
    yield {"type": "done", "total_chars": total_chars}


# -- CLI providers ------------------------------------------------------------

def _analyze_prompt_cli(prompt: str, model: str) -> dict:
    text = _run_cli_model(
        _cli_prompt(
            _ANALYZE_PROMPT,
            prompt,
            "Return valid JSON only. No markdown fences.",
        ),
        model,
        timeout=300,
    )
    return _parse_json_response(text)


def _interview_turn_cli(history: list[dict], user_message: str, model: str) -> dict:
    lines = []
    for item in history:
        role = item.get("role", "user")
        content = item.get("content", "")
        lines.append(f"{role}: {content}")
    if user_message:
        lines.append(f"user: {user_message}")
    text = _run_cli_model(
        _cli_prompt(
            _INTERVIEW_SYSTEM,
            "\n".join(lines),
            "Return only the next assistant message.",
        ),
        model,
        timeout=300,
    )
    return {"message": text}


def _stream_interview_cli(history: list[dict], user_message: str, model: str) -> Generator[dict, None, None]:
    if _is_claude_cli(model):
        lines = []
        for item in history:
            role = item.get("role", "user")
            content = item.get("content", "")
            lines.append(f"{role}: {content}")
        if user_message:
            lines.append(f"user: {user_message}")
        yield from _stream_claude(
            _cli_prompt(
                _INTERVIEW_SYSTEM,
                "\n".join(lines),
                "Return only the next assistant message.",
            ),
            model,
            timeout=300,
        )
        return
    yield {"type": "status", "phase": "final_output", "message": "Agent nay se tra noi dung khi hoan tat..."}
    text = _interview_turn_cli(history, user_message, model)["message"]
    if text:
        yield {"type": "text", "content": text}
    yield {"type": "done", "total_chars": len(text)}


def _stream_story_cli(prompt: str, interview_history: list[dict], model: str) -> Generator[dict, None, None]:
    final_prompt = _build_final_prompt(prompt, interview_history)
    if _is_claude_cli(model):
        yield from _stream_claude(
            _cli_prompt(
                _WRITE_STORY_SYSTEM,
                final_prompt,
                "Return only the finished Vietnamese story text.",
            ),
            model,
            timeout=900,
        )
        return
    yield {"type": "status", "phase": "final_output", "message": "Agent nay se tra noi dung khi hoan tat..."}
    text = _run_cli_model(
        _cli_prompt(
            _WRITE_STORY_SYSTEM,
            final_prompt,
            "Return only the finished Vietnamese story text.",
        ),
        model,
        timeout=900,
    )
    if text:
        yield {"type": "text", "content": text}
    yield {"type": "done", "total_chars": len(text)}


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_prompt(prompt: str, model: str, api_key: str, ollama_url: str = "http://localhost:11434") -> dict:
    if _is_claude_cli(model) or _is_codex(model) or _is_opencode(model):
        return _analyze_prompt_cli(prompt, model)
    if _is_ollama(model):
        return _analyze_prompt_ollama(prompt, model, ollama_url)
    return _analyze_prompt_anthropic(prompt, model, api_key)


def interview_turn(history: list[dict], user_message: str, model: str, api_key: str, ollama_url: str = "http://localhost:11434") -> dict:
    if _is_claude_cli(model) or _is_codex(model) or _is_opencode(model):
        return _interview_turn_cli(history, user_message, model)
    if _is_ollama(model):
        return _interview_turn_ollama(history, user_message, model, ollama_url)
    return _interview_turn_anthropic(history, user_message, model, api_key)


def stream_interview(history: list[dict], user_message: str, model: str, api_key: str, ollama_url: str = "http://localhost:11434") -> Generator[dict, None, None]:
    if _is_claude_cli(model) or _is_codex(model) or _is_opencode(model):
        yield from _stream_interview_cli(history, user_message, model)
    elif _is_ollama(model):
        yield {"type": "status", "phase": "streaming", "message": "Ollama dang tra loi..."}
        yield from _stream_interview_ollama(history, user_message, model, ollama_url)
    else:
        yield {"type": "status", "phase": "streaming", "message": "Anthropic dang tra loi..."}
        yield from _stream_interview_anthropic(history, user_message, model, api_key)


def stream_story(prompt: str, interview_history: list[dict], model: str, api_key: str, ollama_url: str = "http://localhost:11434") -> Generator[dict, None, None]:
    if _is_claude_cli(model) or _is_codex(model) or _is_opencode(model):
        yield from _stream_story_cli(prompt, interview_history, model)
    elif _is_ollama(model):
        yield {"type": "status", "phase": "streaming", "message": "Ollama dang stream noi dung..."}
        yield from _stream_story_ollama(prompt, interview_history, model, ollama_url)
    else:
        yield {"type": "status", "phase": "streaming", "message": "Anthropic dang stream noi dung..."}
        yield from _stream_story_anthropic(prompt, interview_history, model, api_key)


def generate_text(
    system_prompt: str,
    user_prompt: str,
    model: str,
    api_key: str = "",
    ollama_url: str = "http://localhost:11434",
    max_tokens: int = 2048,
    cli_timeout_sec: int = 600,
) -> str:
    """Generic non-streaming text generation for publishing metadata and prompts."""
    if _is_claude_cli(model) or _is_codex(model) or _is_opencode(model):
        return _run_cli_model(_cli_prompt(system_prompt, user_prompt), model, timeout=cli_timeout_sec)
    if _is_ollama(model):
        r = _ollama_chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model,
            ollama_url,
            stream=False,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    client = _get_anthropic_client(api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return msg.content[0].text
