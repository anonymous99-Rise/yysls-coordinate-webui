#!/usr/bin/env node
/**
 * test_core.mjs —— 前端核心逻辑的断言测试（不需要浏览器）
 *
 * 直接 import web/src/lib/core.ts（Node 24 原生支持 TS 类型擦除），
 * 用真实的 data/points.min.json 跑断言 —— 所以这不只是单测，
 * 也是「数据 + 逻辑」接起来之后的联调验收。
 *
 * 用法：node tools/test_core.mjs
 */

import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  applyFilters,
  bounds,
  buildExport,
  decodeMin,
  EMPTY_FILTERS,
  estimateExportLines,
  formatCoord,
  matchTokens,
  nearby,
  parseCoordInput,
  parseQuery,
  progressBy,
  quickCopy,
  sortPoints,
  trimNum,
} from '../web/src/lib/core.ts'

const here = path.dirname(fileURLToPath(import.meta.url))
const project = path.resolve(here, '..')
const readJSON = (p) => JSON.parse(readFileSync(path.join(project, p), 'utf8'))

let pass = 0
let fail = 0

function check(label, cond, detail = '') {
  if (cond) {
    pass++
    console.log(`  [OK]   ${label}${detail ? `  ${detail}` : ''}`)
  } else {
    fail++
    console.log(`  [FAIL] ${label}  ${detail}`)
  }
}

function eq(label, actual, expected) {
  check(label, Object.is(actual, expected) || actual === expected, `实际=${actual} 期望=${expected}`)
}

// ---------------------------------------------------------------------------
console.log('== 0. 加载真实数据 ==')
const min = readJSON('data/points.min.json')
const facets = readJSON('data/facets.json')
const { points } = decodeMin(min)
check('解包成功', points.length > 0, `${points.length} 个点位`)
eq('点位数与 facets 一致', points.length, facets.stats.points)

// ---------------------------------------------------------------------------
console.log('== 1. 解包完整性 ==')
check('id 唯一', new Set(points.map((p) => p.id)).size === points.length)
check('每个点位都有区域', points.every((p) => p.region))
check('每个点位都有分类', points.every((p) => p.category))
check(
  'totalRows 合计 == 原始行数',
  points.reduce((s, p) => s + p.totalRows, 0) === facets.stats.rawRows,
  `${points.reduce((s, p) => s + p.totalRows, 0)} / ${facets.stats.rawRows}`
)
check('sources 已展开', points.every((p) => p.sources.length > 0 && p.sources.every((s) => s.file)))
check(
  'flagBits 已还原',
  points.some((p) => p.flags.includes('cross_file')) && points.every((p) => Array.isArray(p.flags))
)

// ---------------------------------------------------------------------------
console.log('== 2. 各类计数自洽（筛选结果 vs facets 统计）==')
for (const cat of facets.categories.slice(0, 4)) {
  const got = applyFilters(points, { ...EMPTY_FILTERS, categories: [cat.key] }).length
  eq(`分类「${cat.key}」`, got, cat.count)
}
for (const reg of facets.regions) {
  const got = applyFilters(points, { ...EMPTY_FILTERS, regions: [reg.key] }).length
  eq(`区域「${reg.key}」`, got, reg.count)
}
{
  const top = facets.materials[0]
  const got = applyFilters(points, { ...EMPTY_FILTERS, materials: [top.key] }).length
  eq(`材料「${top.key}」（最多的一种）`, got, top.count)
}

