import { useEffect, useMemo, useRef, useState } from 'react'

const WIDTH = 640
const HEIGHT = 360
const PAPER = '#F8F1E4'
const SETTLE_SECONDS = 0.1

function hashText(value) {
  let hash = 2166136261
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}

function randomFactory(seed) {
  let state = seed >>> 0
  return () => {
    state += 0x6D2B79F5
    let value = state
    value = Math.imul(value ^ (value >>> 15), value | 1)
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61)
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296
  }
}

function makeStroke(start, end, width, random) {
  const dx = end.x - start.x
  const dy = end.y - start.y
  const length = Math.max(1, Math.hypot(dx, dy))
  const normal = { x: -dy / length, y: dx / length }
  const points = Array.from({ length: 8 }, (_, index) => {
    const part = index / 7
    const jitter = index === 0 || index === 7 ? 0 : (random() - 0.5) * width * 0.36
    return {
      x: start.x + dx * part + normal.x * jitter,
      y: start.y + dy * part + normal.y * jitter,
    }
  })
  return { points, width }
}

function buildPlan(sceneId, config, salt = '') {
  const random = randomFactory(hashText(`${sceneId}|${config.pattern}|${config.effect}|${salt}`))
  let pattern = config.pattern === 'auto'
    ? ['left_to_right', 'right_to_left', 'top_to_bottom', 'diagonal', 'scribble'][Math.floor(random() * 5)]
    : config.pattern
  const strokes = []
  if (pattern === 'top_to_bottom') {
    const count = 10
    const gap = WIDTH / (count - 1)
    for (let index = 0; index < count; index += 1) {
      const x = index * gap
      strokes.push(makeStroke({ x, y: -30 }, { x, y: HEIGHT + 30 }, Math.max(34, gap * 0.55), random))
    }
  } else {
    const count = 9
    const gap = HEIGHT / (count - 1)
    const levels = Array.from({ length: count }, (_, index) => index * gap)
    if (pattern === 'scribble') levels.sort(() => random() - 0.5)
    for (const level of levels) {
      const slope = pattern === 'diagonal' ? (random() - 0.5) * HEIGHT * 0.45 : (random() - 0.5) * 24
      let start = { x: -35, y: level - slope }
      let end = { x: WIDTH + 35, y: level + slope }
      if (pattern === 'right_to_left') [start, end] = [end, start]
      strokes.push(makeStroke(start, end, Math.max(38, gap * 0.58), random))
    }
  }
  return strokes.map((stroke, index) => ({ ...stroke, start: index / strokes.length, end: (index + 1) / strokes.length }))
}

function partialPoints(points, fraction) {
  if (fraction >= 1) return points
  if (fraction <= 0) return points.slice(0, 1)
  const lengths = points.slice(1).map((point, index) => Math.hypot(point.x - points[index].x, point.y - points[index].y))
  let remaining = lengths.reduce((sum, item) => sum + item, 0) * fraction
  const out = [points[0]]
  for (let index = 0; index < lengths.length; index += 1) {
    if (remaining >= lengths[index]) {
      out.push(points[index + 1])
      remaining -= lengths[index]
      continue
    }
    const amount = remaining / Math.max(lengths[index], 0.0001)
    out.push({
      x: points[index].x + (points[index + 1].x - points[index].x) * amount,
      y: points[index].y + (points[index + 1].y - points[index].y) * amount,
    })
    break
  }
  return out
}

