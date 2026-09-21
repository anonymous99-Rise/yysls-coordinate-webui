#!/usr/bin/env node
/**
 * sync_web_data.mjs —— 把 data/ 的产物同步到 web/public/data/
 *
 * WebUI 只吃三份文件：
 *   points.min.json  6071 个点位的紧凑版（约 550 KB）
 *   facets.json      分类/区域/材料/来源 计数 + 分类配色元信息
 *   notes.json       注意事项与作者署名
 *
 * 刻意不把 6 MB 的 points.json 塞进前端。
 *
 * 用法：node tools/sync_web_data.mjs
 */

import { mkdir, copyFile, stat, readFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const project = path.resolve(here, '..')
const src = path.join(project, 'data')
const dst = path.join(project, 'web', 'public', 'data')

const FILES = ['points.min.json', 'facets.json', 'notes.json', 'issues.json']

const kb = (n) => `${(n / 1024).toFixed(1)} KB`

async function main() {
  const missing = FILES.filter((f) => !existsSync(path.join(src, f)))
  if (missing.length) {
    console.error(`[x] data/ 里缺少产物：${missing.join(', ')}`)
    console.error('    先跑：python tools/etl.py')
    process.exit(1)
  }

  await mkdir(dst, { recursive: true })

  let total = 0
  for (const f of FILES) {
    await copyFile(path.join(src, f), path.join(dst, f))
    const { size } = await stat(path.join(dst, f))
    total += size
    console.log(`  ${f.padEnd(18)} ${kb(size)}`)
  }

  // 反查一遍产物版本，防止同步了过期的数据
  const facets = JSON.parse(await readFile(path.join(dst, 'facets.json'), 'utf8'))
  const s = facets.stats ?? {}
  console.log(
    `  合计 ${kb(total)}  |  ${s.points ?? '?'} 个点位  |  ${s.rawRows ?? '?'} 行原始坐标  |  ${s.issues ?? '?'} 条解析问题`
  )
  console.log(`  -> ${path.relative(process.cwd(), dst)}`)
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
