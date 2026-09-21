#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vision.py —— 截图 + 模板匹配（判断游戏里「有没有那个东西」）
=============================================================

用途
----
自动采集物品要闭环，缺的最后一环是**看见**：
    站在采集物旁边 → 屏幕上有「采集」提示 → 按 F → 提示消失 = 采到了
    没有提示 = 这里没东西 / 已经采过了

「提示在不在」是个纯视觉判断。本模块把这件事做到能测：

  * **截图**：ctypes 直接调 GDI `BitBlt`（不装 mss / pyautogui）。区域截图 2~5 ms，
    全屏 10~20 ms，够 10 Hz 轮询。
  * **匹配**：归一化互相关（NCC）。用 **FFT + 积分图** 实现，所以搜索区域多大都不虚。
    装了 opencv 就走 `cv2.matchTemplate` 快路径，没装也能跑。
  * **自检**：从真实截屏里裁一块当模板，再回去找，验证能找回原坐标。
    这是唯一能证明「截图→匹配→坐标映射」整条链子没错的办法。

为什么不用「像素相等」比：游戏 UI 有抗锯齿、渐变、半透明，还有个位数像素的抖动，
逐像素相等会全军覆没。NCC 对整体亮度变化免疫，这才是能用的判据。

依赖：numpy（必需）、opencv-python（可选，仅加速）
    pip install numpy

用法
----
    # 端到端自检：截屏 → 裁模板 → 匹配验证（不需要游戏）
    python rpa/vision.py --self-test

    # 在一张截图里找模板
    python rpa/vision.py --find --template prompt.png --region 700,380,320,200

    # 存一张截图下来当底图（手工裁模板用）
    python rpa/vision.py --save shot.png --region 700,380,320,200
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
import math
import sys
import time
from collections import OrderedDict
from ctypes import wintypes
from pathlib import Path

try:
    import numpy as np
except ImportError:  # pragma: no cover
    print("[x] 需要 numpy：pip install numpy", file=sys.stderr)
    raise SystemExit(2)

# ---------------------------------------------------------------------------
# GDI 截图
# ---------------------------------------------------------------------------
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
PW_RENDERFULLCONTENT = 0x00000002

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

_user32.GetDC.argtypes = [wintypes.HWND]
_user32.GetDC.restype = wintypes.HDC
_user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_user32.ReleaseDC.restype = ctypes.c_int
_user32.GetSystemMetrics.argtypes = [ctypes.c_int]
_user32.GetSystemMetrics.restype = ctypes.c_int
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
_user32.PrintWindow.restype = wintypes.BOOL
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.IsWindow.restype = wintypes.BOOL

_gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteDC.argtypes = [wintypes.HDC]
_gdi32.DeleteDC.restype = wintypes.BOOL
_gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
_gdi32.BitBlt.restype = wintypes.BOOL
_gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
_gdi32.GetDIBits.restype = ctypes.c_int
_gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE,
                                    wintypes.DWORD]
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def screen_size() -> tuple[int, int]:
    """主屏尺寸。"""
    return _user32.GetSystemMetrics(0), _user32.GetSystemMetrics(1)


