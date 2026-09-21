#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_rpa.py —— RPA 采集链路的自动测试（不需要影刀、不需要游戏）
==============================================================

测三件事：
  1. rpa/collector.py 的解析器与 tools/lib_parse.py **对同一批样本给出一致结果**
     （两处正则是有意重复的：rpa 目录要能单独分发出去，不能 import 项目代码。
       既然重复了，就得有测试盯着它们别跑偏。）
  2. 影刀片段（rpa/scripts/*.py）能当命令行程序独立跑通，并且输出符合 etl.py 的入库要求
  3. 采集产物能被 etl.py 真正吃进去（端到端）

用法：
    python tools/test_rpa.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import shutil
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
RPA = PROJECT / "rpa"
SCRIPTS = RPA / "scripts"
SANDBOX = PROJECT / "raw" / "rpa" / "_selftest"

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(RPA))

import collector  # noqa: E402  (rpa/collector.py)
from lib_parse import parse_line  # noqa: E402  (tools/lib_parse.py)

passed = 0
failed = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [OK]   {label}" + (f"  {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  [FAIL] {label}  {detail}")


def run_script(name: str, args: list[str]) -> tuple[int, str]:
    p = subprocess.run(
        [sys.executable, str(SCRIPTS / name), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(PROJECT),
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# ---------------------------------------------------------------------------
print("== 1. collector 解析器 与 lib_parse 结果一致 ==")
# 两套解析器的分工不同，这是**有意**的差异，测试要盯住的是「别无意中跑偏」：
#   * tools/lib_parse.py  —— 解析原始坐标库，格式是死的（x,y,z,名称），不认识就报 issue
#   * rpa/collector.py    —— 解析剪贴板/OCR 文本，来源不可控，所以更宽容
#     额外支持：空格分隔、括号三元组「(x, y, z)」
# 下面先比公共子集，再单独验 collector 多出来的那部分宽容度。
SAMPLES = [
    "-2400.5,700,-60,我家门口",
    "-3822.8 ,-695.13 ,-58.9953 ,慈心山院传送点",
    "1,2,3,正数坐标",
    "-1.5,2.5,-3.5,负数小数",
    "0,0,0,原点",
    "403.588, -109.02, -3.6795,  一丈红",
    "-2703.57 ,-961.173 ,22",
    "-2400.5 700 -60 空格分隔",
    "这不是坐标",
    "",
    "# 注释行",
]
mismatch = []
for s in SAMPLES:
    a = collector.parse_coord_line(s)
    rows, _ = parse_line(s, file="t", line_no=1)
    b = (rows[0].x, rows[0].y, rows[0].z, rows[0].name) if rows else None
    if (a is None) != (b is None):
        mismatch.append((s, a, b))
        continue
    if a is not None and b is not None:
        if abs(a[0] - b[0]) > 1e-9 or abs(a[1] - b[1]) > 1e-9 or abs(a[2] - b[2]) > 1e-9:
            mismatch.append((s, a, b))
check("两套解析器对共同样本判定一致", not mismatch, f"{len(SAMPLES)} 个样本，不一致 {len(mismatch)}")
for m in mismatch[:5]:
    print(f"        不一致: {m[0]!r} -> collector={m[1]} lib_parse={m[2]}")

# collector 独有的宽容度（有意为之，不是 bug）
paren = "(-183.437, 986.765, -27.3766)"
check("collector 认识括号三元组（OCR 常见写法）", collector.parse_coord_line(paren) is not None)
check("lib_parse 刻意不认括号形式（原始库里不存在这种写法）", not parse_line(paren, file="t", line_no=1)[0])

# lib_parse 独有的修复（同样是有意为之）：原始文件里有一行手滑多打了「3-」前缀
stray = "3-3862.65 ,-689.09 ,-50.71 ,佛泪参慈心山院1"
_srows, _ = parse_line(stray, file="t", line_no=1)
check("lib_parse 会剥掉「3-」这种多余前缀", bool(_srows) and abs(_srows[0].x + 3862.65) < 1e-6,
      f"x={_srows[0].x if _srows else None}")
check("collector 不做这个修复（剪贴板里不会出现这种手滑）", collector.parse_coord_line(stray) is None)

check("UTF-8 名称解析正确", collector.parse_coord_line("-3822.8,-695.13,-58.9953,慈心山院传送点")[3] == "慈心山院传送点")
check("缺名称行被识别为 3 元组", collector.parse_coord_line("-2703.57,-961.173,22")[3] == "")
check("注释行被忽略", collector.parse_coord_line("# whatever") is None)
check("空行被忽略", collector.parse_coord_line("") is None)


# ---------------------------------------------------------------------------
print("== 2. 影刀片段能脱离影刀独立运行 ==")
if SANDBOX.exists():
    shutil.rmtree(SANDBOX)

rc, out = run_script("01_run_init.py", ["--base-dir", "raw/rpa/_selftest", "--tag", "自动测试"])
check("01_run_init 退出码 0", rc == 0, out.strip()[:120])
init = json.loads(out)
out_file = Path(init["outFile"])
check("01 生成了会话目录", out_file.parent.is_dir(), str(out_file.parent))
check("01 写了 session.json", (out_file.parent / "session.json").exists())

rc, out = run_script("02_parse_clipboard.py", [
    "--text", "-2400.5,700,-60,测试点A\n-1 2 3 空格点\n不是坐标\n-2400.5,700,-60,测试点A",
    "--out", str(out_file),
])
check("02_parse_clipboard 退出码 0", rc == 0, out.strip()[:160])
r = json.loads(out)
check("02 解析出 3 条坐标", r["rows"] == 3, f"rows={r['rows']}")
check("02 写入 2 条（去重 1 条）", r["added"] == 2 and r["duplicate"] == 1,
      f"added={r['added']} duplicate={r['duplicate']}")
lines = [ln for ln in out_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
check("02 落盘行数正确", len(lines) == 2, str(lines))
check("02 落盘格式是 x,y,z,名称", all(len(ln.split(",")) == 4 for ln in lines))

# 再写一次同样的内容，应该全部判重
rc, out = run_script("02_parse_clipboard.py", ["--text", lines[0], "--out", str(out_file)])
check("02 重复内容不重复写入", json.loads(out)["added"] == 0)

result_csv = out_file.parent / "result.csv"
rc, out = run_script("03_record_task.py", [
    "--out", str(result_csv), "--point-id", "p00001",
    "--coord", "-2400.5,700,-60", "--name", "测试点A", "--status", "ok",
])
check("03_record_task 退出码 0（负坐标参数不炸）", rc == 0, out.strip()[:160])
r3 = json.loads(out)
check("03 记录了 1 条", r3["total"] == 1 and r3["status"] == "ok", str(r3))

rc, out = run_script("03_record_task.py", [
    "--out", str(result_csv), "--point-id", "p00002",
    "--coord", "-2500,710,-61", "--status", "gone",
])
check("03 第二条记录成功", rc == 0 and json.loads(out)["total"] == 2)
check("03 统计区分了 ok / gone",
      json.loads(out)["counts"].get("ok") == 1 and json.loads(out)["counts"].get("gone") == 1,
      str(json.loads(out)["counts"]))

rc, out = run_script("04_run_finish.py", ["--run-dir", str(out_file.parent), "--out-file", str(out_file)])
check("04_run_finish 退出码 0", rc == 0, out.strip()[:160])
check("04 汇总里数到了 2 条坐标", '"collectedRows": 2' in out)
check("04 汇总里数到了 2 条校验记录", '"verifiedTasks": 2' in out)
check("04 判定可以入库", '"readyForEtl": true' in out)

# 04 应该能识别出脏数据
bad_file = out_file.parent / "bad.ini"
bad_file.write_text("-2400.5,700,-60,正常\n这一行没有坐标\n", encoding="utf-8")
rc, out = run_script("04_run_finish.py", ["--run-dir", str(out_file.parent), "--out-file", str(bad_file)])
check("04 能识别出格式不对的行", '"problems": 1' in out and '"readyForEtl": false' in out, out.strip()[:200])

# ---------------------------------------------------------------------------
print("== 3. 采集产物能被 ETL 吃进去（端到端） ==")
sys.path.insert(0, str(HERE))
import importlib
import etl  # noqa: E402

# 只跑解析部分：把 collector 的输出当作一个 raw 文件过一遍
text = out_file.read_text(encoding="utf-8")
rows_all = []
for i, ln in enumerate(text.splitlines(), start=1):
    got, _ = parse_line(ln, file="rpa/test/collected.ini", line_no=i)
    rows_all.extend(got)
check("ETL 解析器能读懂 collector 的输出", len(rows_all) == 2, f"解析出 {len(rows_all)} 行")
check("解析结果坐标正确", all(abs(r.x) < 20000 for r in rows_all))

# ---------------------------------------------------------------------------
print("== 4. 任务清单 ==")
rc = subprocess.run([sys.executable, str(RPA / "make_tasklist.py"), "--limit", "5"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(PROJECT))
check("make_tasklist 退出码 0", rc.returncode == 0, rc.stderr[:200])
idx = RPA / "tasks" / "index.json"
check("生成了任务索引", idx.exists())
if idx.exists():
    doc = json.loads(idx.read_text(encoding="utf-8"))
    check("索引里有清单", len(doc["lists"]) >= 2, str([l["name"] for l in doc["lists"]]))
    for item in doc["lists"]:
        f = PROJECT / item["file"]
        check(f"清单 {item['name']} 存在且非空", f.exists() and f.stat().st_size > 0, f"{item['rows']} 条")

# ---------------------------------------------------------------------------
# 收尾：清掉自测产生的所有临时产物（含手工跑出来的 stdout 重定向文件）
for junk in (PROJECT / "raw" / "rpa").glob("_selftest*"):
    if junk.is_dir():
        shutil.rmtree(junk, ignore_errors=True)
    else:
        junk.unlink(missing_ok=True)

print()
print(f"结果：{passed} 项通过，{failed} 项失败")
raise SystemExit(1 if failed else 0)
