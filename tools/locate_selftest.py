# -*- coding: utf-8 -*-
"""换机定位 + 后端一致性自检(纯逻辑, 不起 GUI)。

覆盖三块:
1. llama_locate.resolve 的候选链: 配置有效 / 配置失效但程序目录旁有 / 残缺目录放过;
2. owner_of 的三档归属: managed(有清单) / wild(野生) / foreign(WinGet);
3. llama_release 的后端校验: infer_variant 对 cuda 12.4/13.5 的区分、
   verify_variant 能不能拦住"下错档的包"。

测试期间会屏蔽三条碰真实系统的探测(固定盘扫描、进程枚举、PATH), 否则本机
H:\\llama-cpp 会让"应该找不到"的用例假成功。

跑法(系统 Python):
    C:\\Users\\Admin\\AppData\\Local\\Programs\\Python\\Python312\\python.exe tools/locate_selftest.py
"""

import contextlib
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import llama_install          # noqa: E402
import llama_locate           # noqa: E402
import llama_release          # noqa: E402
from llama_locate import (    # noqa: E402
    OWNER_FOREIGN, OWNER_MANAGED, OWNER_WILD,
)

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f"  -> {detail}" if detail and not cond else ""))


def make_install(folder: Path, dlls=("ggml-base.dll", "llama.dll"),
                 manifest: bool = False) -> Path:
    """造一个"看起来装好了"的目录"""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "llama-server.exe").write_bytes(b"MZ")
    for d in dlls:
        (folder / d).write_bytes(b"x")
    if manifest:
        (folder / llama_release.MANIFEST).write_text('{"tag": "b1"}', encoding="utf-8")
    return folder


