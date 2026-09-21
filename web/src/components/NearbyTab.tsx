/**
 * NearbyTab.tsx —— 附近查询
 *
 * 这是整套工具里最贴近实际用法的功能：人已经站在游戏里了，
 * 把当前坐标一贴，就知道脚边有什么、往哪走最近。
 *
 * 距离用三维算（因为 Z 是高度：鬼市在地下 750，垂直差 750 的点其实一点不近），
 * 但也提供「只看水平距离」的开关。
 */

import { useEffect, useMemo, useState } from 'react'
import type { Facets, Point } from '../lib/types'
import { nearby, parseCoordInput, quickCopy, type NearbyHit } from '../lib/core'

interface Props {
  points: Point[]
  facets: Facets
  centerInput: string
  setCenterInput: (v: string) => void
  onSelect: (id: string) => void
  onCopy: (text: string, label: string) => void
  collected: Set<string>
  onToggleCollected: (id: string) => void
}

export default function NearbyTab({
  points, facets, centerInput, setCenterInput, onSelect, onCopy, collected, onToggleCollected,
}: Props) {
  const [top, setTop] = useState(20)
  const [mode, setMode] = useState<'3d' | 'flat'>('3d')
  const [radius, setRadius] = useState(0)      // 0 = 不限
  const [category, setCategory] = useState('')
  const [onlyUncollected, setOnlyUncollected] = useState(false)

  const parsed = useMemo(() => parseCoordInput(centerInput), [centerInput])

  // 记住上次用的坐标，下次打开不用重输
  useEffect(() => {
    const saved = localStorage.getItem('yysls:nearby')
    if (saved && !centerInput) setCenterInput(saved)
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (parsed) localStorage.setItem('yysls:nearby', centerInput)
  }, [parsed, centerInput])

  const pool = useMemo(
    () => (onlyUncollected ? points.filter((p) => !collected.has(p.id)) : points),
    [points, onlyUncollected, collected]
  )

  const hits: NearbyHit[] = useMemo(
    () => (parsed ? nearby(pool, parsed, { top, mode, radius: radius > 0 ? radius : Infinity, category: category || undefined }) : []),
    [pool, parsed, top, mode, radius, category]
  )

  return (
    <div className="panel">
      <h3>我当前站在哪</h3>
      <div className="field">
        <input
          className="input mono"
          placeholder="粘贴当前坐标，例如 -2400.5,700,-60"
          value={centerInput}
          onChange={(e) => setCenterInput(e.target.value)}
        />
        <div className="hint">
          逗号 / 空格分隔都行；直接粘贴带名称的一整行也能认出来。
          {centerInput && !parsed && <span style={{ color: 'var(--danger)' }}> 这串里没找到 3 个数字。</span>}
        </div>
      </div>

      <div className="row wrap" style={{ gap: 8, marginBottom: 12 }}>
        <label className="row" style={{ gap: 4, fontSize: 12 }}>
          显示
          <select className="select" style={{ width: 'auto', padding: '3px 6px' }} value={top} onChange={(e) => setTop(+e.target.value)}>
            {[10, 20, 50, 100].map((n) => <option key={n} value={n}>{n} 条</option>)}
          </select>
        </label>
        <label className="row" style={{ gap: 4, fontSize: 12 }}>
          <input type="radio" checked={mode === '3d'} onChange={() => setMode('3d')} /> 三维距离
        </label>
        <label className="row" style={{ gap: 4, fontSize: 12 }}>
          <input type="radio" checked={mode === 'flat'} onChange={() => setMode('flat')} /> 只看水平
        </label>
      </div>

      <div className="row wrap" style={{ gap: 8, marginBottom: 12 }}>
        <label className="row" style={{ gap: 4, fontSize: 12 }}>
          半径
          <input
            className="input"
            style={{ width: 82, padding: '3px 6px' }}
            type="number"
            step={50}
            value={radius}
            onChange={(e) => setRadius(Math.max(0, +e.target.value || 0))}
          />
          <span className="hint" style={{ margin: 0 }}>{radius > 0 ? '米内' : '不限'}</span>
        </label>
        <select className="select" style={{ width: 'auto', padding: '3px 6px' }} value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">全部分类</option>
          {facets.categories.map((c) => <option key={c.key} value={c.key}>{c.key}</option>)}
        </select>
        <label className="row" style={{ gap: 4, fontSize: 12 }}>
          <input type="checkbox" checked={onlyUncollected} onChange={(e) => setOnlyUncollected(e.target.checked)} />
          排除已收集
        </label>
      </div>

      <h3>最近的点（{hits.length}）</h3>
      {!parsed && <p className="hint">先把坐标填上。</p>}
      {parsed && hits.length === 0 && <p className="hint">这个范围内没有符合条件的点位。</p>}

      <div style={{ display: 'grid', gap: 6 }}>
        {hits.map((h) => {
          const meta = facets.categoryMeta[h.point.category]
          const done = collected.has(h.point.id)
          return (
            <div key={h.point.id} className="note-block" style={{ padding: '8px 10px', cursor: 'pointer' }}>
              <div className="row" style={{ gap: 8, alignItems: 'flex-start' }}>
                <input
                  type="checkbox"
                  checked={done}
                  onChange={(e) => { e.stopPropagation(); onToggleCollected(h.point.id) }}
                  style={{ marginTop: 3 }}
                />
                <div style={{ flex: '1 1 auto', minWidth: 0 }} onClick={() => onSelect(h.point.id)}>
                  <div style={{ fontWeight: 600, fontSize: 13 }}>
                    <span style={{ color: meta?.color }}>{meta?.icon}</span> {h.point.name || '(未命名)'}
                  </div>
                  <div className="hint" style={{ margin: '2px 0 0', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    <span className="mono" style={{ color: 'var(--accent)' }}>{Math.round(h.dist)} 米</span>
                    <span className="mono">水平 {Math.round(h.flat)}</span>
                    <span className="mono">Δz {h.dz > 0 ? '+' : ''}{Math.round(h.dz)}</span>
                    <span>{h.point.region}{h.point.place ? ` · ${h.point.place}` : ''}</span>
                  </div>
                </div>
                <button className="btn small ghost" title="复制坐标" onClick={() => onCopy(quickCopy(h.point), h.point.name || '坐标')}>⧉</button>
              </div>
            </div>
          )
        })}
      </div>

      {hits.length > 0 && (
        <button
          className="btn small"
          style={{ marginTop: 12 }}
          onClick={() => onCopy(hits.map((h) => quickCopy(h.point)).join('\n'), `${hits.length} 条附近坐标`)}
        >
          复制这 {hits.length} 条坐标
        </button>
      )}
    </div>
  )
}
