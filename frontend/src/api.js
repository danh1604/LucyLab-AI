const API = ''

export async function getConfig() {
  const r = await fetch(`${API}/api/config`)
  return r.json()
}

export async function selectFolder(title = 'Chọn thư mục lưu', initialDir = '') {
  const r = await fetch(`${API}/api/system/select-folder`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, initial_dir: initialDir }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Không mở được hộp thoại chọn thư mục' }))
    throw new Error(err.detail || 'Không mở được hộp thoại chọn thư mục')
  }
  return r.json()
}

export async function updateConfig(data) {
  const r = await fetch(`${API}/api/config`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return r.json()
}

export async function getLanguages() {
  const r = await fetch(`${API}/api/languages`)
  if (!r.ok) throw new Error('Không tải được danh sách ngôn ngữ')
  return r.json()
}

export async function getTtsProviders() {
  const r = await fetch(`${API}/api/tts/providers`)
  if (!r.ok) throw new Error('Không tải được danh sách TTS provider')
  return r.json()
}

export async function getTtsProviderVoices(providerId, locale) {
  const r = await fetch(`${API}/api/tts/providers/${encodeURIComponent(providerId)}/voices?locale=${encodeURIComponent(locale)}`)
  if (!r.ok) {
    const error = await r.json().catch(() => ({}))
    throw new Error(error.detail || 'Không tải được danh sách giọng')
  }
  return r.json()
}

export async function getTtsProfile(locale) {
  const r = await fetch(`${API}/api/tts/profile?locale=${encodeURIComponent(locale)}`)
  if (!r.ok) throw new Error('Không tải được cấu hình giọng')
  return r.json()
}

export async function testTtsProvider(providerId, locale, profile) {
  const r = await fetch(`${API}/api/tts/providers/${encodeURIComponent(providerId)}/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ locale, profile }),
  })
  if (!r.ok) {
    const error = await r.json().catch(() => ({}))
    throw new Error(error.detail || 'Không thể tạo audio nghe thử')
  }
  return r.blob()
}

export function localizeStory(payload, onEvent) {
  const controller = new AbortController()
  fetch(`${API}/api/language/localize`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), signal: controller.signal,
  }).then(async response => {
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || 'Language Agent thất bại')
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''
    while (true) {
      const { done, value } = await reader.read(); if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n'); buffer = lines.pop()
      for (const line of lines) if (line.startsWith('data: ') && line.slice(6) !== '[DONE]') { try { onEvent?.(JSON.parse(line.slice(6))) } catch {} }
    }
  }).catch(error => onEvent?.({ type: 'error', error: error.message }))
  return () => controller.abort()
}

// ── Story sessions ──────────────────────────────────────────────────────────

export async function listStorySessions() {
  const r = await fetch(`${API}/api/story-sessions`)
  if (!r.ok) throw new Error('Load sessions failed')
  return r.json()
}

export async function createStorySession(data) {
  const r = await fetch(`${API}/api/story-sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!r.ok) throw new Error('Create session failed')
  return r.json()
}

export async function getStorySession(id) {
  const r = await fetch(`${API}/api/story-sessions/${encodeURIComponent(id)}`)
  if (!r.ok) throw new Error('Load session failed')
  return r.json()
}

export async function updateStorySession(id, data) {
  const r = await fetch(`${API}/api/story-sessions/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!r.ok) throw new Error('Save session failed')
  return r.json()
}

export async function deleteStorySession(id) {
  const r = await fetch(`${API}/api/story-sessions/${encodeURIComponent(id)}`, { method: 'DELETE' })
  if (!r.ok) throw new Error('Delete session failed')
  return r.json()
}

export async function deleteStorySessionProject(id) {
  const r = await fetch(`${API}/api/story-sessions/${encodeURIComponent(id)}/project`, { method: 'DELETE' })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Delete project failed' }))
    throw new Error(err.detail || 'Delete project failed')
  }
  return r.json()
}

export async function previewSplit(text) {
  const r = await fetch(`${API}/api/preview-split`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  return r.json()
}

export async function uploadFile(file) {
  const fd = new FormData()
  fd.append('file', file)
  const r = await fetch(`${API}/api/upload`, { method: 'POST', body: fd })
  if (!r.ok) throw new Error(await r.text())
  return r.json()
}

export function startGenerate(text, outputName, onEvent, onDone, onError, project = '', locale = '') {
  const controller = new AbortController()
  fetch(`${API}/api/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, output_name: outputName, project, locale }),
    signal: controller.signal,
  }).then(r => {
    if (!r.ok) return r.json().then(e => { throw new Error(e.detail || 'Generate failed') })
    const reader = r.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    function pump() {
      reader.read().then(({ done, value }) => {
        if (done) { onDone(); return }
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (data === '[DONE]') { onDone(); return }
          try { onEvent(JSON.parse(data)) } catch {}
        }
        pump()
      }).catch((err) => {
        if (err?.name !== 'AbortError') onError(err?.message || 'Connection lost')
      })
    }
    pump()
  }).catch((err) => {
    if (err?.name !== 'AbortError') onError(err?.message || 'Connection lost')
  })

  return () => controller.abort()
}

// ── Projects ──────────────────────────────────────────────────────────────────

export async function getProjects() {
  const r = await fetch(`${API}/api/projects`)
  return r.json()
}

export async function createProject(name) {
  const r = await fetch(`${API}/api/projects`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(err.detail || 'Create failed')
  }
  return r.json()
}

