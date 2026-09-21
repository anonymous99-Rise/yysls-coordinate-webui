# -*- coding: utf-8 -*-
"""
02_parse_clipboard.py —— 把剪贴板/OCR 里的坐标文本解析并追加到采集文件
========================================================================

这是整条采集链上最核心的一段，影刀方案 A（剪贴板桥接）和方案 B（屏幕 OCR）都用它。

影刀输入变量：
    {"clip_text": ${剪贴板文本}, "out_file": ${输出文件}, "name_hint": ""}

影刀输出变量：
    ${结果}        —— 字典 {"added": 新增行数, "duplicate": 重复跳过数, "rows": 解析条数, "last": 最后一行}
    ${是否新增}    —— 布尔，影刀用它决定要不要在日志里记一笔

支持的输入格式（比你想象的杂，所以正则写得宽容）：
    -2400.5,700,-60,我家门口          ← 标准 4 段
    -2400.5 ,700 ,-60 ,我家门口       ← 带空格
    -2400.5,700,-60                   ← 只有 3 段
    -2400.5 700 -60 我家门口          ← 空格分隔
    (-2400.5, 700, -60)               ← 带括号
    坐标：X=-2400.5 Y=700 Z=-60       ← 从任意文本里捞

单独跑：
    python 02_parse_clipboard.py --text "-2400.5,700,-60,测试" --out /tmp/x.ini
"""

# >>> utf8-guard >>>
# Windows 上往管道/重定向的 stdout 打中文会 UnicodeEncodeError 崩掉
# （Python 默认用系统 ANSI 代码页而不是 UTF-8）。不指望调用方设
# PYTHONIOENCODING —— 脚本自己保证输出编码。
# 自带 import 是刻意的：位置无关，也不依赖文件里其他 import 的先后。
import sys as _sys

for _stream in (_sys.stdout, _sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
# <<< utf8-guard <<<

import json
import os
import re

NUM = r"-?\d+(?:\.\d+)?"

_RE_CSV = re.compile(r"^\s*(" + NUM + r")\s*,\s*(" + NUM + r")\s*,\s*(" + NUM + r")\s*,\s*(.+?)\s*$")
_RE_XYZ = re.compile(r"^\s*(" + NUM + r")\s*,\s*(" + NUM + r")\s*,\s*(" + NUM + r")\s*$")
_RE_SPACE = re.compile(r"^\s*(" + NUM + r")\s+(" + NUM + r")\s+(" + NUM + r")\s*(.*)$")
_RE_PAREN = re.compile(
    r"[（(]\s*(" + NUM + r")\s*[,，]\s*(" + NUM + r")\s*[,，]\s*(" + NUM + r")\s*[)）]"
)


def parse_coord_line(line):
    """一行文本 → (x, y, z, name) 或 None。"""
    line = (line or "").replace("\ufeff", "").strip()
    if not line or line.startswith("#") or line.startswith("["):
        return None
    for rx, has_name in ((_RE_CSV, True), (_RE_XYZ, False), (_RE_SPACE, True)):
        m = rx.match(line)
        if m:
            x, y, z = float(m.group(1)), float(m.group(2)), float(m.group(3))
            name = ""
            if has_name and (m.lastindex or 0) >= 4:
                name = m.group(4).strip().strip(",").strip()
            return x, y, z, name
    m = _RE_PAREN.search(line)
    if m:
        return float(m.group(1)), float(m.group(2)), float(m.group(3)), ""
    return None


def parse_text(text, name_hint=""):
    out = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        got = parse_coord_line(line)
        if got:
            x, y, z, name = got
            out.append((x, y, z, name or name_hint))
    return out


def _fmt_num(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def format_row(row):
    x, y, z, name = row
    return f"{_fmt_num(x)},{_fmt_num(y)},{_fmt_num(z)},{name or '未备注地点'}"


def append_rows(out_file, rows):
    """追加写盘 + 精确去重（同一行在文件里已存在就跳过）。"""
    out_file = os.path.abspath(out_file)
    os.makedirs(os.path.dirname(out_file), exist_ok=True)

    seen = set()
    if os.path.exists(out_file):
        with open(out_file, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    seen.add(ln)

    new_lines = []
    duplicates = 0
    for r in rows:
        line = format_row(r)
        if line in seen:
            duplicates += 1
            continue
        seen.add(line)
        new_lines.append(line)

    if new_lines:
        with open(out_file, "a", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(new_lines) + "\n")

    return {
        "added": len(new_lines),
        "duplicate": duplicates,
        "rows": len(rows),
        "last": new_lines[-1] if new_lines else "",
        "outFile": out_file,
    }


def run(clip_text, out_file, name_hint=""):
    rows = parse_text(clip_text, name_hint)
    if not rows:
        return {"added": 0, "duplicate": 0, "rows": 0, "last": "", "outFile": out_file,
                "unparsable": (clip_text or "")[:120]}
    return append_rows(out_file, rows)


# ---- 影刀调用入口 ---------------------------------------------------------
_clip = globals().get("clip_text", None)
_out = globals().get("out_file", None)
if _clip is not None and _out:
    结果 = run(_clip, _out, globals().get("name_hint", ""))
    是否新增 = 结果.get("added", 0) > 0


# ---- 命令行入口 -----------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import sys

    # 坐标以 '-' 开头会被 argparse 当成选项，改写成 --opt=值 的形式
    _VALUE_OPTS = ("--text", "--out", "--name-hint")
    _argv = sys.argv[1:]
    _norm = []
    _i = 0
    while _i < len(_argv):
        _a = _argv[_i]
        if _a in _VALUE_OPTS and _i + 1 < len(_argv) and _argv[_i + 1].startswith("-"):
            _norm.append(f"{_a}={_argv[_i + 1]}")
            _i += 2
            continue
        _norm.append(_a)
        _i += 1

    ap = argparse.ArgumentParser()
    ap.add_argument("--text", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--name-hint", default="")
    a = ap.parse_args(_norm)
    print(json.dumps(run(a.text, a.out, a.name_hint), ensure_ascii=False))
