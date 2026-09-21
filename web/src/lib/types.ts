/**
 * types.ts —— 数据契约
 *
 * 这里的字段名与 tools/etl.py 的输出严格对应，改动时两边要一起改。
 */

/** 点位在某一份原始文件里的出处 */
export interface SourceRef {
  file: string
  lines: number[]
  count: number
}

/** 一个规范化后的坐标点位 */
export interface Point {
  id: string
  x: number
  y: number
  z: number
  /** 原始名称，逐字保留 */
  name: string
  /** 去掉括号备注与尾部序号后的主体名 */
  base: string
  /** 尾部序号，无则 null */
  index: number | null
  /** 括号里 / 破折号后的备注 */
  note: string
  category: string
  /** 分类是怎么判出来的：dict / pattern / path / person / placeholder / fallback */
  categorySource: string
  material: string
  place: string
  /** 大地图：清河 / 开封 / 江南 */
  region: string
  /** 区域是怎么判出来的：name / place / path / coord / nearest */
  regionSource: string
  /** 高度分层：地表 / 地下 / 深层地下 / 高台 */
  layer: string
  /** 同一坐标在单份文件里重复出现的最大次数（刷怪循环用） */
  repeat: number
  /** 该点位在全部文件里出现的总行数 */
  totalRows: number
  flags: string[]
  /** 紧凑版里只有来源索引，展开后由 data.ts 补全成这个结构 */
  sourceIdx: number[]
  sources: SourceRef[]
}

/** facets.json 的结构 */
export interface CategoryMeta {
  color: string
  icon: string
  order: number
  hint: string
}

export interface FacetItem {
  key: string
  count: number
}

export interface SourceFileInfo {
  file: string
  codec: string
  bytes: number
  rows: number
  isRpa: boolean
  points: number
}

export interface Facets {
  version: number
  stats: {
    rawFiles: number
    rawRows: number
    points: number
    redundantRows: number
    issues: number
    noteFiles: number
    crossFilePoints: number
    coLocatedPoints: number
    regionUnknown: number
    regionFilledByNearest: number
  }
  categoryMeta: Record<string, CategoryMeta>
  categories: FacetItem[]
  regions: FacetItem[]
  materials: FacetItem[]
  places: FacetItem[]
  layers: FacetItem[]
  regionSources: FacetItem[]
  categorySources: FacetItem[]
  flags: FacetItem[]
  sourceFiles: SourceFileInfo[]
}

export interface NoteEntry {
  file: string
  dir: string
  lines: string[]
  alias_of?: string
}

export interface Notes {
  credits: Record<string, string>
  notes: NoteEntry[]
}

/** issues.json —— ETL 的解析问题台账 */
export interface IssueEntry {
  kind: string
  detail: string
  file: string
  line: number
  raw: string
}

export interface IssuesDoc {
  stats: Record<string, number>
  issues: IssueEntry[]
}

/** points.min.json 的结构：字典表 + 行数组 */
export interface PointsMin {
  version: number
  fields: string[]
  dicts: {
    category: string[]
    region: string[]
    material: string[]
    place: string[]
    layer: string[]
    flags: string[]
    sources: string[]
    categorySource: string[]
    regionSource: string[]
  }
  rows: unknown[][]
}

export interface Dataset {
  points: Point[]
  facets: Facets
  notes: Notes
  issues: IssuesDoc
  /** 点位 id → 点位 */
  byId: Map<string, Point>
}
