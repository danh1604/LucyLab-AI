import { useState, useEffect, useRef } from 'react'
import { Film, Loader2, CheckCircle2, Download, AlertCircle } from 'lucide-react'
import { startComposeVideo, startComposeSceneVideo, outputVideoUrl } from '../api.js'

function fmtSec(s) {
  if (!s || s <= 0) return '0:00'
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = Math.floor(s % 60)
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
  return `${m}:${String(sec).padStart(2, '0')}`
}

export default function VideoCompose({ project, audioFile, videoOrder, settings, onDone }) {
  const [step, setStep] = useState('Khoi dong...')
  const [percent, setPercent] = useState(0)
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)
  const [sizeMb, setSizeMb] = useState(0)
  const [outputFile, setOutputFile] = useState(settings?.outputName || 'final_video.mp4')
  const [details, setDetails] = useState({ elapsed: 0, total: 0, eta: 0, speed: '', encoder: '' })
  const [retryVersion, setRetryVersion] = useState(0)
  const startedRef = useRef(false)

  useEffect(() => {
    // React StrictMode intentionally re-runs effects in development. Starting
    // FFmpeg twice would make two jobs overwrite the same destination.
    if (startedRef.current) return
    startedRef.current = true

    const runner = settings?.videoMode === 'scenes'
      ? (handleEvent, handleDone, handleError) => startComposeSceneVideo(project, audioFile, settings, handleEvent, handleDone, handleError)
      : (handleEvent, handleDone, handleError) => startComposeVideo(project, audioFile, videoOrder, settings, handleEvent, handleDone, handleError)

    runner(
      (ev) => {
        if (ev.type === 'step') {
          setStep(ev.message)
          setDetails(prev => ({ ...prev, encoder: ev.encoder_label || prev.encoder }))
        }
        if (ev.type === 'progress') {
          setPercent(ev.percent || 0)
          setStep(`${ev.message || 'Dang xu ly video...'} ${ev.percent || 0}%`)
          setSizeMb(ev.size_mb || 0)
          setDetails(prev => ({
            elapsed: ev.elapsed_sec || 0,
            total: ev.total_sec || 0,
            eta: ev.eta_sec || 0,
            speed: ev.speed || prev.speed || '',
            encoder: ev.encoder_label || prev.encoder || '',
          }))
        }
        if (ev.type === 'speed') setDetails(prev => ({ ...prev, speed: ev.speed || prev.speed }))
        if (ev.type === 'error') setError(ev.message)
        if (ev.type === 'done') {
          setPercent(100)
          setSizeMb(ev.size_mb || 0)
          setOutputFile(ev.output_file || outputFile)
          setDetails(prev => ({ ...prev, encoder: ev.encoder_label || prev.encoder }))
          setDone(true)
          onDone?.(ev.output_file || outputFile)
        }
      },
      () => {},
      (err) => setError(typeof err === 'string' ? err : (err?.message || String(err))),
    )
  }, [retryVersion])

  function retryCompose() {
    startedRef.current = false
    setStep('Khoi dong lai...')
    setPercent(0)
    setError('')
    setDone(false)
    setSizeMb(0)
    setDetails({ elapsed: 0, total: 0, eta: 0, speed: '', encoder: '' })
    setRetryVersion(version => version + 1)
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2">
        <Film size={16} className="text-accent-400" />
        <h2 className="text-lg font-bold text-white">Tao Video</h2>
      </div>

      <div className="glass rounded-2xl p-6 space-y-4">
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm text-slate-400">{step}</span>
          <span className="text-sm font-mono text-white">{percent}%</span>
        </div>
        <div className="h-2 bg-surface-700 rounded-full overflow-hidden">
          <div
            className="h-full bg-accent-500 rounded-full transition-all duration-500"
            style={{ width: `${percent}%` }}
          />
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
          <div className="rounded-lg bg-surface-800 px-3 py-2">
            <p className="text-slate-500">Thoi gian</p>
            <p className="text-white font-mono">{fmtSec(details.elapsed)} / {fmtSec(details.total)}</p>
          </div>
          <div className="rounded-lg bg-surface-800 px-3 py-2">
            <p className="text-slate-500">Con lai</p>
            <p className="text-white font-mono">{fmtSec(details.eta)}</p>
          </div>
          <div className="rounded-lg bg-surface-800 px-3 py-2">
            <p className="text-slate-500">Toc do</p>
            <p className="text-white font-mono">{details.speed || '...'}</p>
          </div>
          <div className="rounded-lg bg-surface-800 px-3 py-2">
            <p className="text-slate-500">Dung luong</p>
            <p className="text-white font-mono">{sizeMb ? `${sizeMb}MB` : '...'}</p>
          </div>
        </div>

        {details.encoder && (
          <p className="text-xs text-slate-500">
            Encoder: <span className="text-accent-300">{details.encoder}</span>
          </p>
        )}

        {done && (
          <div className="flex items-center gap-2 text-green-400">
            <CheckCircle2 size={16} />
            <span className="text-sm font-medium">Hoan thanh! {outputFile} · {sizeMb}MB</span>
          </div>
        )}

        {error && (
          <div className="space-y-3">
            <div className="flex items-start gap-2 text-red-400">
              <AlertCircle size={14} className="mt-0.5 shrink-0" />
              <p className="text-sm">{error}</p>
            </div>
            <button
              type="button"
              onClick={retryCompose}
              className="rounded-lg border border-accent-400/50 px-3 py-1.5 text-xs font-semibold text-accent-200 transition-colors hover:bg-accent-500/10 hover:text-white"
            >
              Thu lai
            </button>
          </div>
        )}

        {!done && !error && (
          <div className="flex items-center gap-2 text-slate-500">
            <Loader2 size={14} className="animate-spin" />
            <span className="text-xs">FFmpeg dang xu ly, co the tiep tuc theo doi chi tiet o tren.</span>
          </div>
        )}
      </div>

      {done && (
        <a
          href={outputVideoUrl(project, outputFile)}
          download={outputFile}
          className="w-full flex items-center justify-center gap-2 py-3 rounded-xl text-sm font-semibold bg-accent-600 hover:bg-accent-500 text-white transition-all"
        >
          <Download size={16} />
          Tai video ve
        </a>
      )}
    </div>
  )
}
