/**
 * NotesTab.tsx —— 原始作者的注意事项与署名
 *
 * 原始库里有 5 份说明文档（注意事项 / 整合说明），里面是真正有用的操作提示：
 * 「传送到旁边 NPC 不在就退出到标题再进」「醉花阴的见闻有些要对话三四次」
 * 这些不是坐标，但比坐标更容易踩坑，所以单独一栏显眼地摆出来。
 *
 * 作者明确写了「免费分享请勿倒卖，分享请标注作者」—— 这份署名必须留着。
 */

import { useMemo, useState } from 'react'
import type { Notes } from '../lib/types'

interface Props {
  notes: Notes
}

/** 把纯文本里的 URL 变成可点的链接 */
function linkify(line: string) {
  const parts = line.split(/(https?:\/\/[^\s，。）]+)/g)
  return parts.map((seg, i) =>
    /^https?:\/\//.test(seg)
      ? <a key={i} href={seg} target="_blank" rel="noreferrer noopener">{seg}</a>
      : <span key={i}>{seg}</span>
  )
}

export default function NotesTab({ notes }: Props) {
  const groups = useMemo(() => {
    const m = new Map<string, typeof notes.notes>()
    for (const n of notes.notes) {
      const arr = m.get(n.dir) ?? []
      arr.push(n)
      m.set(n.dir, arr)
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0], 'zh'))
  }, [notes.notes])

  const [open, setOpen] = useState<string | null>(groups[0]?.[0] ?? null)

  return (
    <div className="panel">
      <h3>署名与声明</h3>
      <div className="note-block">
        {Object.entries(notes.credits).map(([k, v]) => (
          <p key={k}>
            <span style={{ color: 'var(--fg-dim)' }}>{k}：</span>
            {linkify(v)}
          </p>
        ))}
      </div>

      <h3>作者留的注意事项（{groups.length} 份）</h3>
      <p className="hint" style={{ marginBottom: 10 }}>
        这些是原始包里的说明文档原文，不是我编的。踩坑之前先看一眼。
      </p>

      <div style={{ display: 'grid', gap: 8 }}>
        {groups.map(([dir, list]) => {
          const isOpen = open === dir
          return (
            <div key={dir} className="note-block" style={{ padding: 0, overflow: 'hidden' }}>
              <button
                className="filter-head open"
                style={{ padding: '8px 10px' }}
                onClick={() => setOpen(isOpen ? null : dir)}
              >
                <span className="caret" style={{ transform: isOpen ? 'rotate(90deg)' : 'none' }}>▶</span>
                <span style={{ textTransform: 'none', letterSpacing: 0 }}>{dir || '(根目录)'}</span>
                <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--fg-dim)' }}>{list.length} 份</span>
              </button>
              {isOpen && (
                <div style={{ padding: '4px 12px 12px' }}>
                  {list.map((n, i) => (
                    <div key={n.file + i} style={{ marginBottom: 10 }}>
                      <div className="who" style={{ fontSize: 11, color: 'var(--accent)', fontFamily: 'var(--mono)' }}>
                        {n.file}{n.alias_of ? `（与 ${n.alias_of} 内容相同）` : ''}
                      </div>
                      {n.lines.map((ln, j) => <p key={j}>{linkify(ln)}</p>)}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
