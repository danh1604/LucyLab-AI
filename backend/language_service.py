"""Locale catalog and the language-localization agent for LucyLab pipelines."""
import copy
import json
import re
from typing import Generator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from story_service import generate_text, generate_text_openai_compatible, requires_anthropic_key
from tts_service import load_config

router = APIRouter()

# Locale identifiers are stable API values; labels are intentionally Vietnamese because
# the editor UI is Vietnamese while published content can be in any of these locales.
LOCALES = [
    ("vi-VN", "Tiếng Việt"), ("en-US", "English (United States)"), ("en-GB", "English (United Kingdom)"),
    ("zh-CN", "中文（简体）"), ("zh-TW", "中文（繁體）"), ("ja-JP", "日本語"), ("ko-KR", "한국어"),
    ("hi-IN", "हिन्दी"), ("ru-RU", "Русский"), ("es-ES", "Español (España)"), ("es-MX", "Español (México)"),
    ("pt-BR", "Português (Brasil)"), ("pt-PT", "Português (Portugal)"), ("fr-FR", "Français"),
    ("fr-CA", "Français (Canada)"), ("de-DE", "Deutsch"), ("it-IT", "Italiano"), ("id-ID", "Bahasa Indonesia"),
    ("th-TH", "ไทย"), ("tr-TR", "Türkçe"), ("ar-SA", "العربية"), ("bn-IN", "বাংলা"),
    ("ur-PK", "اردو"), ("pa-IN", "ਪੰਜਾਬੀ"), ("ta-IN", "தமிழ்"), ("te-IN", "తెలుగు"),
    ("mr-IN", "मराठी"), ("gu-IN", "ગુજરાતી"), ("kn-IN", "ಕನ್ನಡ"), ("ml-IN", "മലയാളം"),
    ("pl-PL", "Polski"), ("nl-NL", "Nederlands"), ("uk-UA", "Українська"), ("ro-RO", "Română"),
    ("cs-CZ", "Čeština"), ("sv-SE", "Svenska"), ("da-DK", "Dansk"), ("nb-NO", "Norsk bokmål"),
    ("fi-FI", "Suomi"), ("el-GR", "Ελληνικά"), ("he-IL", "עברית"), ("hu-HU", "Magyar"),
    ("ms-MY", "Bahasa Melayu"), ("fil-PH", "Filipino"), ("sw-KE", "Kiswahili"),
]
LOCALE_LABELS = dict(LOCALES)
VIETNAMESE_LANGUAGE_NAMES = {
    "en-US": "Tiếng Anh (Mỹ)", "en-GB": "Tiếng Anh (Anh)", "zh-CN": "Tiếng Trung (giản thể)", "zh-TW": "Tiếng Trung (phồn thể)",
    "ja-JP": "Tiếng Nhật", "ko-KR": "Tiếng Hàn", "hi-IN": "Tiếng Hindi", "ru-RU": "Tiếng Nga", "es-ES": "Tiếng Tây Ban Nha", "es-MX": "Tiếng Tây Ban Nha (Mexico)",
    "pt-BR": "Tiếng Bồ Đào Nha (Brazil)", "pt-PT": "Tiếng Bồ Đào Nha", "fr-FR": "Tiếng Pháp", "fr-CA": "Tiếng Pháp (Canada)", "de-DE": "Tiếng Đức", "it-IT": "Tiếng Ý",
    "id-ID": "Tiếng Indonesia", "th-TH": "Tiếng Thái", "tr-TR": "Tiếng Thổ Nhĩ Kỳ", "ar-SA": "Tiếng Ả Rập", "bn-IN": "Tiếng Bengal", "ur-PK": "Tiếng Urdu",
    "pa-IN": "Tiếng Punjabi", "ta-IN": "Tiếng Tamil", "te-IN": "Tiếng Telugu", "mr-IN": "Tiếng Marathi", "gu-IN": "Tiếng Gujarati", "kn-IN": "Tiếng Kannada",
    "ml-IN": "Tiếng Malayalam", "pl-PL": "Tiếng Ba Lan", "nl-NL": "Tiếng Hà Lan", "uk-UA": "Tiếng Ukraina", "ro-RO": "Tiếng Romania", "cs-CZ": "Tiếng Séc",
    "sv-SE": "Tiếng Thụy Điển", "da-DK": "Tiếng Đan Mạch", "nb-NO": "Tiếng Na Uy", "fi-FI": "Tiếng Phần Lan", "el-GR": "Tiếng Hy Lạp", "he-IL": "Tiếng Do Thái",
    "hu-HU": "Tiếng Hungary", "ms-MY": "Tiếng Mã Lai", "fil-PH": "Tiếng Philippines", "sw-KE": "Tiếng Swahili",
}
BCP47_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})+$")
PRIMARY_LOCALE = {key.split("-", 1)[0]: key for key, _ in LOCALES}
_PROTECTED_SPEECH_RANGES = re.compile(r"https?://\S+|www\.\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", re.IGNORECASE)
_NUMERIC_EXPRESSION_RE = re.compile(
    r"""(?<![A-Za-z_])
    (?P<expression>
      (?:[$€£¥₫₩₹]\s*)?[+-]?
      (?:
        \d{1,2}:\d{2}(?::\d{2})? |
        \d{1,4}[/-]\d{1,2}(?:[/-]\d{1,4})? |
        \d{1,3}(?:[.,]\d{3})+ |
        \d+(?:[.,]\d+)?
      )
      (?:\s*(?:%|‰|°[CF]))?
      (?:\s*(?:USD|VND|EUR|KRW|JPY|GBP|CNY|RMB))?
      (?:[A-Za-z가-힣一-龯]+)?
    )(?![A-Za-z_])""",
    re.VERBOSE | re.IGNORECASE,
)
NUMBER_SPEECH_BATCH_SIZE = 48


