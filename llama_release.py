# -*- coding: utf-8 -*-
"""GitHub release 通道: 查最新构建 → 下载 → 校验 → 就地替换旧版。

为什么彻底不用 winget
--------------------
1. winget 的优先安装作用域是 Program Files, 装完还得提权重开; 本程序一直是
   便携目录(H:\\llama-cpp)直接跑, 两边版本彼此不知情, 「更新」按钮点了等于白点;
2. winget 的包源有缓存延迟, 实测上游已发 b12224 而 winget 仍报 b12182;
3. 我们需要控制"后端"(cuda/vulkan/cpu) — winget 只发一个默认包。

现在的链路(全部标准库, 不再生成任何 .ps1):
    查 releases → 挑与本机后端匹配的资产 → 断点续传下载 → sha256 校验
    → 解压到暂存目录 → 逐文件替换(先备份, 失败整体回滚) → 写安装清单
    → 重启程序, 启动时跑一次 --help 对账。

踩过的坑(改动前先读)
--------------------
- `releases/latest` 指向 v0.9.0 空壳(只有 nightly-tag.txt), 二进制只在 b 前缀的
  pre-release 里, 因此必须扫列表, 不能走 latest;
- 版本比较**只能按 build 数字**: 字符串比较下 "b9999" > "b12224";
- 直接连 GitHub 会走系统代理(WinINET), 但 Python 的 urllib 读注册表时对
  `http=...;https=...` 这种多协议写法处理不可靠, 这里自己解析后显式挂 ProxyHandler;
- Windows 允许 rename 正在运行的 exe, 但不允许覆盖被加载的 dll。所以**不整目录
  rename**: 逐文件覆盖, 覆盖前先把旧文件备份到同级 .bak 目录, 任何一步失败就回滚;
- 旧版本残留的后端 dll(如从 cuda 换 vulkan)必须清掉, 否则按名加载时可能串味,
  因此根目录下的 exe/dll 凡不在新包清单里的, 一律移入备份目录。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import socket
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from llama_install import ARCH
from winenv import system_proxy  # noqa: F401  (自检脚本经 llama_release.system_proxy 调用)

#: 仓库与接口
REPO = "ggml-org/llama.cpp"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=30"
REPO_URL = f"https://github.com/{REPO}"

#: GitHub API 要求带 UA, 缺了直接 403
UA = "llama-shell-updater/1.0 (+https://github.com/ggml-org/llama.cpp)"

#: 安装清单文件名(放在安装目录根, 记录版本/后端/文件列表)
MANIFEST = ".llama-install.json"

#: 单次读取的块大小
CHUNK = 256 * 1024


class ReleaseError(RuntimeError):
    """查询/下载/解压/替换过程中的可读错误"""


class Cancelled(Exception):
    """用户主动中止"""


# ────────────────────────── 小工具 ──────────────────────────

def build_of(tag: str) -> int:
    """'b12224' / 'v0.9.0' → 数字; 取不到返回 0"""
    m = re.search(r"(\d+)", tag or "")
    return int(m.group(1)) if m else 0


def parse_build(version_text: str) -> int:
    """从 `llama-server --version` 的输出里取 build 号。

    实测格式: `version: 0.9.0-dev (build 12224, commit abcdef)`。
    取不到 build 时退化找 `b12224` 这种 tag 写法, 再没有就返回 0。
    """
    text = version_text or ""
    m = re.search(r"build\s*(\d+)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"\bb(\d{3,7})\b", text)
    return int(m.group(1)) if m else 0


def _opener() -> urllib.request.OpenerDirector:
    proxy = system_proxy()
    if proxy:
        # 显式指定, 不再让 urllib 去猜注册表
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    else:
        # 空 dict = 明确直连, 避免环境变量里的失效代理把请求拖死
        handler = urllib.request.ProxyHandler({})
    return urllib.request.build_opener(handler)


def _get_json(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/vnd.github+json",
    })
    try:
        with _opener().open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise ReleaseError(
                "GitHub 接口被限流(未登录每小时 60 次), 请稍后再试或检查代理") from None
        if exc.code == 404:
            raise ReleaseError("GitHub 上找不到该仓库/接口(404)") from None
        raise ReleaseError(f"GitHub 返回 HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        proxy = system_proxy()
        extra = f"(当前代理: {proxy})" if proxy else "(未检测到系统代理)"
        raise ReleaseError(f"连不上 GitHub: {exc.reason} {extra}") from None
    except (socket.timeout, TimeoutError):
        raise ReleaseError("连接 GitHub 超时, 请检查网络/代理") from None


# ────────────────────────── 查询 ──────────────────────────

#: 同一次运行内不重复打接口(GitHub 未登录只有 60 次/小时)
_cache: dict = {"ts": 0.0, "release": None}


def latest_release(force: bool = False, arch: str = ARCH) -> dict:
    """取最新一个带 Windows 预编译包的 b 构建。

    返回 {"tag": "b12224", "build": 12224, "assets": [...]};
    列表里没有可用构建时抛 ReleaseError。
    """
    if not force and _cache["release"] and time.time() - _cache["ts"] < 600:
        return _cache["release"]

    data = _get_json(RELEASES_API)
    if not isinstance(data, list):
        raise ReleaseError("GitHub 返回的数据格式异常")

    suffix = f"-{arch}.zip"
    picked = None
    for rel in data:
        if rel.get("draft"):
            continue
        tag = str(rel.get("tag_name") or "")
        if not re.fullmatch(r"b\d+", tag):
            continue        # v0.9.0 之类的空壳版本没有二进制
        assets = rel.get("assets") or []
        if not any(str(a.get("name", "")).startswith("llama-")
                   and str(a.get("name", "")).endswith(suffix) for a in assets):
            continue
        build = build_of(tag)
        if picked is None or build > picked["build"]:
            picked = {"tag": tag, "build": build, "assets": assets,
                      "published_at": rel.get("published_at", "")}
    if picked is None:
        raise ReleaseError("上游没有找到带 Windows 预编译包的构建")
    _cache.update(ts=time.time(), release=picked)
    return picked


def _asset_name(asset: dict) -> str:
    return str(asset.get("name", ""))


def pick_assets(release: dict, chain: list[str], arch: str = ARCH) -> tuple:
    """在资产列表里按后端优先级链挑主包, 返回 (主包, 命中 tag, cudart 包或 None)。

    链式而不是硬指定: 上游偶尔漏发某个后端(实测有版本缺 vulkan), 缺一档要能自动退。
    """
    assets = release.get("assets") or []
    for tag in chain:
        want = f"-bin-win-{tag}-{arch}.zip"
        main = next((a for a in assets
                     if _asset_name(a).startswith("llama-") and _asset_name(a).endswith(want)),
                    None)
        if main:
            cudart = None
            if tag.startswith("cuda"):
                rt_name = f"cudart-llama-bin-win-{tag}-{arch}.zip"
                cudart = next((a for a in assets if _asset_name(a) == rt_name), None)
            return main, tag, cudart
    tried = ", ".join(chain)
    raise ReleaseError(f"{release['tag']} 没有适配 {arch} 的预编译包 (试过: {tried})")


def asset_url(asset: dict) -> str:
    return str(asset.get("browser_download_url") or asset.get("url") or "")


def asset_digest(asset: dict) -> str:
    """GitHub 新版接口带的 'sha256:xxx'; 没有则返回空串"""
    d = str(asset.get("digest") or "")
    return d.split(":", 1)[1] if d.lower().startswith("sha256:") else ""


# ────────────────────────── 下载与校验 ──────────────────────────

def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for block in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _download_once(url: str, dest: Path, on_progress, cancel, throttle: float = 0.2) -> None:
    """单次下载(断了就从已有字节续传)。"""
    pos = dest.stat().st_size if dest.exists() else 0
    headers = {"User-Agent": UA}
    if pos:
        headers["Range"] = f"bytes={pos}-"
    req = urllib.request.Request(url, headers=headers)
    with _opener().open(req, timeout=90) as resp:
        # 服务器不认 Range 时会回 200 + 全量, 必须从头写
        if pos and getattr(resp, "status", 200) != 206:
            pos = 0
        total = pos
        cl = resp.headers.get("Content-Length")
        if cl and str(cl).isdigit():
            total = int(cl) + pos
        last = 0.0
        with open(dest, "ab" if pos else "wb") as fp:
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                block = resp.read(CHUNK)
                if not block:
                    break
                fp.write(block)
                pos += len(block)
                now = time.time()
                if on_progress and now - last > throttle:
                    last = now
                    on_progress(pos, total)


def download(url: str, dest, size: int = 0, digest: str = "", on_progress=None,
             cancel=None, tries: int = 3) -> Path:
    """断点续传下载 + sha256 校验, 返回本地路径。

    校验失败一律删掉重下: 代理返回的空壳 200(Content-Length: 0) 是实测踩过的,
    只看"文件存在"会把 0 字节包当成功。
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not url:
        raise ReleaseError("下载地址为空")

    last_err = ""
    for attempt in range(1, tries + 1):
        try:
            _download_once(url, dest, on_progress, cancel)
            got = dest.stat().st_size
            if size and got != size:
                raise ReleaseError(f"文件大小不符: 得到 {got} 字节, 应为 {size} 字节")
            if digest:
                if on_progress:
                    on_progress(got, got)
                actual = _sha256_of(dest)
                if actual.lower() != digest.lower():
                    raise ReleaseError("sha256 校验不通过(文件损坏或被中间层替换)")
            return dest
        except Cancelled:
            raise
        except (ReleaseError, urllib.error.URLError, socket.timeout, TimeoutError,
                ConnectionError, OSError) as exc:
            last_err = str(exc) or type(exc).__name__
            if attempt >= tries:
                break
            # 大小/hash 不符必须重下, 不能续传坏文件
            if dest.exists() and ("大小不符" in last_err or "sha256" in last_err):
                try:
                    dest.unlink()
                except OSError:
                    pass
            time.sleep(1.5 * attempt)
    raise ReleaseError(f"下载失败: {last_err}")


