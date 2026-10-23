# -*- coding: utf-8 -*-
"""通用悬停提示(tooltip): 参数名按 help 原样显示后, 中文含义改由这里给出。

设计要点(均为实测/权衡结论, 改动前请先复测):
- **全进程共用一个 Toplevel**: 每个参数行各建一个会多出 250 个真实窗口对象。
- **提示窗弹在指针右下侧偏移位**: 指针不会压在提示窗上, 否则会触发所在行的
  <Leave> → 隐藏 → 指针回到行上 → 再 <Enter>, 形成"显示/隐藏"抖动。
- **显示有延迟, 隐藏有宽限**: 划过时不会一路闪提示; 在行内从标签移到输入框会
  先 <Leave> 再 <Enter>, 靠 HIDE_GRACE 的宽限期把这次"离开"吃掉, 避免闪一下。
- overrideredirect + topmost: 不抢焦点、不打断输入框编辑、始终压在 CTk 窗口之上。
- **定位以"指针所在显示器的工作区"为界**(见 screens 模块): Tk 的
  winfo_screenwidth/height 只反映主显示器, 且副屏坐标可能是负数, 用主屏尺寸判断
  或把坐标钳到 >=0 都会让提示跑到主屏上(详见 place() 的注释)。
"""

import tkinter as tk

from screens import Rect, work_area_at
from ui_theme import ENTRY_BORDER, FONT_FAMILY

TIP_BG = "#242424"
TIP_TITLE = "#6FB3E0"      # 参数名: 偏亮蓝, 与强调色同族
TIP_NAME = "#DCE4EE"       # 中文名
TIP_BODY = "#AFBAC6"       # 中文说明
TIP_META = "#8A939E"       # 类型/默认值等脚注
WRAP = 430                 # 说明文字换行宽度(px)
DELAY = 420                # 悬停多久后弹出(ms)
HIDE_GRACE = 90            # 离开后延迟隐藏(ms), 用于吃掉行内控件间的 <Leave>
OFFSET = (16, 22)          # 相对指针的偏移(px)
FLIP_X = 8                 # 翻到指针左侧时的空隙(px)
FLIP_Y = 12                # 翻到指针上方时的空隙(px)
EDGE = 4                   # 提示窗与工作区边缘的最小间距(px)
POINTER_PAD = 6            # 判定"提示盖住指针"时给指针留的缓冲(px)
PAD = 9


def _clamp(v: int, lo: int, hi: int) -> int:
    if hi < lo:                       # 可用区域比提示窗还小 → 贴左上, 至少露出开头
        return lo
    return min(max(v, lo), hi)


def _hits_pointer(px, py, w, h, x, y, pad=POINTER_PAD) -> bool:
    """提示矩形是否盖住了指针(含缓冲)。

    盖住指针会触发行 <Leave> → 提示被收 → 指针又落回行上 → 再 <Enter>,
    形成"显示/隐藏"抖动(见模块开头的说明), 所以钳制时要避开指针。
    """
    return (px - pad <= x <= px + w + pad) and (py - pad <= y <= py + h + pad)


def place(x: int, y: int, w: int, h: int, area: Rect):
    """把 w×h 的提示窗摆在指针 (x, y) 旁, 且完全落在 area(指针所在显示器的工作区)内。

    **area 允许位于负坐标区**: 副屏在主屏左侧/上方时, 本机副屏矩形就是
    (-1920,0)-(0,1080)。因此这里绝不能出现 max(0, px) 这类钳制——它会把提示窗
    拽回主屏, 表现为"程序拖到副屏后提示仍留在原屏"(用户报的就是这个)。
    """
    px, py = x + OFFSET[0], y + OFFSET[1]
    if px + w > area.right - EDGE:             # 右侧放不下 → 翻到指针左边
        px = x - w - FLIP_X
    if py + h > area.bottom - EDGE:            # 下方放不下 → 翻到指针上方
        py = y - h - FLIP_Y
    px = _clamp(px, area.left + EDGE, area.right - w - EDGE)
    py = _clamp(py, area.top + EDGE, area.bottom - h - EDGE)
    if _hits_pointer(px, py, w, h, x, y):      # 钳制后压在指针身下 → 再翻到上方
        alt = _clamp(y - h - FLIP_Y, area.top + EDGE, area.bottom - h - EDGE)
        if not _hits_pointer(px, alt, w, h, x, y):
            py = alt
    return px, py


