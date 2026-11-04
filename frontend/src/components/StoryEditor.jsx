import AppSelect from './AppSelect.jsx'
import { useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  CheckCircle,
  CheckCircle2,
  Clipboard,
  Eye,
  EyeOff,
  FileText,
  Loader2,
  MessageSquare,
  RotateCcw,
  Sparkles,
  Wand2,
  X,
} from 'lucide-react'
import {
  applyScriptReviewPlan,
  auditAppliedScriptReview,
  buildScriptReviewApplicationPlan,
  completeScriptReviewRewrites,
  getScriptReviewAutoRun,
  reconcileScriptReview,
  saveScriptReview,
  startScriptReviewAutoRun,
  startScriptReview,
  stopScriptReviewAutoRun,
} from '../api.js'
import { useDialog } from './DialogProvider.jsx'

const AGENT_LABELS = {
  spoken_naturalness: 'Văn nói',
  logic_continuity: 'Logic',
  tension_pacing: 'Kịch tính',
  retention_hooks: 'Giữ chân',
  editor_in_chief: 'Tổng biên tập',
  post_rewrite_audit: 'Đối soát',
}

const SEVERITY_LABELS = {
  critical: 'Rất quan trọng',
  important: 'Quan trọng',
  minor: 'Nhỏ',
}

const SEVERITY_CLASSES = {
  critical: 'border-red-500/40 bg-red-500/10 text-red-200',
  important: 'border-amber-500/40 bg-amber-500/10 text-amber-200',
  minor: 'border-slate-500/40 bg-slate-500/10 text-slate-300',
}

function statusLabel(status) {
  if (status === 'applied') return 'Đã áp dụng'
  if (status === 'resolved') return 'Đã xử lý'
  if (status === 'ignored') return 'Bỏ qua'
  if (status === 'superseded') return 'Đã gộp'
  if (status === 'stale') return 'Lệch vị trí'
  return 'Đang mở'
}

function agentStatusLabel(status) {
  if (status === 'running') return 'Đang chạy'
  if (status === 'done') return 'Xong'
  if (status === 'failed') return 'Lỗi'
  return 'Chờ'
}

function sortedComments(comments) {
  return [...(comments || [])].sort((a, b) => {
    const pa = a.priority ? 0 : 1
    const pb = b.priority ? 0 : 1
    if (pa !== pb) return pa - pb
    const sa = a.start ?? 10 ** 9
    const sb = b.start ?? 10 ** 9
    return sa - sb
  })
}

function visibleComments(comments, severityFilter, categoryFilter) {
  return sortedComments(comments).filter(comment => {
    if (comment.status && comment.status !== 'open') return false
    if (severityFilter !== 'all' && comment.severity !== severityFilter) return false
    if (categoryFilter !== 'all' && comment.agent !== categoryFilter) return false
    return true
  })
}

function highlightSegments(text, comments, activeId, setActiveId) {
  const active = sortedComments(comments)
    .filter(c => (!c.status || c.status === 'open') && c.start >= 0 && c.end > c.start)
    .sort((a, b) => a.start - b.start)
  const parts = []
  let cursor = 0
  for (const comment of active) {
    if (comment.start < cursor || comment.start >= text.length) continue
    if (comment.start > cursor) parts.push(text.slice(cursor, comment.start))
    const end = Math.min(text.length, comment.end)
    const cls = comment.severity === 'critical'
      ? 'bg-red-500/25 border-red-400/40'
      : comment.severity === 'important'
        ? 'bg-amber-500/25 border-amber-400/40'
        : 'bg-accent-500/20 border-accent-400/30'
    parts.push(
      <mark
        key={comment.id}
        id={`review-highlight-${comment.id}`}
        onClick={() => setActiveId(comment.id)}
        className={`cursor-pointer rounded border px-0.5 text-slate-50 ${cls} ${activeId === comment.id ? 'ring-1 ring-white/70' : ''}`}
      >
        {text.slice(comment.start, end)}
      </mark>,
    )
    cursor = end
  }
  if (cursor < text.length) parts.push(text.slice(cursor))
  return parts
}

async function textHash(value) {
  if (!globalThis.crypto?.subtle) return ''
  const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('').slice(0, 16)
}

