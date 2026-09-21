/**
 * data.ts —— 数据加载
 *
 * 前端只加载 data/ 同步过来的三份文件（合计约 600 KB）：
 *   points.min.json / facets.json / notes.json
 * 6 MB 的 points.json（带完整来源行号）刻意不发给浏览器。
 */

import { decodeMin } from './core'
import type { Dataset, Facets, IssuesDoc, Notes, PointsMin } from './types'

export const DATA_FILES = ['data/points.min.json', 'data/facets.json', 'data/notes.json', 'data/issues.json'] as const

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: 'no-cache' })
  if (!res.ok) throw new Error(`加载 ${url} 失败：HTTP ${res.status}`)
  return (await res.json()) as T
}

export async function loadDataset(onProgress?: (msg: string) => void): Promise<Dataset> {
  onProgress?.('读取点位数据…')
  const min = await getJSON<PointsMin>('data/points.min.json')

  onProgress?.('读取统计与说明…')
  const [facets, notes, issues] = await Promise.all([
    getJSON<Facets>('data/facets.json'),
    getJSON<Notes>('data/notes.json'),
    getJSON<IssuesDoc>('data/issues.json'),
  ])

  onProgress?.('解包…')
  const { points } = decodeMin(min)

  // 分类元信息缺失时给个兜底，避免 UI 上出现 undefined 颜色
  const meta = { ...facets.categoryMeta }
  for (const c of facets.categories) {
    if (!meta[c.key]) meta[c.key] = { color: '#94a3b8', icon: '•', order: 999, hint: '' }
  }
  facets.categoryMeta = meta

  const byId = new Map(points.map((p) => [p.id, p]))
  return { points, facets, notes, issues, byId }
}
