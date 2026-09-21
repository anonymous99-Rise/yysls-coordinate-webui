/**
 * ProgressTab.tsx —— 收集进度
 *
 * 勾选存在 localStorage 里，刷新不丢。按分类 / 区域 / 来源文件看进度，
 * 也可以把「已收集」单独导出，或者把当前筛选结果整批标成已完成。
 */

import { useMemo, useState } from 'react'
import type { Point } from '../lib/types'
import { progressBy, quickCopy } from '../lib/core'

interface Props {
  points: Point[]
  filtered: Point[]
  collected: Set<string>
  onMarkMany: (ids: string[], done: boolean) => void
  onClear: () => void
  onCopy: (text: string, label: string) => void
}

type Dim = 'category' | 'region' | 'place' | 'material'

const DIM_LABEL: Record<Dim, string> = {
  category: '按分类',
  region: '按区域',
  place: '按地点',
  material: '按材料',
}

export default function ProgressTab({ points, filtered, collected, onMarkMany, onClear, onCopy }: Props) {
  const [dim, setDim] = useState<Dim>('category')
  const buckets = useMemo(() => progressBy(points, collected, dim), [points, collected, dim])
  const done = points.filter((p) => collected.has(p.id))
  const max = Math.max(1, ...buckets.map((b) => b.total))

  return (
    <div className="panel">
      <h3>总进度</h3>
      <div className="progress-row">
        <span className="lb">全部点位</span>
        <span className="bar"><i style={{ width: `${(collected.size / Math.max(1, points.length)) * 100}%` }} /></span>
        <span className="num">{collected.size}/{points.length}</span>
      </div>
      <div className="hint" style={{ marginBottom: 14 }}>
        {((collected.size / Math.max(1, points.length)) * 100).toFixed(1)}% 完成。
        勾选保存在浏览器本地，换设备不同步。
      </div>

      <div className="row wrap" style={{ gap: 6, marginBottom: 10 }}>
        {(Object.keys(DIM_LABEL) as Dim[]).map((k) => (
          <button key={k} className={`btn small${dim === k ? ' primary' : ''}`} onClick={() => setDim(k)}>
            {DIM_LABEL[k]}
          </button>
        ))}
      </div>

      <div style={{ display: 'grid', gap: 6 }}>
        {buckets.slice(0, 24).map((b) => (
          <div className="progress-row" key={b.key}>
            <span className="lb" title={b.key}>{b.key || '(空)'}</span>
            <span className="bar">
              <i style={{ width: `${b.pct * 100}%`, background: b.pct === 1 ? 'var(--jade)' : 'var(--accent)' }} />
            </span>
            <span className="num">{b.done}/{b.total}</span>
          </div>
        ))}
        {buckets.length > 24 && <div className="hint">…还有 {buckets.length - 24} 组，切到别的维度看得更清</div>}
      </div>

      <h3>批量操作</h3>
      <div className="row wrap" style={{ gap: 6 }}>
        <button
          className="btn small"
          disabled={!filtered.length}
          onClick={() => onMarkMany(filtered.map((p) => p.id), true)}
        >
          把当前筛选的 {filtered.length.toLocaleString()} 条标为已收集
        </button>
        <button
          className="btn small"
          disabled={!filtered.length}
          onClick={() => onMarkMany(filtered.map((p) => p.id), false)}
        >
          取消当前筛选的勾选
        </button>
        <button className="btn small" disabled={!done.length} onClick={() => onCopy(done.map(quickCopy).join('\n'), `已收集 ${done.length} 条`)}>
          导出已收集坐标
        </button>
        <button
          className="btn small"
          style={{ color: 'var(--danger)' }}
          disabled={!collected.size}
          onClick={() => { if (confirm(`确定清空全部 ${collected.size} 条收集记录？`)) onClear() }}
        >
          清空进度
        </button>
      </div>

      <h3>分布</h3>
      <div className="bar-chart">
        {buckets.slice(0, 12).map((b) => (
          <div className="r" key={b.key}>
            <span className="lb" title={b.key}>{b.key || '(空)'}</span>
            <span className="bar"><i style={{ width: `${(b.total / max) * 100}%`, background: 'var(--jade)' }} /></span>
            <span className="num">{b.total}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
