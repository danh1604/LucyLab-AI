import AppSelect from './AppSelect.jsx'
import LanguageSettings from './LanguageSettings.jsx'
import { useEffect, useState } from 'react'
import { ChevronRight, Images, Loader2, PenLine, Sparkles } from 'lucide-react'
import { analyzePrompt, extractStickFigureScript, getModels } from '../api.js'

const DEFAULT_MODEL = 'claude-sonnet-4-6'

export default function StoryStart({
  storyTitle,
  onStoryTitleChange,
  onManual,
  onPromptStarted,
  onAIReady,
  onDirectGenerate,
  onStickFigureReady,
  languageSettings,
  onLanguageChange,
}) {
  const [mode, setMode] = useState(null)
  const [prompt, setPrompt] = useState('')
  const [sourceScript, setSourceScript] = useState('')
  const [model, setModel] = useState(DEFAULT_MODEL)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [models, setModels] = useState({ anthropic: [], ollama: [], codex: [], opencode: [] })

  useEffect(() => {
    getModels().then(setModels).catch(() => {})
  }, [])

  function requireTitle() {
    if (!storyTitle?.trim()) {
      setError('Vui lòng nhập tên truyện.')
      return false
    }
    return true
  }

  async function handleContinue() {
    if (!requireTitle()) return
    if (!prompt.trim()) {
      setError('Vui lòng nhập hướng dẫn viết truyện.')
      return
    }
    setError('')
    setLoading(true)
    try {
      await onPromptStarted?.(storyTitle.trim(), prompt.trim(), model)
      const result = await analyzePrompt(prompt.trim(), model)
      if (result.complete) {
        onDirectGenerate(storyTitle.trim(), prompt.trim(), model)
      } else {
        onAIReady(storyTitle.trim(), prompt.trim(), model, result.missing || [])
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function handleStickFigureContinue() {
    if (!requireTitle()) return
    if (sourceScript.trim().length < 80) {
      setError('Kịch bản cần ít nhất 80 ký tự để AI tách lời đọc.')
      return
    }
    setError('')
    setLoading(true)
    try {
      const blueprint = await extractStickFigureScript(sourceScript.trim(), model)
      if (!blueprint?.tts_text?.trim() || !blueprint?.narration_units?.length) {
        throw new Error('AI chưa tách được lời đọc thành các đơn vị narration. Hãy thử lại với kịch bản đầy đủ hơn.')
      }
      await onStickFigureReady?.(storyTitle.trim(), blueprint, model)
    } catch (e) {
      setError(e.message || 'Không thể phân tích kịch bản người que')
    } finally {
      setLoading(false)
    }
  }

  if (!mode) {
    return (
      <div className="space-y-4">
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Tên truyện / tên project</label>
          <input
            value={storyTitle}
            onChange={e => onStoryTitleChange?.(e.target.value)}
            placeholder="VD: Đất đỏ không ngủ"
            className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-accent-500"
          />
        </div>

        <LanguageSettings value={languageSettings} onChange={onLanguageChange} />

        <h2 className="text-xl font-bold text-white text-center mb-6">Bạn muốn bắt đầu thế nào?</h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <button
            onClick={() => requireTitle() && onManual(storyTitle.trim())}
            className="glass rounded-2xl p-6 text-left hover:border-slate-500 border border-surface-600 transition-all group"
          >
            <PenLine size={28} className="text-slate-400 mb-3 group-hover:text-white transition-colors" />
            <p className="text-white font-semibold mb-1">Tự viết / Paste truyện</p>
            <p className="text-xs text-slate-500">Tôi đã có sẵn nội dung, muốn paste vào và duyệt</p>
          </button>
          <button
            onClick={() => { setError(''); setMode('ai') }}
            className="glass rounded-2xl p-6 text-left hover:border-accent-500 border border-surface-600 transition-all group"
          >
            <Sparkles size={28} className="text-accent-400 mb-3 group-hover:text-accent-300 transition-colors" />
            <p className="text-white font-semibold mb-1">Nhờ AI viết truyện</p>
            <p className="text-xs text-slate-500">Mô tả yêu cầu, AI sẽ hỏi thêm rồi viết cho bạn</p>
          </button>
          <button
            onClick={() => { setError(''); setMode('stick') }}
            className="glass rounded-2xl p-6 text-left hover:border-sky-400 border border-surface-600 transition-all group"
          >
            <Images size={28} className="text-sky-300 mb-3 group-hover:text-sky-200 transition-colors" />
            <p className="text-white font-semibold mb-1">AI lọc content · Người que</p>
            <p className="text-xs text-slate-500">Paste kịch bản hoàn chỉnh, AI tách TTS, cảnh, thời lượng và storyboard biến thiên</p>
          </button>
        </div>

        {error && <p className="text-xs text-red-400 text-center">{error}</p>}
      </div>
    )
  }

  if (mode === 'stick') {
    return (
      <div className="space-y-4">
        <button onClick={() => setMode(null)} className="text-xs text-slate-500 hover:text-slate-300 transition-colors">
          ← Quay lại
        </button>
        <div>
          <h2 className="text-lg font-bold text-white">AI lọc content cho video người que</h2>
          <p className="mt-1 text-xs text-slate-500">
            AI giữ nguyên lời đọc, tự chia cảnh theo nhịp kể, quyết định thời lượng từng ảnh và tạo blueprint storyboard. Bạn vẫn có thể sửa lời đọc trước khi tạo TTS.
          </p>
        </div>

        <div className="space-y-1">
          <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Tên truyện / tên project</label>
          <input
            autoFocus
            value={storyTitle}
            onChange={e => onStoryTitleChange?.(e.target.value)}
            placeholder="VD: Ai đã giết Ötzi?"
            className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-sky-400"
          />
        </div>

        <div className="space-y-1">
          <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Kịch bản hoàn chỉnh</label>
          <textarea
            value={sourceScript}
            onChange={e => setSourceScript(e.target.value)}
            placeholder="Paste toàn bộ kịch bản: lời thoại, ghi chú hình ảnh, nhịp độ… AI sẽ tách phần cần TTS và phần cần minh hoạ."
            rows={13}
            className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-sky-400 resize-y"
          />
          <p className="text-xs text-slate-600 text-right">{sourceScript.length} ký tự</p>
        </div>

        {error && <p className="text-xs text-red-400">{error}</p>}

        <button
          onClick={handleStickFigureContinue}
          disabled={loading || sourceScript.trim().length < 80 || !storyTitle?.trim()}
          className="w-full flex items-center justify-center gap-2 py-3 rounded-xl text-sm font-semibold bg-sky-600 hover:bg-sky-500 text-white disabled:opacity-50 disabled:cursor-not-allowed transition-all"
        >
          {loading ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
          {loading ? 'Đang tách nội dung và storyboard...' : 'AI lọc content'}
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <button onClick={() => setMode(null)} className="text-xs text-slate-500 hover:text-slate-300 transition-colors">
        ← Quay lại
      </button>
      <h2 className="text-lg font-bold text-white">Hướng dẫn viết truyện cho AI</h2>
      <p className="text-xs text-slate-500">
        Nhập quy tắc, phong cách, thể loại... AI sẽ phân tích xem cần hỏi thêm gì về nội dung cốt truyện.
      </p>

      <div className="space-y-1">
        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Tên truyện / tên project</label>
        <input
          autoFocus
          value={storyTitle}
          onChange={e => onStoryTitleChange?.(e.target.value)}
          placeholder="VD: Đất đỏ không ngủ"
          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-accent-500"
        />
      </div>

      <div className="space-y-1">
        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Model AI</label>
        <AppSelect
          value={model}
          onChange={e => setModel(e.target.value)}
          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-accent-500"
        >
          {models.anthropic.length > 0 && (
            <optgroup label="Claude Code (local config)">
              {models.anthropic.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
            </optgroup>
          )}
          {models.ollama.length > 0 && (
            <optgroup label="Ollama (local)">
              {models.ollama.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
            </optgroup>
          )}
          {models.codex?.length > 0 && (
            <optgroup label="Codex CLI">
              {models.codex.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
            </optgroup>
          )}
          {models.opencode?.length > 0 && (
            <optgroup label="OpenCode CLI">
              {models.opencode.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
            </optgroup>
          )}
          {models.anthropic.length === 0 && models.ollama.length === 0 && !models.codex?.length && !models.opencode?.length && (
            <option value={DEFAULT_MODEL}>Claude Sonnet 4.6</option>
          )}
        </AppSelect>
      </div>

      <div className="space-y-1">
        <label className="text-xs font-medium text-slate-400 uppercase tracking-wider">Hướng dẫn / Prompt</label>
        <textarea
          value={prompt}
          onChange={e => setPrompt(e.target.value)}
          placeholder="Ví dụ: Viết truyện ngắn tiếng Việt, thể loại kinh dị tâm lý, văn phong phù hợp đọc thành tiếng..."
          rows={6}
          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-accent-500 resize-none"
        />
        <p className="text-xs text-slate-600 text-right">{prompt.length} ký tự</p>
      </div>

      {error && <p className="text-xs text-red-400">{error}</p>}

      <button
        onClick={handleContinue}
        disabled={loading || !prompt.trim() || !storyTitle?.trim()}
        className="w-full flex items-center justify-center gap-2 py-3 rounded-xl text-sm font-semibold bg-accent-600 hover:bg-accent-500 text-white disabled:opacity-50 disabled:cursor-not-allowed transition-all"
      >
        {loading ? <Loader2 size={16} className="animate-spin" /> : <ChevronRight size={16} />}
        {loading ? 'Đang phân tích...' : 'Tiếp tục'}
      </button>
    </div>
  )
}
