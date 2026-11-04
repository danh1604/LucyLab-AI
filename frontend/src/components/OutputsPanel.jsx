import AppSelect from './AppSelect.jsx'
import { useState, useRef, useEffect } from 'react'
import {
  Play, Pause, Download, Trash2, Volume2, Music,
  Pencil, Check, X, Trash, RotateCcw, FolderPlus, Folder, FolderOpen,
  MoveRight,
} from 'lucide-react'
import {
  audioUrl, deleteOutput, renameOutput, moveOutputs,
  getTrash, restoreOutput, permanentDelete,
  getProjects, createProject, renameProject, deleteProject,
} from '../api.js'
import { useDialog } from './DialogProvider.jsx'

// ── Audio player ──────────────────────────────────────────────────────────────

function AudioPlayer({ file, project }) {
  const [playing, setPlaying] = useState(false)
  const [progress, setProgress] = useState(0)
  const [duration, setDuration] = useState(0)
  const audioRef = useRef()

  function fmt(s) {
    const m = Math.floor(s / 60)
    return `${m}:${Math.floor(s % 60).toString().padStart(2, '0')}`
  }

  function toggle() {
    const a = audioRef.current
    if (!a) return
    if (playing) { a.pause(); setPlaying(false) }
    else { a.play(); setPlaying(true) }
  }

  function seek(e) {
    const a = audioRef.current
    if (!a) return
    const rect = e.currentTarget.getBoundingClientRect()
    a.currentTime = ((e.clientX - rect.left) / rect.width) * duration
  }

  return (
    <div className="flex items-center gap-3">
      <audio
        ref={audioRef}
        src={audioUrl(file.name, project)}
        onTimeUpdate={e => setProgress(e.target.currentTime)}
        onDurationChange={e => setDuration(e.target.duration)}
        onEnded={() => setPlaying(false)}
      />
      <button
        onClick={toggle}
        className="w-8 h-8 rounded-full bg-accent-600 hover:bg-accent-500 flex items-center justify-center transition-colors shrink-0"
      >
        {playing
          ? <Pause size={12} className="text-white" />
          : <Play size={12} className="text-white ml-0.5" />}
      </button>
      <div className="flex-1 min-w-0">
        <div className="h-1 bg-surface-500 rounded-full cursor-pointer" onClick={seek}>
          <div
            className="h-full bg-accent-500 rounded-full"
            style={{ width: duration ? `${(progress / duration) * 100}%` : '0%' }}
          />
        </div>
        <div className="flex justify-between text-xs text-slate-600 mt-1">
          <span>{fmt(progress)}</span>
          <span>{fmt(duration)}</span>
        </div>
      </div>
    </div>
  )
}

// ── File row ──────────────────────────────────────────────────────────────────

