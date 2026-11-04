import { useState } from 'react'
import { CheckCircle2, FileText, Languages, Loader2 } from 'lucide-react'

export default function LocalizationReview({ sourceTitle, sourceText, result, onApprove, onBack }) {
  const [title, setTitle] = useState(result?.title || sourceTitle || '')
  const [text, setText] = useState(result?.text || '')
  const [saving, setSaving] = useState(false)
  const qa = result?.qa || {}; const warnings = Array.isArray(qa.warnings) ? qa.warnings : []
  async function approve() { setSaving(true); try { await onApprove?.({ ...result, title, text, stick_figure: result?.stick_figure ? { ...result.stick_figure, tts_text: text } : null }) } finally { setSaving(false) } }
  return <div className="space-y-4">
    <div className="flex items-start gap-3"><div className="rounded-lg bg-sky-500/15 p-2 text-sky-300"><Languages size={20} /></div><div><h2 className="text-lg font-bold text-white">Duyệt bản localized</h2><p className="text-xs text-slate-400">{result?.source_locale} → {result?.target_locale}. Bạn có thể chỉnh trực tiếp trước khi lời đọc, subtitle và ảnh dùng bản này.</p></div></div>
    <div className="grid gap-4 xl:grid-cols-2">
      <section className="space-y-2"><p className="flex items-center gap-1.5 text-xs font-semibold text-slate-400"><FileText size={13} /> Bản gốc</p><div className="max-h-[420px] overflow-y-auto rounded-xl border border-surface-600 bg-black/20 p-4 text-sm leading-7 text-slate-300 whitespace-pre-wrap">{sourceTitle && <p className="mb-3 font-semibold text-white">{sourceTitle}</p>}{sourceText}</div></section>
      <section className="space-y-2"><p className="text-xs font-semibold text-sky-300">Bản dùng để xuất</p><input value={title} onChange={e => setTitle(e.target.value)} className="w-full rounded-lg border border-sky-500/35 bg-surface-900 px-3 py-2 text-sm font-semibold text-white focus:border-sky-400 focus:outline-none" /><textarea value={text} onChange={e => setText(e.target.value)} rows={15} className="w-full resize-y rounded-xl border border-sky-500/35 bg-surface-900 p-3 text-sm leading-7 text-white focus:border-sky-400 focus:outline-none" /></section>
    </div>
    <div className="rounded-xl border border-emerald-500/25 bg-emerald-500/5 p-3 text-xs text-slate-300"><p className="flex items-center gap-1.5 font-semibold text-emerald-300"><CheckCircle2 size={14} /> Language Agent QA · faithful: {qa.fidelity || '—'} · natural: {qa.naturalness || '—'} · terminology: {qa.terminology || '—'}</p>{warnings.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-5 text-amber-200">{warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>}</div>
    <div className="flex gap-3"><button onClick={onBack} className="rounded-lg border border-surface-500 px-4 py-2.5 text-sm text-slate-300 hover:text-white">Quay lại bản gốc</button><button onClick={approve} disabled={!text.trim() || saving} className="ml-auto inline-flex items-center gap-2 rounded-lg bg-accent-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-accent-500 disabled:opacity-50">{saving ? <Loader2 size={16} className="animate-spin" /> : <CheckCircle2 size={16} />}Duyệt & tạo audio</button></div>
  </div>
}
