#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ocr.py —— 屏幕文字识别（读游戏里的数字）
=========================================

为什么要 OCR
------------
燕云十六声的**世界模式 HUD 上不显示世界坐标**（只有 ID / 时间 / 延迟 / FPS，
已实测确认）。坐标只在**地图界面**里以数字形式出现。
所以「自动采集锚点」这条路必须能读屏上的数字 —— 模板匹配做不到，得上 OCR。

为什么选 RapidOCR
-----------------
* 离线、纯 ONNX 推理，不联网、不上传截图；
* 中文识别开箱即用（PP-OCRv4 检测+识别模型）；
* 同机运行的「律匠」用的就是它（其 `_internal` 目录里是
  `rapidocr_onnxruntime` + `onnxruntime` + `ch_PP-OCRv4_det/rec_infer.onnx`），
  说明**在这个游戏的实际画面上验证过可用** —— 这是选型时最有力的旁证。

这里只借鉴「用 OCR 读屏」这个思路和它对引擎的选择；律匠本体是
PolyForm Noncommercial 许可，**没有也不应该抄它的代码**。

坐标系注意
----------
RapidOCR 返回的 box 是 `[[x0,y0],[x1,y1],[x2,y2],[x3,y3]]`（左上、右上、右下、左下），
坐标是**相对于传入图像**的。传入裁剪区域时，记得加上裁剪偏移才能得到屏幕坐标。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np

# 延迟加载：RapidOCR 首次构造要加载 ONNX 模型（约 1~2 秒），
# 不 import 就用不上的人不该付这个代价。
_engine = None
_engine_failed = ""


def available() -> bool:
    """RapidOCR 是否可用（不触发模型加载）。"""
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except ImportError:
        return False


def _get_engine():
    global _engine, _engine_failed
    if _engine is not None:
        return _engine
    if _engine_failed:
        raise RuntimeError(_engine_failed)
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as e:
        _engine_failed = f"RapidOCR 未安装（{e}）。装法：python -m pip install rapidocr-onnxruntime"
        raise RuntimeError(_engine_failed) from e
    _engine = RapidOCR()
    return _engine


class Text:
    """一段识别出来的文字。"""

    __slots__ = ("text", "score", "box", "cx", "cy")

    def __init__(self, text: str, score: float, box):
        self.text = text
        self.score = float(score)
        self.box = box
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        self.cx = sum(xs) / len(xs)
        self.cy = sum(ys) / len(ys)

    @property
    def left(self) -> float:
        return min(float(p[0]) for p in self.box)

    @property
    def top(self) -> float:
        return min(float(p[1]) for p in self.box)

    @property
    def w(self) -> float:
        xs = [float(p[0]) for p in self.box]
        return max(xs) - min(xs)

    @property
    def h(self) -> float:
        ys = [float(p[1]) for p in self.box]
        return max(ys) - min(ys)

    def __repr__(self) -> str:
        return f"Text({self.text!r} @({self.cx:.0f},{self.cy:.0f}) {self.score:.2f})"


def read(img: np.ndarray, *, offset: tuple[int, int] = (0, 0),
         min_score: float = 0.5) -> list[Text]:
    """识别一张图里的所有文字。

    img     BGR 或灰度 ndarray（`vision.grab()` 的输出直接能用）
    offset  若 img 是屏幕的裁剪区域，传它的左上角屏幕坐标；
            返回的 Text 的 cx/cy/left/top 会**加上这个偏移**，
            这样调用方拿到的就是屏幕坐标，不用自己拼。
    """
    engine = _get_engine()
    # RapidOCR 接受 BGR/RGB ndarray；灰度图补成三通道更稳
    arr = img
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)

    result, _ = engine(arr)
    out: list[Text] = []
    if not result:
        return out
    ox, oy = offset
    for row in result:
        # RapidOCR 的返回是 [box, text, score]
        box, text, score = row[0], row[1], row[2]
        if float(score) < min_score or not str(text).strip():
            continue
        shifted = [[float(p[0]) + ox, float(p[1]) + oy] for p in box]
        out.append(Text(str(text).strip(), float(score), shifted))
    return out


# --------------------------------------------------------------------------
# 从识别结果里找坐标
# --------------------------------------------------------------------------