function drawMask(context, plan, progress) {
  context.clearRect(0, 0, WIDTH, HEIGHT)
  context.strokeStyle = '#fff'
  context.fillStyle = '#fff'
  context.lineCap = 'round'
  context.lineJoin = 'round'
  let actor = { point: { x: WIDTH / 2, y: HEIGHT / 2 }, angle: 0 }
  if (progress >= 1) {
    context.fillRect(0, 0, WIDTH, HEIGHT)
    return actor
  }
  for (const stroke of plan) {
    const amount = Math.max(0, Math.min(1, (progress - stroke.start) / Math.max(0.0001, stroke.end - stroke.start)))
    if (!amount) continue
    const points = partialPoints(stroke.points, amount)
    context.lineWidth = stroke.width
    context.beginPath()
    context.moveTo(points[0].x, points[0].y)
    for (const point of points.slice(1)) context.lineTo(point.x, point.y)
    context.stroke()
    const tip = points[points.length - 1]
    context.beginPath()
    context.arc(tip.x, tip.y, stroke.width / 2, 0, Math.PI * 2)
    context.fill()
    const previous = points[Math.max(0, points.length - 2)]
    actor = { point: tip, angle: Math.atan2(tip.y - previous.y, tip.x - previous.x) }
  }
  return actor
}

function drawCover(context, image) {
  const scale = Math.max(WIDTH / image.width, HEIGHT / image.height)
  const width = image.width * scale
  const height = image.height * scale
  context.drawImage(image, (WIDTH - width) / 2, (HEIGHT - height) / 2, width, height)
}

function drawMaskedImage(context, image, mask, filter = 'none', alpha = 1) {
  const layer = document.createElement('canvas')
  layer.width = WIDTH
  layer.height = HEIGHT
  const layerContext = layer.getContext('2d')
  layerContext.filter = filter
  layerContext.globalAlpha = alpha
  drawCover(layerContext, image)
  layerContext.globalAlpha = 1
  layerContext.filter = 'none'
  layerContext.globalCompositeOperation = 'destination-in'
  layerContext.drawImage(mask, 0, 0)
  layerContext.globalCompositeOperation = 'source-over'
  context.drawImage(layer, 0, 0)
}

function drawActor(context, actor, point, angle, fade) {
  if (actor === 'none' || fade <= 0) return
  context.save()
  context.translate(point.x, point.y)
  context.rotate(angle)
  context.globalAlpha = fade
  if (actor === 'brush') {
    context.fillStyle = '#643B22'
    context.fillRect(-66, -10, 92, 20)
    context.fillStyle = '#C69A5E'
    context.fillRect(20, -13, 26, 26)
    context.fillStyle = '#33251D'
    context.beginPath()
    context.moveTo(46, -18); context.lineTo(80, -11); context.lineTo(80, 11); context.lineTo(46, 18); context.fill()
  } else {
    context.fillStyle = '#EFB54D'
    context.beginPath()
    context.moveTo(-88, -12); context.lineTo(36, -25); context.lineTo(56, 18); context.lineTo(-70, 30); context.fill()
    context.fillStyle = '#C6A87F'
    context.beginPath()
    context.moveTo(36, -25); context.lineTo(77, -4); context.lineTo(56, 18); context.fill()
    context.fillStyle = '#282828'
    context.beginPath()
    context.moveTo(77, -4); context.lineTo(87, 4); context.lineTo(56, 18); context.fill()
    if (actor === 'hand') {
      context.fillStyle = '#E0A981'
      context.beginPath()
      context.ellipse(-45, 42, 52, 34, 0, 0, Math.PI * 2)
      context.fill()
      context.fillRect(-15, 8, 70, 28)
    }
  }
  context.restore()
}

