# -*- coding: utf-8 -*-
"""手动诊断脚本: 打印 winget 原始输出, 定位更新检测(版本号识别)失败的原因。

界面本身会把 winget 查询结果写到 logs/winget_debug.log, 升级过程写到
logs/winget_upgrade.log; 只有在需要看**逐字段**匹配过程时才需要跑这个脚本。
特意不加 --source 限定, 以便原样暴露 msstore 源的报错。
"""

import subprocess
import re

WINGET_ID = "ggml.llamacpp"
_VER_RE = re.compile(r'^[a-z]?\d+(\.\d+)*$')


def run(cmd, timeout=30):
    """运行命令并返回完整输出"""
    print(f"\n{'='*60}\n$ {' '.join(cmd)}\n{'='*60}")
    try:
        r = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
            creationflags=0x08000000,
        )
        print(f"[returncode] {r.returncode}")
        print(f"[stdout 长度] {len(r.stdout)} 字符")
        print(f"[stderr 长度] {len(r.stderr)} 字符")
        print(f"\n--- stdout ---\n{r.stdout}")
        print(f"\n--- stderr ---\n{r.stderr}")
        return r
    except Exception as e:
        print(f"[异常] {e}")
        return None


def parse_list(stdout):
    """解析 winget list 输出"""
    print(f"\n--- 解析 winget list ---")
    for line in stdout.splitlines():
        if "llamacpp" in line.lower():
            print(f"找到行: {line!r}")
            parts = line.split()
            print(f"分词: {parts}")
            for p in parts:
                m = _VER_RE.match(p)
                print(f"  {p!r:25} -> 正则匹配: {bool(m)}")
                if m:
                    print(f"  *** 当前版本: {p}")
                    return p
    print("未找到 llama.cpp 行")
    return "未知"


def parse_upgrade(stdout):
    """解析 winget upgrade 输出"""
    print(f"\n--- 解析 winget upgrade ---")
    for line in stdout.splitlines():
        if WINGET_ID in line.lower() or ("llama" in line.lower() and "cpp" in line.lower()):
            print(f"找到行: {line!r}")
            parts = line.split()
            print(f"分词: {parts}")
            versions = [p for p in parts if _VER_RE.match(p) and not p.lower().startswith("ggml")]
            print(f"过滤后的版本号: {versions}")
            if len(versions) >= 2:
                print(f"*** 当前: {versions[0]}, 可用: {versions[1]} → 有更新")
                return (True, versions[0], versions[1])
            elif len(versions) == 1:
                print(f"*** 只匹配到一个版本号 {versions[0]}，判定为已是最新")
                return (False, versions[0], versions[0])
            else:
                print(f"*** 没匹配到任何版本号")
                return (False, "未知", "未知")
    print("winget upgrade 输出中无 llama.cpp 行 → 已是最新")
    return (False, "未知", "未知")


if __name__ == "__main__":
    print("=" * 60)
    print("winget 输出调试 — 检查 llama.cpp 更新检测")
    print("=" * 60)

    # 1. winget list
    r1 = run(["winget", "list", "--id", WINGET_ID, "--accept-source-agreements"])
    if r1 and r1.returncode == 0:
        cur = parse_list(r1.stdout)
    else:
        cur = "未知"

    # 2. winget upgrade（不安装）
    r2 = run(["winget", "upgrade", "--accept-source-agreements"])
    if r2 and r2.returncode == 0:
        result = parse_upgrade(r2.stdout)
        print(f"\n=== 最终判断 ===")
        print(f"has_update: {result[0]}")
        print(f"current_ver: {result[1]}")
        print(f"available_ver: {result[2]}")
    else:
        print(f"\nwinget upgrade 命令失败 (returncode={r2.returncode if r2 else 'N/A'})")

    print("\n按回车退出...")
    input()
