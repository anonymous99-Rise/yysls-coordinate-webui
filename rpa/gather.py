#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gather.py —— 自动采集物品的循环
================================

这是把前两个模块接起来的地方：
    vision.py    看见（截图 + 模板匹配）
    injector.py  动手（扫描码发键 + 全局热键）

三种模式，从「零准备」到「有判断」：

  spam   连点器 —— 固定/随机间隔一直按 F。不需要任何模板，现在就能用。
         对应 yysls_auto 的「连点器」「自动采集」预设（按 1 → 等 10~12 秒）。

  detect 有判断 —— 只在屏幕上**看得见采集提示**时才按 F。
         需要你给一张提示图标的模板 PNG（截图后裁一小块）。这是推荐模式：
         没东西的地方不会瞎按，采完了会自己停。

  spot   定点确认 —— detect 的加强版：按完 F 后等提示消失，确认采集成功并计数；
         提示一直不消失就重试，超过重试上限判定这个点已枯竭。

为什么值得做 detect/spot 而不是只做连点器
----------------------------------------
连点器会在没东西的地方空按几百次，你还不知道它到底采到没有。
detect/spot 能回答「这个地方还有没有」—— 这正是我们那 693 个「同坐标多名」可疑点
和 412 个无名点需要的能力：让机器去判断，而不是人跑几百趟。

用法
----
    # 1) 先看环境：发键通不通、屏幕能不能截
    python rpa/gather.py --check

    # 2) 零准备先用连点器（当前窗口会收到 F）
    python rpa/gather.py --mode spam --key f --interval 1.5

    # 3) 有判断的采集（需要模板）
    python rpa/gather.py --mode spot --key f \
        --template prompt.png --region 700,380,320,220 --threshold 0.82

    # 4) 挂机跑：F10 开始 / F12 停止（全屏游戏里按得到，Ctrl+C 按不到）
    python rpa/gather.py --mode spot --template prompt.png --region ... --hotkey

    # 干跑：不真发键，只打印判断结果（先确认模板对不对）
    python rpa/gather.py --mode detect --template prompt.png --region ... --dry-run
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
from injector import Humanize, HotkeyWatcher, human_pause  # noqa: E402

try:
    import vision as vis  # noqa: E402
except ImportError as e:  # pragma: no cover
    print(f"[x] 视觉模块不可用（{e}）。detect/spot 模式需要 numpy：pip install numpy", file=sys.stderr)
    vis = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 采集会话记录
