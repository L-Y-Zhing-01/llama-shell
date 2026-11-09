# -*- coding: utf-8 -*-
"""启动前环境自检 + 一键安装缺失依赖。

为什么是独立模块
----------------
main.py 顶层就 `import ttkbootstrap`, 缺它的时候 main.py 连模块体都进不去
(import 阶段直接抛 ImportError, 还没轮到 __main__ 的 try), 所以补救逻辑
不能写在 main.py 里。这个文件只依赖标准库(tkinter 除外, 用到时才 import),
由 启动.vbs 在拉起主程序之前调用。

三件事
------
1. 复查依赖(上一次可能已装好) → 有就直接拉起 main.py 并退出;
2. 缺依赖 → 用**原生 tkinter** 弹窗(此时 ttkbootstrap 还不可用), 说明原因;
3. 点「一键安装」→ 跑 `python -m pip install -r requirements.txt`, 输出流式
   进窗口, 成功后自动重启程序。

代理
----
pip 和 curl 一样只认 HTTPS_PROXY 环境变量、不读 Windows 系统代理(WinINET)。
所以装之前要把注册表里的系统代理注入子进程环境, 否则 Clash/Steam++ 那类机器
会卡在 "Collecting ttkbootstrap" 直到超时。

换源
----
默认走官方源; 失败(超时/连接错误)后自动用清华源重试一次 —— 一键安装卡在
网络上是最差的结果, 这一步是给它留的退路。
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback
from pathlib import Path

from winenv import CREATE_NO_WINDOW, system_proxy

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
REQ_FILE = ROOT / "requirements.txt"
MAIN_FILE = ROOT / "main.py"

#: 缺任何一个就触发安装
REQUIRED_MODULES = ("ttkbootstrap",)

#: 自动重试用的国内镜像(官方源失败后才用)
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


# ── 探测 ──────────────────────────────────────────────────────────

def missing_modules() -> list[str]:
    """当前解释器缺哪些第三方依赖。用 find_spec 而不是 import, 不执行包体。"""
    import importlib.util

    out = []
    for name in REQUIRED_MODULES:
        try:
            if importlib.util.find_spec(name) is None:
                out.append(name)
        except (ImportError, ValueError, AttributeError):
            out.append(name)
    return out


def pip_env() -> dict:
    """pip 子进程的环境: 编码统一 + 代理注入(pip 不读系统代理)。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONHOME", None)          # 多版本机器上会串环境
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    if not (env.get("HTTPS_PROXY") or env.get("HTTP_PROXY")):
        px = system_proxy()
        if px:
            env["HTTP_PROXY"] = px
            env["HTTPS_PROXY"] = px
    return env


def pip_cmd(extra: list[str] | None = None) -> list[str]:
    args = [sys.executable, "-m", "pip", "install", "-r", str(REQ_FILE),
            "--disable-pip-version-check", "--no-input"]
    if extra:
        args += extra
    return args


def _log_file() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / "boot.log"


def write_boot_log(title: str, detail: str) -> None:
    """故障落盘。pythonw 下 stderr 是无底洞, 没有这个文件就等于没记录。"""
    try:
        from datetime import datetime

        with open(_log_file(), "a", encoding="utf-8") as fp:
            fp.write(f"\n{'=' * 60}\n{datetime.now():%Y-%m-%d %H:%M:%S}  {title}\n")
            fp.write((detail or "").rstrip() + "\n")
    except OSError:
        pass


def pythonw_beside() -> str:
    """同目录的 pythonw.exe(无控制台启动主程序); 没有就用当前解释器。"""
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    return str(cand) if cand.is_file() else str(exe)


def spawn_main() -> None:
    """拉起主程序。用 pythonw 避免闪控制台。"""
    subprocess.Popen([pythonw_beside(), str(MAIN_FILE)], cwd=str(ROOT),
                     close_fds=True)


# ── 界面(原生 tkinter, 不能用 ttkbootstrap) ──────────────────────