// ---------------------------------------------------------------------------
console.log('== 3. 搜索 ==')
{
  const r = applyFilters(points, { ...EMPTY_FILTERS, q: '龙骨' })
  check('搜「龙骨」有结果', r.length > 0, `${r.length} 条`)
  check(
    '结果都真的跟「龙骨」有关（而不是被来源文件名误伤）',
    r.every((p) => [p.name, p.base, p.note, p.material, p.place].some((s) => (s ?? '').includes('龙骨'))),
    `命中 ${r.length} 条，其中分类为材料的有 ${r.filter((p) => p.category === '材料').length} 条`
  )
}
{
  // 回归测试：文件名里塞满材料名的那份文件，不该把搜索污染了
  const messy = points.filter((p) => p.sources.some((s) => s.file.includes('草药 菌子 白术一丈红')))
  check('存在那份「文件名塞满材料名」的来源文件', messy.length > 100, `${messy.length} 条来自它`)
  const r = applyFilters(points, { ...EMPTY_FILTERS, q: '龙骨' })
  check(
    '自由文本不会因为文件名而误命中',
    r.length < messy.length / 10,
    `龙骨命中 ${r.length} 条；若把来源并进检索文本会是 ${messy.length}+ 条`
  )
  const bySrc = applyFilters(points, { ...EMPTY_FILTERS, q: 'src:草药' })
  check('按来源筛要用 src: 前缀（子串匹配）', bySrc.length >= messy.length, `src:草药 命中 ${bySrc.length} 条`)
  const byExactSrc = applyFilters(points, { ...EMPTY_FILTERS, q: 'src:一丈红全图' })
  check('src: 也能用文件名片段', byExactSrc.length > 0, `src:一丈红全图 命中 ${byExactSrc.length} 条`)
}
{
  const r = applyFilters(points, { ...EMPTY_FILTERS, q: 'cat:材料 region:清河' })
  check('字段前缀能一起用', r.length > 0, `${r.length} 条`)
  check('结果同时满足两个条件', r.every((p) => p.category === '材料' && p.region === '清河'))
}
{
  const p = parseQuery('龙骨 region:清河 flag:placeholder_name')
  eq('自由词数', p.tokens.length, 1)
  eq('region 条件数', p.fields.regions?.length, 1)
  eq('flag 条件数', p.fields.flags?.length, 1)
}
{
  // 回归：分享出去的链接里空格可能被编码成 + 或 %2B，两种都得能解析
  const a = parseQuery('龙骨+cat:材料')
  eq('加号当分隔符（自由词）', a.tokens.length, 1)
  eq('加号当分隔符（字段条件）', a.fields.categories?.[0], '材料')
  const r = applyFilters(points, { ...EMPTY_FILTERS, q: '龙骨+cat:材料' })
  check('加号分隔的查询能搜出结果', r.length > 0, `${r.length} 条`)
  check('结果同时满足两个条件', r.every((p) => p.category === '材料' && (p.name + p.material).includes('龙骨')))
  const r2 = applyFilters(points, { ...EMPTY_FILTERS, q: '龙骨 cat:材料' })
  eq('空格与加号结果一致', r.length, r2.length)
}
{
  const r = applyFilters(points, { ...EMPTY_FILTERS, q: '一个绝对不存在的关键词zzzz' })
  eq('搜不存在的词返回 0', r.length, 0)
}
{
  const r = applyFilters(points, { ...EMPTY_FILTERS, onlyRepeat: true })
  check('只看刷怪循环点', r.length > 0 && r.every((p) => p.repeat >= 2), `${r.length} 条`)
}
{
  const p = points.find((x) => x.category === '材料')
  check('按材料名能搜到', matchTokens(p, [p.material.toLowerCase()]), `material=${p.material}`)
  check('按区域名能搜到', matchTokens(p, [p.region.toLowerCase()]), `region=${p.region}`)
  check('按备注搜得到（多词 AND）', !matchTokens(p, ['一个不存在的词zzz', p.region.toLowerCase()]))
}

// ---------------------------------------------------------------------------
console.log('== 4. 排序 ==')
{
  const asc = sortPoints(points, 'repeat', 'asc')
  const desc = sortPoints(points, 'repeat', 'desc')
  check('repeat asc 单调', asc.every((p, i) => i === 0 || asc[i - 1].repeat <= p.repeat))
  check('repeat desc 单调', desc.every((p, i) => i === 0 || desc[i - 1].repeat >= p.repeat))
  check('desc 的第一个就是重复最多的', desc[0].repeat === Math.max(...points.map((p) => p.repeat)), `repeat=${desc[0].repeat} name=${desc[0].name}`)
  eq('排序不改变元素个数', asc.length, points.length)
}

// ---------------------------------------------------------------------------
console.log('== 5. 附近查询 ==')
{
  const p = points[100]
  const hits = nearby(points, [p.x, p.y, p.z], { top: 5 })
  eq('取最近的 5 个', hits.length, 5)
  check('最近的必然是它自己', hits[0].point.id === p.id && hits[0].dist === 0, `dist=${hits[0].dist}`)
  check('按距离递增', hits.every((h, i) => i === 0 || hits[i - 1].dist <= h.dist))
}
{
  const hits = nearby(points, [0, 0, 0], { top: 10, mode: 'flat' })
  check('水平模式也能用', hits.length > 0)
  check('水平模式按 flat 排序', hits.every((h, i) => i === 0 || hits[i - 1].flat <= h.flat))
}
{
  const hits = nearby(points, [0, 0, 0], { top: 10, radius: 300 })
  check('半径限制生效', hits.every((h) => h.dist <= 300), `${hits.length} 条 / 半径 300`)
}
check('坐标输入解析：逗号', JSON.stringify(parseCoordInput('-2400.5,700,-60')) === JSON.stringify([-2400.5, 700, -60]))
check('坐标输入解析：空格', JSON.stringify(parseCoordInput('-2400 700 -60')) === JSON.stringify([-2400, 700, -60]))
check(
  '坐标输入解析：整行带名称',
  JSON.stringify(parseCoordInput('-4033.78 ,-2494.08 ,-43.0471 ,附子')) === JSON.stringify([-4033.78, -2494.08, -43.0471])
)
check('坐标输入解析：垃圾输入返回 null', parseCoordInput('只有文字没有数字') === null)

