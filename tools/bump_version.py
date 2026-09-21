#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bump_version.py —— 版本号提升 + 打标签 + 生成 CHANGELOG 条目
================================================================

`VERSION` 是**唯一真源**。这个脚本负责：改 VERSION、同步 web/package.json、
往 CHANGELOG.md 插一条、提交、打 tag。推上去之后 GitHub Actions 会自动建 Release。

用法
----
    python tools/bump_version.py patch            # 0.1.0 -> 0.1.1
    python tools/bump_version.py minor            # 0.1.0 -> 0.2.0
    python tools/bump_version.py major            # 0.1.0 -> 1.0.0
    python tools/bump_version.py 0.2.0            # 直接指定
    python tools/bump_version.py patch --dry-run  # 只看会改什么，不动文件
    python tools/bump_version.py patch --no-tag   # 只提交不打标签
    python tools/bump_version.py patch --notes "修了标定的边界情况"

设计上的几个取舍
----------------
* **不做完推送**：提交和打标签是本地动作，推送交给你（或 CI）。脚本只提示下一步命令。
  自动 push 太容易在没想清楚的时候把东西推出去。
* **CHANGELOG 用固定四段**：新增 / 变更 / 修复 / 文档。段名固定，方便以后脚本化汇总；
  没内容的段落删掉，不留空标题。
* **校验工作区干净**：有未提交改动时拒绝执行。否则那条 release 提交会把无关改动裹进去，
  以后想 revert 一个版本就得连带 revert 别的东西。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
VERSION_FILE = PROJECT / "VERSION"
CHANGELOG = PROJECT / "CHANGELOG.md"
PKG_JSON = PROJECT / "web" / "package.json"

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def run(cmd: list[str], *, check: bool = True, capture: bool = True) -> str:
    r = subprocess.run(cmd, cwd=PROJECT, capture_output=capture, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise SystemExit(f"[x] 命令失败：{' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return (r.stdout or "") + (r.stderr or "")


def read_version() -> str:
    if not VERSION_FILE.exists():
        raise SystemExit(f"[x] 找不到 {VERSION_FILE}")
    v = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not SEMVER.match(v):
        raise SystemExit(f"[x] VERSION 内容不是 semver：{v!r}")
    return v


def bump(current: str, part: str) -> str:
    if SEMVER.match(part):
        return part
    m = SEMVER.match(current)
    if not m:
        raise SystemExit(f"[x] 当前版本不是 semver：{current!r}")
    major, minor, patch = (int(x) for x in m.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise SystemExit(f"[x] 不认识的自增类型：{part}（可选 major/minor/patch 或直接给 x.y.z）")


def ensure_clean(dry: bool) -> None:
    """拒绝在脏工作区上做 release —— 否则那条提交会裹进无关改动。"""
    out = run(["git", "status", "--porcelain"])
    dirty = [ln for ln in out.splitlines() if ln.strip()]
    if dirty:
        print("[!] 工作区有未提交的改动：")
        for ln in dirty[:15]:
            print(f"      {ln}")
        if len(dirty) > 15:
            print(f"      …还有 {len(dirty) - 15} 条")
        raise SystemExit("[x] 先把它们提交或 stash 掉，再执行版本提升。")
    print("  ✓ 工作区干净")


def sync_package_json(new: str, dry: bool) -> bool:
    if not PKG_JSON.exists():
        return False
    doc = json.loads(PKG_JSON.read_text(encoding="utf-8"))
    old = doc.get("version")
    if old == new:
        return False
    doc["version"] = new
    if not dry:
        PKG_JSON.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  web/package.json  version: {old} -> {new}")
    return True


def update_changelog(new: str, notes: str, dry: bool) -> None:
    today = date.today().isoformat()
    entry = [f"## v{new} — {today}", ""]
    if notes:
        entry += [notes, ""]
    else:
        entry += [
            "### 新增",
            "",
            "- （在这里写）",
            "",
            "### 变更",
            "",
            "- （在这里写）",
            "",
            "### 修复",
            "",
            "- （在这里写）",
            "",
        ]
    block = "\n".join(entry) + "\n"

    if CHANGELOG.exists():
        text = CHANGELOG.read_text(encoding="utf-8")
        # 插在第一个 ## 之前（最新的在最上面）
        m = re.search(r"^## ", text, re.M)
        if m:
            text = text[: m.start()] + block + text[m.start():]
        else:
            text = text.rstrip() + "\n\n" + block
    else:
        text = "# 更新日志\n\n本文件由 `tools/bump_version.py` 维护。格式遵循 Keep a Changelog 的思路，\n版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。\n\n" + block
    if not dry:
        CHANGELOG.write_text(text, encoding="utf-8")
    print(f"  CHANGELOG.md 已插入 v{new} 条目" + ("（模板待填）" if not notes else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="提升版本号并打标签")
    ap.add_argument("part", help="major / minor / patch，或直接给 x.y.z")
    ap.add_argument("--notes", default="", help="CHANGELOG 里这条的描述（不填则生成待填模板）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将做什么，不改文件不提交")
    ap.add_argument("--no-tag", action="store_true", help="只提交不打标签")
    ap.add_argument("--no-commit", action="store_true", help="只改文件，不提交")
    args = ap.parse_args()

    current = read_version()
    new = bump(current, args.part)
    if new == current:
        raise SystemExit(f"[x] 新版本和当前一样：{current}")

    print(f"== 版本提升 {current} -> {new}" + ("（dry-run）" if args.dry_run else "") + " ==")
    if not args.dry_run:
        ensure_clean(False)

    if not args.dry_run:
        VERSION_FILE.write_text(new + "\n", encoding="utf-8")
    print(f"  VERSION: {current} -> {new}")
    sync_package_json(new, args.dry_run)
    update_changelog(new, args.notes, args.dry_run)

    if args.dry_run or args.no_commit:
        print("\n（未提交）")
        return 0

    run(["git", "add", "-A"])
    run(["git", "commit", "-q", "-m", f"chore(release): v{new}"])
    print(f"  ✓ 已提交：chore(release): v{new}")

    if not args.no_tag:
        run(["git", "tag", "-a", f"v{new}", "-m", f"v{new}"])
        print(f"  ✓ 已打标签：v{new}")

    print("\n下一步（脚本刻意不代劳，推送前自己看一眼）：")
    print("  git push origin HEAD")
    if not args.no_tag:
        print(f"  git push origin v{new}     # 这一步会触发 Release 工作流")
    print("\n标签推上去后，GitHub Actions 会跑测试、打包、建 Release。")
    print("查看：gh run watch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