class _TipWindow:
    """单例提示窗(通过 get/show/hide 使用, 不要自行实例化)"""

    _inst: "_TipWindow | None" = None

    @classmethod
    def get(cls, master=None) -> "_TipWindow":
        if cls._inst is None or not cls._inst.top.winfo_exists():
            cls._inst = cls(master)
        return cls._inst

    def __init__(self, master=None):
        self.top = tk.Toplevel(master)
        self.top.withdraw()
        self.top.overrideredirect(True)          # 去掉标题栏/边框, 不进任务栏
        try:
            self.top.attributes("-topmost", True)
        except tk.TclError:
            pass

        outer = tk.Frame(self.top, bg=ENTRY_BORDER, bd=0, highlightthickness=0)  # 1px 描边
        outer.pack(fill="both", expand=True)
        inner = tk.Frame(outer, bg=TIP_BG, bd=0, highlightthickness=0)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.box = tk.Frame(inner, bg=TIP_BG)
        self.box.pack(fill="both", expand=True, padx=PAD, pady=PAD)

        kw = {"bg": TIP_BG, "justify": "left", "anchor": "w", "wraplength": WRAP}
        self.l_title = tk.Label(self.box, fg=TIP_TITLE, font=(FONT_FAMILY, 9, "bold"), **kw)
        self.l_name = tk.Label(self.box, fg=TIP_NAME, font=(FONT_FAMILY, 10), **kw)
        self.l_body = tk.Label(self.box, fg=TIP_BODY, font=(FONT_FAMILY, 9), **kw)
        self.l_meta = tk.Label(self.box, fg=TIP_META, font=(FONT_FAMILY, 8), **kw)
        self._labels = (self.l_title, self.l_name, self.l_body, self.l_meta)

    def show(self, title: str, name: str, body: str, meta: str, x: int, y: int) -> None:
        """在指针 (x, y) 附近弹出提示; 空字段自动收起对应行"""
        packed = []
        for widget, text in zip(self._labels, (title, name, body, meta)):
            widget.pack_forget()
            widget.configure(text=text or "")
            if text:
                widget.pack(fill="x", pady=(4 if packed else 0, 4))
                packed.append(widget)
        if packed:
            packed[-1].pack_configure(pady=(4 if len(packed) > 1 else 0, 0))

        # 先量尺寸、再定位、最后显示: withdraw 状态下 update_idletasks 也能算出请求
        # 尺寸, 这样不会"先在旧位置闪一帧再跳到新位置"(副屏尤其明显)。
        self.top.update_idletasks()
        w, h = self.top.winfo_reqwidth(), self.top.winfo_reqheight()

        area = work_area_at(x, y)
        if area is None:                          # 非 Windows / API 失败 → 退回主屏尺寸
            area = Rect(0, 0, self.top.winfo_screenwidth(),
                        self.top.winfo_screenheight())
        px, py = place(x, y, w, h, area)
        try:
            # px/py 可能为负(副屏在主屏左侧): "+-100+50" 才是 Tk 的**绝对负坐标**写法,
            # 若写成 "-100+50" 会被解释成"距屏幕右边缘 100px", 反而跑错位置。
            self.top.geometry(f"+{px}+{py}")
        except tk.TclError:                       # 极端情况兜底, 别让提示消失
            self.top.geometry(f"+{area.left + EDGE}+{area.top + EDGE}")
        self.top.deiconify()
        self.top.lift()

    def hide(self) -> None:
        try:
            self.top.withdraw()
        except tk.TclError:
            pass


def _descendants(widget) -> list:
    """控件自身 + 其全部后代(参数行的标签/输入框都要能触发提示)。

    跳过 Toplevel 型后代: ttk.Combobox 内部带有 popdown(下拉列表)这类独立窗口,
    把提示绑上去会在展开列表时误弹提示。
    """
    out = [widget]
    for child in widget.winfo_children():
        if isinstance(child, tk.Toplevel):
            continue
        out.extend(_descendants(child))
    return out


class Tooltip:
    """把悬停提示绑到一个控件及其所有子控件上。

    content_fn() 返回 (title, name, body, meta), 每次弹出时求值, 内容可动态生成。
    """

    def __init__(self, widget, content_fn, delay: int = DELAY):
        self.widget = widget
        self.content_fn = content_fn
        self.delay = delay
        self._show_id = None
        self._hide_id = None
        self._shown = False
        for w in _descendants(widget):
            # add="+" 追加绑定: 不能覆盖原本的 FocusOut/Return 等绑定
            w.bind("<Enter>", self._on_enter, add="+")
            w.bind("<Leave>", self._on_leave, add="+")
            w.bind("<ButtonPress>", self._on_press, add="+")
        widget.bind("<Destroy>", self._on_destroy, add="+")

    # ── 内部 ──
    def _cancel(self, attr: str) -> None:
        aid = getattr(self, attr)
        if aid is not None:
            try:
                self.widget.after_cancel(aid)
            except Exception:
                pass
            setattr(self, attr, None)

    def _on_enter(self, e) -> None:
        self._cancel("_hide_id")                  # 行内换控件: 吃掉刚才的"离开"
        if self._show_id is None and not self._shown:
            x, y = e.x_root, e.y_root
            try:
                self._show_id = self.widget.after(self.delay, lambda: self._show(x, y))
            except Exception:
                self._show_id = None

    def _on_leave(self, _e=None) -> None:
        self._cancel("_show_id")
        if self._shown:                           # 已显示 → 宽限期后收
            try:
                self._hide_id = self.widget.after(HIDE_GRACE, self._do_hide)
            except Exception:
                self._do_hide()
        else:
            self._do_hide()

    def _on_press(self, _e=None) -> None:
        """点击(如展开下拉框)时立即收起, 免得盖住弹出的列表"""
        self._cancel("_show_id")
        self._do_hide()

    def _show(self, x: int, y: int) -> None:
        self._show_id = None
        try:
            content = self.content_fn()
        except Exception:
            return
        if not content:
            return
        _TipWindow.get(self.widget.winfo_toplevel()).show(*content, x, y)
        self._shown = True

    def _do_hide(self) -> None:
        self._hide_id = None
        self._shown = False
        if _TipWindow._inst is not None:          # 没有实例就没什么可收的
            _TipWindow._inst.hide()

    def _on_destroy(self, _e=None) -> None:
        self._cancel("_show_id")
        self._cancel("_hide_id")
