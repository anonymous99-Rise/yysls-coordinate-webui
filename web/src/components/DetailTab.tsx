/**
 * DetailTab.tsx —— 选中点位的详情
 */

import type { Facets, Point } from '../lib/types'
import { COORD_FORMAT_LABEL, formatCoord, quickCopy, trimNum, type CoordFormat } from '../lib/core'
import { CATEGORY_SOURCE_LABEL, FLAG_INFO, LAYER_LABEL, REGION_SOURCE_LABEL } from '../lib/labels'

interface Props {
  point: Point | null
  facets: Facets
  collected: boolean
  onToggleCollected: (id: string) => void
  onCopy: (text: string, label: string) => void
  onUseAsCenter: (p: Point) => void
}

const COPY_FORMATS: CoordFormat[] = ['csv', 'spaced', 'xyz', 'plain']

export default function DetailTab({ point, facets, collected, onToggleCollected, onCopy, onUseAsCenter }: Props) {
  if (!point) {
    return (
      <div className="panel">
        <div className="empty" style={{ padding: '50px 10px' }}>
          <div className="big">📍</div>
          <div>点表格里的一行，或在地图上点一个点</div>
          <div className="hint" style={{ marginTop: 10 }}>坐标会在这里展开，带一键复制</div>
        </div>
      </div>
    )
  }

  const meta = facets.categoryMeta[point.category]

  return (
    <div className="panel">
      <div className="detail-name">{point.name || '(未命名)'}</div>
      <div className="detail-sub">
        <span className="tag cat" style={{ color: meta?.color, borderColor: meta?.color }}>
          {meta?.icon} {point.category}
        </span>
        <span className="tag">{point.region}{point.place ? ` · ${point.place}` : ''}</span>
        {point.material && <span className="tag">{point.material}</span>}
        {point.repeat > 1 && <span className="tag rep">刷怪循环 ×{point.repeat}</span>}
      </div>

      <div className="coord-big">
        <div className="vals">
          <span className="axis">X </span>{trimNum(point.x)}
          <span style={{ color: 'var(--fg-dim)' }}>, </span>
          <span className="axis">Y </span>{trimNum(point.y)}
          <span style={{ color: 'var(--fg-dim)' }}>, </span>
          <span className="axis">Z </span>{trimNum(point.z)}
        </div>
        <button className="btn primary" onClick={() => onCopy(quickCopy(point), point.name || '坐标')} title="复制 x,y,z,名称">
          复制
        </button>
      </div>

      <div className="row wrap" style={{ gap: 6, marginBottom: 14 }}>
        {COPY_FORMATS.map((f) => (
          <button key={f} className="btn small" onClick={() => onCopy(formatCoord(point, f), COORD_FORMAT_LABEL[f])}>
            {COORD_FORMAT_LABEL[f]}
          </button>
        ))}
      </div>

      <div className="row" style={{ gap: 6, marginBottom: 16 }}>
        <button className={`btn small${collected ? ' primary' : ''}`} onClick={() => onToggleCollected(point.id)}>
          {collected ? '✓ 已收集' : '标记已收集'}
        </button>
        <button className="btn small" onClick={() => onUseAsCenter(point)}>以此为中心找附近</button>
      </div>

      <h3>明细</h3>
      <dl className="kv">
        <dt>主体名</dt><dd>{point.base || '—'}</dd>
        {point.index !== null && (
          <>
            <dt>序号</dt>
            <dd>{point.index}</dd>
          </>
        )}
        <dt>备注</dt><dd>{point.note || '—'}</dd>
        <dt>地点</dt><dd>{point.place || '—'}</dd>
        <dt>高度层</dt><dd>{LAYER_LABEL[point.layer] ?? point.layer}</dd>
        <dt>出现行数</dt>
        <dd>{point.totalRows} 行{point.sources.length > 1 ? ` · 来自 ${point.sources.length} 份文件` : ''}</dd>
        <dt>分类依据</dt><dd>{CATEGORY_SOURCE_LABEL[point.categorySource] ?? point.categorySource}</dd>
        <dt>区域依据</dt><dd>{REGION_SOURCE_LABEL[point.regionSource] ?? point.regionSource}</dd>
        <dt>点位 id</dt><dd className="mono">{point.id}</dd>
      </dl>

      {point.flags.length > 0 && (
        <>
          <h3>数据标记</h3>
          <div style={{ display: 'grid', gap: 8, marginBottom: 14 }}>
            {point.flags.map((f) => (
              <div key={f} className="note-block">
                <div className="who">
                  {FLAG_INFO[f]?.kind === 'warn' ? '⚠ ' : 'ℹ '}
                  {FLAG_INFO[f]?.label ?? f} <span style={{ color: 'var(--fg-dim)' }}>({f})</span>
                </div>
                <p>{FLAG_INFO[f]?.desc ?? '（暂无说明）'}</p>
              </div>
            ))}
          </div>
        </>
      )}

      <h3>出现在这些原始文件里</h3>
      <div className="src-list">
        {point.sources.map((s) => (
          <div className="src-item" key={s.file}>
            <span className="c">×{s.count}</span>
            <span className="f">{s.file}</span>
            {s.lines.length > 0 && (
              <span className="c" style={{ color: 'var(--fg-dim)' }}>行 {s.lines.join(',')}{s.count > s.lines.length ? '…' : ''}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
