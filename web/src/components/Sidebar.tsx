/**
 * Sidebar.tsx —— 左侧筛选栏
 *
 * 分组：区域 / 分类 / 材料 / 地点 / 高度层 / 数据标记 / 来源文件 / 收集进度
 * 语义：组内多选是「或」，组间是「且」。
 */

import { useMemo, useState } from 'react'
import type { Facets, Point } from '../lib/types'
import type { Filters } from '../lib/core'
import { FLAG_INFO } from '../lib/labels'

export interface FilterOption {
  key: string
  label: string
  count: number
  color?: string
  icon?: string
}

interface GroupProps {
  title: string
  options: FilterOption[]
  selected: string[]
  onToggle: (key: string) => void
  onClear: () => void
  defaultOpen?: boolean
  searchable?: boolean
  /** 折叠时先显示多少条 */
  maxVisible?: number
  /** 选项太多时列表区最大高度 */
  listHeight?: number
}

function FilterGroup({
  title, options, selected, onToggle, onClear,
  defaultOpen = false, searchable = false, maxVisible = 8, listHeight = 260,
}: GroupProps) {
  const [open, setOpen] = useState(defaultOpen)
  const [q, setQ] = useState('')
  const [expanded, setExpanded] = useState(false)

  const filtered = useMemo(() => {
    const kw = q.trim().toLowerCase()
    return kw ? options.filter((o) => o.label.toLowerCase().includes(kw) || o.key.toLowerCase().includes(kw)) : options
  }, [options, q])

  const visible = expanded || filtered.length <= maxVisible ? filtered : filtered.slice(0, maxVisible)
  const needScroll = expanded && filtered.length > 12

  return (
    <div className="filter-group">
      <button className={`filter-head${open ? ' open' : ''}`} onClick={() => setOpen((v) => !v)}>
        <span className="caret">▶</span>
        <span>{title}</span>
        {selected.length > 0 && <span className="badge">{selected.length}</span>}
        <span className="count" style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--fg-dim)' }}>
          {options.length}
        </span>
      </button>

      {open && (
        <>
          {searchable && filtered.length > 6 && (
            <div className="filter-search">
              <input
                className="input"
                placeholder={`搜${title}…`}
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
            </div>
          )}
          <div className="filter-body" style={needScroll ? { maxHeight: listHeight, overflowY: 'auto' } : undefined}>
            {selected.length > 0 && (
              <button className="filter-item" onClick={onClear}>
                <span className="check">×</span>
                <span className="label" style={{ color: 'var(--danger)' }}>清空本组</span>
              </button>
            )}
            {visible.map((o) => {
              const active = selected.includes(o.key)
              return (
                <button
                  key={o.key}
                  className={`filter-item${active ? ' active' : ''}`}
                  onClick={() => onToggle(o.key)}
                  title={o.key}
                >
                  <span className="check">{active ? '✓' : ''}</span>
                  {o.color && <span className="dot" style={{ background: o.color }} />}
                  <span className="label">
                    {o.icon ? `${o.icon} ` : ''}
                    {o.label}
                  </span>
                  <span className="count">{o.count.toLocaleString()}</span>
                </button>
              )
            })}
            {filtered.length === 0 && <div className="hint" style={{ padding: '4px 7px' }}>没有匹配项</div>}
            {filtered.length > maxVisible && (
              <button className="filter-item" onClick={() => setExpanded((v) => !v)}>
                <span className="check" />
                <span className="label" style={{ color: 'var(--accent)' }}>
                  {expanded ? '收起' : `展开全部 ${filtered.length} 项`}
                </span>
              </button>
            )}
          </div>
        </>
      )}
    </div>
  )
}

/** 只留下真正会在 UI 上出现的标志，并且按「值得关注」排序 */
const FLAG_ORDER = [
  'placeholder_name', 'name_missing', 'co_located_with_other',
  'glued_split', 'stray_prefix', 'region_nearest', 'region_inferred',
  'cross_file', 'duplicated', 'suspicious_range', 'whitespace_delimited',
  'name_contains_comma',
]

interface Props {
  facets: Facets
  points: Point[]
  filters: Filters
  setFilters: (fn: (f: Filters) => Filters) => void
  collectedCount: number
  className?: string
}