export default function StoryEditor({
  initialText,
  storyTitle = '',
  storyPrompt = '',
  model = '',
  sessionId = '',
  scriptReview = null,
  onApprove,
  onRefilterStickFigure,
  onRewrite,
  onChange,
  onReviewChange,
  onAutoRunUpdate,
}) {
  const { confirm: askConfirm, notify } = useDialog()
  const [text, setText] = useState(initialText || '')
  const [review, setReview] = useState(scriptReview || null)
  const [running, setRunning] = useState(false)
  const [reviewError, setReviewError] = useState('')
  const [agentStates, setAgentStates] = useState([])
  const [activeCommentId, setActiveCommentId] = useState('')
  const [severityFilter, setSeverityFilter] = useState('all')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [showHighlights, setShowHighlights] = useState(true)
  const [reconciling, setReconciling] = useState(false)
  const [backfillingRewrites, setBackfillingRewrites] = useState(false)
  const [planningApplications, setPlanningApplications] = useState(false)
  const [auditingRewrite, setAuditingRewrite] = useState(false)
  const [startingAutoRun, setStartingAutoRun] = useState(false)
  const [stoppingAutoRun, setStoppingAutoRun] = useState(false)
  const [refilteringStickFigure, setRefilteringStickFigure] = useState(false)
  const autoRun = review?.auto_review_run || null
  const autoRunActive = autoRun?.status === 'running' || autoRun?.status === 'stopping'

  useEffect(() => setText(initialText || ''), [initialText])
  useEffect(() => setReview(scriptReview || null), [scriptReview])

  function syncAutoRunResponse(data) {
    if (!data) return
    if (typeof data.story_text === 'string') setText(data.story_text)
    if (data.review) setReview(data.review)
    if (typeof data.story_text === 'string' && data.review) onAutoRunUpdate?.(data.story_text, data.review)
  }

  useEffect(() => {
    if (!sessionId || !autoRunActive) return undefined
    let cancelled = false
    async function poll() {
      try {
        const data = await getScriptReviewAutoRun(sessionId)
        if (!cancelled) syncAutoRunResponse(data)
      } catch {
        // The existing status remains visible; the next poll can recover from a transient error.
      }
    }
    poll()
    const timer = setInterval(poll, 1800)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [sessionId, autoRunActive])

  const words = text.trim() ? text.trim().split(/\s+/).length : 0
  const chars = text.length
  const comments = review?.comments || []
  const filteredComments = visibleComments(comments, severityFilter, categoryFilter)
  const openCount = comments.filter(c => c.status === 'open' || !c.status).length
  const staleCount = comments.filter(c => c.status === 'stale').length
  const historyComments = comments.filter(c => ['applied', 'superseded', 'resolved', 'ignored'].includes(c.status))
  const missingRewriteCount = comments.filter(c => (
    (c.status === 'open' || c.status === 'stale' || !c.status)
    && c.quote
    && !String(c.rewrite || '').trim()
  )).length
  const applicableRewriteCount = comments.filter(c => (
    (c.status === 'open' || c.status === 'stale' || !c.status)
    && c.quote
    && String(c.rewrite || '').trim()
  )).length
  const reviewStale = Number.isFinite(Number(review?.source_length)) && Number(review.source_length) !== text.length

  const categories = useMemo(() => {
    const ids = new Set(comments.map(c => c.agent).filter(Boolean))
    return [...ids]
  }, [comments])

  useEffect(() => {
    if (!activeCommentId || !showHighlights) return
    const frame = requestAnimationFrame(() => {
      document.getElementById(`review-highlight-${activeCommentId}`)?.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      })
    })
    return () => cancelAnimationFrame(frame)
  }, [activeCommentId, showHighlights, comments])

  function updateText(nextText) {
    setText(nextText)
    onChange?.(nextText)
  }

  async function handleRefilterStickFigure() {
    if (!onRefilterStickFigure || refilteringStickFigure) return
    const approved = await askConfirm({
      tone: 'warning',
      title: 'Lọc lại từ kịch bản thô?',
      message: 'AI sẽ bỏ bản narration/blueprint người que hiện tại và lọc lại từ đúng kịch bản thô ban đầu. Audio, video và phụ đề đang gắn trong session sẽ được bỏ khỏi pipeline này; file đã xuất trên ổ đĩa không bị xóa.',
    })
    if (!approved) return
    setRefilteringStickFigure(true)
    try {
      await onRefilterStickFigure()
      await notify({ tone: 'success', title: 'Đã lọc lại content người que', message: 'Bạn đang xem narration và blueprint mới. Hãy duyệt lại trước khi tạo audio.' })
    } catch (error) {
      await notify({ tone: 'danger', title: 'Không thể lọc lại content', message: error?.message || 'Vui lòng thử lại.' })
    } finally {
      setRefilteringStickFigure(false)
    }
  }

  function persistReview(nextReview) {
    setReview(nextReview)
    onReviewChange?.(nextReview)
    if (sessionId) saveScriptReview(sessionId, nextReview).catch(() => {})
  }

  function patchComment(commentId, patch) {
    if (!review) return
    const next = {
      ...review,
      updated_at: new Date().toISOString(),
      comments: (review.comments || []).map(c => c.id === commentId ? { ...c, ...patch } : c),
    }
    persistReview(next)
  }

  function focusComment(commentId) {
    setActiveCommentId(commentId)
    if (!showHighlights) setShowHighlights(true)
  }

  function handleStartReview() {
    if (!text.trim()) {
      setReviewError('Chưa có kịch bản để review.')
      return
    }
    setReviewError('')
    setRunning(true)
    setAgentStates([
      'spoken_naturalness',
      'logic_continuity',
      'tension_pacing',
      'retention_hooks',
      'editor_in_chief',
    ].map(id => ({ id, label: AGENT_LABELS[id], status: 'queued' })))
    startScriptReview(
      {
        session_id: sessionId,
        story_text: text,
        story_prompt: storyPrompt,
        story_title: storyTitle,
        model,
      },
      (ev) => {
        if (ev.type === 'start') {
          setReview(ev.review)
          if (ev.review?.reviewers) setAgentStates(ev.review.reviewers)
        }
        if (ev.type === 'agent_start' || ev.type === 'editor_start') {
          const id = ev.agent?.id
          setAgentStates(prev => prev.map(a => a.id === id ? { ...a, status: 'running' } : a))
        }
        if (ev.type === 'agent_comment') {
          setReview(prev => {
            if (!prev) return prev
            const exists = (prev.comments || []).some(c => c.id === ev.comment?.id)
            if (exists || !ev.comment) return prev
            return { ...prev, comments: [...(prev.comments || []), ev.comment] }
          })
        }
        if (ev.type === 'agent_done') {
          const result = ev.result || {}
          setAgentStates(prev => prev.map(a => (
            a.id === result.agent_id ? { ...a, status: result.status || 'done', error: result.error || '' } : a
          )))
        }
        if (ev.type === 'summary') {
          setReview(prev => prev ? { ...prev, summary: ev.summary || {}, comments: ev.comments || prev.comments || [] } : prev)
        }
        if (ev.type === 'done') {
          persistReview(ev.review)
          setAgentStates(prev => prev.map(a => ({ ...a, status: a.status === 'failed' ? 'failed' : 'done' })))
          notify({ tone: 'success', title: 'Đã review kịch bản', message: `${ev.review?.comments?.length || 0} comment cần xem.` })
        }
        if (ev.type === 'error') {
          setReviewError(ev.message || 'Review kịch bản thất bại')
        }
      },
      () => setRunning(false),
      (e) => {
        setRunning(false)
        setReviewError(e?.message || String(e))
      },
    )
  }

  async function handleReconcile() {
    if (!review) return
    setReconciling(true)
    try {
      const data = await reconcileScriptReview(text, review)
      persistReview(data.review)
      notify({ tone: 'success', title: 'Đã map lại comment' })
    } catch (e) {
      notify({ tone: 'error', title: 'Không map lại được', message: e?.message || String(e) })
    } finally {
      setReconciling(false)
    }
  }

  async function handleCompleteRewrites() {
    if (!review || !missingRewriteCount) return
    setBackfillingRewrites(true)
    try {
      const data = await completeScriptReviewRewrites({
        story_text: text,
        story_prompt: storyPrompt,
        story_title: storyTitle,
        model,
        review,
      })
      persistReview(data.review)
      if (data.remaining) {
        notify({
          tone: 'warning',
          title: 'Còn rewrite chưa tạo được',
          message: `Đã tạo ${data.generated || 0}; còn ${data.remaining} comment cần thử lại.`,
        })
      } else {
        notify({ tone: 'success', title: 'Đã tạo rewrite', message: `${data.generated || 0} comment đã có thể áp dụng.` })
      }
    } catch (e) {
      notify({ tone: 'error', title: 'Không tạo được rewrite', message: e?.message || String(e) })
    } finally {
      setBackfillingRewrites(false)
    }
  }

  async function handleStartAutoRun() {
    if (!sessionId || !review || !text.trim()) {
      notify({ tone: 'warning', title: 'Chưa thể chạy tự động', message: 'Hãy lưu session và review kịch bản trước.' })
      return
    }
    const approved = await askConfirm({
      tone: 'warning',
      title: 'Tự động áp dụng và đối soát?',
      message: 'Workflow sẽ lặp tối đa 5 vòng, chỉ áp dụng rewrite đã qua Tổng biên tập. Bạn vẫn cần tự duyệt trước khi tạo audio.',
      confirmText: 'Bắt đầu tự động',
    })
    if (!approved) return
    setStartingAutoRun(true)
    try {
      const data = await startScriptReviewAutoRun(sessionId, text, review)
      syncAutoRunResponse(data)
      notify({ tone: 'success', title: 'Đã bắt đầu workflow tự động', message: 'Bạn có thể rời trang; tiến trình được lưu theo session.' })
    } catch (e) {
      notify({ tone: 'error', title: 'Không bắt đầu được workflow', message: e?.message || String(e) })
    } finally {
      setStartingAutoRun(false)
    }
  }

  async function handleStopAutoRun() {
    if (!sessionId || !autoRunActive) return
    setStoppingAutoRun(true)
    try {
      const data = await stopScriptReviewAutoRun(sessionId)
      syncAutoRunResponse(data)
      notify({ tone: 'warning', title: 'Đang dừng workflow', message: 'Bước hiện tại sẽ hoàn tất rồi workflow mới dừng.' })
    } catch (e) {
      notify({ tone: 'error', title: 'Không yêu cầu dừng được', message: e?.message || String(e) })
    } finally {
      setStoppingAutoRun(false)
    }
  }

  async function applyRewrite(comment) {
    if (!comment?.rewrite?.trim()) return
    if (comment.start < 0 || comment.end <= comment.start) {
      patchComment(comment.id, { status: 'stale' })
      return
    }
    const current = text.slice(comment.start, comment.end)
    if (current !== comment.quote) {
      patchComment(comment.id, { status: 'stale' })
      notify({ tone: 'warning', title: 'Comment bị lệch', message: 'Đoạn gốc đã thay đổi, không tự áp dụng để tránh sửa nhầm.' })
      return
    }
    const nextText = text.slice(0, comment.start) + comment.rewrite.trim() + text.slice(comment.end)
    updateText(nextText)
    const nextReview = {
      ...review,
      updated_at: new Date().toISOString(),
      application_plan: review?.application_plan ? { ...review.application_plan, status: 'stale' } : undefined,
      comments: comments.map(item => item.id === comment.id ? { ...item, status: 'applied' } : item),
    }
    try {
      const data = await reconcileScriptReview(nextText, nextReview)
      persistReview(data.review)
    } catch {
      persistReview(nextReview)
    }
  }

  async function getApplicationPlan(activeReview) {
    const currentHash = await textHash(text)
    const plan = activeReview?.application_plan
    if (plan?.status === 'ready' && plan.source_hash && plan.source_hash === currentHash) return activeReview
    setPlanningApplications(true)
    try {
      const data = await buildScriptReviewApplicationPlan({
        story_text: text,
        story_prompt: storyPrompt,
        story_title: storyTitle,
        model,
        review: activeReview,
      })
      persistReview(data.review)
      if (data.review?.application_plan?.status === 'waiting_rewrites') {
        throw new Error('Hãy tạo rewrite còn thiếu trước khi áp dụng tất cả.')
      }
      if (data.review?.application_plan?.status !== 'ready') {
        throw new Error(data.review?.application_plan?.summary || 'Tổng biên tập chưa lập được kế hoạch áp dụng.')
      }
      return data.review
    } finally {
      setPlanningApplications(false)
    }
  }

  async function runPostRewriteAudit(activeReview, storyText, appliedIds) {
    setAuditingRewrite(true)
    try {
      const data = await auditAppliedScriptReview({
        story_text: storyText,
        story_prompt: storyPrompt,
        story_title: storyTitle,
        model,
        review: activeReview,
        applied_comment_ids: appliedIds,
      })
      persistReview(data.review)
      const count = data.audit?.comment_count || 0
      notify({
        tone: count ? 'warning' : 'success',
        title: count ? 'Đối soát còn điểm cần xem' : 'Đối soát đạt',
        message: count ? `Đã thêm ${count} comment đối soát mới.` : 'Không phát hiện vấn đề quan trọng sau khi ghép rewrite.',
      })
    } catch (e) {
      notify({ tone: 'warning', title: 'Chưa đối soát được', message: e?.message || String(e) })
    } finally {
      setAuditingRewrite(false)
    }
  }

  async function handlePostRewriteAudit() {
    if (!review || !text.trim()) return
    await runPostRewriteAudit(review, text, comments.filter(item => item.status === 'applied').map(item => item.id))
  }

  async function applyAllRewrites() {
    if (!review || !applicableRewriteCount) return
    let plannedReview
    try {
      plannedReview = await getApplicationPlan(review)
    } catch (e) {
      notify({ tone: 'warning', title: 'Chưa thể áp dụng tất cả', message: e?.message || String(e) })
      return
    }
    const plan = plannedReview.application_plan || {}
    const plannedCount = (plan.entries || []).length
    if (!plannedCount) {
      notify({ tone: 'warning', title: 'Không có rewrite an toàn để áp dụng', message: plan.summary || 'Tổng biên tập đã loại các rewrite xung đột.' })
      return
    }
    const approved = await askConfirm({
      tone: 'warning',
      title: 'Áp dụng tất cả rewrite?',
      message: `${plannedCount} rewrite đã qua Tổng biên tập sẽ được áp dụng. Các rewrite xung đột đã bị loại khỏi kế hoạch.`,
      confirmText: 'Áp dụng tất cả',
    })
    if (!approved) return

    let data
    try {
      data = await applyScriptReviewPlan({
        story_text: text,
        story_prompt: storyPrompt,
        story_title: storyTitle,
        model,
        review: plannedReview,
      })
    } catch (e) {
      notify({ tone: 'error', title: 'Không áp dụng được rewrite', message: e?.message || String(e) })
      return
    }
    const nextText = data.story_text
    const nextReview = data.review
    const appliedIds = data.result?.applied_ids || []
    const staleIds = data.result?.stale_ids || []
    if (appliedIds.length) updateText(nextText)
    persistReview(nextReview)
    notify({
      tone: appliedIds.length ? 'success' : 'warning',
      title: appliedIds.length ? 'Đã áp dụng rewrite' : 'Không có rewrite nào được áp dụng',
      message: `${appliedIds.length} đã áp dụng${staleIds.length ? `, ${staleIds.length} bị lệch` : ''}.`,
    })
    if (appliedIds.length) await runPostRewriteAudit(nextReview, nextText, appliedIds)
  }

  async function copyRewrite(comment) {
    try {
      await navigator.clipboard.writeText(comment.rewrite || comment.suggestion || '')
      notify({ tone: 'success', title: 'Đã copy đề xuất' })
    } catch {
      notify({ tone: 'warning', title: 'Không copy được', message: 'Bạn có thể chọn text và copy thủ công.' })
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <FileText size={16} className="text-accent-400" />
          <h2 className="text-lg font-bold text-white">Chỉnh sửa & Duyệt truyện</h2>
        </div>
        <div className="flex items-center gap-3 text-xs text-slate-500 font-mono">
          <span>{words.toLocaleString()} từ</span>
          <span>{chars.toLocaleString()} ký tự</span>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-surface-600 bg-surface-900/60 px-3 py-2">
        <p className="text-xs text-slate-500">Chỉnh sửa trực tiếp, review kịch bản nếu cần, rồi nhấn Duyệt.</p>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setShowHighlights(v => !v)}
            disabled={!comments.length}
            className="inline-flex items-center gap-1.5 rounded-lg border border-surface-500 px-3 py-2 text-xs font-semibold text-slate-300 hover:text-white hover:border-accent-400 disabled:opacity-40"
          >
            {showHighlights ? <EyeOff size={13} /> : <Eye size={13} />}
            Highlight
          </button>
          <button
            type="button"
            onClick={handleReconcile}
            disabled={!review || reconciling || autoRunActive}
            className="inline-flex items-center gap-1.5 rounded-lg border border-surface-500 px-3 py-2 text-xs font-semibold text-slate-300 hover:text-white hover:border-accent-400 disabled:opacity-40"
          >
            {reconciling ? <Loader2 size={13} className="animate-spin" /> : <RotateCcw size={13} />}
            Map lại
          </button>
          {missingRewriteCount > 0 && (
            <button
              type="button"
              onClick={handleCompleteRewrites}
              disabled={backfillingRewrites || running || autoRunActive}
              className="inline-flex items-center gap-1.5 rounded-lg border border-green-500/40 px-3 py-2 text-xs font-semibold text-green-200 hover:border-green-400 hover:text-green-100 disabled:opacity-40"
            >
              {backfillingRewrites ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
              Tạo rewrite còn thiếu ({missingRewriteCount})
            </button>
          )}
          {applicableRewriteCount > 0 && (
            <button
              type="button"
              onClick={applyAllRewrites}
              disabled={running || backfillingRewrites || planningApplications || auditingRewrite || autoRunActive}
              className="inline-flex items-center gap-1.5 rounded-lg border border-accent-400/50 px-3 py-2 text-xs font-semibold text-accent-100 hover:border-accent-300 hover:text-white disabled:opacity-40"
            >
              {planningApplications ? <Loader2 size={13} className="animate-spin" /> : <Wand2 size={13} />}
              Áp dụng tất cả ({applicableRewriteCount})
            </button>
          )}
          {review && (
            <button
              type="button"
              onClick={handlePostRewriteAudit}
              disabled={running || planningApplications || auditingRewrite || autoRunActive || !text.trim()}
              className="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/40 px-3 py-2 text-xs font-semibold text-amber-100 hover:border-amber-400 hover:text-white disabled:opacity-40"
            >
              {auditingRewrite ? <Loader2 size={13} className="animate-spin" /> : <CheckCircle2 size={13} />}
              Đối soát bản đã sửa
            </button>
          )}
          {review && !autoRunActive && (
            <button
              type="button"
              onClick={handleStartAutoRun}
              disabled={!sessionId || startingAutoRun || running || !text.trim()}
              className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-bold text-white hover:bg-emerald-500 disabled:opacity-40"
            >
              {startingAutoRun ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
              Áp dụng tất cả + Đối soát tự động
            </button>
          )}
          {autoRunActive && (
            <button
              type="button"
              onClick={handleStopAutoRun}
              disabled={stoppingAutoRun || autoRun?.status === 'stopping'}
              className="inline-flex items-center gap-1.5 rounded-lg border border-red-500/50 px-3 py-2 text-xs font-semibold text-red-200 hover:border-red-400 hover:text-red-100 disabled:opacity-40"
            >
              {stoppingAutoRun || autoRun?.status === 'stopping' ? <Loader2 size={13} className="animate-spin" /> : <X size={13} />}
              {autoRun?.status === 'stopping' ? 'Đang dừng' : 'Dừng sau bước hiện tại'}
            </button>
          )}
          <button
            type="button"
            onClick={handleStartReview}
            disabled={running || autoRunActive || !text.trim()}
            className="inline-flex items-center gap-1.5 rounded-lg bg-accent-600 hover:bg-accent-500 disabled:opacity-40 px-3 py-2 text-xs font-bold text-white"
          >
            {running ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
            Review kịch bản
          </button>
        </div>
      </div>

      {autoRun && (
        <div className={`rounded-xl border px-3 py-3 text-sm ${autoRunActive ? 'border-emerald-500/35 bg-emerald-500/10 text-emerald-100' : autoRun.status === 'passed' ? 'border-green-500/35 bg-green-500/10 text-green-100' : 'border-amber-500/35 bg-amber-500/10 text-amber-100'}`}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="font-semibold">{autoRun.status === 'passed' ? 'Đối soát đạt' : autoRun.status === 'stopped' ? 'Workflow đã dừng' : autoRun.status === 'blocked' || autoRun.status === 'max_iterations' || autoRun.status === 'interrupted' ? 'Workflow cần xem lại' : `Tự động hóa: ${autoRun.current_step || 'đang chuẩn bị'}`}</p>
            <span className="font-mono text-xs">Vòng {autoRun.iteration || 0}/{autoRun.max_iterations || 5}</span>
          </div>
          <p className="mt-1 text-xs opacity-85">{autoRun.blocked_reason || autoRun.events?.at(-1)?.message || 'Đang chờ bước tiếp theo.'}</p>
        </div>
      )}

      {reviewError && (
        <div className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          <AlertCircle size={15} className="mt-0.5 shrink-0" />
          <span>{reviewError}</span>
        </div>
      )}

      {running && (
        <div className="grid grid-cols-1 sm:grid-cols-5 gap-2">
          {agentStates.map(agent => (
            <div key={agent.id} className="rounded-xl border border-surface-600 bg-black/20 px-3 py-2">
              <p className="text-xs font-semibold text-white truncate">{agent.label || AGENT_LABELS[agent.id] || agent.id}</p>
              <p className={`text-[11px] ${agent.status === 'failed' ? 'text-red-300' : agent.status === 'done' ? 'text-green-300' : agent.status === 'running' ? 'text-accent-300' : 'text-slate-500'}`}>
                {agent.status === 'running' && <Loader2 size={11} className="inline mr-1 animate-spin" />}
                {agentStatusLabel(agent.status)}
              </p>
            </div>
          ))}
        </div>
      )}

      {review?.summary && Object.keys(review.summary).length > 0 && (
        <div className="rounded-xl border border-accent-500/20 bg-accent-500/10 px-3 py-2 text-sm text-slate-200">
          <div className="flex items-center gap-2 text-xs font-bold text-accent-200 mb-1">
            <Wand2 size={13} />
            Tổng biên tập
          </div>
          {review.summary.overall_score ? (
            <p className="text-xs text-slate-400 mb-1">Điểm tổng thể: {review.summary.overall_score}/10</p>
          ) : null}
          {[...(review.summary.main_problems || []), ...(review.summary.fix_strategy || [])].slice(0, 5).map((line, idx) => (
            <p key={idx} className="text-xs leading-5">• {line}</p>
          ))}
        </div>
      )}

      {(reviewStale || staleCount > 0) && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
          Review có thể đã lệch vì kịch bản đã được sửa. Bấm “Map lại” để tìm lại vị trí comment.
        </div>
      )}

      <div className="grid grid-cols-1 2xl:grid-cols-[minmax(0,1fr)_25rem] gap-4">
        <div className="space-y-3 min-w-0">
          {showHighlights && comments.length > 0 && (
            <div className="rounded-xl border border-surface-600 bg-surface-900/80 p-4 min-h-[24rem] max-h-[42rem] overflow-y-auto">
              <p className="text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2">Bản xem highlight</p>
              <div className="whitespace-pre-wrap text-sm leading-7 text-slate-200">
                {highlightSegments(text, comments, activeCommentId, setActiveCommentId)}
              </div>
            </div>
          )}

          <textarea
            value={text}
            onChange={e => updateText(e.target.value)}
            rows={22}
            disabled={autoRunActive}
            className="w-full bg-surface-900 border border-surface-500 rounded-xl px-4 py-3 text-sm text-slate-200 leading-relaxed resize-none focus:outline-none focus:border-accent-500 transition-colors disabled:cursor-wait disabled:opacity-70"
            placeholder="Paste hoặc chỉnh sửa nội dung truyện ở đây..."
            spellCheck={false}
          />
        </div>

        <aside className="rounded-xl border border-surface-600 bg-surface-900/70 overflow-hidden">
          <div className="border-b border-surface-600 p-3 space-y-3">
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <MessageSquare size={15} className="text-accent-300" />
                <p className="text-sm font-bold text-white">Review comments</p>
              </div>
              <span className="rounded-full bg-accent-500/15 px-2 py-0.5 text-xs font-semibold text-accent-200">{openCount}</span>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <AppSelect value={severityFilter} onChange={e => setSeverityFilter(e.target.value)} style={{ colorScheme: 'dark' }} className="bg-surface-900 border border-surface-600 rounded-lg px-2 py-1.5 text-xs text-slate-100">
                <option className="bg-surface-900 text-slate-100" value="all">Mọi mức</option>
                <option className="bg-surface-900 text-slate-100" value="critical">Rất quan trọng</option>
                <option className="bg-surface-900 text-slate-100" value="important">Quan trọng</option>
                <option className="bg-surface-900 text-slate-100" value="minor">Nhỏ</option>
              </AppSelect>
              <AppSelect value={categoryFilter} onChange={e => setCategoryFilter(e.target.value)} style={{ colorScheme: 'dark' }} className="bg-surface-900 border border-surface-600 rounded-lg px-2 py-1.5 text-xs text-slate-100">
                <option className="bg-surface-900 text-slate-100" value="all">Mọi agent</option>
                {categories.map(id => <option className="bg-surface-900 text-slate-100" key={id} value={id}>{AGENT_LABELS[id] || id}</option>)}
              </AppSelect>
            </div>
          </div>

          <div className="max-h-[46rem] overflow-y-auto divide-y divide-surface-700">
            {filteredComments.length === 0 ? (
              <div className="p-4 text-sm text-slate-500">
                Chưa có comment review. Bấm “Review kịch bản” để đội reviewer đánh dấu các đoạn cần xem.
              </div>
            ) : filteredComments.map(comment => (
              <article
                key={comment.id}
                onClick={() => focusComment(comment.id)}
                className={`p-3 space-y-2 cursor-pointer hover:bg-surface-800/60 ${activeCommentId === comment.id ? 'bg-surface-800/80' : ''}`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="text-xs font-bold text-white truncate">
                      {comment.priority ? '★ ' : ''}{AGENT_LABELS[comment.agent] || comment.agent}
                    </p>
                    <p className="text-[11px] text-slate-500">{statusLabel(comment.status)}</p>
                  </div>
                  <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${SEVERITY_CLASSES[comment.severity] || SEVERITY_CLASSES.important}`}>
                    {SEVERITY_LABELS[comment.severity] || comment.severity}
                  </span>
                </div>

                <blockquote className="rounded-lg border border-surface-600 bg-black/20 px-2 py-1.5 text-xs leading-5 text-slate-300">
                  “{comment.quote}”
                </blockquote>

                <div className="space-y-1 text-xs leading-5">
                  <p className="text-red-200"><span className="font-semibold">Vấn đề:</span> {comment.issue}</p>
                  {comment.why_it_matters && <p className="text-slate-300"><span className="font-semibold">Vì sao:</span> {comment.why_it_matters}</p>}
                  {comment.suggestion && <p className="text-accent-100"><span className="font-semibold">Gợi ý:</span> {comment.suggestion}</p>}
                  {comment.rewrite && (
                    <p className="rounded-lg border border-green-500/20 bg-green-500/10 px-2 py-1.5 text-green-100">
                      <span className="font-semibold">Rewrite:</span> {comment.rewrite}
                    </p>
                  )}
                </div>

                <div className="flex flex-wrap gap-1.5">
                  <button type="button" onClick={(e) => { e.stopPropagation(); applyRewrite(comment) }} disabled={autoRunActive || !comment.rewrite || comment.status === 'applied' || comment.status === 'superseded'} className="inline-flex items-center gap-1 rounded-lg border border-surface-500 px-2 py-1 text-[11px] font-semibold text-slate-300 hover:text-white hover:border-accent-400 disabled:opacity-40">
                    <Wand2 size={11} /> Áp dụng
                  </button>
                  <button type="button" onClick={(e) => { e.stopPropagation(); copyRewrite(comment) }} className="inline-flex items-center gap-1 rounded-lg border border-surface-500 px-2 py-1 text-[11px] font-semibold text-slate-300 hover:text-white hover:border-accent-400">
                    <Clipboard size={11} /> Copy
                  </button>
                  <button type="button" onClick={(e) => { e.stopPropagation(); patchComment(comment.id, { status: 'resolved' }) }} disabled={autoRunActive} className="inline-flex items-center gap-1 rounded-lg border border-surface-500 px-2 py-1 text-[11px] font-semibold text-slate-300 hover:text-white hover:border-green-400 disabled:opacity-40">
                    <CheckCircle2 size={11} /> Xử lý
                  </button>
                  <button type="button" onClick={(e) => { e.stopPropagation(); patchComment(comment.id, { status: 'ignored' }) }} disabled={autoRunActive} className="inline-flex items-center gap-1 rounded-lg border border-surface-500 px-2 py-1 text-[11px] font-semibold text-slate-300 hover:text-white hover:border-red-400 disabled:opacity-40">
                    <X size={11} /> Bỏ qua
                  </button>
                </div>
              </article>
            ))}
          </div>
          {staleCount > 0 && (
            <details className="border-t border-amber-500/20 bg-amber-500/5">
              <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-amber-200">Không map được ({staleCount})</summary>
              <div className="max-h-48 overflow-y-auto space-y-2 px-3 pb-3 text-xs text-amber-100/80">
                {comments.filter(comment => comment.status === 'stale').map(comment => (
                  <p key={comment.id} className="rounded border border-amber-500/20 bg-black/10 px-2 py-1.5">“{comment.quote}”</p>
                ))}
              </div>
            </details>
          )}
          {historyComments.length > 0 && (
            <details className="border-t border-surface-600 bg-black/10">
              <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-slate-400">Lịch sử đã xử lý ({historyComments.length})</summary>
              <div className="max-h-56 overflow-y-auto space-y-2 px-3 pb-3 text-xs text-slate-400">
                {historyComments.map(comment => (
                  <div key={comment.id} className="rounded border border-surface-600 px-2 py-1.5">
                    <span className="font-semibold text-slate-300">{statusLabel(comment.status)}:</span> “{comment.quote}”
                  </div>
                ))}
              </div>
            </details>
          )}
        </aside>
      </div>

      <div className="flex gap-3">
        {onRefilterStickFigure && (
          <button
            type="button"
            onClick={handleRefilterStickFigure}
            disabled={autoRunActive || refilteringStickFigure}
            className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium border border-sky-400/40 bg-sky-500/10 text-sky-200 hover:bg-sky-500/20 hover:text-white transition-all disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {refilteringStickFigure ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
            {refilteringStickFigure ? 'Đang lọc lại từ kịch bản thô...' : 'Lọc lại từ kịch bản thô'}
          </button>
        )}
        {onRewrite && (
          <button
            onClick={onRewrite}
            className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium border border-surface-500 text-slate-400 hover:text-white hover:border-slate-400 transition-all"
          >
            <RotateCcw size={14} />
            Viết lại
          </button>
        )}
        <button
          onClick={() => onApprove(text.trim())}
          disabled={!text.trim() || autoRunActive}
          className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-semibold bg-accent-600 hover:bg-accent-500 text-white disabled:opacity-40 disabled:cursor-not-allowed transition-all"
        >
          <CheckCircle size={16} />
          Duyệt truyện & Tiếp tục tạo Audio
        </button>
      </div>
    </div>
  )
}
