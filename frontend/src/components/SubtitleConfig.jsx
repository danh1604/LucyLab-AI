import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertCircle, CheckCircle2, Download, Loader2, Lock, Move, Music2, RotateCcw, Subtitles, Type, Unlock, Upload } from 'lucide-react'
import { getFonts, getSubtitleTiming, outputVideoUrl, startRenderSubtitles, subtitleBackgroundMusicUrl, uploadSubtitleBackgroundMusic } from '../api.js'

const DEFAULT_SUBTITLE_FONT = 'JetBrains Mono NL SemiBold'

const DEFAULT_STYLE = {
  fontFamily: DEFAULT_SUBTITLE_FONT,
  fontSize16x9: 54,
  fontSize9x16: 72,
  textColor: '#ffffff',
  outlineColor: '#000000',
  outlineWidth: 3,
  backgroundEnabled: false,
  backgroundColor: '#000000',
  backgroundOpacity: 0.55,
  bold: true,
  align: 'center',
  position: 'bottom',
  marginX: 90,
  marginY: 150,
  maxWidthPct: 82,
  lineHeight: 1.12,
  maxCharsPerLine16x9: 42,
  maxLines16x9: 2,
  maxCueChars16x9: 110,
  maxCueWords16x9: 24,
  maxCharsPerLine9x16: 24,
  maxLines9x16: 2,
  maxCueChars9x16: 60,
  maxCueWords9x16: 18,
  subtitleLayout16x9: { x: 50, y: 86, widthPct: 82, locked: false },
  subtitleLayout9x16: { x: 50, y: 86, widthPct: 82, locked: false },
  backgroundMusicEnabled: false,
  backgroundMusicFile: '',
  backgroundMusicVolume: 15,
  burnSubtitles: true,
  renderTargets: { '16x9': true, '9x16': true },
  verticalVideoMode: 'crop',
}

const MAX_CHARS_PER_LINE = {
  '16x9': 42,
  '9x16': 24,
}
const MAX_CUE_CHARS = 60
const MAX_CUE_WORDS = 18
const MODE_CUE_LIMITS = {
  '16x9': { maxChars: 110, maxWords: 24 },
  '9x16': { maxChars: MAX_CUE_CHARS, maxWords: MAX_CUE_WORDS },
}

const DEFAULT_LAYOUT = { x: 50, y: 86, widthPct: 82, locked: false }

function normalizeLayout(value, fallback = DEFAULT_LAYOUT) {
  const source = value && typeof value === 'object' ? value : fallback
  const number = (key, min, max, defaultValue) => {
    const parsed = Number(source[key])
    return Math.max(min, Math.min(max, Number.isFinite(parsed) ? parsed : defaultValue))
  }
  return {
    x: number('x', 3, 97, fallback.x),
    y: number('y', 3, 97, fallback.y),
    widthPct: number('widthPct', 30, 95, fallback.widthPct),
    locked: Boolean(source.locked),
  }
}

function legacyLayout(saved, suffix) {
  const position = saved?.[`subtitlePosition${suffix}`]
  return normalizeLayout({
    x: position?.x,
    y: position?.y,
    widthPct: saved?.[`maxWidthPct${suffix}`] ?? saved?.maxWidthPct,
    locked: false,
  })
}

function normalizeSubtitleStyle(saved = {}) {
  const next = { ...DEFAULT_STYLE, ...(saved || {}) }
  next.renderTargets = {
    '16x9': saved?.renderTargets?.['16x9'] !== false,
    '9x16': saved?.renderTargets?.['9x16'] ?? saved?.renderVertical ?? true,
  }
  next.burnSubtitles = saved?.burnSubtitles !== false
  for (const suffix of ['16x9', '9x16']) {
    next[`subtitleLayout${suffix}`] = normalizeLayout(
      saved?.[`subtitleLayout${suffix}`],
      legacyLayout(saved, suffix),
    )
  }
  // Upgrade sessions created before sentence-first cueing. Preserve deliberate
  // custom values, but replace the old shipped defaults automatically.
  if (
    (Number(saved?.maxCueChars16x9) === 84 && Number(saved?.maxCueWords16x9) === 16) ||
    (Number(saved?.maxCueChars16x9) === 96 && Number(saved?.maxCueWords16x9) === 24)
  ) {
    next.maxCueChars16x9 = DEFAULT_STYLE.maxCueChars16x9
    next.maxCueWords16x9 = DEFAULT_STYLE.maxCueWords16x9
  }
  if (Number(saved?.maxCueChars9x16) === 56 && Number(saved?.maxCueWords9x16) === 11) {
    next.maxCueChars9x16 = DEFAULT_STYLE.maxCueChars9x16
    next.maxCueWords9x16 = DEFAULT_STYLE.maxCueWords9x16
  }
  return next
}

function fmtSec(s) {
  if (!s || s <= 0) return '0:00'
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return `${m}:${String(sec).padStart(2, '0')}`
}

function fallbackTiming(storyText, duration, warning = 'Không tải được timing metadata từ server; đang dùng timing xấp xỉ.') {
  const text = (storyText || '').replace(/\s+/g, ' ').trim()
  if (!text) return { warning: 'Chưa có nội dung truyện để tạo phụ đề.', captions: [] }
  const sentences = text.split(/(?<=[.!?…])\s+/).filter(Boolean)
  const totalWeight = sentences.reduce((sum, item) => sum + Math.max(1, item.length), 0) || 1
  let cursor = 0
  return {
    warning,
    captions: sentences.map((item, index) => {
      const length = Math.max(0.8, duration * (Math.max(1, item.length) / totalWeight))
      const start = cursor
      const end = Math.min(duration, start + length)
      cursor = end
      return { index: index + 1, text: item, start_sec: start, end_sec: end, duration_sec: end - start }
    }),
  }
}

