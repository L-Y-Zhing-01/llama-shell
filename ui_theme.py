# -*- coding: utf-8 -*-
"""界面配色装载 + 原生控件样式 + CTk 全局主题。

色值唯一来源是 theme.py(深/浅两套, 语义 token 与 Mo-Shen 一致)。本模块只做三件事:
  1. 把当前主题灌进模块级变量 —— 界面代码只认这些名字, 不写十六进制色值;
  2. 把同一个调色板写进 customtkinter 的全局主题表(CTk 控件按它取默认色);
  3. 配置 ttk(clam) 与 Tk option 的下拉列表配色。

**调用方必须 `import ui_theme as ut` 后用 `ut.INK` 取色, 不能 from-import**:
换主题时模块变量会被重新赋值, from-import 绑死的是旧字符串, 换肤不会生效。

参数行使用经典 tk 控件而非 CTk 控件: CTk 控件每次尺寸变化都要重绘抗锯齿圆角
画布, 227 行参数曾导致拖拽缩放窗口单步耗时 ~45ms; 原生控件无此开销。
"""

import ctypes
import os
from tkinter import ttk

import customtkinter as ctk

import theme as palettes

FONT_FAMILY = "Microsoft YaHei UI"
FONT_SIZE = 10
FONT = (FONT_FAMILY, FONT_SIZE)

# 当前生效的主题 key
CURRENT = palettes.DEFAULT_THEME

# ────────────────────────── 调色板变量(apply 时重新赋值) ──────────────────────────
BG = BG_SOFT = CARD = CARD_LINE = DIVIDER = ""
FIELD_BG = FIELD_LINE = FIELD_FOCUS = ""
INK = TEXT2 = MUTED = FAINT = ""
LABEL_FG = CARD_TITLE = CARD_NUM = CHIP_BG = ""
BTN_BG = BTN_FG = BTN_ACTIVE_BG = BTN_ACTIVE_FG = ""
PRIMARY_BG = PRIMARY_FG = PRIMARY_ACTIVE_BG = ""
RUN_BG = RUN_FG = RUN_ACTIVE_BG = ""
DISABLED_BG = DISABLED_FG = ""
CHECK_BG = CHECK_BORDER = CHECK_ON_BG = CHECK_ON_FG = ""
OK = WARN = ERR = ""
LOG_BG = LOG_FG = LOG_OK = LOG_ERR = LOG_SELECT_BG = ON_ACCENT = ""
SEL_BG = SEL_FG = SEL_ACTIVE_BG = ""
DANGER_BG = DANGER_FG = DANGER_ACTIVE_BG = ""
TIP_BG = TIP_LINE = TIP_TITLE = TIP_NAME = TIP_BODY = TIP_META = ""

# 旧名兼容(界面里仍有一批 SURFACE/ENTRY_* 的叫法, 语义完全对应)
SURFACE = ENTRY_BG = ENTRY_BORDER = ""
COMBO_BORDER = COMBO_BTN_BG = COMBO_BTN_HOVER = ""
TEXT = ACCENT = WHITE = ""


def apply(key: str) -> dict:
    """切换主题: 灌模块变量 → 重写 CTk 全局主题表。返回所用调色板。

    只改"取色来源", 已经建好的控件不会变色 —— 换肤必须重建界面
    (见 main.App._swap_theme), 这是 Mo-Shen 方案里定下的规矩。
    """
    global CURRENT
    pal = palettes.get_theme(key)
    CURRENT = pal["key"]

    g = globals()
    for name, token in _TOKENS.items():
        g[name] = pal[token]

    _apply_ctk(pal)
    try:
        ctk.set_appearance_mode("dark" if CURRENT == "dark" else "light")
    except Exception:
        pass
    return pal


