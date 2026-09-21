#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
injector.py —— 往游戏里可靠地发键（DirectInput 扫描码）
======================================================

为什么需要单独一个模块
----------------------
普通模拟按键（pyautogui / `keyboard.press()`）发的是**虚拟键码（VK）**，走 Windows 消息队列。
而 DirectX / DirectInput 类型的游戏直接从设备层读状态，不经过消息队列 —— 结果就是
**按键被静默忽略，脚本看着在跑，游戏毫无反应**。

pydirectinput 的作者原话（https://github.com/learncodebygaming/pydirectinput）：

    PyAutoGUI uses Virtual Key Codes (VKs) and the deprecated mouse_event() and
    keybd_event() win32 functions. You may find that PyAutoGUI does not work in
    some applications, particularly in video games and other software that rely
    on DirectX.

本模块的做法和 pydirectinput 一致——**发 DirectInput 扫描码（KEYEVENTF_SCANCODE）**——
但用 ctypes 直接调 `SendInput`，所以：

  * **零依赖**：不装 pydirectinput 也能用（与项目「核心管线零依赖」的原则一致）
  * pydirectinput 装了的话可以用 `--backend pydirectinput` 切过去对比

另一个坑：**UIPI（用户界面特权隔离）**。低完整性级别的进程不能向高完整性级别进程注入输入。
游戏如果是管理员权限跑的，本脚本也必须是管理员，否则 SendInput 会静默失败。
所以这里带了 `--elevate` 和权限自检。

用法
----
    # 回环自检：发一个键，用自己的钩子收回来，证明注入链路真的通
    python rpa/injector.py --self-test

    # 对游戏发一串键（F 采集三下）
    python rpa/injector.py --keys f,f,f --interval 0.4

    # 不真发键，只打印将要做什么（无游戏环境下验证流程）
    python rpa/injector.py --keys f,space --dry-run

    # 全局热键驱动：F10 开始循环按 F，F12 停
    python rpa/injector.py --loop --keys f --interval 1.5 --start-key F10 --stop-key F12

    # 检查权限
    python rpa/injector.py --check