function currentCaption(timing, currentTime) {
  return timing?.captions?.find(c => currentTime >= c.start_sec && currentTime <= c.end_sec) || null
}

function cueWordCount(text) {
  return (text || '').trim().split(/\s+/).filter(Boolean).length
}

function cueFits(text, maxChars, maxWords) {
  return text.length <= maxChars && cueWordCount(text) <= maxWords
}

function splitSentencesForCues(text) {
  return (text || '').replace(/\s+/g, ' ').trim().split(/(?<=[.!?…。！？])\s+|(?<=[。！？])/).map(item => item.trim()).filter(Boolean)
}

function splitLongSentenceAtPauses(sentence, maxChars, maxWords) {
  const tokens = (sentence || '').match(/\S+/g) || []
  const chunks = []
  let start = 0
  while (start < tokens.length) {
    let end = start
    let lastPause = -1
    while (end < tokens.length) {
      const candidate = tokens.slice(start, end + 1).join(' ')
      if (end > start && !cueFits(candidate, maxChars, maxWords)) break
      if (/[,;:—–]$/.test(tokens[end])) lastPause = end + 1
      end += 1
    }
    if (end >= tokens.length) {
      chunks.push(tokens.slice(start).join(' '))
      break
    }
    const cut = lastPause > start ? lastPause : Math.max(start + 1, end)
    chunks.push(tokens.slice(start, cut).join(' '))
    start = cut
  }
  return chunks.filter(Boolean)
}

function splitTextIntoCueBlocks(text, maxChars = MAX_CUE_CHARS, maxWords = MAX_CUE_WORDS) {
  const blocks = []
  let pending = ''
  const shortThreshold = Math.max(18, Math.floor(maxChars * 0.48))
  const flush = () => {
    if (pending) blocks.push(pending)
    pending = ''
  }
  for (const sentence of splitSentencesForCues(text)) {
    const pieces = cueFits(sentence, maxChars, maxWords) ? [sentence] : splitLongSentenceAtPauses(sentence, maxChars, maxWords)
    for (const piece of pieces) {
      if (pending) {
        const combined = `${pending} ${piece}`
        if (cueFits(combined, maxChars, maxWords)) {
          pending = combined
          if (pending.length >= shortThreshold) flush()
          continue
        }
        flush()
      }
      if (cueFits(piece, maxChars, maxWords) && piece.length < shortThreshold) pending = piece
      else blocks.push(piece)
    }
  }
  flush()
  return blocks
}

function buildSubtitleCues(timing, mode = '9x16', style = DEFAULT_STYLE) {
  if (!timing?.captions) return timing?.subtitle_cues || []
  const suffix = mode === '9x16' ? '9x16' : '16x9'
  const defaults = MODE_CUE_LIMITS[mode] || MODE_CUE_LIMITS['9x16']
  const limits = {
    maxChars: Math.max(12, Math.min(180, Number(style[`maxCueChars${suffix}`]) || defaults.maxChars)),
    maxWords: Math.max(2, Math.min(36, Number(style[`maxCueWords${suffix}`]) || defaults.maxWords)),
  }
  const cues = []
  for (const caption of timing.captions) {
    const blocks = splitTextIntoCueBlocks(caption.text, limits.maxChars, limits.maxWords)
    if (!blocks.length) continue
    const start = Number(caption.start_sec || 0)
    const end = Number(caption.end_sec || start)
    const duration = Math.max(0.01, end - start)
    const weights = blocks.map(block => Math.max(1, block.length))
    const total = weights.reduce((sum, item) => sum + item, 0) || 1
    let cursor = start
    blocks.forEach((block, index) => {
      const cueEnd = index === blocks.length - 1 ? end : Math.min(end, cursor + duration * (weights[index] / total))
      cues.push({ index: cues.length + 1, text: block, start_sec: cursor, end_sec: cueEnd, duration_sec: Math.max(0.01, cueEnd - cursor) })
      cursor = cueEnd
    })
  }
  return cues
}

function wrapCueText(text, maxChars, maxLines = 2) {
  const words = (text || '').replace(/\s+/g, ' ').trim().split(' ')
  const blocks = []
  let lines = []
  let current = ''
  for (const word of words) {
    if (!word) continue
    const candidate = current ? `${current} ${word}` : word
    if (candidate.length <= maxChars || !current) {
      current = candidate
    } else {
      lines.push(current)
      if (lines.length >= maxLines) {
        blocks.push(lines.join('\n'))
        lines = []
      }
      current = word
    }
  }
  if (current) lines.push(current)
  if (lines.length) blocks.push(lines.join('\n'))
  if (blocks.length <= 1) return blocks[0] || ''
  const allWords = blocks.join(' ').split(/\s+/).filter(Boolean)
  const midpoint = Math.max(1, Math.floor(allWords.length / 2))
  return `${allWords.slice(0, midpoint).join(' ')}\n${allWords.slice(midpoint).join(' ')}`
}

