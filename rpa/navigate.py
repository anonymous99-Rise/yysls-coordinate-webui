#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
navigate.py —— 用坐骑「识途」把角色带到指定坐标
================================================

游戏机制（已核实）
------------------
坐骑有「识途」技能，用法是：**打开地图 → 选一个目的地 → 弹框里点【识途】→ 自动寻路**。
两个必须尊重的约束：

  1. **寻路过程中任何操作都会打断寻路** → 传送期间一个键都不能发，只能看着
  2. **遇到水会停下来** → 不能死等固定时长，必须检测「画面不动了」

这意味着移动完全不需要第三方工具、不读内存、不注入进程 —— 零封号风险。
整条链是：**世界坐标 →（mapnav 变换）→ 地图像素 → 点一下 → 点【识途】→ 等到达 → 采集**

依赖的兄弟模块
--------------
    mapnav.py    世界坐标 → 地图像素（用 140 个界碑/传送点当锚点标定）
    vision.py    模板匹配（找【识途】按钮、找地图特征）+ 帧差（判断到没到）
    injector.py  发键与点击（扫描码 / PostMessage）

用法
----
    # 跑之前先把标定做出来（详见 mapnav.py 头部注释）
    python rpa/mapnav.py --fit pairs.json

    # 干跑：把「要点哪里」全套打印出来，不真的操作
    python rpa/navigate.py --route data/nav-龙骨.csv --dry-run

    # 真跑（游戏需在前台；F10 开始 / F12 暂停）
    python rpa/navigate.py --route data/nav-龙骨.csv \
        --map-key m --pathfind-template shitu.png --map-region 0,0,1600,900 --hotkey

