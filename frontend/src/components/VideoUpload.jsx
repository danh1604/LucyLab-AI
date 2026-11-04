import AppSelect from './AppSelect.jsx'
import { useState, useRef, useCallback, useEffect } from 'react'
import { Upload, Trash2, Video, AlertCircle, CheckCircle2, Loader2, GripVertical, Images } from 'lucide-react'
import { uploadVideo, deleteVideo, listVideos } from '../api.js'
import SceneImageVideoTool from './SceneImageVideoTool.jsx'
import LoopVideoTool from './LoopVideoTool.jsx'

function fmtSec(s) {
  if (!s || s <= 0) return '0:00'
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = Math.floor(s % 60)
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
  return `${m}:${String(sec).padStart(2, '0')}`
}

const DEFAULT_SETTINGS = {
  outputName: 'final_video.mp4',
  videoEncoder: 'nvidia_nvenc',
  quality: 'high',
  overwriteMode: 'version',
  videoMode: 'background',
  autoGenerateScenesAfterReferences: false,
  directorUseVisualPlan: true,
  sceneReveal: { enabled: false, effect: 'brush', durationSec: 1.2, pattern: 'auto', actor: 'none' },
}

function projectVideoName(project) {
  const stem = String(project || '')
    .replace(/[<>:"/\\|?*\x00-\x1f]+/g, ' ')
    .trim()
    .replace(/[^\p{L}\p{N}._-]+/gu, '_')
    .replace(/^[_\-.]+|[_\-.]+$/g, '')
  return `${stem || 'video'}.mp4`
}

export default function VideoUpload({
  project,
  audioFile,
  audioDuration,
  storyText = '',
  storyTitle = '',
  structuredStoryboard = null,
  outputLocale = 'vi-VN',
  initialSettings = DEFAULT_SETTINGS,
  onSettingsChange,
  onCompose,
}) {
  const [videos, setVideos] = useState([])
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState('')
  const [error, setError] = useState('')
  const [dragging, setDragging] = useState(false)
  const [settings, setSettings] = useState({ ...DEFAULT_SETTINGS, ...initialSettings })
  const [mode, setMode] = useState(initialSettings?.videoMode || 'background')
  const fileInputRef = useRef(null)

  const totalDuration = videos.reduce((s, v) => s + (v.duration_sec || 0), 0)
  const hasEnough = totalDuration >= audioDuration
  const excess = totalDuration - audioDuration
  const shortage = audioDuration - totalDuration

  useEffect(() => {
    const next = {
      ...DEFAULT_SETTINGS,
      ...initialSettings,
      sceneReveal: { ...DEFAULT_SETTINGS.sceneReveal, ...(initialSettings?.sceneReveal || {}) },
    }
    // New projects use their project name as the downloadable video name.
    // Preserve an explicit name and names saved in an existing session.
    if (project && (!initialSettings?.outputName || initialSettings.outputName === 'final_video.mp4')) {
      next.outputName = projectVideoName(project)
    }
    setSettings(next)
    setMode(initialSettings?.videoMode || 'background')
  }, [initialSettings, project])

  useEffect(() => {
    if (!project) return
    let cancelled = false
    listVideos(project)
      .then(data => {
        if (!cancelled) setVideos(Array.isArray(data) ? data : [])
      })
      .catch(() => {})
    return () => { cancelled = true }
  }, [project])

  const handleFiles = useCallback(async (files) => {
    setError('')
    const validExts = ['.mp4', '.mov', '.mkv', '.avi', '.webm']
    const toUpload = Array.from(files).filter(f =>
      validExts.some(ext => f.name.toLowerCase().endsWith(ext))
    )
    if (!toUpload.length) {
      setError('Chỉ chấp nhận file video: mp4, mov, mkv, avi, webm')
      return
    }

    setUploading(true)
    for (const file of toUpload) {
      setUploadProgress(`Đang upload ${file.name}...`)
      try {
        const result = await uploadVideo(project, file)
        setVideos(prev => [...prev, result])
      } catch (e) {
        setError(`Lỗi upload ${file.name}: ${e.message}`)
      }
    }
    setUploading(false)
    setUploadProgress('')
  }, [project])

  async function handleDelete(filename, i) {
    try {
      await deleteVideo(project, filename)
      setVideos(prev => prev.filter((_, idx) => idx !== i))
    } catch (e) {
      setError(`Lỗi xóa: ${e.message}`)
    }
  }

  function onDrop(e) {
    e.preventDefault()
    setDragging(false)
    handleFiles(e.dataTransfer.files)
  }

  function updateSettings(patch) {
    const next = { ...settings, ...patch }
    setSettings(next)
    onSettingsChange?.(next)
  }

  function updateMode(nextMode) {
    setMode(nextMode)
    const outputName = settings.outputName === 'final_video.mp4'
      ? nextMode === 'scenes' ? 'final_video_scene.mp4'
        : nextMode === 'loop' ? 'final_video_loop.mp4'
          : settings.outputName
      : settings.outputName
    updateSettings({ videoMode: nextMode, outputName })
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Video size={16} className="text-accent-400" />
          <h2 className="text-lg font-bold text-white">Upload Video Nền</h2>
        </div>
        <span className="text-xs text-slate-500">
          Audio: <span className="font-mono text-white">{fmtSec(audioDuration)}</span>
        </span>
      </div>

      <div className="inline-flex rounded-xl border border-surface-600 bg-surface-900 p-1">
        <button
          type="button"
          onClick={() => updateMode('background')}
          className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-colors ${
            mode === 'background' ? 'bg-accent-600 text-white' : 'text-slate-400 hover:text-white'
          }`}
        >
          <Video size={15} />
          Video nền
        </button>
        <button
          type="button"
          onClick={() => updateMode('scenes')}
          className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-colors ${
            mode === 'scenes' ? 'bg-accent-600 text-white' : 'text-slate-400 hover:text-white'
          }`}
        >
          <Images size={15} />
          Ảnh theo cảnh
        </button>
        <button
          type="button"
          onClick={() => updateMode('loop')}
          className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-colors ${
            mode === 'loop' ? 'bg-accent-600 text-white' : 'text-slate-400 hover:text-white'
          }`}
        >
          <Video size={15} />
          Video loop
        </button>
      </div>

      {mode === 'background' && (
        <>
      {/* Drop zone */}
      <div
        onDragOver={e => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => fileInputRef.current?.click()}
        className={`border-2 border-dashed rounded-2xl p-8 text-center cursor-pointer transition-all ${
          dragging ? 'border-accent-400 bg-accent-500/10' : 'border-surface-500 hover:border-slate-500 hover:bg-surface-800'
        }`}
      >
        <Upload size={24} className={`mx-auto mb-2 ${dragging ? 'text-accent-400' : 'text-slate-500'}`} />
        <p className="text-sm text-slate-400">Kéo thả hoặc click để chọn video</p>
        <p className="text-xs text-slate-600 mt-1">MP4, MOV, MKV, AVI, WEBM · Chọn nhiều file cùng lúc</p>
        <input
          ref={fileInputRef}
          type="file"
          accept="video/*"
          multiple
          className="hidden"
          onChange={e => handleFiles(e.target.files)}
        />
      </div>

      {uploading && (
        <div className="flex items-center gap-2 text-sm text-accent-400">
          <Loader2 size={14} className="animate-spin" />
          {uploadProgress}
        </div>
      )}

      {error && <p className="text-xs text-red-400">{error}</p>}

      {/* Video list */}
      {videos.length > 0 && (
        <div className="space-y-2">
          {videos.map((v, i) => (
            <div key={i} className="glass rounded-xl px-4 py-3 flex items-center gap-3">
              <GripVertical size={14} className="text-slate-600 cursor-grab" />
              <Video size={14} className="text-accent-400 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-white truncate">{v.filename}</p>
                <p className="text-xs text-slate-500 font-mono">{fmtSec(v.duration_sec)} · {v.size_mb}MB</p>
              </div>
              <button
                onClick={() => handleDelete(v.filename, i)}
                className="p-1.5 text-slate-600 hover:text-red-400 transition-colors"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Duration status */}
      {videos.length > 0 && (
        <div className={`rounded-xl p-4 border ${hasEnough ? 'border-green-500/30 bg-green-500/10' : 'border-red-500/30 bg-red-500/10'}`}>
          <div className="flex items-center justify-between mb-2">
            <span className="flex items-center gap-1.5 text-sm font-medium">
              {hasEnough
                ? <><CheckCircle2 size={14} className="text-green-400" /><span className="text-green-400">Đủ độ dài video</span></>
                : <><AlertCircle size={14} className="text-red-400" /><span className="text-red-400">Chưa đủ độ dài</span></>
              }
            </span>
            <span className="text-xs font-mono text-slate-400">
              {fmtSec(totalDuration)} / {fmtSec(audioDuration)}
            </span>
          </div>

          {/* Progress bar */}
          <div className="h-1.5 bg-surface-700 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all ${hasEnough ? 'bg-green-500' : 'bg-red-500'}`}
              style={{ width: `${Math.min(100, (totalDuration / audioDuration) * 100)}%` }}
            />
          </div>

          <p className="text-xs mt-2">
            {hasEnough
              ? excess > 1
                ? <span className="text-slate-400">Video cuối sẽ bị cắt bớt <strong className="text-white">{fmtSec(excess)}</strong></span>
                : <span className="text-green-400">Vừa đủ!</span>
              : <span className="text-red-300">Cần thêm <strong className="text-white">{fmtSec(shortage)}</strong> video nữa</span>
            }
          </p>
        </div>
      )}
        </>
      )}

      {mode === 'scenes' && (
        <SceneImageVideoTool
          project={project}
          audioFile={audioFile}
          storyText={storyText}
          storyTitle={storyTitle}
          structuredStoryboard={structuredStoryboard}
          outputLocale={outputLocale}
          settings={{ ...settings, videoMode: 'scenes' }}
          onSettingsChange={onSettingsChange}
          onCompose={onCompose}
        />
      )}

      {mode === 'loop' && (
        <LoopVideoTool
          project={project}
          settings={{ ...settings, videoMode: 'loop' }}
          onSettingsChange={onSettingsChange}
          onCompose={onCompose}
        />
      )}

      <div className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-3">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-white">Cấu hình xuất video</p>
            <p className="text-xs text-slate-500">Mặc định dùng NVIDIA NVENC để giảm tải CPU</p>
          </div>
          <span className="text-xs text-accent-300 bg-accent-500/10 border border-accent-500/20 rounded-full px-2 py-1">
            {settings.videoEncoder === 'nvidia_nvenc' ? 'GPU' : settings.videoEncoder === 'intel_qsv' ? 'Intel GPU' : 'CPU'}
          </span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label className="space-y-1">
            <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Tên file</span>
            <input
              value={settings.outputName}
              onChange={e => updateSettings({ outputName: e.target.value })}
              className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
              placeholder={projectVideoName(project)}
            />
          </label>

          <label className="space-y-1">
            <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Encoder</span>
            <AppSelect
              value={settings.videoEncoder}
              onChange={e => updateSettings({ videoEncoder: e.target.value })}
              className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
            >
              <option value="nvidia_nvenc">NVIDIA NVENC (khuyên dùng)</option>
              <option value="cpu_x264">CPU libx264</option>
              <option value="intel_qsv">Intel Quick Sync</option>
            </AppSelect>
          </label>

          <label className="space-y-1">
            <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Chất lượng</span>
            <AppSelect
              value={settings.quality}
              onChange={e => updateSettings({ quality: e.target.value })}
              className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
            >
              <option value="fast">Nhanh</option>
              <option value="balanced">Cân bằng</option>
              <option value="high">Chất lượng cao</option>
            </AppSelect>
          </label>

          <label className="space-y-1">
            <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Nếu trùng file</span>
            <AppSelect
              value={settings.overwriteMode}
              onChange={e => updateSettings({ overwriteMode: e.target.value })}
              className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
            >
              <option value="version">Tạo bản mới</option>
              <option value="overwrite">Ghi đè</option>
            </AppSelect>
          </label>
        </div>
      </div>

      {mode === 'background' && (
      <button
        onClick={() => onCompose(videos.map(v => v.filename), { ...settings, videoMode: 'background' })}
        disabled={!hasEnough || uploading}
        className="w-full flex items-center justify-center gap-2 py-3 rounded-xl text-sm font-semibold bg-accent-600 hover:bg-accent-500 text-white disabled:opacity-40 disabled:cursor-not-allowed transition-all"
      >
        <Video size={16} />
        Tạo Video
      </button>
      )}
    </div>
  )
}
