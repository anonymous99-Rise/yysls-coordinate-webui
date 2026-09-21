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
import math
import re
import shutil
import subprocess
import sys
import time
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
skipped = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [OK]   {label}" + (f"  {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  [FAIL] {label}  {detail}")


def skip(label: str, why: str) -> None:
    """环境不支持时明确记为「跳过」，而不是让它冒充失败。

    「跳过」和「通过」要分开计数：把环境问题混进失败里，会让人以为代码坏了；
    混进通过里，又会让真正的回归藏起来。
    """
    global skipped
    skipped += 1
    print(f"  [跳过] {label}  ——  {why}")


def input_io_available() -> tuple[bool, str]:
    """探测当前会话还能不能做键鼠输入。

    锁屏 / 会话断开 / 切到别的桌面时，SendInput 会静默失败、低层键盘钩子也收不到键。
    这不是代码问题，是环境问题 —— 而且**今天早些时候同样这两条测试是通过的**
    （实测 2 秒按 4 次、回环收到探针 z/x/q），所以必须先探测再断言。
    """
    try:
        import ctypes
        from ctypes import wintypes
        # 打不开输入桌面 = 锁屏或会话不活跃
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        u32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        u32.OpenInputDesktop.restype = wintypes.HANDLE
        u32.CloseDesktop.argtypes = [wintypes.HANDLE]
        h = u32.OpenInputDesktop(0, False, 0x0001)
        if not h:
            return False, "打不开输入桌面（会话被锁屏/断开？）"
        u32.CloseDesktop(h)
    except Exception as e:
        return False, f"桌面探测失败：{type(e).__name__}: {e}"
    try:
        if not injector.press_key("f9", 0.01):
            return False, "SendInput 返回失败（注入被拦或会话不可交互）"
    except Exception as e:
        return False, f"试发键失败：{type(e).__name__}: {e}"
    return True, ""


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
print("== 5. 输入模块（injector）==")
sys.path.insert(0, str(PROJECT / "rpa"))
import injector as inj  # noqa: E402

check("按键名归一化：大写/别名", inj.normalize_key("F") == "f" and inj.normalize_key("Space") == "space"
      and inj.normalize_key("Return") == "enter")
check("扫描码表：F=0x21 Space=0x39 F10=0x44 F12=0x58",
      inj.SCANCODES["f"][0] == 0x21 and inj.SCANCODES["space"][0] == 0x39
      and inj.SCANCODES["f10"][0] == 0x44 and inj.SCANCODES["f12"][0] == 0x58)
check("方向键标了扩展位", inj.SCANCODES["up"][1] is True and inj.SCANCODES["f"][1] is False)

try:
    inj.press_key("这个键不存在zzz")
    check("不认识的键名会报错", False, "居然没报错")
except ValueError:
    check("不认识的键名会报错", True)

check("dry-run 不发键也不报错", inj.press_key("f", 0.01, dry_run=True) is True)

# 拟人化：区间必须落在配置范围内，且真的在抖（不是每次一样）
h = inj.Humanize(click_offset_px=3, jitter_ratio=0.2)
offs = [h.offset() for _ in range(40)]
check("随机偏移落在 ±click_offset_px 内", all(abs(dx) <= 3 and abs(dy) <= 3 for dx, dy in offs))
check("随机偏移确实在变（不是固定值）", len({o for o in offs}) > 3)
off_off = inj.Humanize(enabled=False).offset()
check("关掉拟人化后偏移恒为 0", off_off == (0, 0))
pts = [h.jitter_point(1000, 500, 80, 60) for _ in range(40)]
check("区域抖动也落在了合理范围", all(940 <= x <= 1060 and 440 <= y <= 560 for x, y in pts))

# 全局热键：能不能注册 + 能不能被真实按键触发（回环）
# ⚠️ 这条是**环境敏感**的：钩子能否捕获注入的按键，取决于会话有没有可交互桌面、
# 钩子有没有被占用、机器负载。先探测，探不到就明确「跳过」而不是报失败。
_io_ok, _io_why = input_io_available()
if not _io_ok:
    skip("全局热键能被真实按键触发", _io_why)
else:
    hot_ok, hot_detail = False, ""
    for attempt in range(3):
        try:
            w = inj.HotkeyWatcher(poll_interval=0.02)
            fired: list[str] = []
            w.add("F9", lambda: fired.append("f9"))
            backend = w.start()
            time.sleep(0.6 + attempt * 0.3)
            for _ in range(3):
                inj.press_key("f9", 0.05)
                time.sleep(0.4)
            w.stop()
            if fired:
                hot_ok, hot_detail = True, f"后端={backend} 触发 {len(fired)} 次（第 {attempt + 1} 轮）"
                break
            hot_detail = f"后端={backend} 3 轮共 9 次按键都没触发"
        except Exception as e:
            hot_detail = f"{type(e).__name__}: {e}"
        time.sleep(0.5)
    check("全局热键能被真实按键触发", hot_ok,
          hot_detail + ("" if hot_ok else "  ← 会话可交互，所以这说明热键链路真坏了"))

# 窗口枚举
try:
    rows = inj._enum_visible_windows()
    check("能枚举到可见窗口", len(rows) > 0, f"{len(rows)} 个")
    check("窗口枚举带 pid 和标题", all(isinstance(r[1], int) and isinstance(r[2], str) for r in rows))
except Exception as e:
    check("能枚举到可见窗口", False, f"{type(e).__name__}: {e}")

# ---------------------------------------------------------------------------
print("== 6. 视觉模块（vision）==")
try:
    import numpy as np  # noqa: E402
    import vision as vis  # noqa: E402
    HAVE_VIS = True
except ImportError as e:
    HAVE_VIS = False
    print(f"  [跳过] 缺依赖：{e}（pip install numpy）")

if HAVE_VIS:
    # 灰度：必须和手算 BT.601 一致
    px = np.array([[[120, 200, 30]]], dtype=np.uint8)  # BGR
    want = 0.114 * 120 + 0.587 * 200 + 0.299 * 30
    got = float(vis.to_gray(px)[0, 0])
    check("灰度转换与手算 BT.601 一致", abs(got - want) < 1e-6, f"{got:.4f} vs {want:.4f}")

    # 积分图：和朴素求和对比
    a = np.arange(1, 1 + 20 * 25, dtype=float).reshape(20, 25)
    ii = vis._integral(a)
    naive = float(a[5:9, 7:13].sum())
    from_integral = float(ii[9, 13] - ii[5, 13] - ii[9, 7] + ii[5, 7])
    check("积分图窗口求和 == 朴素求和", abs(naive - from_integral) < 1e-9,
          f"{from_integral} vs {naive}")

    # NCC：从图里裁模板再找回去，位置和分数都要对
    rng = np.random.default_rng(3)
    big = rng.integers(0, 256, size=(160, 200)).astype(float)
    for tx, ty in ((0, 0), (37, 53), (152, 120)):
        tpl = big[ty:ty + 32, tx:tx + 40]
        hit = vis.find_best(big, tpl, use_cv2=False)
        check(f"自研 NCC 找回 ({tx},{ty})", abs(hit.x - tx) <= 1 and abs(hit.y - ty) <= 1 and hit.score > 0.999,
              f"@({hit.x},{hit.y}) score={hit.score:.5f}")

    # 反面：噪声不该匹配上
    noise = rng.integers(0, 256, size=(32, 40)).astype(float)
    hit = vis.find_best(big, noise, use_cv2=False)
    check("随机噪声匹配分数低", hit.score < 0.5, f"score={hit.score:.4f}")

    # 纯色模板必须明确报错（这是踩过的坑：早先静默返回 0，看起来像"在左上角匹配到 0 分"）
    try:
        vis.find_best(big, np.full((32, 40), 128.0), use_cv2=False)
        check("纯色模板被明确拒绝", False, "没报错")
    except ValueError:
        check("纯色模板被明确拒绝", True)

    # 模板比搜索区域还大
    try:
        vis.find_best(big[:10, :10], big[:50, :50], use_cv2=False)
        check("超大模板被拒绝", False, "没报错")
    except ValueError:
        check("超大模板被拒绝", True)

    # find_all + 非极大值抑制：同一个图案放两处，应找到 2 个
    canvas = rng.integers(0, 256, size=(120, 200)).astype(float)
    patch = rng.integers(0, 256, size=(24, 24)).astype(float)
    canvas[10:34, 10:34] = patch
    canvas[70:94, 140:164] = patch
    hits = vis.find_all(canvas, patch, threshold=0.99, use_cv2=False)
    check("find_all 找到两处（非极大值抑制生效）", len(hits) == 2,
          str([(h.x, h.y) for h in hits]))

    # cv2 是否可用的判断必须诚实（早先自检里那行"cv2 对照"其实跑的是自研代码）
    avail = vis.cv2_available()
    try:
        import cv2  # noqa: F401
        real = True
    except ImportError:
        real = False
    check("cv2_available() 与真实情况一致", avail == real, f"报告={avail} 实际={real}")

# ---------------------------------------------------------------------------
print("== 7. 采集循环（gather）==")
gather_py = PROJECT / "rpa" / "gather.py"

# 回归：--duration 曾经和按键时长共用一个名字，导致 `--duration 2` 把 F 键按住 2 秒、
# 循环只跑 1 轮。现在必须分成 --duration（总时长）和 --key-duration（按一下多长）。
p = subprocess.run(
    [sys.executable, str(gather_py), "--mode", "spam", "--key", "f",
     "--interval", "0.25", "--jitter", "0", "--duration", "2", "--key-duration", "0.02"],
    capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(PROJECT),
)
check("spam 模式退出码 0", p.returncode == 0, (p.stderr or "")[:200])
m = re.search(r"发键 (\d+) 次", p.stdout or "")
_pressed = int(m.group(1)) if m else -1
if not _io_ok:
    # 发键被环境拦掉时，这里必然数到 0 次。别让它冒充失败：
    # 这条断言真正要盯的是「--duration 不冒充按键时长」，而那要靠真的发得出去才验得了。
    skip("2 秒 / 0.25 秒间隔 至少按 4 次（--duration 不再冒充按键时长）", _io_why)
else:
    check("2 秒 / 0.25 秒间隔 至少按 4 次（--duration 不再冒充按键时长）",
          _pressed >= 4, f"实际按了 {_pressed} 次")

p = subprocess.run([sys.executable, str(gather_py), "--mode", "detect"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(PROJECT))
check("detect 缺 --template 时给出人话提示", "需要 --template" in (p.stdout or ""), (p.stdout or "")[:80])

p = subprocess.run([sys.executable, str(gather_py), "--mode", "detect", "--template", "x.png"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(PROJECT))
check("detect 缺 --region 时给出人话提示", "需要 --region" in (p.stdout or ""))

p = subprocess.run([sys.executable, str(gather_py), "--mode", "spam", "--duration", "1", "--dry-run"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(PROJECT))
check("dry-run 会明说不会真发键", "--dry-run" in (p.stdout or "") or "不会真的发键" in (p.stdout or ""))
check("采集会话目录与日志已生成",
      any((PROJECT / "raw" / "rpa").glob("gather_*/gather_log.csv")), "")
for d in (PROJECT / "raw" / "rpa").glob("gather_*"):
    shutil.rmtree(d, ignore_errors=True)
for f in (PROJECT / "raw" / "rpa").glob("_tpl.png"):
    f.unlink(missing_ok=True)

# ---------------------------------------------------------------------------
print("== 8. 地图标定与寻路（mapnav / navigate）==")
try:
    import mapnav as navmod  # noqa: E402
    HAVE_NAV = True
except ImportError as e:
    HAVE_NAV = False
    print(f"  [跳过] 缺依赖：{e}")

if HAVE_NAV:
    rng = np.random.default_rng(11)
    world = rng.uniform(-4000, 400, size=(12, 2))
    true_s, true_th = 0.185, np.radians(-7.5)
    aa, bb = true_s * np.cos(true_th), true_s * np.sin(true_th)
    tx0, ty0 = 1180.0, 640.0
    screen = np.column_stack([aa * world[:, 0] - bb * world[:, 1] + tx0,
                              bb * world[:, 0] + aa * world[:, 1] + ty0])

    t, allm = navmod.fit_best(world, screen)
    check("相似变换能精确还原已知变换（尺度/旋转/平移）",
          abs(t.scale - true_s) < 1e-6 and abs(t.rotation_deg - math.degrees(true_th)) < 1e-6
          and t.rms < 1e-6,
          f"scale={t.scale:.6f} rot={t.rotation_deg:+.3f}° RMS={t.rms:.2e}")

    # 回归：Transform 的字段顺序是 (model, params, rms, n, max_err)，
    # 早先用 `Transform(model, params, *_err(...), n)` 把 max_err 和 n 弄反了 ——
    # 打印出来的「最大误差」一直是点的个数（3 个点显示 3.00，看着挺合理，就骗过去了）。
    # 这里直接盯住 n 和 max_err 各自是不是对的。
    check("Transform.n 是点的个数", t.n == len(world), f"n={t.n} 期望 {len(world)}")
    check("完美线性关系下 max_err 接近 0（而不是等于点数）",
          t.max_err < 1e-6, f"max_err={t.max_err:.2e}")
    outl_w = np.vstack([world, [[9000.0, 9000.0]]])
    outl_s = np.vstack([screen, [[9999.0, 9999.0]]])
    t_out = navmod.fit_affine(outl_w, outl_s)
    check("有离群点时 max_err 真的反映最大偏差（且 >= RMS）",
          t_out.n == len(outl_w) and t_out.max_err > t_out.rms,
          f"n={t_out.n} max_err={t_out.max_err:.1f} rms={t_out.rms:.1f}")

    noisy = screen + rng.normal(0, 1.0, screen.shape)
    t2, _ = navmod.fit_best(world, noisy)
    check("加 σ=1px 噪声后仍稳定", t2.rms < 2.5, f"RMS={t2.rms:.2f}px")
    check("噪声下尺度估计没跑偏", abs(t2.scale - true_s) < 0.002, f"scale={t2.scale:.5f}")

    # 关键诊断：地图 Y 轴与世界 Y 轴相反时，相似变换表达不了，必须自动切到仿射并提醒
    flipped = np.column_stack([screen[:, 0], 900 - screen[:, 1]])
    tf, allf = navmod.fit_best(world, flipped)
    check("地图 Y 翻转能被诊断出来并自动改用仿射",
          tf.model == "affine" and allf["affine"].rms < 1e-6 and allf["similarity"].rms > 100,
          f"similarity RMS={allf['similarity'].rms:.1f} vs affine={allf['affine'].rms:.4f}")

    check("仅 2 点也能解相似变换", navmod.fit_similarity(world[:2], screen[:2]).rms < 1e-6)
    try:
        navmod.fit_affine(world[:2], screen[:2])
        check("仿射变换点不够时会明确报错", False, "居然没报错")
    except ValueError:
        check("仿射变换点不够时会明确报错", True)

    # 锚点：我们从数据里真的取到了界碑/传送点
    anchors = navmod.load_anchors()
    check("从数据里取到界碑/传送点锚点", len(anchors) >= 100, f"{len(anchors)} 个")
    check("锚点带世界坐标和名字", all("x" in a and "y" in a and "name" in a for a in anchors))

    # 锚点分布质量：**这套指标必须能区分好坏** —— 上一版用设计矩阵条件数，
    # 因被世界坐标的绝对值主导，把铺得最开的样本也判成「分布差」。
    # 一个永远报警的警告比没有警告更糟：用户会学会无视它。
    allp = np.array([[a["x"], a["y"]] for a in anchors], dtype=float)
    picks = navmod.suggest_anchors(4)
    check("推荐的锚点带具体地名（不是「开封」这种占位名）",
          all(len(p["name"]) >= 4 and p["name"] not in ("清河", "开封", "江南") for p in picks),
          str([p["name"] for p in picks]))
    good = np.array([[p["x"], p["y"]] for p in picks], dtype=float)
    mg, hg = navmod.spread_quality(good, allp)
    check("铺得开的锚点被判为好", "良好" in hg, hg)
    coll = np.column_stack([np.linspace(-3000, -1000, 4), np.linspace(-1000, -998, 4)])
    _, hc = navmod.spread_quality(coll, allp)
    check("接近共线的锚点被判为危险", "危险" in hc, hc)
    corner = allp[(allp[:, 0] > -1500) & (allp[:, 1] > 500)][:4]
    mk, _ = navmod.spread_quality(corner, allp)
    check("挤在一角的锚点被判为偏差", mk["coverage"] < 0.35, f"覆盖 {mk['coverage'] * 100:.0f}%")
    check("好样本的覆盖率明显高于挤角样本", mg["coverage"] > mk["coverage"] + 0.3,
          f"{mg['coverage']:.2f} vs {mk['coverage']:.2f}")

    # 端到端：画到图上，再读回像素确认圈真画在该在的位置
    try:
        from PIL import Image
        tmp = PROJECT / "raw" / "rpa" / "_navselftest"
        tmp.mkdir(parents=True, exist_ok=True)
        W2, H2 = 1200, 700
        fake = rng.integers(60, 90, size=(H2, W2, 3), dtype=np.uint8)
        fake[::40, :] = 120
        fake[:, ::40] = 120
        map_png = tmp / "fake_map.png"
        Image.fromarray(fake).save(map_png)

        def truth(px, py):
            return aa * px - bb * py + tx0, bb * px + aa * py + ty0

        picked = [anchors[i] for i in (0, 20, 60)]
        pairs_doc = {"map": str(map_png), "pairs": [
            {"world": [p["x"], p["y"]], "label": p["name"],
             "screen": [round(v, 1) for v in truth(p["x"], p["y"])]} for p in picked]}
        pf = tmp / "pairs.json"
        pf.write_text(json.dumps(pairs_doc, ensure_ascii=False), encoding="utf-8")

        r = subprocess.run([sys.executable, str(PROJECT / "rpa" / "mapnav.py"),
                            "--fit", str(pf), "--calibration", str(tmp / "calib.json")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("--fit 命令行跑通", r.returncode == 0, (r.stderr or "")[:150])
        tc = navmod.Transform.from_dict(
            json.loads((tmp / "calib.json").read_text(encoding="utf-8"))["transform"])
        # 像素只给了 1 位小数（模拟人工量像素），所以容差按 0.1px 舍入量级给
        check("标定精度达到人工量像素的极限（<0.1px）", tc.rms < 0.1, f"RMS={tc.rms:.4f}px")

        overlay = tmp / "overlay.png"
        r = subprocess.run([sys.executable, str(PROJECT / "rpa" / "mapnav.py"),
                            "--overlay", str(map_png), "--out", str(overlay),
                            "--calibration", str(tmp / "calib.json"), "--no-labels"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        check("--overlay 命令行跑通", r.returncode == 0 and overlay.exists())

        im = np.asarray(Image.open(overlay).convert("RGB")).astype(int)
        hit = miss = 0
        for p in anchors:
            ex, ey = truth(p["x"], p["y"])
            cx, cy = int(round(ex)), int(round(ey))
            if not (10 <= cx < W2 - 10 and 10 <= cy < H2 - 10):
                continue
            win = im[max(0, cy - 8):cy + 9, max(0, cx - 8):cx + 9]
            if ((win[:, :, 0] > 200) & (win[:, :, 1] < 110) & (win[:, :, 2] < 110)).any():
                hit += 1
            else:
                miss += 1
        check("投影标记真的画在了正确像素上（读回图像验证）", miss == 0 and hit > 50,
              f"{hit} 个命中 / {miss} 个错位")
        shutil.rmtree(tmp, ignore_errors=True)
    except ImportError:
        print("  [跳过] 画图核对需要 PIL")

    # navigate.py：干跑与报错路径
    navtest = PROJECT / "raw" / "rpa" / "_navtest.csv"
    navtest.parent.mkdir(parents=True, exist_ok=True)
    navtest.write_text("pointId,name,x,y\np1,测试点,-2404,972\np2,测试点2,-2335,960\n",
                       encoding="utf-8-sig")
    try:
        r = subprocess.run([sys.executable, str(PROJECT / "rpa" / "navigate.py"),
                            "--route", str(navtest),
                            "--calibration", str(PROJECT / "data" / "map_calibration.json"),
                            "--dry-run"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           cwd=str(PROJECT))
        # 没有真实标定文件时会明确拒绝，而不是瞎跑
        ok_msg = ("先跑" in (r.stdout or "")) or ("dry-run" in (r.stdout or ""))
        check("navigate 缺标定时明确报错、不瞎跑", ok_msg, (r.stdout or "")[:90])
    finally:
        navtest.unlink(missing_ok=True)
        for d in (PROJECT / "raw" / "rpa").glob("nav_*"):
            shutil.rmtree(d, ignore_errors=True)

print("== 9. 帧差与到达检测（vision）==")
if HAVE_VIS:
    a = np.zeros((40, 60, 3), dtype=np.uint8)
    b = a.copy()
    check("同一帧差异为 0", vis.frame_diff(a, b) == 0.0)
    b[10:20, 10:20] = 255
    d = vis.frame_diff(a, b)
    check("局部变化能算出差异", 0 < d < 255, f"差异={d:.2f}")
    try:
        vis.frame_diff(np.zeros((10, 10, 3), dtype=np.uint8), np.zeros((20, 20, 3), dtype=np.uint8))
        check("尺寸不同会报错而不是静默算错", False, "没报错")
    except ValueError:
        check("尺寸不同会报错而不是静默算错", True)

    # MotionDetector：喂合成帧序列，验证「连续静止才算停」的判据
    md = vis.MotionDetector((0, 0, 40, 60), threshold=2.0, still_frames=3, poll=0.01)
    check("MotionDetector 参数就绪", md.still_frames == 3 and md.threshold == 2.0)

# ---------------------------------------------------------------------------
# 收尾：清掉自测产生的所有临时产物（含手工跑出来的 stdout 重定向文件）
for junk in (PROJECT / "raw" / "rpa").glob("_selftest*"):
    if junk.is_dir():
        shutil.rmtree(junk, ignore_errors=True)
    else:
        junk.unlink(missing_ok=True)

print()
tail = f"结果：{passed} 项通过，{failed} 项失败"
if skipped:
    tail += f"，{skipped} 项因环境不支持跳过"
print(tail)
if skipped:
    print("  （跳过不等于通过：环境恢复可交互桌面后再跑一次，这两条必须真过）")
raise SystemExit(1 if failed else 0)
