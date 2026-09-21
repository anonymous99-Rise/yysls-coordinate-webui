/**
 * MapView.tsx —— 地图散点视图（Canvas 2D）
 *
 * 用游戏的世界坐标 (x, y) 直接画二维点阵：北朝上、x 向右。
 * 6 千个点用 Canvas 画毫无压力，所以这里是「所见即所得」的实时重绘。
 *
 * 交互：
 *   滚轮 = 以光标为中心缩放；拖拽 = 平移；悬停 = 提示；点击 = 选中并联动右侧详情
 *   底部可挂一张游戏地图截图当底图（存在内存里，关掉页面就没了）
 *
 * 命中检测用世界坐标网格分桶，不靠遍历 —— 否则每次 mousemove 都要扫 6000 个点。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Facets, Point } from '../lib/types'
import { bounds, categoryColor, trimNum } from '../lib/core'

interface Props {
  points: Point[]
  facets: Facets
  selected: string | null
  onSelect: (id: string) => void
  collected: Set<string>
  onOpenDetail: () => void
}

type ColorMode = 'category' | 'region' | 'altitude' | 'repeat'

const COLOR_MODE_LABEL: Record<ColorMode, string> = {
  category: '按分类',
  region: '按区域',
  altitude: '按高度',
  repeat: '按重复次数',
}

interface Viewport { x0: number; yTop: number; scale: number }

const REGION_COLOR: Record<string, string> = {
  清河: '#4fb3a1',
  开封: '#d6a24a',
  江南: '#c58bd6',
}

export default function MapView({
  points, facets, selected, onSelect, collected, onOpenDetail,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [size, setSize] = useState({ w: 800, h: 600 })
  const [vp, setVp] = useState<Viewport | null>(null)
  const [hover, setHover] = useState<{ p: Point; sx: number; sy: number } | null>(null)
  const [dragging, setDragging] = useState(false)
  const [colorMode, setColorMode] = useState<ColorMode>('category')
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [bg, setBg] = useState<{ url: string; opacity: number; scale: number; dx: number; dy: number } | null>(null)
  const [showLabels, setShowLabels] = useState(true)

  const view = useMemo(() => bounds(points.length ? points : [{ x: 0, y: 0 } as Point]), [points])

  // ---- 容器尺寸 --------------------------------------------------------
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(() => {
      setSize({ w: el.clientWidth, h: el.clientHeight })
    })
    ro.observe(el)
    setSize({ w: el.clientWidth, h: el.clientHeight })
    return () => ro.disconnect()
  }, [])

  // ---- 适应视图 --------------------------------------------------------
  const fit = useCallback(() => {
    const wx = view.maxX - view.minX || 1
    const wy = view.maxY - view.minY || 1
    const scale = Math.min(size.w / wx, size.h / wy) * 0.94
    const cx = (view.minX + view.maxX) / 2
    const cy = (view.minY + view.maxY) / 2
    setVp({ scale, x0: cx - size.w / (2 * scale), yTop: cy + size.h / (2 * scale) })
  }, [view, size])

  useEffect(() => { fit() }, [fit])

  // ---- 屏幕 ↔ 世界 -----------------------------------------------------
  const toScreen = useCallback(
    (wx: number, wy: number) => {
      if (!vp) return [0, 0] as const
      return [(wx - vp.x0) * vp.scale, (vp.yTop - wy) * vp.scale] as const
    },
    [vp]
  )
  const toWorld = useCallback(
    (sx: number, sy: number) => {
      if (!vp) return [0, 0] as const
      return [vp.x0 + sx / vp.scale, vp.yTop - sy / vp.scale] as const
    },
    [vp]
  )

  // ---- 命中检测网格（世界坐标分桶）-------------------------------------
  const CELL = 150
  const grid = useMemo(() => {
    const g = new Map<string, Point[]>()
    for (const p of points) {
      const k = `${Math.floor(p.x / CELL)},${Math.floor(p.y / CELL)}`
      const arr = g.get(k)
      if (arr) arr.push(p)
      else g.set(k, [p])
    }
    return g
  }, [points])

  const hitTest = useCallback(
    (sx: number, sy: number): Point | null => {
      if (!vp) return null
      const [wx, wy] = toWorld(sx, sy)
      const rWorld = 12 / vp.scale // 12px 的命中半径
      const c0x = Math.floor((wx - rWorld) / CELL)
      const c1x = Math.floor((wx + rWorld) / CELL)
      const c0y = Math.floor((wy - rWorld) / CELL)
      const c1y = Math.floor((wy + rWorld) / CELL)
      let best: Point | null = null
      let bestD = Infinity
      for (let cx = c0x; cx <= c1x; cx++) {
        for (let cy = c0y; cy <= c1y; cy++) {
          const arr = grid.get(`${cx},${cy}`)
          if (!arr) continue
          for (const p of arr) {
            if (hidden.has(p.category)) continue
            const d = (p.x - wx) ** 2 + (p.y - wy) ** 2
            if (d < bestD) { bestD = d; best = p }
          }
        }
      }
      return bestD <= rWorld * rWorld ? best : null
    },
    [vp, toWorld, grid, hidden]
  )

  // ---- 绘制 ------------------------------------------------------------
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !vp) return
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    canvas.width = Math.max(1, Math.floor(size.w * dpr))
    canvas.height = Math.max(1, Math.floor(size.h * dpr))
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, size.w, size.h)

    const css = getComputedStyle(document.documentElement)
    const border = css.getPropertyValue('--border').trim() || '#26303d'
    const fgMuted = css.getPropertyValue('--fg-muted').trim() || '#8b98a9'
    const bgElev = css.getPropertyValue('--bg-elev').trim() || '#151b24'

    // 底图
    if (bg) {
      const img = bgImageCache.get(bg.url)
      if (img && img.complete) {
        const [x0, y0] = toScreen(view.minX, view.maxY)
        const [x1, y1] = toScreen(view.maxX, view.minY)
        const w = (x1 - x0) * bg.scale
        const h = (y1 - y0) * bg.scale
        ctx.globalAlpha = bg.opacity
        ctx.drawImage(img, x0 + bg.dx, y0 + bg.dy, w, h)
        ctx.globalAlpha = 1
      }
    }

    // 网格 + 坐标刻度
    const step = niceStep(160 / vp.scale)
    ctx.strokeStyle = border
    ctx.globalAlpha = 0.5
    ctx.lineWidth = 1
    // 刻度要能一眼看清：字号 11.5px + 用 --fg-muted（比 --fg-dim 亮一档）
    ctx.font = '11.5px ui-monospace, SFMono-Regular, Consolas, monospace'
    ctx.fillStyle = fgMuted
    const [wl, wt] = toWorld(0, 0)
    const [wr, wb] = toWorld(size.w, size.h)
    for (let x = Math.ceil(wl / step) * step; x <= wr; x += step) {
      const [sx] = toScreen(x, 0)
      ctx.beginPath(); ctx.moveTo(sx, 0); ctx.lineTo(sx, size.h); ctx.stroke()
      if (showLabels) {
        const label = String(Math.round(x))
        const tw = ctx.measureText(label).width
        // 加个半透明底衬，避免数字压在点阵上看不清
        ctx.save()
        ctx.globalAlpha = 0.75
        ctx.fillStyle = bgElev
        ctx.fillRect(sx + 3, 2, tw + 6, 14)
        ctx.restore()
        ctx.fillStyle = fgMuted
        ctx.fillText(label, sx + 6, 13)
      }
    }
    for (let y = Math.ceil(wb / step) * step; y <= wt; y += step) {
      const [, sy] = toScreen(0, y)
      ctx.beginPath(); ctx.moveTo(0, sy); ctx.lineTo(size.w, sy); ctx.stroke()
      if (showLabels) {
        const label = String(Math.round(y))
        const tw = ctx.measureText(label).width
        ctx.save()
        ctx.globalAlpha = 0.75
        ctx.fillStyle = bgElev
        ctx.fillRect(2, sy - 15, tw + 7, 14)
        ctx.restore()
        ctx.fillStyle = fgMuted
        ctx.fillText(label, 5, sy - 5)
      }
    }
    ctx.globalAlpha = 1

    // 点位
    let drawn = 0
    for (const p of points) {
      if (hidden.has(p.category)) continue
      const [sx, sy] = toScreen(p.x, p.y)
      if (sx < -20 || sy < -20 || sx > size.w + 20 || sy > size.h + 20) continue
      const isSel = p.id === selected
      const isDone = collected.has(p.id)
      ctx.beginPath()
      const r = isSel ? 6 : p.repeat > 1 ? 3.4 : 2.4
      ctx.arc(sx, sy, r, 0, Math.PI * 2)
      if (isDone) {
        ctx.strokeStyle = 'var(--fg-dim)'
        ctx.strokeStyle = '#5d6a7a'
        ctx.lineWidth = 1.2
        ctx.stroke()
      } else {
        ctx.fillStyle = colorFor(p, colorMode, facets)
        ctx.fill()
      }
      if (isSel) {
        ctx.strokeStyle = '#ffffff'
        ctx.lineWidth = 2
        ctx.stroke()
      }
      drawn++
    }

    // 悬停十字
    if (hover) {
      ctx.strokeStyle = '#ffffff'
      ctx.globalAlpha = 0.65
      ctx.lineWidth = 1
      const [hx, hy] = toScreen(hover.p.x, hover.p.y)
      ctx.beginPath(); ctx.arc(hx, hy, 11, 0, Math.PI * 2); ctx.stroke()
      ctx.globalAlpha = 1
    }

    // 右下角信息
    ctx.fillStyle = fgMuted
    ctx.font = '11.5px ui-monospace, SFMono-Regular, Consolas, monospace'
    ctx.fillText(`显示 ${drawn.toLocaleString()} / ${points.length.toLocaleString()} 点  ·  缩放 ${vp.scale.toFixed(2)}×`, 10, size.h - 10)
  }, [points, vp, size, selected, collected, hidden, colorMode, facets, hover, bg, toScreen, toWorld, view, showLabels])

  // ---- 交互 ------------------------------------------------------------
  const onWheel = useCallback(
    (e: React.WheelEvent) => {
      if (!vp) return
      e.preventDefault()
      const rect = canvasRef.current!.getBoundingClientRect()
      const sx = e.clientX - rect.left
      const sy = e.clientY - rect.top
      const [wx, wy] = toWorld(sx, sy)
      const k = e.deltaY < 0 ? 1.18 : 1 / 1.18
      const scale = Math.min(Math.max(vp.scale * k, 0.02), 20)
      setVp({ scale, x0: wx - sx / scale, yTop: wy + sy / scale })
    },
    [vp, toWorld]
  )

  const drag = useRef<{ sx: number; sy: number; x0: number; yTop: number; moved: boolean } | null>(null)

  const onPointerDown = (e: React.PointerEvent) => {
    if (!vp) return
    ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
    drag.current = { sx: e.clientX, sy: e.clientY, x0: vp.x0, yTop: vp.yTop, moved: false }
    setDragging(true)
  }

  const onPointerMove = (e: React.PointerEvent) => {
    if (!vp) return
    const rect = canvasRef.current!.getBoundingClientRect()
    const sx = e.clientX - rect.left
    const sy = e.clientY - rect.top
    if (drag.current) {
      const dx = e.clientX - drag.current.sx
      const dy = e.clientY - drag.current.sy
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.current.moved = true
      setVp({
        scale: vp.scale,
        x0: drag.current.x0 - dx / vp.scale,
        yTop: drag.current.yTop + dy / vp.scale,
      })
      return
    }
    const hit = hitTest(sx, sy)
    setHover(hit ? { p: hit, sx, sy } : null)
  }

  const onPointerUp = (e: React.PointerEvent) => {
    const wasDrag = drag.current?.moved
    drag.current = null
    setDragging(false)
    if (wasDrag) return
    const rect = canvasRef.current!.getBoundingClientRect()
    const hit = hitTest(e.clientX - rect.left, e.clientY - rect.top)
    if (hit) { onSelect(hit.id); onOpenDetail() }
  }

  const zoomBy = (k: number) => {
    if (!vp) return
    const cx = size.w / 2
    const cy = size.h / 2
    const [wx, wy] = toWorld(cx, cy)
    const scale = Math.min(Math.max(vp.scale * k, 0.02), 20)
    setVp({ scale, x0: wx - cx / scale, yTop: wy + cy / scale })
  }

  // 图例：只列当前结果里真的有的分类
  const legend = useMemo(() => {
    const present = new Map<string, number>()
    for (const p of points) present.set(p.category, (present.get(p.category) ?? 0) + 1)
    return facets.categories
      .filter((c) => present.has(c.key))
      .map((c) => ({ key: c.key, count: present.get(c.key)!, color: categoryColor(facets, c.key), icon: facets.categoryMeta[c.key]?.icon }))
  }, [points, facets])

  return (
    <div className="map-wrap" ref={wrapRef}>
      <canvas
        ref={canvasRef}
        className={`map-canvas${dragging ? ' grabbing' : ''}`}
        style={{ width: size.w, height: size.h }}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => { setHover(null); drag.current = null; setDragging(false) }}
      />

      <div className="map-hud">
        <div className="map-panel">
          <div className="row wrap" style={{ gap: 6 }}>
            <select className="select" style={{ width: 'auto', padding: '3px 6px' }} value={colorMode} onChange={(e) => setColorMode(e.target.value as ColorMode)}>
              {(Object.keys(COLOR_MODE_LABEL) as ColorMode[]).map((k) => (
                <option key={k} value={k}>{COLOR_MODE_LABEL[k]}</option>
              ))}
            </select>
            <button className="btn small" onClick={fit} title="缩放到全部点">适应</button>
            <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11.5 }}>
              <input type="checkbox" checked={showLabels} onChange={(e) => setShowLabels(e.target.checked)} />
              坐标刻度
            </label>
            <label className="btn small" style={{ cursor: 'pointer' }}>
              底图
              <input
                type="file"
                accept="image/*"
                style={{ display: 'none' }}
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (!f) return
                  const url = URL.createObjectURL(f)
                  const img = new Image()
                  img.src = url
                  bgImageCache.set(url, img)
                  setBg({ url, opacity: 0.45, scale: 1, dx: 0, dy: 0 })
                }}
              />
            </label>
          </div>
          {bg && (
            <div style={{ marginTop: 6, display: 'grid', gap: 3, fontSize: 11 }}>
              <label>透明度 <input type="range" min={0} max={1} step={0.05} value={bg.opacity} onChange={(e) => setBg({ ...bg, opacity: +e.target.value })} /></label>
              <label>缩放 <input type="range" min={0.4} max={2.5} step={0.02} value={bg.scale} onChange={(e) => setBg({ ...bg, scale: +e.target.value })} /></label>
              <label>左移 <input type="range" min={-600} max={600} step={5} value={bg.dx} onChange={(e) => setBg({ ...bg, dx: +e.target.value })} /></label>
              <label>上移 <input type="range" min={-600} max={600} step={5} value={bg.dy} onChange={(e) => setBg({ ...bg, dy: +e.target.value })} /></label>
              <button className="btn small" onClick={() => setBg(null)}>移除底图</button>
              <span style={{ color: 'var(--fg-dim)' }}>底图仅本次会话有效，不写本地存储</span>
            </div>
          )}
        </div>

        <div className="map-panel legend">
          {legend.map((l) => (
            <span
              key={l.key}
              className={`item${hidden.has(l.key) ? ' off' : ''}`}
              onClick={() =>
                setHidden((s) => {
                  const n = new Set(s)
                  if (n.has(l.key)) n.delete(l.key)
                  else n.add(l.key)
                  return n
                })
              }
              title="点击显示/隐藏这一类"
            >
              <span className="dot" style={{ background: l.color }} />
              {l.icon} {l.key} {l.count.toLocaleString()}
            </span>
          ))}
        </div>

        <div className="map-zoom" style={{ marginLeft: 'auto' }}>
          <button className="btn" onClick={() => zoomBy(1.25)} title="放大">＋</button>
          <button className="btn" onClick={() => zoomBy(1 / 1.25)} title="缩小">－</button>
        </div>
      </div>

      {hover && (
        <div
          className="map-tip"
          style={{ left: Math.min(hover.sx + 14, size.w - 240), top: Math.max(hover.sy - 10, 4) }}
        >
          <div className="nm">{hover.p.name || '(未命名)'}</div>
          <div className="cd">
            {trimNum(hover.p.x)}, {trimNum(hover.p.y)}, {trimNum(hover.p.z)}
          </div>
          <div style={{ fontSize: 11, color: 'var(--fg-dim)' }}>
            {hover.p.region} · {hover.p.category}
            {hover.p.repeat > 1 && ` · 重复 ×${hover.p.repeat}`}
          </div>
        </div>
      )}
    </div>
  )
}

// 底图缓存：URL → HTMLImageElement（不进 localStorage，避免把存储塞爆）
const bgImageCache = new Map<string, HTMLImageElement>()

function colorFor(p: Point, mode: ColorMode, facets: Facets): string {
  switch (mode) {
    case 'category': return categoryColor(facets, p.category)
    case 'region': return REGION_COLOR[p.region] ?? '#64748b'
    case 'altitude': {
      // 从地下（-760）到高台（+220）映射到蓝 → 黄
      const t = Math.min(1, Math.max(0, (p.z + 760) / 980))
      return `hsl(${210 - t * 170}, 72%, ${52 + t * 8}%)`
    }
    case 'repeat': {
      const t = Math.min(1, Math.log10(Math.max(1, p.repeat)) / Math.log10(600))
      return `hsl(${170 - t * 170}, 80%, ${50 + t * 10}%)`
    }
  }
}

/** 让网格步长落在 1/2/5 × 10^n 上，缩放时不会出现 137 这种丑数字 */
function niceStep(raw: number): number {
  const exp = Math.floor(Math.log10(Math.max(raw, 1e-6)))
  const base = Math.pow(10, exp)
  const n = raw / base
  if (n <= 1) return base
  if (n <= 2) return 2 * base
  if (n <= 5) return 5 * base
  return 10 * base
}
