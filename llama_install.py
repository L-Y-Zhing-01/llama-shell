# -*- coding: utf-8 -*-
"""按本机硬件挑选 llama.cpp 的 Windows 预编译包。

为什么必须适配
--------------
上游每个 b 版本同时发 win-cpu / win-cuda-12.4 / win-cuda-13.5 / win-vulkan /
win-rocm / win-sycl, 名字只差中间一段。固定下 cpu-x64 的结果是:
  · 有 N 卡的机器全程 CPU 推理(7B Q4 从 ~40 tok/s 掉到个位数);
  · arm64 机器装上的是 x64 包, 直接跑不起来;
  · CUDA 包缺了配套的 cudart 运行库, 双击报「找不到 cublas64_*.dll」。

选型规则
--------
按显卡厂商给一条**优先级链**, 由 llama_release 在 release 资产里逐个试, 命中即用:
  NVIDIA → cuda-12.4 → cuda-13.5 → vulkan → cpu
  AMD    → vulkan → rocm-10.0 → cpu        (ROCm 需另装 HIP SDK, 只作备选)
  Intel  → vulkan → sycl → cpu             (SYCL 需 oneAPI 运行库, 只作备选)
  其他   → cpu
链式而不是硬指定: 上游偶尔漏发某个后端(实测有版本缺 vulkan), 缺一档要能自动退。
cuda 包额外拉 cudart-* 运行库, 否则装完启动即缺 DLL。
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
from pathlib import Path

from winenv import CREATE_NO_WINDOW as _NO_WINDOW

#: 资产名里的架构段: llama-b12219-bin-win-<tag>-<arch>.zip
ARCH = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"

#: (tag, 界面显示名, 适用说明) —— 顺序即下拉框顺序
VARIANTS = [
    ("cuda-12.4", "CUDA 12.4", "NVIDIA 显卡，驱动 ≥ 525，兼容面最广"),
    ("cuda-13.5", "CUDA 13.5", "NVIDIA 显卡，需较新驱动"),
    ("vulkan", "Vulkan", "AMD / Intel 显卡通用，无 CUDA 时的首选"),
    ("rocm-10.0", "ROCm 10.0", "AMD 显卡，需额外装 HIP SDK"),
    ("sycl", "SYCL", "Intel Arc 显卡，需 oneAPI 运行库"),
    ("cpu", "CPU", "纯 CPU 推理，无独显机器"),
]

_LABEL2TAG = {label: tag for tag, label, _ in VARIANTS}


def tag_label(tag: str) -> str:
    return next((label for t, label, _ in VARIANTS if t == tag), tag)


#: 虚拟显示器（向日葵/ToDesk/Parsec/远程桌面 Idd 驱动等）不是真显卡,
#: 混进列表只会把"本机: xxx"一行污染掉, 选型也拿不到正确厂商
_VIRTUAL_HINTS = ("idd", "virtual", "remote", "reflector", "basic display",
                  "parsec", "oray", "todesk", "sunlogin", "spacedesk")


def _is_virtual(name: str) -> bool:
    low = name.lower()
    return any(h in low for h in _VIRTUAL_HINTS)


#: 核显型号(名字里带这些的是集显, 走 Vulkan 只会跟 CPU 抢内存带宽)。
#: 定这套规则的原因: 只看厂商会误判 —— "AMD Radeon(TM) 780M"(锐龙笔记本核显)
#: 含 radeon 不含 graphics, "Intel(R) Arc(TM) Graphics"(Core Ultra 核显) 含 arc,
#: 两者都会被当成独显。轻薄本上这类名字极常见, 只有实测名单能拦住。
_IGPU_PATTERNS = (
    r"radeon.*\b\d{3}m\b",              # 780M / 760M / 680M / 890M
    r"vega",                            # Vega 8 / Vega 11
    r"radeon(\(tm\))?\s+graphics",      # 无型号后缀的 Radeon 核显
    r"iris", r"\buhd\b", r"hd graphics",  # Intel 核显/超极本
    r"gma\b",
)


def is_igpu(name: str) -> bool:
    """这个名字是不是核显。独显前缀(Arc A770 / B580 / Radeon RX)优先排除。"""
    low = name.lower()
    if "arc" in low and not re.search(r"\b[ab]\d{3}\b", low):
        # "Arc(TM) Graphics" / "Arc(TM) 140V" 是核显; "Arc(TM) A770" / "B580" 是独显
        return True
    return any(re.search(p, low) for p in _IGPU_PATTERNS)


def detect_gpus(timeout: int = 6) -> list[str]:
    """枚举本机显卡名(含核显, 已剔除虚拟显示器)。拿不到返回空列表。

    用 CIM 而不是 nvidia-smi: 后者只在装了 N 卡时存在, 而这里要判断的是
    "有没有 AMD / Intel 的卡", 必须拿到全量设备名。
    """
    ps = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
          "(Get-CimInstance Win32_VideoController).Name")
    names = []
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace",
                           creationflags=_NO_WINDOW)
        names = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    except (OSError, subprocess.SubprocessError):
        pass
    if not names:
        # 老系统/PS 被裁掉时退回 wmic
        try:
            r = subprocess.run(["wmic", "path", "win32_VideoController", "get", "name"],
                               capture_output=True, text=True, timeout=timeout,
                               encoding="utf-8", errors="replace",
                               creationflags=_NO_WINDOW)
            names = [ln.strip() for ln in (r.stdout or "").splitlines()[1:] if ln.strip()]
        except (OSError, subprocess.SubprocessError):
            return []
    return [n for n in names if not _is_virtual(n)]


def recommend(gpu_names: list[str]) -> list[str]:
    """按独显厂商给出优先级链(tag 列表)。认不出独显就只给 cpu, 宁慢勿错。

    核显先剔掉: Intel UHD / AMD APU / Core Ultra 的 Arc Graphics 跑 Vulkan 要跟
    CPU 抢同一份内存带宽, 实测 7B 模型比纯 CPU 还慢, 不能因为"有显卡"就选它。
    """
    discrete = [n for n in gpu_names if not is_igpu(n)]
    text = " ".join(discrete).lower()
    if not text:
        return ["cpu"]
    nvidia = any(k in text for k in ("nvidia", "geforce", "quadro", "tesla"))
    if nvidia:
        return ["cuda-12.4", "cuda-13.5", "vulkan", "cpu"]
    amd_discrete = ("radeon" in text and "graphics" not in text) or "rx " in text
    if amd_discrete:
        return ["vulkan", "rocm-10.0", "cpu"]
    intel_arc = "arc" in text
    if intel_arc:
        return ["vulkan", "sycl", "cpu"]
    # 认不出来的独显/无独显: 只用 CPU
    return ["cpu"]


def describe(gpu_names: list[str], chain: list[str]) -> str:
    """给界面的"检测到 …"一行文字。核显单独标出来, 免得用户以为没识别到。"""
    if gpu_names:
        head = " / ".join(gpu_names[:2])
        if len(gpu_names) > 2:
            head += f" 等 {len(gpu_names)} 个显示设备"
        if not any(not is_igpu(n) for n in gpu_names):
            head += "（核显，按 CPU 处理）"
    else:
        head = "未识别到独立显卡"
    picks = " → ".join(tag_label(t) for t in chain)
    return f"本机: {head} ({ARCH})   自动选择: {picks}"


#: 下载包 + 解压产物 + CUDA 运行库的峰值占用(实测约 4~5 GB, 留余量)
NEED_BYTES = 6 * 1024 ** 3


def precheck_dir(dest, action: str = "安装", need: int = NEED_BYTES) -> str | None:
    """安装/更新前检查目标目录, 返回错误文案; None 表示通过。

    换机器常踩的两类坑: ①装到 C:\\Program Files 无提权 → 解压到一半直接失败;
    ②目标盘空间不足 → 解压中途炸, 错误信息完全看不出是空间问题。
    只探"最近的已存在父目录"能否写, 不在用户点安装之前创建任何目录。

    更新也走这里: 覆盖 exe/dll 同样要写权限, 空间还要多算一份旧文件备份
    (备份是复制不是移动), 峰值并不比全新安装低。
    """
    p = Path(dest)
    probe_dir = p
    for _ in range(8):
        if probe_dir.exists():
            break
        if probe_dir.parent == probe_dir:
            return f"{action}位置无效: {p}"
        probe_dir = probe_dir.parent
    else:
        return f"{action}位置过深: {p}"
    if not probe_dir.is_dir():
        return f"{action}位置不是目录: {probe_dir}"
    try:
        probe = probe_dir / ".llama_write_test"
        probe.write_bytes(b"0")
        probe.unlink()
    except OSError as exc:
        return (f"没有写入权限: {probe_dir}\n{exc}\n"
                "请换到用户目录下的位置（默认 %USERPROFILE%\\llama-cpp），"
                "不要放在 C:\\Program Files。")
    try:
        free = shutil.disk_usage(probe_dir).free
    except OSError:
        return None
    if free < need:
        return (f"{action}所需空间不足: 剩 {free / 1024 ** 3:.1f} GB, 需要约 "
                f"{need / 1024 ** 3:.1f} GB\n"
                "（压缩包 + 解压后文件 + CUDA 运行库 + 旧版本备份, CPU 版约需 1.5 GB）")
    return None


def variant_choices() -> list[str]:
    """安装弹窗下拉框的选项: 自动 + 各后端。"""
    return ["自动（按本机硬件）"] + [label for _, label, _ in VARIANTS]

def tag_of_choice(choice: str) -> str | None:
    """下拉框选项 → tag; "自动" 返回 None。"""
    if choice.startswith("自动"):
        return None
    return _LABEL2TAG.get(choice)