export default function SubtitleConfig({
  project,
  inputVideo,
  audioFile,
  storyText,
  audioDuration,
  videoSettings,
  initialStyle = DEFAULT_STYLE,
  onStyleChange,
  onDone,
}) {
  const [style, setStyle] = useState(() => normalizeSubtitleStyle(initialStyle))
  const [mode, setMode] = useState('16x9')
  const [timing, setTiming] = useState(null)
  const [time, setTime] = useState(0)
  const [loading, setLoading] = useState(true)
  const [rendering, setRendering] = useState(false)
  const [hardwareMode, setHardwareMode] = useState('')
  const [percent, setPercent] = useState(0)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [outputs, setOutputs] = useState(null)
  const [fonts, setFonts] = useState([])
  const [musicUploading, setMusicUploading] = useState(false)
  const [layoutDraft, setLayoutDraft] = useState(null)
  const videoPreviewRef = useRef(null)
  const musicPreviewRef = useRef(null)
  const musicFileRef = useRef(null)
  const previewStageRef = useRef(null)
  const interactionRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getSubtitleTiming(project, audioFile)
      .then(data => { if (!cancelled) setTiming(data) })
      .catch(() => { if (!cancelled) setTiming(fallbackTiming(storyText, audioDuration || 0)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [project, audioFile, storyText, audioDuration])

  useEffect(() => {
    let cancelled = false
    getFonts()
      .then(data => { if (!cancelled) setFonts(Array.isArray(data.fonts) ? data.fonts : []) })
      .catch(() => { if (!cancelled) setFonts([]) })
    return () => { cancelled = true }
  }, [])

  const isVertical = mode === '9x16'
  const subtitleRequested = style.burnSubtitles !== false
  const selectedRenderModes = ['16x9', '9x16'].filter(item => style.renderTargets?.[item])
  const selectedRenderLabel = selectedRenderModes.map(item => item === '16x9' ? '16:9' : '9:16').join(' + ')
  const modeSuffix = isVertical ? '9x16' : '16x9'
  const subtitleLayoutKey = `subtitleLayout${modeSuffix}`
  const savedSubtitleLayout = normalizeLayout(style[subtitleLayoutKey])
  const subtitleLayout = layoutDraft?.mode === mode ? layoutDraft.layout : savedSubtitleLayout
  const fontSize = isVertical ? style.fontSize9x16 : style.fontSize16x9
  const calculatedPreviewMaxChars = Math.floor(((isVertical ? 1080 : 1920) * (subtitleLayout.widthPct / 100)) / (Math.max(1, fontSize) * 0.56))
  const configuredMaxChars = Number(style[`maxCharsPerLine${modeSuffix}`]) || MAX_CHARS_PER_LINE[mode]
  const previewMaxChars = Math.max(8, Math.min(configuredMaxChars, calculatedPreviewMaxChars))
  const previewMaxLines = Math.max(1, Math.min(4, Number(style[`maxLines${modeSuffix}`]) || 2))
  const sharedCues = useMemo(() => buildSubtitleCues(timing, mode, style), [timing, mode, style])
  const cue = useMemo(() => currentCaption({ captions: sharedCues }, time), [sharedCues, time])
  const caption = useMemo(() => cue ? { ...cue, text: wrapCueText(cue.text, previewMaxChars, previewMaxLines) } : null, [cue, previewMaxChars, previewMaxLines])

  function update(next) {
    const merged = { ...style, ...next }
    setStyle(merged)
    onStyleChange?.(merged)
  }

  function setRenderTarget(target, enabled) {
    update({ renderTargets: { ...style.renderTargets, [target]: enabled } })
  }

  function saveLayout(layout) {
    update({ [subtitleLayoutKey]: normalizeLayout(layout) })
  }

  function startLayoutInteraction(event, type, edge = '') {
    if (subtitleLayout.locked) return
    event.preventDefault()
    event.stopPropagation()
    event.currentTarget.setPointerCapture?.(event.pointerId)
    interactionRef.current = {
      type, edge, mode, startClientX: event.clientX, startClientY: event.clientY,
      startLayout: subtitleLayout,
    }
    setLayoutDraft({ mode, layout: subtitleLayout })
  }

  function moveLayoutInteraction(event) {
    const interaction = interactionRef.current
    const rect = previewStageRef.current?.getBoundingClientRect()
    if (!interaction || interaction.mode !== mode || !rect?.width || !rect?.height) return
    const { startLayout } = interaction
    let layout
    if (interaction.type === 'move') {
      layout = normalizeLayout({
        ...startLayout,
        x: startLayout.x + ((event.clientX - interaction.startClientX) / rect.width) * 100,
        y: startLayout.y + ((event.clientY - interaction.startClientY) / rect.height) * 100,
      })
    } else {
      const direction = interaction.edge === 'right' ? 1 : -1
      layout = normalizeLayout({
        ...startLayout,
        widthPct: startLayout.widthPct + ((event.clientX - interaction.startClientX) / rect.width) * 200 * direction,
      })
    }
    interactionRef.current = { ...interaction, currentLayout: layout }
    setLayoutDraft({ mode, layout })
  }

  function finishLayoutInteraction(event) {
    const interaction = interactionRef.current
    if (!interaction || interaction.mode !== mode) return
    event.currentTarget.releasePointerCapture?.(event.pointerId)
    const layout = interaction.currentLayout || interaction.startLayout
    interactionRef.current = null
    setLayoutDraft(null)
    saveLayout(layout)
  }

  function resetLayout() {
    interactionRef.current = null
    setLayoutDraft(null)
    saveLayout(DEFAULT_LAYOUT)
  }

  function toggleLayoutLock() {
    saveLayout({ ...subtitleLayout, locked: !subtitleLayout.locked })
  }

  function handleLayoutShortcut(event) {
    if (event.shiftKey && event.key.toLowerCase() === 'l') {
      event.preventDefault()
      toggleLayoutLock()
    }
  }

  function moveSubtitleWithKeyboard(event) {
    const movement = {
      ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1],
    }[event.key]
    if (!movement || subtitleLayout.locked) return
    event.preventDefault()
    const step = event.shiftKey ? 5 : 1
    saveLayout({ ...subtitleLayout, x: subtitleLayout.x + movement[0] * step, y: subtitleLayout.y + movement[1] * step })
  }

  function resizeSubtitleWithKeyboard(event) {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key) || subtitleLayout.locked) return
    event.preventDefault()
    const step = event.shiftKey ? 5 : 1
    saveLayout({ ...subtitleLayout, widthPct: subtitleLayout.widthPct + (event.key === 'ArrowRight' ? step : -step) })
  }

  const musicRequested = Boolean(style.backgroundMusicEnabled)
  const musicEnabled = Boolean(musicRequested && style.backgroundMusicFile)
  const musicVolume = Math.max(0, Math.min(100, Number(style.backgroundMusicVolume ?? 15)))

  useEffect(() => {
    const music = musicPreviewRef.current
    if (!music) return
    music.volume = musicVolume / 100
    if (!musicEnabled) music.pause()
  }, [musicEnabled, musicVolume, style.backgroundMusicFile])

  function syncPreviewMusic(currentTime = 0, shouldPlay = false) {
    const music = musicPreviewRef.current
    if (!music || !musicEnabled) return
    if (Number.isFinite(music.duration) && music.duration > 0) music.currentTime = currentTime % music.duration
    music.volume = musicVolume / 100
    if (shouldPlay) music.play().catch(() => {})
  }

  async function handleMusicUpload(file) {
    if (!file) return
    setError('')
    setMusicUploading(true)
    try {
      const uploaded = await uploadSubtitleBackgroundMusic(project, file)
      update({ backgroundMusicEnabled: true, backgroundMusicFile: uploaded.filename })
    } catch (e) {
      setError(e.message || 'Không thể tải nhạc nền')
    } finally {
      setMusicUploading(false)
    }
  }

  function render() {
    setError('')
    setOutputs(null)
    setPercent(0)
    setHardwareMode('')

    // A clean horizontal video already exists from the Video step. When the
    // user explicitly disables subtitles, selects only 16:9, and has not
    // requested background music, there is nothing left to render here.
    // Pass the existing file straight to post-production instead of running
    // an unnecessary FFmpeg job.
    const canSkipRender = !subtitleRequested
      && selectedRenderModes.length === 1
      && selectedRenderModes[0] === '16x9'
      && !musicEnabled
    if (canSkipRender) {
      const passthroughOutputs = { '16x9': inputVideo }
      setPercent(100)
      setStatus('Video sạch 16:9 đã sẵn sàng — chuyển thẳng sang hậu kỳ')
      setOutputs(passthroughOutputs)
      setRendering(false)
      onDone?.(passthroughOutputs, style)
      return
    }

    setRendering(true)
    setStatus(`Đang chuẩn bị render ${subtitleRequested ? 'phụ đề' : 'video không phụ đề'}...`)
    startRenderSubtitles(
      {
        project,
        input_video: inputVideo,
        audio_file: audioFile,
        story_text: storyText,
        style,
        video_encoder: videoSettings?.videoEncoder || 'nvidia_nvenc',
        quality: videoSettings?.quality || 'high',
        overwrite_mode: videoSettings?.overwriteMode || 'version',
        background_music: musicEnabled ? style.backgroundMusicFile : '',
        background_music_volume: musicEnabled ? musicVolume / 100 : 0,
        render_modes: selectedRenderModes,
        vertical_video_mode: style.verticalVideoMode === 'fit' ? 'fit' : 'crop',
        burn_subtitles: subtitleRequested,
      },
      (ev) => {
        if (ev.type === 'step') {
          setStatus(ev.message || `Đang render ${subtitleRequested ? 'phụ đề' : 'video không phụ đề'}...`)
          if (ev.hardware_acceleration) setHardwareMode(ev.hardware_acceleration)
        }
        if (ev.type === 'progress') {
          setPercent(ev.percent || 0)
          setStatus(`${ev.message || `Đang render ${subtitleRequested ? 'phụ đề' : 'video không phụ đề'}...`} ${ev.percent || 0}%`)
        }
        if (ev.type === 'error') {
          setError(ev.message || `Render ${subtitleRequested ? 'phụ đề' : 'video không phụ đề'} thất bại`)
          setRendering(false)
        }
        if (ev.type === 'done') {
          setPercent(100)
          setStatus('Hoàn thành')
          setOutputs(ev.outputs || {})
          setRendering(false)
          onDone?.(ev.outputs || {}, style)
        }
      },
      () => setRendering(false),
      (e) => {
        setError(e?.message || String(e))
        setRendering(false)
      },
    )
  }

  const previewStyle = {
    fontFamily: style.fontFamily?.trim() || DEFAULT_SUBTITLE_FONT,
    fontSize: `${Math.max(12, Math.round(fontSize / (isVertical ? 5.5 : 6.5)))}px`,
    color: style.textColor,
    fontWeight: style.bold ? 800 : 500,
    lineHeight: style.lineHeight,
    textAlign: 'center',
    textShadow: `0 0 2px ${style.outlineColor}, 0 0 ${style.outlineWidth + 2}px ${style.outlineColor}`,
    background: style.backgroundEnabled ? `${style.backgroundColor}${Math.round(style.backgroundOpacity * 255).toString(16).padStart(2, '0')}` : 'transparent',
    maxWidth: '100%',
    overflowWrap: 'normal',
    wordBreak: 'normal',
    whiteSpace: 'pre-line',
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Subtitles size={17} className="text-accent-400" />
          <h2 className="text-lg font-bold text-white">Chèn phụ đề</h2>
        </div>
        <div className="inline-flex rounded-lg border border-surface-600 overflow-hidden text-xs">
          {['16x9', '9x16'].map(item => (
            <button
              key={item}
              onClick={() => {
                interactionRef.current = null
                setLayoutDraft(null)
                setMode(item)
              }}
              className={`px-3 py-1.5 ${mode === item ? 'bg-accent-600 text-white' : 'bg-surface-900 text-slate-400 hover:text-white'}`}
            >
              {item}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <Loader2 size={15} className="animate-spin" />
          Đang tải timing phụ đề...
        </div>
      ) : (
        <>
          {timing?.warning && (
            <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-300">
              <AlertCircle size={15} className="mt-0.5 shrink-0" />
              <span>{timing.warning}</span>
            </div>
          )}

          <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_18rem] gap-4">
            <div className="rounded-2xl border border-surface-600 bg-black overflow-hidden">
              <div ref={previewStageRef} tabIndex={0} onKeyDown={handleLayoutShortcut} className={`relative mx-auto bg-black outline-none focus-visible:ring-2 focus-visible:ring-accent-300 ${isVertical ? 'aspect-[9/16] max-h-[560px]' : 'aspect-video'}`}>
                <video
                  ref={videoPreviewRef}
                  src={outputVideoUrl(project, inputVideo)}
                  controls
                  onTimeUpdate={e => setTime(e.currentTarget.currentTime)}
                  onPlay={e => syncPreviewMusic(e.currentTarget.currentTime, true)}
                  onPause={() => musicPreviewRef.current?.pause()}
                  onSeeked={e => syncPreviewMusic(e.currentTarget.currentTime, !e.currentTarget.paused)}
                  onEnded={() => musicPreviewRef.current?.pause()}
                  className={`absolute inset-0 w-full h-full ${isVertical && style.verticalVideoMode !== 'fit' ? 'object-cover' : 'object-contain'}`}
                />
                <audio
                  ref={musicPreviewRef}
                  src={musicEnabled ? subtitleBackgroundMusicUrl(project, style.backgroundMusicFile) : undefined}
                  loop
                  preload="auto"
                  onLoadedMetadata={() => {
                    const video = videoPreviewRef.current
                    if (video) syncPreviewMusic(video.currentTime, !video.paused)
                  }}
                />
                {subtitleRequested && caption && (
                  <div
                    style={{ left: `${subtitleLayout.x}%`, top: `${subtitleLayout.y}%`, width: `${subtitleLayout.widthPct}%`, touchAction: 'none' }}
                    className="group absolute z-10 max-w-[calc(100%-2rem)] -translate-x-1/2 -translate-y-1/2 pointer-events-none"
                  >
                    <button
                      type="button"
                      aria-label={`Di chuyển phụ đề ${mode}`}
                      aria-pressed={subtitleLayout.locked}
                      disabled={subtitleLayout.locked}
                      onPointerDown={event => startLayoutInteraction(event, 'move')}
                      onPointerMove={moveLayoutInteraction}
                      onPointerUp={finishLayoutInteraction}
                      onPointerCancel={finishLayoutInteraction}
                      onKeyDown={moveSubtitleWithKeyboard}
                      className={`pointer-events-auto relative left-1/2 block max-w-full -translate-x-1/2 border-0 bg-transparent p-0 text-inherit outline-none transition-shadow focus-visible:ring-2 focus-visible:ring-accent-300 ${subtitleLayout.locked ? 'cursor-not-allowed opacity-90' : 'cursor-grab hover:ring-1 hover:ring-accent-300/80 active:cursor-grabbing'}`}
                    >
                      <span style={previewStyle} className="block rounded px-2 py-1">
                        {caption.text}
                      </span>
                      <span className="pointer-events-none absolute -right-2 -top-2 rounded-full border border-surface-500 bg-surface-950 p-1 text-accent-300 shadow-sm">
                        {subtitleLayout.locked ? <Lock size={12} /> : <Move size={12} />}
                      </span>
                    </button>
                    {!subtitleLayout.locked && ['left', 'right'].map(edge => (
                      <button
                        key={edge}
                        type="button"
                        aria-label={`Đổi bề rộng phụ đề ${mode}`}
                        onPointerDown={event => startLayoutInteraction(event, 'resize', edge)}
                        onPointerMove={moveLayoutInteraction}
                        onPointerUp={finishLayoutInteraction}
                        onPointerCancel={finishLayoutInteraction}
                        onKeyDown={resizeSubtitleWithKeyboard}
                        className={`pointer-events-auto absolute top-1/2 h-9 w-3 -translate-y-1/2 cursor-ew-resize rounded-full border border-accent-300/80 bg-surface-950/90 opacity-0 shadow-sm transition-opacity focus:opacity-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-300 group-hover:opacity-100 ${edge === 'left' ? '-left-1.5' : '-right-1.5'}`}
                        title="Kéo để đổi bề rộng"
                      />
                    ))}
                  </div>
                )}
              </div>
            </div>

            <div className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-3">
              <div className="flex items-center justify-between gap-2 text-sm font-semibold text-white">
                <div className="flex items-center gap-2">
                  <Type size={15} className="text-accent-400" />
                  Style
                </div>
                <div className="flex items-center gap-1.5">
                  <button type="button" onClick={resetLayout} className="inline-flex items-center gap-1 rounded-md border border-surface-500 px-2 py-1 text-xs font-medium text-slate-300 hover:border-accent-400 hover:text-white" title={`Đặt lại bố cục ${mode}`}>
                    <RotateCcw size={13} />Reset
                  </button>
                  <button type="button" onClick={toggleLayoutLock} className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-medium ${subtitleLayout.locked ? 'border-amber-400/70 bg-amber-400/10 text-amber-200' : 'border-surface-500 text-slate-300 hover:border-accent-400 hover:text-white'}`} title="Shift + L">
                    {subtitleLayout.locked ? <Lock size={13} /> : <Unlock size={13} />}
                    {subtitleLayout.locked ? 'Đã ghim' : 'Ghim'}
                  </button>
                </div>
              </div>

              <label className="space-y-1 block">
                <span className="text-xs text-slate-400">Font</span>
                <input
                  value={style.fontFamily}
                  list="subtitle-font-list"
                  onChange={e => update({ fontFamily: e.target.value })}
                  placeholder="Gõ để tìm font trên máy"
                  className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white"
                />
                <datalist id="subtitle-font-list">
                  {fonts.map(font => (
                    <option key={font} value={font} />
                  ))}
                </datalist>
                <span className="block text-xs text-slate-600">
                  {fonts.length ? `${fonts.length} font khả dụng` : 'Đang tải danh sách font...'}
                </span>
              </label>

              <div className="grid grid-cols-2 gap-2">
                <label className="space-y-1">
                  <span className="text-xs text-slate-400">Size 16:9</span>
                  <input type="number" value={style.fontSize16x9} onChange={e => update({ fontSize16x9: Number(e.target.value) })} className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white" />
                </label>
                <label className="space-y-1">
                  <span className="text-xs text-slate-400">Size 9:16</span>
                  <input type="number" value={style.fontSize9x16} onChange={e => update({ fontSize9x16: Number(e.target.value) })} className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white" />
                </label>
              </div>

              <div className="rounded-xl border border-surface-600 bg-surface-950/45 p-3 space-y-2">
                <p className="text-[11px] leading-4 text-slate-500">Ưu tiên câu trọn vẹn; câu ngắn ghép với câu sau. Câu quá dài mới ngắt ở dấu phẩy hoặc điểm ngắt tự nhiên.</p>
                <p className="text-xs font-semibold text-slate-300">Mật độ phụ đề {mode === '16x9' ? '16:9' : '9:16'}</p>
                <div className="grid grid-cols-2 gap-2">
                  <label className="space-y-1">
                    <span className="text-xs text-slate-400">Ký tự / dòng</span>
                    <input type="number" min="8" max="90" value={style[`maxCharsPerLine${modeSuffix}`]} onChange={e => update({ [`maxCharsPerLine${modeSuffix}`]: Number(e.target.value) })} className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white" />
                  </label>
                  <label className="space-y-1">
                    <span className="text-xs text-slate-400">Dòng tối đa</span>
                    <input type="number" min="1" max="4" value={style[`maxLines${modeSuffix}`]} onChange={e => update({ [`maxLines${modeSuffix}`]: Number(e.target.value) })} className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white" />
                  </label>
                  <label className="space-y-1">
                    <span className="text-xs text-slate-400">Ký tự / cue</span>
                    <input type="number" min="12" max="180" value={style[`maxCueChars${modeSuffix}`]} onChange={e => update({ [`maxCueChars${modeSuffix}`]: Number(e.target.value) })} className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white" />
                  </label>
                  <label className="space-y-1">
                    <span className="text-xs text-slate-400">Từ / cue</span>
                    <input type="number" min="2" max="36" value={style[`maxCueWords${modeSuffix}`]} onChange={e => update({ [`maxCueWords${modeSuffix}`]: Number(e.target.value) })} className="w-full bg-surface-900 border border-surface-500 rounded-lg px-3 py-2 text-sm text-white" />
                  </label>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <label className="space-y-1">
                  <span className="text-xs text-slate-400">Màu chữ</span>
                  <input type="color" value={style.textColor} onChange={e => update({ textColor: e.target.value })} className="w-full h-10 bg-surface-900 border border-surface-500 rounded-lg" />
                </label>
                <label className="space-y-1">
                  <span className="text-xs text-slate-400">Màu viền</span>
                  <input type="color" value={style.outlineColor} onChange={e => update({ outlineColor: e.target.value })} className="w-full h-10 bg-surface-900 border border-surface-500 rounded-lg" />
                </label>
              </div>

              <div className="rounded-lg border border-dashed border-surface-600 bg-surface-950/45 px-3 py-2.5 text-xs leading-5 text-slate-400">
                Nắm phần giữa để kéo vị trí. Rê chuột đến hai mép để hiện tay nắm đổi bề rộng. Mỗi bản 16:9 và 9:16 có bố cục riêng. Ghim sẽ khóa kéo/resize; nhấn Shift + L trên preview để ghim hoặc mở ghim.
              </div>

              <label className="flex items-center gap-2 text-sm text-slate-300">
                <input type="checkbox" checked={style.backgroundEnabled} onChange={e => update({ backgroundEnabled: e.target.checked })} />
                Bật nền chữ
              </label>

              <div className="text-xs text-slate-500">
                {timing?.captions?.length || 0} câu · đang xem {fmtSec(time)}
              </div>
            </div>
          </div>

          <div className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-4">
            <label className={`flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition-colors ${subtitleRequested ? 'border-accent-400 bg-accent-500/10' : 'border-surface-600 bg-surface-950/35 hover:border-surface-400'}`}>
              <input
                type="checkbox"
                checked={subtitleRequested}
                onChange={e => update({ burnSubtitles: e.target.checked })}
                className="mt-0.5"
              />
              <span>
                <span className="block text-sm font-semibold text-white">Chèn phụ đề vào video</span>
                <span className="mt-1 block text-xs leading-5 text-slate-400">Tắt để xuất video sạch, vẫn giữ tiếng đọc và nhạc nền nếu đã chọn.</span>
              </span>
            </label>
            <div>
              <h3 className="text-sm font-semibold text-white">Đầu ra render</h3>
              <p className="mt-1 text-xs leading-5 text-slate-400">Chọn đúng các phiên bản cần xuất. Video không được chọn sẽ giữ nguyên file đã render trước đó.</p>
            </div>

            <div className="grid gap-2 sm:grid-cols-2">
              <label className={`cursor-pointer rounded-xl border p-3 transition-colors ${style.renderTargets?.['16x9'] ? 'border-accent-400 bg-accent-500/10' : 'border-surface-600 hover:border-surface-400'}`}>
                <input className="sr-only" type="checkbox" checked={Boolean(style.renderTargets?.['16x9'])} onChange={e => setRenderTarget('16x9', e.target.checked)} />
                <span className="block text-sm font-medium text-white">16:9 · Video ngang</span>
                <span className="mt-1 block text-xs leading-5 text-slate-400">Bản ngang tiêu chuẩn.</span>
              </label>
              <label className={`cursor-pointer rounded-xl border p-3 transition-colors ${style.renderTargets?.['9x16'] ? 'border-accent-400 bg-accent-500/10' : 'border-surface-600 hover:border-surface-400'}`}>
                <input className="sr-only" type="checkbox" checked={Boolean(style.renderTargets?.['9x16'])} onChange={e => setRenderTarget('9x16', e.target.checked)} />
                <span className="block text-sm font-medium text-white">9:16 · Video dọc</span>
                <span className="mt-1 block text-xs leading-5 text-slate-400">Dành cho Shorts, Reels và TikTok.</span>
              </label>
            </div>

            {style.renderTargets?.['9x16'] && (
              <div className="grid gap-2 sm:grid-cols-2">
                <label className={`cursor-pointer rounded-xl border p-3 transition-colors ${style.verticalVideoMode !== 'fit' ? 'border-accent-400 bg-accent-500/10' : 'border-surface-600 hover:border-surface-400'}`}>
                  <input className="sr-only" type="radio" name="vertical-video-mode" value="crop" checked={style.verticalVideoMode !== 'fit'} onChange={() => update({ verticalVideoMode: 'crop' })} />
                  <span className="block text-sm font-medium text-white">Phóng to, cắt khung</span>
                  <span className="mt-1 block text-xs leading-5 text-slate-400">Lấp đầy toàn bộ khung dọc như hiện tại.</span>
                </label>
                <label className={`cursor-pointer rounded-xl border p-3 transition-colors ${style.verticalVideoMode === 'fit' ? 'border-accent-400 bg-accent-500/10' : 'border-surface-600 hover:border-surface-400'}`}>
                  <input className="sr-only" type="radio" name="vertical-video-mode" value="fit" checked={style.verticalVideoMode === 'fit'} onChange={() => update({ verticalVideoMode: 'fit' })} />
                  <span className="block text-sm font-medium text-white">Giữ khung ngang</span>
                  <span className="mt-1 block text-xs leading-5 text-slate-400">Giữ trọn video ngang trong canvas dọc, có viền đen trên và dưới.</span>
                </label>
              </div>
            )}
          </div>

          <div className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-4">
            <div className="flex items-start justify-between gap-4">
              <div className="flex items-start gap-3">
                <div className="mt-0.5 rounded-lg bg-accent-500/10 p-2 text-accent-300">
                  <Music2 size={17} />
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-white">Nhạc nền</h3>
                  <p className="mt-1 text-xs leading-5 text-slate-400">Tùy chọn. Nhạc sẽ tự lặp đến hết video và được trộn cùng phần thuyết minh khi render.</p>
                </div>
              </div>
              <label className="inline-flex shrink-0 items-center gap-2 text-sm text-slate-200">
                <input
                  type="checkbox"
                  checked={Boolean(style.backgroundMusicEnabled)}
                  onChange={e => update({ backgroundMusicEnabled: e.target.checked })}
                />
                Bật nhạc nền
              </label>
            </div>

            {style.backgroundMusicEnabled && (
              <div className="grid grid-cols-1 gap-4 border-t border-surface-700 pt-4 md:grid-cols-[minmax(0,1fr)_18rem] md:items-end">
                <div className="space-y-2">
                  <input
                    ref={musicFileRef}
                    type="file"
                    accept="audio/mpeg,audio/wav,audio/x-wav,audio/mp4,audio/aac,audio/ogg,audio/flac,audio/opus,.mp3,.wav,.m4a,.aac,.ogg,.flac,.opus"
                    className="hidden"
                    onChange={e => {
                      handleMusicUpload(e.target.files?.[0])
                      e.target.value = ''
                    }}
                  />
                  <button
                    type="button"
                    onClick={() => musicFileRef.current?.click()}
                    disabled={musicUploading}
                    className="inline-flex items-center gap-2 rounded-lg border border-surface-500 bg-surface-950 px-3 py-2 text-sm font-medium text-slate-200 transition-colors hover:border-accent-400 hover:text-white disabled:cursor-wait disabled:opacity-50"
                  >
                    {musicUploading ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}
                    {musicUploading ? 'Đang tải nhạc...' : style.backgroundMusicFile ? 'Đổi nhạc nền' : 'Chọn nhạc nền'}
                  </button>
                  {style.backgroundMusicFile ? (
                    <p className="truncate text-xs text-emerald-300" title={style.backgroundMusicFile}>Đã chọn: {style.backgroundMusicFile}</p>
                  ) : (
                    <p className="text-xs text-amber-300">Chọn một file nhạc để bật khi render.</p>
                  )}
                </div>

                <label className="block space-y-2">
                  <span className="flex items-center justify-between text-xs text-slate-400">
                    Âm lượng nhạc nền
                    <strong className="font-mono text-white">{musicVolume}%</strong>
                  </span>
                  <input
                    type="range"
                    min="0"
                    max="100"
                    step="1"
                    value={musicVolume}
                    onChange={e => update({ backgroundMusicVolume: Number(e.target.value) })}
                    className="w-full accent-accent-500"
                  />
                </label>
              </div>
            )}

            {musicEnabled && (
              <p className="text-xs text-slate-500">Bấm Play ở video preview để nghe nhạc nền đã đồng bộ và cân chỉnh âm lượng trước khi xuất.</p>
            )}
          </div>

          {rendering && (
            <div className="rounded-2xl border border-surface-600 bg-surface-900/60 p-4 space-y-2">
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-400">{status}</span>
                <span className="font-mono text-white">{percent}%</span>
              </div>
              {hardwareMode && (
                <p className="text-[11px] text-slate-500">
                  {hardwareMode === 'cuda_hybrid'
                    ? 'GPU: CUDA decode + NVENC encode · phụ đề giữ CPU để đúng bố cục'
                    : hardwareMode === 'nvenc_standard'
                      ? 'GPU: NVENC encode · phụ đề/scale chạy CPU'
                      : 'CPU libx264 fallback · kiểm tra log nếu cần'}
                </p>
              )}
              <div className="h-2 bg-surface-700 rounded-full overflow-hidden">
                <div className="h-full bg-accent-500 transition-all" style={{ width: `${percent}%` }} />
              </div>
            </div>
          )}

          {error && (
            <div className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
              <AlertCircle size={15} className="mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {outputs && (
            <div className="rounded-2xl border border-green-500/30 bg-green-500/10 p-4 space-y-3">
              <div className="flex items-center gap-2 text-sm font-semibold text-green-300">
                <CheckCircle2 size={16} />
                Đã xuất video {subtitleRequested ? 'có phụ đề' : 'không phụ đề'}
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {Object.entries(outputs).map(([key, filename]) => (
                  <a key={key} href={outputVideoUrl(project, filename)} download={filename} className="inline-flex items-center justify-center gap-2 rounded-xl border border-green-500/30 px-4 py-2 text-sm font-semibold text-green-200 hover:text-white">
                    <Download size={15} />
                    Tải {key}
                  </a>
                ))}
              </div>
            </div>
          )}

          <button
            onClick={render}
            disabled={rendering || (subtitleRequested && !timing?.captions?.length) || !selectedRenderModes.length || (musicRequested && !musicEnabled)}
            className="w-full inline-flex items-center justify-center gap-2 rounded-xl bg-accent-600 hover:bg-accent-500 disabled:opacity-40 disabled:cursor-not-allowed px-4 py-3 text-sm font-semibold text-white transition-colors"
          >
            {rendering ? <Loader2 size={16} className="animate-spin" /> : musicRequested ? <Music2 size={16} /> : <Subtitles size={16} />}
            {!selectedRenderModes.length
              ? 'Chọn ít nhất một đầu ra'
              : musicRequested
                ? (musicEnabled ? `Render ${subtitleRequested ? 'phụ đề' : 'video không phụ đề'} + nhạc nền ${selectedRenderLabel}` : 'Chọn nhạc nền để render')
                : `Render ${subtitleRequested ? 'phụ đề' : 'video không phụ đề'} ${selectedRenderLabel}`}
          </button>
        </>
      )}
    </div>
  )
}
