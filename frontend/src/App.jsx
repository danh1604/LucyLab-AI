import { useState, useEffect, useCallback, useRef } from 'react'
import { ArrowLeft, AudioLines, Download, FileText, Languages, Mic, PenLine, Scissors, Zap } from 'lucide-react'
import ConfigPanel from './components/ConfigPanel.jsx'
import StorySessionsPanel from './components/StorySessionsPanel.jsx'
import PipelineNav from './components/PipelineNav.jsx'
import PipelineLanguageStatus from './components/PipelineLanguageStatus.jsx'
import StoryStart from './components/StoryStart.jsx'
import StoryInterview from './components/StoryInterview.jsx'
import StoryGenerator from './components/StoryGenerator.jsx'
import StoryEditor from './components/StoryEditor.jsx'
import LocalizationReview from './components/LocalizationReview.jsx'
import AudioConfig from './components/AudioConfig.jsx'
import AudioReview from './components/AudioReview.jsx'
import ProgressPanel from './components/ProgressPanel.jsx'
import VideoUpload from './components/VideoUpload.jsx'
import VideoCompose from './components/VideoCompose.jsx'
import VideoCutterTool from './components/VideoCutterTool.jsx'
import AudioCutterTool from './components/AudioCutterTool.jsx'
import SubtitleConfig from './components/SubtitleConfig.jsx'
import PublishTool from './components/PublishTool.jsx'
import OutputsPanel from './components/OutputsPanel.jsx'
import {
  startGenerate,
  getOutputs,
  getAudioDuration,
  createStorySession,
  getStorySession,
  updateStorySession,
  outputVideoUrl,
  getProjects,
  localizeStory,
  extractStickFigureScript,
} from './api.js'

const DEFAULT_LANGUAGE = { source_locale: 'auto', target_locale: 'vi-VN', review_before_tts: true }
const INITIAL_STORY = { prompt: '', model: 'claude-sonnet-4-6', missing: [], interviewHistory: [], text: '', language: DEFAULT_LANGUAGE }
const INITIAL_JOB = { total: 0, completed: 0, segments: [], done: false, merging: false, normalizingNumbers: false, numberReplacementCount: 0, success: false, outputFile: null, sizeMb: 0, successCount: 0, errorCount: 0 }
const INITIAL_VIDEO_SETTINGS = {
  outputName: 'final_video.mp4', videoEncoder: 'nvidia_nvenc', quality: 'high', overwriteMode: 'version', videoMode: 'background',
  sceneReveal: { enabled: false, effect: 'brush', durationSec: 1.2, pattern: 'auto', actor: 'none' },
}
const DEFAULT_SUBTITLE_FONT = 'JetBrains Mono NL SemiBold'
const INITIAL_SUBTITLE_SETTINGS = { fontFamily: DEFAULT_SUBTITLE_FONT, fontSize16x9: 54, fontSize9x16: 72, textColor: '#ffffff', outlineColor: '#000000', outlineWidth: 3, backgroundEnabled: false, backgroundColor: '#000000', backgroundOpacity: 0.55, bold: true, align: 'center', position: 'bottom', marginX: 90, marginY: 150, maxWidthPct: 82, lineHeight: 1.12, maxCharsPerLine16x9: 42, maxLines16x9: 2, maxCueChars16x9: 110, maxCueWords16x9: 24, maxCharsPerLine9x16: 24, maxLines9x16: 2, maxCueChars9x16: 60, maxCueWords9x16: 18, subtitleLayout16x9: { x: 50, y: 86, widthPct: 82, locked: false }, subtitleLayout9x16: { x: 50, y: 86, widthPct: 82, locked: false }, burnSubtitles: true, renderTargets: { '16x9': true, '9x16': true }, verticalVideoMode: 'crop' }