【诚实的边界】本模块的**编排逻辑与坐标换算都能自动测试**（见 tools/test_rpa.py），
但「地图打开后长什么样、【识途】按钮在哪个位置、地图是否需要先拖到某个区域」
这几点**只有你开着游戏才能确认**。所以第一次务必先 `--dry-run`，
再手动跑一个点看看落点对不对，别直接挂机。
"""

# Windows 控制台默认用 ANSI 代码页（cp1252 / cp936），直接 print 中文会
# UnicodeEncodeError 崩掉。不指望调用方设 PYTHONIOENCODING —— 脚本自己保证输出编码。
# 这个缺陷在 CI 上才暴露：本地一直设着 PYTHONIOENCODING=utf-8，正好把它盖住了，
# 而任何非 UTF-8 控制台的 Windows 用户都会撞上。
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


from __future__ import annotations

import argparse
import csv
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
sys.path.insert(0, str(HERE))

import injector as inj  # noqa: E402
from injector import HotkeyWatcher  # noqa: E402

try:
    import vision as vis  # noqa: E402
except ImportError:  # pragma: no cover
    vis = None  # type: ignore[assignment]

try:
    import mapnav as nav  # noqa: E402
except ImportError:  # pragma: no cover
    nav = None  # type: ignore[assignment]


def load_route(path: str | Path) -> list[dict]:
    """读任务清单（需要 x,y 列）。也接受 mapnav --tasklist 产出的 screenX/screenY。"""
    rows = list(csv.DictReader(Path(path).open(encoding="utf-8-sig")))
    out = []
    for r in rows:
        try:
            out.append({
                "pointId": r.get("pointId", ""),
                "name": r.get("name", ""),
                "x": float(r["x"]),
                "y": float(r["y"]),
                "screen": (float(r["screenX"]), float(r["screenY"]))
                if r.get("screenX") and r.get("screenY") else None,
            })
        except (KeyError, ValueError):
            continue
    return out


def find_button(template: str, region: tuple[int, int, int, int],
                threshold: float) -> tuple[bool, tuple[int, int] | None, float]:
    """在地图区域里找【识途】按钮。找不到就返回 False —— 调用方必须当成失败处理。"""
    if vis is None:
        raise RuntimeError("需要 numpy：pip install numpy")
    x, y, w, h = region
    img = vis.grab(x, y, w, h)
    tpl = vis.load_gray(template)
    hit = vis.find_best(vis.to_gray(img), tpl)
    if hit.score < threshold:
        return False, None, float(hit.score)
    return True, (x + hit.center[0], y + hit.center[1]), float(hit.score)


def gothere(args: argparse.Namespace, transform, item: dict, log) -> tuple[bool, str]:
    """把一个坐标点走到。返回 (是否确认到达, 说明)。"""
    if item["screen"]:
        px, py = item["screen"]
    else:
        px, py = transform.project(item["x"], item["y"])

    px, py = int(round(px)), int(round(py))
    if not (args.map_clip[0] <= px <= args.map_clip[2] and args.map_clip[1] <= py <= args.map_clip[3]):
        return False, f"落点 ({px},{py}) 不在可点击的地图区域内 {args.map_clip}，跳过"

    if args.dry_run:
        log(f"    [dry-run] 开地图({args.map_key}) → 点 ({px},{py}) → 点【识途】→ 关地图")
        return True, "dry-run"

    # ---- 1. 开地图 ----
    inj.press_key(args.map_key, args.key_duration)
    time.sleep(args.map_open_wait)

    # ---- 2. 点目的地 ----
    inj.click_at(px, py, duration=args.key_duration)
    time.sleep(args.popup_wait)

    # ---- 3. 找并点【识途】 ----
    if args.pathfind_template:
        found, pos, score = find_button(args.pathfind_template, args.map_region, args.pathfind_threshold)
        if not found:
            return False, f"没找到【识途】按钮（最高分 {score:.3f}）；地图可能没打开或模板不对"
        if args.verbose:
            log(f"    【识途】按钮 @{pos} score={score:.3f}")
        inj.click_at(pos[0], pos[1], duration=args.key_duration)
    else:
        log("    [!] 没给 --pathfind-template，假定弹框按钮在固定位置")
        if not args.pathfind_fixed:
            return False, "缺少 --pathfind-template 或 --pathfind-fixed，无法点【识途】"
        fx, fy = args.pathfind_fixed
        inj.click_at(fx, fy, duration=args.key_duration)

    time.sleep(args.popup_wait)

    # ---- 4. 关地图（寻路期间不能再动）----
    inj.press_key("esc", args.key_duration)
    time.sleep(args.close_wait)

    # ---- 5. 等到达：靠帧差，不死等 ----
    arrived, waited = (False, 0.0)
    if args.arrive_region:
        if vis is None:
            return False, "到达检测需要 numpy"
        md = vis.MotionDetector(args.arrive_region, threshold=args.still_threshold,
                                still_frames=args.still_frames, poll=args.still_poll)
        arrived, waited = md.wait_until_still(timeout=args.arrive_timeout)
        if not arrived:
            return False, f"等待 {waited:.0f}s 画面一直没静止 —— 可能卡住或遇水停了"
    else:
        time.sleep(args.arrive_timeout)
        arrived, waited = True, args.arrive_timeout
        log(f"    [!] 没给 --arrive-region，只能盲等 {args.arrive_timeout}s")

    return arrived, f"约 {waited:.1f}s 后画面静止"


def run(args: argparse.Namespace) -> int:
    if nav is None:
        print("[x] 需要 numpy 和 mapnav.py")
        return 2
    if not inj.is_admin():
        print("[!] 当前不是管理员。游戏若以管理员运行，点击会被 UIPI 拦掉。")

    route = load_route(args.route)
    if not route:
        print(f"[x] {args.route} 里没读到有效坐标")
        return 2
    print(f"路线共 {len(route)} 个点")

    transform = None
    need_transform = any(not r["screen"] for r in route)
    if need_transform:
        p = Path(args.calibration)
        if not p.exists():
            print(f"[x] 路线里没有 screenX/screenY，而标定文件 {p} 不存在。")
            print("    先跑：python rpa/mapnav.py --fit pairs.json")
            return 2
        transform = nav.Transform.from_dict(json.loads(p.read_text(encoding="utf-8"))["transform"])
        print(f"标定：{transform.model}  RMS={transform.rms:.2f}px")

    if args.dry_run:
        print("[dry-run] 不会真的操作游戏")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = PROJECT / "raw" / "rpa" / f"nav_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "nav_log.csv"
    with log_path.open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerow(["ts", "pointId", "name", "worldX", "worldY",
                                "screenX", "screenY", "ok", "note"])

    def log(msg: str) -> None:
        print(msg, flush=True)

    state = {"run": threading.Event(), "stop": threading.Event()}
    state["run"].set()
    watcher = None
    if args.hotkey:
        watcher = HotkeyWatcher()
        watcher.add(args.start_key, lambda: (state["run"].set(), log(f"  [{args.start_key}] ▶ 继续")))
        watcher.add(args.stop_key, lambda: (state["run"].clear(), log(f"  [{args.stop_key}] ⏸ 暂停")))
        try:
            log(f"热键后端：{watcher.start()}")
        except Exception as e:
            print(f"[x] 热键注册失败：{e}")
            return 2

    ok_n = fail_n = 0
    try:
        for i, item in enumerate(route, 1):
            while not state["run"].is_set() and not state["stop"].is_set():
                time.sleep(0.1)
            if state["stop"].is_set():
                break
            px = item["screen"][0] if item["screen"] else transform.project(item["x"], item["y"])[0]
            py = item["screen"][1] if item["screen"] else transform.project(item["x"], item["y"])[1]
            log(f"  [{i}/{len(route)}] {item['name'] or item['pointId']}  "
                f"世界({item['x']:.0f},{item['y']:.0f}) → 地图({px:.0f},{py:.0f})")
            ok, note = gothere(args, transform, item, log)
            log(f"      {'✓' if ok else '✗'} {note}")
            if ok:
                ok_n += 1
            else:
                fail_n += 1
            with log_path.open("a", encoding="utf-8-sig", newline="") as f:
                csv.writer(f).writerow([datetime.now().isoformat(timespec="seconds"),
                                        item["pointId"], item["name"], f"{item['x']:.2f}",
                                        f"{item['y']:.2f}", f"{px:.1f}", f"{py:.1f}",
                                        1 if ok else 0, note])
            if ok and args.tap_key and not args.dry_run:
                # 到了就采一下（详细采集逻辑用 gather.py；这里只做一下简单按键）
                time.sleep(args.tap_delay)
                inj.press_key(args.tap_key, args.key_duration)
    except KeyboardInterrupt:
        log("\n收到 Ctrl+C，收工")
    finally:
        if watcher:
            watcher.stop()

    print(f"\n== 寻路结果 ==\n  成功 {ok_n} · 失败 {fail_n}\n  日志：{log_path}")
    if fail_n:
        print("  失败的都写在日志 note 列里 —— 先看那几条，多半是模板/落点/地图区域的问题。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="用坐骑识途走到指定坐标")
    ap.add_argument("--route", default="", help="任务清单 CSV（需含 x,y；有 screenX/screenY 更好）")
    ap.add_argument("--calibration", default=str(PROJECT / "data" / "map_calibration.json"))
    ap.add_argument("--map-key", default="m", help="打开地图的按键（默认 m）")
    ap.add_argument("--map-region", type=lambda s: tuple(int(v) for v in s.split(",")),
                    default=(0, 0, 1600, 900), help="地图全屏区域 x,y,w,h")
    ap.add_argument("--map-clip", type=lambda s: tuple(int(v) for v in s.split(",")),
                    default=(0, 0, 1600, 900), help="允许点击的地图区域 x0,y0,x1,y1（避开边框/UI）")
    ap.add_argument("--pathfind-template", default="", help="【识途】按钮的模板图")
    ap.add_argument("--pathfind-threshold", type=float, default=0.85)
    ap.add_argument("--pathfind-fixed", type=lambda s: tuple(int(v) for v in s.split(",")),
                    default=None, help="不想用模板时，直接给按钮固定坐标 x,y")
    ap.add_argument("--arrive-region", type=lambda s: tuple(int(v) for v in s.split(",")),
                    default=None, help="用于判断「到没到」的画面区域 x,y,w,h（建议框在角色附近）")
    ap.add_argument("--arrive-timeout", type=float, default=90.0, help="等到达的最长秒数")
    ap.add_argument("--still-threshold", type=float, default=2.0, help="帧差低于多少算静止")
    ap.add_argument("--still-frames", type=int, default=3, help="连续几次静止才算停下")
    ap.add_argument("--still-poll", type=float, default=0.35)
    ap.add_argument("--map-open-wait", type=float, default=1.2, help="开地图后等多久（等界面加载完）")
    ap.add_argument("--popup-wait", type=float, default=0.8, help="点完等弹框/响应")
    ap.add_argument("--close-wait", type=float, default=0.8, help="关地图后等多久")
    ap.add_argument("--key-duration", type=float, default=0.06)
    ap.add_argument("--tap-key", default="", help="到达后顺手按一下（比如 f 采集）")
    ap.add_argument("--tap-delay", type=float, default=0.5)
    ap.add_argument("--hotkey", action="store_true", help="F10 继续 / F12 暂停")
    ap.add_argument("--start-key", default="F10")
    ap.add_argument("--stop-key", default="F12")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if not args.route:
        ap.print_help()
        print("\n提示：路线文件可以用 rpa/make_tasklist.py 生成，"
              "再用 rpa/mapnav.py --tasklist 加上地图像素列。")
        return 0
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
