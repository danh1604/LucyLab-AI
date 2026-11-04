import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  Power,
  PowerOff,
  RefreshCw,
  ShieldCheck,
  TestTube2,
  Trash2,
  Upload,
} from 'lucide-react'
import {
  deleteGptAccount as apiDeleteGptAccount,
  disableGptAccount as apiDisableGptAccount,
  enableGptAccount as apiEnableGptAccount,
  importGptAccounts as apiImportGptAccounts,
  importLocalCodexAccount as apiImportLocalCodexAccount,
  listGptAccounts as apiListGptAccounts,
  pollCodexLogin,
  probeGptAccount as apiProbeGptAccount,
  startCodexLogin,
  updateConfig,
} from '../api.js'
import { useDialog } from './DialogProvider.jsx'

export default function GptAccountsPanel({ cfg, setCfg }) {
  const { confirm: askConfirm, notify } = useDialog()
  const [accounts, setAccounts] = useState([])
  const [pool, setPool] = useState(null)
  const [activeAccountId, setActiveAccountId] = useState(null)
  const [importText, setImportText] = useState('')
  const [accountBusy, setAccountBusy] = useState(null)
  const [importing, setImporting] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [configOpen, setConfigOpen] = useState(false)
  const [codexLoginSession, setCodexLoginSession] = useState(null)
  const pollerRef = useRef(null)

  const accountPool = pool || summarizeClientAccountPool(accounts)
  const activeAccount = useMemo(
    () => accounts.find(account => account.id === activeAccountId),
    [accounts, activeAccountId],
  )

  useEffect(() => {
    refreshGptAccounts()
    return () => {
      if (pollerRef.current) clearInterval(pollerRef.current)
    }
  }, [])

  function applyAccountsResult(result) {
    if (Array.isArray(result.accounts)) setAccounts(result.accounts)
    if (result.pool) setPool(result.pool)
    setActiveAccountId(result.activeAccountId ?? null)
  }

  async function saveAccountConfig() {
    await updateConfig({
      gpt2api_admin_base_url: cfg.gpt2api_admin_base_url || 'http://127.0.0.1:17188',
      gpt2api_admin_user: cfg.gpt2api_admin_user || 'admin',
      gpt2api_admin_password: cfg.gpt2api_admin_password || 'admin123',
      gpt2api_codex_auth_path: cfg.gpt2api_codex_auth_path || '',
      gpt2api_codex_client_id: cfg.gpt2api_codex_client_id || 'app_EMoamEEZ73f0CkXaXp7hrann',
      gpt2api_account_name_prefix: cfg.gpt2api_account_name_prefix || 'lucylab-codex',
    })
  }

  async function refreshGptAccounts() {
    setLoading(true)
    setError('')
    try {
      await saveAccountConfig()
      applyAccountsResult(await apiListGptAccounts())
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setLoading(false)
    }
  }

  async function handleImportAccounts() {
    setImporting(true)
    setError('')
    setNotice('')
    try {
      await saveAccountConfig()
      const result = await apiImportGptAccounts(importText)
      applyAccountsResult(result)
      setImportText('')
      setNotice(`Đã import ${result.imported ?? 0} account${result.selectedAccountId ? `, account #${result.selectedAccountId} đã được bật.` : '.'}`)
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setImporting(false)
    }
  }

  async function handleImportLocalCodex() {
    setImporting(true)
    setError('')
    setNotice('')
    try {
      await saveAccountConfig()
      const result = await apiImportLocalCodexAccount()
      applyAccountsResult(result)
      setNotice(result.selectedAccountId ? `Đã import Codex OAuth local: #${result.selectedAccountId}.` : 'Đã import Codex OAuth local.')
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setImporting(false)
    }
  }

  async function handleStartCodexLogin() {
    setImporting(true)
    setError('')
    setNotice('')
    const loginWindow = window.open('about:blank', '_blank')
    try {
      if (loginWindow) loginWindow.document.write('<p style="font-family:Arial,sans-serif;padding:24px">Starting Codex OAuth...</p>')
      await saveAccountConfig()
      const session = await startCodexLogin()
      setCodexLoginSession(session)
      if (session.loginUrl && loginWindow) loginWindow.location.href = session.loginUrl
      if (session.sessionId) beginCodexLoginPolling(session.sessionId)
      setNotice(session.message || 'Hoàn tất đăng nhập OpenAI trong cửa sổ vừa mở.')
    } catch (err) {
      if (loginWindow) loginWindow.close()
      setError(messageOf(err))
      setImporting(false)
    }
  }

  function beginCodexLoginPolling(sessionId) {
    if (pollerRef.current) clearInterval(pollerRef.current)
    pollerRef.current = setInterval(async () => {
      try {
        const session = await pollCodexLogin(sessionId)
        setCodexLoginSession(session)
        if (session.accounts) applyAccountsResult(session)
        if (['imported', 'failed', 'timeout'].includes(session.status)) {
          clearInterval(pollerRef.current)
          pollerRef.current = null
          setImporting(false)
          if (session.status === 'imported') {
            setNotice(session.message || 'Đã import Codex account.')
          } else {
            setError(session.message || 'Codex login chưa hoàn tất.')
          }
        }
      } catch (err) {
        clearInterval(pollerRef.current)
        pollerRef.current = null
        setImporting(false)
        setError(messageOf(err))
      }
    }, 1500)
  }

  async function accountAction(id, action, label) {
    setAccountBusy(id)
    setError('')
    setNotice('')
    try {
      const result = await action(id)
      applyAccountsResult(result)
      setNotice(label)
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setAccountBusy(null)
    }
  }

  async function handleDeleteAccount(account) {
    const ok = await askConfirm({
      tone: 'danger',
      title: `Xóa GPT account #${account.id}?`,
      message: account.name || 'Account này sẽ bị xóa khỏi gpt2api.',
      details: 'Thao tác này xóa account trong pool gpt2api. File truyện/video hiện có không bị ảnh hưởng.',
      confirmText: 'Xóa account',
      cancelText: 'Giữ lại',
    })
    if (!ok) return
    setAccountBusy(account.id)
    setError('')
    try {
      const result = await apiDeleteGptAccount(account.id)
      applyAccountsResult(result)
      await notify({ tone: 'success', title: 'Đã xóa GPT account', message: `Account #${account.id} đã được xóa.` })
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setAccountBusy(null)
    }
  }

  async function handleDeleteUnusableAccounts() {
    const targets = accounts.filter(isUnusableForDelete)
    if (!targets.length) {
      await notify({ tone: 'info', title: 'Không có account lỗi', message: 'Danh sách hiện tại không có account invalid/disabled cần dọn.' })
      return
    }
    const ok = await askConfirm({
      tone: 'danger',
      title: `Xóa ${targets.length} GPT account lỗi?`,
      message: 'Các account token invalid hoặc đã tắt sẽ bị xóa khỏi gpt2api.',
      details: 'Account đang cooldown không bị xóa để tránh mất quota có thể reset sau.',
      confirmText: 'Dọn account lỗi',
      cancelText: 'Giữ lại',
    })
    if (!ok) return
    setAccountBusy(-1)
    setError('')
    try {
      let result = null
      for (const account of targets) {
        result = await apiDeleteGptAccount(account.id)
      }
      if (result) applyAccountsResult(result)
      await notify({ tone: 'success', title: 'Đã dọn account lỗi', message: `Đã xóa ${targets.length} account khỏi gpt2api.` })
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setAccountBusy(null)
    }
  }

  return (
    <div className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <KeyRound size={16} className="text-accent-300" />
            <p className="text-sm font-semibold text-white">GPT accounts trong gpt2api</p>
          </div>
          <p className="mt-1 text-xs text-slate-500">
            Import account giống ComicForge: sub2api JSON, OAuth JSON, ChatGPT session JSON, dòng name@@refresh_token, hoặc Codex OAuth local.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleDeleteUnusableAccounts}
            disabled={loading || importing || accountBusy !== null || !accounts.length}
            className="inline-flex items-center gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs font-semibold text-red-300 hover:bg-red-500/20 disabled:opacity-50"
          >
            {accountBusy === -1 ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
            Dọn lỗi
          </button>
          <button
            onClick={refreshGptAccounts}
            disabled={loading || importing || accountBusy !== null}
            className="inline-flex items-center gap-2 rounded-lg border border-surface-500 bg-surface-800 px-3 py-2 text-xs font-semibold text-slate-200 hover:border-accent-500 hover:text-white disabled:opacity-50"
          >
            {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
            Làm mới
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <PoolBadge label="Enabled" value={accountPool.enabledCount} tone="success" />
        <PoolBadge label="Usable" value={accountPool.usableCount} tone="accent" />
        <PoolBadge label="1K web" value={accountPool.webReadyCount} tone="muted" />
        <PoolBadge label="2K Codex" value={accountPool.codexReadyCount} tone="muted" />
      </div>

      <div className="rounded-xl border border-surface-600 bg-surface-950/60 p-3">
        <button
          onClick={() => setConfigOpen(open => !open)}
          className="flex w-full items-center justify-between text-left"
        >
          <span className="text-xs font-semibold uppercase tracking-wider text-slate-300">Cấu hình admin đầy đủ</span>
          <span className="text-xs text-accent-300">{configOpen ? 'Ẩn' : 'Mở'}</span>
        </button>
        {configOpen && (
          <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <ConfigInput label="Admin base URL" value={cfg.gpt2api_admin_base_url || 'http://127.0.0.1:17188'} onChange={value => setCfg(p => ({ ...p, gpt2api_admin_base_url: value }))} />
            <ConfigInput label="Admin user" value={cfg.gpt2api_admin_user || 'admin'} onChange={value => setCfg(p => ({ ...p, gpt2api_admin_user: value }))} />
            <div className="space-y-1">
              <label className="text-xs font-medium uppercase tracking-wider text-slate-400">Admin password</label>
              <div className="relative">
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={cfg.gpt2api_admin_password || 'admin123'}
                  onChange={e => setCfg(p => ({ ...p, gpt2api_admin_password: e.target.value }))}
                  className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 pr-9 font-mono text-sm text-white focus:border-accent-500 focus:outline-none"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(v => !v)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-200"
                >
                  {showPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            </div>
            <ConfigInput label="Codex auth path" value={cfg.gpt2api_codex_auth_path || ''} onChange={value => setCfg(p => ({ ...p, gpt2api_codex_auth_path: value }))} placeholder="C:\\Users\\Admin\\.codex\\auth.json" />
            <ConfigInput label="Codex client ID" value={cfg.gpt2api_codex_client_id || 'app_EMoamEEZ73f0CkXaXp7hrann'} onChange={value => setCfg(p => ({ ...p, gpt2api_codex_client_id: value }))} />
            <ConfigInput label="Account prefix" value={cfg.gpt2api_account_name_prefix || 'lucylab-codex'} onChange={value => setCfg(p => ({ ...p, gpt2api_account_name_prefix: value }))} />
            <div className="sm:col-span-2">
              <button
                onClick={async () => {
                  setError('')
                  try {
                    await saveAccountConfig()
                    setNotice('Đã lưu cấu hình GPT account.')
                  } catch (err) {
                    setError(messageOf(err))
                  }
                }}
                className="rounded-lg bg-surface-700 px-3 py-2 text-xs font-semibold text-white hover:bg-surface-600"
              >
                Lưu cấu hình account
              </button>
            </div>
          </div>
        )}
      </div>

      {activeAccount ? (
        <div className="rounded-xl border border-accent-500/35 bg-accent-950/20 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <ShieldCheck size={14} className="text-accent-300" />
            <span className="font-mono text-xs text-accent-200">pool lead #{activeAccount.id}</span>
            <StatusPill account={activeAccount} />
            {activeAccount.plan_type ? <SmallPill>{activeAccount.plan_type}</SmallPill> : null}
          </div>
          <p className="mt-1 truncate text-sm font-semibold text-white">{activeAccount.name}</p>
          <p className="mt-1 font-mono text-xs text-slate-400">quota {quotaLabel(activeAccount)} {resetLabel(activeAccount)}</p>
        </div>
      ) : null}

      <div className="space-y-2 rounded-xl border border-surface-600 bg-surface-950/50 p-3">
        <textarea
          value={importText}
          onChange={e => setImportText(e.target.value)}
          spellCheck={false}
          placeholder="Paste sub2api JSON, OAuth JSON, ChatGPT session JSON, hoặc name@@refresh_token mỗi dòng..."
          className="min-h-28 w-full resize-y rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 font-mono text-xs leading-5 text-white placeholder-slate-600 focus:border-accent-500 focus:outline-none"
        />
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          <ActionButton onClick={handleImportAccounts} disabled={importing || accountBusy !== null || !importText.trim()} icon={importing ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}>
            Import paste
          </ActionButton>
          <ActionButton onClick={handleImportLocalCodex} disabled={importing || accountBusy !== null} icon={importing ? <Loader2 size={15} className="animate-spin" /> : <KeyRound size={15} />}>
            Import Codex local
          </ActionButton>
          <ActionButton onClick={handleStartCodexLogin} disabled={importing || accountBusy !== null} icon={importing ? <Loader2 size={15} className="animate-spin" /> : <ExternalLink size={15} />}>
            Add Codex account
          </ActionButton>
        </div>
        {codexLoginSession ? (
          <div className="rounded-lg border border-surface-600 bg-surface-900 px-3 py-2 text-xs text-slate-300">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-accent-300">{codexLoginSession.status}</span>
              {codexLoginSession.accountId ? <SmallPill>imported #{codexLoginSession.accountId}</SmallPill> : null}
            </div>
            {codexLoginSession.message ? <p className="mt-1 break-words text-slate-400">{codexLoginSession.message}</p> : null}
            {codexLoginSession.loginUrl ? (
              <a className="mt-2 inline-flex items-center gap-1 text-accent-300 hover:text-accent-200" href={codexLoginSession.loginUrl} target="_blank" rel="noreferrer">
                Open Codex OAuth <ExternalLink size={12} />
              </a>
            ) : null}
          </div>
        ) : null}
      </div>

      {notice ? (
        <div className="flex items-start gap-2 rounded-lg border border-success/30 bg-success/10 px-3 py-2 text-sm text-success">
          <CheckCircle2 size={16} className="mt-0.5 shrink-0" />
          <span>{notice}</span>
        </div>
      ) : null}
      {error ? (
        <div className="flex items-start gap-2 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <span className="break-words">{friendlyErrorMessage(error)}</span>
        </div>
      ) : null}

      <div className="max-h-96 space-y-2 overflow-y-auto pr-1">
        {accounts.length === 0 ? (
          <div className="rounded-xl border border-dashed border-surface-500 px-3 py-5 text-center text-sm text-slate-500">
            Chưa có GPT account nào được đọc từ gpt2api.
          </div>
        ) : (
          accounts.map(account => {
            const busy = accountBusy === account.id
            return (
              <div key={account.id} className={`rounded-xl border p-3 ${account.status === 1 ? 'border-accent-500/40 bg-accent-950/10' : 'border-surface-600 bg-surface-950/40'}`}>
                <div className="flex min-w-0 items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs text-slate-500">#{account.id}</span>
                      <StatusPill account={account} />
                      {isLikelyCodexOAuthAccount(account) ? <SmallPill>Codex 2K</SmallPill> : <SmallPill>Web 1K</SmallPill>}
                      {account.plan_type ? <SmallPill>{account.plan_type}</SmallPill> : null}
                    </div>
                    <p className="mt-1 truncate text-sm font-semibold text-white">{account.name}</p>
                    <div className="mt-2 grid grid-cols-1 gap-1 font-mono text-[11px] text-slate-400 sm:grid-cols-2">
                      <InfoLine label="quota" value={`${quotaLabel(account)} ${resetLabel(account)}`} />
                      <InfoLine label="auth" value={`${account.provider || '-'} / ${account.auth_type || '-'} / status ${account.status}`} />
                      <InfoLine label="model" value={account.default_model || '-'} />
                      <InfoLine label="token" value={`AT ${account.has_access_token ? 'yes' : 'no'} / RT ${account.has_refresh_token ? 'yes' : 'no'}`} />
                      <InfoLine label="test" value={`${testStatusLabel(account.last_test_status)} ${account.last_test_latency_ms ? `${account.last_test_latency_ms}ms` : ''}`} />
                      <InfoLine label="usage" value={`ok ${account.success_count ?? 0} / err ${account.error_count ?? 0}`} />
                      <InfoLine label="limits" value={`rpm ${account.rpm_limit ?? 0} / tpm ${account.tpm_limit ?? 0}`} />
                      <InfoLine label="updated" value={formatDateTime(account.updated_at)} />
                    </div>
                    {account.remark ? <p className="mt-2 line-clamp-2 break-words text-xs text-slate-500">{account.remark}</p> : null}
                    {(account.last_error || account.last_test_error) ? (
                      <p className="mt-2 line-clamp-3 break-words text-xs leading-5 text-red-300">
                        {friendlyErrorMessage(account.last_error || account.last_test_error)}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex shrink-0 flex-col gap-1.5">
                    <IconButton title="Probe account" disabled={accountBusy !== null || importing} onClick={() => accountAction(account.id, apiProbeGptAccount, `Đã probe account #${account.id}.`)}>
                      {busy ? <Loader2 size={14} className="animate-spin" /> : <TestTube2 size={14} />}
                    </IconButton>
                    {account.status === 1 ? (
                      <IconButton title="Tắt account" disabled={accountBusy !== null || importing} onClick={() => accountAction(account.id, apiDisableGptAccount, `Đã tắt account #${account.id}.`)}>
                        <PowerOff size={14} />
                      </IconButton>
                    ) : (
                      <IconButton title="Bật account" disabled={accountBusy !== null || importing || !canUseGptAccount(account)} onClick={() => accountAction(account.id, apiEnableGptAccount, `Đã bật account #${account.id}.`)}>
                        <Power size={14} />
                      </IconButton>
                    )}
                    <IconButton title="Xóa account" danger disabled={accountBusy !== null || importing} onClick={() => handleDeleteAccount(account)}>
                      <Trash2 size={14} />
                    </IconButton>
                  </div>
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}

function ConfigInput({ label, value, onChange, placeholder = '' }) {
  return (
    <div className="space-y-1">
      <label className="text-xs font-medium uppercase tracking-wider text-slate-400">{label}</label>
      <input
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-lg border border-surface-500 bg-surface-900 px-3 py-2 font-mono text-sm text-white placeholder-slate-600 focus:border-accent-500 focus:outline-none"
      />
    </div>
  )
}

function ActionButton({ children, icon, disabled, onClick }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center justify-center gap-2 rounded-lg bg-accent-600 px-3 py-2 text-sm font-semibold text-white transition-colors hover:bg-accent-500 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {icon}
      {children}
    </button>
  )
}

function IconButton({ children, title, disabled, danger = false, onClick }) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={`grid h-8 w-8 place-items-center rounded-lg border transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${
        danger
          ? 'border-red-500/30 bg-red-500/10 text-red-300 hover:bg-red-500/20'
          : 'border-surface-500 bg-surface-800 text-slate-300 hover:border-accent-500 hover:text-white'
      }`}
    >
      {children}
    </button>
  )
}

function PoolBadge({ label, value, tone }) {
  const toneClass = tone === 'success'
    ? 'border-success/35 bg-success/10 text-success'
    : tone === 'accent'
      ? 'border-accent-500/35 bg-accent-950/25 text-accent-200'
      : 'border-surface-500 bg-surface-800 text-slate-300'
  return (
    <div className={`rounded-lg border px-3 py-2 ${toneClass}`}>
      <p className="text-[11px] uppercase tracking-wider opacity-75">{label}</p>
      <p className="mt-1 font-mono text-lg font-semibold">{value ?? 0}</p>
    </div>
  )
}

function SmallPill({ children }) {
  return <span className="rounded-full border border-surface-500 bg-surface-800 px-2 py-0.5 text-[11px] font-medium text-slate-300">{children}</span>
}

function StatusPill({ account }) {
  const status = accountStatusLabel(account)
  const cls = status === 'enabled'
    ? 'border-success/35 bg-success/15 text-success'
    : status === 'cooldown' || status === 'web blocked'
      ? 'border-yellow-500/35 bg-yellow-500/10 text-yellow-300'
      : status === 'token invalid' || status === 'banned'
        ? 'border-red-500/35 bg-red-500/10 text-red-300'
        : 'border-surface-500 bg-surface-800 text-slate-300'
  return <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${cls}`}>{status}</span>
}

function InfoLine({ label, value }) {
  return (
    <span className="min-w-0 truncate">
      <span className="text-slate-600">{label}</span> {value || '-'}
    </span>
  )
}

function summarizeClientAccountPool(accounts) {
  const enabled = accounts.filter(account => account.status === 1)
  const usable = enabled.filter(canUseGptAccount)
  return {
    enabledCount: enabled.length,
    usableCount: usable.length,
    codexReadyCount: usable.filter(account => canUseGptAccount(account) && isLikelyCodexOAuthAccount(account)).length,
    webReadyCount: usable.filter(account => canUseGptAccount(account) && !isLikelyCodexOAuthAccount(account) && !isWebBlockedAccount(account)).length,
    cooldownCount: accounts.filter(isCooldownActive).length,
    invalidCount: accounts.filter(isDeadGptAccount).length,
    webBlockedCount: accounts.filter(isWebBlockedAccount).length,
  }
}

function accountStatusLabel(account) {
  if (isCooldownActive(account)) return 'cooldown'
  if (isWebBlockedAccount(account)) return 'web blocked'
  if (isDeadGptAccount(account)) return 'token invalid'
  if (account.status === 1) return 'enabled'
  if (account.status === 2) return 'broken'
  if (account.status === -1) return 'banned'
  return 'disabled'
}

function isLikelyCodexOAuthAccount(account) {
  const marker = `${account.name || ''} ${account.remark || ''} ${account.default_model || ''}`.toLowerCase()
  return marker.includes('codex') && (!account.auth_type || account.auth_type === 'oauth')
}

function canUseGptAccount(account) {
  return !isDeadGptAccount(account) && !isCooldownActive(account)
}

function isUnusableForDelete(account) {
  return isDeadGptAccount(account) || (account.status === 0 && !isCooldownActive(account))
}

function isCooldownActive(account) {
  return typeof account.cooldown_until === 'number' && account.cooldown_until * 1000 > Date.now()
}

function isDeadGptAccount(account) {
  const combined = `${account.last_error || ''}\n${account.last_test_error || ''}`.toLowerCase()
  return [
    'refresh token has already been used',
    'refresh_token_invalidated',
    'token_invalidated',
    'authentication token has been invalidated',
    'token has been invalidated',
    'token_revoked',
    'please try signing in again',
  ].some(needle => combined.includes(needle))
}

function isWebBlockedAccount(account) {
  const combined = `${account.last_error || ''}\n${account.last_test_error || ''}`.toLowerCase()
  return combined.includes('gpt image2 web bootstrap 403') || combined.includes('gpt image2 web requirements 403')
}

function quotaLabel(account) {
  const remaining = account.image_quota_remaining
  const total = account.image_quota_total
  if (typeof remaining === 'number' && typeof total === 'number' && total > 0) return `${remaining}/${total}`
  if (typeof remaining === 'number') return String(remaining)
  return 'unknown'
}

function resetLabel(account) {
  const resetAt = account.cooldown_until || account.image_quota_reset_at
  return resetAt ? `reset ${formatDateTime(resetAt)}` : ''
}

function testStatusLabel(value) {
  if (value === 1) return 'ok'
  if (value === 2) return 'failed'
  if (value === -1) return 'failed'
  return 'untested'
}

function formatDateTime(epochSeconds) {
  if (!epochSeconds) return '-'
  try {
    return new Intl.DateTimeFormat('vi-VN', {
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    }).format(new Date(epochSeconds * 1000))
  } catch {
    return String(epochSeconds)
  }
}

function friendlyErrorMessage(value) {
  const message = String(value || '')
  const lower = message.toLowerCase()
  if (lower.includes('暂无可用账号')) return 'gpt2api báo không có account khả dụng. Hãy import/bật account, chờ hết cooldown, hoặc dùng Codex OAuth cho route 2K.'
  if (lower.includes('refresh token has already been used') || lower.includes('token_invalidated')) return 'Refresh token đã hỏng hoặc đã bị dùng lại. Cần import lại account OAuth mới.'
  if (lower.includes('web bootstrap 403') || lower.includes('web requirements 403')) return 'Route web 1K bị ChatGPT chặn HTTP 403. Hãy re-import web session hoặc dùng Codex OAuth với 2K.'
  return message
}

function messageOf(error) {
  return error instanceof Error ? error.message : String(error || 'Unknown error')
}