def virtual_screen() -> tuple[int, int, int, int]:
    """多屏合起来的虚拟桌面 (x, y, w, h)。"""
    return (
        _user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        _user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


class _Grabber:
    """截图器，基于 **CreateDIBSection**。

    为什么不用「CreateCompatibleBitmap + BitBlt + GetDIBits」这条常见路线：
    实测那种写法**无论区域大小恒定 16~18 ms**，把 DC/位图复用起来也一点没变快
    —— 说明开销全在 `GetDIBits` 那次格式转换上（它要跟显示驱动同步）。
    mss 之所以快，用的是另一个办法：建一个 **DIB 段**（CreateDIBSection），
    `BitBlt` 直接画进那块内存，然后拿内存指针给 numpy 当 view 用 —— **零拷贝、不调 GetDIBits**。
    这里照这个思路实现，实测小区域降到 1~2 ms。
    """

    def __init__(self) -> None:
        self._hdc = 0
        self._mem = 0
        self._bmp = 0
        self._bits: int = 0
        self._size = (0, 0)

    def _ensure(self, w: int, h: int) -> None:
        if self._mem and self._size == (w, h):
            return
        self.close()
        self._hdc = _user32.GetDC(None)
        if not self._hdc:
            raise OSError("GetDC 失败")
        self._mem = _gdi32.CreateCompatibleDC(self._hdc)
        if not self._mem:
            raise OSError("CreateCompatibleDC 失败")

        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h            # 负值 = 自上而下
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0        # BI_RGB
        bits = ctypes.c_void_p()
        self._bmp = _gdi32.CreateDIBSection(self._hdc, ctypes.byref(bi), DIB_RGB_COLORS,
                                            ctypes.byref(bits), None, 0)
        if not self._bmp or not bits:
            raise OSError("CreateDIBSection 失败")
        self._bits = bits.value or 0
        _gdi32.SelectObject(self._mem, self._bmp)
        self._size = (w, h)

    def grab(self, x: int, y: int, w: int, h: int) -> np.ndarray:
        w, h = int(w), int(h)
        self._ensure(w, h)
        if not _gdi32.BitBlt(self._mem, 0, 0, w, h, self._hdc, int(x), int(y), SRCCOPY):
            err = ctypes.get_last_error()
            # 错误码 6 = ERROR_INVALID_HANDLE。实测最常见的原因不是代码问题，
            # 而是**会话被锁屏/断开**——此时 GetDC 拿回来的句柄用不了。
            # 明确写出来，否则只有一个数字，排查时容易往错的方向找。
            hint = "（错误码 6 = 无效句柄：通常是会话被锁屏/断开，读不到桌面）" if err == 6 else ""
            raise OSError(f"BitBlt 失败：{err}{hint}")
        # 直接把那块 DIB 内存当 numpy 读，零拷贝；copy 一次是因为下一帧会覆盖它
        buf = (ctypes.c_ubyte * (w * h * 4)).from_address(self._bits)
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, :3]
        return np.ascontiguousarray(arr)

    def close(self) -> None:
        if self._bmp:
            _gdi32.DeleteObject(self._bmp)
            self._bmp = 0
        if self._mem:
            _gdi32.DeleteDC(self._mem)
            self._mem = 0
        if self._hdc:
            _user32.ReleaseDC(None, self._hdc)
            self._hdc = 0
        self._bits = 0
        self._size = (0, 0)


_grabber = _Grabber()


def grab(x: int = 0, y: int = 0, w: int | None = None, h: int | None = None) -> np.ndarray:
    """截屏，返回 (h, w, 3) 的 **BGR** uint8 数组（和 OpenCV 一致）。

    内部复用 GDI 对象，所以连续调用会明显快于每次新建。
    BGR 而不是 RGB：后面要用 cv2 的话它就是这个顺序，省一次转换。
    """
    if w is None or h is None:
        sw, sh = screen_size()
        w = w or sw
        h = h or sh
    return _grabber.grab(x, y, w, h)


def grab_window(hwnd: int) -> np.ndarray:
    """截指定窗口（PrintWindow）。

    ⚠️ DirectX 独占全屏的窗口经常截出黑图；窗口化/无边框一般可以。
    这是「后台截图」路线，能不能用取决于游戏怎么渲染 —— 先试，黑图就退回全屏 grab()。
    """
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        raise OSError("GetWindowRect 失败")
    w, h = rect.right - rect.left, rect.bottom - rect.top
    hdc = _user32.GetDC(None)
    mem = _gdi32.CreateCompatibleDC(hdc)
    bmp = _gdi32.CreateCompatibleBitmap(hdc, w, h)
    try:
        _gdi32.SelectObject(mem, bmp)
        if not _user32.PrintWindow(wintypes.HWND(hwnd), mem, PW_RENDERFULLCONTENT):
            raise OSError("PrintWindow 失败（该窗口可能不支持后台截图）")
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        buf = ctypes.create_string_buffer(w * h * 4)
        if _gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS) == 0:
            raise OSError("GetDIBits 失败")
        return np.ascontiguousarray(np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, :3])
    finally:
        _gdi32.DeleteObject(bmp)
        _gdi32.DeleteDC(mem)
        _user32.ReleaseDC(None, hdc)


