/**
 * PointTable.tsx —— 列表视图
 *
 * 6 千行不可能全渲染，按页切（默认每页 100 条）。
 * 坐标列点一下就是「复制这一行的 x,y,z,名称」—— 这是最高频的动作，
 * 所以按钮做得小、位置固定、鼠标悬停才显形。
 */

import { useMemo, useState } from 'react'
import type { Facets, Point } from '../lib/types'
import type { SortKey } from '../lib/core'
import type { SortState } from '../lib/store'
import { quickCopy, trimNum } from '../lib/core'

interface Props {
  points: Point[]
  facets: Facets
  selected: string | null
  onSelect: (id: string) => void
  collected: Set<string>
  onToggleCollected: (id: string) => void
  sort: SortState
  onSort: (key: SortKey) => void
  onCopy: (text: string, label: string) => void
}

const COLUMNS: { key: SortKey | null; label: string; width?: string; cls?: string }[] = [
  { key: null, label: '', width: '34px' },
  { key: 'name', label: '名称' },
  { key: 'category', label: '分类', width: '92px' },
  { key: 'region', label: '区域 / 地点', width: '150px' },
  { key: 'material', label: '材料', width: '104px' },
  { key: null, label: '坐标', width: '260px' },
  { key: 'repeat', label: '重复', width: '68px' },
  { key: 'files', label: '来源', width: '58px' },
]

export default function PointTable({
  points, facets, selected, onSelect, collected, onToggleCollected, sort, onSort, onCopy,
}: Props) {
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(100)

  const pageCount = Math.max(1, Math.ceil(points.length / pageSize))
  const safePage = Math.min(page, pageCount - 1)
  const slice = useMemo(
    () => points.slice(safePage * pageSize, safePage * pageSize + pageSize),
    [points, safePage, pageSize]
  )

  return (
    <>
      <div className="content">
        {points.length === 0 ? (
          <div className="empty">
            <div className="big">🧭</div>
            <div>没有符合条件的点位</div>
            <div className="hint" style={{ marginTop: 8 }}>试试清掉几个筛选条件，或换个关键词</div>
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                {COLUMNS.map((c, i) => (
                  <th
                    key={i}
                    style={{ width: c.width }}
                    onClick={c.key ? () => onSort(c.key as SortKey) : undefined}
                  >
                    {c.label}
                    {c.key === sort.key && <span className="sort">{sort.dir === 'asc' ? '▲' : '▼'}</span>}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {slice.map((p) => {
                const meta = facets.categoryMeta[p.category]
                const done = collected.has(p.id)
                return (
                  <tr
                    key={p.id}
                    className={`${selected === p.id ? 'selected' : ''}${done ? ' done' : ''}`}
                    onClick={() => onSelect(p.id)}
                  >
                    <td onClick={(e) => { e.stopPropagation(); onToggleCollected(p.id) }}>
                      <input type="checkbox" checked={done} readOnly title="标记为已收集" />
                    </td>
                    <td>
                      <div className="cell-name">
                        <span className="cat" title={p.category}>{meta?.icon ?? '•'}</span>
                        <span className="name" title={p.name || '(未命名)'}>{p.name || '(未命名)'}</span>
                        {p.note && <span className="cell-note">（{p.note}）</span>}
                      </div>
                    </td>
                    <td>
                      <span className="tag cat" style={{ color: meta?.color, borderColor: meta?.color }}>
                        {p.category}
                      </span>
                    </td>
                    <td>
                      {p.region}
                      {p.place && <span style={{ color: 'var(--fg-dim)' }}> · {p.place}</span>}
                    </td>
                    <td>{p.material || <span style={{ color: 'var(--fg-dim)' }}>—</span>}</td>
                    <td>
                      <div className="coord-cell">
                        <code>{trimNum(p.x)}, {trimNum(p.y)}, {trimNum(p.z)}</code>
                        <button
                          className="btn small ghost copy"
                          title="复制 x,y,z,名称"
                          onClick={(e) => { e.stopPropagation(); onCopy(quickCopy(p), p.name || '(未命名)') }}
                        >
                          ⧉
                        </button>
                      </div>
                    </td>
                    <td>
                      {p.repeat > 1 ? <span className="tag rep">×{p.repeat}</span> : <span style={{ color: 'var(--fg-dim)' }}>1</span>}
                    </td>
                    <td style={{ color: 'var(--fg-dim)' }}>{p.sources.length}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="pager">
        <span>
          共 <b style={{ color: 'var(--fg)' }}>{points.length.toLocaleString()}</b> 条
          {points.length > 0 && <> · 第 {safePage + 1}/{pageCount} 页</>}
        </span>
        <span className="spacer" />
        <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          每页
          <select
            className="select"
            style={{ width: 'auto', padding: '3px 6px' }}
            value={pageSize}
            onChange={(e) => { setPageSize(Number(e.target.value)); setPage(0) }}
          >
            {[50, 100, 200, 500].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <button className="btn small" disabled={safePage === 0} onClick={() => setPage(0)}>⏮</button>
        <button className="btn small" disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>上一页</button>
        <button className="btn small" disabled={safePage >= pageCount - 1} onClick={() => setPage(safePage + 1)}>下一页</button>
        <button className="btn small" disabled={safePage >= pageCount - 1} onClick={() => setPage(pageCount - 1)}>⏭</button>
      </div>
    </>
  )
}
