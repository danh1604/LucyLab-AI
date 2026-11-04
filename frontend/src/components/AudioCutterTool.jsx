import AppSelect from './AppSelect.jsx'
import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertCircle, AudioLines, CheckCircle2, Download, Loader2, Pause, Play, Scissors, Upload } from 'lucide-react'
import { audioCutDownloadUrl, cutAudio, uploadAudioForCut } from '../api.js'

function fmtSec(value) {
  const total = Math.max(0, Number(value) || 0)
  const m = Math.floor(total / 60)
  const s = Math.floor(total % 60)
  const ms = Math.round((total - Math.floor(total)) * 10)
  return `${m}:${String(s).padStart(2, '0')}.${ms}`
}

function fileStem(name) {
  return String(name || 'audio').replace(/\.[^.]+$/, '').replace(/[^\w-]+/g, '_').replace(/^_+|_+$/g, '') || 'audio'
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, Number(value) || 0))
}

export default function AudioCutterTool() {
  const fileRef = useRef(null)
  const audioRef = useRef(null)
  const canvasRef = useRef(null)
  const dragRef = useRef(null)
  const [file, setFile] = useState(null)
  const [upload, setUpload] = useState(null)
  const [objectUrl, setObjectUrl] = useState('')
  const [duration, setDuration] = useState(0)
  const [peaks, setPeaks] = useState([])
  const [start, setStart] = useState(0)
  const [end, setEnd] = useState(0)
  const [playhead, setPlayhead] = useState(0)
  const [outputName, setOutputName] = useState('audio_cut')
  const [outputFormat, setOutputFormat] = useState('mp3')
  const [uploading, setUploading] = useState(false)
  const [cutting, setCutting] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')

  const selectedDuration = Math.max(0, end - start)
  const timelineDuration = upload?.duration_sec || duration || 0

  const drawWaveform = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas || !timelineDuration) return
    const rect = canvas.getBoundingClientRect()
    const ratio = window.devicePixelRatio || 1
    const width = Math.max(1, Math.floor(rect.width))
    const height = Math.max(1, Math.floor(rect.height))
    canvas.width = width * ratio
    canvas.height = height * ratio
    const ctx = canvas.getContext('2d')
    ctx.scale(ratio, ratio)
    ctx.clearRect(0, 0, width, height)
    ctx.fillStyle = '#0a0a0f'
    ctx.fillRect(0, 0, width, height)

    const startX = (start / timelineDuration) * width
    const endX = (end / timelineDuration) * width
    const middle = height / 2
    const columns = Math.max(1, Math.min(peaks.length || 96, Math.floor(width / 3)))
    const step = Math.max(1, Math.floor((peaks.length || 1) / columns))
    for (let index = 0; index < columns; index += 1) {
      const sampleIndex = Math.min(peaks.length - 1, index * step)
      const peak = peaks[sampleIndex] || 0.07
      const x = (index / columns) * width
      const barHeight = Math.max(2, peak * (height - 22))
      const inside = x >= startX && x <= endX
      ctx.fillStyle = inside ? '#a78bfa' : '#3b3654'
      ctx.fillRect(x, middle - barHeight / 2, Math.max(1, width / columns - 1), barHeight)
    }
    ctx.fillStyle = 'rgba(0, 0, 0, 0.42)'
    ctx.fillRect(0, 0, startX, height)
    ctx.fillRect(endX, 0, width - endX, height)
    ctx.fillStyle = 'rgba(167, 139, 250, 0.13)'
    ctx.fillRect(startX, 0, Math.max(0, endX - startX), height)
    for (const x of [startX, endX]) {
      ctx.fillStyle = '#f5f3ff'
      ctx.fillRect(Math.round(x) - 1, 0, 2, height)
      ctx.fillStyle = '#8b5cf6'
      ctx.fillRect(Math.round(x) - 5, 7, 10, 18)
    }
    const playX = (playhead / timelineDuration) * width
    ctx.fillStyle = '#fbbf24'
    ctx.fillRect(Math.round(playX), 0, 2, height)
  }, [duration, end, peaks, playhead, start, timelineDuration, upload?.duration_sec])

  useEffect(() => {
    drawWaveform()
    const observer = new ResizeObserver(drawWaveform)
    if (canvasRef.current) observer.observe(canvasRef.current)
    return () => observer.disconnect()
  }, [drawWaveform])

  useEffect(() => () => {
    if (objectUrl) URL.revokeObjectURL(objectUrl)
  }, [objectUrl])

  async function decodeWaveform(nextFile) {
    const context = new (window.AudioContext || window.webkitAudioContext)()
    try {
      const data = await nextFile.arrayBuffer()
      const buffer = await context.decodeAudioData(data.slice(0))
      const channel = buffer.getChannelData(0)
      const buckets = Math.min(1400, Math.max(220, Math.ceil(buffer.duration * 45)))
      const blockSize = Math.max(1, Math.floor(channel.length / buckets))
      const nextPeaks = Array.from({ length: buckets }, (_, index) => {
        let max = 0
        const offset = index * blockSize
        for (let cursor = 0; cursor < blockSize && offset + cursor < channel.length; cursor += 1) {
          max = Math.max(max, Math.abs(channel[offset + cursor]))
        }
        return Math.sqrt(max)
      })
      setPeaks(nextPeaks)
      setDuration(buffer.duration)
      setStart(0)
      setEnd(buffer.duration)
    } finally {
      await context.close()
    }
  }

  async function handleFile(nextFile) {
    if (!nextFile) return
    setError('')
    setResult(null)
    setFile(nextFile)
    setUpload(null)
    setOutputName(`${fileStem(nextFile.name)}_cut`)
    setPeaks([])
    setDuration(0)
    setStart(0)
    setEnd(0)
    setPlayhead(0)
    if (objectUrl) URL.revokeObjectURL(objectUrl)
    setObjectUrl(URL.createObjectURL(nextFile))
    try {
      await decodeWaveform(nextFile)
    } catch {
      setError('Trình duyệt không đọc được waveform của file này. Bạn vẫn có thể thử upload để cắt bằng FFmpeg.')
    }
    setUploading(true)
    try {
      const meta = await uploadAudioForCut(nextFile)
      setUpload(meta)
      setDuration(meta.duration_sec || duration)
      setEnd(meta.duration_sec || duration)
    } catch (e) {
      setError(e.message || 'Không thể upload audio')
    } finally {
      setUploading(false)
    }
  }

  function timeFromPointer(event) {
    const rect = canvasRef.current.getBoundingClientRect()
    return clamp(((event.clientX - rect.left) / rect.width) * timelineDuration, 0, timelineDuration)
  }

  function handlePointerDown(event) {
    if (!timelineDuration) return
    const time = timeFromPointer(event)
    const tolerance = Math.max(0.35, timelineDuration * 0.018)
    let mode = 'new'
    if (Math.abs(time - start) <= tolerance) mode = 'start'
    else if (Math.abs(time - end) <= tolerance) mode = 'end'
    else if (time > start && time < end) mode = 'move'
    dragRef.current = { mode, anchor: time, initialStart: start, initialEnd: end }
    canvasRef.current.setPointerCapture(event.pointerId)
    if (mode === 'new') {
      const nextStart = clamp(time, 0, Math.max(0, timelineDuration - 0.1))
      setStart(nextStart)
      setEnd(Math.min(timelineDuration, nextStart + Math.max(1, Math.min(8, timelineDuration / 5))))
    }
  }

  function handlePointerMove(event) {
    const drag = dragRef.current
    if (!drag || !timelineDuration) return
    const time = timeFromPointer(event)
    if (drag.mode === 'start') setStart(Math.min(time, end - 0.05))
    if (drag.mode === 'end') setEnd(Math.max(time, start + 0.05))
    if (drag.mode === 'move') {
      const delta = time - drag.anchor
      const length = drag.initialEnd - drag.initialStart
      const nextStart = clamp(drag.initialStart + delta, 0, timelineDuration - length)
      setStart(nextStart)
      setEnd(nextStart + length)
    }
  }

  function stopPreview() {
    const player = audioRef.current
    if (player) player.pause()
  }

  async function toggleSelectionPreview() {
    const player = audioRef.current
    if (!player || !selectedDuration) return
    if (!player.paused && player.currentTime >= start && player.currentTime < end) {
      stopPreview()
      return
    }
    player.currentTime = start
    await player.play()
  }

  async function handleCut() {
    if (!upload?.id || selectedDuration <= 0) return
    setError('')
    setResult(null)
    setCutting(true)
    try {
      setResult(await cutAudio({
        upload_id: upload.id,
        start_sec: start,
        end_sec: end,
        output_name: outputName.trim() || 'audio_cut',
        output_format: outputFormat,
      }))
    } catch (e) {
      setError(e.message || 'Cắt audio thất bại')
    } finally {
      setCutting(false)
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <AudioLines size={18} className="text-accent-400" />
          <div>
            <h2 className="text-lg font-bold text-white">Cắt audio theo waveform</h2>
            <p className="text-xs text-slate-500">Kéo hai tay nắm hoặc cả vùng chọn như CapCut, nghe thử trước khi xuất.</p>
          </div>
        </div>
        {timelineDuration > 0 && <span className="font-mono text-xs text-slate-400">Tổng: <span className="text-white">{fmtSec(timelineDuration)}</span></span>}
      </div>

      <div
        onClick={() => fileRef.current?.click()}
        onDrop={(event) => { event.preventDefault(); handleFile(event.dataTransfer.files?.[0]) }}
        onDragOver={(event) => event.preventDefault()}
        className="cursor-pointer rounded-xl border-2 border-dashed border-surface-500 bg-surface-900/40 px-6 py-7 text-center transition-colors hover:border-accent-500"
      >
        {uploading ? <Loader2 size={24} className="mx-auto mb-2 animate-spin text-accent-400" /> : <Upload size={24} className="mx-auto mb-2 text-slate-500" />}
        <p className="text-sm text-slate-200">{uploading ? 'Đang chuẩn bị audio...' : file?.name || 'Kéo thả hoặc click để chọn audio'}</p>
        <p className="mt-1 text-xs text-slate-500">MP3, WAV, M4A, AAC, OGG, FLAC, OPUS</p>
        <input ref={fileRef} type="file" accept="audio/*,.m4a,.aac,.ogg,.flac,.opus" className="hidden" onChange={(event) => handleFile(event.target.files?.[0])} />
      </div>

      {objectUrl && timelineDuration > 0 && (
        <div className="space-y-4 rounded-xl border border-surface-600 bg-surface-900/60 p-4">
          <audio
            ref={audioRef}
            src={objectUrl}
            controls
            className="w-full"
            onLoadedMetadata={(event) => {
              const nextDuration = event.currentTarget.duration
              if (Number.isFinite(nextDuration) && nextDuration > 0 && !duration) {
                setDuration(nextDuration); setEnd(nextDuration)
              }
            }}
            onTimeUpdate={(event) => {
              const current = event.currentTarget.currentTime
              setPlayhead(current)
              if (current >= end && end > start) { event.currentTarget.pause(); event.currentTarget.currentTime = end }
            }}
            onEnded={() => setPlayhead(end)}
          />

          <div className="relative select-none">
            <canvas
              ref={canvasRef}
              role="img"
              aria-label="Waveform audio và vùng cắt đang chọn"
              className="h-40 w-full touch-none rounded-lg border border-surface-600"
              onPointerDown={handlePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={() => { dragRef.current = null }}
              onPointerCancel={() => { dragRef.current = null }}
            />
            <div className="mt-2 flex justify-between font-mono text-[11px] text-slate-500"><span>0:00.0</span><span>{fmtSec(timelineDuration)}</span></div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
            <label className="space-y-1"><span className="text-xs font-medium text-slate-400">Bắt đầu</span><input type="number" min="0" max={Math.max(0, end - 0.05)} step="0.01" value={start.toFixed(2)} onChange={(event) => setStart(clamp(event.target.value, 0, Math.max(0, end - 0.05)))} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 font-mono text-sm text-white focus:border-accent-500 focus:outline-none" /></label>
            <label className="space-y-1"><span className="text-xs font-medium text-slate-400">Kết thúc</span><input type="number" min={start + 0.05} max={timelineDuration} step="0.01" value={end.toFixed(2)} onChange={(event) => setEnd(clamp(event.target.value, start + 0.05, timelineDuration))} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 font-mono text-sm text-white focus:border-accent-500 focus:outline-none" /></label>
            <button type="button" onClick={toggleSelectionPreview} className="inline-flex items-center justify-center gap-2 rounded-lg border border-accent-500/50 px-3 py-2 text-sm font-semibold text-accent-200 transition-colors hover:bg-accent-500/10">
              {audioRef.current && !audioRef.current.paused && playhead >= start && playhead < end ? <Pause size={15} /> : <Play size={15} />}
              Nghe đoạn
            </button>
          </div>
          <p className="text-sm text-slate-400">Vùng chọn: <span className="font-mono font-semibold text-white">{fmtSec(start)} → {fmtSec(end)}</span> <span className="ml-1 text-accent-300">({fmtSec(selectedDuration)})</span></p>
        </div>
      )}

      {timelineDuration > 0 && (
        <div className="grid grid-cols-1 gap-3 rounded-xl border border-surface-600 bg-surface-900/60 p-4 sm:grid-cols-[minmax(0,1fr)_8rem_auto] sm:items-end">
          <label className="space-y-1"><span className="text-xs font-medium text-slate-400">Tên file xuất</span><input value={outputName} onChange={(event) => setOutputName(event.target.value)} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white focus:border-accent-500 focus:outline-none" /></label>
          <label className="space-y-1"><span className="text-xs font-medium text-slate-400">Định dạng</span><AppSelect value={outputFormat} onChange={(event) => setOutputFormat(event.target.value)} className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white focus:border-accent-500 focus:outline-none"><option value="mp3">MP3</option><option value="wav">WAV</option><option value="m4a">M4A</option></AppSelect></label>
          <button onClick={handleCut} disabled={!upload || uploading || cutting || selectedDuration <= 0} className="inline-flex items-center justify-center gap-2 rounded-lg bg-accent-600 px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-accent-500 disabled:cursor-not-allowed disabled:opacity-40">{cutting ? <Loader2 size={16} className="animate-spin" /> : <Scissors size={16} />}{cutting ? 'Đang xuất...' : 'Cắt & xuất'}</button>
        </div>
      )}

      {error && <div className="flex items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-red-200"><AlertCircle size={16} className="mt-0.5 shrink-0" />{error}</div>}
      {result && <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-success/30 bg-emerald-500/10 p-4"><div className="flex min-w-0 items-center gap-2"><CheckCircle2 size={17} className="shrink-0 text-success" /><div><p className="truncate text-sm font-semibold text-white">{result.filename}</p><p className="text-xs text-slate-400">{fmtSec(result.duration_sec)} · {result.size_mb}MB</p></div></div><a href={audioCutDownloadUrl(result.filename)} download={result.filename} className="inline-flex items-center gap-2 rounded-lg border border-success/50 px-3 py-2 text-sm font-semibold text-emerald-100 transition-colors hover:bg-emerald-500/10"><Download size={15} />Tải về</a></div>}
    </div>
  )
}