export async function renameProject(name, newName) {
  const r = await fetch(`${API}/api/projects/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ new_name: newName }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(err.detail || 'Rename failed')
  }
  return r.json()
}

export async function deleteProject(name) {
  const r = await fetch(`${API}/api/projects/${encodeURIComponent(name)}`, { method: 'DELETE' })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(err.detail || 'Delete failed')
  }
  return r.json()
}

export async function getProjectSize(name) {
  const r = await fetch(`${API}/api/projects/${encodeURIComponent(name)}/size`)
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Size failed' }))
    throw new Error(err.detail || 'Size failed')
  }
  return r.json()
}

// ── Outputs ───────────────────────────────────────────────────────────────────

export async function getOutputs(project = '') {
  const params = project ? `?project=${encodeURIComponent(project)}` : ''
  const r = await fetch(`${API}/api/outputs${params}`)
  return r.json()
}

export async function renameOutput(filename, newName, project = '') {
  const params = project ? `?project=${encodeURIComponent(project)}` : ''
  const r = await fetch(`${API}/api/outputs/${encodeURIComponent(filename)}${params}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ new_name: newName }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(err.detail || 'Rename failed')
  }
  return r.json()
}

export async function moveOutputs(files, fromProject, toProject) {
  const r = await fetch(`${API}/api/outputs/move`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ files, from_project: fromProject, to_project: toProject }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(err.detail || 'Move failed')
  }
  return r.json()
}

export async function deleteOutput(filename, project = '') {
  const params = project ? `?project=${encodeURIComponent(project)}` : ''
  await fetch(`${API}/api/outputs/${encodeURIComponent(filename)}${params}`, { method: 'DELETE' })
}

export function audioUrl(filename, project = '', version = '') {
  const params = new URLSearchParams()
  if (project) params.set('project', project)
  if (version) params.set('v', String(version))
  const query = params.toString()
  return `${API}/api/outputs/${encodeURIComponent(filename)}${query ? `?${query}` : ''}`
}

// ── Trash ─────────────────────────────────────────────────────────────────────

export async function getTrash() {
  const r = await fetch(`${API}/api/trash`)
  return r.json()
}

export async function restoreOutput(filename, project = '') {
  const params = project ? `?project=${encodeURIComponent(project)}` : ''
  const r = await fetch(`${API}/api/trash/${encodeURIComponent(filename)}/restore${params}`, { method: 'POST' })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(err.detail || 'Restore failed')
  }
  return r.json()
}

export async function permanentDelete(filename) {
  await fetch(`${API}/api/trash/${encodeURIComponent(filename)}`, { method: 'DELETE' })
}

// ── Models ────────────────────────────────────────────────────────────────────

export async function getModels() {
  const r = await fetch(`${API}/api/models`)
  return r.json()
}

async function requestJson(path, options = {}, fallback = 'Request failed') {
  const r = await fetch(`${API}${path}`, options)
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data.detail || data.error || fallback)
  return data
}

export async function listGptAccounts() {
  return requestJson('/api/gpt-accounts', {}, 'Load GPT accounts failed')
}

export async function startGpt2apiRuntime() {
  return requestJson('/api/gpt2api/runtime/start', { method: 'POST' }, 'Không thể khởi động gpt2api')
}

export async function importGptAccounts(text) {
  return requestJson('/api/gpt-accounts/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  }, 'Import GPT accounts failed')
}

export async function importLocalCodexAccount() {
  return requestJson('/api/gpt-accounts/import-codex', { method: 'POST' }, 'Import local Codex account failed')
}

export async function startCodexLogin() {
  return requestJson('/api/gpt-accounts/codex-login/start', { method: 'POST' }, 'Start Codex login failed')
}

export async function pollCodexLogin(sessionId) {
  return requestJson(`/api/gpt-accounts/codex-login/${encodeURIComponent(sessionId)}`, {}, 'Load Codex login failed')
}

export async function enableGptAccount(id) {
  return requestJson(`/api/gpt-accounts/${encodeURIComponent(id)}/enable`, { method: 'POST' }, 'Enable GPT account failed')
}

export async function disableGptAccount(id) {
  return requestJson(`/api/gpt-accounts/${encodeURIComponent(id)}/disable`, { method: 'POST' }, 'Disable GPT account failed')
}

export async function selectGptAccount(id) {
  return requestJson(`/api/gpt-accounts/${encodeURIComponent(id)}/select`, { method: 'POST' }, 'Select GPT account failed')
}

export async function probeGptAccount(id) {
  return requestJson(`/api/gpt-accounts/${encodeURIComponent(id)}/probe`, { method: 'POST' }, 'Probe GPT account failed')
}

export async function deleteGptAccount(id) {
  return requestJson(`/api/gpt-accounts/${encodeURIComponent(id)}`, { method: 'DELETE' }, 'Delete GPT account failed')
}

// ── Story ─────────────────────────────────────────────────────────────────────

export async function analyzePrompt(prompt, model) {
  const r = await fetch(`${API}/api/story/analyze-prompt`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, model }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }))
    throw new Error(err.detail || 'Analyze failed')
  }
  return r.json()
}

export async function interviewTurn(history, userMessage, model) {
  const r = await fetch(`${API}/api/story/interview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ history, user_message: userMessage, model }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }))
    throw new Error(err.detail || 'Interview failed')
  }
  return r.json()
}

export function startInterviewTurn(history, userMessage, model, onEvent, onDone, onError) {
  fetch(`${API}/api/story/interview-stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ history, user_message: userMessage, model }),
  }).then(r => {
    if (!r.ok) return r.json().then(e => { throw new Error(e.detail || 'Interview failed') })
    const reader = r.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    function pump() {
      reader.read().then(({ done, value }) => {
        if (done) { onDone(); return }
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (data === '[DONE]') { onDone(); return }
          try { onEvent(JSON.parse(data)) } catch {}
        }
        pump()
      }).catch(onError)
    }
    pump()
  }).catch(onError)
}