// ---------------------------------------------------------------------------
console.log('== 6. 坐标格式化 ==')
eq('trimNum 去尾零', trimNum(3.1), '3.1')
eq('trimNum 保精度', trimNum(-58.9953), '-58.9953')
eq('trimNum 处理负零', trimNum(-0), '0')
eq('trimNum 整数', trimNum(-3822), '-3822')
{
  const p = { x: -3822.8, y: -695.13, z: -58.9953, name: '慈心山院传送点', note: '' }
  eq('csv 格式', formatCoord(p, 'csv'), '-3822.8,-695.13,-58.9953,慈心山院传送点')
  eq('spaced 格式', formatCoord(p, 'spaced'), '-3822.8 ,-695.13 ,-58.9953 ,慈心山院传送点')
  eq('xyz 格式', formatCoord(p, 'xyz'), '-3822.8,-695.13,-58.9953')
  check('json 格式可解析', (() => { try { JSON.parse(formatCoord(p, 'json')); return true } catch { return false } })())
}
check('quickCopy 与 csv 一致', quickCopy(points[0]) === formatCoord(points[0], 'csv'))

// ---------------------------------------------------------------------------
console.log('== 7. 导出 ==')
{
  const sample = points.slice(0, 50)
  const txt = buildExport(sample, { format: 'csv', repeatTimes: false, header: false })
  const lines = txt.trimEnd().split('\n')
  eq('行数 == 点位数', lines.length, 50)
  check('每行都是 x,y,z,名称', lines.every((l) => /^-?[\d.]+,-?[\d.]+,-?[\d.]+,.+$/.test(l)))
}
{
  // 唯一那条缺名称的点位（原始文件里只有 x,y,z），导出时要补成「未备注地点」
  const nameless = points.filter((p) => !p.name)
  check('存在缺名称的点位', nameless.length >= 1, `${nameless.length} 条`)
  const line = formatCoord(nameless[0], 'csv')
  check('缺名称导出时会补占位名', line.endsWith(',未备注地点'), line)
  check('补名后仍是合法的一行', /^-?[\d.]+,-?[\d.]+,-?[\d.]+,.+$/.test(line))
}
{
  const p = points.reduce((a, b) => (b.repeat > a.repeat ? b : a))
  const txt = buildExport([p], { format: 'csv', repeatTimes: true, header: false })
  const lines = txt.trimEnd().split('\n')
  eq(`repeat=${p.repeat} 的点重复输出`, lines.length, p.repeat)
  check('重复的每一行都一样', new Set(lines).size === 1)
  eq('估算行数一致', estimateExportLines([p], { format: 'csv', repeatTimes: true, header: false }), p.repeat)
}
{
  const txt = buildExport(points.slice(0, 3), { format: 'csv', repeatTimes: false, header: true })
  check('带头部注释', txt.startsWith('#'))
}
{
  const txt = buildExport(points.slice(0, 5), { format: 'json', repeatTimes: false, header: false })
  check('json 导出可解析且是数组', Array.isArray(JSON.parse(txt)) && JSON.parse(txt).length === 5)
}
{
  const txt = buildExport(points.slice(0, 20), { format: 'spaced', repeatTimes: false, header: false })
  check('spaced 导出与原始 24小时合版.ini 风格一致', /^-?[\d.]+ ,-?[\d.]+ ,-?[\d.]+ ,.+$/.test(txt.split('\n')[0]))
}

// ---------------------------------------------------------------------------
console.log('== 8. 进度统计 ==')
{
  const collected = new Set(points.slice(0, 100).map((p) => p.id))
  const buckets = progressBy(points, collected, 'category')
  const total = buckets.reduce((s, b) => s + b.total, 0)
  eq('分类进度总数 == 点位数', total, points.length)
  eq('已完成总数 == 收藏数', buckets.reduce((s, b) => s + b.done, 0), 100)
  check('百分比在 0~1 之间', buckets.every((b) => b.pct >= 0 && b.pct <= 1))
}

// ---------------------------------------------------------------------------
console.log('== 9. 地图包围盒 ==')
{
  const b = bounds(points)
  check('所有点都在包围盒内', points.every((p) => p.x >= b.minX && p.x <= b.maxX && p.y >= b.minY && p.y <= b.maxY))
  check('包围盒比数据本身大一点（留了边距）', b.minX < Math.min(...points.map((p) => p.x)))
}

// ---------------------------------------------------------------------------
console.log()
console.log(`结果：${pass} 项通过，${fail} 项失败`)
process.exit(fail ? 1 : 0)
