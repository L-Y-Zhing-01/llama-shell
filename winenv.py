# -*- coding: utf-8 -*-
"""Windows 进程/网络环境小工具 —— 全项目共享的常量与纯标准库函数。

独立成模块的原因
----------------
1. `CREATE_NO_WINDOW` 原先在 6 个文件里各有一份副本(还有一份裸字面量),
   谁也不敢先改, 收敛到这里作为唯一来源;
2. `system_proxy()` 原先在 boot_check 与 llama_release 双实现(~35 行 95% 重叠),
   同样收敛到这里。boot_check 必须只依赖标准库, 所以本模块也**只依赖标准库**。
"""

from __future__ import annotations

import os
import subprocess

#: subprocess 的"不弹控制台窗口"标志。
#: 用 getattr 兜底而不是裸写 0x08000000: 万一某天在非 Windows 平台跑测试,
#: 缺这个常量也不至于直接 NameError(值为 0 时 creationflags 无效果)。
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def system_proxy() -> str:
    """读系统代理, 返回 'http://host:port' 之类的完整写法; 没有返回空串。

    优先级: 环境变量(HTTPS_PROXY / HTTP_PROXY) → WinINET 注册表。
    curl.exe 与 urllib 都不认 WinINET 的 ProxyServer 多协议写法, 必须自己拆:
    `http=127.0.0.1:7890;https=127.0.0.1:7890;socks=...` → 优先 https。
    """
    env = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if env:
        return env
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            enable = winreg.QueryValueEx(key, "ProxyEnable")[0]
            server = str(winreg.QueryValueEx(key, "ProxyServer")[0] or "")
    except (OSError, ImportError, FileNotFoundError):
        return ""
    if not enable or not server:
        return ""
    if "=" not in server:
        return server if "://" in server else f"http://{server}"
    parts = {}
    for item in server.split(";"):
        k, _, v = item.partition("=")
        if v:
            parts[k.strip().lower()] = v.strip()
    picked = parts.get("https") or parts.get("http") or parts.get("socks")
    if not picked:
        return ""
    if picked.startswith("socks") and "://" not in picked:
        return f"socks5://{picked}"
    return picked if "://" in picked else f"http://{picked}"
