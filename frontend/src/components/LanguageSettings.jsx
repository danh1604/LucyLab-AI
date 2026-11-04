import { useEffect, useState } from 'react'
import { Languages } from 'lucide-react'
import AppSelect from './AppSelect.jsx'
import { getLanguages } from '../api.js'

const FALLBACK = [
  { id: 'vi-VN', label: 'Tiếng Việt' }, { id: 'en-US', label: 'English (United States) (Tiếng Anh Mỹ)' }, { id: 'en-GB', label: 'English (United Kingdom) (Tiếng Anh Anh)' },
  { id: 'zh-CN', label: '中文（简体） (Tiếng Trung giản thể)' }, { id: 'ja-JP', label: '日本語 (Tiếng Nhật)' }, { id: 'ko-KR', label: '한국어 (Tiếng Hàn)' }, { id: 'hi-IN', label: 'हिन्दी (Tiếng Hindi)' }, { id: 'ru-RU', label: 'Русский (Tiếng Nga)' },
]

export default function LanguageSettings({ value, onChange, compact = false }) {
  const [languages, setLanguages] = useState(FALLBACK)
  const state = { source_locale: 'auto', target_locale: 'vi-VN', review_before_tts: true, ...(value || {}) }
  useEffect(() => { getLanguages().then(data => setLanguages(data.languages || FALLBACK)).catch(() => {}) }, [])
  const update = patch => onChange?.({ ...state, ...patch })
  return <div className={`${compact ? 'p-3' : 'p-4'} rounded-xl border border-sky-500/25 bg-sky-500/5 space-y-3`}>
    {!compact && <div className="flex items-start gap-2"><Languages size={17} className="mt-0.5 text-sky-300" /><div><p className="text-sm font-semibold text-white">Ngôn ngữ nội dung xuất bản</p><p className="text-xs leading-relaxed text-slate-400">Bản gốc luôn được giữ. TTS, ảnh có chữ, subtitle, thumbnail và metadata sẽ dùng ngôn ngữ đầu ra.</p></div></div>}
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <label className="space-y-1"><span className="text-xs font-medium text-slate-400">Ngôn ngữ đầu vào</span><AppSelect value={state.source_locale} onChange={e => update({ source_locale: e.target.value })} searchable className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white"><option value="auto">Tự nhận diện</option>{languages.map(language => <option key={language.id} value={language.id}>{language.label}</option>)}</AppSelect></label>
      <label className="space-y-1"><span className="text-xs font-medium text-slate-400">Ngôn ngữ đầu ra</span><AppSelect value={state.target_locale} onChange={e => update({ target_locale: e.target.value })} searchable className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 text-sm text-white">{languages.map(language => <option key={language.id} value={language.id}>{language.label}</option>)}</AppSelect></label>
    </div>
    <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-300"><input type="checkbox" checked={state.review_before_tts} onChange={e => update({ review_before_tts: e.target.checked })} className="h-4 w-4 rounded border-surface-500 bg-surface-900" />Dừng để duyệt bản localized trước khi tạo audio</label>
  </div>
}
