/**
 * DataTab.tsx —— 数据体检
 *
 * 这一栏是给「不放心数据」的人看的：原始 27099 行怎么变成 6071 个点的、
 * 哪些行被修过、每个点怎么归的类，全部摊开。
 */

import { useState } from 'react'
import type { Facets, IssuesDoc } from '../lib/types'
import { CATEGORY_SOURCE_LABEL, FLAG_INFO, STAT_LABEL } from '../lib/labels'

interface Props {
  facets: Facets
  issues: IssuesDoc
}

const ISSUE_LABEL: Record<string, string> = {
  glued_lines_split: '两行粘成一行，已拆开',
  stray_prefix_fixed: '坐标前的手滑前缀，已剥掉',
  missing_name: '只有坐标没有名称',
  name_contains_comma: '名称里含 ASCII 逗号',
  unparsed_line: '解析不了的行',
  section_header: '段落式 ini 头',
}

export default function DataTab({ facets, issues }: Props) {
  const [openIssue, setOpenIssue] = useState(false)
  const s = facets.stats

  return (
    <div className="panel">
      <h3>从原始数据到这里</h3>
      <dl className="kv" style={{ gridTemplateColumns: '110px 1fr' }}>
        <dt>{STAT_LABEL.rawFiles}</dt><dd>{s.rawFiles}</dd>
        <dt>{STAT_LABEL.rawRows}</dt><dd>{s.rawRows.toLocaleString()}</dd>
        <dt>{STAT_LABEL.points}</dt><dd style={{ color: 'var(--accent)', fontWeight: 600 }}>{s.points.toLocaleString()}</dd>
        <dt>{STAT_LABEL.redundantRows}</dt>
        <dd>{s.redundantRows.toLocaleString()}（{((s.redundantRows / Math.max(1, s.rawRows)) * 100).toFixed(1)}%，同一坐标被多份文件重复收录）</dd>
        <dt>{STAT_LABEL.crossFilePoints}</dt><dd>{s.crossFilePoints.toLocaleString()}</dd>
        <dt>{STAT_LABEL.coLocatedPoints}</dt><dd>{s.coLocatedPoints.toLocaleString()}</dd>
        <dt>{STAT_LABEL.noteFiles}</dt><dd>{s.noteFiles}</dd>
      </dl>
      <p className="hint">
        重复行没有被丢掉：每个点都记着它在哪些文件里出现过几次（看详情页的「出现行数」和 repeat）。
      </p>

      <h3>解析问题（{issues.issues.length} 条）</h3>
      <div style={{ display: 'grid', gap: 4, marginBottom: 8 }}>
        {Object.entries(issues.stats).map(([k, n]) => (
          <div className="progress-row" key={k}>
            <span className="lb" title={k}>{ISSUE_LABEL[k] ?? k}</span>
            <span style={{ flex: '1 1 auto' }} />
            <span className="num">{n}</span>
          </div>
        ))}
      </div>
      <button className="btn small" onClick={() => setOpenIssue((v) => !v)}>
        {openIssue ? '收起明细' : '展开原始行'}
      </button>
      {openIssue && (
        <div className="preview" style={{ whiteSpace: 'pre-wrap', maxHeight: 340 }}>
          {issues.issues.map((i) => (
            `[${i.kind}] ${i.file}:${i.line}\n    ${i.detail}\n    ${i.raw.slice(0, 200)}\n`
          )).join('\n')}
        </div>
      )}

      <h3>分类是怎么判出来的</h3>
      <div className="bar-chart">
        {facets.categorySources.map((c) => (
          <div className="r" key={c.key}>
            <span className="lb" title={CATEGORY_SOURCE_LABEL[c.key] ?? c.key}>{CATEGORY_SOURCE_LABEL[c.key] ?? c.key}</span>
            <span className="bar"><i style={{ width: `${(c.count / s.points) * 100}%`, background: 'var(--jade)' }} /></span>
            <span className="num">{c.count}</span>
          </div>
        ))}
      </div>
      <p className="hint" style={{ marginTop: 8 }}>
        「占位名」那 {facets.categorySources.find((c) => c.key === 'placeholder')?.count ?? 0} 条，
        名称本身是「示例」「未备注地点」，改看它出自哪份文件来归类。
      </p>

      <h3>区域是怎么判出来的</h3>
      <div className="bar-chart">
        {facets.regionSources.map((c) => (
          <div className="r" key={c.key}>
            <span className="lb">{c.key}</span>
            <span className="bar"><i style={{ width: `${(c.count / s.points) * 100}%`, background: 'var(--accent)' }} /></span>
            <span className="num">{c.count}</span>
          </div>
        ))}
      </div>

      <h3>数据标记说明</h3>
      <div style={{ display: 'grid', gap: 8 }}>
        {facets.flags.map((f) => (
          <div className="note-block" key={f.key}>
            <div className="who">
              {FLAG_INFO[f.key]?.kind === 'warn' ? '⚠ ' : 'ℹ '}
              {FLAG_INFO[f.key]?.label ?? f.key}
              <span style={{ color: 'var(--fg-dim)' }}> · {f.count} 条 · </span>
              <span style={{ color: 'var(--fg-dim)', fontFamily: 'var(--mono)' }}>{f.key}</span>
            </div>
            <p>{FLAG_INFO[f.key]?.desc ?? '（暂无说明）'}</p>
          </div>
        ))}
      </div>

      <h3>来源文件（{facets.sourceFiles.length}）</h3>
      <div className="src-list">
        {facets.sourceFiles
          .slice()
          .sort((a, b) => b.points - a.points)
          .map((f) => (
            <div className="src-item" key={f.file}>
              <span className="c">{f.points}</span>
              <span className="f">{f.file}</span>
              <span className="c" style={{ color: 'var(--fg-dim)' }}>{f.codec} · {f.rows} 行</span>
            </div>
          ))}
      </div>
    </div>
  )
}