# ────────────────────────── 安装清单 / 后端推断 ──────────────────────────

def manifest_path(dest) -> Path:
    return Path(dest) / MANIFEST


def read_manifest(dest) -> dict:
    try:
        return json.loads(manifest_path(dest).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def infer_variant(dest) -> str:
    """推断当前装的是哪个后端。

    优先读安装清单; 老版本(手动解压/PS 脚本装的)没有清单, 按 dll 名判断:
    ggml-cuda.dll / ggml-vulkan.dll / ggml-hip.dll / ggml-sycl.dll。

    CUDA 两档的 ggml-cuda.dll 同名, 只能靠运行库区分(cublas64_12 / cublas64_13),
    因此**必须先看 cublas 再看 ggml-cuda** —— 反过来的话 13.5 的机器会被误判成
    12.4, 更新时就会下错档(虽然同家族能跑, 但等于每次更新都在降级驱动要求)。
    """
    known = read_manifest(dest).get("variant")
    if known:
        return str(known)
    try:
        names = {p.name.lower() for p in Path(dest).glob("*.dll")}
    except OSError:
        return "cpu"
    if "cublas64_13.dll" in names:
        return "cuda-13.5"
    if "ggml-cuda.dll" in names or "cublas64_12.dll" in names:
        return "cuda-12.4"
    if "ggml-vulkan.dll" in names:
        return "vulkan"
    if "ggml-hip.dll" in names or "ggml-rocm.dll" in names:
        return "rocm-10.0"
    if "ggml-sycl.dll" in names:
        return "sycl"
    return "cpu"


#: 后端 → 家族。cuda-12.4 / 13.5 的 dll 名完全相同, 只能合到同一家族里比对
_VARIANT_FAMILY = {
    "cuda-12.4": "cuda",
    "cuda-13.5": "cuda",
    "vulkan": "vulkan",
    "rocm-10.0": "rocm",
    "sycl": "sycl",
    "cpu": "cpu",
}

#: 家族 → 包内必有的特征 dll(任一命中即可)
_FAMILY_DLLS = {
    "cuda": ("ggml-cuda.dll",),
    "vulkan": ("ggml-vulkan.dll",),
    "rocm": ("ggml-hip.dll", "ggml-rocm.dll"),
    "sycl": ("ggml-sycl.dll",),
}

def family_of(variant: str) -> str:
    """后端名 → 家族(cuda / vulkan / rocm / sycl / cpu)"""
    return _VARIANT_FAMILY.get((variant or "").lower(), "cpu")


def detect_family(root) -> str:
    """按目录里的 dll 判断这是哪个后端家族的包; 都没有则当 cpu"""
    try:
        names = {p.name.lower() for p in Path(root).glob("*.dll")}
    except OSError:
        return "cpu"
    for fam, dlls in _FAMILY_DLLS.items():
        if any(d in names for d in dlls):
            return fam
    return "cpu"


def verify_variant(root, variant: str) -> None:
    """确认解压出来的包确实是 variant 这一档, 不符抛 ReleaseError。

    这是"下载的版本 = 已装的平台版本"的最后一道闸。下列情况都会在这里露出来,
    而不是等替换完、启动时才报缺 DLL:
      · 上游这个 build 漏发了对应后端的包(实测有版本缺 vulkan);
      · 资产名规则变了, 按名字挑到了 cpu 包;
      · 代理/镜像返回了别的文件(sha256 缺失时 size 校验也可能同大小)。
    只比家族: cuda 12.4 与 13.5 的 dll 同名, 细分要靠 cudart 包, 缺 cudart 时
    若按子版本严格比会把正常包误杀。
    """
    want = family_of(variant)
    got = detect_family(root)
    if want == got:
        return
    raise ReleaseError(
        f"下载的包与目标后端不符: 期望 {variant}（{want} 家族），包里是 {got}。\n"
        "已放弃本次安装，安装目录未做任何改动。")



def infer_chain(dest) -> list[str]:
    """更新时的候选链: 先试当前后端, 再退到通用档。

    换后端不该在更新时悄悄发生, 所以只把"当前档 → cpu"作为退路;
    用户想换后端请走安装弹窗手动指定。
    """
    variant = infer_variant(dest)
    return [variant] if variant == "cpu" else [variant, "cpu"]


# ────────────────────────── 解压与替换 ──────────────────────────

def _safe_extract(zip_path: Path, target: Path) -> None:
    """解压并挡住 ../ 逃逸(zip 里的路径不可信)"""
    target.mkdir(parents=True, exist_ok=True)
    base = target.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            out = (target / info.filename).resolve()
            if out != base and base not in out.parents:
                raise ReleaseError(f"压缩包内含非法路径: {info.filename}")
        zf.extractall(target)


def _content_root(staging: Path) -> Path:
    """包内文件所在的根: 有的版本多套一层目录, 有的直接平铺"""
    if (staging / "llama-server.exe").is_file():
        return staging
    subdirs = [p for p in staging.iterdir() if p.is_dir()]
    if len(subdirs) == 1 and (subdirs[0] / "llama-server.exe").is_file():
        return subdirs[0]
    found = next(iter(staging.rglob("llama-server.exe")), None)
    if found is None:
        raise ReleaseError("压缩包里没有 llama-server.exe, 不是预编译包")
    return found.parent


def _replace_files(src_root: Path, dest: Path, tag: str, variant: str) -> dict:
    """把暂存目录的文件覆盖到安装目录, 失败整体回滚。

    顺序: 备份将被覆盖的旧文件 → 移走不再属于新包的 exe/dll → 复制新文件
          → 写清单。任何一步抛异常, 按记录逆序还原。

    成功后备份**保留**为 `.<目录名>.bak-<旧tag>`(只留一代): 新版本万一起不来,
    用户还能把这个目录改名回去, 比"回滚逻辑只覆盖失败瞬间"实用。
    """
    dest.mkdir(parents=True, exist_ok=True)
    old_tag = str(read_manifest(dest).get("tag") or "old")
    for stale in dest.parent.glob(f".{dest.name}.bak-*"):
        shutil.rmtree(stale, ignore_errors=True)
    backup = dest.parent / f".{dest.name}.bak-{old_tag}"
    backup.mkdir(parents=True, exist_ok=True)

    new_files = [p.relative_to(src_root) for p in src_root.rglob("*") if p.is_file()]
    if not new_files:
        raise ReleaseError("压缩包是空的")
    new_set = {str(r).replace("\\", "/").lower() for r in new_files}

    saved: list[tuple[Path, Path]] = []     # (备份位置, 原位置)
    created: list[Path] = []
    removed: list[tuple[Path, Path]] = []   # (备份位置, 原位置)
    try:
        for rel in new_files:
            old = dest / rel
            if old.is_file():
                keep = backup / rel
                keep.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old, keep)
                saved.append((keep, old))
        # 旧后端残留: 根目录下不在新包里的 exe/dll(如从 cuda 换 vulkan 后的旧 dll)
        for p in list(dest.iterdir()):
            if not p.is_file() or p.suffix.lower() not in (".exe", ".dll"):
                continue
            if p.name.lower() in new_set:
                continue
            keep = backup / p.name
            shutil.move(str(p), str(keep))
            removed.append((keep, p))
        for rel in new_files:
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            existed = target.exists()
            shutil.copy2(src_root / rel, target)
            if not existed:
                created.append(target)
    except Exception:
        # 回滚: 删掉新写入的 → 把移走的放回 → 把备份复制回去
        for p in created:
            try:
                p.unlink()
            except OSError:
                pass
        for keep, orig in removed:
            try:
                shutil.move(str(keep), str(orig))
            except OSError:
                pass
        for keep, orig in saved:
            try:
                shutil.copy2(keep, orig)
            except OSError:
                pass
        raise

    files = sorted(str(r).replace("\\", "/") for r in new_files)
    manifest = {
        "tag": tag,
        "variant": variant,
        "arch": ARCH,
        "installed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": files,
        "backup": str(backup),
    }
    try:
        manifest_path(dest).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return manifest


