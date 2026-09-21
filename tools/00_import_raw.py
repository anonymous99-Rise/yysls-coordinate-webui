#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
00_import_raw.py —— 把原始坐标库导入本项目的 raw/ 目录
=======================================================

原始库（SOURCE）里的 122 个文件存在三类冗余/噪声：
  1. 内容完全相同的重复文件（8 组，同 sha256）
  2. 空文件
  3. 命名与内容不符（例：宝箱坐标\\宝箱坐标.txt 里其实全是芍药坐标）

本脚本做的事：
  * 逐字节 sha256 分组，每组只保留一个「最佳代表」
  * 代表选取是数据驱动的：解析内容里的坐标标签，优先保留
    「文件名与内容标签互相印证」的那一份（于是会丢掉被错误命名的副本，
    保留命名正确的那份），而不是傻按字母序
  * 被丢弃的副本不丢信息：文件名作为 alias 记进 raw/_manifest.json
  * raw/ 里保存的是**原始字节**（GBK/UTF-8 原样），方便日后对账；
    编码归一化交给 etl.py

用法：
    python tools/00_import_raw.py
    python tools/00_import_raw.py --source D:\\blog\\yysls_coordinate --force
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
import hashlib
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
DEFAULT_SOURCE = Path(r"D:\blog\yysls_coordinate")
RAW = PROJECT / "raw"

DATA_EXT = {".ini", ".txt", ".cfg"}
NOTE_EXT = {".md", ".bat"}
ALL_EXT = DATA_EXT | NOTE_EXT

# 已经被归类到明确类别目录的路径片段（用于择优时的次要加分）
CATEGORY_HINTS = (
    "宝箱坐标", "界碑坐标", "蹊跷坐标", "奇术坐标", "杂项坐标", "分区域", "小狼整合",
)

COORD_RE = re.compile(
    r"^\s*(-?\d+(?:\.\d+)?)\s*[,\t ]\s*(-?\d+(?:\.\d+)?)\s*[,\t ]\s*(-?\d+(?:\.\d+)?)\s*,\s*(.+?)\s*$"
)


def read_text_lossy(path: Path) -> tuple[str, str]:
    """返回 (文本, 编码)。优先 UTF-8，失败回落 GB18030（cp936 的超集）。"""
    data = path.read_bytes()
    if not data:
        return "", "empty"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("gb18030", errors="replace"), "gb18030"


def labels_of(path: Path) -> list[str]:
    """抽出文件里所有坐标行的第 4 字段（名称），用于命名一容性打分。"""
    if path.suffix.lower() not in DATA_EXT:
        return []
    text, _ = read_text_lossy(path)
    out: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        m = COORD_RE.match(line)
        if m:
            name = m.group(4).strip()
            if name:
                out.append(name)
    return out


def name_content_score(stem: str, labels: list[str]) -> int:
    """文件名与内容标签的吻合度。越高说明「名字配得上内容」。"""
    if not labels:
        return 0
    stem_n = stem.strip()
    if not stem_n:
        return 0
    score = 0
    seen: set[str] = set()
    for lab in labels:
        if lab in seen:
            continue
        seen.add(lab)
        core = re.split(r"[\s（(\-—0-9]", lab, maxsplit=1)[0].strip()
        if stem_n == lab or stem_n == core:
            score += 10          # 完全一致
        elif stem_n and (stem_n in lab or lab in stem_n):
            score += 6
        elif core and (stem_n in core or core in stem_n):
            score += 4
    return score


# 典型的「副本」命名痕迹：(1)、(2)、- 副本、copy、bak
COPY_SUFFIX_RE = re.compile(r"(\s*\(\d+\)|- ?副本|[-_ ]?copy|[-_ ]?bak)\s*$", re.IGNORECASE)