function cleanProjectName(value) {
  return (value || '').replace(/[<>:"/\\|?*\x00-\x1f]+/g, ' ').replace(/\s+/g, ' ').trim()
}

function cleanFileStem(value) {
  return cleanProjectName(value).replace(/[^\w-]+/g, '_').replace(/^_+|_+$/g, '') || `truyen_${new Date().toISOString().slice(0, 10)}`
}

function isProbablyVietnamese(value) {
  const sample = String(value || '').slice(0, 4000)
  const letters = sample.match(/[A-Za-zÀ-ỹĐđ]/g) || []
  const marked = sample.match(/[À-ỹĐđ]/g) || []
  return letters.length >= 20 && marked.length >= Math.max(3, Math.floor(letters.length * 0.012))
}

function isActiveLocalizedStory(story, language) {
  const localization = story?.localization
  const currentText = String(story?.text || '').replace(/\s+/g, ' ').trim()
  const storedLocalizedText = String(localization?.text || '').replace(/\s+/g, ' ').trim()
  return Boolean(
    localization?.target_locale &&
    localization.target_locale === language?.target_locale &&
    (
      story?.narration_locale === language.target_locale ||
      (currentText && currentText === storedLocalizedText)
    )
  )
}

export default function App() {
  const [toolOpen, setToolOpen] = useState('')
  // Pipeline state
  const [stage, setStage] = useState('STORY_START')
  const [storyTitle, setStoryTitle] = useState('')
  const [storyData, setStoryData] = useState(INITIAL_STORY)
  const [approvedStory, setApprovedStory] = useState('')
  const [localization, setLocalization] = useState(null)
  const [localizeProgress, setLocalizeProgress] = useState('')
  const [currentSessionId, setCurrentSessionId] = useState(null)
  const [sessionsRefresh, setSessionsRefresh] = useState(0)
  const currentSessionIdRef = useRef(null)
  const saveTimerRef = useRef(null)

  // Audio state (existing TTS pipeline)
  const [job, setJob] = useState(null)
  const [running, setRunning] = useState(false)
  const [outputs, setOutputs] = useState([])
  const [stopFn, setStopFn] = useState(null)
  const [currentProject, setCurrentProject] = useState('')
  const [audioFile, setAudioFile] = useState(null)
  const [audioDuration, setAudioDuration] = useState(0)

  // Video state
  const [videoOrder, setVideoOrder] = useState([])
  const [videoSettings, setVideoSettings] = useState(INITIAL_VIDEO_SETTINGS)
  const [outputVideoFile, setOutputVideoFile] = useState(null)
  const [subtitleSettings, setSubtitleSettings] = useState(INITIAL_SUBTITLE_SETTINGS)
  const [subtitleOutputs, setSubtitleOutputs] = useState({})

  const loadOutputs = useCallback(() => {
    getOutputs(currentProject).then(setOutputs).catch(() => {})
  }, [currentProject])

  useEffect(() => { loadOutputs() }, [loadOutputs])

  function sessionPayload(next = {}) {
    const nextStage = next.stage ?? stage
    const nextStoryData = next.storyData ?? storyData
    const nextApprovedStory = next.approvedStory ?? approvedStory
    const nextStoryTitle = next.storyTitle ?? storyTitle ?? nextStoryData.storyTitle ?? ''
    return {
      title: (nextStoryTitle || nextStoryData.prompt || nextApprovedStory || 'Truyện mới').slice(0, 80),
      story_title: nextStoryTitle,
      stage: nextStage,
      story_data: { ...nextStoryData, storyTitle: nextStoryTitle },
      approved_story: nextApprovedStory,
      current_project: next.currentProject ?? currentProject,
      audio_file: next.audioFile ?? audioFile,
      audio_duration: next.audioDuration ?? audioDuration,
      video_order: next.videoOrder ?? videoOrder,
      video_settings: next.videoSettings ?? videoSettings,
      output_video_file: Object.prototype.hasOwnProperty.call(next, 'outputVideoFile') ? next.outputVideoFile : outputVideoFile,
      subtitle_settings: next.subtitleSettings ?? subtitleSettings,
      subtitle_outputs: next.subtitleOutputs ?? subtitleOutputs,
    }
  }

  async function ensureSession(next = {}) {
    if (currentSessionIdRef.current) return currentSessionIdRef.current
    const created = await createStorySession(sessionPayload(next))
    currentSessionIdRef.current = created.id
    setCurrentSessionId(created.id)
    setSessionsRefresh(v => v + 1)
    return created.id
  }

  async function saveSession(next = {}) {
    const id = next.sessionId || currentSessionIdRef.current || await ensureSession(next)
    await updateStorySession(id, sessionPayload(next))
    setSessionsRefresh(v => v + 1)
  }

  function saveSessionSoon(next = {}) {
    clearTimeout(saveTimerRef.current)
    // A debounced save belongs to the session that was active when the user
    // typed.  Never let it write its old state into a session opened later.
    const scheduledSessionId = currentSessionIdRef.current
    saveTimerRef.current = setTimeout(() => {
      if (scheduledSessionId !== currentSessionIdRef.current) return
      saveSession({ ...next, ...(scheduledSessionId ? { sessionId: scheduledSessionId } : {}) }).catch(() => {})
    }, 800)
  }

  async function openSession(id) {
    clearTimeout(saveTimerRef.current)
    const s = await getStorySession(id)
    currentSessionIdRef.current = s.id
    setCurrentSessionId(s.id)
    const loadedStoryData = { ...INITIAL_STORY, ...(s.story_data || {}) }
    const loadedLanguage = { ...DEFAULT_LANGUAGE, ...(loadedStoryData.language || {}) }
    const recoveredLocalizedStory = isActiveLocalizedStory(loadedStoryData, loadedLanguage) && !loadedStoryData.narration_locale
      ? { ...loadedStoryData, narration_locale: loadedLanguage.target_locale }
      : loadedStoryData
    const loadedTitle = s.story_title || recoveredLocalizedStory.storyTitle || s.title || ''
    setStoryTitle(loadedTitle)
    setStoryData(recoveredLocalizedStory)
    setApprovedStory(s.approved_story || '')
    setLocalization(recoveredLocalizedStory.localization || null)
    setCurrentProject(s.current_project || '')
    setAudioFile(s.audio_file || null)
    setAudioDuration(s.audio_duration || 0)
    setVideoOrder(s.video_order || [])
    setVideoSettings({ ...INITIAL_VIDEO_SETTINGS, ...(s.video_settings || {}) })
    setOutputVideoFile(s.output_video_file || null)
    const savedSubtitleSettings = s.subtitle_settings || {}
    const shouldUpgradeSubtitleDensity = (
      (Number(savedSubtitleSettings.maxCueChars16x9) === 84 && Number(savedSubtitleSettings.maxCueWords16x9) === 16) ||
      (Number(savedSubtitleSettings.maxCueChars16x9) === 96 && Number(savedSubtitleSettings.maxCueWords16x9) === 24)
    )
    setSubtitleSettings({
      ...INITIAL_SUBTITLE_SETTINGS,
      ...savedSubtitleSettings,
      ...(shouldUpgradeSubtitleDensity ? { maxCueChars16x9: 110, maxCueWords16x9: 24 } : {}),
    })
    setSubtitleOutputs(s.subtitle_outputs || {})
    setJob(null)
    setRunning(false)
    // Localizing is a transient browser request and cannot resume after a
    // reload. Recover failed/interrupted sessions into the editable stage.
    const restoredStage = s.stage === 'LOCALIZING' ? 'STORY_EDITING' : (s.stage || 'STORY_START')
    setStage(restoredStage)
    if (restoredStage !== s.stage || recoveredLocalizedStory !== loadedStoryData) {
      updateStorySession(s.id, {
        stage: restoredStage,
        story_title: loadedTitle,
        story_data: { ...recoveredLocalizedStory, storyTitle: loadedTitle },
      }).catch(() => {})
    }
  }

  function newSession() {
    clearTimeout(saveTimerRef.current)
    currentSessionIdRef.current = null
    setCurrentSessionId(null)
    setStoryTitle('')
    setStage('STORY_START')
    setStoryData(INITIAL_STORY)
    setApprovedStory('')
    setLocalization(null)
    setLocalizeProgress('')
    setJob(null)
    setRunning(false)
    setCurrentProject('')
    setAudioFile(null)
    setAudioDuration(0)
    setVideoOrder([])
    setVideoSettings(INITIAL_VIDEO_SETTINGS)
    setOutputVideoFile(null)
    setSubtitleSettings(INITIAL_SUBTITLE_SETTINGS)
    setSubtitleOutputs({})
  }

  // ── Story handlers ────────────────────────────────────────────────────────────

  async function handleManualEdit(title) {
    const nextTitle = cleanProjectName(title)
    setStoryTitle(nextTitle)
    const nextStory = { ...INITIAL_STORY, storyTitle: nextTitle, language: { ...DEFAULT_LANGUAGE, ...(storyData.language || {}) } }
    setStoryData(nextStory)
    await ensureSession({ stage: 'STORY_EDITING', storyData: nextStory, storyTitle: nextTitle })
    setStage('STORY_EDITING')
  }

  async function handlePromptStarted(title, prompt, model) {
    const nextTitle = cleanProjectName(title)
    setStoryTitle(nextTitle)
    const nextStory = { ...storyData, storyTitle: nextTitle, prompt, model }
    setStoryData(nextStory)
    const sessionId = await ensureSession({ stage: 'STORY_START', storyData: nextStory, storyTitle: nextTitle })
    saveSession({ sessionId, stage: 'STORY_START', storyData: nextStory, storyTitle: nextTitle }).catch(() => {})
  }

  async function handleAIReady(title, prompt, model, missing) {
    const nextTitle = cleanProjectName(title)
    setStoryTitle(nextTitle)
    const nextStory = { ...storyData, storyTitle: nextTitle, prompt, model, missing }
    setStoryData(nextStory)
    const sessionId = await ensureSession({ stage: 'STORY_INTERVIEW', storyData: nextStory, storyTitle: nextTitle })
    setStage('STORY_INTERVIEW')
    saveSession({ sessionId, stage: 'STORY_INTERVIEW', storyData: nextStory, storyTitle: nextTitle }).catch(() => {})
  }

  async function handleDirectGenerate(title, prompt, model) {
    const nextTitle = cleanProjectName(title)
    setStoryTitle(nextTitle)
    const nextStory = { ...storyData, storyTitle: nextTitle, prompt, model, interviewHistory: [] }
    setStoryData(nextStory)
    const sessionId = await ensureSession({ stage: 'STORY_GENERATING', storyData: nextStory, storyTitle: nextTitle })
    setStage('STORY_GENERATING')
    saveSession({ sessionId, stage: 'STORY_GENERATING', storyData: nextStory, storyTitle: nextTitle }).catch(() => {})
  }

  async function handleStickFigureReady(title, blueprint, model) {
    const nextTitle = cleanProjectName(title)
    const narration = String(blueprint?.tts_text || '').trim()
    if (!narration) throw new Error('Blueprint người que không có lời đọc TTS.')
    setStoryTitle(nextTitle)
    const nextStory = {
      ...INITIAL_STORY,
      storyTitle: nextTitle,
      model,
      text: narration,
      language: { ...DEFAULT_LANGUAGE, ...(storyData.language || {}) },
      source_script: String(blueprint?.source_script || ''),
      content_mode: 'stick_figure',
      stick_figure: { ...blueprint, tts_text: narration },
    }
    setStoryData(nextStory)
    const sessionId = await ensureSession({ stage: 'STORY_EDITING', storyData: nextStory, storyTitle: nextTitle })
    setStage('STORY_EDITING')
    saveSession({ sessionId, stage: 'STORY_EDITING', storyData: nextStory, storyTitle: nextTitle }).catch(() => {})
  }

  async function handleRefilterStickFigure() {
    const sourceScript = String(storyData.source_script || storyData.stick_figure?.source_script || '').trim()
    if (sourceScript.length < 80) {
      throw new Error('Không tìm thấy kịch bản thô ban đầu để lọc lại.')
    }
    const model = storyData.model || 'claude-sonnet-4-6'
    const blueprint = await extractStickFigureScript(sourceScript, model)
    const narration = String(blueprint?.tts_text || '').trim()
    if (!narration || !blueprint?.narration_units?.length) {
      throw new Error('AI chưa tách được lời đọc thành các đơn vị narration. Hãy thử lại bằng model khác.')
    }

    const nextStory = {
      ...INITIAL_STORY,
      storyTitle,
      source_title: storyData.source_title || storyTitle,
      model,
      text: narration,
      language: { ...DEFAULT_LANGUAGE, ...(storyData.language || {}) },
      source_script: sourceScript,
      content_mode: 'stick_figure',
      stick_figure: { ...blueprint, tts_text: narration },
    }
    setStoryData(nextStory)
    setApprovedStory('')
    setLocalization(null)
    setJob(null)
    setRunning(false)
    setCurrentProject('')
    setAudioFile(null)
    setAudioDuration(0)
    setVideoOrder([])
    setVideoSettings(INITIAL_VIDEO_SETTINGS)
    setOutputVideoFile(null)
    setSubtitleOutputs({})
    setStage('STORY_EDITING')
    await saveSession({
      stage: 'STORY_EDITING',
      storyData: nextStory,
      approvedStory: '',
      currentProject: '',
      audioFile: null,
      audioDuration: 0,
      videoOrder: [],
      videoSettings: INITIAL_VIDEO_SETTINGS,
      outputVideoFile: null,
      subtitleOutputs: {},
    })
  }

  function handleInterviewDone(history) {
    const nextStory = { ...storyData, interviewHistory: history }
    setStoryData(nextStory)
    setStage('STORY_GENERATING')
    saveSession({ stage: 'STORY_GENERATING', storyData: nextStory }).catch(() => {})
  }

  function handleStoryGenDone(text) {
    const nextStory = { ...storyData, text }
    setStoryData(nextStory)
    setStage('STORY_EDITING')
    saveSession({ stage: 'STORY_EDITING', storyData: nextStory }).catch(() => {})
  }

  function commitLocalization(result, sourceStory) {
    const localizedText = String(result?.text || '').trim()
    const localizedTitle = cleanProjectName(result?.title || sourceStory.source_title || storyTitle)
    const nextStory = {
      ...sourceStory,
      text: localizedText,
      localization: result,
      narration_locale: result?.target_locale || sourceStory.language?.target_locale || '',
      language: { ...(sourceStory.language || DEFAULT_LANGUAGE), source_locale: result?.source_locale || sourceStory.language?.source_locale, detected_source_locale: result?.source_locale || '', target_locale: result?.target_locale || sourceStory.language?.target_locale },
      stick_figure: result?.stick_figure || sourceStory.stick_figure,
    }
    setStoryTitle(localizedTitle)
    setStoryData(nextStory)
    setLocalization(result)
    setApprovedStory(localizedText)
    setStage('AUDIO_CONFIG')
    saveSession({ stage: 'AUDIO_CONFIG', storyData: nextStory, approvedStory: localizedText, storyTitle: localizedTitle }).catch(() => {})
  }

  function handleApprove(text) {
    const existingStickBlueprint = storyData.content_mode === 'stick_figure' && storyData.stick_figure
      ? storyData.stick_figure
      : null
    // The final approved narration is the sole source for Visual Plan V2.
    // Discard units from the pre-edit draft so the backend derives fresh units
    // from exactly what will be sent to TTS.
    const { narration_units: _oldNarrationUnits, scenes: _oldScenes, ...freshStickBlueprint } = existingStickBlueprint || {}
    const language = { ...DEFAULT_LANGUAGE, ...(storyData.language || {}) }
    const baseStory = {
      ...storyData,
      text,
      source_title: storyData.source_title || storyTitle,
      stick_figure: existingStickBlueprint
        ? { ...freshStickBlueprint, tts_text: text }
        : storyData.stick_figure,
    }

    // Returning to Nội dung from a localized pipeline opens the published
    // locale text (for example Korean), not the original Vietnamese. Keep its
    // source metadata intact and send the edited localized version straight to
    // audio instead of translating the same language a second time.
    if (isActiveLocalizedStory(storyData, language)) {
      const refreshedLocalization = {
        ...storyData.localization,
        text,
        title: storyData.localization?.title || storyTitle,
        target_locale: language.target_locale,
        stick_figure: baseStory.stick_figure || storyData.localization?.stick_figure || null,
      }
      const localizedStory = {
        ...baseStory,
        source_text: storyData.source_text || '',
        localization: refreshedLocalization,
        narration_locale: language.target_locale,
      }
      setStoryData(localizedStory)
      setLocalization(refreshedLocalization)
      setApprovedStory(text)
      setStage('AUDIO_CONFIG')
      saveSession({ stage: 'AUDIO_CONFIG', storyData: localizedStory, approvedStory: text }).catch(() => {})
      return
    }

    const nextStory = { ...baseStory, source_text: text }
    const sameExplicitLocale = language.source_locale !== 'auto'
      && language.source_locale === language.target_locale
    const isVietnameseAutoFlow = language.source_locale === 'auto'
      && language.target_locale === 'vi-VN'
      && isProbablyVietnamese(text)
    if (sameExplicitLocale || isVietnameseAutoFlow) {
      // Identical explicitly selected locales do not need detection,
      // translation, or language QA. Vietnamese auto-detect retains the
      // established local fast path.
      const identityLocale = sameExplicitLocale ? language.target_locale : 'vi-VN'
      const legacyStory = {
        ...nextStory,
        localization: null,
        narration_locale: identityLocale,
        language: { ...language, source_locale: identityLocale, detected_source_locale: identityLocale },
      }
      setStoryData(legacyStory)
      setLocalization(null)
      setApprovedStory(text)
      setStage('AUDIO_CONFIG')
      saveSession({ stage: 'AUDIO_CONFIG', storyData: legacyStory, approvedStory: text }).catch(() => {})
      return
    }
    setStage('LOCALIZING')
    setLocalizeProgress('Đang nhận diện ngôn ngữ…')
    saveSession({ stage: 'LOCALIZING', storyData: nextStory }).catch(() => {})
    localizeStory({ source_locale: language.source_locale, target_locale: language.target_locale, title: nextStory.source_title, text, stick_figure: nextStory.stick_figure || null }, event => {
      if (event.type === 'phase') setLocalizeProgress(event.phase === 'detecting' ? 'Đang nhận diện ngôn ngữ…' : event.phase === 'qa' ? 'Đang tự kiểm bản localized…' : 'Đang localize nội dung…')
      if (event.type === 'error') {
        setStage('STORY_EDITING')
        setLocalizeProgress('')
        saveSession({ stage: 'STORY_EDITING', storyData: nextStory }).catch(() => {})
        alert(event.error || 'Language Agent thất bại')
      }
      if (event.type === 'complete') {
        setLocalizeProgress('')
        if (language.review_before_tts) {
          const prepared = { ...event.result, source_title: nextStory.source_title, source_text: text }
          setLocalization(prepared)
          setStoryData({ ...nextStory, localization: prepared })
          setStage('LOCALIZATION_REVIEW')
          saveSession({ stage: 'LOCALIZATION_REVIEW', storyData: { ...nextStory, localization: prepared } }).catch(() => {})
        } else commitLocalization(event.result, nextStory)
      }
    })
  }

  function handleRewrite() {
    setStage('STORY_START')
    saveSession({ stage: 'STORY_START' }).catch(() => {})
  }

  // ── Audio handlers ────────────────────────────────────────────────────────────

  async function uniqueProjectName(baseName) {
    const base = cleanProjectName(baseName) || cleanFileStem(baseName)
    const projects = await getProjects().catch(() => [])
    if (!projects.includes(base) || currentProject === base) return base
    let i = 2
    while (projects.includes(`${base} - ${i}`)) i += 1
    return `${base} - ${i}`
  }

  async function handleAudioStart(text, outputName) {
    if (running) return
    const outputLocale = storyData.language?.target_locale || 'vi-VN'
    const isVietnameseLegacyFlow = storyData.language?.source_locale === 'vi-VN' && outputLocale === 'vi-VN'
    const projectBase = isVietnameseLegacyFlow ? (storyTitle || outputName) : `${storyTitle || outputName} - ${outputLocale}`
    const project = currentProject || await uniqueProjectName(projectBase)
    const audioOutput = outputName || cleanFileStem(storyTitle || project)
    setCurrentProject(project)
    setJob({ ...INITIAL_JOB })
    setRunning(true)
    setStage('AUDIO_GENERATING')
    saveSession({ stage: 'AUDIO_GENERATING', currentProject: project, approvedStory: text, storyTitle: storyTitle || project }).catch(() => {})

    const stop = startGenerate(
      text,
      audioOutput,
      (ev) => {
        setJob(prev => {
          if (!prev) return prev
          if (ev.type === 'normalizing_numbers') return {
            ...prev,
            normalizingNumbers: ev.phase === 'start',
            numberReplacementCount: ev.phase === 'done' ? (ev.replacement_count || 0) : prev.numberReplacementCount,
          }
          if (ev.type === 'start') return { ...prev, total: ev.total }
          if (ev.type === 'progress') {
            const newSeg = { index: ev.index, status: ev.success ? 'ok' : 'error', preview: ev.text_preview || ev.error || '', filename: ev.filename || '', error: ev.error || null }
            const segs = [...prev.segments.filter(s => s.index !== ev.index), newSeg].sort((a, b) => a.index - b.index)
            return { ...prev, segments: segs, completed: ev.completed, successCount: segs.filter(s => s.status === 'ok').length, errorCount: segs.filter(s => s.status === 'error').length }
          }
          if (ev.type === 'error') return { ...prev, normalizingNumbers: false, error: ev.error, completed: ev.completed }
          if (ev.type === 'merging') return { ...prev, merging: true }
          if (ev.type === 'done') return { ...prev, done: true, merging: false, normalizingNumbers: false, success: ev.success, outputFile: ev.output_file || null, sizeMb: ev.size_mb || 0, successCount: ev.results || prev.successCount, errorCount: ev.errors || prev.errorCount, error: ev.reason || prev.error || null }
          return prev
        })
      },
      () => {
        setRunning(false)
        loadOutputs()
      },
      (err) => {
        setJob(prev => prev ? { ...prev, done: true, success: false, error: err } : prev)
        setRunning(false)
      },
      project,
      outputLocale,
    )
    setStopFn(() => stop)
  }

  function handleAudioDone(filename, durationSec) {
    setAudioFile(filename)
    setAudioDuration(durationSec)
    setStage('AUDIO_REVIEW')
    saveSession({ stage: 'AUDIO_REVIEW', audioFile: filename, audioDuration: durationSec }).catch(() => {})
  }

  function handleStop() {
    stopFn?.()
    setRunning(false)
    setJob(prev => prev ? { ...prev, done: true, success: false, error: 'Đã dừng bởi người dùng' } : prev)
  }

  // A completed TTS job stops at review so the creator can hear it before video.
  useEffect(() => {
    if (stage === 'AUDIO_GENERATING' && job?.done && job?.success && job?.outputFile) {
      setAudioFile(job.outputFile)
      // Fetch real duration before saving the review state.
      getAudioDuration(currentProject, job.outputFile)
        .then(r => Number(r.duration_sec) || 0)
        .catch(() => 0)
        .then(durationSec => {
          setAudioDuration(durationSec)
          setStage('AUDIO_REVIEW')
          saveSession({ stage: 'AUDIO_REVIEW', audioFile: job.outputFile, audioDuration: durationSec }).catch(() => {})
        })
    }
  }, [job?.done, job?.success, job?.outputFile, stage, currentProject])

  // ── Video handlers ────────────────────────────────────────────────────────────

  function handleCompose(order, settings) {
    setVideoOrder(order)
    setVideoSettings(settings)
    setOutputVideoFile(null)
    setSubtitleOutputs({})
    setStage('VIDEO_COMPOSING')
    saveSession({ stage: 'VIDEO_COMPOSING', videoOrder: order, videoSettings: settings, outputVideoFile: null, subtitleOutputs: {} }).catch(() => {})
  }

  // ── Render ────────────────────────────────────────────────────────────────────

  const visibleStoryText = storyData.text || approvedStory || ''
  const canOpenStage = {
    STORY: Boolean(visibleStoryText.trim() || storyData.prompt || storyData.interviewHistory?.length),
    AUDIO: Boolean(visibleStoryText.trim()),
    VIDEO: Boolean(audioFile || currentProject),
    SUBTITLE: Boolean(outputVideoFile),
    DONE: Boolean(outputVideoFile || stage === 'DONE'),
  }

  function handlePipelineOpen(group) {
    if (group === 'STORY' && canOpenStage.STORY) {
      const language = { ...DEFAULT_LANGUAGE, ...(storyData.language || {}) }
      const editorStory = { ...storyData, text: visibleStoryText }
      // Legacy sessions created before narration_locale existed can still be
      // recognized here while their current text exactly equals localization.
      // Persist the marker before the editor receives user changes.
      const nextStory = isActiveLocalizedStory(editorStory, language)
        ? { ...editorStory, narration_locale: language.target_locale }
        : editorStory
      const nextStage = visibleStoryText.trim() ? 'STORY_EDITING' : 'STORY_START'
      setStoryData(nextStory)
      setStage(nextStage)
      saveSession({ stage: nextStage, storyData: nextStory }).catch(() => {})
    } else if (group === 'AUDIO' && canOpenStage.AUDIO) {
      const nextStage = audioFile ? 'AUDIO_REVIEW' : 'AUDIO_CONFIG'
      setStage(nextStage)
      saveSession({ stage: nextStage }).catch(() => {})
    } else if (group === 'VIDEO' && canOpenStage.VIDEO) {
      setStage('VIDEO_UPLOAD')
      saveSession({ stage: 'VIDEO_UPLOAD' }).catch(() => {})
    } else if (group === 'SUBTITLE' && canOpenStage.SUBTITLE) {
      setStage('SUBTITLE_CONFIG')
      saveSession({ stage: 'SUBTITLE_CONFIG' }).catch(() => {})
    } else if (group === 'DONE' && canOpenStage.DONE) {
      setStage('DONE')
      saveSession({ stage: 'DONE' }).catch(() => {})
    }
  }

  return (
    <div className="min-h-screen bg-surface-900">
      <div className="app-atmosphere fixed inset-0 pointer-events-none" aria-hidden="true" />

      <div className="relative max-w-[100rem] mx-auto px-4 sm:px-6 py-8">
        <header className="max-w-3xl mx-auto text-center mb-10">
          <div className="inline-flex items-center gap-2 bg-accent-500/10 border border-accent-500/20 text-accent-400 text-xs font-medium px-3 py-1.5 rounded-full mb-4">
            <Zap size={11} />
            Audio Story Pipeline
          </div>
          <h1 className="text-3xl font-bold text-white tracking-tight mb-2 flex items-center justify-center gap-3">
            <Mic size={28} className="text-accent-400" />
            LucyLab Story Studio
          </h1>
          <p className="text-slate-500 text-sm">AI viết truyện · TTS · Ghép video · Xuất hoàn chỉnh</p>
          <div className="mt-4 flex flex-wrap justify-center gap-2">
            <button
              onClick={() => setToolOpen(current => current === 'video' ? '' : 'video')}
              className="inline-flex items-center gap-2 rounded-xl border border-accent-500/30 bg-accent-500/10 px-4 py-2 text-sm font-semibold text-accent-300 hover:bg-accent-500/20 hover:text-white transition-colors"
            >
              {toolOpen === 'video' ? <ArrowLeft size={15} /> : <Scissors size={15} />}
              {toolOpen === 'video' ? 'Quay lại Studio' : 'Tool cắt video'}
            </button>
            <button
              onClick={() => setToolOpen(current => current === 'audio' ? '' : 'audio')}
              className="inline-flex items-center gap-2 rounded-xl border border-sky-400/30 bg-sky-500/10 px-4 py-2 text-sm font-semibold text-sky-200 hover:bg-sky-500/20 hover:text-white transition-colors"
            >
              {toolOpen === 'audio' ? <ArrowLeft size={15} /> : <AudioLines size={15} />}
              {toolOpen === 'audio' ? 'Quay lại Studio' : 'Tool cắt audio'}
            </button>
          </div>
        </header>

        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_23rem] 2xl:grid-cols-[minmax(0,1fr)_25rem] gap-5 items-start">
          <main className="space-y-4 min-w-0">
          {toolOpen ? (
            <div className="glass rounded-2xl p-6">
              {toolOpen === 'video' ? <VideoCutterTool /> : <AudioCutterTool />}
            </div>
          ) : (
          <>
          <ConfigPanel onSaved={() => {}} />

          <PipelineNav stage={stage} canOpen={canOpenStage} onOpenStage={handlePipelineOpen} />
          <PipelineLanguageStatus language={storyData.language} />

          <div className="glass rounded-2xl p-6">
            {/* Story stages */}
            {stage === 'STORY_START' && (
              <StoryStart
                storyTitle={storyTitle}
                onStoryTitleChange={(value) => {
                  // Keep the controlled input exactly as typed.  Sanitising here
                  // trims a just-entered trailing space, making multi-word titles
                  // impossible to type.  Titles are normalised when a workflow is
                  // started and a project/session is created instead.
                  const nextTitle = value
                  setStoryTitle(nextTitle)
                  const nextStory = { ...storyData, storyTitle: nextTitle }
                  setStoryData(nextStory)
                  saveSessionSoon({ stage: 'STORY_START', storyData: nextStory, storyTitle: nextTitle })
                }}
                onManual={handleManualEdit}
                onPromptStarted={handlePromptStarted}
                onAIReady={handleAIReady}
                onDirectGenerate={handleDirectGenerate}
                onStickFigureReady={handleStickFigureReady}
                languageSettings={storyData.language || DEFAULT_LANGUAGE}
                onLanguageChange={(language) => {
                  const previousLanguage = { ...DEFAULT_LANGUAGE, ...(storyData.language || {}) }
                  const localeChanged = previousLanguage.source_locale !== language.source_locale || previousLanguage.target_locale !== language.target_locale
                  const shouldRestoreSource = localeChanged && isActiveLocalizedStory(storyData, previousLanguage)
                  const sourceText = storyData.source_text || storyData.text
                  const restoredTitle = storyData.source_title || storyTitle
                  const { narration_locale: _oldNarrationLocale, ...storyWithoutLocalizedMarker } = storyData
                  const nextStory = shouldRestoreSource
                    ? {
                        ...storyWithoutLocalizedMarker,
                        storyTitle: restoredTitle,
                        text: sourceText,
                        source_text: sourceText,
                        language,
                        localization: null,
                        stick_figure: storyData.stick_figure ? { ...storyData.stick_figure, tts_text: sourceText } : null,
                      }
                    : { ...storyData, language }
                  setStoryData(nextStory)
                  if (shouldRestoreSource) {
                    setStoryTitle(restoredTitle)
                    setLocalization(null)
                    setApprovedStory(sourceText)
                  }
                  saveSessionSoon({ stage: 'STORY_START', storyData: nextStory, storyTitle: shouldRestoreSource ? restoredTitle : storyTitle, approvedStory: shouldRestoreSource ? sourceText : approvedStory })
                }}
              />
            )}

            {stage === 'STORY_INTERVIEW' && (
              <StoryInterview
                prompt={storyData.prompt}
                model={storyData.model}
                missing={storyData.missing}
                initialHistory={storyData.interviewHistory}
                onHistoryChange={(history) => {
                  const nextStory = { ...storyData, interviewHistory: history }
                  setStoryData(nextStory)
                  saveSessionSoon({ stage: 'STORY_INTERVIEW', storyData: nextStory })
                }}
                onReady={handleInterviewDone}
              />
            )}

            {stage === 'STORY_GENERATING' && (
              <StoryGenerator
                prompt={storyData.prompt}
                interviewHistory={storyData.interviewHistory}
                model={storyData.model}
                initialText={storyData.text}
                onProgress={(text) => {
                  const nextStory = { ...storyData, text }
                  setStoryData(nextStory)
                  saveSessionSoon({ stage: 'STORY_GENERATING', storyData: nextStory })
                }}
                onDone={handleStoryGenDone}
              />
            )}

            {stage === 'STORY_EDITING' && (
              <StoryEditor
                initialText={storyData.text}
                storyTitle={storyTitle}
                storyPrompt={storyData.prompt || ''}
                model={storyData.model || ''}
                sessionId={currentSessionId}
                scriptReview={storyData.scriptReview || null}
                onApprove={handleApprove}
                onRefilterStickFigure={storyData.content_mode === 'stick_figure' && String(storyData.source_script || storyData.stick_figure?.source_script || '').trim().length >= 80
                  ? handleRefilterStickFigure
                  : null}
                onChange={(text) => {
                  const nextStory = { ...storyData, text }
                  setStoryData(nextStory)
                  saveSessionSoon({ stage: 'STORY_EDITING', storyData: nextStory })
                }}
                onReviewChange={(scriptReview) => {
                  const nextStory = { ...storyData, scriptReview }
                  setStoryData(nextStory)
                  saveSessionSoon({ stage: 'STORY_EDITING', storyData: nextStory })
                }}
                onAutoRunUpdate={(text, scriptReview) => {
                  const nextStory = { ...storyData, text, scriptReview }
                  setStoryData(nextStory)
                }}
                onRewrite={storyData.text ? handleRewrite : null}
              />
            )}

            {stage === 'LOCALIZING' && (
              <div className="flex min-h-48 flex-col items-center justify-center gap-3 text-center">
                <Languages className="animate-pulse text-sky-300" size={28} />
                <p className="text-sm font-semibold text-white">{localizeProgress || 'Đang chuẩn bị bản đa ngôn ngữ…'}</p>
                <p className="max-w-md text-xs text-slate-500">Language Agent đang giữ nguyên nội dung gốc, localize lời đọc và kiểm tra text sẽ xuất hiện trong video.</p>
              </div>
            )}

            {stage === 'LOCALIZATION_REVIEW' && localization && (
              <LocalizationReview
                sourceTitle={storyData.source_title || storyTitle}
                sourceText={storyData.source_text || storyData.text}
                result={localization}
                onBack={() => { setStage('STORY_EDITING'); saveSession({ stage: 'STORY_EDITING' }).catch(() => {}) }}
                onApprove={(result) => commitLocalization(result, storyData)}
              />
            )}

            {/* Audio stages */}
            {stage === 'AUDIO_CONFIG' && (
              <AudioConfig
                storyText={approvedStory}
                storyTitle={storyTitle || currentProject}
                outputLocale={storyData.language?.target_locale || 'vi-VN'}
                onStart={handleAudioStart}
              />
            )}

            {stage === 'AUDIO_GENERATING' && (
              <div className="space-y-4">
                {running && (
                  <div className="flex justify-end">
                    <button
                      onClick={handleStop}
                      className="text-xs text-danger border border-danger/30 bg-danger/10 hover:bg-danger/20 px-4 py-2 rounded-lg transition-colors"
                    >
                      Dừng xử lý
                    </button>
                  </div>
                )}
                {job && <ProgressPanel job={job} />}
                {job?.done && job?.success && (
                  <p className="text-center text-xs text-slate-400">Đang chuẩn bị màn hình review audio…</p>
                )}
                {job?.done && !job?.success && (
                  <div className="text-center space-y-2">
                    <p className="text-xs text-red-400">{job.error || 'Tạo audio thất bại'}</p>
                    <button
                      onClick={() => setStage('AUDIO_CONFIG')}
                      className="text-xs text-accent-400 underline"
                    >
                      Thử lại
                    </button>
                  </div>
                )}
              </div>
            )}

            {stage === 'AUDIO_REVIEW' && audioFile && (
              <AudioReview
                project={currentProject}
                audioFile={audioFile}
                audioDuration={audioDuration}
                onRegenerate={() => {
                  setStage('AUDIO_CONFIG')
                  saveSession({ stage: 'AUDIO_CONFIG' }).catch(() => {})
                }}
                onContinue={() => {
                  setStage('VIDEO_UPLOAD')
                  saveSession({ stage: 'VIDEO_UPLOAD', audioFile, audioDuration }).catch(() => {})
                }}
              />
            )}

            {/* Video stages */}
            {stage === 'VIDEO_UPLOAD' && (
              <VideoUpload
                project={currentProject}
                audioFile={audioFile}
                audioDuration={audioDuration}
                storyText={visibleStoryText}
                storyTitle={storyTitle || currentProject}
                structuredStoryboard={storyData.content_mode === 'stick_figure' ? storyData.stick_figure : null}
                outputLocale={storyData.language?.target_locale || 'vi-VN'}
                initialSettings={videoSettings}
                onSettingsChange={(settings) => {
                  setVideoSettings(settings)
                  saveSessionSoon({ stage: 'VIDEO_UPLOAD', videoSettings: settings })
                }}
                onCompose={handleCompose}
              />
            )}

            {stage === 'VIDEO_COMPOSING' && (
              <VideoCompose
                project={currentProject}
                audioFile={audioFile}
                videoOrder={videoOrder}
                settings={videoSettings}
                onDone={(filename) => {
                  setOutputVideoFile(filename || null)
                  setStage('SUBTITLE_CONFIG')
                  saveSession({ stage: 'SUBTITLE_CONFIG', outputVideoFile: filename || null }).catch(() => {})
                }}
              />
            )}

            {stage === 'SUBTITLE_CONFIG' && outputVideoFile && (
              <SubtitleConfig
                project={currentProject}
                inputVideo={outputVideoFile}
                audioFile={audioFile}
                storyText={visibleStoryText}
                audioDuration={audioDuration}
                videoSettings={videoSettings}
                initialStyle={subtitleSettings}
                onStyleChange={(style) => {
                  setSubtitleSettings(style)
                  saveSessionSoon({ stage: 'SUBTITLE_CONFIG', subtitleSettings: style })
                }}
                onDone={(outputs, style) => {
                  const nextOutputs = { ...(subtitleOutputs || {}), ...(outputs || {}) }
                  setSubtitleOutputs(nextOutputs)
                  setSubtitleSettings(style)
                  setStage('DONE')
                  saveSession({ stage: 'DONE', subtitleOutputs: nextOutputs, subtitleSettings: style }).catch(() => {})
                }}
              />
            )}

            {stage === 'DONE' && Object.keys(subtitleOutputs || {}).length > 0 && (
              <PublishTool
                project={currentProject}
                sessionId={currentSessionId || ''}
                storyTitle={storyTitle || currentProject}
                storyText={visibleStoryText}
                audioFile={audioFile}
                subtitleSettings={subtitleSettings}
                outputLocale={storyData.language?.target_locale || 'vi-VN'}
                verticalVideo={subtitleOutputs?.['9x16']}
                horizontalVideo={subtitleOutputs?.['16x9']}
                videoSettings={videoSettings}
              />
            )}

            {stage === 'DONE' && (
              <div className="text-center space-y-4 py-6">
                <div className="text-4xl mb-2">🎬</div>
                <h2 className="text-xl font-bold text-white">Video hoàn chỉnh!</h2>
                <p className="text-sm text-slate-400">Dự án <strong className="text-white">{currentProject}</strong> đã được tạo thành công.</p>
                {Object.keys(subtitleOutputs || {}).length > 0 && (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-w-xl mx-auto">
                    {Object.entries(subtitleOutputs).map(([kind, filename]) => (
                      <a
                        key={kind}
                        href={outputVideoUrl(currentProject, filename)}
                        download={filename}
                        className="inline-flex items-center justify-center gap-2 rounded-xl border border-accent-500/30 bg-accent-500/10 px-4 py-2 text-sm font-semibold text-accent-200 hover:text-white hover:bg-accent-500/20"
                      >
                        <Download size={15} />
                        Tải {kind}
                      </a>
                    ))}
                  </div>
                )}
                {outputVideoFile && (
                  <button
                    onClick={() => {
                      setStage('SUBTITLE_CONFIG')
                      saveSession({ stage: 'SUBTITLE_CONFIG' }).catch(() => {})
                    }}
                    className="px-5 py-2.5 rounded-xl text-sm font-semibold border border-surface-500 text-slate-300 hover:text-white hover:border-accent-400 transition-all"
                  >
                    Chỉnh / render lại phụ đề
                  </button>
                )}
                <button
                  onClick={() => {
                    newSession()
                  }}
                  className="px-6 py-2.5 rounded-xl text-sm font-semibold bg-accent-600 hover:bg-accent-500 text-white transition-all"
                >
                  Tạo dự án mới
                </button>
              </div>
            )}

            {stage === 'DONE' && (
              <div className="mt-5 rounded-2xl border border-surface-600 bg-surface-900/60 overflow-hidden text-left">
                <div className="px-4 py-3 border-b border-surface-600 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <FileText size={15} className="text-accent-400 shrink-0" />
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-white">Nội dung truyện</p>
                      <p className="text-xs text-slate-500 font-mono">{visibleStoryText.length.toLocaleString()} ký tự</p>
                    </div>
                  </div>
                  <button
                    onClick={() => {
                      const nextStory = { ...storyData, text: visibleStoryText }
                      setStoryData(nextStory)
                      setStage('STORY_EDITING')
                      saveSession({ stage: 'STORY_EDITING', storyData: nextStory }).catch(() => {})
                    }}
                    disabled={!visibleStoryText.trim()}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-surface-500 text-xs font-semibold text-slate-300 hover:text-white hover:border-accent-400 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                  >
                    <PenLine size={13} />
                    Chỉnh sửa
                  </button>
                </div>

                {visibleStoryText.trim() ? (
                  <textarea
                    readOnly
                    value={visibleStoryText}
                    rows={18}
                    className="w-full bg-transparent px-4 py-3 text-sm text-slate-200 leading-relaxed resize-none focus:outline-none"
                  />
                ) : (
                  <div className="px-4 py-8 text-center text-sm text-slate-500">
                    Session này chưa có nội dung truyện được lưu.
                  </div>
                )}

                {audioFile && (
                  <div className="px-4 py-3 border-t border-surface-600">
                    <button
                      onClick={() => setStage('VIDEO_UPLOAD')}
                      className="w-full px-5 py-2.5 rounded-xl text-sm font-semibold border border-surface-500 text-slate-300 hover:text-white hover:border-accent-400 transition-all"
                    >
                      Tiếp tục tạo video
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Outputs panel only shown in non-pipeline stages or when done */}
          {(stage === 'STORY_START' || stage === 'DONE') && (
            <OutputsPanel
              outputs={outputs}
              currentProject={currentProject}
              onProjectChange={(p) => { setCurrentProject(p) }}
              onRefresh={loadOutputs}
            />
          )}
          </>
          )}
          </main>

          {!toolOpen && <aside className="lg:sticky lg:top-6 min-w-0">
            <StorySessionsPanel
              currentSessionId={currentSessionId}
              refreshKey={sessionsRefresh}
              onOpen={openSession}
              onNew={newSession}
              onDeletedSession={(sessionId) => {
                if (sessionId === currentSessionIdRef.current) newSession()
                setSessionsRefresh(v => v + 1)
              }}
              onDeletedProject={(sessionId) => {
                if (sessionId === currentSessionIdRef.current) newSession()
                setSessionsRefresh(v => v + 1)
                loadOutputs()
              }}
            />
          </aside>}
        </div>

        <footer className="text-center mt-12 text-xs text-slate-700">
          LucyLab Story Studio · AI → Audio → Video
        </footer>
      </div>
    </div>
  )
}
