import { useEffect, useState } from 'react'
import { AudioLines, ChevronRight, FileAudio, RotateCcw } from 'lucide-react'
import { audioUrl, getSubtitleTiming } from '../api.js'

function fmtDuration(value) {
  const seconds = Math.max(0, Math.round(Number(value) || 0))
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`
}

export default function AudioReview({ project, audioFile, audioDuration, onRegenerate, onContinue }) {
  const [processing, setProcessing] = useState(null)
  // The backend overwrites the same filename on regeneration. Give this player
  // a unique URL per review mount so it cannot reuse an older browser cache.
  const [mediaVersion] = useState(() => Date.now())

  useEffect(() => {
    let cancelled = false
    setProcessing(null)
    if (!project || !audioFile) return () => { cancelled = true }
    getSubtitleTiming(project, audioFile)
      .then((timing) => {
        if (!cancelled) setProcessing(timing?.processing || null)
      })
      .catch(() => {
        if (!cancelled) setProcessing(null)
      })
    return () => { cancelled = true }
  }, [project, audioFile])

  return (
    <section className="mx-auto max-w-2xl space-y-5" aria-labelledby="audio-review-title">
      <div className="text-center space-y-2">
        <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl border border-accent-400/25 bg-accent-500/10 text-accent-300">
          <AudioLines size={21} />
        </div>
        <h2 id="audio-review-title" className="text-xl font-bold text-white">Nghe review audio</h2>
        <p className="mx-auto max-w-lg text-sm leading-6 text-slate-400">
          Kiểm tra giọng đọc, nhịp nghỉ và phần cuối câu trước khi chuyển sang dựng video.
        </p>
      </div>

      <div className="rounded-xl border border-surface-600 bg-surface-900 p-4 sm:p-5">
        <div className="mb-4 flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent-500/10 text-accent-300">
            <FileAudio size={19} />
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-white">{audioFile || 'Audio mới tạo'}</p>
            <p className="mt-0.5 text-xs text-slate-400">
              {audioDuration > 0 ? `${fmtDuration(audioDuration)} · đã sẵn sàng review` : 'Đang đọc thời lượng audio…'}
            </p>
          </div>
        </div>
        <audio controls preload="metadata" src={audioUrl(audioFile, project, mediaVersion)} className="block w-full">
          Trình duyệt này không hỗ trợ phát audio.
        </audio>
        {processing && (
          <div className="mt-4 rounded-lg border border-surface-600 bg-surface-950/50 px-3 py-2.5 text-xs text-slate-400">
            <p className="font-semibold uppercase tracking-wider text-slate-500">Thông số audio đã dùng</p>
            <div className="mt-2 grid gap-1 sm:grid-cols-2">
              <span>Giọng: <span className="font-mono text-slate-300">{processing.voice_id || 'Không lưu'}</span></span>
              <span>Tốc độ: <span className="text-slate-300">{processing.speed ?? '—'}x</span></span>
              <span>Trim đầu/cuối: <span className="text-slate-300">{processing.trim_silence ? `Bật (${processing.silence_db ?? '—'} dB)` : 'Tắt'}</span></span>
              <span>Nối đoạn: <span className="text-slate-300">{Math.round((processing.effective_gap_sec ?? 0.03) * 1000)}ms{Number(processing.join_fade_sec || 0) > 0 ? ` + fade ${Math.round(Number(processing.join_fade_sec) * 1000)}ms` : ' · không fade'}</span></span>
            </div>
          </div>
        )}
      </div>

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-between">
        <button type="button" onClick={onRegenerate} className="inline-flex items-center justify-center gap-2 rounded-lg border border-surface-500 px-4 py-2.5 text-sm font-semibold text-slate-300 transition-colors hover:border-accent-400 hover:text-white">
          <RotateCcw size={16} /> Tạo lại audio
        </button>
        <button type="button" onClick={onContinue} className="inline-flex items-center justify-center gap-2 rounded-lg bg-accent-600 px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-accent-500">
          Tiếp tục dựng video <ChevronRight size={16} />
        </button>
      </div>
    </section>
  )
}
