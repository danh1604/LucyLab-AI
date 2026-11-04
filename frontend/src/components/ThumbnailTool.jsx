import AppSelect from './AppSelect.jsx'
import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertCircle, CheckCircle2, Download, Image, Loader2, Maximize2, RefreshCw, ShieldCheck, Sparkles, Trash2, Upload, Wand2 } from 'lucide-react'
import {
  createThumbnailSafePrompt,
  deleteThumbnail,
  deleteThumbnailReference,
  listThumbnails,
  startThumbnailGenerate,
  thumbnailReferenceFileUrl,
  thumbnailReferenceUrl,
  thumbnailUrl,
  upscaleThumbnail,
  uploadThumbnailReference,
} from '../api.js'
import { useDialog } from './DialogProvider.jsx'
import ThumbnailDirectorPanel from './ThumbnailDirectorPanel.jsx'

const FORMULAS = [
  { value: 'mystery', label: 'Bí ẩn', hint: 'Mở một câu hỏi khiến người xem tò mò.' },
  { value: 'warning', label: 'Cảnh báo', hint: 'Tạo cảm giác không nên bỏ qua.' },
  { value: 'secret', label: 'Sự thật bị giấu', hint: 'Nhấn vào bí mật hoặc cú lật.' },
  { value: 'entity', label: 'Thứ phía sau', hint: 'Tập trung vào hiện diện đáng sợ.' },
]