def to_gray(img: np.ndarray) -> np.ndarray:
    """BGR → 灰度 float64。加权系数用 BT.601。"""
    if img.ndim == 2:
        return img.astype(np.float64)
    b, g, r = img[:, :, 0], img[:, :, 1], img[:, :, 2]
    return (0.114 * b + 0.587 * g + 0.299 * r).astype(np.float64)


def load_gray(path: str | Path) -> np.ndarray:
    """读图片并转灰度。用 PIL（本机已有）。"""
    from PIL import Image
    return to_gray(np.asarray(Image.open(path).convert("RGB"))[:, :, ::-1])


# ---------------------------------------------------------------------------
# 帧差：判断"画面有没有在动"
# ---------------------------------------------------------------------------
def frame_diff(a: np.ndarray, b: np.ndarray) -> float:
    """两帧的平均绝对差（0~255）。同尺寸才行，否则报错而不是静默算错。"""
    if a.shape != b.shape:
        raise ValueError(f"两帧尺寸不同：{a.shape} vs {b.shape}")
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


class MotionDetector:
    """靠帧差判断画面静止还是运动中。

    用途：坐骑「识途」自动寻路时，人不能干预（一操作就打断），所以只能靠画面判断到没到。
    实测约束（来自游戏机制）：**寻路遇到水会停下来**，所以不能死等一个固定时长 ——
    要能识别「已经不动了」，然后继续下一步。

    判据：连续 `still_frames` 次采样的帧差都低于 `threshold` 才算「停下来了」。
    单次低帧差可能是画面恰好卡在某一帧，连续多次才可信。
    """

    def __init__(self, region: tuple[int, int, int, int], *, threshold: float = 2.0,
                 still_frames: int = 3, poll: float = 0.35) -> None:
        self.region = region
        self.threshold = threshold
        self.still_frames = still_frames
        self.poll = poll
        self._prev: np.ndarray | None = None
        self._still = 0
        self.moving = False

    def sample(self) -> float:
        """采一帧，返回与上一帧的差异。第一次采样返回 -1。"""
        x, y, w, h = self.region
        cur = to_gray(grab(x, y, w, h))
        if self._prev is None or self._prev.shape != cur.shape:
            self._prev = cur
            return -1.0
        d = frame_diff(self._prev, cur)
        self._prev = cur
        if d < self.threshold:
            self._still += 1
        else:
            self._still = 0
        self.moving = d >= self.threshold
        return d

    def wait_until_still(self, timeout: float = 60.0) -> tuple[bool, float]:
        """等到画面连续静止。返回 (是否等到, 实际等待秒数)。

        超时返回 False —— 调用方应该据此把这一步标记为「没确认到达」，
        而不是假装成功了继续往下走。
        """
        import time as _t
        t0 = _t.perf_counter()
        self._prev = None
        self._still = 0
        while _t.perf_counter() - t0 < timeout:
            self.sample()
            if self._still >= self.still_frames:
                return True, _t.perf_counter() - t0
            _t.sleep(self.poll)
        return False, _t.perf_counter() - t0


# ---------------------------------------------------------------------------
# 模板匹配：归一化互相关
# ---------------------------------------------------------------------------
class Match:
    __slots__ = ("score", "x", "y", "w", "h")

    def __init__(self, score: float, x: int, y: int, w: int, h: int) -> None:
        self.score, self.x, self.y, self.w, self.h = score, x, y, w, h

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2

    def __repr__(self) -> str:
        return f"Match(score={self.score:.4f}, x={self.x}, y={self.y}, size={self.w}x{self.h})"


