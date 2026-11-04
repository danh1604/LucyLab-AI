import AppSelect from './AppSelect.jsx'
import { useState, useEffect } from 'react'
import { Settings, Key, Mic, Gauge, Hash, Users, Clock, Save, Eye, EyeOff, ChevronDown, ChevronUp, Pause, Plus, Sparkles, Trash2, Loader2 } from 'lucide-react'
import { getConfig, updateConfig, getLanguages, getModels, startGpt2apiRuntime, testThumbnailVision, getTtsProviders, getTtsProviderVoices, testTtsProvider } from '../api.js'
import GptAccountsPanel from './GptAccountsPanel.jsx'

const TABS = ['TTS', 'AI Story', 'Ngôn ngữ', 'Image API', 'YouTube', 'Channel']
const DEFAULT_CUSTOM_IMAGE_BASE_URL = 'https://api.openai.com'
const DEFAULT_CUSTOM_IMAGE_PATH = '/v1/images/edits'
const DEFAULT_SAFE_PROMPT_BASE_URL = 'https://api.openai.com'
const DEFAULT_SAFE_PROMPT_PATH = '/v1/chat/completions'
const BUILTIN_VOICE_PRESETS = [
  { id: 'mhsL3CPLxmLYdSTKp3GANz', key: 'huyen', label: 'Huyền' },
  { id: 'cLZiqtzLcKYqwYrWJemAJH', key: 'chi-mai', label: 'Chi Mai' },
]
const VOICE_LOCALE_FALLBACK = [
  { id: 'en-US', label: 'English (United States) (Tiếng Anh Mỹ)' },
  { id: 'en-GB', label: 'English (United Kingdom) (Tiếng Anh Anh)' },
  { id: 'ko-KR', label: '한국어 (Tiếng Hàn)' },
  { id: 'ja-JP', label: '日本語 (Tiếng Nhật)' },
  { id: 'zh-CN', label: '中文（简体） (Tiếng Trung giản thể)' },
]

function normalizeCustomVoicePresets(value) {
  const seenIds = new Set(BUILTIN_VOICE_PRESETS.map(preset => preset.id))
  return (Array.isArray(value) ? value : []).flatMap((item, index) => {
    const id = String(item?.id || '').trim()
    const label = String(item?.label || '').trim()
    if (!id || !label || seenIds.has(id)) return []
    seenIds.add(id)
    return [{ id, label, key: `custom-${index}-${id}` }]
  })
}
const IMAGE_SIZE_OPTIONS = [
  { value: '1024x1024', label: '1024×1024 · Vuông GPT' },
  { value: '1536x1024', label: '1536×1024 · Ngang GPT' },
  { value: '1024x1536', label: '1024×1536 · Dọc GPT' },
  { value: '1792x1024', label: '1792×1024 · 16:9 legacy' },
  { value: '1024x1792', label: '1024×1792 · 9:16 legacy' },
  { value: '2560x1440', label: '2560×1440 · 16:9 Seedream' },
  { value: '1440x2560', label: '1440×2560 · 9:16 Seedream' },
  { value: '2048x2048', label: '2048×2048 · Vuông lớn' },
  { value: '3840x2160', label: '3840×2160 · 4K 16:9' },
  { value: '2160x3840', label: '2160×3840 · 4K 9:16' },
]

function normalizeCustomImageConfig(data) {
  const next = { ...data }
  const base = String(next.custom_image_base_url || '').trim().replace(/\/+$/, '')
  let path = String(next.custom_image_path || DEFAULT_CUSTOM_IMAGE_PATH).trim()
  if (!path.startsWith('/')) path = `/${path}`

  for (const suffix of [path, '/v1/images/edits', '/images/edits', '/v1/images/generations', '/images/generations']) {
    if (base.toLowerCase().endsWith(suffix.toLowerCase())) {
      next.custom_image_base_url = base.slice(0, -suffix.length).replace(/\/+$/, '') || base
      next.custom_image_path = suffix
      return next
    }
  }

  next.custom_image_base_url = base || DEFAULT_CUSTOM_IMAGE_BASE_URL
  next.custom_image_path = path || DEFAULT_CUSTOM_IMAGE_PATH
  return next
}

function normalizeSafePromptConfig(data) {
  const next = { ...data }
  const base = String(next.safe_prompt_base_url || '').trim().replace(/\/+$/, '')
  let path = String(next.safe_prompt_path || DEFAULT_SAFE_PROMPT_PATH).trim()
  if (!path.startsWith('/')) path = `/${path}`

  for (const suffix of [path, '/v1/chat/completions', '/chat/completions']) {
    if (base.toLowerCase().endsWith(suffix.toLowerCase())) {
      next.safe_prompt_base_url = base.slice(0, -suffix.length).replace(/\/+$/, '') || base
      next.safe_prompt_path = suffix
      return next
    }
  }

  next.safe_prompt_base_url = base || DEFAULT_SAFE_PROMPT_BASE_URL
  next.safe_prompt_path = path || DEFAULT_SAFE_PROMPT_PATH
  return next
}

function normalizeScriptReviewConfig(data) {
  const next = { ...data }
  const base = String(next.script_review_base_url || '').trim().replace(/\/+$/, '')
  let path = String(next.script_review_path || DEFAULT_SAFE_PROMPT_PATH).trim()
  if (!path.startsWith('/')) path = `/${path}`

  for (const suffix of [path, '/v1/chat/completions', '/chat/completions']) {
    if (base.toLowerCase().endsWith(suffix.toLowerCase())) {
      next.script_review_base_url = base.slice(0, -suffix.length).replace(/\/+$/, '') || base
      next.script_review_path = suffix
      return next
    }
  }

  next.script_review_base_url = base || DEFAULT_SAFE_PROMPT_BASE_URL
  next.script_review_path = path || DEFAULT_SAFE_PROMPT_PATH
  return next
}

function normalizeVisualDirectorConfig(data) {
  const next = { ...data }
  const base = String(next.visual_director_base_url || '').trim().replace(/\/+$/, '')
  let path = String(next.visual_director_path || DEFAULT_SAFE_PROMPT_PATH).trim()
  if (!path.startsWith('/')) path = `/${path}`

  for (const suffix of [path, '/v1/chat/completions', '/chat/completions']) {
    if (base.toLowerCase().endsWith(suffix.toLowerCase())) {
      next.visual_director_base_url = base.slice(0, -suffix.length).replace(/\/+$/, '') || base
      next.visual_director_path = suffix
      return next
    }
  }

  next.visual_director_base_url = base || DEFAULT_SAFE_PROMPT_BASE_URL
  next.visual_director_path = path || DEFAULT_SAFE_PROMPT_PATH
  return next
}

function normalizeStickFigureConfig(data) {
  const next = { ...data }
  const base = String(next.stick_figure_base_url || '').trim().replace(/\/+$/, '')
  let path = String(next.stick_figure_path || DEFAULT_SAFE_PROMPT_PATH).trim()
  if (!path.startsWith('/')) path = `/${path}`
  for (const suffix of [path, '/v1/chat/completions', '/chat/completions']) {
    if (base.toLowerCase().endsWith(suffix.toLowerCase())) {
      next.stick_figure_base_url = base.slice(0, -suffix.length).replace(/\/+$/, '') || base
      next.stick_figure_path = suffix
      return next
    }
  }
  next.stick_figure_base_url = base || DEFAULT_SAFE_PROMPT_BASE_URL
  next.stick_figure_path = path || DEFAULT_SAFE_PROMPT_PATH
  return next
}

function normalizeThumbnailVisionConfig(data) {
  const next = { ...data }
  const base = String(next.thumbnail_vision_base_url || '').trim().replace(/\/+$/, '')
  let path = String(next.thumbnail_vision_path || DEFAULT_SAFE_PROMPT_PATH).trim()
  if (!path.startsWith('/')) path = `/${path}`
  for (const suffix of [path, '/v1/chat/completions', '/chat/completions']) {
    if (base.toLowerCase().endsWith(suffix.toLowerCase())) {
      next.thumbnail_vision_base_url = base.slice(0, -suffix.length).replace(/\/+$/, '') || base
      next.thumbnail_vision_path = suffix
      return next
    }
  }
  next.thumbnail_vision_base_url = base || DEFAULT_SAFE_PROMPT_BASE_URL
  next.thumbnail_vision_path = path || DEFAULT_SAFE_PROMPT_PATH
  return next
}

function normalizeYouTubeMetadataConfig(data) {
  const next = { ...data }
  const base = String(next.youtube_metadata_base_url || '').trim().replace(/\/+$/, '')
  let path = String(next.youtube_metadata_path || DEFAULT_SAFE_PROMPT_PATH).trim()
  if (!path.startsWith('/')) path = `/${path}`
  for (const suffix of [path, '/v1/chat/completions', '/chat/completions']) {
    if (base.toLowerCase().endsWith(suffix.toLowerCase())) {
      next.youtube_metadata_base_url = base.slice(0, -suffix.length).replace(/\/+$/, '') || base
      next.youtube_metadata_path = suffix
      return next
    }
  }
  next.youtube_metadata_base_url = base || DEFAULT_SAFE_PROMPT_BASE_URL
  next.youtube_metadata_path = path || DEFAULT_SAFE_PROMPT_PATH
  return next
}

function normalizeConfig(data) {
  return normalizeYouTubeMetadataConfig(normalizeThumbnailVisionConfig(normalizeStickFigureConfig(normalizeVisualDirectorConfig(normalizeScriptReviewConfig(normalizeSafePromptConfig(normalizeCustomImageConfig(data)))))))
}

