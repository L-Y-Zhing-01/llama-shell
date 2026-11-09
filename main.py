# -*- coding: utf-8 -*-
"""llama.cpp 模型管理器 — 单页 GUI 主程序。

只负责界面装配、事件分发与定时刷新; 具体职责已拆到:
    app_config.py     路径常量与配置读写
    ui_theme.py       主题层(ttkbootstrap 封装)与字体
    param_ui.py       参数行控件与参数面板
    server_process.py llama-server 子进程管理
    updater.py        本地版本查询与更新检查(比对 build 号)
    llama_release.py  GitHub release 下载 / 校验 / 就地替换
    llama_install.py  按本机显卡挑后端
    install_dialog.py 未检测到 llama.cpp 时的安装弹窗
    llama_params.py   参数定义表与命令行构建
    monitor.py        GPU/RAM 监控线程
    winenv.py         CREATE_NO_WINDOW / system_proxy 等共享常量与小工具
"""

import os
import shutil
import socket
import subprocess
import sys
import threading
import tkinter as tk
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from time import perf_counter
from tkinter import filedialog, messagebox

try:
    import ttkbootstrap as tb
except ImportError as _tb_exc:      # 缺依赖时模块体都进不去, 交给环境自检模块
    try:
        import boot_check
        boot_check.fatal_missing("ttkbootstrap", _tb_exc)
    except Exception:
        # 连 boot_check 都导入不了(sys.path 被改/文件被挪)时的最后防线:
        # 手写 crash.log, 保证 pythonw 下不是无声失败
        try:
            from datetime import datetime as _dt
            _log = Path(__file__).resolve().parent / "logs" / "crash.log"
            _log.parent.mkdir(parents=True, exist_ok=True)
            with open(_log, "a", encoding="utf-8") as _fp:
                _fp.write(f"\n{_dt.now():%Y-%m-%d %H:%M:%S}  缺少 ttkbootstrap\n"
                          f"{_tb_exc!r}\n（boot_check 模块不可达, 请检查启动方式）\n")
        except OSError:
            pass
    raise SystemExit(1) from None

from app_config import (
    LOG_DIR, SCRIPT_DIR, init_models_config, load_config, load_models_config,
    pick_models_dir, remember_models_dir, resolve_help_url,
    save_config, save_models_config,
)
from llama_params import build_argv, build_command_string, find_param_by_flag, get_default_params
import llama_install
import install_dialog
import llama_locate
import llama_release
from monitor import MonitorWorker
from param_ui import ParamsPanel
from server_process import (
    ServerProcess, argv_has, argv_value, kill_llama_servers, wait_port_released,
    wait_until_ready,
)
import tooltip as tooltip_mod
import ui_theme as ut
from ui_theme import DEFAULT_THEME
from updater import LlamaUpdater

CUSTOM_CTX_LABEL = "+ 自定义…"   # 上下文预设下拉里的特殊项, 选中后弹出自定义对话框

#: 本机 IP 重探间隔(ms): 换网/VPN 后无需重启程序
IP_REFRESH_MS = 5000

#: 普通按钮 → bootstyle。ttkbootstrap 的按钮配色靠构造参数, 不能事后 configure。
_BOOTSTYLE = {
    "primary": "primary",
    "danger": "danger",
    None: "secondary-outline",
}


