# -*- coding: utf-8 -*-
"""参数界面: 参数行控件与参数面板。

设计要点(均为实测结论, 改动前请先复测):
- 参数行统一用经典 tk 控件, 不用 CTk/ttk——同布局下 30 行参数行的窗口缩放重绘
  成本  CTk 65ms > ttk 30ms > 经典 tk 20ms。
- 每行两列(标签 + 输入控件), 面板按 group 分标签页, 每页两列排布。
- **参数名按 `llama-server --help` 原样显示**(如 `--cache-ram`), 不做中文翻译;
  中文含义与详细说明由悬停提示(tooltip.py)给出。因此标签宽度按"本页最长 flag"
  统一设定(见 ParamsPanel), 同页内输入框才能左端对齐。
"""

import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

from llama_params import PARAM_DEFINITIONS, PARAM_GROUP_LABELS, PARAM_GROUPS, ParamDef
from tooltip import Tooltip
from ui_theme import (
    ACCENT, ENTRY_BG, ENTRY_BORDER, FONT, FONT_FAMILY, SURFACE, TEXT, WHITE,
    setup_native_style,
)

# 参数名字号比取值小一号: flag 最长 29 字符(--spec-draft-cpu-strict-batch),
# 10pt 要 183px、9pt 只要 166px, 两列布局下这一号之差很关键。
FLAG_FONT = (FONT_FAMILY, 9)

# 取值控件的类型 → 悬停提示里的中文说明
_TYPE_CN = {
    "int": "整数", "float": "小数", "str": "文本", "path": "路径",
    "bool": "开关", "bool_pair": "三态开关", "choice": "下拉选择",
}


def _default_text(param: ParamDef) -> str:
    """把默认值写成好读的中文(界面上的空白/None 各有含义)"""
    d = param.default
    if d is None:
        return "跟随服务端默认"
    if d == "":
        return "空 (不传该参数)"
    if d is True:
        return "开"
    if d is False:
        return "关"
    return str(d)


def _tooltip_content(param: ParamDef) -> tuple[str, str, str, str]:
    """(参数名及等价写法, 中文名, 中文说明, 类型/默认值脚注)"""
    names = [param.cli_flag]
    for f in (param.cli_short, param.alias, param.negation_flag, *param.extra_flags):
        if f and f not in names:
            names.append(f)
    # 用 "·" 显式分隔各写法: 两个空格连读会被误认成一个长 flag
    # (实测踩过: "--cache-list  -cl" 被看成不存在的 "--cache-list-cl")
    title = " · ".join(names)

    name = param.label if param.label and param.label != param.cli_flag else ""
    body = param.help_text or ""

    meta = f"类型: {_TYPE_CN.get(param.type, param.type)}    默认: {_default_text(param)}"
    if param.choices:
        shown = ", ".join(str(c) or "空" for c in param.choices[:12])
        if len(param.choices) > 12:
            shown += f" … (共 {len(param.choices)} 项)"
        meta += f"\n可选: {shown}"
    return title, name, body, meta


class ParamRow(tk.Frame):
    """参数行基类: Label(参数名) + 输入控件"""

    LABEL_PADX = (6, 10)          # 参数名列左右留白, 见 ParamsPanel 里 label_px 的算法

    def __init__(self, parent, param: ParamDef, on_change=None):
        super().__init__(parent, bg=SURFACE)
        self.param = param
        self.on_change = on_change
        # 第 0 列是参数名(其 minsize 由面板按本页最长参数名统一设定), 第 1 列吃掉剩余宽度
        self.grid_columnconfigure(1, weight=1)

        # 参数名与 --help 保持一致, 不翻译; 中文含义走悬停提示。
        # 只有 extra_args 没有对应的 flag(它是自由格式的附加参数), 退化为显示中文名。
        # 这里刻意不用 tk.Label 的 width: 它的单位是"字符数", 会按字体的平均字宽放大
        # ——29 字符的 flag 会被算成 205px, 实际只需 168px, 白白挤掉输入框。
        self.label = tk.Label(
            self, text=param.cli_flag or param.label or param.key,
            bg=SURFACE, fg=TEXT, anchor="w", bd=0, font=FLAG_FONT,
        )
        self.label.grid(row=0, column=0, padx=self.LABEL_PADX, pady=4, sticky="w")
        # 注意: 悬停提示**不能在这里挂**——子类的输入控件(entry/combo/check)是在
        # super().__init__() 之后才创建的, 在这里挂只能覆盖到行框架与参数名标签,
        # 鼠标移到输入框上就不会弹提示。统一由 create_param_row 在整行建好后挂。

    def get_value(self):
        raise NotImplementedError

    def set_value(self, val):
        raise NotImplementedError

    def _changed(self):
        if self.on_change:
            self.on_change()


