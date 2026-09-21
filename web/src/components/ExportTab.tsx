/**
 * ExportTab.tsx —— 批量导出
 *
 * 导出格式刻意对齐原始库：坐标软件吃的是 `x,y,z,名称`（合集.ini 风格）
 * 或 `x ,y ,z ,名称`（24小时合版.ini 风格），两种都留。
 * 「重复 N 次」是给刷怪循环用的 —— 原始库里就是靠重复行数实现挂机循环的。
 */

import { useMemo, useState } from 'react'
import type { Point } from '../lib/types'
import { buildExport, COORD_FORMAT_LABEL, estimateExportLines, quickCopy, type CoordFormat } from '../lib/core'

interface Props {
  filtered: Point[]
  all: Point[]
  selectedPoint: Point | null
  collected: Set<string>
  onCopy: (text: string, label: string) => void
}

type Scope = 'filtered' | 'selected' | 'collected' | 'all'

const SCOPE_LABEL: Record<Scope, string> = {
  filtered: '当前筛选结果',
  selected: '只看选中的那一个',
  collected: '已收集的',
  all: '全部点位',
}

export default function ExportTab({ filtered, all, selectedPoint, collected, onCopy }: Props) {
  const [scope, setScope] = useState<Scope>('filtered')
  const [format, setFormat] = useState<CoordFormat>('csv')
  const [repeatTimes, setRepeatTimes] = useState(false)
  const [header, setHeader] = useState(false)

  const list = useMemo(() => {
    switch (scope) {
      case 'filtered': return filtered
      case 'selected': return selectedPoint ? [selectedPoint] : []
      case 'collected': return all.filter((p) => collected.has(p.id))
      case 'all': return all
    }
  }, [scope, filtered, all, selectedPoint, collected])

  const opts = { format, repeatTimes, header }
  const text = useMemo(() => buildExport(list, opts), [list, format, repeatTimes, header])
  const lines = estimateExportLines(list, opts)

  const download = () => {
    const blob = new Blob(['\ufeff' + text], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    const stamp = new Date().toISOString().slice(0, 10)
    const tag = scope === 'filtered' && list.length ? `筛选${list.length}条` : SCOPE_LABEL[scope]
    a.href = url
    a.download = `燕云坐标_${tag}_${stamp}.ini`
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return (
    <div className="panel">
      <h3>导出什么</h3>
      <div className="field">
        <select className="select" value={scope} onChange={(e) => setScope(e.target.value as Scope)}>
          {(Object.keys(SCOPE_LABEL) as Scope[]).map((k) => (
            <option key={k} value={k} disabled={k === 'selected' && !selectedPoint}>
              {SCOPE_LABEL[k]}
            </option>
          ))}
        </select>
        <div className="hint">当前筛选结果 {filtered.length.toLocaleString()} 条 · 全库 {all.length.toLocaleString()} 条</div>
      </div>

      <h3>格式</h3>
      <div className="field">
        <select className="select" value={format} onChange={(e) => setFormat(e.target.value as CoordFormat)}>
          {(Object.keys(COORD_FORMAT_LABEL) as CoordFormat[]).map((k) => (
            <option key={k} value={k}>{COORD_FORMAT_LABEL[k]}</option>
          ))}
        </select>
        <div className="hint">
          坐标软件一般吃 <code>x,y,z,名称</code>；<code>spaced</code> 是原始 24小时合版.ini 的写法，两种都保留。
        </div>
      </div>

      <div className="field">
        <label className="row" style={{ gap: 8 }}>
          <input type="checkbox" checked={repeatTimes} onChange={(e) => setRepeatTimes(e.target.checked)} />
          <span>按重复次数展开（挂机刷怪循环用）</span>
        </label>
        <div className="hint">
          原始靠「同一个坐标写 N 遍」实现打完传送回去再打。勾上后
          {repeatTimes ? ' 会把重复次数还原' : ' 每个点只输出一次'}。
        </div>
        <label className="row" style={{ gap: 8, marginTop: 8 }}>
          <input type="checkbox" checked={header} onChange={(e) => setHeader(e.target.checked)} />
          <span>加两行注释头</span>
        </label>
        <div className="hint">有些坐标软件不认注释行，默认不加。</div>
      </div>

      <h3>预览</h3>
      <div className="hint" style={{ marginBottom: 6 }}>
        将输出 <b style={{ color: 'var(--accent)' }}>{lines.toLocaleString()}</b> 行
        {list.length ? `（${list.length.toLocaleString()} 个点位${repeatTimes ? '，含重复展开' : ''}）` : ''}
      </div>
      <div className="preview">{text.slice(0, 1600) || '（空）'}{text.length > 1600 ? '\n…' : ''}</div>

      <div className="row wrap" style={{ gap: 8, marginTop: 14 }}>
        <button className="btn primary" disabled={!list.length} onClick={download}>下载 .ini</button>
        <button className="btn" disabled={!list.length} onClick={() => onCopy(text, `${lines} 行导出内容`)}>复制全部</button>
        {selectedPoint && (
          <button className="btn small" onClick={() => onCopy(quickCopy(selectedPoint), selectedPoint.name || '坐标')}>
            只复制选中那条
          </button>
        )}
      </div>
    </div>
  )
}
