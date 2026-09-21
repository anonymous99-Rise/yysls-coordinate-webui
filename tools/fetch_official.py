#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_official.py —— 拉取官方大地图的点位数据并缓存
=====================================================

数据源
------
燕云十六声官方大地图小程序（16.163.com/m/map）背后的公开接口：

    GET https://s3.game.163.com/ac59075b964b0715/maps/regions?mapId=<n>
    GET https://s3.game.163.com/ac59075b964b0715/category/points?mapId=<n>

`/category/points` 只要给 `mapId`，就会返回该地图下**全部分类与点位**：

    data.categories[] -> {name, childCategories[]}
      childCategories[] -> {id, name, pointsNum, pointList[]}
        pointList[] -> {id, categoryId, name, description, longitude, latitude}

坐标是官方自己的经纬度体系（数量级 ±700000），**不是**游戏内那个世界坐标，
所以要用它必须先解出两者的换算关系（见 tools/fit_official_transform.py）。

为什么要缓存而不是每次现拉
--------------------------
* 这是别人家的服务，没理由反复打。拉一次存下来，之后全部走本地缓存。
* 数据要能进 git 做版本对比 —— 官方更新了哪些点，diff 一眼可见。
* 离线也能跑 ETL 与测试。

用法
----
    python tools/fetch_official.py                # 拉全部 9 张图，写 data/official_points.json
    python tools/fetch_official.py --force        # 忽略缓存重新拉
    python tools/fetch_official.py --maps 0,1     # 只拉指定 mapId
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
OUT = PROJECT / "data" / "official_points.json"
REGIONS = PROJECT / "data" / "official_regions.json"

BASE = "https://s3.game.163.com/ac59075b964b0715"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Referer": "https://16.163.com/m/map/",
    "Accept": "application/json, text/plain, */*",
}


def api(path: str, params: dict | None = None, *, retries: int = 3) -> dict:
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                doc = json.loads(r.read().decode("utf-8"))
            if not doc.get("success"):
                raise RuntimeError(f"接口返回失败：code={doc.get('code')} msg={doc.get('msg')}")
            return doc
        except Exception as e:  # 网络抖动重试一次，别因为一次超时就整批失败
            last = e
            time.sleep(1.0 + i)
    raise RuntimeError(f"请求失败 {url}：{last}")


def fetch_map(map_id: int) -> dict:
    doc = api("/category/points", {"mapId": map_id})
    return doc.get("data") or {}


def flatten(map_id: int, map_name: str, data: dict) -> list[dict]:
    """把嵌套的 categories -> childCategories -> pointList 摊平成点位列表。"""
    out = []
    for cat in data.get("categories", []) or []:
        for sub in cat.get("childCategories", []) or []:
            for p in sub.get("pointList", []) or []:
                try:
                    lng = float(p["longitude"])
                    lat = float(p["latitude"])
                except (KeyError, TypeError, ValueError):
                    continue
                out.append({
                    "mapId": map_id,
                    "mapName": map_name,
                    "category": sub.get("name", ""),
                    "categoryGroup": cat.get("name", ""),
                    "categoryId": sub.get("id"),
                    "id": p.get("id"),
                    "name": (p.get("name") or "").strip(),
                    "description": (p.get("description") or "").strip(),
                    "lng": lng,
                    "lat": lat,
                })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="忽略已有缓存重新拉")
    ap.add_argument("--maps", default="", help="只拉这些 mapId，逗号分隔")
    args = ap.parse_args()

    if OUT.exists() and not args.force:
        doc = json.loads(OUT.read_text(encoding="utf-8"))
        print(f"已有缓存 {OUT}（{len(doc.get('points', []))} 个点，"
              f"抓取于 {doc.get('fetchedAt', '?')}）")
        print("  要重拉请加 --force")
        return 0

    # 地图清单从 regions 缓存里拿；没有就现拉一次
    if REGIONS.exists():
        maps = [{"id": m["id"], "name": m["name"]}
                for m in json.loads(REGIONS.read_text(encoding="utf-8"))["maps"]]
    else:
        maps = [{"id": m["id"], "name": m["name"]}
                for m in api("/maps/regions").get("data", {}).get("maps", [])]

    if args.maps:
        want = {int(x) for x in args.maps.split(",") if x.strip()}
        maps = [m for m in maps if m["id"] in want]

    all_points: list[dict] = []
    per_map = []
    for m in maps:
        try:
            data = fetch_map(m["id"])
        except Exception as e:
            print(f"  [!] mapId={m['id']} {m['name']}: {e}")
            continue
        pts = flatten(m["id"], m["name"], data)
        all_points.extend(pts)
        cats = Counter(p["category"] for p in pts)
        per_map.append({"mapId": m["id"], "name": m["name"], "points": len(pts),
                        "categories": len(cats)})
        print(f"  mapId={m['id']:<2} {m['name']:<8} {len(pts):>5} 个点 / {len(cats)} 个分类")
        time.sleep(0.5)   # 对人家的服务客气一点

    doc = {
        "source": "燕云十六声官方大地图（16.163.com/m/map）",
        "api": f"{BASE}/category/points?mapId=<n>",
        "fetchedAt": datetime.now().isoformat(timespec="seconds"),
        "note": "坐标是官方经纬度体系，不是游戏内世界坐标；换算见 tools/fit_official_transform.py",
        "mapSummary": per_map,
        "points": all_points,
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n  合计 {len(all_points)} 个点 -> {OUT}")
    print(f"  文件大小 {OUT.stat().st_size / 1024:.0f} KB")
    print("\n  分类分布（Top 20）：")
    for k, n in Counter(p["category"] for p in all_points).most_common(20):
        print(f"    {k:<12} {n:>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
