import { useEffect, useMemo, useState } from 'react'
import { useRef } from 'react'
import { AlertCircle, CheckCircle2, Cloud, ImagePlus, Images, Loader2, LocateFixed, Pencil, RefreshCw, Server, StopCircle, Trash2, WandSparkles, X } from 'lucide-react'
import {
  applySceneSafePrompts,
  cancelSceneGeneration,
  createDirectorScenePlan,
  createScenePlan,
  createStickFigureVisualPlan,
  approveStickFigureVisualPlan,
  directorReferenceUrl,
  getConfig,
  getScenePlan,
  listGptAccounts,
  regenerateDirectorScenePrompt,
  sceneImageUrl,
  selectSceneImage,
  selectDirectorReference,
  startGenerateDirectorReferences,
  startGenerateScenes,
  uploadStickFigureStyleReference,
  updateScene,
} from '../api.js'
import { useDialog } from './DialogProvider.jsx'
import SceneRevealPreview from './SceneRevealPreview.jsx'

const DEFAULT_SCENE_REVEAL = Object.freeze({
  enabled: false,
  effect: 'brush',
  durationSec: 1.2,
  pattern: 'auto',
  actor: 'none',
})

function normalizeSceneRevealSettings(value = {}) {
  const duration = Number(value.durationSec ?? value.duration_sec ?? DEFAULT_SCENE_REVEAL.durationSec)
  return {
    enabled: Boolean(value.enabled),
    effect: value.effect === 'pencil' ? 'pencil' : 'brush',
    durationSec: Math.min(3, Math.max(0.4, Number.isFinite(duration) ? duration : DEFAULT_SCENE_REVEAL.durationSec)),
    pattern: ['auto', 'left_to_right', 'right_to_left', 'top_to_bottom', 'diagonal', 'scribble'].includes(value.pattern)
      ? value.pattern : 'auto',
    actor: ['none', 'hand', 'pencil', 'brush'].includes(value.actor) ? value.actor : 'none',
  }
}

function resolvedSceneReveal(scene, globalReveal) {
  const override = scene?.reveal_override
  if (!override || override.mode === 'inherit') return globalReveal
  if (override.mode === 'off') return { ...globalReveal, enabled: false }
  return normalizeSceneRevealSettings({ ...globalReveal, ...override, enabled: true })
}

function sceneRevealPayload(config) {
  return {
    enabled: Boolean(config.enabled),
    effect: config.effect,
    duration_sec: Number(config.durationSec),
    pattern: config.pattern,
    actor: config.actor,
  }
}

function fmtSec(s) {
  if (!s || s <= 0) return '0:00'
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = Math.floor(s % 60)
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
  return `${m}:${String(sec).padStart(2, '0')}`
}

function hasSceneImage(scene) {
  return scene?.status === 'succeeded' && !!scene?.image_file
}

function isTerminalSceneGeneration(scene) {
  return ['succeeded', 'failed', 'cancelled'].includes(scene?.status)
}

function clampParallelCount(value) {
  const parsed = Number.parseInt(value, 10)
  if (!Number.isFinite(parsed)) return 1
  return Math.max(1, parsed)
}

const VISUAL_PLAN_INSTRUCTION_PRESETS = [
  {
    label: 'Ít cảnh mạnh',
    value: 'Ưu tiên ít cảnh mạnh. Gộp các ý liên quan thành một hình có lực; chỉ tách khi chủ thể, địa điểm, bằng chứng hoặc cảm xúc đổi rõ rệt.',
  },
  {
    label: 'Nhiều minh họa',
    value: 'Ưu tiên nhiều minh họa khác nhau khi ý độc lập đổi rõ rệt; không lặp biến thể của cùng một cảnh và không tách các mẩu câu liên quan.',
  },
  {
    label: 'Ưu tiên nhân vật',
    value: 'Ưu tiên nhân vật, hành động, biểu cảm và tương tác; chỉ dùng sơ đồ hoặc bản đồ khi đó là cách rõ nhất để giải thích ý.',
  },
  {
    label: 'Ưu tiên sơ đồ/bản đồ',
    value: 'Ưu tiên bản đồ, timeline, sơ đồ và so sánh trực quan cho dữ kiện; dùng nhân vật khi cần tạo đồng cảm hoặc minh họa hành động.',
  },
  {
    label: 'Nhịp bí ẩn chậm',
    value: 'Giữ nhịp bí ẩn chậm: ưu tiên cảnh có không khí, cho phép giữ lâu hơn khi cùng một hình vẫn tăng tò mò; tránh cắt liên tục.',
  },
]

function getGenerationTargets(plan, sceneIds = null, retryFailed = false) {
  const scenes = plan?.scenes || []
  const wanted = new Set(sceneIds || [])
    if (wanted.size) return scenes.filter(scene => wanted.has(scene.id))
    if (retryFailed) {
      return scenes.filter(scene => (
        scene.status === 'failed'
        || scene.status === 'running'
        || scene.status === 'cancelled'
        || (scene.status === 'succeeded' && !scene.image_file)
      ))
  }
  return scenes.filter(scene => !hasSceneImage(scene))
}

function estimateSceneProgress(scene) {
  const explicit = Number(scene?.progress || 0)
  if (explicit > 0) return Math.max(1, Math.min(100, Math.round(explicit)))
  if (scene?.status !== 'running') return 0
  const startedAt = Date.parse(scene.started_at || scene.last_progress_at || '')
  if (!Number.isFinite(startedAt)) return 1
  const elapsedSec = Math.max(0, (Date.now() - startedAt) / 1000)
  return Math.max(1, Math.min(95, Math.round((elapsedSec / 240) * 95)))
}

function sceneImageHistory(scene) {
  const items = Array.isArray(scene?.image_history) ? scene.image_history : []
  const byFile = new Map()
  for (const item of items) {
    if (item?.image_file) byFile.set(item.image_file, item)
  }
  if (scene?.image_file && !byFile.has(scene.image_file)) {
    byFile.set(scene.image_file, {
      id: scene.image_file.replace(/\.[^.]+$/, ''),
      image_file: scene.image_file,
      selected: true,
      source: 'current',
    })
  }
  return Array.from(byFile.values()).map(item => ({
    ...item,
    selected: item.image_file === scene?.image_file || (!scene?.image_file && item.selected),
  }))
}

function joinUrl(base = '', path = '') {
  if (!base) return path || ''
  if (!path) return base
  return `${base.replace(/\/+$/, '')}/${path.replace(/^\/+/, '')}`
}

function shortAccountName(account) {
  if (!account) return ''
  const raw = account.name || account.remark || `#${account.id}`
  if (!raw.includes('@')) return raw
  const [name, domain] = raw.split('@')
  return `${name.slice(0, 3)}...@${domain}`
}

function classifyGptAccount(account, pool = {}) {
  if (!account) {
    if ((pool.codexReadyCount || 0) > 0 && (pool.webReadyCount || 0) <= 0) return 'Codex OAuth'
    if ((pool.webReadyCount || 0) > 0 && (pool.codexReadyCount || 0) <= 0) return 'ChatGPT web'
    if ((pool.webReadyCount || 0) > 0 && (pool.codexReadyCount || 0) > 0) return 'Codex + Web'
    return 'chưa rõ account'
  }
  const marker = `${account.name || ''} ${account.remark || ''} ${account.auth_type || ''}`.toLowerCase()
  if (marker.includes('codex')) return 'Codex OAuth'
  if (marker.includes('web') || (account.has_access_token && !account.has_refresh_token)) return 'ChatGPT web'
  if (account.auth_type) return account.auth_type
  return 'gpt2api'
}

function buildImageProviderInfo(config = {}, accountData = null, accountError = '') {
  const provider = String(config.image_generation_provider || 'gpt2api').toLowerCase()
  if (provider === 'custom') {
    const endpoint = joinUrl(config.custom_image_base_url || '', config.custom_image_path || '/v1/images/edits')
    return {
      kind: 'custom',
      title: 'API custom',
      subtitle: endpoint || 'Chưa cấu hình endpoint',
      model: config.custom_image_model || 'chưa chọn model',
      size: config.custom_image_size || 'chưa chọn size',
      pool: '',
      warning: '',
    }
  }

  const accounts = Array.isArray(accountData?.accounts) ? accountData.accounts : []
  const pool = accountData?.pool || {}
  const active = accounts.find(item => String(item.id) === String(accountData?.activeAccountId))
  const accountMode = classifyGptAccount(active, pool)
  const accountName = active ? `Active #${active.id} ${shortAccountName(active)}` : 'Chưa chọn account active'
  const gptBase = config.scene_gpt2api_base_url || config.gpt2api_base_url || ''
  return {
    kind: 'gpt2api',
    title: `gpt2api · ${accountMode}`,
    subtitle: `${accountName}${gptBase ? ` · ${gptBase}` : ''}`,
    model: config.scene_gpt2api_image_model || config.gpt2api_image_model || 'gpt-image-2',
    size: [config.scene_gpt2api_image_resolution, config.scene_gpt2api_image_size].filter(Boolean).join(' · ') || 'chưa chọn size',
    pool: `Pool: ${pool.usableCount ?? 0}/${pool.enabledCount ?? 0} usable · Codex ${pool.codexReadyCount ?? 0} · Web ${pool.webReadyCount ?? 0}`,
    warning: accountError,
  }
}

function safeRuleLevel(scene) {
  return Number(scene?.safe_rule_level || scene?.safe_level || 0)
}

function safeLlmLevel(scene) {
  return Number(scene?.safe_llm_level || 0)
}

function hasSafeRule(scene) {
  return safeRuleLevel(scene) > 0 && !!(scene?.safe_rule_prompt || scene?.safe_prompt)
}

function hasSafeLlm(scene) {
  return safeLlmLevel(scene) > 0 && !!scene?.safe_llm_prompt
}

function activePromptMode(scene) {
  const mode = String(scene?.active_prompt_mode || scene?.prompt_mode || '').toLowerCase()
  if ((mode === 'safe_llm' || mode === 'llm') && hasSafeLlm(scene)) return 'safe_llm'
  if ((mode === 'safe_rule' || mode === 'safe' || mode === 'rule') && hasSafeRule(scene)) return 'safe_rule'
  if ((mode === 'director' || mode === 'storyboard') && scene?.director_prompt) return 'director'
  if (hasSafeRule(scene)) return 'safe_rule'
  if (hasSafeLlm(scene)) return 'safe_llm'
  if (scene?.director_prompt) return 'director'
  return 'original'
}

function referenceHistory(ref) {
  const items = Array.isArray(ref?.image_history) ? ref.image_history : []
  const byFile = new Map()
  for (const item of items) {
    if (item?.image_file) byFile.set(item.image_file, item)
  }
  if (ref?.image_file && !byFile.has(ref.image_file)) {
    byFile.set(ref.image_file, {
      id: ref.image_file.replace(/\.[^.]+$/, ''),
      image_file: ref.image_file,
      selected: true,
    })
  }
  return Array.from(byFile.values()).map(item => ({
    ...item,
    selected: item.image_file === ref?.image_file || (!ref?.image_file && item.selected),
  }))
}

function mergeProgressFromPlan(prev, data, activeIds) {
  if (!activeIds?.length) return prev
  const active = (data?.scenes || []).filter(scene => activeIds.includes(scene.id))
  if (!active.length) return prev
  const done = active.filter(scene => hasSceneImage(scene) || scene.status === 'failed').length
  const running = active.filter(scene => scene.status === 'running')
  const runningProgress = running.reduce((sum, scene) => sum + (estimateSceneProgress(scene) / 100), 0)
  if (!running.length) {
    return {
      ...prev,
      done: Math.max(prev.done || 0, done),
      total: prev.total || active.length,
      runningProgress: done >= active.length ? 0 : prev.runningProgress || 0,
    }
  }
  const firstRunning = running[0]
  const firstProgress = estimateSceneProgress(firstRunning)
  return {
    ...prev,
    done,
    total: prev.total || active.length,
    runningProgress,
    message: running.length > 1
      ? `${running.length} scene dang tao`
      : `${firstRunning.id} ${firstProgress}% - ${firstRunning.task_status || 'running'}`,
  }
}

