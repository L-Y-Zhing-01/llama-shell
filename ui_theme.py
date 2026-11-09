"""主题层 —— 封装 ttkbootstrap 的 Style。

设计原则
--------
1. **颜色唯一来源**是当前 ttkbootstrap 主题的 16 个槽位（bg / fg / primary / …）。
   本模块不持有任何十六进制色值，也不再维护双调色板（旧的 theme.py 已废弃）。
2. **ttk 控件创建后自动跟随 `theme_use()`**：不登记、不重放、不重建界面。
   换肤成本只与控件数量线性相关（实测 1360 个控件约 54ms）。
3. 只有"非 ttk"的两类东西需要在这里手工取色:
   · Windows 标题栏（DwmSetWindowAttribute）
   · tooltip 那个独立 Toplevel
4. 需要跟着主题变的**自定义 style**（状态栏、提示文字、模型列表等）统一写在
   `_rebuild_styles()`，由 `Style.on_theme_change()` 在每次切换后自动重跑 ——
   这是 ttkbootstrap 官方钩子，也是 `theme_use()` 会清空显式配色时唯一可靠的
   补救点（实测：自定义 background 在切换后被重置成新主题的默认值）。

对外开放接口
------------
    bind(style, key)                 窗口建好后调一次
    set_theme(name)                  切换
    CURRENT                          当前主题名（模块变量）
    color("bg") / colors()           取当前主题色
    theme_palette(name)              取任意主题的色（不切换，用于预览）
    all_themes() / THEME_FAMILIES    主题清单
    apply_titlebar_theme(root, dark) Windows 标题栏跟随深浅
"""

from __future__ import annotations

import ctypes
import tkinter as tk
import tkinter.font as tkfont

from ttkbootstrap.style import Style

# ------------------------------------------------------------------ 字体
FONT_FAMILY = "Microsoft YaHei UI"
FONT_SIZE = 10
FONT = (FONT_FAMILY, FONT_SIZE)
MONO_FONT = ("Consolas", FONT_SIZE)      # 状态栏、命令预览

# ------------------------------------------------------------------ 主题清单
DEFAULT_THEME = "bootstrap-dark"

#: (family, 中文名, 一句话描述) —— 顺序即主题选择器里的排列顺序
THEME_FAMILIES: list[tuple[str, str, str]] = [
    ("bootstrap",   "Bootstrap",   "经典蓝，对比清晰"),
    ("nord",        "Nord",        "北欧冷灰，低饱和"),
    ("catppuccin",  "Catppuccin",  "柔和紫灰，糖果感"),
    ("dracula",     "Dracula",     "紫夜，暗色高对比"),
    ("tokyo-night", "Tokyo Night", "深夜蓝紫，不刺眼"),
    ("one",         "Atom One",    "编辑器中性灰"),
    ("gruvbox",     "Gruvbox",     "复古暖褐，高辨识"),
    ("solarized",   "Solarized",   "暖砂配青，护眼"),
    ("everforest",  "Everforest",  "林间绿，自然柔和"),
    ("pydata",      "PyData",      "青碧色，偏技术感"),
    ("vapor",       "Vapor",       "霓虹紫青，赛博"),
    ("minty",       "Minty",       "薄荷绿，清爽"),
    ("pulse",       "Pulse",       "脉冲紫，低饱和"),
    ("united",      "United",      "砖红，稳重"),
    ("sandstone",   "Sandstone",   "砂岩灰褐"),
]

_MODES = ("light", "dark")

# ------------------------------------------------------------------ 状态
CURRENT = DEFAULT_THEME
_STYLE: Style | None = None


# ------------------------------------------------------------------ 主题名工具
def all_themes() -> list[str]:
    """全部 30 个主题名，按组内浅/深成对排列。"""
    return [f"{fam}-{mode}" for fam, _, _ in THEME_FAMILIES for mode in _MODES]


def family_of(name: str) -> str:
    return name.rsplit("-", 1)[0] if name else ""


def mode_of(name: str) -> str:
    return "dark" if name.endswith("-dark") else "light"


def is_dark(name: str) -> bool:
    return mode_of(name) == "dark"


def family_label(name: str) -> str:
    fam = family_of(name)
    for key, label, _ in THEME_FAMILIES:
        if key == fam:
            return label
    return fam


def display_name(name: str) -> str:
    return f"{family_label(name)} · {'深色' if is_dark(name) else '浅色'}"


def is_valid(name: str) -> bool:
    return name in all_themes()


# ------------------------------------------------------------------ 绑定与切换
def bind(style_obj: Style, key: str | None = None) -> None:
    """绑定 ttkbootstrap 的 Style 单例（窗口建好后调用一次）。

    `on_theme_change` 注册时会立即执行一次回调，因此自定义 style
    在第一次换肤之前就已经就位。
    """
    global _STYLE, CURRENT
    _STYLE = style_obj
    if key:
        CURRENT = key
    _setup_fonts(getattr(style_obj, "master", None))
    style_obj.on_theme_change(_rebuild_styles)


def style() -> Style:
    if _STYLE is None:
        raise RuntimeError("ui_theme.bind() 尚未调用")
    return _STYLE


def colors():
    """当前主题的配色对象（属性式访问：.bg / .fg / .primary …）"""
    return style().colors


def color(key: str) -> str:
    """按名字取一个颜色槽。可用：bg fg primary secondary success info warning
    danger light dark selectbg selectfg border inputfg inputbg active"""
    return getattr(style().colors, key)


