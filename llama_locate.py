# -*- coding: utf-8 -*-
"""运行时定位已装的 llama-server, 并判断那个目录归谁管。

为什么不再只信 config.json
--------------------------
config 里存的是绝对路径, 同一台机器上没问题; 换机器时有三类场景会失效:
  1. 把程序目录连 config.json 一起拷过去 —— 盘符/路径不同, 记的路径不存在;
  2. 对面机器上的 llama.cpp 是手动解压的, 没进 PATH —— 裸命令 which 也找不到;
  3. 模型已经在跑 —— 只有进程表里那条 ExecutablePath 是当下的真相。
所以位置是"每次启动重新解析"的结果, config 只当第一候选(缓存), 命中不了继续往下找。

候选链(成本从低到高, 命中即停)
------------------------------
  0 配置记录(是 exe 就用; 是裸命令就 which 一次)
  1 程序目录旁: <脚本目录>/llama-cpp、<上一级>/llama-cpp（llama.cpp 写法同理）
  2 正在运行的 llama-server 进程的 ExecutablePath(最准, 代价 ~0.35s)
  3 PATH
  4 WinGet 的 Links 目录
  5 %USERPROFILE%\\llama-cpp
  6 各**本地固定盘**根下的 llama-cpp / llama.cpp
都不中返回 None, 由界面让用户指定一次。

不做全盘搜索: 一个 H: 盘扫一遍几十秒, 还撞权限目录与系统目录; 同名 exe 出现在
多个位置时也无法判断哪份"在用"。网络盘用 GetDriveTypeW 先筛掉, 避免断线盘符卡死。

候选是否可信
------------
目录里必须有 llama-server.exe, 且至少要有一份 ggml/llama 的 dll —— 光秃秃一个 exe
多半是"只解压了 exe"或残留物, 跑起来报缺 DLL, 不如不认。唯一例外是配置里显式写的
路径: 那是用户自己指的, 认它。

归属三档(决定"能不能直接覆盖它")
--------------------------------
  managed  有 .llama-install.json —— 本程序装的, 更新时静默接管
  wild     整套文件但没有清单  —— 手动解压/别处拷来, 更新前要用户确认一次
  foreign  在 WinGet 目录下     —— 别的包管理器在管, 不覆盖, 引导改用安装流程
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from llama_release import MANIFEST
from winenv import CREATE_NO_WINDOW as _NO_WINDOW

OWNER_MANAGED = "managed"
OWNER_WILD = "wild"
OWNER_FOREIGN = "foreign"

#: 目录算"整套安装"的最小特征: 除 exe 外还得有这些里任意一个
_LIB_HINTS = ("llama.dll", "ggml-base.dll", "ggml.dll")

#: 来源说明(界面/状态栏直接显示, 中文)
SRC_CONFIG = "配置记录"
SRC_BESIDE = "程序目录旁"
SRC_RUNNING = "运行中的进程"
SRC_PATH = "PATH"
SRC_WINGET = "WinGet"
SRC_HOME = "用户目录"
SRC_DRIVE = "磁盘根目录"


@dataclass(frozen=True)
class Found:
    """一次定位结果: 用哪个 exe、怎么找到的、那个目录归谁"""

    exe: Path
    source: str
    owner: str

    @property
    def dest(self) -> Path:
        return self.exe.parent

    @property
    def display(self) -> str:
        return f"{self.exe}（来源: {self.source}）"


def _is_complete(folder: Path) -> bool:
    """这个目录是不是一整套 llama.cpp(而不是孤零零一个 exe)"""
    try:
        if not (folder / "llama-server.exe").is_file():
            return False
        if (folder / MANIFEST).is_file():
            return True
        names = {p.name.lower() for p in folder.glob("*.dll")}
    except OSError:
        return False
    return any(h in names for h in _LIB_HINTS)


def owner_of(dest) -> str:
    """目录归属: 本程序装的 / 野生的 / 别的包管理器在管"""
    d = Path(dest)
    try:
        if (d / MANIFEST).is_file():
            return OWNER_MANAGED
    except OSError:
        pass
    low = str(d).replace("/", "\\").lower()
    if "\\microsoft\\winget\\" in low:
        return OWNER_FOREIGN
    return OWNER_WILD


def running_server_paths(timeout: float = 6) -> list[Path]:
    """正在运行的 llama-server 的完整路径(拿不到返回空表)。

    用 CIM 的 ExecutablePath: 这是唯一能区分"同一个 exe 名字、不同目录"的手段,
    也正好覆盖"用户已经跑着模型、准备顺手更新"的场景。
    """
    ps = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
          "(Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\")"
          ".ExecutablePath")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace",
                           creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return []
    return [Path(s.strip()) for s in (r.stdout or "").splitlines() if s.strip()]


def fixed_drives() -> list[Path]:
    """本地固定磁盘的根(C:\\ / D:\\)。

    刻意只取 DRIVE_FIXED: GetDriveTypeW 不会去访问盘上的文件, 断线的网络盘
    和没插的移动盘都会被直接排除, 不至于让启动卡在 UNC 超时上。
    """
    try:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        # 非 Windows / ctypes 不可用: 只给程序所在盘与用户目录所在盘
        anchor = Path.home().anchor or "C:\\"
        return [Path(anchor)]
    roots = []
    for i in range(26):
        if not mask & (1 << i):
            continue
        root = f"{chr(65 + i)}:\\"
        try:
            if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:   # DRIVE_FIXED
                roots.append(Path(root))
        except Exception:
            continue
    return roots


def candidates(configured: str, script_dir):
    """按代价从小到大产出 (exe, 来源)。惰性 —— 前面命中就不会跑后面的探测。"""
    raw = (configured or "").strip()
    if raw:
        p = Path(raw)
        if p.is_file():
            yield p, SRC_CONFIG
        else:
            # 裸命令(乃至"旧机器上的绝对路径"): 先看 PATH 里有没有同名命令
            hit = shutil.which(raw)
            if hit:
                yield Path(hit), SRC_PATH

    base = Path(script_dir)
    for d in (base / "llama-cpp", base.parent / "llama-cpp",
              base / "llama.cpp", base.parent / "llama.cpp"):
        yield d / "llama-server.exe", SRC_BESIDE

    for p in running_server_paths():
        yield p, SRC_RUNNING

    hit = shutil.which("llama-server")
    if hit:
        yield Path(hit), SRC_PATH

    local = os.environ.get("LOCALAPPDATA")
    if local:
        yield (Path(local) / "Microsoft" / "WinGet" / "Links"
               / "llama-server.exe"), SRC_WINGET

    home = Path.home()
    yield home / "llama-cpp" / "llama-server.exe", SRC_HOME
    yield home / "llama.cpp" / "llama-server.exe", SRC_HOME

    for drive in fixed_drives():
        yield drive / "llama-cpp" / "llama-server.exe", SRC_DRIVE
        yield drive / "llama.cpp" / "llama-server.exe", SRC_DRIVE


def resolve(configured: str, script_dir) -> Found | None:
    """按候选链找一份可用的 llama-server; 全都不中返回 None。"""
    seen: set[str] = set()
    for exe, source in candidates(configured, script_dir):
        try:
            key = str(exe).casefold()
            if key in seen:
                continue
            seen.add(key)
            # 配置里显式写的路径直接认(用户自己指的); 其余必须是整套安装
            ok = (exe.is_file() if source == SRC_CONFIG else _is_complete(exe.parent))
            if ok:
                return Found(exe, source, owner_of(exe.parent))
        except OSError:
            continue
    return None