def _integral(a: np.ndarray) -> np.ndarray:
    """积分图，形状 (H+1, W+1)。

    刻意用 float64：累加 800×600 这种尺寸时总和能到 1e8 量级，
    float32 尾数只有 24 位（约 1.7e7 整数可精确表示），积分图会丢精度。
    这里不能省。
    """
    return np.pad(a.cumsum(0).cumsum(1), ((1, 0), (1, 0)))


def _window_sums(ii: np.ndarray, th: int, tw: int) -> np.ndarray:
    """用积分图一次算出所有 (th, tw) 窗口的和，返回 (H-th+1, W-tw+1)。"""
    return ii[th:, tw:] - ii[:-th, tw:] - ii[th:, :-tw] + ii[:-th, :-tw]


# ---------------------------------------------------------------------------
# 模板侧的缓存
# ---------------------------------------------------------------------------
# 轮询同一个模板做匹配时，模板的 FFT / 均值 / 方差每次都是同一份东西。
# 实测这部分占单次匹配的 10%~30%（40×40 模板在 800×600 上要 17.75 ms），
# 而画面 FFT 才是真正随场景变化的部分 —— 所以缓存模板侧。
_TPL_CACHE: OrderedDict = OrderedDict()
_TPL_CACHE_MAX = 8


def _tpl_key(tpl_f32: np.ndarray, fft_shape: tuple[int, int]) -> tuple:
    import hashlib
    return (tpl_f32.shape, fft_shape,
            hashlib.blake2b(tpl_f32.tobytes(), digest_size=8).digest())


def _tpl_side(tpl: np.ndarray, fft_shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray, float, float]:
    """返回 (反转后的模板 float32, FFT, 均值, 标准差)。带 LRU 缓存。"""
    tpl_f32 = np.ascontiguousarray(tpl, dtype=np.float32)
    key = _tpl_key(tpl_f32, fft_shape)
    hit = _TPL_CACHE.get(key)
    if hit is not None:
        _TPL_CACHE.move_to_end(key)
        return hit

    rev = np.ascontiguousarray(tpl_f32[::-1, ::-1])
    ft = np.fft.rfft2(rev, s=fft_shape)
    entry = (rev, ft, float(tpl_f32.mean()), float(tpl_f32.std()))
    _TPL_CACHE[key] = entry
    if len(_TPL_CACHE) > _TPL_CACHE_MAX:
        _TPL_CACHE.popitem(last=False)
    return entry


def tpl_cache_info() -> dict:
    """缓存状态（测试与调优用）。"""
    return {"entries": len(_TPL_CACHE), "max": _TPL_CACHE_MAX}


def tpl_cache_clear() -> None:
    _TPL_CACHE.clear()


def cv2_available() -> bool:
    """cv2 装没装。**必须显式判断** —— 早先自检里写了个「cv2 对照」分支，
    但 ncc_map 内部遇到 ImportError 会静默回落到自研实现，
    于是那行「cv2」其实跑的还是自研代码 —— 测试在撒谎，比没有测试更糟。"""
    try:
        import cv2  # noqa: PLC0415, F401
        return True
    except ImportError:
        return False


