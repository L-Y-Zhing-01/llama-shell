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

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

import customtkinter as ctk

from llama_params import PARAM_DEFINITIONS, PARAM_GROUP_LABELS, PARAM_GROUPS, ParamDef
from tooltip import Tooltip
import ui_theme as ut
from ui_theme import FONT, FONT_FAMILY, setup_native_style

# 注意: 颜色一律走 ut.XXX 属性访问 —— 换肤时模块变量会被重新赋值,
# from-import 绑死的旧色值不会跟着变(见 ui_theme 模块头注释)。

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
        super().__init__(parent, bg=ut.SURFACE)
        self.param = param
        self.on_change = on_change
        # 第 0 列是参数名(其 minsize 由面板按本页最长参数名统一设定), 第 1 列吃掉剩余宽度
        self.grid_columnconfigure(1, weight=1)

        # 参数名与 --help 保持一致, 不翻译; 中文含义走悬停提示。
        # 回退链仅在定义缺失 cli_flag 时兜底(当前参数表内不存在这种条目)。
        # 这里刻意不用 tk.Label 的 width: 它的单位是"字符数", 会按字体的平均字宽放大
        # ——29 字符的 flag 会被算成 205px, 实际只需 168px, 白白挤掉输入框。
        self.label = tk.Label(
            self, text=param.cli_flag or param.label or param.key,
            bg=ut.SURFACE, fg=ut.TEXT, anchor="w", bd=0, font=FLAG_FONT,
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
        parent, bg=ut.ENTRY_BG, fg=ut.TEXT, insertbackground=ut.TEXT,
        selectbackground=ut.PRIMARY_BG, selectforeground=ut.PRIMARY_FG,
        relief="flat", highlightthickness=1,
        highlightbackground=ut.ENTRY_BORDER, highlightcolor=ut.ACCENT,
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
        self.entry.bind("<FocusOut>", lambda e=None: self._changed())
        self.entry.bind("<Return>", lambda e=None: self._changed())

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


# 路径参数的浏览对话框过滤规则: 先按 key 归到类别, 再取对应的 filetypes。
# 没登记的 key 一律给「所有文件」, 不给用户添堵。
_FILETYPES = {
    "gguf": [("GGUF 模型", "*.gguf"), ("所有文件", "*.*")],
    "json": [("JSON 文件", "*.json"), ("所有文件", "*.*")],
    "ini": [("INI 文件", "*.ini"), ("所有文件", "*.*")],
    "pem": [("证书/私钥", "*.pem;*.crt;*.key"), ("所有文件", "*.*")],
    "text": [("文本文件", "*.txt;*.gbnf;*.jinja"), ("所有文件", "*.*")],
}
_KEY_FILETYPES = {
    "spec_draft_model": "gguf", "mmproj": "gguf",
    "lookup_cache_static": "gguf", "lookup_cache_dynamic": "gguf",
    "json_schema_file": "json", "ui_config_file": "json", "mcp_servers_config": "json",
    "models_preset": "ini",
    "ssl_cert_file": "pem", "ssl_key_file": "pem",
    "api_key_file": "text", "grammar_file": "text", "chat_template_file": "text",
}
_ALL_FILETYPES = [("所有文件", "*.*")]


def _path_key(path_str: str) -> str:
    """路径判等用的键: Windows 下大小写与斜杠方向都不敏感"""
    return os.path.normcase(os.path.normpath(path_str.strip()))


class PathParamRow(EntryParamRow):
    """路径输入行: 文本输入 + 「…」浏览按钮。

    选文件 / 选目录 / 多选追加 由 `ParamDef.browse_mode` 决定:
    - 'file'  单个文件, 选中后替换；
    - 'dir'   文件夹, 见 llama_params.PATH_DIR_KEYS；
    - 'files' 逗号分隔的多值路径(--lora / --control-vector), 多选后追加去重。
    按钮用 Label 模拟而不是 tk.Button: Windows 上经典 Button 的 bg 被系统主题接管,
    换肤不生效; Label 的底色完全由我们控制。
    """

    def __init__(self, parent, param, on_change=None, **kw):
        super().__init__(parent, param, on_change, **kw)
        self.entry.grid_configure(padx=(5, 2))
        self.browse_btn = tk.Label(
            self, text="…", bg=ut.BTN_BG, fg=ut.BTN_FG, font=FONT,
            padx=5, bd=0, cursor="hand2",
            highlightthickness=1, highlightbackground=ut.ENTRY_BORDER,
        )
        self.browse_btn.grid(row=0, column=2, padx=(0, 5), pady=4, sticky="ns")
        self.browse_btn.bind("<Button-1>", lambda e=None: self._browse())
        self.browse_btn.bind("<Enter>", self._on_hover_in)
        self.browse_btn.bind("<Leave>", self._on_hover_out)

    def _on_hover_in(self, _e=None):
        self.browse_btn.configure(bg=ut.BTN_ACTIVE_BG, fg=ut.BTN_ACTIVE_FG)

    def _on_hover_out(self, _e=None):
        self.browse_btn.configure(bg=ut.BTN_BG, fg=ut.BTN_FG)

    def _browse(self):
        cur = self.entry.get().strip()
        mode = self.param.browse_mode or "file"
        title = f"选择 {self.param.label or self.param.cli_flag}"
        start = self._start_dir(cur)
        if mode == "dir":
            chosen = filedialog.askdirectory(
                parent=self, title=title, initialdir=start, mustexist=False)
        else:
            ftypes = _FILETYPES.get(_KEY_FILETYPES.get(self.param.key, ""), _ALL_FILETYPES)
            ask = (filedialog.askopenfilenames if mode == "files"
                   else filedialog.askopenfilename)
            chosen = ask(parent=self, title=title, initialdir=start, filetypes=ftypes)
        if not chosen:
            return
        self.entry.delete(0, "end")
        if mode == "files":
            self.entry.insert(0, self._merge_multi(cur, chosen))
        else:
            self.entry.insert(0, str(Path(chosen)))
        self._changed()

    @staticmethod
    def _merge_multi(cur: str, picked) -> str:
        """把新选中的文件并进现有的逗号列表(去重, 不覆盖用户已填的项)

        去重按 normcase+normpath 比较: 用户手打的 `C:/x.gguf` 与对话框返回的
        `C:\\x.gguf` 是同一个文件, 不规范化就会重复追加。
        """
        items = [s.strip() for s in cur.split(",") if s.strip()]
        seen = {_path_key(s) for s in items}
        for p in picked:
            sp = str(Path(p))
            key = _path_key(sp)
            if key not in seen:
                items.append(sp)
                seen.add(key)
        return ",".join(items)

    @staticmethod
    def _start_dir(cur: str) -> str:
        """对话框起始目录: 当前值所在处优先, 否则用户主目录"""
        first = cur.split(",")[0].strip() if cur else ""
        if first:
            p = Path(first).expanduser()
            try:
                if p.is_dir():
                    return str(p)
                if p.parent.is_dir():
                    return str(p.parent)
            except OSError:
                pass
        home = Path.home()
        return str(home) if home.is_dir() else "C:/"


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
        self.combo.bind("<<ComboboxSelected>>", lambda e=None: self._changed())
        self.combo.bind("<Return>", lambda e=None: self._changed())

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
            bg=ut.SURFACE, fg=ut.INK, selectcolor=ut.CHECK_BG,
            activebackground=ut.SURFACE, activeforeground=ut.INK,
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
        self.combo.bind("<<ComboboxSelected>>", lambda e=None: self._changed())

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
    "path": PathParamRow,
}


