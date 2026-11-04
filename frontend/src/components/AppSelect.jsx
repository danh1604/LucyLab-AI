import { Children, isValidElement, useEffect, useId, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown, Search } from 'lucide-react'

function flatten(children, group = '') {
  return Children.toArray(children).flatMap(child => {
    if (!isValidElement(child)) return []
    if (child.type === 'option') return [{ value: String(child.props.value ?? ''), label: String(child.props.children ?? ''), disabled: Boolean(child.props.disabled), group }]
    if (child.type === 'optgroup') return flatten(child.props.children, String(child.props.label ?? ''))
    return flatten(child.props.children, group)
  })
}

/** Accessible, portal-based replacement for browser select controls. */
export default function AppSelect({ value, onChange, children, className = '', disabled = false, searchable, style, ...rest }) {
  const id = useId()
  const triggerRef = useRef(null)
  const menuRef = useRef(null)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [position, setPosition] = useState(null)
  const options = useMemo(() => flatten(children), [children])
  const selected = options.find(option => option.value === String(value ?? ''))
  const isSearchable = searchable ?? options.length > 9
  const filtered = options.filter(option => option.label.toLocaleLowerCase().includes(query.toLocaleLowerCase()) || option.group.toLocaleLowerCase().includes(query.toLocaleLowerCase()))

  function place() {
    const rect = triggerRef.current?.getBoundingClientRect()
    if (!rect) return
    const width = Math.max(rect.width, 220)
    const left = Math.min(rect.left, window.innerWidth - width - 12)
    const below = window.innerHeight - rect.bottom
    const visibleCount = Math.max(1, query ? filtered.length : options.length)
    const searchHeight = isSearchable ? 76 : 0
    // Fit compact selects to their actual content. Long lists retain a capped,
    // scrollable surface rather than borrowing the full viewport height.
    const contentHeight = 12 + searchHeight + Math.min(visibleCount, 7) * 40
    const availableHeight = below > 260 ? below - 16 : rect.top - 16
    const menuHeight = Math.min(320, contentHeight, Math.max(84, availableHeight))
    setPosition({ left: Math.max(12, left), width, top: below > 260 ? rect.bottom + 6 : undefined, bottom: below > 260 ? undefined : window.innerHeight - rect.top + 6, menuHeight })
  }

  useEffect(() => {
    if (!open) return undefined
    place()
    const closeOutside = event => {
      if (!triggerRef.current?.contains(event.target) && !menuRef.current?.contains(event.target)) setOpen(false)
    }
    const reposition = () => place()
    document.addEventListener('mousedown', closeOutside)
    window.addEventListener('resize', reposition)
    window.addEventListener('scroll', reposition, true)
    return () => {
      document.removeEventListener('mousedown', closeOutside)
      window.removeEventListener('resize', reposition)
      window.removeEventListener('scroll', reposition, true)
    }
  }, [open, query])

  function choose(option) {
    if (option.disabled) return
    onChange?.({ target: { value: option.value, name: rest.name } })
    setOpen(false)
    setQuery('')
    requestAnimationFrame(() => triggerRef.current?.focus())
  }

  function onKeyDown(event) {
    if (disabled) return
    const current = Math.max(0, options.findIndex(option => option.value === String(value ?? '')))
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setOpen(v => !v); return }
    if (event.key === 'Escape') { setOpen(false); return }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) { setOpen(true); return }
      const delta = event.key === 'ArrowDown' ? 1 : -1
      const next = options[(current + delta + options.length) % options.length]
      choose(next)
    }
  }

  const menu = open && position && createPortal(
    <div ref={menuRef} role="listbox" aria-labelledby={id} className="fixed z-50 flex flex-col overflow-hidden rounded-xl border border-surface-500 bg-surface-900 shadow-lg shadow-black/40" style={{ left: position.left, width: position.width, top: position.top, bottom: position.bottom, height: position.menuHeight }}>
      {isSearchable && <div className="border-b border-surface-700 p-2">
        <label className="sr-only" htmlFor={`${id}-search`}>Tìm lựa chọn</label>
        <div className="flex items-center gap-2 rounded-lg bg-black/20 px-2.5 py-2 text-slate-300 focus-within:ring-1 focus-within:ring-accent-400">
          <Search size={14} aria-hidden="true" />
          <input id={`${id}-search`} autoFocus value={query} onChange={event => setQuery(event.target.value)} placeholder="Tìm lựa chọn..." className="min-w-0 flex-1 bg-transparent text-sm text-white outline-none placeholder:text-slate-500" />
        </div>{options.length > 9 && <p className="mt-1.5 px-1 text-[11px] text-slate-500">{filtered.length} lựa chọn · cuộn để xem thêm</p>}
      </div>}
      <div className="app-select-options min-h-0 flex-1 overflow-y-auto p-1.5">
        {filtered.length ? filtered.map((option, index) => <div key={`${option.group}-${option.value}-${index}`}>
          {option.group && (index === 0 || option.group !== filtered[index - 1]?.group) && <p className="px-2.5 pb-1 pt-2 text-[11px] font-semibold text-slate-500">{option.group}</p>}
          <button type="button" role="option" aria-selected={option.value === String(value ?? '')} disabled={option.disabled} onClick={() => choose(option)} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm text-slate-200 transition-colors hover:bg-accent-500/15 hover:text-white disabled:cursor-not-allowed disabled:opacity-40">
            <span className="min-w-0 flex-1 truncate">{option.label}</span>{option.value === String(value ?? '') && <Check size={15} className="shrink-0 text-accent-300" />}
          </button>
        </div>) : <p className="px-2.5 py-4 text-center text-sm text-slate-500">Không tìm thấy lựa chọn</p>}
      </div>
    </div>, document.body,
  )

  return <>
    <button ref={triggerRef} id={id} type="button" role="combobox" aria-expanded={open} aria-haspopup="listbox" disabled={disabled} onClick={() => { if (!disabled) setOpen(v => !v) }} onKeyDown={onKeyDown} className={`${className} flex items-center gap-2 text-left disabled:cursor-not-allowed disabled:opacity-50`} style={style} {...rest}>
      <span className="min-w-0 flex-1 truncate">{selected?.label || 'Chọn...'}</span><ChevronDown size={16} className={`shrink-0 text-slate-400 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
    </button>
    {menu}
  </>
}
