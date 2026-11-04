import { useEffect, useMemo, useState } from 'react'
import { ChevronRight, FileText, FolderOpen, Mic2 } from 'lucide-react'
import { getTtsProfile } from '../api.js'

function safeStem(value) {
  return (value || '')
    .replace(/[<>:"/\\|?*\x00-\x1f]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/[^\w-]+/g, '_')
    .replace(/^_+|_+$/g, '') || `truyen_${new Date().toISOString().slice(0, 10)}`
}

export default function AudioConfig({ storyText, storyTitle, outputLocale = 'vi-VN', onStart }) {
  const defaultName = useMemo(() => safeStem(storyTitle), [storyTitle])
  const [outputName, setOutputName] = useState(defaultName)
  const [ttsProfile, setTtsProfile] = useState(null)
  const preview = storyText?.slice(0, 200) || ''
  const chars = storyText?.length || 0

  useEffect(() => {
    let active = true
    getTtsProfile(outputLocale)
      .then(data => { if (active) setTtsProfile(data) })
      .catch(error => { if (active) setTtsProfile({ configured: false, error: error.message }) })
    return () => { active = false }
  }, [outputLocale])

  function handleStart() {
    onStart(storyText, (outputName.trim() || defaultName))
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2">
        <Mic2 size={16} className="text-accent-400" />
        <h2 className="text-lg font-bold text-white">Cấu hình tạo Audio</h2>
      </div>

      <div className="glass rounded-xl p-4 space-y-2">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-1.5 text-xs font-medium text-slate-400 uppercase tracking-wider">
            <FileText size={12} /> Truyện đã duyệt
          </span>
          <span className="text-xs text-slate-500 font-mono">{chars.toLocaleString()} ký tự</span>
        </div>
        <p className="text-xs text-sky-300">Ngôn ngữ xuất: {outputLocale}. {ttsProfile?.configured ? <>Provider: <strong>{ttsProfile.provider_label}</strong> · Giọng: <span className="font-mono">{ttsProfile.profile?.voice_id}</span></> : ttsProfile?.error || 'Đang kiểm tra voice profile…'}</p>
        <p className="text-xs text-slate-400 leading-relaxed line-clamp-3">{preview}{chars > 200 ? '...' : ''}</p>
      </div>

      <div className="space-y-1.5">
        <label className="flex items-center gap-1.5 text-xs font-medium text-slate-400 uppercase tracking-wider">
          <FolderOpen size={12} /> Tên file audio xuất ra
        </label>
        <input
          type="text"
          value={outputName}
          onChange={e => setOutputName(e.target.value)}
          placeholder={defaultName}
          className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-600 focus:outline-none focus:border-accent-500 transition-colors"
        />
        <p className="text-xs text-slate-600">Project sẽ dùng tên truyện: <span className="text-slate-400">{storyTitle}</span></p>
      </div>

      <p className="text-xs text-slate-500 bg-surface-800 rounded-lg px-3 py-2">
        Cài đặt voice, tốc độ, số workers... có thể chỉnh trong phần <strong>Cấu hình</strong> ở trên cùng.
      </p>

      <button
        onClick={handleStart}
        disabled={!storyText || ttsProfile?.configured === false}
        className="w-full flex items-center justify-center gap-2 py-3 rounded-xl text-sm font-semibold bg-accent-600 hover:bg-accent-500 text-white disabled:opacity-40 transition-all"
      >
        <Mic2 size={16} />
        {ttsProfile?.configured === false ? 'Cần cấu hình voice' : 'Tạo Audio'}
        <ChevronRight size={16} />
      </button>
    </div>
  )
}
