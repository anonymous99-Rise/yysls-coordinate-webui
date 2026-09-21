#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fit_official_transform.py —— 解算「官方大地图经纬度 → 游戏世界坐标」的换算
=========================================================================

结论先说（全部有交叉验证支撑，推导见 tools/README_coord.md）
------------------------------------------------------------
1. 官方 `longitude/latitude` **不是**全球统一地理坐标，而是**每张图各自画布**的坐标。
   证据：map0 与 map1 解出的缩放分别为 0.0076177 / 0.0050778，比值 **1.5002 ≈ 3/2**；
   把 map0 参数套到 map1 上 RMS 从 47 涨到 2485；全局单一仿射的相对误差 18.6%。
2. 变换类型是**镜像相似变换**（rotation + uniform scale + mirror + translation），
   只有 4 个参数。官方画布相对游戏世界是**镜像**的：仿射线性部分行列式为负。
   只允许旋转的相似变换拟不出来，相对误差高达 25%~42% —— 那是模型族选窄了，不是数据问题。
3. 4 参数的镜像相似变换**优于** 6 参数仿射：
        map0  LOO 2.35% (reflected)  vs  2.40% (affine)
        map1  LOO 2.14% (reflected)  vs  2.45% (affine)
   参数更少、能外推，所以默认用它。

模型
----
    v = −lat                             # 一次镜像
    x = a·lng − b·v + tx
    y = b·lng + a·v + ty
其中 (a, b) 给出缩放 √(a²+b²) 与旋转 atan2(b, a)。map0 的旋转是 −89.66°，
map1 是 −91.30°，都在 −90° 附近 —— 即「x 由 lat 决定、y 由 lng 决定」，符合直觉。

锚点从哪来
----------
官方「传送点」分类里的点是有名字的实体（佛爷寨、临江驿…），我方数据里也有同名界碑。
两边同名即同一实体，构成真正的**点级对应**。
（早先用「区域地名」做锚点失败 —— 官方区域是标注图钉，我方的 place 是同名点云重心，
  两者不是同一个物理位置；那是错误的锚点，不是错误的模型。）

每张图需要 ≥3 对同名点才能解出 4 参数；≥6 对才能做有意义的 LOO 交叉验证。

用法
----
    python tools/fit_official_transform.py                 # 解算并写 data/official_transform.json
    python tools/fit_official_transform.py --report-only   # 只看报告不落盘
    python tools/fit_official_transform.py --model affine  # 改用 6 参数仿射
    python tools/fit_official_transform.py --plot
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT / "tools") not in sys.path:
    sys.path.insert(0, str(PROJECT / "tools"))

OFFICIAL = PROJECT / "data" / "official_points.json"
OURS = PROJECT / "data" / "points.json"
OUT = PROJECT / "data" / "official_transform.json"

# 官方分类里能提供可信点级锚点的：只有「传送点」是有唯一名字的实体点。
# 其他分类（猫戏/曲径寻幽/天地万籁/不平事…）大量使用**通用占位名**
# （「猫戏」重复 23 次、「曲径寻幽（蓝蝴蝶）」重复多次），同名纯属巧合，
# 拿来当锚点会把拟合带偏 —— 实测这些分类的预测相对误差 31%~91%。
ANCHOR_CATEGORIES = ["传送点"]

SUFFIX = r"(传送点|传送|界碑|驿站码头|驿站|码头|渡口|车夫|船夫|土地庙|神龛|路标)$"

import re  # noqa: E402

_SUF = re.compile(SUFFIX)
_NOISE = re.compile(r"[\s·・\-—_（）()【】\[\]「」、,，。．.]+")

# 解得可信所需的最少锚点数
MIN_ANCHORS = 3
# 做 LOO 交叉验证建议的锚点数
GOOD_ANCHORS = 6
# LOO 相对误差低于此值才算「可用」
ACCEPT_REL = 3.0


def norm(name: str) -> str:
    """归一化名称：去装饰字符与类型后缀（「金明池驿站码头」→「金明池」）。"""
    s = _NOISE.sub("", (name or "").strip())
    for _ in range(3):
        t = _SUF.sub("", s)
        if t == s:
            break
        s = t
    return s


# ------------------------------------------------------------------ 模型

