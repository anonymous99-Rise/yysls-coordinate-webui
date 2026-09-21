/**
 * core.ts —— 纯逻辑层（不碰 DOM、不碰网络，因此能在 Node 里直接跑测试）
 *
 * 搜索 / 筛选 / 排序 / 附近 / 导出 / 坐标格式化 全在这里。
 * 组件只负责渲染，逻辑一律从这里取 —— 好处是 tools/test_core.mjs
 * 不用浏览器就能把这些行为断言一遍。
 */

import type { Facets, Point, PointsMin, SourceRef } from './types'

// --------------------------------------------------------------------------
// 数据解包
// --------------------------------------------------------------------------
export interface Decoded {
  points: Point[]
  sourceNames: string[]
  flagNames: string[]
}

export function decodeMin(min: PointsMin): Decoded {
  const d = min.dicts
  const rows = min.rows

  // 先把 sources 展开成 SourceRef：同一文件的多个点位共享同一对象，省内存
  const srcCache = new Map<string, SourceRef>()
  const sourceOf = (name: string): SourceRef => {
    let s = srcCache.get(name)
    if (!s) {
      s = { file: name, lines: [], count: 0 }
      srcCache.set(name, s)
    }
    return s
  }

  const points: Point[] = new Array(rows.length)

  for (let i = 0; i < rows.length; i++) {
    const r = rows[i] as [
      number, number, number, string, string, number, string,
      number, number, number, number, number, number, number,
      number, number[], number, number,
    ]
    // flagBits → 名字（同一组合复用同一个数组，避免 6000 份重复数组）
    const flagNames = bitsToNames(r[14], d.flags)
    const srcRefs = r[15].map((idx) => sourceOf(d.sources[idx]))

    points[i] = {
      id: `p${String(i + 1).padStart(5, '0')}`,
      x: r[0],
      y: r[1],
      z: r[2],
      name: r[3],
      base: r[4],
      index: r[5] < 0 ? null : r[5],
      note: r[6],
      category: d.category[r[7]] ?? '',
      material: d.material[r[8]] ?? '',
      place: d.place[r[9]] ?? '',
      region: d.region[r[10]] ?? '',
      layer: d.layer[r[11]] ?? '',
      repeat: r[12],
      totalRows: r[13],
      flags: flagNames,
      sourceIdx: r[15],
      sources: srcRefs,
      categorySource: d.categorySource[r[16]] ?? '',
      regionSource: d.regionSource[r[17]] ?? '',
    }
  }

  return { points, sourceNames: d.sources, flagNames: d.flags }
}

const bitCache = new Map<number, string[]>()
export function bitsToNames(bits: number, flagNames: string[]): string[] {
  const hit = bitCache.get(bits)
  if (hit) return hit
  const out: string[] = []
  for (let i = 0; i < flagNames.length; i++) {
    if (bits & (1 << i)) out.push(flagNames[i])
  }
  bitCache.set(bits, out)
  return out
}

// --------------------------------------------------------------------------
// 坐标格式化
// --------------------------------------------------------------------------
/** 去掉浮点尾巴：-58.9953 → "-58.9953"，-3822.8 → "-3822.8"，3.1000 → "3.1" */
export function trimNum(v: number, maxDecimals = 4): string {
  if (!Number.isFinite(v)) return '0'
  let s = v.toFixed(maxDecimals)
  if (s.includes('.')) s = s.replace(/0+$/, '').replace(/\.$/, '')
  return s === '-0' ? '0' : s
}

export type CoordFormat = 'csv' | 'spaced' | 'xyz' | 'plain' | 'json'

export const COORD_FORMAT_LABEL: Record<CoordFormat, string> = {
  csv: 'x,y,z,名称',
  spaced: 'x ,y ,z ,名称（带空格）',
  xyz: 'x,y,z（不带名称）',
  plain: '名称 → x,y,z（阅读用）',
  json: 'JSON 对象',
}

/**
 * 按指定模板输出一行。
 * 名称缺失的那 1 个点（原始文件里只有 x,y,z）导出时补成「未备注地点」——
 * 和原始库里其他无名点位的写法保持一致，也避免尾部空字段把导入工具搞崩。
 */
export function formatCoord(p: Point, fmt: CoordFormat): string {
  const x = trimNum(p.x)
  const y = trimNum(p.y)
  const z = trimNum(p.z)
  const raw = p.name || '未备注地点'
  const name = p.note ? `${raw}（${p.note}）` : raw
  switch (fmt) {
    case 'csv':
      return `${x},${y},${z},${name}`
    case 'spaced':
      return `${x} ,${y} ,${z} ,${name}`
    case 'xyz':
      return `${x},${y},${z}`
    case 'plain':
      return `${name}  →  ${x}, ${y}, ${z}`
    case 'json':
      return JSON.stringify({ x: p.x, y: p.y, z: p.z, name: raw, note: p.note || undefined })
  }
}

