#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
import_official.py —— 把官方大地图点位换算进我们的流水线
==========================================================

做什么
------
1. 读 `data/official_transform.json`（由 tools/fit_official_transform.py 解算），
   只处理 `status` 为 ok / ok-thin 的图。
2. 把该图**全部官方点位**从官方画布经纬度换算成游戏世界坐标。
3. 写成我方 ETL 认识的原始格式，落到 `raw/official/<图名>/<官方分类>.ini`：

       x ,y ,z ,名称（备注）

   写进 `raw/official/` 而不是 `raw/` 根下，是为了：
     * `00_import_raw.py --force` 会清空 raw/ 但**保留 raw/official/**，不会被误删；
     * 溯源清晰 —— 每个点的 `sources[].file` 会带 `official/`，WebUI 上一眼能看出是官方数据。
4. 顺带写 `raw/official/README.md` 记录来源与换算参数（会被当成说明文档收录）。

为什么备注能自动进 note 字段
----------------------------
`lib_parse.split_note` 把**括号收尾**的文字当备注（正则 `[（(]([^）)]*)[)）]$`）。
所以只要把官方 description 放在名称末尾的括号里，ETL 就会自动拆成 `name` + `note`，
**不需要改 ETL 一行代码**。官方 description 里本身的括号要替换掉，
否则正则匹配不到（它要求括号内不含括号）。

命名规则
--------
官方的名字分两类：
  * 真名（见闻「【神仙渡·见闻】第一枝花」、传送点「佛爷寨」、心法「清河心法-千山法」…）
    → 原样使用，信息量最大。
  * 占位名（同一个分类里大量重复，如「蹊跷采集」×400、「清河宝箱」重复 686 次）
    → 原样用会在列表里变成一片同名，无法区分。改成 `<图名><分类短名><序号>`，
      例如「清河蹊跷采集12」。序号在该分类内唯一。

用法
----
    python tools/import_official.py                # 导入已解算的图
    python tools/import_official.py --dry-run      # 只预览不写盘
    python tools/import_official.py --maps 0,1     # 只导入指定图
    python tools/import_official.py --max-note 80  # 限制备注长度
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT / "tools") not in sys.path:
    sys.path.insert(0, str(PROJECT / "tools"))

from fit_official_transform import apply_transform, norm  # noqa: E402  (共用一个真源)

OFFICIAL = PROJECT / "data" / "official_points.json"
TRANSFORM = PROJECT / "data" / "official_transform.json"
OURS = PROJECT / "data" / "points.json"
RAW_OFFICIAL = PROJECT / "raw" / "official"


def build_ours_index(tol_source: str = "official/"):
    """从 **上一轮 ETL 输出** 里取「我方自带（非官方）点」做参照。

    用来避免同一个界碑被显示两次：官方「传送点」分类里的 110 个点，
    和我们手工抄的 70 个「XX界碑 / XX传送点」是同一批实体。

    刻意排除 `sources[].file` 里带 `official/` 的点，否则会拿上一轮导入的官方点
    去和这一轮的官方点比对，自己把自己过滤掉。
    若 points.json 不存在（首次运行），返回空索引 —— 那就先都导进来，
    等下一轮再收敛。
    """
    if not OURS.exists():
        return {}
    doc = json.loads(OURS.read_text(encoding="utf-8"))
    pts = doc["points"] if isinstance(doc, dict) else doc
    idx: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for p in pts:
        files = " ".join(str(s.get("file", "")) for s in (p.get("sources") or []))
        if tol_source in files:
            continue
        k = norm(p.get("name") or "")
        if k:
            idx[k].append((float(p["x"]), float(p["y"])))
    return idx

# 文件名里不能出现的字符（Windows）
BAD_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# 备注里要把括号抹掉，否则 split_note 的 [^）)]* 匹配不到
INNER_PAREN = re.compile(r"[（()）]")
WS = re.compile(r"\s+")

# 分类短名（用于占位名重命名，避免文件名过长）
CAT_SHORT = {
    "宝箱收集（地上）": "宝箱",
    "宝箱收集（地下）": "地下宝箱",
    "蹊跷采集": "蹊跷",
    "妙妙喵": "妙妙喵",
    "猫戏": "猫戏",
    "曲径寻幽": "曲径寻幽",
    "天地万籁": "天地万籁",
    "野祀游火": "野祀游火",
    "营地": "营地",
    "天涯客": "天涯客",
    "解谜洞窟": "解谜洞窟",
    "悬壶治疗": "悬壶",
}


def safe_name(s: str) -> str:
    return BAD_FS.sub("_", s).strip().strip(".")


def clean_note(desc: str, limit: int) -> str:
    """把 description 整理成能塞进括号里的备注。"""
    s = WS.sub(" ", (desc or "").strip())
    if not s or s in {"-", "—", "--"}:
        return ""
    s = INNER_PAREN.sub("·", s)      # 关键：内部括号要抹掉
    s = s.replace("【", "[").replace("】", "]")
    s = s.replace("\n", " ").strip()
    if limit and len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--maps", default="", help="只导入这些 mapId（逗号分隔），默认全部已解算的图")
    ap.add_argument("--max-note", type=int, default=120, help="备注最大长度，0=不限")
    ap.add_argument("--include-thin", action="store_true", default=True,
                    help="是否包含 status=ok-thin（锚点偏少但仍可用）的图")
    ap.add_argument("--no-skip-covered", action="store_true",
                    help="不跳过已被手工数据覆盖的点（默认会跳过，避免同一界碑显示两次）")
    ap.add_argument("--cover-tol", type=float, default=400.0,
                    help="判定「已被覆盖」的距离阈值（世界坐标单位，默认 400）")
    args = ap.parse_args()

    for f in (OFFICIAL, TRANSFORM):
        if not f.exists():
            print(f"缺少 {f}")
            print("  先跑：python tools/fetch_official.py")
            print("        python tools/fit_official_transform.py")
            return 1

    off_doc = json.loads(OFFICIAL.read_text(encoding="utf-8"))
    tr = json.loads(TRANSFORM.read_text(encoding="utf-8"))
    points = off_doc["points"]

    ok_status = {"ok"} | ({"ok-thin"} if args.include_thin else set())
    solved = {int(k): v for k, v in tr["maps"].items() if v["status"] in ok_status}
    if args.maps:
        want = {int(x) for x in args.maps.split(",") if x.strip()}
        solved = {k: v for k, v in solved.items() if k in want}

    if not solved:
        print("没有已解算的图可导入。先跑 tools/fit_official_transform.py，")
        print("并参考其中给出的「补锚点方法」把 data/anchors_manual.json 补齐。")
        return 2

    print(f"待导入的图：{len(solved)} 张")
    for mid, v in sorted(solved.items()):
        print(f"  map{mid} {v['mapName']:<8} {v['nInlier']} 锚点  "
              f"LOO 相对误差 {v['looRelativeError']:.2f}%  [{v['status']}]")

    # 按 (mapId, category) 分组
    groups: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for p in points:
        if p["mapId"] in solved:
            groups[(p["mapId"], p["category"])].append(p)

    # 判断哪些分类是「占位名为主」：同一 (图, 分类) 内最高频名字占比 > 40% 即需编号。
    # 只用重复率判断，**不能**附加「点数多就编号」这类条件 —— 见闻 521 点里 518 个名字唯一，
    # 却被大分组条件误编号，白丢了官方最有信息量的那批名字。
    placeholder_cat: dict[tuple[int, str], bool] = {}
    for key, ps in groups.items():
        c = Counter(p["name"] for p in ps)
        top = c.most_common(1)[0][1]
        placeholder_cat[key] = (top / len(ps)) > 0.4

    written: dict[str, int] = {}
    by_cat = Counter()
    by_map = Counter()
    total = 0
    skipped_no_transform = 0
    skipped_covered = 0
    covered_names: list[str] = []

    ours_idx = {} if args.no_skip_covered else build_ours_index()
    if ours_idx:
        print(f"\n参照我方自带点：{len(ours_idx)} 个不同名字"
              f"（距离阈值 {args.cover_tol:.0f}；命中的官方点会被跳过）")

    for (mid, cat), ps in sorted(groups.items()):
        v = solved[mid]
        map_name = v["mapName"]
        short = CAT_SHORT.get(cat, cat)
        need_index = placeholder_cat[(mid, cat)]

        lines: list[str] = []
        for i, p in enumerate(sorted(ps, key=lambda q: q["id"]), start=1):
            try:
                x, y = apply_transform(v, p["lng"], p["lat"])
            except Exception:
                skipped_no_transform += 1
                continue
            if not (-20000 < x < 20000 and -20000 < y < 20000):
                skipped_no_transform += 1     # 换算结果离谱说明参数不对，宁可不写
                continue

            nm = (p["name"] or "").strip()
            if need_index:
                # 占位名 → 图名+分类短名+序号，保证列表里可区分
                if nm.startswith(map_name):
                    nm = f"{nm}{i}"
                else:
                    nm = f"{map_name}{short}{i}"
            else:
                nm = f"{map_name}{short}{i}" if not nm else nm

            # 已被手工数据覆盖的同名点就跳过：官方「传送点」与我们的「XX界碑/XX传送点」
            # 是同一批实体，两份都留会在地图上叠出重影。
            # 保留**我方**那份 —— 它是在游戏内按坐标读数抄下来的，比官方地图上
            # 手放的图钉更贴近实际可传送位置；而且留两份没有任何额外信息。
            key = norm(p["name"])
            if key and key in ours_idx:
                near = min((abs(x - ox) + abs(y - oy) for ox, oy in ours_idx[key]),
                           default=float("inf"))
                if near <= args.cover_tol:
                    skipped_covered += 1
                    if len(covered_names) < 8:
                        covered_names.append(p["name"])
                    continue

            note = clean_note(p.get("description", ""), args.max_note)
            label = f"{nm}（{note}）" if note else nm
            lines.append(f"{x:.2f} ,{y:.2f} ,0 ,{label}")

        if not lines:
            continue
        rel = f"{safe_name(map_name)}/{safe_name(cat)}.ini"
        dest = RAW_OFFICIAL / rel
        if not args.dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written[rel] = len(lines)
        by_cat[cat] += len(lines)
        by_map[map_name] += len(lines)
        total += len(lines)

    print(f"\n按图统计：")
    for name, n in by_map.most_common():
        print(f"  {name:<10} {n:>5} 点")
    print(f"\n按分类统计：")
    for cat, n in by_cat.most_common():
        mark = " [编号命名]" if any(placeholder_cat[(m, cat)] for m in solved) else ""
        print(f"  {cat:<18} {n:>5}{mark}")
    if skipped_no_transform:
        print(f"\n  [!] {skipped_no_transform} 个点换算结果越界或失败，已跳过")
    if skipped_covered:
        print(f"\n  已被手工坐标覆盖、跳过 {skipped_covered} 个官方点"
              f"（示例：{'、'.join(covered_names)}）")
        print("  要全部保留（例如想对比两套坐标的差异）加 --no-skip-covered")

    print(f"\n共 {total} 点，写入 {len(written)} 个文件"
          f"{'（--dry-run，未落盘）' if args.dry_run else ''} -> {RAW_OFFICIAL}")

    if not args.dry_run:
        README = RAW_OFFICIAL / "README.md"
        README.write_text(f"""# 官方大地图换算数据

本目录**不是手工抄的坐标**，而是由脚本从燕云十六声官方大地图接口拉取点位后、
按解算出的换算关系转成游戏世界坐标生成的。**请勿手工编辑** —— 重跑脚本会覆盖。

- 生成时间：{datetime.now().isoformat(timespec="seconds")}
- 生成脚本：`tools/import_official.py`
- 数据来源：{off_doc.get("source", "官方大地图")}
- 接口：`{off_doc.get("api", "")}`
- 抓取时间：{off_doc.get("fetchedAt", "")}
- 换算参数：`data/official_transform.json`

## 换算关系

官方大地图的 `longitude/latitude` **不是全球地理坐标**，而是**每张图各自画布**的坐标
（map0 与 map1 解出的缩放比为 1.5002，全局单一仿射的相对误差 18.6%，逐图后降到 2.2%）。
变换类型是**镜像相似变换**（含一次坐标轴镜像），只有 4 个参数：

    v = -lat
    x = a*lng - b*v + tx
    y = b*lng + a*v + ty

推导与交叉验证过程见 `docs/官方数据接入设计.md`。

## 覆盖范围

| 图 | 锚点 | LOO 相对误差 | 状态 |
| --- | --- | --- | --- |
""" + "\n".join(
            f"| map{mid} {v['mapName']} | {v['nInlier']} | "
            f"{v['looRelativeError']:.2f}% | {v['status']} |"
            for mid, v in sorted(solved.items())
        ) + """

未列出的图（河西 / 风催凉州 / 不见山 / 滹沱 / 开封皇宫 / 青州 / 江南）**暂无锚点**：
原始手工坐标库只覆盖清河与开封，解不出那几张图的参数。
补法见 `data/anchors_manual.json` 的模板与 `docs/官方数据接入设计.md`。

## 命名说明

官方部分分类使用**占位名**（「蹊跷采集」重复 400 次、「清河宝箱」重复 686 次），
直接沿用会在一张列表里出现大量同名条目。这些分类的点被改名为
`<图名><分类短名><序号>`（如「清河蹊跷采集12」），序号在该分类内唯一。
名称唯一的分类（传送点 / 见闻 / 万事知 / 心法 / 奇术 / 野外首领…）保持官方原名。

官方 `description` 字段是**解谜或获取方法**，被写在名称末尾的括号里，
ETL 的 `split_note` 会自动把它拆进 `note` 字段。
""", encoding="utf-8")
        print(f"已写入 {README}")

    print("\n下一步：")
    print("  python tools/etl.py        # 官方数据会随 raw/ 一起进流水线")
    print("  python tools/verify.py     # 复核")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
