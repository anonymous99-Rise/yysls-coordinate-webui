/**
 * store.ts —— 本地状态：主题、已收集进度、URL 深链接
 *
 * 全部只用 localStorage / location.hash，不需要后端。
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import type { Filters } from './core'
import { EMPTY_FILTERS } from './core'

// --------------------------------------------------------------------------
// localStorage 状态
// --------------------------------------------------------------------------
export function useLocalStorage<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key)
      if (raw == null) return initial
      return JSON.parse(raw) as T
    } catch {
      return initial
    }
  })

  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value))
    } catch {
      /* 隐私模式下写不进去，忽略 */
    }
  }, [key, value])

  return [value, setValue] as const
}

// --------------------------------------------------------------------------
// 主题
// --------------------------------------------------------------------------
export type Theme = 'dark' | 'light'

export function useTheme() {
  const [theme, setTheme] = useLocalStorage<Theme>('yysls:theme', 'dark')
  useEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])
  const toggle = useCallback(() => setTheme((t) => (t === 'dark' ? 'light' : 'dark')), [setTheme])
  return { theme, setTheme, toggle }
}

// --------------------------------------------------------------------------
// 已收集进度
// --------------------------------------------------------------------------
export function useCollected() {
  const [ids, setIds] = useLocalStorage<string[]>('yysls:collected', [])
  const collected = useMemo(() => new Set(ids), [ids])

  const toggle = useCallback(
    (id: string) => {
      setIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
    },
    [setIds]
  )

  const clear = useCallback(() => setIds([]), [setIds])

  const markMany = useCallback(
    (list: string[], done: boolean) => {
      setIds((prev) => {
        const s = new Set(prev)
        for (const id of list) (done ? s.add(id) : s.delete(id))
        return [...s]
      })
    },
    [setIds]
  )

  return { collected, count: ids.length, toggle, clear, markMany }
}

// --------------------------------------------------------------------------
// URL 深链接
// --------------------------------------------------------------------------
const URL_KEYS = {
  q: 'q',
  region: 'reg',
  category: 'cat',
  material: 'mat',
  place: 'place',
  layer: 'layer',
  flag: 'flag',
  source: 'src',
  view: 'view',
  sort: 'sort',
  dir: 'dir',
  onlyRepeat: 'rep',
  progress: 'pg',
  selected: 'sel',
} as const

export interface UrlState {
  filters: Filters
  view: ViewMode
  sort: SortState
  selected: string | null
}

export type ViewMode = 'table' | 'map'
export interface SortState {
  key: import('./core').SortKey
  dir: 'asc' | 'desc'
}

/** 把状态写进 location.hash —— 方便把一份筛选结果直接发给朋友 */
export function useUrlState(initial: UrlState) {
  const [state, setState] = useState<UrlState>(() => {
    const p = new URLSearchParams(location.hash.replace(/^#/, ''))
    const list = (k: string) => {
      const v = p.get(k)
      return v ? v.split(',').filter(Boolean) : []
    }
    const sortKey = (p.get(URL_KEYS.sort) || initial.sort.key) as SortState['key']
    return {
      filters: {
        q: p.get(URL_KEYS.q) ?? initial.filters.q,
        regions: list(URL_KEYS.region),
        categories: list(URL_KEYS.category),
        materials: list(URL_KEYS.material),
        places: list(URL_KEYS.place),
        layers: list(URL_KEYS.layer),
        flags: list(URL_KEYS.flag),
        sources: list(URL_KEYS.source),
        progress: ((p.get(URL_KEYS.progress) as Filters['progress']) || initial.filters.progress),
        onlyRepeat: p.get(URL_KEYS.onlyRepeat) === '1',
      },
      view: ((p.get(URL_KEYS.view) as ViewMode) || initial.view),
      sort: { key: sortKey, dir: ((p.get(URL_KEYS.dir) as 'asc' | 'desc') || initial.sort.dir) },
      selected: p.get(URL_KEYS.selected) || initial.selected,
    }
  })

  useEffect(() => {
    const p = new URLSearchParams()
    const f = state.filters
    const put = (k: string, v: string) => v && p.set(k, v)
    put(URL_KEYS.q, f.q)
    put(URL_KEYS.region, f.regions.join(','))
    put(URL_KEYS.category, f.categories.join(','))
    put(URL_KEYS.material, f.materials.join(','))
    put(URL_KEYS.place, f.places.join(','))
    put(URL_KEYS.layer, f.layers.join(','))
    put(URL_KEYS.flag, f.flags.join(','))
    put(URL_KEYS.source, f.sources.join(','))
    put(URL_KEYS.progress, f.progress === 'all' ? '' : f.progress)
    put(URL_KEYS.onlyRepeat, f.onlyRepeat ? '1' : '')
    put(URL_KEYS.view, state.view === 'table' ? '' : state.view)
    put(URL_KEYS.sort, state.sort.key === 'name' ? '' : state.sort.key)
    put(URL_KEYS.dir, state.sort.dir === 'asc' ? '' : state.sort.dir)
    put(URL_KEYS.selected, state.selected ?? '')

    const hash = p.toString()
    const next = hash ? `#${hash}` : location.pathname
    history.replaceState(null, '', next)
  }, [state])

  return [state, setState] as const
}

export const DEFAULT_FILTERS = EMPTY_FILTERS
