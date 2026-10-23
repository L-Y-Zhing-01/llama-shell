# -*- coding: utf-8 -*-
"""多显示器(Windows)屏幕几何: 取"指针所在显示器的工作区"。

为什么需要(均为本机实测):
- Tk 的 `winfo_screenwidth/height` 在 Windows 上**只反映主显示器**——双屏各 1920 时
  它仍返回 1920。拿它做"提示放不下就翻转"的判断, 等于用主屏的尺寸去衡量副屏的位置。
- 副屏可能整体位于**负坐标区**: 本机副屏在主屏左侧, 矩形为 (-1920,0)-(0,1080),
  指针 x 会是 -800 这类负数。任何 `max(0, x)` / `max(4, x)` 式的钳制都会把窗口
  拽回主屏——这正是提示"错位留在原屏"的直接原因。
- `rcWork` 已扣除任务栏(本机主屏 work 底部 1032 < 1080), 按工作区定位可避免提示被
  任务栏压住。

坐标一致性: 本进程 DPI 感知状态与 Tk 相同(都 unaware), 实测两种模式下
`GetMonitorInfo` 与 Tk 的 `e.x_root` / `winfo_x()` 数值完全相同, 无需换算。
"""
import ctypes
import sys
from typing import NamedTuple


class Rect(NamedTuple):
    """屏幕矩形(虚拟桌面绝对坐标, 副屏在主屏左/上侧时取负值)"""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom


class _WINRECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _WINRECT),
        ("rcWork", _WINRECT),
        ("dwFlags", ctypes.c_ulong),
    ]


_user32 = None
_MONITORENUMPROC = None

if sys.platform == "win32":
    try:
        from ctypes import wintypes

        _user32 = ctypes.windll.user32
        _MONITORENUMPROC = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
            ctypes.POINTER(_WINRECT), wintypes.LPARAM,
        )
    except Exception:                    # pragma: no cover - 极端环境下退回主屏
        _user32 = None
        _MONITORENUMPROC = None


def _to_rect(r: _WINRECT) -> Rect:
    return Rect(r.left, r.top, r.right, r.bottom)


def all_monitors() -> list:
    """枚举所有显示器, 返回 [(显示器矩形, 工作区矩形)]; 失败返回空列表。

    每次调用都重新枚举: 实测单次开销 < 1ms, 且能自动跟上显示器热插拔/改分辨率。
    """
    if _user32 is None:
        return []
    found = []

    def _cb(hmon, _hdc, _lprc, _lparam):
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if _user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            found.append((_to_rect(mi.rcMonitor), _to_rect(mi.rcWork)))
        return True

    try:
        _user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(_cb), 0)
    except Exception:                    # pragma: no cover
        return []
    return found


def _dist2(r: Rect, x: int, y: int) -> int:
    """点到矩形的距离平方(0 表示点在矩形内), 用于兜底选最近的显示器"""
    dx = max(r.left - x, 0, x - r.right)
    dy = max(r.top - y, 0, y - r.bottom)
    return dx * dx + dy * dy


def work_area_at(x: int, y: int):
    """点 (x, y) 所在显示器的工作区(已扣任务栏); 拿不到则返回 None。

    None 表示调用方应退回自己的兜底方案(见 tooltip._TipWindow.show)。
    """
    mons = all_monitors()
    if not mons:
        return None
    for mon, work in mons:
        if mon.contains(x, y):
            return work
    # 点在显示器阵列之外(理论上不该出现, 例如多屏排列有缝) → 取最近的显示器
    return min(mons, key=lambda mw: _dist2(mw[0], x, y))[1]


def virtual_bounds():
    """整个虚拟桌面的边界(多屏拼合); 失败返回 None"""
    if _user32 is None:
        return None
    try:
        x = _user32.GetSystemMetrics(76)          # SM_XVIRTUALSCREEN
        y = _user32.GetSystemMetrics(77)          # SM_YVIRTUALSCREEN
        w = _user32.GetSystemMetrics(78)          # SM_CXVIRTUALSCREEN
        h = _user32.GetSystemMetrics(79)          # SM_CYVIRTUALSCREEN
    except Exception:                             # pragma: no cover
        return None
    return Rect(x, y, x + w, y + h)


def describe() -> str:
    """当前显示器拓扑的一行摘要(调试/日志用)"""
    mons = all_monitors()
    if not mons:
        return "无法枚举显示器(非 Windows 或 API 失败)"
    parts = []
    for i, (mon, work) in enumerate(mons):
        tag = "主屏" if i == 0 else f"屏{i}"
        parts.append(f"{tag} {mon.left},{mon.top}-{mon.right},{mon.bottom} "
                     f"(work 底 {work.bottom})")
    return "; ".join(parts)


if __name__ == "__main__":                        # 手动查看拓扑
    print(describe())
    print("虚拟桌面:", virtual_bounds())