/** 常用的那一种：直接给你能贴进坐标软件的一行 */
export function quickCopy(p: Point): string {
  return formatCoord(p, 'csv')
}

// --------------------------------------------------------------------------
// 查询解析：支持 cat:材料 region:清河 这类字段前缀
// --------------------------------------------------------------------------
export interface Filters {
  q: string
  regions: string[]
  categories: string[]
  materials: string[]
  places: string[]
  layers: string[]
  flags: string[]
  sources: string[]
  progress: 'all' | 'collected' | 'uncollected'
  /** 只看「刷怪循环」点（同一坐标重复 ≥ 2 次） */
  onlyRepeat: boolean
}

export const EMPTY_FILTERS: Filters = {
  q: '',
  regions: [],
  categories: [],
  materials: [],
  places: [],
  layers: [],
  flags: [],
  sources: [],
  progress: 'all',
  onlyRepeat: false,
}

export interface ParsedQuery {
  /** 自由文本词（全部要命中，AND） */
  tokens: string[]
  /** 从 `key:value` 里抽出来的追加筛选 */
  fields: Partial<Pick<Filters, 'regions' | 'categories' | 'materials' | 'places' | 'layers' | 'flags' | 'sources'>>
}

const FIELD_ALIAS: Record<string, keyof ParsedQuery['fields']> = {
  region: 'regions',
  reg: 'regions',
  区域: 'regions',
  cat: 'categories',
  category: 'categories',
  分类: 'categories',
  mat: 'materials',
  material: 'materials',
  材料: 'materials',
  place: 'places',
  地点: 'places',
  layer: 'layers',
  层: 'layers',
  flag: 'flags',
  标志: 'flags',
  src: 'sources',
  source: 'sources',
  来源: 'sources',
  file: 'sources',
}

/**
 * 把搜索框里的一串字拆成词与字段条件。
 * 例：`龙骨 region:清河 flag:placeholder_name`
 *
 * 分隔符同时接受空格和 `+`：分享出去的链接里空格可能被编码成 `+` 或 `%2B`，
 * 两种都当分隔符处理，否则别人打开你的链接会看到「0 条结果」且毫无提示。
 */
export function parseQuery(q: string): ParsedQuery {
  const tokens: string[] = []
  const fields: ParsedQuery['fields'] = {}

  for (const raw of q.trim().split(/[\s+]+/)) {
    if (!raw) continue
    const m = /^([a-zA-Z\u4e00-\u9fa5_]+)[:：](.+)$/.exec(raw)
    if (m) {
      const key = FIELD_ALIAS[m[1].toLowerCase()] ?? FIELD_ALIAS[m[1]]
      if (key) {
        const list = (fields[key] ??= [] as string[])
        list.push(m[2])
        continue
      }
    }
    tokens.push(raw.toLowerCase())
  }
  return { tokens, fields }
}

/** 单个点位是否命中全部自由词 */
export function matchTokens(p: Point, tokens: string[]): boolean {
  if (!tokens.length) return true
  const hay = pointHaystack(p)
  for (const t of tokens) {
    if (!hay.includes(t)) return false
  }
  return true
}

/** 拼接可检索文本；重建两次以上就缓存 —— 搜索时反复调用 */
const hayCache = new WeakMap<Point, string>()
/**
 * 自由文本只搜「这个点是什么」，**不搜它出自哪份原始文件**。
 *
 * 这里踩过一个坑：原始库里有一份文件名叫
 * 「草药 菌子 白术一丈红 魏紫 … 龙骨 毒晶芍药 黄铜 酒香玉.ini」，
 * 把材料名全塞进了文件名。若把来源文件名并进检索文本，搜「龙骨」会
 * 命中 1542 个点（实际只有 34 个叫龙骨），搜索直接废掉。
 * 要按来源筛请用左侧「来源」面板或 `src:` 前缀 —— 那才是它该待的地方。
 */
export function pointHaystack(p: Point): string {
  const cached = hayCache.get(p)
  if (cached !== undefined) return cached
  const s = [p.name, p.base, p.note, p.material, p.place, p.region, p.category, p.layer]
    .join('\u0001')
    .toLowerCase()
  hayCache.set(p, s)
  return s
}