export function startGenerateStory(prompt, interviewHistory, model, onEvent, onDone, onError) {
  let es = null
  fetch(`${API}/api/story/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, interview_history: interviewHistory, model }),
  }).then(r => {
    if (!r.ok) return r.json().then(e => { throw new Error(e.detail || 'Generate failed') })
    const reader = r.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    function pump() {
      reader.read().then(({ done, value }) => {
        if (done) { onDone(); return }
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (data === '[DONE]') { onDone(); return }
          try { onEvent(JSON.parse(data)) } catch {}
        }
        pump()
      }).catch(onError)
    }
    pump()
  }).catch(onError)
  return () => { try { es?.close() } catch {} }
}

export async function extractStickFigureScript(sourceScript, model) {
  const r = await fetch(`${API}/api/story/stick-figure/extract`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_script: sourceScript, model }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Không thể phân tích kịch bản người que' }))
    throw new Error(err.detail || 'Không thể phân tích kịch bản người que')
  }
  return r.json()
}

export function startScriptReview(payload, onEvent, onDone, onError) {
  const controller = new AbortController()
  fetch(`${API}/api/script-review/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal: controller.signal,
  }).then(r => {
    if (!r.ok) return r.json().then(e => { throw new Error(e.detail || 'Script review failed') })
    const reader = r.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    function pump() {
      reader.read().then(({ done, value }) => {
        if (done) { onDone(); return }
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (data === '[DONE]') { onDone(); return }
          try { onEvent(JSON.parse(data)) } catch {}
        }
        pump()
      }).catch((err) => {
        if (err?.name !== 'AbortError') onError(err?.message || 'Connection lost')
      })
    }
    pump()
  }).catch((err) => {
    if (err?.name !== 'AbortError') onError(err?.message || 'Connection lost')
  })
  return () => controller.abort()
}

export async function saveScriptReview(sessionId, review) {
  if (!sessionId) return null
  const r = await fetch(`${API}/api/story-sessions/${encodeURIComponent(sessionId)}/review`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ review }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Save script review failed' }))
    throw new Error(err.detail || 'Save script review failed')
  }
  return r.json()
}

export async function reconcileScriptReview(storyText, review) {
  const r = await fetch(`${API}/api/script-review/reconcile`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ story_text: storyText, review }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Reconcile script review failed' }))
    throw new Error(err.detail || 'Reconcile script review failed')
  }
  return r.json()
}

export async function completeScriptReviewRewrites(payload) {
  const r = await fetch(`${API}/api/script-review/rewrites`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Complete script rewrites failed' }))
    throw new Error(err.detail || 'Complete script rewrites failed')
  }
  return r.json()
}

export async function buildScriptReviewApplicationPlan(payload) {
  const r = await fetch(`${API}/api/script-review/application-plan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Build application plan failed' }))
    throw new Error(err.detail || 'Build application plan failed')
  }
  return r.json()
}

export async function auditAppliedScriptReview(payload) {
  const r = await fetch(`${API}/api/script-review/post-apply-audit`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Post-apply script audit failed' }))
    throw new Error(err.detail || 'Post-apply script audit failed')
  }
  return r.json()
}

export async function applyScriptReviewPlan(payload) {
  const r = await fetch(`${API}/api/script-review/apply-plan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Apply script review plan failed' }))
    throw new Error(err.detail || 'Apply script review plan failed')
  }
  return r.json()
}

export async function startScriptReviewAutoRun(sessionId, storyText, review) {
  const r = await fetch(`${API}/api/story-sessions/${encodeURIComponent(sessionId)}/script-review/auto-run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ max_iterations: 5, story_text: storyText, review }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Start automatic review failed' }))
    throw new Error(err.detail || 'Start automatic review failed')
  }
  return r.json()
}

export async function getScriptReviewAutoRun(sessionId) {
  const r = await fetch(`${API}/api/story-sessions/${encodeURIComponent(sessionId)}/script-review/auto-run`)
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Get automatic review status failed' }))
    throw new Error(err.detail || 'Get automatic review status failed')
  }
  return r.json()
}

export async function stopScriptReviewAutoRun(sessionId) {
  const r = await fetch(`${API}/api/story-sessions/${encodeURIComponent(sessionId)}/script-review/auto-run/stop`, {
    method: 'POST',
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Stop automatic review failed' }))
    throw new Error(err.detail || 'Stop automatic review failed')
  }
  return r.json()
}

// ── Video ─────────────────────────────────────────────────────────────────────

export async function uploadVideo(project, file, onProgress) {
  const fd = new FormData()
  fd.append('file', file)
  const r = await fetch(`${API}/api/video/upload/${encodeURIComponent(project)}`, {
    method: 'POST',
    body: fd,
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }))
    throw new Error(err.detail || 'Upload failed')
  }
  return r.json()
}

export async function listVideos(project) {
  const r = await fetch(`${API}/api/video/list/${encodeURIComponent(project)}`)
  return r.json()
}

export async function deleteVideo(project, filename) {
  const r = await fetch(`${API}/api/video/${encodeURIComponent(project)}/${encodeURIComponent(filename)}`, { method: 'DELETE' })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }))
    throw new Error(err.detail || 'Delete failed')
  }
  return r.json()
}

export function startComposeVideo(project, audioFile, videoOrder, settings, onEvent, onDone, onError) {
  fetch(`${API}/api/video/compose`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      project,
      audio_file: audioFile,
      video_order: videoOrder,
      output_name: settings?.outputName || 'final_video.mp4',
      video_encoder: settings?.videoEncoder || 'nvidia_nvenc',
      quality: settings?.quality || 'high',
      overwrite_mode: settings?.overwriteMode || 'version',
      video_mode: settings?.videoMode || 'background',
      loop_video: settings?.loopVideo || '',
    }),
  }).then(r => {
    if (!r.ok) return r.json().then(e => { throw new Error(e.detail || 'Compose failed') })
    const reader = r.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    let terminal = ''
    function pump() {
      reader.read().then(({ done, value }) => {
        if (done) {
          if (terminal === 'done') onDone()
          else if (terminal !== 'error') onError(new Error('Ket noi render da dong truoc khi backend tra ket qua.'))
          return
        }
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (data === '[DONE]') {
            if (terminal === 'done') onDone()
            else if (terminal !== 'error') onError(new Error('Ket noi render da dong truoc khi backend tra ket qua.'))
            return
          }
          try {
            const event = JSON.parse(data)
            if (event.type === 'done' || event.type === 'error') terminal = event.type
            onEvent(event)
          } catch {}
        }
        pump()
      }).catch(onError)
    }
    pump()
  }).catch(onError)
}

export async function listLoopAssets(project) {
  const r = await fetch(`${API}/api/loop-assets/${encodeURIComponent(project)}`)
  if (!r.ok) throw new Error('Load loop assets failed')
  return r.json()
}

export async function uploadLoopVideo(project, file) {
  const fd = new FormData()
  fd.append('file', file)
  const r = await fetch(`${API}/api/loop-assets/upload/${encodeURIComponent(project)}`, { method: 'POST', body: fd })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Upload loop video failed' }))
    throw new Error(err.detail || 'Upload loop video failed')
  }
  return r.json()
}

export async function deleteLoopAsset(project, type, filename) {
  const r = await fetch(`${API}/api/loop-assets/${encodeURIComponent(project)}/${type}/${encodeURIComponent(filename)}`, { method: 'DELETE' })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Delete loop asset failed' }))
    throw new Error(err.detail || 'Delete loop asset failed')
  }
  return r.json()
}

export function loopVideoUrl(project, filename) {
  return `${API}/api/loop-assets/video/${encodeURIComponent(project)}/${encodeURIComponent(filename)}`
}

export function loopImageUrl(project, filename) {
  return `${API}/api/loop-assets/image/${encodeURIComponent(project)}/${encodeURIComponent(filename)}`
}

export function startLoopImageGenerate(payload, onEvent, onDone, onError) {
  fetch(`${API}/api/loop-assets/generate`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }).then(r => {
    if (!r.ok) return r.json().then(e => { throw new Error(e.detail || 'Generate loop image failed') })
    const reader = r.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    function pump() {
      reader.read().then(({ done, value }) => {
        if (done) { onDone?.(); return }
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (data === '[DONE]') { onDone?.(); return }
          try { onEvent?.(JSON.parse(data)) } catch {}
        }
        pump()
      }).catch(onError)
    }
    pump()
  }).catch(onError)
}

export async function createLoopPrompt(project, filename) {
  const r = await fetch(`${API}/api/loop-assets/loop-prompt`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ project, filename }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Generate loop prompt failed' }))
    throw new Error(err.detail || 'Generate loop prompt failed')
  }
  return r.json()
}

export async function getScenePlan(project) {
  const r = await fetch(`${API}/api/scenes/${encodeURIComponent(project)}`)
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Scene plan not found' }))
    throw new Error(err.detail || 'Scene plan not found')
  }
  return r.json()
}

export async function createScenePlan(payload) {
  const r = await fetch(`${API}/api/scenes/plan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Create scene plan failed' }))
    throw new Error(err.detail || 'Create scene plan failed')
  }
  return r.json()
}

