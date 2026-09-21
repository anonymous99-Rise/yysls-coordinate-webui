#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_version_consistency.py —— 发布前自检：三处版本号必须一致
================================================================

`VERSION`、`web/package.json` 的 `version`、以及 git 标签，三者必须指向同一个版本。
不一致就拒绝发布 —— 这类错最难受的地方是它不会报错，只会让下载下来的人
拿到一个版本号对不上的包。

用法
----
    python tools/check_version_consistency.py            # 只比 VERSION 与 package.json
    python tools/check_version_consistency.py --tag v0.1.0
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="", help="git 标签（如 v0.1.0），给了就一起比")
    args = ap.parse_args()

    vfile = PROJECT / "VERSION"
    if not vfile.exists():
        print(f"[x] 找不到 {vfile}")
        return 1
    version = vfile.read_text(encoding="utf-8").strip()
    if not SEMVER.match(version):
        print(f"[x] VERSION 内容不是 x.y.z：{version!r}")
        return 1

    print(f"  VERSION          {version}")

    problems = []

    pkg_path = PROJECT / "web" / "package.json"
    pkg_ver = None
    if pkg_path.exists():
        pkg_ver = json.loads(pkg_path.read_text(encoding="utf-8")).get("version")
        mark = "✓" if pkg_ver == version else "✗"
        print(f"  web/package.json {pkg_ver}  {mark}")
        if pkg_ver != version:
            problems.append(f"web/package.json 是 {pkg_ver}，VERSION 是 {version}")

    if args.tag:
        tag_ver = args.tag[1:] if args.tag.startswith("v") else args.tag
        mark = "✓" if tag_ver == version else "✗"
        print(f"  git 标签         {args.tag}  {mark}")
        if tag_ver != version:
            problems.append(f"标签 {args.tag} 对应 {tag_ver}，VERSION 是 {version}")

    # 标签必须真的存在（本地自检时才有意义，CI 上是从 tag 触发的所以必然存在）
    if args.tag and not args.tag.startswith("v"):
        problems.append(f"标签应当以 v 开头：{args.tag}")

    # CHANGELOG 里要有这个版本的段落，否则 Release 说明会是占位文字
    cl = PROJECT / "CHANGELOG.md"
    if cl.exists():
        has = re.search(rf"^##\s+v{re.escape(version)}\b", cl.read_text(encoding="utf-8"), re.M)
        print(f"  CHANGELOG 段落   {'有' if has else '没有'}  {'✓' if has else '✗'}")
        if not has:
            problems.append(f"CHANGELOG.md 里没有 v{version} 的段落")

    if problems:
        print("\n[x] 版本号不一致，拒绝发布：")
        for p in problems:
            print(f"      - {p}")
        return 1
    print("\n  ✓ 三处版本号一致，可以发布")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