class LocalizeRequest(BaseModel):
    source_locale: str = "auto"
    target_locale: str = "vi-VN"
    title: str = ""
    text: str
    stick_figure: dict | None = None


def _valid_locale(value: str, allow_auto: bool = False) -> str:
    value = (value or "").strip()
    if allow_auto and value == "auto":
        return value
    if value.lower() in PRIMARY_LOCALE:
        return PRIMARY_LOCALE[value.lower()]
    if value in LOCALE_LABELS or BCP47_RE.fullmatch(value):
        return value
    raise HTTPException(400, f"Locale không hợp lệ: {value or '(trống)'}")


def _parse_json(text: str) -> dict:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Language Agent did not return a JSON object")
    data = json.loads(raw[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("Language Agent returned invalid JSON")
    return data


def _looks_vietnamese(text: str) -> bool:
    """Deterministic Vietnamese detection for the no-translation path."""
    sample = str(text or "")[:4000]
    letters = re.findall(r"[A-Za-zÀ-ỹĐđ]", sample)
    if len(letters) < 20:
        return False
    marked = re.findall(r"[À-ỹĐđ]", sample)
    # Restrictive on purpose: ambiguous ASCII text should still use the agent
    # when the user selected auto-detect.
    return len(marked) >= max(3, int(len(letters) * 0.012))


def _identity_localization(body: LocalizeRequest, source_locale: str, target_locale: str, reason: str) -> dict:
    blueprint = copy.deepcopy(body.stick_figure) if isinstance(body.stick_figure, dict) else None
    if blueprint is not None:
        blueprint["tts_text"] = body.text.strip()
        # Final visual beats are created after TTS from the localized narration.
        # Do not preserve sentence ids from a differently tokenized language.
        blueprint.pop("narration_units", None)
    return {
        "source_locale": source_locale,
        "target_locale": target_locale,
        "title": body.title.strip(),
        "text": body.text.strip(),
        "stick_figure": blueprint,
        "qa": {"fidelity": "pass", "naturalness": "pass", "terminology": "pass", "warnings": [reason]},
        "provider": "bypass",
        "model": "not-used",
    }


def _language_llm(system: str, user: str) -> tuple[str, str, str]:
    cfg = load_config()
    provider = str(cfg.get("language_agent_provider") or "default").strip().lower()
    model = str(cfg.get("language_agent_model") or cfg.get("default_story_model") or "claude-sonnet-4-6").strip()
    if provider == "custom":
        return (
            generate_text_openai_compatible(
                system, user,
                base_url=cfg.get("language_agent_base_url") or "https://api.openai.com",
                path=cfg.get("language_agent_path") or "/v1/chat/completions",
                model=model or "gpt-4o-mini",
                api_key=cfg.get("language_agent_api_key") or "",
                api_key_path=cfg.get("language_agent_api_key_path") or "",
                max_tokens=12000,
            ), "custom", model,
        )
    if not cfg.get("anthropic_api_key") and requires_anthropic_key(model):
        raise RuntimeError("Chưa cấu hình model/Anthropic API key cho Language Agent.")
    return (
        generate_text(system, user, model, cfg.get("anthropic_api_key", ""), cfg.get("ollama_url", "http://localhost:11434"), max_tokens=12000, cli_timeout_sec=900),
        "default", model,
    )


_NUMBER_SPEECH_SYSTEM = """You are LucyLab's number speech-normalization agent.
You receive narration segments plus exact numeric-expression spans extracted by code. Return strict JSON only.

For EVERY supplied item, write a natural spoken replacement in target_locale. The replacement must preserve the exact meaning and context of that expression. Handle years, thousands separators, decimal separators, dates, clock times, ratios, money, currencies, units, percentages, temperatures, signs, and attached unit symbols naturally for speech.

You are NOT allowed to rewrite, translate, remove, reorder, summarize, or add to the narration. You can only supply `spoken` text that will replace the exact `expression` span. Never return digits in `spoken`; spell the complete number/expression in words. Preserve the supplied id and expression exactly.

Return exactly:
{"replacements":[{"id":"n_0001","expression":"900.000","spoken":"..."}]}
No markdown and no commentary."""


def _number_tokens_for_segments(segments: list[str]) -> list[dict]:
    """Extract only replaceable numeric spans, never URLs or email addresses."""
    tokens: list[dict] = []
    for segment_index, text in enumerate(segments):
        protected = [match.span() for match in _PROTECTED_SPEECH_RANGES.finditer(text)]
        occurrence = 0
        for match in _NUMERIC_EXPRESSION_RE.finditer(text):
            start, end = match.span("expression")
            if any(start < protected_end and end > protected_start for protected_start, protected_end in protected):
                continue
            occurrence += 1
            tokens.append({
                "id": f"s{segment_index + 1:04d}_n{occurrence:03d}",
                "segment_index": segment_index,
                "start": start,
                "end": end,
                "expression": match.group("expression"),
                "context": text,
            })
    return tokens


def normalize_number_expressions_for_tts(
    segments: list[str],
    target_locale: str,
    progress_callback=None,
) -> tuple[list[str], dict]:
    """Use the configured Language Agent to verbalize number spans only.

    The agent never receives authority to rewrite a segment. It returns replacements
    for code-extracted spans, then this function applies them to a copy used only
    for TTS. The original display segments remain untouched for subtitle captions.
    """
    original = [str(segment or "") for segment in segments]
    tokens = _number_tokens_for_segments(original)
    if not tokens:
        return original, {"enabled": True, "applied": False, "replacement_count": 0, "provider": "not-used", "model": "not-used"}

    replacements: dict[str, str] = {}
    provider = ""
    model = ""
    total_batches = (len(tokens) + NUMBER_SPEECH_BATCH_SIZE - 1) // NUMBER_SPEECH_BATCH_SIZE
    for batch_index, offset in enumerate(range(0, len(tokens), NUMBER_SPEECH_BATCH_SIZE), start=1):
        batch = tokens[offset:offset + NUMBER_SPEECH_BATCH_SIZE]
        if progress_callback:
            progress_callback(batch_index, total_batches, len(tokens))
        payload = {
            "target_locale": target_locale,
            "target_language": LOCALE_LABELS.get(target_locale, target_locale),
            "items": [
                {"id": item["id"], "expression": item["expression"], "context": item["context"]}
                for item in batch
            ],
        }
        raw, provider, model = _language_llm(_NUMBER_SPEECH_SYSTEM, json.dumps(payload, ensure_ascii=False))
        data = _parse_json(raw)
        rows = data.get("replacements") if isinstance(data.get("replacements"), list) else []
        expected = {item["id"]: item["expression"] for item in batch}
        received: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            token_id = str(row.get("id") or "")
            expression = str(row.get("expression") or "")
            spoken = re.sub(r"\s+", " ", str(row.get("spoken") or "")).strip()
            if token_id not in expected or expression != expected[token_id]:
                raise ValueError("Number Speech Agent returned an unknown or modified numeric span")
            if not spoken or re.search(r"\d", spoken):
                raise ValueError(f"Number Speech Agent did not spell out {expression!r} as words")
            received[token_id] = spoken
        if set(received) != set(expected):
            missing = len(set(expected) - set(received))
            raise ValueError(f"Number Speech Agent omitted {missing} numeric expression(s)")
        replacements.update(received)

    by_segment: dict[int, list[dict]] = {}
    for token in tokens:
        by_segment.setdefault(token["segment_index"], []).append(token)
    spoken_segments = list(original)
    for segment_index, segment_tokens in by_segment.items():
        cursor = 0
        parts: list[str] = []
        for token in sorted(segment_tokens, key=lambda item: item["start"]):
            parts.append(original[segment_index][cursor:token["start"]])
            parts.append(replacements[token["id"]])
            cursor = token["end"]
        parts.append(original[segment_index][cursor:])
        spoken_segments[segment_index] = "".join(parts)

    return spoken_segments, {
        "enabled": True,
        "applied": True,
        "replacement_count": len(tokens),
        "provider": provider,
        "model": model,
    }


_SYSTEM = """You are LucyLab's language-localization editor and factual QA agent.
Return strict JSON only. Detect the primary source language when requested. Localize narration naturally for speaking in the target locale; preserve every fact, sequence, character, proper noun, number, uncertainty, and dramatic intent. Never invent evidence or remove a meaningful beat.
If a stick_figure content blueprint is supplied, localize its tts_text, visual_style and reusable reference descriptions. Do not create scenes, shots, durations, visual beats, prompts, storyboard images, or narration_units: LucyLab will make the Visual Plan after final TTS timing is available. On-screen text, if present in a reusable reference, must be short and exactly in the target locale.
Perform a private QA pass and repair the result before returning it. Return:
{
  "detected_source_locale":"BCP-47",
  "detected_confident":true,
  "localized_title":"...",
  "localized_text":"...",
  "localized_stick_figure":{},
  "qa":{"fidelity":"pass|warning","naturalness":"pass|warning","terminology":"pass|warning","warnings":["..."]}
}
Use an empty object for localized_stick_figure when no blueprint is supplied. Do not use markdown."""


def _localize(body: LocalizeRequest) -> dict:
    source_locale = _valid_locale(body.source_locale, allow_auto=True)
    target_locale = _valid_locale(body.target_locale)
    if not (body.text or "").strip():
        raise HTTPException(400, "Nội dung để localize đang trống")
    if source_locale != "auto" and source_locale == target_locale:
        return _identity_localization(body, source_locale, target_locale, "Ngôn ngữ đầu vào và đầu ra trùng nhau; bỏ qua Language Agent.")
    if source_locale == "auto" and target_locale == "vi-VN" and _looks_vietnamese(body.text):
        return _identity_localization(body, "vi-VN", "vi-VN", "Đã nhận diện tiếng Việt bằng luật cục bộ; bỏ qua Language Agent.")
    payload = {
        "source_locale": source_locale,
        "target_locale": target_locale,
        "target_language": LOCALE_LABELS.get(target_locale, target_locale),
        "title": body.title,
        "text": body.text,
        "stick_figure": body.stick_figure or None,
    }
    raw, provider, model = _language_llm(_SYSTEM, json.dumps(payload, ensure_ascii=False))
    data = _parse_json(raw)
    detected = _valid_locale(str(data.get("detected_source_locale") or ("vi-VN" if source_locale == "auto" else source_locale)))
    localized_text = str(data.get("localized_text") or "").strip()
    if not localized_text:
        raise HTTPException(400, "Language Agent không trả lời đọc đã localize")
    source_length = len(body.text.strip())
    if source_length >= 500 and len(localized_text) < source_length * 0.35:
        raise HTTPException(422, "Bản localize ngắn bất thường so với kịch bản gốc; đã chặn để không làm hỏng audio. Hãy thử lại bằng model khác.")
    localized_blueprint = data.get("localized_stick_figure")
    if body.stick_figure and not isinstance(localized_blueprint, dict):
        localized_blueprint = copy.deepcopy(body.stick_figure)
    if isinstance(localized_blueprint, dict):
        localized_blueprint["tts_text"] = localized_text
        localized_blueprint.pop("narration_units", None)
        localized_blueprint.pop("scenes", None)
    return {
        "source_locale": detected,
        "target_locale": target_locale,
        "title": str(data.get("localized_title") or body.title or "").strip(),
        "text": localized_text,
        "stick_figure": localized_blueprint or None,
        "qa": data.get("qa") if isinstance(data.get("qa"), dict) else {"fidelity": "warning", "naturalness": "warning", "terminology": "warning", "warnings": []},
        "provider": provider,
        "model": model,
    }


@router.get("/api/languages")
def list_languages():
    return {
        "default_source_locale": "auto", "default_target_locale": "vi-VN",
        "languages": [{"id": key, "label": label if key == "vi-VN" else f"{label} ({VIETNAMESE_LANGUAGE_NAMES.get(key, key)})"} for key, label in LOCALES],
        "custom_pattern": BCP47_RE.pattern,
    }


@router.post("/api/language/localize")
def localize_story(body: LocalizeRequest):
    def events() -> Generator[str, None, None]:
        yield f"data: {json.dumps({'type': 'phase', 'phase': 'detecting'}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'phase', 'phase': 'localizing'}, ensure_ascii=False)}\n\n"
        try:
            result = _localize(body)
            yield f"data: {json.dumps({'type': 'phase', 'phase': 'qa'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'complete', 'result': result}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'error': str(exc)}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
