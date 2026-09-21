#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
query.py —— 查数据的小工具（复核 / 排查用）
==========================================

用法：
    python tools/query.py 开封              # 名称含「开封」的点
    python tools/query.py --cat 其他        # 按分类查
    python tools/query.py --flag missing_name
    python tools/query.py --src 合集        # 按来源文件查
    python tools/query.py --region 清河 --limit 20
    python tools/query.py --near -2400,-700,0 --top 10   # 找最近的点
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", default="", help="名称子串")
    ap.add_argument("--cat", default="")
    ap.add_argument("--region", default="")
    ap.add_argument("--flag", default="")
    ap.add_argument("--src", default="")
    ap.add_argument("--near", default="")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    doc = json.loads((DATA / "points.json").read_text(encoding="utf-8"))
    pts = doc["points"]

    if args.near:
        x, y, z = (float(v) for v in args.near.split(","))
        for p in pts:
            p["_d"] = math.dist((x, y, z), (p["x"], p["y"], p["z"]))
        pts = sorted(pts, key=lambda p: p["_d"])[: args.top]
        for p in pts:
            print(f"  {p['_d']:9.1f}m  {p['x']:>10.2f},{p['y']:>10.2f},{p['z']:>9.2f}  "
                  f"{p['category']:<8} {p['name']}  [{p['region'] or '?'}]")
        return 0

    sel = pts
    if args.name:
        sel = [p for p in sel if args.name in p["name"]]
    if args.cat:
        sel = [p for p in sel if p["category"] == args.cat]
    if args.region:
        sel = [p for p in sel if p["region"] == args.region]
    if args.flag:
        sel = [p for p in sel if args.flag in p["flags"]]
    if args.src:
        sel = [p for p in sel if any(args.src in s["file"] for s in p["sources"])]

    print(f"命中 {len(sel)} / {len(pts)} 点位")
    for p in sel[: args.limit]:
        srcs = " | ".join(f"{s['file']}:{s['lines'][:3]}x{s['count']}" for s in p["sources"][:3])
        print(f"  {p['id']}  {p['x']:>10.2f},{p['y']:>10.2f},{p['z']:>9.2f}  "
              f"[{p['category']}] mat={p['material'] or '-'} place={p['place'] or '-'} "
              f"region={p['region'] or '-'}({p['regionSource'] or '-'}) repeat={p['repeat']}")
        print(f"        name={p['name']!r} note={p['note']!r} flags={p['flags']}")
        print(f"        <- {srcs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