function safeName(value, fallback = 'thumbnail') {
  return (value || '')
    .replace(/\.[^.]+$/, '')
    .replace(/[<>:"/\\|?*\x00-\x1f]+/g, '_')
    .replace(/\s+/g, ' ')
    .trim() || fallback
}

function statusText(value) {
  if (value === 'requesting') return 'Đang gửi yêu cầu tạo ảnh...'
  if (value === 'running' || value === 'queued' || value === 'pending') return 'Provider đang tạo thumbnail theo reference...'
  if (value === 'finalizing') return 'Đang chờ ảnh hoàn tất...'
  if (value === 'compositing') return 'Đang chuẩn hóa thumbnail...'
  if (value === 'upscaling') return 'GPU đang tăng nét và xuất đúng độ phân giải...'
  if (value === 'succeeded') return 'Thumbnail đã tạo xong'
  return value || 'Đang tạo thumbnail...'
}

export default function ThumbnailTool({ project, storyTitle, storyText, sessionId = '', outputLocale = 'vi-VN' }) {
  const { confirm, notify } = useDialog()
  const referenceInputRef = useRef(null)
  const activeScopeRef = useRef(`${project}:${sessionId}:${storyTitle}`)
  const [channelName, setChannelName] = useState(() => localStorage.getItem('lucylab_thumbnail_channel') || '')
  const [title, setTitle] = useState(storyTitle || project || '')
  const [genreLabel, setGenreLabel] = useState('')
  const [hook, setHook] = useState('')
  const [formula, setFormula] = useState('mystery')
  const [context, setContext] = useState((storyText || '').slice(0, 1400))
  const [outputName, setOutputName] = useState(safeName(storyTitle || project || 'thumbnail'))
  const [history, setHistory] = useState([])
  const [current, setCurrent] = useState(null)
  const [provider, setProvider] = useState(null)
  const [referenceAttached, setReferenceAttached] = useState(null)
  const [generating, setGenerating] = useState(false)
  const [progress, setProgress] = useState(0)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [safeRulePrompt, setSafeRulePrompt] = useState('')
  const [safeRuleLevel, setSafeRuleLevel] = useState(0)
  const [safeLlmPrompt, setSafeLlmPrompt] = useState('')
  const [safeLlmLevel, setSafeLlmLevel] = useState(0)
  const [activePromptMode, setActivePromptMode] = useState('original')
  const [safeBusy, setSafeBusy] = useState('')
  const [referenceChoice, setReferenceChoice] = useState({ type: 'default', value: '', label: 'Reference mặc định' })
  const [referenceUploading, setReferenceUploading] = useState(false)
  const [toolMode, setToolMode] = useState(() => localStorage.getItem('lucylab_thumbnail_mode') || 'quick')
  const [upscaleMode, setUpscaleMode] = useState(() => localStorage.getItem('lucylab_thumbnail_upscale_mode') || '2k')
  const [upscaleBusy, setUpscaleBusy] = useState('')

  const activeFormula = useMemo(
    () => FORMULAS.find(item => item.value === formula) || FORMULAS[0],
    [formula],
  )

  useEffect(() => {
    if (!title && (storyTitle || project)) setTitle(storyTitle || project)
  }, [project, storyTitle, title])

  useEffect(() => {
    if (!context && storyText) setContext(storyText.slice(0, 1400))
  }, [context, storyText])

  useEffect(() => {
    if (!outputName && (storyTitle || project)) setOutputName(safeName(storyTitle || project))
  }, [outputName, project, storyTitle])

  function resetSafePrompts() {
    setSafeRulePrompt('')
    setSafeRuleLevel(0)
    setSafeLlmPrompt('')
    setSafeLlmLevel(0)
    setActivePromptMode('original')
  }

  function buildSafeBasePayload() {
    return {
      project,
      title: title.trim(),
      session_id: sessionId,
      channel_name: channelName.trim(),
      genre_label: genreLabel.trim(),
      hook: hook.trim(),
      context: context.trim(),
      formula,
      output_name: outputName.trim(),
      reference_image: referenceChoice.type === 'default' ? '' : referenceChoice.value,
      upscale_mode: upscaleMode,
    }
  }

  function activeSafePrompt() {
    if (activePromptMode === 'safe_llm' && safeLlmPrompt) {
      return { prompt: safeLlmPrompt, level: safeLlmLevel, mode: 'safe_llm', label: `Safe LLM lv${safeLlmLevel}` }
    }
    if (activePromptMode === 'safe_rule' && safeRulePrompt) {
      return { prompt: safeRulePrompt, level: safeRuleLevel, mode: 'safe_rule', label: `Safe Rule lv${safeRuleLevel}` }
    }
    return { prompt: '', level: 0, mode: 'original', label: 'Gốc' }
  }

  async function refreshHistory(targetProject = project, targetSessionId = sessionId, targetStoryTitle = storyTitle, { selectFirst = false } = {}) {
    const targetScope = `${targetProject}:${targetSessionId}:${targetStoryTitle}`
    if (!targetProject) {
      setHistory([])
      setCurrent(null)
      return
    }
    try {
      const data = await listThumbnails(targetProject, targetSessionId, targetStoryTitle)
      if (activeScopeRef.current !== targetScope) return
      const items = data.items || []
      setHistory(items)
      setCurrent(previous => {
        if (selectFirst) return items[0] || null
        return items.find(item => item.filename === previous?.filename) || items[0] || null
      })
    } catch {
      if (activeScopeRef.current === targetScope) {
        setHistory([])
        setCurrent(null)
      }
    }
  }

  async function handleReferenceUpload(file) {
    if (!file || !project) return
    setReferenceUploading(true)
    setError('')
    try {
      const data = await uploadThumbnailReference(project, file)
      setReferenceChoice({
        type: 'uploaded',
        value: data.reference_image || data.filename,
        label: file.name || data.filename,
      })
      notify({ tone: 'success', title: 'Đã đổi ảnh reference', message: file.name || data.filename })
    } catch (e) {
      setError(e?.message || String(e))
    } finally {
      setReferenceUploading(false)
      if (referenceInputRef.current) referenceInputRef.current.value = ''
    }
  }

  function useGeneratedAsReference(item) {
    if (!item?.filename) return
    setReferenceChoice({
      type: 'generated',
      value: `generated:${item.filename}`,
      label: item.filename,
    })
    notify({ title: 'Đã chọn reference', message: item.filename })
  }

  async function handleDeleteThumbnail(item) {
    if (!item?.filename || !project) return
    const ok = await confirm({
      tone: 'danger',
      title: 'Xóa thumbnail?',
      message: `File này sẽ bị xóa khỏi project và khỏi lịch sử thumbnail.`,
      details: item.filename,
      confirmText: 'Xóa',
      cancelText: 'Hủy',
    })
    if (!ok) return
    try {
      await deleteThumbnail(project, item.filename)
      const nextHistory = history.filter(x => x.filename !== item.filename)
      setHistory(nextHistory)
      if (current?.filename === item.filename) setCurrent(nextHistory[0] || null)
      if (referenceChoice.type === 'generated' && referenceChoice.value === `generated:${item.filename}`) {
        setReferenceChoice({ type: 'default', value: '', label: 'Reference mặc định' })
      }
      notify({ tone: 'success', title: 'Đã xóa thumbnail', message: item.filename })
    } catch (e) {
      notify({ tone: 'error', title: 'Không xóa được thumbnail', message: e?.message || String(e) })
    }
  }

  async function handleDeleteUploadedReference() {
    if (!project || referenceChoice.type !== 'uploaded' || !referenceChoice.value) return
    const ok = await confirm({
      tone: 'danger',
      title: 'Xóa ảnh reference upload?',
      message: 'Ảnh reference upload này sẽ bị xóa khỏi thư mục project.',
      details: referenceChoice.label || referenceChoice.value,
      confirmText: 'Xóa',
      cancelText: 'Hủy',
    })
    if (!ok) return
    try {
      await deleteThumbnailReference(project, referenceChoice.value)
      setReferenceChoice({ type: 'default', value: '', label: 'Reference mặc định' })
      notify({ tone: 'success', title: 'Đã xóa reference upload' })
    } catch (e) {
      notify({ tone: 'error', title: 'Không xóa được reference', message: e?.message || String(e) })
    }
  }

  useEffect(() => {
    activeScopeRef.current = `${project}:${sessionId}:${storyTitle}`
    setHistory([])
    setCurrent(null)
    setError('')
    refreshHistory(project, sessionId, storyTitle, { selectFirst: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project, sessionId, storyTitle])

  useEffect(() => {
    setReferenceChoice({ type: 'default', value: '', label: 'Reference mặc định' })
  }, [project])

  function handleGenerate() {
    setError('')
    setProgress(1)
    setStatus('Đang chuẩn bị...')
    setProvider(null)
    setReferenceAttached(null)

    if (!project) {
      setError('Chưa có project để lưu thumbnail.')
      return
    }
    if (!title.trim()) {
      setError('Hãy nhập headline thumbnail.')
      return
    }

    localStorage.setItem('lucylab_thumbnail_channel', channelName.trim())
    localStorage.setItem('lucylab_thumbnail_upscale_mode', upscaleMode)
    setGenerating(true)
    startThumbnailGenerate(
      {
        ...buildSafeBasePayload(),
        prompt_mode: activeSafePrompt().mode,
        safe_prompt: activeSafePrompt().prompt,
        safe_level: activeSafePrompt().level,
      },
      (ev) => {
        if (ev.type === 'provider') {
          setProvider(ev)
          setStatus(`${ev.provider || 'provider'} · ${ev.model || ''}`)
        }
        if (ev.type === 'start') {
          setReferenceAttached(Boolean(ev.reference_attached))
        }
        if (ev.type === 'task') {
          setStatus(`Đã nhận task ${ev.task_id}`)
        }
        if (ev.type === 'progress' || ev.type === 'start') {
          setProgress(ev.progress || progress)
          setStatus(statusText(ev.status))
        }
        if (ev.type === 'done') {
          setProgress(100)
          setStatus('Hoàn thành')
          setCurrent(ev.thumbnail)
          setHistory(prev => [ev.thumbnail, ...prev.filter(x => x.filename !== ev.thumbnail.filename)])
          notify({ tone: 'success', title: 'Đã tạo thumbnail', message: ev.thumbnail?.filename })
        }
        if (ev.type === 'error') {
          setError(ev.message || 'Tạo thumbnail thất bại')
          setGenerating(false)
        }
      },
      () => {
        setGenerating(false)
        refreshHistory()
      },
      (e) => {
        setGenerating(false)
        setError(e?.message || String(e))
      },
    )
  }

  async function handleCreateSafePrompt(mode) {
    setError('')
    if (!project) {
      setError('Chưa có project để tạo safe prompt.')
      return
    }
    if (!title.trim()) {
      setError('Hãy nhập headline thumbnail trước khi tạo safe prompt.')
      return
    }
    const nextLevel = mode === 'llm'
      ? Math.min(5, Number(safeLlmLevel || 0) + 1)
      : Math.min(5, Number(safeRuleLevel || 0) + 1)
    setSafeBusy(mode)
    try {
      const data = await createThumbnailSafePrompt({
        ...buildSafeBasePayload(),
        mode,
        level: nextLevel,
      })
      if (data.prompt_mode === 'safe_llm') {
        setSafeLlmPrompt(data.prompt || '')
        setSafeLlmLevel(data.safe_level || nextLevel)
        setActivePromptMode('safe_llm')
      } else {
        setSafeRulePrompt(data.prompt || '')
        setSafeRuleLevel(data.safe_level || nextLevel)
        setActivePromptMode('safe_rule')
      }
      notify({
        tone: 'success',
        title: data.prompt_mode === 'safe_llm' ? 'Đã tạo Safe LLM' : 'Đã tạo Safe Rule',
        message: `Đang dùng level ${data.safe_level || nextLevel} cho lần tạo tiếp theo.`,
      })
    } catch (e) {
      setError(e?.message || String(e))
    } finally {
      setSafeBusy('')
    }
  }

  async function handleUpscale(item) {
    if (!project || !item?.filename || upscaleMode === 'original') return
    setUpscaleBusy(item.filename)
    setError('')
    try {
      const enhanced = await upscaleThumbnail(project, item.filename, upscaleMode)
      setCurrent(enhanced)
      setHistory(prev => [enhanced, ...prev.filter(entry => entry.filename !== enhanced.filename)])
      notify({
        tone: 'success',
        title: `Đã tăng nét ${upscaleMode.toUpperCase()}`,
        message: `${enhanced.width}×${enhanced.height} · ${enhanced.upscale_engine}`,
      })
    } catch (e) {
      setError(e?.message || String(e))
    } finally {
      setUpscaleBusy('')
    }
  }

  const previewSrc = current?.filename ? `${thumbnailUrl(project, current.filename)}?t=${encodeURIComponent(current.created_at || '')}` : ''
  const referencePreviewSrc = referenceChoice.type === 'uploaded'
    ? `${thumbnailReferenceFileUrl(project, referenceChoice.value)}?t=${encodeURIComponent(referenceChoice.label || '')}`
    : referenceChoice.type === 'generated'
      ? `${thumbnailUrl(project, referenceChoice.value.replace(/^generated:/, ''))}?ref=1`
      : `${thumbnailReferenceUrl(project)}?v=option-a`

  if (toolMode === 'director') {
    return (
      <ThumbnailDirectorPanel
        project={project}
        storyTitle={storyTitle}
        storyText={storyText}
        sessionId={sessionId}
        outputLocale={outputLocale}
        onQuick={() => {
          localStorage.setItem('lucylab_thumbnail_mode', 'quick')
          setToolMode('quick')
        }}
      />
    )
  }

  return (
    <section className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <Image size={16} className="text-accent-400" />
          <div>
            <h3 className="text-sm font-bold text-white">Tạo thumbnail 16:9</h3>
            <p className="text-xs text-slate-500">Template Option A: khung cố định · hook optional · nền AI</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              localStorage.setItem('lucylab_thumbnail_mode', 'director')
              setToolMode('director')
            }}
            className="inline-flex items-center gap-1.5 rounded-lg bg-accent-600 px-3 py-2 text-xs font-semibold text-white hover:bg-accent-500 transition-colors"
          >
            <Sparkles size={13} />
            Director
          </button>
          <button
            type="button"
            onClick={refreshHistory}
            className="inline-flex items-center gap-1.5 rounded-lg border border-surface-500 px-3 py-2 text-xs font-semibold text-slate-300 hover:text-white hover:border-accent-400 transition-colors"
          >
            <RefreshCw size={13} />
            Làm mới
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[25rem_minmax(0,1fr)] 2xl:grid-cols-[28rem_minmax(0,1fr)] gap-5 items-start">
        <div className="space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className="space-y-1">
              <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Tên kênh</span>
              <input
              value={channelName}
                onChange={e => setChannelName(e.target.value)}
                placeholder="vd: LucyLab Horror"
                className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
              />
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Công thức click</span>
              <AppSelect
                value={formula}
                onChange={e => { setFormula(e.target.value); resetSafePrompts() }}
                className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
              >
                {FORMULAS.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
              </AppSelect>
            </label>
          </div>

          <label className="block space-y-1">
            <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Headline thumbnail</span>
            <input
              value={title}
              onChange={e => {
                setTitle(e.target.value)
                if (!outputName || outputName === safeName(title)) setOutputName(safeName(e.target.value))
                resetSafePrompts()
              }}
              placeholder="vd: Căn phòng không được mở"
              className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
            />
          </label>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className="space-y-1">
              <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Hook phụ (optional)</span>
              <input
                value={hook}
                onChange={e => { setHook(e.target.value); resetSafePrompts() }}
                placeholder="Để trống nếu không cần hook phụ"
                className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
              />
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Thể loại dưới tiêu đề (optional)</span>
              <input
                value={genreLabel}
                onChange={e => { setGenreLabel(e.target.value); resetSafePrompts() }}
                placeholder="vd: TRUYỆN MA DÂN GIAN"
                className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
              />
            </label>
            <div className="space-y-1">
              <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Độ nét xuất</span>
              <div className="grid grid-cols-3 rounded-lg border border-surface-500 bg-surface-900 p-1" role="radiogroup" aria-label="Độ nét thumbnail">
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
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className="space-y-1">
              <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Tên file</span>
              <input
                value={outputName}
                onChange={e => setOutputName(e.target.value)}
                placeholder="thumbnail"
                className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
              />
            </label>
          </div>

          <label className="block space-y-1">
            <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Nội dung cảnh / bối cảnh</span>
            <textarea
              value={context}
              onChange={e => { setContext(e.target.value); resetSafePrompts() }}
              rows={5}
              placeholder="Mô tả đoạn đáng sợ nhất của truyện để AI tạo thumbnail theo reference..."
              className="scene-prompt-editor w-full rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent-500 resize-y"
            />
          </label>

          <div className="rounded-xl border border-surface-600 bg-black/20 px-3 py-2">
            <p className="text-xs text-slate-300">{activeFormula.label}</p>
            <p className="text-xs text-slate-500">{activeFormula.hint}</p>
            {provider && (
              <p className="mt-1 text-xs text-slate-500 font-mono truncate">
                {provider.provider} · {provider.model} · {provider.size}
              </p>
            )}
            {referenceAttached === false && (
              <p className="mt-2 text-xs text-amber-300">
                Provider hiện tại không attach ảnh reference thật; AI chỉ bám theo mô tả trong prompt.
              </p>
            )}
            {referenceAttached === true && (
              <p className="mt-2 text-xs text-green-300">
                Đã attach ảnh reference đang chọn vào request tạo thumbnail.
              </p>
            )}
          </div>

          <div className="rounded-xl border border-surface-600 bg-black/20 p-3 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-xs font-semibold text-slate-300">Safe prompt</p>
                <p className="text-xs text-slate-500">Dùng khi provider chặn thumbnail vì policy.</p>
              </div>
              <div className="flex flex-wrap gap-1.5">
                <button
                  type="button"
                  onClick={() => setActivePromptMode('original')}
                  className={`rounded-lg px-2.5 py-1.5 text-xs font-semibold border transition-colors ${activePromptMode === 'original' ? 'border-accent-400 bg-accent-500/15 text-white' : 'border-surface-500 text-slate-400 hover:text-white'}`}
                >
                  Gốc
                </button>
                {safeRulePrompt && (
                  <button
                    type="button"
                    onClick={() => setActivePromptMode('safe_rule')}
                    className={`rounded-lg px-2.5 py-1.5 text-xs font-semibold border transition-colors ${activePromptMode === 'safe_rule' ? 'border-accent-400 bg-accent-500/15 text-white' : 'border-surface-500 text-slate-400 hover:text-white'}`}
                  >
                    Rule lv{safeRuleLevel}
                  </button>
                )}
                {safeLlmPrompt && (
                  <button
                    type="button"
                    onClick={() => setActivePromptMode('safe_llm')}
                    className={`rounded-lg px-2.5 py-1.5 text-xs font-semibold border transition-colors ${activePromptMode === 'safe_llm' ? 'border-accent-400 bg-accent-500/15 text-white' : 'border-surface-500 text-slate-400 hover:text-white'}`}
                  >
                    LLM lv{safeLlmLevel}
                  </button>
                )}
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => handleCreateSafePrompt('rule')}
                disabled={generating || Boolean(safeBusy) || safeRuleLevel >= 5}
                className="inline-flex items-center justify-center gap-2 rounded-lg border border-surface-500 px-3 py-2 text-xs font-semibold text-slate-300 hover:text-white hover:border-accent-400 disabled:opacity-40 transition-colors"
              >
                {safeBusy === 'rule' ? <Loader2 size={14} className="animate-spin" /> : <ShieldCheck size={14} />}
                {safeRulePrompt ? 'Tăng Safe Rule' : 'Safe Rule'}
              </button>
              <button
                type="button"
                onClick={() => handleCreateSafePrompt('llm')}
                disabled={generating || Boolean(safeBusy) || safeLlmLevel >= 5}
                className="inline-flex items-center justify-center gap-2 rounded-lg border border-surface-500 px-3 py-2 text-xs font-semibold text-slate-300 hover:text-white hover:border-accent-400 disabled:opacity-40 transition-colors"
              >
                {safeBusy === 'llm' ? <Loader2 size={14} className="animate-spin" /> : <Wand2 size={14} />}
                {safeLlmPrompt ? 'Tăng Safe LLM' : 'Safe LLM'}
              </button>
            </div>

            {activeSafePrompt().prompt && (
              <textarea
                value={activeSafePrompt().prompt}
                readOnly
                rows={3}
                className="scene-prompt-editor w-full rounded-lg px-3 py-2 text-xs focus:outline-none resize-y"
              />
            )}
          </div>

          {generating && (
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-400">{status}</span>
                <span className="font-mono text-white">{progress}%</span>
              </div>
              <div className="h-2 rounded-full bg-surface-700 overflow-hidden">
                <div className="h-full bg-accent-500 transition-all" style={{ width: `${progress}%` }} />
              </div>
            </div>
          )}

          {error && (
            <div className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              <AlertCircle size={15} className="mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <button
            onClick={handleGenerate}
            disabled={generating || Boolean(safeBusy) || !project || !title.trim()}
            className="w-full inline-flex items-center justify-center gap-2 rounded-xl bg-accent-600 hover:bg-accent-500 disabled:opacity-40 disabled:cursor-not-allowed px-4 py-3 text-sm font-semibold text-white transition-colors"
          >
            {generating ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
            Tạo thumbnail · {upscaleMode === 'original' ? 'Gốc' : upscaleMode.toUpperCase()}{activeSafePrompt().mode !== 'original' ? ` · ${activeSafePrompt().label}` : ''}
          </button>
        </div>

        <div className="space-y-3">
          <div className="rounded-xl border border-surface-600 bg-black/20 p-3 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p className="text-xs font-semibold text-white">Ảnh reference</p>
                <p className="text-xs text-slate-500 truncate max-w-[28rem]">{referenceChoice.label}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => setReferenceChoice({ type: 'default', value: '', label: 'Reference mặc định' })}
                  className="rounded-lg border border-surface-500 px-2.5 py-1.5 text-xs font-semibold text-slate-300 hover:text-white hover:border-accent-400 transition-colors"
                >
                  Mặc định
                </button>
                <button
                  type="button"
                  onClick={() => referenceInputRef.current?.click()}
                  disabled={referenceUploading || !project}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-surface-500 px-2.5 py-1.5 text-xs font-semibold text-slate-300 hover:text-white hover:border-accent-400 disabled:opacity-40 transition-colors"
                >
                  {referenceUploading ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
                  Upload
                </button>
                {referenceChoice.type === 'uploaded' && (
                  <button
                    type="button"
                    onClick={handleDeleteUploadedReference}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-red-500/40 px-2.5 py-1.5 text-xs font-semibold text-red-200 hover:text-white hover:bg-red-500/10 transition-colors"
                  >
                    <Trash2 size={13} />
                    Xóa
                  </button>
                )}
                <input
                  ref={referenceInputRef}
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  className="hidden"
                  onChange={e => handleReferenceUpload(e.target.files?.[0])}
                />
              </div>
            </div>
            <div className="rounded-lg overflow-hidden border border-surface-700 bg-black aspect-video relative">
              {project ? (
                <img
                  src={referencePreviewSrc}
                  className="h-full w-full object-cover"
                  alt="Thumbnail reference"
                  onError={e => { e.currentTarget.style.display = 'none' }}
                />
              ) : (
                <div className="h-full w-full flex items-center justify-center text-xs text-slate-500">
                  Chưa có project để tải ảnh reference
                </div>
              )}
              <div className="absolute left-3 bottom-3 rounded-md border border-black/40 bg-black/70 px-2 py-1 text-[11px] font-semibold text-stone-200">
                {referenceChoice.type === 'generated' ? 'Reference từ ảnh đã tạo' : referenceChoice.type === 'uploaded' ? 'Reference upload' : 'Reference mặc định'}
              </div>
            </div>
          </div>

          {current && (
            <div className="rounded-xl border border-green-500/30 bg-green-500/10 p-3 space-y-1">
              {previewSrc && (
                <img
                  src={previewSrc}
                  className="mb-2 w-full rounded-lg border border-green-500/20 object-cover"
                  alt={current.filename || 'thumbnail'}
                />
              )}
              <div className="flex items-center gap-2 text-sm font-semibold text-green-300">
                <CheckCircle2 size={15} />
                Thumbnail đã lưu
              </div>
              <p className="text-xs text-white truncate">{current.filename}</p>
              <p className="text-xs text-slate-500 break-all">{current.saved_path}</p>
              {(current.width || current.height) && (
                <p className="text-xs text-green-200/80">
                  {current.width}×{current.height} · {current.upscale_engine || 'provider'}
                </p>
              )}
              <div className="flex flex-wrap gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => useGeneratedAsReference(current)}
                  className="inline-flex items-center gap-1.5 text-xs text-accent-300 hover:text-white"
                >
                  <Image size={13} />
                  Dùng làm reference
                </button>
                <a
                  href={thumbnailUrl(project, current.filename)}
                  download={current.filename}
                  className="inline-flex items-center gap-1.5 text-xs text-accent-300 hover:text-white"
                >
                  <Download size={13} />
                  Tải ảnh
                </a>
                {upscaleMode !== 'original' && (
                  <button
                    type="button"
                    onClick={() => handleUpscale(current)}
                    disabled={Boolean(upscaleBusy) || current.upscale_mode === upscaleMode}
                    className="inline-flex items-center gap-1.5 text-xs text-accent-300 hover:text-white disabled:text-slate-500"
                  >
                    {upscaleBusy === current.filename ? <Loader2 size={13} className="animate-spin" /> : <Maximize2 size={13} />}
                    {current.upscale_mode === upscaleMode ? `Đã ${upscaleMode.toUpperCase()}` : `Tăng nét ${upscaleMode.toUpperCase()}`}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => handleDeleteThumbnail(current)}
                  className="inline-flex items-center gap-1.5 text-xs text-red-300 hover:text-white"
                >
                  <Trash2 size={13} />
                  Xóa
                </button>
              </div>
            </div>
          )}

          {history.length > 0 && (
            <div className="rounded-xl border border-surface-600 overflow-hidden">
              <div className="px-3 py-2 border-b border-surface-600 flex items-center justify-between">
                <span className="text-xs font-semibold text-white">Thumbnail đã tạo</span>
                <span className="text-xs text-slate-500">{history.length}</span>
              </div>
              <div className="max-h-64 overflow-y-auto divide-y divide-surface-700">
                {history.map(item => (
                  <div
                    key={item.filename}
                    className="w-full px-3 py-2 flex items-center gap-3 text-left hover:bg-surface-800/70 transition-colors"
                  >
                    <button type="button" onClick={() => setCurrent(item)} className="min-w-0 flex flex-1 items-center gap-3 text-left">
                      <img src={thumbnailUrl(project, item.filename)} className="h-12 aspect-video object-cover rounded-md border border-surface-600 shrink-0" />
                      <div className="min-w-0">
                        <p className="text-xs text-white truncate">{item.title}</p>
                        <p className="text-xs text-slate-500 truncate">{item.filename}</p>
                      </div>
                    </button>
                    <button
                      type="button"
                      onClick={() => useGeneratedAsReference(item)}
                      className="shrink-0 rounded-lg border border-surface-500 px-2 py-1 text-[11px] font-semibold text-slate-300 hover:text-white hover:border-accent-400"
                    >
                      Reference
                    </button>
                    {upscaleMode !== 'original' && (
                      <button
                        type="button"
                        onClick={() => handleUpscale(item)}
                        disabled={Boolean(upscaleBusy) || item.upscale_mode === upscaleMode}
                        className="shrink-0 inline-flex items-center gap-1 rounded-lg border border-surface-500 px-2 py-1 text-[11px] font-semibold text-slate-300 hover:text-white hover:border-accent-400 disabled:opacity-40"
                      >
                        {upscaleBusy === item.filename ? <Loader2 size={11} className="animate-spin" /> : <Maximize2 size={11} />}
                        {item.upscale_mode === upscaleMode ? upscaleMode.toUpperCase() : 'Tăng nét'}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => handleDeleteThumbnail(item)}
                      className="shrink-0 rounded-lg border border-red-500/40 px-2 py-1 text-[11px] font-semibold text-red-200 hover:text-white hover:bg-red-500/10"
                    >
                      Xóa
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