export default function SceneRevealPreview({ imageUrl, sceneId, config }) {
  const canvasRef = useRef(null)
  const [image, setImage] = useState(null)
  const [playing, setPlaying] = useState(true)
  const [progress, setProgress] = useState(0)
  const [run, setRun] = useState(0)
  const normalized = useMemo(() => ({
    enabled: Boolean(config?.enabled),
    effect: config?.effect === 'pencil' ? 'pencil' : 'brush',
    durationSec: Math.min(3, Math.max(0.4, Number(config?.durationSec || 1.2))),
    pattern: config?.pattern || 'auto',
    actor: config?.actor || 'none',
  }), [config?.enabled, config?.effect, config?.durationSec, config?.pattern, config?.actor])
  const plan = useMemo(() => buildPlan(sceneId || 'scene', normalized), [sceneId, normalized])
  const colorPlan = useMemo(() => buildPlan(sceneId || 'scene', normalized, 'color'), [sceneId, normalized])

  useEffect(() => {
    if (!imageUrl) {
      setImage(null)
      return undefined
    }
    const next = new Image()
    next.onload = () => setImage(next)
    next.onerror = () => setImage(null)
    next.src = imageUrl
    return () => { next.onload = null; next.onerror = null }
  }, [imageUrl])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !image) return undefined
    const context = canvas.getContext('2d')
    const mask = document.createElement('canvas')
    mask.width = WIDTH
    mask.height = HEIGHT
    const maskContext = mask.getContext('2d')
    let frameId = 0
    let startedAt = 0
    const render = (nextProgress) => {
      context.fillStyle = PAPER
      context.fillRect(0, 0, WIDTH, HEIGHT)
      if (!normalized.enabled) {
        drawCover(context, image)
        return
      }
      const settleFraction = Math.min(1, SETTLE_SECONDS / normalized.durationSec)
      const animationFraction = Math.max(0, 1 - settleFraction)
      // Match the renderer: the final 0.1 seconds are the untouched source
      // image, so no partial pencil or brush mask can remain on screen.
      if (nextProgress >= animationFraction) {
        drawCover(context, image)
        return
      }
      const animationProgress = Math.min(1, nextProgress / Math.max(animationFraction, 0.0001))
      if (normalized.effect === 'pencil') {
        const lineProgress = Math.min(1, animationProgress / 0.45)
        const lineActor = drawMask(maskContext, plan, lineProgress)
        drawMaskedImage(context, image, mask, 'grayscale(1) contrast(2.2) brightness(1.1)', 0.7)
        if (animationProgress > 0.45) {
          const colorProgress = Math.min(1, (animationProgress - 0.45) / 0.55)
          const colorActor = drawMask(maskContext, colorPlan, colorProgress)
          drawMaskedImage(context, image, mask)
          const fade = Math.min(1, animationProgress * 6, (1 - animationProgress) * 10 + 0.1)
          drawActor(context, normalized.actor, colorActor.point, colorActor.angle, fade)
        } else {
          drawActor(context, normalized.actor, lineActor.point, lineActor.angle, Math.min(1, animationProgress * 6))
        }
      } else {
        const actor = drawMask(maskContext, plan, animationProgress)
        drawMaskedImage(context, image, mask)
        const fade = Math.min(1, animationProgress * 6, (1 - animationProgress) * 10 + 0.1)
        drawActor(context, normalized.actor, actor.point, actor.angle, fade)
      }
    }
    const tick = (stamp) => {
      if (!startedAt) startedAt = stamp
      const next = playing && normalized.enabled
        ? Math.min(1, (stamp - startedAt) / (normalized.durationSec * 1000))
        : (normalized.enabled ? progress : 1)
      render(next)
      if (playing && normalized.enabled && next < 1) {
        setProgress(next)
        frameId = requestAnimationFrame(tick)
      } else {
        setProgress(next)
      }
    }
    frameId = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frameId)
  }, [image, normalized, plan, colorPlan, playing, run])

  function restart() {
    setProgress(0)
    setPlaying(true)
    setRun(value => value + 1)
  }

  return (
    <div className="space-y-2">
      <canvas ref={canvasRef} width={WIDTH} height={HEIGHT} className="w-full rounded-lg border border-surface-600 bg-[#F8F1E4]" />
      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="text-slate-500">Preview nhịp hiệu ứng · {Math.round(progress * 100)}%</span>
        <div className="flex gap-2">
          <button type="button" onClick={() => setPlaying(value => !value)} className="rounded-md border border-surface-500 px-2 py-1 text-slate-300 hover:border-accent-400 hover:text-white">
            {playing ? 'Dừng' : 'Play'}
          </button>
          <button type="button" onClick={restart} className="rounded-md border border-surface-500 px-2 py-1 text-slate-300 hover:border-accent-400 hover:text-white">Chạy lại</button>
        </div>
      </div>
    </div>
  )
}