export default function SceneImageVideoTool({
  project,
  audioFile,
  storyText,
  storyTitle,
  structuredStoryboard = null,
  outputLocale = 'vi-VN',
  settings,
  onSettingsChange,
  onCompose,
}) {
  const { confirm: askConfirm } = useDialog()
  const [plan, setPlan] = useState(null)
  const [style, setStyle] = useState('Cinematic Vietnamese horror, realistic digital painting, dramatic light, moody shadows')
  const [targetSec, setTargetSec] = useState(12)
  const [planMode, setPlanMode] = useState(structuredStoryboard?.mode === 'stick_figure' ? 'stick_figure' : 'fast')
  const [generationMode, setGenerationMode] = useState(() => localStorage.getItem('lucylab_scene_generation_mode') || 'parallel')
  const [referenceGenerationMode, setReferenceGenerationMode] = useState(() => localStorage.getItem('lucylab_reference_generation_mode') || 'parallel')
  const [parallelCount, setParallelCount] = useState(() => clampParallelCount(localStorage.getItem('lucylab_scene_parallel_count') || 3))
  const [safeMode, setSafeMode] = useState('rule')
  const [loading, setLoading] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [referenceGenerating, setReferenceGenerating] = useState(false)
  const [safeApplying, setSafeApplying] = useState(false)
  const [safeApplyingSceneId, setSafeApplyingSceneId] = useState('')
  const [progress, setProgress] = useState({ done: 0, total: 0, message: '' })
  const [referenceProgress, setReferenceProgress] = useState({ done: 0, total: 0, message: '' })
  const [activeSceneIds, setActiveSceneIds] = useState([])
  const [sceneGenerationKinds, setSceneGenerationKinds] = useState({})
  const [error, setError] = useState('')
  const [editing, setEditing] = useState(null)
  const [draftPrompt, setDraftPrompt] = useState('')
  const [reviewing, setReviewing] = useState(null)
  const [selectingImage, setSelectingImage] = useState('')
  const [selectingReference, setSelectingReference] = useState('')
  const [imageProviderInfo, setImageProviderInfo] = useState(null)
  const [imageProviderLoading, setImageProviderLoading] = useState(false)
  const [imageProviderError, setImageProviderError] = useState('')
  const [styleReferenceFile, setStyleReferenceFile] = useState(null)
  const [styleReferenceId, setStyleReferenceId] = useState('')
  const [createAfterStyleSheetPick, setCreateAfterStyleSheetPick] = useState(false)
  const [showAllReferences, setShowAllReferences] = useState(false)
  const [autoPrepareReferences, setAutoPrepareReferences] = useState(false)
  const [autoGenerateScenesAfterReferences, setAutoGenerateScenesAfterReferences] = useState(() => Boolean(settings?.autoGenerateScenesAfterReferences))
  const [directorUseVisualPlan, setDirectorUseVisualPlan] = useState(() => settings?.directorUseVisualPlan !== false)
  const [visualPlanInstruction, setVisualPlanInstruction] = useState(() => String(settings?.visualPlanInstruction || ''))
  const [previewSceneId, setPreviewSceneId] = useState('')
  const [revealUpdatingSceneId, setRevealUpdatingSceneId] = useState('')
  const activeSceneKey = activeSceneIds.join('|')
  const sceneRefs = useRef({})
  const styleReferenceInputRef = useRef(null)
  const referenceRunFinalizedRef = useRef(false)

  async function loadImageProviderInfo(silent = false) {
    if (!silent) setImageProviderLoading(true)
    setImageProviderError('')
    try {
      const config = await getConfig()
      const provider = String(config.image_generation_provider || 'gpt2api').toLowerCase()
      let accountData = null
      let accountError = ''
      if (provider !== 'custom') {
        try {
          accountData = await listGptAccounts()
        } catch (e) {
          accountError = e?.message || 'Không đọc được account gpt2api'
        }
      }
      setImageProviderInfo(buildImageProviderInfo(config, accountData, accountError))
    } catch (e) {
      setImageProviderError(e?.message || 'Không đọc được cấu hình tạo ảnh')
    } finally {
      setImageProviderLoading(false)
    }
  }

  useEffect(() => {
    loadImageProviderInfo(true)
  }, [])

  useEffect(() => {
    if (!project) return
    let cancelled = false
    getScenePlan(project)
      .then(data => {
        if (!cancelled) {
          setPlan(data)
          if (data.visual_style) setStyle(data.visual_style)
          setPlanMode(['director', 'stick_figure'].includes(data.mode) ? data.mode : 'fast')
        }
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [project])

  useEffect(() => {
    if (structuredStoryboard?.mode !== 'stick_figure' && !structuredStoryboard?.tts_text?.trim()) return
    setPlanMode('stick_figure')
    setStyle(structuredStoryboard.visual_style || '2D hand-drawn stick-figure historical explainer, clean black ink outlines, warm off-white paper, muted earth brown and icy blue palette, flat editorial infographic composition')
  }, [structuredStoryboard])

  useEffect(() => {
    localStorage.setItem('lucylab_scene_generation_mode', generationMode)
    localStorage.setItem('lucylab_reference_generation_mode', referenceGenerationMode)
    localStorage.setItem('lucylab_scene_parallel_count', String(clampParallelCount(parallelCount)))
  }, [generationMode, referenceGenerationMode, parallelCount])

  useEffect(() => {
    if (plan?.mode === 'stick_figure' && plan.references?.some(ref => ref.id === 'style_source' && ref.image_file)) {
      setStyleReferenceId('style_source')
    }
  }, [plan])

  useEffect(() => {
    const savedInstruction = String(settings?.visualPlanInstruction || '')
    setVisualPlanInstruction(current => current === savedInstruction ? current : savedInstruction)
  }, [settings?.visualPlanInstruction])

  useEffect(() => {
    const savedValue = Boolean(settings?.autoGenerateScenesAfterReferences)
    setAutoGenerateScenesAfterReferences(current => current === savedValue ? current : savedValue)
  }, [settings?.autoGenerateScenesAfterReferences])

  useEffect(() => {
    const savedValue = settings?.directorUseVisualPlan !== false
    setDirectorUseVisualPlan(current => current === savedValue ? current : savedValue)
  }, [settings?.directorUseVisualPlan])

  const planHasRunningScene = (plan?.scenes || []).some(scene => scene.status === 'running')

  useEffect(() => {
    if (!project || (!generating && !referenceGenerating && !planHasRunningScene)) return
    let cancelled = false
    const refreshPlan = () => {
      getScenePlan(project)
        .then(data => {
          if (cancelled) return
          setPlan(data)
          if (generating) {
            setProgress(prev => mergeProgressFromPlan(prev, data, activeSceneIds))
            const activeScenes = (data.scenes || []).filter(scene => activeSceneIds.includes(scene.id))
            // An SSE connection can disappear after the backend has persisted
            // every terminal result. Do not leave the UI locked in "generating"
            // or point "Hiện tại" at a failed scene in that case.
            if (activeScenes.length === activeSceneIds.length
              && activeScenes.length > 0
              && activeScenes.every(isTerminalSceneGeneration)) {
              setGenerating(false)
              setActiveSceneIds([])
            }
          }
        })
        .catch(() => {})
    }
    refreshPlan()
    const timer = window.setInterval(refreshPlan, 2500)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [project, generating, referenceGenerating, planHasRunningScene, activeSceneKey])

  const stats = useMemo(() => {
    const scenes = plan?.scenes || []
    const references = Array.isArray(plan?.references) ? plan.references : []
    return {
      total: scenes.length,
      done: scenes.filter(hasSceneImage).length,
      failed: scenes.filter(s => s.status === 'failed').length,
      safeRuleFailed: scenes.filter(s => s.status === 'failed' && hasSafeRule(s)).length,
      safeLlmFailed: scenes.filter(s => s.status === 'failed' && hasSafeLlm(s)).length,
      running: scenes.filter(s => s.status === 'running').length,
      references: references.length,
      referencesDone: references.filter(ref => ref.image_file).length,
      referencesFailed: references.filter(ref => ref.status === 'failed').length,
    }
  }, [plan])

  const visibleReferences = useMemo(() => {
    const references = Array.isArray(plan?.references) ? plan.references : []
    return showAllReferences ? references : references.slice(0, 24)
  }, [plan, showAllReferences])

  const currentScene = useMemo(() => {
    const scenes = plan?.scenes || []
    const active = new Set(activeSceneIds)
    return scenes.find(scene => scene.status === 'running')
      || (generating ? scenes.find(scene => active.has(scene.id) && !isTerminalSceneGeneration(scene)) : null)
      || null
  }, [plan, activeSceneKey, generating])

  const stickFigureAvailable = Boolean(structuredStoryboard?.tts_text?.trim() || plan?.mode === 'stick_figure')
  const hasReferenceWorkflow = ['director', 'stick_figure'].includes(plan?.mode)
  const hasStyleCheatSheet = Boolean(
    styleReferenceFile
    || styleReferenceId
    || plan?.references?.some(ref => ref.id === 'style_source' && (ref.image_file || ref.status === 'succeeded')),
  )

  function updateVisualPlanInstruction(value) {
    const nextInstruction = String(value || '').slice(0, 2000)
    setVisualPlanInstruction(nextInstruction)
    onSettingsChange?.({ ...settings, visualPlanInstruction: nextInstruction })
  }

  function updateAutoGenerateScenesAfterReferences(value) {
    const nextValue = Boolean(value)
    setAutoGenerateScenesAfterReferences(nextValue)
    onSettingsChange?.({ ...settings, autoGenerateScenesAfterReferences: nextValue })
  }

  function updateDirectorUseVisualPlan(value) {
    const nextValue = Boolean(value)
    setDirectorUseVisualPlan(nextValue)
    onSettingsChange?.({ ...settings, directorUseVisualPlan: nextValue })
  }

  const globalSceneReveal = useMemo(
    () => normalizeSceneRevealSettings(settings?.sceneReveal || DEFAULT_SCENE_REVEAL),
    [settings?.sceneReveal],
  )
  const previewScenes = useMemo(
    () => (plan?.scenes || []).filter(scene => Boolean(scene.image_file)),
    [plan],
  )
  const previewScene = useMemo(
    () => previewScenes.find(scene => scene.id === previewSceneId) || previewScenes[0] || null,
    [previewSceneId, previewScenes],
  )

  useEffect(() => {
    if (!previewScenes.length) {
      if (previewSceneId) setPreviewSceneId('')
      return
    }
    if (!previewScenes.some(scene => scene.id === previewSceneId)) setPreviewSceneId(previewScenes[0].id)
  }, [previewSceneId, previewScenes])

  function updateGlobalSceneReveal(patch) {
    const next = normalizeSceneRevealSettings({ ...globalSceneReveal, ...patch })
    onSettingsChange?.({ ...settings, sceneReveal: next })
  }

  async function saveSceneRevealOverride(scene, override) {
    if (!project || !scene?.id) return
    setRevealUpdatingSceneId(scene.id)
    setError('')
    try {
      const data = await updateScene(project, scene.id, { reveal_override: override })
      setPlan(data)
    } catch (e) {
      setError(e?.message || 'Không thể lưu hiệu ứng riêng cho scene')
    } finally {
      setRevealUpdatingSceneId('')
    }
  }

  function setSceneRevealMode(scene, mode) {
    if (mode === 'inherit') {
      saveSceneRevealOverride(scene, { mode: 'inherit' })
      return
    }
    if (mode === 'off') {
      saveSceneRevealOverride(scene, { mode: 'off' })
      return
    }
    saveSceneRevealOverride(scene, { mode: 'custom', ...sceneRevealPayload(resolvedSceneReveal(scene, globalSceneReveal)) })
  }

  function updateSceneRevealOverride(scene, patch) {
    const next = normalizeSceneRevealSettings({ ...resolvedSceneReveal(scene, globalSceneReveal), ...patch, enabled: true })
    saveSceneRevealOverride(scene, { mode: 'custom', ...sceneRevealPayload(next) })
  }

  async function handleCreatePlan(styleFileOverride = null) {
    setError('')
    setLoading(true)
    try {
      const payload = {
        project,
        audio_file: audioFile,
        story_text: storyText || '',
        story_title: storyTitle || project,
        visual_style: style,
        target_scene_sec: Number(targetSec) || 12,
        min_scene_sec: 7,
        max_scene_sec: Math.max(12, Number(targetSec) + 6),
        max_scenes: 140,
        visual_plan_instruction: visualPlanInstruction,
      }
      let data
      if (planMode === 'stick_figure') {
        if (!structuredStoryboard?.tts_text?.trim()) {
          throw new Error('Không tìm thấy blueprint người que. Hãy quay lại bước Content để AI lọc kịch bản.')
        }
        let nextStyleReferenceId = styleReferenceId
        const styleFileToUpload = styleFileOverride || styleReferenceFile
        if (styleFileToUpload) {
          const uploaded = await uploadStickFigureStyleReference(project, styleFileToUpload)
          nextStyleReferenceId = uploaded.reference_id || 'style_source'
          setStyleReferenceId(nextStyleReferenceId)
          setStyleReferenceFile(null)
        }
        data = await createStickFigureVisualPlan(project, {
          ...payload,
          // Keep the blueprint narration/references, but let the editable
          // "Phong cách ảnh" field be the sole source of visual style.
          content_blueprint: { ...structuredStoryboard, visual_style: style },
          style_reference_id: nextStyleReferenceId,
          auto_prepare_references: autoPrepareReferences,
          output_locale: outputLocale,
          visual_plan_instruction: visualPlanInstruction,
        })
      } else if (planMode === 'director') {
        data = await createDirectorScenePlan(project, {
          ...payload,
          use_visual_plan: directorUseVisualPlan,
        })
      } else {
        data = await createScenePlan(payload)
      }
      setPlan(data)
      const planMessage = planMode === 'stick_figure'
        ? 'Đã tạo storyboard người que với thời lượng biến thiên'
        : planMode === 'director'
          ? (directorUseVisualPlan ? 'Đã tạo Visual Plan Director theo narration' : 'Đã tạo Director plan theo thời lượng cố định')
          : 'Đã tạo plan scene'
      setProgress({ done: 0, total: data.scenes?.length || 0, message: planMessage })
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function requestCreatePlan() {
    if (planMode !== 'stick_figure' || hasStyleCheatSheet) {
      await handleCreatePlan()
      return
    }
    const createWithoutSheet = await askConfirm({
      tone: 'info',
      title: 'Thêm style cheat sheet tham khảo?',
      message: 'Một cheat sheet giúp AI giữ nhân vật, nét vẽ, palette và bố cục đồng nhất giữa các ảnh. Bạn vẫn có thể tạo Visual Plan mà không cần ảnh mẫu.',
      confirmText: 'Tạo không có cheat sheet',
      cancelText: 'Thêm cheat sheet',
    })
    if (createWithoutSheet) {
      await handleCreatePlan()
      return
    }
    setCreateAfterStyleSheetPick(true)
    window.setTimeout(() => styleReferenceInputRef.current?.click(), 0)
  }

  function handleStyleReferencePick(file) {
    setStyleReferenceFile(file || null)
    setStyleReferenceId('')
    if (!file || !createAfterStyleSheetPick) return
    setCreateAfterStyleSheetPick(false)
    void handleCreatePlan(file)
  }

  function handleClearImageStyle() {
    setStyle('')
  }

  async function handleApproveVisualPlan() {
    if (!plan || plan.approval_status === 'approved') return
    setLoading(true)
    setError('')
    try {
      const approved = await approveStickFigureVisualPlan(project, autoPrepareReferences)
      setPlan(approved)
      setProgress(prev => ({ ...prev, message: 'Visual Plan đã được duyệt' }))
      if (autoPrepareReferences) handleGenerateReferences(null, false, approved)
    } catch (e) {
      setError(e.message || 'Không thể duyệt Visual Plan')
    } finally {
      setLoading(false)
    }
  }

  function handleGenerate(sceneIds = null, retryFailed = false) {
    if (!plan || generating) return
    const visualPlanNeedsApproval = plan.mode === 'stick_figure'
      && Number(plan.schema_version || 0) === 2
      && plan.approval_status !== 'approved'
    if (visualPlanNeedsApproval) {
      setError('Hãy duyệt Visual Plan trước khi tạo hoặc retry ảnh.')
      setProgress(prev => ({ ...prev, message: 'Visual Plan chưa được duyệt' }))
      return
    }
    const targets = getGenerationTargets(plan, sceneIds, retryFailed)
    // A batch retry is recovery work: send each failed scene as its own
    // request concurrently. Normal generation still respects the selected
    // sequential/parallel mode.
    const batchRetry = retryFailed && !sceneIds?.length
    const generationConcurrency = (generationMode === 'parallel' || batchRetry) ? clampParallelCount(parallelCount) : 1
    setError('')
    if (!targets.length) {
      setActiveSceneIds([])
      setProgress({ done: 0, total: 0, sceneProgress: 0, message: 'Khong co scene nao can tao' })
      return
    }
    setActiveSceneIds(targets.map(scene => scene.id))
    const generationKind = retryFailed ? 'retry' : 'initial'
    setSceneGenerationKinds(current => targets.reduce(
      (next, scene) => ({ ...next, [scene.id]: generationKind }),
      current,
    ))
    // The provider may not emit its first SSE event immediately. Mark queued
    // work locally so a Retry visibly starts the render state at once.
    const queuedAt = new Date().toISOString()
    setPlan(prev => targets.reduce(
      (next, scene) => markScene(next, scene.id, {
        status: 'running', error: '', progress: 0, task_status: 'queued', started_at: queuedAt,
      }),
      prev,
    ))
    setGenerating(true)
    setProgress({ done: 0, total: targets.length, runningProgress: 0, sceneProgress: 0, message: `Dang gui job tao anh (${generationConcurrency} luong)...` })
    startGenerateScenes(
      { project, scene_ids: sceneIds, retry_failed: retryFailed, concurrency: generationConcurrency },
      (ev) => {
        if (ev.type === 'start') {
          if (Array.isArray(ev.scene_ids)) setActiveSceneIds(ev.scene_ids)
          setProgress({ done: 0, total: ev.total || 0, runningProgress: 0, sceneProgress: 0, message: `Model anh: ${ev.model || 'gpt-image-2'} · ${ev.concurrency || generationConcurrency} luong` })
        } else if (ev.type === 'scene_start') {
          setProgress({ done: ev.index - 1, total: ev.total, sceneProgress: 0, message: `Dang tao ${ev.scene_id}` })
          setPlan(prev => markScene(prev, ev.scene_id, { status: 'running', error: '' }))
        } else if (ev.type === 'scene_task') {
          setProgress(prev => ({ ...prev, message: `${ev.scene_id} task ${ev.task_id}` }))
          setPlan(prev => markScene(prev, ev.scene_id, { status: 'running', task_id: ev.task_id || '' }))
        } else if (ev.type === 'scene_attempt') {
          setProgress(prev => ({ ...prev, message: `${ev.scene_id} attempt ${ev.attempt}/${ev.total_attempts || 3}` }))
          setPlan(prev => markScene(prev, ev.scene_id, {
            status: 'running',
            task_status: 'retrying',
            attempt: ev.attempt,
          }))
        } else if (ev.type === 'scene_retry') {
          setProgress(prev => ({ ...prev, message: `${ev.scene_id} retry sau ${ev.retry_after_sec}s` }))
          setPlan(prev => markScene(prev, ev.scene_id, {
            status: 'running',
            task_status: 'retry_wait',
            last_generation_error: ev.message || '',
            retry_after_sec: ev.retry_after_sec,
          }))
        } else if (ev.type === 'scene_progress') {
          const sceneProgress = Math.max(0, Math.min(99, Number(ev.progress || 0)))
          setProgress(prev => ({
            done: Math.max(0, Number(ev.completed ?? prev.done ?? 0)),
            total: ev.total || prev.total || 0,
            sceneProgress,
            runningProgress: prev.runningProgress,
            message: `${ev.scene_id} ${sceneProgress}%${ev.status ? ` - ${ev.status}` : ''}`,
          }))
          setPlan(prev => markScene(prev, ev.scene_id, {
            status: 'running',
            progress: sceneProgress,
            task_status: ev.status || 'running',
          }))
        } else if (ev.type === 'scene_saved') {
          setProgress(prev => ({ ...prev, sceneProgress: 100, message: `${ev.scene_id} da luu anh` }))
          setPlan(prev => markScene(prev, ev.scene_id, { progress: 100 }))
        } else if (ev.type === 'scene_done') {
          setProgress({
            done: ev.completed,
            total: ev.total,
            sceneProgress: 0,
            message: ev.selected === false ? `${ev.scene_id} da luu vao lich su` : `${ev.scene_id} hoan thanh`,
          })
          // A parallel batch can still have other scenes in flight. Remove
          // this completed scene immediately so its finished image is not
          // covered by the batch-wide "rendering" overlay.
          setActiveSceneIds(ids => ids.filter(id => id !== ev.scene_id))
          setSceneGenerationKinds(current => {
            const next = { ...current }
            delete next[ev.scene_id]
            return next
          })
          setPlan(prev => {
            const previousScene = prev?.scenes?.find(item => item.id === ev.scene_id)
            const candidateFile = ev.candidate_file || ev.image_file || `${ev.scene_id}.png`
            // Retrying a scene that already has a selected image deliberately
            // creates a *variant*. Keep the selected image on the card and
            // surface the new candidate through its saved history instead of
            // briefly swapping the preview and making the result look lost.
            const selectedImageFile = ev.selected === false
              ? (previousScene?.image_file || candidateFile)
              : (ev.image_file || candidateFile)
            return markScene(prev, ev.scene_id, {
              status: 'succeeded',
              task_status: 'succeeded',
              image_file: selectedImageFile,
              last_candidate_image_file: candidateFile,
              error: '',
            })
          })
          getScenePlan(project).then(setPlan).catch(() => {})
        } else if (ev.type === 'scene_error') {
          setProgress({ done: ev.completed, total: ev.total, sceneProgress: 0, message: `${ev.scene_id} loi` })
          setError(ev.message || 'Tao anh scene loi')
          setActiveSceneIds(ids => ids.filter(id => id !== ev.scene_id))
          setSceneGenerationKinds(current => {
            const next = { ...current }
            delete next[ev.scene_id]
            return next
          })
          setPlan(prev => {
            const scene = prev?.scenes?.find(item => item.id === ev.scene_id)
            return markScene(prev, ev.scene_id, {
              status: scene?.image_file ? 'succeeded' : 'failed',
              error: ev.message || 'Tao anh scene loi',
            })
          })
          getScenePlan(project).then(setPlan).catch(() => {})
        } else if (ev.type === 'cancelled') {
          setProgress(prev => ({ ...prev, message: ev.message || 'Da dung' }))
        } else if (ev.type === 'done') {
          if (ev.plan) setPlan(ev.plan)
        } else if (ev.type === 'error') {
          setError(ev.message || 'Tao anh scene loi')
        }
      },
      () => {
        setGenerating(false)
        setActiveSceneIds([])
        setSceneGenerationKinds({})
        getScenePlan(project).then(setPlan).catch(() => {})
      },
      (e) => {
        setGenerating(false)
        setActiveSceneIds([])
        setSceneGenerationKinds({})
        setError(e?.message || String(e))
      },
    )
  }

  function handleGenerateReferences(referenceIds = null, retryFailed = false, sourcePlan = plan) {
    if (!sourcePlan || referenceGenerating) return
    const references = sourcePlan.references || []
    const wanted = new Set(referenceIds || [])
    const targets = wanted.size
      ? references.filter(ref => wanted.has(ref.id))
      : references.filter(ref => retryFailed ? (ref.status === 'failed' || !ref.image_file) : !ref.image_file)
    setError('')
    if (!targets.length) {
      setReferenceProgress({ done: 0, total: 0, message: 'Không có reference cần tạo' })
      return
    }
    referenceRunFinalizedRef.current = false
    setReferenceGenerating(true)
    setReferenceProgress({ done: 0, total: targets.length, message: 'Đang tạo reference...' })
    startGenerateDirectorReferences(
      project,
      {
        reference_ids: referenceIds,
        retry_failed: retryFailed,
        mode: referenceGenerationMode,
        concurrency: referenceGenerationMode === 'parallel' ? clampParallelCount(parallelCount) : 1,
      },
      (ev) => {
        if (ev.type === 'start') {
          setReferenceProgress({ done: 0, total: ev.total || 0, message: `Reference model: ${ev.model || 'image model'} · ${ev.concurrency || 1} luồng` })
        } else if (ev.type === 'reference_start') {
          setReferenceProgress({ done: ev.index - 1, total: ev.total, message: `Đang tạo ${ev.reference_id}` })
          setPlan(prev => markReference(prev, ev.reference_id, { status: 'running', error: '' }))
        } else if (ev.type === 'reference_progress') {
          setReferenceProgress(prev => ({ ...prev, message: `${ev.reference_id} ${ev.progress || 0}%${ev.status ? ` - ${ev.status}` : ''}` }))
          setPlan(prev => markReference(prev, ev.reference_id, { status: 'running', progress: ev.progress || 0, task_status: ev.status || 'running' }))
        } else if (ev.type === 'reference_done') {
          setReferenceProgress({ done: ev.completed || ev.index, total: ev.total, message: `${ev.reference_id} hoàn thành` })
          setPlan(prev => markReference(prev, ev.reference_id, { status: 'succeeded', image_file: ev.image_file || ev.candidate_file, error: '' }))
          getScenePlan(project).then(setPlan).catch(() => {})
        } else if (ev.type === 'reference_error') {
          setReferenceProgress({ done: ev.completed || ev.index, total: ev.total, message: `${ev.reference_id} lỗi` })
          setPlan(prev => markReference(prev, ev.reference_id, { status: 'failed', error: ev.message || 'Tạo reference lỗi' }))
        } else if (ev.type === 'done') {
          if (ev.plan) setPlan(ev.plan)
        } else if (ev.type === 'error') {
          setError(ev.message || 'Tạo reference lỗi')
        }
      },
      async () => {
        if (referenceRunFinalizedRef.current) return
        referenceRunFinalizedRef.current = true
        setReferenceGenerating(false)
        try {
          const latestPlan = await getScenePlan(project)
          setPlan(latestPlan)
          const references = Array.isArray(latestPlan?.references) ? latestPlan.references : []
          const incomplete = references.filter(ref => !ref.image_file && ref.status !== 'failed')
          const failed = references.filter(ref => !ref.image_file && ref.status === 'failed')
          if (!autoGenerateScenesAfterReferences || !references.length || incomplete.length) return
          if (failed.length) {
            const message = `Không tạo ảnh video: ${failed.length}/${references.length} reference bị lỗi. Hãy retry reference lỗi trước.`
            setError(message)
            setReferenceProgress(prev => ({ ...prev, message }))
            return
          }
          setProgress(prev => ({ ...prev, message: 'Toàn bộ reference đã hoàn tất · bắt đầu tạo ảnh video...' }))
          window.setTimeout(() => handleGenerate(null, false), 0)
        } catch (e) {
          setError(e?.message || 'Không thể kiểm tra trạng thái reference sau khi tạo')
        }
      },
      (e) => {
        setReferenceGenerating(false)
        setError(e?.message || String(e))
      },
    )
  }

  async function handleSelectReference(referenceId, imageFile) {
    if (!referenceId || !imageFile || selectingReference) return
    setError('')
    setSelectingReference(`${referenceId}:${imageFile}`)
    try {
      const data = await selectDirectorReference(project, referenceId, imageFile)
      setPlan(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setSelectingReference('')
    }
  }

  async function handleRegenerateDirectorPrompt(scene) {
    if (!scene?.id || loading || generating) return
    setError('')
    setLoading(true)
    try {
      const data = await regenerateDirectorScenePrompt(project, scene.id)
      setPlan(data)
      setProgress(prev => ({ ...prev, message: `Đã regenerate prompt ${scene.id}` }))
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleApplySafePrompts() {
    if (!project || !plan || generating || safeApplying || safeApplyingSceneId) return
    setError('')
    setSafeApplying(true)
    try {
      const failedSceneIds = (plan.scenes || [])
        .filter(scene => scene.status === 'failed' && !hasSceneImage(scene))
        .map(scene => scene.id)
      if (!failedSceneIds.length) {
        setProgress(prev => ({ ...prev, message: 'Không có scene lỗi cần safe prompt' }))
        return
      }
      const data = await applySceneSafePrompts(project, { scene_ids: failedSceneIds, only_failed: true, mode: safeMode })
      setPlan(data)
      const batch = data.last_safe_prompt_batch || {}
      setProgress(prev => ({
        ...prev,
        message: `Đã tạo safe ${batch.mode || safeMode} cho ${batch.applied ?? failedSceneIds.length} scene lỗi${batch.max_level ? ` · lv${batch.max_level}` : ''}`,
      }))
    } catch (e) {
      setError(e.message)
    } finally {
      setSafeApplying(false)
    }
  }

  async function handleApplySafePromptForScene(scene) {
    if (!project || !scene?.id || generating || safeApplying || safeApplyingSceneId) return
    setError('')
    setSafeApplyingSceneId(scene.id)
    try {
      const data = await applySceneSafePrompts(project, { scene_ids: [scene.id], only_failed: false, mode: safeMode })
      setPlan(data)
      const updated = data.scenes?.find(item => item.id === scene.id)
      setProgress(prev => ({
        ...prev,
        message: `Đã tạo safe ${safeMode} cho ${scene.id}${
          safeMode === 'llm'
            ? (updated?.safe_llm_level ? ` · lv${updated.safe_llm_level}` : '')
            : (safeRuleLevel(updated) ? ` · lv${safeRuleLevel(updated)}` : '')
        }`,
      }))
    } catch (e) {
      setError(e.message)
    } finally {
      setSafeApplyingSceneId('')
    }
  }

  async function handleSetActivePromptMode(scene, mode) {
    if (!scene?.id || generating || safeApplying || safeApplyingSceneId) return
    setError('')
    try {
      const data = await updateScene(project, scene.id, { active_prompt_mode: mode })
      setPlan(data)
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleCancel() {
    if (!project || cancelling) return
    setCancelling(true)
    setError('')
    try {
      const result = await cancelSceneGeneration(project)
      const count = Number(result?.cancelled_scenes || 0)
      setProgress(prev => ({
        ...prev,
        message: count ? `Đã dừng ${count} ảnh đang retry/tạo` : 'Đã gửi lệnh dừng retry',
      }))
    } catch (e) {
      setError(`Không thể dừng retry: ${e.message}`)
    } finally {
      setGenerating(false)
      setActiveSceneIds([])
      setCancelling(false)
    }
  }

  function scrollToCurrentScene() {
    if (!currentScene?.id) return
    const node = sceneRefs.current[currentScene.id]
    if (!node) return
    node.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' })
  }

  function openEdit(scene) {
    setEditing(scene)
    const mode = activePromptMode(scene)
    if (mode === 'director') {
      setDraftPrompt(scene.director_prompt || '')
    } else if (mode === 'safe_llm') {
      setDraftPrompt(scene.safe_llm_prompt || '')
    } else if (mode === 'safe_rule') {
      setDraftPrompt(scene.safe_rule_prompt || scene.safe_prompt || '')
    } else {
      setDraftPrompt(scene.prompt || '')
    }
  }

  async function savePrompt() {
    if (!editing) return
    try {
      const mode = activePromptMode(editing)
      const patch = mode === 'director'
        ? { director_prompt: draftPrompt }
        : mode === 'safe_llm'
        ? { safe_llm_prompt: draftPrompt }
        : mode === 'safe_rule'
          ? { safe_rule_prompt: draftPrompt }
          : { prompt: draftPrompt }
      const data = await updateScene(project, editing.id, patch)
      setPlan(data)
      setEditing(null)
      setDraftPrompt('')
    } catch (e) {
      setError(e.message)
    }
  }

  async function openReview(scene) {
    setError('')
    setReviewing(scene)
    try {
      const data = await getScenePlan(project)
      setPlan(data)
      setReviewing(data.scenes?.find(item => item.id === scene.id) || scene)
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleSelectImage(scene, imageFile) {
    if (!scene || !imageFile || selectingImage) return
    setError('')
    setSelectingImage(imageFile)
    try {
      const data = await selectSceneImage(project, scene.id, imageFile)
      setPlan(data)
      setReviewing(data.scenes?.find(item => item.id === scene.id) || null)
    } catch (e) {
      setError(e.message)
    } finally {
      setSelectingImage('')
    }
  }

  function markScene(currentPlan, sceneId, patch) {
    if (!currentPlan?.scenes) return currentPlan
    return {
      ...currentPlan,
      scenes: currentPlan.scenes.map(scene => (
        scene.id === sceneId ? { ...scene, ...patch } : scene
      )),
    }
  }

  function markReference(currentPlan, referenceId, patch) {
    if (!currentPlan?.references) return currentPlan
    return {
      ...currentPlan,
      references: currentPlan.references.map(ref => (
        ref.id === referenceId ? { ...ref, ...patch } : ref
      )),
    }
  }

  const canCompose = stats.total > 0 && stats.done === stats.total && !generating && !referenceGenerating
  const visualPlanNeedsApproval = plan?.mode === 'stick_figure'
    && Number(plan?.schema_version || 0) === 2
    && plan.approval_status !== 'approved'
  const percent = progress.total
    ? Math.round(((progress.done + (progress.runningProgress ?? ((progress.sceneProgress || 0) / 100))) / progress.total) * 100)
    : 0

  return (
    <div className="space-y-5">
      <div className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-white">Tạo ảnh theo từng cảnh</p>
            <p className="text-xs text-slate-500">Dùng timing audio để chia scene, tạo ảnh theo provider đã cấu hình rồi dựng thành video 16:9.</p>
          </div>
          <span className="text-xs text-accent-300 bg-accent-500/10 border border-accent-500/20 rounded-full px-2 py-1">
            {stats.done}/{stats.total || 0} ảnh
          </span>
        </div>

        <div className="rounded-xl border border-surface-600 bg-surface-950/45 px-3 py-3 flex flex-col xl:flex-row xl:items-center xl:justify-between gap-3">
          <div className="flex items-start gap-3 min-w-0">
            <div className={`mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-lg border ${
              imageProviderInfo?.kind === 'custom'
                ? 'border-sky-400/30 bg-sky-500/10 text-sky-300'
                : 'border-accent-400/30 bg-accent-500/10 text-accent-300'
            }`}>
              {imageProviderInfo?.kind === 'custom' ? <Cloud size={17} /> : <Server size={17} />}
            </div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">Provider ảnh</span>
                {imageProviderLoading && <Loader2 size={12} className="animate-spin text-accent-300" />}
              </div>
              <p className="truncate text-sm font-semibold text-white">
                {imageProviderInfo?.title || (imageProviderLoading ? 'Đang đọc cấu hình...' : 'Chưa đọc cấu hình')}
              </p>
              <p className="truncate text-xs text-slate-500">
                {imageProviderInfo?.subtitle || imageProviderError || 'Bấm refresh để đọc lại provider hiện tại'}
              </p>
              {(imageProviderInfo?.warning || imageProviderError) && (
                <p className="mt-1 text-xs text-amber-300">{imageProviderInfo?.warning || imageProviderError}</p>
              )}
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] xl:flex xl:items-center gap-2">
            <div className="min-w-0 rounded-lg border border-surface-600 bg-surface-900/70 px-3 py-2">
              <p className="text-[11px] uppercase tracking-wider text-slate-500">Model</p>
              <p className="truncate text-xs font-semibold text-slate-200">{imageProviderInfo?.model || '...'}</p>
            </div>
            <div className="min-w-0 rounded-lg border border-surface-600 bg-surface-900/70 px-3 py-2">
              <p className="text-[11px] uppercase tracking-wider text-slate-500">Size</p>
              <p className="truncate text-xs font-semibold text-slate-200">{imageProviderInfo?.size || '...'}</p>
            </div>
            {imageProviderInfo?.pool && (
              <div className="min-w-0 rounded-lg border border-surface-600 bg-surface-900/70 px-3 py-2 sm:col-span-2 xl:max-w-xs">
                <p className="text-[11px] uppercase tracking-wider text-slate-500">Account pool</p>
                <p className="truncate text-xs font-semibold text-slate-200">{imageProviderInfo.pool}</p>
              </div>
            )}
            <button
              type="button"
              onClick={() => loadImageProviderInfo(false)}
              disabled={imageProviderLoading}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-surface-500 px-3 text-xs font-semibold text-slate-300 hover:border-accent-400 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RefreshCw size={14} className={imageProviderLoading ? 'animate-spin' : ''} />
              Làm mới
            </button>
          </div>
        </div>

        <div className="rounded-xl border border-surface-600 bg-surface-950/50 p-3 flex flex-col gap-2">
          <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Mode dựng ảnh</span>
          <div className="inline-flex w-fit rounded-xl border border-surface-600 bg-surface-900 p-1">
            <button
              type="button"
              onClick={() => setPlanMode('fast')}
              disabled={loading || generating || referenceGenerating}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                planMode === 'fast' ? 'bg-accent-600 text-white' : 'text-slate-400 hover:text-white'
              } disabled:opacity-50 disabled:cursor-not-allowed`}
            >
              Fast
            </button>
            <button
              type="button"
              onClick={() => setPlanMode('director')}
              disabled={loading || generating || referenceGenerating}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                planMode === 'director' ? 'bg-accent-600 text-white' : 'text-slate-400 hover:text-white'
              } disabled:opacity-50 disabled:cursor-not-allowed`}
            >
              Director
            </button>
            {stickFigureAvailable && (
              <button
                type="button"
                onClick={() => setPlanMode('stick_figure')}
                disabled={loading || generating || referenceGenerating}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                  planMode === 'stick_figure' ? 'bg-sky-600 text-white' : 'text-slate-400 hover:text-white'
                } disabled:opacity-50 disabled:cursor-not-allowed`}
              >
                Người que
              </button>
            )}
          </div>
          <p className="text-xs text-slate-500">
            {planMode === 'stick_figure'
              ? 'Sau khi có audio, AI lập Visual Plan theo từng ý narration và timing thực tế. Bạn duyệt plan trước rồi mới tạo storyboard, reference và ảnh final.'
              : planMode === 'director'
              ? 'AI tạo Visual Bible, storyboard, reference nhân vật/bối cảnh và prompt điện ảnh. Bật Visual Plan để AI tự chia nhịp cảnh theo narration và timing audio thay vì cố định giây/cảnh.'
              : 'Dùng template prompt hiện tại: nhanh, rẻ, phù hợp khi chỉ cần ảnh nền theo timing.'}
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-[1fr_140px] gap-3">
          <label className="space-y-1">
            <span className="flex items-center justify-between gap-2 text-xs font-medium text-slate-400 uppercase tracking-wider">
              <span>Phong cách ảnh</span>
              {style && (
                <button
                  type="button"
                  onClick={handleClearImageStyle}
                  disabled={loading || generating || referenceGenerating}
                  className="normal-case inline-flex items-center gap-1 rounded-md border border-red-400/30 bg-red-500/10 px-2 py-1 text-[11px] font-semibold text-red-200 hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Trash2 size={12} />
                  Xóa phong cách
                </button>
              )}
            </span>
            <input
              value={style}
              onChange={e => setStyle(e.target.value)}
              placeholder="Để AI tự chọn phong cách theo nội dung"
              className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
            />
          </label>
          <label className="space-y-1">
            <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">
              {planMode === 'stick_figure' ? 'Timing AI' : planMode === 'director' && directorUseVisualPlan ? 'Nhịp mục tiêu AI' : 'Giây / cảnh'}
            </span>
            <input
              type="number"
              min="5"
              max="30"
              value={targetSec}
              onChange={e => setTargetSec(e.target.value)}
              disabled={planMode === 'stick_figure' || (planMode === 'director' && directorUseVisualPlan)}
              title={planMode === 'stick_figure' || (planMode === 'director' && directorUseVisualPlan) ? 'AI quyết định visual beat từ ý narration và timing audio thực tế, không dùng thời lượng cố định.' : ''}
              className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 disabled:opacity-50 disabled:cursor-not-allowed"
            />
          </label>
          {planMode === 'stick_figure' && (
            <label className="space-y-1 sm:col-span-2">
              <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Style sheet tham chiếu (khuyến nghị)</span>
              <input
                ref={styleReferenceInputRef}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                onChange={e => handleStyleReferencePick(e.target.files?.[0] || null)}
                className="block w-full text-xs text-slate-400 file:mr-3 file:rounded-lg file:border-0 file:bg-sky-500/15 file:px-3 file:py-2 file:text-xs file:font-semibold file:text-sky-200 hover:file:bg-sky-500/25"
              />
              <p className="text-xs text-slate-600">
                {styleReferenceFile ? `Sẽ dùng: ${styleReferenceFile.name}` : styleReferenceId ? 'Đang dùng style sheet đã tải lên.' : 'Tải ảnh mẫu nhân vật/bối cảnh để giữ nét vẽ, palette và bố cục thống nhất.'}
              </p>
            </label>
          )}
        </div>

        {planMode === 'director' && (
          <label className="flex cursor-pointer items-start gap-2 rounded-xl border border-accent-400/25 bg-accent-500/5 px-3 py-2.5 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={directorUseVisualPlan}
              onChange={e => updateDirectorUseVisualPlan(e.target.checked)}
              disabled={loading || generating || referenceGenerating}
              className="mt-0.5"
            />
            <span>
              <span className="block font-semibold text-accent-100">AI tự lập Visual Plan theo narration</span>
              <span className="mt-0.5 block text-slate-500">AI tự quyết mỗi cảnh kéo dài bao lâu dựa trên ý nghĩa lời đọc và timing audio. Tắt để dùng cách chia cố định theo “Giây / cảnh”.</span>
            </span>
          </label>
        )}

        {(planMode === 'stick_figure' || (planMode === 'director' && directorUseVisualPlan)) && (
          <section className="rounded-xl border border-sky-400/25 bg-sky-500/5 p-3 space-y-3">
            <div>
              <p className="text-sm font-semibold text-white">Chỉ đạo Visual Plan <span className="text-slate-500 font-normal">(tùy chọn)</span></p>
              <p className="mt-1 text-xs text-slate-400">
                Chỉ ảnh hưởng cách AI gộp/tách beat, loại hình, camera, cảm xúc và nhịp cảnh. Không đổi lời narration hoặc timing audio; Director vẫn tự tạo Visual Bible, reference và prompt ảnh final sau khi plan xong.
              </p>
            </div>
            <textarea
              value={visualPlanInstruction}
              onChange={e => updateVisualPlanInstruction(e.target.value)}
              maxLength={2000}
              rows={3}
              placeholder="Ví dụ: Giữ các ý gia đình trong cùng một cảnh, chỉ chuyển khi câu chuyện sang địa điểm hoặc sự kiện khác."
              className="w-full resize-y bg-surface-950 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-600 focus:outline-none focus:border-sky-400"
            />
            <div className="flex flex-wrap gap-2">
              {VISUAL_PLAN_INSTRUCTION_PRESETS.map(preset => (
                <button
                  key={preset.label}
                  type="button"
                  onClick={() => updateVisualPlanInstruction(preset.value)}
                  className={`rounded-lg border px-2.5 py-1.5 text-xs font-semibold transition-colors ${
                    visualPlanInstruction === preset.value
                      ? 'border-sky-300 bg-sky-400/20 text-sky-100'
                      : 'border-surface-600 bg-surface-900 text-slate-300 hover:border-sky-400/50 hover:text-white'
                  }`}
                >
                  {preset.label}
                </button>
              ))}
            </div>
          </section>
        )}

        <div className="rounded-xl border border-surface-600 bg-surface-950/50 p-3 flex flex-col lg:flex-row lg:items-end gap-3">
          <div className="space-y-1">
            <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Chế độ tạo</span>
            <div className="inline-flex rounded-xl border border-surface-600 bg-surface-900 p-1">
              <button
                type="button"
                onClick={() => setGenerationMode('sequential')}
                disabled={generating}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                  generationMode === 'sequential'
                    ? 'bg-accent-600 text-white'
                    : 'text-slate-400 hover:text-white'
                } disabled:opacity-50 disabled:cursor-not-allowed`}
              >
                Tuần tự
              </button>
              <button
                type="button"
                onClick={() => setGenerationMode('parallel')}
                disabled={generating}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                  generationMode === 'parallel'
                    ? 'bg-accent-600 text-white'
                    : 'text-slate-400 hover:text-white'
                } disabled:opacity-50 disabled:cursor-not-allowed`}
              >
                Song song
              </button>
            </div>
          </div>

          <div className="space-y-1">
            <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Safe mode</span>
            <div className="inline-flex rounded-xl border border-surface-600 bg-surface-900 p-1">
              <button
                type="button"
                onClick={() => setSafeMode('rule')}
                disabled={generating || safeApplying || !!safeApplyingSceneId}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                  safeMode === 'rule'
                    ? 'bg-amber-500/20 text-amber-100'
                    : 'text-slate-400 hover:text-white'
                } disabled:opacity-50 disabled:cursor-not-allowed`}
              >
                Rule
              </button>
              <button
                type="button"
                onClick={() => setSafeMode('llm')}
                disabled={generating || safeApplying || !!safeApplyingSceneId}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                  safeMode === 'llm'
                    ? 'bg-amber-500/20 text-amber-100'
                    : 'text-slate-400 hover:text-white'
                } disabled:opacity-50 disabled:cursor-not-allowed`}
              >
                LLM
              </button>
            </div>
          </div>

          {plan?.mode === 'stick_figure' && Number(plan?.schema_version || 0) === 2 && plan.approval_status !== 'approved' && (
            <>
              <label className="inline-flex items-center gap-2 rounded-xl border border-sky-400/30 px-3 py-2 text-xs text-slate-300">
                <input type="checkbox" checked={autoPrepareReferences} onChange={e => setAutoPrepareReferences(e.target.checked)} />
                Tự tạo reference sau khi duyệt
              </label>
              <button
                onClick={handleApproveVisualPlan}
                disabled={loading || generating || referenceGenerating}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold bg-sky-600 hover:bg-sky-500 text-white disabled:opacity-40"
              >
                <CheckCircle2 size={16} /> Duyệt Visual Plan
              </button>
            </>
          )}
          {hasReferenceWorkflow && (
            <div className="space-y-1">
              <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Reference</span>
              <div className="inline-flex rounded-xl border border-surface-600 bg-surface-900 p-1">
                <button
                  type="button"
                  onClick={() => setReferenceGenerationMode('sequential')}
                  disabled={referenceGenerating}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                    referenceGenerationMode === 'sequential'
                      ? 'bg-sky-500/20 text-sky-100'
                      : 'text-slate-400 hover:text-white'
                  } disabled:opacity-50 disabled:cursor-not-allowed`}
                >
                  Tuần tự
                </button>
                <button
                  type="button"
                  onClick={() => setReferenceGenerationMode('parallel')}
                  disabled={referenceGenerating}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                    referenceGenerationMode === 'parallel'
                      ? 'bg-sky-500/20 text-sky-100'
                      : 'text-slate-400 hover:text-white'
                  } disabled:opacity-50 disabled:cursor-not-allowed`}
                >
                  Song song
                </button>
              </div>
            </div>
          )}

          {hasReferenceWorkflow && (
            <label className="flex w-full max-w-md cursor-pointer items-start gap-2 rounded-xl border border-sky-400/25 bg-sky-500/5 px-3 py-2.5 text-xs text-slate-300">
              <input
                type="checkbox"
                checked={autoGenerateScenesAfterReferences}
                onChange={e => updateAutoGenerateScenesAfterReferences(e.target.checked)}
                disabled={generating || referenceGenerating || loading}
                className="mt-0.5"
              />
              <span>
                <span className="block font-semibold text-sky-100">Tự tạo ảnh video sau khi reference xong</span>
                <span className="mt-0.5 block text-slate-500">Chỉ chạy khi tất cả reference thành công. Nếu còn reference lỗi, hệ thống dừng và chỉ báo sau khi các reference còn lại đã hoàn tất.</span>
              </span>
            </label>
          )}

          <label className="space-y-1 w-full lg:w-44">
            <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Ảnh cùng lúc</span>
            <input
              type="number"
              min="1"
              value={parallelCount}
              onChange={e => setParallelCount(clampParallelCount(e.target.value))}
              disabled={generating || referenceGenerating || (generationMode !== 'parallel' && referenceGenerationMode !== 'parallel')}
              className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500 disabled:opacity-45 disabled:cursor-not-allowed"
            />
          </label>

          <p className="text-xs text-slate-500 lg:pb-2">
            Mỗi luồng gửi một request ảnh độc lập. Scene: {generationMode === 'parallel' ? `${clampParallelCount(parallelCount)} luồng` : '1 luồng'}.
            {hasReferenceWorkflow && ` Reference: ${referenceGenerationMode === 'parallel' ? `${clampParallelCount(parallelCount)} luồng` : 'từng ảnh'}.`}
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            onClick={requestCreatePlan}
            disabled={!project || !audioFile || loading || generating || referenceGenerating}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold bg-surface-700 hover:bg-surface-600 text-white disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {loading ? <Loader2 size={16} className="animate-spin" /> : <ImagePlus size={16} />}
            {planMode === 'stick_figure'
              ? (plan?.mode === 'stick_figure' && Number(plan?.schema_version || 0) === 2 ? 'Tái lập Visual Plan từ narration' : 'Tạo Visual Plan')
              : planMode === 'director' ? (directorUseVisualPlan ? 'Tạo Visual Plan Director' : 'Tạo Director plan') : 'Tạo lại plan scene'}
          </button>
          {hasReferenceWorkflow && (
            <button
              onClick={() => handleGenerateReferences(null, false)}
              disabled={!plan || (plan?.mode === 'stick_figure' && Number(plan?.schema_version || 0) === 2 && plan.approval_status !== 'approved') || referenceGenerating || generating || loading}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold border border-sky-400/40 text-sky-200 hover:text-sky-100 hover:border-sky-300 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {referenceGenerating ? <Loader2 size={16} className="animate-spin" /> : <Images size={16} />}
              Tạo reference còn thiếu
            </button>
          )}
          {hasReferenceWorkflow && stats.referencesFailed > 0 && (
            <button
              onClick={() => handleGenerateReferences(null, true)}
              disabled={!plan || (plan?.mode === 'stick_figure' && Number(plan?.schema_version || 0) === 2 && plan.approval_status !== 'approved') || referenceGenerating || generating || loading}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold border border-surface-500 text-slate-300 hover:text-white hover:border-sky-400 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <RefreshCw size={16} />
              Retry reference lỗi
            </button>
          )}
          <button
            onClick={() => handleGenerate(null, false)}
            disabled={!plan || (plan?.mode === 'stick_figure' && Number(plan?.schema_version || 0) === 2 && plan.approval_status !== 'approved') || generating || loading || referenceGenerating}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold bg-accent-600 hover:bg-accent-500 text-white disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {generating ? <Loader2 size={16} className="animate-spin" /> : <WandSparkles size={16} />}
            Tạo ảnh còn thiếu
          </button>
          <button
            onClick={handleApplySafePrompts}
            disabled={!plan || !stats.failed || generating || loading || referenceGenerating || safeApplying || !!safeApplyingSceneId}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold border border-amber-400/40 text-amber-200 hover:text-amber-100 hover:border-amber-300 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {safeApplying ? <Loader2 size={16} className="animate-spin" /> : <WandSparkles size={16} />}
            Safe {safeMode === 'llm' ? 'LLM' : 'Rule'} ảnh lỗi{stats.failed ? ` (${stats.failed})` : ''}
          </button>
          <button
            onClick={() => handleGenerate(null, true)}
            disabled={!plan || (plan?.mode === 'stick_figure' && Number(plan?.schema_version || 0) === 2 && plan.approval_status !== 'approved') || generating || loading || referenceGenerating}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold border border-surface-500 text-slate-300 hover:text-white hover:border-accent-400 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <RefreshCw size={16} />
            Retry ảnh lỗi ({clampParallelCount(parallelCount)} luồng)
          </button>
          {(generating || planHasRunningScene) && (
            <button
              onClick={handleCancel}
              disabled={cancelling}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold border border-red-500/40 text-red-300 hover:text-red-200"
            >
              {cancelling ? <Loader2 size={16} className="animate-spin" /> : <StopCircle size={16} />}
              {cancelling ? 'Đang dừng…' : 'Dừng tạo / retry'}
            </button>
          )}
        </div>

        {(generating || progress.message) && (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-400">{progress.message}</span>
              <span className="font-mono text-white">{percent}%</span>
            </div>
            <div className="h-2 bg-surface-700 rounded-full overflow-hidden">
              <div className="h-full bg-accent-500 rounded-full transition-all duration-500" style={{ width: `${percent}%` }} />
            </div>
          </div>
        )}

        {(referenceGenerating || referenceProgress.message) && (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-400">{referenceProgress.message}</span>
              <span className="font-mono text-white">
                {referenceProgress.total ? `${Math.round((referenceProgress.done / referenceProgress.total) * 100)}%` : '0%'}
              </span>
            </div>
            <div className="h-2 bg-surface-700 rounded-full overflow-hidden">
              <div
                className="h-full bg-sky-500 rounded-full transition-all duration-500"
                style={{ width: `${referenceProgress.total ? Math.round((referenceProgress.done / referenceProgress.total) * 100) : 0}%` }}
              />
            </div>
          </div>
        )}

        {error && (
          <div className="flex items-start gap-2 text-sm text-red-400">
            <AlertCircle size={15} className="mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}
      </div>

      {plan && (
        <>
        {plan.mode === 'stick_figure' && Number(plan.schema_version || 0) === 2 && (
          <div className="rounded-2xl border border-sky-400/25 bg-sky-500/5 p-4 space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-sm font-semibold text-white">Visual Plan AI</p>
                <p className="text-xs text-slate-400">{plan.coverage?.covered_units || 0}/{plan.coverage?.total_units || 0} ý narration đã được phủ · {stats.total} visual beat · trung bình {stats.total ? `${(Number(plan.audio_duration_sec || 0) / stats.total).toFixed(1)}s` : '0s'}/beat</p>
              </div>
              <span className={`rounded-full px-2 py-1 text-xs font-semibold ${plan.approval_status === 'approved' ? 'bg-emerald-500/15 text-emerald-200' : 'bg-amber-500/15 text-amber-200'}`}>
                {plan.approval_status === 'approved' ? 'Đã duyệt' : 'Chờ duyệt'}
              </span>
            </div>
            {plan.coverage?.critic_status && <p className="text-xs text-slate-500">Critic: {plan.coverage.critic_status}</p>}
            {plan.visual_plan_instruction?.trim() && (
              <div className="rounded-lg border border-sky-400/20 bg-surface-950/40 px-3 py-2">
                <p className="text-xs font-semibold uppercase tracking-wider text-sky-200">Chỉ đạo đã dùng</p>
                <p className="mt-1 whitespace-pre-wrap text-xs text-slate-300">{plan.visual_plan_instruction}</p>
              </div>
            )}
          </div>
        )}
        {hasReferenceWorkflow && (
          <div className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-white">{plan.mode === 'stick_figure' ? 'Storyboard tổng quan & references' : 'Director references'}</p>
                <p className="text-xs text-slate-500">
                  {plan.mode === 'stick_figure'
                    ? `${stats.referencesDone}/${stats.references || 0} asset đã tạo · storyboard là contact sheet để duyệt nhịp truyện, không lặp lại từng ảnh final.`
                    : `${stats.referencesDone}/${stats.references || 0} reference đã chọn · dùng để giữ nhân vật/bối cảnh đồng nhất.`}
                </p>
              </div>
              <button
                onClick={() => handleGenerateReferences(null, false)}
                disabled={referenceGenerating || generating || loading || !plan.references?.length || (plan?.mode === 'stick_figure' && Number(plan?.schema_version || 0) === 2 && plan.approval_status !== 'approved')}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold border border-sky-400/40 text-sky-200 hover:text-sky-100 hover:border-sky-300 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {referenceGenerating ? <Loader2 size={16} className="animate-spin" /> : <Images size={16} />}
                {plan.mode === 'stick_figure' ? 'Tạo storyboard tổng quan & reference' : 'Tạo reference'}
              </button>
            </div>

            {stats.references > visibleReferences.length && (
              <button
                type="button"
                onClick={() => setShowAllReferences(true)}
                className="text-left text-xs font-semibold text-sky-300 hover:text-sky-100"
              >
                Hiện tất cả {stats.references} reference (đang xem 24 để giao diện mượt)
              </button>
            )}
            {showAllReferences && stats.references > 24 && (
              <button
                type="button"
                onClick={() => setShowAllReferences(false)}
                className="text-left text-xs font-semibold text-slate-400 hover:text-slate-200"
              >
                Chỉ hiện 24 reference đầu
              </button>
            )}

            {plan.visual_bible && (
              <div className="rounded-xl border border-surface-700 bg-surface-950/50 p-3">
                <p className="text-xs font-semibold uppercase tracking-wider text-accent-200 mb-1">Visual Bible</p>
                <p className="text-xs text-slate-400 line-clamp-3">
                  {plan.visual_bible.logline || plan.visual_bible.visual_style || 'Director đã tạo visual bible cho truyện.'}
                </p>
              </div>
            )}

            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              {visibleReferences.map(ref => {
                const history = referenceHistory(ref)
                return (
                  <article key={ref.id} className={`rounded-xl border border-surface-600 bg-surface-950/45 overflow-hidden ${ref.status === 'running' ? 'scene-card-running' : ''}`}>
                    <div className="relative aspect-video bg-surface-800 flex items-center justify-center text-slate-600">
                      {ref.image_file ? (
                        <img src={directorReferenceUrl(project, ref.image_file)} alt={ref.name || ref.id} loading="lazy" decoding="async" className="h-full w-full object-cover" />
                      ) : ref.status === 'running' ? (
                        <Loader2 size={26} className="animate-spin text-sky-300" />
                      ) : (
                        <Images size={26} />
                      )}
                      {ref.status === 'running' && (
                        <>
                          <div className="scene-loading-sheen pointer-events-none" />
                          <div className="absolute inset-0 flex items-center justify-center bg-slate-950/38 pointer-events-none">
                            <span className="inline-flex items-center gap-2 rounded-full border border-sky-300/35 bg-slate-950/85 px-3 py-1.5 text-xs font-semibold text-sky-100 shadow-lg">
                              <Loader2 size={14} className="animate-spin" />
                              Đang tạo reference
                            </span>
                          </div>
                        </>
                      )}
                    </div>
                    <div className="p-3 space-y-2">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold text-white">{ref.name || ref.id}</p>
                          <p className="text-[11px] uppercase tracking-wider text-slate-500">{ref.type || 'reference'} · {ref.id}</p>
                        </div>
                        <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] ${
                          ref.status === 'succeeded' ? 'border-green-500/30 bg-green-500/10 text-green-300'
                            : ref.status === 'failed' ? 'border-red-500/30 bg-red-500/10 text-red-300'
                              : ref.status === 'running' ? 'border-sky-500/30 bg-sky-500/10 text-sky-300'
                                : 'border-surface-500 bg-surface-800 text-slate-400'
                        }`}>
                          {ref.status || 'pending'}
                        </span>
                      </div>
                      <p className="text-xs text-slate-400 line-clamp-3">{ref.description || ref.prompt}</p>
                      {ref.error && <p className="text-xs text-red-400">{ref.error}</p>}
                      {history.length > 1 && (
                        <div className="flex gap-1 overflow-x-auto pb-1">
                          {history.map(item => (
                            <button
                              key={item.image_file}
                              type="button"
                              onClick={() => handleSelectReference(ref.id, item.image_file)}
                              disabled={selectingReference === `${ref.id}:${item.image_file}`}
                              className={`h-12 w-20 shrink-0 overflow-hidden rounded-lg border ${
                                item.selected ? 'border-sky-300' : 'border-surface-600 hover:border-sky-400'
                              } disabled:opacity-50`}
                              title={item.image_file}
                            >
                              <img src={directorReferenceUrl(project, item.image_file)} alt="" loading="lazy" decoding="async" className="h-full w-full object-cover" />
                            </button>
                          ))}
                        </div>
                      )}
                      <div className="flex flex-wrap gap-2">
                        <button
                          type="button"
                          onClick={() => handleGenerateReferences([ref.id], true)}
                          disabled={referenceGenerating || generating || loading}
                          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold border border-surface-500 text-slate-300 hover:text-white hover:border-sky-400 disabled:opacity-40"
                        >
                          <RefreshCw size={13} />
                          Retry
                        </button>
                      </div>
                    </div>
                  </article>
                )
              })}
            </div>
          </div>
        )}

        <div className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-white">Hiệu ứng vẽ ảnh</p>
              <p className="text-xs text-slate-500">Áp dụng khi dựng video từ ảnh theo cảnh. Nền mở đầu là giấy kem; scene riêng có thể ghi đè bên dưới.</p>
            </div>
            <span className={`rounded-full border px-2 py-1 text-xs font-semibold ${globalSceneReveal.enabled ? 'border-amber-400/35 bg-amber-500/10 text-amber-200' : 'border-surface-500 bg-surface-800 text-slate-400'}`}>
              {globalSceneReveal.enabled ? 'Đang bật' : 'Tắt mặc định'}
            </span>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <label className="space-y-1">
              <span className="text-xs font-medium uppercase tracking-wider text-slate-400">Hiệu ứng</span>
              <select
                value={globalSceneReveal.enabled ? globalSceneReveal.effect : 'none'}
                onChange={event => updateGlobalSceneReveal(event.target.value === 'none' ? { enabled: false } : { enabled: true, effect: event.target.value })}
                className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white focus:border-accent-500 focus:outline-none"
              >
                <option value="none">Không hiệu ứng</option>
                <option value="brush">Nét cọ reveal</option>
                <option value="pencil">Bút chì rồi lên màu</option>
              </select>
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium uppercase tracking-wider text-slate-400">Thời lượng (giây)</span>
              <input
                type="number"
                min="0.4"
                max="3"
                step="0.1"
                value={globalSceneReveal.durationSec}
                onChange={event => updateGlobalSceneReveal({ durationSec: event.target.value })}
                className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white focus:border-accent-500 focus:outline-none"
              />
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium uppercase tracking-wider text-slate-400">Hướng nét</span>
              <select value={globalSceneReveal.pattern} onChange={event => updateGlobalSceneReveal({ pattern: event.target.value })} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white focus:border-accent-500 focus:outline-none">
                <option value="auto">Tự động theo scene</option>
                <option value="left_to_right">Trái sang phải</option>
                <option value="right_to_left">Phải sang trái</option>
                <option value="top_to_bottom">Trên xuống dưới</option>
                <option value="diagonal">Chéo</option>
                <option value="scribble">Nét nguệch ngoạc</option>
              </select>
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium uppercase tracking-wider text-slate-400">Vật thể vẽ</span>
              <select value={globalSceneReveal.actor} onChange={event => updateGlobalSceneReveal({ actor: event.target.value })} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white focus:border-accent-500 focus:outline-none">
                <option value="none">Không có</option>
                <option value="hand">Tay minh họa 2D</option>
                <option value="pencil">Bút chì</option>
                <option value="brush">Cọ mực</option>
              </select>
            </label>
          </div>

          {previewScene ? (
            <div className="grid grid-cols-1 gap-3 rounded-xl border border-surface-700 bg-surface-950/45 p-3 lg:grid-cols-[minmax(0,1fr)_220px]">
              <SceneRevealPreview
                imageUrl={sceneImageUrl(project, previewScene.image_file)}
                sceneId={previewScene.id}
                config={resolvedSceneReveal(previewScene, globalSceneReveal)}
              />
              <label className="space-y-1 self-start">
                <span className="text-xs font-medium uppercase tracking-wider text-slate-400">Ảnh preview</span>
                <select value={previewScene.id} onChange={event => setPreviewSceneId(event.target.value)} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white focus:border-accent-500 focus:outline-none">
                  {previewScenes.map(scene => <option key={scene.id} value={scene.id}>{scene.id} · {fmtSec(scene.start_sec)}-{fmtSec(scene.end_sec)}</option>)}
                </select>
                <p className="text-xs leading-relaxed text-slate-500">Preview chạy nhanh trong trình duyệt để xem nhịp. File MP4 dùng renderer Pillow + FFmpeg chính thức.</p>
              </label>
            </div>
          ) : (
            <p className="text-xs text-slate-500">Tạo ít nhất một ảnh scene để xem preview. Cấu hình vẫn được lưu cho lần dựng sau.</p>
          )}
        </div>

        <div className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-white">Scene plan</p>
              <p className="text-xs text-slate-500">
                {stats.total} cảnh · timing: {plan.timing_source || 'unknown'} · audio {fmtSec(plan.audio_duration_sec)}
                {plan.mode === 'director' && plan.visual_plan?.enabled ? ` · Visual Plan: ${plan.visual_plan.status === 'visual_plan_ai' ? 'AI' : 'fallback'}` : ''}
                {stats.safeRuleFailed ? ` · ${stats.safeRuleFailed} rule` : ''}
                {stats.safeLlmFailed ? ` · ${stats.safeLlmFailed} llm` : ''}
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={scrollToCurrentScene}
                disabled={!currentScene}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold border border-surface-500 text-slate-300 hover:text-white hover:border-accent-400 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <LocateFixed size={16} />
                Hiện tại
              </button>
              <button
                onClick={() => onCompose([], { ...settings, videoMode: 'scenes' })}
                disabled={!canCompose}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold bg-accent-600 hover:bg-accent-500 text-white disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <CheckCircle2 size={16} />
                Dựng video từ ảnh
              </button>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 max-h-[520px] overflow-y-auto pr-1">
            {plan.scenes?.map(scene => {
              // The plan polling request can return the pre-retry `succeeded`
              // snapshot before the worker persists its new queued status.
              // Keep the local job state authoritative until SSE reports a
              // terminal result, otherwise Retry appears to do nothing.
              const locallyQueued = generating && activeSceneIds.includes(scene.id)
              const sceneBusy = scene.status === 'running' || locallyQueued
              const displayStatus = sceneBusy ? 'running' : scene.status
              const safeSceneBusy = safeApplyingSceneId === scene.id
              const historyCount = sceneImageHistory(scene).length
              const hasNewVariant = Boolean(
                scene.last_candidate_image_file
                && scene.last_candidate_image_file !== scene.image_file,
              )
              const generationKind = sceneGenerationKinds[scene.id] || scene.generation_kind || 'initial'
              const generationLabel = generationKind === 'retry'
                ? (scene.image_file ? 'Đang tạo biến thể mới' : 'Đang tạo lại ảnh lỗi')
                : 'Đang tạo ảnh'
              const renderingLabel = generationKind === 'retry'
                ? (scene.image_file ? 'Đang render biến thể mới' : 'Đang render lại ảnh lỗi')
                : 'Đang render ảnh'
              const lastRetryError = scene.last_generation_error && !scene.error ? scene.last_generation_error : ''
              const ruleReady = hasSafeRule(scene)
              const llmReady = hasSafeLlm(scene)
              const activeMode = activePromptMode(scene)
              const sceneRevealMode = scene?.reveal_override?.mode || 'inherit'
              const sceneReveal = resolvedSceneReveal(scene, globalSceneReveal)
              const revealUpdating = revealUpdatingSceneId === scene.id
              return (
              <div
                key={scene.id}
                ref={node => {
                  if (node) sceneRefs.current[scene.id] = node
                }}
                className={`rounded-xl border bg-surface-900 overflow-hidden transition-colors duration-200 ${
                  sceneBusy ? 'scene-card-running border-accent-500/60' : 'border-surface-600'
                }`}
              >
                <div className="relative">
                  {scene.image_file ? (
                    <img
                      src={sceneImageUrl(project, scene.image_file)}
                      alt={scene.id}
                      loading="lazy"
                      decoding="async"
                      className="w-full aspect-video object-cover bg-surface-800"
                    />
                  ) : (
                    <div className="w-full aspect-video bg-surface-800 flex items-center justify-center text-slate-600">
                      <ImagePlus size={28} />
                    </div>
                  )}
                  {sceneBusy && (
                    <div className="absolute inset-0 bg-slate-950/48 pointer-events-none">
                      <div className="scene-loading-sheen" />
                      <div className="absolute left-3 top-3 inline-flex items-center gap-2 rounded-full border border-accent-400/30 bg-surface-950/80 px-2.5 py-1 text-[11px] font-semibold text-accent-200">
                        <Loader2 size={12} className="animate-spin" />
                        {generationLabel}
                      </div>
                      <div className="absolute inset-0 flex items-center justify-center">
                        <span className="inline-flex items-center gap-2 rounded-full border border-accent-300/35 bg-slate-950/90 px-3 py-1.5 text-xs font-semibold text-accent-100 shadow-lg">
                          <Loader2 size={15} className="animate-spin" />
                          {renderingLabel}
                        </span>
                      </div>
                    </div>
                  )}
                </div>
                <div className="p-3 space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-mono text-accent-300">{scene.id} · {fmtSec(scene.start_sec)}-{fmtSec(scene.end_sec)}</span>
                    <div className="flex shrink-0 items-center gap-1.5">
                      {ruleReady && (
                        <button
                          type="button"
                          onClick={() => handleSetActivePromptMode(scene, 'safe_rule')}
                          disabled={activeMode === 'safe_rule' || generating || safeApplying || !!safeApplyingSceneId}
                          className={`text-[11px] px-2 py-0.5 rounded-full border transition-colors ${
                            activeMode === 'safe_rule'
                              ? 'border-amber-300/60 bg-amber-500/20 text-amber-100'
                              : 'border-amber-400/20 bg-amber-500/5 text-amber-300/55 hover:text-amber-100 hover:border-amber-300/50'
                          } disabled:cursor-default`}
                        >
                          rule lv{safeRuleLevel(scene)}
                        </button>
                      )}
                      {llmReady && (
                        <button
                          type="button"
                          onClick={() => handleSetActivePromptMode(scene, 'safe_llm')}
                          disabled={activeMode === 'safe_llm' || generating || safeApplying || !!safeApplyingSceneId}
                          className={`text-[11px] px-2 py-0.5 rounded-full border transition-colors ${
                            activeMode === 'safe_llm'
                              ? 'border-sky-300/60 bg-sky-500/20 text-sky-100'
                              : 'border-sky-400/20 bg-sky-500/5 text-sky-300/55 hover:text-sky-100 hover:border-sky-300/50'
                          } disabled:cursor-default`}
                        >
                          llm lv{safeLlmLevel(scene)}
                        </button>
                      )}
                      {scene.director_prompt && (
                        <button
                          type="button"
                          onClick={() => handleSetActivePromptMode(scene, 'director')}
                          disabled={activeMode === 'director' || generating || safeApplying || !!safeApplyingSceneId}
                          className={`text-[11px] px-2 py-0.5 rounded-full border transition-colors ${
                            activeMode === 'director'
                              ? 'border-sky-300/60 bg-sky-500/20 text-sky-100'
                              : 'border-sky-400/20 bg-sky-500/5 text-sky-300/55 hover:text-sky-100 hover:border-sky-300/50'
                          } disabled:cursor-default`}
                        >
                          director
                        </button>
                      )}
                      <span className={`text-[11px] px-2 py-0.5 rounded-full border ${
                        displayStatus === 'succeeded' ? 'text-green-300 border-green-500/30 bg-green-500/10'
                          : displayStatus === 'failed' ? 'text-red-300 border-red-500/30 bg-red-500/10'
                            : displayStatus === 'running' ? 'text-accent-300 border-accent-500/30 bg-accent-500/10'
                              : 'text-slate-400 border-surface-500 bg-surface-800'
                      }`}>
                        {displayStatus || 'pending'}
                      </span>
                    </div>
                  </div>
                  <p className="text-xs text-slate-400 line-clamp-3">{scene.text}</p>
                  {scene.storyboard && (
                    <div className="rounded-lg border border-surface-700 bg-surface-950/50 px-2 py-1.5 text-xs text-slate-300 space-y-1">
                      <p className="line-clamp-2">
                        <span className="font-semibold text-sky-200">Storyboard:</span>{' '}
                        {scene.storyboard.visual_moment || scene.storyboard.visual_goal || scene.storyboard.prompt || 'Director scene'}
                      </p>
                      {!!scene.reference_ids?.length && (
                        <p className="text-slate-500">Refs: {scene.reference_ids.join(', ')}</p>
                      )}
                    </div>
                  )}
                  {(ruleReady || llmReady) && scene.status === 'failed' && (
                    <p className="text-xs text-amber-300">
                      Retry tiếp theo sẽ dùng {
                        activeMode === 'safe_llm' ? `safe LLM lv${safeLlmLevel(scene)}`
                          : activeMode === 'safe_rule' ? `safe rule lv${safeRuleLevel(scene)}`
                            : activeMode === 'director' ? 'director prompt'
                            : 'prompt gốc'
                      }.
                    </p>
                  )}
                  {scene.status === 'running' && (
                    <p className="text-xs text-accent-300">
                      {scene.task_status === 'retry_wait'
                        ? `Chờ retry ${scene.retry_after_sec ? `${scene.retry_after_sec}s` : ''}`
                        : `${generationLabel} ${estimateSceneProgress(scene)}%`}
                      {scene.attempt ? ` · lần ${scene.attempt}/3` : ''}
                      {scene.task_id ? ` · ${scene.task_id}` : ''}
                    </p>
                  )}
                  {hasNewVariant && !sceneBusy && (
                    <p className="flex items-center gap-1.5 text-xs text-emerald-300">
                      <CheckCircle2 size={13} />
                      Đã tạo biến thể mới · mở Lịch sử ({historyCount}) để xem hoặc chọn ảnh dùng cho video.
                    </p>
                  )}
                  {scene.error && <p className="text-xs text-red-400">{scene.error}</p>}
                  {lastRetryError && (
                    <p className="text-xs text-amber-300">
                      Retry gần nhất lỗi: {lastRetryError}
                    </p>
                  )}
                  {scene.image_file && (
                    <div className="space-y-2 rounded-lg border border-surface-700 bg-surface-950/45 p-2.5">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-semibold text-slate-300">Hiệu ứng scene</span>
                        {revealUpdating && <Loader2 size={13} className="animate-spin text-accent-300" />}
                      </div>
                      <select
                        value={sceneRevealMode}
                        disabled={revealUpdating}
                        onChange={event => setSceneRevealMode(scene, event.target.value)}
                        className="w-full rounded-md border border-surface-600 bg-surface-900 px-2 py-1.5 text-xs text-white focus:border-accent-500 focus:outline-none disabled:opacity-50"
                      >
                        <option value="inherit">Kế thừa cấu hình video</option>
                        <option value="off">Tắt hiệu ứng ở scene này</option>
                        <option value="custom">Tùy chỉnh scene này</option>
                      </select>
                      {sceneRevealMode === 'custom' && (
                        <div className="grid grid-cols-2 gap-2">
                          <label className="space-y-1">
                            <span className="text-[11px] text-slate-500">Kiểu</span>
                            <select value={sceneReveal.effect} disabled={revealUpdating} onChange={event => updateSceneRevealOverride(scene, { effect: event.target.value })} className="w-full rounded-md border border-surface-600 bg-surface-900 px-2 py-1.5 text-xs text-white focus:border-accent-500 focus:outline-none">
                              <option value="brush">Nét cọ</option>
                              <option value="pencil">Bút chì → màu</option>
                            </select>
                          </label>
                          <label className="space-y-1">
                            <span className="text-[11px] text-slate-500">Thời lượng</span>
                            <input type="number" min="0.4" max="3" step="0.1" value={sceneReveal.durationSec} disabled={revealUpdating} onChange={event => updateSceneRevealOverride(scene, { durationSec: event.target.value })} className="w-full rounded-md border border-surface-600 bg-surface-900 px-2 py-1.5 text-xs text-white focus:border-accent-500 focus:outline-none" />
                          </label>
                          <label className="space-y-1">
                            <span className="text-[11px] text-slate-500">Hướng nét</span>
                            <select value={sceneReveal.pattern} disabled={revealUpdating} onChange={event => updateSceneRevealOverride(scene, { pattern: event.target.value })} className="w-full rounded-md border border-surface-600 bg-surface-900 px-2 py-1.5 text-xs text-white focus:border-accent-500 focus:outline-none">
                              <option value="auto">Tự động</option>
                              <option value="left_to_right">Trái → phải</option>
                              <option value="right_to_left">Phải → trái</option>
                              <option value="top_to_bottom">Trên → dưới</option>
                              <option value="diagonal">Chéo</option>
                              <option value="scribble">Nguệch ngoạc</option>
                            </select>
                          </label>
                          <label className="space-y-1">
                            <span className="text-[11px] text-slate-500">Vật thể</span>
                            <select value={sceneReveal.actor} disabled={revealUpdating} onChange={event => updateSceneRevealOverride(scene, { actor: event.target.value })} className="w-full rounded-md border border-surface-600 bg-surface-900 px-2 py-1.5 text-xs text-white focus:border-accent-500 focus:outline-none">
                              <option value="none">Không có</option>
                              <option value="hand">Tay 2D</option>
                              <option value="pencil">Bút chì</option>
                              <option value="brush">Cọ mực</option>
                            </select>
                          </label>
                        </div>
                      )}
                    </div>
                  )}
                  <div className="flex flex-wrap gap-2">
                    <button
                      onClick={() => openReview(scene)}
                      disabled={!scene.image_file && historyCount === 0}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold border border-surface-500 text-slate-300 hover:text-white hover:border-accent-400 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      <Images size={13} />
                      Lịch sử {historyCount ? `(${historyCount})` : ''}
                    </button>
                    <button
                      onClick={() => openEdit(scene)}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold border border-surface-500 text-slate-300 hover:text-white hover:border-accent-400"
                    >
                      <Pencil size={13} />
                      Prompt
                    </button>
                    {scene.director_prompt && (
                      <button
                        onClick={() => handleRegenerateDirectorPrompt(scene)}
                        disabled={loading || generating}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold border border-surface-500 text-slate-300 hover:text-white hover:border-sky-400 disabled:opacity-40"
                      >
                        {loading ? <Loader2 size={13} className="animate-spin" /> : <WandSparkles size={13} />}
                        AI prompt
                      </button>
                    )}
                    <button
                      onClick={() => handleApplySafePromptForScene(scene)}
                      disabled={generating || safeApplying || !!safeApplyingSceneId}
                      className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold border border-amber-400/35 text-amber-200 hover:text-amber-100 hover:border-amber-300 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      {safeSceneBusy ? <Loader2 size={13} className="animate-spin" /> : <WandSparkles size={13} />}
                      {safeSceneBusy ? 'Đang safe' : `Safe ${safeMode === 'llm' ? 'LLM' : 'Rule'}`}
                    </button>
                    {sceneBusy ? (
                      <button
                        onClick={handleCancel}
                        disabled={cancelling}
                        title="Dừng toàn bộ ảnh đang tạo hoặc retry trong project này"
                        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold border border-red-500/40 text-red-300 hover:text-red-200 disabled:opacity-40"
                      >
                        {cancelling ? <Loader2 size={13} className="animate-spin" /> : <StopCircle size={13} />}
                        {cancelling ? 'Đang dừng' : 'Dừng retry'}
                      </button>
                    ) : (
                      <button
                        onClick={() => handleGenerate([scene.id], true)}
                        disabled={generating || referenceGenerating || visualPlanNeedsApproval}
                        title={visualPlanNeedsApproval ? 'Hãy duyệt Visual Plan trước khi retry ảnh' : 'Tạo lại ảnh cho scene này'}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-semibold border border-surface-500 text-slate-300 hover:text-white hover:border-accent-400 disabled:opacity-40"
                      >
                        <RefreshCw size={13} />
                        {visualPlanNeedsApproval ? 'Duyệt plan trước' : 'Retry'}
                      </button>
                    )}
                  </div>
                </div>
              </div>
              )
            })}
          </div>
        </div>
        </>
      )}

      {reviewing && (
        <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="w-full max-w-6xl max-h-[90vh] rounded-2xl border border-surface-500 bg-surface-900 shadow-2xl overflow-hidden flex flex-col">
            <div className="flex items-start justify-between gap-3 border-b border-surface-600 px-5 py-4">
              <div>
                <h3 className="text-lg font-bold text-white">Review ảnh {reviewing.id}</h3>
                <p className="text-xs text-slate-500">
                  {fmtSec(reviewing.start_sec)}-{fmtSec(reviewing.end_sec)} · chọn ảnh sẽ dùng khi dựng video từ scene.
                </p>
              </div>
              <button
                onClick={() => setReviewing(null)}
                className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-surface-500 text-slate-400 hover:text-white hover:border-accent-400"
                aria-label="Đóng"
              >
                <X size={16} />
              </button>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px] gap-0 min-h-0">
              <div className="p-5 space-y-4 min-h-0 overflow-y-auto">
                {reviewing.image_file ? (
                  <img
                    src={sceneImageUrl(project, reviewing.image_file)}
                    alt={`${reviewing.id} selected`}
                    className="w-full aspect-video object-contain rounded-xl border border-surface-600 bg-surface-950"
                  />
                ) : (
                  <div className="w-full aspect-video rounded-xl border border-surface-600 bg-surface-950 flex items-center justify-center text-slate-600">
                    <ImagePlus size={32} />
                  </div>
                )}
                <div className="rounded-xl border border-surface-600 bg-surface-950 p-4 space-y-2">
                  <p className="text-xs font-semibold text-slate-300">Nội dung scene</p>
                  <p className="text-sm text-slate-400 leading-relaxed">{reviewing.text || 'Chưa có nội dung scene.'}</p>
                </div>
              </div>

              <div className="border-t lg:border-t-0 lg:border-l border-surface-600 p-4 space-y-3 min-h-0 overflow-y-auto bg-surface-950/40">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-semibold text-white">Lịch sử ảnh</p>
                  <span className="text-xs text-slate-500">{sceneImageHistory(reviewing).length} ảnh</span>
                </div>

                {sceneImageHistory(reviewing).length === 0 ? (
                  <div className="rounded-xl border border-surface-600 bg-surface-900 p-4 text-sm text-slate-400">
                    Chưa có ảnh nào cho scene này.
                  </div>
                ) : (
                  <div className="space-y-3">
                    {sceneImageHistory(reviewing).map((item, index) => {
                      const selected = item.image_file === reviewing.image_file
                      return (
                        <div
                          key={item.image_file}
                          className={`rounded-xl border overflow-hidden bg-surface-900 ${
                            selected ? 'border-accent-400' : 'border-surface-600'
                          }`}
                        >
                          <img
                            src={sceneImageUrl(project, item.image_file)}
                            alt={`${reviewing.id} variant ${index + 1}`}
                            className="w-full aspect-video object-cover bg-surface-800"
                          />
                          <div className="p-3 space-y-2">
                            <div className="flex items-start justify-between gap-2">
                              <div className="min-w-0">
                                <p className="text-xs font-mono text-slate-300 truncate">{item.image_file}</p>
                                <p className="text-[11px] text-slate-500">
                                  {item.created_at || 'Không rõ thời gian'}{item.task_id ? ` · ${item.task_id}` : ''}
                                </p>
                              </div>
                              {selected && (
                                <span className="shrink-0 text-[11px] px-2 py-0.5 rounded-full border border-green-500/30 bg-green-500/10 text-green-300">
                                  Đang chọn
                                </span>
                              )}
                            </div>
                            <button
                              onClick={() => handleSelectImage(reviewing, item.image_file)}
                              disabled={selected || selectingImage === item.image_file}
                              className="w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs font-semibold bg-accent-600 hover:bg-accent-500 text-white disabled:bg-surface-700 disabled:text-slate-500 disabled:cursor-not-allowed"
                            >
                              {selectingImage === item.image_file ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
                              {selected ? 'Đang dùng cho video' : 'Chọn ảnh này'}
                            </button>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {editing && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="w-full max-w-3xl rounded-2xl border border-surface-500 bg-surface-900 p-5 space-y-4 shadow-2xl">
            <div>
              <h3 className="text-lg font-bold text-white">Sửa prompt {editing.id}</h3>
              <p className="text-xs text-slate-500">
                {activePromptMode(editing) === 'director'
                  ? 'Đang sửa Director prompt; storyboard/reference vẫn được giữ lại.'
                  : activePromptMode(editing) === 'safe_llm'
                  ? `Đang sửa safe LLM lv${safeLlmLevel(editing)}; prompt gốc vẫn được giữ lại.`
                  : activePromptMode(editing) === 'safe_rule'
                    ? `Đang sửa safe rule lv${safeRuleLevel(editing)}; prompt gốc vẫn được giữ lại.`
                  : 'Prompt này sẽ dùng cho lần tạo tiếp theo.'}
              </p>
            </div>
            {activePromptMode(editing) !== 'original' && editing.original_prompt && (
              <details className="rounded-xl border border-surface-600 bg-surface-950/60 p-3">
                <summary className="cursor-pointer text-xs font-semibold text-slate-300">Xem prompt gốc</summary>
                <p className="mt-2 max-h-32 overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-slate-500">
                  {editing.original_prompt}
                </p>
              </details>
            )}
            <textarea
              value={draftPrompt}
              onChange={e => setDraftPrompt(e.target.value)}
              rows={12}
              className="scene-prompt-editor w-full rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-accent-500"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setEditing(null)}
                className="px-4 py-2 rounded-xl text-sm font-semibold border border-surface-500 text-slate-300 hover:text-white"
              >
                Hủy
              </button>
              <button
                onClick={savePrompt}
                className="px-4 py-2 rounded-xl text-sm font-semibold bg-accent-600 hover:bg-accent-500 text-white"
              >
                Lưu prompt
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