# 参数名与输入框的默认请求宽度只求"够放", 真正的宽度由网格分配:
# tk.Entry/ttk.Combobox 不显式指定 width 时请求 20 字符(~166px), 两列布局下
# 这个请求宽度会把内容撑出滚动区, 逼出横向滚动条。
_ENTRY_WIDTH_CHARS = 10

def _native_entry(parent) -> tk.Entry:
    """创建与 CTk 深色主题一致的经典 tk 输入框"""
    return tk.Entry(
        parent, bg=ENTRY_BG, fg=TEXT, insertbackground=TEXT,
        selectbackground=ACCENT, selectforeground=WHITE,
        relief="flat", highlightthickness=1,
        highlightbackground=ENTRY_BORDER, highlightcolor=ACCENT,
        font=FONT, width=_ENTRY_WIDTH_CHARS,
    )


class EntryParamRow(ParamRow):
    """文本输入行: int / float / str / path 共用一个实现, 只差取值转换与显示格式"""

    convert = staticmethod(str)   # 文本 → 值
    g_format = False              # 浮点值是否用 %g 显示(去掉多余的 .0)

    def __init__(self, parent, param, on_change=None, **kw):
        super().__init__(parent, param, on_change, **kw)
        self.entry = _native_entry(self)
        self.entry.grid(row=0, column=1, padx=5, pady=4, sticky="ew")
        self.entry.bind("<FocusOut>", lambda e: self._changed())
        self.entry.bind("<Return>", lambda e: self._changed())

    def get_value(self):
        try:
            return self.convert(self.entry.get())
        except (ValueError, TypeError):
            return self.param.default

    def set_value(self, val):
        self.entry.delete(0, "end")
        if val is None or val == "":
            text = ""
        elif self.g_format and isinstance(val, float):
            text = f"{val:g}"
        else:
            text = str(val)
        self.entry.insert(0, text)


class IntParamRow(EntryParamRow):
    convert = staticmethod(int)


class FloatParamRow(EntryParamRow):
    convert = staticmethod(float)
    g_format = True


class StrParamRow(EntryParamRow):
    pass


class ChoiceParamRow(ParamRow):
    """下拉选择行(choice); 空字符串在界面上显示为「未设置」"""

    UNSET = "未设置"

    def __init__(self, parent, param, on_change=None, **kw):
        super().__init__(parent, param, on_change, **kw)
        self._raw_choices = list(param.choices or [])
        self._display_choices = [c if c != "" else self.UNSET for c in self._raw_choices]

        self.combo = ttk.Combobox(
            self, values=self._display_choices, style="Param.TCombobox",
            width=_ENTRY_WIDTH_CHARS,
        )
        self.combo.grid(row=0, column=1, padx=5, pady=4, sticky="ew")
        self.combo.bind("<<ComboboxSelected>>", lambda e: self._changed())
        self.combo.bind("<Return>", lambda e: self._changed())

    def get_value(self):
        display = self.combo.get()
        if display in self._display_choices:
            return self._raw_choices[self._display_choices.index(display)]
        return display

    def set_value(self, val):
        val_str = str(val) if val is not None and val != "" else ""
        if val_str in self._raw_choices:
            self.combo.set(self._display_choices[self._raw_choices.index(val_str)])
        else:
            self.combo.set(val_str or self.UNSET)


