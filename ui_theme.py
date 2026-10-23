# -*- coding: utf-8 -*-
"""界面配色与原生控件样式。

参数行使用经典 tk 控件而非 CTk 控件: CTk 控件每次尺寸变化都要重绘抗锯齿圆角
画布, 227 行参数曾导致拖拽缩放窗口单步耗时 ~45ms; 原生控件无此开销。
这里集中定义这些控件需要的颜色与 ttk 样式。
"""

from tkinter import ttk

SURFACE = "gray17"        # 面板底色 (CTkFrame 深色)
ENTRY_BG = "#343638"      # 输入框底色 (CTkEntry 深色)
ENTRY_BORDER = "#565B5E"  # 边框/箭头按钮色
# CTkComboBox 专用: 默认按钮底色/边框取自 CTk 主题(偏亮的 gray, 在深色界面上像"白框"),
# 这里压到与输入框同族的深灰, 边框保持 1px 仅作分隔
COMBO_BORDER = "#454A4E"      # 下拉框边框
COMBO_BTN_BG = "#3C4145"      # 右侧箭头按钮底色
COMBO_BTN_HOVER = "#4A5055"   # 箭头按钮悬停
TEXT = "#DCE4EE"          # 前景色
ACCENT = "#1F6AA5"        # 聚焦高亮 (CTk 主题蓝)
WHITE = "#FFFFFF"
DISABLED_FG = "gray60"

FONT_FAMILY = "Microsoft YaHei UI"
FONT_SIZE = 10
FONT = (FONT_FAMILY, FONT_SIZE)

_style_ready = False


def setup_native_style(root_widget) -> None:
    """配置下拉框的深色 ttk 样式, 与 CTk 界面融为一体(仅执行一次)"""
    global _style_ready
    if _style_ready:
        return
    _style_ready = True

    style = ttk.Style()
    try:
        style.theme_use("clam")   # clam 支持完整的颜色定制
    except Exception:
        pass

    style.configure(
        "Param.TCombobox", fieldbackground=ENTRY_BG, background=ENTRY_BORDER,
        foreground=TEXT, arrowcolor=TEXT, bordercolor=ENTRY_BORDER,
        lightcolor=ENTRY_BG, darkcolor=ENTRY_BG, arrowsize=14, font=FONT,
    )
    style.map(
        "Param.TCombobox",
        fieldbackground=[("readonly", ENTRY_BG), ("focus", ENTRY_BG)],
        foreground=[("disabled", DISABLED_FG)],
        bordercolor=[("focus", ACCENT)],
        lightcolor=[("focus", ACCENT)],
        darkcolor=[("focus", ACCENT)],
    )
    # 下拉列表(列表框)配色
    for opt, val in (
        ("*TCombobox*Listbox.background", ENTRY_BG),
        ("*TCombobox*Listbox.foreground", TEXT),
        ("*TCombobox*Listbox.selectBackground", ACCENT),
        ("*TCombobox*Listbox.selectForeground", WHITE),
    ):
        root_widget.option_add(opt, val)