// --------------------------------------------------------------------------
// 筛选
// --------------------------------------------------------------------------
export function applyFilters(points: Point[], f: Filters): Point[] {
  const { tokens, fields } = parseQuery(f.q)

  const regions = union(f.regions, fields.regions)
  const categories = union(f.categories, fields.categories)
  const materials = union(f.materials, fields.materials)
  const places = union(f.places, fields.places)
  const layers = union(f.layers, fields.layers)
  const flags = union(f.flags, fields.flags)
  const sources = union(f.sources, fields.sources)

  const anyFacet =
    regions.length || categories.length || materials.length || places.length ||
    layers.length || flags.length || sources.length

  if (!tokens.length && !anyFacet && f.progress === 'all' && !f.onlyRepeat) {
    return points
  }

  const out: Point[] = []
  for (const p of points) {
    if (tokens.length && !matchTokens(p, tokens)) continue
    if (regions.length && !regions.includes(p.region)) continue
    if (categories.length && !categories.includes(p.category)) continue
    if (materials.length && !materials.includes(p.material)) continue
    if (places.length && !places.includes(p.place)) continue
    if (layers.length && !layers.includes(p.layer)) continue
    if (flags.length && !flags.every((fl) => p.flags.includes(fl))) continue
    // 来源用子串匹配：原始文件名里带空格（「草药 菌子 白术一丈红 ….ini」），
    // 而搜索框是按空格分词的，要求全等的话 src: 基本没法用。
    if (sources.length && !p.sources.some((s) => sources.some((f) => s.file.includes(f)))) continue
    if (f.onlyRepeat && p.repeat < 2) continue
    out.push(p)
  }
  return out
}

function union(a: string[], b?: string[]): string[] {
  if (!b || !b.length) return a
  return a.length ? [...a, ...b] : [...b]
}

// --------------------------------------------------------------------------
// 排序
// --------------------------------------------------------------------------
export type SortKey =
  | 'name' | 'region' | 'category' | 'material' | 'place'
  | 'repeat' | 'totalRows' | 'files' | 'z'

export const SORT_LABEL: Record<SortKey, string> = {
  name: '名称',
  region: '区域',
  category: '分类',
  material: '材料',
  place: '地点',
  repeat: '重复次数',
  totalRows: '总出现行数',
  files: '来源文件数',
  z: '高度 Z',
}

/**
 * 比较函数一律写成「升序」语义，升降由 sortPoints 的 dir 统一翻转。
 * （早先几个 key 自己写成 b-a 又叠了 reverse，结果 desc 排成了 asc。）
 */
export function compareBy(key: SortKey) {
  switch (key) {
    case 'name': return (a: Point, b: Point) => a.name.localeCompare(b.name, 'zh-Hans-CN')
    case 'region': return (a: Point, b: Point) => a.region.localeCompare(b.region, 'zh') || a.place.localeCompare(b.place, 'zh')
    case 'category': return (a: Point, b: Point) => a.category.localeCompare(b.category, 'zh') || a.name.localeCompare(b.name, 'zh')
    case 'material': return (a: Point, b: Point) => a.material.localeCompare(b.material, 'zh') || a.name.localeCompare(b.name, 'zh')
    case 'place': return (a: Point, b: Point) => a.place.localeCompare(b.place, 'zh') || a.name.localeCompare(b.name, 'zh')
    case 'repeat': return (a: Point, b: Point) => a.repeat - b.repeat
    case 'totalRows': return (a: Point, b: Point) => a.totalRows - b.totalRows
    case 'files': return (a: Point, b: Point) => a.sources.length - b.sources.length
    case 'z': return (a: Point, b: Point) => a.z - b.z
  }
}

export function sortPoints(points: Point[], key: SortKey, dir: 'asc' | 'desc'): Point[] {
  const cmp = compareBy(key)
  const out = [...points].sort(cmp)
  if (dir === 'desc') out.reverse()
  return out
}

// --------------------------------------------------------------------------
// 附近查询 —— 站在游戏里想知道脚边有什么
// --------------------------------------------------------------------------
export interface NearbyHit {
  point: Point
  /** 三维真实距离 */
  dist: number
  /** 水平距离（忽略高度） */
  flat: number
  /** 高度差 */
  dz: number
}

export interface NearbyOptions {
  top?: number
  /** 三维（默认，推荐）还是只看水平距离 */
  mode?: '3d' | 'flat'
  /** 只找这个距离以内的 */
  radius?: number
  category?: string
}

export function nearby(points: Point[], target: [number, number, number], opts: NearbyOptions = {}): NearbyHit[] {
  const [tx, ty, tz] = target
  const top = opts.top ?? 20
  const mode = opts.mode ?? '3d'
  const radius = opts.radius ?? Infinity

  const hits: NearbyHit[] = []
  for (const p of points) {
    if (opts.category && p.category !== opts.category) continue
    const dx = p.x - tx
    const dy = p.y - ty
    const dz = p.z - tz
    const flat = Math.hypot(dx, dy)
    const dist = Math.hypot(dx, dy, dz)
    const key = mode === '3d' ? dist : flat
    if (key > radius) continue
    hits.push({ point: p, dist, flat, dz })
  }
  hits.sort((a, b) => (mode === '3d' ? a.dist - b.dist : a.flat - b.flat) || a.dz - b.dz)
  return hits.slice(0, top)
}

