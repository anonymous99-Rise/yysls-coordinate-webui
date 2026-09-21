#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mapnav.py —— 地图标定与投影（把世界坐标换算成地图上的像素）
==============================================================

为什么需要这个
--------------
游戏里坐骑有个「识途」技能，用法是：**打开地图 → 选一个目的地 → 弹框里点【识途】→ 自动寻路**。
也就是说移动这一环**不需要任何第三方工具、不读内存、不注入进程**，只是两次 GUI 点击。

那么问题就变成一件事：**6071 个世界坐标，怎么变成地图上该点哪里？**

页面需要两组数：世界坐标（我们有）和地图屏幕像素（要标定）。两次点击之间的桥梁就是一个
二维变换。本模块负责解它、验证它、并把它画出来给人看。

    world (x, y)  --[变换]-->  地图屏幕像素 (px, py)

为什么标定是可信的：**我们手里有 117 个界碑 + 23 个传送点的精确世界坐标**，
它们在地图上必定有图标。用其中 2~3 个解变换，用剩下 114 个反过来验证 ——
残差小说明我们的坐标系和游戏地图一致；残差大说明坐标系定义不同，那也得先知道。

用法
----
    # 1) 标定：给几个「世界坐标 ↔ 地图像素」对应点
    python rpa/mapnav.py --fit pairs.json

    # 2) 验证：把全部 117 个界碑投影到地图截图上，画成一张 PNG 自己看
    python rpa/mapnav.py --overlay map.png --out check.png

    # 3) 换算单个坐标
    python rpa/mapnav.py --project -3822.8,-695.13

    # 4) 换算一整份任务清单，输出「点哪里」的 CSV
    python rpa/mapnav.py --tasklist rpa/tasks/farm-龙骨.csv --out nav-龙骨.csv

pairs.json 的样子（世界坐标 → 地图上该点的像素坐标，屏幕左上角为原点）::

    {
      "map": "苏州地图截图.png",
      "map_size": [1600, 900],
      "pairs": [
        {"world": [-3196.73, -894.18], "screen": [742, 318], "label": "不羡仙界碑"},
        {"world": [-2645.00, -170.17], "screen": [930, 505], "label": "临江驿"},
        {"world": [-3417.57, -2069.42], "screen": [560, 690], "label": "伏马庄界碑"}
      ]
    }

像素坐标怎么量：把地图截图丢进画图 / Snipaste，鼠标指到图标上，读左下角/右下角的坐标即可。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
DATA = PROJECT / "data"
DEFAULT_CALIB = DATA / "map_calibration.json"


# ---------------------------------------------------------------------------
# 变换模型
# ---------------------------------------------------------------------------
@dataclass
class Transform:
    """地图变换。

    similarity:  px = a*wx - b*wy + tx ;  py = b*wx + a*wy + ty
                 （a = 缩放×cosθ，b = 缩放×sinθ，4 个参数）
    affine:      px = a11*wx + a12*wy + tx ;  py = a21*wx + a22*wy + ty
                 （6 个参数，能表达翻转/错切）

    默认用 similarity：参数少、点少时更稳。但**如果地图 Y 轴方向和世界 Y 轴相反**
    （翻转），similarity 表达不了，残差会明显偏大 —— 所以两种都算一遍给你对比。
    """
    model: str
    params: list[float]
    rms: float
    n: int
    max_err: float = 0.0

    def project(self, wx: float, wy: float) -> tuple[float, float]:
        p = self.params
        if self.model == "similarity":
            a, b, tx, ty = p
            return a * wx - b * wy + tx, b * wx + a * wy + ty
        a11, a12, tx, a21, a22, ty = p
        return a11 * wx + a12 * wy + tx, a21 * wx + a22 * wy + ty

    @property
    def scale(self) -> float:
        p = self.params
        if self.model == "similarity":
            return math.hypot(p[0], p[1])
        return math.sqrt(abs(p[0] * p[4] - p[1] * p[3]))

    @property
    def rotation_deg(self) -> float:
        p = self.params
        if self.model == "similarity":
            return math.degrees(math.atan2(p[1], p[0]))
        return math.degrees(math.atan2(p[3], p[0]))

    def to_dict(self) -> dict:
        return {
            "model": self.model, "params": [float(v) for v in self.params],
            "rms_px": float(self.rms), "max_err_px": float(self.max_err), "n_points": self.n,
            "scale": float(self.scale), "rotation_deg": float(self.rotation_deg),
        }

    @classmethod
    def from_dict(cls, d: dict) -> Transform:
        return cls(d["model"], [float(v) for v in d["params"]],
                   float(d["rms_px"]), int(d.get("n_points", 0)), float(d.get("max_err_px", 0.0)))