class BoolParamRow(ParamRow):
    """纯开关型 bool(只有正向 flag)"""

    def __init__(self, parent, param, on_change=None, **kw):
        super().__init__(parent, param, on_change, **kw)
        self.var = tk.BooleanVar(value=False)
        self.check = tk.Checkbutton(
            self, variable=self.var, command=self._changed,
            bg=SURFACE, fg=TEXT, selectcolor=ENTRY_BG,
            activebackground=SURFACE, activeforeground=TEXT,
            highlightthickness=0, bd=0, relief="flat", font=FONT,
        )
        self.check.grid(row=0, column=1, padx=5, pady=4, sticky="w")

    def get_value(self):
        return bool(self.var.get())

    def set_value(self, val):
        self.var.set(bool(val))


class BoolPairParamRow(ParamRow):
    """三态 bool_pair(默认 / 开 / 关)"""

    DEFAULT, ON, OFF = "默认", "开", "关"

    def __init__(self, parent, param, on_change=None, **kw):
        super().__init__(parent, param, on_change, **kw)
        self.combo = ttk.Combobox(
            self, values=[self.DEFAULT, self.ON, self.OFF], state="readonly",
            style="Param.TCombobox", width=6,
        )
        self.combo.set(self.DEFAULT)
        self.combo.grid(row=0, column=1, padx=5, pady=4, sticky="w")
        self.combo.bind("<<ComboboxSelected>>", lambda e: self._changed())

    def get_value(self):
        v = self.combo.get()
        if v == self.ON:
            return True
        if v == self.OFF:
            return False
        return None

    def set_value(self, val):
        if val is True:
            self.combo.set(self.ON)
        elif val is False:
            self.combo.set(self.OFF)
        else:
            self.combo.set(self.DEFAULT)


_ROW_TYPES = {
    "int": IntParamRow,
    "float": FloatParamRow,
    "choice": ChoiceParamRow,
    "bool": BoolParamRow,
    "bool_pair": BoolPairParamRow,
}


def create_param_row(parent, param: ParamDef, on_change=None) -> ParamRow:
    """按参数类型创建对应的行控件(path/str 及未知类型都按文本输入处理)"""
    row = _ROW_TYPES.get(param.type, StrParamRow)(parent, param, on_change)
    # 整行(含输入控件)建好后再挂悬停提示, 见 ParamRow.__init__ 里的说明
    Tooltip(row, lambda p=param: _tooltip_content(p))
    return row


class ParamsPanel(ctk.CTkFrame):
    """参数面板: 每个分组一个标签页, 页内两列排布参数行"""

    def __init__(self, parent, on_change=None, **kw):
        super().__init__(parent, **kw)
        self.on_change = on_change
        self.rows: dict[str, ParamRow] = {}

        setup_native_style(self)

        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        for group, label in PARAM_GROUP_LABELS.items():
            tab = self.tabview.add(label)
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(0, weight=1)
            sf = ctk.CTkScrollableFrame(tab)
            sf.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
            # 两列布局: 左右两列各占一半宽度, 每行先左后右
            sf.grid_columnconfigure(0, weight=1, uniform="paramcol")
            sf.grid_columnconfigure(1, weight=1, uniform="paramcol")

            # 本页参数名占宽统一 = 本页最宽的参数名 + 左右留白, 这样同页输入框左端对齐。
            # 用实测的 winfo_reqwidth 而不是字体度量: 同长度的 flag 宽度并不相同
            # (m/w 比 i/l 宽, 同为 29 字符实测 166~189px), 只有控件自己算出来的才准。
            params = PARAM_GROUPS.get(group, [])
            rows = [create_param_row(sf, p, self._on_param_changed) for p in params]
            label_px = max((r.label.winfo_reqwidth() for r in rows), default=0)
            label_px += sum(ParamRow.LABEL_PADX)
            for i, (p, row) in enumerate(zip(params, rows)):
                row.grid_columnconfigure(0, minsize=label_px)
                row.grid(row=i // 2, column=i % 2, sticky="ew", padx=3, pady=1)
                self.rows[p.key] = row

    def _on_param_changed(self):
        if self.on_change:
            self.on_change()

    def load_params(self, params: dict):
        """从字典加载参数到面板; 缺失的键回退到参数定义的默认值"""
        defaults = {p.key: p.default for p in PARAM_DEFINITIONS}
        for key, row in self.rows.items():
            val = params.get(key, None)
            row.set_value(defaults.get(key) if val is None else val)

    def collect_params(self) -> dict:
        return {key: row.get_value() for key, row in self.rows.items()}
