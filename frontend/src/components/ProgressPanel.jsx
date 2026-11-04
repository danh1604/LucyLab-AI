import { CheckCircle, XCircle, Loader, AlertTriangle } from 'lucide-react'

function Waveform() {
  return (
    <div className="waveform">
      {[10, 18, 14, 20, 12].map((h, i) => (
        <span key={i} style={{ height: h }} />
      ))}
    </div>
  )
}

export default function ProgressPanel({ job }) {
  if (!job) return null

  const { total, segments = [], completed = 0, merging, normalizingNumbers, numberReplacementCount = 0, done, error, outputFile, sizeMb, successCount, errorCount } = job

  const pct = total > 0 ? Math.round((completed / total) * 100) : 0

  return (
    <div className="glass rounded-2xl overflow-hidden animate-fade-in">
      {/* Header */}
      <div className="px-5 py-4 border-b border-surface-600">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-3">
            {done ? (
              job.success
                ? <CheckCircle size={18} className="text-success" />
                : <XCircle size={18} className="text-danger" />
            ) : (merging || normalizingNumbers) ? (
              <div className="w-4 h-4 border-2 border-accent-500 border-t-transparent rounded-full animate-spin" />
            ) : (
              <Waveform />
            )}
            <span className="text-sm font-semibold text-white">
              {done
                ? (job.success ? 'Hoàn tất!' : 'Có lỗi xảy ra')
                : normalizingNumbers
                ? 'Đang chuẩn hóa cách đọc số bằng AI...'
                : merging
                ? 'Đang ghép file MP3...'
                : `Đang xử lý ${completed}/${total} đoạn`}
            </span>
          </div>
          <span className="text-sm font-bold text-accent-400">{normalizingNumbers ? 'AI' : `${pct}%`}</span>
        </div>

        {/* Progress bar */}
        <div className="h-1.5 bg-surface-600 rounded-full overflow-hidden">
          <div
            className="h-full progress-bar rounded-full"
            style={{ width: `${done && job.success ? 100 : pct}%` }}
          />
        </div>

        {done && (
          <div className="flex gap-4 mt-3 text-xs">
            <span className="text-success">✓ {successCount} đoạn thành công</span>
            {errorCount > 0 && <span className="text-danger">✗ {errorCount} lỗi</span>}
            {sizeMb > 0 && <span className="text-slate-400">{sizeMb} MB</span>}
          </div>
        )}

        {!done && !normalizingNumbers && numberReplacementCount > 0 && (
          <p className="mt-3 text-xs text-slate-400">Đã chuẩn hóa {numberReplacementCount} biểu thức số cho giọng đọc; phụ đề vẫn giữ script gốc.</p>
        )}

        {error && (
          <div className="mt-3 flex items-start gap-2 text-xs text-warn bg-warn/10 border border-warn/20 rounded-lg p-3">
            <AlertTriangle size={12} className="mt-0.5 shrink-0" />
            <span>{error === 'JWT_EXPIRED' ? 'JWT Token hết hạn! Cập nhật token mới trong Cấu hình.' : error}</span>
          </div>
        )}
      </div>

      {/* Segment list */}
      <div className="max-h-72 overflow-y-auto">
        {segments.map((seg) => (
          <div
            key={seg.index}
            className="segment-enter flex items-center gap-3 px-5 py-2.5 border-b border-surface-700 last:border-0"
          >
            <div className="w-8 text-xs text-slate-600 font-mono text-right shrink-0">
              {String(seg.index).padStart(4, '0')}
            </div>
            <div className="shrink-0">
              {seg.status === 'ok' && <CheckCircle size={13} className="text-success" />}
              {seg.status === 'error' && <XCircle size={13} className="text-danger" />}
              {seg.status === 'processing' && (
                <div className="w-3.5 h-3.5 border-2 border-accent-400 border-t-transparent rounded-full animate-spin" />
              )}
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-xs text-slate-400 truncate">
                {seg.preview || seg.error || 'Đang xử lý...'}
              </p>
            </div>
            {seg.filename && (
              <span className="text-xs text-slate-600 font-mono shrink-0">{seg.filename}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
