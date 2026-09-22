#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
严格验证「每图本地画布」：子区域包围盒是否被父区域包含？

逻辑
----
开封皇宫（map6）在地理上位于开封（map1）城内。若官方经纬度是**全局公共空间**，
则 map6 的所有点位坐标必须**落在 map1 的坐标范围内**（子集关系）。

若 map6 有大量点在 map1 范围之外，则官方坐标**不可能是全局的** ——
只能是每张图各自的本地画布（同一点在不同图里有不同坐标）。

这是不依赖任何进游戏操作、纯靠接口数据就能给出的判据。
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
P = Path(__file__).resolve().parent.parent
off = json.loads((P / "data" / "official_points.json").read_text(encoding="utf-8"))
pts = off["points"]

by_map = {}
for p in pts:
    by_map.setdefault(p["mapId"], []).append(p)

info = {}
for mid, ps in by_map.items():
    lngs = [p["lng"] for p in ps]
    lats = [p["lat"] for p in ps]
    info[mid] = {"name": ps[0]["mapName"], "n": len(ps),
                 "lng": (min(lngs), max(lngs)), "lat": (min(lats), max(lats))}

print("=" * 90)
print("一、各图点位包围盒")
print("=" * 90)
print(f"{'mapId':>5} {'图名':<9}{'点数':>6}  {'lng 范围':>26}  {'lat 范围':>26}")
print("-" * 90)
for mid in sorted(info):
    v = info[mid]
    print(f"{mid:>5} {v['name']:<9}{v['n']:>6}  "
          f"[{v['lng'][0]:>11.0f},{v['lng'][1]:>11.0f}]  "
          f"[{v['lat'][0]:>11.0f},{v['lat'][1]:>11.0f}]")
print("-" * 90)

# 地理上的父子关系（按燕云十六声的世界设定）
PARENT = {
    6: (1, "开封皇宫 位于 开封城内"),
    3: (2, "风催凉州 位于 河西走廊（与河西同属西北）"),
}

print("\n" + "=" * 90)
print("二、关键判据：子区域的点，有多少落在父区域的坐标范围之外？")
print("=" * 90)
for child, (parent, why) in PARENT.items():
    if child not in info or parent not in info:
        continue
    c, p = info[child], info[parent]
    print(f"\n{why}（map{child} {c['name']} ⊂ map{parent} {p['name']}）")
    print(f"  父 map{parent} {p['name']:<9} lng[{p['lng'][0]:.0f},{p['lng'][1]:.0f}] "
          f"lat[{p['lat'][0]:.0f},{p['lat'][1]:.0f}]")
    print(f"  子 map{child} {c['name']:<9} lng[{c['lng'][0]:.0f},{c['lng'][1]:.0f}] "
          f"lat[{c['lat'][0]:.0f},{c['lat'][1]:.0f}]")

    outside_lng = sum(1 for q in by_map[child]
                      if q["lng"] < p["lng"][0] or q["lng"] > p["lng"][1])
    outside_lat = sum(1 for q in by_map[child]
                      if q["lat"] < p["lat"][0] or q["lat"] > p["lat"][1])
    total = len(by_map[child])
    print(f"  子区域点在父区域经度范围外：{outside_lng}/{total} = {outside_lng / total * 100:.1f}%")
    print(f"  子区域点在父区域纬度范围外：{outside_lat}/{total} = {outside_lat / total * 100:.1f}%")

    # 子范围是否被父范围包含
    contained = (c["lng"][0] >= p["lng"][0] and c["lng"][1] <= p["lng"][1]
                 and c["lat"][0] >= p["lat"][0] and c["lat"][1] <= p["lat"][1])
    print(f"  子包围盒是否被父包围盒包含：{'是' if contained else '**否**'}")
    if not contained:
        print("  → 若是全局公共空间，子区域必然被父区域包含。此处不成立，")
        print("     所以官方经纬度**不是全局坐标，而是每张图各自的本地画布**。")

print("\n" + "=" * 90)
print("三、各图之间两两重叠程度（重叠高 ≠ 同一空间，但能看出排布）")
print("=" * 90)
ids = sorted(info)
print("     " + "".join(f"{j:>7}" for j in ids))
for i in ids:
    row = []
    for j in ids:
        if i >= j:
            row.append("      -")
            continue
        a, b = info[i], info[j]
        ov_lng = max(0, min(a["lng"][1], b["lng"][1]) - max(a["lng"][0], b["lng"][0]))
        ov_lat = max(0, min(a["lat"][1], b["lat"][1]) - max(a["lat"][0], b["lat"][0]))
        ia = (a["lng"][1] - a["lng"][0]) or 1
        ib = (b["lng"][1] - b["lng"][0]) or 1
        frac = ov_lng / min(ia, ib)
        row.append(f"{frac * 100:>6.0f}%")
    print(f"  {i:>3} " + "".join(f"{c:>7}" for c in row))

print("\n结论：官方 9 张图的坐标各自独立（本地画布），互不构成一个可直接换算的空间。")
print("      因此每张图都需要自己的锚点来解算 —— 这与逐图拟合实测结果一致。")
