import { useState, useEffect, useRef } from 'react'
import { Send, Loader2, Sparkles, PenLine } from 'lucide-react'
import { startInterviewTurn } from '../api.js'

export default function StoryInterview({ prompt, model, missing, initialHistory = [], onHistoryChange, onReady }) {
  const [history, setHistory] = useState(initialHistory)
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [aiStatus, setAiStatus] = useState('AI đang trả lời...')
  const bottomRef = useRef(null)
  const inputRef = useRef(null)
  const stopRef = useRef(null)

  // Start interview automatically on mount
  useEffect(() => {
    if (!initialHistory.length) startInterview()
  }, [])

  useEffect(() => {
    onHistoryChange?.(history)
  }, [history])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [history, loading])

  async function startInterview() {
    const firstMsg = missing?.length
      ? `Tôi nhận thấy prompt của bạn còn thiếu: ${missing.join(', ')}. Hãy để tôi hỏi thêm một số câu.`
      : ''
    streamAssistant([], firstMsg)
  }

  function streamAssistant(baseHistory, userMessage) {
    setLoading(true)
    setError('')
    setAiStatus('Đang kết nối LLM...')
    const assistantIndex = baseHistory.length
    setHistory([...baseHistory, { role: 'assistant', content: '' }])

    stopRef.current = startInterviewTurn(
      baseHistory,
      userMessage,
      model,
      (ev) => {
        if (ev.type === 'status') {
          setAiStatus(ev.message || 'LLM đang xử lý...')
          return
        }
        if (ev.type !== 'text') return
        setAiStatus('AI đang trả lời...')
        setHistory(prev => prev.map((m, i) => (
          i === assistantIndex ? { ...m, content: m.content + ev.content } : m
        )))
      },
      () => {
        setLoading(false)
        inputRef.current?.focus()
      },
      (e) => {
        setLoading(false)
        setError(e?.message || String(e))
        inputRef.current?.focus()
      },
    )
  }

  function sendMessage() {
    const msg = input.trim()
    if (!msg || loading) return
    setInput('')
    setError('')

    const newHistory = [...history, { role: 'user', content: msg }]
    streamAssistant(newHistory, '')
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 mb-2">
        <Sparkles size={16} className="text-accent-400" />
        <h2 className="text-lg font-bold text-white">AI đang khai thác nội dung truyện</h2>
      </div>

      <div className="glass rounded-2xl p-4 space-y-3 max-h-96 overflow-y-auto">
        {history.map((m, i) => (
          <div key={i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
              m.role === 'user'
                ? 'bg-accent-600 text-white rounded-br-sm'
                : 'bg-surface-700 text-slate-200 rounded-bl-sm'
            }`}>
              {m.role === 'assistant' && (
                <div className="flex items-center gap-1 mb-1">
                  <Sparkles size={10} className="text-accent-400" />
                  <span className="text-xs text-accent-400 font-medium">AI</span>
                </div>
              )}
              {m.content ? (
                <p className="whitespace-pre-wrap">{m.content}</p>
              ) : (
                <div className="flex items-center gap-2 text-slate-400">
                  <Loader2 size={12} className="animate-spin text-accent-400" />
                  <span>{aiStatus}</span>
                </div>
              )}
            </div>
          </div>
        ))}
        {error && <p className="text-xs text-red-400 text-center">{error}</p>}
        <div ref={bottomRef} />
      </div>

      <div className="flex gap-2">
        <textarea
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Trả lời câu hỏi của AI... (Enter để gửi, Shift+Enter xuống dòng)"
          rows={2}
          disabled={loading}
          className="flex-1 bg-surface-900 border border-surface-500 rounded-xl px-3 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-accent-500 resize-none disabled:opacity-50"
        />
        <button
          onClick={sendMessage}
          disabled={loading || !input.trim()}
          className="px-4 py-2 rounded-xl bg-accent-600 hover:bg-accent-500 text-white disabled:opacity-40 transition-all flex items-center justify-center"
        >
          <Send size={16} />
        </button>
      </div>

      <button
        onClick={() => onReady(history)}
        className="w-full flex items-center justify-center gap-2 py-3 rounded-xl text-sm font-semibold border-2 border-accent-500 text-accent-400 hover:bg-accent-500/10 transition-all"
      >
        <PenLine size={16} />
        Đã đủ thông tin, viết truyện thôi!
      </button>
    </div>
  )
}
