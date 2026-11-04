import { useEffect, useState } from 'react'
import { BookOpen, Clock, FileText, FolderX, Plus, RefreshCcw, Trash2 } from 'lucide-react'
import { deleteStorySession, deleteStorySessionProject, getProjectSize, listStorySessions } from '../api.js'
import { useDialog } from './DialogProvider.jsx'

function fmtDate(value) {
  if (!value) return ''
  try {
    return new Date(value).toLocaleString('vi-VN', {
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return value
  }
}

const STAGE_LABELS = {
  STORY_START: 'Bắt đầu',
  STORY_INTERVIEW: 'Đang hỏi đáp',
  STORY_GENERATING: 'Đang viết',
  STORY_EDITING: 'Đang sửa truyện',
  AUDIO_CONFIG: 'Chờ tạo audio',
  AUDIO_GENERATING: 'Đang tạo audio',
  AUDIO_REVIEW: 'Chờ duyệt audio',
  VIDEO_UPLOAD: 'Chờ video',
  VIDEO_COMPOSING: 'Đang ghép video',
  SUBTITLE_CONFIG: 'Chờ phụ đề',
  SUBTITLE_RENDERING: 'Đang chèn phụ đề',
  DONE: 'Hoàn thành',
}

export default function StorySessionsPanel({ currentSessionId, refreshKey, onOpen, onNew, onDeletedSession, onDeletedProject }) {
  const { confirm: askConfirm, notify } = useDialog()
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(false)

  async function load() {
    setLoading(true)
    try {
      setSessions(await listStorySessions())
    } catch {
      setSessions([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [refreshKey])

  async function handleDelete(e, id) {
    e.stopPropagation()
    const ok = await askConfirm({
      tone: 'warning',
      title: 'Xóa session truyện?',
      message: 'Session này sẽ biến mất khỏi danh sách.',
      details: 'Thao tác này chỉ xóa metadata session, không xóa toàn bộ file nặng của project.',
      confirmText: 'Xóa session',
      cancelText: 'Hủy',
    })
    if (!ok) return
    await deleteStorySession(id)
    onDeletedSession?.(id)
    load()
  }

  async function handleDeleteProject(e, session) {
    e.stopPropagation()
    if (!session.current_project) {
      await notify({
        tone: 'info',
        title: 'Chưa có project để xóa',
        message: 'Session này chưa trỏ tới folder project nào.',
      })
      return
    }

    let sizeText = ''
    try {
      const size = await getProjectSize(session.current_project)
      sizeText = size.size_gb >= 1 ? `${size.size_gb}GB` : `${size.size_mb}MB`
    } catch {}

    const ok = await askConfirm({
      tone: 'danger',
      title: 'Xóa toàn bộ dự án?',
      message: `"${session.title}" sẽ bị xóa vĩnh viễn.`,
      details: [
        'Audio, video nền, video final, subtitle, timing và session sẽ bị xóa khỏi ổ đĩa.',
        sizeText ? `Dung lượng sẽ giải phóng khoảng: ${sizeText}` : '',
        'Không thể hoàn tác.',
      ].filter(Boolean).join('\n'),
      confirmText: 'Xóa dự án',
      cancelText: 'Giữ lại',
    })
    if (!ok) return

    try {
      const result = await deleteStorySessionProject(session.id)
      await notify({
        tone: 'success',
        title: 'Đã xóa dự án',
        message: `Giải phóng khoảng ${result.deleted_gb || 0}GB.`,
        details: result.deleted_sessions ? `Đã dọn ${result.deleted_sessions} session liên quan.` : '',
      })
      load()
      onDeletedProject?.(session.id, result)
    } catch (err) {
      await notify({
        tone: 'error',
        title: 'Xóa dự án thất bại',
        message: err?.message || 'Không thể xóa dự án.',
      })
    }
  }

  return (
    <div className="glass rounded-2xl overflow-hidden">
      <div className="px-5 py-4 border-b border-surface-600 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <BookOpen size={15} className="text-accent-400" />
          <span className="text-sm font-semibold text-white">Story sessions</span>
          {sessions.length > 0 && (
            <span className="text-xs text-slate-500 bg-surface-600 px-2 py-0.5 rounded-full">{sessions.length}</span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={load}
            className="p-1.5 rounded-lg text-slate-500 hover:text-slate-300 hover:bg-surface-700 transition-colors"
            title="Làm mới"
          >
            <RefreshCcw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
          <button
            onClick={onNew}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent-600 hover:bg-accent-500 text-white text-xs font-semibold transition-colors"
          >
            <Plus size={13} />
            Truyện mới
          </button>
        </div>
      </div>

      {sessions.length > 0 ? (
        <div className="divide-y divide-surface-700 max-h-80 overflow-y-auto">
          {sessions.map(s => (
            <button
              key={s.id}
              onClick={() => onOpen(s.id)}
              className={`w-full px-5 py-3 text-left transition-colors hover:bg-surface-700/70 ${
                currentSessionId === s.id ? 'bg-accent-600/10' : ''
              }`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-white truncate">{s.title}</p>
                  <div className="mt-1 flex items-center gap-3 text-xs text-slate-500">
                    <span className="inline-flex items-center gap-1">
                      <Clock size={11} />
                      {fmtDate(s.updated_at)}
                    </span>
                    <span>{STAGE_LABELS[s.stage] || s.stage}</span>
                    {s.chars > 0 && (
                      <span className="inline-flex items-center gap-1">
                        <FileText size={11} />
                        {s.chars.toLocaleString()} ký tự
                      </span>
                    )}
                  </div>
                </div>
                {s.current_project && (
                  <span
                    onClick={(e) => handleDeleteProject(e, s)}
                    className="p-1.5 rounded-lg text-slate-500 hover:text-red-300 hover:bg-red-500/10 transition-colors shrink-0"
                    title="Xóa toàn bộ dự án và file nặng"
                  >
                    <FolderX size={15} />
                  </span>
                )}
                <span
                  onClick={(e) => handleDelete(e, s.id)}
                  className="p-1.5 rounded-lg text-slate-600 hover:text-danger hover:bg-danger/10 transition-colors shrink-0"
                  title="Xóa session"
                >
                  <Trash2 size={13} />
                </span>
              </div>
            </button>
          ))}
        </div>
      ) : (
        <div className="px-5 py-6 text-center text-xs text-slate-600">
          Chưa có session truyện nào
        </div>
      )}
    </div>
  )
}