def fit_affine(world: np.ndarray, screen: np.ndarray) -> Transform:
    """最小二乘仿射拟合（6 参数）。至少 3 个点。"""
    n = len(world)
    if n < 3:
        raise ValueError(f"仿射变换至少需要 3 个对应点，只给了 {n} 个")
    A = np.column_stack([world[:, 0], world[:, 1], np.ones(n)])
    cx, *_ = np.linalg.lstsq(A, screen[:, 0], rcond=None)
    cy, *_ = np.linalg.lstsq(A, screen[:, 1], rcond=None)
    params = [cx[0], cx[1], cx[2], cy[0], cy[1], cy[2]]
    return Transform("affine", params, *_err(params, world, screen, "affine"), n)


def fit_similarity(world: np.ndarray, screen: np.ndarray) -> Transform:
    """最小二乘相似变换（旋转+等比缩放+平移，4 参数）。至少 2 个点。"""
    n = len(world)
    if n < 2:
        raise ValueError(f"相似变换至少需要 2 个对应点，只给了 {n} 个")
    # px = a*wx - b*wy + tx ;  py = b*wx + a*wy + ty
    A = np.zeros((2 * n, 4))
    A[0::2, 0] = world[:, 0]
    A[0::2, 1] = -world[:, 1]
    A[0::2, 2] = 1.0
    A[1::2, 0] = world[:, 1]
    A[1::2, 1] = world[:, 0]
    A[1::2, 3] = 1.0
    bvec = np.empty(2 * n)
    bvec[0::2] = screen[:, 0]
    bvec[1::2] = screen[:, 1]
    sol, *_ = np.linalg.lstsq(A, bvec, rcond=None)
    params = list(sol)
    return Transform("similarity", params, *_err(params, world, screen, "similarity"), n)


def _err(params: list[float], world: np.ndarray, screen: np.ndarray, model: str) -> tuple[float, float]:
    t = Transform(model, params, 0.0, len(world))
    pred = np.array([t.project(float(w[0]), float(w[1])) for w in world])
    d = np.linalg.norm(pred - screen, axis=1)
    return float(np.sqrt((d ** 2).mean())), float(d.max())


def fit_best(world: np.ndarray, screen: np.ndarray) -> tuple[Transform, dict[str, Transform]]:
    """两种模型都拟一遍，返回（推荐用的那个, 全部结果）。

    选谁不是看谁残差小（仿射参数多，残差必然更小），而是看**仿射比相似好多少**：
      * 好得有限（< 1.5 px）→ 用 similarity，参数少更不容易过拟合
      * 好很多 → 说明地图存在翻转或错切，只能用 affine，并且要提醒一声
    """
    out: dict[str, Transform] = {}
    if len(world) >= 2:
        out["similarity"] = fit_similarity(world, screen)
    if len(world) >= 3:
        out["affine"] = fit_affine(world, screen)

    if not out:
        raise ValueError("至少需要 2 个对应点")
    if "affine" not in out:
        return out["similarity"], out
    sim, aff = out["similarity"], out["affine"]
    if aff.rms < sim.rms - 1.5:
        return aff, out
    return sim, out


# ---------------------------------------------------------------------------
# 锚点数据：界碑 / 传送点
# ---------------------------------------------------------------------------
def load_anchors(region: str = "") -> list[dict]:
    """从规范化数据里取界碑与传送点，作为标定锚点。"""
    src = DATA / "points.json"
    if not src.exists():
        raise SystemExit(f"[x] 找不到 {src}；先跑 python tools/etl.py")
    doc = json.loads(src.read_text(encoding="utf-8"))
    out = []
    for p in doc["points"]:
        is_anchor = (
            any("界碑" in s["file"] for s in p["sources"])
            or "界碑" in p["name"]
            or "传送点" in p["name"]
        )
        if not is_anchor:
            continue
        if region and p["region"] != region:
            continue
        out.append({"id": p["id"], "name": p["name"], "x": p["x"], "y": p["y"], "z": p["z"],
                    "region": p["region"]})
    return out


