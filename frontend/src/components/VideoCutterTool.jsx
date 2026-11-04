import { useRef, useState } from 'react'
import { AlertCircle, CheckCircle2, Download, FolderOpen, Loader2, Scissors, Upload, Video } from 'lucide-react'
import { cutVideoDownloadUrl, selectFolder, startVideoCut, uploadVideoForCut } from '../api.js'

function fmtSec(value) {
  const total = Math.max(0, Math.floor(value || 0))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${m}:${String(s).padStart(2, '0')}`
}

export default function VideoCutterTool() {
  const fileRef = useRef(null)
  const [upload, setUpload] = useState(null)
  const [baseName, setBaseName] = useState('')
  const [outputDir, setOutputDir] = useState('')
  const [minutes, setMinutes] = useState(3)
  const [seconds, setSeconds] = useState(0)
  const [uploading, setUploading] = useState(false)
  const [cutting, setCutting] = useState(false)
  const [percent, setPercent] = useState(0)
  const [status, setStatus] = useState('')
  const [files, setFiles] = useState([])
  const [error, setError] = useState('')

  const segmentSec = Number(minutes || 0) * 60 + Number(seconds || 0)
  const estimatedParts = upload?.duration_sec && segmentSec > 0
    ? Math.ceil(upload.duration_sec / segmentSec)
    : 0

  async function handleFile(file) {
    if (!file) return
    setError('')
    setFiles([])
    setPercent(0)
    setUploading(true)
    try {
      const meta = await uploadVideoForCut(file)
      setUpload(meta)
      if (!baseName.trim()) {
        setBaseName(file.name.replace(/\.[^.]+$/, '').replace(/[^\w-]+/g, '_').replace(/^_+|_+$/g, '') || 'video')
      }
    } catch (e) {
      setError(e.message || String(e))
    } finally {
      setUploading(false)
    }
  }

  async function handleSelectOutputDir() {
    setError('')
    try {
      const result = await selectFolder('Chọn thư mục lưu video đã cắt', outputDir)
      if (result.path) setOutputDir(result.path)
    } catch (e) {
      setError(e.message || 'Không mở được hộp thoại chọn thư mục')
    }
  }

  function handleCut() {
    setError('')
    setFiles([])
    setPercent(0)
    if (!upload?.id) {
      setError('Hãy upload video trước')
      return
    }
    if (!baseName.trim()) {
      setError('Hãy nhập tên video trước khi cắt')
      return
    }
    if (segmentSec <= 0) {
      setError('Thời lượng mỗi đoạn phải lớn hơn 0')
      return
    }
    setCutting(true)
    setStatus('Đang chuẩn bị cắt video...')
    startVideoCut(
      {
        upload_id: upload.id,
        base_name: baseName.trim(),
        minutes: Number(minutes || 0),
        seconds: Number(seconds || 0),
        output_dir: outputDir.trim(),
      },
      (ev) => {
        if (ev.type === 'start') setStatus(`Sẽ tạo ${ev.total} đoạn video`)
        if (ev.type === 'segment_start') setStatus(`Đang cắt ${ev.filename}`)
        if (ev.type === 'segment_done') {
          setPercent(ev.percent || 0)
          setFiles(prev => [...prev, ev.file])
          setStatus(`Đã cắt ${ev.index}/${ev.total}`)
        }
        if (ev.type === 'error') {
          setError(ev.message || 'Cắt video thất bại')
          setCutting(false)
        }
        if (ev.type === 'done') {
          setFiles(ev.files || [])
          setPercent(100)
          setStatus('Hoàn thành')
        }
      },
      () => setCutting(false),
      (e) => {
        setError(e?.message || String(e))
        setCutting(false)
      },
    )
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Scissors size={17} className="text-accent-400" />
          <h2 className="text-lg font-bold text-white">Cắt video thành nhiều đoạn</h2>
        </div>
        {upload && (
          <span className="text-xs text-slate-500">
            Video: <span className="font-mono text-white">{fmtSec(upload.duration_sec)}</span>
          </span>
        )}
      </div>

      <div
        onClick={() => fileRef.current?.click()}
        onDrop={(e) => {
          e.preventDefault()
          handleFile(e.dataTransfer.files?.[0])
        }}
        onDragOver={(e) => e.preventDefault()}
        className="border-2 border-dashed border-surface-500 hover:border-accent-500 rounded-2xl p-8 text-center cursor-pointer transition-colors bg-surface-900/40"
      >
        {uploading ? (
          <Loader2 size={24} className="animate-spin mx-auto mb-3 text-accent-400" />
        ) : (
          <Upload size={24} className="mx-auto mb-3 text-slate-500" />
        )}
        <p className="text-sm text-slate-300">
          {uploading ? 'Đang upload video...' : upload ? upload.original_name : 'Kéo thả hoặc click để chọn video'}
        </p>
        <p className="text-xs text-slate-600 mt-1">MP4, MOV, MKV, AVI, WEBM</p>
        <input
          ref={fileRef}
          type="file"
          accept="video/*"
          className="hidden"
          onChange={(e) => handleFile(e.target.files?.[0])}
        />
      </div>

      <div className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-4">
        <label className="block space-y-1">
          <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Thư mục lưu</span>
          <div className="flex gap-2">
            <input
              value={outputDir}
              onChange={e => setOutputDir(e.target.value)}
              placeholder="vd: D:\Videos\Cuts (để trống dùng thư mục mặc định)"
              className="flex-1 min-w-0 bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
            />
            <button
              type="button"
              onClick={handleSelectOutputDir}
              className="inline-flex items-center gap-1.5 rounded-lg border border-surface-500 px-3 py-2 text-xs font-semibold text-slate-300 hover:text-white hover:border-accent-400 transition-colors shrink-0"
            >
              <FolderOpen size={13} />
              Chọn
            </button>
          </div>
          <span className="block text-xs text-slate-600">
            Backend sẽ tự tạo thư mục nếu chưa tồn tại.
          </span>
        </label>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <label className="sm:col-span-1 space-y-1">
            <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Tên video</span>
            <input
              value={baseName}
              onChange={e => setBaseName(e.target.value)}
              placeholder="vd: nha"
              className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
            />
          </label>

          <label className="space-y-1">
            <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Phút</span>
            <input
              type="number"
              min="0"
              value={minutes}
              onChange={e => setMinutes(e.target.value)}
              className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
            />
          </label>

          <label className="space-y-1">
            <span className="text-xs font-medium text-slate-400 uppercase tracking-wider">Giây</span>
            <input
              type="number"
              min="0"
              max="59"
              value={seconds}
              onChange={e => setSeconds(e.target.value)}
              className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
            />
          </label>
        </div>

        <div className="flex items-center justify-between text-xs text-slate-500">
          <span>Mỗi đoạn: <span className="font-mono text-white">{fmtSec(segmentSec)}</span></span>
          <span>Dự kiến: <span className="font-mono text-white">{estimatedParts || 0}</span> video</span>
        </div>

        {cutting && (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-400">{status}</span>
              <span className="font-mono text-white">{percent}%</span>
            </div>
            <div className="h-2 rounded-full bg-surface-700 overflow-hidden">
              <div className="h-full bg-accent-500 transition-all" style={{ width: `${percent}%` }} />
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
          onClick={handleCut}
          disabled={uploading || cutting || !upload || !baseName.trim() || segmentSec <= 0}
          className="w-full inline-flex items-center justify-center gap-2 rounded-xl bg-accent-600 hover:bg-accent-500 disabled:opacity-40 disabled:cursor-not-allowed px-4 py-3 text-sm font-semibold text-white transition-colors"
        >
          {cutting ? <Loader2 size={16} className="animate-spin" /> : <Scissors size={16} />}
          Cắt
        </button>
      </div>

      {files.length > 0 && (
        <div className="rounded-2xl border border-surface-600 bg-surface-900/60 overflow-hidden">
          <div className="px-4 py-3 border-b border-surface-600 flex items-center gap-2">
            <CheckCircle2 size={15} className="text-green-400" />
            <span className="text-sm font-semibold text-white">Video đã cắt</span>
            <span className="text-xs text-slate-500 bg-surface-700 px-2 py-0.5 rounded-full">{files.length}</span>
          </div>
          <div className="divide-y divide-surface-700">
            {files.map(file => (
              <div key={file.filename} className="px-4 py-3 flex items-center justify-between gap-3">
                <div className="min-w-0 flex items-center gap-2">
                  <Video size={14} className="text-accent-400 shrink-0" />
                  <div className="min-w-0">
                    <p className="text-sm text-white truncate">{file.filename}</p>
                    {file.saved_path && <p className="text-xs text-slate-600 truncate">{file.saved_path}</p>}
                    <p className="text-xs text-slate-500">{fmtSec(file.duration_sec)} · {file.size_mb}MB</p>
                  </div>
                </div>
                {file.url && (
                  <a
                    href={cutVideoDownloadUrl(file.filename)}
                    download={file.filename}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-surface-500 px-3 py-1.5 text-xs font-semibold text-slate-300 hover:text-white hover:border-accent-400 transition-colors"
                  >
                    <Download size={13} />
                    Tải
                  </a>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