def ncc_map(hay: np.ndarray, tpl: np.ndarray, *, use_cv2: bool = True) -> np.ndarray:
    """归一化互相关图，形状 (H-th+1, W-tw+1)，取值 [-1, 1]。

    实现要点：
      * 分子（互相关）用 FFT：把模板反转后做卷积 = 互相关，O(N log N)。
        直接双重循环在 1600×900 上会慢到没法用。
      * 分母（局部均值/方差）用积分图，O(1) 每个窗口。
      * NCC 对整体亮度/对比度线性变化免疫 —— 游戏 UI 有渐变和抗锯齿，这点很关键。

    ⚠️ **纯色模板没有意义**：模板方差为 0 时 NCC 的分母是 0，数学上是 0/0。
    早期版本这里静默返回全 0，结果 argmax 落在 (0,0)，看起来像「在左上角匹配到了分数 0」——
    排查了半天才发现是模板选在了空白处。现在直接报错，并且把标准差打出来。
    """
    H, W = hay.shape
    th, tw = tpl.shape
    if th > H or tw > W:
        raise ValueError(f"模板 {tw}x{th} 比搜索区域 {W}x{H} 还大")

    # 模板侧：均值/方差/FFT 都是同一份，走缓存
    fft_shape = (H + th - 1, W + tw - 1)
    _rev, ft, t_mean, t_std = _tpl_side(tpl, fft_shape)
    if t_std < 1.0:
        raise ValueError(
            f"模板几乎是纯色（标准差 {t_std:.3f}），NCC 无意义，匹配结果不可信。"
            f" 请换一块有纹理/有边界的模板（比如图标、文字、按钮边缘）。"
        )

    if use_cv2:
        try:
            import cv2  # noqa: PLC0415
            return cv2.matchTemplate(np.ascontiguousarray(hay, dtype=np.float32),
                                     np.ascontiguousarray(tpl, dtype=np.float32),
                                     cv2.TM_CCOEFF_NORMED)
        except ImportError:
            pass

    n = th * tw
    # 分母：局部均值与方差（积分图走 float64，不能省精度）
    hay64 = np.ascontiguousarray(hay, dtype=np.float64)
    ii = _integral(hay64)
    iis = _integral(hay64 * hay64)
    s1 = _window_sums(ii, th, tw)
    s2 = _window_sums(iis, th, tw)

    # 画面的整体均值和方差 —— 直接从刚算好的积分图里拿，免费。
    # （原来单独调一次 hay.std()，800×600 上要 7.4 ms，白花）
    total = float(ii[-1, -1])
    total_sq = float(iis[-1, -1])
    n_all = float(H * W)
    g_mean = total / n_all
    g_var = total_sq / n_all - g_mean * g_mean
    if g_var < 1.0:
        raise ValueError("搜索区域几乎是纯色（可能是黑屏或空白区域），无法匹配。")

    mean_h = s1 / n
    var_h = s2 - n * mean_h * mean_h
    np.maximum(var_h, 0, out=var_h)
    t_var = (t_std ** 2) * n

    # 分子：画面 FFT 用 float32（字节少一半，快接近一倍；匹配只看分数大小，
    # float32 的 ~1e-4 相对误差对 0.8 这种阈值毫无影响）
    fh = np.fft.rfft2(np.ascontiguousarray(hay, dtype=np.float32), s=fft_shape)
    conv = np.fft.irfft2(fh * ft, s=fft_shape)
    cross = conv[th - 1: H, tw - 1: W]          # 与 (H-th+1, W-tw+1) 对齐

    num = cross.astype(np.float64) - n * mean_h * t_mean
    den = np.sqrt(var_h) * math.sqrt(t_var)
    out = np.zeros_like(num)
    np.divide(num, den, out=out, where=den > 1e-9)
    np.clip(out, -1.0, 1.0, out=out)
    return out


def find_best(hay: np.ndarray, tpl: np.ndarray, *, use_cv2: bool = True) -> Match:
    """在 hay 里找 tpl 最像的位置。"""
    m = ncc_map(hay, tpl, use_cv2=use_cv2)
    idx = int(np.argmax(m))
    y, x = divmod(idx, m.shape[1])
    return Match(float(m[y, x]), int(x), int(y), tpl.shape[1], tpl.shape[0])