function FileRow({ file, project, onDelete, onRename, selected, onToggleSelect }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef()

  useEffect(() => { if (editing) inputRef.current?.focus() }, [editing])

  function startEdit() { setDraft(file.name.replace(/\.mp3$/, '')); setError(''); setEditing(true) }
  function cancel() { setEditing(false); setError('') }

  async function confirm() {
    const newName = draft.trim() + '.mp3'
    if (newName === file.name) { cancel(); return }
    setSaving(true); setError('')
    try { await onRename(file.name, newName); setEditing(false) }
    catch (e) { setError(e.message) }
    finally { setSaving(false) }
  }

  function onKeyDown(e) {
    if (e.key === 'Enter') confirm()
    if (e.key === 'Escape') cancel()
  }

  return (
    <div className={`px-5 py-3 space-y-2 transition-colors ${selected ? 'bg-accent-600/5' : ''}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <button
            onClick={onToggleSelect}
            className={`shrink-0 w-4 h-4 rounded border flex items-center justify-center transition-colors ${
              selected ? 'bg-accent-600 border-accent-600' : 'border-slate-600 hover:border-slate-400'
            }`}
          >
            {selected && <Check size={9} className="text-white" />}
          </button>
          <Volume2 size={13} className="text-accent-400 shrink-0" />
          {editing ? (
            <div className="flex items-center gap-1 flex-1 min-w-0">
              <input
                ref={inputRef}
                value={draft}
                onChange={e => setDraft(e.target.value)}
                onKeyDown={onKeyDown}
                className="flex-1 min-w-0 bg-surface-800 border border-accent-500 rounded px-2 py-0.5 text-xs text-white focus:outline-none"
              />
              <span className="text-xs text-slate-600 shrink-0">.mp3</span>
            </div>
          ) : (
            <span className="text-xs font-medium text-white truncate">{file.name}</span>
          )}
          <span className="text-xs text-slate-600 shrink-0">{file.size_mb} MB</span>
        </div>

        <div className="flex items-center gap-1 shrink-0">
          {editing ? (
            <>
              <button onClick={confirm} disabled={saving}
                className="p-1.5 text-slate-500 hover:text-success transition-colors disabled:opacity-40" title="Lưu">
                <Check size={13} />
              </button>
              <button onClick={cancel}
                className="p-1.5 text-slate-500 hover:text-slate-300 transition-colors" title="Huỷ">
                <X size={13} />
              </button>
            </>
          ) : (
            <>
              <button onClick={startEdit}
                className="p-1.5 text-slate-500 hover:text-accent-400 transition-colors" title="Đổi tên">
                <Pencil size={13} />
              </button>
              <a href={audioUrl(file.name, project)} download={file.name}
                className="p-1.5 text-slate-500 hover:text-accent-400 transition-colors" title="Tải xuống">
                <Download size={13} />
              </a>
              <button onClick={() => onDelete(file.name)}
                className="p-1.5 text-slate-500 hover:text-danger transition-colors" title="Xoá">
                <Trash2 size={13} />
              </button>
            </>
          )}
        </div>
      </div>
      {error && <p className="text-xs text-red-400">{error}</p>}
      <AudioPlayer file={file} project={project} />
    </div>
  )
}

// ── Project tab ───────────────────────────────────────────────────────────────

function ProjectTab({ name, label, active, onSelect, onRename, onDelete }) {
  const { notify } = useDialog()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const inputRef = useRef()

  const isDefault = name === ''

  useEffect(() => { if (editing) inputRef.current?.focus() }, [editing])

  function startEdit(e) { e.stopPropagation(); setDraft(name); setEditing(true) }
  function cancel() { setEditing(false) }

  async function confirm() {
    if (!draft.trim() || draft.trim() === name) { cancel(); return }
    setSaving(true)
    try { await onRename(name, draft.trim()); setEditing(false) }
    catch (e) { notify({ tone: 'error', title: 'Không đổi được tên project', message: e.message }) }
    finally { setSaving(false) }
  }

  function onKeyDown(e) {
    if (e.key === 'Enter') confirm()
    if (e.key === 'Escape') cancel()
  }

  if (editing) {
    return (
      <div className="flex items-center gap-1 bg-surface-700 border border-accent-500 rounded-lg px-2 py-1">
        <input
          ref={inputRef}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          className="w-28 bg-transparent text-xs text-white focus:outline-none"
        />
        <button onClick={confirm} disabled={saving} className="text-slate-400 hover:text-success">
          <Check size={11} />
        </button>
        <button onClick={cancel} className="text-slate-400 hover:text-slate-200">
          <X size={11} />
        </button>
      </div>
    )
  }

  return (
    <button
      onClick={() => onSelect(name)}
      className={`group flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors whitespace-nowrap ${
        active
          ? 'bg-accent-600 text-white'
          : 'bg-surface-700 text-slate-400 hover:bg-surface-600 hover:text-white'
      }`}
    >
      {active
        ? <FolderOpen size={12} />
        : <Folder size={12} />}
      {label}
      {!isDefault && (
        <span className="hidden group-hover:flex items-center gap-0.5 ml-1">
          <span
            onClick={startEdit}
            className="hover:text-accent-300 transition-colors"
            title="Đổi tên"
          >
            <Pencil size={10} />
          </span>
          <span
            onClick={(e) => { e.stopPropagation(); onDelete(name) }}
            className="hover:text-danger transition-colors"
            title="Xóa project"
          >
            <X size={10} />
          </span>
        </span>
      )}
    </button>
  )
}

// ── Project bar ───────────────────────────────────────────────────────────────

function ProjectBar({ projects, currentProject, onSelect, onCreate, onRename, onDelete }) {
  const { notify } = useDialog()
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [saving, setSaving] = useState(false)
  const inputRef = useRef()

  useEffect(() => { if (creating) inputRef.current?.focus() }, [creating])

  async function confirmCreate() {
    if (!newName.trim()) { setCreating(false); return }
    setSaving(true)
    try { await onCreate(newName.trim()); setCreating(false); setNewName('') }
    catch (e) { notify({ tone: 'error', title: 'Không tạo được project', message: e.message }) }
    finally { setSaving(false) }
  }

  function onKeyDown(e) {
    if (e.key === 'Enter') confirmCreate()
    if (e.key === 'Escape') { setCreating(false); setNewName('') }
  }

  return (
    <div className="flex items-center gap-1.5 overflow-x-auto pb-1 scrollbar-none">
      <ProjectTab
        name="" label="Chung"
        active={currentProject === ''}
        onSelect={onSelect}
        onRename={() => {}}
        onDelete={() => {}}
      />
      {projects.map(p => (
        <ProjectTab
          key={p} name={p} label={p}
          active={currentProject === p}
          onSelect={onSelect}
          onRename={onRename}
          onDelete={onDelete}
        />
      ))}
      {creating ? (
        <div className="flex items-center gap-1 bg-surface-700 border border-accent-500 rounded-lg px-2 py-1.5">
          <input
            ref={inputRef}
            value={newName}
            onChange={e => setNewName(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Tên project..."
            className="w-28 bg-transparent text-xs text-white placeholder-slate-600 focus:outline-none"
          />
          <button onClick={confirmCreate} disabled={saving} className="text-slate-400 hover:text-success">
            <Check size={11} />
          </button>
          <button onClick={() => { setCreating(false); setNewName('') }} className="text-slate-400 hover:text-slate-200">
            <X size={11} />
          </button>
        </div>
      ) : (
        <button
          onClick={() => setCreating(true)}
          className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs text-slate-500 hover:text-accent-400 hover:bg-surface-700 transition-colors whitespace-nowrap"
          title="Tạo project mới"
        >
          <FolderPlus size={12} />
          Tạo mới
        </button>
      )}
    </div>
  )
}

// ── Main panel ────────────────────────────────────────────────────────────────

export default function OutputsPanel({ outputs, currentProject, onProjectChange, onRefresh }) {
  const { confirm: askConfirm, notify } = useDialog()
  const [projects, setProjects] = useState([])
  const [trash, setTrash] = useState([])
  const [trashOpen, setTrashOpen] = useState(false)
  const [restoring, setRestoring] = useState(null)
  const [purging, setPurging] = useState(null)
  const [selected, setSelected] = useState(new Set())
  const [moveTarget, setMoveTarget] = useState('')
  const [moving, setMoving] = useState(false)

  useEffect(() => { loadProjects() }, [])
  useEffect(() => { loadTrash() }, [outputs])
  useEffect(() => { setSelected(new Set()) }, [currentProject])

  useEffect(() => {
    const options = [
      ...(currentProject !== '' ? [''] : []),
      ...projects.filter(p => p !== currentProject),
    ]
    if (options.length && !options.includes(moveTarget)) setMoveTarget(options[0])
  }, [projects, currentProject])

  async function loadProjects() {
    const list = await getProjects()
    setProjects(list)
  }

  async function loadTrash() {
    const items = await getTrash()
    setTrash(items)
  }

  async function handleCreateProject(name) {
    await createProject(name)
    await loadProjects()
    onProjectChange(name)
  }

  async function handleRenameProject(oldName, newName) {
    await renameProject(oldName, newName)
    await loadProjects()
    if (currentProject === oldName) onProjectChange(newName)
  }

  async function handleDeleteProject(name) {
    const ok = await askConfirm({
      tone: 'danger',
      title: 'Xóa vĩnh viễn project?',
      message: `Project "${name}" sẽ bị xóa khỏi ổ đĩa.`,
      details: 'Toàn bộ audio, video, subtitle, timing và session liên quan sẽ bị xóa. Không thể hoàn tác.',
      confirmText: 'Xóa project',
      cancelText: 'Giữ lại',
    })
    if (!ok) return
    try {
      const result = await deleteProject(name)
      await notify({
        tone: 'success',
        title: 'Đã xóa project',
        message: `Giải phóng khoảng ${result.deleted_gb || 0}GB.`,
        details: result.deleted_sessions ? `Đã dọn ${result.deleted_sessions} session liên quan.` : '',
      })
      await loadProjects()
      await loadTrash()
      if (currentProject === name) onProjectChange('')
    } catch (e) {
      await notify({ tone: 'error', title: 'Xóa project thất bại', message: e.message })
    }
  }

  function toggleSelect(name) {
    setSelected(prev => {
      const next = new Set(prev)
      next.has(name) ? next.delete(name) : next.add(name)
      return next
    })
  }

  function toggleAll() {
    setSelected(prev =>
      prev.size === outputs.length ? new Set() : new Set(outputs.map(f => f.name))
    )
  }

  async function handleMove() {
    if (!selected.size) return
    setMoving(true)
    try {
      await moveOutputs([...selected], currentProject, moveTarget)
      setSelected(new Set())
      onRefresh()
    } catch (e) {
      notify({ tone: 'error', title: 'Không chuyển được file', message: e.message })
    } finally {
      setMoving(false)
    }
  }

  async function handleDelete(name) {
    const ok = await askConfirm({
      tone: 'warning',
      title: 'Chuyển file vào thùng rác?',
      message: `"${name}" sẽ được chuyển vào thùng rác.`,
      confirmText: 'Chuyển',
      cancelText: 'Hủy',
    })
    if (!ok) return
    await deleteOutput(name, currentProject)
    onRefresh()
    loadTrash()
  }

  async function handleRename(oldName, newName) {
    await renameOutput(oldName, newName, currentProject)
    onRefresh()
  }

  async function handleRestore(name) {
    setRestoring(name)
    try { await restoreOutput(name, currentProject); onRefresh(); loadTrash() }
    catch (e) { notify({ tone: 'error', title: 'Không khôi phục được file', message: e.message }) }
    finally { setRestoring(null) }
  }

  async function handlePermanentDelete(name) {
    const ok = await askConfirm({
      tone: 'danger',
      title: 'Xóa vĩnh viễn file?',
      message: `"${name}" sẽ bị xóa khỏi ổ đĩa.`,
      details: 'Không thể khôi phục sau thao tác này.',
      confirmText: 'Xóa vĩnh viễn',
      cancelText: 'Giữ lại',
    })
    if (!ok) return
    setPurging(name)
    try {
      await permanentDelete(name)
      loadTrash()
    } catch (e) {
      notify({ tone: 'error', title: 'Xóa file thất bại', message: e.message })
    } finally {
      setPurging(null)
    }
  }

  const showPanel = outputs.length > 0 || trash.length > 0 || projects.length > 0

  if (!showPanel) return null

  return (
    <div className="space-y-3">
      <div className="glass rounded-2xl overflow-hidden animate-fade-in">
        {/* Header */}
        <div className="px-5 py-4 border-b border-surface-600 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Music size={15} className="text-accent-400" />
            <span className="text-sm font-semibold text-white">File đã tạo</span>
            {outputs.length > 0 && (
              <span className="text-xs text-slate-500 bg-surface-600 px-2 py-0.5 rounded-full">{outputs.length}</span>
            )}
          </div>
          <button onClick={onRefresh} className="text-xs text-slate-500 hover:text-slate-300 transition-colors">
            Làm mới
          </button>
        </div>

        {/* Project tabs */}
        <div className="px-5 py-3 border-b border-surface-700">
          <ProjectBar
            projects={projects}
            currentProject={currentProject}
            onSelect={onProjectChange}
            onCreate={handleCreateProject}
            onRename={handleRenameProject}
            onDelete={handleDeleteProject}
          />
        </div>

        {/* Batch action bar */}
        {selected.size > 0 && (
          <div className="px-5 py-2.5 border-b border-accent-500/30 bg-accent-600/10 flex items-center gap-3 flex-wrap">
            <button onClick={toggleAll} className="text-xs text-accent-400 hover:text-accent-300 transition-colors">
              {selected.size === outputs.length ? 'Bỏ chọn tất cả' : 'Chọn tất cả'}
            </button>
            <span className="text-xs text-slate-400">{selected.size} file đã chọn</span>
            <span className="flex-1" />
            <span className="text-xs text-slate-400 shrink-0">Chuyển tới:</span>
            <AppSelect
              value={moveTarget}
              onChange={e => setMoveTarget(e.target.value)}
              className="bg-surface-800 border border-surface-500 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-accent-500"
            >
              {currentProject !== '' && <option value="">Chung</option>}
              {projects.filter(p => p !== currentProject).map(p => (
                <option key={p} value={p}>{p}</option>
              ))}
            </AppSelect>
            <button
              onClick={handleMove}
              disabled={moving}
              className="flex items-center gap-1 px-3 py-1 rounded-lg bg-accent-600 hover:bg-accent-500 text-white text-xs font-semibold transition-colors disabled:opacity-40"
            >
              <MoveRight size={12} />
              {moving ? 'Đang chuyển...' : 'Chuyển'}
            </button>
            <button
              onClick={() => setSelected(new Set())}
              className="p-1 text-slate-500 hover:text-slate-300 transition-colors"
            >
              <X size={13} />
            </button>
          </div>
        )}

        {/* File list */}
        {outputs.length > 0 ? (
          <div className="divide-y divide-surface-600">
            {outputs.map(file => (
              <FileRow
                key={file.name}
                file={file}
                project={currentProject}
                onDelete={handleDelete}
                onRename={handleRename}
                selected={selected.has(file.name)}
                onToggleSelect={() => toggleSelect(file.name)}
              />
            ))}
          </div>
        ) : (
          <div className="px-5 py-8 text-center text-xs text-slate-600">
            {currentProject ? `Project "${currentProject}" chưa có file nào` : 'Chưa có file nào trong Chung'}
          </div>
        )}
      </div>

      {/* Trash */}
      {trash.length > 0 && (
        <div className="glass rounded-2xl overflow-hidden">
          <button
            onClick={() => setTrashOpen(p => !p)}
            className="w-full px-5 py-3 flex items-center justify-between hover:bg-surface-700 transition-colors"
          >
            <div className="flex items-center gap-2">
              <Trash size={13} className="text-slate-500" />
              <span className="text-xs font-semibold text-slate-400">Thùng rác</span>
              <span className="text-xs text-slate-600 bg-surface-700 px-2 py-0.5 rounded-full">{trash.length}</span>
            </div>
            <span className="text-xs text-slate-600">{trashOpen ? 'Thu gọn' : 'Xem'}</span>
          </button>

          {trashOpen && (
            <div className="border-t border-surface-600 divide-y divide-surface-700">
              {trash.map(file => (
                <div key={file.name} className="px-5 py-2.5 flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <Volume2 size={12} className="text-slate-600 shrink-0" />
                    <span className="text-xs text-slate-400 truncate">{file.name}</span>
                    <span className="text-xs text-slate-600 shrink-0">{file.size_mb} MB</span>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      onClick={() => handleRestore(file.name)}
                      disabled={restoring === file.name}
                      className="flex items-center gap-1 px-2 py-1 rounded text-xs text-slate-400 hover:text-accent-400 hover:bg-surface-700 transition-colors disabled:opacity-40"
                    >
                      <RotateCcw size={11} />
                      Khôi phục
                    </button>
                    <button
                      onClick={() => handlePermanentDelete(file.name)}
                      disabled={purging === file.name}
                      className="p-1.5 text-slate-600 hover:text-danger transition-colors disabled:opacity-40"
                      title="Xoá vĩnh viễn"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