/** 解析用户粘贴的坐标："-2400,700,-60" / "-2400 700 -60" / 带名称的一整行都行 */
export function parseCoordInput(raw: string): [number, number, number] | null {
  const nums = raw.match(/-?\d+(?:\.\d+)?/g)
  if (!nums || nums.length < 3) return null
  const [x, y, z] = nums.slice(0, 3).map(Number)
  if (![x, y, z].every(Number.isFinite)) return null
  return [x, y, z]
}

// --------------------------------------------------------------------------
// 导出
// --------------------------------------------------------------------------
export interface ExportOptions {
  format: CoordFormat
  /** 按 repeat 次数重复输出（挂机刷怪循环要的就是这个） */
  repeatTimes: boolean
  /** 是否加一行注释头 */
  header: boolean
  /** 导出前再截断 */
  limit?: number
}

export function buildExport(points: Point[], opts: ExportOptions): string {
  const list = opts.limit ? points.slice(0, opts.limit) : points
  const lines: string[] = []
  if (opts.header) {
    const stamp = new Date().toISOString().slice(0, 19).replace('T', ' ')
    lines.push(`# 燕云十六声坐标库导出  ${list.length} 条  ${stamp}`)
    lines.push('# 格式：x,y,z,名称   （可直接导入坐标软件）')
  }
  if (opts.format === 'json') {
    return JSON.stringify(list.map((p) => JSON.parse(formatCoord(p, 'json'))), null, 2)
  }
  for (const p of list) {
    const line = formatCoord(p, opts.format)
    if (opts.repeatTimes && p.repeat > 1) {
      for (let i = 0; i < p.repeat; i++) lines.push(line)
    } else {
      lines.push(line)
    }
  }
  return lines.join('\n') + '\n'
}

/** 估算导出后的行数（导出确认框里显示） */
export function estimateExportLines(points: Point[], opts: ExportOptions): number {
  const list = opts.limit ? points.slice(0, opts.limit) : points
  let n = 0
  for (const p of list) n += opts.repeatTimes ? Math.max(1, p.repeat) : 1
  return n + (opts.header ? 2 : 0)
}

// --------------------------------------------------------------------------
// 收集进度
// --------------------------------------------------------------------------
export interface ProgressBucket {
  key: string
  total: number
  done: number
  pct: number
}

export function progressBy(points: Point[], collected: Set<string>, field: keyof Point): ProgressBucket[] {
  const map = new Map<string, { total: number; done: number }>()
  for (const p of points) {
    const key = String(p[field] ?? '') || '(空)'
    const b = map.get(key) ?? { total: 0, done: 0 }
    b.total++
    if (collected.has(p.id)) b.done++
    map.set(key, b)
  }
  return [...map.entries()]
    .map(([key, b]) => ({ key, ...b, pct: b.total ? b.done / b.total : 0 }))
    .sort((a, b) => b.total - a.total)
}

// --------------------------------------------------------------------------
// 通用小工具
// --------------------------------------------------------------------------
export function groupCount<T>(items: T[], keyOf: (t: T) => string): Map<string, number> {
  const m = new Map<string, number>()
  for (const it of items) {
    const k = keyOf(it)
    m.set(k, (m.get(k) ?? 0) + 1)
  }
  return m
}

/** 地图散点的包围盒（带 5% 边距） */
export function bounds(points: Point[]): { minX: number; maxX: number; minY: number; maxY: number } {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
  for (const p of points) {
    if (p.x < minX) minX = p.x
    if (p.x > maxX) maxX = p.x
    if (p.y < minY) minY = p.y
    if (p.y > maxY) maxY = p.y
  }
  if (!Number.isFinite(minX)) return { minX: 0, maxX: 1, minY: 0, maxY: 1 }
  const padX = (maxX - minX) * 0.04 || 1
  const padY = (maxY - minY) * 0.04 || 1
  return { minX: minX - padX, maxX: maxX + padX, minY: minY - padY, maxY: maxY + padY }
}

export function categoryColor(facets: Facets, category: string): string {
  return facets.categoryMeta?.[category]?.color ?? '#94a3b8'
}

export function categoryIcon(facets: Facets, category: string): string {
  return facets.categoryMeta?.[category]?.icon ?? '•'
}
