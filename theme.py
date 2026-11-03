# -*- coding: utf-8 -*-
"""集中式主题配色 —— llama-shell 的**唯一**颜色来源。

取值方案与 Mo-Shen 启动器一致: 每套主题一个同名字典, 语义化 token, 深/浅对应。
界面代码里不再出现任何十六进制色值, 只通过 ui_theme 的模块级变量取色。

字段说明(前 33 项与 Mo-Shen 完全同名同义)
    bg / bg_soft      窗口底色 / 条带底色
    card / card_line  面板底色 / 面板描边
    divider           卡片内分隔线
    field_*           输入框 底色 / 描边 / 聚焦描边
    ink / text2       主文字 / 次级文字
    muted / faint     弱化文字 / 极弱文字(占位、禁用)
    label_fg / card_title / card_num   标签 / 卡片标题 / 序号
    chip_bg           胶囊、标签页底槽
    btn_*             普通按钮 常态/文字/悬停
    primary_*         主按钮(加载模型)、选中项
    run_*             运行中(主按钮已启动态 / 模型列表运行中)
    disabled_*        禁用态
    check_*           复选框 底色/描边/选中底色/选中勾色
    ok / warn / err   状态色(成功/警告/错误)
    log_*             日志区 底色/文字/成功/失败/选中(终端隐喻: 两套主题都深底)
                      命令预览不用它 —— 那是常驻可见的文本区, 走 field_*/ink 跟主题走
    on_accent         实色块上的文字色

llama-shell 追加的 token
    sel_*             模型列表选中项(常态/文字/悬停)
    danger_*          危险按钮(卸载模型)
    tip_*             悬停提示窗(底色/描边/标题/中文名/说明/脚注)
"""
from __future__ import annotations

LIGHT = {
    "key": "light",
    "label": "浅色",
    "bg": "#FFFFFF",
    "bg_soft": "#FAFAFB",
    "card": "#FBFCFD",
    "card_line": "#E9EBEF",
    "divider": "#E9EBEF",
    "field_bg": "#FFFFFF",
    "field_line": "#E3E6EB",
    "field_focus": "#8F97A3",
    "ink": "#16181D",
    "text2": "#3D424A",
    "muted": "#8A8F98",
    "faint": "#A6ABB3",
    "label_fg": "#5B616B",
    "card_title": "#7D838D",
    "card_num": "#C6CBD2",
    "chip_bg": "#F2F4F6",
    "btn_bg": "#F0F2F5",
    "btn_fg": "#5B616B",
    "btn_active_bg": "#E7EAEF",
    "btn_active_fg": "#16181D",
    "primary_bg": "#16181D",
    "primary_fg": "#FFFFFF",
    "primary_active_bg": "#33373E",
    "run_bg": "#E7F5EC",
    "run_fg": "#116B41",
    "run_active_bg": "#D5EEDF",
    "disabled_bg": "#9AA0AA",
    "disabled_fg": "#FFFFFF",
    "check_bg": "#FFFFFF",
    "check_border": "#C4CAD3",
    "check_on_bg": "#16181D",
    "check_on_fg": "#FFFFFF",
    "ok": "#1F9D55",
    "warn": "#B7791F",
    "err": "#C4372F",
    "log_bg": "#16181D",
    "log_fg": "#7C8797",
    "log_ok": "#6EE7A8",
    "log_err": "#E0837C",
    "log_select_bg": "#2A2E35",
    "on_accent": "#FFFFFF",
    # —— llama-shell 追加 ——
    "sel_bg": "#16181D",
    "sel_fg": "#FFFFFF",
    "sel_active_bg": "#33373E",
    "danger_bg": "#C4372F",
    "danger_fg": "#FFFFFF",
    "danger_active_bg": "#A62A24",
    "tip_bg": "#FFFFFF",
    "tip_line": "#D5DAE1",
    "tip_title": "#16181D",
    "tip_name": "#3D424A",
    "tip_body": "#3D424A",
    "tip_meta": "#8A8F98",
}

DARK = {
    "key": "dark",
    "label": "深色",
    "bg": "#17181C",
    "bg_soft": "#1D1F25",
    "card": "#202229",
    "card_line": "#2E3138",
    "divider": "#2E3138",
    "field_bg": "#17191E",
    "field_line": "#34383F",
    "field_focus": "#707A88",
    "ink": "#E8EAEE",
    "text2": "#B6BBC4",
    "muted": "#868C96",
    "faint": "#63686F",
    "label_fg": "#9AA0A9",
    "card_title": "#A3A9B3",
    "card_num": "#4C5158",
    "chip_bg": "#262932",
    "btn_bg": "#2A2D35",
    "btn_fg": "#D5D9DF",
    "btn_active_bg": "#353942",
    "btn_active_fg": "#FFFFFF",
    "primary_bg": "#E8EAEE",
    "primary_fg": "#17181C",
    "primary_active_bg": "#FFFFFF",
    "run_bg": "#1D3A2B",
    "run_fg": "#7CD6A4",
    "run_active_bg": "#254632",
    "disabled_bg": "#3A3E46",
    "disabled_fg": "#8A9099",
    "check_bg": "#17191E",
    "check_border": "#4B5058",
    "check_on_bg": "#E8EAEE",
    "check_on_fg": "#17181C",
    "ok": "#4CC38A",
    "warn": "#D9A24A",
    "err": "#EE7B72",
    "log_bg": "#101114",
    "log_fg": "#8A919D",
    "log_ok": "#6EE7A8",
    "log_err": "#F09189",
    "log_select_bg": "#2A2E36",
    "on_accent": "#17181C",
    # —— llama-shell 追加 ——
    "sel_bg": "#E8EAEE",
    "sel_fg": "#17181C",
    "sel_active_bg": "#FFFFFF",
    "danger_bg": "#B5473F",
    "danger_fg": "#FFFFFF",
    "danger_active_bg": "#C9574E",
    "tip_bg": "#262932",
    "tip_line": "#3A3E46",
    "tip_title": "#E8EAEE",
    "tip_name": "#B6BBC4",
    "tip_body": "#B6BBC4",
    "tip_meta": "#868C96",
}

THEMES = {"light": LIGHT, "dark": DARK}
DEFAULT_THEME = "dark"


def get_theme(key: str) -> dict:
    """按 key 取主题, 未知 key 回落到默认主题。"""
    return THEMES.get(key) or THEMES[DEFAULT_THEME]


def next_theme_key(key: str) -> str:
    """循环切换到下一个主题。"""
    keys = list(THEMES)
    try:
        idx = keys.index(key)
    except ValueError:
        return DEFAULT_THEME
    return keys[(idx + 1) % len(keys)]
