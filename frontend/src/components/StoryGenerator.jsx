import { useState, useEffect, useRef } from 'react'
import { Sparkles, Loader2, RotateCcw, ChevronDown, ChevronUp } from 'lucide-react'
import { startGenerateStory } from '../api.js'

export default function StoryGenerator({ prompt, interviewHistory, model, initialText = '', onDone, onProgress }) {
  const [text, setText] = useState(initialText || '')
  const [chars, setChars] = useState((initialText || '').length)
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [showContext, setShowContext] = useState(true)
  const [status, setStatus] = useState('Dang ket noi LLM...')
  const textareaRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    setText(initialText || '')
    setChars((initialText || '').length)
    setError('')
    setDone(false)
    setStatus('Dang ket noi LLM...')
    startGenerateStory(
      prompt,
      interviewHistory,
      model,
      (ev) => {
        if (cancelled) return
        if (ev.type === 'status') {
          setStatus(ev.message || 'LLM dang xu ly...')
        }
        if (ev.type === 'text') {
          setStatus('AI dang viet truyen...')
          setText(prev => {
            const next = prev + ev.content
            setChars(next.length)
            onProgress?.(next)
            return next
          })
          // Auto-scroll
          if (textareaRef.current) {
            textareaRef.current.scrollTop = textareaRef.current.scrollHeight
          }
        }
      },
      () => { if (!cancelled) setDone(true) },
      (err) => {
        if (!cancelled) {
          setError(typeof err === 'string' ? err : (err?.message || String(err)))
        }
      },
    )
    return () => { cancelled = true }
  }, [prompt, interviewHistory, model, attempt])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Sparkles size={16} className="text-accent-400" />
          <h2 className="text-lg font-bold text-white">AI đang viết truyện...</h2>
        </div>
        <div className="flex items-center gap-2">
          {!done && !error && <Loader2 size={14} className="animate-spin text-accent-400" />}
          <span className="text-xs text-slate-500 font-mono">{chars.toLocaleString()} ký tự</span>
        </div>
      </div>

      {!done && !error && (
        <div className="rounded-lg border border-accent-500/20 bg-accent-500/10 px-3 py-2 text-xs text-accent-200">
          {status}
        </div>
      )}

      <div className="rounded-xl border border-surface-600 bg-surface-900/60 overflow-hidden">
        <button
          onClick={() => setShowContext(v => !v)}
          className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left hover:bg-surface-800/60 transition-colors"
        >
          <div>
            <p className="text-sm font-semibold text-white">Nội dung đã chốt</p>
            <p className="text-xs text-slate-500">
              Prompt ban đầu{interviewHistory?.length ? ` · ${interviewHistory.length} lượt hỏi đáp` : ''}
            </p>
          </div>
          {showContext ? <ChevronUp size={15} className="text-slate-500" /> : <ChevronDown size={15} className="text-slate-500" />}
        </button>

        {showContext && (
          <div className="border-t border-surface-600 p-4 space-y-3 max-h-80 overflow-y-auto">
            <div className="rounded-lg bg-surface-800 px-3 py-2">
              <p className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-1">Yêu cầu ban đầu</p>
              <p className="text-sm text-slate-200 whitespace-pre-wrap leading-relaxed">{prompt}</p>
            </div>

            {interviewHistory?.map((m, i) => (
              <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-[86%] rounded-xl px-3 py-2 text-sm leading-relaxed ${
                  m.role === 'user'
                    ? 'bg-accent-600/80 text-white rounded-br-sm'
                    : 'bg-surface-700 text-slate-200 rounded-bl-sm'
                }`}>
                  <p className="text-[11px] font-semibold uppercase tracking-wider opacity-70 mb-1">
                    {m.role === 'user' ? 'Bạn' : 'AI'}
                  </p>
                  <p className="whitespace-pre-wrap">{m.content}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <textarea
        ref={textareaRef}
        readOnly
        value={text}
        rows={18}
        className="w-full bg-surface-900 border border-surface-600 rounded-xl px-4 py-3 text-sm text-slate-200 leading-relaxed resize-none focus:outline-none font-mono"
        placeholder="Truyện sẽ hiện ra ở đây..."
      />

      {error && (
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs text-red-400">{error}</p>
          <button
            onClick={() => setAttempt(a => a + 1)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-red-400/30 bg-red-400/10 px-3 py-1.5 text-xs font-semibold text-red-300 hover:bg-red-400/20 transition-colors"
          >
            <RotateCcw size={13} />
            Thử lại
          </button>
        </div>
      )}

      {done && !error && (
        <button
          onClick={() => onDone(text)}
          className="w-full py-3 rounded-xl text-sm font-semibold bg-accent-600 hover:bg-accent-500 text-white transition-all"
        >
          Chỉnh sửa & Duyệt truyện →
        </button>
      )}
    </div>
  )
}