# token → 模块变量名(子集足够, 其余按需)
_TOKENS = {
    "BG": "bg",
    "BG_SOFT": "bg_soft",
    "CARD": "card",
    "CARD_LINE": "card_line",
    "DIVIDER": "divider",
    "FIELD_BG": "field_bg",
    "FIELD_LINE": "field_line",
    "FIELD_FOCUS": "field_focus",
    "INK": "ink",
    "TEXT2": "text2",
    "MUTED": "muted",
    "FAINT": "faint",
    "LABEL_FG": "label_fg",
    "CARD_TITLE": "card_title",
    "CARD_NUM": "card_num",
    "CHIP_BG": "chip_bg",
    "BTN_BG": "btn_bg",
    "BTN_FG": "btn_fg",
    "BTN_ACTIVE_BG": "btn_active_bg",
    "BTN_ACTIVE_FG": "btn_active_fg",
    "PRIMARY_BG": "primary_bg",
    "PRIMARY_FG": "primary_fg",
    "PRIMARY_ACTIVE_BG": "primary_active_bg",
    "RUN_BG": "run_bg",
    "RUN_FG": "run_fg",
    "RUN_ACTIVE_BG": "run_active_bg",
    "DISABLED_BG": "disabled_bg",
    "DISABLED_FG": "disabled_fg",
    "CHECK_BG": "check_bg",
    "CHECK_BORDER": "check_border",
    "CHECK_ON_BG": "check_on_bg",
    "CHECK_ON_FG": "check_on_fg",
    "OK": "ok",
    "WARN": "warn",
    "ERR": "err",
    "LOG_BG": "log_bg",
    "LOG_FG": "log_fg",
    "LOG_OK": "log_ok",
    "LOG_ERR": "log_err",
    "LOG_SELECT_BG": "log_select_bg",
    "ON_ACCENT": "on_accent",
    "SEL_BG": "sel_bg",
    "SEL_FG": "sel_fg",
    "SEL_ACTIVE_BG": "sel_active_bg",
    "DANGER_BG": "danger_bg",
    "DANGER_FG": "danger_fg",
    "DANGER_ACTIVE_BG": "danger_active_bg",
    "TIP_BG": "tip_bg",
    "TIP_LINE": "tip_line",
    "TIP_TITLE": "tip_title",
    "TIP_NAME": "tip_name",
    "TIP_BODY": "tip_body",
    "TIP_META": "tip_meta",
    # 旧名
    "SURFACE": "card",
    "ENTRY_BG": "field_bg",
    "ENTRY_BORDER": "field_line",
    "COMBO_BORDER": "field_line",
    "COMBO_BTN_BG": "btn_bg",
    "COMBO_BTN_HOVER": "btn_active_bg",
    "TEXT": "ink",
    "ACCENT": "field_focus",
    "WHITE": "on_accent",
}


