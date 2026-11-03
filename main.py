# -*- coding: utf-8 -*-
"""llama.cpp 模型管理器 — 单页 GUI 主程序。

只负责界面装配、事件分发与定时刷新; 具体职责已拆到:
    app_config.py     路径常量与配置读写
    ui_theme.py       配色与原生控件样式
    param_ui.py       参数行控件与参数面板
    server_process.py llama-server 子进程管理
    updater.py        版本查询与 winget 更新
    llama_params.py   参数定义表与命令行构建
    monitor.py        GPU/RAM 监控线程
"""

import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from app_config import (
    LOG_DIR, init_models_config, load_config, load_models_config,
    remember_models_dir, resolve_help_url, resolve_path, save_config,
    save_models_config,
)
from llama_params import build_argv, build_command_string, find_param_by_flag, get_default_params
from monitor import MonitorWorker
from param_ui import ParamsPanel
from server_process import ServerProcess, wait_port_released, wait_until_ready
import theme as theme_mod
import tooltip as tooltip_mod
import ui_theme as ut
from updater import LlamaUpdater

CUSTOM_CTX_LABEL = "+ 自定义…"   # 上下文预设下拉里的特殊项, 选中后弹出自定义对话框


def _combo_kw() -> dict:
    """CTkComboBox 统一样式: 1px 描边 + 与输入框同族的底色/箭头。

    必须在**建控件时**调用取色(不能做成模块常量): 换肤后要按新主题重建下拉框。
    """
    return dict(
        border_width=1, border_color=ut.COMBO_BORDER,
        fg_color=ut.ENTRY_BG, button_color=ut.COMBO_BTN_BG,
        button_hover_color=ut.COMBO_BTN_HOVER,
        dropdown_fg_color=ut.CARD, dropdown_hover_color=ut.CHIP_BG,
        text_color=ut.INK,
    )