export async function createDirectorScenePlan(project, payload) {
  const r = await fetch(`${API}/api/scenes/${encodeURIComponent(project)}/director/prepare`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Create director plan failed' }))
    throw new Error(err.detail || 'Create director plan failed')
  }
  return r.json()
}

export async function createStickFigureVisualPlan(project, payload) {
  const r = await fetch(`${API}/api/scenes/${encodeURIComponent(project)}/stick-figure/visual-plan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!r.ok) {
    const raw = await r.text()
    let detail = ''
    try {
      detail = JSON.parse(raw)?.detail || ''
    } catch (_) {
      detail = raw.trim()
    }
    throw new Error(detail || `Không thể tạo plan người que (HTTP ${r.status})`)
  }
  return r.json()
}

export async function approveStickFigureVisualPlan(project, autoPrepareReferences = false) {
  const r = await fetch(`${API}/api/scenes/${encodeURIComponent(project)}/stick-figure/visual-plan/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ auto_prepare_references: autoPrepareReferences }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Cannot approve Visual Plan' }))
    throw new Error(err.detail || 'Cannot approve Visual Plan')
  }
  return r.json()
}

export async function uploadStickFigureStyleReference(project, file) {
  const form = new FormData()
  form.append('file', file)
  const r = await fetch(`${API}/api/scenes/${encodeURIComponent(project)}/stick-figure/style-reference`, {
    method: 'POST',
    body: form,
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Không thể tải style sheet' }))
    throw new Error(err.detail || 'Không thể tải style sheet')
  }
  return r.json()
}

export async function updateScene(project, sceneId, patch) {
  const r = await fetch(`${API}/api/scenes/${encodeURIComponent(project)}/${encodeURIComponent(sceneId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Update scene failed' }))
    throw new Error(err.detail || 'Update scene failed')
  }
  return r.json()
}

export function directorReferenceUrl(project, filename) {
  return `${API}/api/scenes/${encodeURIComponent(project)}/director/reference/${encodeURIComponent(filename)}`
}

export function startGenerateDirectorReferences(project, payload, onEvent, onDone, onError) {
  fetch(`${API}/api/scenes/${encodeURIComponent(project)}/director/references/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  }).then(r => {
    if (!r.ok) return r.json().then(e => { throw new Error(e.detail || 'Generate director references failed') })
    const reader = r.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    function pump() {
      reader.read().then(({ done, value }) => {
        if (done) { onDone?.(); return }
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (data === '[DONE]') { onDone?.(); return }
          try { onEvent(JSON.parse(data)) } catch {}
        }
        pump()
      }).catch(onError)
    }
    pump()
  }).catch(onError)
}

export async function selectDirectorReference(project, referenceId, imageFile) {
  const r = await fetch(`${API}/api/scenes/${encodeURIComponent(project)}/director/references/${encodeURIComponent(referenceId)}/select`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image_file: imageFile }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Select director reference failed' }))
    throw new Error(err.detail || 'Select director reference failed')
  }
  return r.json()
}

