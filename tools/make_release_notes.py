#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_release_notes.py —— 从 VERSION + CHANGELOG 生成 Release 说明
====================================================================

为什么单独一个脚本，而不是写在 workflow 的 shell 里
--------------------------------------------------
最初这段是嵌在 `release.yml` 的 PowerShell heredoc 里的，结果 YAML 的块标量
和 PowerShell 的 `@"..."@` 抢缩进，直接把 workflow 弄成非法 YAML。
嵌套引号/缩进这一类问题这个项目里已经踩过三次了（argparse 的 help、
git commit 的消息、现在这个），所以规矩定下来：**workflow 只做编排，
任何有逻辑的东西都放进能被本地单独跑通的脚本**。

用法
----
    python tools/make_release_notes.py --out release-notes.md
    python tools/make_release_notes.py --version 0.1.0 --print
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
VERSION_FILE = PROJECT / "VERSION"
CHANGELOG = PROJECT / "CHANGELOG.md"

ASSETS = """## 下载

| 文件 | 说明 |
|---|---|
| `yysls-webui-{tag}.zip` | **WebUI 静态站**，解压后双击 `index.html` 即可用（不需要装 Node） |
| `yysls-rpa-toolkit-{tag}.zip` | **采集工具包**：Python 脚本 + 数据 + 文档（需要 Python 3.10+ 与 numpy） |

> 部署在线的版本：<https://anonymous99-Rise.github.io/yysls-coordinate-webui/>

## 更新内容
"""

FOOTER = """---

原始坐标由 **Hermit**、**小狼** 等人无偿分享，本仓库只做格式整编。
沿用原作者的要求：**免费分享请勿倒卖，转发请标注原作者。**
"""


def read_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def changelog_section(version: str) -> str:
    """从 CHANGELOG 里抠出某个版本的段落（到下一个 `## v` 之前）。"""
    if not CHANGELOG.exists():
        return ""
    text = CHANGELOG.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if re.match(rf"^##\s+v{re.escape(version)}\b", ln):
            start = i
            break
    if start is None:
        return ""
    out = []
    for ln in lines[start + 1:]:
        if re.match(r"^##\s+v", ln):
            break
        out.append(ln)
    return "\n".join(out).strip()


def build(version: str) -> str:
    tag = f"v{version}"
    body = changelog_section(version)
    parts = [ASSETS.format(tag=tag), "", body or "（CHANGELOG 里没有这个版本的段落）", "", FOOTER]
    return "\n".join(parts).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="", help="不给就用 VERSION 文件里的")
    ap.add_argument("--out", default="release-notes.md")
    ap.add_argument("--print", dest="do_print", action="store_true")
    args = ap.parse_args()

    version = args.version or read_version()
    notes = build(version)

    if args.do_print:
        sys.stdout.write(notes)
        return 0

    out = Path(args.out)
    if not out.is_absolute():
        out = PROJECT / out
    out.write_text(notes, encoding="utf-8")
    print(f"已生成 v{version} 的发布说明：{out}（{len(notes)} 字符）")
    if "（CHANGELOG 里没有这个版本的段落）" in notes:
        print("[!] CHANGELOG 里没找到这个版本，说明里是占位文字 —— 检查版本号是否写错。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