class App(tb.Window):
    def __init__(self):
        # 配置要在建窗之前读: 窗口的初始主题由它决定(tb.Window 只接受构造期指定)
        self.config = load_config()
        theme_key = self.config.get("theme", DEFAULT_THEME)
        if not ut.is_valid(theme_key):
            theme_key = DEFAULT_THEME

        super().__init__(themename=theme_key)
        self.title("llama.cpp 模型管理器")
        self.geometry("1000x620")
        # 参数名直接显示 flag(如 --spec-draft-cpu-strict-batch, 实测占 205px),
        # 两列布局下再窄就会把输入框挤没(实测 800px 时该页可用宽度为 -26px),
        # 因此最小宽度与默认宽度一致。
        self.minsize(1000, 420)

        # 主题层: 绑定 Style 单例并注册自定义样式钩子(on_theme_change 会立即跑一次)
        self.theme_key = theme_key
        ut.bind(self.style, theme_key)

        # 启动扫描目录: 上次使用的那个; 该路径不可用(移动盘没插/换电脑)时按历史回退,
        # 见 app_config.pick_models_dir —— 绝不把配置里的记录抹掉
        self.models_dir, self.models_dir_source = pick_models_dir(self.config)
        # llama-server 的位置每次启动重新解析: config 里的绝对路径只当第一候选,
        # 换机器/手动解压/换盘符都能自己找回来(见 llama_locate)
        self.llama_cmd, self.locate_note = self._resolve_llama_cmd()
        self.monitor_interval = self.config.get("monitor_interval_ms", 1000)
        self.context_presets = self.config.get("context_presets", [])

        # 版本号 & 可用性检测
        self.updater = LlamaUpdater(self.llama_cmd, LOG_DIR)
        # 上次因"模型在跑"而只下载未替换的更新, 此时没有任何 llama-server 在跑,
        # 覆盖 exe/dll 不会撞文件锁 —— 在界面出来之前落地, 后面的对账就按新版跑。
        self.pending_note = self._apply_pending_update()
        self.llama_available = self._check_llama_available()
        self._refresh_version_title()

        # 启动对账: 按 llama-server --help 与上一次的参数表比对增删
        # (exe 可能被手动/本程序更新过, 面板得跟上)
        sync = (self.updater.auto_sync_params()
                if self.llama_available else {"added": [], "removed": []})
        self.auto_added_params = sync["added"]        # 新增 flag(归入「新增功能」页)
        self.sync_removed_params = sync["removed"]    # 上次有、本次 --help 里消失的 flag

        # 模型配置
        self.models_cfg = load_models_config()
        if not self.models_cfg.get("models"):
            self.models_cfg = init_models_config(self.models_dir)

        # 进程 & 监控: 多开 — 每个模型一个 ServerProcess, 键为模型名
        self.servers: dict[str, ServerProcess] = {}
        self.monitor = MonitorWorker(self.monitor_interval)
        self.monitor.start()

        # 状态
        self.selected_model: str = ""
        self.model_buttons: dict = {}
        self._restarting = False    # 重启(卸载→加载)进行中, 期间屏蔽再次触发
        self._closing = False       # 已确认退出, 防止关闭流程重入
        self._updating = False      # 正在下载/替换 llama.cpp, 此时退出会打断替换

        # 构建 UI
        self._build_ui()
        self._refresh_model_list()
        # 目录下拉框的显示值: 创建时只给了 values 没给值, 启动即为空白;
        # 这里按已解析的 models_dir(上次使用的目录)补上, 与列表保持一致
        self._sync_dir_combo()
        self._check_process_state()
        self._tick_monitor()

        # 对账有变化时给出报告: 新增参数的介绍(归入「新增功能」页)与已取消的参数
        if self.auto_added_params or self.sync_removed_params:
            self._set_status(f"参数表已更新: 新增 {len(self.auto_added_params)}, "
                             f"取消 {len(self.sync_removed_params)}, 见「新增功能」页")
            self._log_param_changes()
            self.after(300, self._show_param_change_report)

        # 列表为空时把"为什么空"说清楚: 是目录没模型, 还是路径压根不存在
        if not self.model_buttons:
            reason = {"目录不存在": "目录不存在(移动盘未插/换机打开?)",
                      "上次使用(无模型)": "上次使用的目录里没有 .gguf",
                      "历史目录(无模型)": "历史目录里没有 .gguf"}.get(
                          self.models_dir_source, "目录内没有 .gguf 模型")
            self._set_status(f"模型列表为空: {reason} — {self.models_dir}"
                             f"（点「模型目录」重新选择）")

        ut.apply_titlebar_theme(self, ut.is_dark(ut.CURRENT))

        # 本机 IP 定时重探(换 Wi-Fi / 插网线 / 连 VPN 后自动跟上)
        self._ip_job = self.after(IP_REFRESH_MS, self._refresh_ip)

        # 上次暂存的更新已落地 / 本次启动重新定位过 llama.cpp: 在状态栏说一声
        startup_notes = [n for n in (self.locate_note, self.pending_note) if n]
        if startup_notes:
            self.after(400, lambda: self._set_status("；".join(startup_notes)))

        # 未检测到 llama.cpp 时提醒用户
        if not self.llama_available:
            self.after(200, self._show_llama_missing_dialog)

    # ────────────── 多实例辅助 ──────────────

    def _server_of(self, name: str = None):
        """取某模型(缺省为当前选中)的 ServerProcess; 没加载过返回 None"""
        return self.servers.get(self.selected_model if name is None else name)

    def _running_servers(self) -> dict:
        """所有仍在运行的实例: {模型名: ServerProcess}"""
        return {n: s for n, s in self.servers.items() if s.is_running()}

    def _check_port_conflict(self, name: str, argv: list) -> bool:
        """加载前与其他运行中实例做端口冲突检查。返回是否允许继续。

        端口相同的服务不能同时监听, 除非双方都勾了 --reuse-port;
        冲突时弹窗提醒, 用户选「仍要加载」才放行(实际能否绑定由 llama-server 决定)。
        """
        try:
            port = int(argv_value(argv, "--port", 8080))
        except (TypeError, ValueError):
            port = 8080
        conflicts = [(n, s) for n, s in self.servers.items()
                     if n != name and s.is_running() and s.get_host_port()[1] == port]
        if not conflicts:
            return True
        names = ", ".join(n for n, _ in conflicts)
        if argv_has(argv, "--reuse-port") and all(
                argv_has(s.argv, "--reuse-port") for _, s in conflicts):
            self._set_status(f"端口 {port} 与 {names} 共用 (双方均勾选 --reuse-port)")
            return True
        go = messagebox.askyesno(
            "端口冲突",
            f"端口 {port} 已被运行中的模型占用: {names}\n\n"
            "两个服务不能共用同一端口, 除非双方都勾选 --reuse-port 参数。\n\n"
            "仍要加载吗? (不推荐, 新进程可能绑定端口失败)")
        if not go:
            self._set_status(f"已取消加载: 端口 {port} 与 {names} 冲突")
        return go

    # ────────────── UI 构建 ──────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=0, minsize=380)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=0)  # topbar
        self.grid_rowconfigure(1, weight=1)  # body

        self._build_topbar()
        self._build_sidebar()

        # 右侧主区域
        self.right = tb.Frame(self)
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
        topbar = tb.Frame(self)
        topbar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=2)
        # 按钮区单独占一列(weight=1): 让它吃掉全部剩余宽度, 最右的图标按钮才不会被
        # 9 个等宽按钮挤出窗口(实测直接同排 grid 时图标会被裁掉半个)。
        topbar.grid_columnconfigure(0, weight=1)
        bar = tb.Frame(topbar)
        bar.grid(row=0, column=0, sticky="ew")

        buttons = [
            ("加载模型", self.on_load_clicked, "primary"),
            ("卸载模型", self.on_unload_clicked, "danger"),
            ("重启模型", self.on_restart_clicked, None),
            ("保存参数", self.on_save_params, None),
            ("重置参数", self.on_reset_params, None),
            ("打开日志", self.on_open_log, None),
            ("官网", self.on_open_llama_site, None),
            ("更新", self.on_check_update, None),
            ("帮助", self.on_open_help_site, None),
        ]
        self.btn_update = None   # 检查/更新期间需要禁用
        for col, (text, cmd, kind) in enumerate(buttons):
            bar.grid_columnconfigure(col, weight=1, uniform="btn")
            btn = tb.Button(bar, text=text, command=cmd, bootstyle=_BOOTSTYLE[kind])
            btn.grid(row=0, column=col, padx=4, pady=4, sticky="ew")
            if text == "更新":
                self.btn_update = btn

        # 主题入口: 标题栏最右侧一个图标按钮, 点开主题选择面板(15 组 × 浅深)
        self.btn_theme = tb.Button(
            topbar, text="◐", width=3, bootstyle="secondary-outline",
            command=self.on_open_theme_panel,
        )
        self.btn_theme.grid(row=0, column=1, padx=(2, 4), pady=4, sticky="ns")
        tooltip_mod.Tooltip(self.btn_theme,
                            lambda: ("主题", "", self._theme_tooltip(), ""))

    def _build_sidebar(self):
        sidebar = tb.Frame(self, width=380)
        sidebar.grid(row=1, column=0, sticky="nsew", padx=4, pady=2)
        sidebar.grid_rowconfigure(1, weight=1)
        sidebar.grid_columnconfigure(0, weight=1)

        # 模型目录: 历史(上次使用目录已自动加载) + 浏览选新目录
        dir_frame = tb.Frame(sidebar)
        dir_frame.grid(row=0, column=0, sticky="ew", padx=2, pady=2)
        dir_frame.grid_columnconfigure(0, weight=1)
        self.dir_combo = tb.Combobox(
            dir_frame, values=self._dir_combo_values(), font=ut.FONT)
        self.dir_combo.grid(row=0, column=0, padx=5, pady=4, sticky="ew")
        self.dir_combo.bind("<<ComboboxSelected>>",
                            lambda e: self.on_pick_dir_from_history(self.dir_combo.get()))
        tb.Button(dir_frame, text="删除", width=5, bootstyle="secondary-outline",
                  command=self.on_delete_dir_from_history).grid(
            row=0, column=1, padx=(0, 2), pady=4)
        tb.Button(dir_frame, text="模型目录", width=9, bootstyle="secondary-outline",
                  command=self.on_browse_models_dir).grid(
            row=0, column=2, padx=(0, 5), pady=4)

        self.model_list_frame = tb.ScrolledFrame(sidebar, auto_hide=True)
        self.model_list_frame.grid(row=1, column=0, sticky="nsew", padx=2, pady=2)
        self.model_list_frame.grid_columnconfigure(0, weight=1)

        ctx_frame = tb.Frame(sidebar)
        ctx_frame.grid(row=2, column=0, sticky="ew", padx=2, pady=4)
        ctx_frame.grid_columnconfigure(1, weight=1)
        tb.Label(ctx_frame, text="上下文预设").grid(
            row=0, column=0, padx=(5, 2), pady=4, sticky="w")

        preset_labels = self._ctx_preset_labels()
        self.ctx_preset_combo = tb.Combobox(
            ctx_frame, values=preset_labels, font=ut.FONT)
        self.ctx_preset_combo.grid(row=0, column=1, padx=(2, 5), pady=4, sticky="ew")
        self.ctx_preset_combo.bind(
            "<<ComboboxSelected>>",
            lambda e: self.on_ctx_preset_changed(self.ctx_preset_combo.get()))

        tb.Button(ctx_frame, text="刷新模型列表", bootstyle="secondary-outline",
                  command=self._refresh_model_list).grid(
            row=1, column=0, columnspan=2, padx=5, pady=4, sticky="ew")

    def _build_monitor_bar(self):
        monitor_frame = tb.Frame(self.right)
        monitor_frame.grid(row=1, column=0, sticky="ew", padx=2, pady=2)
        monitor_frame.grid_columnconfigure(1, weight=1)
        monitor_frame.grid_columnconfigure(3, weight=1)

        # (标题, 进度条属性名, bootstyle, 标签属性名)
        rows = [
            ("GPU:", "gpu_bar", "success-striped", "gpu_label"),
            ("RAM:", "ram_bar", "info-striped", "ram_label"),
        ]
        for row, (title, bar_attr, style_name, label_attr) in enumerate(rows):
            tb.Label(monitor_frame, text=title, width=5, anchor="e").grid(
                row=row, column=0, padx=(4, 2), pady=2)
            bar = tb.Progressbar(monitor_frame, maximum=1, value=0,
                                 bootstyle=style_name)
            bar.grid(row=row, column=1, padx=2, pady=2, sticky="ew")
            label = tb.Label(monitor_frame, text="--", width=42, anchor="w")
            label.grid(row=row, column=2, padx=4, pady=2, sticky="w")
            setattr(self, bar_attr, bar)
            setattr(self, label_attr, label)

    def _build_status_bar(self):
        status_frame = tb.Frame(self.right)
        status_frame.grid(row=2, column=0, sticky="ew", padx=2, pady=2)
        status_frame.grid_columnconfigure(0, weight=1)
        self.status_label = tb.Label(status_frame, text="状态: 空闲",
                                     anchor="w", style=ut.S_STATUS)
        self.status_label.grid(row=0, column=0, padx=8, pady=4, sticky="ew")
        tb.Button(status_frame, text="网页运行", width=9, bootstyle="secondary-outline",
                  command=self.on_open_web).grid(
            row=0, column=1, padx=(8, 8), pady=4)

    def _build_command_preview(self):
        cmd_frame = tb.Frame(self.right)
        cmd_frame.grid(row=3, column=0, sticky="ew", padx=2, pady=2)
        cmd_frame.grid_columnconfigure(0, weight=1)
        cmd_frame.grid_rowconfigure(2, weight=1)

        cmd_top = tb.Frame(cmd_frame)
        cmd_top.grid(row=0, column=0, sticky="ew", padx=2)
        cmd_top.grid_columnconfigure(0, weight=1)
        tb.Label(cmd_top, text="命令预览:", anchor="w").grid(
            row=0, column=0, padx=4, sticky="w")
        tb.Button(cmd_top, text="复制", width=5, bootstyle="secondary-outline",
                  command=self.on_copy_command).grid(row=0, column=1, padx=4)
        # 本机 IP: 供局域网设备访问, 只读可选可复制(不影响左侧的「复制」按钮 —— 它只复制命令)
        tb.Label(cmd_top, text="本机 IP:", anchor="e").grid(
            row=0, column=2, padx=(8, 2))
        self.ip_entry = tb.Entry(cmd_top, width=15, justify="center", font=ut.MONO_FONT)
        self.ip_entry.grid(row=0, column=3, padx=(0, 4))
        self._set_ip_entry()
        self.ip_entry.configure(state="readonly")
        self.ip_entry.bind("<FocusIn>", self._select_ip)
        self.ip_entry.bind("<Double-Button-1>", self._select_ip)

        # 额外参数不是 llama.cpp 参数(无 --flag), 不进参数面板, 放在命令预览旁:
        # 它直接影响命令末尾的拼接, 与预览放一起最直观。
        extra_row = tb.Frame(cmd_frame)
        extra_row.grid(row=1, column=0, sticky="ew", padx=2)
        extra_row.grid_columnconfigure(1, weight=1)
        tb.Label(extra_row, text="额外参数:", anchor="w").grid(
            row=0, column=0, padx=4, sticky="w")
        self.extra_entry = tb.Entry(extra_row, font=ut.FONT)
        self.extra_entry.grid(row=0, column=1, sticky="ew", padx=4, pady=2)
        self.extra_entry.bind("<Return>", lambda e: self._on_extra_args_changed())
        self.extra_entry.bind("<FocusOut>", lambda e: self._on_extra_args_changed())
        tb.Label(extra_row, text="如: --verbose 1", style=ut.S_HINT).grid(
            row=0, column=2, padx=(0, 4))

        # 命令预览是一段常驻可见的只读文本: 用 Text + 只读状态, 配色自动跟随主题
        self.cmd_textbox = tb.Text(cmd_frame, height=4, font=ut.MONO_FONT,
                                   wrap="word", padx=6, pady=4)
        self.cmd_textbox.grid(row=2, column=0, sticky="nsew", padx=2, pady=2)
        self.cmd_textbox.insert("1.0", "请选择模型...")
        self.cmd_textbox.configure(state="disabled")

    #: UDP 探测目标: 只用来问内核"从哪张网卡出去", 不实际发包。
    #: 写多个是因为部分企业网/校园网会拦特定网段, 单个失败会被误判成本机只有回环。
    _IP_PROBES = ("8.8.8.8", "114.114.114.114", "223.5.5.5", "1.1.1.1")

    @classmethod
    def _local_ip(cls) -> str:
        """本机在局域网中的 IPv4 —— 每次运行时探测, 不写死任何地址。

        1) 对多个探测目标各做一次 UDP connect(只查路由表), 收集出口地址;
        2) 再枚举本机网卡地址, 覆盖那些没走默认路由的网段;
        3) 统一排序后取最优: 192.168.x < 10.x < 172.16-31.x(Docker/WSL 常用)
           < 其他私有段 < 公网; 回环 / 169.254.x / 0.0.0.0 一律剔除。

        之所以"排序"而不是"取第一个": 装了 VPN / WSL / Hyper-V 的机器,
        网卡枚举的第一个经常是虚拟网卡, 直接取会把 not-a-LAN-ip 显示给用户。
        """
        cands = []
        for target in cls._IP_PROBES:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.connect((target, 80))
                ip = sock.getsockname()[0]
                if ip:
                    cands.append(ip)
            except OSError:
                continue
            finally:
                sock.close()
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                cands.append(info[4][0])
        except OSError:
            pass
        return cls._best_ip(cands)

    @staticmethod
    def _best_ip(cands) -> str:
        """从候选地址里挑最像"局域网地址"的那个; 全不可用退回 127.0.0.1。"""
        import ipaddress
        best, best_rank = "127.0.0.1", 99
        for ip in cands:
            try:
                addr = ipaddress.IPv4Address(ip)
            except ValueError:
                continue
            if addr.is_loopback or addr.is_link_local or addr.is_unspecified:
                continue
            if ip.startswith("192.168."):
                rank = 0
            elif ip.startswith("10."):
                rank = 1
            elif addr in ipaddress.IPv4Network("172.16.0.0/12"):
                rank = 2
            elif addr.is_private:
                rank = 3
            else:
                rank = 4               # 公网地址: 仅在前几档都没有时才用
            if rank < best_rank:       # 同档取先出现的(探测结果排在枚举之前)
                best, best_rank = ip, rank
        return best

    def _set_ip_entry(self, ip: str = None):
        ip = ip or self._local_ip()
        self._ip_shown = ip
        self.ip_entry.configure(state="normal")
        self.ip_entry.delete(0, "end")
        self.ip_entry.insert(0, ip)
        self.ip_entry.configure(state="readonly")

    def _refresh_ip(self):
        """定时重探 IP: 换 Wi-Fi / 插网线 / 连上 VPN 之后不用重启程序。

        输入框正在聚焦时跳过覆盖 —— 用户可能正要点全选复制, 下次再来。
        """
        try:
            if self.focus_get() is not self.ip_entry:
                ip = self._local_ip()
                if ip != getattr(self, "_ip_shown", None):
                    self._set_ip_entry(ip)
        except Exception:
            pass
        self._ip_job = self.after(IP_REFRESH_MS, self._refresh_ip)

    def _select_ip(self, _e=None):
        """获得焦点/双击时全选, Ctrl+C 即复制"""
        try:
            self.ip_entry.select_range(0, "end")
            self.ip_entry.icursor("end")
        except Exception:
            pass
        return "break"          # 双击时吃掉默认的"选一个词", 保持全选

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

        def _raise():
            """确保窗口可见: CTk 5.2.2 的坑 —— set_appearance_mode 会 withdraw
            主窗并嵌套跑 update(), 这期间创建的 Toplevel 会被它 titlebar 配色的
            withdraw→revert 流程钉在 withdrawn(实测复现), 且时机不定,
            所以在多个点位反复兜底。"""
            if not dlg.winfo_exists():
                return
            if dlg.state() in ("withdrawn", "iconic"):
                try:
                    dlg.deiconify()
                except Exception:
                    pass
            dlg.lift()
            dlg.focus_force()

        def _grab():
            if dlg.winfo_exists():
                _raise()
                try:
                    dlg.grab_set()
                except Exception:
                    pass

        dlg.after(150, _grab)
        dlg.after(450, _raise)      # 竞态窗口内创建的弹窗需要二次/三次抬升
        dlg.after(1000, _raise)

    def on_delete_dir_from_history(self):
        """弹出历史目录列表, 逐条删除记录(仅删记录, 不动磁盘; 当前使用中的不可删)"""
        if self._dialog_active("_dirdel_dlg"):
            return
        dlg = tb.Toplevel(self)
        dlg.title("删除历史目录")
        dlg.resizable(False, False)
        dlg.transient(self)
        tb.Label(dlg, text="仅删除下拉框里的历史记录, 不会删除磁盘上的文件夹。",
                 justify="left").grid(row=0, column=0, padx=14,
                                      pady=(12, 6), sticky="w")
        body = tb.Frame(dlg)
        body.grid(row=1, column=0, padx=14, pady=(0, 14), sticky="ew")

        def rebuild():
            for w in body.winfo_children():
                w.destroy()
            hist = self.config.get("models_dir_history", [])
            if not hist:
                tb.Label(body, text="历史记录为空", style=ut.S_HINT).grid(
                    row=0, column=0, padx=2, pady=6, sticky="w")
                return
            for i, d in enumerate(hist):
                cur = d.casefold() == str(self.models_dir).casefold()
                tag = ("  (当前使用)" if cur
                       else ("" if Path(d).is_dir() else "  (不存在)"))
                tb.Label(body, text=d + tag, justify="left",
                         wraplength=520).grid(
                    row=i, column=0, padx=(2, 8), pady=3, sticky="w")
                btn = tb.Button(body, text="删除", width=5, bootstyle="danger-outline",
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
        running = self._running_servers()
        if running:
            self._set_status(f"有模型运行中 ({', '.join(running)}), 请先卸载再切换模型目录")
            self._sync_dir_combo()
            return
        p = Path(dir_str)
        if not p.is_dir():
            self._set_status(f"目录不存在: {dir_str}")
            self._sync_dir_combo()
            return

        self.models_dir = p.resolve()
        self.models_dir_source = "用户选择"
        self.config["models_dir_history"] = remember_models_dir(
            self.config, str(self.models_dir))
        # 实例按需创建(ServerProcess 用 models_dir 作子进程工作目录), 目录切换时必然空闲
        self.servers.clear()

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
            # 三种状态靠切换 ttk style 实现(见 _highlight_model_button):
            # ttk 按钮没有 fg_color / anchor 之类的构造参数, 左对齐由样式表给出。
            btn = tb.Button(self.model_list_frame, text=name,
                            style=ut.S_MODEL_IDLE,
                            command=lambda n=name: self.on_select_model(n))
            btn.grid(row=i, column=0, sticky="ew", padx=2, pady=1)
            self.model_buttons[name] = btn
            # 悬停提示: 该模型参数配置里的 --tags(保存参数后即时生效, 无需重开)
            tooltip_mod.Tooltip(btn, lambda n=name: self._model_tip(n))

        # 自动选中第一个
        if names and not self.selected_model:
            self.on_select_model(names[0])
        else:
            self._highlight_model_button()

    def _model_tip(self, name: str):
        """模型列表的悬停提示: 只显示该模型参数配置里的 --tags 标签内容。

        每次弹出时实时求值, 参数保存后立即生效; 没设 tags 返回全空 —— 不弹窗。
        (Tooltip.show 固定解包 4 元组, 只用 body 字段。)
        """
        params = self.models_cfg.get("models", {}).get(name, {}).get("params") or {}
        tags = str(params.get("tags") or "")
        body = "、".join(t.strip() for t in tags.replace("，", ",").split(",") if t.strip())
        return ("", "", body, "")

    def _highlight_model_button(self, name: str = None):
        """模型列表样式: 运行中=实心绿(可多个, 优先), 选中=实心主色, 其余=描边。

        name 缺省时用 self.selected_model, 供加载/卸载等状态变化点无参刷新。
        ttk 按钮不暴露底色, 因此这里改的是 style 名 —— 换肤后样式表重建,
        同一批 style 名会自动拿到新主题的配色, 不需要额外重放。
        """
        if name is None:
            name = self.selected_model
        running_set = frozenset(n for n, s in self.servers.items() if s.is_running())
        # 变化才重绘: (运行中集合, 选中, 列表长度) 都没变就跳过
        key = (running_set, name, len(self.model_buttons))
        if key == getattr(self, "_hl_key", None):
            return
        self._hl_key = key
        for n, btn in self.model_buttons.items():
            if n in running_set:
                btn.configure(style=ut.S_MODEL_RUN)
            elif n == name:
                btn.configure(style=ut.S_MODEL_SEL)
            else:
                btn.configure(style=ut.S_MODEL_IDLE)

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
        dlg = tb.Toplevel(self)
        dlg.title("自定义上下文预设")
        dlg.resizable(False, False)
        dlg.transient(self)
        # ttk.Entry 没有 placeholder, 提示并入标签文字(比灰字占位更不容易误读成已填值)
        tb.Label(dlg, text="名称 (留空自动取名):").grid(
            row=0, column=0, padx=(14, 6), pady=(16, 4), sticky="e")
        e_label = tb.Entry(dlg, width=22, font=ut.FONT)
        e_label.grid(row=0, column=1, padx=(0, 14), pady=(16, 4), sticky="w")
        tb.Label(dlg, text="上下文 token (如 98304):").grid(
            row=1, column=0, padx=(14, 6), pady=4, sticky="e")
        e_val = tb.Entry(dlg, width=22, font=ut.FONT)
        e_val.grid(row=1, column=1, padx=(0, 14), pady=4, sticky="w")

        def confirm():
            self._add_custom_ctx_preset(e_label.get(), e_val.get(), dlg)

        btns = tb.Frame(dlg)
        btns.grid(row=2, column=0, columnspan=2, padx=14, pady=(10, 14), sticky="e")
        tb.Button(btns, text="取消", width=7, bootstyle="secondary-outline",
                  command=dlg.destroy).grid(row=0, column=0, padx=4)
        tb.Button(btns, text="确定", width=7, bootstyle="primary",
                  command=confirm).grid(row=0, column=1)
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

        name = self.selected_model
        params = self._collect_params()
        argv = build_argv(params, self.llama_cmd)

        # 端口冲突检查: 与其他运行中实例比端口(同模型重载不算, start() 会先停旧的)
        if not self._check_port_conflict(name, argv):
            return

        # 保存参数
        self.models_cfg["models"][name]["params"] = params
        self.models_cfg["current_model"] = name
        save_models_config(self.models_cfg)

        server = self.servers.get(name)
        if server is None:
            server = ServerProcess(self.models_dir, LOG_DIR)
            self.servers[name] = server
        try:
            server.start(argv, name)
        except Exception as e:
            self._set_status(f"启动失败: {e}")
            return

        host, port = server.get_host_port()
        self._set_status(f"加载中: {name} → {host}:{port} ...")
        self._highlight_model_button()   # server 已登记, 立即亮显运行中的模型

        # 后台线程等就绪(回调绑定模型名, 多实例各自回报)
        threading.Thread(
            target=wait_until_ready,
            args=(host, port, 60,
                  lambda ok, n=name: self.after(0, lambda: self._on_server_ready(ok, n))),
            daemon=True,
        ).start()

    def _on_server_ready(self, ok: bool, name: str = ""):
        self._highlight_model_button()   # 就绪/失败后刷新一次运行中亮显
        server = self._server_of(name)
        if ok:
            if name == self.selected_model:
                self._set_status_running()
            else:
                host, port = server.get_host_port() if server else ("", 0)
                self._set_status(f"{name} 已就绪 ({host}:{port})")
        elif server is not None and server.is_running():
            self._set_status(f"{name} 未就绪 (60秒内 /health 无响应) — 进程仍在运行，请查看日志")
        else:
            code = server.return_code() if server is not None else None
            self._set_status(f"{name} 进程已退出 (code={code}) — 请查看日志")

    def on_unload_clicked(self):
        """卸载当前选中的模型(多开时其他模型不受影响)"""
        server = self._server_of()
        if server is None or server.process is None:
            self._set_status("该模型未在运行 — 空闲")
            return
        name = self.selected_model
        self._set_status(f"卸载中: {name} ...")
        self.update_idletasks()             # 先把状态画出来, unload 可能要等几秒
        if server.unload():
            self.servers.pop(name, None)
            self._highlight_model_button()  # 卸载后取消运行中亮显
            self._set_status("已卸载 — 空闲")
        else:
            self._set_status("卸载失败 — 进程仍在运行")
            messagebox.showwarning(
                "卸载失败",
                "llama-server 进程无法结束, 仍占着端口与显存。\n"
                f"PID: {server.pid()}\n\n请到任务管理器手动结束, 或查看日志确认原因。")

    def on_restart_clicked(self):
        """重启 = 先卸载(确认进程退出 + 端口释放), 再加载, 不做"边跑边起"。

        旧实现是 stop() 后盲等 500ms 再 start(): 进程若没死透(强杀失败/显存未回收),
        新实例会和旧实例抢端口与显存, 表现为"重启后加载失败"或显存翻倍。
        只重启当前选中的模型, 其他实例不受影响。
        """
        if not self.selected_model:
            return
        server = self._server_of()
        if server is None or not server.is_running():
            self.on_load_clicked()
            return
        if self._restarting:
            self._set_status("重启进行中 — 请稍候")
            return

        self._restarting = True
        host, port = server.get_host_port()
        self._set_status(f"重启 ① 卸载中: {server.model_name} ...")
        # 卸载要等进程退出(最长 0.8s 优雅 + 5s 强杀), 放到后台线程, 不冻结界面。
        # 后台线程不碰 Tk: 只写 state, 由主线程轮询拾取(跨线程调 after 不可靠)。
        state = {"done": False, "exited": False, "port_ok": False}
        threading.Thread(target=self._unload_worker,
                         args=(server, host, port, state), daemon=True).start()
        self._poll_unload(state)

    def _unload_worker(self, server: ServerProcess, host: str, port: int, state: dict):
        """后台线程: 卸载旧进程, 再等端口释放, 只写 state 不碰界面"""
        exited = server.unload()
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
                f"PID: {self._server_of().pid()}\n\n"
                "请到任务管理器手动结束后重试, 或查看日志确认原因。")
            return
        # 卸载成功: 清掉旧实例条目, _restart_load 会建新的 ServerProcess
        self.servers.pop(self.selected_model, None)
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
        server = self._server_of()
        log = server.log_path if server is not None else None
        if log and log.exists():
            os.startfile(str(log))
        elif LOG_DIR.exists():
            os.startfile(str(LOG_DIR))
        else:
            self._set_status("无日志文件")

    def on_open_web(self):
        """打开选中模型服务的网页界面: 优先用运行中的进程, 其次用当前参数"""
        server = self._server_of()
        if server is not None and server.is_running():
            host, port = server.get_host_port()
        else:
            params = self._collect_params() if self.selected_model else {}
            host = params.get("host") or "127.0.0.1"
            port = params.get("port") or 8080
        # 0.0.0.0 / :: 是"监听所有网卡", 浏览器打不开; 本机访问一律用 127.0.0.1
        if host in ("0.0.0.0", "::", ""):
            host = "127.0.0.1"
        url = f"http://{host}:{port}"
        webbrowser.open(url)
        self._set_status(f"已在浏览器打开 {url}")

    def on_open_llama_site(self):
        """打开 llama.cpp 官网(仓库主页)。本地帮助站走 on_open_help_site。"""
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

    def _resolve_llama_cmd(self) -> tuple[str, str]:
        """启动时按现场环境重新定位 llama-server, 返回 (命令, 状态栏说明)。

        config 里的绝对路径只当第一候选: 把程序目录拷到别的机器、盘符变了、
        别人是手动解压没进 PATH —— 这些情况下旧记录都是错的。找到就写回配置,
        让下次启动少走几步; 找不到则原样返回, 交给"未检测到"的安装流程。
        """
        configured = self.config.get("llama_server_cmd", "llama-server")
        try:
            found = llama_locate.resolve(configured, SCRIPT_DIR)
        except Exception:
            # 定位失败(权限/系统调用异常)绝不能拖垮启动: 退回配置值
            return configured, ""
        if found is None:
            return configured, ""

        exe = str(found.exe)
        if exe.casefold() == str(configured).casefold():
            return exe, ""
        self.config["llama_server_cmd"] = exe
        try:
            save_config(self.config)
        except OSError:
            pass
        return exe, f"已重新定位 llama.cpp: {found.display}"

    def _check_llama_available(self) -> bool:
        """检测 llama-server 是否可用(在 PATH 中, 或配置项本身是可执行文件路径)"""
        return bool(Path(self.llama_cmd).is_file() or shutil.which(self.llama_cmd))

    def _refresh_version_title(self):
        """把当前已安装的版本号显示在窗口标题栏"""
        self.installed_version = self.updater.get_version() if self.llama_available else ""
        self.title(f"llama.cpp 模型管理器  —  {self.installed_version}"
                   if self.installed_version else "llama.cpp 模型管理器")

    def on_check_update(self):
        """检查 llama.cpp 更新(比对 GitHub release 的 build 号)"""
        if not self.llama_available:
            self._set_status("未检测到 llama.cpp, 先安装再谈更新")
            self._show_llama_missing_dialog()
            return
        if self.updater.install_dir() is None:
            messagebox.showwarning(
                "无法更新",
                f"当前命令 “{self.llama_cmd}” 不是本程序安装的文件路径, "
                "找不到要替换的目录。\n\n"
                "请先用「安装」装一份本程序管理的版本(CUDA / Vulkan / CPU 可选)。")
            return

        self._set_status("正在检查 llama.cpp 更新...")
        self.btn_update.configure(state="disabled")

        def check_worker():
            info = self.updater.check_update()
            self.after(0, lambda: self._on_update_checked(info))

        threading.Thread(target=check_worker, daemon=True).start()

    def _on_update_checked(self, info: dict):
        self.btn_update.configure(state="normal")

        if not info["ok"]:
            self._set_status(f"更新检查失败: {info['error']}")
            messagebox.showwarning("更新检查", f"没能查到最新版本。\n\n{info['error']}")
            return

        if not info["has_update"]:
            self._refresh_version_title()
            self._set_status(f"已是最新 ({info['current']})")
            messagebox.showinfo(
                "更新检查",
                f"llama.cpp 已是最新。\n\n当前: {info['current']}\n最新: {info['latest']}")
            return

        dest = self.updater.install_dir()
        release = info.get("_release")
        if dest is None or not release:
            messagebox.showwarning("无法更新", "没能定位安装目录或远端构建信息，请重试。")
            return

        # ── 下载的包必须与"已装的那个后端"同一档 ──
        # 本地档位按清单/dll 推断, 再在这个版本的资产里挑同档。挑不到说明上游漏发,
        # 宁可停下让用户决定, 也不能悄悄装个 cpu 包把 GPU 推理关掉。
        local_variant = llama_release.infer_variant(dest)
        chain = llama_release.infer_chain(dest)
        try:
            _, hit_tag, _ = llama_release.pick_assets(release, chain)
        except llama_release.ReleaseError as exc:
            self._set_status(f"没有适配的包: {exc}")
            messagebox.showerror("没有适配的包",
                                 f"{info['latest']} 里找不到能用的包。\n\n{exc}")
            return

        local_label = llama_install.tag_label(local_variant)
        hit_label = llama_install.tag_label(hit_tag)
        variant_block = f"已装后端: {local_label}\n本次下载: {hit_label}"
        if hit_tag != local_variant:
            variant_block += (f"\n\n⚠ 上游这个版本没有发布 {local_label} 包，"
                              f"只能退到 {hit_label}（GPU 推理会变慢）。")

        # ── 覆盖的是谁的目录 ──
        owner = llama_locate.owner_of(dest)
        if owner == llama_locate.OWNER_FOREIGN:
            self._set_status("该目录由 WinGet 管理，未做改动")
            if messagebox.askyesno(
                    "这个目录不归本程序管",
                    f"{dest}\n\n是 WinGet 装的副本。直接覆盖会让 winget 的安装记录错乱，"
                    "所以这里不动它。\n\n"
                    "要装一份由本程序管理的版本吗？（按本机显卡选后端，"
                    "装完自动改用新位置，WinGet 那份保持原样）"):
                self._show_llama_missing_dialog(
                    "当前目录里的 llama.cpp 由 WinGet 管理，这里另装一份由本程序管理的。\n"
                    "装完会把新位置写进配置，原副本不做任何改动。")
            return
        if owner == llama_locate.OWNER_WILD and not self._owner_acknowledged(dest):
            if not messagebox.askyesno(
                    "确认接管这个目录",
                    f"{dest}\n\n"
                    "这个目录里的 llama.cpp 不是本程序安装的（没有安装清单），"
                    "可能是手动解压或从别的机器拷过来的。\n\n"
                    "更新会在这个目录内替换 exe/dll：旧文件先复制到 "
                    f".{Path(dest).name}.bak-<版本> 备份，失败自动回滚。\n\n"
                    "确定由本程序接管更新吗？（只问这一次）"):
                self._set_status("已取消更新（未接管该目录）")
                return
            self._acknowledge_owner(dest)

        # 覆盖前先确认能写、放得下: 备份是复制不是移动, 峰值不比全新安装低
        problem = llama_install.precheck_dir(dest, action="更新")
        if problem:
            self._set_status("更新目标不可用")
            messagebox.showerror("更新目标不可用", problem)
            return

        # 有模型在跑时不能覆盖 exe/dll(Windows 会拒绝写被加载的 dll):
        # 两段式 —— 只下载校验, 下次启动时再替换。
        running = self._running_servers()
        if running:
            choice = messagebox.askyesnocancel(
                "发现更新",
                f"当前版本: {info['current']}\n最新版本: {info['latest']}\n"
                f"{variant_block}\n\n"
                f"有 {len(running)} 个模型正在运行, 运行中无法覆盖 exe/dll。\n\n"
                "是：现在停止模型并立即更新\n"
                "否：只下载, 下次启动本程序时自动生效\n"
                "取消：放弃")
            if choice is None:
                self._set_status("已取消更新")
                return
            stage_only = not choice
        else:
            if not messagebox.askyesno(
                    "发现更新",
                    f"当前版本: {info['current']}\n最新版本: {info['latest']}\n"
                    f"{variant_block}\n\n"
                    "将下载约 200MB 并就地替换(旧文件留备份, 失败自动回滚),\n"
                    "完成后自动重启以刷新参数面板。\n\n是否更新?"):
                self._set_status(
                    f"用户取消更新 (当前: {info['current']}, 最新: {info['latest']})")
                return
            stage_only = False

        if not stage_only:
            for s in running.values():
                try:
                    s.stop()
                except Exception:
                    pass
            # 历史上残留的 llama-server 也会锁住 dll, 一并清扫
            kill_llama_servers(extra_names=(self.llama_cmd,))

        self._start_update_download(info, stage_only)

    def _owner_acknowledged(self, dest) -> bool:
        """这个目录之前是否已同意由本程序接管更新(按目录记一次)"""
        ack = str(self.config.get("update_owner_ack") or "")
        return bool(ack) and ack.casefold() == str(dest).casefold()

    def _acknowledge_owner(self, dest) -> None:
        self.config["update_owner_ack"] = str(dest)
        try:
            save_config(self.config)
        except OSError:
            pass

    def _start_update_download(self, info: dict, stage_only: bool):
        """后台下载 + 校验(+ 立即替换)。进度只回状态栏, 不弹模态进度条。"""
        dest = self.updater.install_dir()
        chain = llama_release.infer_chain(dest)
        # 固定住"确认时看到的那一版": stage 内部原本会自己再查一次(10 分钟缓存),
        # 缓存过期就会下到比确认框里更新的 build —— 变成"确认 A、装了 B"
        release = info.get("_release")
        self.btn_update.configure(state="disabled")
        self._updating = True
        self._update_cancel = threading.Event()
        self._set_status("准备下载…")

        def log(msg: str):
            self.after(0, lambda m=msg: self._set_status(f"更新 {info['latest']}: {m}"))

        def progress(done: int, total: int):
            mb = done / 1024 ** 2
            text = (f"更新: 下载中 {mb:.0f} / {total / 1024 ** 2:.0f} MB"
                    if total else f"更新: 已下载 {mb:.0f} MB")
            self.after(0, lambda t=text: self._set_status(t))

        def worker():
            try:
                staged = llama_release.stage(
                    dest, chain=chain, release=release, on_log=log,
                    on_progress=progress, cancel=self._update_cancel)
                if stage_only:
                    llama_release.save_pending(dest, staged)
                    result = (True, staged["tag"], True, "")
                else:
                    manifest = llama_release.commit(staged, dest, on_log=log)
                    llama_release.cleanup_cache(dest)
                    result = (True, manifest["tag"], False, "")
            except llama_release.Cancelled:
                result = (False, "", False, "已取消下载")
            except Exception as exc:
                result = (False, "", False, f"{type(exc).__name__}: {exc}")
            try:
                self.after(0, lambda: self._on_update_completed(result))
            except tk.TclError:
                pass        # 窗口已被关掉, 更新结果无处可报

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_completed(self, result):
        ok, tag, staged_only, message = result
        self._updating = False
        self.btn_update.configure(state="normal")

        if not ok:
            self._set_status(f"更新失败: {message}")
            messagebox.showerror("更新失败", f"llama.cpp 更新失败\n\n{message}")
            return

        if staged_only:
            self._set_status(f"更新已下载 ({tag}), 下次启动生效")
            messagebox.showinfo(
                "更新已下载",
                f"{tag} 已下载并校验完成。\n\n"
                "因为还有模型在运行, 这次没有替换文件。\n"
                "关闭模型后重启本程序, 新版本会自动生效并刷新参数面板。")
            return

        self.llama_available = self._check_llama_available()
        self._refresh_version_title()
        self._set_status(f"更新成功 ({self.installed_version}), 正在重启程序...")
        messagebox.showinfo(
            "更新完成",
            f"llama.cpp 已更新到 {tag}。\n\n"
            "程序将重启, 启动时按新版 --help 对账参数表。")
        self._restart_app()

    def _apply_pending_update(self) -> str:
        """启动前把上次因"模型在跑"而暂存的更新落地。

        此刻还没有任何 llama-server 进程, 覆盖 exe/dll 不会撞文件锁。
        返回一句给状态栏的说明; 无事可做返回空串。
        """
        dest = self.updater.install_dir()
        if dest is None:
            return ""
        try:
            meta = llama_release.load_pending(dest)
            if not meta:
                return ""
            manifest = llama_release.apply_pending(dest)
            return f"已应用待生效的更新: {manifest['tag']} ({manifest['variant']})"
        except Exception as exc:
            return f"待生效的更新应用失败(安装目录已保持原状): {exc}"

    def _restart_app(self):
        """用同一个解释器重新拉起本程序, 然后退出当前实例"""
        script = Path(__file__).resolve()
        for s in self.servers.values():
            try:
                s.stop()
            except Exception:
                pass
        # 更新后要换掉 exe/dll, 残留进程会锁文件导致替换失败, 顺手清扫
        try:
            kill_llama_servers(extra_names=(self.llama_cmd,))
        except Exception:
            pass
        for stop in (self.monitor.stop,):
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

        win = tb.Toplevel(self)
        win.title("llama.cpp 参数表更新报告")
        win.geometry("700x440")
        win.minsize(560, 320)
        win.transient(self)
        self._register_dialog(win, "_param_report_dlg")

        ver = self.installed_version or "当前版本"
        tb.Label(
            win, text=f"参数表已更新  —  llama.cpp {ver}",
            font=(ut.FONT_FAMILY, 15, "bold"),
        ).pack(pady=(16, 8), padx=22, anchor="w")

        box = tb.Text(win, font=(ut.FONT_FAMILY, 11), wrap="word", padx=8, pady=6)
        box.pack(fill="both", expand=True, padx=22, pady=(0, 12))
        box.insert("1.0", "\n".join(lines))
        box.configure(state="disabled")

        tb.Button(win, text="知道了", width=10, bootstyle="primary",
                  command=win.destroy).pack(pady=(0, 16))

    def _show_llama_missing_dialog(self, notice: str = ""):
        """未检测到 llama.cpp 时的安装弹窗(实现在 install_dialog.show)。

        notice 用于"已经有 llama.cpp 但想让本程序另装一份"的场景
        (比如当前目录归 WinGet 管): 此时标题变成「安装 / 重装」。
        """
        install_dialog.show(self, notice)

    # ── 安装链路辅助(与弹窗解耦, 更新通道复用同一套) ──

    def _default_install_dir(self) -> Path:
        """默认安装位置: 已配置过完整路径就沿用它的目录。

        重装/修复时若因为默认值跳到 %USERPROFILE%\\llama-cpp, 用户机器上就会
        出现两份 llama.cpp, 一份在跑、一份刚装, 版本还对不上。
        """
        p = Path(self.llama_cmd)
        if p.is_absolute() and p.parent.is_dir():
            return p.parent
        return Path.home() / "llama-cpp"

    def _locate_installed_server(self, dest: Path | None = None):
        """定位装好的 llama-server.exe; 找不到返回 None。

        给了 dest 就只在这个目录里找 —— 回落到 PATH 会把别处的旧副本写进配置,
        表现成"装完还在跑旧版本"。只有没给 dest 时才退到默认位置与 PATH。
        """
        cands: list[Path] = []
        if dest is not None:
            cands.append(dest / "llama-server.exe")
            try:
                cands.extend(sorted(dest.glob("*/llama-server.exe")))
            except OSError:
                pass
        else:
            cands.append(self._default_install_dir() / "llama-server.exe")
        for c in cands:
            try:
                if c.is_file():
                    return c
            except OSError:
                continue
        if dest is not None:
            return None
        found = shutil.which("llama-server")
        return Path(found) if found else None

    # ────────────── 主题 ──────────────

    def _theme_tooltip(self) -> str:
        return f"当前: {ut.display_name(ut.CURRENT)}\n点击选择其他主题"

    def _set_theme(self, name: str):
        """切换主题: 改 Style → ttk 控件自动重绘(无需登记/重放/重建)。"""
        if name == ut.CURRENT or not ut.is_valid(name):
            return
        t0 = perf_counter()
        ut.set_theme(name)
        self.theme_key = name
        ut.apply_titlebar_theme(self, ut.is_dark(name))
        tooltip_mod.reset_tip_window()      # 提示窗是建窗时取色, 丢弃重建

        # "变化才重绘"的两处缓存失效: 监控文字与主题面板的选中描边
        for attr in ("_last_gpu_text", "_last_ram_text"):
            setattr(self, attr, None)
        self._paint_theme_cells()

        self._save_theme_choice(name)
        self._log_theme_swap(name, t0)

    def _save_theme_choice(self, key: str):
        self.config["theme"] = key
        try:
            save_config(self.config)
        except OSError as e:
            self._set_status(f"主题已切换, 但配置保存失败: {e}")

    @staticmethod
    def _log_theme_swap(key: str, t0: float):
        """记录一次换肤耗时(调性能时先看这个文件, 别猜)"""
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(LOG_DIR / "ui_perf.log", "a", encoding="utf-8") as fp:
                fp.write(f"[{stamp}] theme→{key}  "
                         f"{(perf_counter() - t0) * 1000:.1f}ms\n")
        except Exception:
            pass

    # ── 主题选择面板: 15 组 × 浅/深, 每张卡片按该主题的真实配色绘制 ──

    CELL_W, CELL_H = 92, 42          # 单个预览色块的尺寸
    COLS = 3                         # 每行放几组

    def on_open_theme_panel(self):
        if self._dialog_active("_theme_dlg"):
            return

        win = tb.Toplevel(self)
        win.title("选择主题")
        win.geometry("720x600")
        win.minsize(640, 420)
        win.transient(self)

        head = tb.Frame(win)
        head.pack(fill="x", padx=16, pady=(14, 6))
        tb.Label(head, text="主题", font=(ut.FONT_FAMILY, 15, "bold")).pack(side="left")
        self._theme_now = tb.Label(head, text=ut.display_name(ut.CURRENT),
                                   style=ut.S_HINT)
        self._theme_now.pack(side="left", padx=10)

        sf = tb.ScrolledFrame(win, auto_hide=True, padding=8)
        sf.pack(fill="both", expand=True, padx=10, pady=(0, 12))
        for c in range(self.COLS):
            sf.grid_columnconfigure(c, weight=1, uniform="themecol")

        self._theme_cells = []
        for i, (fam, label, desc) in enumerate(ut.THEME_FAMILIES):
            card = tb.Frame(sf, padding=6)
            card.grid(row=i // self.COLS, column=i % self.COLS,
                      padx=4, pady=4, sticky="nsew")

            tb.Label(card, text=label, font=(ut.FONT_FAMILY, 11, "bold"),
                     anchor="w").pack(fill="x")
            tb.Label(card, text=desc, style=ut.S_HINT, anchor="w").pack(fill="x", pady=(0, 4))

            row = tb.Frame(card)
            row.pack(fill="x")
            for j, mode in enumerate(("light", "dark")):
                name = f"{fam}-{mode}"
                cv = tk.Canvas(row, width=self.CELL_W, height=self.CELL_H,
                               highlightthickness=2, bd=0, cursor="hand2")
                cv.grid(row=0, column=j, padx=(0 if j == 0 else 6, 0))
                cv.bind("<Button-1>", lambda e, n=name: self._pick_theme(n))
                self._theme_cells.append((cv, name))

        self._paint_theme_cells()
        self._register_dialog(win, "_theme_dlg")

    def _paint_theme_cells(self):
        """按各主题的真实配色重绘预览块(选中项加主色描边)。

        Canvas 是原生控件, 颜色不会跟随 ttk 样式, 因此换肤后必须整体重画一次。
        """
        cells = getattr(self, "_theme_cells", None)
        if not cells:
            return
        cur = ut.color("primary")
        idle = ut.color("border")
        for cv, name in cells:
            try:
                if not cv.winfo_exists():
                    continue
            except Exception:
                continue
            pal = ut.theme_palette(name)
            cv.delete("all")
            cv.configure(highlightbackground=cur if name == ut.CURRENT else idle)
            w, h = self.CELL_W, self.CELL_H
            # 底色 + 顶部主色条 + 一块"输入框" + 两行文字示意
            cv.create_rectangle(0, 0, w, h, fill=pal.bg, outline=pal.border)
            cv.create_rectangle(0, 0, w, 7, fill=pal.primary, outline="")
            cv.create_rectangle(7, 13, w - 7, 26, fill=pal.inputbg, outline=pal.border)
            cv.create_line(9, 33, w // 2, 33, fill=pal.fg, width=2)
            cv.create_line(9, 38, w - 20, 38, fill=pal.secondary, width=2)
        try:
            if getattr(self, "_theme_now", None) is not None:
                self._theme_now.configure(text=ut.display_name(ut.CURRENT))
        except Exception:
            pass

    def _pick_theme(self, name: str):
        self._set_theme(name)

    # ────────────── 状态与监控 ──────────────

    def _set_status(self, text: str):
        # 只在文本变化时才重绘状态栏, 避免定时器反复触发画布重绘
        full = f"状态: {text}"
        if full == getattr(self, "_last_status_text", None):
            return
        self._last_status_text = full
        self.status_label.configure(text=full)

    def _set_status_running(self, name: str = None):
        server = self._server_of(name)
        if server is None or not server.is_running():
            return
        host, port = server.get_host_port()
        mm, ss = divmod(server.uptime_seconds(), 60)
        hh, mm = divmod(mm, 60)
        self._set_status(
            f"运行中: {server.model_name} | {host}:{port} | 运行 {hh:02d}:{mm:02d}:{ss:02d}")

    def _check_process_state(self):
        """定时检查各实例进程是否已意外退出, 并刷新状态栏"""
        # 重启的「卸载中→加载中」两段状态由 on_restart_clicked 自己维护, 别被覆盖
        if not self._restarting:
            # 先报异常退出的实例(进程引用保留, 便于看日志/PID)
            for n, s in self.servers.items():
                if s.process is not None and not s.is_running() and s.return_code() is not None:
                    self._set_status(f"{n} 已停止 (code={s.return_code()})")
                    self._highlight_model_button()   # 进程意外退出, 取消运行中亮显
                    break
            else:
                server = self._server_of()
                if server is not None and server.is_running():
                    self._set_status_running()
                else:
                    running = self._running_servers()
                    if running:
                        parts = [f"{n}:{s.get_host_port()[1]}"
                                 for n, s in sorted(running.items())]
                        self._set_status("运行中: " + ", ".join(parts))
                    # 全部空闲则不动状态栏(保留最近一条消息)
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
        bar.configure(value=frac)

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
        if getattr(self, "_ip_job", None):
            try:
                self.after_cancel(self._ip_job)
            except Exception:
                pass
            self._ip_job = None
        if getattr(self, "_updating", False):
            if not messagebox.askyesno(
                    "退出确认",
                    "正在下载/替换 llama.cpp, 现在退出会中止本次更新。\n"
                    "（若已进入解压替换阶段, 安装目录会自动回滚到更新前）\n\n确定退出吗?"):
                return
            cancel = getattr(self, "_update_cancel", None)
            if cancel is not None:
                cancel.set()
        running = self._running_servers()
        if self._restarting:
            if not messagebox.askyesno(
                    "退出确认",
                    "模型正在重启(卸载→加载)中, 现在退出会中断重启并卸载模型。\n\n确定退出吗?"):
                return
        elif running:
            up = ", ".join(
                f"{n}(已运行 {divmod(s.uptime_seconds(), 60)[0]:02d}:"
                f"{divmod(s.uptime_seconds(), 60)[1]:02d})"
                for n, s in sorted(running.items()))
            if not messagebox.askyesno(
                    "退出确认",
                    f"仍有模型在运行: {up}\n\n"
                    "关闭 llama-shell 会卸载所有模型, 并清理本机全部 llama-server 进程"
                    "(含历史遗留的孤儿进程), 正在进行的请求会被中断。\n\n确定关闭吗?"):
                self._set_status_running()      # 用户取消: 状态栏恢复"运行中"
                return

        self._closing = True        # 之后后台线程的回调一律丢弃, 不再 update 界面
        survivors = []
        if self.servers:
            self._set_status("退出中: 正在卸载模型 ...")
            self.update_idletasks()
            for n, s in self.servers.items():
                if s.process is not None and not s.unload():
                    survivors.append(f"{n} (PID: {s.pid()})")

        # 兜底清扫: 按进程名枚举本机所有 llama-server, 连同历史遗留的孤儿一起结束。
        # 本程序是这些服务的主要入口, 残留进程会白占端口和显存; 只按可执行文件名匹配,
        # 不按端口/命令行, 避免误杀别的程序。
        self._set_status("退出中: 清理所有 llama-server 进程 ...")
        self.update_idletasks()
        killed, failed = kill_llama_servers(extra_names=(self.llama_cmd,))
        if failed:
            survivors.extend(f"PID {pid} ({name})" for pid, name in failed)

        if survivors:
            messagebox.showwarning(
                "未能结束模型进程",
                "以下 llama-server 进程仍然存活, 显存未释放:\n"
                + "\n".join(survivors)
                + "\n\n请到任务管理器手动结束。")

        self.monitor.stop()
        for s in self.servers.values():
            s.stop()      # 未运行时就只做收尾(关日志句柄)
        self.destroy()


def _crash_report(title: str, detail: str) -> None:
    """把未捕获异常落到 logs/crash.log 并弹框告知。

    pythonw 没有控制台：异常只会打到 stderr（= 无底洞），表现为"双击了没反应"。
    启动期和 Tk 回调里的异常都必须走这里，否则用户只能看到"VBS 无法启动"。
    """
    import traceback
    from datetime import datetime
    path = LOG_DIR / "crash.log"
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fp:
            fp.write(f"\n{'=' * 60}\n{datetime.now():%Y-%m-%d %H:%M:%S}  {title}\n")
            fp.write(detail.rstrip() + "\n")
    except Exception:
        pass
    try:
        root = tk._default_root
        if root is not None:
            root.withdraw()
        messagebox.showerror(
            "llama-shell 启动/运行出错",
            f"{title}\n\n{detail.strip().splitlines()[-1] if detail.strip() else ''}"
            f"\n\n完整堆栈已写入:\n{path}")
    except Exception:
        pass


if __name__ == "__main__":
    try:
        app = App()
        # Tk 回调里的异常默认只 print 到 stderr（pythonw 下等于丢弃）
        app.report_callback_exception = lambda exc, val, tb: _crash_report(
            "界面回调异常", "".join(traceback.format_exception(exc, val, tb)))
        app.protocol("WM_DELETE_WINDOW", app.on_closing)
        app.mainloop()
    except Exception:
        _crash_report("启动失败", traceback.format_exc())