export async function regenerateDirectorScenePrompt(project, sceneId, note = '') {
  const r = await fetch(`${API}/api/scenes/${encodeURIComponent(project)}/director/scenes/${encodeURIComponent(sceneId)}/regenerate-prompt`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ note }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Regenerate director prompt failed' }))
    throw new Error(err.detail || 'Regenerate director prompt failed')
  }
  return r.json()
}

export async function applySceneSafePrompts(project, payload = {}) {
  const r = await fetch(`${API}/api/scenes/${encodeURIComponent(project)}/safe-prompts`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Apply safe prompts failed' }))
    throw new Error(err.detail || 'Apply safe prompts failed')
  }
  return r.json()
}

export async function selectSceneImage(project, sceneId, imageFile) {
  const r = await fetch(`${API}/api/scenes/${encodeURIComponent(project)}/${encodeURIComponent(sceneId)}/select-image`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ image_file: imageFile }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Select scene image failed' }))
    throw new Error(err.detail || 'Select scene image failed')
  }
  return r.json()
}

export async function cancelSceneGeneration(project) {
  const r = await fetch(`${API}/api/scenes/cancel`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project }),
  })
  return r.json()
}

export function startGenerateScenes(payload, onEvent, onDone, onError) {
  fetch(`${API}/api/scenes/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(r => {
    if (!r.ok) return r.json().then(e => { throw new Error(e.detail || 'Generate scenes failed') })
    const reader = r.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    function pump() {
      reader.read().then(({ done, value }) => {
        if (done) { onDone?.(); return }
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (data === '[DONE]') { onDone?.(); return }
          try { onEvent(JSON.parse(data)) } catch {}
        }
        pump()
      }).catch(onError)
    }
    pump()
  }).catch(onError)
}

export function startComposeSceneVideo(project, audioFile, settings, onEvent, onDone, onError) {
  fetch(`${API}/api/scenes/compose`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      project,
      audio_file: audioFile,
      output_name: settings?.outputName || 'final_video_scene.mp4',
      video_encoder: settings?.videoEncoder || 'nvidia_nvenc',
      quality: settings?.quality || 'high',
      overwrite_mode: settings?.overwriteMode || 'version',
      scene_reveal: {
        enabled: Boolean(settings?.sceneReveal?.enabled),
        effect: settings?.sceneReveal?.effect || 'brush',
        duration_sec: Number(settings?.sceneReveal?.durationSec || 1.2),
        pattern: settings?.sceneReveal?.pattern || 'auto',
        actor: settings?.sceneReveal?.actor || 'none',
      },
    }),
  }).then(r => {
    if (!r.ok) return r.json().then(e => { throw new Error(e.detail || 'Compose scene video failed') })
    const reader = r.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    let terminal = ''
    function pump() {
      reader.read().then(({ done, value }) => {
        if (done) {
          if (terminal === 'done') onDone?.()
          else if (terminal !== 'error') onError?.(new Error('Ket noi render da dong truoc khi backend tra ket qua.'))
          return
        }
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (data === '[DONE]') {
            if (terminal === 'done') onDone?.()
            else if (terminal !== 'error') onError?.(new Error('Ket noi render da dong truoc khi backend tra ket qua.'))
            return
          }
          try {
            const event = JSON.parse(data)
            if (event.type === 'done' || event.type === 'error') terminal = event.type
            onEvent(event)
          } catch {}
        }
        pump()
      }).catch(onError)
    }
    pump()
  }).catch(onError)
}

export function sceneImageUrl(project, filename) {
  return `${API}/api/scenes/${encodeURIComponent(project)}/image/${encodeURIComponent(filename)}`
}

export function videoUrl(project, filename) {
  return `${API}/api/video/file/${encodeURIComponent(project)}/${encodeURIComponent(filename)}`
}

export function outputVideoUrl(project, filename = 'final_video.mp4') {
  return `${API}/api/output-video/${encodeURIComponent(project)}?filename=${encodeURIComponent(filename)}`
}

// ── Subtitles ───────────────────────────────────────────────────────────────

export async function getSubtitleTiming(project, audioFile) {
  const r = await fetch(`${API}/api/subtitles/timing?project=${encodeURIComponent(project)}&audio_file=${encodeURIComponent(audioFile)}`)
  if (!r.ok) throw new Error('Timing metadata not found')
  return r.json()
}

export function subtitleSrtUrl(project, audioFile, mode = '16x9', maxChars = 110, maxWords = 24) {
  const params = new URLSearchParams({
    project: String(project || ''),
    audio_file: String(audioFile || ''),
    mode: mode === '9x16' ? '9x16' : '16x9',
    max_chars: String(maxChars),
    max_words: String(maxWords),
  })
  return `${API}/api/subtitles/srt?${params.toString()}`
}

export async function getFonts() {
  const r = await fetch(`${API}/api/fonts`)
  if (!r.ok) return { fonts: [] }
  return r.json()
}

export async function uploadSubtitleBackgroundMusic(project, file) {
  const fd = new FormData()
  fd.append('file', file)
  const r = await fetch(`${API}/api/subtitles/background-music/upload?project=${encodeURIComponent(project)}`, {
    method: 'POST',
    body: fd,
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }))
    throw new Error(err.detail || 'Background music upload failed')
  }
  return r.json()
}

export function subtitleBackgroundMusicUrl(project, filename) {
  return `${API}/api/subtitles/background-music/${encodeURIComponent(project)}/${encodeURIComponent(filename)}`
}

export function startRenderSubtitles(payload, onEvent, onDone, onError) {
  fetch(`${API}/api/subtitles/render`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(r => {
    if (!r.ok) return r.json().then(e => { throw new Error(e.detail || 'Subtitle render failed') })
    const reader = r.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    function pump() {
      reader.read().then(({ done, value }) => {
        if (done) { onDone(); return }
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (data === '[DONE]') { onDone(); return }
          try { onEvent(JSON.parse(data)) } catch {}
        }
        pump()
      }).catch(onError)
    }
    pump()
  }).catch(onError)
}

// ── Metadata ─────────────────────────────────────────────────────────────────

export async function uploadVideoForCut(file) {
  const fd = new FormData()
  fd.append('file', file)
  const r = await fetch(`${API}/api/video-cutter/upload`, {
    method: 'POST',
    body: fd,
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }))
    throw new Error(err.detail || 'Upload failed')
  }
  return r.json()
}

export async function registerProjectVideoForCut(project, filename) {
  const r = await fetch(`${API}/api/video-cutter/project-video`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project, filename }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }))
    throw new Error(err.detail || 'Register video failed')
  }
  return r.json()
}

export function startVideoCut(payload, onEvent, onDone, onError) {
  fetch(`${API}/api/video-cutter/cut`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(r => {
    if (!r.ok) return r.json().then(e => { throw new Error(e.detail || 'Cut failed') })
    const reader = r.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    function pump() {
      reader.read().then(({ done, value }) => {
        if (done) { onDone(); return }
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (data === '[DONE]') { onDone(); return }
          try { onEvent(JSON.parse(data)) } catch {}
        }
        pump()
      }).catch(onError)
    }
    pump()
  }).catch(onError)
}

export function cutVideoDownloadUrl(filename) {
  return `${API}/api/video-cutter/download/${encodeURIComponent(filename)}`
}

export async function uploadAudioForCut(file) {
  const fd = new FormData()
  fd.append('file', file)
  const r = await fetch(`${API}/api/audio-cutter/upload`, { method: 'POST', body: fd })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }))
    throw new Error(err.detail || 'Upload audio failed')
  }
  return r.json()
}

export async function cutAudio(payload) {
  const r = await fetch(`${API}/api/audio-cutter/cut`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }))
    throw new Error(err.detail || 'Audio cut failed')
  }
  return r.json()
}

export function audioCutDownloadUrl(filename) {
  return `${API}/api/audio-cutter/download/${encodeURIComponent(filename)}`
}

export async function uploadOutro(file) {
  const fd = new FormData()
  fd.append('file', file)
  const r = await fetch(`${API}/api/outro/upload`, {
    method: 'POST',
    body: fd,
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: r.statusText }))
    throw new Error(err.detail || 'Upload outro failed')
  }
  return r.json()
}

export function startAppendOutro(payload, onEvent, onDone, onError) {
  fetch(`${API}/api/outro/append`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(r => {
    if (!r.ok) return r.json().then(e => { throw new Error(e.detail || 'Append outro failed') })
    const reader = r.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    function pump() {
      reader.read().then(({ done, value }) => {
        if (done) { onDone(); return }
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (data === '[DONE]') { onDone(); return }
          try { onEvent(JSON.parse(data)) } catch {}
        }
        pump()
      }).catch(onError)
    }
    pump()
  }).catch(onError)
}

// YouTube publish
export async function getYouTubeAuthStatus() {
  const r = await fetch(`${API}/api/youtube/auth/status`)
  if (!r.ok) throw new Error('YouTube auth status failed')
  return r.json()
}

export async function startYouTubeAuth() {
  const r = await fetch(`${API}/api/youtube/auth/start`, { method: 'POST' })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'YouTube auth failed' }))
    throw new Error(err.detail || 'YouTube auth failed')
  }
  return r.json()
}

export async function logoutYouTube() {
  const r = await fetch(`${API}/api/youtube/auth/logout`, { method: 'POST' })
  if (!r.ok) throw new Error('YouTube logout failed')
  return r.json()
}

export async function createYouTubePublishPlan(payload) {
  const r = await fetch(`${API}/api/publish/youtube/plan/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Create publish plan failed' }))
    throw new Error(err.detail || 'Create publish plan failed')
  }
  return r.json()
}

