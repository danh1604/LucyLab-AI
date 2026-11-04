import { useEffect, useMemo, useRef, useState } from 'react'
import { CalendarClock, CheckCircle2, ChevronDown, ChevronUp, Download, ExternalLink, Image, Loader2, LogIn, Pause, Play, RefreshCw, Scissors, UploadCloud, Wand2, Youtube } from 'lucide-react'
import {
  buildShortsCampaign,
  createYouTubePublishPlan,
  generateYouTubeMetadata,
  generateYouTubeThumbnail,
  getYouTubeAuthStatus,
  getYouTubePublishPlan,
  logoutYouTube,
  startYouTubeAuth,
  startYouTubeUpload,
  updateYouTubePublishItem,
  youtubeThumbnailUrl,
  youtubePublishVideoUrl,
} from '../api.js'
import { useDialog } from './DialogProvider.jsx'

function toLocalInput(value) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function fromLocalInput(value) {
  if (!value) return ''
  return new Date(value).toISOString()
}

function fmtDate(value) {
  if (!value) return 'Chưa đặt lịch'
  try {
    return new Intl.DateTimeFormat('vi-VN', {
      dateStyle: 'short',
      timeStyle: 'short',
    }).format(new Date(value))
  } catch {
    return value
  }
}

function statusLabel(status) {
  if (status === 'scheduled') return 'Đã lên lịch'
  if (status === 'unlisted') return 'Không công khai'
  if (status === 'uploading') return 'Đang upload'
  if (status === 'failed') return 'Lỗi'
  if (status === 'approved') return 'Đã duyệt'
  return 'Nháp'
}