def find_all(hay: np.ndarray, tpl: np.ndarray, *, threshold: float = 0.85,
             max_hits: int = 50, use_cv2: bool = True) -> list[Match]:
    """找出所有 ≥ threshold 的位置，并对重叠结果做非极大值抑制。"""
    m = ncc_map(hay, tpl, use_cv2=use_cv2)
    th, tw = tpl.shape
    hits: list[Match] = []
    work = m.copy()
    for _ in range(max_hits):
        idx = int(np.argmax(work))
        y, x = divmod(idx, work.shape[1])
        score = float(work[y, x])
        if score < threshold:
            break
        hits.append(Match(score, int(x), int(y), tw, th))
        y0, y1 = max(0, y - th // 2), min(work.shape[0], y + th // 2 + 1)
        x0, x1 = max(0, x - tw // 2), min(work.shape[1], x + tw // 2 + 1)
        work[y0:y1, x0:x1] = -1.0
    return hits


# ---------------------------------------------------------------------------
# 自检：证明「截图 → 匹配 → 坐标映射」整条链子是对的
# ---------------------------------------------------------------------------
def self_test(*, region: tuple[int, int, int, int] | None = None,
              cut: tuple[int, int, int, int] | None = None) -> int:
    """从真实截屏里裁一块当模板，再回去找它，看能不能找回原坐标。

    这是唯一能证明整条链子没错的办法 —— 比「函数没抛异常」强得多。
    """
    print("== 视觉链路自检 ==")
    sw, sh = screen_size()
    print(f"  屏幕      : {sw}x{sh}")

    if region is None:
        # 挑屏幕中偏左上的一块：避开任务栏和边缘，内容一般有足够纹理
        rx, ry = sw // 5, sh // 4
        rw, rh = min(500, sw - rx - 20), min(360, sh - ry - 20)
        region = (rx, ry, rw, rh)
    rx, ry, rw, rh = region
    print(f"  搜索区域  : ({rx}, {ry}) {rw}x{rh}")

    t0 = time.perf_counter()
    hay = grab(rx, ry, rw, rh)
    dt_grab = (time.perf_counter() - t0) * 1000
    hay_g = to_gray(hay)
    print(f"  截图      : {hay.shape}  {dt_grab:.1f} ms")

    if float(hay_g.std()) < 1.0:
        print("  [!] 搜索区域几乎是纯色（可能是黑屏/无桌面会话），匹配验证没有意义。")
        print("      换一台有画面的机器，或用 --region 指定一片有内容的区域。")
        return 2

    if cut is None:
        # 别固定裁某个偏移 —— 那块可能是空白。扫一遍找**最有纹理**的一小块，
        # 这样自检在任何屏幕上都能跑（早期版本固定裁 (rw/3, rh/3)，
        # 正好落在空白处，于是 NCC 分母为 0 报了个看不懂的错误）。
        cw, ch = 48, 40
        best_std, best_pos = -1.0, (0, 0)
        for yy in range(0, max(1, rh - ch), 12):
            for xx in range(0, max(1, rw - cw), 12):
                s = float(hay_g[yy:yy + ch, xx:xx + cw].std())
                if s > best_std:
                    best_std, best_pos = s, (xx, yy)
        if best_std < 3.0:
            print(f"  [!] 整块搜索区域都很平（最高局部标准差只有 {best_std:.1f}），"
                  f"没有可用纹理做模板。")
            print("      桌面是不是空的？用 --region 指一片有内容（图标/文字/窗口）的区域。")
            return 2
        cut = (best_pos[0], best_pos[1], cw, ch)
        print(f"  选模板    : 扫到最有纹理的一块（局部标准差 {best_std:.1f}）")
    cx, cy, cw, ch = cut
    tpl = hay_g[cy:cy + ch, cx:cx + cw]
    print(f"  裁模板    : 从区域内 ({cx}, {cy}) 裁 {cw}x{ch}，标准差 {tpl.std():.1f}")

    # 两套实现都对一遍：自研 FFT/NCC 路径 + cv2 路径（**真的装了才跑**）
    plan: list[tuple[str, bool]] = [("自研 NCC (FFT+积分图)", False)]
    if cv2_available():
        plan.append(("cv2.matchTemplate", True))
    else:
        print("  [跳过] cv2 未安装，没有对照实现可跑（自研路径已是唯一实现）")

    for label, use_cv2 in plan:
        try:
            find_best(hay_g, tpl, use_cv2=use_cv2)  # 预热（首次含 FFT plan 建立开销）
            t0 = time.perf_counter()
            n_rep = 5
            for _ in range(n_rep):
                hit = find_best(hay_g, tpl, use_cv2=use_cv2)
            dt = (time.perf_counter() - t0) / n_rep * 1000
        except Exception as e:
            print(f"  [{label}] 失败：{type(e).__name__}: {e}")
            return 1
        ok = abs(hit.x - cx) <= 1 and abs(hit.y - cy) <= 1 and hit.score > 0.99
        print(f"  [{label}] score={hit.score:.5f} @({hit.x},{hit.y}) 期望({cx},{cy})  "
              f"{dt:.1f} ms/次  {'✓' if ok else '✗ 位置或分数不对'}")
        if not ok:
            return 1

    # 性能实测：把整条链路的耗时拆开，别只看匹配
    t0 = time.perf_counter()
    for _ in range(5):
        V_grab = grab(rx, ry, rw, rh)
    t_grab = (time.perf_counter() - t0) / 5 * 1000
    t0 = time.perf_counter()
    for _ in range(5):
        to_gray(V_grab)
    t_gray = (time.perf_counter() - t0) / 5 * 1000
    total = t_grab + t_gray
    print(f"  链路耗时  : 截图 {t_grab:.1f} ms + 灰度 {t_gray:.1f} ms"
          f"  →  一帧约 {total:.0f} ms（不含匹配），上限约 {1000 / max(total, 0.1):.0f} Hz")

    # 反面验证：拿一块「和画面不可能像」的噪声图去匹配，分数应该很低
    rng = np.random.default_rng(7)
    noise = rng.integers(0, 255, size=(ch, cw)).astype(np.float64)
    bad = find_best(hay_g, noise, use_cv2=False)
    print(f"  [反面验证] 随机噪声模板 score={bad.score:.5f}（应当明显偏低）")
    if bad.score > 0.5:
        print("  [x] 噪声都能匹配上，说明打分逻辑有问题")
        return 1

    # 正面验证「纯色模板会被明确拒绝」，而不是静默返回 0
    flat = np.full((ch, cw), 128.0)
    try:
        find_best(hay_g, flat, use_cv2=False)
        print("  [x] 纯色模板居然没被拒绝 —— 这正是早期那个坑，必须报错")
        return 1
    except ValueError:
        print("  [边界情况] 纯色模板被明确拒绝（而不是静默返回 0）✓")

    print("\n结论：截图 → 灰度 → 匹配 → 坐标映射，整条链子验证通过。")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _parse_region(s: str) -> tuple[int, int, int, int]:
    vals = [int(v) for v in s.replace("，", ",").split(",")]
    if len(vals) != 4:
        raise argparse.ArgumentTypeError("区域格式应为 x,y,w,h")
    return vals[0], vals[1], vals[2], vals[3]


def main() -> int:
    ap = argparse.ArgumentParser(description="截图与模板匹配")
    ap.add_argument("--self-test", action="store_true", help="端到端自检")
    ap.add_argument("--region", type=_parse_region, default=None, help="区域 x,y,w,h")
    ap.add_argument("--find", action="store_true", help="在区域里找模板")
    ap.add_argument("--template", default="", help="模板图片路径")
    ap.add_argument("--threshold", type=float, default=0.85, help="匹配阈值")
    ap.add_argument("--save", default="", help="把截图保存到这个路径")
    args = ap.parse_args()

    if args.self_test:
        return self_test(region=args.region)

    if args.save:
        r = args.region
        img = grab(*r) if r else grab()
        from PIL import Image
        Image.fromarray(img[:, :, ::-1]).save(args.save)
        print(f"已保存 {img.shape[1]}x{img.shape[0]} → {args.save}")
        return 0

    if args.find:
        if not args.template:
            print("[x] --find 需要 --template")
            return 2
        r = args.region
        img = grab(*r) if r else grab()
        hay = to_gray(img)
        tpl = load_gray(args.template)
        hits = find_all(hay, tpl, threshold=args.threshold)
        ox, oy = (r[0], r[1]) if r else (0, 0)
        if not hits:
            print(f"没找到（阈值 {args.threshold}）")
            return 1
        print(f"找到 {len(hits)} 处：")
        for h in hits:
            cx, cy = h.center
            print(f"  屏幕坐标 ({ox + cx}, {oy + cy})  score={h.score:.4f}  左上({ox + h.x},{oy + h.y})")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