def pick_keeper(group: list[dict]) -> dict:
    """在一组内容相同的文件里挑代表。

    优先级：文件名↔内容吻合度 > 不是「副本」式命名 > 位于明确的类别目录
            > 路径层级浅 > 路径字典序。
    最后一条保证结果确定、可复现。
    """
    def rank(f: dict):
        rel: str = f["rel"]
        path = Path(rel)
        hint = 1 if any(h in rel for h in CATEGORY_HINTS) else 0
        depth = len(path.parts)
        copyish = 1 if COPY_SUFFIX_RE.search(path.stem) else 0
        return (-f["score"], copyish, -hint, depth, rel)

    return sorted(group, key=rank)[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(DEFAULT_SOURCE))
    ap.add_argument("--force", action="store_true", help="raw/ 已有内容时也重建")
    args = ap.parse_args()

    source = Path(args.source)
    if not source.is_dir():
        print(f"[x] 源目录不存在: {source}", file=sys.stderr)
        return 2

    if RAW.exists() and any(RAW.iterdir()) and not args.force:
        print(f"[!] {RAW} 非空；如需重建请加 --force")
        return 1

    # --force 重建时先清空 raw/，但 **保留 raw/rpa/**（那是影刀 RPA 的采集落地区，
    # 由本导入脚本之外的流程写入，不能被清掉）。
    if args.force and RAW.exists():
        for child in RAW.iterdir():
            if child.name == "rpa":
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        print(f"  [force] 已清空 {RAW}（保留 raw/rpa/）")

    files = sorted(
        (p for p in source.rglob("*") if p.is_file() and p.suffix.lower() in ALL_EXT),
        key=lambda p: str(p.relative_to(source)),
    )

    groups: dict[str, list[dict]] = defaultdict(list)
    empty: list[str] = []

    for p in files:
        rel = str(p.relative_to(source))
        raw = p.read_bytes()
        if not raw:
            empty.append(rel)
            continue
        digest = hashlib.sha256(raw).hexdigest()
        labs = labels_of(p)
        groups[digest].append(
            {
                "abs": p,
                "rel": rel,
                "sha256": digest,
                "size": len(raw),
                "encoding": read_text_lossy(p)[1],
                "n_rows": len(labs),
                "labels": labs,
                "score": name_content_score(p.stem, labs),
            }
        )

    imported: list[dict] = []
    skipped: list[dict] = []
    alias_map: dict[str, list[str]] = {}

    for digest, group in sorted(groups.items()):
        keeper = pick_keeper(group)
        dest = RAW / keeper["rel"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(keeper["abs"], dest)

        aliases = sorted(g["rel"] for g in group if g["rel"] != keeper["rel"])
        if aliases:
            alias_map[keeper["rel"]] = aliases

        imported.append(
            {
                "rel": keeper["rel"],
                "sha256": digest,
                "size": keeper["size"],
                "encoding": keeper["encoding"],
                "n_rows": keeper["n_rows"],
                "name_content_score": keeper["score"],
                "aliases": aliases,
            }
        )
        for g in group:
            if g["rel"] != keeper["rel"]:
                skipped.append(
                    {
                        "rel": g["rel"],
                        "reason": "duplicate_content",
                        "dup_of": keeper["rel"],
                        "sha256": digest,
                        "name_content_score": g["score"],
                    }
                )
        # 组内多份不同命名时，打印一下择优理由，便于人工复核
        if len(group) > 1:
            print(f"  去重组 sha256={digest[:12]} 保留 <{keeper['rel']}> (score={keeper['score']})")
            for g in group:
                if g["rel"] != keeper["rel"]:
                    print(f"      - 丢弃 <{g['rel']}> (score={g['score']})")

    for rel in empty:
        skipped.append({"rel": rel, "reason": "empty_file"})

    manifest = {
        "source": str(source),
        "raw_dir": str(RAW),
        "imported_count": len(imported),
        "skipped_count": len(skipped),
        "empty_count": len(empty),
        "imported": imported,
        "skipped": sorted(skipped, key=lambda x: x["rel"]),
        "alias_map": alias_map,
    }
    (RAW / "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    print(f"源文件      : {len(files)}")
    print(f"导入 raw/   : {len(imported)}")
    print(f"跳过(重复)  : {len(skipped) - len(empty)}")
    print(f"跳过(空文件): {len(empty)}")
    print(f"清单        : {RAW / '_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
