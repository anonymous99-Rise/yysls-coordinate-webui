#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""

import sys
etl.py —— 原始坐标 → 规范化数据集
==================================

输入：raw/（00_import_raw.py 导入的原始字节副本，外加 raw/rpa/ 下影刀采集的新数据）
输出：
    data/points.json      全字段规范点位（含来源可追溯，体积大）
    data/points.min.json  紧凑版（字典表 + 行数组），WebUI 用
    data/facets.json      分类/区域/材料/来源 计数 + 字段字典
    data/notes.json       注意事项与致谢（从纯文本文件里抽出来的）
    data/issues.json      解析异常与修复记录（供人工复核）
    data/_unclassified.txt 没被 taxonomy 认出来的名称（用来迭代词典）
    exports/*.ini         按材料/分类/区域重新生成的可导入文件

核心原则：
  * 零丢失 —— 解析不了的行全部进 issues.json，不静默吞掉
  * 不丢重复语义 —— 同一坐标在单个文件里出现 N 次是「刷怪循环」，
    聚合成 repeat 字段保留，而不是简单去重
  * 可追溯 —— 每个点位都记着原始文件 + 行号

用法：
    python tools/etl.py
    python tools/etl.py --no-exports
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
import json
from collections import Counter, defaultdict
from pathlib import Path

import taxonomy as tax
from lib_parse import (
    COORD_ABS_LIMIT,
    PLACEHOLDER_NAMES,
    Issue,
    Row,
    classify_full,
    decode_bytes,
    layer_of,
    normalize_key,
    parse_line,
    pick_material,
    pick_place,
    region_from_coord,
    region_from_name,
    region_from_path,
    split_index,
    split_note,
)

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
RAW = PROJECT / "raw"
DATA = PROJECT / "data"
EXPORTS = PROJECT / "exports"

DATA_EXT = {".ini", ".txt", ".cfg"}
# 纯说明文档（原包根目录的 README 之类）。只当说明收录，永远不当坐标解析。
NOTE_EXT = {".md"}

CREDITS = {
    "来源": "D:\\blog\\yysls_coordinate（原始手工坐标库）",
    "分区攻略作者": "Hermit",
    "材料整合": "小狼",
    "声明": "本传送坐标为免费分享，请勿倒卖；转载请注明原作者。",
    "射覆题库": "https://docs.qq.com/sheet/DV3FUdUp5ZmpZcmhS?tab=BB08J2",
    "象棋残局库": "https://chessdb.cn/query",
}


def iter_raw_files() -> list[Path]:
    out = []
    for p in sorted(RAW.rglob("*")):
        if not p.is_file():
            continue
        if p.name == "_manifest.json":
            continue
        if p.suffix.lower() not in DATA_EXT:
            continue
        out.append(p)
    return out


def iter_note_files() -> list[Path]:
    """纯说明文档。排除 raw/rpa/ —— 那是本项目自己的说明，不该混进「原作者的话」里。"""
    out = []
    for p in sorted(RAW.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in NOTE_EXT:
            continue
        if str(p.relative_to(RAW)).replace("\\", "/").startswith("rpa/"):
            continue
        out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-exports", action="store_true")
    args = ap.parse_args()

    DATA.mkdir(parents=True, exist_ok=True)
    EXPORTS.mkdir(parents=True, exist_ok=True)

    rows: list[Row] = []
    issues: list[Issue] = []
    notes: list[dict] = []
    file_meta: list[dict] = []
    issues_by_file: dict[str, list[Issue]] = defaultdict(list)

    for path in iter_raw_files():
        rel = str(path.relative_to(RAW)).replace("\\", "/")
        text, codec = decode_bytes(path.read_bytes())
        parsed_here = 0
        for i, line in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
            got, iss = parse_line(line, file=rel, line_no=i)
            rows.extend(got)
            issues_by_file[rel].extend(iss)
            parsed_here += len(got)

        file_meta.append(
            {
                "file": rel,
                "codec": codec,
                "bytes": path.stat().st_size,
                "rows": parsed_here,
                "is_rpa": rel.startswith("rpa/"),
            }
        )

        # 没有任何坐标行、但有正文 → 当作说明文档
        if parsed_here == 0 and text.strip():
            notes.append(
                {
                    "file": rel,
                    "dir": str(Path(rel).parent).replace("\\", "/"),
                    "lines": [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()],
                }
            )

    # 说明文档里的「不是坐标的行」不算解析问题；只有真正含坐标的文件才需要报错
    for rel, items in issues_by_file.items():
        has_data = any(f["file"] == rel and f["rows"] > 0 for f in file_meta)
        for it in items:
            if it.kind == "unparsed_line" and not has_data:
                continue
            issues.append(it)

    # 纯说明文档（原包根目录的 README 之类）：只收录，不解析坐标
    for path in iter_note_files():
        rel = str(path.relative_to(RAW)).replace("\\", "/")
        text, codec = decode_bytes(path.read_bytes())
        if not text.strip():
            continue
        file_meta.append({"file": rel, "codec": codec, "bytes": path.stat().st_size, "rows": 0, "is_rpa": False})
        notes.append(
            {
                "file": rel,
                "dir": str(Path(rel).parent).replace("\\", "/"),
                "lines": [ln.rstrip() for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()],
            }
        )

    # ---- 按别名把说明文档挂回被去重掉的位置 -----------------------------
    manifest_path = RAW / "_manifest.json"
    alias_map: dict[str, list[str]] = {}
    if manifest_path.exists():
        alias_map = json.loads(manifest_path.read_text(encoding="utf-8")).get("alias_map", {})
    for note in notes:
        for alias in alias_map.get(note["file"], []):
            notes.append(
                {
                    "file": alias,
                    "dir": str(Path(alias).parent).replace("\\", "/"),
                    "lines": note["lines"],
                    "alias_of": note["file"],
                }
            )

    # ---- 聚合 -----------------------------------------------------------
    groups: dict[tuple, dict] = {}
    for r in rows:
        key = (round(r.x, 2), round(r.y, 2), round(r.z, 2), normalize_key(r.name))
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "x": round(r.x, 4),
                "y": round(r.y, 4),
                "z": round(r.z, 4),
                "name": r.name,
                "sources": {},          # file -> {"lines": [...], "count": n}
                "repairs": set(),
                "flags": set(),
            }
        src = g["sources"].setdefault(r.file, {"lines": [], "count": 0})
        src["count"] += 1
        if len(src["lines"]) < 8:
            src["lines"].append(r.line)
        g["repairs"].update(r.repairs)
        # 原始名有出入时保留更长的那一个（信息更多）
        if len(r.name) > len(g["name"]):
            g["name"] = r.name

    # 同坐标不同名 → 可能是一次「软件没刷新」留下的伪重复，标记出来
    by_xyz: dict[tuple, set[str]] = defaultdict(set)
    for g in groups.values():
        by_xyz[(g["x"], g["y"], g["z"])].add(normalize_key(g["name"]))
    colocated = {k for k, v in by_xyz.items() if len(v) > 1}

    points: list[dict] = []
    for idx, g in enumerate(
        sorted(groups.values(), key=lambda g: (g["name"], g["x"], g["y"])), start=1
    ):
        name: str = g["name"]
        base, note = split_note(name)
        base2, index = split_index(base)
        # 分类要看「名称 + 它出自哪个文件」—— 名称是占位符时路径更靠谱
        src_paths = sorted(g["sources"])
        category, cat_src = classify_full(name, src_paths[0] if src_paths else "")
        material = pick_material(name)
        place = pick_place(name)
        if not material:
            # 名称里没有材料，但文件名是材料名（例：牛背筋.txt 里全是「示例」）
            for rel in src_paths:
                m = pick_material(Path(rel).stem)
                if m:
                    material = m
                    break

        # 区域：名称区域词 > 地名 > 来源路径 > 坐标兜底
        region = region_from_name(name)
        region_src = "name" if region else ""
        if not region:
            region = tax.REGION_BY_PLACE.get(place, "")
            region_src = "place" if region else ""
        if not region:
            for rel in src_paths:
                r = region_from_path(rel)
                if r:
                    region, region_src = r, "path"
                    break
        if not region:
            r = region_from_coord(g["x"], g["y"])
            if r:
                region, region_src = r, "coord"

        flags = set(g["flags"])
        total = sum(s["count"] for s in g["sources"].values())
        repeat = max(s["count"] for s in g["sources"].values())
        if total > 1:
            flags.add("duplicated")
        if len(g["sources"]) > 1:
            flags.add("cross_file")
        if name in PLACEHOLDER_NAMES:
            flags.add("placeholder_name")
        if not name:
            flags.add("name_missing")
        if (g["x"], g["y"], g["z"]) in colocated:
            flags.add("co_located_with_other")
        if region_src == "coord":
            flags.add("region_inferred")
        flags.update(g["repairs"])
        if abs(g["x"]) > COORD_ABS_LIMIT or abs(g["y"]) > COORD_ABS_LIMIT:
            flags.add("suspicious_range")

        src_list = [
            {"file": f, "count": s["count"], "lines": s["lines"]}
            for f, s in sorted(g["sources"].items())
        ]
        points.append(
            {
                "id": f"p{idx:05d}",
                "x": g["x"],
                "y": g["y"],
                "z": g["z"],
                "name": name,
                "base": base2,
                "index": index,
                "note": note,
                "category": category,
                "categorySource": cat_src,
                "material": material,
                "place": place,
                "region": region,
                "regionSource": region_src,
                "layer": layer_of(g["z"]),
                "repeat": repeat,
                "totalRows": total,
                "fileCount": len(g["sources"]),
                "sources": src_list,
                "flags": sorted(flags),
            }
        )

    # ---- 区域补全：靠坐标边界判不出来的，就近借邻居的区域 ----------------
    # 实测清河与开封在 X∈[-2730,-2169] 有窄重叠带，硬边界会判错或判不出（697 条）。
    # 这里改成「最近邻投票」：找最近的一个已知区域的点，借它的区域，并打 region_nearest 标记。
    # 用 200 单位网格分桶 + 逐圈外扩，避免 O(n²)。
    import math

    CELL = 200.0
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    known_idx = [i for i, p in enumerate(points) if p["region"]]
    for i in known_idx:
        p = points[i]
        grid[(int(p["x"] // CELL), int(p["y"] // CELL))].append(i)

    def nearest_known(px: float, py: float) -> int | None:
        cx, cy = int(px // CELL), int(py // CELL)
        for ring in range(0, 60):
            best, best_d = None, float("inf")
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    if ring > 0 and max(abs(dx), abs(dy)) != ring:
                        continue  # 只看当前这一圈
                    for i in grid.get((cx + dx, cy + dy), ()):
                        q = points[i]
                        d = (q["x"] - px) ** 2 + (q["y"] - py) ** 2
                        if d < best_d:
                            best, best_d = i, d
            if best is not None:
                return best
        return None

    filled = 0
    for p in points:
        if p["region"]:
            continue
        i = nearest_known(p["x"], p["y"])
        if i is None:
            continue
        p["region"] = points[i]["region"]
        p["regionSource"] = "nearest"
        p["flags"] = sorted(set(p["flags"]) | {"region_nearest"})
        filled += 1

    # ---- facets ---------------------------------------------------------
    def facet(field: str) -> list[dict]:
        c: Counter = Counter()
        for p in points:
            v = p.get(field)
            if isinstance(v, list):
                for x in v:
                    c[x] += 1
            elif v not in ("", None):
                c[str(v)] += 1
        return [{"key": k, "count": n} for k, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))]

    flag_counter: Counter = Counter()
    for p in points:
        for f in p["flags"]:
            flag_counter[f] += 1

    stats = {
        "rawFiles": len(file_meta),
        "rawRows": len(rows),
        "points": len(points),
        "redundantRows": len(rows) - len(points),
        "issues": len(issues),
        "noteFiles": len({n["file"] for n in notes}),
        "crossFilePoints": sum(1 for p in points if "cross_file" in p["flags"]),
        "coLocatedPoints": sum(1 for p in points if "co_located_with_other" in p["flags"]),
        "regionUnknown": sum(1 for p in points if not p["region"]),
        "regionFilledByNearest": filled,
    }

    # 分类按 CATEGORY_META.order 排，方便 UI 直接照序渲染
    def cat_sort(item: dict) -> tuple:
        meta = tax.CATEGORY_META.get(item["key"], {})
        return (meta.get("order", 999), -item["count"])

    cat_facet = sorted(facet("category"), key=cat_sort)

    facets = {
        "version": 1,
        "generatedBy": "tools/etl.py",
        "stats": stats,
        "categoryMeta": tax.CATEGORY_META,
        "categories": cat_facet,
        "regions": facet("region"),
        "materials": facet("material"),
        "places": facet("place"),
        "layers": facet("layer"),
        "regionSources": facet("regionSource"),
        "categorySources": facet("categorySource"),
        "flags": [{"key": k, "count": n} for k, n in flag_counter.most_common()],
        "sourceFiles": sorted(
            (
                {
                    "file": f["file"],
                    "codec": f["codec"],
                    "bytes": f["bytes"],
                    "rows": f["rows"],
                    "isRpa": f["is_rpa"],
                    "points": sum(1 for p in points if any(s["file"] == f["file"] for s in p["sources"])),
                }
                for f in file_meta
            ),
            key=lambda x: x["file"],
        ),
    }

    # ---- 紧凑版（WebUI 用）：字典表 + 行数组 ------------------------------
    def dict_of(field: str) -> tuple[list[str], dict[str, int]]:
        # 空串固定占 0 号位（material/place/region 都可能为空）
        vals = sorted({p[field] for p in points if p[field]})
        if any(not p[field] for p in points):
            vals = [""] + vals
        return vals, {v: i for i, v in enumerate(vals)}

    cat_d, cat_i = dict_of("category")
    reg_d, reg_i = dict_of("region")
    mat_d, mat_i = dict_of("material")
    plc_d, plc_i = dict_of("place")
    lay_d, lay_i = dict_of("layer")

    flags_all = sorted({f for p in points for f in p["flags"]})
    flag_i = {f: i for i, f in enumerate(flags_all)}
    src_all = sorted({s["file"] for p in points for s in p["sources"]})
    src_i = {s: i for i, s in enumerate(src_all)}
    catsrc_all = sorted({p["categorySource"] for p in points})
    catsrc_i = {s: i for i, s in enumerate(catsrc_all)}
    regsrc_all = sorted({p["regionSource"] for p in points})
    regsrc_i = {s: i for i, s in enumerate(regsrc_all)}

    min_rows = [
        [
            p["x"], p["y"], p["z"], p["name"], p["base"], p["index"] if p["index"] is not None else -1,
            p["note"], cat_i[p["category"]], mat_i[p["material"]], plc_i[p["place"]],
            reg_i[p["region"]], lay_i[p["layer"]], p["repeat"], p["totalRows"],
            sum(1 << flag_i[f] for f in p["flags"]),
            sorted(src_i[s["file"]] for s in p["sources"]),
            catsrc_i[p["categorySource"]], regsrc_i[p["regionSource"]],
        ]
        for p in points
    ]

    points_min = {
        "version": 1,
        "fields": [
            "x", "y", "z", "name", "base", "index", "note", "category", "material",
            "place", "region", "layer", "repeat", "totalRows", "flagBits", "srcIdx",
            "categorySource", "regionSource",
        ],
        "dicts": {
            "category": cat_d,
            "region": reg_d,
            "material": mat_d,
            "place": plc_d,
            "layer": lay_d,
            "flags": flags_all,
            "sources": src_all,
            "categorySource": catsrc_all,
            "regionSource": regsrc_all,
        },
        "rows": min_rows,
    }

    # ---- 落盘 -----------------------------------------------------------
    (DATA / "points.json").write_text(
        json.dumps({"version": 1, "stats": stats, "points": points}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    (DATA / "points.min.json").write_text(
        json.dumps(points_min, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    (DATA / "facets.json").write_text(json.dumps(facets, ensure_ascii=False, indent=2), encoding="utf-8")
    (DATA / "notes.json").write_text(
        json.dumps({"credits": CREDITS, "notes": notes}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DATA / "issues.json").write_text(
        json.dumps(
            {
                "stats": Counter(i.kind for i in issues),
                "issues": [
                    {"kind": i.kind, "detail": i.detail, "file": i.file, "line": i.line, "raw": i.raw}
                    for i in issues
                ],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )

    unclassified = Counter(p["name"] for p in points if p["category"] == "其他")
    (DATA / "_unclassified.txt").write_text(
        "# taxonomy.py 没认出来的名称（按出现次数排序）。补进 CATEGORY_RULES 后重跑 etl.py 即可。\n"
        + "\n".join(f"{n:5d}  {k}" for k, n in unclassified.most_common()),
        encoding="utf-8",
    )

    # ---- 导出 -----------------------------------------------------------
    written: list[str] = []
    if not args.no_exports:
        def dump(subdir: str, key: str, items: list[dict]) -> None:
            if not items:
                return
            d = EXPORTS / subdir
            d.mkdir(parents=True, exist_ok=True)
            safe = "".join(c for c in key if c not in '\\/:*?"<>|').strip() or "unnamed"
            lines = [f"{p['x']},{p['y']},{p['z']},{p['name']}" for p in items]
            (d / f"{safe}.ini").write_text("\n".join(lines) + "\n", encoding="utf-8")
            written.append(f"{subdir}/{safe}.ini ({len(lines)})")

        for field, sub in (("material", "by-material"), ("category", "by-category"), ("region", "by-region")):
            buckets: dict[str, list[dict]] = defaultdict(list)
            for p in points:
                if p[field]:
                    buckets[p[field]].append(p)
            for k, v in buckets.items():
                dump(sub, k, sorted(v, key=lambda p: (p["name"], p["x"], p["y"])))

        dump("all", "全部去重点位", sorted(points, key=lambda p: (p["region"], p["name"], p["x"])))

    # ---- 汇报 -----------------------------------------------------------
    print("== 解析 ==")
    print(f"  原始文件        : {stats['rawFiles']}")
    print(f"  解析出的坐标行  : {stats['rawRows']}")
    print(f"  规范化点位      : {stats['points']}")
    print(f"  冗余行(已聚合)  : {stats['redundantRows']}")
    print(f"  跨文件出现的点  : {stats['crossFilePoints']}")
    print(f"  同坐标多名(可疑): {stats['coLocatedPoints']}")
    print(f"  区域未判定      : {stats['regionUnknown']}")
    print(f"  解析问题        : {stats['issues']}  明细见 data/issues.json")
    print("== 分类 ==")
    for c in facets["categories"]:
        print(f"  {c['key']:<10} {c['count']:>6}")
    print("== 区域 ==")
    for c in facets["regions"]:
        print(f"  {c['key'] or '(未判定)':<10} {c['count']:>6}")
    if unclassified:
        print(f"== 未分类名称 {len(unclassified)} 种，Top 10（见 data/_unclassified.txt）==")
        for k, n in unclassified.most_common(10):
            print(f"  {n:>5}  {k}")
    if written:
        print(f"== 导出 {len(written)} 个文件 → exports/ ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
