/**
 * Toolbar.tsx —— 顶部工具条
 */

import type { Filters, SortKey } from '../lib/core'
import { SORT_LABEL } from '../lib/core'
import type { SortState, ViewMode } from '../lib/store'

interface Props {
  filters: Filters
  setFilters: (fn: (f: Filters) => Filters) => void
  view: ViewMode
  setView: (v: ViewMode) => void
  sort: SortState
  onSort: (k: SortKey) => void
  resultCount: number
  totalCount: number
  collectedCount: number
  onExport: () => void
}

const SORT_KEYS: SortKey[] = ['name', 'region', 'category', 'material', 'place', 'repeat', 'totalRows', 'files', 'z']

export default function Toolbar({
  filters, setFilters, view, setView, sort, onSort, resultCount, totalCount, collectedCount, onExport,
}: Props) {
  const activeCount =
    filters.regions.length + filters.categories.length + filters.materials.length +
    filters.places.length + filters.layers.length + filters.flags.length + filters.sources.length +
    (filters.onlyRepeat ? 1 : 0) + (filters.progress !== 'all' ? 1 : 0)

  const narrowed = resultCount !== totalCount

  return (
    <div className="toolbar">
      <div className="search-wrap">
        <span className="ico">🔍</span>
        <input
          id="global-search"
          className="input"
          placeholder="搜名称 / 材料 / 地点 / 备注…   空格分词，支持 cat:材料 region:清河 这类前缀"
          value={filters.q}
          onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value }))}
        />
        {filters.q
          ? <button className="btn small ghost" style={{ position: 'absolute', right: 6, top: '50%', transform: 'translateY(-50%)' }}
              onClick={() => setFilters((f) => ({ ...f, q: '' }))}>×</button>
          : <kbd>/</kbd>}
      </div>

      <div className="seg" role="group" aria-label="视图">
        <button className={view === 'table' ? 'active' : ''} onClick={() => setView('table')}>列表</button>
        <button className={view === 'map' ? 'active' : ''} onClick={() => setView('map')}>地图</button>
      </div>

      <select
        className="select"
        style={{ width: 'auto' }}
        value={sort.key}
        onChange={(e) => onSort(e.target.value as SortKey)}
        title="排序字段"
      >
        {SORT_KEYS.map((k) => <option key={k} value={k}>按{SORT_LABEL[k]}</option>)}
      </select>
      <button
        className="btn icon"
        title={sort.dir === 'asc' ? '升序（点击切降序）' : '降序（点击切升序）'}
        onClick={() => onSort(sort.key)}
      >
        {sort.dir === 'asc' ? '↑' : '↓'}
      </button>

      <span className="chip" style={{ flex: '0 0 auto' }}>
        {narrowed
          ? <>筛出 <b>{resultCount.toLocaleString()}</b> / {totalCount.toLocaleString()}</>
          : <>共 <b>{totalCount.toLocaleString()}</b> 条</>}
      </span>

      {activeCount > 0 && (
        <button className="btn small ghost" onClick={() => setFilters(() => ({ ...filters, regions: [], categories: [], materials: [], places: [], layers: [], flags: [], sources: [], progress: 'all', onlyRepeat: false }))}>
          清筛选 ({activeCount})
        </button>
      )}

      <span className="spacer" />

      {collectedCount > 0 && <span className="chip">已收集 <b>{collectedCount.toLocaleString()}</b></span>}
      <button className="btn primary" onClick={onExport} disabled={!resultCount}>导出</button>
    </div>
  )
}
