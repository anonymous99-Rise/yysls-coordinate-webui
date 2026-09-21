#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collector.py —— 剪贴板坐标采集器（纯标准库，不依赖影刀也能跑）
==============================================================

用途
----
把「记录当前坐标」这个动作变成一条流水线：
    游戏里按一下记录热键 → 坐标进剪贴板 → 本脚本轮询剪贴板 → 解析 → 去重 → 追加到文件

为什么要有这个「不依赖影刀」的版本
--------------------------------
影刀 RPA 是编排器（管窗口、发按键、调度循环），但「剪贴板 → 坐标 → 落盘」
这段纯逻辑没必要绑在影刀里。单独一个脚本有三个好处：
  1. 影刀没装 / 装不上 / 版本抽风时，照样能采集
  2. 这段逻辑可以脱离 GUI 自动测试（见 tools/test_rpa.py）
  3. 影刀流程里只需要「发键 + 调这一行命令」，流程更短更稳

用法
----
    # 一直盯着剪贴板，坐标一变就记一条（Ctrl+C 停止）
    python rpa/collector.py

    # 指定输出文件和轮询间隔（毫秒）
    python rpa/collector.py --out raw/rpa/manual/collected.ini --interval 300

    # 只解析一次剪贴板里的内容，打印结果（调试用）
    python rpa/collector.py --once --verbose

    # 直接解析一段文本（不碰剪贴板）
    python rpa/collector.py --text "-2400.5,700,-60,我家门口"

落盘格式
--------
`x,y,z,名称` —— 与原始库一致，也是 etl.py 能直接吃进管线的格式。
同时写一份 `session.json` 记录本次会话的元信息。