def theme_palette(name: str):
    """取**任意**主题的配色对象，不改变当前主题（供预览面板用）。

    优先直接读 Style 的主题定义表；读不到时退化为临时切换再切回
    （老版本 ttkbootstrap 没有 _theme_definitions，这里留个后路）。
    """
    st = style()
    defs = getattr(st, "_theme_definitions", None)
    if isinstance(defs, dict) and name in defs:
        return defs[name].colors
    cur = st.theme_use()
    try:
        st.theme_use(name)
        pal = st.colors
    finally:
        st.theme_use(cur)
    return pal


def set_theme(name: str) -> str:
    """切换主题。名字非法时回落默认值，返回实际生效的主题名。"""
    global CURRENT
    if not is_valid(name):
        name = DEFAULT_THEME
    if name == CURRENT:
        return CURRENT
    style().theme_use(name)
    CURRENT = name
    return CURRENT


# ------------------------------------------------------------------ 自定义样式
#: 次要说明文字
S_HINT = "Hint.TLabel"
#: 状态栏（等宽）
S_STATUS = "Status.TLabel"

#: 模型列表按钮的三种状态样式。
#: ttk 的按钮**不支持** anchor 构造参数（那是 CTk 的写法，直接传会 TclError），
#: 只能经样式表设置；而 ttkbootstrap 的 primary/success/secondary-outline 是公用的，
#: 改它们会波及全程序所有按钮，所以这里克隆一份改名，只把 anchor 换成左对齐。
S_MODEL_IDLE = "ModelIdle.TButton"
S_MODEL_SEL = "ModelSel.TButton"
S_MODEL_RUN = "ModelRun.TButton"

_MODEL_STYLE_SRC = {
    S_MODEL_IDLE: "secondary.Outline.TButton",
    S_MODEL_SEL: "primary.TButton",
    S_MODEL_RUN: "success.TButton",
}

#: 全局紧凑 padding —— 控件的「文字 ↔ 上下边框」留白。
#:
#: 只写**基类样式名**：ttk 的样式选项按名字继承（primary.TButton → TButton），
#: 所以这一张表就能覆盖全部 bootstyle 变体（按钮 31→25px, 输入框 31→25px），
#: 1300 多个控件一个都不用改。标签页内的控件是同一批类，自动跟着变。
#:
#: 为什么不做成"逐个控件 padY"：ttk 控件没有内部留白的实例属性，
#: 想按控件调只能给每个控件配独立 style，必然写成屎山。
#:
#: 注意：
#: · 该表必须由 _rebuild_styles 重放 —— theme_use() 会把 padding 还原成主题默认值；
#: · TCombobox 的 padding 是「左 上 右 下」四值，右侧留给下拉箭头，别压；
#: · round-toggle 的高度由指示器图片决定，padding 对它无效（本来也够矮）；
#: · TLabel 没有 padding，它的高度纯由字体决定，想再矮只能改 FONT_SIZE。
COMPACT_PADDING = {
    "TButton": (8, 1),
    "TEntry": (6, 2),
    "TCombobox": (5, 2, 7, 2),
    "TNotebook.Tab": (6, 2),
    "TRadiobutton": (0, 0),
    "TCheckbutton": (0, 0),
    "TLabelframe": (6, 4),
}


def _rebuild_styles(st: Style) -> None:
    """重建随主题变化的自定义 style。

    必须在每次 theme_use 之后重跑：实测显式配置的 background 会被新主题覆盖，
    on_theme_change 是官方提供的、在样式表重建后执行的钩子。
    """
    c = st.colors
    # 先压全局 padding，后面克隆模型按钮样式时会连紧凑值一起继承过去
    for name, pad in COMPACT_PADDING.items():
        st.configure(name, padding=pad)
    st.configure(S_HINT, font=FONT, foreground=c.secondary)
    st.configure(S_STATUS, font=MONO_FONT, foreground=c.fg)
    for dst, src in _MODEL_STYLE_SRC.items():
        _clone_button_style(st, src, dst, anchor="w", font=FONT)


def _clone_button_style(st: Style, src: str, dst: str, **extra) -> None:
    """把 src 按钮样式的配色（含状态映射）复制成一个新名字，再覆盖少量选项。

    ttkbootstrap 在每次换主题时按 bootstyle 重算配色，自定义样式名不会自动跟着变，
    所以这个函数必须在 on_theme_change 回调里重跑（见 _rebuild_styles）。
    """
    cfg = dict(st.configure(src) or {})
    cfg.pop("style", None)
    st.configure(dst, **cfg)
    mp = st.map(src) or {}
    if mp:
        st.map(dst, **mp)
    if extra:
        st.configure(dst, **extra)


def _setup_fonts(root) -> None:
    """把 Tk 的具名字体换成中文字体，避免 ttk 默认 Segoe UI 下中文发虚。"""
    if root is None:
        return
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(name, root=root).configure(
                family=FONT_FAMILY, size=FONT_SIZE)
        except Exception:
            pass


# ------------------------------------------------------------------ Windows 标题栏
def apply_titlebar_theme(root: tk.Misc, dark: bool) -> None:
    """让系统标题栏跟随深浅色（Win10 1809+ 用 attr 20，更旧的用 19）。"""
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
            ) == 0:
                break
    except Exception:
        pass          # 非 Windows 或系统太旧：静默跳过
