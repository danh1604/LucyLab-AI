import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, Info, Trash2, X, XCircle } from 'lucide-react'

const DialogContext = createContext(null)

const TONES = {
  info: {
    icon: Info,
    iconClass: 'text-accent-300 bg-accent-500/10 border-accent-400/20',
    buttonClass: 'bg-accent-600 hover:bg-accent-500 shadow-accent-500/20',
  },
  success: {
    icon: CheckCircle2,
    iconClass: 'text-emerald-300 bg-emerald-500/10 border-emerald-400/20',
    buttonClass: 'bg-emerald-600 hover:bg-emerald-500 shadow-emerald-500/20',
  },
  warning: {
    icon: AlertTriangle,
    iconClass: 'text-amber-300 bg-amber-500/10 border-amber-400/20',
    buttonClass: 'bg-amber-600 hover:bg-amber-500 shadow-amber-500/20',
  },
  danger: {
    icon: Trash2,
    iconClass: 'text-red-300 bg-red-500/10 border-red-400/20',
    buttonClass: 'bg-red-600 hover:bg-red-500 shadow-red-500/20',
  },
  error: {
    icon: XCircle,
    iconClass: 'text-red-300 bg-red-500/10 border-red-400/20',
    buttonClass: 'bg-red-600 hover:bg-red-500 shadow-red-500/20',
  },
}

function normalizeLines(value) {
  if (!value) return []
  return String(value).split('\n').filter(Boolean)
}

export function DialogProvider({ children }) {
  const [dialog, setDialog] = useState(null)
  const resolverRef = useRef(null)
  const primaryRef = useRef(null)

  const close = useCallback((value) => {
    const resolver = resolverRef.current
    resolverRef.current = null
    setDialog(null)
    resolver?.(value)
  }, [])

  const open = useCallback((options) => new Promise((resolve) => {
    resolverRef.current = resolve
    setDialog({
      type: options.type || 'alert',
      tone: options.tone || 'info',
      title: options.title || 'Thông báo',
      message: options.message || '',
      details: options.details || '',
      confirmText: options.confirmText || 'Đồng ý',
      cancelText: options.cancelText || 'Hủy',
    })
  }), [])

  const confirm = useCallback((options) => open({ ...options, type: 'confirm' }), [open])
  const notify = useCallback((options) => open({ ...options, type: 'alert' }), [open])

  useEffect(() => {
    if (!dialog) return
    const timer = setTimeout(() => primaryRef.current?.focus(), 0)
    function onKeyDown(e) {
      if (e.key === 'Escape') close(dialog.type === 'confirm' ? false : true)
      if (e.key === 'Enter' && e.ctrlKey) close(true)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => {
      clearTimeout(timer)
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [dialog, close])

  const value = useMemo(() => ({ confirm, notify }), [confirm, notify])
  const tone = TONES[dialog?.tone] || TONES.info
  const Icon = tone.icon

  return (
    <DialogContext.Provider value={value}>
      {children}
      {dialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center px-4 py-6">
          <button
            type="button"
            aria-label="Đóng dialog"
            className="absolute inset-0 bg-black/70 backdrop-blur-sm animate-fade-in"
            onClick={() => close(dialog.type === 'confirm' ? false : true)}
          />
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="app-dialog-title"
            className="relative w-full max-w-md overflow-hidden rounded-2xl border border-surface-500 bg-surface-900 shadow-2xl shadow-black/60 animate-slide-up"
          >
            <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/20 to-transparent" />
            <div className="p-5">
              <div className="flex items-start gap-4">
                <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border ${tone.iconClass}`}>
                  <Icon size={20} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-start gap-3">
                    <h2 id="app-dialog-title" className="text-base font-bold leading-6 text-white">
                      {dialog.title}
                    </h2>
                    <button
                      type="button"
                      onClick={() => close(dialog.type === 'confirm' ? false : true)}
                      className="ml-auto rounded-lg p-1 text-slate-500 transition-colors hover:bg-surface-700 hover:text-slate-200"
                      aria-label="Đóng"
                    >
                      <X size={16} />
                    </button>
                  </div>
                  <div className="mt-2 space-y-1 text-sm leading-6 text-slate-300">
                    {normalizeLines(dialog.message).map((line, idx) => (
                      <p key={idx}>{line}</p>
                    ))}
                  </div>
                </div>
              </div>

              {dialog.details && (
                <div className="mt-4 rounded-xl border border-surface-600 bg-black/20 px-3 py-2.5 text-xs leading-5 text-slate-400">
                  {normalizeLines(dialog.details).map((line, idx) => (
                    <p key={idx}>{line}</p>
                  ))}
                </div>
              )}

              <div className="mt-5 flex justify-end gap-2">
                {dialog.type === 'confirm' && (
                  <button
                    type="button"
                    onClick={() => close(false)}
                    className="rounded-xl border border-surface-500 bg-surface-800 px-4 py-2 text-sm font-semibold text-slate-300 transition-colors hover:bg-surface-700 hover:text-white"
                  >
                    {dialog.cancelText}
                  </button>
                )}
                <button
                  ref={primaryRef}
                  type="button"
                  onClick={() => close(true)}
                  className={`rounded-xl px-4 py-2 text-sm font-bold text-white shadow-lg transition-all active:translate-y-px ${tone.buttonClass}`}
                >
                  {dialog.confirmText}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </DialogContext.Provider>
  )
}

export function useDialog() {
  const ctx = useContext(DialogContext)
  if (!ctx) throw new Error('useDialog must be used inside DialogProvider')
  return ctx
}
