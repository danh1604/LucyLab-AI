import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, Download, Image, Loader2, RefreshCw, Repeat2, Sparkles, Trash2, Upload, Video } from 'lucide-react'
import {
  createLoopPrompt,
  deleteLoopAsset,
  listLoopAssets,
  loopImageUrl,
  loopVideoUrl,
  startLoopImageGenerate,
  uploadLoopVideo,
} from '../api.js'
import { useDialog } from './DialogProvider.jsx'

const DEFAULT_STYLE = 'A peaceful old Vietnamese mountain town at blue hour, a cozy wooden tea shop with warm lantern light, stone-paved alley, traditional tiled-roof houses, distant mountains, warm orange and cool blue cinematic lighting, nostalgic lo-fi atmosphere, detailed anime background art, semi-realistic hand-painted digital illustration, wide establishing shot, 16:9.'

function fmtSec(value) {
  const sec = Math.max(0, Math.round(Number(value) || 0))
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`
}

export default function LoopVideoTool({ project, settings, onSettingsChange, onCompose }) {
  const { confirm, notify } = useDialog()
  const inputRef = useRef(null)
  const [assets, setAssets] = useState({ videos: [], images: [], provider: null })
  const [loading, setLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [prompting, setPrompting] = useState('')
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [progress, setProgress] = useState(0)
  const [createMode, setCreateMode] = useState('random')
  const [style, setStyle] = useState(settings?.loopStyle || DEFAULT_STYLE)
  const [subject, setSubject] = useState(settings?.loopSubject || '')
  const [selectedVideo, setSelectedVideo] = useState(settings?.loopVideo || '')

  function update(patch) {
    onSettingsChange?.({ ...settings, videoMode: 'loop', ...patch })
  }

  async function refresh() {
    if (!project) return
    setLoading(true)
    try {
      const data = await listLoopAssets(project)
      setAssets(data)
    } catch (e) {
      setError(e?.message || String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { refresh() }, [project]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setStyle(settings?.loopStyle || DEFAULT_STYLE)
    setSubject(settings?.loopSubject || '')
    setSelectedVideo(settings?.loopVideo || '')
  }, [settings?.loopStyle, settings?.loopSubject, settings?.loopVideo])

  async function chooseVideo(filename) {
    setSelectedVideo(filename)
    update({ loopVideo: filename })
  }

  async function upload(file) {
    if (!file || !project) return
    setUploading(true)
    setError('')
    try {
      const item = await uploadLoopVideo(project, file)
      await refresh()
      await chooseVideo(item.filename)
      notify({ tone: 'success', title: 'Da tai video loop', message: `${item.filename} · ${fmtSec(item.duration_sec)}` })
    } catch (e) {
      setError(e?.message || String(e))
    } finally {
      setUploading(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  function generateImage() {
    if (!project) return
    if (!style.trim()) {
      setError('Hay nhap phong cach anh loop.')
      return
    }
    if (createMode === 'prompt' && !subject.trim()) {
      setError('Hay mo ta boi canh hoac canh vat can tao.')
      return
    }
    setGenerating(true)
    setError('')
    setStatus('Dang gui yeu cau tao anh 2K...')
    setProgress(1)
    update({ loopStyle: style.trim(), loopSubject: subject.trim() })
    startLoopImageGenerate(
      { project, style: style.trim(), subject: subject.trim(), mode: createMode },
      event => {
        if (event.type === 'provider') setStatus(`${event.provider} · ${event.model || ''} · ${event.size || ''}`)
        if (event.type === 'start') setStatus(`Dang tao: ${event.subject || ''}`)
        if (event.type === 'task') setStatus(`Provider dang xu ly task ${event.task_id}`)
        if (event.type === 'progress') {
          setProgress(event.progress || 1)
          setStatus(event.status === 'finalizing' ? 'Dang hoan tat va tai anh...' : 'Dang tao anh loop...')
        }
        if (event.type === 'done') {
          setProgress(100)
          setStatus('Da tao anh loop 2K')
          setAssets(prev => ({ ...prev, images: [event.image, ...prev.images.filter(x => x.filename !== event.image.filename)] }))
          notify({ tone: 'success', title: 'Da tao anh loop', message: event.image.filename })
        }
        if (event.type === 'error') setError(event.message || 'Tao anh loop that bai')
      },
      () => setGenerating(false),
      e => { setGenerating(false); setError(e?.message || String(e)) },
    )
  }

  async function generatePrompt(filename) {
    setPrompting(filename)
    setError('')
    try {
      const data = await createLoopPrompt(project, filename)
      setAssets(prev => ({
        ...prev,
        images: prev.images.map(item => item.filename === filename ? data.image : item),
      }))
      notify({ tone: 'success', title: 'Da sinh prompt loop', message: 'Prompt da duoc luu cung anh.' })
    } catch (e) {
      setError(e?.message || String(e))
    } finally {
      setPrompting('')
    }
  }

  async function remove(type, filename) {
    const ok = await confirm({
      tone: 'danger', title: type === 'video' ? 'Xoa video loop?' : 'Xoa anh loop?',
      message: 'File se bi xoa khoi project.', details: filename, confirmText: 'Xoa', cancelText: 'Huy',
    })
    if (!ok) return
    try {
      await deleteLoopAsset(project, type, filename)
      setAssets(prev => ({ ...prev, [type === 'video' ? 'videos' : 'images']: prev[type === 'video' ? 'videos' : 'images'].filter(item => item.filename !== filename) }))
      if (type === 'video' && selectedVideo === filename) {
        setSelectedVideo('')
        update({ loopVideo: '' })
      }
    } catch (e) {
      setError(e?.message || String(e))
    }
  }

  const chosen = assets.videos.find(item => item.filename === selectedVideo)

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <Repeat2 size={17} className="text-accent-400" />
          <div>
            <h2 className="text-lg font-bold text-white">Video loop</h2>
            <p className="text-xs text-slate-500">Lap mot clip ngan den het audio. Anh tao ra de ban tai ve va tao loop bang cong cu khac.</p>
          </div>
        </div>
        <button type="button" onClick={refresh} disabled={loading} className="inline-flex items-center gap-1.5 rounded-lg border border-surface-500 px-3 py-2 text-xs font-semibold text-slate-300 hover:text-white disabled:opacity-40">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> Lam moi
        </button>
      </div>

      <section className="rounded-xl border border-surface-600 bg-black/20 p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-white">1. Chon video loop</p>
            <p className="text-xs text-slate-500">Clip ngan se tu lap bang FFmpeg den khi audio ket thuc.</p>
          </div>
          <button type="button" onClick={() => inputRef.current?.click()} disabled={uploading || !project} className="inline-flex items-center gap-1.5 rounded-lg border border-accent-400/60 px-3 py-2 text-xs font-semibold text-accent-200 hover:bg-accent-500/10 disabled:opacity-40">
            {uploading ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />} Tai video loop
          </button>
          <input ref={inputRef} type="file" accept="video/*" className="hidden" onChange={e => upload(e.target.files?.[0])} />
        </div>
        {assets.videos.length === 0 ? (
          <p className="rounded-lg border border-dashed border-surface-600 px-3 py-5 text-center text-xs text-slate-500">Chua co video loop. Tai len mot clip 16:9 ngan truoc khi xuat video.</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {assets.videos.map(item => (
              <div key={item.filename} className={`flex items-center gap-3 rounded-lg border p-2.5 ${selectedVideo === item.filename ? 'border-accent-400 bg-accent-500/10' : 'border-surface-600 bg-surface-900/60'}`}>
                <button type="button" onClick={() => chooseVideo(item.filename)} className="min-w-0 flex flex-1 text-left">
                  <Video size={15} className="mr-2 mt-0.5 shrink-0 text-accent-400" />
                  <span className="min-w-0"><span className="block truncate text-xs font-semibold text-white">{item.filename}</span><span className="text-xs text-slate-500">{fmtSec(item.duration_sec)} · {item.size_mb} MB</span></span>
                </button>
                <button type="button" onClick={() => remove('video', item.filename)} className="p-1.5 text-slate-500 hover:text-red-300"><Trash2 size={14} /></button>
              </div>
            ))}
          </div>
        )}
        {chosen && <p className="text-xs text-green-300"><CheckCircle2 size={13} className="mr-1 inline" />Dang dung {chosen.filename}; se lap tu {fmtSec(chosen.duration_sec)} den het audio.</p>}
      </section>

      <section className="rounded-xl border border-surface-600 bg-black/20 p-4 space-y-3">
        <div className="flex items-center gap-2"><Image size={16} className="text-accent-400" /><div><p className="text-sm font-semibold text-white">2. Tao anh nguon cho loop</p><p className="text-xs text-slate-500">Anh 16:9 2K, khong co nguoi. Anh nay khong tu chuyen thanh video trong LucyLab.</p></div></div>
        <label className="block space-y-1"><span className="text-xs font-medium uppercase tracking-wider text-slate-400">Phong cach anh</span><textarea value={style} onChange={e => setStyle(e.target.value)} onBlur={() => update({ loopStyle: style.trim() })} rows={5} className="scene-prompt-editor w-full rounded-lg px-3 py-2 text-sm focus:outline-none resize-y" /></label>
        <div className="flex rounded-lg border border-surface-500 bg-surface-900 p-1 w-fit">
          <button type="button" onClick={() => setCreateMode('random')} className={`rounded-md px-3 py-1.5 text-xs font-semibold ${createMode === 'random' ? 'bg-accent-600 text-white' : 'text-slate-400 hover:text-white'}`}>Random theo phong cach</button>
          <button type="button" onClick={() => setCreateMode('prompt')} className={`rounded-md px-3 py-1.5 text-xs font-semibold ${createMode === 'prompt' ? 'bg-accent-600 text-white' : 'text-slate-400 hover:text-white'}`}>Nhap boi canh</button>
        </div>
        {createMode === 'prompt' && <label className="block space-y-1"><span className="text-xs font-medium uppercase tracking-wider text-slate-400">Canh vat / su vat can tao</span><textarea value={subject} onChange={e => setSubject(e.target.value)} onBlur={() => update({ loopSubject: subject.trim() })} rows={3} placeholder="vd: con hem da ven nui sau con mua nhe, tiem tra go vang den, khong co nguoi" className="scene-prompt-editor w-full rounded-lg px-3 py-2 text-sm focus:outline-none resize-y" /></label>}
        <button type="button" onClick={generateImage} disabled={generating || !project} className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-accent-600 px-4 py-3 text-sm font-semibold text-white hover:bg-accent-500 disabled:opacity-40"><Sparkles size={16} />{generating ? 'Dang tao anh loop 2K...' : createMode === 'random' ? 'Tao random theo phong cach' : 'Tao anh theo boi canh'}</button>
        {generating && <div className="space-y-1"><div className="flex justify-between text-xs text-slate-400"><span>{status}</span><span>{progress}%</span></div><div className="h-1.5 overflow-hidden rounded-full bg-surface-700"><div className="h-full bg-accent-500 transition-all" style={{ width: `${progress}%` }} /></div></div>}
        {assets.provider && <p className="text-xs font-mono text-slate-500">{assets.provider.provider} · {assets.provider.model} · {assets.provider.size}</p>}
      </section>

      {assets.images.length > 0 && <section className="space-y-3"><div className="flex items-center justify-between"><p className="text-sm font-semibold text-white">Anh loop da tao</p><span className="text-xs text-slate-500">{assets.images.length}</span></div><div className="grid grid-cols-1 lg:grid-cols-2 gap-4">{assets.images.map(item => <article key={item.filename} className="overflow-hidden rounded-xl border border-surface-600 bg-surface-900/60"><img src={`${loopImageUrl(project, item.filename)}?t=${encodeURIComponent(item.created_at || '')}`} className="aspect-video w-full object-cover" alt={item.subject || item.filename} /><div className="space-y-2 p-3"><p className="text-sm text-white">{item.subject}</p><p className="text-xs font-mono text-slate-500">{item.resolution || 'high quality'} · {item.filename}</p>{item.loop_prompt && <textarea readOnly value={item.loop_prompt} rows={8} className="scene-prompt-editor w-full rounded-lg px-3 py-2 text-xs focus:outline-none resize-y" />}<div className="flex flex-wrap gap-2"><a href={loopImageUrl(project, item.filename)} download={item.filename} className="inline-flex items-center gap-1.5 rounded-lg border border-surface-500 px-2.5 py-1.5 text-xs font-semibold text-slate-300 hover:text-white"><Download size={13} />Tai anh</a><button type="button" onClick={() => generatePrompt(item.filename)} disabled={prompting === item.filename} className="inline-flex items-center gap-1.5 rounded-lg border border-accent-400/60 px-2.5 py-1.5 text-xs font-semibold text-accent-200 hover:bg-accent-500/10 disabled:opacity-40">{prompting === item.filename ? <Loader2 size={13} className="animate-spin" /> : <Repeat2 size={13} />}{item.loop_prompt ? 'Tao lai prompt loop' : 'Sinh prompt loop'}</button><button type="button" onClick={() => remove('image', item.filename)} className="inline-flex items-center gap-1.5 rounded-lg border border-red-500/40 px-2.5 py-1.5 text-xs font-semibold text-red-200 hover:bg-red-500/10"><Trash2 size={13} />Xoa</button></div></div></article>)}</div></section>}
      {error && <p className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>}
      <button type="button" onClick={() => onCompose([], { ...settings, videoMode: 'loop', loopVideo: selectedVideo, loopStyle: style.trim(), loopSubject: subject.trim() })} disabled={!selectedVideo || uploading || generating} className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-accent-600 px-4 py-3 text-sm font-semibold text-white hover:bg-accent-500 disabled:cursor-not-allowed disabled:opacity-40"><Repeat2 size={16} />Tao video loop</button>
    </div>
  )
}