export async function getYouTubePublishPlan(project) {
  const r = await fetch(`${API}/api/publish/youtube/plan/${encodeURIComponent(project)}`)
  if (!r.ok) throw new Error('Load publish plan failed')
  return r.json()
}

export async function updateYouTubePublishItem(project, itemId, data) {
  const r = await fetch(`${API}/api/publish/youtube/plan/${encodeURIComponent(project)}/items/${encodeURIComponent(itemId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!r.ok) throw new Error('Update publish item failed')
  return r.json()
}

export async function generateYouTubeMetadata(project, itemId, model = '') {
  const r = await fetch(`${API}/api/publish/youtube/metadata/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project, item_id: itemId, model }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Generate metadata failed' }))
    throw new Error(err.detail || 'Generate metadata failed')
  }
  return r.json()
}

export async function generateYouTubeThumbnail(project, itemId, model = '') {
  const r = await fetch(`${API}/api/publish/youtube/thumbnail/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project, item_id: itemId, model }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Generate thumbnail failed' }))
    throw new Error(err.detail || 'Generate thumbnail failed')
  }
  return r.json()
}

export function youtubeThumbnailUrl(project, itemId) {
  return `${API}/api/publish/youtube/thumbnail/${encodeURIComponent(project)}/${encodeURIComponent(itemId)}`
}

export function youtubePublishVideoUrl(project, itemId) {
  return `${API}/api/publish/youtube/video/${encodeURIComponent(project)}/${encodeURIComponent(itemId)}`
}

export async function buildShortsCampaign(project, longItemId = '', verticalFilename = '', model = '', role = '') {
  const r = await fetch(`${API}/api/publish/youtube/shorts-campaign`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project, long_item_id: longItemId, vertical_filename: verticalFilename, model, role }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Build shorts campaign failed' }))
    throw new Error(err.detail || 'Build shorts campaign failed')
  }
  return r.json()
}

// Standalone thumbnail tool
export async function listThumbnails(project, sessionId = '', storyTitle = '') {
  const query = new URLSearchParams()
  if (sessionId) query.set('session_id', sessionId)
  if (storyTitle) query.set('story_title', storyTitle)
  const suffix = query.size ? `?${query.toString()}` : ''
  const r = await fetch(`${API}/api/thumbnails/${encodeURIComponent(project)}/items${suffix}`)
  if (!r.ok) throw new Error('Load thumbnails failed')
  return r.json()
}

export function thumbnailUrl(project, filename) {
  return `${API}/api/thumbnails/${encodeURIComponent(project)}/files/${encodeURIComponent(filename)}`
}

export async function deleteThumbnail(project, filename) {
  const r = await fetch(`${API}/api/thumbnails/${encodeURIComponent(project)}/files/${encodeURIComponent(filename)}`, {
    method: 'DELETE',
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Delete thumbnail failed' }))
    throw new Error(err.detail || 'Delete thumbnail failed')
  }
  return r.json()
}

export async function upscaleThumbnail(project, filename, mode = '2k') {
  const r = await fetch(`${API}/api/thumbnails/${encodeURIComponent(project)}/upscale`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename, mode }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Upscale thumbnail failed' }))
    throw new Error(err.detail || 'Upscale thumbnail failed')
  }
  return r.json()
}

export function thumbnailReferenceUrl(project) {
  return `${API}/api/thumbnails/${encodeURIComponent(project)}/template-reference`
}

export function thumbnailReferenceFileUrl(project, filename) {
  return `${API}/api/thumbnails/${encodeURIComponent(project)}/reference-files/${encodeURIComponent(filename)}`
}

export async function uploadThumbnailReference(project, file) {
  const form = new FormData()
  form.append('file', file)
  const r = await fetch(`${API}/api/thumbnails/${encodeURIComponent(project)}/reference`, {
    method: 'POST',
    body: form,
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Upload reference failed' }))
    throw new Error(err.detail || 'Upload reference failed')
  }
  return r.json()
}

export async function deleteThumbnailReference(project, filename) {
  const r = await fetch(`${API}/api/thumbnails/${encodeURIComponent(project)}/reference-files/${encodeURIComponent(filename)}`, {
    method: 'DELETE',
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Delete reference failed' }))
    throw new Error(err.detail || 'Delete reference failed')
  }
  return r.json()
}

export async function createThumbnailSafePrompt(payload) {
  const r = await fetch(`${API}/api/thumbnails/safe-prompt`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Create safe thumbnail prompt failed' }))
    throw new Error(err.detail || 'Create safe thumbnail prompt failed')
  }
  return r.json()
}

export async function generatePostVideoDescription(payload) {
  const r = await fetch(`${API}/api/post-production/video-description/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Generate video description failed' }))
    throw new Error(err.detail || 'Generate video description failed')
  }
  return r.json()
}