def run_gui(reason: str = "") -> int:
    """缺依赖时的补救窗口: 说明 + 一键安装 + 流式日志。返回进程退出码。"""
    import tkinter as tk
    from tkinter import ttk

    missing = missing_modules()
    root = tk.Tk()
    root.title("llama-shell - 环境检查")
    root.geometry("780x540")
    root.minsize(700, 480)

    pad = {"padx": 12, "pady": (6, 4)}
    title = "启动前检查：缺少运行依赖" if missing else "启动前检查"
    ttk.Label(root, text=title, font=("Microsoft YaHei UI", 13, "bold")).pack(
        anchor="w", **pad)

    info = tk.Text(root, height=6, wrap="word", relief="groove", borderwidth=1)
    info.pack(fill="x", padx=12, pady=(0, 6))
    lines = []
    if missing:
        lines.append("缺少: " + "、".join(missing) + "（界面主题库，本程序必需）")
    else:
        lines.append("依赖已就绪。")
    lines.append("解释器: " + sys.executable)
    lines.append(f"Python: {sys.version.split()[0]}   requirements: {REQ_FILE}")
    if reason:
        lines.append("")
        lines.append(reason)
    lines.append("")
    lines.append("点「一键安装」会用上面的解释器执行 pip install -r requirements.txt，"
                 "装完自动重启程序。也可以关掉本窗口，自己在命令行装。")
    info.insert("1.0", "\n".join(lines))
    info.configure(state="disabled")

    bar = ttk.Progressbar(root, mode="determinate", maximum=100)
    bar.pack(fill="x", padx=12, pady=(2, 6))

    log_box = tk.Text(root, height=14, wrap="none", relief="groove", borderwidth=1,
                      background="#111418", foreground="#E6E6E6",
                      insertbackground="#E6E6E6", font=("Consolas", 9))
    log_box.pack(fill="both", expand=True, padx=12, pady=(0, 6))
    scroll = ttk.Scrollbar(log_box, orient="vertical", command=log_box.yview)
    log_box.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")

    row = ttk.Frame(root)
    row.pack(fill="x", padx=12, pady=(0, 12))

    state = {"busy": False, "proc": None, "job": None}

    def append(text: str) -> None:
        log_box.insert("end", text.rstrip("\n") + "\n")
        log_box.see("end")

    def set_busy(busy: bool) -> None:
        state["busy"] = busy
        btn_install.configure(state="disabled" if busy else "normal")
        btn_close.configure(text="停止" if busy else "退出")
        if busy:
            bar.configure(mode="indeterminate")
            bar.start(14)
        else:
            bar.stop()
            bar.configure(mode="determinate", value=0)

    # ── 安装执行 ──
    def run_pip(extra: list[str], tag: str) -> None:
        cmd = pip_cmd(extra)
        append(f"\n>>> [{tag}] " + " ".join(cmd[1:]))
        px = pip_env().get("HTTPS_PROXY")
        if px:
            append(f"    代理: {px}")
        q: "queue.Queue" = queue.Queue()

        def reader(proc):
            try:
                for raw in proc.stdout:
                    q.put(("line", raw))
            except Exception:
                pass
            finally:
                q.put(("done", proc.wait()))

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    env=pip_env(), cwd=str(ROOT),
                                    creationflags=CREATE_NO_WINDOW)
        except OSError as exc:
            append(f"无法启动 pip: {exc}")
            finish(-1)
            return
        state["proc"] = proc
        threading.Thread(target=reader, args=(proc,), daemon=True).start()
        pump(q, tag, extra)

    def pump(q, tag: str, extra: list[str]) -> None:
        try:
            kind, payload = q.get_nowait()
        except queue.Empty:
            state["job"] = root.after(60, lambda: pump(q, tag, extra))
            return
        if kind == "line":
            try:
                append(payload.decode("utf-8", "replace").rstrip())
            except Exception:
                pass
            state["job"] = root.after(10, lambda: pump(q, tag, extra))
            return
        state["proc"] = None
        finish(payload, tag, extra)

    def finish(code, tag: str = "", extra=None) -> None:
        if code != 0 and tag == "官方源" and not (extra or []):
            append("\n官方源失败，自动改用清华镜像重试一次…")
            run_pip(["-i", MIRROR], "清华源")
            return
        still = missing_modules()
        if not still:
            set_busy(False)
            append("\n安装完成。正在重启程序…")
            write_boot_log("依赖安装成功", f"pip 退出码 {code}")
            root.after(600, restart)
            return
        set_busy(False)
        append(f"\n安装未成功（pip 退出码 {code}），仍缺: {'、'.join(still)}")
        append("可尝试手动执行：")
        append(f'  "{sys.executable}" -m pip install -r "{REQ_FILE}"')
        write_boot_log("依赖安装失败", f"pip 退出码 {code}, 仍缺 {still}")

    def install() -> None:
        if state["busy"]:
            return
        set_busy(True)
        log_box.delete("1.0", "end")
        append("开始安装依赖…")
        run_pip([], "官方源")

    def restart() -> None:
        try:
            root.destroy()
        except Exception:
            pass
        spawn_main()

    def on_close() -> None:
        if state["busy"] and state["proc"] is not None:
            try:
                subprocess.run(["taskkill", "/PID", str(state["proc"].pid), "/T", "/F"],
                               capture_output=True, timeout=10, creationflags=CREATE_NO_WINDOW)
            except Exception:
                pass
        if state["job"]:
            try:
                root.after_cancel(state["job"])
            except Exception:
                pass
        root.destroy()

    btn_install = ttk.Button(row, text="一键安装", command=install)
    btn_install.pack(side="left")
    ttk.Button(row, text="打开项目文件夹",
               command=lambda: subprocess.Popen(["explorer", str(ROOT)])).pack(
        side="left", padx=(8, 0))
    btn_close = ttk.Button(row, text="退出", command=on_close)
    btn_close.pack(side="right")

    root.protocol("WM_DELETE_WINDOW", on_close)
    if not missing:
        append("依赖已就绪，无需安装。")
    root.mainloop()
    return 0


def fatal_missing(name: str, exc: BaseException) -> None:
    """给 main.py 顶层 import 失败时调用: 尽量弹窗, 退不回去就写日志。"""
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    write_boot_log(f"缺少依赖 {name}", detail)
    try:
        run_gui(reason=f"导入 {name} 失败: {exc}")
    except Exception:
        write_boot_log("无法显示环境检查窗口", traceback.format_exc())


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    force_ui = "--ui" in argv

    if not REQ_FILE.is_file():
        write_boot_log("缺少 requirements.txt", f"未找到 {REQ_FILE}")
        return 1

    missing = missing_modules()
    if not missing and not force_ui:
        spawn_main()          # 依赖齐 → 直接交给主程序, 本进程退场
        return 0

    try:
        import tkinter  # noqa: F401
    except ImportError:
        # 连 tkinter 都没有: 无窗可弹, 只能写日志(启动.vbs 那边会先拦一次)
        write_boot_log("缺少 tkinter", "标准库 tkinter 不可用，需重装 Python 并勾选 tcl/tk")
        return 2

    return run_gui()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        write_boot_log("boot_check 崩溃", traceback.format_exc())
        raise SystemExit(1)