def _apply_ctk(pal: dict) -> None:
    """把调色板写进 customtkinter 的全局主题表。

    CTk 控件在 **创建时** 从 ThemeManager.theme 取默认色, 所以这里改完只影响
    之后新建的控件 —— 正好配合"换肤即重建"的做法, 不必逐个控件 configure。
    只覆盖用得到的段落, 其余保持 CTk 原样。
    """
    t = ctk.ThemeManager.theme

    def put(section: str, **kw) -> None:
        node = t.get(section)
        if isinstance(node, dict):
            for k, v in kw.items():
                if k in node:
                    node[k] = v

    put("CTk", fg_color=pal["bg"])
    put("CTkFrame", fg_color=pal["card"], top_fg_color=pal["bg_soft"],
        border_color=pal["card_line"])
    put("CTkToplevel", fg_color=pal["bg"])
    put("CTkLabel", text_color=pal["ink"])
    put("CTkButton", fg_color=pal["btn_bg"], hover_color=pal["btn_active_bg"],
        text_color=pal["btn_fg"], text_color_disabled=pal["faint"],
        border_color=pal["card_line"])
    put("CTkEntry", fg_color=pal["field_bg"], border_color=pal["field_line"],
        text_color=pal["ink"], placeholder_text_color=pal["faint"])
    put("CTkComboBox", fg_color=pal["field_bg"], border_color=pal["field_line"],
        button_color=pal["btn_bg"], button_hover_color=pal["btn_active_bg"],
        text_color=pal["ink"])
    put("DropdownMenu", fg_color=pal["card"], hover_color=pal["chip_bg"],
        text_color=pal["ink"])
    put("CTkCheckBox", fg_color=pal["check_on_bg"], hover_color=pal["check_on_bg"],
        border_color=pal["check_border"], checkmark_color=pal["check_on_fg"],
        text_color=pal["ink"], text_color_disabled=pal["faint"])
    put("CTkSwitch", progress_color=pal["primary_bg"], button_color=pal["card"],
        button_hover_color=pal["card"], text_color=pal["ink"],
        fg_color=pal["chip_bg"])
    put("CTkProgressBar", fg_color=pal["chip_bg"], progress_color=pal["ok"],
        border_color=pal["chip_bg"])
    put("CTkScrollbar", button_color=pal["card_line"],
        button_hover_color=pal["field_line"], fg_color="transparent")
    put("CTkTextbox", fg_color=pal["field_bg"], border_color=pal["card_line"],
        text_color=pal["ink"], scrollbar_button_color=pal["card_line"],
        scrollbar_button_hover_color=pal["field_line"])
    put("CTkSegmentedButton", fg_color=pal["chip_bg"],
        selected_color=pal["primary_bg"], selected_hover_color=pal["primary_active_bg"],
        unselected_color=pal["chip_bg"], unselected_hover_color=pal["btn_active_bg"],
        text_color=pal["ink"], text_color_disabled=pal["faint"])
    put("CTkScrollableFrame", label_fg_color=pal["chip_bg"],
        label_text_color=pal["muted"])


_root_for_options = None


def setup_native_style(root_widget=None) -> None:
    """配置 ttk 下拉框与 Tk 下拉列表配色(每次建面板都会重设, 换肤后自动跟随)。

    ttk 必须用 clam 引擎: Windows 的 vista 引擎改不了 Combobox 底色。
    """
    global _root_for_options
    if root_widget is not None:
        _root_for_options = root_widget

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass

    style.configure(
        "Param.TCombobox", fieldbackground=ENTRY_BG, background=ENTRY_BORDER,
        foreground=TEXT, arrowcolor=MUTED, bordercolor=ENTRY_BORDER,
        lightcolor=ENTRY_BG, darkcolor=ENTRY_BG, arrowsize=14, font=FONT,
    )
    style.map(
        "Param.TCombobox",
        fieldbackground=[("readonly", ENTRY_BG), ("focus", ENTRY_BG)],
        foreground=[("disabled", FAINT)],
        arrowcolor=[("disabled", FAINT)],
        bordercolor=[("focus", ACCENT)],
        lightcolor=[("focus", ACCENT)],
        darkcolor=[("focus", ACCENT)],
    )

    if _root_for_options is None:
        return
    # 展开后的列表是独立的 Tk 窗口, 只能靠 option 数据库改色
    for opt, val in (
        ("*TCombobox*Listbox.background", ENTRY_BG),
        ("*TCombobox*Listbox.foreground", TEXT),
        ("*TCombobox*Listbox.selectBackground", PRIMARY_BG),
        ("*TCombobox*Listbox.selectForeground", PRIMARY_FG),
    ):
        _root_for_options.option_add(opt, val)


def apply_titlebar_theme(root, dark: bool) -> None:
    """让系统标题栏跟随深浅主题(Win10 1809+ / Win11 生效, 旧系统静默忽略)。"""
    if os.name != "nt":
        return
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        if not hwnd:
            return
        value = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE(19 为旧版取值)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    ctypes.c_void_p(hwnd), attr, ctypes.byref(value), 4) == 0:
                return
    except Exception:
        pass


# 导入即生效: 界面模块可能在 apply 之前就取色
apply(CURRENT)