export async function getPostVideoDescription(project) {
  const r = await fetch(`${API}/api/post-production/video-description/${encodeURIComponent(project)}`)
  if (!r.ok) throw new Error('Load video description failed')
  return r.json()
}

export async function savePostVideoDescription(project, description) {
  const r = await fetch(`${API}/api/post-production/video-description`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project, description }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Save video description failed' }))
    throw new Error(err.detail || 'Save video description failed')
  }
  return r.json()
}

export function startThumbnailGenerate(payload, onEvent, onDone, onError) {
  fetch(`${API}/api/thumbnails/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(r => {
    if (!r.ok) return r.json().then(e => { throw new Error(e.detail || 'Generate thumbnail failed') })
    const reader = r.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    function pump() {
      reader.read().then(({ done, value }) => {
        if (done) { onDone(); return }
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (data === '[DONE]') { onDone(); return }
          try { onEvent(JSON.parse(data)) } catch {}
        }
        pump()
      }).catch(onError)
    }
    pump()
  }).catch(onError)
}

export async function createThumbnailDirectorRun(payload) {
  const r = await fetch(`${API}/api/thumbnails/director/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Create Thumbnail Director run failed' }))
    throw new Error(err.detail || 'Create Thumbnail Director run failed')
  }
  return r.json()
}

export async function selectStickFigurePackagingTitle(project, title) {
  const r = await fetch(`${API}/api/stick-figure/packaging/${encodeURIComponent(project)}/selected-title`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Update stick-figure SEO title failed' }))
    throw new Error(err.detail || 'Update stick-figure SEO title failed')
  }
  return r.json()
}

export async function listThumbnailDirectorRuns(project, sessionId = '', storyTitle = '') {
  const query = new URLSearchParams()
  if (sessionId) query.set('session_id', sessionId)
  if (storyTitle) query.set('story_title', storyTitle)
  const suffix = query.size ? `?${query.toString()}` : ''
  const r = await fetch(`${API}/api/thumbnails/director/runs/${encodeURIComponent(project)}${suffix}`)
  if (!r.ok) throw new Error('Load Thumbnail Director runs failed')
  return r.json()
}

export async function getThumbnailDirectorRun(project, runId) {
  const r = await fetch(`${API}/api/thumbnails/director/runs/${encodeURIComponent(project)}/${encodeURIComponent(runId)}`)
  if (!r.ok) throw new Error('Load Thumbnail Director run failed')
  return r.json()
}

export function startThumbnailDirectorEvents(project, runId, onEvent, onDone, onError) {
  const controller = new AbortController()
  fetch(`${API}/api/thumbnails/director/runs/${encodeURIComponent(project)}/${encodeURIComponent(runId)}/events`, {
    signal: controller.signal,
  }).then(r => {
    if (!r.ok) return r.json().then(e => { throw new Error(e.detail || 'Thumbnail Director stream failed') })
    const reader = r.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    function pump() {
      reader.read().then(({ done, value }) => {
        if (done) { onDone?.(); return }
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const raw = line.slice(6)
          if (raw === '[DONE]') { onDone?.(); return }
          try { onEvent?.(JSON.parse(raw)) } catch {}
        }
        pump()
      }).catch(error => {
        if (error?.name !== 'AbortError') onError?.(error)
      })
    }
    pump()
  }).catch(error => {
    if (error?.name !== 'AbortError') onError?.(error)
  })
  return () => controller.abort()
}

