import { Languages } from 'lucide-react'

function localeLabel(locale) {
  if (!locale || locale === 'auto') return 'Tự nhận diện'

  try {
    const [language, region] = String(locale).split('-')
    const languageName = new Intl.DisplayNames(['vi'], { type: 'language' }).of(language)
    const regionName = region ? new Intl.DisplayNames(['vi'], { type: 'region' }).of(region) : ''
    return regionName ? `${languageName} (${regionName})` : languageName
  } catch {
    return String(locale)
  }
}

export default function PipelineLanguageStatus({ language }) {
  const settings = { source_locale: 'auto', target_locale: 'vi-VN', ...(language || {}) }
  const configuredSource = settings.source_locale || 'auto'
  const detectedSource = settings.detected_source_locale || ''
  const source = detectedSource || configuredSource
  const sourceNote = configuredSource === 'auto'
    ? (detectedSource ? 'đã nhận diện' : 'sẽ nhận diện khi duyệt')
    : 'đã chọn'

  return (
    <div className="mb-6 flex flex-wrap items-center justify-center gap-x-3 gap-y-2 text-xs" aria-label="Ngôn ngữ của nội dung">
      <span className="inline-flex items-center gap-1.5 text-slate-400">
        <Languages size={14} className="text-sky-300" />
        Ngôn ngữ nội dung
      </span>
      <span className="inline-flex items-center gap-1.5 rounded-md border border-surface-500 bg-surface-900/80 px-2.5 py-1.5 text-slate-200">
        <span>Đầu vào:</span>
        <strong className="font-medium text-white">{localeLabel(source)}</strong>
        <span className="text-slate-500">({sourceNote})</span>
      </span>
      <span className="text-slate-600" aria-hidden="true">→</span>
      <span className="inline-flex items-center gap-1.5 rounded-md border border-sky-500/25 bg-sky-500/5 px-2.5 py-1.5 text-slate-200">
        <span>Xuất bản:</span>
        <strong className="font-medium text-sky-200">{localeLabel(settings.target_locale)}</strong>
      </span>
    </div>
  )
}