def isolated(tmp: Path):
    """屏蔽会命中真实安装的探测: 固定盘 / 进程表 / PATH / 用户目录"""
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(llama_locate, "fixed_drives", lambda: []))
    stack.enter_context(mock.patch.object(llama_locate, "running_server_paths",
                                          lambda *a, **k: []))
    stack.enter_context(mock.patch("pathlib.Path.home",
                                   staticmethod(lambda: tmp / "nohome")))
    stack.enter_context(mock.patch.object(llama_locate.shutil, "which",
                                          lambda *a, **k: None))
    return stack


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="llama-locate-"))
    app_dir = tmp / "llama-shell"
    app_dir.mkdir()

    with isolated(tmp):
        # ── 1. 候选链 ──
        good = make_install(tmp / "installed" / "llama-cpp")
        found = llama_locate.resolve(str(good / "llama-server.exe"), app_dir)
        check("配置路径有效 → 直接采用",
              found is not None and found.source == llama_locate.SRC_CONFIG, str(found))

        # 配置记的是别的机器上的路径(失效) → 程序目录旁那份要被找到
        beside = make_install(app_dir / "llama-cpp")
        found = llama_locate.resolve(r"H:\no-such-dir\llama-server.exe", app_dir)
        check("配置失效 → 程序目录旁命中",
              found is not None and found.exe == beside / "llama-server.exe"
              and found.source == llama_locate.SRC_BESIDE, str(found))

        # 上一级目录下的 llama.cpp(程序在 repo 里, llama-cpp 与 repo 同级)
        upper_root = tmp / "upper" / "llama-shell"
        upper_root.mkdir(parents=True)
        upper = make_install(tmp / "upper" / "llama.cpp")
        found = llama_locate.resolve(r"D:\gone\llama-server.exe", upper_root)
        check("配置失效 → 上一级 llama.cpp 命中",
              found is not None and found.exe.parent == upper, str(found))

        # 裸命令、PATH 里没有 → 仍应靠程序目录旁命中
        found = llama_locate.resolve("llama-server", app_dir)
        check("裸命令(不在 PATH) → 仍能定位", found is not None, str(found))

        # 残缺目录(只有 exe 没 dll) 不能算命中
        solo = tmp / "solo" / "llama-shell"
        solo.mkdir(parents=True)
        (solo.parent / "llama-cpp").mkdir()
        (solo.parent / "llama-cpp" / "llama-server.exe").write_bytes(b"MZ")
        found = llama_locate.resolve("", solo)
        check("残缺目录(缺 dll)被跳过 → None", found is None, str(found))

        # 配置显式指向一个孤零零的 exe: 用户自己指的, 认它
        only_exe = solo.parent / "llama-cpp" / "llama-server.exe"
        found = llama_locate.resolve(str(only_exe), solo)
        check("配置显式指定的残缺路径仍被认",
              found is not None and found.source == llama_locate.SRC_CONFIG, str(found))

        # ── 2. 归属三档 ──
        check("有安装清单 → managed",
              llama_locate.owner_of(make_install(tmp / "m", manifest=True)) == OWNER_MANAGED)
        check("无清单但有整套文件 → wild",
              llama_locate.owner_of(make_install(tmp / "w")) == OWNER_WILD)
        winget_dir = tmp / "AppData" / "Microsoft" / "WinGet" / "Links"
        make_install(winget_dir)
        check("WinGet 目录 → foreign", llama_locate.owner_of(winget_dir) == OWNER_FOREIGN)

    # ── 3. 后端一致性(不依赖文件系统探测) ──
    cuda12 = make_install(tmp / "v_cuda", dlls=("ggml-cuda.dll", "cublas64_12.dll"))
    check("含 cublas64_12 → cuda-12.4",
          llama_release.infer_variant(cuda12) == "cuda-12.4",
          llama_release.infer_variant(cuda12))
    cuda13 = make_install(tmp / "v_cuda13", dlls=("ggml-cuda.dll", "cublas64_13.dll"))
    check("cublas64_13 优先于 ggml-cuda → cuda-13.5",
          llama_release.infer_variant(cuda13) == "cuda-13.5",
          llama_release.infer_variant(cuda13))

    pkg_cuda = make_install(tmp / "pkg_cuda", dlls=("ggml-cuda.dll",))
    try:
        llama_release.verify_variant(pkg_cuda, "cuda-12.4")
        check("期望 cuda、包里是 cuda → 通过", True)
    except llama_release.ReleaseError as exc:
        check("期望 cuda、包里是 cuda → 通过", False, str(exc))

    pkg_cpu = make_install(tmp / "pkg_cpu", dlls=("ggml-cpu.dll",))
    try:
        llama_release.verify_variant(pkg_cpu, "cuda-12.4")
        check("期望 cuda、包里是 cpu → 必须拦住", False, "没有抛错")
    except llama_release.ReleaseError as exc:
        check("期望 cuda、包里是 cpu → 必须拦住", "不符" in str(exc), str(exc))

    try:
        llama_release.verify_variant(pkg_cuda, "cpu")
        check("期望 cpu、包里是 cuda → 必须拦住", False, "没有抛错")
    except llama_release.ReleaseError:
        check("期望 cpu、包里是 cuda → 必须拦住", True)

    check("family 归类",
          (llama_release.family_of("cuda-13.5"), llama_release.family_of("rocm-10.0"),
           llama_release.family_of("nonsense")) == ("cuda", "rocm", "cpu"))

    # ── 4. 目录预检的措辞(更新与安装要能区分) ──
    msg = llama_install.precheck_dir(tmp / "nope", action="更新", need=1 << 62)
    check("空间不足时按 action 措辞", msg is not None and "更新" in msg, str(msg))

    # ── 5. 真实环境: 本机配置应当能定位到 llama-server ──
    import app_config
    cfg = app_config.load_config()
    real = llama_locate.resolve(cfg.get("llama_server_cmd", "llama-server"),
                                app_config.SCRIPT_DIR)
    check("本机 config 能定位到 llama-server", real is not None, str(real))
    if real is not None:
        print(f"       本机: {real.display} / 归属 {real.owner}")

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("失败项: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
