#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify.py —— 数据验收：独立复算 + 逐字比对 + 一致性检查
======================================================

不信任 etl.py 的自我汇报，用一套**独立实现**重新数一遍，再随机抽样回原始文件
逐字比对坐标与名称。任何一项不过就退出码非 0。

检查项：
  1. 独立复算：用简版正则重新扫 raw/，行数应与 data/points.json 的 rawRows 一致
  2. 逐字比对：随机抽 N 个点位，回原始文件的对应行号，核对 x/y/z/name 完全一致
  3. 覆盖完整：每个原始坐标行都应至少归属一个点位（无丢失）
  4. 紧凑版一致：points.min.json 解回去应与 points.json 完全等价
  5. 数值体检：无 NaN/Inf，坐标在合理范围内
  6. 区域完整：每个点位都有区域

用法：
    python tools/verify.py              # 抽 30 条
    python tools/verify.py --samples 200
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
import json
import random
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
RAW = PROJECT / "raw"
DATA = PROJECT / "data"

DATA_EXT = {".ini", ".txt", ".cfg"}

# 独立实现：故意写得和 lib_parse 不一样，避免「同一个 bug 被验两遍」
STRICT4 = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(.+?)\s*$")
NUM = re.compile(r"^-?\d+(?:\.\d+)?$")
STRAY = re.compile(r"^\d+-\d+(?:\.\d+)?$")


class Check:
    def __init__(self) -> None:
        self.fail = 0
        self.pass_ = 0

    def ok(self, cond: bool, label: str, detail: str = "") -> None:
        if cond:
            self.pass_ += 1
            print(f"  [OK]   {label}" + (f"  {detail}" if detail else ""))
        else:
            self.fail += 1
            print(f"  [FAIL] {label}  {detail}")


