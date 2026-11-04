import AppSelect from './AppSelect.jsx'
import { useEffect, useRef, useState } from 'react'
import { AlertCircle, CheckCircle2, Download, Film, FolderOpen, Loader2, Scissors, Upload, Video } from 'lucide-react'
import {
  cutVideoDownloadUrl,
  generatePostVideoDescription,
  getPostVideoDescription,
  getConfig,
  outputVideoUrl,
  registerProjectVideoForCut,
  savePostVideoDescription,
  selectFolder,
  startAppendOutro,
  startVideoCut,
  subtitleSrtUrl,
  uploadOutro,
} from '../api.js'
import { useDialog } from './DialogProvider.jsx'
import ThumbnailTool from './ThumbnailTool.jsx'
import YouTubePublishPanel from './YouTubePublishPanel.jsx'

function fmtSec(value) {
  const total = Math.max(0, Math.floor(value || 0))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${m}:${String(s).padStart(2, '0')}`
}

function safeName(value, fallback = 'video') {
  return (value || '')
    .replace(/\.[^.]+$/, '')
    .replace(/[^\w-]+/g, '_')
    .replace(/^_+|_+$/g, '') || fallback
}

function CompletedVideoPreview({ project, filename, mode }) {
  if (!filename) return null
  const isVertical = mode === '9x16'
  const label = isVertical ? 'Bản hoàn thành 9:16' : 'Bản hoàn thành 16:9'
  return (
    <article className="min-w-0 rounded-xl border border-surface-600 bg-surface-950/60 p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-white">
          <Video size={14} className="text-accent-400" />
          {label}
        </span>
        <a
          href={outputVideoUrl(project, filename)}
          download={filename}
          className="shrink-0 text-xs text-accent-300 hover:text-white transition-colors"
          title={`Tải ${label}`}
        >
          <Download size={14} />
        </a>
      </div>
      <div className={isVertical ? 'mx-auto aspect-[9/16] h-[19rem] max-w-full overflow-hidden rounded-lg bg-black' : 'aspect-video overflow-hidden rounded-lg bg-black'}>
        <video
          controls
          preload="metadata"
          src={outputVideoUrl(project, filename)}
          className="h-full w-full object-contain"
        />
      </div>
      <p className="truncate text-[11px] text-slate-500" title={filename}>{filename}</p>
    </article>
  )
}

function subtitleCueLimits(mode, style = {}) {
  const suffix = mode === '9x16' ? '9x16' : '16x9'
  const defaults = mode === '9x16' ? { maxChars: 60, maxWords: 18 } : { maxChars: 110, maxWords: 24 }
  return {
    maxChars: Math.max(12, Math.min(180, Number(style[`maxCueChars${suffix}`]) || defaults.maxChars)),
    maxWords: Math.max(2, Math.min(36, Number(style[`maxCueWords${suffix}`]) || defaults.maxWords)),
  }
}

export default function PublishTool({
  project,
  sessionId,
  storyTitle,
  storyText,
  audioFile,
  subtitleSettings = {},
  outputLocale = 'vi-VN',
  verticalVideo,
  horizontalVideo,
  videoSettings,
}) {
  const { notify } = useDialog()
  const outroRef = useRef(null)
  const [cutInput, setCutInput] = useState(null)
  const [cutBaseName, setCutBaseName] = useState('')
  const [cutOutputDir, setCutOutputDir] = useState('')
  const [minutes, setMinutes] = useState(3)
  const [seconds, setSeconds] = useState(0)
  const [cutting, setCutting] = useState(false)
  const [cutPercent, setCutPercent] = useState(0)
  const [cutStatus, setCutStatus] = useState('')
  const [cutFiles, setCutFiles] = useState([])
  const [cutError, setCutError] = useState('')

  const [outroPath, setOutroPath] = useState('')
  const [outroMeta, setOutroMeta] = useState(null)
  const [outroOutputDir, setOutroOutputDir] = useState('')
  const [outroOutputName, setOutroOutputName] = useState('')
  const [overwriteMode, setOverwriteMode] = useState('version')
  const [uploadingOutro, setUploadingOutro] = useState(false)
  const [appending, setAppending] = useState(false)
  const [appendPercent, setAppendPercent] = useState(0)
  const [appendStatus, setAppendStatus] = useState('')
  const [appendResult, setAppendResult] = useState(null)
  const [appendError, setAppendError] = useState('')
  const [videoDescription, setVideoDescription] = useState('')
  const [descriptionHydrated, setDescriptionHydrated] = useState(false)
  const [descriptionSaving, setDescriptionSaving] = useState(false)
  const [descriptionBusy, setDescriptionBusy] = useState(false)
  const [descriptionError, setDescriptionError] = useState('')

  const segmentSec = Number(minutes || 0) * 60 + Number(seconds || 0)
  const estimatedParts = cutInput?.duration_sec && segmentSec > 0
    ? Math.ceil(cutInput.duration_sec / segmentSec)
    : 0

  useEffect(() => {
    let cancelled = false
    if (!project || !verticalVideo) return
    setCutInput(null)
    setCutError('')
    registerProjectVideoForCut(project, verticalVideo)
      .then(meta => {
        if (cancelled) return
        setCutInput(meta)
        setCutBaseName(safeName(project || verticalVideo, 'short'))
      })
      .catch(e => {
        if (!cancelled) setCutError(e.message || 'Không lấy được video 9:16')
      })
    return () => { cancelled = true }
  }, [project, verticalVideo])

  useEffect(() => {
    let cancelled = false
    getConfig()
      .then(cfg => {
        if (cancelled) return
        if (cfg.outro_video_path) setOutroPath(cfg.outro_video_path)
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    let cancelled = false
    setDescriptionHydrated(false)
    setVideoDescription('')
    if (!project) return () => { cancelled = true }
    getPostVideoDescription(project)
      .then(data => {
        if (cancelled) return
        setVideoDescription(data.description || '')
        setDescriptionHydrated(true)
      })
      .catch(() => {
        if (!cancelled) setDescriptionHydrated(true)
      })
    return () => { cancelled = true }
  }, [project])

  useEffect(() => {
    if (!project || !descriptionHydrated) return
    const timer = setTimeout(() => {
      setDescriptionSaving(true)
      savePostVideoDescription(project, videoDescription)
        .catch(() => {})
        .finally(() => setDescriptionSaving(false))
    }, 900)
    return () => clearTimeout(timer)
  }, [project, videoDescription, descriptionHydrated])

  useEffect(() => {
    if (!outroOutputName && horizontalVideo) {
      setOutroOutputName(`${safeName(horizontalVideo, 'video')}_with_outro.mp4`)
    }
  }, [horizontalVideo, outroOutputName])

  async function handleOutroUpload(file) {
    if (!file) return
    setUploadingOutro(true)
    setAppendError('')
    try {
      const meta = await uploadOutro(file)
      setOutroMeta(meta)
      setOutroPath(meta.path)
      notify({
        tone: 'success',
        title: 'Đã lưu outro',
        message: 'Outro này sẽ được dùng lại cho các lần sau.',
        details: meta.path,
      })
    } catch (e) {
      notify({ tone: 'error', title: 'Upload outro thất bại', message: e.message })
    } finally {
      setUploadingOutro(false)
    }
  }

  async function chooseFolder(title, currentValue, setter, setError) {
    if (setError) setError('')
    try {
      const result = await selectFolder(title, currentValue)
      if (result.path) setter(result.path)
    } catch (e) {
      const message = e.message || 'Không mở được hộp thoại chọn thư mục'
      if (setError) setError(message)
      else notify({ tone: 'error', title: 'Không chọn được thư mục', message })
    }
  }

  async function handleGenerateDescription() {
    setDescriptionError('')
    if (!storyText?.trim()) {
      setDescriptionError('Chưa có nội dung truyện để AI viết mô tả.')
      return
    }
    setDescriptionBusy(true)
    try {
      const data = await generatePostVideoDescription({
        project,
        story_title: storyTitle || project,
        story_text: storyText,
      })
      setVideoDescription(data.description || '')
      setDescriptionHydrated(true)
      notify({
        tone: data.warning ? 'warning' : 'success',
        title: data.warning ? 'Đã tạo mô tả nháp' : 'Đã tạo mô tả video',
        message: data.warning || '',
      })
    } catch (e) {
      setDescriptionError(e?.message || String(e))
    } finally {
      setDescriptionBusy(false)
    }
  }

  function handleCut() {
    setCutError('')
    setCutFiles([])
    setCutPercent(0)
    if (!cutInput?.id) {
      setCutError('Chưa có video 9:16 để cắt.')
      return
    }
    if (!cutOutputDir.trim()) {
      setCutError('Hãy nhập thư mục lưu trước khi cắt.')
      return
    }
    if (!cutBaseName.trim()) {
      setCutError('Hãy nhập tên video trước khi cắt.')
      return
    }
    if (segmentSec <= 0) {
      setCutError('Thời lượng mỗi đoạn phải lớn hơn 0.')
      return
    }
    setCutting(true)
    setCutStatus('Đang chuẩn bị cắt video...')
    startVideoCut(
      {
        upload_id: cutInput.id,
        base_name: cutBaseName.trim(),
        minutes: Number(minutes || 0),
        seconds: Number(seconds || 0),
        output_dir: cutOutputDir.trim(),
      },
      (ev) => {
        if (ev.type === 'start') setCutStatus(`Sẽ tạo ${ev.total} đoạn video`)
        if (ev.type === 'segment_start') setCutStatus(`Đang cắt ${ev.filename}`)
        if (ev.type === 'segment_done') {
          setCutPercent(ev.percent || 0)
          setCutFiles(prev => [...prev, ev.file])
          setCutStatus(`Đã cắt ${ev.index}/${ev.total}`)
        }
        if (ev.type === 'error') {
          setCutError(ev.message || 'Cắt video thất bại')
          setCutting(false)
        }
        if (ev.type === 'done') {
          setCutFiles(ev.files || [])
          setCutPercent(100)
          setCutStatus('Hoàn thành')
        }
      },
      () => setCutting(false),
      (e) => {
        setCutError(e?.message || String(e))
        setCutting(false)
      },
    )
  }

  function handleAppendOutro() {
    setAppendError('')
    setAppendResult(null)
    setAppendPercent(0)
    if (!horizontalVideo) {
      setAppendError('Chưa có video 16:9 để ghép outro.')
      return
    }
    if (!outroPath.trim()) {
      setAppendError('Hãy upload hoặc nhập đường dẫn file outro.')
      return
    }
    if (!outroOutputDir.trim()) {
      setAppendError('Hãy nhập thư mục lưu file đã ghép outro.')
      return
    }
    if (!outroOutputName.trim()) {
      setAppendError('Hãy nhập tên file output.')
      return
    }
    setAppending(true)
    setAppendStatus('Đang chuẩn bị ghép outro...')
    startAppendOutro(
      {
        project,
        input_video: horizontalVideo,
        outro_path: outroPath.trim(),
        output_name: outroOutputName.trim(),
        output_dir: outroOutputDir.trim(),
        overwrite_mode: overwriteMode,
        video_encoder: videoSettings?.videoEncoder || 'nvidia_nvenc',
        quality: videoSettings?.quality || 'high',
      },
      (ev) => {
        if (ev.type === 'step') {
          setAppendStatus(ev.message || 'Đang xử lý...')
          if (ev.percent) setAppendPercent(ev.percent)
        }
        if (ev.type === 'progress') {
          setAppendPercent(ev.percent || 0)
          setAppendStatus(`${ev.message || 'Đang xử lý...'} ${ev.percent || 0}%`)
        }
        if (ev.type === 'error') {
          setAppendError(ev.message || 'Ghép outro thất bại')
          setAppending(false)
        }
        if (ev.type === 'done') {
          setAppendResult(ev)
          setAppendPercent(100)
          setAppendStatus('Hoàn thành')
        }
      },
      () => setAppending(false),
      (e) => {
        setAppendError(e?.message || String(e))
        setAppending(false)
      },
    )
  }

  return (
    <div className="space-y-5 text-left">
      <div className="text-center space-y-2">
        <div className="mx-auto w-12 h-12 rounded-2xl bg-accent-500/10 border border-accent-500/20 flex items-center justify-center">
          <Film size={22} className="text-accent-300" />
        </div>
        <h2 className="text-xl font-bold text-white">Hậu kỳ / Xuất bản</h2>
        <p className="text-sm text-slate-400">
          Cắt video Shorts từ bản 9:16 và ghép outro cho bản 16:9.
        </p>
      </div>

      {(horizontalVideo || verticalVideo) && (
        <section className="grid grid-cols-1 gap-4 md:grid-cols-[minmax(0,16fr)_minmax(11rem,9fr)] md:items-start">
          <CompletedVideoPreview project={project} filename={horizontalVideo} mode="16x9" />
          <CompletedVideoPreview project={project} filename={verticalVideo} mode="9x16" />
        </section>
      )}

      {audioFile && (horizontalVideo || verticalVideo) && (
        <section className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-bold text-white">File phụ đề SRT</h3>
              <p className="text-xs text-slate-500">Tải cue khớp với mật độ phụ đề đã dùng khi render từng tỉ lệ video.</p>
            </div>
            <div className="flex flex-wrap gap-2">
              {horizontalVideo && (() => {
                const limits = subtitleCueLimits('16x9', subtitleSettings)
                return (
                  <a
                    href={subtitleSrtUrl(project, audioFile, '16x9', limits.maxChars, limits.maxWords)}
                    download
                    className="inline-flex items-center gap-2 rounded-xl border border-accent-400/40 px-3 py-2 text-xs font-semibold text-accent-200 hover:border-accent-300 hover:text-white"
                  >
                    <Download size={14} /> Tải SRT 16:9
                  </a>
                )
              })()}
              {verticalVideo && (() => {
                const limits = subtitleCueLimits('9x16', subtitleSettings)
                return (
                  <a
                    href={subtitleSrtUrl(project, audioFile, '9x16', limits.maxChars, limits.maxWords)}
                    download
                    className="inline-flex items-center gap-2 rounded-xl border border-accent-400/40 px-3 py-2 text-xs font-semibold text-accent-200 hover:border-accent-300 hover:text-white"
                  >
                    <Download size={14} /> Tải SRT 9:16
                  </a>
                )
              })()}
            </div>
          </div>
        </section>
      )}

      <section className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-bold text-white">Mô tả video</h3>
            <p className="text-xs text-slate-500">
              AI viết teaser hấp dẫn, tránh tiết lộ twist/kết truyện.
              {descriptionHydrated && (
                <span className="ml-2 text-slate-400">{descriptionSaving ? 'Đang lưu...' : 'Đã lưu vào project'}</span>
              )}
            </p>
          </div>
          <button
            type="button"
            onClick={handleGenerateDescription}
            disabled={descriptionBusy || !storyText?.trim()}
            className="inline-flex items-center gap-2 rounded-lg border border-surface-500 px-3 py-2 text-xs font-semibold text-slate-300 hover:text-white hover:border-accent-400 disabled:opacity-40 transition-colors"
          >
            {descriptionBusy ? <Loader2 size={14} className="animate-spin" /> : <Film size={14} />}
            Tạo mô tả
          </button>
        </div>
        <textarea
          value={videoDescription}
          onChange={e => setVideoDescription(e.target.value)}
          onBlur={() => project && savePostVideoDescription(project, videoDescription).catch(() => {})}
          rows={6}
          placeholder="Bấm Tạo mô tả để AI viết, sau đó chỉnh lại nếu cần..."
          className="scene-prompt-editor w-full rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent-500 resize-y"
        />
        {descriptionError && (
          <div className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
            <AlertCircle size={15} className="mt-0.5 shrink-0" />
            <span>{descriptionError}</span>
          </div>
        )}
      </section>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <section className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Scissors size={16} className="text-accent-400" />
              <h3 className="text-sm font-bold text-white">Cắt Shorts 9:16</h3>
            </div>
            {cutInput && <span className="text-xs text-slate-500 font-mono">{fmtSec(cutInput.duration_sec)}</span>}
          </div>

          <div className="rounded-xl border border-surface-600 bg-black/20 px-3 py-2 text-xs text-slate-400 truncate">
            Input: <span className="text-white">{verticalVideo || 'Chưa có video 9:16'}</span>
          </div>

          <label className="block space-y-1">
            <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Thư mục lưu</span>
            <div className="flex gap-2">
              <input
                value={cutOutputDir}
                onChange={e => setCutOutputDir(e.target.value)}
                placeholder="vd: D:\\Videos\\Shorts"
                className="flex-1 min-w-0 bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
              />
              <button
                type="button"
                onClick={() => chooseFolder('Chọn thư mục lưu Shorts', cutOutputDir, setCutOutputDir, setCutError)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-surface-500 px-3 py-2 text-xs font-semibold text-slate-300 hover:text-white hover:border-accent-400 transition-colors shrink-0"
              >
                <FolderOpen size={13} />
                Chọn
              </button>
            </div>
          </label>

          <div className="grid grid-cols-3 gap-3">
            <label className="space-y-1">
              <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Tên video</span>
              <input value={cutBaseName} onChange={e => setCutBaseName(e.target.value)} className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500" />
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Phút</span>
              <input type="number" min="0" value={minutes} onChange={e => setMinutes(e.target.value)} className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500" />
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Giây</span>
              <input type="number" min="0" max="59" value={seconds} onChange={e => setSeconds(e.target.value)} className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500" />
            </label>
          </div>

          <div className="flex items-center justify-between text-xs text-slate-500">
            <span>Mỗi đoạn: <span className="font-mono text-white">{fmtSec(segmentSec)}</span></span>
            <span>Dự kiến: <span className="font-mono text-white">{estimatedParts || 0}</span> video</span>
          </div>

          {cutting && (
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-400">{cutStatus}</span>
                <span className="font-mono text-white">{cutPercent}%</span>
              </div>
              <div className="h-2 rounded-full bg-surface-700 overflow-hidden">
                <div className="h-full bg-accent-500 transition-all" style={{ width: `${cutPercent}%` }} />
              </div>
            </div>
          )}

          {cutError && (
            <div className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              <AlertCircle size={15} className="mt-0.5 shrink-0" />
              <span>{cutError}</span>
            </div>
          )}

          <button
            onClick={handleCut}
            disabled={cutting || !cutInput || !cutOutputDir.trim() || segmentSec <= 0}
            className="w-full inline-flex items-center justify-center gap-2 rounded-xl bg-accent-600 hover:bg-accent-500 disabled:opacity-40 disabled:cursor-not-allowed px-4 py-3 text-sm font-semibold text-white transition-colors"
          >
            {cutting ? <Loader2 size={16} className="animate-spin" /> : <Scissors size={16} />}
            Cắt Shorts
          </button>

          {cutFiles.length > 0 && (
            <div className="rounded-xl border border-surface-600 overflow-hidden">
              <div className="px-3 py-2 border-b border-surface-600 flex items-center gap-2">
                <CheckCircle2 size={14} className="text-green-400" />
                <span className="text-xs font-semibold text-white">Đã cắt {cutFiles.length} video</span>
              </div>
              <div className="max-h-56 overflow-y-auto divide-y divide-surface-700">
                {cutFiles.map(file => (
                  <div key={file.filename} className="px-3 py-2 flex items-center justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-xs text-white truncate">{file.filename}</p>
                      <p className="text-xs text-slate-600 truncate">{file.saved_path}</p>
                    </div>
                    {file.url && (
                      <a href={cutVideoDownloadUrl(file.filename)} download={file.filename} className="text-xs text-accent-300 hover:text-white">
                        <Download size={13} />
                      </a>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </section>

        <section className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-4">
          <div className="flex items-center gap-2">
            <Video size={16} className="text-accent-400" />
            <h3 className="text-sm font-bold text-white">Ghép outro 16:9</h3>
          </div>

          <div className="rounded-xl border border-surface-600 bg-black/20 px-3 py-2 text-xs text-slate-400 truncate">
            Input: <span className="text-white">{horizontalVideo || 'Chưa có video 16:9'}</span>
          </div>

          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <button
                onClick={() => outroRef.current?.click()}
                disabled={uploadingOutro}
                className="inline-flex items-center gap-2 rounded-lg border border-surface-500 px-3 py-2 text-xs font-semibold text-slate-300 hover:text-white hover:border-accent-400 disabled:opacity-40"
              >
                {uploadingOutro ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
                Upload outro
              </button>
              <span className="text-xs text-slate-500 truncate">
                {outroMeta?.filename || (outroPath ? 'Đang dùng outro đã lưu' : 'Chưa có outro')}
              </span>
              <input ref={outroRef} type="file" accept="video/*" className="hidden" onChange={e => handleOutroUpload(e.target.files?.[0])} />
            </div>
            <input
              value={outroPath}
              onChange={e => setOutroPath(e.target.value)}
              placeholder="Đường dẫn outro, hoặc upload để tự lưu dùng lại"
              className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
            />
          </div>

          <label className="block space-y-1">
            <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Thư mục lưu</span>
            <div className="flex gap-2">
              <input
                value={outroOutputDir}
                onChange={e => setOutroOutputDir(e.target.value)}
                placeholder="vd: D:\\Videos\\Full"
                className="flex-1 min-w-0 bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
              />
              <button
                type="button"
                onClick={() => chooseFolder('Chọn thư mục lưu video đã ghép outro', outroOutputDir, setOutroOutputDir, setAppendError)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-surface-500 px-3 py-2 text-xs font-semibold text-slate-300 hover:text-white hover:border-accent-400 transition-colors shrink-0"
              >
                <FolderOpen size={13} />
                Chọn
              </button>
            </div>
          </label>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className="space-y-1">
              <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Tên file output</span>
              <input value={outroOutputName} onChange={e => setOutroOutputName(e.target.value)} className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500" />
            </label>
            <label className="space-y-1">
              <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Nếu trùng file</span>
              <AppSelect value={overwriteMode} onChange={e => setOverwriteMode(e.target.value)} className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500">
                <option value="version">Tạo bản mới</option>
                <option value="overwrite">Ghi đè</option>
              </AppSelect>
            </label>
          </div>

          {appending && (
            <div className="space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-400">{appendStatus}</span>
                <span className="font-mono text-white">{appendPercent}%</span>
              </div>
              <div className="h-2 rounded-full bg-surface-700 overflow-hidden">
                <div className="h-full bg-accent-500 transition-all" style={{ width: `${appendPercent}%` }} />
              </div>
            </div>
          )}

          {appendError && (
            <div className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              <AlertCircle size={15} className="mt-0.5 shrink-0" />
              <span>{appendError}</span>
            </div>
          )}

          <button
            onClick={handleAppendOutro}
            disabled={appending || !horizontalVideo || !outroPath.trim() || !outroOutputDir.trim() || !outroOutputName.trim()}
            className="w-full inline-flex items-center justify-center gap-2 rounded-xl bg-accent-600 hover:bg-accent-500 disabled:opacity-40 disabled:cursor-not-allowed px-4 py-3 text-sm font-semibold text-white transition-colors"
          >
            {appending ? <Loader2 size={16} className="animate-spin" /> : <Film size={16} />}
            Ghép Outro
          </button>

          {appendResult && (
            <div className="rounded-xl border border-green-500/30 bg-green-500/10 p-3 space-y-1">
              <div className="flex items-center gap-2 text-sm font-semibold text-green-300">
                <CheckCircle2 size={15} />
                Đã tạo file có outro
              </div>
              <p className="text-xs text-white">{appendResult.filename}</p>
              <p className="text-xs text-slate-500 break-all">{appendResult.saved_path}</p>
              <p className="text-xs text-slate-500">{fmtSec(appendResult.duration_sec)} · {appendResult.size_mb}MB</p>
            </div>
          )}
        </section>
      </div>

      <ThumbnailTool
        project={project}
        storyTitle={storyTitle}
        storyText={storyText}
        sessionId={sessionId}
        outputLocale={outputLocale}
      />

      <YouTubePublishPanel
        project={project}
        storyTitle={storyTitle}
        storyText={storyText}
        horizontalVideo={horizontalVideo}
        verticalVideo={verticalVideo}
        cutFiles={cutFiles}
        appendResult={appendResult}
      />
    </div>
  )
}