function PublishItem({ project, item, onPlanChange, onRegenerateShort, regeneratingRole }) {
  const { notify } = useDialog()
  const [draft, setDraft] = useState(item)
  const [busy, setBusy] = useState('')
  const [uploadPercent, setUploadPercent] = useState(0)
  const [previewError, setPreviewError] = useState('')
  const [previewPlaying, setPreviewPlaying] = useState(false)
  const previewRef = useRef(null)

  useEffect(() => setDraft(item), [item])

  async function savePatch(patch) {
    const plan = await updateYouTubePublishItem(project, item.id, patch)
    onPlanChange(plan)
  }

  async function handleQuickSave(patch) {
    try {
      await savePatch(patch)
    } catch (e) {
      notify({ tone: 'error', title: 'Không lưu draft YouTube được', message: e.message })
    }
  }

  async function handleSave() {
    setBusy('save')
    try {
      const plan = await updateYouTubePublishItem(project, item.id, {
        title: draft.title,
        description: draft.description,
        tags: String(draft.tags || '').split(',').map(x => x.trim()).filter(Boolean),
        scheduled_at: fromLocalInput(toLocalInput(draft.scheduled_at)),
      })
      onPlanChange(plan)
      notify({ tone: 'success', title: 'Đã lưu item YouTube' })
    } catch (e) {
      notify({ tone: 'error', title: 'Không lưu được', message: e.message })
    } finally {
      setBusy('')
    }
  }

  async function handleGenerateMetadata() {
    setBusy('metadata')
    try {
      const plan = await generateYouTubeMetadata(project, item.id)
      onPlanChange(plan)
    } catch (e) {
      notify({ tone: 'error', title: 'Không tạo metadata được', message: e.message })
    } finally {
      setBusy('')
    }
  }

  async function handleGenerateThumbnail() {
    setBusy('thumbnail')
    try {
      const plan = await generateYouTubeThumbnail(project, item.id)
      onPlanChange(plan)
    } catch (e) {
      notify({ tone: 'error', title: 'Không tạo thumbnail được', message: e.message })
    } finally {
      setBusy('')
    }
  }

  function handleUpload(mode = 'scheduled') {
    setBusy(mode === 'unlisted' ? 'unlisted' : 'upload')
    setUploadPercent(0)
    startYouTubeUpload(
      project,
      item.id,
      mode,
      (ev) => {
        if (ev.type === 'progress') setUploadPercent(ev.percent || 0)
        if (ev.type === 'done') {
          setUploadPercent(100)
          notify({
            tone: 'success',
            title: ev.mode === 'unlisted' ? 'Đã upload video không công khai' : 'Đã upload và lên lịch',
            message: ev.youtube_url,
          })
        }
        if (ev.type === 'error') {
          notify({ tone: 'error', title: 'Upload YouTube lỗi', message: ev.message })
          setBusy('')
        }
      },
      async () => {
        setBusy('')
        try { onPlanChange(await getYouTubePublishPlan(project)) } catch {}
      },
      (e) => {
        notify({ tone: 'error', title: 'Upload YouTube lỗi', message: e?.message || String(e) })
        setBusy('')
      },
    )
  }

  function handleRegenerateShort() {
    onRegenerateShort?.(item)
  }

  const tagsValue = Array.isArray(draft.tags) ? draft.tags.join(', ') : (draft.tags || '')

  async function togglePreview() {
    const video = previewRef.current
    if (!video) return
    try {
      if (video.paused) await video.play()
      else video.pause()
    } catch (error) {
      setPreviewError(error?.message || 'Không thể phát preview video.')
    }
  }

  return (
    <article className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className={`text-[11px] font-bold rounded-full px-2 py-0.5 ${item.kind === 'short' ? 'bg-red-500/15 text-red-200' : 'bg-accent-500/15 text-accent-200'}`}>
              {item.kind === 'short' ? 'Shorts 9:16' : 'Video 16:9'}
            </span>
            {item.role && (
              <span className="text-[11px] font-bold rounded-full px-2 py-0.5 bg-amber-500/15 text-amber-200">
                {item.role === 'teaser' ? 'Mồi' : item.role === 'climax' ? 'Cao trào' : item.role}
              </span>
            )}
            <span className="text-xs text-slate-500">{statusLabel(item.status)}</span>
          </div>
          <p className="mt-1 text-sm text-white font-semibold truncate">{item.source_file}</p>
          <p className="text-xs text-slate-500 flex items-center gap-1">
            <CalendarClock size={12} /> {fmtDate(item.scheduled_at)}
          </p>
        </div>
        {item.youtube_url && (
          <a href={item.youtube_url} target="_blank" rel="noreferrer" className="text-accent-300 hover:text-white">
            <ExternalLink size={16} />
          </a>
        )}
      </div>

      {item.kind === 'long' && (
        <div className="grid grid-cols-[11rem_minmax(0,1fr)] gap-3 items-stretch">
          <div className="rounded-xl overflow-hidden bg-black/30 border border-surface-600 aspect-video">
            {item.thumbnail_path ? (
              <img src={`${youtubeThumbnailUrl(project, item.id)}?t=${Date.now()}`} className="w-full h-full object-cover" />
            ) : (
              <div className="w-full h-full flex items-center justify-center text-slate-600 text-xs">Chưa có thumbnail</div>
            )}
          </div>
          <button
            onClick={handleGenerateThumbnail}
            disabled={Boolean(busy)}
            className="rounded-xl border border-surface-500 text-xs font-semibold text-slate-300 hover:text-white hover:border-accent-400 transition-colors flex items-center justify-center gap-2"
          >
            {busy === 'thumbnail' ? <Loader2 size={14} className="animate-spin" /> : <Image size={14} />}
            Tạo thumbnail
          </button>
        </div>
      )}

      {item.kind === 'short' && (
        <div className="flex flex-wrap items-end gap-3">
          <video
            ref={previewRef}
            controls
            playsInline
            preload="metadata"
            src={youtubePublishVideoUrl(project, item.id)}
            onPlay={() => setPreviewPlaying(true)}
            onPause={() => setPreviewPlaying(false)}
            onEnded={() => setPreviewPlaying(false)}
            onError={() => setPreviewError('Trình duyệt không thể phát preview này. Hãy mở video trực tiếp để kiểm tra.')}
            className="w-40 aspect-[9/16] rounded-xl border border-surface-600 bg-black object-cover"
          />
          <button
            type="button"
            onClick={togglePreview}
            className="inline-flex items-center gap-2 rounded-lg border border-accent-400/60 bg-accent-500/10 px-3 py-2 text-xs font-semibold text-accent-100 hover:bg-accent-500/20 hover:text-white"
          >
            {previewPlaying ? <Pause size={14} /> : <Play size={14} />}
            {previewPlaying ? 'Tạm dừng' : 'Xem trước'}
          </button>
          <a
            href={youtubePublishVideoUrl(project, item.id)}
            download={item.source_file || 'short.mp4'}
            className="inline-flex items-center gap-2 rounded-lg border border-surface-500 px-3 py-2 text-xs font-semibold text-slate-300 hover:border-accent-400 hover:text-white"
          >
            <Download size={14} />
            Tải Short
          </a>
          {item.role && !item.youtube_video_id && (
            <button
              type="button"
              onClick={handleRegenerateShort}
              disabled={Boolean(busy) || Boolean(regeneratingRole)}
              title={`Tạo lại riêng Short ${item.role === 'teaser' ? 'Mồi' : 'Cao trào'}`}
              className="inline-flex items-center gap-2 rounded-lg border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-xs font-semibold text-amber-100 transition-colors hover:bg-amber-500/20 hover:text-white disabled:cursor-wait disabled:opacity-50"
            >
              {regeneratingRole === item.role ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
              {regeneratingRole === item.role ? 'Đang tạo lại...' : `Tạo lại ${item.role === 'teaser' ? 'Mồi' : 'Cao trào'}`}
            </button>
          )}
          {previewError && (
            <a href={youtubePublishVideoUrl(project, item.id)} target="_blank" rel="noreferrer" className="text-xs text-amber-200 underline underline-offset-4">
              Mở video trực tiếp
            </a>
          )}
        </div>
      )}

      <label className="block space-y-1">
        <span className="text-xs uppercase tracking-wider text-slate-500 font-semibold">Tiêu đề</span>
        <input
          value={draft.title || ''}
          onChange={e => setDraft(v => ({ ...v, title: e.target.value }))}
          onBlur={() => handleQuickSave({ title: draft.title || '' })}
          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
        />
      </label>

      <label className="block space-y-1">
        <span className="text-xs uppercase tracking-wider text-slate-500 font-semibold">Mô tả</span>
        <textarea
          value={draft.description || ''}
          onChange={e => setDraft(v => ({ ...v, description: e.target.value }))}
          onBlur={() => handleQuickSave({ description: draft.description || '' })}
          rows={4}
          className="scene-prompt-editor w-full rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-accent-500 resize-y"
        />
      </label>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label className="space-y-1">
          <span className="text-xs uppercase tracking-wider text-slate-500 font-semibold">Tags</span>
          <input
            value={tagsValue}
            onChange={e => setDraft(v => ({ ...v, tags: e.target.value }))}
            onBlur={() => handleQuickSave({ tags: String(draft.tags || '').split(',').map(x => x.trim()).filter(Boolean) })}
            className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
          />
        </label>
        <label className="space-y-1">
          <span className="text-xs uppercase tracking-wider text-slate-500 font-semibold">Lịch đăng</span>
          <input
            type="datetime-local"
            value={toLocalInput(draft.scheduled_at)}
            onChange={e => setDraft(v => ({ ...v, scheduled_at: fromLocalInput(e.target.value) }))}
            onBlur={() => handleQuickSave({ scheduled_at: draft.scheduled_at || '' })}
            className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
          />
        </label>
      </div>

      {(busy === 'upload' || busy === 'unlisted') && (
        <div className="space-y-2">
          <div className="flex justify-between text-xs text-slate-400">
            <span>Đang upload YouTube...</span>
            <span>{uploadPercent}%</span>
          </div>
          <div className="h-2 rounded-full bg-surface-700 overflow-hidden">
            <div className="h-full bg-red-500 transition-all" style={{ width: `${uploadPercent}%` }} />
          </div>
        </div>
      )}

      {item.error && <p className="text-xs text-red-300 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2">{item.error}</p>}

      <div className="flex flex-wrap gap-2">
        <button onClick={handleSave} disabled={Boolean(busy)} className="inline-flex items-center gap-2 rounded-lg border border-surface-500 px-3 py-2 text-xs font-semibold text-slate-300 hover:text-white hover:border-accent-400">
          {busy === 'save' ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
          Lưu
        </button>
        <button onClick={handleGenerateMetadata} disabled={Boolean(busy)} className="inline-flex items-center gap-2 rounded-lg border border-surface-500 px-3 py-2 text-xs font-semibold text-slate-300 hover:text-white hover:border-accent-400">
          {busy === 'metadata' ? <Loader2 size={14} className="animate-spin" /> : <Wand2 size={14} />}
          Tạo lại metadata
        </button>
        {item.kind === 'long' && !item.youtube_video_id && (
          <button onClick={() => handleUpload('unlisted')} disabled={Boolean(busy)} className="inline-flex items-center gap-2 rounded-lg border border-amber-500/50 bg-amber-500/10 hover:bg-amber-500/20 px-3 py-2 text-xs font-semibold text-amber-100">
            {busy === 'unlisted' ? <Loader2 size={14} className="animate-spin" /> : <UploadCloud size={14} />}
            Upload không công khai
          </button>
        )}
        <button onClick={() => handleUpload('scheduled')} disabled={Boolean(busy) || item.status === 'scheduled'} className="inline-flex items-center gap-2 rounded-lg bg-red-600 hover:bg-red-500 disabled:opacity-40 px-3 py-2 text-xs font-semibold text-white">
          {busy === 'upload' ? <Loader2 size={14} className="animate-spin" /> : <UploadCloud size={14} />}
          {item.youtube_video_id && item.status === 'unlisted' ? 'Đặt lịch công khai' : 'Duyệt & lên lịch'}
        </button>
      </div>
    </article>
  )
}

export default function YouTubePublishPanel({
  project,
  storyTitle,
  storyText,
  horizontalVideo,
  verticalVideo,
  cutFiles,
  appendResult,
}) {
  const { notify } = useDialog()
  const [auth, setAuth] = useState(null)
  const [plan, setPlan] = useState(null)
  const [campaign, setCampaign] = useState(null)
  const [loading, setLoading] = useState(false)
  const [regeneratingRole, setRegeneratingRole] = useState('')
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem('lucylab_youtube_publish_collapsed') === '1')

  const longSource = useMemo(() => {
    if (appendResult?.saved_path) return { path: appendResult.saved_path }
    if (horizontalVideo) return { filename: horizontalVideo }
    return null
  }, [appendResult, horizontalVideo])

  const shortSources = useMemo(() => {
    if (cutFiles?.length) return cutFiles.map(f => ({ path: f.saved_path || f.path || '', filename: f.filename || '' }))
    if (verticalVideo) return []
    return []
  }, [cutFiles, verticalVideo])

  async function refresh() {
    try {
      const [authState, currentPlan] = await Promise.all([
        getYouTubeAuthStatus(),
        project ? getYouTubePublishPlan(project).catch(() => null) : Promise.resolve(null),
      ])
      setAuth(authState)
      if (currentPlan?.items?.length) setPlan(currentPlan)
    } catch (e) {
      notify({ tone: 'error', title: 'Không tải được YouTube Publish', message: e.message })
    }
  }

  useEffect(() => { refresh() }, [project])

  useEffect(() => {
    localStorage.setItem('lucylab_youtube_publish_collapsed', collapsed ? '1' : '0')
  }, [collapsed])

  async function handleConnect() {
    try {
      const r = await startYouTubeAuth()
      window.open(r.auth_url, '_blank', 'noopener,noreferrer,width=900,height=720')
      notify({ title: 'Đăng nhập YouTube', message: 'Hoàn tất OAuth trong tab mới rồi bấm Làm mới.' })
    } catch (e) {
      notify({ tone: 'error', title: 'Không mở OAuth được', message: e.message })
    }
  }

  async function handleCreatePlan() {
    if (!longSource && !shortSources.length) {
      notify({ tone: 'error', title: 'Chưa có video để lên lịch' })
      return
    }
    setLoading(true)
    try {
      const next = await createYouTubePublishPlan({
        project,
        story_title: storyTitle || project,
        story_text: storyText || '',
        long_video: longSource,
        short_videos: shortSources,
      })
      setPlan(next)
    } catch (e) {
      notify({ tone: 'error', title: 'Không tạo được kế hoạch', message: e.message })
    } finally {
      setLoading(false)
    }
  }

  async function handleLogout() {
    await logoutYouTube()
    setAuth({ connected: false })
  }

  const longItem = useMemo(
    () => (plan?.items || []).find(it => it.kind === 'long') || null,
    [plan],
  )

  async function handleBuildCampaign() {
    if (!longItem) {
      notify({ tone: 'error', title: 'Cần video dài trong kế hoạch trước' })
      return
    }
    setLoading(true)
    try {
      const res = await buildShortsCampaign(project, longItem.id, verticalVideo || '')
      setCampaign(res)
      try { setPlan(await getYouTubePublishPlan(project)) } catch {}
      notify({
        tone: res.warnings?.length ? 'warning' : 'success',
        title: 'Đã tạo Shorts campaign',
        message: res.warnings?.[0] || `${res.created?.length || 0} short (teaser + cao trào) từ video dài. Nhớ gắn Related Video trong Studio.`,
      })
    } catch (e) {
      notify({ tone: 'error', title: 'Không tạo được Shorts campaign', message: e.message })
    } finally {
      setLoading(false)
    }
  }

  async function handleRegenerateShort(item) {
    if (!item?.role || regeneratingRole) return
    const sourceFile = verticalVideo || plan?.shorts_vertical_source_file || campaign?.vertical_source_file || ''
    if (!sourceFile) {
      notify({ tone: 'error', title: 'Thiếu video 9:16', message: 'Hãy render video 9:16 trước khi tạo lại Short.' })
      return
    }
    setRegeneratingRole(item.role)
    try {
      const res = await buildShortsCampaign(project, item.long_item_id || longItem?.id || '', sourceFile, '', item.role)
      setCampaign(res)
      const nextPlan = await getYouTubePublishPlan(project)
      setPlan(nextPlan)
      const label = item.role === 'teaser' ? 'Mồi' : 'Cao trào'
      notify({
        tone: res.warnings?.length ? 'warning' : 'success',
        title: `Đã tạo lại Short ${label}`,
        message: res.warnings?.[0] || `Chỉ Short ${label} đã được thay thế; Short còn lại không bị thay đổi.`,
      })
    } catch (e) {
      notify({ tone: 'error', title: `Không tạo lại được Short ${item.role === 'teaser' ? 'Mồi' : 'Cao trào'}`, message: e.message })
    } finally {
      setRegeneratingRole('')
    }
  }

  return (
    <section className="rounded-2xl border border-surface-600 bg-surface-950/60 p-4 space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Youtube size={18} className="text-red-400" />
          <h3 className="text-base font-bold text-white">YouTube Publish Center</h3>
        </div>
        <div className="flex items-center gap-1">
          <button onClick={refresh} className="p-2 rounded-lg text-slate-500 hover:text-white hover:bg-surface-800" title="Làm mới">
            <RefreshCw size={14} />
          </button>
          <button
            type="button"
            onClick={() => setCollapsed(v => !v)}
            aria-expanded={!collapsed}
            className="inline-flex items-center gap-1.5 rounded-lg border border-surface-600 px-2.5 py-2 text-xs font-semibold text-slate-300 hover:text-white hover:border-accent-400"
          >
            {collapsed ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
            {collapsed ? 'Mở' : 'Thu gọn'}
          </button>
        </div>
      </div>

      {collapsed && (
        <div className="rounded-xl border border-surface-700 bg-black/20 px-3 py-2 text-xs text-slate-400 flex flex-wrap gap-x-4 gap-y-1">
          <span>{auth?.connected ? auth?.channel?.title || 'YouTube đã kết nối' : 'Chưa kết nối YouTube'}</span>
          <span>Video dài: {longSource ? '1' : '0'}</span>
          <span>Shorts: {shortSources.length}</span>
          <span>Kế hoạch: {plan?.items?.length || 0} item</span>
        </div>
      )}

      {!collapsed && (
        <>
      <div className="rounded-xl border border-surface-700 bg-black/20 px-3 py-2 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-white font-semibold truncate">
            {auth?.connected ? auth?.channel?.title || 'YouTube đã kết nối' : 'Chưa kết nối YouTube'}
          </p>
          <p className="text-xs text-slate-500 truncate">
            {auth?.connected ? 'Sẵn sàng upload và lên lịch.' : 'Cần OAuth YouTube trước khi upload thật.'}
          </p>
        </div>
        {auth?.connected ? (
          <button onClick={handleLogout} className="text-xs text-slate-400 hover:text-white">Đăng xuất</button>
        ) : (
          <button onClick={handleConnect} className="inline-flex items-center gap-2 rounded-lg bg-red-600 px-3 py-2 text-xs font-semibold text-white hover:bg-red-500">
            <LogIn size={14} />
            Kết nối
          </button>
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
        <div className="rounded-xl bg-surface-900 border border-surface-700 p-3">
          <p className="text-slate-500">Video dài</p>
          <p className="text-white truncate">{longSource ? '1 file' : 'Chưa có'}</p>
        </div>
        <div className="rounded-xl bg-surface-900 border border-surface-700 p-3">
          <p className="text-slate-500">Shorts đã cắt</p>
          <p className="text-white">{shortSources.length} file</p>
        </div>
        <button
          onClick={handleCreatePlan}
          disabled={loading || (!longSource && !shortSources.length)}
          className="rounded-xl bg-accent-600 hover:bg-accent-500 disabled:opacity-40 px-3 py-3 text-sm font-semibold text-white inline-flex items-center justify-center gap-2"
        >
          {loading ? <Loader2 size={15} className="animate-spin" /> : <CalendarClock size={15} />}
          Tạo kế hoạch
        </button>
      </div>

      {longItem && (
        <div className="rounded-xl border border-surface-700 bg-black/20 p-3 space-y-2">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm text-white font-semibold">Shorts Campaign</p>
              <p className="text-xs text-slate-500">Tự cắt 2 Shorts (mồi + cao trào, mỗi clip &lt; 3 phút) từ video 9:16 đã render và nối về video dài.</p>
            </div>
            <button
              onClick={handleBuildCampaign}
              disabled={loading || !verticalVideo}
              className="shrink-0 inline-flex items-center gap-2 rounded-lg bg-red-600 hover:bg-red-500 disabled:opacity-40 px-3 py-2 text-xs font-semibold text-white"
            >
              {loading ? <Loader2 size={14} className="animate-spin" /> : <Scissors size={14} />}
              Tạo Shorts campaign
            </button>
          </div>
          {!longItem.youtube_video_id && (
            <p className="text-xs rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-amber-100">
              Upload video dài ở chế độ không công khai trước, sau đó tạo campaign và gắn Related Video trong Studio.
            </p>
          )}
          {!verticalVideo && (
            <p className="text-xs rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-red-200">
              Cần render bản 9:16 có phụ đề trước khi tạo Shorts campaign.
            </p>
          )}
          {campaign?.related_video_checklist?.length > 0 && (
            <ul className="text-xs text-amber-200/90 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 space-y-1 list-disc list-inside">
              {campaign.related_video_checklist.map((line, i) => <li key={i}>{line}</li>)}
            </ul>
          )}
        </div>
      )}

      {plan?.items?.length > 0 && (
        <div className="space-y-3">
          {plan.items.map(item => (
            <PublishItem
              key={item.id}
              project={project}
              item={item}
              onPlanChange={setPlan}
              onRegenerateShort={handleRegenerateShort}
              regeneratingRole={regeneratingRole}
            />
          ))}
        </div>
      )}
        </>
      )}
    </section>
  )
}