def create_param_row(parent, param: ParamDef, on_change=None) -> ParamRow:
    """按参数类型创建对应的行控件(str 及未知类型按文本输入处理, 路径类带浏览按钮)"""
    if param.browse_mode:
        # --lora / --control-vector 是 str 类型但取值是路径列表, 也走带按钮的行
        cls = PathParamRow
    else:
        cls = _ROW_TYPES.get(param.type, StrParamRow)
    row = cls(parent, param, on_change)
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

        self.tabview = ctk.CTkTabview(
            self,
            fg_color=ut.CARD, border_color=ut.CARD_LINE,
            # 选中页用"升起的一层"(CARD_LINE)而不是主色块: CTk 的分段按钮
            # 只有一个 text_color, 选中块若用 primary(深色主题里就是 ink 本身)
            # 会出现"同色字压同色底"——标签直接看不见。
            segmented_button_fg_color=ut.BG_SOFT,
            segmented_button_selected_color=ut.FIELD_LINE,
            segmented_button_selected_hover_color=ut.CARD_LINE,
            segmented_button_unselected_color=ut.BG_SOFT,
            segmented_button_unselected_hover_color=ut.CHIP_BG,
            text_color=ut.INK,
        )
        self.tabview.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        for group, label in PARAM_GROUP_LABELS.items():
            tab = self.tabview.add(label)
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(0, weight=1)
            sf = ctk.CTkScrollableFrame(
                tab, fg_color=ut.CARD,
                scrollbar_button_color=ut.CARD_LINE,
                scrollbar_button_hover_color=ut.ENTRY_BORDER,
            )
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