"""

from __future__ import annotations

import argparse
import ctypes
import random
import sys
import threading
import time
from ctypes import wintypes

# ---------------------------------------------------------------------------
# 键盘注入
# ---------------------------------------------------------------------------
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

# DirectInput 扫描码（set 1）。值取自 pydirectinput / Microsoft DIK 文档。
# 带 True 的是「扩展键」，需要额外置 KEYEVENTF_EXTENDEDKEY。
SCANCODES: dict[str, tuple[int, bool]] = {
    "esc": (0x01, False), "escape": (0x01, False),
    "1": (0x02, False), "2": (0x03, False), "3": (0x04, False), "4": (0x05, False),
    "5": (0x06, False), "6": (0x07, False), "7": (0x08, False), "8": (0x09, False),
    "9": (0x0A, False), "0": (0x0B, False), "-": (0x0C, False), "=": (0x0D, False),
    "backspace": (0x0E, False), "tab": (0x0F, False),
    "q": (0x10, False), "w": (0x11, False), "e": (0x12, False), "r": (0x13, False),
    "t": (0x14, False), "y": (0x15, False), "u": (0x16, False), "i": (0x17, False),
    "o": (0x18, False), "p": (0x19, False), "[": (0x1A, False), "]": (0x1B, False),
    "enter": (0x1C, False), "return": (0x1C, False), "ctrl": (0x1D, False),
    "a": (0x1E, False), "s": (0x1F, False), "d": (0x20, False), "f": (0x21, False),
    "g": (0x22, False), "h": (0x23, False), "j": (0x24, False), "k": (0x25, False),
    "l": (0x26, False), ";": (0x27, False), "'": (0x28, False), "`": (0x29, False),
    "shift": (0x2A, False), "\\": (0x2B, False),
    "z": (0x2C, False), "x": (0x2D, False), "c": (0x2E, False), "v": (0x2F, False),
    "b": (0x30, False), "n": (0x31, False), "m": (0x32, False), ",": (0x33, False),
    ".": (0x34, False), "/": (0x35, False), "alt": (0x38, False),
    "space": (0x39, False), "capslock": (0x3A, False),
    "f1": (0x3B, False), "f2": (0x3C, False), "f3": (0x3D, False), "f4": (0x3E, False),
    "f5": (0x3F, False), "f6": (0x40, False), "f7": (0x41, False), "f8": (0x42, False),
    "f9": (0x43, False), "f10": (0x44, False), "f11": (0x57, False), "f12": (0x58, False),
    "up": (0xC8, True), "down": (0xD0, True), "left": (0xCB, True), "right": (0xCD, True),
    "home": (0xC7, True), "end": (0xCF, True), "pageup": (0xC9, True), "pagedown": (0xD1, True),
    "insert": (0xD2, True), "delete": (0xD3, True),
    "numpad0": (0x52, False), "numpad1": (0x4F, False), "numpad2": (0x50, False),
    "numpad3": (0x51, False), "numpad4": (0x4B, False), "numpad5": (0x4C, False),
    "numpad6": (0x4D, False), "numpad7": (0x47, False), "numpad8": (0x48, False),
    "numpad9": (0x49, False),
}

# 虚拟键码（给 GetAsyncKeyState 做热键轮询用）
VK_CODES: dict[str, int] = {
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "esc": 0x1B, "space": 0x20, "enter": 0x0D, "tab": 0x09,
}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("padding", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010

WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
MK_LBUTTON, MK_RBUTTON = 0x0001, 0x0002

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = wintypes.SHORT
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.SetCursorPos.restype = wintypes.BOOL
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.PostMessageW.restype = wintypes.BOOL
_user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_user32.FindWindowW.restype = wintypes.HWND
_user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL


def _send(scancode: int, extended: bool, keyup: bool) -> bool:
    flags = KEYEVENTF_SCANCODE
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if keyup:
        flags |= KEYEVENTF_KEYUP
    inp = INPUT(
        type=INPUT_KEYBOARD,
        u=_INPUTUNION(ki=KEYBDINPUT(wVk=0, wScan=scancode, dwFlags=flags, time=0, dwExtraInfo=None)),
    )
    return _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) == 1


def normalize_key(key: str) -> str:
    k = (key or "").strip().lower()
    alias = {"esc": "esc", "return": "enter", " ": "space", "lshift": "shift", "lctrl": "ctrl"}
    return alias.get(k, k)


def press_key(key: str, duration: float = 0.06, *, dry_run: bool = False) -> bool:
    """发一次「按下 → 停 duration → 抬起」。返回是否成功。

    关键是 SCANCODE 而不是 VK —— 这是能不能被 DirectX 游戏认到的分水岭。
    """
    k = normalize_key(key)
    entry = SCANCODES.get(k)
    if entry is None:
        raise ValueError(f"不认识的按键名：{key!r}（可用键见 SCANCODES，或直接在命令行 --keys 里试）")
    scancode, extended = entry
    if dry_run:
        print(f"    [dry-run] 按下 {k} (scancode 0x{scancode:02X}) 持续 {duration}s")
        return True
    ok_down = _send(scancode, extended, keyup=False)
    time.sleep(duration)
    ok_up = _send(scancode, extended, keyup=True)
    return ok_down and ok_up


def send_keys(keys: list[str], interval: float = 0.3, duration: float = 0.06, *, dry_run: bool = False) -> int:
    sent = 0
    for i, k in enumerate(keys):
        try:
            if press_key(k, duration, dry_run=dry_run):
                sent += 1
        except ValueError as e:
            print(f"    [跳过] {e}")
        if i < len(keys) - 1:
            time.sleep(interval)
    return sent


# ---------------------------------------------------------------------------
# 鼠标
# ---------------------------------------------------------------------------
def _send_mouse(dx: int, dy: int, flags: int) -> bool:
    inp = INPUT(type=INPUT_MOUSE,
                u=_INPUTUNION(mi=MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=flags,
                                            time=0, dwExtraInfo=None)))
    return _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) == 1


def click_at(x: int, y: int, *, button: str = "left", duration: float = 0.06,
             dry_run: bool = False) -> bool:
    """前台模式：移动真实光标到 (x, y) 再点击。要求目标窗口在前台。"""
    down = MOUSEEVENTF_LEFTDOWN if button == "left" else MOUSEEVENTF_RIGHTDOWN
    up = MOUSEEVENTF_LEFTUP if button == "left" else MOUSEEVENTF_RIGHTUP
    if dry_run:
        print(f"    [dry-run] 前台点击 ({x}, {y}) {button}")
        return True
    _user32.SetCursorPos(int(x), int(y))
    time.sleep(duration)
    ok1 = _send_mouse(0, 0, down)
    time.sleep(duration)
    ok2 = _send_mouse(0, 0, up)
    return ok1 and ok2


def click_background(hwnd: int, x: int, y: int, *, button: str = "left",
                     duration: float = 0.06, dry_run: bool = False) -> bool:
    """后台模式：往目标窗口投递鼠标消息，**不移动真实光标**，窗口可以不在前台。

    ⚠️ 局限：投递的是窗口消息（WM_LBUTTONDOWN），很多 DirectX 游戏从设备层读输入、
    不看消息队列，这类窗口会没反应。它在普通 Win32 控件型界面上最有效
    （登录器、各类工具窗口、部分棋牌/模拟器外壳）。
    先试前台模式，前台不行再试这个，别反过来。
    """
    if dry_run:
        print(f"    [dry-run] 后台点击 hwnd={hwnd} ({x}, {y}) {button}")
        return True
    pt = wintypes.POINT(int(x), int(y))
    if not _user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(pt)):
        return False
    lp = (pt.y << 16) | (pt.x & 0xFFFF)
    if button == "left":
        msg_down, msg_up, mk = WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON
    else:
        msg_down, msg_up, mk = WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON
    ok1 = _user32.PostMessageW(wintypes.HWND(hwnd), msg_down, mk, lp)
    time.sleep(duration)
    ok2 = _user32.PostMessageW(wintypes.HWND(hwnd), msg_up, 0, lp)
    return bool(ok1) and bool(ok2)


def _enum_visible_windows() -> list[tuple[int, int, str]]:
    """枚举所有「可见且有标题」的窗口，返回 [(hwnd, pid, title)]。"""
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]

    rows: list[tuple[int, int, str]] = []

    def cb(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        n = _user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        _user32.GetWindowTextW(hwnd, buf, n + 1)
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rows.append((int(hwnd), int(pid.value), buf.value))
        return True

    _user32.EnumWindows(WNDENUMPROC(cb), 0)
    return rows


def list_visible_windows() -> list[tuple[int, str]]:
    """列出所有可见且有标题的窗口 (pid, title)。找目标窗口前先跑这个看一眼。"""
    return [(pid, t) for _, pid, t in _enum_visible_windows()]


def _pid_of_process(process: str) -> int:
    """按进程名取 pid（取第一个实例）。"""
    import subprocess
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process}.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=8,
        ).stdout
    except Exception:
        return 0
    lines = [ln for ln in out.strip().splitlines() if ln.startswith('"')]
    if not lines:
        return 0
    try:
        return int(lines[0].split('","')[1])
    except (IndexError, ValueError):
        return 0


def find_window(title: str | None = None, *, process: str | None = None,
                timeout: float = 10.0) -> int:
    """按窗口标题片段 / 进程名找主窗口句柄。找到返回 hwnd，超时返回 0。

    标题匹配是**大小写不敏感的子串**匹配。注意 Windows 11 记事本的标题是
    「无标题 - Notepad」而不是「记事本」—— 找不到目标时先跑 `--list-windows` 看一眼，
    别硬猜标题。
    """
    if not title and not process:
        return 0
    title_l = (title or "").lower()
    deadline = time.time() + timeout
    while time.time() < deadline:
        target_pid = _pid_of_process(process) if process else None
        for hwnd, pid, t in _enum_visible_windows():
            if title_l and title_l not in t.lower():
                continue
            if target_pid is not None and pid != target_pid:
                continue
            return hwnd
        time.sleep(0.3)
    return 0


def get_cursor() -> tuple[int, int]:
    p = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(p))
    return int(p.x), int(p.y)


# ---------------------------------------------------------------------------
# 拟人化：延迟用区间、点击带随机偏移
# ---------------------------------------------------------------------------
from dataclasses import dataclass  # noqa: E402  (放在这里是因为只有拟人化用得到)


@dataclass
class Humanize:
    """让操作别那么"机器"。

    参数模型借自 lvjiang（PolyForm Noncommercial 1.0.0）的 InputSimConfig：
    他们的做法是把前后延迟、移动时长都做成**区间**，再叠一个像素级随机偏移和
    区域中心抖动。纯等间隔的输入是很容易被模式识别出来的特征。
    """
    before_click: tuple[float, float] = (0.05, 0.22)
    after_click: tuple[float, float] = (0.12, 0.40)
    move_duration: tuple[float, float] = (0.08, 0.30)
    click_offset_px: int = 3
    jitter_ratio: float = 0.15
    enabled: bool = True

    def wait_before(self) -> None:
        if self.enabled:
            time.sleep(random.uniform(*self.before_click))

    def wait_after(self) -> None:
        if self.enabled:
            time.sleep(random.uniform(*self.after_click))

    def offset(self) -> tuple[int, int]:
        if not self.enabled or self.click_offset_px <= 0:
            return 0, 0
        n = self.click_offset_px
        return random.randint(-n, n), random.randint(-n, n)

    def jitter_point(self, x: int, y: int, w: int = 0, h: int = 0) -> tuple[int, int]:
        """在给定区域中心附近抖动一下再点，而不是每次都点死同一个像素。"""
        dx, dy = self.offset()
        if self.enabled and w and h:
            r = self.jitter_ratio
            dx += int(random.uniform(-r, r) * w / 2)
            dy += int(random.uniform(-r, r) * h / 2)
        return x + dx, y + dy


def human_pause(base: float, jitter: float, enabled: bool = True) -> float:
    """把一个固定间隔变成 base ± jitter 的随机值。返回实际睡了多久。"""
    if not enabled or jitter <= 0:
        time.sleep(max(0.0, base))
        return base
    t = max(0.0, random.uniform(base - jitter, base + jitter))
    time.sleep(t)
    return t


# ---------------------------------------------------------------------------
# 全局热键
# ---------------------------------------------------------------------------
class HotkeyWatcher:
    """全局热键监听。装了 keyboard 就用它的钩子（事件驱动、延迟低），
    否则退回 ctypes 轮询 GetAsyncKeyState（零依赖，50ms 精度对启停键完全够用）。

    注意分工：`keyboard` 库适合**监听**，不适合**往游戏里发键**（它发 VK）。
    这是两件事，别混。
    """

    def __init__(self, poll_interval: float = 0.05) -> None:
        self.poll_interval = poll_interval
        self._callbacks: dict[str, list] = {}
        self._handles: list = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.backend = ""

    def add(self, key: str, callback) -> None:
        self._callbacks.setdefault(normalize_key(key), []).append(callback)

    def start(self) -> str:
        keys = list(self._callbacks)
        try:
            import keyboard  # noqa: PLC0415 (可选依赖，按需导入)
            for k in keys:
                self._handles.append(keyboard.add_hotkey(k, lambda kk=k: self._fire(kk)))
            self.backend = "keyboard 钩子"
            return self.backend
        except Exception:
            pass

        # 纯标准库兜底：轮询 GetAsyncKeyState 的高位（按下）
        missing = [k for k in keys if k not in VK_CODES]
        if missing:
            raise RuntimeError(f"轮询模式只支持这些键：{sorted(VK_CODES)}；不支持的：{missing}")
        self.backend = "GetAsyncKeyState 轮询"
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()
        return self.backend

    def _poll(self) -> None:
        vks = {k: VK_CODES[k] for k in self._callbacks}
        was_down: dict[str, bool] = {k: False for k in vks}
        while not self._stop.is_set():
            for k, vk in vks.items():
                down = bool(_user32.GetAsyncKeyState(vk) & 0x8000)
                if down and not was_down[k]:
                    self._fire(k)
                was_down[k] = down
            time.sleep(self.poll_interval)

    def _fire(self, key: str) -> None:
        for cb in self._callbacks.get(key, []):
            try:
                cb()
            except Exception as e:  # 回调里出错不该拖垮监听
                print(f"    [热键回调异常] {key}: {e}")

    def stop(self) -> None:
        self._stop.set()
        if self._handles:
            try:
                import keyboard  # noqa: PLC0415
                for h in self._handles:
                    keyboard.remove_hotkey(h)
            except Exception:
                pass
            self._handles.clear()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


# ---------------------------------------------------------------------------
# 权限
# ---------------------------------------------------------------------------
def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate() -> None:
    """请求管理员权限重启自己。游戏若以管理员运行，这一步是必需的（UIPI）。"""
    if is_admin():
        return
    params = " ".join(f'"{a}"' if " " in a else a for a in sys.argv)
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    sys.exit(0)


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
def self_test() -> int:
    """回环自检：注入一串键，再用自己的钩子收回来。

    证明的不是「代码能跑」，而是「注入链路真的把键送到了系统输入队列」。
    这比「SendInput 返回 1」强得多——返回值只说明调用没报错。

    判据用的是**连续子序列**而不是「收到了某个键」：后台会话里本来就可能有零散按键
    （实测就捕获到过 i/j/k/a/n 这种杂音），只查单键会误判。
    """
    marker = ["z", "x", "q"]  # 连起来不像人手动敲的，也没被系统占用
    print("== 发键链路自检 ==")
    print(f"  管理员权限: {'是' if is_admin() else '否（游戏若提权运行，注入会被 UIPI 拦掉）'}")
    print(f"  探针序列  : {marker}")

    captured: list[str] = []
    watcher: HotkeyWatcher | None = None
    stop = threading.Event()

    try:
        import keyboard  # noqa: PLC0415
        h = keyboard.on_press(lambda e: captured.append((e.name or "").lower()))
        mode = "keyboard 钩子回环"

        def cleanup():
            keyboard.unhook(h)
    except Exception:
        mode = "GetAsyncKeyState 轮询回环"
        vk = {"z": 0x5A, "x": 0x58, "q": 0x51}
        seen = set()

        def poll():
            while not stop.is_set():
                for k, code in vk.items():
                    if _user32.GetAsyncKeyState(code) & 0x8000:
                        seen.add(k)
                time.sleep(0.005)

        threading.Thread(target=poll, daemon=True).start()

        def cleanup():
            stop.set()
            captured.extend(sorted(seen, key=lambda k: list(vk).index(k)))

    print(f"  回环方式  : {mode}")
    time.sleep(0.25)
    sent = 0
    for k in marker:
        if press_key(k, 0.05):
            sent += 1
        time.sleep(0.12)
    print(f"  SendInput : 成功 {sent}/{len(marker)} 次调用")
    time.sleep(0.5)
    cleanup()

    def has_run(seq: list[str], needle: list[str]) -> bool:
        n = len(needle)
        return any(seq[i:i + n] == needle for i in range(len(seq) - n + 1))

    got = has_run(captured, marker)
    print(f"  收到回环  : {'是 —— 键真的进了系统输入队列 ✓' if got else '否'}")
    print(f"  捕获记录  : {captured[:12]}{'…' if len(captured) > 12 else ''}")

    if got:
        print("\n结论：注入链路可用。剩下就是让目标窗口在前台了。")
        return 0
    print("\n结论：没按顺序收到探针序列。可能原因：当前会话没有可交互桌面 / 钩子被占用 / UIPI 拦截。")
    return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="往游戏里发键（DirectInput 扫描码）")
    ap.add_argument("--keys", default="", help="逗号分隔的按键序列，如 f,f,space")
    ap.add_argument("--interval", type=float, default=0.3, help="两次按键之间的间隔秒数")
    ap.add_argument("--duration", type=float, default=0.06, help="每次按下保持多久")
    ap.add_argument("--loop", action="store_true", help="循环发送，直到按停止键")
    ap.add_argument("--repeat", type=int, default=1, help="循环次数（配合 --keys 使用）")
    ap.add_argument("--start-key", default="F10", help="启动热键（--loop 用）")
    ap.add_argument("--stop-key", default="F12", help="停止热键（--loop 用）")
    ap.add_argument("--dry-run", action="store_true", help="不真发键，只打印")
    ap.add_argument("--elevate", action="store_true", help="权限不足时自动请求管理员重启")
    ap.add_argument("--check", action="store_true", help="只检查环境与权限")
    ap.add_argument("--self-test", action="store_true", help="发键链路回环自检")
    ap.add_argument("--click", default="", help="点击屏幕坐标，如 --click 960,540")
    ap.add_argument("--click-button", default="left", choices=["left", "right"])
    ap.add_argument("--background", action="store_true",
                    help="后台模式：向窗口投递消息，不移动真实光标")
    ap.add_argument("--window-title", default="", help="后台模式的目标窗口标题片段")
    ap.add_argument("--window-process", default="", help="后台模式的目标进程名（不含 .exe）")
    ap.add_argument("--find-window", action="store_true", help="只查找窗口并打印句柄")
    ap.add_argument("--list-windows", action="store_true",
                    help="列出所有可见窗口的 进程名/标题（找目标窗口用）")
    args = ap.parse_args()

    if args.list_windows:
        rows = _enum_visible_windows()
        print(f"共 {len(rows)} 个可见窗口：")
        for hwnd, pid, t in rows:
            print(f"  hwnd=0x{hwnd:<9X} pid={pid:<8} {t[:70]}")
        return 0

    if args.find_window:
        hwnd = find_window(args.window_title or None, process=args.window_process or None)
        if hwnd:
            print(f"找到窗口句柄：0x{hwnd:X}  (十进制 {hwnd})")
            return 0
        print("没找到窗口。下面这些是当前可见窗口，照着填 --window-title / --window-process：")
        for _, pid, t in _enum_visible_windows()[:15]:
            print(f"  pid={pid:<8} {t[:70]}")
        return 1

    if args.click:
        try:
            cx, cy = (int(v) for v in args.click.replace("，", ",").split(","))
        except ValueError:
            print("[x] --click 的格式应该是 x,y")
            return 2
        if args.background:
            hwnd = find_window(args.window_title or None, process=args.window_process or None)
            if not hwnd:
                print("[x] 没找到目标窗口，无法后台点击")
                return 1
            ok = click_background(hwnd, cx, cy, button=args.click_button, dry_run=args.dry_run)
            print(f"后台点击 ({cx}, {cy}) → {'成功' if ok else '失败'}")
        else:
            ok = click_at(cx, cy, button=args.click_button, dry_run=args.dry_run)
            print(f"前台点击 ({cx}, {cy}) → {'成功' if ok else '失败'}")
        return 0 if ok else 1

    if args.check:
        print(f"Python      : {sys.version.split()[0]}")
        print(f"管理员权限  : {'是' if is_admin() else '否'}")
        print(f"平台        : {sys.platform}")
        try:
            import pydirectinput  # noqa: PLC0415, F401
            print("pydirectinput: 已安装（可用作对照后端）")
        except ImportError:
            print("pydirectinput: 未安装（本模块不需要它）")
        try:
            import keyboard  # noqa: PLC0415, F401
            print("keyboard    : 已安装（热键走事件钩子）")
        except ImportError:
            print("keyboard    : 未安装（热键退回 GetAsyncKeyState 轮询）")
        return 0

    if args.self_test:
        return self_test()

    if args.elevate and not is_admin():
        print("权限不足，正在请求管理员权限重启…")
        elevate()

    keys = [k for k in (args.keys.split(",") if args.keys else []) if k.strip()]
    if not keys and not args.loop:
        ap.print_help()
        return 0

    if not args.loop:
        print(f"发送 {len(keys)} 个键：{keys}（间隔 {args.interval}s，保持 {args.duration}s）")
        sent = 0
        for r in range(max(1, args.repeat)):
            if args.repeat > 1:
                print(f"  第 {r + 1}/{args.repeat} 轮")
            sent += send_keys(keys, args.interval, args.duration, dry_run=args.dry_run)
        print(f"完成，成功 {sent} / {len(keys) * max(1, args.repeat)}")
        return 0

    # --loop：热键驱动
    running = threading.Event()
    stop_all = threading.Event()
    watcher = HotkeyWatcher()
    watcher.add(args.start_key, lambda: (running.set(), print(f"  [{args.start_key}] ▶ 开始")))
    watcher.add(args.stop_key, lambda: (running.clear(), print(f"  [{args.stop_key}] ⏹ 停止")))

    try:
        backend = watcher.start()
    except Exception as e:
        print(f"[x] 热键注册失败：{e}")
        return 2

    print(f"热键后端：{backend}")
    print(f"{args.start_key} 开始循环发键 / {args.stop_key} 停止 / Ctrl+C 退出")
    if args.dry_run:
        print("（dry-run 模式，不会真的发键）")

    def worker() -> None:
        while not stop_all.is_set():
            if running.is_set():
                send_keys(keys, args.interval, args.duration, dry_run=args.dry_run)
            else:
                time.sleep(0.05)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        while not stop_all.is_set():
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，退出")
    finally:
        stop_all.set()
        watcher.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