def _btn_colors(kind: str | None) -> dict:
    """按钮配色: primary=主操作, danger=危险操作, None=普通按钮(跟随主题默认)。"""
    if kind == "primary":
        return {"fg_color": ut.PRIMARY_BG, "hover_color": ut.PRIMARY_ACTIVE_BG,
                "text_color": ut.PRIMARY_FG}
    if kind == "danger":
        return {"fg_color": ut.DANGER_BG, "hover_color": ut.DANGER_ACTIVE_BG,
                "text_color": ut.DANGER_FG}
    return {}


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("llama.cpp 模型管理器")
        self.geometry("1000x600")
        # 参数名直接显示 flag(如 --spec-draft-cpu-strict-batch, 实测占 205px),
        # 两列布局下再窄就会把输入框挤没(实测 800px 时该页可用宽度为 -26px),
        # 因此最小宽度与默认宽度一致。
        self.minsize(1000, 400)

        # 配置
        self.config = load_config()
        # 主题: config 里记着上次用的; 未知值由 ui_theme 回落默认
        self.theme_key = self.config.get("theme", theme_mod.DEFAULT_THEME)
        ut.apply(self.theme_key)
        # 窗口本身在 super().__init__ 时已按旧主题建好, 底色要单独改
        self.configure(fg_color=ut.BG)
        self.models_dir = resolve_path(self.config["models_dir"])
        self.llama_cmd = self.config.get("llama_server_cmd", "llama-server")
        self.monitor_interval = self.config.get("monitor_interval_ms", 1000)
        self.context_presets = self.config.get("context_presets", [])

        # 版本号 & 可用性检测
        self.updater = LlamaUpdater(self.llama_cmd, LOG_DIR)
        self.llama_available = self._check_llama_available()
        self._refresh_version_title()

        # 启动对账: 按 llama-server --help 与上一次的参数表比对增删
        # (装在 PATH 里的 llama.cpp 可能已被 winget 独立升级, 面板得跟上)
        sync = (self.updater.auto_sync_params()
                if self.llama_available else {"added": [], "removed": []})
        self.auto_added_params = sync["added"]        # 新增 flag(归入「新增功能」页)
        self.sync_removed_params = sync["removed"]    # 上次有、本次 --help 里消失的 flag

        # 模型配置
        self.models_cfg = load_models_config()
        if not self.models_cfg.get("models"):
            self.models_cfg = init_models_config(self.models_dir)

        # 进程 & 监控
        self.server = ServerProcess(self.models_dir, LOG_DIR)
        self.monitor = MonitorWorker(self.monitor_interval)
        self.monitor.start()

        # 状态
        self.selected_model: str = ""
        self.model_buttons: dict = {}
        self._restarting = False    # 重启(卸载→加载)进行中, 期间屏蔽再次触发
        self._closing = False       # 已确认退出, 防止关闭流程重入

        # 构建 UI
        self._build_ui()
        self._refresh_model_list()
        self._check_process_state()
        self._tick_monitor()

        # 对账有变化时给出报告: 新增参数的介绍(归入「新增功能」页)与已取消的参数
        if self.auto_added_params or self.sync_removed_params:
            self._set_status(f"参数表已更新: 新增 {len(self.auto_added_params)}, "
                             f"取消 {len(self.sync_removed_params)}, 见「新增功能」页")
            self._log_param_changes()
            self.after(300, self._show_param_change_report)

        ut.apply_titlebar_theme(self, ut.CURRENT == "dark")

        # 未检测到 llama.cpp 时提醒用户
        if not self.llama_available:
            self.after(200, self._show_llama_missing_dialog)

    # ────────────── UI 构建 ──────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=0, minsize=380)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=0)  # topbar
        self.grid_rowconfigure(1, weight=1)  # body

        self._build_topbar()
        self._build_sidebar()

        # 右侧主区域
        self.right = ctk.CTkFrame(self)
        self.right.grid(row=1, column=1, sticky="nsew", padx=2, pady=2)
        self.right.grid_columnconfigure(0, weight=1)
        self.right.grid_rowconfigure(0, weight=1)
        for row in (1, 2, 3):
            self.right.grid_rowconfigure(row, weight=0)

        self.params_panel = ParamsPanel(self.right, on_change=self._on_params_changed)
        self.params_panel.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)

        self._build_monitor_bar()
        self._build_status_bar()
        self._build_command_preview()

    def _build_topbar(self):
        topbar = ctk.CTkFrame(self, height=44)
        topbar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=2)
        # 按钮区单独占一列(weight=1): 让它吃掉全部剩余宽度, 最右的图标按钮才不会被
        # 9 个等宽按钮挤出窗口(实测直接同排 grid 时图标会被裁掉半个)。
        topbar.grid_columnconfigure(0, weight=1)
        bar = ctk.CTkFrame(topbar, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew")

        buttons = [
            ("加载模型", self.on_load_clicked, "primary"),
            ("卸载模型", self.on_unload_clicked, "danger"),
            ("重启模型", self.on_restart_clicked, None),
            ("保存参数", self.on_save_params, None),
            ("重置参数", self.on_reset_params, None),
            ("打开日志", self.on_open_log, None),
            ("官网", self.on_open_help, None),
            ("更新", self.on_check_update, None),
            ("帮助", self.on_open_help_site, None),
        ]
        self.btn_update = None   # 检查/更新期间需要禁用
        for col, (text, cmd, kind) in enumerate(buttons):
            bar.grid_columnconfigure(col, weight=1, uniform="btn")
            btn = ctk.CTkButton(bar, text=text, command=cmd, **_btn_colors(kind))
            btn.grid(row=0, column=col, padx=4, pady=4, sticky="ew")
            if text == "更新":
                self.btn_update = btn

        # 主题开关: 标题栏最右侧只放一个图标(图标=点一下会切到的主题)。
        # 字号 16 是实测值: ☀/☾ 在 CTk 默认 13 号下笔画糊成一团, 16 号才看得出形。
        self.btn_theme = ctk.CTkButton(
            topbar, text=self._theme_button_icon(), width=40,
            font=(ut.FONT_FAMILY, 16), command=self.on_toggle_theme,
        )
        self.btn_theme.grid(row=0, column=1, padx=(2, 4), pady=4, sticky="ns")
        tooltip_mod.Tooltip(self.btn_theme,
                            lambda: ("主题", "", self._theme_tooltip(), ""))

    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self, width=380)
        sidebar.grid(row=1, column=0, sticky="nsew", padx=4, pady=2)
        sidebar.grid_rowconfigure(1, weight=1)
        sidebar.grid_columnconfigure(0, weight=1)

        # 模型目录: 历史(上次使用目录已自动加载) + 浏览选新目录
        dir_frame = ctk.CTkFrame(sidebar)
        dir_frame.grid(row=0, column=0, sticky="ew", padx=2, pady=2)
        dir_frame.grid_columnconfigure(0, weight=1)
        # 边框粗细/颜色见 _combo_kw(CTk 默认 2px 浅灰, 在深色界面上像"白框")
        self.dir_combo = ctk.CTkComboBox(
            dir_frame, values=self._dir_combo_values(),
            command=self.on_pick_dir_from_history, **_combo_kw())
        self.dir_combo.grid(row=0, column=0, padx=5, pady=4, sticky="ew")
        ctk.CTkButton(dir_frame, text="删除", width=44,
                      command=self.on_delete_dir_from_history).grid(
            row=0, column=1, padx=(0, 2), pady=4)
        ctk.CTkButton(dir_frame, text="模型目录", width=78,
                      command=self.on_browse_models_dir).grid(
            row=0, column=2, padx=(0, 5), pady=4)

        self.model_list_frame = ctk.CTkScrollableFrame(sidebar, label_text="模型列表")
        self.model_list_frame.grid(row=1, column=0, sticky="nsew", padx=2, pady=2)
        self.model_list_frame.grid_columnconfigure(0, weight=1)
        # 隐藏内置滚动条(滚轮滚动走画布绑定, 不受影响); grid_remove 保留布局参数
        self.model_list_frame._scrollbar.grid_remove()

        ctx_frame = ctk.CTkFrame(sidebar)
        ctx_frame.grid(row=2, column=0, sticky="ew", padx=2, pady=4)
        ctx_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(ctx_frame, text="上下文预设").grid(
            row=0, column=0, padx=(5, 2), pady=4, sticky="w")

        preset_labels = self._ctx_preset_labels()
        self.ctx_preset_combo = ctk.CTkComboBox(
            ctx_frame, values=preset_labels, command=self.on_ctx_preset_changed,
            **_combo_kw())
        self.ctx_preset_combo.grid(row=0, column=1, padx=(2, 5), pady=4, sticky="ew")

        ctk.CTkButton(ctx_frame, text="刷新模型列表",
                      command=self._refresh_model_list).grid(
            row=1, column=0, columnspan=2, padx=5, pady=4, sticky="ew")

    def _build_monitor_bar(self):
        monitor_frame = ctk.CTkFrame(self.right, height=52)
        monitor_frame.grid(row=1, column=0, sticky="ew", padx=2, pady=2)
        monitor_frame.grid_columnconfigure(1, weight=1)
        monitor_frame.grid_columnconfigure(3, weight=1)

        # (标题, 进度条属性名, 颜色, 标签属性名)
        rows = [
            ("GPU:", "gpu_bar", ut.OK, "gpu_label"),
            ("RAM:", "ram_bar", ut.FIELD_FOCUS, "ram_label"),
        ]
        for row, (title, bar_attr, color, label_attr) in enumerate(rows):
            ctk.CTkLabel(monitor_frame, text=title, width=40, anchor="e").grid(
                row=row, column=0, padx=(4, 2), pady=2)
            bar = ctk.CTkProgressBar(monitor_frame, width=200, progress_color=color)
            bar.grid(row=row, column=1, padx=2, pady=2, sticky="ew")
            bar.set(0)
            label = ctk.CTkLabel(monitor_frame, text="--", width=300, anchor="w")
            label.grid(row=row, column=2, padx=4, pady=2, sticky="w")
            setattr(self, bar_attr, bar)
            setattr(self, label_attr, label)

    def _build_status_bar(self):
        status_frame = ctk.CTkFrame(self.right, height=34)
        status_frame.grid(row=2, column=0, sticky="ew", padx=2, pady=2)
        status_frame.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(status_frame, text="状态: 空闲",
                                         anchor="w", font=("Consolas", 13))
        self.status_label.grid(row=0, column=0, padx=8, pady=4, sticky="ew")
        ctk.CTkButton(status_frame, text="网页运行", width=90,
                      command=self.on_open_web).grid(row=0, column=1, padx=(8, 8), pady=4)

    def _build_command_preview(self):
        cmd_frame = ctk.CTkFrame(self.right, height=100)
        cmd_frame.grid(row=3, column=0, sticky="ew", padx=2, pady=2)
        cmd_frame.grid_columnconfigure(0, weight=1)
        cmd_frame.grid_rowconfigure(2, weight=1)

        cmd_top = ctk.CTkFrame(cmd_frame, fg_color="transparent")
        cmd_top.grid(row=0, column=0, sticky="ew", padx=2)
        cmd_top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(cmd_top, text="命令预览:", anchor="w").grid(row=0, column=0, padx=4, sticky="w")
        ctk.CTkButton(cmd_top, text="复制", width=60,
                      command=self.on_copy_command).grid(row=0, column=1, padx=4)

        # 额外参数不是 llama.cpp 参数(无 --flag), 不进参数面板, 放在命令预览旁:
        # 它直接影响命令末尾的拼接, 与预览放一起最直观。
        extra_row = ctk.CTkFrame(cmd_frame, fg_color="transparent")
        extra_row.grid(row=1, column=0, sticky="ew", padx=2)
        extra_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(extra_row, text="额外参数:", anchor="w").grid(row=0, column=0, padx=4, sticky="w")
        self.extra_entry = ctk.CTkEntry(extra_row, placeholder_text="自由追加到命令末尾, 如: --verbose 1")
        self.extra_entry.grid(row=0, column=1, sticky="ew", padx=4, pady=2)
        self.extra_entry.bind("<Return>", lambda e: self._on_extra_args_changed())
        self.extra_entry.bind("<FocusOut>", lambda e: self._on_extra_args_changed())

        # 命令预览跟主题走(按只读输入框取色): 它是常驻可见的一段命令文本,
        # 若沿用日志那套"两套主题都深底"的配色, 浅色主题下会留一块黑块。
        self.cmd_textbox = ctk.CTkTextbox(
            cmd_frame, height=60, font=("Consolas", 11),
            fg_color=ut.FIELD_BG, text_color=ut.INK, border_color=ut.FIELD_LINE,
            scrollbar_button_color=ut.CARD_LINE,
            scrollbar_button_hover_color=ut.FIELD_FOCUS,
        )
        self.cmd_textbox.grid(row=2, column=0, sticky="nsew", padx=2, pady=2)
        self.cmd_textbox.insert("1.0", "请选择模型...")
        self.cmd_textbox.configure(state="disabled")

    # ────────────── 模型目录切换 ──────────────

    def _dir_combo_values(self) -> list:
        """历史目录下拉: 当前目录在最前, 其余按最近使用排序(不存在的隐藏)"""
        vals = [str(self.models_dir)]
        for d in self.config.get("models_dir_history", []):
            if Path(d).is_dir() and d.casefold() != str(self.models_dir).casefold():
                vals.append(d)
        return vals

    def _sync_dir_combo(self):
        """目录切换后同步下拉框显示与候选值"""
        self.dir_combo.configure(values=self._dir_combo_values())
        self.dir_combo.set(str(self.models_dir))

    def on_browse_models_dir(self):
        init = str(self.models_dir) if self.models_dir.is_dir() else str(Path(__file__).parent)
        chosen = filedialog.askdirectory(parent=self, title="选择模型文件夹",
                                         initialdir=init, mustexist=True)
        if chosen:
            self._switch_models_dir(chosen)

    def on_pick_dir_from_history(self, choice: str):
        if Path(choice).is_dir() and Path(choice).resolve() != self.models_dir:
            self._switch_models_dir(choice)
        else:
            self._sync_dir_combo()   # 选中无效项或当前项: 恢复显示

    def _delete_dir_history_entry(self, dir_str: str) -> bool:
        """从历史中删除一条目录记录并落盘; 返回是否删除(当前目录拒绝删除)"""
        d = str(dir_str).strip()
        if d.casefold() == str(self.models_dir).casefold():
            self._set_status("当前使用的目录不会从历史中删除")
            return False
        hist = self.config.get("models_dir_history", [])
        kept = [x for x in hist if x.casefold() != d.casefold()]
        if len(kept) == len(hist):
            self._set_status(f"历史中没有该目录: {d}")
            return False
        self.config["models_dir_history"] = kept
        save_config(self.config)
        self._sync_dir_combo()
        self._set_status(f"已从历史记录中删除: {d}")
        return True

    # ────────────── 模态子窗口 ──────────────

    def _dialog_active(self, attr: str) -> bool:
        """同名子窗口已开着则置顶并返回 True(调用方应直接返回, 不再新建)"""
        dlg = getattr(self, attr, None)
        if dlg is not None and dlg.winfo_exists():
            dlg.lift(); dlg.focus_force()
            return True
        return False

    def _register_dialog(self, dlg, attr: str) -> None:
        """登记子窗口并做置顶/模态抓取(延迟抓取: 等窗口先完成首次布局)"""
        setattr(self, attr, dlg)

        def _grab():
            if dlg.winfo_exists():
                try:
                    dlg.grab_set()
                except Exception:
                    pass
        dlg.after(150, _grab)
        dlg.lift(); dlg.focus_force()

    def on_delete_dir_from_history(self):
        """弹出历史目录列表, 逐条删除记录(仅删记录, 不动磁盘; 当前使用中的不可删)"""
        if self._dialog_active("_dirdel_dlg"):
            return
        dlg = ctk.CTkToplevel(self)
        dlg.title("删除历史目录")
        dlg.resizable(False, False)
        dlg.transient(self)
        ctk.CTkLabel(dlg, text="仅删除下拉框里的历史记录, 不会删除磁盘上的文件夹。",
                     justify="left").grid(row=0, column=0, padx=14,
                                          pady=(12, 6), sticky="w")
        body = ctk.CTkFrame(dlg, fg_color="transparent")
        body.grid(row=1, column=0, padx=14, pady=(0, 14), sticky="ew")

        def rebuild():
            for w in body.winfo_children():
                w.destroy()
            hist = self.config.get("models_dir_history", [])
            if not hist:
                ctk.CTkLabel(body, text="历史记录为空",
                             text_color=ut.FAINT).grid(
                    row=0, column=0, padx=2, pady=6, sticky="w")
                return
            for i, d in enumerate(hist):
                cur = d.casefold() == str(self.models_dir).casefold()
                tag = ("  (当前使用)" if cur
                       else ("" if Path(d).is_dir() else "  (不存在)"))
                ctk.CTkLabel(body, text=d + tag, justify="left",
                             wraplength=520).grid(
                    row=i, column=0, padx=(2, 8), pady=3, sticky="w")
                btn = ctk.CTkButton(body, text="删除", width=48,
                                    command=lambda dd=d: remove(dd))
                if cur:
                    btn.configure(state="disabled")
                btn.grid(row=i, column=1, padx=(0, 2), pady=3, sticky="e")

        def remove(dd):
            if self._delete_dir_history_entry(dd):
                rebuild()

        rebuild()
        self._register_dialog(dlg, "_dirdel_dlg")

    def _switch_models_dir(self, dir_str: str):
        """切换模型目录: 保存位置与历史, 重新扫描并重建列表"""
        if self.server.is_running():
            self._set_status("模型运行中, 请先卸载再切换模型目录")
            self._sync_dir_combo()
            return
        p = Path(dir_str)
        if not p.is_dir():
            self._set_status(f"目录不存在: {dir_str}")
            self._sync_dir_combo()
            return

        self.models_dir = p.resolve()
        self.config["models_dir_history"] = remember_models_dir(
            self.config, str(self.models_dir))
        # ServerProcess 用 models_dir 作为子进程工作目录, 目录变了要重建(此时必然空闲)
        self.server = ServerProcess(self.models_dir, LOG_DIR)

        self._sync_dir_combo()
        self.selected_model = ""   # 原选中模型未必在新目录里, 交由刷新重新自动选中
        self._refresh_model_list()
        self._set_status(f"模型目录: {self.models_dir} "
                         f"(共 {len(self.model_buttons)} 个模型)")

    # ────────────── 模型列表 ──────────────

    def _refresh_model_list(self):
        """扫描模型目录, 同步 models_config.json, 重建左侧列表"""
        models = self.models_cfg.setdefault("models", {})
        for gguf in sorted(self.models_dir.glob("*.gguf")):
            name, path = gguf.stem, str(gguf.resolve())
            if name not in models:
                params = get_default_params()
                params["model"] = path
                models[name] = {
                    "gguf_file": gguf.name,
                    "params": dict(params),
                    "original_params": dict(params),
                }
            else:
                # 路径随本机重新解析(保证跨电脑拷贝后仍正确)
                models[name]["gguf_file"] = gguf.name
                models[name].setdefault("params", {})["model"] = path
                # 旧格式迁移: 缺少 original_params 时, 以当前参数为原始值
                orig = models[name].get("original_params")
                if orig is None:
                    orig = dict(models[name].get("params") or get_default_params())
                    models[name]["original_params"] = orig
                orig["model"] = path

        # 清理: 目录里没有、且记录的绝对路径也不存在(文件已被删除)的模型。
        # 属于其他历史目录的模型保留条目 → 来回切换目录不丢参数。
        kept = {}
        for k, v in models.items():
            if (self.models_dir / v["gguf_file"]).exists():
                kept[k] = v
                continue
            mpath = (v.get("params") or {}).get("model") or ""
            if mpath and Path(mpath).exists():
                kept[k] = v
        self.models_cfg["models"] = kept
        save_models_config(self.models_cfg)

        for w in self.model_list_frame.winfo_children():
            w.destroy()

        # 列表只显示当前目录扫描到的模型(其他目录的条目留在配置里)
        names = sorted(gguf.stem for gguf in self.models_dir.glob("*.gguf"))
        self.model_buttons = {}
        self._hl_key = None   # 列表重建后强制重新高亮
        for i, name in enumerate(names):
            btn = ctk.CTkButton(self.model_list_frame, text=name, anchor="w", height=32,
                                command=lambda n=name: self.on_select_model(n))
            btn.grid(row=i, column=0, sticky="ew", padx=2, pady=1)
            self.model_buttons[name] = btn

        # 自动选中第一个
        if names and not self.selected_model:
            self.on_select_model(names[0])
        else:
            self._highlight_model_button()

    def _highlight_model_button(self, name: str = None):
        """模型列表配色: 运行中=绿色(优先), 选中=蓝色, 其余=主题默认。

        name 缺省时用 self.selected_model, 供加载/卸载等状态变化点无参刷新。
        """
        if name is None:
            name = self.selected_model
        normal = ut.BTN_BG
        running = self.server.model_name if self.server.is_running() else ""
        # 变化才重绘: (运行中, 选中, 列表长度) 都没变就跳过
        key = (running, name, len(self.model_buttons))
        if key == getattr(self, "_hl_key", None):
            return
        self._hl_key = key
        for n, btn in self.model_buttons.items():
            if n == running:
                btn.configure(fg_color=ut.RUN_BG, text_color=ut.RUN_FG,
                              hover_color=ut.RUN_ACTIVE_BG)
            elif n == name:
                btn.configure(fg_color=ut.SEL_BG, text_color=ut.SEL_FG,
                              hover_color=ut.SEL_ACTIVE_BG)
            else:
                btn.configure(fg_color=normal, text_color=ut.BTN_FG,
                              hover_color=ut.BTN_ACTIVE_BG)

    # ────────────── 事件处理 ──────────────

    def on_select_model(self, name: str):
        self.selected_model = name
        self._highlight_model_button(name)

        model_cfg = self.models_cfg["models"].get(name, {})
        params = model_cfg.get("params", get_default_params())
        self.params_panel.load_params(params)
        self.extra_entry.delete(0, "end")
        self.extra_entry.insert(0, str(params.get("extra_args") or ""))
        self._refresh_command_preview()
        self._refresh_ctx_preset(params)

    def _ctx_preset_labels(self) -> list:
        """预设下拉候选: 各预设 + 自定义入口"""
        return ([f'{p["label"]} ({p["value"]})' for p in self.context_presets]
                + [CUSTOM_CTX_LABEL])

    def on_ctx_preset_changed(self, value: str):
        if value == CUSTOM_CTX_LABEL:
            # 显示回当前实际值, 再弹出自定义对话框
            self._refresh_ctx_preset(self._collect_params())
            self._open_custom_ctx_dialog()
            return
        # 解析 "32k (32768)" 格式
        try:
            val = int(value.split("(")[-1].rstrip(")"))
        except ValueError:
            return
        if "ctx_size" in self.params_panel.rows:
            self.params_panel.rows["ctx_size"].set_value(val)
            self._refresh_command_preview()

    def _open_custom_ctx_dialog(self):
        """自定义上下文预设: 输入名称与 token 数, 确认后写入 config.json 永久保存"""
        if self._dialog_active("_ctx_dialog"):
            return
        dlg = ctk.CTkToplevel(self)
        dlg.title("自定义上下文预设")
        dlg.resizable(False, False)
        dlg.transient(self)
        ctk.CTkLabel(dlg, text="名称:").grid(
            row=0, column=0, padx=(14, 6), pady=(16, 4), sticky="e")
        e_label = ctk.CTkEntry(dlg, width=160, placeholder_text="如 96k (留空自动取名)")
        e_label.grid(row=0, column=1, padx=(0, 14), pady=(16, 4), sticky="w")
        ctk.CTkLabel(dlg, text="上下文 (token):").grid(
            row=1, column=0, padx=(14, 6), pady=4, sticky="e")
        e_val = ctk.CTkEntry(dlg, width=160, placeholder_text="如 98304")
        e_val.grid(row=1, column=1, padx=(0, 14), pady=4, sticky="w")

        def confirm():
            self._add_custom_ctx_preset(e_label.get(), e_val.get(), dlg)

        btns = ctk.CTkFrame(dlg, fg_color="transparent")
        btns.grid(row=2, column=0, columnspan=2, padx=14, pady=(10, 14), sticky="e")
        ctk.CTkButton(btns, text="取消", width=70, command=dlg.destroy).grid(
            row=0, column=0, padx=4)
        ctk.CTkButton(btns, text="确定", width=70, command=confirm).grid(row=0, column=1)
        e_val.bind("<Return>", lambda _e: confirm())
        self._register_dialog(dlg, "_ctx_dialog")

    def _add_custom_ctx_preset(self, label_raw: str, value_raw: str, parent=None):
        """校验并添加自定义预设: 成功则持久化并应用到 ctx_size; 值重复时仅选中已有项"""
        try:
            v = int(str(value_raw).strip())
        except ValueError:
            messagebox.showwarning("自定义上下文", "上下文必须是整数, 如 98304", parent=parent)
            return False
        if not (64 <= v <= 1048576):
            messagebox.showwarning("自定义上下文", "取值范围 64 ~ 1048576", parent=parent)
            return False
        dup = next((p for p in self.context_presets if p["value"] == v), None)
        if dup is None:
            label = str(label_raw).strip() or (f"{v // 1024}k" if v % 1024 == 0 else str(v))
            self.context_presets.append({"label": label, "value": v})
            self.config["context_presets"] = self.context_presets
            save_config(self.config)
            self.ctx_preset_combo.configure(values=self._ctx_preset_labels())
            shown = f"{label} ({v})"
        else:
            shown = f'{dup["label"]} ({v})'
        self.ctx_preset_combo.set(shown)
        if "ctx_size" in self.params_panel.rows:
            self.params_panel.rows["ctx_size"].set_value(v)
            self._refresh_command_preview()
        return True

    def _refresh_ctx_preset(self, params: dict):
        ctx = params.get("ctx_size", 0)
        match = next((f'{p["label"]} ({p["value"]})' for p in self.context_presets
                      if p["value"] == ctx), None)
        self.ctx_preset_combo.set(match or f"自定义 ({ctx})")

    def _collect_params(self) -> dict:
        """面板参数 + 额外参数(extra_args 不是 llama.cpp 参数, 由预览区输入框提供)"""
        return {**self.params_panel.collect_params(),
                "extra_args": self.extra_entry.get().strip()}

    def _on_extra_args_changed(self):
        self._refresh_command_preview()
        self.on_save_params()

    def _on_params_changed(self):
        self._refresh_command_preview()
        self._refresh_ctx_preset(self._collect_params())

    def _current_command(self) -> str:
        return build_command_string(self._collect_params(), self.llama_cmd)

    def _refresh_command_preview(self):
        if not self.selected_model:
            return
        self.cmd_textbox.configure(state="normal")
        self.cmd_textbox.delete("1.0", "end")
        self.cmd_textbox.insert("1.0", self._current_command())
        self.cmd_textbox.configure(state="disabled")

    def on_copy_command(self):
        if not self.selected_model:
            return
        self.clipboard_clear()
        self.clipboard_append(self._current_command())

    def on_load_clicked(self):
        if not self.selected_model:
            return
        if self._restarting:
            self._set_status("重启进行中 — 请稍候")
            return

        params = self._collect_params()
        argv = build_argv(params, self.llama_cmd)

        # 保存参数
        self.models_cfg["models"][self.selected_model]["params"] = params
        self.models_cfg["current_model"] = self.selected_model
        save_models_config(self.models_cfg)

        try:
            self.server.start(argv, self.selected_model)
        except Exception as e:
            self._set_status(f"启动失败: {e}")
            return

        host, port = self.server.get_host_port()
        self._set_status(f"加载中: {self.selected_model} → {host}:{port} ...")
        self._highlight_model_button()   # server.model_name 已更新, 立即亮显运行中的模型

        # 后台线程等就绪
        threading.Thread(
            target=wait_until_ready,
            args=(host, port, 60, lambda ok: self.after(0, lambda: self._on_server_ready(ok))),
            daemon=True,
        ).start()

    def _on_server_ready(self, ok: bool):
        self._highlight_model_button()   # 就绪/失败后刷新一次运行中亮显
        if ok:
            self._set_status_running()
        elif self.server.is_running():
            self._set_status("未就绪 (60秒内 /health 无响应) — 进程仍在运行，请查看日志")
        else:
            self._set_status(f"进程已退出 (code={self.server.return_code()}) — 请查看日志")

    def on_unload_clicked(self):
        if self.server.process is None:
            self._set_status("已卸载 — 空闲")
            return
        self._set_status(f"卸载中: {self.server.model_name} ...")
        self.update_idletasks()             # 先把状态画出来, unload 可能要等几秒
        if self.server.unload():
            self._highlight_model_button()  # 卸载后取消运行中亮显
            self._set_status("已卸载 — 空闲")
        else:
            self._set_status("卸载失败 — 进程仍在运行")
            messagebox.showwarning(
                "卸载失败",
                "llama-server 进程无法结束, 仍占着端口与显存。\n"
                f"PID: {self.server.pid()}\n\n请到任务管理器手动结束, 或查看日志确认原因。")

    def on_restart_clicked(self):
        """重启 = 先卸载(确认进程退出 + 端口释放), 再加载, 不做"边跑边起"。

        旧实现是 stop() 后盲等 500ms 再 start(): 进程若没死透(强杀失败/显存未回收),
        新实例会和旧实例抢端口与显存, 表现为"重启后加载失败"或显存翻倍。
        """
        if not self.selected_model:
            return
        if not self.server.is_running():
            self.on_load_clicked()
            return
        if self._restarting:
            self._set_status("重启进行中 — 请稍候")
            return

        self._restarting = True
        host, port = self.server.get_host_port()
        self._set_status(f"重启 ① 卸载中: {self.server.model_name} ...")
        # 卸载要等进程退出(最长 0.8s 优雅 + 5s 强杀), 放到后台线程, 不冻结界面。
        # 后台线程不碰 Tk: 只写 state, 由主线程轮询拾取(跨线程调 after 不可靠)。
        state = {"done": False, "exited": False, "port_ok": False}
        threading.Thread(target=self._unload_worker,
                         args=(host, port, state), daemon=True).start()
        self._poll_unload(state)

    def _unload_worker(self, host: str, port: int, state: dict):
        """后台线程: 卸载旧进程, 再等端口释放, 只写 state 不碰界面"""
        exited = self.server.unload()
        port_ok = wait_port_released(host, port, timeout=10) if exited else False
        state["exited"], state["port_ok"], state["done"] = exited, port_ok, True

    def _poll_unload(self, state: dict):
        if self._closing:
            return                  # 窗口已进入退出流程, 不再推进重启
        if not state["done"]:
            self.after(120, lambda: self._poll_unload(state))
            return
        self._on_unload_done(state["exited"], state["port_ok"])

    def _on_unload_done(self, exited: bool, port_ok: bool):
        self._highlight_model_button()      # 卸载后取消"运行中"亮显
        if not exited:
            self._restarting = False
            self._set_status("卸载失败 — 旧进程仍在运行, 已取消重启")
            messagebox.showwarning(
                "重启已取消",
                "旧 llama-server 进程无法结束, 仍占着端口与显存。\n"
                f"PID: {self.server.pid()}\n\n"
                "请到任务管理器手动结束后重试, 或查看日志确认原因。")
            return
        if port_ok:
            self._set_status("重启 ② 已卸载, 正在加载 ...")
        else:
            self._set_status("已卸载, 但端口仍被占用 — 直接尝试加载(可能失败, 请查看日志)")
        self.after(300, self._restart_load)

    def _restart_load(self):
        self._restarting = False
        self.on_load_clicked()

    def on_save_params(self):
        if not self.selected_model:
            return
        self.models_cfg["models"][self.selected_model]["params"] = self._collect_params()
        save_models_config(self.models_cfg)
        self._set_status("参数已保存")

    def on_reset_params(self):
        """把参数恢复为首次扫描模型时的原始值"""
        if not self.selected_model:
            return
        model_cfg = self.models_cfg["models"].get(self.selected_model, {})
        orig = model_cfg.get("original_params")
        if not orig:
            self._set_status("无原始参数可重置")
            return

        params = dict(orig)
        # 原始路径仍在磁盘上就用它(模型可能属于其他历史目录); 否则按当前目录拼
        mpath = orig.get("model")
        if not (mpath and Path(mpath).exists()):
            params["model"] = str((self.models_dir / f"{self.selected_model}.gguf").resolve())
        self.models_cfg["models"][self.selected_model]["params"] = params
        save_models_config(self.models_cfg)
        self.params_panel.load_params(params)
        self.extra_entry.delete(0, "end")
        self.extra_entry.insert(0, str(params.get("extra_args") or ""))
        self._refresh_command_preview()
        self._refresh_ctx_preset(params)
        self._set_status("已重置为原始参数")

    def on_open_log(self):
        log = self.server.log_path
        if log and log.exists():
            os.startfile(str(log))
        elif LOG_DIR.exists():
            os.startfile(str(LOG_DIR))
        else:
            self._set_status("无日志文件")

    def on_open_web(self):
        """打开模型服务的网页界面: 优先用运行中的进程, 其次用当前参数"""
        if self.server.is_running():
            host, port = self.server.get_host_port()
        else:
            params = self._collect_params() if self.selected_model else {}
            host = params.get("host") or "127.0.0.1"
            port = params.get("port") or 8080
        url = f"http://{host}:{port}"
        webbrowser.open(url)
        self._set_status(f"已在浏览器打开 {url}")

    def on_open_help(self):
        webbrowser.open(LlamaUpdater.REPO_URL)
        self._set_status(f"已在浏览器打开 llama.cpp 官网: {LlamaUpdater.REPO_URL}")

    def on_open_help_site(self):
        """打开帮助站点: help_url 可为本地路径(相对/绝对)、file:// 或 http(s)://"""
        url = resolve_help_url(self.config.get("help_url", ""))
        if url is None:
            messagebox.showwarning(
                "帮助",
                "帮助页面文件不存在(默认应为程序目录下 help/index.html)。\n"
                "部署在线站点后, 可在 config.json 中修改 help_url 为完整网址。")
            self._set_status("帮助文件不存在, 未打开")
            return
        webbrowser.open(url)
        self._set_status(f"已在浏览器打开帮助: {url}")

    # ────────────── 版本与更新 ──────────────

    def _check_llama_available(self) -> bool:
        """检测 llama-server 是否可用(在 PATH 中, 或配置项本身是可执行文件路径)"""
        return bool(Path(self.llama_cmd).is_file() or shutil.which(self.llama_cmd))

    def _refresh_version_title(self):
        """把当前已安装的版本号显示在窗口标题栏"""
        self.installed_version = self.updater.get_version() if self.llama_available else ""
        self.title(f"llama.cpp 模型管理器  —  {self.installed_version}"
                   if self.installed_version else "llama.cpp 模型管理器")

    def on_check_update(self):
        """检查 llama.cpp 更新, 由用户决定是否更新"""
        self._set_status("正在检查 llama.cpp 更新...")
        self.btn_update.configure(state="disabled")

        def check_worker():
            result = self.updater.check_update()
            self.after(0, lambda: self._on_update_checked(result))

        threading.Thread(target=check_worker, daemon=True).start()

    def _on_update_checked(self, check_result):
        has_update, current_ver, available_ver = check_result
        self.btn_update.configure(state="normal")

        if not has_update:
            self._refresh_version_title()
            self._set_status("已是最新版本")
            messagebox.showinfo("更新检查", "llama.cpp 已是最新版本")
            return

        msg = (f"发现 llama.cpp 新版本!\n\n"
               f"当前版本: {current_ver}\n"
               f"最新版本: {available_ver}\n\n"
               "更新完成后程序会自动重启, 并按新版参数刷新面板。\n\n是否更新?")
        if not messagebox.askyesno("发现更新", msg):
            self._set_status(f"用户取消更新 (当前版本: {current_ver}, 最新版本: {available_ver})")
            return

        if self.server.is_running():
            self._set_status("正在停止当前模型以便更新...")
            self.server.stop()

        self._set_status("正在更新 llama.cpp(需下载约 200MB, 可能要几分钟)...")
        self.btn_update.configure(state="disabled")

        def report_progress(step: str):
            # winget 的当前步骤回传到状态栏; _set_status 内部做去重, 不会反复重绘
            self.after(0, lambda s=step: self._set_status(f"正在更新 llama.cpp — {s}"))

        def update_worker():
            result = self.updater.run_upgrade(on_progress=report_progress)
            self.after(0, lambda: self._on_update_completed(result))

        threading.Thread(target=update_worker, daemon=True).start()

    def _on_update_completed(self, upgrade_result):
        success, message = upgrade_result
        self.btn_update.configure(state="normal")

        if not success:
            self._set_status(f"更新失败: {message}")
            messagebox.showerror("更新失败", f"llama.cpp 更新失败\n\n{message}")
            return

        self.llama_available = self._check_llama_available()
        self._refresh_version_title()
        self._set_status(f"更新成功 ({self.installed_version}), 正在重启程序...")

        # 参数表的差异不在这里处理: 重启后启动对账会读一次新的 --help 再补齐参数,
        # 这样无论从界面更新还是手动 winget 更新, 面板都能跟上。
        messagebox.showinfo(
            "更新完成",
            f"llama.cpp 更新成功!\n\n最新版本: {self.installed_version or '未知'}\n\n"
            "程序将自动重启并按新版本参数刷新面板。",
        )
        self._restart_app()

    def _restart_app(self):
        """用同一个解释器重新拉起本程序, 然后退出当前实例"""
        script = Path(__file__).resolve()
        for stop in (self.server.stop, self.monitor.stop):
            try:
                stop()
            except Exception:
                pass

        flags = getattr(subprocess, "DETACHED_PROCESS", 0)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            # 本程序可能由 pythonw 启动(无控制台), 显式给子进程空 stdio 免得句柄继承出问题
            subprocess.Popen([sys.executable, str(script), *sys.argv[1:]],
                             cwd=str(script.parent), close_fds=True, creationflags=flags,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except OSError as exc:
            messagebox.showerror("重启失败", f"自动重启失败, 请手动重新启动程序。\n\n{exc}")
            return
        self.destroy()

    def _log_param_changes(self):
        """把参数表比对结果落盘, 方便事后翻看"""
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            lines = [f"\n[{stamp}] llama.cpp {self.installed_version or '?'} 参数表比对",
                     f"新增 {len(self.auto_added_params)}: {', '.join(self.auto_added_params) or '无'}",
                     f"取消 {len(self.sync_removed_params)}: {', '.join(self.sync_removed_params) or '无'}"]
            with open(LOG_DIR / "param_changes.log", "a", encoding="utf-8") as fp:
                fp.write("\n".join(lines) + "\n")
        except Exception:
            pass

    def _show_param_change_report(self):
        """参数表比对报告: 新增参数的介绍 + 已取消的参数清单"""
        def describe(flag: str) -> str:
            p = find_param_by_flag(flag)
            return f"{p.cli_flag}  {p.help_text}" if p else flag

        lines = []
        if self.auto_added_params:
            lines.append(f"── 新增参数 {len(self.auto_added_params)} 个"
                         f"（已归入「新增功能」标签页）──")
            seen = set()
            for f in self.auto_added_params:
                d = describe(f)
                if d not in seen:
                    seen.add(d)
                    lines.append("  + " + d)
        if self.sync_removed_params:
            if lines:
                lines.append("")
            lines.append(f"── 已取消的参数 {len(self.sync_removed_params)} 个"
                         f"（本次 llama-server --help 中已不存在）──")
            for f in self.sync_removed_params:
                lines.append("  - " + f)
            lines.append("  注: 以上条目仅作提示, 仍保留在参数面板中, 可自行核对后清理。")
        if not lines:
            return

        win = ctk.CTkToplevel(self)
        win.title("llama.cpp 参数表更新报告")
        win.geometry("700x440")
        win.minsize(560, 320)
        win.transient(self)
        win.after(120, lambda: (win.lift(), win.focus_force()))

        ver = self.installed_version or "当前版本"
        ctk.CTkLabel(
            win, text=f"参数表已更新  —  llama.cpp {ver}",
            font=("Microsoft YaHei", 16, "bold"), text_color=ut.INK,
        ).pack(pady=(16, 8), padx=22, anchor="w")

        box = ctk.CTkTextbox(win, font=("Microsoft YaHei", 12), wrap="word")
        box.pack(fill="both", expand=True, padx=22, pady=(0, 12))
        box.insert("1.0", "\n".join(lines))
        box.configure(state="disabled")

        ctk.CTkButton(win, text="知道了", command=win.destroy).pack(pady=(0, 16))

    def _show_llama_missing_dialog(self):
        """未检测到 llama.cpp 时的提醒窗口, 附可复制的开源项目地址"""
        win = ctk.CTkToplevel(self)
        win.title("未检测到 llama.cpp")
        win.geometry("640x310")
        win.minsize(540, 280)
        win.transient(self)
        win.after(120, lambda: (win.lift(), win.focus_force()))

        ctk.CTkLabel(
            win, text="未检测到可用的 llama.cpp",
            font=("Microsoft YaHei", 17, "bold"), text_color=ut.ERR,
        ).pack(pady=(20, 8), padx=22, anchor="w")

        tip = (
            f"系统 PATH 中找不到命令 “{self.llama_cmd}”，程序无法加载模型。\n\n"
            "安装方式一（推荐，自动加入 PATH）：\n"
            "    winget install --id ggml.llamacpp\n\n"
            "安装方式二：从开源项目主页下载预编译包，并确保 llama-server 在 PATH 中。"
        )
        ctk.CTkLabel(win, text=tip, font=("Microsoft YaHei", 12),
                     justify="left", anchor="w", wraplength=580).pack(padx=22, anchor="w")

        ctk.CTkLabel(win, text="开源项目地址：", font=("Microsoft YaHei", 12),
                     anchor="w").pack(padx=22, pady=(12, 2), anchor="w")

        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(fill="x", padx=22)
        row.grid_columnconfigure(0, weight=1)

        ent = ctk.CTkEntry(row, font=("Consolas", 12))
        ent.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ent.insert(0, LlamaUpdater.REPO_URL)
        ent.configure(state="readonly")

        btn_copy = ctk.CTkButton(row, text="复制链接", width=90)

        def copy_url():
            self.clipboard_clear()
            self.clipboard_append(LlamaUpdater.REPO_URL)
            self.update()
            btn_copy.configure(text="已复制")
            win.after(1500, lambda: btn_copy.configure(text="复制链接"))

        btn_copy.configure(command=copy_url)
        btn_copy.grid(row=0, column=1)

        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.pack(fill="x", padx=22, pady=(16, 18))
        ctk.CTkButton(btns, text="打开项目主页",
                      command=lambda: webbrowser.open(LlamaUpdater.REPO_URL)).pack(side="left")
        ctk.CTkButton(btns, text="关闭", command=win.destroy).pack(side="right")

    # ────────────── 主题 ──────────────

    def _theme_button_icon(self) -> str:
        """图标表示"点一下会切到"的主题: 当前浅色显示月亮(切深色), 深色显示太阳"""
        return "☾" if ut.CURRENT == "light" else "☀"

    def _theme_tooltip(self) -> str:
        nxt = theme_mod.THEMES[theme_mod.next_theme_key(ut.CURRENT)]["label"]
        return f"切换到{nxt}主题"

    def on_toggle_theme(self):
        self._swap_theme(theme_mod.next_theme_key(ut.CURRENT))

    def _swap_theme(self, key: str):
        """换肤: 存状态 → 换调色板 → 重建界面 → 回填。

        CTk/tk 控件的颜色是**创建时**取死的, 逐控件 configure 会漏(参数面板 250+
        个原生控件), 所以按 Mo-Shen 的做法原地重建界面, 再把用户状态填回去。
        """
        if key not in theme_mod.THEMES or key == ut.CURRENT:
            return

        selected = self.selected_model
        params = self._collect_params() if selected else None
        raw_status = getattr(self, "_last_status_text", "") or ""
        status = raw_status[len("状态: "):] if raw_status.startswith("状态: ") else ""

        ut.apply(key)
        self.theme_key = key
        tooltip_mod.reset_tip_window()      # 提示窗颜色也是建窗时取的
        self.config["theme"] = key
        try:
            save_config(self.config)
        except OSError as e:
            self._set_status(f"主题已切换, 但配置保存失败: {e}")

        # 拆掉旧界面(只留窗口本身), 并清掉所有"变化才重绘"的缓存
        for child in self.winfo_children():
            child.destroy()
        self.model_buttons = {}
        self._hl_key = None
        self._last_status_text = None
        for attr in ("_last_gpu_frac", "_last_ram_frac"):
            setattr(self, attr, -1.0)      # 进度条缓存: 必须给数值, _set_progress 要做减法
        for attr in ("_last_gpu_text", "_last_ram_text"):
            setattr(self, attr, None)
        self.configure(fg_color=ut.BG)

        self._build_ui()
        self._refresh_model_list()

        if selected and selected in self.model_buttons:
            # 回填未保存的编辑(列表刷新可能已经把参数重置成配置里的值)
            self.selected_model = selected
            if params is not None:
                self.params_panel.load_params(params)
            self.extra_entry.delete(0, "end")
            self.extra_entry.insert(0, str((params or {}).get("extra_args") or ""))
            self._refresh_command_preview()
            self._refresh_ctx_preset(params or {})
            self._highlight_model_button(selected)

        ut.apply_titlebar_theme(self, ut.CURRENT == "dark")
        if status:
            self._set_status(status)

    # ────────────── 状态与监控 ──────────────

    def _set_status(self, text: str):
        # 只在文本变化时才重绘状态栏, 避免定时器反复触发画布重绘
        full = f"状态: {text}"
        if full == getattr(self, "_last_status_text", None):
            return
        self._last_status_text = full
        self.status_label.configure(text=full)

    def _set_status_running(self):
        if not self.server.is_running():
            return
        host, port = self.server.get_host_port()
        mm, ss = divmod(self.server.uptime_seconds(), 60)
        hh, mm = divmod(mm, 60)
        self._set_status(
            f"运行中: {self.server.model_name} | {host}:{port} | 运行 {hh:02d}:{mm:02d}:{ss:02d}")

    def _check_process_state(self):
        """定时检查进程是否已意外退出"""
        # 重启的「卸载中→加载中」两段状态由 on_restart_clicked 自己维护, 别被覆盖
        if not self._restarting:
            if self.server.process is not None and not self.server.is_running():
                code = self.server.return_code()
                if code is not None:
                    self._set_status(f"已停止 (code={code})")
                    self._highlight_model_button()   # 进程意外退出, 取消运行中亮显
            elif self.server.is_running():
                self._set_status_running()
        self.after(200, self._check_process_state)

    def _tick_monitor(self):
        snap = self.monitor.latest()
        # 以下均为"变化才重绘": CTk 控件 configure/set 都会重绘画布
        if snap.vram_total_mb > 0:
            self._set_progress("gpu", self.gpu_bar, snap.vram_used_mb / snap.vram_total_mb)
            self._set_label_cached("gpu",
                f"{snap.gpu_name} | {snap.vram_used_mb:.0f}/{snap.vram_total_mb:.0f} MiB | "
                f"Util {snap.gpu_util_pct:.0f}% | {snap.gpu_temp_c:.0f}°C")
        elif snap.error:
            self._set_label_cached("gpu", f"GPU: {snap.error}")

        if snap.ram_total_gb > 0:
            self._set_progress("ram", self.ram_bar, snap.ram_pct / 100.0)
            self._set_label_cached("ram",
                f"{snap.ram_used_gb:.1f}/{snap.ram_total_gb:.1f} GB ({snap.ram_pct:.0f}%)")

        self.after(self.monitor_interval, self._tick_monitor)

    def _set_progress(self, which: str, bar, frac: float) -> None:
        """进度条变化很小就不重绘"""
        frac = min(frac, 1.0)
        attr = f"_last_{which}_frac"
        if abs(frac - getattr(self, attr, -1)) <= 0.001:
            return
        setattr(self, attr, frac)
        bar.set(frac)

    def _set_label_cached(self, which: str, text: str):
        """标签文本没变就不 configure"""
        attr = f"_last_{which}_text"
        if text == getattr(self, attr, None):
            return
        setattr(self, attr, text)
        (self.gpu_label if which == "gpu" else self.ram_label).configure(text=text)

    # ────────────── 清理 ──────────────

    def on_closing(self):
        if self._closing:
            return
        if self._restarting:
            if not messagebox.askyesno(
                    "退出确认",
                    "模型正在重启(卸载→加载)中, 现在退出会中断重启并卸载模型。\n\n确定退出吗?"):
                return
        elif self.server.is_running():
            mm, ss = divmod(self.server.uptime_seconds(), 60)
            hh, mm = divmod(mm, 60)
            if not messagebox.askyesno(
                    "退出确认",
                    f"模型仍在运行: {self.server.model_name}\n"
                    f"已运行 {hh:02d}:{mm:02d}:{ss:02d}\n\n"
                    "关闭 llama-shell 会卸载该模型(结束 llama-server 进程), "
                    "正在进行的请求会被中断。\n\n确定关闭吗?"):
                self._set_status_running()      # 用户取消: 状态栏恢复"运行中"
                return

        self._closing = True        # 之后后台线程的回调一律丢弃, 不再 update 界面
        if self.server.process is not None:
            self._set_status("退出中: 正在卸载模型 ...")
            self.update_idletasks()
            if not self.server.unload():
                messagebox.showwarning(
                    "未能结束模型进程",
                    "llama-server 进程仍然存活, 显存未释放。\n"
                    f"PID: {self.server.pid()}\n\n请到任务管理器手动结束。")

        self.monitor.stop()
        self.server.stop()      # 未运行时就只做收尾(关日志句柄)
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
