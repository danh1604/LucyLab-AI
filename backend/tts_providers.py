"""Provider abstractions for LucyLab text-to-speech.

The generation pipeline depends only on :class:`TTSProvider`.  Provider
implementations own their authentication and HTTP details, so adding another
engine does not require changes to the narration/timing/video pipeline.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from dataclasses import dataclass, field
from html import escape
import os
import tempfile
import time
from typing import Any
from urllib.parse import quote

import requests


class TTSProviderError(RuntimeError):
    """An actionable, provider-facing synthesis/configuration failure."""


@dataclass(frozen=True)
class SynthesisRequest:
    text: str
    locale: str
    voice_id: str
    speed: float
    profile: dict[str, Any]
    provider_settings: dict[str, Any]


@dataclass(frozen=True)
class SynthesisResult:
    audio: bytes
    extension: str
    content_type: str
    usage: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class TTSProvider(ABC):
    """DIP boundary between orchestration and a concrete TTS vendor."""

    provider_id: str
    label: str
    # Vivibe is deliberately exempt: its legacy raw WAV flow stays untouched.
    requires_pcm_normalization = False
    max_parallel_requests = 3

    def definition(self) -> dict[str, Any]:
        data = {
            "id": self.provider_id,
            "label": self.label,
            "credential_fields": [],
            "profile_fields": [],
            "supports_voice_catalog": False,
            "requires_pcm_normalization": self.requires_pcm_normalization,
        }
        suggested = self.default_profile("ko-KR")
        if suggested.get("voice_id"):
            data["suggested_profiles"] = {"ko-KR": suggested}
        return data

    def default_profile(self, locale: str) -> dict[str, Any]:
        return {"provider": self.provider_id, "speed": 1.0}

    def validate(self, provider_settings: dict[str, Any], profile: dict[str, Any]) -> None:
        for field in self.definition().get("credential_fields", []):
            key = field["key"]
            if field.get("required") and not str(provider_settings.get(key) or "").strip():
                raise TTSProviderError(f"{self.label}: chưa cấu hình {field['label']}.")
        for field in self.definition().get("profile_fields", []):
            key = field["key"]
            if field.get("required") and not str(profile.get(key) or "").strip():
                raise TTSProviderError(f"{self.label}: chưa chọn {field['label']} cho ngôn ngữ này.")

    def list_voices(self, locale: str, provider_settings: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    @abstractmethod
    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        raise NotImplementedError


class VivibeProvider(TTSProvider):
    provider_id = "vivibe"
    label = "Vivibe"
    api_url = "https://api.lucylab.io/json-rpc"

    def definition(self) -> dict[str, Any]:
        data = super().definition()
        data["profile_fields"] = [
            {"key": "voice_id", "label": "Voice ID Vivibe", "type": "text", "required": True, "placeholder": "Voice ID từ Vivibe"},
            {"key": "speed", "label": "Tốc độ đọc", "type": "number", "min": 0.5, "max": 2, "step": 0.1, "default": 1},
        ]
        data["legacy_credentials"] = True
        return data

    def validate(self, provider_settings: dict[str, Any], profile: dict[str, Any]) -> None:
        if not str(provider_settings.get("jwt_token") or "").strip():
            raise TTSProviderError("Vivibe: chưa cấu hình JWT Token trong tab TTS.")
        if not str(profile.get("voice_id") or "").strip():
            raise TTSProviderError("Vivibe: chưa chọn Voice ID cho ngôn ngữ này.")

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        token = str(request.provider_settings.get("jwt_token") or "").strip()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Origin": "https://www.vivibe.app",
            "Referer": "https://www.vivibe.app/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        payload = {
            "method": "tts",
            "input": {
                "text": request.text,
                "userVoiceId": request.voice_id,
                "speed": request.speed,
                "blockVersion": 0,
            },
        }
        try:
            response = requests.post(self.api_url, json=payload, headers=headers, timeout=60)
        except requests.RequestException as exc:
            raise TTSProviderError(f"Vivibe: không kết nối được: {exc}") from exc
        if response.status_code in {401, 403}:
            raise TTSProviderError("Vivibe: JWT Token đã hết hạn hoặc không có quyền dùng voice này.")
        if response.status_code != 200:
            raise TTSProviderError(f"Vivibe: HTTP {response.status_code}: {response.text[:300]}")
        try:
            payload = response.json()
            if payload.get("error"):
                raise TTSProviderError(f"Vivibe: {payload['error'].get('message', payload['error'])}")
            result = payload.get("result") or {}
            url = str(result.get("url") or "")
        except ValueError as exc:
            raise TTSProviderError("Vivibe: phản hồi tạo audio không hợp lệ.") from exc
        if not url:
            raise TTSProviderError("Vivibe: không nhận được URL audio.")
        try:
            audio_response = requests.get(url, timeout=120)
            audio_response.raise_for_status()
        except requests.RequestException as exc:
            raise TTSProviderError(f"Vivibe: không tải được audio: {exc}") from exc
        audio = audio_response.content
        if len(audio) <= 500:
            raise TTSProviderError("Vivibe: audio trả về rỗng hoặc quá ngắn.")
        return SynthesisResult(
            audio=audio,
            extension="wav",
            content_type=audio_response.headers.get("content-type", "audio/wav"),
            usage={"credits_used": result.get("creditsUsed", 0)},
            metadata={"provider": self.provider_id, "voice_id": request.voice_id},
        )


class AzureSpeechProvider(TTSProvider):
    provider_id = "azure_speech"
    label = "Azure Speech"
    requires_pcm_normalization = True

    def definition(self) -> dict[str, Any]:
        data = super().definition()
        data.update({
            "credential_fields": [
                {"key": "api_key", "label": "Speech key", "type": "secret", "required": True, "placeholder": "Azure Speech resource key"},
                {"key": "region", "label": "Region", "type": "text", "required": True, "placeholder": "Ví dụ: koreacentral"},
            ],
            "profile_fields": [
                {"key": "voice_id", "label": "Azure voice", "type": "text", "required": True, "placeholder": "Ví dụ: ko-KR-HyunsuNeural"},
                {"key": "speed", "label": "Tốc độ đọc", "type": "number", "min": 0.5, "max": 2, "step": 0.1, "default": 1},
                {"key": "style", "label": "Style (tuỳ chọn)", "type": "text", "required": False, "placeholder": "Ví dụ: sad"},
            ],
            "supports_voice_catalog": True,
        })
        return data

    def default_profile(self, locale: str) -> dict[str, Any]:
        profile = super().default_profile(locale)
        if locale == "ko-KR":
            profile["voice_id"] = "ko-KR-HyunsuNeural"
        return profile

    @staticmethod
    def _base_url(region: str) -> str:
        return f"https://{region.strip().lower()}.tts.speech.microsoft.com"

    def list_voices(self, locale: str, provider_settings: dict[str, Any]) -> list[dict[str, Any]]:
        self.validate(provider_settings, {"voice_id": "catalog"})
        region = str(provider_settings["region"])
        try:
            response = requests.get(
                f"{self._base_url(region)}/cognitiveservices/voices/list",
                headers={"Ocp-Apim-Subscription-Key": str(provider_settings["api_key"])},
                timeout=30,
            )
            response.raise_for_status()
            voices = response.json()
        except requests.RequestException as exc:
            raise TTSProviderError(f"Azure Speech: không tải được danh sách giọng: {exc}") from exc
        return [
            {
                "id": item.get("ShortName", ""),
                "label": item.get("LocalName") or item.get("DisplayName") or item.get("ShortName", ""),
                "gender": item.get("Gender", ""),
                "styles": item.get("StyleList") or [],
            }
            for item in voices
            if item.get("Locale") == locale and item.get("ShortName")
        ]

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        self.validate(request.provider_settings, {**request.profile, "voice_id": request.voice_id})
        region = str(request.provider_settings["region"])
        style = str(request.profile.get("style") or "").strip()
        rate = f"{int(round((request.speed - 1.0) * 100)):+d}%"
        spoken = escape(request.text)
        content = f'<prosody rate="{rate}">{spoken}</prosody>'
        if style:
            content = f'<mstts:express-as style="{escape(style)}">{content}</mstts:express-as>'
        ssml = (
            '<speak version="1.0" xml:lang="{locale}" '
            'xmlns="http://www.w3.org/2001/10/synthesis" '
            'xmlns:mstts="https://www.w3.org/2001/mstts">'
            '<voice name="{voice}">{content}</voice></speak>'
        ).format(locale=escape(request.locale), voice=escape(request.voice_id), content=content)
        try:
            response = requests.post(
                f"{self._base_url(region)}/cognitiveservices/v1",
                data=ssml.encode("utf-8"),
                headers={
                    "Ocp-Apim-Subscription-Key": str(request.provider_settings["api_key"]),
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm",
                    "User-Agent": "LucyLab-Story-Studio",
                },
                timeout=90,
            )
        except requests.RequestException as exc:
            raise TTSProviderError(f"Azure Speech: không kết nối được: {exc}") from exc
        if response.status_code != 200:
            raise TTSProviderError(f"Azure Speech: HTTP {response.status_code}: {response.text[:500]}")
        if len(response.content) <= 500:
            raise TTSProviderError("Azure Speech: audio trả về rỗng hoặc quá ngắn.")
        return SynthesisResult(
            audio=response.content,
            extension="wav",
            content_type="audio/wav",
            metadata={"provider": self.provider_id, "voice_id": request.voice_id, "region": region, "style": style},
        )


class ElevenLabsProvider(TTSProvider):
    provider_id = "elevenlabs"
    label = "ElevenLabs"
    requires_pcm_normalization = True

    def definition(self) -> dict[str, Any]:
        data = super().definition()
        data.update({
            "credential_fields": [
                {"key": "api_key", "label": "ElevenLabs API key", "type": "secret", "required": True, "placeholder": "xi-..."},
            ],
            "profile_fields": [
                {"key": "voice_id", "label": "ElevenLabs Voice ID", "type": "text", "required": True, "placeholder": "Voice ID từ ElevenLabs"},
                {"key": "model_id", "label": "Model", "type": "select", "required": True, "default": "eleven_multilingual_v2", "options": [
                    {"value": "eleven_multilingual_v2", "label": "Multilingual v2 · ổn định cho narration"},
                    {"value": "eleven_v3", "label": "Eleven v3 · biểu cảm"},
                    {"value": "eleven_flash_v2_5", "label": "Flash v2.5 · nhanh"},
                ]},
                {"key": "speed", "label": "Tốc độ đọc", "type": "number", "min": 0.5, "max": 2, "step": 0.1, "default": 1},
                {"key": "stability", "label": "Stability", "type": "number", "min": 0, "max": 1, "step": 0.05, "default": 0.5},
                {"key": "similarity_boost", "label": "Similarity", "type": "number", "min": 0, "max": 1, "step": 0.05, "default": 0.75},
                {"key": "style", "label": "Style", "type": "number", "min": 0, "max": 1, "step": 0.05, "default": 0},
            ],
            "supports_voice_catalog": True,
        })
        return data

    def list_voices(self, locale: str, provider_settings: dict[str, Any]) -> list[dict[str, Any]]:
        self.validate(provider_settings, {"voice_id": "catalog", "model_id": "eleven_multilingual_v2"})
        try:
            response = requests.get(
                "https://api.elevenlabs.io/v1/voices",
                headers={"xi-api-key": str(provider_settings["api_key"])},
                timeout=30,
            )
            response.raise_for_status()
            voices = response.json().get("voices", [])
        except requests.RequestException as exc:
            raise TTSProviderError(f"ElevenLabs: không tải được danh sách giọng: {exc}") from exc
        return [
            {
                "id": item.get("voice_id", ""),
                "label": item.get("name") or item.get("voice_id", ""),
                "category": item.get("category", ""),
            }
            for item in voices
            if item.get("voice_id")
        ]

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        self.validate(request.provider_settings, {**request.profile, "voice_id": request.voice_id})
        model_id = str(request.profile.get("model_id") or "eleven_multilingual_v2")
        voice_settings = {
            "speed": request.speed,
            "stability": float(request.profile.get("stability", 0.5)),
            "similarity_boost": float(request.profile.get("similarity_boost", 0.75)),
            "style": float(request.profile.get("style", 0)),
            "use_speaker_boost": bool(request.profile.get("use_speaker_boost", True)),
        }
        try:
            response = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{quote(request.voice_id, safe='')}",
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": str(request.provider_settings["api_key"]), "Content-Type": "application/json"},
                json={"text": request.text, "model_id": model_id, "voice_settings": voice_settings},
                timeout=90,
            )
        except requests.RequestException as exc:
            raise TTSProviderError(f"ElevenLabs: không kết nối được: {exc}") from exc
        if response.status_code != 200:
            raise TTSProviderError(f"ElevenLabs: HTTP {response.status_code}: {response.text[:500]}")
        if len(response.content) <= 500:
            raise TTSProviderError("ElevenLabs: audio trả về rỗng hoặc quá ngắn.")
        return SynthesisResult(
            audio=response.content,
            extension="mp3",
            content_type=response.headers.get("content-type", "audio/mpeg"),
            usage={"character_cost": response.headers.get("character-cost", "")},
            metadata={"provider": self.provider_id, "voice_id": request.voice_id, "model_id": model_id},
        )


class EdgeTTSProvider(TTSProvider):
    """Local edge-tts client: no user API key, MP3 output for the pipeline."""

    provider_id = "edge_tts"
    label = "Microsoft Edge TTS · miễn phí"
    requires_pcm_normalization = True
    max_parallel_requests = 2

    _default_voices = {
        "en-US": "en-US-AriaNeural",
        "en-GB": "en-GB-SoniaNeural",
        "ko-KR": "ko-KR-SunHiNeural",
        "ja-JP": "ja-JP-NanamiNeural",
    }

    def definition(self) -> dict[str, Any]:
        data = super().definition()
        data.update({
            "profile_fields": [
                {"key": "voice_id", "label": "Edge voice", "type": "text", "required": True, "placeholder": "Ví dụ: ko-KR-SunHiNeural"},
                {"key": "speed", "label": "Tốc độ đọc", "type": "number", "min": 0.5, "max": 2, "step": 0.1, "default": 1},
            ],
            "supports_voice_catalog": True,
            "no_api_key_required": True,
        })
        # Surface every built-in starter profile, while the generic base class
        # still exposes the Korean one for all providers.
        data["suggested_profiles"] = {
            locale: self.default_profile(locale) for locale in self._default_voices
        }
        return data

    def default_profile(self, locale: str) -> dict[str, Any]:
        profile = super().default_profile(locale)
        voice_id = self._default_voices.get(locale)
        if voice_id:
            profile["voice_id"] = voice_id
        return profile

    @staticmethod
    def _module():
        try:
            import edge_tts
            return edge_tts
        except ImportError as exc:
            raise TTSProviderError("Edge TTS chưa được cài. Chạy install.bat hoặc pip install edge-tts.") from exc

    def list_voices(self, locale: str, provider_settings: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            voices = asyncio.run(self._module().list_voices())
        except TTSProviderError:
            raise
        except Exception as exc:
            raise TTSProviderError(f"Edge TTS: không tải được danh sách giọng: {exc}") from exc
        return [
            {
                "id": item.get("ShortName", ""),
                "label": item.get("FriendlyName") or item.get("ShortName", ""),
                "gender": item.get("Gender", ""),
            }
            for item in voices
            if item.get("Locale") == locale and item.get("ShortName")
        ]

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        self.validate(request.provider_settings, {**request.profile, "voice_id": request.voice_id})
        rate = f"{int(round((request.speed - 1.0) * 100)):+d}%"
        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(prefix="lucylab_edge_", suffix=".mp3", delete=False) as temp_file:
                temp_path = temp_file.name
            edge_tts = self._module()
            for attempt in range(4):  # Edge service intermittently answers "No audio was received"
                try:
                    asyncio.run(edge_tts.Communicate(request.text, voice=request.voice_id, rate=rate).save(temp_path))
                    break
                except edge_tts.exceptions.NoAudioReceived:
                    if attempt == 3:
                        raise
                    time.sleep(2 * (attempt + 1))
            with open(temp_path, "rb") as audio_file:
                audio = audio_file.read()
        except TTSProviderError:
            raise
        except Exception as exc:
            raise TTSProviderError(f"Edge TTS: không thể tạo audio: {exc}") from exc
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
        if len(audio) <= 500:
            raise TTSProviderError("Edge TTS: audio trả về rỗng hoặc quá ngắn.")
        return SynthesisResult(
            audio=audio,
            extension="mp3",
            content_type="audio/mpeg",
            metadata={"provider": self.provider_id, "voice_id": request.voice_id},
        )


class TTSProviderRegistry:
    def __init__(self, providers: list[TTSProvider]):
        self._providers = {provider.provider_id: provider for provider in providers}

    def get(self, provider_id: str) -> TTSProvider:
        provider = self._providers.get(provider_id)
        if not provider:
            raise TTSProviderError(f"TTS provider không được hỗ trợ: {provider_id}")
        return provider

    def definitions(self) -> list[dict[str, Any]]:
        return [provider.definition() for provider in self._providers.values()]


# Edge is intentionally the first suggested non-Vivibe option: it works for
# English, Korean and Japanese with no account or API key. Explicit profiles
# always win, so existing Vivibe/Azure/ElevenLabs projects remain unchanged.
TTS_PROVIDERS = TTSProviderRegistry([VivibeProvider(), EdgeTTSProvider(), AzureSpeechProvider(), ElevenLabsProvider()])


def resolve_tts_profile(config: dict[str, Any], locale: str) -> tuple[TTSProvider, dict[str, Any], dict[str, Any]]:
    """Resolve exact locale profile without silently changing provider."""
    locale = (locale or "vi-VN").strip() or "vi-VN"
    profiles = config.get("tts_language_profiles") or {}
    stored_profile = profiles.get(locale) if isinstance(profiles, dict) else None
    if isinstance(stored_profile, dict):
        profile = dict(stored_profile)
        provider_id = str(profile.get("provider") or "vivibe")
    elif locale == "vi-VN":
        provider_id = "vivibe"
        profile = {"provider": provider_id, "voice_id": config.get("voice_id", ""), "speed": config.get("speed", 1.0)}
    else:
        edge = TTS_PROVIDERS.get("edge_tts")
        profile = edge.default_profile(locale)
        if not profile.get("voice_id"):
            raise TTSProviderError(f"Chưa có profile TTS cho {locale}. Vào Cấu hình → Ngôn ngữ → Voice theo ngôn ngữ để thêm provider và voice.")
        provider_id = edge.provider_id

    provider = TTS_PROVIDERS.get(provider_id)
    if provider_id == "vivibe":
        settings = {"jwt_token": config.get("jwt_token", "")}
        profile.setdefault("voice_id", config.get("voice_id", ""))
        profile.setdefault("speed", config.get("speed", 1.0))
    else:
        settings_root = config.get("tts_provider_settings") or {}
        settings = dict(settings_root.get(provider_id) or {}) if isinstance(settings_root, dict) else {}
    profile.setdefault("provider", provider_id)
    profile.setdefault("speed", 1.0)
    provider.validate(settings, profile)
    return provider, settings, profile
