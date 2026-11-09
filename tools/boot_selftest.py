# -*- coding: utf-8 -*-
"""启动前环境自检（boot_check）的自检。

跑法(在项目根目录):
    python tools/boot_selftest.py

只验逻辑, 不真装包、不联网。覆盖:
  1. 依赖探测: 本机齐全时必须返回空; 打桩缺失时必须报出来;
  2. 代理: 环境变量已有时不覆盖; 只有注册表设置时可读(本机可能为 0);
  3. pip 命令与环境: 指向 requirements.txt, 编码/版本检查变量就位;
  4. 拉起主程序: argv 用 pythonw + 绝对路径, cwd 是项目根;
  5. 日志落盘: 写到临时目录, 不污染 logs/;
  6. 窗口构建: 依赖齐全态 / 缺依赖态都能建起来(打桩 mainloop 立即返回)。
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import boot_check as bc  # noqa: E402


def main() -> int:
    fails = []

    # ── 1. 依赖探测 ──
    if bc.missing_modules():
        fails.append(f"本机依赖不全, 探测结果: {bc.missing_modules()}")
    real = bc.missing_modules
    bc.missing_modules = lambda: ["ttkbootstrap"]
    if bc.missing_modules() != ["ttkbootstrap"]:
        fails.append("打桩缺失未被识别")
    bc.missing_modules = real
    print(f"依赖探测: 本机 {real() or '齐全'}, 打桩缺失可识别")

    # ── 2. 代理 ──
    import os

    keep = {k: os.environ.get(k) for k in ("HTTP_PROXY", "HTTPS_PROXY")}
    os.environ["HTTPS_PROXY"] = "http://example:1"
    os.environ["HTTP_PROXY"] = "http://example:1"
    if bc.pip_env().get("HTTPS_PROXY") != "http://example:1":
        fails.append("已有代理被覆盖了(会把用户环境改坏)")
    for k in ("HTTP_PROXY", "HTTPS_PROXY"):
        os.environ.pop(k, None)
    env2 = bc.pip_env()
    reg = bc.system_proxy()
    if reg and env2.get("HTTPS_PROXY") != reg:
        fails.append(f"注册表代理没注入: {reg} != {env2.get('HTTPS_PROXY')}")
    for k, v in keep.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    print(f"代理: 环境变量优先保持, 注册表读值 {reg or '(本机未启用系统代理)'}")

    # ── 3. pip 命令/环境 ──
    cmd = bc.pip_cmd()
    if "install" not in cmd or "-r" not in cmd:
        fails.append(f"pip 命令不对: {cmd}")
    if str(bc.REQ_FILE) not in cmd:
        fails.append("pip 命令没指向 requirements.txt")
    if "-i" in cmd:
        fails.append("默认就带镜像源了(应只在失败后重试时加)")
    retry = bc.pip_cmd(["-i", bc.MIRROR])
    if bc.MIRROR not in retry:
        fails.append("换源重试参数没传进去")
    env3 = bc.pip_env()
    if env3.get("PYTHONIOENCODING") != "utf-8":
        fails.append("pip 子进程没统一 utf-8, 中文路径下会乱码")
    if env3.get("PIP_DISABLE_PIP_VERSION_CHECK") != "1":
        fails.append("没关 pip 版本检查(会多一次网络请求)")
    print("pip: 命令/换源/编码/版本检查 均已就位")

    # ── 4. 拉起主程序 ──
    calls = []
    real_popen = bc.subprocess.Popen
    bc.subprocess.Popen = lambda *a, **k: calls.append((a, k)) or object()
    try:
        bc.spawn_main()
    finally:
        bc.subprocess.Popen = real_popen
    if not calls:
        fails.append("spawn_main 没启动任何进程")
    else:
        args, kwargs = calls[0]
        argv = args[0]
        if len(argv) != 2 or not argv[1].endswith("main.py"):
            fails.append(f"spawn_main 参数不对: {argv}")
        if kwargs.get("cwd") != str(bc.ROOT):
            fails.append(f"spawn_main 的 cwd 不对: {kwargs.get('cwd')}")
        if "pythonw" not in Path(argv[0]).name.lower():
            fails.append(f"没用 pythonw 启动(会闪控制台): {argv[0]}")
        print(f"拉起主程序: {Path(argv[0]).name} {Path(argv[1]).name}  cwd={kwargs.get('cwd')}")

    # ── 5. 日志落盘(指向临时目录) ──
    tmp = Path(tempfile.mkdtemp(prefix="boot 自检_"))
    real_log = bc.LOG_DIR
    bc.LOG_DIR = tmp
    try:
        bc.write_boot_log("自检标题", "自检正文")
        log = tmp / "boot.log"
        body = log.read_text(encoding="utf-8") if log.is_file() else ""
        if "自检标题" not in body or "自检正文" not in body:
            fails.append(f"日志没写进去: {body!r}")
    finally:
        bc.LOG_DIR = real_log
    print("日志: 落盘正常且未污染项目 logs/")

    # ── 6. 窗口构建(两种状态) ──
    import tkinter as tk

    real_mainloop = tk.Tk.mainloop
    tk.Tk.mainloop = lambda self: (self.update(), self.destroy())
    real_missing = bc.missing_modules
    try:
        bc.missing_modules = lambda: []
        rc_ok = bc.run_gui()
        bc.missing_modules = lambda: ["ttkbootstrap"]
        rc_bad = bc.run_gui(reason="自检模拟缺依赖")
    finally:
        bc.missing_modules = real_missing
        tk.Tk.mainloop = real_mainloop
    if rc_ok != 0 or rc_bad != 0:
        fails.append(f"窗口构建返回值异常: {rc_ok} / {rc_bad}")
    print("窗口: 依赖齐全态 / 缺依赖态 均可构建")

    print()
    if fails:
        print("FAIL")
        for f in fails:
            print("  - " + f)
        return 1
    print("PASS  环境自检: 依赖探测/代理/pip/拉起主程序/日志/窗口 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
