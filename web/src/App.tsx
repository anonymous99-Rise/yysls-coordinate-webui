/**
 * App.tsx —— 骨架与状态中枢
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import Sidebar from './components/Sidebar'
import Toolbar from './components/Toolbar'
import PointTable from './components/PointTable'
import MapView from './components/MapView'
import RightPanel, { type TabKey } from './components/RightPanel'
import { loadDataset } from './lib/data'
import type { Dataset } from './lib/types'
import {
  applyFilters, EMPTY_FILTERS, quickCopy, sortPoints,
  type Filters, type SortKey,
} from './lib/core'
import { useCollected, useTheme, useUrlState, type SortState, type ViewMode } from './lib/store'

export default function App() {
  const [data, setData] = useState<Dataset | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loadingMsg, setLoadingMsg] = useState('正在加载坐标数据…')
  const [toastMsg, setToastMsg] = useState<string | null>(null)

  const { theme, toggle: toggleTheme } = useTheme()
  const { collected, count: collectedCount, toggle: toggleCollected, clear: clearCollected, markMany } = useCollected()

  const [urlState, setUrlState] = useUrlState({
    filters: EMPTY_FILTERS,
    view: 'table' as ViewMode,
    sort: { key: 'name', dir: 'asc' } as SortState,
    selected: null,
  })

  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [panelOpen, setPanelOpen] = useState(false)
  const [tab, setTab] = useState<TabKey>('detail')
  const [nearbyInput, setNearbyInput] = useState('')

  // ---- 载入 ------------------------------------------------------------
  useEffect(() => {
    loadDataset(setLoadingMsg).then(setData).catch((e: Error) => setError(e.message))
  }, [])

  // ---- 派生 ------------------------------------------------------------
  const points = data?.points ?? []

  const filtered = useMemo(() => {
    const base = applyFilters(points, urlState.filters)
    const progressed =
      urlState.filters.progress === 'collected'
        ? base.filter((p) => collected.has(p.id))
        : urlState.filters.progress === 'uncollected'
          ? base.filter((p) => !collected.has(p.id))
          : base
    return sortPoints(progressed, urlState.sort.key, urlState.sort.dir)
  }, [points, urlState.filters, urlState.sort, collected])

  const selectedPoint = useMemo(
    () => (urlState.selected ? points.find((p) => p.id === urlState.selected) ?? null : null),
    [points, urlState.selected]
  )

  const setFilters = useCallback(
    (fn: (f: Filters) => Filters) => setUrlState((s) => ({ ...s, filters: fn(s.filters) })),
    [setUrlState]
  )

  const onSort = useCallback(
    (key: SortKey) =>
      setUrlState((s) => ({
        ...s,
        sort: { key, dir: s.sort.key === key && s.sort.dir === 'asc' ? 'desc' : 'asc' },
      })),
    [setUrlState]
  )

  const showToast = useCallback((msg: string) => {
    setToastMsg(msg)
    window.setTimeout(() => setToastMsg((cur) => (cur === msg ? null : cur)), 1800)
  }, [])

  const copy = useCallback(
    async (text: string, label: string) => {
      try {
        await navigator.clipboard.writeText(text)
        showToast(`已复制：${label}`)
      } catch {
        // 非 https / 无权限时的兜底
        const ta = document.createElement('textarea')
        ta.value = text
        ta.style.position = 'fixed'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select()
        const ok = document.execCommand('copy')
        ta.remove()
        showToast(ok ? `已复制：${label}` : '复制失败，请手动选中')
      }
    },
    [showToast]
  )

  const select = useCallback(
    (id: string) => setUrlState((s) => ({ ...s, selected: s.selected === id ? null : id })),
    [setUrlState]
  )

  // ---- 快捷键 ----------------------------------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      const typing = target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable
      if (e.key === '/' && !typing) {
        e.preventDefault()
        document.getElementById('global-search')?.focus()
      }
      if (e.key === 'Escape' && !typing) {
        setSidebarOpen(false)
        setPanelOpen(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // ---- 渲染 ------------------------------------------------------------
  if (error) {
    return (
      <div className="error-box">
        <h2>数据加载失败</h2>
        <p>前端要读 <code>data/points.min.json</code> 等三份文件。如果你是把 index.html 单独拷出来的，它是找不到数据的。</p>
        <pre>{error}</pre>
        <p className="hint">
          正确做法：先跑 <code>python tools/etl.py</code>，再 <code>npm run build</code>，
          然后把整个 <code>dist/</code> 目录（而不是单个 html 文件）拿去部署；
          或者用 <code>npm run dev</code> 起本地服务。
        </p>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="loading">
        <div className="spinner" />
        <div>{loadingMsg}</div>
      </div>
    )
  }

  const { facets } = data

  return (
    <div className="app">
      <header className="topbar">
        <button className="btn icon sidebar-toggle" onClick={() => setSidebarOpen((v) => !v)} title="筛选">☰</button>
        <div className="brand">
          <h1>燕云十六声 · 坐标库</h1>
          <span className="sub">where winds meet · coordinates</span>
        </div>

        <div className="topbar-stats">
          <span className="chip"><b>{facets.stats.points.toLocaleString()}</b> 点位</span>
          <span className="chip">原始 <b>{facets.stats.rawRows.toLocaleString()}</b> 行</span>
          {facets.regions.map((r) => (
            <span className="chip" key={r.key}>{r.key} <b>{r.count.toLocaleString()}</b></span>
          ))}
          <span className="chip"><b>{facets.stats.coLocatedPoints}</b> 待实地确认</span>
          {collectedCount > 0 && <span className="chip"><b>{collectedCount}</b> 已收集</span>}
        </div>

        <div className="topbar-actions">
          <button className="btn icon" onClick={toggleTheme} title="切换深浅色">
            {theme === 'dark' ? '☀' : '🌙'}
          </button>
          <button className="btn icon" onClick={() => { setTab('notes'); setPanelOpen(true) }} title="作者说明与署名">?</button>
          <button className="btn icon sidebar-toggle" onClick={() => setPanelOpen((v) => !v)} title="详情面板">▤</button>
        </div>
      </header>

      <div className="body">
        <Sidebar
          facets={facets}
          points={points}
          filters={urlState.filters}
          setFilters={setFilters}
          collectedCount={collectedCount}
          className={sidebarOpen ? 'open' : ''}
        />

        <main className="main">
          <Toolbar
            filters={urlState.filters}
            setFilters={setFilters}
            view={urlState.view}
            setView={(v) => setUrlState((s) => ({ ...s, view: v }))}
            sort={urlState.sort}
            onSort={onSort}
            resultCount={filtered.length}
            totalCount={points.length}
            collectedCount={collectedCount}
            onExport={() => { setTab('export'); setPanelOpen(true) }}
          />

          {urlState.view === 'table' ? (
            <PointTable
              points={filtered}
              facets={facets}
              selected={urlState.selected}
              onSelect={select}
              collected={collected}
              onToggleCollected={toggleCollected}
              sort={urlState.sort}
              onSort={onSort}
              onCopy={copy}
            />
          ) : (
            <>
              <div className="content" style={{ padding: 0 }}>
                <MapView
                  points={filtered}
                  facets={facets}
                  selected={urlState.selected}
                  onSelect={select}
                  collected={collected}
                  onOpenDetail={() => setPanelOpen(true)}
                />
              </div>
              <div className="pager">
                <span>地图上有 <b style={{ color: 'var(--fg)' }}>{filtered.length.toLocaleString()}</b> 个点</span>
                <span className="spacer" />
                <span className="hint" style={{ margin: 0 }}>滚轮缩放 · 拖拽平移 · 点击选中</span>
              </div>
            </>
          )}
        </main>

        <RightPanel
          tab={tab}
          setTab={setTab}
          facets={facets}
          notes={data.notes}
          issues={data.issues}
          point={selectedPoint}
          all={points}
          filtered={filtered}
          collected={collected}
          onToggleCollected={toggleCollected}
          onMarkMany={markMany}
          onClearCollected={clearCollected}
          onCopy={copy}
          nearbyInput={nearbyInput}
          setNearbyInput={setNearbyInput}
          onSelect={select}
          className={panelOpen ? 'open' : ''}
        />
      </div>

      {toastMsg && <div className="toast">{toastMsg}</div>}

      {(sidebarOpen || panelOpen) && (
        <div
          className="mask"
          style={{ background: 'transparent', zIndex: 25 }}
          onClick={() => { setSidebarOpen(false); setPanelOpen(false) }}
        />
      )}
    </div>
  )
}

/** 让 ts 别忘了 quickCopy 被工具栏/表格用着（按需 re-export，便于以后拆包） */
export { quickCopy }
