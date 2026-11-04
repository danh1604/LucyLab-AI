import AppSelect from './AppSelect.jsx'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  Award,
  Check,
  CheckCircle2,
  ChevronLeft,
  Clock3,
  Download,
  Eye,
  History,
  Image,
  Lock,
  Loader2,
  Maximize2,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Square,
  Star,
  Wand2,
} from 'lucide-react'
import {
  addCustomThumbnailDirectorConcept,
  addThumbnailProfileReference,
  approveThumbnailDirectorConcepts,
  cancelThumbnailDirectorRun,
  createThumbnailDirectorRun,
  getThumbnailDirectorRun,
  createThumbnailProfile,
  getProjectSelectedThumbnail,
  listThumbnailDirectorRuns,
  listThumbnailProfiles,
  regenerateThumbnailDirectorConcepts,
  retryThumbnailDirectorCandidate,
  selectStickFigurePackagingTitle,
  selectProjectThumbnail,
  setThumbnailProfilePrimary,
  startThumbnailDirectorEvents,
  thumbnailProfileReferenceUrl,
  thumbnailUrl,
  upscaleThumbnailDirectorCandidate,
} from '../api.js'
import { useDialog } from './DialogProvider.jsx'

const FORMULAS = [
  { value: 'mystery', label: 'Bí ẩn' },
  { value: 'warning', label: 'Cảnh báo' },
  { value: 'secret', label: 'Sự thật bị giấu' },
  { value: 'entity', label: 'Thứ phía sau' },
]

const ACTIVE_RUN_STATES = new Set(['queued', 'planning', 'generating'])
const ACTIVE_CANDIDATE_STATES = new Set(['queued', 'generating', 'upscaling', 'evaluating', 'ocr_retry'])

function statusLabel(status) {
  if (status === 'queued') return 'Đang chờ'
  if (status === 'planning') return 'AI đang lên concept'
  if (status === 'awaiting_approval') return 'Chờ duyệt concept'
  if (status === 'generating') return 'Đang tạo ảnh'
  if (status === 'upscaling') return 'GPU đang tăng nét'
  if (status === 'evaluating') return 'Vision đang kiểm tra'
  if (status === 'ocr_retry') return 'Đang sửa chữ'
  if (status === 'succeeded') return 'Đã tạo'
  if (status === 'needs_review') return 'Cần xem lại'
  if (status === 'failed') return 'Lỗi'
  if (status === 'cancelled') return 'Đã dừng'
  if (status === 'interrupted') return 'Bị gián đoạn'
  if (status === 'completed_with_errors') return 'Hoàn thành có lỗi'
  if (status === 'completed') return 'Hoàn thành'
  return status || 'Chưa chạy'
}

function statusClass(status) {
  if (status === 'succeeded' || status === 'completed') return 'border-emerald-500/35 bg-emerald-500/10 text-emerald-200'
  if (status === 'needs_review' || status === 'completed_with_errors') return 'border-amber-500/35 bg-amber-500/10 text-amber-200'
  if (status === 'failed' || status === 'interrupted') return 'border-red-500/35 bg-red-500/10 text-red-200'
  if (ACTIVE_RUN_STATES.has(status) || ACTIVE_CANDIDATE_STATES.has(status)) return 'border-accent-500/35 bg-accent-500/10 text-accent-200'
  return 'border-surface-500 bg-surface-800 text-slate-300'
}

function scoreClass(score) {
  if (score >= 85) return 'text-emerald-300'
  if (score >= 70) return 'text-amber-300'
  return 'text-red-300'
}

function hasValidVisionRubric(vision) {
  const scores = vision?.scores
  const keys = ['text_fidelity', 'small_view_readability', 'visual_intrigue', 'story_relevance', 'brand_consistency', 'spoiler_safety']
  return vision?.status === 'completed'
    && typeof vision?.required_text_ok === 'boolean'
    && keys.every(key => Number.isFinite(Number(scores?.[key])))
}

function visionState(vision) {
  if (hasValidVisionRubric(vision)) return { label: `VLM ${vision.score}/100`, tone: scoreClass(vision.score) }
  if (vision?.status === 'skipped') return { label: 'VLM đã tắt', tone: 'text-slate-400' }
  if (vision?.status === 'unconfigured') return { label: 'VLM chưa cấu hình', tone: 'text-amber-300' }
  if (vision?.status === 'failed') return { label: 'VLM lỗi', tone: 'text-red-300' }
  if (vision?.status === 'invalid') return { label: 'Không đánh giá được', tone: 'text-amber-300' }
  if (vision?.status === 'completed') return { label: 'Không đánh giá được', tone: 'text-amber-300' }
  return { label: 'Chưa đánh giá', tone: 'text-slate-500' }
}

