#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓一张游戏窗口的图，存到 rpa/shots/，用于人工确认抓屏链路与窗口定位是否正确。

只读操作：不发送任何输入，不动游戏状态。
"""
import ctypes
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")

import injector as inj          # noqa: E402
import vision as vis            # noqa: E402

OUT = Path(__file__).resolve().parent / "shots"
OUT.mkdir(exist_ok=True)

user32 = ctypes.windll.user32

print("== 可见顶层窗口（含尺寸） ==")
wins = inj._enum_visible_windows()
for hwnd, pid, title in wins:
    r = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    print(f"  hwnd={hwnd:<12} {r.right - r.left:>5}x{r.bottom - r.top:<5} "
          f"pid={pid:<7} {title[:60]}")

game = inj.find_window(title="燕云十六声")
print(f"\nfind_window(燕云十六声) -> {game}")

if not game:
    print("[!] 没找到游戏窗口")
    raise SystemExit(1)

# 客户区尺寸（不含标题栏/边框）—— 抓图与坐标换算都要用它
rc = ctypes.wintypes.RECT()
ctypes.windll.user32.GetClientRect(game, ctypes.byref(rc))
print(f"客户区 GetClientRect: {rc.right}x{rc.bottom}")

pt = ctypes.wintypes.POINT(0, 0)
ctypes.windll.user32.ClientToScreen(game, ctypes.byref(pt))
print(f"客户区在屏幕上的左上角: ({pt.x}, {pt.y})")

fg = user32.GetForegroundWindow()
print(f"当前前台窗口: {fg}   是游戏吗: {fg == game}")

print("\n== 抓图 ==")
try:
    t0 = time.perf_counter()
    img_screen = vis.grab(pt.x, pt.y, rc.right, rc.bottom)
    dt = (time.perf_counter() - t0) * 1000
    print(f"  按客户区屏幕矩形抓: shape={img_screen.shape}  {dt:.1f} ms")
    try:
        from PIL import Image
        Image.fromarray(img_screen).save(OUT / "00_game_client.png")
        print(f"  已存 {OUT / '00_game_client.png'}")
    except ImportError:
        import numpy as np
        np.save(OUT / "00_game_client.npy", img_screen)
        print("  Pillow 未装，已存 npy")

    t0 = time.perf_counter()
    img_win = vis.grab_window(game)
    dt = (time.perf_counter() - t0) * 1000
    print(f"  grab_window(hwnd): shape={img_win.shape}  {dt:.1f} ms")
    try:
        from PIL import Image
        Image.fromarray(img_win).save(OUT / "01_game_hwnd.png")
        print(f"  已存 {OUT / '01_game_hwnd.png'}")
    except ImportError:
        pass

    full = vis.grab()
    try:
        from PIL import Image
        Image.fromarray(full).save(OUT / "02_fullscreen.png")
        print(f"  全屏已存 {OUT / '02_fullscreen.png'}  shape={full.shape}")
    except ImportError:
        pass
except Exception as e:
    print(f"  [!] 抓图失败: {type(e).__name__}: {e}")

print("\n== 运动检测（1.5 秒，看画面是否有变化）==")
try:
    md = vis.MotionDetector(region=(pt.x, pt.y, rc.right, rc.bottom))
    md.prime()
    time.sleep(1.5)
    print(f"  帧间差异 = {md.diff():.4f}")
    print(f"  wait_until_still(0.5s, 1.5s 超时) -> {md.wait_until_still(0.5, 1.5)}")
except Exception as e:
    print(f"  [!] {type(e).__name__}: {e}")
