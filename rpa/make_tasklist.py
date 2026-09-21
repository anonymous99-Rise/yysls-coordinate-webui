#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_tasklist.py —— 从规范化数据生成「待办清单」，喂给影刀 RPA 去跑
==================================================================

采完的坐标躺在 data/ 里是静态的；要让 RPA 有活干，得先知道「差什么」。
这个脚本把三类缺口变成 RPA 可直接读的 CSV（RPA 读表格比读 JSON 省事）：

    1. needs-name      原始数据没给名字的点（占位名「示例」「未备注地点」+ 唯一那条缺名称的）
                       → 实地走过去看一眼，就知道那是什么
    2. verify-dup      同一坐标挂着多个名字的点（693 条）
                       → 原始作者自己说过「坐标软件没刷新会导出一堆一样的坐标」，
                         哪些是真重复、哪些是同一个点上的多个东西，只能实地确认
    3. farm-<材料>     按材料分片的挂机采集路线（可选）
                       → 直接喂给坐标软件批量传送

输出：rpa/tasks/<清单名>.csv，列固定为
    pointId,x,y,z,name,category,region,place,note,repeat,flags
影刀那边用「读取表格」指令读它，逐行取坐标即可。

用法：
    python rpa/make_tasklist.py                    # 三类清单都生成
    python rpa/make_tasklist.py --only needs-name
    python rpa/make_tasklist.py --farm 龙骨 --farm 佛泪参
    python rpa/make_tasklist.py --limit 200        # 每类最多取 200 条（先试跑用）
"""

from __future__ import annotations

# >>> utf8-guard >>>
# Windows 上往管道/重定向的 stdout 打中文会 UnicodeEncodeError 崩掉
# （Python 默认用系统 ANSI 代码页而不是 UTF-8）。不指望调用方设
# PYTHONIOENCODING —— 脚本自己保证输出编码。
# 自带 import 是刻意的：位置无关，也不依赖文件里其他 import 的先后。
import sys as _sys

for _stream in (_sys.stdout, _sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
# <<< utf8-guard <<<

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
DATA = PROJECT / "data"
TASKS = HERE / "tasks"

COLUMNS = [
    "pointId", "x", "y", "z", "name", "category",
    "region", "place", "note", "repeat", "flags",
]


def load_points() -> tuple[list[dict], dict]:
    src = DATA / "points.json"
    if not src.exists():
        raise SystemExit(f"[x] 找不到 {src}；先跑 python tools/etl.py")
    doc = json.loads(src.read_text(encoding="utf-8"))
    return doc["points"], doc["stats"]


def row_of(p: dict) -> dict:
    return {
        "pointId": p["id"],
        "x": p["x"], "y": p["y"], "z": p["z"],
        "name": p["name"] or "",
        "category": p["category"],
        "region": p["region"], "place": p["place"],
        "note": p["note"], "repeat": p["repeat"],
        "flags": "|".join(p["flags"]),
    }


def write_task(name: str, rows: list[dict]) -> Path:
    TASKS.mkdir(parents=True, exist_ok=True)
    path = TASKS / f"{name}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", choices=["", "needs-name", "verify-dup", "farm"])
    ap.add_argument("--farm", action="append", default=[], help="按材料名生成挂机路线（可多次）")
    ap.add_argument("--limit", type=int, default=0, help="每类最多取多少条（0=不限）")
    args = ap.parse_args()

    points, stats = load_points()
    made: list[tuple[str, int, Path]] = []

    def cap(rows: list[dict]) -> list[dict]:
        return rows[: args.limit] if args.limit else rows

    if args.only in ("", "needs-name"):
        # 占位名 + 缺名称：这两类必须人工看一眼才知道是什么
        rows = [p for p in points if "placeholder_name" in p["flags"] or "name_missing" in p["flags"]]
        rows.sort(key=lambda p: (p["region"], p["x"], p["y"]))
        made.append(("needs-name", len(rows), write_task("needs-name", cap([row_of(p) for p in rows]))))

    if args.only in ("", "verify-dup"):
        rows = [p for p in points if "co_located_with_other" in p["flags"]]
        rows.sort(key=lambda p: (p["x"], p["y"], p["z"]))
        made.append(("verify-dup", len(rows), write_task("verify-dup", cap([row_of(p) for p in rows]))))

    if args.farm:
        by_material: dict[str, list[dict]] = {}
        for p in points:
            if p["material"]:
                by_material.setdefault(p["material"], []).append(p)
        for m in args.farm:
            rows = by_material.get(m)
            if not rows:
                print(f"  [!] 没有材料叫「{m}」的点位，跳过")
                continue
            rows = sorted(rows, key=lambda p: (p["region"], p["x"], p["y"]))
            name = f"farm-{m}"
            made.append((name, len(rows), write_task(name, cap([row_of(p) for p in rows]))))

    print("== 生成的任务清单 ==")
    for name, total, path in made:
        print(f"  {name:16} {total:>6} 条  ->  {path.relative_to(PROJECT)}")

    idx = {
        "generatedAt": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "sourceStats": stats,
        "lists": [{"name": n, "rows": t, "file": str(p.relative_to(PROJECT)).replace('\\', '/')} for n, t, p in made],
    }
    (TASKS / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  索引 -> {(TASKS / 'index.json').relative_to(PROJECT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