def load_pairs(path: str | Path) -> tuple[np.ndarray, np.ndarray, dict]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    pairs = doc.get("pairs", [])
    if len(pairs) < 2:
        raise SystemExit("[x] pairs 里至少要有 2 组对应点")
    world = np.array([p["world"][:2] for p in pairs], dtype=float)
    screen = np.array([p["screen"][:2] for p in pairs], dtype=float)
    return world, screen, doc


# ---------------------------------------------------------------------------
# 可视化核对：把锚点投影到地图截图上
# ---------------------------------------------------------------------------
def draw_overlay(map_png: str | Path, t: Transform, anchors: list[dict],
                 out_png: str | Path, *, radius: int = 5,
                 labels: bool = True) -> tuple[int, int, list[dict]]:
    """把锚点世界坐标投影到地图截图上画出来，供人眼核对。

    这是整个标定流程里最重要的一步：残差是数字，图是直觉。
    投影点要是整齐地落在界碑图标上，标定就是对的。
    """
    from PIL import Image, ImageDraw
    img = Image.open(map_png).convert("RGB")
    draw = ImageDraw.Draw(img)
    W, H = img.size
    inside = 0
    outside: list[dict] = []
    for a in anchors:
        px, py = t.project(a["x"], a["y"])
        if -radius <= px <= W + radius and -radius <= py <= H + radius:
            inside += 1
            color = (255, 60, 60)
        else:
            outside.append({**a, "px": px, "py": py})
            color = (60, 120, 255)   # 画在画面外的用蓝色标出来，别让它悄悄消失
        cx, cy = int(round(px)), int(round(py))
        draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                     outline=color, width=2)
        draw.line([cx - radius - 3, cy, cx + radius + 3, cy], fill=color, width=1)
        draw.line([cx, cy - radius - 3, cx, cy + radius + 3], fill=color, width=1)
        if labels:
            draw.text((cx + radius + 2, cy - 6), a.get("name", "")[:12], fill=(255, 235, 120))
    img.save(out_png)
    return inside, len(outside), outside


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="地图标定与投影")
    ap.add_argument("--fit", default="", help="从对应点文件拟合变换")
    ap.add_argument("--pairs", default="", help="对应点文件（默认取 --fit 的值）")
    ap.add_argument("--overlay", default="", help="把锚点投影画到这张地图截图上")
    ap.add_argument("--out", default="", help="--overlay 的输出图片 / --tasklist 的输出 CSV")
    ap.add_argument("--project", default="", help="换算单个世界坐标，如 -3822.8,-695.13")
    ap.add_argument("--tasklist", default="", help="批量换算任务清单 CSV")
    ap.add_argument("--calibration", default=str(DEFAULT_CALIB), help="标定文件路径")
    ap.add_argument("--region", default="", help="只用这个区域的锚点")
    ap.add_argument("--no-labels", action="store_true")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而不是人话")
    args = ap.parse_args()

    # ---- 拟合 ----------------------------------------------------------
    if args.fit:
        pairs_file = args.pairs or args.fit
        world, screen, doc = load_pairs(pairs_file)
        best, allm = fit_best(world, screen)
        print(f"用到 {len(world)} 组对应点：")
        for p in doc["pairs"]:
            print(f"  世界 ({p['world'][0]:>9.2f},{p['world'][1]:>9.2f})"
                  f"  →  像素 ({p['screen'][0]:>6.0f},{p['screen'][1]:>6.0f})"
                  f"   {p.get('label', '')}")
        print()
        for name, t in allm.items():
            print(f"  [{name:10}] RMS={t.rms:7.2f} px  最大误差={t.max_err:7.2f} px"
                  f"  缩放={t.scale:.5f}  旋转={t.rotation_deg:+.2f}°")
        print(f"\n  采用：{best.model}（理由见代码注释：残差改善不足 1.5px 时优先用参数更少的 similarity）")

        # 用一个没参与拟合的锚点做留出验证（如果锚点够多）
        anchors = load_anchors(args.region)
        if len(anchors) >= 10:
            used = {(round(p["world"][0], 2), round(p["world"][1], 2)) for p in doc["pairs"]}
            holdout = [a for a in anchors
                       if (round(a["x"], 2), round(a["y"], 2)) not in used]
            if holdout:
                print(f"  [留出检查] 另外 {len(holdout)} 个锚点没参与拟合，看它们预测到哪（前 8 个）：")
                for a in holdout[:8]:
                    px, py = best.project(a["x"], a["y"])
                    print(f"    {a['name'][:16]:<16} → ({px:7.1f},{py:7.1f})")
                print("    这些数值本身说明不了什么 —— 用 --overlay 画到地图截图上，"
                      "红圈套准界碑图标才算对。")

        calib = {
            "transform": best.to_dict(),
            "alternatives": {k: v.to_dict() for k, v in allm.items()},
            "pairsFile": str(pairs_file),
            "map": doc.get("map", ""),
            "anchors": len(load_anchors(args.region)),
        }
        out = Path(args.calibration)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(calib, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n标定已保存：{out}")
        if best.rms > 8:
            print(f"[!] RMS 残差 {best.rms:.1f} px 偏大。可能原因：对应点量错了、"
                  f"地图有翻转/错切（看 affine 那行是不是好很多）、或者地图不是等比例投影。")
        return 0

    # ---- 需要已保存的标定 ----------------------------------------------
    def need_calib() -> Transform:
        p = Path(args.calibration)
        if not p.exists():
            raise SystemExit(f"[x] 没有标定文件 {p}；先跑 --fit")
        return Transform.from_dict(json.loads(p.read_text(encoding="utf-8"))["transform"])

    if args.overlay:
        t = need_calib()
        anchors = load_anchors(args.region)
        out_png = args.out or str(PROJECT / "data" / "map_overlay.png")
        inside, outside, outl = draw_overlay(args.overlay, t, anchors, out_png,
                                            labels=not args.no_labels)
        print(f"标定模型 {t.model}  RMS={t.rms:.1f}px  缩放={t.scale:.5f}  旋转={t.rotation_deg:+.2f}°")
        print(f"锚点 {len(anchors)} 个：{inside} 个落在地图内，{outside} 个落在画面外")
        for a in outl[:8]:
            print(f"   [画面外] {a['name'][:16]:<16} → ({a['px']:.0f},{a['py']:.0f})")
        print(f"已输出：{out_png}")
        print("打开这张图核对：红圈应该正好套在界碑图标上。")
        return 0

    if args.project:
        t = need_calib()
        vals = [float(v) for v in args.project.replace("，", ",").split(",")[:2]]
        px, py = t.project(vals[0], vals[1])
        if args.json:
            print(json.dumps({"world": vals, "screen": [round(px, 1), round(py, 1)],
                              "model": t.model, "rms_px": t.rms}, ensure_ascii=False))
        else:
            print(f"世界 ({vals[0]}, {vals[1]})  →  地图像素 ({px:.1f}, {py:.1f})")
            print(f"（模型 {t.model}，RMS {t.rms:.2f}px —— 也就是说这个落点大概有 ±{t.rms:.0f} 像素的不确定度）")
        return 0

    if args.tasklist:
        t = need_calib()
        src = Path(args.tasklist)
        rows = list(csv.DictReader(src.open(encoding="utf-8-sig")))
        if not rows:
            raise SystemExit("[x] 任务清单是空的")
        out = Path(args.out) if args.out else src.with_name(src.stem + "_nav.csv")
        with out.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["pointId", "name", "worldX", "worldY", "screenX", "screenY", "note"])
            oob = 0
            for r in rows:
                try:
                    wx, wy = float(r["x"]), float(r["y"])
                except (KeyError, ValueError):
                    continue
                px, py = t.project(wx, wy)
                note = ""
                if not (0 <= px <= 10000 and 0 <= py <= 10000):
                    note = "可能在地图可视范围外"
                    oob += 1
                w.writerow([r.get("pointId", ""), r.get("name", ""),
                            f"{wx:.2f}", f"{wy:.2f}", f"{px:.1f}", f"{py:.1f}", note])
        print(f"换算 {len(rows)} 条 → {out}")
        if oob:
            print(f"  其中 {oob} 条可能落在地图可视范围外（需要先在地图上把该区域挪到视野里）")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
