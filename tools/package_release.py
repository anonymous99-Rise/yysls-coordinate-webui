#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
package_release.py —— 打两个发布包
====================================

产出两个 zip，对应两类使用者：

  yysls-webui-<tag>.zip        静态站，解压双击 index.html 就能用，不需要装任何东西
  yysls-rpa-toolkit-<tag>.zip  采集工具包，Python 脚本 + 数据 + 文档，需要 Python 与 numpy

分开放是刻意的：只想查坐标的人不该被迫下一个装 Python 才能用的包。

用法
----
    python tools/package_release.py --tag v0.1.0
    python tools/package_release.py --tag v0.1.0 --dry-run    # 只列出会打进什么
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
import shutil
import sys
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent

# 进工具包的东西（顺序无所谓，只是清单）
TOOLKIT_DIRS = ["rpa", "tools", "data"]
TOOLKIT_FILES = ["README.md", "CHANGELOG.md", "VERSION"]

# 明确排除的：缓存、测试脚本的临时产物、以及不该跟着发布的东西
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", "_selftest", "_navselftest"}
EXCLUDE_SUFFIX = {".pyc", ".pyo"}
EXCLUDE_NAMES = {
    # 自测/CI 用的，发布包里没意义
    "test_core.mjs",
}
# data/ 里这几个是中间产物，体积不小且可从 points.json 重新生成
EXCLUDE_RELPATHS = {
    "data/_unclassified.txt",
}


def iter_files(root: Path) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        if p.suffix in EXCLUDE_SUFFIX or p.name in EXCLUDE_NAMES:
            continue
        if str(rel).replace("\\", "/") in EXCLUDE_RELPATHS:
            continue
        # 采集临时产物
        if rel.parts[0] == "raw":
            continue
        out.append(p)
    return out


def zip_dir(src: Path, dest: Path, arc_prefix: str = "") -> tuple[int, int]:
    files = iter_files(src)
    total = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in files:
            arc = str(f.relative_to(src))
            if arc_prefix:
                arc = f"{arc_prefix}/{arc}"
            z.write(f, arc)
            total += f.stat().st_size
    return len(files), total


def make_webui(tag: str, dry: bool) -> tuple[Path, int, int]:
    dist = PROJECT / "dist"
    if not dist.exists():
        raise SystemExit("[x] 没有 dist/；先跑 cd web && npm run build")
    dest = PROJECT / f"yysls-webui-{tag}.zip"
    if dry:
        n = len(iter_files(dist))
        return dest, n, sum(f.stat().st_size for f in iter_files(dist))
    dest.unlink(missing_ok=True)
    return (dest, *zip_dir(dist, dest))


def make_toolkit(tag: str, dry: bool) -> tuple[Path, int, int]:
    dest = PROJECT / f"yysls-rpa-toolkit-{tag}.zip"
    stage = PROJECT / "pack" / f"rpa-toolkit-{tag}"
    if dry:
        n = sum(len(iter_files(PROJECT / d)) for d in TOOLKIT_DIRS) + len(TOOLKIT_FILES)
        return dest, n, 0
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    for d in TOOLKIT_DIRS:
        src = PROJECT / d
        if not src.exists():
            print(f"    [!] 跳过不存在的目录 {d}")
            continue
        for f in iter_files(src):
            rel = f.relative_to(src)
            out = stage / d / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, out)
    for f in TOOLKIT_FILES:
        src = PROJECT / f
        if src.exists():
            shutil.copy2(src, stage / f)

    dest.unlink(missing_ok=True)
    n, size = zip_dir(stage, dest)
    shutil.rmtree(stage.parent, ignore_errors=True)
    return dest, n, size


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", choices=["webui", "toolkit"], default=None)
    args = ap.parse_args()

    print(f"== 打包 {args.tag}" + ("（dry-run）" if args.dry_run else "") + " ==")
    results = []
    if args.only != "toolkit":
        results.append(("WebUI 静态站", make_webui(args.tag, args.dry_run)))
    if args.only != "webui":
        results.append(("采集工具包", make_toolkit(args.tag, args.dry_run)))

    for label, (path, n, size) in results:
        rel = path.relative_to(PROJECT) if path.is_absolute() else path
        mb = f"{size / 1024 / 1024:.2f} MB" if size else "—"
        print(f"  {label:<14} {n:>4} 个文件  {mb:>10}  ->  {rel}")
    if args.dry_run:
        print("\n（dry-run，没有真的写文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