# ---------------------------------------------------------------------------
class Session:
    def __init__(self, out_dir: Path, mode: str, cfg: dict) -> None:
        self.dir = out_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = self.dir / "gather_log.csv"
        self._new = not self.csv_path.exists()
        self.stats = {
            "mode": mode, "startedAt": datetime.now().isoformat(timespec="seconds"),
            "config": cfg, "loops": 0, "pressed": 0, "detected": 0,
            "confirmed": 0, "failed": 0, "skipped": 0,
        }
        self._lock = threading.Lock()

    def log(self, event: str, **kv) -> None:
        with self._lock:
            with self.csv_path.open("a", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                if self._new:
                    w.writerow(["ts", "event", "score", "x", "y", "note"])
                    self._new = False
                w.writerow([
                    datetime.now().isoformat(timespec="seconds"), event,
                    kv.get("score", ""), kv.get("x", ""), kv.get("y", ""), kv.get("note", ""),
                ])

    def bump(self, key: str, n: int = 1) -> None:
        with self._lock:
            self.stats[key] = self.stats.get(key, 0) + n

    def save(self) -> Path:
        self.stats["finishedAt"] = datetime.now().isoformat(timespec="seconds")
        p = self.dir / "gather_session.json"
        p.write_text(json.dumps(self.stats, ensure_ascii=False, indent=2), encoding="utf-8")
        return p


# ---------------------------------------------------------------------------
# 探测：屏幕上有采集提示吗
# ---------------------------------------------------------------------------
class PromptDetector:
    """在一次截图里找采集提示模板。抓不到画面/匹配异常都不会把循环搞崩。"""

    def __init__(self, template_path: str, region: tuple[int, int, int, int], threshold: float) -> None:
        if vis is None:
            raise RuntimeError("detect/spot 模式需要 numpy：pip install numpy")
        self.tpl = vis.load_gray(template_path)
        self.region = region
        self.threshold = threshold
        self.last_error = ""
        if float(self.tpl.std()) < 3.0:
            raise ValueError(
                f"模板图片 {template_path} 几乎是纯色（标准差 {self.tpl.std():.2f}）。"
                f" 裁模板时要包含图标/文字/边框这类有对比的内容。"
            )

    def detect(self) -> tuple[bool, float, tuple[int, int] | None]:
        """返回 (是否看到提示, 最高分, 屏幕坐标)。抓图失败不抛异常，只报没看到。"""
        x, y, w, h = self.region
        try:
            img = vis.grab(x, y, w, h)
            hay = vis.to_gray(img)
            hit = vis.find_best(hay, self.tpl)
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return False, 0.0, None
        self.last_error = ""
        seen = hit.score >= self.threshold
        return seen, float(hit.score), ((x + hit.center[0], y + hit.center[1]) if seen else None)


# ---------------------------------------------------------------------------
# 主循环
# ---------------------------------------------------------------------------
def run(args: argparse.Namespace) -> int:
    if args.mode in ("detect", "spot") and not args.template:
        print("[x] detect/spot 模式需要 --template <提示图标.png>")
        print("    怎么弄：游戏里出现采集提示时截图（Win+Shift+S），裁下那块提示图标存成 PNG。")
        return 2

    if args.mode in ("detect", "spot") and vis is None:
        print("[x] detect/spot 模式需要 numpy：pip install numpy")
        return 2

    if vis is None and args.mode in ("detect", "spot"):
        print("[x] detect/spot 模式需要 numpy：pip install numpy")
        return 2

    region = args.region or (0, 0, 0, 0)
    if args.mode in ("detect", "spot") and (region[2] == 0 or region[3] == 0):
        print("[x] detect/spot 模式需要 --region x,y,w,h 指定「提示会出现在屏幕哪块」")
        return 2

    detector = None
    if args.mode in ("detect", "spot"):
        try:
            detector = PromptDetector(args.template, region, args.threshold)
        except Exception as e:
            print(f"[x] 模板不可用：{e}")
            return 2
        print(f"模板已加载：{args.template}  尺寸 {detector.tpl.shape[1]}x{detector.tpl.shape[0]}"
              f"  标准差 {detector.tpl.std():.1f}")
        print(f"搜索区域：{region}  阈值 {args.threshold}")

    if args.dry_run:
        print("[dry-run] 不会真的发键")
    if not inj.is_admin():
        print("[!] 当前不是管理员。游戏若以管理员运行，按键会被 UIPI 拦掉（改成管理员跑这个脚本）。")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = PROJECT / "raw" / "rpa" / f"gather_{ts}_{args.mode}"
    sess = Session(out_dir, args.mode, {
        "key": args.key, "interval": args.interval, "jitter": args.jitter,
        "template": args.template, "region": list(region), "threshold": args.threshold,
    })
    hum = Humanize(enabled=not args.no_humanize)
    print(f"采集会话：{out_dir}")

    state = {"run": threading.Event(), "stop": threading.Event()}
    if args.hotkey:
        state["run"].set()  # 不带热键模式直接开跑
        watcher = HotkeyWatcher()
        watcher.add(args.start_key, lambda: (state["run"].set(), print(f"  [{args.start_key}] ▶ 开始")))
        watcher.add(args.stop_key, lambda: (state["run"].clear(), print(f"  [{args.stop_key}] ⏹ 暂停")))
        try:
            backend = watcher.start()
            print(f"热键后端：{backend}  （{args.start_key} 开始 / {args.stop_key} 暂停 / Ctrl+C 退出）")
        except Exception as e:
            print(f"[x] 热键注册失败：{e}")
            return 2
    else:
        state["run"].set()
        watcher = None
        print("按 Ctrl+C 结束（窗口模式下可以；全屏游戏里请用 --hotkey）")

    if args.duration:
        print(f"将在 {args.duration} 秒后自动停止")

    t0 = time.time()
    idle_rounds = 0
    exhausted = 0
    try:
        while not state["stop"].is_set():
            if args.duration and time.time() - t0 >= args.duration:
                print(f"到达设定时长 {args.duration}s，收工")
                break
            if not state["run"].is_set():
                time.sleep(0.1)
                continue

            sess.bump("loops")

            # ---- 采集动作 -------------------------------------------------
            if args.mode == "spam":
                acted = inj.press_key(args.key, args.key_duration, dry_run=args.dry_run)
                if acted:
                    sess.bump("pressed")
                    sess.log("press", note=f"spam#{sess.stats['pressed']}")
                    if args.verbose:
                        print(f"  按 {args.key}（第 {sess.stats['pressed']} 次）")
                human_pause(args.interval, args.jitter, enabled=not args.no_humanize)

            else:  # detect / spot
                seen, score, pos = detector.detect()
                if seen:
                    sess.bump("detected")
                    idle_rounds = 0
                    sess.log("detect", score=round(score, 4), x=pos[0], y=pos[1])
                    if args.verbose:
                        print(f"  看到提示 score={score:.3f} @{pos}")
                    if args.dry_run:
                        sess.log("skip", note="dry-run 不发键")
                        sess.bump("skipped")
                        time.sleep(0.5)
                        continue

                    # 拟人化：点前等一下，再按键
                    hum.wait_before()
                    ok = inj.press_key(args.key, args.key_duration)
                    sess.bump("pressed")
                    hum.wait_after()

                    if args.mode == "spot" and ok:
                        # 按完确认提示消失 = 采集成功
                        confirmed = False
                        for attempt in range(args.confirm_tries):
                            time.sleep(args.confirm_wait)
                            still, s2, _ = detector.detect()
                            if not still:
                                confirmed = True
                                break
                        if confirmed:
                            sess.bump("confirmed")
                            sess.log("confirmed", score=round(score, 4), note="提示已消失")
                            if args.verbose:
                                print("    ✓ 采集成功（提示消失）")
                        else:
                            sess.bump("failed")
                            exhausted += 1
                            sess.log("failed", score=round(score, 4),
                                     note=f"按了 {args.confirm_tries} 次提示仍在")
                            print(f"   ⚠ 提示没消失（第 {exhausted} 次）—— 可能已枯竭或按键没生效")
                    else:
                        human_pause(args.interval, args.jitter, enabled=not args.no_humanize)
                else:
                    idle_rounds += 1
                    if idle_rounds % max(1, args.idle_log_every) == 0:
                        sess.log("idle", score=round(score, 4),
                                 note=f"连续 {idle_rounds} 轮没看到提示")
                        if args.verbose:
                            print(f"  没看到提示（连续 {idle_rounds} 轮，最高分 {score:.3f}）")
                    if args.stop_when_idle and idle_rounds >= args.stop_when_idle:
                        print(f"连续 {idle_rounds} 轮没看到提示，判定该点已采完，停止。")
                        break
                    time.sleep(args.idle_interval)

    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，收工")
    finally:
        if watcher is not None:
            watcher.stop()
        meta = sess.save()

    s = sess.stats
    print()
    print("== 本次采集 ==")
    print(f"  循环 {s['loops']} 轮 · 看到提示 {s['detected']} 次 · 发键 {s['pressed']} 次")
    if args.mode == "spot":
        print(f"  确认采到 {s['confirmed']} 次 · 失败/枯竭 {s['failed']} 次")
    print(f"  日志：{sess.csv_path}")
    print(f"  汇总：{meta}")
    return 0


# ---------------------------------------------------------------------------
# 环境自检
# ---------------------------------------------------------------------------
def check() -> int:
    print("== 采集环境自检 ==")
    print(f"  Python      : {sys.version.split()[0]} ({sys.platform})")
    print(f"  管理员权限  : {'是' if inj.is_admin() else '否（游戏提权运行时按键会被拦）'}")
    if vis is None:
        print("  numpy       : 未装 —— detect/spot 模式不可用（pip install numpy）")
    else:
        print(f"  numpy       : {__import__('numpy').__version__}")
        print(f"  cv2         : {'已装（匹配走快路径）' if vis.cv2_available() else '未装（用自研 FFT/NCC，够用）'}")
        try:
            sw, sh = vis.screen_size()
            t0 = time.perf_counter()
            img = vis.grab(0, 0, min(200, sw), min(150, sh))
            dt = (time.perf_counter() - t0) * 1000
            print(f"  截图        : {img.shape}  {dt:.1f} ms")
        except Exception as e:
            print(f"  截图        : 失败 {type(e).__name__}: {e}")
    try:
        import keyboard  # noqa: PLC0415, F401
        print("  keyboard    : 已装（热键走事件钩子）")
    except ImportError:
        print("  keyboard    : 未装（热键退回 GetAsyncKeyState 轮询）")
    print()
    print("  发键链路回环自检（真发一个键再收回来）：")
    rc = inj.self_test()
    return rc


def _parse_region(s: str) -> tuple[int, int, int, int]:
    v = [int(x) for x in s.replace("，", ",").split(",")]
    if len(v) != 4:
        raise argparse.ArgumentTypeError("区域格式应为 x,y,w,h")
    return v[0], v[1], v[2], v[3]


def main() -> int:
    ap = argparse.ArgumentParser(description="自动采集物品")
    ap.add_argument("--check", action="store_true", help="环境自检")
    ap.add_argument("--mode", default="spam", choices=["spam", "detect", "spot"])
    ap.add_argument("--key", default="f", help="采集键（默认 f）")
    ap.add_argument("--template", default="", help="采集提示的模板图（detect/spot 必需）")
    ap.add_argument("--region", type=_parse_region, default=None, help="提示出现的区域 x,y,w,h")
    ap.add_argument("--threshold", type=float, default=0.82, help="匹配阈值（默认 0.82）")
    ap.add_argument("--interval", type=float, default=1.2, help="动作间隔基准秒数")
    ap.add_argument("--jitter", type=float, default=0.4, help="间隔抖动 ±秒（拟人化）")
    ap.add_argument("--key-duration", type=float, default=0.06,
                    help="每次按键「按住」多久（默认 0.06s；这是敲一下，不是长按）")
    ap.add_argument("--idle-interval", type=float, default=0.4, help="没看到提示时的轮询间隔")
    ap.add_argument("--duration", type=float, default=0.0, help="总共跑多少秒后停（0=不限）")
    ap.add_argument("--stop-when-idle", type=int, default=0,
                    help="连续 N 轮没看到提示就停（0=不停）")
    ap.add_argument("--confirm-tries", type=int, default=3, help="spot 模式确认重试次数")
    ap.add_argument("--confirm-wait", type=float, default=0.6, help="每次确认前等多久")
    ap.add_argument("--idle-log-every", type=int, default=20, help="多少轮空闲记一条日志")
    ap.add_argument("--hotkey", action="store_true", help="F10 开始 / F12 暂停（全屏游戏必开）")
    ap.add_argument("--start-key", default="F10")
    ap.add_argument("--stop-key", default="F12")
    ap.add_argument("--no-humanize", action="store_true", help="关掉拟人化抖动")
    ap.add_argument("--dry-run", action="store_true", help="不发键，只判断")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.check:
        return check()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
