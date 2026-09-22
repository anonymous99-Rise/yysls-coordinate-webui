#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
世界模式下的输入测试 + 自动读坐标
=================================

上一次输入测试是在**活动面板**里做的，只证明了普通窗口消息链路可用。
DirectX 游戏真正的考验是**世界模式**下的 DirectInput（角色/镜头输入），
那条路只认扫描码，虚拟键码会被静默丢弃。现在游戏已在世界模式，可以真测了。

顺便验证一条对明天很有用的链路：
    脚本发键打开地图 → 抓图 → 视觉读出坐标
如果这条能通，「补锚点」就不用人工抄数字了 —— 脚本每张图跑一遍就有锚点。

只做可逆操作：开地图 / 关地图。
"""
import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
import injector as inj          # noqa: E402
import vision as vis            # noqa: E402

user32 = ctypes.windll.user32
SHOTS = Path(__file__).resolve().parent / "shots"
SHOTS.mkdir(exist_ok=True)

hwnd = inj.find_game_window()
if not hwnd:
    raise SystemExit("[!] 没找到游戏窗口")
if not inj.ensure_foreground(hwnd):
    raise SystemExit("[!] 无法把游戏调到前台")
rc = ctypes.wintypes.RECT()
user32.GetClientRect(hwnd, ctypes.byref(rc))
pt = ctypes.wintypes.POINT(0, 0)
user32.ClientToScreen(hwnd, ctypes.byref(pt))
X, Y, W, H = pt.x, pt.y, rc.right, rc.bottom
REGION = (X, Y, W, H)

print(f"游戏在前台 ✓  客户区 {W}x{H} @ ({X},{Y})")


def shot(tag: str):
    img = vis.grab(X, Y, W, H)
    try:
        from PIL import Image
        Image.fromarray(img).save(SHOTS / f"{tag}.png")
    except ImportError:
        pass
    return img


import numpy as np  # noqa: E402

world = shot("30_world")
g = vis.to_gray(world)
print(f"世界模式基线：亮度 {g.mean():.1f}±{g.std():.1f}")

print("\n== 世界模式下按 M（开地图）==")
t0 = time.perf_counter()
inj.press_key("m")
time.sleep(2.0)                      # 地图有展开动画
after = shot("31_map_open")
dt = (time.perf_counter() - t0) * 1000
d = vis.frame_diff(g, vis.to_gray(after))
ga = vis.to_gray(after)
print(f"  耗时 {dt:.0f} ms   帧差 {d:.2f}   新帧亮度 {ga.mean():.1f}±{ga.std():.1f}")
if d > 2.0:
    print("  → 世界模式下输入**有效**（DirectInput 扫描码链路可用）")
else:
    print("  → 无变化：该键在世界模式可能不是地图键，或需要别的发键方式")

print("\n== 关地图（Esc）==")
inj.press_key("esc")
time.sleep(1.6)
back = shot("32_map_closed")
gb = vis.to_gray(back)
print(f"  与基线帧差 {vis.frame_diff(g, gb):.2f}   亮度 {gb.mean():.1f}±{gb.std():.1f}")
if vis.frame_diff(g, gb) < 12:
    print("  → 已回到世界模式 ✓")
else:
    print("  → 还没完全回到原样，可能需要再按一次 Esc")

print(f"\n截图：{SHOTS}")
print("  31_map_open.png 最关键 —— 如果地图上有坐标数字，说明可以自动读锚点")