# ────────────────────────── 对外主流程 ──────────────────────────

def _cache_dir(dest: Path) -> Path:
    return dest.parent / f".{dest.name}.cache"


def stage(dest, chain: list[str] | None = None, on_log=None, on_progress=None,
          cancel=None, release: dict | None = None) -> dict:
    """下载并解压到暂存目录, 不在安装目录动任何文件。

    返回 {"tag","variant","staging","main_zip","cudart_zip","staging_root"}。
    """
    dest = Path(dest)
    log = on_log or (lambda _s: None)
    chain = chain or ["cpu"]
    rel = release or latest_release()

    main_asset, tag, cudart_asset = pick_assets(rel, chain)
    log(f"目标构建: {rel['tag']}   后端: {tag}   包: {_asset_name(main_asset)}")
    if cudart_asset:
        log(f"CUDA 运行库: {_asset_name(cudart_asset)}")

    cache = _cache_dir(dest)
    cache.mkdir(parents=True, exist_ok=True)

    def size_of(a):
        try:
            return int(a.get("size") or 0)
        except (TypeError, ValueError):
            return 0

    main_zip = download(
        asset_url(main_asset), cache / _asset_name(main_asset),
        size=size_of(main_asset), digest=asset_digest(main_asset),
        on_progress=on_progress, cancel=cancel)
    log(f"主包下载完成: {main_zip.name}  {main_zip.stat().st_size / 1024 ** 2:.1f} MB")

    cudart_zip = None
    if cudart_asset:
        cudart_zip = download(
            asset_url(cudart_asset), cache / _asset_name(cudart_asset),
            size=size_of(cudart_asset), digest=asset_digest(cudart_asset),
            on_progress=on_progress, cancel=cancel)
        log(f"运行库下载完成: {cudart_zip.name}")

    staging = dest.parent / f".{dest.name}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    log("正在解压…")
    _safe_extract(main_zip, staging)
    if cudart_zip:
        _safe_extract(cudart_zip, staging)
    root = _content_root(staging)
    # 包内容必须与目标后端一致: 上游漏发/挑错资产/代理换包都在这里拦住,
    # 而不是替换完启动时才报"找不到 ggml-cuda.dll"
    verify_variant(root, tag)
    log(f"解压完成, 共 {sum(1 for p in root.rglob('*') if p.is_file())} 个文件")

    return {"tag": rel["tag"], "variant": tag, "staging": staging,
            "main_zip": main_zip, "cudart_zip": cudart_zip, "staging_root": root}