# 游戏里坐标可能写成 (-3418.35, -2379.74) / -3418.35,-2379.74 / X:-3418 Y:-2379 …
_NUM = r"[-−—]?\d{1,6}(?:\.\d+)?"
_PAIR = re.compile(rf"\(?\s*({_NUM})\s*[,，]\s*({_NUM})\s*\)?")
_LABELLED = re.compile(rf"[XxXxＸｘ]\s*[:：]\s*({_NUM}).*?[YyYyＹｙ]\s*[:：]\s*({_NUM})",
                       re.S)


def _to_float(s: str) -> float:
    """把识别出的数字串转 float。OCR 常把负号认成全角减号或破折号。"""
    return float(s.replace("−", "-").replace("—", "-").replace("－", "-").replace("–", "-"))


def find_coords(texts: list[Text]) -> list[tuple[float, float, Text]]:
    """从识别结果里挑出形如「(x, y)」的坐标。返回 [(x, y, 该 Text)]。

    优先认带 X/Y 标签的写法，其次认括号里的数字对 —— 后者误报率高
    （「剩余时间：23天20时」这类文本里也有数字），所以调用方最好再按
    坐标量级（游戏世界坐标大约 ±5000 以内）和屏幕位置过滤一遍。
    """
    found: list[tuple[float, float, Text]] = []
    seen: set[int] = set()

    for t in texts:
        m = _LABELLED.search(t.text)
        if m:
            found.append((_to_float(m.group(1)), _to_float(m.group(2)), t))
            seen.add(id(t))

    for t in texts:
        if id(t) in seen:
            continue
        m = _PAIR.search(t.text)
        if m:
            found.append((_to_float(m.group(1)), _to_float(m.group(2)), t))
    return found


def plausible_world_coords(x: float, y: float, *, limit: float = 6000.0) -> bool:
    """粗筛：燕云十六声的世界坐标在 ±5000 量级（实测数据范围
    x[-4066, 472] y[-2597, 1515]）。明显超出这个范围的多半是认错了。"""
    return abs(x) <= limit and abs(y) <= limit


def self_test() -> int:
    """自检：造一张写了坐标的图，看能不能读回来。不依赖游戏。"""
    print("== OCR 自检 ==")
    if not available():
        print("  [跳过] rapidocr_onnxruntime 未安装")
        print("         装法：python -m pip install rapidocr-onnxruntime")
        return 0

    from PIL import Image, ImageDraw

    W, H = 640, 200
    img = Image.new("RGB", (W, H), (18, 20, 26))
    d = ImageDraw.Draw(img)
    d.text((30, 40), "坐标 (-3418.35, -2379.74)", fill=(235, 235, 235))
    d.text((30, 110), "太平武墓  剩余时间 23天20时", fill=(235, 235, 235))
    arr = np.array(img)[:, :, ::-1].copy()      # RGB -> BGR

    ts = read(arr)
    print(f"  识别到 {len(ts)} 段文字：")
    for t in ts:
        print(f"    {t!r}")

    coords = find_coords(ts)
    print(f"  解析出坐标 {len(coords)} 组：")
    ok = False
    for x, y, t in coords:
        good = plausible_world_coords(x, y)
        print(f"    ({x}, {y})   量级合理={good}   来源={t.text!r}")
        if abs(x + 3418.35) < 1 and abs(y + 2379.74) < 1:
            ok = True
    print(f"\n结论：{'坐标识别正确 ✓' if ok else '没能正确读回坐标'}")
    return 0 if ok else 1


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--image", default="", help="识别一张图片文件并打印全部文字")
    ap.add_argument("--capture", action="store_true", help="抓当前屏幕并识别")
    ap.add_argument("--save", default="", help="把抓到的图存到这个路径")
    args = ap.parse_args()

    if args.self_test or (not args.image and not args.capture):
        return self_test()

    if args.image:
        arr = np.array(__import__("PIL.Image", fromlist=["Image"]).open(args.image))
        arr = arr[:, :, ::-1].copy()
        off = (0, 0)
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import vision as vis
        arr = vis.grab()
        off = (0, 0)
        if args.save:
            from PIL import Image
            Image.fromarray(arr).save(args.save)
            print(f"已存 {args.save}")

    ts = read(arr, offset=off)
    print(f"识别到 {len(ts)} 段文字：")
    for t in ts:
        print(f"  {t!r}")
    coords = find_coords(ts)
    if coords:
        print(f"\n候选坐标 {len(coords)} 组：")
        for x, y, t in coords:
            print(f"  ({x}, {y})  量级合理={plausible_world_coords(x, y)}  来源={t.text!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