def fit_reflected(src, dst):
    """镜像相似变换（4 参数）。内部在 (lng, −lat) 上做普通相似变换闭式解。"""
    n = len(src)
    if n < 2:
        raise ValueError("至少需要 2 对点")
    f = [(x, -y) for x, y in src]
    mx = sum(p[0] for p in f) / n
    my = sum(p[1] for p in f) / n
    ux = sum(p[0] for p in dst) / n
    uy = sum(p[1] for p in dst) / n
    re = im = den = 0.0
    for (x, y), (u, v) in zip(f, dst):
        dx, dy = x - mx, y - my
        du, dv = u - ux, v - uy
        re += dx * du + dy * dv
        im += dx * dv - dy * du
        den += dx * dx + dy * dy
    if den <= 0:
        raise ValueError("源点全部重合，无法拟合")
    a, b = re / den, im / den
    tx = ux - (a * mx - b * my)
    ty = uy - (b * mx + a * my)
    return {"kind": "reflected", "params": [a, b, tx, ty], "n": n,
            "scale": math.hypot(a, b), "rotation": math.degrees(math.atan2(b, a))}


def fit_similarity_plain(src, dst):
    """纯旋转相似变换（4 参数，det>0）。保留作为对照 —— 实测拟合不了官方画布。"""
    n = len(src)
    if n < 2:
        raise ValueError("至少需要 2 对点")
    mx = sum(p[0] for p in src) / n
    my = sum(p[1] for p in src) / n
    ux = sum(p[0] for p in dst) / n
    uy = sum(p[1] for p in dst) / n
    re = im = den = 0.0
    for (x, y), (u, v) in zip(src, dst):
        dx, dy = x - mx, y - my
        du, dv = u - ux, v - uy
        re += dx * du + dy * dv
        im += dx * dv - dy * du
        den += dx * dx + dy * dy
    if den <= 0:
        raise ValueError("源点全部重合")
    a, b = re / den, im / den
    tx = ux - (a * mx - b * my)
    ty = uy - (b * mx + a * my)
    return {"kind": "similarity", "params": [a, b, tx, ty], "n": n,
            "scale": math.hypot(a, b), "rotation": math.degrees(math.atan2(b, a))}


def fit_affine(src, dst):
    """6 参数仿射。"""
    A, rhs = [], []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1.0, 0.0, 0.0, 0.0]); rhs.append(u)
        A.append([0.0, 0.0, 0.0, x, y, 1.0]); rhs.append(v)
    m = len(A)
    ATA = [[sum(A[k][i] * A[k][j] for k in range(m)) for j in range(6)] for i in range(6)]
    ATb = [sum(A[k][i] * rhs[k] for k in range(m)) for i in range(6)]
    return {"kind": "affine", "params": _solve6(ATA, ATb), "n": len(src)}


def _solve6(M, v):
    a = [row[:] + [v[i]] for i, row in enumerate(M)]
    for c in range(6):
        piv = max(range(c, 6), key=lambda r: abs(a[r][c]))
        if abs(a[piv][c]) < 1e-12:
            raise ValueError("正规方程奇异（点太少或共线）")
        a[c], a[piv] = a[piv], a[c]
        pv = a[c][c]
        a[c] = [x / pv for x in a[c]]
        for r in range(6):
            if r != c and a[r][c]:
                f = a[r][c]
                a[r] = [x - f * y for x, y in zip(a[r], a[c])]
    return [a[i][6] for i in range(6)]


FITTERS = {"reflected": fit_reflected, "similarity": fit_similarity_plain,
           "affine": fit_affine}
MODEL_NEED = {"reflected": 2, "similarity": 2, "affine": 6}


def apply_transform(model, lng, lat):
    """把官方 (lng, lat) 换算成游戏世界 (x, y)。供其它脚本与 WebUI 复用。"""
    k = model["kind"]
    p = model["params"]
    if k in ("reflected", "similarity"):
        a, b, tx, ty = p
        v = -lat if k == "reflected" else lat
        return (a * lng - b * v + tx, b * lng + a * v + ty)
    m11, m12, tx, m21, m22, ty = p
    return (m11 * lng + m12 * lat + tx, m21 * lng + m22 * lat + ty)


def residuals(model, src, dst):
    return [math.dist(apply_transform(model, s[0], s[1]), d) for s, d in zip(src, dst)]