export default function ConfigPanel({ onSaved }) {
  const [cfg, setCfg] = useState(null)
  const [saving, setSaving] = useState(false)
  const [showJwt, setShowJwt] = useState(false)
  const [showAiKey, setShowAiKey] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [saved, setSaved] = useState(false)
  const [visionTesting, setVisionTesting] = useState(false)
  const [visionTestMessage, setVisionTestMessage] = useState('')
  const [gpt2apiStarting, setGpt2apiStarting] = useState(false)
  const [gpt2apiStartMessage, setGpt2apiStartMessage] = useState('')
  const [tab, setTab] = useState('TTS')
  const [voiceProfileLocale, setVoiceProfileLocale] = useState('en-US')
  const [voiceProfileLocales, setVoiceProfileLocales] = useState(VOICE_LOCALE_FALLBACK)
  const [ttsProviders, setTtsProviders] = useState([])
  const [ttsSetupProvider, setTtsSetupProvider] = useState('azure_speech')
  const [providerVoices, setProviderVoices] = useState([])
  const [providerVoiceLoading, setProviderVoiceLoading] = useState(false)
  const [providerVoiceMessage, setProviderVoiceMessage] = useState('')
  const [ttsPreviewing, setTtsPreviewing] = useState(false)
  const [vivibePreviewMessage, setVivibePreviewMessage] = useState('')
  const [newVoiceName, setNewVoiceName] = useState('')
  const [newVoiceId, setNewVoiceId] = useState('')
  const [models, setModels] = useState({ anthropic: [], ollama: [], codex: [], opencode: [] })
  const autoVoiceProviderId = cfg?.tts_language_profiles?.[voiceProfileLocale]?.provider || (voiceProfileLocale === 'ko-KR' ? 'edge_tts' : 'vivibe')

  useEffect(() => {
    getConfig().then(data => setCfg(normalizeConfig(data)))
    getModels().then(setModels).catch(() => {})
    getLanguages()
      .then(data => setVoiceProfileLocales((data.languages || []).filter(locale => locale.id !== 'vi-VN')))
      .catch(() => {})
    getTtsProviders().then(data => setTtsProviders(data.providers || [])).catch(() => {})
  }, [])

  useEffect(() => {
    if (!cfg || autoVoiceProviderId !== 'edge_tts' || voiceProfileLocale === 'custom') return
    let active = true
    setProviderVoiceLoading(true)
    getTtsProviderVoices('edge_tts', voiceProfileLocale)
      .then(data => {
        if (!active) return
        setProviderVoices(data.voices || [])
        setProviderVoiceMessage(`${(data.voices || []).length} giọng Edge TTS cho ${voiceProfileLocale}.`)
      })
      .catch(error => {
        if (active) setProviderVoiceMessage(error?.message || String(error))
      })
      .finally(() => { if (active) setProviderVoiceLoading(false) })
    return () => { active = false }
  }, [Boolean(cfg), autoVoiceProviderId, voiceProfileLocale])

  if (!cfg) return null

  async function handleSave() {
    setSaving(true)
    await updateConfig({
      jwt_token: cfg.jwt_token,
      voice_id: cfg.voice_id,
      voice_presets: cfg.voice_presets ?? [],
      speed: cfg.speed,
      max_chars: cfg.max_chars,
      workers: cfg.workers,
      delay: cfg.delay,
      sentence_gap: cfg.sentence_gap,
      optimize_text: cfg.optimize_text ?? false,
      trim_silence: cfg.trim_silence ?? false,
      silence_db: cfg.silence_db ?? -35,
      anthropic_api_key: cfg.anthropic_api_key ?? '',
      default_story_model: cfg.default_story_model ?? 'claude-sonnet-4-6',
      youtube_metadata_provider: cfg.youtube_metadata_provider ?? 'default',
      youtube_metadata_base_url: cfg.youtube_metadata_base_url ?? DEFAULT_SAFE_PROMPT_BASE_URL,
      youtube_metadata_path: cfg.youtube_metadata_path ?? DEFAULT_SAFE_PROMPT_PATH,
      youtube_metadata_model: cfg.youtube_metadata_model ?? '',
      youtube_metadata_api_key: cfg.youtube_metadata_api_key ?? '',
      youtube_metadata_api_key_path: cfg.youtube_metadata_api_key_path ?? '',
      safe_prompt_model: cfg.safe_prompt_model ?? '',
      safe_prompt_provider: cfg.safe_prompt_provider ?? 'default',
      safe_prompt_base_url: cfg.safe_prompt_base_url ?? DEFAULT_SAFE_PROMPT_BASE_URL,
      safe_prompt_path: cfg.safe_prompt_path ?? DEFAULT_SAFE_PROMPT_PATH,
      safe_prompt_api_key: cfg.safe_prompt_api_key ?? '',
      safe_prompt_api_key_path: cfg.safe_prompt_api_key_path ?? '',
      script_review_model: cfg.script_review_model ?? '',
      script_review_provider: cfg.script_review_provider ?? 'default',
      script_review_base_url: cfg.script_review_base_url ?? DEFAULT_SAFE_PROMPT_BASE_URL,
      script_review_path: cfg.script_review_path ?? DEFAULT_SAFE_PROMPT_PATH,
      script_review_api_key: cfg.script_review_api_key ?? '',
      script_review_api_key_path: cfg.script_review_api_key_path ?? '',
      script_review_max_comments_per_agent: Number(cfg.script_review_max_comments_per_agent ?? 12),
      script_review_min_severity: cfg.script_review_min_severity ?? 'important',
      visual_director_model: cfg.visual_director_model ?? '',
      visual_director_provider: cfg.visual_director_provider ?? 'default',
      visual_director_base_url: cfg.visual_director_base_url ?? DEFAULT_SAFE_PROMPT_BASE_URL,
      visual_director_path: cfg.visual_director_path ?? DEFAULT_SAFE_PROMPT_PATH,
      visual_director_api_key: cfg.visual_director_api_key ?? '',
      visual_director_api_key_path: cfg.visual_director_api_key_path ?? '',
      stick_figure_model: cfg.stick_figure_model ?? '',
      stick_figure_provider: cfg.stick_figure_provider ?? 'default',
      stick_figure_base_url: cfg.stick_figure_base_url ?? DEFAULT_SAFE_PROMPT_BASE_URL,
      stick_figure_path: cfg.stick_figure_path ?? DEFAULT_SAFE_PROMPT_PATH,
      stick_figure_api_key: cfg.stick_figure_api_key ?? '',
      stick_figure_api_key_path: cfg.stick_figure_api_key_path ?? '',
      thumbnail_vision_base_url: cfg.thumbnail_vision_base_url ?? DEFAULT_SAFE_PROMPT_BASE_URL,
      thumbnail_vision_path: cfg.thumbnail_vision_path ?? DEFAULT_SAFE_PROMPT_PATH,
      thumbnail_vision_model: cfg.thumbnail_vision_model ?? '',
      thumbnail_vision_api_key: cfg.thumbnail_vision_api_key ?? '',
      thumbnail_vision_api_key_path: cfg.thumbnail_vision_api_key_path ?? '',
      ffmpeg_path: cfg.ffmpeg_path ?? 'ffmpeg',
      ffprobe_path: cfg.ffprobe_path ?? 'ffprobe',
      ollama_url: cfg.ollama_url ?? 'http://localhost:11434',
      youtube_client_secrets_path: cfg.youtube_client_secrets_path ?? '',
      youtube_timezone: cfg.youtube_timezone ?? 'Asia/Ho_Chi_Minh',
      youtube_long_slots: String(cfg.youtube_long_slots || '20:00').split(',').map(x => x.trim()).filter(Boolean),
      youtube_short_slots: String(cfg.youtube_short_slots || '12:00,21:30').split(',').map(x => x.trim()).filter(Boolean),
      youtube_min_gap_hours: Number(cfg.youtube_min_gap_hours ?? 6),
      youtube_max_long_per_day: Number(cfg.youtube_max_long_per_day ?? 1),
      youtube_max_shorts_per_day: Number(cfg.youtube_max_shorts_per_day ?? 2),
      youtube_campaign_mode: cfg.youtube_campaign_mode ?? false,
      youtube_campaign_long_slots: String(cfg.youtube_campaign_long_slots || '20:00').split(',').map(x => x.trim()).filter(Boolean),
      youtube_campaign_short_slots: String(cfg.youtube_campaign_short_slots || '12:00,20:00').split(',').map(x => x.trim()).filter(Boolean),
      gpt2api_base_url: cfg.gpt2api_base_url ?? 'http://127.0.0.1:7860/v1/images/generations',
      gpt2api_image_model: cfg.gpt2api_image_model ?? '',
      image_generation_provider: cfg.image_generation_provider ?? 'gpt2api',
      scene_gpt2api_base_url: cfg.scene_gpt2api_base_url ?? 'http://127.0.0.1:17200/v1',
      scene_gpt2api_api_key: cfg.scene_gpt2api_api_key ?? '',
      scene_gpt2api_api_key_path: cfg.scene_gpt2api_api_key_path ?? 'D:\\Project\\Deepseek\\gpt2api\\.local\\openai-compatible-api-key.txt',
      scene_gpt2api_asset_base_url: cfg.scene_gpt2api_asset_base_url ?? 'http://127.0.0.1:17080',
      scene_gpt2api_image_model: cfg.scene_gpt2api_image_model ?? 'gpt-image-2',
      scene_gpt2api_image_resolution: cfg.scene_gpt2api_image_resolution ?? '1K',
      scene_gpt2api_image_size: cfg.scene_gpt2api_image_size ?? '1792x1024',
      gpt2api_autostart: cfg.gpt2api_autostart ?? true,
      gpt2api_start_script: cfg.gpt2api_start_script ?? '',
      gpt2api_startup_wait_sec: Number(cfg.gpt2api_startup_wait_sec ?? 240),
      gpt2api_admin_base_url: cfg.gpt2api_admin_base_url ?? 'http://127.0.0.1:17188',
      gpt2api_admin_user: cfg.gpt2api_admin_user ?? 'admin',
      gpt2api_admin_password: cfg.gpt2api_admin_password ?? 'admin123',
      gpt2api_codex_auth_path: cfg.gpt2api_codex_auth_path ?? '',
      gpt2api_codex_client_id: cfg.gpt2api_codex_client_id ?? 'app_EMoamEEZ73f0CkXaXp7hrann',
      gpt2api_account_name_prefix: cfg.gpt2api_account_name_prefix ?? 'lucylab-codex',
      custom_image_base_url: cfg.custom_image_base_url ?? DEFAULT_CUSTOM_IMAGE_BASE_URL,
      custom_image_path: cfg.custom_image_path ?? DEFAULT_CUSTOM_IMAGE_PATH,
      custom_image_api_key: cfg.custom_image_api_key ?? '',
      custom_image_model: cfg.custom_image_model ?? 'gpt-image-1',
      custom_image_size: cfg.custom_image_size ?? '1536x1024',
      thumbnail_image_size: cfg.thumbnail_image_size ?? '1280x720',
      channel_positioning: cfg.channel_positioning ?? '',
      channel_audience: cfg.channel_audience ?? '',
      channel_pillars: cfg.channel_pillars ?? '',
      channel_banned_title_patterns: cfg.channel_banned_title_patterns ?? '',
      channel_banned_hashtags: cfg.channel_banned_hashtags ?? '',
      language_agent_provider: cfg.language_agent_provider ?? 'default',
      language_agent_base_url: cfg.language_agent_base_url ?? DEFAULT_SAFE_PROMPT_BASE_URL,
      language_agent_path: cfg.language_agent_path ?? DEFAULT_SAFE_PROMPT_PATH,
      language_agent_model: cfg.language_agent_model ?? '',
      language_agent_api_key: cfg.language_agent_api_key ?? '',
      language_agent_api_key_path: cfg.language_agent_api_key_path ?? '',
      tts_language_profiles: cfg.tts_language_profiles ?? {},
      tts_provider_settings: cfg.tts_provider_settings ?? {},
    })
    setSaving(false)
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
    onSaved?.()
  }

  async function handleTestThumbnailVision() {
    setVisionTesting(true)
    setVisionTestMessage('')
    try {
      await testThumbnailVision()
      setVisionTestMessage('Vision API phản hồi thành công.')
    } catch (error) {
      setVisionTestMessage(error?.message || String(error))
    } finally {
      setVisionTesting(false)
    }
  }

  async function handleStartGpt2api() {
    setGpt2apiStarting(true)
    setGpt2apiStartMessage('')
    try {
      const result = await startGpt2apiRuntime()
      setGpt2apiStartMessage(result.status === 'ready'
        ? 'gpt2api đã sẵn sàng: API ảnh và quản lý tài khoản đều phản hồi.'
        : 'Đang khởi động gpt2api nền; app sẽ chờ cả API ảnh và quản lý tài khoản sẵn sàng.')
    } catch (error) {
      setGpt2apiStartMessage(error?.message || String(error))
    } finally {
      setGpt2apiStarting(false)
    }
  }

  function handleImageProviderChange(provider) {
    setCfg(p => ({ ...p, image_generation_provider: provider }))
    if (provider === 'gpt2api') handleStartGpt2api()
    else setGpt2apiStartMessage('')
  }

  const field = (label, icon, key, type = 'text', extra = {}) => (
    <div className="space-y-1">
      <label className="flex items-center gap-1.5 text-xs font-medium text-slate-400 uppercase tracking-wider">
        {icon}
        {label}
      </label>
      <input
        type={type}
        value={cfg[key] ?? ''}
        onChange={e => setCfg(p => ({ ...p, [key]: type === 'number' ? parseFloat(e.target.value) : e.target.value }))}
        className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-accent-500 transition-colors"
        {...extra}
      />
    </div>
  )

  const sizeSelect = (key, fallback) => {
    const value = cfg[key] || fallback
    const hasCurrent = IMAGE_SIZE_OPTIONS.some(option => option.value === value)
    return (
      <AppSelect
        value={value}
        onChange={e => setCfg(p => ({ ...p, [key]: e.target.value }))}
        className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
      >
        {!hasCurrent && <option value={value}>{value} · hiện tại</option>}
        {IMAGE_SIZE_OPTIONS.map(option => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </AppSelect>
    )
  }

  const storyModelOptions = (emptyLabel = 'Dùng model mặc định') => (
    <>
      <option value="">{emptyLabel}</option>
      {models.anthropic.length > 0 && (
        <optgroup label="Claude Code (local config)">
          {models.anthropic.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
        </optgroup>
      )}
      {models.ollama.length > 0 && (
        <optgroup label="Ollama (local)">
          {models.ollama.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
        </optgroup>
      )}
      {models.codex?.length > 0 && (
        <optgroup label="Codex CLI">
          {models.codex.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
        </optgroup>
      )}
      {models.opencode?.length > 0 && (
        <optgroup label="OpenCode CLI">
          {models.opencode.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
        </optgroup>
      )}
      {models.anthropic.length === 0 && models.ollama.length === 0 && !models.codex?.length && !models.opencode?.length && (
        <>
          <option value="claude-sonnet-4-6">Claude Sonnet 4.6 (local config)</option>
          <option value="claude-opus-4-8">Claude Opus 4.8 (local config)</option>
          <option value="claude-haiku-4-5-20251001">Claude Haiku 4.5 (local config)</option>
        </>
      )}
    </>
  )

  const configuredStoryModel = String(cfg.default_story_model || 'claude-sonnet-4-6')
  const listedStoryModelIds = [
    ...(models.anthropic || []),
    ...(models.ollama || []),
    ...(models.codex || []),
    ...(models.opencode || []),
  ].map(model => String(model.id))
  const languageAgentModel = String(cfg.language_agent_model || '')
  const languageAgentCustom = (cfg.language_agent_provider || 'default') === 'custom'
  const customVoicePresets = normalizeCustomVoicePresets(cfg.voice_presets)
  const voicePresets = [...BUILTIN_VOICE_PRESETS, ...customVoicePresets]
  const voiceChoice = (voiceId) => voicePresets.find(preset => preset.id === String(voiceId || ''))?.key || 'custom'
  const setPresetVoice = (selectValue, onChange) => {
    const preset = voicePresets.find(item => item.key === selectValue)
    onChange(preset ? preset.id : '')
  }
  const addVoicePreset = () => {
    const label = newVoiceName.trim()
    const id = newVoiceId.trim()
    if (!label || !id || voicePresets.some(preset => preset.id === id)) return
    setCfg(previous => ({ ...previous, voice_presets: [...normalizeCustomVoicePresets(previous.voice_presets), { label, id }] }))
    setNewVoiceName('')
    setNewVoiceId('')
  }
  const removeVoicePreset = (id) => setCfg(previous => ({
    ...previous,
    voice_presets: normalizeCustomVoicePresets(previous.voice_presets).filter(preset => preset.id !== id),
  }))
  const providerById = Object.fromEntries(ttsProviders.map(provider => [provider.id, provider]))
  const profileRaw = (cfg.tts_language_profiles || {})[voiceProfileLocale] || {}
  const impliedProviderId = profileRaw.provider || (voiceProfileLocale === 'ko-KR' ? 'edge_tts' : 'vivibe')
  const activeTtsProvider = providerById[impliedProviderId] || providerById.vivibe
  const suggestedTtsProfile = activeTtsProvider?.suggested_profiles?.[voiceProfileLocale] || {}
  const activeTtsProfile = { ...suggestedTtsProfile, ...profileRaw, provider: impliedProviderId, speed: profileRaw.speed ?? suggestedTtsProfile.speed ?? 1 }

  const updateTtsProfile = (updates) => setCfg(previous => ({
    ...previous,
    tts_language_profiles: {
      ...(previous.tts_language_profiles || {}),
      [voiceProfileLocale]: { ...activeTtsProfile, ...updates },
    },
  }))
  const updateTtsProviderSettings = (providerId, key, value) => setCfg(previous => ({
    ...previous,
    tts_provider_settings: {
      ...(previous.tts_provider_settings || {}),
      [providerId]: { ...((previous.tts_provider_settings || {})[providerId] || {}), [key]: value },
    },
  }))
  const changeTtsProvider = (providerId) => {
    const provider = providerById[providerId]
    const suggested = provider?.suggested_profiles?.[voiceProfileLocale] || {}
    updateTtsProfile({ ...suggested, provider: providerId, voice_id: suggested.voice_id || '' })
    setProviderVoices([])
    setProviderVoiceMessage('')
  }
  const selectVoiceProfileLocale = (locale) => {
    setVoiceProfileLocale(locale)
    if (locale === 'custom' || (cfg.tts_language_profiles || {})[locale]) return
    // Adapter metadata, not a UI hard-code, decides which locale receives a
    // useful starter profile (Azure exposes Korean Hyunsu here).
    const suggestedProvider = ttsProviders.find(provider => provider.suggested_profiles?.[locale])
    if (!suggestedProvider) return
    const suggestion = suggestedProvider.suggested_profiles[locale]
    setCfg(previous => ({
      ...previous,
      tts_language_profiles: {
        ...(previous.tts_language_profiles || {}),
        [locale]: { ...suggestion, provider: suggestedProvider.id },
      },
    }))
  }
  const loadProviderVoices = async () => {
    if (!activeTtsProvider?.supports_voice_catalog) return
    setProviderVoiceLoading(true)
    setProviderVoiceMessage('')
    try {
      const data = await getTtsProviderVoices(activeTtsProvider.id, voiceProfileLocale)
      setProviderVoices(data.voices || [])
      setProviderVoiceMessage(`${(data.voices || []).length} giọng khả dụng cho ${voiceProfileLocale}.`)
    } catch (error) {
      setProviderVoiceMessage(error?.message || String(error))
    } finally {
      setProviderVoiceLoading(false)
    }
  }
  const previewTtsVoice = async () => {
    if (!activeTtsProvider) return
    setTtsPreviewing(true)
    setProviderVoiceMessage('')
    try {
      const blob = await testTtsProvider(activeTtsProvider.id, voiceProfileLocale, activeTtsProfile)
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      audio.onended = () => URL.revokeObjectURL(url)
      await audio.play()
    } catch (error) {
      setProviderVoiceMessage(error?.message || String(error))
    } finally {
      setTtsPreviewing(false)
    }
  }

  const previewVivibeVoice = async (voiceOverride = '', voiceLabel = '') => {
    const voiceId = String(voiceOverride || cfg?.voice_id || '').trim()
    if (!voiceId) return
    setTtsPreviewing(true)
    setVivibePreviewMessage('')
    try {
      const blob = await testTtsProvider('vivibe', 'vi-VN', {
        provider: 'vivibe',
        voice_id: voiceId,
        speed: Number(cfg?.speed || 1),
      })
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      const cleanup = () => URL.revokeObjectURL(url)
      audio.onended = cleanup
      audio.onerror = cleanup
      await audio.play()
      setVivibePreviewMessage(`Đang phát thử: ${voiceLabel || voicePresets.find(preset => preset.id === voiceId)?.label || voiceId}`)
    } catch (error) {
      setVivibePreviewMessage(error?.message || String(error))
    } finally {
      setTtsPreviewing(false)
    }
  }

  return (
    <div className="glass rounded-2xl overflow-hidden">
      <button
        onClick={() => setExpanded(p => !p)}
        className="w-full flex items-center justify-between px-5 py-4 hover:bg-surface-700 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Settings size={16} className="text-accent-400" />
          <span className="text-sm font-semibold text-white">Cấu hình</span>
          {cfg.voice_id && (
            <span className="text-xs text-slate-500 ml-2 font-mono">
              {cfg.voice_id.slice(0, 8)}...
            </span>
          )}
        </div>
        {expanded ? <ChevronUp size={14} className="text-slate-500" /> : <ChevronDown size={14} className="text-slate-500" />}
      </button>

      {expanded && (
        <div className="px-5 pb-5 space-y-4 border-t border-surface-600 pt-4 animate-fade-in">
          {/* Tabs */}
          <div className="flex gap-1 bg-surface-800 rounded-lg p-1">
            {TABS.map(t => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`flex-1 py-1.5 rounded-md text-xs font-medium transition-all ${
                  tab === t ? 'bg-surface-600 text-white' : 'text-slate-500 hover:text-slate-300'
                }`}
              >
                {t}
              </button>
            ))}
          </div>

          {tab === 'AI Story' && (
            <div className="space-y-4">
              <div className="space-y-1">
                <label className="flex items-center gap-1.5 text-xs font-medium text-slate-400 uppercase tracking-wider">
                  <Key size={12} /> Anthropic API Key (tuỳ chọn)
                </label>
                <div className="relative">
                  <input
                    type={showAiKey ? 'text' : 'password'}
                    value={cfg.anthropic_api_key || ''}
                    onChange={e => setCfg(p => ({ ...p, anthropic_api_key: e.target.value }))}
                    placeholder="sk-ant-..."
                    className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 pr-10 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-accent-500 transition-colors font-mono"
                  />
                  <button
                    onClick={() => setShowAiKey(p => !p)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
                  >
                    {showAiKey ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
                <p className="text-xs text-slate-600">Không cần nhập nếu dùng Claude Code/Codex/OpenCode local config.</p>
              </div>

              <div className="space-y-1">
                <label className="flex items-center gap-1.5 text-xs font-medium text-slate-400 uppercase tracking-wider">
                  <Sparkles size={12} /> Model mặc định
                </label>
                <AppSelect
                  value={cfg.default_story_model || 'claude-sonnet-4-6'}
                  onChange={e => setCfg(p => ({ ...p, default_story_model: e.target.value }))}
                  className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                >
                  {models.anthropic.length > 0 && (
                    <optgroup label="Claude Code (local config)">
                      {models.anthropic.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
                    </optgroup>
                  )}
                  {models.ollama.length > 0 && (
                    <optgroup label="Ollama (local)">
                      {models.ollama.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
                    </optgroup>
                  )}
                  {models.codex?.length > 0 && (
                    <optgroup label="Codex CLI">
                      {models.codex.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
                    </optgroup>
                  )}
                  {models.opencode?.length > 0 && (
                    <optgroup label="OpenCode CLI">
                      {models.opencode.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
                    </optgroup>
                  )}
                  {models.anthropic.length === 0 && models.ollama.length === 0 && !models.codex?.length && !models.opencode?.length && (
                    <>
                      <option value="claude-sonnet-4-6">Claude Sonnet 4.6 (local config)</option>
                      <option value="claude-opus-4-8">Claude Opus 4.8 (local config)</option>
                      <option value="claude-haiku-4-5-20251001">Claude Haiku 4.5 (local config)</option>
                    </>
                  )}
                </AppSelect>
                <p className="text-xs text-slate-600">Phạm vi: viết truyện từ yêu cầu, hỏi đáp làm rõ và là model kế thừa cho các agent chưa chọn model riêng.</p>
              </div>

              <div className="space-y-1">
                <label className="flex items-center gap-1.5 text-xs font-medium text-slate-400 uppercase tracking-wider">
                  <Sparkles size={12} /> Model safe prompt LLM
                </label>
                <AppSelect
                  value={cfg.safe_prompt_model || ''}
                  onChange={e => setCfg(p => ({ ...p, safe_prompt_model: e.target.value }))}
                  className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                >
                  <option value="">Dùng model mặc định</option>
                  {models.anthropic.length > 0 && (
                    <optgroup label="Claude Code (local config)">
                      {models.anthropic.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
                    </optgroup>
                  )}
                  {models.ollama.length > 0 && (
                    <optgroup label="Ollama (local)">
                      {models.ollama.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
                    </optgroup>
                  )}
                  {models.codex?.length > 0 && (
                    <optgroup label="Codex CLI">
                      {models.codex.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
                    </optgroup>
                  )}
                  {models.opencode?.length > 0 && (
                    <optgroup label="OpenCode CLI">
                      {models.opencode.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
                    </optgroup>
                  )}
                  {models.anthropic.length === 0 && models.ollama.length === 0 && !models.codex?.length && !models.opencode?.length && (
                    <>
                      <option value="claude-sonnet-4-6">Claude Sonnet 4.6 (local config)</option>
                      <option value="claude-opus-4-8">Claude Opus 4.8 (local config)</option>
                      <option value="claude-haiku-4-5-20251001">Claude Haiku 4.5 (local config)</option>
                    </>
                  )}
                </AppSelect>
                <p className="text-xs text-slate-600">Phạm vi: chỉ dùng khi chọn Safe mode LLM rồi bấm Safe; không thay đổi prompt hoặc ảnh đang tạo bình thường.</p>
              </div>

              <div className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-3">
                <div>
                  <p className="text-sm font-semibold text-white">Provider Safe Prompt</p>
                  <p className="text-xs text-slate-500">Phạm vi: Safe LLM cho ảnh scene, reference và thumbnail. Không dùng để tạo Visual Plan hay narration.</p>
                </div>

                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Provider</label>
                  <AppSelect
                    value={cfg.safe_prompt_provider || 'default'}
                    onChange={e => setCfg(p => ({ ...p, safe_prompt_provider: e.target.value }))}
                    className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                  >
                    <option value="default">Dùng model/agent mặc định ở trên</option>
                    <option value="custom">Custom OpenAI-compatible API</option>
                  </AppSelect>
                </div>

                {(cfg.safe_prompt_provider || 'default') === 'custom' && (
                  <div className="space-y-3">
                    <div className="space-y-1">
                      <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Base URL</label>
                      <input
                        value={cfg.safe_prompt_base_url || DEFAULT_SAFE_PROMPT_BASE_URL}
                        onChange={e => setCfg(p => ({ ...p, safe_prompt_base_url: e.target.value }))}
                        placeholder="https://api.example.com"
                        className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                      />
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Path</label>
                        <input
                          value={cfg.safe_prompt_path || DEFAULT_SAFE_PROMPT_PATH}
                          onChange={e => setCfg(p => ({ ...p, safe_prompt_path: e.target.value }))}
                          placeholder="/v1/chat/completions"
                          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                        />
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Model</label>
                        <input
                          value={cfg.safe_prompt_model || 'gpt-4o-mini'}
                          onChange={e => setCfg(p => ({ ...p, safe_prompt_model: e.target.value }))}
                          placeholder="gpt-4o-mini"
                          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                        />
                      </div>
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">API key</label>
                        <input
                          type="password"
                          value={cfg.safe_prompt_api_key || ''}
                          onChange={e => setCfg(p => ({ ...p, safe_prompt_api_key: e.target.value }))}
                          placeholder="sk-..."
                          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                        />
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">API key path</label>
                        <input
                          value={cfg.safe_prompt_api_key_path || ''}
                          onChange={e => setCfg(p => ({ ...p, safe_prompt_api_key_path: e.target.value }))}
                          placeholder="D:\\Secrets\\safe-llm-key.txt"
                          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                        />
                      </div>
                    </div>
                  </div>
                )}

                <p className="text-xs text-slate-600">Custom dùng endpoint chat completions, không dùng endpoint tạo ảnh. Safe Rule vẫn chạy local.</p>
              </div>

              <div className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-3">
                <div>
                  <p className="text-sm font-semibold text-white">Script Review Agents</p>
                  <p className="text-xs text-slate-500">Phạm vi: chỉ review kịch bản ở bước Nội dung — văn nói, logic, kịch tính, giữ chân và tổng biên tập.</p>
                </div>

                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Provider</label>
                  <AppSelect
                    value={cfg.script_review_provider || 'default'}
                    onChange={e => setCfg(p => ({ ...p, script_review_provider: e.target.value }))}
                    className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                  >
                    <option value="default">Dùng local agent/model mặc định</option>
                    <option value="custom">Custom OpenAI-compatible API</option>
                  </AppSelect>
                </div>

                {(cfg.script_review_provider || 'default') !== 'custom' ? (
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Model reviewer</label>
                    <AppSelect
                      value={cfg.script_review_model || ''}
                      onChange={e => setCfg(p => ({ ...p, script_review_model: e.target.value }))}
                      className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                    >
                      {storyModelOptions('Dùng model của truyện')}
                    </AppSelect>
                  </div>
                ) : (
                  <div className="space-y-3">
                    <div className="space-y-1">
                      <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Base URL</label>
                      <input
                        value={cfg.script_review_base_url || DEFAULT_SAFE_PROMPT_BASE_URL}
                        onChange={e => setCfg(p => ({ ...p, script_review_base_url: e.target.value }))}
                        placeholder="https://api.example.com"
                        className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                      />
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Path</label>
                        <input
                          value={cfg.script_review_path || DEFAULT_SAFE_PROMPT_PATH}
                          onChange={e => setCfg(p => ({ ...p, script_review_path: e.target.value }))}
                          placeholder="/v1/chat/completions"
                          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                        />
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Model</label>
                        <input
                          value={cfg.script_review_model || ''}
                          onChange={e => setCfg(p => ({ ...p, script_review_model: e.target.value }))}
                          placeholder="gpt-4o-mini / gemini-2.5-flash..."
                          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                        />
                      </div>
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">API key</label>
                        <input
                          type="password"
                          value={cfg.script_review_api_key || ''}
                          onChange={e => setCfg(p => ({ ...p, script_review_api_key: e.target.value }))}
                          placeholder="sk-..."
                          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                        />
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">API key path</label>
                        <input
                          value={cfg.script_review_api_key_path || ''}
                          onChange={e => setCfg(p => ({ ...p, script_review_api_key_path: e.target.value }))}
                          placeholder="D:\\Secrets\\review-llm-key.txt"
                          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                        />
                      </div>
                    </div>
                  </div>
                )}

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Comment / agent</label>
                    <input
                      type="number"
                      min="1"
                      max="40"
                      value={cfg.script_review_max_comments_per_agent ?? 12}
                      onChange={e => setCfg(p => ({ ...p, script_review_max_comments_per_agent: Number(e.target.value) }))}
                      className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Mức tối thiểu</label>
                    <AppSelect
                      value={cfg.script_review_min_severity || 'important'}
                      onChange={e => setCfg(p => ({ ...p, script_review_min_severity: e.target.value }))}
                      className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                    >
                      <option value="critical">Chỉ rất quan trọng</option>
                      <option value="important">Quan trọng trở lên</option>
                      <option value="minor">Tất cả, gồm lỗi nhỏ</option>
                    </AppSelect>
                  </div>
                </div>

                <p className="text-xs text-slate-600">Tổng biên tập nhận kết quả từ 4 reviewer, lọc trùng và xếp priority trước khi hiện comment.</p>
              </div>

              <div className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-3">
                <div>
                  <p className="text-sm font-semibold text-white">AI Director & Thumbnail Director</p>
                  <p className="text-xs text-slate-500">Phạm vi: Video mode Director (Visual Bible, reference plan, storyboard, prompt scene) và Thumbnail Director (phân tích truyện, concept, headline, prompt thumbnail). Không dùng cho AI lọc content hoặc Visual Plan Người que.</p>
                </div>

                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Provider</label>
                  <AppSelect
                    value={cfg.visual_director_provider || 'default'}
                    onChange={e => setCfg(p => ({ ...p, visual_director_provider: e.target.value }))}
                    className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                  >
                    <option value="default">Dùng local agent/model mặc định</option>
                    <option value="custom">Custom OpenAI-compatible API</option>
                  </AppSelect>
                </div>

                {(cfg.visual_director_provider || 'default') !== 'custom' ? (
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Model director</label>
                    <AppSelect
                      value={cfg.visual_director_model || ''}
                      onChange={e => setCfg(p => ({ ...p, visual_director_model: e.target.value }))}
                      className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                    >
                      {storyModelOptions('Dùng model mặc định')}
                    </AppSelect>
                  </div>
                ) : (
                  <div className="space-y-3">
                    <div className="space-y-1">
                      <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Base URL</label>
                      <input
                        value={cfg.visual_director_base_url || DEFAULT_SAFE_PROMPT_BASE_URL}
                        onChange={e => setCfg(p => ({ ...p, visual_director_base_url: e.target.value }))}
                        placeholder="https://api.example.com"
                        className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                      />
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Path</label>
                        <input
                          value={cfg.visual_director_path || DEFAULT_SAFE_PROMPT_PATH}
                          onChange={e => setCfg(p => ({ ...p, visual_director_path: e.target.value }))}
                          placeholder="/v1/chat/completions"
                          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                        />
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Model</label>
                        <input
                          value={cfg.visual_director_model || ''}
                          onChange={e => setCfg(p => ({ ...p, visual_director_model: e.target.value }))}
                          placeholder="gpt-4o-mini / gemini-2.5-flash..."
                          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                        />
                      </div>
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">API key</label>
                        <input
                          type="password"
                          value={cfg.visual_director_api_key || ''}
                          onChange={e => setCfg(p => ({ ...p, visual_director_api_key: e.target.value }))}
                          placeholder="sk-..."
                          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                        />
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">API key path</label>
                        <input
                          value={cfg.visual_director_api_key_path || ''}
                          onChange={e => setCfg(p => ({ ...p, visual_director_api_key_path: e.target.value }))}
                          placeholder="D:\\Secrets\\director-llm-key.txt"
                          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                        />
                      </div>
                    </div>
                  </div>
                )}

                <p className="text-xs text-slate-600">Chỉ ảnh hưởng Director mode và Thumbnail Director; không thay đổi pipeline Người que.</p>
              </div>

              <div className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-3">
                <div>
                  <p className="text-sm font-semibold text-white">AI Người que: Lọc content & Visual Plan</p>
                  <p className="text-xs text-slate-500">Phạm vi: toàn bộ LLM pipeline Người que — lọc kịch bản thô thành narration, tạo/tái lập Visual Plan, critic và kế hoạch storyboard/reference. Không dùng để tạo ảnh final, thumbnail, title, mô tả hay hashtag.</p>
                </div>

                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Provider</label>
                  <AppSelect
                    value={cfg.stick_figure_provider || 'default'}
                    onChange={e => setCfg(p => ({ ...p, stick_figure_provider: e.target.value }))}
                    className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-sky-400"
                  >
                    <option value="default">Dùng local agent/model mặc định</option>
                    <option value="custom">Custom OpenAI-compatible API</option>
                  </AppSelect>
                </div>

                {(cfg.stick_figure_provider || 'default') !== 'custom' ? (
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Model AI Người que (tuỳ chọn)</label>
                    <AppSelect
                      value={cfg.stick_figure_model || ''}
                      onChange={e => setCfg(p => ({ ...p, stick_figure_model: e.target.value }))}
                      className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-sky-400"
                    >
                      {storyModelOptions('Dùng model chọn trong form lọc content')}
                    </AppSelect>
                  </div>
                ) : (
                  <div className="space-y-3">
                    <div className="space-y-1">
                      <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Base URL</label>
                      <input
                        value={cfg.stick_figure_base_url || DEFAULT_SAFE_PROMPT_BASE_URL}
                        onChange={e => setCfg(p => ({ ...p, stick_figure_base_url: e.target.value }))}
                        placeholder="https://api.example.com"
                        className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-sky-400 font-mono"
                      />
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Path</label>
                        <input
                          value={cfg.stick_figure_path || DEFAULT_SAFE_PROMPT_PATH}
                          onChange={e => setCfg(p => ({ ...p, stick_figure_path: e.target.value }))}
                          placeholder="/v1/chat/completions"
                          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-sky-400 font-mono"
                        />
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Model</label>
                        <input
                          value={cfg.stick_figure_model || ''}
                          onChange={e => setCfg(p => ({ ...p, stick_figure_model: e.target.value }))}
                          placeholder="gpt-4o-mini / gemini-2.5-flash..."
                          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-sky-400"
                        />
                      </div>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">API key</label>
                        <input
                          type="password"
                          value={cfg.stick_figure_api_key || ''}
                          onChange={e => setCfg(p => ({ ...p, stick_figure_api_key: e.target.value }))}
                          placeholder="sk-..."
                          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-sky-400 font-mono"
                        />
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">API key path</label>
                        <input
                          value={cfg.stick_figure_api_key_path || ''}
                          onChange={e => setCfg(p => ({ ...p, stick_figure_api_key_path: e.target.value }))}
                          placeholder="D:\\Secrets\\stick-figure-llm-key.txt"
                          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-sky-400 font-mono"
                        />
                      </div>
                    </div>
                  </div>
                )}

                <p className="text-xs text-slate-600">Provider custom dùng OpenAI-compatible chat API. Khi chọn custom, mọi bước AI Người que ở trên dùng model này; model chọn trong form lọc content sẽ bị ghi đè.</p>
              </div>

              <div className="rounded-xl border border-surface-600 bg-black/20 p-4 space-y-3">
                <div>
                  <p className="text-sm font-semibold text-white">Thumbnail Vision</p>
                  <p className="text-xs text-slate-500">Phạm vi: chỉ kiểm tra thumbnail sau khi tạo — OCR chữ, chấm chất lượng và quyết định retry. Không tạo concept, prompt hay ảnh.</p>
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Base URL</label>
                  <input
                    value={cfg.thumbnail_vision_base_url || DEFAULT_SAFE_PROMPT_BASE_URL}
                    onChange={e => setCfg(p => ({ ...p, thumbnail_vision_base_url: e.target.value }))}
                    placeholder="https://api.example.com"
                    className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                  />
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Path</label>
                    <input
                      value={cfg.thumbnail_vision_path || DEFAULT_SAFE_PROMPT_PATH}
                      onChange={e => setCfg(p => ({ ...p, thumbnail_vision_path: e.target.value }))}
                      placeholder="/v1/chat/completions"
                      className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Vision model</label>
                    <input
                      value={cfg.thumbnail_vision_model || ''}
                      onChange={e => setCfg(p => ({ ...p, thumbnail_vision_model: e.target.value }))}
                      placeholder="gpt-4o / gemini-2.5-flash..."
                      className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                    />
                  </div>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">API key</label>
                    <input
                      type="password"
                      value={cfg.thumbnail_vision_api_key || ''}
                      onChange={e => setCfg(p => ({ ...p, thumbnail_vision_api_key: e.target.value }))}
                      placeholder="sk-..."
                      className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">API key path</label>
                    <input
                      value={cfg.thumbnail_vision_api_key_path || ''}
                      onChange={e => setCfg(p => ({ ...p, thumbnail_vision_api_key_path: e.target.value }))}
                      placeholder="D:\\Secrets\\thumbnail-vision-key.txt"
                      className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                    />
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={handleTestThumbnailVision}
                    disabled={visionTesting}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-surface-500 px-3 py-2 text-xs font-semibold text-slate-300 hover:border-accent-400 hover:text-white disabled:opacity-40"
                  >
                    {visionTesting ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
                    Kiểm tra Vision
                  </button>
                  {visionTestMessage && <span className="text-xs text-slate-400">{visionTestMessage}</span>}
                </div>
                <p className="text-xs text-slate-600">Để trống model thì Director vẫn tạo ảnh nhưng bỏ qua OCR, auto retry và xếp hạng.</p>
              </div>

              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Ollama URL</label>
                <input
                  value={cfg.ollama_url || 'http://localhost:11434'}
                  onChange={e => setCfg(p => ({ ...p, ollama_url: e.target.value }))}
                  placeholder="http://localhost:11434"
                  className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                />
                <p className="text-xs text-slate-600">Địa chỉ Ollama server. Để trống nếu chạy local cổng 11434.</p>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">FFmpeg path</label>
                  <input
                    value={cfg.ffmpeg_path || 'ffmpeg'}
                    onChange={e => setCfg(p => ({ ...p, ffmpeg_path: e.target.value }))}
                    className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">FFprobe path</label>
                  <input
                    value={cfg.ffprobe_path || 'ffprobe'}
                    onChange={e => setCfg(p => ({ ...p, ffprobe_path: e.target.value }))}
                    className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                  />
                </div>
              </div>
            </div>
          )}

          {tab === 'Ngôn ngữ' && (
            <div className="space-y-5">
              <div className="rounded-xl border border-surface-600 bg-surface-900/60 p-4 space-y-3">
                <div>
                  <p className="text-sm font-semibold text-white">Language Agent</p>
                  <p className="text-xs text-slate-500">Phạm vi: chỉ chạy khi ngôn ngữ đầu vào khác đầu ra — dịch narration, chữ trên ảnh, title/mô tả/hashtag và QA trước TTS. Bỏ qua khi hai ngôn ngữ trùng nhau.</p>
                </div>

                <div className="rounded-lg border border-sky-400/20 bg-sky-500/5 px-3 py-2 text-xs text-slate-300">
                  {languageAgentCustom
                    ? <>Đang dùng API custom{languageAgentModel ? <> · model: <span className="font-mono text-sky-200">{languageAgentModel}</span></> : ' · chưa chọn model'}</>
                    : languageAgentModel
                      ? <>Đang dùng model riêng: <span className="font-mono text-sky-200">{languageAgentModel}</span></>
                      : <>Đang kế thừa model viết truyện: <span className="font-mono text-sky-200">{configuredStoryModel}</span></>}
                </div>

                <label className="space-y-1 block">
                  <span className="text-xs font-medium text-slate-400">Provider</span>
                  <AppSelect value={cfg.language_agent_provider || 'default'} onChange={e => setCfg(p => ({ ...p, language_agent_provider: e.target.value }))} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white">
                    <option value="default">Dùng model trong app</option>
                    <option value="custom">Custom OpenAI-compatible API</option>
                  </AppSelect>
                </label>

                {!languageAgentCustom ? (
                  <label className="space-y-1 block">
                    <span className="text-xs font-medium text-slate-400">Model cho Language Agent</span>
                    <AppSelect value={languageAgentModel} onChange={e => setCfg(p => ({ ...p, language_agent_model: e.target.value }))} searchable className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white">
                      {languageAgentModel && !listedStoryModelIds.includes(languageAgentModel) && <option value={languageAgentModel}>{languageAgentModel} · cấu hình đang lưu</option>}
                      {storyModelOptions(`Kế thừa model viết truyện — ${configuredStoryModel}`)}
                    </AppSelect>
                    <span className="block text-xs text-slate-500">Chọn “Kế thừa” để dùng đúng model viết truyện hiện tại; không cần nhớ hay gõ ID model.</span>
                  </label>
                ) : (
                  <div className="space-y-3">
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <label className="space-y-1"><span className="text-xs font-medium text-slate-400">Base URL</span><input value={cfg.language_agent_base_url || ''} onChange={e => setCfg(p => ({ ...p, language_agent_base_url: e.target.value }))} placeholder="https://api.example.com" className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white font-mono" /></label>
                      <label className="space-y-1"><span className="text-xs font-medium text-slate-400">Path</span><input value={cfg.language_agent_path || ''} onChange={e => setCfg(p => ({ ...p, language_agent_path: e.target.value }))} placeholder="/v1/chat/completions" className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white font-mono" /></label>
                    </div>
                    <label className="space-y-1 block"><span className="text-xs font-medium text-slate-400">Model của API custom</span><input value={cfg.language_agent_model || ''} onChange={e => setCfg(p => ({ ...p, language_agent_model: e.target.value }))} placeholder="ID model theo nhà cung cấp API" className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white font-mono" /></label>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <label className="space-y-1"><span className="text-xs font-medium text-slate-400">API key</span><input type="password" value={cfg.language_agent_api_key || ''} onChange={e => setCfg(p => ({ ...p, language_agent_api_key: e.target.value }))} placeholder="sk-..." className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white font-mono" /></label>
                      <label className="space-y-1"><span className="text-xs font-medium text-slate-400">Đường dẫn API key</span><input value={cfg.language_agent_api_key_path || ''} onChange={e => setCfg(p => ({ ...p, language_agent_api_key_path: e.target.value }))} placeholder="D:\\Secrets\\language-agent-key.txt" className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white font-mono" /></label>
                    </div>
                  </div>
                )}
              </div>
              <div className="rounded-xl border border-surface-600 bg-surface-900/60 p-4 space-y-3">
                <div><p className="text-sm font-semibold text-white">Voice theo ngôn ngữ</p><p className="text-xs text-slate-500">Mỗi locale chọn provider và voice riêng. Edge TTS tự tải danh sách giọng cho Anh, Hàn và Nhật.</p></div>
                <AppSelect value={voiceProfileLocale} onChange={e => selectVoiceProfileLocale(e.target.value)} searchable className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white">
                  {voiceProfileLocales.map(locale => <option key={locale.id} value={locale.id}>{locale.label}</option>)}
                  <option value="custom">Locale tùy chỉnh…</option>
                </AppSelect>
                {voiceProfileLocale === 'custom' ? <input onBlur={e => e.target.value && setVoiceProfileLocale(e.target.value)} placeholder="Ví dụ: pt-BR" className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white" /> : <>
                  <label className="space-y-1 block"><span className="text-xs font-medium text-slate-400">TTS provider</span><AppSelect value={impliedProviderId} onChange={e => changeTtsProvider(e.target.value)} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white">{ttsProviders.map(provider => <option key={provider.id} value={provider.id}>{provider.label}</option>)}{!ttsProviders.length && <option value="vivibe">Vivibe</option>}</AppSelect></label>
                  {activeTtsProvider?.profile_fields?.map(field => {
                    const value = activeTtsProfile[field.key] ?? field.default ?? ''
                    if (field.key === 'voice_id' && impliedProviderId === 'vivibe') return <div key={field.key} className="space-y-1"><label className="text-xs font-medium text-slate-400">Giọng đọc</label><AppSelect value={voiceChoice(value)} onChange={e => setPresetVoice(e.target.value, voiceId => updateTtsProfile({ voice_id: voiceId }))} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white">{voicePresets.map(preset => <option key={preset.key} value={preset.key}>{preset.label}</option>)}<option value="custom">Voice ID tuỳ chỉnh…</option></AppSelect>{voiceChoice(value) === 'custom' && <input value={value} onChange={e => updateTtsProfile({ voice_id: e.target.value })} placeholder="Nhập Voice ID" className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white font-mono" />}</div>
                    if (field.key === 'voice_id' && activeTtsProvider?.supports_voice_catalog) {
                      const isKnownVoice = providerVoices.some(voice => voice.id === value)
                      const selectValue = isKnownVoice ? value : '__custom_voice__'
                      return <div key={field.key} className="space-y-1"><label className="text-xs font-medium text-slate-400">{field.label}</label><AppSelect value={selectValue} onChange={e => updateTtsProfile({ voice_id: e.target.value === '__custom_voice__' ? '' : e.target.value })} searchable className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white">{!isKnownVoice && value && <option value="__custom_voice__">Giọng hiện tại · {value}</option>}<option value="__custom_voice__">Nhập Voice ID thủ công…</option>{providerVoices.map(voice => <option key={voice.id} value={voice.id}>{voice.label} · {voice.id}</option>)}</AppSelect>{selectValue === '__custom_voice__' && <input value={value} onChange={e => updateTtsProfile({ voice_id: e.target.value })} placeholder={field.placeholder || 'Nhập Voice ID'} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white font-mono" />}</div>
                    }
                    if (field.type === 'select') return <label key={field.key} className="space-y-1 block"><span className="text-xs font-medium text-slate-400">{field.label}</span><AppSelect value={value} onChange={e => updateTtsProfile({ [field.key]: e.target.value })} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white">{(field.options || []).map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</AppSelect></label>
                    const inputType = field.type === 'number' ? 'number' : 'text'
                    return <label key={field.key} className="space-y-1 block"><span className="text-xs font-medium text-slate-400">{field.label}</span><input type={inputType} min={field.min} max={field.max} step={field.step} value={value} onChange={e => updateTtsProfile({ [field.key]: inputType === 'number' ? Number(e.target.value) : e.target.value })} placeholder={field.placeholder} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white font-mono" /></label>
                  })}
                  <div className="flex flex-wrap gap-2 pt-1">{activeTtsProvider?.supports_voice_catalog && <button type="button" onClick={loadProviderVoices} disabled={providerVoiceLoading} className="rounded-lg border border-sky-400/40 px-3 py-2 text-xs font-semibold text-sky-200 hover:border-sky-300 disabled:opacity-50">{providerVoiceLoading ? 'Đang tải giọng…' : 'Tải danh sách giọng'}</button>}<button type="button" onClick={previewTtsVoice} disabled={ttsPreviewing || !activeTtsProfile.voice_id} className="rounded-lg border border-accent-400/50 px-3 py-2 text-xs font-semibold text-accent-200 hover:border-accent-300 disabled:opacity-50">{ttsPreviewing ? 'Đang tạo thử…' : 'Nghe thử giọng'}</button></div>
                  {providerVoiceMessage && <p className="text-xs text-sky-200">{providerVoiceMessage}</p>}
                </>}
              </div>
              <div className="rounded-xl border border-surface-600 bg-surface-900/60 p-4 space-y-3">
                <div><p className="text-sm font-semibold text-white">Kết nối TTS provider</p><p className="text-xs text-slate-500">Key chỉ dùng bởi provider đã chọn; không tự đổi provider khi lỗi.</p></div>
                <AppSelect value={ttsSetupProvider} onChange={e => setTtsSetupProvider(e.target.value)} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white">{ttsProviders.filter(provider => !provider.legacy_credentials).map(provider => <option key={provider.id} value={provider.id}>{provider.label}</option>)}{!ttsProviders.length && <option value="azure_speech">Azure Speech</option>}</AppSelect>
                {ttsSetupProvider === 'edge_tts' && <p className="rounded-lg border border-emerald-400/25 bg-emerald-400/5 px-3 py-2 text-xs text-emerald-200">Không cần API key. Edge TTS dùng giọng neural có sẵn qua package local.</p>}
                {(providerById[ttsSetupProvider]?.credential_fields || []).map(field => <label key={field.key} className="space-y-1 block"><span className="text-xs font-medium text-slate-400">{field.label}</span><input type={field.type === 'secret' ? 'password' : 'text'} value={((cfg.tts_provider_settings || {})[ttsSetupProvider] || {})[field.key] || ''} onChange={e => updateTtsProviderSettings(ttsSetupProvider, field.key, e.target.value)} placeholder={field.placeholder} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white font-mono" /></label>)}
              </div>
            </div>
          )}

          {tab === 'Image API' && (
            <div className="space-y-4">
              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Provider tạo ảnh</label>
                <AppSelect
                  value={cfg.image_generation_provider || 'gpt2api'}
                  onChange={e => handleImageProviderChange(e.target.value)}
                  className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                >
                  <option value="gpt2api">gpt2api trên máy</option>
                  <option value="custom">Custom OpenAI-compatible API</option>
                </AppSelect>
                <p className="text-xs text-slate-600">Phạm vi: tạo ảnh theo cảnh, reference, storyboard và thumbnail YouTube. Không dùng cho LLM lập plan, viết prompt hay metadata.</p>
              </div>

              {(cfg.image_generation_provider || 'gpt2api') === 'gpt2api' && (
                <div className="flex flex-wrap items-center gap-3 rounded-xl border border-sky-400/25 bg-sky-500/5 px-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-semibold text-sky-100">Tự khởi động gpt2api</p>
                    <p className="text-xs text-slate-400">Nếu service chưa chạy, app gọi launcher gpt2api cục bộ khi chọn provider hoặc trước lúc tạo ảnh.</p>
                    {gpt2apiStartMessage && <p className="mt-1 text-xs text-sky-200">{gpt2apiStartMessage}</p>}
                  </div>
                  <button
                    type="button"
                    onClick={handleStartGpt2api}
                    disabled={gpt2apiStarting}
                    className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-sky-400/40 px-3 py-2 text-xs font-semibold text-sky-100 hover:border-sky-300 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {gpt2apiStarting ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
                    {gpt2apiStarting ? 'Đang bật...' : 'Bật ngay'}
                  </button>
                </div>
              )}

              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">gpt2api base URL</label>
                <input
                  value={cfg.scene_gpt2api_base_url || 'http://127.0.0.1:17200/v1'}
                  onChange={e => setCfg(p => ({ ...p, scene_gpt2api_base_url: e.target.value }))}
                  placeholder="http://127.0.0.1:17200/v1"
                  className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                />
                <p className="text-xs text-slate-600">Dùng base URL kiểu ComicForge. Có thể nhập /v1 hoặc endpoint /v1/images/edits, backend sẽ tự chuẩn hóa.</p>
              </div>

              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">API key path</label>
                <input
                  value={cfg.scene_gpt2api_api_key_path || 'D:\\Project\\Deepseek\\gpt2api\\.local\\openai-compatible-api-key.txt'}
                  onChange={e => setCfg(p => ({ ...p, scene_gpt2api_api_key_path: e.target.value }))}
                  className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                />
                <p className="text-xs text-slate-600">Để trống API key trực tiếp thì backend tự đọc key từ file này.</p>
              </div>

              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Asset base URL</label>
                <input
                  value={cfg.scene_gpt2api_asset_base_url || 'http://127.0.0.1:17080'}
                  onChange={e => setCfg(p => ({ ...p, scene_gpt2api_asset_base_url: e.target.value }))}
                  placeholder="http://127.0.0.1:17080"
                  className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                />
                <p className="text-xs text-slate-600">Dùng khi gpt2api trả ảnh bằng relative URL như /assets/...</p>
              </div>

              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">API key trực tiếp (tùy chọn)</label>
                <input
                  type="password"
                  value={cfg.scene_gpt2api_api_key || ''}
                  onChange={e => setCfg(p => ({ ...p, scene_gpt2api_api_key: e.target.value }))}
                  placeholder="Để trống nếu dùng key path"
                  className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Model ảnh</label>
                  <input
                    value={cfg.scene_gpt2api_image_model || 'gpt-image-2'}
                    onChange={e => setCfg(p => ({ ...p, scene_gpt2api_image_model: e.target.value }))}
                    className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Resolution</label>
                  <AppSelect
                    value={cfg.scene_gpt2api_image_resolution || '1K'}
                    onChange={e => setCfg(p => ({ ...p, scene_gpt2api_image_resolution: e.target.value }))}
                    className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                  >
                    <option value="1K">1K</option>
                    <option value="2K">2K</option>
                  </AppSelect>
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Size</label>
                  {sizeSelect('scene_gpt2api_image_size', '1792x1024')}
                </div>
              </div>

              <GptAccountsPanel cfg={cfg} setCfg={setCfg} />

              <div className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-3">
                <div>
                  <p className="text-sm font-semibold text-white">Custom OpenAI-compatible</p>
                  <p className="text-xs text-slate-500">Dùng /v1/images/edits khi cần gửi ảnh reference; backend tự fallback về generations nếu không có reference.</p>
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Base URL</label>
                  <input
                    value={cfg.custom_image_base_url || DEFAULT_CUSTOM_IMAGE_BASE_URL}
                    onChange={e => setCfg(p => ({ ...p, custom_image_base_url: e.target.value }))}
                    placeholder="https://api.example.com"
                    className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                  />
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">API key</label>
                    <input
                      type="password"
                      value={cfg.custom_image_api_key || ''}
                      onChange={e => setCfg(p => ({ ...p, custom_image_api_key: e.target.value }))}
                      placeholder="sk-..."
                      className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Path</label>
                    <input
                      value={cfg.custom_image_path || DEFAULT_CUSTOM_IMAGE_PATH}
                      onChange={e => setCfg(p => ({ ...p, custom_image_path: e.target.value }))}
                      placeholder="/v1/images/edits"
                      className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                    />
                  </div>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Model</label>
                    <input
                      value={cfg.custom_image_model || 'gpt-image-1'}
                      onChange={e => setCfg(p => ({ ...p, custom_image_model: e.target.value }))}
                      className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Size chung</label>
                    {sizeSelect('custom_image_size', '1536x1024')}
                  </div>
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Size thumbnail</label>
                    {sizeSelect('thumbnail_image_size', '2560x1440')}
                  </div>
                </div>
              </div>
            </div>
          )}

          {tab === 'YouTube' && (
            <div className="space-y-4">
              <div className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-3">
                <div>
                  <p className="text-sm font-semibold text-white">YouTube Content AI</p>
                  <p className="text-xs text-slate-500">Phạm vi: metadata xuất bản — title, mô tả, hashtag/tag, publish plan và Shorts campaign. Với Người que, cũng dùng cho bộ SEO packaging sau khi nội dung đã được duyệt.</p>
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Provider</label>
                  <AppSelect
                    value={cfg.youtube_metadata_provider || 'default'}
                    onChange={e => setCfg(p => ({ ...p, youtube_metadata_provider: e.target.value }))}
                    className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                  >
                    <option value="default">Dùng model mặc định / agent local</option>
                    <option value="custom">Custom OpenAI-compatible API</option>
                  </AppSelect>
                </div>
                {(cfg.youtube_metadata_provider || 'default') === 'custom' && (
                  <div className="space-y-3">
                    <div className="space-y-1">
                      <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Base URL</label>
                      <input
                        value={cfg.youtube_metadata_base_url || DEFAULT_SAFE_PROMPT_BASE_URL}
                        onChange={e => setCfg(p => ({ ...p, youtube_metadata_base_url: e.target.value }))}
                        placeholder="https://api.example.com"
                        className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                      />
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Path</label>
                        <input value={cfg.youtube_metadata_path || DEFAULT_SAFE_PROMPT_PATH} onChange={e => setCfg(p => ({ ...p, youtube_metadata_path: e.target.value }))} placeholder="/v1/chat/completions" className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono" />
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Model</label>
                        <input value={cfg.youtube_metadata_model || ''} onChange={e => setCfg(p => ({ ...p, youtube_metadata_model: e.target.value }))} placeholder="gpt-4o-mini / gemini-2.5-flash" className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500" />
                      </div>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">API key</label>
                        <input type="password" value={cfg.youtube_metadata_api_key || ''} onChange={e => setCfg(p => ({ ...p, youtube_metadata_api_key: e.target.value }))} placeholder="sk-..." className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono" />
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">API key path</label>
                        <input value={cfg.youtube_metadata_api_key_path || ''} onChange={e => setCfg(p => ({ ...p, youtube_metadata_api_key_path: e.target.value }))} placeholder="D:\\Secrets\\youtube-content-key.txt" className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono" />
                      </div>
                    </div>
                  </div>
                )}
              </div>

              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">YouTube client_secret.json</label>
                <input
                  value={cfg.youtube_client_secrets_path || ''}
                  onChange={e => setCfg(p => ({ ...p, youtube_client_secrets_path: e.target.value }))}
                  placeholder="D:\\Secrets\\youtube_client_secret.json"
                  className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                />
                <p className="text-xs text-slate-600">Tạo OAuth Client loại Desktop app trong Google Cloud Console rồi trỏ tới file JSON này.</p>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Timezone</label>
                  <input
                    value={cfg.youtube_timezone || 'Asia/Ho_Chi_Minh'}
                    onChange={e => setCfg(p => ({ ...p, youtube_timezone: e.target.value }))}
                    className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Video dài/ngày</label>
                  <input
                    type="number"
                    min="0"
                    value={cfg.youtube_max_long_per_day ?? 1}
                    onChange={e => setCfg(p => ({ ...p, youtube_max_long_per_day: Number(e.target.value) }))}
                    className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Shorts/ngày</label>
                  <input
                    type="number"
                    min="0"
                    value={cfg.youtube_max_shorts_per_day ?? 2}
                    onChange={e => setCfg(p => ({ ...p, youtube_max_shorts_per_day: Number(e.target.value) }))}
                    className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Cách nhau tối thiểu (giờ)</label>
                  <input
                    type="number"
                    min="1"
                    value={cfg.youtube_min_gap_hours ?? 6}
                    onChange={e => setCfg(p => ({ ...p, youtube_min_gap_hours: Number(e.target.value) }))}
                    className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Giờ video dài</label>
                  <input
                    value={Array.isArray(cfg.youtube_long_slots) ? cfg.youtube_long_slots.join(',') : (cfg.youtube_long_slots || '20:00')}
                    onChange={e => setCfg(p => ({ ...p, youtube_long_slots: e.target.value }))}
                    placeholder="20:00"
                    className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Giờ Shorts</label>
                  <input
                    value={Array.isArray(cfg.youtube_short_slots) ? cfg.youtube_short_slots.join(',') : (cfg.youtube_short_slots || '12:00,21:30')}
                    onChange={e => setCfg(p => ({ ...p, youtube_short_slots: e.target.value }))}
                    placeholder="12:00,21:30"
                    className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                  />
                </div>
              </div>

              <div className="rounded-lg border border-surface-500 bg-surface-900/40 p-3 space-y-3">
                <label className="flex items-center gap-2 text-sm text-white cursor-pointer">
                  <input
                    type="checkbox"
                    checked={cfg.youtube_campaign_mode ?? false}
                    onChange={e => setCfg(p => ({ ...p, youtube_campaign_mode: e.target.checked }))}
                    className="accent-accent-500"
                  />
                  Lịch chiến dịch (2 video dài + 4 Shorts/tuần)
                </label>
                <p className="text-xs text-slate-500">Khi bật: T2 teaser · T3 long · T4 climax · T6 teaser · T7 long · CN climax. Tắt thì dùng lịch theo slot mặc định phía trên.</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Giờ long (campaign)</label>
                    <input
                      value={Array.isArray(cfg.youtube_campaign_long_slots) ? cfg.youtube_campaign_long_slots.join(',') : (cfg.youtube_campaign_long_slots || '20:00')}
                      onChange={e => setCfg(p => ({ ...p, youtube_campaign_long_slots: e.target.value }))}
                      placeholder="20:00"
                      className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Giờ Shorts (campaign)</label>
                    <input
                      value={Array.isArray(cfg.youtube_campaign_short_slots) ? cfg.youtube_campaign_short_slots.join(',') : (cfg.youtube_campaign_short_slots || '12:00,20:00')}
                      onChange={e => setCfg(p => ({ ...p, youtube_campaign_short_slots: e.target.value }))}
                      placeholder="12:00,20:00"
                      className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                    />
                  </div>
                </div>
              </div>

              {false && (<>
              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">gpt2api image endpoint</label>
                <input
                  value={cfg.gpt2api_base_url || 'http://127.0.0.1:7860/v1/images/generations'}
                  onChange={e => setCfg(p => ({ ...p, gpt2api_base_url: e.target.value }))}
                  className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 font-mono"
                />
              </div>

              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">gpt2api image model</label>
                <input
                  value={cfg.gpt2api_image_model || ''}
                  onChange={e => setCfg(p => ({ ...p, gpt2api_image_model: e.target.value }))}
                  placeholder="Để trống nếu endpoint không cần model"
                  className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
                />
              </div>
              </>)}
            </div>
          )}

          {tab === 'Channel' && (
            <div className="space-y-4">
              <p className="text-xs text-slate-500">Định vị kênh được ghép vào mọi prompt: viết truyện, tiêu đề, mô tả, thumbnail. Để trống field nào thì bỏ qua field đó.</p>
              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Định vị kênh</label>
                <textarea
                  rows={2}
                  value={cfg.channel_positioning ?? ''}
                  onChange={e => setCfg(p => ({ ...p, channel_positioning: e.target.value }))}
                  placeholder="Truyện kinh dị đời thường Việt Nam — nhà trọ, khách sạn, đường đêm, căn phòng có bí mật."
                  className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-accent-500"
                />
              </div>
              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Khán giả mục tiêu</label>
                <textarea
                  rows={2}
                  value={cfg.channel_audience ?? ''}
                  onChange={e => setCfg(p => ({ ...p, channel_audience: e.target.value }))}
                  placeholder="Người Việt 25–44 tuổi, nghe truyện trên điện thoại và TV, thích bối cảnh trưởng thành quen thuộc."
                  className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-accent-500"
                />
              </div>
              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Trụ cột nội dung (mỗi dòng một trụ cột)</label>
                <textarea
                  rows={3}
                  value={cfg.channel_pillars ?? ''}
                  onChange={e => setCfg(p => ({ ...p, channel_pillars: e.target.value }))}
                  placeholder={"Nhà trọ – chung cư\nKhách sạn – ca trực đêm\nTài xế – đường về quê"}
                  className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-accent-500"
                />
              </div>
              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Cấm trong tiêu đề (phân tách bằng dấu phẩy)</label>
                <input
                  value={cfg.channel_banned_title_patterns ?? ''}
                  onChange={e => setCfg(p => ({ ...p, channel_banned_title_patterns: e.target.value }))}
                  placeholder="||, FULL, [TRUYỆN AUDIO], P1, P2, đoạn"
                  className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-accent-500"
                />
              </div>
              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Cấm hashtag (phân tách bằng dấu phẩy)</label>
                <input
                  value={cfg.channel_banned_hashtags ?? ''}
                  onChange={e => setCfg(p => ({ ...p, channel_banned_hashtags: e.target.value }))}
                  placeholder="#minecraft, #gameplay"
                  className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-accent-500"
                />
              </div>
            </div>
          )}

          {tab === 'TTS' && <>
          {/* JWT Token */}
          <div className="space-y-1">
            <label className="flex items-center gap-1.5 text-xs font-medium text-slate-400 uppercase tracking-wider">
              <Key size={12} />
              JWT Token
            </label>
            <div className="relative">
              <input
                type={showJwt ? 'text' : 'password'}
                value={cfg.jwt_token || ''}
                onChange={e => setCfg(p => ({ ...p, jwt_token: e.target.value }))}
                placeholder="Paste JWT token từ vivibe.app..."
                className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 pr-10 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-accent-500 transition-colors font-mono"
              />
              <button
                onClick={() => setShowJwt(p => !p)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
              >
                {showJwt ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
            <p className="text-xs text-slate-600">F12 → Network → play thử → json-rpc → Authorization: Bearer ...</p>
          </div>

          <div className="space-y-1">
            <label className="flex items-center gap-1.5 text-xs font-medium text-slate-400 uppercase tracking-wider">
              <Mic size={12} /> Giọng đọc tiếng Việt
            </label>
            <AppSelect
              value={voiceChoice(cfg.voice_id)}
              onChange={e => setPresetVoice(e.target.value, voiceId => setCfg(p => ({ ...p, voice_id: voiceId })))}
              className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
            >
              {voicePresets.map(preset => <option key={preset.key} value={preset.key}>{preset.label}</option>)}
              <option value="custom">Voice ID tuỳ chỉnh…</option>
            </AppSelect>
            {voiceChoice(cfg.voice_id) === 'custom' && (
              <input
                value={cfg.voice_id || ''}
                onChange={e => setCfg(p => ({ ...p, voice_id: e.target.value }))}
                placeholder="Nhập Voice ID tuỳ chỉnh"
                className="mt-2 w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-accent-500 font-mono"
              />
            )}
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <button
                type="button"
                onClick={previewVivibeVoice}
                disabled={ttsPreviewing || !cfg.voice_id}
                className="inline-flex items-center gap-1.5 rounded-lg border border-accent-400/50 px-3 py-2 text-xs font-semibold text-accent-200 hover:border-accent-300 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {ttsPreviewing ? <Loader2 size={14} className="animate-spin" /> : <Mic size={14} />}
                {ttsPreviewing ? 'Đang tạo thử…' : 'Nghe thử giọng ViVibe'}
              </button>
              {vivibePreviewMessage && <span className="text-xs text-sky-200">{vivibePreviewMessage}</span>}
            </div>
            <p className="text-xs text-slate-600">Huyền và Chi Mai tự gán đúng Voice ID khi chọn.</p>
          </div>

          <div className="rounded-xl border border-surface-600 bg-surface-900/60 p-3 space-y-3">
            <div>
              <p className="text-sm font-semibold text-white">Danh sách giọng của bạn</p>
              <p className="text-xs text-slate-500">Thêm một lần, sau đó giọng sẽ xuất hiện trong mọi dropdown Voice ID.</p>
            </div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
              <input value={newVoiceName} onChange={e => setNewVoiceName(e.target.value)} placeholder="Tên giọng, ví dụ: Minh Anh" className="w-full rounded-lg border border-surface-500 bg-surface-950 px-3 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-accent-500" />
              <input value={newVoiceId} onChange={e => setNewVoiceId(e.target.value)} placeholder="Voice ID" className="w-full rounded-lg border border-surface-500 bg-surface-950 px-3 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-accent-500 font-mono" />
              <button type="button" onClick={addVoicePreset} disabled={!newVoiceName.trim() || !newVoiceId.trim() || voicePresets.some(preset => preset.id === newVoiceId.trim())} className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-accent-400/45 bg-accent-500/10 px-3 py-2 text-sm font-semibold text-accent-100 hover:bg-accent-500/20 disabled:cursor-not-allowed disabled:opacity-40"><Plus size={15} />Thêm giọng</button>
            </div>
            {customVoicePresets.length > 0 ? <div className="space-y-1.5">
              {customVoicePresets.map(preset => <div key={preset.key} className="flex items-center gap-2 rounded-lg bg-surface-950/70 px-3 py-2">
                <span className="min-w-0 flex-1 truncate text-sm font-medium text-slate-200">{preset.label}</span>
                <span className="hidden max-w-[16rem] truncate font-mono text-xs text-slate-500 sm:block">{preset.id}</span>
                <button type="button" onClick={() => previewVivibeVoice(preset.id, preset.label)} disabled={ttsPreviewing} className="inline-flex items-center gap-1 rounded-md border border-accent-400/40 px-2 py-1.5 text-xs font-semibold text-accent-200 hover:border-accent-300 disabled:cursor-wait disabled:opacity-50" title={`Nghe thử ${preset.label}`}>
                  {ttsPreviewing ? <Loader2 size={13} className="animate-spin" /> : <Mic size={13} />} Nghe thử
                </button>
                <button type="button" onClick={() => removeVoicePreset(preset.id)} className="rounded-md p-1.5 text-slate-500 hover:bg-red-500/10 hover:text-red-300" title={`Xoá ${preset.label}`}><Trash2 size={14} /></button>
              </div>)}
            </div> : <p className="text-xs text-slate-600">Chưa có giọng tự thêm.</p>}
          </div>

          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1">
              <label className="flex items-center gap-1 text-xs font-medium text-slate-400 uppercase tracking-wider">
                <Gauge size={12} /> Tốc độ
              </label>
              <div className="relative">
                <input
                  type="number"
                  min="0.5" max="2.0" step="0.1"
                  value={cfg.speed}
                  onChange={e => setCfg(p => ({ ...p, speed: parseFloat(e.target.value) }))}
                  className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 transition-colors"
                />
              </div>
              <input type="range" min="0.5" max="2.0" step="0.1" value={cfg.speed}
                onChange={e => setCfg(p => ({ ...p, speed: parseFloat(e.target.value) }))}
                className="w-full h-1 mt-1" />
            </div>

            <div className="space-y-1">
              <label className="flex items-center gap-1 text-xs font-medium text-slate-400 uppercase tracking-wider">
                <Users size={12} /> Workers
              </label>
              <input
                type="number" min="1" max="10"
                value={cfg.workers}
                onChange={e => setCfg(p => ({ ...p, workers: parseInt(e.target.value) }))}
                className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 transition-colors"
              />
            </div>

            <div className="space-y-1">
              <label className="flex items-center gap-1 text-xs font-medium text-slate-400 uppercase tracking-wider">
                <Clock size={12} /> Delay (s)
              </label>
              <input
                type="number" min="0" max="10" step="0.5"
                value={cfg.delay}
                onChange={e => setCfg(p => ({ ...p, delay: parseFloat(e.target.value) }))}
                className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 transition-colors"
              />
            </div>
          </div>

          <div className="space-y-1">
            <label className="flex items-center gap-1.5 text-xs font-medium text-slate-400 uppercase tracking-wider">
              <Hash size={12} /> Max ký tự/đoạn
            </label>
            <input
              type="number" min="500" max="5000" step="100"
              value={cfg.max_chars}
              onChange={e => setCfg(p => ({ ...p, max_chars: parseInt(e.target.value) }))}
              className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 transition-colors"
            />
          </div>

          <div className="space-y-1">
            <label className="flex items-center justify-between text-xs font-medium text-slate-400 uppercase tracking-wider">
              <span className="flex items-center gap-1"><Pause size={12} /> Ngắt nghỉ giữa đoạn</span>
              <span className="text-accent-400 font-mono normal-case">{(cfg.sentence_gap ?? 0.3).toFixed(1)}s</span>
            </label>
            <input
              type="range" min="0" max="2.0" step="0.1"
              value={cfg.sentence_gap ?? 0.3}
              onChange={e => setCfg(p => ({ ...p, sentence_gap: parseFloat(e.target.value) }))}
              className="w-full h-1.5 mt-1"
            />
            <div className="flex justify-between text-xs text-slate-600">
              <span>0s (vẫn đệm nối 30ms)</span>
              <span>2s</span>
            </div>
          </div>

          <div className="space-y-3 border border-surface-600 rounded-xl p-3">
            <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Xử lý âm thanh</p>

            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-white">Tối ưu text</p>
                <p className="text-xs text-slate-500">Bỏ xuống dòng thừa → giảm pause TTS engine</p>
              </div>
              <button
                onClick={() => setCfg(p => ({ ...p, optimize_text: !p.optimize_text }))}
                className={`px-3 py-1 rounded-lg text-xs font-semibold transition-colors ${
                  cfg.optimize_text ? 'bg-accent-600 text-white' : 'bg-surface-700 text-slate-400'
                }`}
              >
                {cfg.optimize_text ? 'BẬT' : 'TẮT'}
              </button>
            </div>

            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm text-white">Cắt khoảng lặng thừa</p>
                <p className="text-xs text-slate-500">
                  {cfg.trim_silence
                    ? 'Chỉ cắt đầu/cuối từng đoạn; giữ nguyên khoảng lặng trong câu'
                    : 'Luôn đệm nối 30ms và fade 12ms để tránh tiếng bụp'}
                </p>
              </div>
              <button
                onClick={() => setCfg(p => ({ ...p, trim_silence: !p.trim_silence }))}
                className={`px-3 py-1 rounded-lg text-xs font-semibold transition-colors ${
                  cfg.trim_silence ? 'bg-accent-600 text-white' : 'bg-surface-700 text-slate-400'
                }`}
              >
                {cfg.trim_silence ? 'BẬT' : 'TẮT'}
              </button>
            </div>

            {cfg.trim_silence && (
              <div className="space-y-1">
                <label className="flex items-center justify-between text-xs text-slate-400">
                  <span>Ngưỡng silence</span>
                  <span className="text-accent-400 font-mono">{cfg.silence_db ?? -35} dB</span>
                </label>
                <input
                  type="range" min="-60" max="-20" step="1"
                  value={cfg.silence_db ?? -35}
                  onChange={e => setCfg(p => ({ ...p, silence_db: parseFloat(e.target.value) }))}
                  className="w-full h-1.5"
                />
                <div className="flex justify-between text-xs text-slate-600">
                  <span>-60 dB (giữ âm nhiều hơn)</span>
                  <span>-20 dB (cắt mạnh hơn)</span>
                </div>
              </div>
            )}
          </div>
          </>}

          <button
            onClick={handleSave}
            disabled={saving}
            className={`w-full flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-semibold transition-all ${
              saved
                ? 'bg-success/20 text-success border border-success/30'
                : 'bg-accent-600 hover:bg-accent-500 text-white'
            }`}
          >
            <Save size={14} />
            {saved ? 'Đã lưu!' : saving ? 'Đang lưu...' : 'Lưu cấu hình'}
          </button>
        </div>
      )}
    </div>
  )
}
