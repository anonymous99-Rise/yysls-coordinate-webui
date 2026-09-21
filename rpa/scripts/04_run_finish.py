# -*- coding: utf-8 -*-
"""
04_run_finish.py —— 收尾：汇总本次采集，并把它登记进管线
=========================================================

影刀输入变量：
    {"run_dir": ${会话目录}, "out_file": ${输出文件}}

影刀输出变量：
    ${汇总}      —— 字典（见下）
    ${汇总文本}  —— 一行给影刀弹窗/日志用的中文摘要
    ${是否可入库}—— 布尔。True 说明采到了东西，可以去跑 etl.py

它做三件事：
    1. 数一遍 collected.ini / result.csv 里到底有多少条
    2. 把计数合并进 session.json（一次采集 = 一个目录 = 一份完整元信息）
    3. 校验采集文件是否满足 etl.py 的入库要求（必须每行都是 x,y,z,名称）
       —— 不满足就在 reportedProblems 里点名，避免脏数据悄悄混进主数据

单独跑：
    python 04_run_finish.py --run-dir raw/rpa/_selftest --out-file raw/rpa/_selftest/collected.ini
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

import json
import os
import re
from datetime import datetime

# etl.py 能吃的行格式：x,y,z,名称
_ROW = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*,\s*.+$")


def _count_lines(path):
    if not os.path.exists(path):
        return 0, []
    total = 0
    bad = []
    with open(path, "r", encoding="utf-8") as f:
        for i, ln in enumerate(f, start=1):
            ln = ln.rstrip("\n")
            if not ln.strip() or ln.lstrip().startswith("#"):
                continue
            total += 1
            if not _ROW.match(ln):
                bad.append({"line": i, "raw": ln[:160]})
    return total, bad


def finish(run_dir, out_file):
    run_dir = os.path.abspath(run_dir)
    out_file = os.path.abspath(out_file)
    meta_path = os.path.join(run_dir, "session.json")

    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            meta = {}

    collected, bad_rows = _count_lines(out_file)
    result_csv = os.path.join(run_dir, "result.csv")
    verified = 0
    if os.path.exists(result_csv):
        with open(result_csv, "r", encoding="utf-8-sig") as f:
            verified = max(0, sum(1 for _ in f) - 1)  # 减掉表头

    meta.update({
        "finishedAt": datetime.now().isoformat(timespec="seconds"),
        "collectedRows": collected,
        "verifiedTasks": verified,
        "reportedProblems": bad_rows[:20],
        "readyForEtl": collected > 0 and not bad_rows,
    })
    os.makedirs(run_dir, exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    summary = {
        "runDir": run_dir,
        "collectedRows": collected,
        "verifiedTasks": verified,
        "problems": len(bad_rows),
        "readyForEtl": meta["readyForEtl"],
    }
    text = f"本次采集 {collected} 条坐标，逐点校验 {verified} 条"
    if bad_rows:
        text += f"；有 {len(bad_rows)} 行格式不对，已记进 session.json 的 reportedProblems"
    else:
        text += "；格式检查通过，可以直接跑 python tools/etl.py 并入主数据"

    return summary, text


# ---- 影刀调用入口 ---------------------------------------------------------
_run_dir = globals().get("run_dir", None)
_out_file = globals().get("out_file", None)
if _run_dir and _out_file:
    汇总, 汇总文本 = finish(_run_dir, _out_file)
    是否可入库 = 汇总["readyForEtl"]


# ---- 命令行入口 -----------------------------------------------------------
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--out-file", required=True)
    a = ap.parse_args()
    s, t = finish(a.run_dir, a.out_file)
    print(json.dumps(s, ensure_ascii=False, indent=2))
    print(t)
