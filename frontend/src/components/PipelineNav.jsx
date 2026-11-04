import { BookOpen, Check, Film, Mic2, Subtitles, Video } from 'lucide-react'

const STAGES = [
  { key: 'STORY', label: 'Nội dung', icon: BookOpen },
  { key: 'AUDIO', label: 'Audio', icon: Mic2 },
  { key: 'VIDEO', label: 'Video', icon: Video },
  { key: 'SUBTITLE', label: 'Phụ đề', icon: Subtitles },
  { key: 'DONE', label: 'Hậu kỳ', icon: Film },
]

const STAGE_GROUP = {
  STORY_START: 'STORY',
  STORY_INTERVIEW: 'STORY',
  STORY_GENERATING: 'STORY',
  STORY_EDITING: 'STORY',
  AUDIO_CONFIG: 'AUDIO',
  AUDIO_GENERATING: 'AUDIO',
  AUDIO_REVIEW: 'AUDIO',
  VIDEO_UPLOAD: 'VIDEO',
  VIDEO_COMPOSING: 'VIDEO',
  SUBTITLE_CONFIG: 'SUBTITLE',
  DONE: 'DONE',
}

const GROUP_ORDER = ['STORY', 'AUDIO', 'VIDEO', 'SUBTITLE', 'DONE']

export default function PipelineNav({ stage, canOpen = {}, onOpenStage }) {
  const current = STAGE_GROUP[stage] || 'STORY'
  const currentIdx = GROUP_ORDER.indexOf(current)

  return (
    <div className="flex items-center justify-center gap-0 mb-8">
      {STAGES.map((s, i) => {
        const done = i < currentIdx
        const active = s.key === current
        const clickable = Boolean(onOpenStage) && Boolean(canOpen[s.key])
        const Icon = s.icon
        return (
          <div key={s.key} className="flex items-center">
            <button
              type="button"
              onClick={() => clickable && onOpenStage(s.key)}
              disabled={!clickable}
              className={`flex flex-col items-center gap-1 ${clickable ? 'cursor-pointer' : 'cursor-default'}`}
              title={clickable ? `Mở lại bước ${s.label}` : s.label}
            >
              <div className={`w-9 h-9 rounded-full flex items-center justify-center border-2 transition-all ${
                done ? 'bg-accent-600 border-accent-600' :
                active ? 'bg-accent-500/20 border-accent-500' :
                  'bg-surface-800 border-surface-600'
              }`}>
                {done
                  ? <Check size={16} className="text-white" />
                  : <Icon size={16} className={active ? 'text-accent-400' : 'text-slate-600'} />
                }
              </div>
              <span className={`text-xs font-medium ${active ? 'text-accent-400' : done ? 'text-slate-400' : 'text-slate-600'}`}>
                {s.label}
              </span>
            </button>
            {i < STAGES.length - 1 && (
              <div className={`w-16 h-0.5 mx-1 mb-5 transition-all ${i < currentIdx ? 'bg-accent-600' : 'bg-surface-600'}`} />
            )}
          </div>
        )
      })}
    </div>
  )
}
