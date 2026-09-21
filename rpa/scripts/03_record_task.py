# -*- coding: utf-8 -*-
"""
03_record_task.py —— 按清单跑图时，逐条记录结果（方案 C 用）
=============================================================

用途：从 rpa/tasks/*.csv 里逐条取出坐标 → 让坐标软件传送过去 → 按 F 采集/确认 →
      把「这个点到底有没有、叫什么」记回 result.csv。跑完一遍，数据就被人工+实地校验过一轮。

影刀输入变量：
    {"out_file": ${输出文件}, "point_id": ${当前任务ID}, "coord": ${当前坐标},
     "name": ${当前名称}, "status": "ok", "note": ""}

    status 取值：
        ok       —— 到场确认，点还在
        gone     —— 到场发现没有（坐标失效 / 已被采过）
        moved    —— 有东西但位置偏了（把实际坐标填在 actual_coord）
        bad      —— 坐标本身有问题（穿模、落点在水里/墙里）
        skip     —— 本次跳过

影刀输出变量：
    ${结果}        —— 字典统计
    ${是否有效}    —— 布尔

单独跑：
    python 03_record_task.py --out raw/rpa/_selftest/result.csv --point-id p0001 \
        --coord "-2400,700,-60" --name "示例点" --status ok
"""

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

import csv
import json
import os
from datetime import datetime

HEADER = ["pointId", "status", "x", "y", "z", "name", "actualX", "actualY", "actualZ", "note", "recordedAt", "clipRaw"]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return ""


def run(out_file, point_id, coord, name="", status="ok", note="", actual_coord="", clip_raw=""):
    out_file = os.path.abspath(out_file)
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    new_file = not os.path.exists(out_file)

    parts = [p for p in str(coord or "").replace("，", ",").split(",") if p.strip()]
    x = _num(parts[0]) if len(parts) > 0 else ""
    y = _num(parts[1]) if len(parts) > 1 else ""
    z = _num(parts[2]) if len(parts) > 2 else ""

    aparts = [p for p in str(actual_coord or "").replace("，", ",").split(",") if p.strip()]
    ax = _num(aparts[0]) if len(aparts) > 0 else ""
    ay = _num(aparts[1]) if len(aparts) > 1 else ""
    az = _num(aparts[2]) if len(aparts) > 2 else ""

    row = {
        "pointId": point_id or "",
        "status": status or "ok",
        "x": x, "y": y, "z": z,
        "name": name or "",
        "actualX": ax, "actualY": ay, "actualZ": az,
        "note": note or "",
        "recordedAt": datetime.now().isoformat(timespec="seconds"),
        "clipRaw": (clip_raw or "")[:200],
    }

    with open(out_file, "a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        if new_file:
            w.writeheader()
        w.writerow(row)

    # 顺带做一次统计，影刀可以直接拿去做进度提示
    counts = {}
    total = 0
    try:
        with open(out_file, "r", encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                total += 1
                st = r.get("status", "")
                counts[st] = counts.get(st, 0) + 1
    except OSError:
        pass

    return {
        "recorded": True,
        "status": row["status"],
        "total": total,
        "counts": counts,
        "okRate": round(counts.get("ok", 0) / total, 3) if total else 0,
        "outFile": out_file,
    }


# ---- 影刀调用入口 ---------------------------------------------------------
_out = globals().get("out_file", None)
_pid = globals().get("point_id", None)
if _out and _pid is not None:
    结果 = run(
        _out,
        _pid,
        globals().get("coord", ""),
        globals().get("name", ""),
        globals().get("status", "ok"),
        globals().get("note", ""),
        globals().get("actual_coord", ""),
        globals().get("clip_raw", ""),
    )
    是否有效 = 结果.get("status") == "ok"


# ---- 命令行入口 -----------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import sys

    # 坑：坐标全都以 '-' 开头，argparse 会把 "--coord -2400,700,-60" 里的值
    # 当成另一个选项，直接报 "expected one argument"。这里把它改写成
    # --coord=-2400,700,-60 的形式，让两种写法都能用。
    _VALUE_OPTS = ("--coord", "--actual-coord", "--clip-raw")

    def _normalize(argv):
        out = []
        i = 0
        while i < len(argv):
            a = argv[i]
            if a in _VALUE_OPTS and i + 1 < len(argv) and argv[i + 1].startswith("-"):
                out.append(f"{a}={argv[i + 1]}")
                i += 2
                continue
            out.append(a)
            i += 1
        return out

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--point-id", default="")
    ap.add_argument("--coord", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--status", default="ok")
    ap.add_argument("--note", default="")
    ap.add_argument("--actual-coord", default="")
    ap.add_argument("--clip-raw", default="")
    a = ap.parse_args(_normalize(sys.argv[1:]))
    print(json.dumps(run(a.out, a.point_id, a.coord, a.name, a.status, a.note, a.actual_coord, a.clip_raw),
                     ensure_ascii=False, indent=2))
