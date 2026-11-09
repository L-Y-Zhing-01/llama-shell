# -*- coding: utf-8 -*-
"""安装/更新链路的自检(除显卡探测外全离线)。

跑法(在项目根目录):
    python tools/install_selftest.py

检查项:
  1. 安装弹窗能建起来, 且不再有 winget / 脚本预览区;
  2. 后端选型矩阵(含核显反例)与原判定一致;
  3. build 号按数字比较(b9999 < b12224), 版本串能解析;
  4. release 资产挑选: 链式退档 / cuda 带 cudart / 无适配资产报错;
  5. 系统代理读取不抛异常;
  6. 替换: 覆盖旧文件、清掉旧后端 dll、保留用户文件、写安装清单;
  7. 回滚: 复制中途失败时安装目录恢复原状;
  8. pending 往返: 存 → 读 → 应用;
  9. _locate_installed_server 只在给定目录里找(不回落 PATH 里的旧副本)。
"""

import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import test_stubs            # noqa: E402  (打桩复用)
import main as app_main      # noqa: E402
import llama_install as li   # noqa: E402
import llama_release as lr   # noqa: E402


def _all_texts(widget) -> list:
    """递归收集控件上的 text, 用来断言弹窗里有什么/没什么"""
    out = []
    for child in widget.winfo_children():
        try:
            text = child.cget("text")
            if text:
                out.append(str(text))
        except Exception:
            pass
        out.extend(_all_texts(child))
    return out