export async function cancelThumbnailDirectorRun(project, runId) {
  const r = await fetch(`${API}/api/thumbnails/director/runs/${encodeURIComponent(project)}/${encodeURIComponent(runId)}/cancel`, { method: 'POST' })
  if (!r.ok) throw new Error('Cancel Thumbnail Director run failed')
  return r.json()
}

export async function approveThumbnailDirectorConcepts(project, runId, conceptIds) {
  const r = await fetch(`${API}/api/thumbnails/director/runs/${encodeURIComponent(project)}/${encodeURIComponent(runId)}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ concept_ids: conceptIds }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Approve thumbnail concepts failed' }))
    throw new Error(err.detail || 'Approve thumbnail concepts failed')
  }
  return r.json()
}

export async function regenerateThumbnailDirectorConcepts(project, runId, keepConceptIds) {
  const r = await fetch(`${API}/api/thumbnails/director/runs/${encodeURIComponent(project)}/${encodeURIComponent(runId)}/concepts/regenerate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keep_concept_ids: keepConceptIds }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Regenerate thumbnail concepts failed' }))
    throw new Error(err.detail || 'Regenerate thumbnail concepts failed')
  }
  return r.json()
}

export async function addCustomThumbnailDirectorConcept(project, runId, headline) {
  const r = await fetch(`${API}/api/thumbnails/director/runs/${encodeURIComponent(project)}/${encodeURIComponent(runId)}/concepts/custom`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ headline }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Add custom thumbnail concept failed' }))
    throw new Error(err.detail || 'Add custom thumbnail concept failed')
  }
  return r.json()
}

export async function retryThumbnailDirectorCandidate(project, runId, candidateId, mode = 'original') {
  const r = await fetch(`${API}/api/thumbnails/director/runs/${encodeURIComponent(project)}/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(candidateId)}/retry`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Retry thumbnail candidate failed' }))
    throw new Error(err.detail || 'Retry thumbnail candidate failed')
  }
  return r.json()
}

export async function upscaleThumbnailDirectorCandidate(project, runId, candidateId, mode = '2k') {
  const r = await fetch(`${API}/api/thumbnails/director/runs/${encodeURIComponent(project)}/${encodeURIComponent(runId)}/candidates/${encodeURIComponent(candidateId)}/upscale`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Upscale thumbnail candidate failed' }))
    throw new Error(err.detail || 'Upscale thumbnail candidate failed')
  }
  return r.json()
}

export async function listThumbnailProfiles() {
  const r = await fetch(`${API}/api/thumbnail-profiles`)
  if (!r.ok) throw new Error('Load thumbnail profiles failed')
  return r.json()
}

export async function createThumbnailProfile(channelName) {
  const r = await fetch(`${API}/api/thumbnail-profiles`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ channel_name: channelName }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Create thumbnail profile failed' }))
    throw new Error(err.detail || 'Create thumbnail profile failed')
  }
  return r.json()
}

export async function addThumbnailProfileReference(profileId, project, filename) {
  const r = await fetch(`${API}/api/thumbnail-profiles/${encodeURIComponent(profileId)}/references`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project, filename }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Add profile reference failed' }))
    throw new Error(err.detail || 'Add profile reference failed')
  }
  return r.json()
}

export async function setThumbnailProfilePrimary(profileId, referenceId) {
  const r = await fetch(`${API}/api/thumbnail-profiles/${encodeURIComponent(profileId)}/primary`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reference_id: referenceId }),
  })
  if (!r.ok) throw new Error('Set primary thumbnail reference failed')
  return r.json()
}

export function thumbnailProfileReferenceUrl(profileId, referenceId) {
  return `${API}/api/thumbnail-profiles/${encodeURIComponent(profileId)}/references/${encodeURIComponent(referenceId)}`
}

export async function selectProjectThumbnail(project, filename) {
  const r = await fetch(`${API}/api/thumbnails/${encodeURIComponent(project)}/selected`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ filename }),
  })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Select project thumbnail failed' }))
    throw new Error(err.detail || 'Select project thumbnail failed')
  }
  return r.json()
}

export async function getProjectSelectedThumbnail(project) {
  const r = await fetch(`${API}/api/thumbnails/${encodeURIComponent(project)}/selected`)
  if (!r.ok) throw new Error('Load selected thumbnail failed')
  return r.json()
}

export async function testThumbnailVision() {
  const r = await fetch(`${API}/api/thumbnail-vision/test`, { method: 'POST' })
  if (!r.ok) {
    const err = await r.json().catch(() => ({ detail: 'Thumbnail Vision test failed' }))
    throw new Error(err.detail || 'Thumbnail Vision test failed')
  }
  return r.json()
}

export function startYouTubeUpload(project, itemId, mode, onEvent, onDone, onError) {
  fetch(`${API}/api/publish/youtube/items/${encodeURIComponent(itemId)}/upload`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project, item_id: itemId, mode }),
  }).then(r => {
    if (!r.ok) return r.json().then(e => { throw new Error(e.detail || 'YouTube upload failed') })
    const reader = r.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    function pump() {
      reader.read().then(({ done, value }) => {
        if (done) { onDone(); return }
        buf += decoder.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop()
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (data === '[DONE]') { onDone(); return }
          try { onEvent(JSON.parse(data)) } catch {}
        }
        pump()
      }).catch(onError)
    }
    pump()
  }).catch(onError)
}

export async function getAudioDuration(project, filename) {
  const r = await fetch(`${API}/api/audio/duration?project=${encodeURIComponent(project)}&filename=${encodeURIComponent(filename)}`)
  if (!r.ok) return { duration_sec: 0 }
  return r.json()
}

export async function getMetadata(project) {
  const r = await fetch(`${API}/api/metadata/${encodeURIComponent(project)}`)
  return r.json()
}

export async function saveMetadata(project, data) {
  const r = await fetch(`${API}/api/metadata/${encodeURIComponent(project)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return r.json()
}