def rms(errs):
    return math.sqrt(sum(e * e for e in errs) / len(errs)) if errs else float("inf")


def span(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return math.dist((min(xs), min(ys)), (max(xs), max(ys)))


def loo_rms(fitter, src, dst, need):
    """留一交叉验证：唯一能回答「参数是不是过拟合」的方法。"""
    if len(src) < need + 2:
        return float("nan"), []
    errs = []
    for i in range(len(src)):
        tr = [j for j in range(len(src)) if j != i]
        try:
            m = fitter([src[j] for j in tr], [dst[j] for j in tr])
        except ValueError:
            errs.append(float("inf"))
            continue
        errs.append(math.dist(apply_transform(m, src[i][0], src[i][1]), dst[i]))
    return rms(errs), errs


def ransac(fitter, need, src, dst, thresh, iters=3000, seed=20260101):
    """最小样本 RANSAC：找**最大一致集**，剔除错配点（同名不同物的情形）。

    注意两个曾把结果搞错的坑：
    1. `best` 必须初始化为空集。若初始化为「全部点」，更新条件
       `len(inl) > len(best)` 永远不成立，函数会退化成「只用局部精修不断收缩」，
       最终收敛到某个碰巧自洽的小子集 —— 再在这个子集上算 LOO，
       得到的是**选择偏差**而不是泛化误差（实测会把 38 对的 map0 缩到 5 对、
       报出虚低的 0.92%）。
    2. 局部精修只允许**增长**：若某轮把点数改少了，说明模型被拉偏，应停止。
    """
    rng = random.Random(seed)
    n = len(src)
    if n <= need:
        return list(range(n))

    best: list[int] = []
    for _ in range(iters):
        idx = rng.sample(range(n), need)
        if any(math.dist(src[i], src[j]) < 1e-9
               for a, i in enumerate(idx) for j in idx[a + 1:]):
            continue
        try:
            m = fitter([src[i] for i in idx], [dst[i] for i in idx])
        except ValueError:
            continue
        inl = [k for k in range(n)
               if math.dist(apply_transform(m, src[k][0], src[k][1]), dst[k]) <= thresh]
        if len(inl) > len(best):
            best = inl

    if len(best) < need:
        return list(range(n))       # 没找到一致集，说明不该剔除，退回全集

    # 只增长式精修
    for _ in range(20):
        m = fitter([src[i] for i in best], [dst[i] for i in best])
        errs = residuals(m, src, dst)
        new = [k for k in range(n) if errs[k] <= thresh]
        if len(new) <= len(best):
            break
        best = new
    return sorted(best)


# ------------------------------------------------------------------ 配对

def build_ours_index(our_points):
    idx = {}
    for p in our_points:
        k = norm(p.get("name") or "")
        if k:
            idx.setdefault(k, []).append(p)
    out = {}
    for k, ps in idx.items():
        seen = []
        for p in ps:      # 同一物理点常被多文件重复记录，按坐标去重
            if any(abs(p["x"] - q["x"]) < 0.5 and abs(p["y"] - q["y"]) < 0.5 for q in seen):
                continue
            seen.append(p)
        out[k] = seen
    return out


def candidate_rank(m):
    """选哪个我方点当锚点：界碑/传送点（可传送实体）优先于地图地名标签。"""
    cat, name = m.get("category") or "", m.get("name") or ""
    sc = 0
    if cat == "设施与NPC":
        sc -= 100
    if name.endswith(("界碑", "传送点")):
        sc -= 50
    if cat == "地点标记":
        sc += 10
    return sc


def build_anchors(off_points, our_index, categories, manual=None):
    """一个官方点 → 恰好一个我方点。**这点至关重要**：
    早先允许一个官方点同时对上我方「地点标记」和「界碑」（两者相距可达 137），
    几何上自相矛盾，把 map0 的相对误差从 2.2% 拖到 24.5%。"""
    per_map = defaultdict(list)
    for o in off_points:
        if o["category"] not in categories:
            continue
        cands = our_index.get(norm(o["name"]))
        if not cands:
            continue
        m = sorted(cands, key=candidate_rank)[0]
        per_map[o["mapId"]].append((o, m))

    # 并入手工锚点（游戏内实测的界碑坐标）：用来解开我方数据未覆盖的 region
    sentinel = {"category": "手工锚点", "region": ""}
    for a in (manual or []):
        mid = int(a["mapId"])
        off_like = {"id": f"manual-{a['name']}", "name": a["name"],
                    "lng": float(a["lng"]), "lat": float(a["lat"]),
                    "category": "传送点", "mapId": mid,
                    "mapName": a.get("mapName", "")}
        our_like = {"x": float(a["x"]), "y": float(a["y"]), "name": a["name"],
                    **sentinel}
        per_map[mid].append((off_like, our_like))
    return per_map


def load_manual_anchors(path: Path, off_points) -> list[dict]:
    """载入手工锚点。两种写法：

    (a) 直给官方坐标（推荐，游戏内抄一次即可）：
        [{"name": "界碑名", "x": 1234.5, "y": -678.9, "mapId": 2}]
        脚本自动用官方同名传送点补齐 (lng, lat)。

    (b) 官方与游戏坐标都给：
        [{"name": "界碑名", "x": 1234.5, "y": -678.9, "mapId": 2,
          "lng": -647202.0, "lat": 601102.0}]
    """
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("anchors", [])
    by_name = {}
    for o in off_points:
        if o["category"] in ANCHOR_CATEGORIES:
            by_name.setdefault(norm(o["name"]), []).append(o)

    out, unresolved = [], []
    for a in raw:
        name = a.get("name", "")
        mid = a.get("mapId")
        if a.get("lng") is not None and a.get("lat") is not None:
            out.append({**a, "lng": float(a["lng"]), "lat": float(a["lat"])})
            continue
        cands = [o for o in by_name.get(norm(name), [])
                 if mid is None or o["mapId"] == int(mid)]
        if len(cands) == 1:
            out.append({**a, "lng": cands[0]["lng"], "lat": cands[0]["lat"],
                        "mapId": cands[0]["mapId"]})
        else:
            unresolved.append((name, mid, len(cands)))
    if unresolved:
        print(f"  [!] {len(unresolved)} 条手工锚点无法在官方数据里定位，已跳过：")
        for n, m, c in unresolved[:10]:
            print(f"      name={n!r} mapId={m} 官方同名候选 {c} 个")
    return out


# ------------------------------------------------------------------ 主流程

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="reflected",
                    choices=["reflected", "similarity", "affine"],
                    help="变换模型，默认 reflected（4 参镜像相似，实测最优）")
    ap.add_argument("--category", default=",".join(ANCHOR_CATEGORIES),
                    help="用哪些官方分类取锚点")
    ap.add_argument("--tol-factor", type=float, default=0.0,
                    help="RANSAC 外点剔除阈值 = 我方点云跨度 × 此比例。"
                         "默认 0 = 不剔除 —— 实测 map0/map1 全部锚点的最大残差仅跨度的 2.2%%，"
                         "没有需要剔除的错配点；强行用小阈值剔除只会缩成小子集、"
                         "报出带选择偏差的虚低误差。")
    ap.add_argument("--anchors", default=str(PROJECT / "data" / "anchors_manual.json"),
                    help="手工锚点文件（游戏内实测的界碑坐标），用于解开我方数据未覆盖的区域")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--plot", action="store_true")
    ap.add_argument("--json-out", default=str(OUT))
    args = ap.parse_args()

    for f in (OFFICIAL, OURS):
        if not f.exists():
            print(f"缺少 {f}\n  先跑：python tools/fetch_official.py / python tools/etl.py")
            return 1

    off_doc = json.loads(OFFICIAL.read_text(encoding="utf-8"))
    our_doc = json.loads(OURS.read_text(encoding="utf-8"))
    off_points = off_doc["points"]
    our_points = our_doc["points"] if isinstance(our_doc, dict) else our_doc
    maps = {m["mapId"]: m["name"] for m in off_doc.get("mapSummary", [])}

    cats = [c.strip() for c in args.category.split(",") if c.strip()]
    our_index = build_ours_index(our_points)
    manual = load_manual_anchors(Path(args.anchors), off_points)
    anchors = build_anchors(off_points, our_index, cats, manual)
    fitter = FITTERS[args.model]
    need = MODEL_NEED[args.model]

    print("官方大地图 → 游戏世界坐标  换算解算")
    print(f"  官方 {len(off_points)} 点 / {len(maps)} 图，锚点分类：{'/'.join(cats)}")
    print(f"  我方 {len(our_points)} 点")
    if manual:
        print(f"  手工锚点 {len(manual)} 条（来自 {args.anchors}）")
    print(f"  模型：{args.model}（{need} 参数最小样本）\n")

    print(f"{'mapId':>5} {'图名':<9}{'锚点':>5}{'内点':>5}  {'模型':<11}"
          f"{'缩放':>11}{'旋转°':>9}{'内点RMS':>9}{'相对':>8}{'LOO-RMS':>9}{'相对':>8}  判定")
    print("-" * 118)

    results, unsolved = {}, []
    for mid in sorted(maps):
        sub = anchors.get(mid, [])
        name = maps[mid]
        if len(sub) < MIN_ANCHORS:
            print(f"{mid:>5} {name:<9}{len(sub):>5}{'—':>5}  锚点不足"
                  f"（需 ≥{MIN_ANCHORS} 对同名点）")
            unsolved.append({"mapId": mid, "name": name, "anchors": len(sub),
                             "status": "insufficient-anchors",
                             "need": MIN_ANCHORS - len(sub)})
            continue

        src_all = [(o["lng"], o["lat"]) for o, _ in sub]
        dst_all = [(m["x"], m["y"]) for _, m in sub]
        if args.tol_factor > 0 and len(sub) > need + 2:
            thr = span(dst_all) * args.tol_factor
            keep = ransac(fitter, need, src_all, dst_all, thr)
        else:
            keep = list(range(len(sub)))
        src = [src_all[i] for i in keep]
        dst = [dst_all[i] for i in keep]
        model = fitter(src, dst)
        errs = residuals(model, src, dst)
        r = rms(errs)
        sp = span(dst)
        rel = r / sp * 100 if sp else float("inf")
        loo, _ = loo_rms(fitter, src, dst, need)
        rel_loo = loo / sp * 100 if sp and loo == loo else float("inf")

        if rel_loo < ACCEPT_REL and len(src) >= MIN_ANCHORS:
            status = "ok" if len(src) >= GOOD_ANCHORS else "ok-thin"
        else:
            status = "unreliable"
        mark = {"ok": "✓ 可用", "ok-thin": "~ 锚点偏少，可用但建议补",
                "unreliable": "✗ 不可信"}[status]
        scale = model.get("scale") or math.hypot(*model["params"][:2])
        rot = model.get("rotation")
        if rot is None:
            m11, m12, _, m21, m22, _ = model["params"]
            rot = math.degrees(math.atan2(m21, m11))

        print(f"{mid:>5} {name:<9}{len(sub):>5}{len(src):>5}  {model['kind']:<11}"
              f"{scale:>11.7f}{rot:>9.2f}{r:>9.1f}{rel:>7.2f}%{loo:>9.1f}{rel_loo:>7.2f}%  {mark}")

        results[mid] = {
            "mapId": mid, "mapName": name, "status": status,
            "kind": model["kind"], "params": model["params"],
            "scale": scale, "rotation": rot,
            "nAnchor": len(sub), "nInlier": len(src),
            "rms": r, "relativeError": rel,
            "looRms": loo if loo == loo else None,
            "looRelativeError": rel_loo if rel_loo == rel_loo else None,
            "span": sp,
            "anchors": [
                {"official": {"id": o["id"], "name": o["name"], "lng": o["lng"], "lat": o["lat"],
                              "category": o["category"]},
                 "ours": {"name": m["name"], "x": m["x"], "y": m["y"],
                          "category": m.get("category"), "region": m.get("region")},
                 "residual": errs[i]}
                for i, (o, m) in enumerate((sub[j] for j in keep))
            ],
            "rejected": [
                {"official": {"id": o["id"], "name": o["name"], "lng": o["lng"], "lat": o["lat"]},
                 "ours": {"name": m["name"], "x": m["x"], "y": m["y"]},
                 "residual": errs_all}
                for j, (o, m) in enumerate(sub) if j not in keep
                for errs_all in [math.dist(apply_transform(model, o["lng"], o["lat"]),
                                           (m["x"], m["y"]))]
            ],
        }

    print("-" * 118)

    solved = {k: v for k, v in results.items() if v["status"].startswith("ok")}
    cov_off = sum(1 for p in off_points if p["mapId"] in solved)
    print(f"\n可直接使用：{len(solved)}/{len(maps)} 张图，覆盖官方 {cov_off}/{len(off_points)} 个点")

    if unsolved:
        print(f"\n需要补锚点的 {len(unsolved)} 张图：")
        for u in unsolved:
            print(f"  map{u['mapId']} {u['name']:<8} 现有 {u['anchors']} 对，"
                  f"还差 {u['need']} 对")
        print("\n  补锚点方法（游戏内 3 分钟/图）：")
        print("    1. 在目标区域找一个**界碑**，走到它旁边；")
        print("    2. 读游戏内的世界坐标 (x, y)，并记下界碑名字（与官方大地图上的名字一致）；")
        print("    3. 每张图记 5~6 个**分散**的界碑，写进 data/anchors_manual.json：")
        print('       [{"name": "界碑名", "x": 1234.5, "y": -678.9, "mapId": 2}, ...]')
        print("    4. 重跑本脚本 —— 会自动并入手工锚点。")

    # 两图参数对比（判断是否共用缩放/姿态）
    if len(solved) >= 2:
        ks = sorted(solved)
        s0, s1 = solved[ks[0]]["scale"], solved[ks[1]]["scale"]
        r0, r1 = solved[ks[0]]["rotation"], solved[ks[1]]["rotation"]
        print(f"\n各图缩放/姿态对比（若比值≈1 说明可共用一套参数，实测**不是**）：")
        print(f"  {ks[0]} vs {ks[1]}: 缩放 {s0:.7f} / {s1:.7f} = {s0 / s1:.4f}"
              f"   旋转 {r0:+.2f}° / {r1:+.2f}° 差 {r0 - r1:+.2f}°")
        print("  → 缩放比明显偏离 1，官方确实是**每图独立画布**，必须逐图解算。")

    if args.report_only:
        print("\n（--report-only，未落盘）")
        return 0

    doc = {
        "source": "tools/fit_official_transform.py",
        "fittedAt": datetime.now().isoformat(timespec="seconds"),
        "model": args.model,
        "inputOrder": "(lng, lat) -> (x, y)",
        "anchorCategories": cats,
        "officialApi": off_doc.get("api", ""),
        "officialFetchedAt": off_doc.get("fetchedAt", ""),
        "note": ("官方经纬度是每张图各自的画布坐标，不是全球地理坐标；"
                 "变换为镜像相似变换（含一次坐标轴镜像），逐图独立解算。"),
        "maps": {str(k): v for k, v in sorted(results.items())},
        "unsolved": unsolved,
        "coverage": {"solvedMaps": len(solved), "totalMaps": len(maps),
                     "officialPointsUsable": cov_off, "officialPointsTotal": len(off_points)},
    }
    out = Path(args.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写入 {out}")

    if args.plot and solved:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            n = len(solved)
            fig, axes = plt.subplots(1, n, figsize=(5.6 * n, 5.0), squeeze=False)
            for ax, mid in zip(axes[0], sorted(solved)):
                v = solved[mid]
                a = v["anchors"]
                dx = [x["ours"]["x"] for x in a]
                dy = [x["ours"]["y"] for x in a]
                px = [apply_transform(v, x["official"]["lng"], x["official"]["lat"])[0] for x in a]
                py = [apply_transform(v, x["official"]["lng"], x["official"]["lat"])[1] for x in a]
                ax.plot(dx, dy, "o", ms=6, label="我方界碑")
                ax.plot(px, py, "x", ms=9, label="官方换算")
                for i in range(len(dx)):
                    ax.plot([dx[i], px[i]], [dy[i], py[i]], "-", lw=0.8, alpha=0.6)
                ax.set_aspect("equal"); ax.legend()
                ax.set_title(f"map{mid} {v['mapName']}\nLOO 相对误差 {v['looRelativeError']:.2f}%")
            fig.tight_layout()
            fp = PROJECT / "rpa" / "official_fit.png"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(fp, dpi=130)
            print(f"图已写入 {fp}")
        except ImportError:
            print("（未安装 matplotlib，跳过 --plot）")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