def _make_package(folder: Path, files: dict) -> Path:
    """把 {相对路径: 内容} 打成 zip, 模拟上游预编译包"""
    folder.mkdir(parents=True, exist_ok=True)
    zip_path = folder.parent / (folder.name + ".zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        for rel, content in files.items():
            p = folder / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
            zf.write(p, rel)
    return zip_path


def main() -> int:
    fails = []
    test_stubs.stub_config_io(block_dialogs=False)

    # ── 1. 弹窗 ──
    app = app_main.App()
    app.withdraw()
    app.update_idletasks()
    app._show_llama_missing_dialog()
    app.update()
    win = getattr(app, "_llama_dlg", None)
    if win is None or not win.winfo_exists():
        print("FAIL 弹窗没建起来")
        return 1
    texts = _all_texts(win)
    if not any("下载并安装" in t for t in texts):
        fails.append("弹窗里找不到「下载并安装」按钮")
    if any("winget" in t.lower() for t in texts):
        fails.append("弹窗里仍有 winget 字样(应已彻底移除)")
    for gone in ("_stream_install", "_parse_progress", "_build_install_script",
                 "_powershell_install_argv", "_kill_tree"):
        if hasattr(app, gone):
            fails.append(f"废弃方法 {gone} 仍然存在")
    print(f"弹窗已建, 文本控件 {len(texts)} 个, 无 winget 通道")

    # ── 2. 后端选型矩阵 ──
    matrix = [
        (["NVIDIA GeForce RTX 3060 Ti"], "cuda-12.4"),
        (["AMD Radeon RX 7900 XTX"], "vulkan"),
        (["AMD Radeon Pro W6800"], "vulkan"),
        (["Intel(R) Arc(TM) A770 Graphics"], "vulkan"),
        (["Intel(R) Arc(TM) B580"], "vulkan"),
        (["Intel(R) UHD Graphics 630"], "cpu"),
        (["AMD Radeon(TM) Graphics"], "cpu"),
        (["AMD Radeon(TM) 780M"], "cpu"),
        (["AMD Radeon 760M"], "cpu"),
        (["Intel(R) Arc(TM) Graphics"], "cpu"),
        (["Intel(R) Arc(TM) 140V"], "cpu"),
        ([], "cpu"),
    ]
    for gpus, want in matrix:
        got = li.recommend(gpus)[0]
        if got != want:
            fails.append(f"选型错误 {gpus or '(无显卡)'}: 首选 {got} != {want}")
    print(f"硬件选型: {len(matrix)} 组已核对")

    # ── 3. build 号 ──
    if lr.build_of("b9999") >= lr.build_of("b12224"):
        fails.append("build 号比较仍按字符串(b9999 不应大于 b12224)")
    if lr.parse_build("version: 0.9.0-dev (build 12224, commit abc)") != 12224:
        fails.append("parse_build 解析不出 (build 12224)")
    if lr.parse_build("b12182") != 12182:
        fails.append("parse_build 认不出裸 tag 写法")
    if lr.parse_build("乱七八糟") != 0:
        fails.append("parse_build 对无法识别的内容应返回 0")
    print("build 号: 数字比较与三种输入形态已核对")

    # ── 4. 资产挑选 ──
    def fake_release(names, tag="b99999"):
        return {"tag": tag, "build": lr.build_of(tag),
                "assets": [{"name": n, "size": 10, "digest": "",
                            "browser_download_url": "http://example/" + n} for n in names]}

    rel = fake_release(["llama-b99999-bin-win-vulkan-x64.zip",
                        "llama-b99999-bin-win-cpu-x64.zip"])
    main_asset, tag, cudart = lr.pick_assets(
        rel, ["cuda-12.4", "cuda-13.5", "vulkan", "cpu"])
    if tag != "vulkan" or cudart is not None:
        fails.append(f"链式退档错误: 命中 {tag}, cudart={cudart}")

    rel = fake_release(["llama-b99999-bin-win-cuda-12.4-x64.zip",
                        "cudart-llama-bin-win-cuda-12.4-x64.zip"])
    _, tag, cudart = lr.pick_assets(rel, ["cuda-12.4"])
    if tag != "cuda-12.4" or cudart is None:
        fails.append("CUDA 包没有带上 cudart 运行库(装完会缺 cublas64_*.dll)")

    rel = fake_release(["llama-b99999-bin-win-cpu-x64.zip"])
    try:
        lr.pick_assets(rel, ["vulkan"])
        fails.append("没有适配资产时应抛 ReleaseError, 实际没抛")
    except lr.ReleaseError:
        pass
    print("资产挑选: 退档 / cudart / 无适配报错 三项已核对")

    # ── 5. 系统代理 ──
    try:
        proxy = lr.system_proxy()
        print(f"系统代理: {proxy or '(未开启)'}")
    except Exception as exc:
        fails.append(f"system_proxy 抛异常: {type(exc).__name__}: {exc}")

    # ── 6. 替换 ──
    tmp = Path(tempfile.mkdtemp(prefix="llama 替换测试_"))
    dest = tmp / "llama-cpp"
    dest.mkdir()
    (dest / "llama-server.exe").write_bytes(b"OLD-EXE")
    (dest / "ggml-cuda.dll").write_bytes(b"OLD-CUDA")
    (dest / "notes.txt").write_bytes(b"user file")

    src = tmp / "pkg"
    src.mkdir()
    (src / "llama-server.exe").write_bytes(b"NEW-EXE")
    (src / "ggml-vulkan.dll").write_bytes(b"NEW-VULKAN")

    manifest = lr._replace_files(src, dest, "b99999", "vulkan")
    if (dest / "llama-server.exe").read_bytes() != b"NEW-EXE":
        fails.append("替换后 exe 不是新版本")
    if (dest / "ggml-cuda.dll").exists():
        fails.append("旧后端 dll(ggml-cuda.dll)没有清掉")
    if not (dest / "ggml-vulkan.dll").is_file():
        fails.append("新后端 dll 没落盘")
    if (dest / "notes.txt").read_bytes() != b"user file":
        fails.append("用户自己的文件被误删")
    if manifest.get("variant") != "vulkan" or lr.infer_variant(dest) != "vulkan":
        fails.append(f"安装清单/后端推断不对: {manifest} / {lr.infer_variant(dest)}")
    print("替换: 覆盖 / 清旧 dll / 保留用户文件 / 清单 四项已核对")

    # ── 7. 回滚 ──
    dest2 = tmp / "rollback"
    dest2.mkdir()
    (dest2 / "llama-server.exe").write_bytes(b"OLD-EXE")
    (dest2 / "ggml-cuda.dll").write_bytes(b"OLD-CUDA")
    orig_copy2 = lr.shutil.copy2

    def boom(s, d, *a, **k):
        # 只让"写入安装目录的 exe"这一步失败, 模拟文件被占用
        if Path(d).parent == dest2 and Path(d).name == "llama-server.exe":
            raise OSError(13, "模拟文件被占用")
        return orig_copy2(s, d, *a, **k)

    lr.shutil.copy2 = boom
    try:
        lr._replace_files(src, dest2, "b99999", "vulkan")
        fails.append("复制失败时应抛异常, 实际没抛")
    except OSError:
        pass
    finally:
        lr.shutil.copy2 = orig_copy2
    if (dest2 / "llama-server.exe").read_bytes() != b"OLD-EXE":
        fails.append("回滚后 exe 没恢复原内容")
    if (dest2 / "ggml-cuda.dll").read_bytes() != b"OLD-CUDA":
        fails.append("回滚后旧后端 dll 没放回原位")
    if (dest2 / "ggml-vulkan.dll").exists():
        fails.append("回滚后仍残留新写入的 dll")
    print("回滚: exe / 旧 dll / 新增文件 三项已还原")

    # ── 8. pending 往返 ──
    dest3 = tmp / "pending-dest"
    dest3.mkdir()
    (dest3 / "llama-server.exe").write_bytes(b"OLD")
    pkg = _make_package(tmp / "pending-pkg", {
        "llama-server.exe": b"PENDING-NEW", "ggml-vulkan.dll": b"VK"})
    staging = tmp / ".pending-dest.staging"
    staging.mkdir()
    (staging / "llama-server.exe").write_bytes(b"PENDING-NEW")
    (staging / "ggml-vulkan.dll").write_bytes(b"VK")
    lr.save_pending(dest3, {"tag": "b100001", "variant": "vulkan",
                            "staging": staging, "main_zip": pkg, "cudart_zip": None})
    meta = lr.load_pending(dest3)
    if not meta or meta["tag"] != "b100001":
        fails.append(f"pending 元数据读不回来: {meta}")
    elif (dest3 / "llama-server.exe").read_bytes() != b"OLD":
        fails.append("save_pending 不该动安装目录")
    applied = lr.apply_pending(dest3)
    if not applied or (dest3 / "llama-server.exe").read_bytes() != b"PENDING-NEW":
        fails.append("apply_pending 没把新版本落地")
    if lr.load_pending(dest3) is not None:
        fails.append("apply_pending 之后 pending 标记应已清除")
    print("pending: 存 / 读 / 应用 / 清理 四项已核对")

    # ── 8b. 跨机器 pending: 后端不符必须拒绝 ──
    # 把整个目录(含 pending 包)拷到显卡不同的机器时, 装上去就是一堆跑不起来的 dll
    dest4 = tmp / "pending-mix"
    dest4.mkdir()
    (dest4 / "llama-server.exe").write_bytes(b"OLD")
    (dest4 / "ggml-vulkan.dll").write_bytes(b"VK")          # 本机是 vulkan
    pkg2 = _make_package(tmp / "pending-pkg2", {
        "llama-server.exe": b"CUDA-NEW", "ggml-cuda.dll": b"CU"})
    st2 = tmp / ".pending-mix.staging"
    st2.mkdir()
    (st2 / "llama-server.exe").write_bytes(b"CUDA-NEW")
    (st2 / "ggml-cuda.dll").write_bytes(b"CU")
    lr.save_pending(dest4, {"tag": "b100002", "variant": "cuda-12.4",
                            "staging": st2, "main_zip": pkg2, "cudart_zip": None})
    try:
        lr.apply_pending(dest4)
        fails.append("跨机器(后端不同)的 pending 应当被拒绝")
    except lr.ReleaseError:
        pass
    if (dest4 / "llama-server.exe").read_bytes() != b"OLD":
        fails.append("被拒绝的 pending 不该改动安装目录")
    if lr.load_pending(dest4) is not None:
        fails.append("被拒绝的 pending 应被清除, 否则每次启动都会失败")
    print("跨机 pending: 后端不符被拒 / 目录未动 / 包已清 三项已核对")

    # ── 9. 定位 exe 不回落 PATH ──
    empty = tmp / "empty"
    empty.mkdir()
    if app._locate_installed_server(empty) is not None:
        fails.append("给定空目录时不该回落到 PATH 里的 llama-server")
    (empty / "bin").mkdir()
    (empty / "bin" / "llama-server.exe").write_bytes(b"stub")
    found = app._locate_installed_server(empty)
    if found is None or found.parent != empty / "bin":
        fails.append(f"一级子目录里的 exe 没找到, 实际 {found}")
    print(f"定位 exe: {found}")

    try:
        app.monitor.stop()
    except Exception:
        pass
    app.after(0, app.destroy)
    shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fails:
        print("FAIL")
        for f in fails:
            print("  - " + f)
        return 1
    print("PASS  安装链路: 弹窗/build 比较/资产挑选/替换/回滚/pending 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