def commit(staged: dict, dest, on_log=None) -> dict:
    """把暂存的包落到安装目录(带备份回滚), 返回安装清单"""
    dest = Path(dest)
    log = on_log or (lambda _s: None)
    try:
        log(f"正在替换 {dest} …")
        manifest = _replace_files(staged["staging_root"], dest,
                                  staged["tag"], staged["variant"])
    finally:
        shutil.rmtree(staged["staging"], ignore_errors=True)
    log(f"已安装 {manifest['tag']} ({manifest['variant']})")
    if manifest.get("backup"):
        log(f"上一版保留在 {manifest['backup']}（下次更新时清理）")
    return manifest


def cleanup_cache(dest) -> None:
    """删掉下载缓存(包已解压/安装后没用了, 一个 cuda 包 200MB+)"""
    shutil.rmtree(_cache_dir(Path(dest)), ignore_errors=True)


# ── 两段式更新: 有模型在跑时只落盘, 下次启动再生效 ──

def pending_paths(dest) -> tuple[Path, Path]:
    dest = Path(dest)
    base = dest.parent / f".{dest.name}.pending"
    return base.with_name(base.name + ".zip"), base.with_name(base.name + ".json")


def save_pending(dest, staged: dict) -> Path:
    """把暂存包挪到 pending 位置(不碰安装目录)"""
    zip_path, meta_path = pending_paths(dest)
    shutil.move(str(staged["main_zip"]), str(zip_path))
    meta = {"tag": staged["tag"], "variant": staged["variant"],
            "cudart": "", "staged_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    if staged.get("cudart_zip"):
        cudart_dst = zip_path.with_name(zip_path.stem + ".cudart.zip")
        shutil.move(str(staged["cudart_zip"]), str(cudart_dst))
        meta["cudart"] = cudart_dst.name
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(Path(staged["staging"]), ignore_errors=True)
    cleanup_cache(dest)
    return zip_path


def load_pending(dest) -> dict | None:
    zip_path, meta_path = pending_paths(dest)
    if not zip_path.is_file() or not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    meta["zip"] = zip_path
    cudart = zip_path.with_name(zip_path.stem + ".cudart.zip")
    meta["cudart_zip"] = cudart if cudart.is_file() else None
    return meta


def clear_pending(dest) -> None:
    zip_path, meta_path = pending_paths(dest)
    for p in (zip_path, meta_path, zip_path.with_name(zip_path.stem + ".cudart.zip")):
        try:
            p.unlink()
        except OSError:
            pass


def _has_backend_dlls(dest) -> bool:
    """目录里有没有后端特征 dll(ggml-*.dll)。残缺安装(只剩 exe)时无从判断档位"""
    try:
        return any(p.suffix.lower() == ".dll" and p.name.lower().startswith("ggml-")
                   for p in Path(dest).glob("*.dll"))
    except OSError:
        return False


def apply_pending(dest, on_log=None) -> dict | None:
    """把上次下载好的包落地。返回安装清单; 没有待应用的包返回 None。

    跨机器也要防一手: 把整个目录(含 pending 包)拷到另一台显卡不同的机器时,
    cuda 包落到 AMD 机上装上去就是一堆跑不起来的 dll, 这里直接拒绝并清掉。
    目录里没有任何 ggml-*.dll 时不作判断(那是残缺安装, 判断依据不足)。
    """
    meta = load_pending(dest)
    if meta is None:
        return None
    log = on_log or (lambda _s: None)
    dest = Path(dest)

    if (dest / "llama-server.exe").is_file() and _has_backend_dlls(dest):
        local = infer_variant(dest)
        if family_of(meta["variant"]) != family_of(local):
            clear_pending(dest)
            raise ReleaseError(
                f"待生效的更新是 {meta['variant']} 包，而当前安装目录是 {local} —— "
                "多半是换了台显卡不同的机器。已丢弃这个包，请重新检查更新。")

    staging = dest.parent / f".{dest.name}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    log(f"发现待生效的更新 {meta['tag']}, 正在应用…")
    _safe_extract(meta["zip"], staging)
    if meta.get("cudart_zip"):
        _safe_extract(meta["cudart_zip"], staging)
    root = _content_root(staging)
    verify_variant(root, meta["variant"])
    staged = {"tag": meta["tag"], "variant": meta["variant"], "staging": staging,
              "staging_root": root}
    manifest = commit(staged, dest, on_log=log)
    clear_pending(dest)
    return manifest


# ── 一步到位的安装/更新(供界面调用) ──

def install_latest(dest, chain: list[str] | None = None, on_log=None,
                   on_progress=None, cancel=None) -> dict:
    """下载 + 解压 + 落到 dest(首次安装与整包重装都用这个)"""
    staged = stage(dest, chain=chain, on_log=on_log, on_progress=on_progress,
                   cancel=cancel)
    manifest = commit(staged, dest, on_log=on_log)
    cleanup_cache(dest)
    return manifest
