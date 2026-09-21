/**
 * RightPanel.tsx —— 右侧标签页容器
 */

import { useEffect, useState } from 'react'
import type { Facets, IssuesDoc, Notes, Point } from '../lib/types'
import DetailTab from './DetailTab'
import NearbyTab from './NearbyTab'
import ExportTab from './ExportTab'
import ProgressTab from './ProgressTab'
import NotesTab from './NotesTab'
import DataTab from './DataTab'

export type TabKey = 'detail' | 'nearby' | 'export' | 'progress' | 'notes' | 'data'

interface Props {
  tab: TabKey
  setTab: (t: TabKey) => void
  facets: Facets
  notes: Notes
  issues: IssuesDoc
  point: Point | null
  all: Point[]
  filtered: Point[]
  collected: Set<string>
  onToggleCollected: (id: string) => void
  onMarkMany: (ids: string[], done: boolean) => void
  onClearCollected: () => void
  onCopy: (text: string, label: string) => void
  nearbyInput: string
  setNearbyInput: (v: string) => void
  onSelect: (id: string) => void
  className?: string
}

const TABS: { key: TabKey; label: string }[] = [
  { key: 'detail', label: '详情' },
  { key: 'nearby', label: '附近' },
  { key: 'export', label: '导出' },
  { key: 'progress', label: '进度' },
  { key: 'notes', label: '说明' },
  { key: 'data', label: '数据' },
]

export default function RightPanel(props: Props) {
  const { tab, setTab, point, notes } = props

  // 选中一个点就自动跳到详情（除非用户正在别处干活）
  const [pinned, setPinned] = useState(false)
  useEffect(() => {
    if (point && !pinned && tab !== 'detail') setTab('detail')
  }, [point, pinned, tab, setTab])

  return (
    <aside className={`side-right${props.className ? ` ${props.className}` : ''}`}>
      <div className="tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={tab === t.key ? 'active' : ''}
            onClick={() => { setTab(t.key); setPinned(true) }}
            title={
              t.key === 'notes' ? `${notes.notes.length} 份作者说明`
                : t.key === 'data' ? `解析问题 ${props.issues.issues.length} 条`
                : undefined
            }
          >
            {t.label}
            {t.key === 'progress' && props.collected.size > 0 && (
              <span className="n"> {props.collected.size}</span>
            )}
          </button>
        ))}
      </div>

      {tab === 'detail' && (
        <DetailTab
          point={point}
          facets={props.facets}
          collected={point ? props.collected.has(point.id) : false}
          onToggleCollected={props.onToggleCollected}
          onCopy={props.onCopy}
          onUseAsCenter={(p) => {
            props.setNearbyInput(`${p.x},${p.y},${p.z}`)
            setTab('nearby')
            setPinned(true)
          }}
        />
      )}
      {tab === 'nearby' && (
        <NearbyTab
          points={props.all}
          facets={props.facets}
          centerInput={props.nearbyInput}
          setCenterInput={props.setNearbyInput}
          onSelect={props.onSelect}
          onCopy={props.onCopy}
          collected={props.collected}
          onToggleCollected={props.onToggleCollected}
        />
      )}
      {tab === 'export' && (
        <ExportTab
          filtered={props.filtered}
          all={props.all}
          selectedPoint={point}
          collected={props.collected}
          onCopy={props.onCopy}
        />
      )}
      {tab === 'progress' && (
        <ProgressTab
          points={props.all}
          filtered={props.filtered}
          collected={props.collected}
          onMarkMany={props.onMarkMany}
          onClear={props.onClearCollected}
          onCopy={props.onCopy}
        />
      )}
      {tab === 'notes' && <NotesTab notes={notes} />}
      {tab === 'data' && <DataTab facets={props.facets} issues={props.issues} />}
    </aside>
  )
}