export default function Sidebar({ facets, points, filters, setFilters, collectedCount, className }: Props) {
  const toggle = (field: keyof Filters) => (key: string) =>
    setFilters((f) => {
      const cur = f[field] as string[]
      const next = cur.includes(key) ? cur.filter((x) => x !== key) : [...cur, key]
      return { ...f, [field]: next } as Filters
    })

  const clear = (field: keyof Filters) => () => setFilters((f) => ({ ...f, [field]: [] }) as Filters)

  const flagOptions = useMemo<FilterOption[]>(() => {
    const counts = new Map(facets.flags.map((f) => [f.key, f.count]))
    return FLAG_ORDER.filter((k) => counts.has(k)).map((k) => ({
      key: k,
      label: FLAG_INFO[k]?.label ?? k,
      count: counts.get(k) ?? 0,
      icon: FLAG_INFO[k]?.kind === 'warn' ? '⚠' : 'ℹ',
    }))
  }, [facets.flags])

  const sourceOptions = useMemo<FilterOption[]>(
    () =>
      facets.sourceFiles
        .filter((f) => f.points > 0)
        .map((f) => ({ key: f.file, label: f.file, count: f.points })),
    [facets.sourceFiles]
  )

  return (
    <aside className={`sidebar${className ? ` ${className}` : ''}`}>
      <FilterGroup
        title="区域"
        defaultOpen
        options={facets.regions.map((r) => ({ key: r.key, label: r.key || '(未判定)', count: r.count, color: regionColor(r.key) }))}
        selected={filters.regions}
        onToggle={toggle('regions')}
        onClear={clear('regions')}
      />

      <FilterGroup
        title="分类"
        defaultOpen
        options={facets.categories.map((c) => ({
          key: c.key,
          label: c.key,
          count: c.count,
          color: facets.categoryMeta[c.key]?.color,
          icon: facets.categoryMeta[c.key]?.icon,
        }))}
        selected={filters.categories}
        onToggle={toggle('categories')}
        onClear={clear('categories')}
      />

      <FilterGroup
        title="材料"
        searchable
        options={facets.materials.map((m) => ({ key: m.key, label: m.key, count: m.count }))}
        selected={filters.materials}
        onToggle={toggle('materials')}
        onClear={clear('materials')}
      />

      <FilterGroup
        title="地点"
        searchable
        options={facets.places.map((p) => ({ key: p.key, label: p.key, count: p.count }))}
        selected={filters.places}
        onToggle={toggle('places')}
        onClear={clear('places')}
      />

      <FilterGroup
        title="高度层"
        options={facets.layers.map((l) => ({ key: l.key, label: l.key, count: l.count }))}
        selected={filters.layers}
        onToggle={toggle('layers')}
        onClear={clear('layers')}
      />

      <FilterGroup
        title="数据标记"
        searchable
        options={flagOptions}
        selected={filters.flags}
        onToggle={toggle('flags')}
        onClear={clear('flags')}
      />

      <FilterGroup
        title="来源文件"
        searchable
        maxVisible={6}
        listHeight={300}
        options={sourceOptions}
        selected={filters.sources}
        onToggle={toggle('sources')}
        onClear={clear('sources')}
      />

      <div className="filter-group">
        <div className="filter-head open" style={{ cursor: 'default' }}>
          <span>收集进度</span>
        </div>
        <div className="filter-body">
          {(
            [
              ['all', '全部', points.length],
              ['uncollected', '未收集', points.length - collectedCount],
              ['collected', '已收集', collectedCount],
            ] as const
          ).map(([k, label, n]) => (
            <button
              key={k}
              className={`filter-item${filters.progress === k ? ' active' : ''}`}
              onClick={() => setFilters((f) => ({ ...f, progress: k }))}
            >
              <span className="check">{filters.progress === k ? '✓' : ''}</span>
              <span className="label">{label}</span>
              <span className="count">{n.toLocaleString()}</span>
            </button>
          ))}
          <button
            className={`filter-item${filters.onlyRepeat ? ' active' : ''}`}
            onClick={() => setFilters((f) => ({ ...f, onlyRepeat: !f.onlyRepeat }))}
            title="同一坐标重复出现 ≥2 次，通常是导出工具用来做刷怪循环的写法"
          >
            <span className="check">{filters.onlyRepeat ? '✓' : ''}</span>
            <span className="label">只看刷怪循环点</span>
            <span className="count">{(facets.flags.find((f) => f.key === 'duplicated')?.count ?? 0).toLocaleString()}</span>
          </button>
        </div>
      </div>
    </aside>
  )
}

function regionColor(region: string): string {
  switch (region) {
    case '清河': return '#4fb3a1'
    case '开封': return '#d6a24a'
    case '江南': return '#c58bd6'
    default: return '#64748b'
  }
}
