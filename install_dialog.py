# -*- coding: utf-8 -*-
"""安装弹窗: 未检测到 llama.cpp 时的一键安装界面。

为什么独立成模块
----------------
main.py 的 App 一个类同时背着主体界面 / 主题面板 / 更新流程, 这个 ~200 行的
弹窗闭包是其中最大的一块。拆出后 main.py 只留一行转发; 更新通道也要用的
安装链路辅助(_default_install_dir / _locate_installed_server)留在 App 上,
弹窗专属的(install_chain / append_log / after_install_success)搬到这里。

弹窗内部保持原来的"闭包式对话框"写法: 控件 + 几个互相引用的闭包函数
(start / refresh_hint / finish / worker), 它们共享一撮局部状态, 改成类
反而要传来传去, 维持闭包最直白。

对外入口:
    show(app, notice="")     建窗并进入交互; notice 用于"另装一份"场景
    install_chain(app, tag)  候选后端链(显卡探测结果缓存在 app 上)
    append_log(log_box, txt) 日志框追加(只留最近 400 行)
    after_install_success()  装好后写配置 → 刷版本号 → 问是否重启
"""

from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox

import ttkbootstrap as tb

import llama_install
import llama_release
import ui_theme as ut
from app_config import save_config


def install_chain(app, tag: str = None) -> list:
    """预编译包的候选后端链: 手动指定就只用那一档, 否则按显卡厂商排。

    显卡探测要起一次 PowerShell(~0.4s), 结果缓存 —— 弹窗重开/改安装位置
    都要重新显示这一行, 不能每次都探一遍。
    """
    if tag:
        return [tag]
    if getattr(app, "_hw_chain", None) is None:
        app._hw_gpus = llama_install.detect_gpus()
        app._hw_chain = llama_install.recommend(app._hw_gpus)
    return app._hw_chain


def append_log(log_box, text: str) -> None:
    log_box.configure(state="normal")
    log_box.insert("end", text.rstrip("\n") + "\n")
    try:      # 只留最近 400 行: 长时间安装的滚动输出会把框撑到卡
        total = int(log_box.index("end-1c").split(".")[0])
        if total > 400:
            log_box.delete("1.0", f"{total - 400}.0")
    except Exception:
        pass
    log_box.see("end")
    log_box.configure(state="disabled")


def after_install_success(app, parent, exe: Path) -> None:
    """安装成功: 写回配置 → 刷新版本号 → 询问是否立即重启。

    必须自动写回完整路径: 安装不会改当前进程的 PATH, 重启也救不了 which()
    探测; 只提示用户手动配 PATH 的话, 重启后依旧「未检测到」(已实测踩过)。
    """
    app.llama_cmd = str(exe)
    app.updater.llama_cmd = str(exe)
    app.config["llama_server_cmd"] = str(exe)
    try:
        save_config(app.config)
    except OSError as exc:
        app._set_status(f"已装好, 但配置保存失败: {exc}")

    app.llama_available = app._check_llama_available()
    app._refresh_version_title()
    app._set_status(f"llama.cpp 已安装: {exe}")

    if messagebox.askyesno(
            "安装完成",
            f"llama.cpp 已安装, 并已写入 config.json:\n{exe}\n\n"
            "参数面板的版本号与参数表要重启后才对得齐。\n\n是否立即重启程序?",
            parent=parent):
        parent.destroy()
        app._restart_app()