function ConceptCandidate({ project, run, candidate, selectedFilename, profile, onRetry, onUpscale, onSelect, onAddProfile, upscaleMode, busy }) {
  const running = ACTIVE_CANDIDATE_STATES.has(candidate.status)
  const vision = candidate.vision || {}
  const visionResult = visionState(vision)
  const issues = Array.isArray(vision.text_issues) ? vision.text_issues : []
  const isRecommended = run.recommended_candidate_id === candidate.id
  const isSelected = selectedFilename && selectedFilename === candidate.filename

  return (
    <article className={`relative overflow-hidden rounded-xl border bg-[#0d0d15] transition-colors ${isSelected ? 'border-emerald-400/60' : isRecommended ? 'border-accent-400/60' : 'border-surface-600'} ${running ? 'scene-card-running' : ''}`}>
      <div className="relative aspect-video bg-black/35 overflow-hidden">
        {candidate.filename ? (
          <img
            src={`${thumbnailUrl(project, candidate.filename)}?t=${encodeURIComponent([
              run.updated_at,
              candidate.updated_at,
              candidate.completed_at,
              candidate.progress,
              candidate.attempts?.length,
              candidate.filename,
            ].filter(Boolean).join('|'))}`}
            alt={`${candidate.headline} - biến thể ${candidate.variant}`}
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="h-full w-full flex flex-col items-center justify-center gap-3 text-slate-600">
            {running ? <Loader2 size={28} className="animate-spin text-accent-400" /> : <Image size={30} />}
            <span className="text-xs font-medium">{statusLabel(candidate.status)}</span>
          </div>
        )}
        {running && <div className="scene-loading-sheen pointer-events-none" />}
        {running && candidate.filename && (
          <div className="absolute inset-0 flex items-center justify-center bg-slate-950/42 pointer-events-none">
            <span className="inline-flex items-center gap-2 rounded-full border border-accent-300/35 bg-slate-950/85 px-3 py-1.5 text-xs font-semibold text-accent-100 shadow-lg">
              <Loader2 size={14} className="animate-spin" />
              Đang tạo lại ảnh
            </span>
          </div>
        )}
        <div className="absolute left-3 top-3 flex items-center gap-2">
          <span className={`rounded-md border px-2 py-1 text-[11px] font-semibold ${statusClass(candidate.status)}`}>
            {statusLabel(candidate.status)}
          </span>
          {isRecommended && (
            <span className="inline-flex items-center gap-1 rounded-md border border-accent-400/40 bg-black/75 px-2 py-1 text-[11px] font-semibold text-accent-200">
              <Award size={12} /> Đề xuất
            </span>
          )}
          {isSelected && (
            <span className="inline-flex items-center gap-1 rounded-md border border-emerald-400/40 bg-black/75 px-2 py-1 text-[11px] font-semibold text-emerald-200">
              <Check size={12} /> Đang dùng
            </span>
          )}
        </div>
        {hasValidVisionRubric(vision) && typeof candidate.score === 'number' && (
          <div className="absolute right-3 top-3 rounded-md border border-black/50 bg-black/80 px-2 py-1">
            <span className={`font-mono text-sm font-bold ${scoreClass(candidate.score)}`}>{candidate.score}</span>
            <span className="text-[10px] text-slate-400"> / 100</span>
          </div>
        )}
        {running && (
          <div className="absolute inset-x-0 bottom-0 h-1 bg-black/40">
            <div className="h-full bg-accent-500 transition-[width] duration-200" style={{ width: `${candidate.progress || 3}%` }} />
          </div>
        )}
      </div>

      <div className="p-3 space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-white text-pretty">{candidate.headline}</p>
            <p className="mt-1 text-xs text-slate-500">
              Biến thể {candidate.variant} · {candidate.reference_files?.length || 0} reference
              {candidate.width ? ` · ${candidate.width}×${candidate.height}` : ''}
              {candidate.upscale_engine ? ` · ${candidate.upscale_engine}` : ''}
            </p>
          </div>
          {candidate.attempts?.length > 1 && (
            <span className="shrink-0 inline-flex items-center gap-1 text-[11px] text-slate-400">
              <History size={12} /> {candidate.attempts.length} lần
            </span>
          )}
        </div>

        <div className="flex items-center gap-2 text-xs">
          <Eye size={13} className={visionResult.tone} />
          <span className="text-slate-400">Đánh giá:</span>
          <span className={`font-medium ${visionResult.tone}`}>{visionResult.label}</span>
        </div>

        {vision.status === 'unconfigured' && (
          <div className="flex items-start gap-2 rounded-lg border border-amber-500/25 bg-amber-500/8 px-2.5 py-2 text-xs text-amber-200">
            <Eye size={13} className="mt-0.5 shrink-0" /> Vision chưa cấu hình; ảnh chưa được OCR và chấm điểm.
          </div>
        )}
        {(vision.status === 'invalid' || vision.status === 'failed') && (
          <div className="flex items-start gap-2 rounded-lg border border-amber-500/25 bg-amber-500/8 px-2.5 py-2 text-xs text-amber-100">
            <AlertCircle size={13} className="mt-0.5 shrink-0 text-amber-300" />
            <span>{vision.message || 'Vision không trả về đầy đủ rubric nên ảnh này chưa được đánh giá.'}</span>
          </div>
        )}
        {issues.length > 0 && (
          <div className="rounded-lg border border-amber-500/25 bg-amber-500/8 px-2.5 py-2">
            <p className="text-xs font-semibold text-amber-200">Vision phát hiện</p>
            <p className="mt-1 text-xs text-amber-100/80 text-pretty">{issues.join(' · ')}</p>
          </div>
        )}
        {candidate.error && (
          <div className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-2.5 py-2 text-xs text-red-200">
            <AlertCircle size={13} className="mt-0.5 shrink-0" />
            <span className="break-words min-w-0">{candidate.error}</span>
          </div>
        )}

        <details className="group rounded-lg border border-surface-700 bg-black/20 px-2.5 py-2">
          <summary className="cursor-pointer select-none text-xs font-medium text-slate-400 hover:text-white">Prompt và đánh giá</summary>
          <div className="mt-2 space-y-2 text-xs text-slate-400">
            {vision.summary && <p className="text-pretty">{vision.summary}</p>}
            <p className="max-h-28 overflow-y-auto whitespace-pre-wrap text-slate-500">{candidate.prompt}</p>
          </div>
        </details>

        <div className="flex flex-wrap gap-2">
          {candidate.filename && (
            <>
              <button
                type="button"
                onClick={() => onSelect(candidate)}
                disabled={isSelected}
                className="inline-flex items-center gap-1.5 rounded-lg bg-accent-600 px-2.5 py-2 text-xs font-semibold text-white hover:bg-accent-500 disabled:opacity-45"
              >
                <CheckCircle2 size={13} /> {isSelected ? 'Đã chọn' : 'Chọn ảnh'}
              </button>
              <a
                href={thumbnailUrl(project, candidate.filename)}
                download={candidate.filename}
                className="inline-flex items-center gap-1.5 rounded-lg border border-surface-500 px-2.5 py-2 text-xs font-semibold text-slate-300 hover:border-accent-400 hover:text-white"
              >
                <Download size={13} /> Tải ảnh
              </a>
              {upscaleMode !== 'original' && (
                <button
                  type="button"
                  onClick={() => onUpscale(candidate)}
                  disabled={running || Boolean(busy) || candidate.upscale_mode === upscaleMode}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-surface-500 px-2.5 py-2 text-xs font-semibold text-slate-300 hover:border-accent-400 hover:text-white disabled:opacity-40"
                >
                  {busy === `upscale:${candidate.id}` ? <Loader2 size={13} className="animate-spin" /> : <Maximize2 size={13} />}
                  {candidate.upscale_mode === upscaleMode ? `Đã ${upscaleMode.toUpperCase()}` : `Tăng nét ${upscaleMode.toUpperCase()}`}
                </button>
              )}
            </>
          )}
          <button
            type="button"
            onClick={() => onRetry(candidate, 'original')}
            disabled={running}
            className="inline-flex items-center gap-1.5 rounded-lg border border-surface-500 px-2.5 py-2 text-xs font-semibold text-slate-300 hover:border-accent-400 hover:text-white disabled:opacity-40"
          >
            <RefreshCw size={13} /> Retry
          </button>
          <button
            type="button"
            onClick={() => onRetry(candidate, 'safe_rule')}
            disabled={running}
            className="inline-flex items-center gap-1.5 rounded-lg border border-surface-500 px-2.5 py-2 text-xs font-semibold text-slate-300 hover:border-amber-400 hover:text-white disabled:opacity-40"
          >
            <ShieldCheck size={13} /> Safe Rule
          </button>
          <button
            type="button"
            onClick={() => onRetry(candidate, 'safe_llm')}
            disabled={running}
            className="inline-flex items-center gap-1.5 rounded-lg border border-surface-500 px-2.5 py-2 text-xs font-semibold text-slate-300 hover:border-amber-400 hover:text-white disabled:opacity-40"
          >
            <Wand2 size={13} /> Safe LLM
          </button>
          {candidate.filename && profile && (
            <button
              type="button"
              onClick={() => onAddProfile(candidate)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-surface-500 px-2.5 py-2 text-xs font-semibold text-slate-300 hover:border-accent-400 hover:text-white"
            >
              <Star size={13} /> Thêm vào profile
            </button>
          )}
        </div>
      </div>
    </article>
  )
}

export default function ThumbnailDirectorPanel({ project, storyTitle, storyText, sessionId = '', outputLocale = 'vi-VN', onQuick }) {
  const { notify } = useDialog()
  const stopStreamRef = useRef(null)
  const activeScopeRef = useRef(`${project}:${sessionId}:${storyTitle}`)
  const [channelName, setChannelName] = useState(() => localStorage.getItem('lucylab_thumbnail_channel') || '')
  const [headline, setHeadline] = useState(storyTitle || project || '')
  const [hook, setHook] = useState('')
  const [genreLabel, setGenreLabel] = useState('')
  const [formula, setFormula] = useState('mystery')
  const [layoutMode, setLayoutMode] = useState(() => localStorage.getItem('lucylab_thumbnail_layout_mode') === 'channel_format' ? 'channel_format' : 'free')
  const [lockHeadline, setLockHeadline] = useState(false)
  const [visionEnabled, setVisionEnabled] = useState(() => localStorage.getItem('lucylab_thumbnail_vision_enabled') !== 'false')
  const [upscaleMode, setUpscaleMode] = useState(() => localStorage.getItem('lucylab_thumbnail_upscale_mode') || '2k')
  const [concurrency, setConcurrency] = useState(6)
  const [profiles, setProfiles] = useState([])
  const [profileId, setProfileId] = useState('')
  const [runs, setRuns] = useState([])
  const [run, setRun] = useState(null)
  const [selected, setSelected] = useState(null)
  const [creating, setCreating] = useState(false)
  const [actionBusy, setActionBusy] = useState('')
  const [approvedConceptIds, setApprovedConceptIds] = useState([])
  const [customConceptHeadline, setCustomConceptHeadline] = useState('')
  const [packagingBusy, setPackagingBusy] = useState(false)
  const [error, setError] = useState('')

  const profile = useMemo(() => profiles.find(item => item.id === profileId) || null, [profiles, profileId])
  const candidatesByConcept = useMemo(() => {
    const map = new Map()
    for (const candidate of run?.candidates || []) {
      const items = map.get(candidate.concept_id) || []
      items.push(candidate)
      map.set(candidate.concept_id, items)
    }
    return map
  }, [run])
  const visibleConcepts = useMemo(() => {
    const concepts = run?.concepts || []
    // After approval, an unselected concept has intentionally received no
    // candidate jobs. Do not render it as a false "preparing variant" card.
    if (run?.status === 'awaiting_approval') return concepts
    const approved = new Set(run?.approved_concept_ids || [])
    if (approved.size) return concepts.filter(concept => approved.has(concept.id))
    // Legacy runs did not persist approved_concept_ids; retain their display.
    return concepts
  }, [run])
  const hasLiveCandidate = useMemo(
    () => (run?.candidates || []).some(candidate => ACTIVE_CANDIDATE_STATES.has(candidate.status)),
    [run],
  )

  function connectStream(runId) {
    const streamProject = project
    const streamScope = `${streamProject}:${sessionId}:${storyTitle}`
    stopStreamRef.current?.()
    stopStreamRef.current = startThumbnailDirectorEvents(
      streamProject,
      runId,
      event => {
        if (activeScopeRef.current !== streamScope) return
        if (event.type === 'snapshot') setRun(event.run)
        if (event.type === 'error') setError(event.message || 'Mất kết nối tiến trình Director')
      },
      () => {
        if (activeScopeRef.current !== streamScope) return
        stopStreamRef.current = null
        refreshRuns(streamProject)
        getProjectSelectedThumbnail(streamProject).then(data => {
          if (activeScopeRef.current === streamScope) setSelected(data)
        }).catch(() => {})
      },
      e => {
        if (activeScopeRef.current === streamScope) setError(e?.message || String(e))
      },
    )
  }

  async function refreshRuns(targetProject = project, targetSessionId = sessionId, targetStoryTitle = storyTitle, { selectFirst = false } = {}) {
    const targetScope = `${targetProject}:${targetSessionId}:${targetStoryTitle}`
    if (!targetProject) {
      setRuns([])
      setRun(null)
      return
    }
    try {
      const data = await listThumbnailDirectorRuns(targetProject, targetSessionId, targetStoryTitle)
      if (activeScopeRef.current !== targetScope) return
      const nextRuns = data.runs || []
      setRuns(nextRuns)
      setRun(previous => {
        if (selectFirst) return nextRuns[0] || null
        return nextRuns.find(item => item.id === previous?.id) || nextRuns[0] || null
      })
      setError('')
    } catch (e) {
      if (activeScopeRef.current === targetScope) setError(e?.message || String(e))
    }
  }

  async function refreshProfiles() {
    try {
      const data = await listThumbnailProfiles()
      setProfiles(data.profiles || [])
      setProfileId(current => current || data.profiles?.find(item => item.channel_name === channelName)?.id || '')
    } catch {
      setProfiles([])
    }
  }

  useEffect(() => {
    setHeadline(storyTitle || project || '')
  }, [project, storyTitle])

  useEffect(() => {
    activeScopeRef.current = `${project}:${sessionId}:${storyTitle}`
    stopStreamRef.current?.()
    setRuns([])
    setRun(null)
    setSelected(null)
    setError('')
    if (!project) return
    const targetProject = project
    const targetScope = `${targetProject}:${sessionId}:${storyTitle}`
    Promise.all([
      refreshRuns(targetProject, sessionId, storyTitle, { selectFirst: true }),
      refreshProfiles(),
      getProjectSelectedThumbnail(targetProject).then(data => {
        if (activeScopeRef.current === targetScope) setSelected(data)
      }).catch(() => {
        if (activeScopeRef.current === targetScope) setSelected(null)
      }),
    ])
    return () => stopStreamRef.current?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project, sessionId, storyTitle])

  const conceptKey = (run?.concepts || []).map(item => `${item.id}:${item.headline}`).join('|')
  useEffect(() => {
    if (run?.status === 'awaiting_approval') setApprovedConceptIds((run.concepts || []).map(item => item.id))
  // The set is reset only when Director gives a new set of concepts, never on polling snapshots.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.id, conceptKey, run?.status])

  useEffect(() => {
    if (run?.id && ACTIVE_RUN_STATES.has(run.status)) connectStream(run.id)
    return () => stopStreamRef.current?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.id, run?.status])

  // SSE supplies rich incremental updates. Polling is deliberately kept as a
  // lightweight safety net: a proxy/browser may close a stream while the image
  // job keeps running, and the completed asset must still appear without F5.
  useEffect(() => {
    if (!project || !run?.id || (!ACTIVE_RUN_STATES.has(run.status) && !hasLiveCandidate)) return
    let cancelled = false
    const refreshRun = () => {
      getThumbnailDirectorRun(project, run.id)
        .then(next => {
          if (!cancelled) setRun(next)
        })
        .catch(() => {})
    }
    refreshRun()
    const timer = window.setInterval(refreshRun, 1800)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [project, run?.id, run?.status, hasLiveCandidate])

  async function handleCreate() {
    setError('')
    if (!project || !storyText?.trim()) {
      setError('Project chưa có toàn bộ nội dung truyện để Director phân tích.')
      return
    }
    setCreating(true)
    localStorage.setItem('lucylab_thumbnail_channel', channelName.trim())
    localStorage.setItem('lucylab_thumbnail_vision_enabled', String(visionEnabled))
    localStorage.setItem('lucylab_thumbnail_upscale_mode', upscaleMode)
    localStorage.setItem('lucylab_thumbnail_layout_mode', layoutMode)
    try {
      let chosenProfileId = profileId
      if (channelName.trim() && !chosenProfileId) {
        const createdProfile = await createThumbnailProfile(channelName.trim())
        chosenProfileId = createdProfile.id
        setProfiles(prev => [createdProfile, ...prev.filter(item => item.id !== createdProfile.id)])
        setProfileId(createdProfile.id)
      }
      const created = await createThumbnailDirectorRun({
        project,
        story_title: storyTitle || project,
        story_text: storyText,
        session_id: sessionId,
        target_locale: outputLocale,
        channel_name: channelName.trim(),
        headline_seed: headline.trim() || storyTitle || project,
        hook: hook.trim(),
        genre_label: genreLabel.trim(),
        formula,
        layout_mode: layoutMode,
        profile_id: chosenProfileId,
        lock_headline: lockHeadline,
        vision_enabled: visionEnabled,
        upscale_mode: upscaleMode,
        concurrency: Number(concurrency || 6),
      })
      setRun(created)
      setRuns(prev => [created, ...prev.filter(item => item.id !== created.id)])
      connectStream(created.id)
      notify({ tone: 'success', title: 'Thumbnail Director đã bắt đầu', message: 'AI đang phân tích toàn bộ truyện.' })
    } catch (e) {
      setError(e?.message || String(e))
    } finally {
      setCreating(false)
    }
  }

  async function handleSelectPackagingTitle(title) {
    if (!run?.packaging?.scope || !title || packagingBusy) return
    setPackagingBusy(true)
    setError('')
    try {
      const packaging = await selectStickFigurePackagingTitle(project, title)
      setRun(previous => previous ? { ...previous, packaging } : previous)
      notify({ tone: 'success', title: 'Đã chọn title SEO', message: 'Title này sẽ được dùng cho video dài Người que khi xuất bản.' })
    } catch (e) {
      setError(e?.message || String(e))
    } finally {
      setPackagingBusy(false)
    }
  }

  async function handleApproveConcepts() {
    if (!run?.id || !approvedConceptIds.length) return
    setActionBusy('approve-concepts')
    setError('')
    try {
      const next = await approveThumbnailDirectorConcepts(project, run.id, approvedConceptIds)
      setRun(next)
      connectStream(run.id)
      notify({ tone: 'success', title: 'Đã chốt concept', message: `Đang tạo ${approvedConceptIds.length * 2} ảnh cho các concept đã chọn.` })
    } catch (e) {
      setError(e?.message || String(e))
    } finally {
      setActionBusy('')
    }
  }

  async function handleRegenerateUnselectedConcepts() {
    if (!run?.id) return
    setActionBusy('regenerate-concepts')
    setError('')
    try {
      const next = await regenerateThumbnailDirectorConcepts(project, run.id, approvedConceptIds)
      setRun(next)
      connectStream(run.id)
    } catch (e) {
      setError(e?.message || String(e))
    } finally {
      setActionBusy('')
    }
  }

  async function handleAddCustomConcept() {
    const headlineValue = customConceptHeadline.trim()
    if (!run?.id || !headlineValue) return
    setActionBusy('custom-concept')
    setError('')
    try {
      const next = await addCustomThumbnailDirectorConcept(project, run.id, headlineValue)
      setRun(next)
      setCustomConceptHeadline('')
      connectStream(run.id)
    } catch (e) {
      setError(e?.message || String(e))
    } finally {
      setActionBusy('')
    }
  }

  async function handleRetryRun() {
    if (!run || runActive) return
    // A Director run persists its original request. Retrying it should be
    // reproducible and must not silently inherit any form edits made later.
    const previousRequest = run.request && typeof run.request === 'object' ? run.request : null
    if (!previousRequest) {
      handleCreate()
      return
    }
    setError('')
    setCreating(true)
    try {
      const created = await createThumbnailDirectorRun({
        ...previousRequest,
        project,
        session_id: sessionId,
        target_locale: previousRequest.target_locale || outputLocale || 'vi-VN',
        story_title: previousRequest.story_title || storyTitle || project,
        story_text: previousRequest.story_text || storyText,
      })
      setRun(created)
      setRuns(prev => [created, ...prev.filter(item => item.id !== created.id)])
      connectStream(created.id)
      notify({ tone: 'success', title: 'Đã retry Thumbnail Director', message: 'Đang chạy lại với đúng cấu hình của run vừa lỗi.' })
    } catch (e) {
      setError(e?.message || String(e))
    } finally {
      setCreating(false)
    }
  }

  async function handleCancel() {
    if (!run?.id) return
    try {
      setRun(await cancelThumbnailDirectorRun(project, run.id))
    } catch (e) {
      setError(e?.message || String(e))
    }
  }

  async function handleRetry(candidate, mode) {
    if (!run?.id) return
    const key = `${candidate.id}:${mode}`
    setActionBusy(key)
    setError('')
    try {
      const next = await retryThumbnailDirectorCandidate(project, run.id, candidate.id, mode)
      setRun(next)
      connectStream(run.id)
    } catch (e) {
      setError(e?.message || String(e))
    } finally {
      setActionBusy('')
    }
  }

  async function handleUpscale(candidate) {
    if (!run?.id || upscaleMode === 'original') return
    const key = `upscale:${candidate.id}`
    setActionBusy(key)
    setError('')
    try {
      const next = await upscaleThumbnailDirectorCandidate(project, run.id, candidate.id, upscaleMode)
      setRun(next)
      connectStream(run.id)
    } catch (e) {
      setError(e?.message || String(e))
    } finally {
      setActionBusy('')
    }
  }

  async function handleSelect(candidate) {
    setActionBusy(`select:${candidate.id}`)
    try {
      const data = await selectProjectThumbnail(project, candidate.filename)
      setSelected(data)
      const needsUpscale = upscaleMode !== 'original' && candidate.upscale_mode !== upscaleMode
      if (needsUpscale) {
        const next = await upscaleThumbnailDirectorCandidate(project, run.id, candidate.id, upscaleMode)
        setRun(next)
        connectStream(run.id)
        notify({ tone: 'success', title: 'Đã chọn thumbnail chính', message: `GPU đang tăng nét bản đã chọn lên ${upscaleMode.toUpperCase()}.` })
      } else {
        notify({ tone: 'success', title: 'Đã chọn thumbnail chính', message: candidate.filename })
      }
    } catch (e) {
      setError(e?.message || String(e))
    } finally {
      setActionBusy('')
    }
  }

  async function handleAddProfile(candidate) {
    let targetProfile = profile
    setActionBusy(`profile:${candidate.id}`)
    try {
      if (!targetProfile) {
        targetProfile = await createThumbnailProfile(channelName.trim() || 'Kênh mặc định')
        setProfileId(targetProfile.id)
      }
      const updated = await addThumbnailProfileReference(targetProfile.id, project, candidate.filename)
      setProfiles(prev => [updated, ...prev.filter(item => item.id !== updated.id)])
      notify({ tone: 'success', title: 'Đã thêm vào profile kênh', message: 'Ảnh sẽ được dùng làm style reference cho các dự án sau.' })
    } catch (e) {
      setError(e?.message || String(e))
    } finally {
      setActionBusy('')
    }
  }

  async function handlePrimary(referenceId) {
    if (!profile) return
    try {
      const updated = await setThumbnailProfilePrimary(profile.id, referenceId)
      setProfiles(prev => prev.map(item => item.id === updated.id ? updated : item))
    } catch (e) {
      setError(e?.message || String(e))
    }
  }

  const runActive = run && ACTIVE_RUN_STATES.has(run.status)
  const retryableRun = Boolean(run && !runActive && (
    run.error || ['failed', 'interrupted', 'completed_with_errors'].includes(run.status)
  ))
  const processed = (run?.candidates || []).filter(item => ['succeeded', 'needs_review', 'failed', 'cancelled'].includes(item.status)).length
  const expectedCandidates = (run?.approved_concept_ids?.length || 0) * 2
    || (run?.candidates?.length || 0)
    || ((run?.concepts?.length || 0) * 2)

  return (
    <section className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 sm:p-5 space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 flex h-9 w-9 items-center justify-center rounded-lg border border-accent-500/30 bg-accent-500/10 text-accent-300">
            <Sparkles size={17} />
          </div>
          <div>
            <h3 className="text-base font-bold text-white">Thumbnail Director</h3>
            <p className="text-xs text-slate-400">3 concept · 2 biến thể · VLM đánh giá tùy chọn</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onQuick}
            className="inline-flex items-center gap-1.5 rounded-lg border border-surface-500 px-3 py-2 text-xs font-semibold text-slate-300 hover:border-accent-400 hover:text-white"
          >
            <ChevronLeft size={14} /> Quick
          </button>
          <button
            type="button"
            onClick={() => { refreshRuns(); refreshProfiles() }}
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-surface-500 text-slate-400 hover:border-accent-400 hover:text-white"
            title="Làm mới dữ liệu Director"
          >
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 2xl:grid-cols-[22rem_minmax(0,1fr)] gap-5 items-start">
        <aside className="space-y-4 2xl:sticky 2xl:top-4">
          <div className="rounded-xl border border-surface-600 bg-black/20 p-4 space-y-3">
            <div className="grid grid-cols-1 gap-3">
              <label className="space-y-1">
                <span className="text-xs font-semibold text-slate-400">Tên kênh</span>
                <input value={channelName} onChange={e => setChannelName(e.target.value)} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500" placeholder="Da Thoại Audio" />
              </label>
              <label className="space-y-1">
                <span className="text-xs font-semibold text-slate-400">Headline gợi ý</span>
                <input value={headline} onChange={e => setHeadline(e.target.value)} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500" />
              </label>
              <div className="grid grid-cols-2 gap-2">
                <label className="space-y-1">
                  <span className="text-xs font-semibold text-slate-400">Hook optional</span>
                  <input value={hook} onChange={e => setHook(e.target.value)} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500" placeholder="Để trống" />
                </label>
                <label className="space-y-1">
                  <span className="text-xs font-semibold text-slate-400">Thể loại optional</span>
                  <input value={genreLabel} onChange={e => setGenreLabel(e.target.value)} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500" placeholder="Để trống" />
                </label>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <label className="space-y-1">
                  <span className="text-xs font-semibold text-slate-400">Góc click</span>
                  <AppSelect value={formula} onChange={e => setFormula(e.target.value)} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500">
                    {FORMULAS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
                  </AppSelect>
                </label>
                <label className="space-y-1">
                  <span className="text-xs font-semibold text-slate-400">Ảnh đồng thời</span>
                  <input type="number" min="1" value={concurrency} onChange={e => setConcurrency(Math.max(1, Number.parseInt(e.target.value, 10) || 1))} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500" />
                </label>
              </div>
              <fieldset className="space-y-2">
                <legend className="text-xs font-semibold text-slate-400">Chế độ bố cục</legend>
                <div className="grid grid-cols-2 rounded-lg border border-surface-500 bg-surface-900 p-1" role="radiogroup" aria-label="Chế độ bố cục thumbnail">
                  {[
                    { value: 'free', label: 'Linh hoạt', detail: 'AI tự chọn bố cục phù hợp từng concept.' },
                    { value: 'channel_format', label: 'Format kênh', detail: 'Trái chữ · phải ảnh · mép giấy xé.' },
                  ].map(option => (
                    <button
                      key={option.value}
                      type="button"
                      role="radio"
                      aria-checked={layoutMode === option.value}
                      onClick={() => setLayoutMode(option.value)}
                      className={`rounded-md px-2 py-2 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-300 ${layoutMode === option.value ? 'bg-accent-600 text-white' : 'text-slate-400 hover:bg-surface-800 hover:text-white'}`}
                    >
                      <span className="block text-xs font-semibold">{option.label}</span>
                      <span className={`mt-0.5 block text-[10px] leading-4 ${layoutMode === option.value ? 'text-accent-100' : 'text-slate-500'}`}>{option.detail}</span>
                    </button>
                  ))}
                </div>
                {layoutMode === 'channel_format' && (
                  <p className="rounded-lg border border-slate-500/70 bg-slate-800/55 px-3 py-2 text-[11px] leading-4 text-slate-300">
                    Khóa hệ nhận diện: panel chữ màu giấy kem–xám 38% bên trái, minh họa truyện 62% bên phải, mép chuyển giấy xé/blur nhẹ. Cả 3 concept dùng cùng màu/chữ/bố cục, nhưng mỗi concept có headline ngắn riêng. Chữ được ghép sau khi tạo ảnh nên không bị AI cắt hoặc viết sai.
                  </p>
                )}
              </fieldset>
              <div className="space-y-1">
                <span className="text-xs font-semibold text-slate-400">Độ nét ảnh chọn</span>
                <div className="grid grid-cols-3 rounded-lg border border-surface-500 bg-surface-900 p-1" role="radiogroup" aria-label="Độ nét thumbnail Director">
                  {[
                    { value: 'original', label: 'Gốc' },
                    { value: '2k', label: '2K' },
                    { value: '4k', label: '4K' },
                  ].map(option => (
                    <button
                      key={option.value}
                      type="button"
                      role="radio"
                      aria-checked={upscaleMode === option.value}
                      onClick={() => setUpscaleMode(option.value)}
                      className={`rounded-md px-2 py-1.5 text-xs font-semibold transition-colors ${upscaleMode === option.value ? 'bg-accent-600 text-white' : 'text-slate-400 hover:text-white'}`}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>
              <label className="flex items-center gap-2 rounded-lg border border-surface-700 bg-surface-900/70 px-3 py-2 text-xs text-slate-300">
                <input type="checkbox" checked={lockHeadline} onChange={e => setLockHeadline(e.target.checked)} className="accent-accent-500" />
                Khóa headline cho cả 3 concept
              </label>
              <label className="flex items-start gap-2 rounded-lg border border-surface-700 bg-surface-900/70 px-3 py-2 text-xs text-slate-300">
                <input type="checkbox" checked={visionEnabled} onChange={e => setVisionEnabled(e.target.checked)} className="mt-0.5 accent-accent-500" />
                <span>
                  <span className="block font-medium text-slate-200">Đánh giá bằng VLM</span>
                  <span className="mt-0.5 block leading-4 text-slate-500">OCR chữ, chấm điểm và tự thử sửa chữ một lần khi cần.</span>
                </span>
              </label>
            </div>

            <button
              type="button"
              onClick={handleCreate}
              disabled={creating || runActive || !project || !storyText?.trim()}
              className="w-full inline-flex items-center justify-center gap-2 rounded-xl bg-accent-600 px-4 py-3 text-sm font-semibold text-white hover:bg-accent-500 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {creating ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
              Đề xuất 3 concept
            </button>
          </div>

          <div className="rounded-xl border border-surface-600 bg-black/20 p-4 space-y-3">
            <div className="flex items-center justify-between gap-2">
              <div>
                <p className="text-sm font-semibold text-white">Profile kênh</p>
                <p className="text-xs text-slate-500">Chỉ học ảnh khi bạn chủ động thêm.</p>
              </div>
              <span className="text-xs text-slate-500">{profile?.references?.length || 0} ảnh</span>
            </div>
            <AppSelect value={profileId} onChange={e => setProfileId(e.target.value)} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500">
              <option value="">Tạo theo tên kênh khi chạy</option>
              {profiles.map(item => <option key={item.id} value={item.id}>{item.channel_name}</option>)}
            </AppSelect>
            {profile?.references?.length > 0 ? (
              <div className="grid grid-cols-3 gap-2">
                {profile.references.slice(0, 6).map(reference => {
                  const primary = profile.primary_reference_id === reference.id
                  return (
                    <button key={reference.id} type="button" onClick={() => handlePrimary(reference.id)} className={`relative overflow-hidden rounded-lg border aspect-video ${primary ? 'border-accent-400' : 'border-surface-600 hover:border-surface-400'}`} title={primary ? 'Reference chính' : 'Đặt làm reference chính'}>
                      <img src={thumbnailProfileReferenceUrl(profile.id, reference.id)} className="h-full w-full object-cover" />
                      {primary && <span className="absolute right-1 top-1 rounded bg-black/75 p-1 text-accent-200"><Star size={10} fill="currentColor" /></span>}
                    </button>
                  )
                })}
              </div>
            ) : (
              <div className="rounded-lg border border-dashed border-surface-600 px-3 py-4 text-center text-xs text-slate-500">Chưa có style reference đã duyệt.</div>
            )}
          </div>

          {runs.length > 0 && (
            <div className="rounded-xl border border-surface-600 bg-black/20 p-4 space-y-2">
              <p className="text-sm font-semibold text-white">Lịch sử Director</p>
              <div className="max-h-48 overflow-y-auto space-y-1">
                {runs.map(item => (
                  <button key={item.id} type="button" onClick={() => setRun(item)} className={`w-full rounded-lg px-3 py-2 text-left transition-colors ${run?.id === item.id ? 'bg-accent-500/12 text-white' : 'text-slate-400 hover:bg-surface-800 hover:text-white'}`}>
                    <div className="flex items-center justify-between gap-2 text-xs">
                      <span className="truncate">{item.story_title}</span>
                      <span className="shrink-0 text-[10px]">{item.progress || 0}%</span>
                    </div>
                    <p className="mt-1 text-[11px] text-slate-500">{statusLabel(item.status)} · {item.created_at}</p>
                  </button>
                ))}
              </div>
            </div>
          )}
        </aside>

        <div className="min-w-0 space-y-5">
          {error && (
            <div className="flex items-start gap-2 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-3 text-sm text-red-200">
              <AlertCircle size={16} className="mt-0.5 shrink-0" /> <span className="break-words">{error}</span>
            </div>
          )}

          {run ? (
            <>
              <div className="rounded-xl border border-surface-600 bg-black/20 p-4 space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className={`rounded-md border px-2 py-1 text-[11px] font-semibold ${statusClass(run.status)}`}>{statusLabel(run.status)}</span>
                      <span className="text-xs text-slate-500">{processed}/{expectedCandidates} ảnh</span>
                      <span className="rounded-md border border-surface-600 bg-surface-800 px-2 py-1 text-[10px] text-slate-300">
                        Ảnh chọn: {run.upscale_mode === 'original' ? 'GỐC' : (run.upscale_mode || '2k').toUpperCase()}
                      </span>
                      {run.layout_mode === 'channel_format' && (
                        <span className="rounded-md border border-slate-500/70 bg-slate-800 px-2 py-1 text-[10px] text-slate-200">Format kênh</span>
                      )}
                    </div>
                    <p className="mt-2 text-sm font-semibold text-white">{run.phase || run.story_title}</p>
                  </div>
                  {runActive && (
                    <button type="button" onClick={handleCancel} className="inline-flex items-center gap-1.5 rounded-lg border border-red-500/40 px-3 py-2 text-xs font-semibold text-red-200 hover:bg-red-500/10">
                      <Square size={12} fill="currentColor" /> Dừng
                    </button>
                  )}
                  {retryableRun && (
                    <button
                      type="button"
                      onClick={handleRetryRun}
                      disabled={creating}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-accent-400/45 px-3 py-2 text-xs font-semibold text-accent-100 hover:bg-accent-500/10 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {creating ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
                      Retry toàn bộ
                    </button>
                  )}
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-surface-700">
                  <div className="h-full bg-accent-500 transition-[width] duration-200" style={{ width: `${run.progress || 0}%` }} />
                </div>
                {run.headline_qa?.status === 'completed' && (
                  <p className="flex items-center gap-2 text-xs text-emerald-300"><CheckCircle2 size={13} /> Headline đã được kiểm theo ngôn ngữ đầu ra ({run.headline_qa.target_locale || run.target_locale || 'vi-VN'}).</p>
                )}
                {run.headline_qa?.status === 'skipped_locked' && (
                  <p className="flex items-center gap-2 text-xs text-slate-400"><ShieldCheck size={13} /> Headline được giữ nguyên vì bạn đã khóa headline.</p>
                )}
                {run.headline_qa?.status === 'skipped_channel_format' && (
                  <p className="flex items-center gap-2 text-xs text-slate-400"><ShieldCheck size={13} /> Run cũ đã khóa headline theo Format kênh. Tạo run mới để dùng headline riêng và chữ ghép chính xác.</p>
                )}
                {run.vision_enabled === false && run.concepts?.length > 0 && (
                  <p className="flex items-center gap-2 text-xs text-slate-400"><Eye size={13} /> VLM đã tắt cho run này; ảnh không được OCR hoặc xếp hạng.</p>
                )}
                {run.vision_enabled !== false && !run.vision_configured && run.concepts?.length > 0 && (
                  <p className="flex items-center gap-2 text-xs text-amber-300"><Eye size={13} /> Vision chưa cấu hình; Director vẫn tạo ảnh nhưng không OCR hoặc xếp hạng.</p>
                )}
                {run.packaging?.scope === 'stick_figure_long_form' && (
                  <div className="rounded-lg border border-emerald-400/25 bg-emerald-500/5 px-3 py-3 text-xs text-slate-200">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="inline-flex items-center gap-1.5 font-semibold text-emerald-200"><Sparkles size={13} /> Gói SEO Người que · {run.packaging.target_locale}</span>
                      <span className="text-slate-400">Title, mô tả và hashtag chỉ áp dụng video dài</span>
                    </div>
                    {run.packaging.core_promise && <p className="mt-2 leading-5 text-slate-300">{run.packaging.core_promise}</p>}
                    <div className="mt-3 flex flex-wrap gap-2">
                      {(run.packaging.titles || []).map(item => {
                        const selectedTitle = run.packaging.selected_title === item.text
                        return (
                          <button
                            key={`${item.kind}:${item.text}`}
                            type="button"
                            disabled={packagingBusy || selectedTitle}
                            onClick={() => handleSelectPackagingTitle(item.text)}
                            className={`rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors disabled:cursor-default ${selectedTitle ? 'border-emerald-400/60 bg-emerald-500/12 text-emerald-100' : 'border-surface-500 text-slate-300 hover:border-emerald-400 hover:text-white disabled:opacity-70'}`}
                          >
                            <span className="block text-[10px] uppercase tracking-wide opacity-70">{item.kind}{item.recommended ? ' · đề xuất' : ''}</span>
                            <span className="block max-w-xs font-semibold">{item.text}</span>
                          </button>
                        )
                      })}
                    </div>
                    {(run.packaging.hashtags || []).length > 0 && <p className="mt-3 text-[11px] text-slate-400">{run.packaging.hashtags.join(' ')}</p>}
                  </div>
                )}
                {run.packaging_error && <p className="text-xs text-amber-300">SEO packaging chưa tạo được; Director đang dùng fallback: {run.packaging_error}</p>}
                {run.error && <p className="text-xs text-red-300">{run.error}</p>}
              </div>

              {run.status === 'awaiting_approval' && (
                <div className="rounded-xl border border-accent-400/35 bg-accent-500/10 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h4 className="text-sm font-semibold text-white">Duyệt concept trước khi tạo ảnh</h4>
                      <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-300">Chọn các headline/chủ đề bạn muốn giữ. Chỉ các concept đã chọn mới tạo 2 biến thể ảnh; chưa hài lòng thì đề xuất một bộ concept mới, không tốn request ảnh.</p>
                    </div>
                    <span className="rounded-md border border-accent-300/35 bg-surface-950/55 px-2 py-1 text-xs font-semibold text-accent-100">{approvedConceptIds.length}/{(run.concepts || []).length} đã chọn</span>
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={handleApproveConcepts}
                      disabled={!approvedConceptIds.length || actionBusy === 'approve-concepts'}
                      className="inline-flex items-center gap-2 rounded-lg bg-accent-600 px-3 py-2 text-xs font-semibold text-white hover:bg-accent-500 disabled:cursor-not-allowed disabled:opacity-45"
                    >
                      {actionBusy === 'approve-concepts' ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
                      Tạo {approvedConceptIds.length * 2} ảnh đã chốt
                    </button>
                    <button type="button" onClick={handleRegenerateUnselectedConcepts} disabled={actionBusy === 'regenerate-concepts' || approvedConceptIds.length >= (run.concepts || []).length} className="inline-flex items-center gap-2 rounded-lg border border-surface-500 px-3 py-2 text-xs font-semibold text-slate-200 hover:border-accent-400 hover:text-white disabled:cursor-not-allowed disabled:opacity-45">
                      {actionBusy === 'regenerate-concepts' ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                      Tạo lại concept chưa chọn
                    </button>
                  </div>
                  <div className="mt-4 border-t border-accent-300/15 pt-4">
                    <label className="block text-xs font-semibold text-slate-200" htmlFor="custom-thumbnail-concept">Ý tưởng concept của bạn</label>
                    <p className="mt-1 text-[11px] leading-4 text-slate-400">Chỉ nhập headline muốn xuất hiện trên ảnh. AI sẽ tự viết bí ẩn, hướng hình và bố cục dựa trên đúng ý tưởng đó.</p>
                    <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                      <input
                        id="custom-thumbnail-concept"
                        value={customConceptHeadline}
                        maxLength={90}
                        onChange={e => setCustomConceptHeadline(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); handleAddCustomConcept() } }}
                        placeholder="Ví dụ: Bức thư cuối cùng không ai được đọc"
                        className="min-w-0 flex-1 rounded-lg border border-surface-500 bg-surface-950 px-3 py-2 text-sm text-white placeholder:text-slate-500 focus:border-accent-400 focus:outline-none"
                      />
                      <button type="button" onClick={handleAddCustomConcept} disabled={!customConceptHeadline.trim() || actionBusy === 'custom-concept'} className="inline-flex shrink-0 items-center justify-center gap-2 rounded-lg border border-accent-400/55 px-3 py-2 text-xs font-semibold text-accent-100 hover:bg-accent-500/10 disabled:cursor-not-allowed disabled:opacity-45">
                        {actionBusy === 'custom-concept' ? <Loader2 size={14} className="animate-spin" /> : <Wand2 size={14} />}
                        AI triển khai ý tưởng
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {visibleConcepts.map((concept, index) => {
                const items = candidatesByConcept.get(concept.id) || []
                return (
                  <section key={concept.id} className="border-b border-surface-700 pb-6 last:border-0 last:pb-0">
                    <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0 max-w-3xl">
                        <div className="flex items-center gap-2">
                          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-accent-500/12 text-xs font-bold text-accent-200">{index + 1}</span>
                          <h4 className="text-sm font-bold text-white">{concept.name}</h4>
                          {run.status === 'awaiting_approval' && (
                            <label className="ml-1 inline-flex cursor-pointer items-center gap-1.5 text-xs font-medium text-slate-200">
                              <input
                                type="checkbox"
                                checked={approvedConceptIds.includes(concept.id)}
                                onChange={e => setApprovedConceptIds(current => e.target.checked ? [...new Set([...current, concept.id])] : current.filter(id => id !== concept.id))}
                                className="accent-accent-500"
                              />
                              Giữ concept này
                            </label>
                          )}
                        </div>
                        <p className="mt-2 text-base font-semibold text-white text-pretty">{concept.headline}</p>
                        {concept.source === 'user_headline' && (
                          <p className="mt-1 inline-flex items-center gap-1 text-[11px] text-accent-200"><Lock size={11} /> Headline do bạn nhập · được khóa nguyên văn</p>
                        )}
                        {concept.headline_qa?.status === 'corrected' && (
                          <p className="mt-1 inline-flex items-center gap-1 text-[11px] text-emerald-300"><CheckCircle2 size={11} /> Đã chuẩn hóa headline theo ngôn ngữ đầu ra</p>
                        )}
                        <p className="mt-1 text-xs leading-5 text-slate-400 text-pretty">{concept.core_mystery || concept.visual_focus}</p>
                      </div>
                      <span className="rounded-md border border-surface-600 bg-surface-800 px-2 py-1 text-[11px] text-slate-400">{concept.emotion}</span>
                    </div>
                    {run.status === 'awaiting_approval' ? (
                      <div className="rounded-xl border border-surface-600 bg-black/15 px-4 py-3 text-xs leading-5 text-slate-300">
                        <span className="font-semibold text-slate-200">Hướng hình:</span> {concept.visual_focus || 'AI sẽ hoàn thiện hướng hình sau khi bạn chốt concept.'}
                        {concept.composition && <><br /><span className="font-semibold text-slate-200">Bố cục:</span> {concept.composition}</>}
                      </div>
                    ) : (
                    <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
                      {items.length > 0 ? items.map(candidate => (
                        <ConceptCandidate
                          key={candidate.id}
                          project={project}
                          run={run}
                          candidate={candidate}
                          selectedFilename={selected?.filename}
                          profile={profile}
                          onRetry={handleRetry}
                          onUpscale={handleUpscale}
                          onSelect={handleSelect}
                          onAddProfile={handleAddProfile}
                          upscaleMode={upscaleMode}
                          busy={actionBusy}
                        />
                      )) : [1, 2].map(value => (
                        <div key={value} className="relative aspect-video overflow-hidden rounded-xl border border-surface-600 bg-[#0d0d15] scene-card-running">
                          <div className="scene-loading-sheen" />
                          <div className="absolute inset-0 flex items-center justify-center text-xs text-slate-500">Đang chuẩn bị biến thể {value}</div>
                        </div>
                      ))}
                    </div>
                    )}
                  </section>
                )
              })}

              {run.status === 'planning' && (
                <div className="relative min-h-64 overflow-hidden rounded-xl border border-surface-600 bg-black/20 scene-card-running">
                  <div className="scene-loading-sheen" />
                  <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 text-slate-400">
                    <Wand2 size={28} className="text-accent-300" />
                    <p className="text-sm font-semibold text-white">Visual Director đang đọc toàn bộ truyện</p>
                    <p className="text-xs">Kết quả concept sẽ xuất hiện trước khi ảnh bắt đầu chạy.</p>
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="min-h-80 rounded-xl border border-dashed border-surface-600 bg-black/10 px-5 py-12 flex flex-col items-center justify-center text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-accent-500/25 bg-accent-500/10 text-accent-300"><Sparkles size={22} /></div>
              <h4 className="mt-4 text-sm font-semibold text-white">Chưa có Director run</h4>
              <p className="mt-2 max-w-md text-xs leading-5 text-slate-500">AI sẽ đọc toàn bộ truyện, dựng ba hướng thumbnail khác nhau và tạo sáu ảnh để bạn chọn.</p>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