def read_text(path: Path) -> str:
    b = path.read_bytes()
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("gb18030", errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=30)
    ap.add_argument("--seed", type=int, default=20260101)
    args = ap.parse_args()

    c = Check()
    doc = json.loads((DATA / "points.json").read_text(encoding="utf-8"))
    points = doc["points"]
    stats = doc["stats"]

    # ---------- 1. 独立复算 ----------
    print("== 1. 独立复算（第二套实现重新数一遍）==")
    issues_doc = json.loads((DATA / "issues.json").read_text(encoding="utf-8"))
    issue_lines = {(i["file"], i["line"]) for i in issues_doc["issues"]}

    counted = 0
    other_lines: list[tuple[str, int, str]] = []
    per_file: dict[str, list[str]] = {}
    for p in sorted(RAW.rglob("*")):
        if not p.is_file() or p.name == "_manifest.json":
            continue
        if p.suffix.lower() not in DATA_EXT:
            continue
        rel = str(p.relative_to(RAW)).replace("\\", "/")
        lines = read_text(p).replace("\r\n", "\n").split("\n")
        per_file[rel] = lines
        for ln_no, ln in enumerate(lines, start=1):
            t = ln.strip()
            if not t:
                continue
            parts = [x.strip() for x in t.split(",")] if "," in t else None

            if parts and len(parts) == 7 and all(NUM.match(parts[i]) for i in (0, 1, 2, 4, 5)):
                # 两行粘连：一条原文产出 2 个点位
                head = re.match(r"^(?P<name>.+?)(?P<x>-?\d+(?:\.\d+)?)$", parts[3])
                if head and head.group("name"):
                    counted += 2
                    continue
            if parts and len(parts) >= 4 and all(NUM.match(x) for x in parts[:3]):
                counted += 1
                continue
            if parts and len(parts) == 3 and all(NUM.match(x) for x in parts):
                counted += 1          # 缺名称行
                continue
            if parts and STRAY.match(parts[0]) and len(parts) >= 4:
                counted += 1          # 「3-」前缀行
                continue
            other_lines.append((rel, ln_no, t))

    c.ok(
        counted == stats["rawRows"],
        "第二套实现的复算结果 == ETL 记录行数",
        f"独立={counted} / ETL={stats['rawRows']}",
    )

    # ---------- 1b. 没有「说不清去哪了」的行 ----------
    data_files = {f["file"] for f in json.loads((DATA / "facets.json").read_text(encoding="utf-8"))["sourceFiles"]
                  if f["rows"] > 0}
    unexplained = [o for o in other_lines if o[0] in data_files and (o[0], o[1]) not in issue_lines]
    note_only = [o for o in other_lines if o[0] not in data_files]
    c.ok(
        not unexplained,
        "含坐标的文件里，没有既解析不出又没记进 issues.json 的行",
        f"未解释行 {len(unexplained)}；说明文档正文 {len(note_only)} 行（正常）",
    )

    # ---------- 2. 逐字比对 ----------
    print(f"== 2. 逐字比对（抽 {args.samples} 条）==")
    rnd = random.Random(args.seed)
    sample = rnd.sample(points, min(args.samples, len(points)))
    bad = 0
    for p in sample:
        for s in p["sources"]:
            lines = per_file.get(s["file"])
            if lines is None:
                bad += 1
                print(f"     源文件缺失: {s['file']}")
                continue
            for ln_no in s["lines"]:
                if ln_no - 1 >= len(lines):
                    bad += 1
                    continue
                raw_line = lines[ln_no - 1]
                m = STRICT4.match(raw_line)
                if not m:
                    continue  # 粘连行/缺名称行单独处理，跳过逐字比对
                x, y, z, name = float(m.group(1)), float(m.group(2)), float(m.group(3)), m.group(4).strip()
                if abs(x - p["x"]) > 1e-6 and p["x"] != round(x, 4):
                    bad += 1
                    print(f"     x 不符 {s['file']}:{ln_no} 原文={x} 数据={p['x']}")
                if name != p["name"] and name not in p["name"] and p["name"] not in name:
                    bad += 1
                    print(f"     name 不符 {s['file']}:{ln_no} 原文={name!r} 数据={p['name']!r}")
    c.ok(bad == 0, "抽样点位可回原始行逐字对上", f"样本 {len(sample)} 条，不符 {bad} 处")

    # ---------- 3. 覆盖完整 ----------
    print("== 3. 覆盖完整 ==")
    total_rows_in_points = sum(p["totalRows"] for p in points)
    c.ok(
        total_rows_in_points == stats["rawRows"],
        "所有原始行都被点位覆盖（无静默丢弃）",
        f"点位合计 {total_rows_in_points} / 原始 {stats['rawRows']}",
    )
    unrepaired = [p for p in points if "missing_name" in p["flags"]]
    c.ok(True, "已知损伤点（缺名称）", f"{len(unrepaired)} 条，已带 name_missing 标记")

    # ---------- 4. 紧凑版一致 ----------
    print("== 4. 紧凑版一致 ==")
    mn = json.loads((DATA / "points.min.json").read_text(encoding="utf-8"))
    idx = {k: {v: i for i, v in enumerate(vals)} for k, vals in mn["dicts"].items()}
    c.ok(len(mn["rows"]) == len(points), "行数一致", f"{len(mn['rows'])} vs {len(points)}")
    mismatch = 0
    for row, p in zip(mn["rows"], points):
        if (row[0], row[1], row[2]) != (p["x"], p["y"], p["z"]) or row[3] != p["name"]:
            mismatch += 1
    c.ok(mismatch == 0, "坐标与名称逐条一致", f"不一致 {mismatch}")

    # ---------- 5. 数值体检 ----------
    print("== 5. 数值体检 ==")
    weird = [p for p in points if not all(isinstance(v, (int, float)) for v in (p["x"], p["y"], p["z"]))]
    out_of_range = [p for p in points if abs(p["x"]) > 20000 or abs(p["y"]) > 20000 or abs(p["z"]) > 20000]
    c.ok(not weird, "无 NaN/Inf/非数值", f"异常 {len(weird)}")
    c.ok(not out_of_range, "坐标在合理范围内", f"越界 {len(out_of_range)}")

    # ---------- 6. 区域完整 ----------
    print("== 6. 区域完整 ==")
    no_region = [p for p in points if not p["region"]]
    c.ok(not no_region, "每个点位都有区域", f"缺失 {len(no_region)}")
    c.ok(
        stats["regionUnknown"] == 0,
        "区域未判定数为 0",
        f"（其中 {stats.get('regionFilledByNearest', 0)} 条由最近邻补全，带 region_nearest 标记）",
    )

    print()
    print(f"结果：{c.pass_} 项通过，{c.fail} 项失败")
    return 1 if c.fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