def show(app, notice: str = "") -> None:
    """未检测到 llama.cpp: 一个按钮走完「查版本 → 下载 → 校验 → 解压 → 写配置」。

    不再有 winget 通道, 也不再生成 .ps1: 全部在进程内用标准库完成 ——
    后端选择、代理、断点续传、sha256 校验、失败回滚都在同一处,
    出错时给的也是原始异常而不是一行看不懂的退出码。

    notice 用于"已经有 llama.cpp 但想让本程序另装一份"的场景(比如当前目录归
    WinGet 管): 此时标题变成「安装 / 重装」, 副标题换成调用方给的说明。
    """
    if app._dialog_active("_llama_dlg"):
        return

    has_any = app.llama_available
    win = tb.Toplevel(app)
    win.title("安装 / 重装 llama.cpp" if has_any else "未检测到 llama.cpp")
    win.geometry("800x700")
    win.minsize(740, 600)
    win.transient(app)

    ctx = {"busy": False, "cancel": None, "closed": False}

    def close():
        ctx["closed"] = True
        if ctx["cancel"] is not None:
            ctx["cancel"].set()     # 下载线程每块都查这个事件
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", close)

    tb.Label(win, text="安装 / 重装 llama.cpp" if has_any else "未检测到可用的 llama.cpp",
             font=(ut.FONT_FAMILY, 15, "bold"),
             bootstyle="primary" if has_any else "danger").pack(
                 pady=(18, 6), padx=22, anchor="w")
    tb.Label(
        win,
        text=notice or (
            f"系统 PATH 中找不到命令 “{app.llama_cmd}”，程序无法加载模型。\n"
            "点一下就跑完: 查上游最新构建 → 按本机显卡选后端 → 下载 → 校验 → 解压。"),
        font=ut.FONT, justify="left", anchor="w", wraplength=740,
    ).pack(padx=22, anchor="w")

    # ── 安装参数 ──
    box = tb.Labelframe(win, text=" 安装（GitHub 官方 release，按本机显卡自动选后端） ",
                        padding=6)
    box.pack(fill="x", padx=22, pady=(14, 0))
    box.grid_columnconfigure(1, weight=1)

    # 后端适配: 默认按显卡自动选, 也可手动指定(CUDA 驱动太老 / 多卡机器想强制 Vulkan)
    tb.Label(box, text="后端:", anchor="e").grid(
        row=1, column=0, padx=(4, 2), pady=4, sticky="e")
    variant_var = tk.StringVar(value=llama_install.variant_choices()[0])
    variant_combo = tb.Combobox(box, textvariable=variant_var, state="readonly",
                                values=llama_install.variant_choices(), font=ut.FONT)
    variant_combo.grid(row=1, column=1, columnspan=2, padx=(0, 4), pady=4, sticky="ew")
    hw_hint = tb.Label(box, text="", font=ut.FONT, style=ut.S_HINT, anchor="w")
    hw_hint.grid(row=2, column=0, columnspan=3, padx=4, pady=(0, 4), sticky="ew")

    tb.Label(box, text="安装位置:", anchor="e").grid(
        row=3, column=0, padx=(4, 2), pady=4, sticky="e")
    dest_entry = tb.Entry(box, font=ut.MONO_FONT)
    dest_entry.grid(row=3, column=1, padx=(0, 6), pady=4, sticky="ew")
    dest_entry.insert(0, str(app._default_install_dir()))
    btn_browse = tb.Button(box, text="浏览…", width=7, bootstyle="secondary-outline")
    btn_browse.grid(row=3, column=2, padx=(0, 4), pady=4)

    row4 = tb.Frame(box)
    row4.grid(row=4, column=0, columnspan=3, padx=4, pady=(8, 4), sticky="ew")
    btn_install = tb.Button(row4, text="下载并安装", width=12, bootstyle="primary")
    btn_install.pack(side="left")
    tb.Label(row4, font=ut.FONT, style=ut.S_HINT,
             text="中断后重跑会断点续传；覆盖前先备份，失败自动回滚").pack(side="left", padx=10)

    # 底行先 pack(side="bottom"): pack 先到的先分配, 这样进度区 expand 时
    # 「打开项目主页/关闭」也不会被顶出窗口(实测 740 高时会被挤掉)。
    btns = tb.Frame(win)
    btns.pack(side="bottom", fill="x", padx=22, pady=(12, 16))
    tb.Button(btns, text="打开项目主页", bootstyle="secondary-outline",
              command=lambda: webbrowser.open(llama_release.REPO_URL)).pack(side="left")
    tb.Button(btns, text="关闭", width=7, bootstyle="secondary-outline",
              command=close).pack(side="right")

    # ── 进度与日志 ──
    box3 = tb.Labelframe(win, text=" 安装进度 ", padding=6)
    box3.pack(fill="both", expand=True, padx=22, pady=(10, 0))
    box3.grid_columnconfigure(0, weight=1)
    box3.grid_rowconfigure(1, weight=1)
    bar = tb.Progressbar(box3, maximum=1, value=0, bootstyle="success-striped")
    bar.grid(row=0, column=0, padx=4, pady=(4, 6), sticky="ew")
    log_box = tb.ScrolledText(box3, height=9, font=ut.MONO_FONT, wrap="none")
    log_box.grid(row=1, column=0, padx=4, pady=(0, 6), sticky="nsew")
    log_box.configure(state="disabled")
    status = tb.Label(box3, text="等待开始", anchor="w", font=ut.FONT,
                      style=ut.S_HINT)
    status.grid(row=2, column=0, padx=4, pady=(0, 4), sticky="w")

    # ── 交互 ──

    def current_dest() -> Path:
        return Path(dest_entry.get().strip() or app._default_install_dir())

    def refresh_hint(*_e):
        tag = llama_install.tag_of_choice(variant_var.get())
        chain = install_chain(app, tag)
        hint = llama_install.describe(getattr(app, "_hw_gpus", []), chain)
        hw_hint.configure(text=hint + ("（手动指定）" if tag else ""))

    def browse():
        cur = dest_entry.get().strip()
        base = Path(cur) if cur else app._default_install_dir()
        init = str(base if base.parent.is_dir() else Path.home())
        chosen = filedialog.askdirectory(parent=win, title="选择安装位置",
                                         initialdir=init, mustexist=False)
        if chosen:
            dest_entry.delete(0, "end")
            dest_entry.insert(0, str(Path(chosen)))

    variant_combo.bind("<<ComboboxSelected>>", refresh_hint)
    btn_browse.configure(command=browse)
    refresh_hint()

    def set_busy(busy: bool):
        ctx["busy"] = busy
        state = "disabled" if busy else "normal"
        btn_install.configure(state=state)
        btn_browse.configure(state=state)
        variant_combo.configure(state="disabled" if busy else "readonly")

    def reset_output():
        log_box.configure(state="normal")
        log_box.delete("1.0", "end")
        log_box.configure(state="disabled")
        bar.configure(value=0)

    def start():
        if ctx["busy"]:
            return
        dest = current_dest()
        # 先验目录可写与剩余空间: 否则解压到一半才炸, 报错也看不出是权限还是空间
        problem = llama_install.precheck_dir(dest)
        if problem:
            messagebox.showerror("安装位置不可用", problem, parent=win)
            return
        tag = llama_install.tag_of_choice(variant_var.get())
        chain = install_chain(app, tag)

        reset_output()
        set_busy(True)
        cancel = threading.Event()
        ctx["cancel"] = cancel
        status.configure(text="正在查询最新构建…")

        def post(fn):
            """回 UI 线程; 弹窗已被关掉时静默丢弃(否则线程里抛 TclError)"""
            try:
                win.after(0, fn)
            except tk.TclError:
                pass

        def log(msg: str):
            post(lambda m=msg: append_log(log_box, m))

        def progress(done: int, total: int):
            def paint():
                bar.configure(value=(done / total) if total else 0)
                status.configure(text=(
                    f"下载中 {done / 1024 ** 2:.0f} / {total / 1024 ** 2:.0f} MB"
                    if total else f"已下载 {done / 1024 ** 2:.0f} MB"))
            post(paint)

        def finish(manifest, error: str):
            if ctx["closed"]:
                return
            ctx["cancel"] = None
            set_busy(False)
            if error:
                status.configure(text="安装失败")
                append_log(log_box, f"[失败] {error}")
                messagebox.showerror("安装失败", error, parent=win)
                return
            bar.configure(value=1)
            status.configure(text=f"安装完成: {manifest['tag']} ({manifest['variant']})")
            append_log(log_box,
                       f"安装完成: {manifest['tag']} / {manifest['variant']}")
            exe = app._locate_installed_server(dest)
            if exe is None:
                messagebox.showwarning(
                    "安装完成",
                    "文件已解压, 但没找到 llama-server.exe。\n\n"
                    f"请确认安装位置: {dest}", parent=win)
                return
            after_install_success(app, win, exe)

        def worker():
            try:
                manifest = llama_release.install_latest(
                    dest, chain=chain, on_log=log,
                    on_progress=progress, cancel=cancel)
                post(lambda m=manifest: finish(m, ""))
            except llama_release.Cancelled:
                post(lambda: finish(None, "已取消"))
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                post(lambda m=msg: finish(None, m))

        threading.Thread(target=worker, daemon=True).start()

    btn_install.configure(command=start)
    app._register_dialog(win, "_llama_dlg")