去重策略
--------
同一个坐标重复按记录键（比如原地站着没动）默认不重复写；
文件内容里「同一行连续出现 N 次」在原始库里是刻意的刷怪循环写法，
所以这里只在**同一秒内完全相同**时去重，避免误伤。
"""

from __future__ import annotations

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

import argparse
import ctypes
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from ctypes import wintypes

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent

# ---------------------------------------------------------------------------
# 坐标解析（与 tools/lib_parse.py 同规则；rpa 目录要能独立分发，故此处自包含）
# ---------------------------------------------------------------------------
NUM = r"-?\d+(?:\.\d+)?"

# 一行里出现「3 个逗号分隔的数字 + 名称」
_RE_CSV = re.compile(rf"^\s*({NUM})\s*,\s*({NUM})\s*,\s*({NUM})\s*,\s*(.+?)\s*$")
# 只有 x,y,z
_RE_XYZ = re.compile(rf"^\s*({NUM})\s*,\s*({NUM})\s*,\s*({NUM})\s*$")
# 空格分隔
_RE_SPACE = re.compile(rf"^\s*({NUM})\s+({NUM})\s+({NUM})\s*(.*)$")
# 从任意文本里兜底捞「(58.9, -695.1, 22)」这类带括号的三元组
_RE_PAREN = re.compile(rf"[（(]\s*({NUM})\s*[,，]\s*({NUM})\s*[,，]\s*({NUM})\s*[)）]")


def parse_coord_line(line: str) -> tuple[float, float, float, str] | None:
    """把一行文本解析成 (x, y, z, name)；解析不出来返回 None。"""
    line = line.replace("\ufeff", "").strip()
    if not line or line.startswith("#") or line.startswith("["):
        return None

    for rx, has_name in ((_RE_CSV, True), (_RE_XYZ, False), (_RE_SPACE, True)):
        m = rx.match(line)
        if m:
            x, y, z = float(m.group(1)), float(m.group(2)), float(m.group(3))
            name = (m.group(4) if has_name and m.lastindex and m.lastindex >= 4 else "").strip()
            name = name.strip(", ").strip()
            return x, y, z, name

    m = _RE_PAREN.search(line)
    if m:
        return float(m.group(1)), float(m.group(2)), float(m.group(3)), ""

    return None


def parse_text(text: str) -> list[tuple[float, float, float, str]]:
    """把一段多行文本解析成坐标列表（保持原顺序，去掉完全重复的行）。"""
    out: list[tuple[float, float, float, str]] = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        got = parse_coord_line(line)
        if got:
            out.append(got)
    return out


def fmt(t: tuple[float, float, float, str]) -> str:
    """格式化成原始库风格的一行。数值去掉多余小数尾巴。"""
    def n(v: float) -> str:
        s = f"{v:.4f}".rstrip("0").rstrip(".")
        return "0" if s in ("", "-0") else s
    x, y, z, name = t
    return f"{n(x)},{n(y)},{n(z)},{name or '未备注地点'}"


# ---------------------------------------------------------------------------
# 剪贴板（纯 ctypes，不装 pyperclip）
# ---------------------------------------------------------------------------
CF_UNICODETEXT = 13

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.argtypes = []
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
_user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = wintypes.HANDLE
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = wintypes.LPVOID
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalUnlock.restype = wintypes.BOOL


def get_clipboard_text() -> str:
    """读剪贴板文本；剪贴板被占用或没有文本时返回空串（不抛异常）。"""
    if not _user32.OpenClipboard(None):
        return ""
    try:
        if not _user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return ""
        handle = _user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = _kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.c_wchar_p(ptr).value or ""
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32.CloseClipboard()


def set_clipboard_text(text: str) -> bool:
    """写剪贴板文本（用来写哨兵值，判断游戏有没有真的把坐标写进去）。"""
    GMEM_MOVEABLE = 0x0002
    _kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    _kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    _kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    _user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]

    buf = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(buf)
    handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
    if not handle:
        return False
    ptr = _kernel32.GlobalLock(handle)
    if not ptr:
        _kernel32.GlobalFree(handle)
        return False
    ctypes.memmove(ptr, buf, size)
    _kernel32.GlobalUnlock(handle)

    if not _user32.OpenClipboard(None):
        _kernel32.GlobalFree(handle)
        return False
    try:
        _user32.EmptyClipboard()
        if not _user32.SetClipboardData(CF_UNICODETEXT, handle):
            _kernel32.GlobalFree(handle)
            return False
    finally:
        _user32.CloseClipboard()
    return True


# ---------------------------------------------------------------------------
# 采集会话
# ---------------------------------------------------------------------------
class Session:
    def __init__(self, out: Path, *, note: str = "") -> None:
        self.out = out
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.out.parent / "session.json"
        self.seen: set[str] = set()          # 已写入的行（精确去重）
        self.stats = {
            "startedAt": datetime.now().isoformat(timespec="seconds"),
            "source": "clipboard",
            "note": note,
            "polled": 0,
            "clipboardHits": 0,
            "parsedRows": 0,
            "written": 0,
            "duplicates": 0,
            "unparsable": 0,
            "unparsableSamples": [],
        }
        # 续写既有文件时，先把已有行读进去，避免同一次采集重复
        if self.out.exists():
            for line in self.out.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.seen.add(line.strip())

    def add(self, rows: list[tuple[float, float, float, str]]) -> int:
        """写入若干坐标，返回真正新增的行数。"""
        if not rows:
            return 0
        new_lines: list[str] = []
        for r in rows:
            line = fmt(r)
            self.stats["parsedRows"] += 1
            if line in self.seen:
                self.stats["duplicates"] += 1
                continue
            self.seen.add(line)
            new_lines.append(line)
        if new_lines:
            with self.out.open("a", encoding="utf-8") as f:
                f.write("\n".join(new_lines) + "\n")
            self.stats["written"] += len(new_lines)
            self.log(f"+{len(new_lines)}  {new_lines[-1]}")
        return len(new_lines)

    def bump_unparsable(self, text: str) -> None:
        self.stats["unparsable"] += 1
        if len(self.stats["unparsableSamples"]) < 10 and text:
            self.stats["unparsableSamples"].append(text[:120])

    def log(self, msg: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        with (self.out.parent / "log.txt").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def close(self) -> None:
        self.stats["finishedAt"] = datetime.now().isoformat(timespec="seconds")
        self.meta_path.write_text(
            json.dumps(self.stats, ensure_ascii=False, indent=2), encoding="utf-8"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    # 坐标以 '-' 开头时，argparse 会把值当成新选项；统一改写成 --opt=值
    _VALUE_OPTS = ("--text", "--out")
    argv = sys.argv[1:]
    norm: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in _VALUE_OPTS and i + 1 < len(argv) and argv[i + 1].startswith("-"):
            norm.append(f"{a}={argv[i + 1]}")
            i += 2
            continue
        norm.append(a)
        i += 1

    ap = argparse.ArgumentParser(description="剪贴板坐标采集器")
    ap.add_argument("--out", default="", help="输出文件（默认 raw/rpa/manual_<时间戳>/collected.ini）")
    ap.add_argument("--interval", type=int, default=250, help="轮询间隔毫秒（默认 250）")
    ap.add_argument("--once", action="store_true", help="只读一次剪贴板就退出")
    ap.add_argument("--text", default="", help="不读剪贴板，直接解析这段文本")
    ap.add_argument("--duration", type=int, default=0, help="采集多少秒后自动停止（0=一直跑）")
    ap.add_argument("--verbose", action="store_true", help="把每次读到的剪贴板内容打出来")
    args = ap.parse_args(norm)

    # --text：纯解析模式，方便管道/测试
    if args.text:
        for r in parse_text(args.text):
            print(fmt(r))
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out) if args.out else PROJECT / "raw" / "rpa" / f"manual_{stamp}" / "collected.ini"
    if not out.is_absolute():
        out = PROJECT / out

    if args.once:
        text = get_clipboard_text()
        rows = parse_text(text)
        print(f"剪贴板内容：{text[:300]!r}")
        print(f"解析出 {len(rows)} 条：")
        for r in rows:
            print("  " + fmt(r))
        return 0

    sess = Session(out, note="clipboard collect")
    sess.log(f"开始采集 → {out}（轮询 {args.interval}ms）")
    sess.log("在游戏里按坐标记录热键，坐标会自己进来。Ctrl+C 结束。")

    last = ""
    t0 = time.time()
    try:
        while True:
            sess.stats["polled"] += 1
            text = get_clipboard_text()
            if text and text != last:
                last = text
                if args.verbose:
                    sess.log(f"剪贴板变了：{text[:120]!r}")
                rows = parse_text(text)
                if rows:
                    sess.stats["clipboardHits"] += 1
                    sess.add(rows)
                else:
                    sess.bump_unparsable(text)
            if args.duration and time.time() - t0 >= args.duration:
                break
            time.sleep(max(0.05, args.interval / 1000))
    except KeyboardInterrupt:
        sess.log("收到 Ctrl+C，收工")
    finally:
        sess.close()

    s = sess.stats
    print()
    print(f"轮询 {s['polled']} 次 · 剪贴板命中 {s['clipboardHits']} 次")
    print(f"解析 {s['parsedRows']} 条 · 写入 {s['written']} 条 · 去重跳过 {s['duplicates']} 条 · 无法解析 {s['unparsable']} 次")
    print(f"输出：{sess.out}")
    print(f"元信息：{sess.meta_path}")
    return 0


if __name__ == "__main__":
    if sys.platform != "win32":
        print("本脚本依赖 Windows 剪贴板 API，只能在 Windows 上跑。")
        raise SystemExit(2)
    raise SystemExit(main())
