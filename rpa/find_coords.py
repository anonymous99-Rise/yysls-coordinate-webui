#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
找坐标：把地图界面放大后再 OCR。
=================================

小号数字直接 OCR 命中率低。常见有效手段是先做 2~3 倍上采样
（OCR 模型对 ~30px 高的字最稳），再分区扫描，避免整屏里的小字被淹没。

用法：
    python rpa/find_coords.py            # 打开地图 → 抓图 → 多尺度 OCR
    python rpa/find_coords.py --keep     # 不自动关地图
"""
import argparse
import ctypes
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")
import injector as inj      # noqa: E402
import ocr                  # noqa: E402
import vision as vis        # noqa: E402

SHOTS = Path(__file__).resolve().parent / "shots"
SHOTS.mkdir(exist_ok=True)


def upscale(img: np.ndarray, k: int) -> np.ndarray:
    """最近邻放大 k 倍。用 numpy 重复而不是 cv2.resize —— 少一个依赖，
    而且文字放大用最近邻足够（OCR 要的是笔画像素数，不是平滑）。"""
    if k <= 1:
        return img
    if img.ndim == 2:
        return np.repeat(np.repeat(img, k, axis=0), k, axis=1)
    return np.repeat(np.repeat(img, k, axis=0), k, axis=1)


def sweep(img: np.ndarray, tag: str, base_off=(0, 0)) -> list:
    """多尺度 + 分区扫一遍，返回所有像坐标的候选。"""
    hits = []
    H, W = img.shape[:2]

    for k in (1, 2, 3):
        big = upscale(img, k)
        ts = ocr.read(big, offset=(0, 0), min_score=0.4)
        # 放大 k 倍后坐标要除回 k，再加区域偏移
        for t in ts:
            t.box = [[p[0] / k + base_off[0], p[1] / k + base_off[1]] for p in t.box]
            t.cx = t.cx / k + base_off[0]
            t.cy = t.cy / k + base_off[1]
        for x, y, t in ocr.find_coords(ts):
            if ocr.plausible_world_coords(x, y):
                hits.append((x, y, t, f"{tag}/x{k}"))

    # 分区：四角 + 底部条 + 中央，各自再放大 3 倍
    REGIONS = {
        "左上": (0, 0, W // 3, H // 4),
        "右上": (W * 2 // 3, 0, W // 3, H // 4),
        "左下": (0, H * 3 // 4, W // 3, H // 4),
        "右下": (W * 2 // 3, H * 3 // 4, W // 3, H // 4),
        "底部条": (0, int(H * 0.88), W, H - int(H * 0.88)),
        "顶部条": (0, 0, W, int(H * 0.08)),
        "中央": (W // 4, H // 3, W // 2, H // 3),
    }
    for name, (x0, y0, w, h) in REGIONS.items():
        if w <= 0 or h <= 0:
            continue
        crop = img[y0:y0 + h, x0:x0 + w]
        for k in (3, 4):
            big = upscale(crop, k)
            ts = ocr.read(big, offset=(0, 0), min_score=0.35)
            for t in ts:
                t.box = [[p[0] / k + x0, p[1] / k + y0] for p in t.box]
                t.cx = t.cx / k + x0
                t.cy = t.cy / k + y0
            for x, y, t in ocr.find_coords(ts):
                if ocr.plausible_world_coords(x, y):
                    hits.append((x, y, t, f"{tag}/{name}/x{k}"))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="不自动关地图")
    ap.add_argument("--image", default="", help="不抓图，直接分析这个文件")
    args = ap.parse_args()

    if args.image:
        from PIL import Image
        img = np.array(Image.open(args.image))[:, :, ::-1].copy()
        print(f"分析文件 {args.image}  {img.shape}")
    else:
        hwnd = inj.find_game_window()
        if not hwnd:
            raise SystemExit("[!] 没找到游戏窗口")
        if not inj.ensure_foreground(hwnd):
            raise SystemExit("[!] 无法把游戏切到前台")
        rc = ctypes.wintypes.RECT()
        ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rc))
        pt = ctypes.wintypes.POINT(0, 0)
        ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(pt))
        X, Y, W, H = pt.x, pt.y, rc.right, rc.bottom

        world = vis.grab(X, Y, W, H)
        from PIL import Image
        Image.fromarray(world).save(SHOTS / "50_world_before.png")

        print("按 m 打开地图…")
        inj.press_key("m")
        time.sleep(2.2)
        img = vis.grab(X, Y, W, H)
        Image.fromarray(img).save(SHOTS / "51_map.png")
        print(f"已抓图 {img.shape} -> {SHOTS / '51_map.png'}")

        if not args.keep:
            inj.press_key("m")
            time.sleep(1.5)

    print("\n== 全屏原尺寸 OCR ==")
    ts = ocr.read(img)
    for t in ts:
        print(f"  {t!r}")

    print("\n== 多尺度 + 分区扫描（只报像坐标的）==")
    hits = sweep(img, "full")
    if not hits:
        print("  没有找到量级合理的 (x, y) 组合")
    seen = set()
    for x, y, t, where in hits:
        key = (round(x, 1), round(y, 1))
        if key in seen:
            continue
        seen.add(key)
        print(f"  ({x}, {y})   来源 [{where}]   原文 {t.text!r}  @({t.cx:.0f},{t.cy:.0f})")

    print(f"\n结论：找到 {len(seen)} 组候选坐标")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
