# -*- coding: utf-8 -*-
"""llama.cpp 自身的版本查询与 winget 更新。

包含三块互相独立的职责:
1. 本地版本 / --help 参数清单: get_version() / get_server_help()
2. winget 查询与升级:          check_update() / run_upgrade()
3. 参数表对账(启动时自动补齐): auto_sync_params() → sync_param_definitions()

坑位记录(改动前务必先读):
- 解析 --help 必须按"定义块"来: 只有定义行上的写法才算参数本体。用宽松正则扫全文会把
  说明文字里提到的参数名(如 --n-ubatch)和小数点被截断的残片(如 --fim-qwen-1)当成新参数。
- 参数入口用 --fim-qwen-1.5b-default 这种带 '.' 的名字, 正则字符集里必须包含 '.'。
- help 里标注 "has been removed" / "DEPRECATED" 的条目是废弃占位, 不能补进面板。
- winget 输出为 UTF-8。若用 text=True 而不指定 encoding, 在 cp936 环境下读取线程
  会抛 UnicodeDecodeError, stdout 直接变成 None——结果是"更新失败"却看不到任何原因。
- 不指定 --source 时 winget 会去搜 msstore 源, 该源有证书锁定校验, 被网络中间层
  干扰时报 0x8A15005E(服务器证书与任何预期值都不匹配)并使整个命令失败,
  因此所有 winget 调用都显式限定 --source winget。
- 升级要下载 200MB+ 安装包(实测约 5~6 分钟), 因此用"总时长上限 + 连续无输出上限"
  双阈值, 而不是单一硬超时。
- on_progress 回调逐条回传 winget 当前步骤, 供界面显示进度。
"""

import queue
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from llama_params import (
    PARAM_DEFINITIONS, PARAM_GROUP_LABELS, ParamDef,
    find_param_by_flag, rebuild_indexes,
)

CREATE_NO_WINDOW = 0x08000000

# winget 常见返回码 → 可读原因
WINGET_CODE_HINTS = {
    0x8A15002B: "该包当前没有可升级的版本(已是最新, 或被 winget pin 固定, 或安装作用域不匹配)",
    0x8A150014: "操作需要管理员权限, 请以管理员身份运行本程序(见下方手动命令)",
    0x8A15005E: "winget 搜索源时证书校验失败(常见于 msstore 源被代理干扰), 重试或改用 --source winget",
    0x8A150109: "程序包正在被占用, 请先关闭所有 llama-*.exe 进程后重试",
    0x8A150011: "安装包哈希校验失败, 一般是下载不完整, 重试即可",
    0x8A15003D: "安装被拒绝或取消",
    0x8A1500A1: "指定的包版本不存在(上游 releases 可能已删除该资产)",
    0x80073D02: "程序正在运行, 请先停止模型服务再更新",
    0x80070005: "拒绝访问(0x80070005), 需要管理员权限",
}

# --help 中没有实际参数含义的 flag, 自动补齐时跳过
_SKIP_FLAGS = {"--help", "--version", "--usage", "--props", "--list-devices"}

# 自动补齐的参数统一放在这个分组(标签页名字会带上数量)。
# 用户裁定: 这个页定位是「新增功能的介绍」, 不再叫「自动补齐」。
_AUTO_GROUP = "Auto"
_AUTO_GROUP_LABEL = "新增功能"


# ────────────────────────── --help 解析 ──────────────────────────

# --help 里每条参数定义以 '-' 起头, 续行(说明/默认值/环境变量)以空白起头
_HELP_FLAG_RE = re.compile(r"--[a-z0-9][-a-z0-9._]*")      # 含 '.' 才不会把 --fim-qwen-1.5b-default 截断
_HELP_SHORT_RE = re.compile(r"(?<![\w.-])-[A-Za-z]\w*")     # 单横线短名, 不误吃 --xxx 内部
_HELP_GAP_RE = re.compile(r"\s{8,}")                        # 参数列与说明列之间的对齐空白
_HELP_DEFAULT_RE = re.compile(r"\(default:\s*([^)]*)\)")
_HELP_INT_RE = re.compile(r"-?\d+")
_HELP_FLOAT_RE = re.compile(r"-?\d*\.\d+")
# 说明里出现这些字样说明该参数已废弃/移除, 不能补进面板
_HELP_DEAD_MARKS = ("has been removed", "deprecated")


def _parse_help_definitions(help_text: str) -> list[dict]:
    """把 --help 拆成参数定义块。

    返回 [{flags, shorts, arg, desc, default, dead}]:
    flags 只取定义行上的写法(不看说明文字), 因此不会把说明里提到的参数名当成本体。
    """
    blocks: list[dict] = []
    head: str | None = None
    body: list[str] = []

    def flush():
        if head is None:
            return
        parts = _HELP_GAP_RE.split(head, maxsplit=1)
        left = parts[0]
        desc = " ".join([*(p.strip() for p in parts[1:]), *body])
        flags = _HELP_FLAG_RE.findall(left)
        if not flags:
            return
        shorts = _HELP_SHORT_RE.findall(left)
        tail = left[left.rfind(flags[-1]) + len(flags[-1]):].strip()
        dead = any(m in (left + " " + desc).lower() for m in _HELP_DEAD_MARKS)
        m = _HELP_DEFAULT_RE.search(desc)
        blocks.append({
            "flags": flags,
            "shorts": [s for s in shorts if s.lower() not in {f.lower() for f in flags}],
            "arg": tail,
            "desc": desc,
            "default": m.group(1).strip() if m else "",
            "dead": dead,
        })

    for line in help_text.splitlines():
        if line.startswith("-"):
            flush()
            head, body = line, []
        elif head is not None and line.strip():
            body.append(line.strip())
    flush()
    return blocks


def _infer_type(arg: str, default: str) -> tuple[str, object]:
    """按取值占位符与默认值推断控件类型"""
    if not arg:
        return "bool", False
    if _HELP_INT_RE.fullmatch(default):
        return "int", int(default)
    if _HELP_FLOAT_RE.fullmatch(default):
        return "float", float(default)
    if arg == "N":
        # 默认值后面常带说明文字(如 "0 - same as --n-ubatch"), 取开头那个数字
        m = _HELP_INT_RE.match(default)
        if m:
            return "int", int(m.group())
        m = _HELP_FLOAT_RE.match(default)
        if m:
            return "float", float(m.group())
    return "str", ""


def _attach(owner: ParamDef, flag: str) -> None:
    """把一个等价写法挂到已有参数上: 负向 flag 优先占 negation_flag, 其余进 extra_flags"""
    fl = flag.lower()
    if fl == owner.cli_flag.lower() or fl in (owner.alias.lower(), owner.negation_flag.lower()):
        return
    if fl in [f.lower() for f in owner.extra_flags]:
        return
    if fl.startswith("--no-") and not owner.negation_flag:
        owner.negation_flag = flag
        if owner.type == "bool":
            owner.type, owner.default = "bool_pair", None
        return
    owner.extra_flags = (*owner.extra_flags, flag)


def sync_param_definitions(help_text: str) -> dict:
    """按 --help 对账参数表, 与上一次的参数表比对增删, 返回比对结果。

    返回 {"added": [flag, ...], "removed": [flag, ...]}:
    - added:   本次新出现、参数表里没有的 flag(已建新条目归入 _AUTO_GROUP);
    - removed: 上一次参数表里有、本次 --help 定义块(非废弃)里完全消失的 flag。
               只做比对报告, 不自动删除条目——防止解析噪声误删真实参数。

    规则:
    - 已废弃/移除的(help 标注 removed / DEPRECATED)一律不补;
    - 同一行并列的多种写法算同一个参数, 已登记的那个作主体, 其余挂成等价写法;
    - 整行都没登记过则以长 flag 建新条目, 归入 _AUTO_GROUP 分组。
    """
    blocks = _parse_help_definitions(help_text)
    # 本次 help 中"活着的"参数写法全集(含短名), 用于取消检测
    present: set = set()
    for b in blocks:
        if b["dead"]:
            continue
        present.update(f.lower() for f in b["flags"])
        present.update(s.lower() for s in b["shorts"])

    # 取消检测必须基于补齐前的参数表快照(否则本次新建的条目也会被算进来)
    removed: list[str] = []
    for p in list(PARAM_DEFINITIONS):
        variants = [f for f in (p.cli_flag, p.cli_short, p.alias,
                                p.negation_flag, *p.extra_flags) if f]
        if not variants:
            continue    # extra_args 这类无 flag 条目不参与比对
        if not any(v.lower() in present for v in variants):
            removed.append(p.cli_flag or variants[0])

    known = LlamaUpdater.defined_flags()
    added: list[str] = []

    for pdef in blocks:
        if pdef["dead"]:
            continue
        usable = [f for f in pdef["flags"]
                  if f.lower() not in _SKIP_FLAGS and f.lower() not in known]
        if not usable:
            continue

        owner = next((find_param_by_flag(f) for f in pdef["flags"] if f.lower() in known), None)
        if owner is None:
            positive = [f for f in usable if not f.lower().startswith("--no-")]
            if not positive:
                # 整行只有负向写法: 挂到同族正向参数, 找不到就放弃(不建孤立的三态)
                owner = find_param_by_flag("--" + usable[0][5:])
                if owner is None:
                    continue
            else:
                primary = positive[0]
                ptype, default = _infer_type(pdef["arg"], pdef["default"])
                owner = ParamDef(
                    key=re.sub(r"[^0-9a-z]+", "_", primary[2:].lower()).strip("_"),
                    cli_flag=primary,
                    cli_short=next((s for s in pdef["shorts"] if s.lower() not in known), ""),
                    label=primary,
                    group=_AUTO_GROUP,
                    type=ptype,
                    default=default,
                    help_text=(pdef["desc"] or "llama.cpp 新增参数") + " [llama.cpp 新增]",
                )
                PARAM_DEFINITIONS.append(owner)
                for s in pdef["shorts"]:
                    if s != owner.cli_short and s.lower() not in known:
                        owner.extra_flags = (*owner.extra_flags, s)

        for flag in usable:
            _attach(owner, flag)
            known.add(flag.lower())
            added.append(flag)

    if added:
        PARAM_GROUP_LABELS[_AUTO_GROUP] = f"{_AUTO_GROUP_LABEL} {len(added)}"
        rebuild_indexes()
    return {"added": added, "removed": removed}


class LlamaUpdater:
    """版本查询 + winget 更新 + 参数表同步"""

    WINGET_ID = "ggml.llamacpp"
    WINGET_SOURCE = "winget"
    REPO_URL = "https://github.com/ggml-org/llama.cpp"

    # winget 升级需下载 200MB+ 安装包(实测约 5~6 分钟, 视网速而定)。
    # 注意: stdout 被重定向时 winget 会关闭进度条动画, 下载期间可以几分钟不输出,
    # 因此"无输出"阈值必须给得很宽松, 否则会误杀正在下载的进程。
    UPGRADE_TIMEOUT = 2700       # 总时长上限(秒)
    UPGRADE_IDLE_TIMEOUT = 900   # 连续无输出上限(秒), 用于识别真正卡死

    # 版本号正则: 同时支持语义化版本号(0.4.1)和 build 编号(b11026)
    _VER_RE = re.compile(r"^[a-z]?\d+(\.\d+)*$")

    def __init__(self, llama_cmd: str, log_dir: Path):
        self.llama_cmd = llama_cmd
        self.log_dir = log_dir

    # ────────────── 本地版本 / 参数清单 ──────────────

    def get_version(self) -> str:
        """获取 llama-server 版本号, 如 '0.6.0-dev (build 11636)'; 失败返回空串"""
        try:
            result = subprocess.run(
                [self.llama_cmd, "--version"],
                capture_output=True, text=True, timeout=10,
                creationflags=CREATE_NO_WINDOW,
            )
            out = (result.stdout + result.stderr).strip()
            for line in out.splitlines():
                line = line.strip()
                if not line.lower().startswith("version"):
                    continue
                # 匹配 "version: 0.6.0-dev (build 11636, commit ...)"
                m = re.search(r"(\d+\.\d+\.\d+[^\s\)]*)\s*(?:\(build\s*(\d+))?", line)
                if m:
                    ver, build = m.group(1), m.group(2)
                    return f"{ver} (build {build})" if build else ver
            return out.splitlines()[0] if out else ""
        except Exception:
            return ""

    def get_server_help(self) -> str:
        """获取 llama-server --help 输出"""
        try:
            result = subprocess.run(
                [self.llama_cmd, "--help"],
                capture_output=True, text=True, timeout=15,
                creationflags=CREATE_NO_WINDOW,
            )
            return result.stdout
        except Exception:
            return ""

    @staticmethod
    def defined_flags() -> set:
        """获取 PARAM_DEFINITIONS 中已定义的所有 flag(小写), 含短名/别名/负向与等价写法"""
        flags = set()
        for p in PARAM_DEFINITIONS:
            for f in (p.cli_flag, p.cli_short, p.alias, p.negation_flag, *p.extra_flags):
                if f:
                    flags.add(f.lower())
        return flags

    # ────────────── winget 查询 ──────────────

    @staticmethod
    def _run_winget(args: list, timeout: int = 30) -> str:
        """执行 winget 并返回 stdout+stderr 合并文本, 失败返回空串

        必须显式 UTF-8 解码: 中文表头按 GBK 解码会损坏;
        错误信息写在 stderr, 因此必须合并读取。
        """
        try:
            r = subprocess.run(
                ["winget"] + args,
                capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
            return (r.stdout or "") + (r.stderr or "")
        except Exception:
            return ""

    def _query_args(self, subcmd: str) -> list:
        return [subcmd, "--id", self.WINGET_ID, "-e",
                "--source", self.WINGET_SOURCE, "--accept-source-agreements"]

    def latest_version(self) -> str:
        """`winget show` 拿到的最新可用版本号(如 'b11636'), 失败返回空串

        show 输出是结构化键值对(「版本: b11636」), 比解析 upgrade 表格
        (列对齐依赖空格、列名本地化)稳健得多。
        """
        output = self._run_winget(self._query_args("show"))
        for line in output.splitlines():
            m = re.match(r"^(?:版本|Version)\s*:\s*(\S+)", line.strip(), re.IGNORECASE)
            if m:
                return m.group(1)
        return ""

    def _installed_version(self, list_output: str) -> str:
        """从 `winget list` 表格行中取出已安装版本号, 取不到返回空串"""
        for line in list_output.splitlines():
            if "llamacpp" not in line.lower():
                continue
            for part in line.split():
                if self._VER_RE.match(part):
                    return part
        return ""

    def check_update(self) -> tuple:
        """检查是否有可用更新, 返回 (has_update, current_ver, available_ver)"""
        list_output = self._run_winget(self._query_args("list"))
        current_ver = self._installed_version(list_output) or self.get_version() or "未知"
        available_ver = self.latest_version()

        # winget show 取不到时, 回退到解析 upgrade 表格
        if not available_ver:
            upgrade_output = self._run_winget(
                ["upgrade", "--source", self.WINGET_SOURCE, "--accept-source-agreements"]
            )
            for line in upgrade_output.splitlines():
                low = line.lower()
                if self.WINGET_ID in low or ("llama" in low and "cpp" in low):
                    versions = [p for p in line.split()
                                if self._VER_RE.match(p) and not p.lower().startswith("ggml")]
                    if len(versions) >= 2:
                        current_ver, available_ver = versions[0], versions[1]
                        break
                    if len(versions) == 1:
                        available_ver = versions[0]
                        break

        self._dump_debug(list_output, current_ver, available_ver)

        if not available_ver:
            # 仍未拿到最新版本 → 视为已是最新(避免误报)
            return (False, current_ver, current_ver)
        return (current_ver.lower() != available_ver.lower(), current_ver, available_ver)

    def _dump_debug(self, list_output: str, current_ver: str, available_ver: str) -> None:
        """把 winget 查询结果落盘, 方便排查版本识别问题"""
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            (self.log_dir / "winget_debug.log").write_text(
                f"=== winget list --id ===\n{list_output}\n\n"
                f"=== current_ver: {current_ver} ===\n"
                f"=== winget show --id (latest) ===\n"
                f"available_ver: {available_ver}\n",
                encoding="utf-8",
            )
        except Exception:
            pass

    # ────────────── winget 升级 ──────────────

    def _upgrade_args(self) -> list:
        return [
            "upgrade", "--id", self.WINGET_ID,
            "--source", self.WINGET_SOURCE,   # 绕开 msstore 源的证书校验失败
            "--accept-source-agreements", "--accept-package-agreements",
            "--disable-interactivity",        # 无控制台时, 交互提示会让进程永久挂起
        ]

    def _log_upgrade(self, args: list, returncode, output: str) -> None:
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self.log_dir / "winget_upgrade.log", "a", encoding="utf-8") as fp:
                fp.write(f"\n{'=' * 64}\n[{stamp}] winget {' '.join(args)}\n"
                         f"returncode: {returncode}\n{'-' * 64}\n{output}\n")
        except Exception:
            pass

    @staticmethod
    def _explain_code(code: int) -> str:
        try:
            return WINGET_CODE_HINTS.get(int(code) & 0xFFFFFFFF, "")
        except Exception:
            return ""

    @staticmethod
    def _clean_tail(output: str, limit: int) -> str:
        """取输出末尾若干字符(去掉空行), 用于错误提示"""
        return "\n".join(l for l in output.splitlines() if l.strip())[-limit:]

    def run_upgrade(self, on_progress=None) -> tuple:
        """执行 winget 升级, 返回 (success, message)"""
        args = self._upgrade_args()
        try:
            proc = subprocess.Popen(
                ["winget"] + args,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception as e:
            return (False, f"无法启动 winget: {type(e).__name__}: {e}")

        # winget 用 \r 原地刷新进度条, 不能按行迭代, 因此按块读取交给主循环
        chunks: list = []
        text_queue: "queue.Queue" = queue.Queue()
        reader_done = threading.Event()
        # read1: 有多少读多少, 不等缓冲区填满(否则输出的时间粒度会被 4KB 缓冲抹平)
        read_chunk = getattr(proc.stdout, "read1", None) or proc.stdout.read

        def _reader():
            try:
                while True:
                    chunk = read_chunk(4096)
                    if not chunk:
                        break
                    text_queue.put(chunk)
            except Exception:
                pass
            finally:
                reader_done.set()

        threading.Thread(target=_reader, daemon=True).start()

        buf = ""
        last_step = reported_step = None
        deadline = time.time() + self.UPGRADE_TIMEOUT
        idle_deadline = time.time() + self.UPGRADE_IDLE_TIMEOUT
        abort_reason = ""

        while True:
            got = False
            while True:
                try:
                    chunk = text_queue.get_nowait()
                except queue.Empty:
                    break
                got = True
                text = chunk.decode("utf-8", "replace")
                chunks.append(text)
                buf += text
                # 按 \r / \n 切分出完整消息, 最后一段可能不完整, 留在 buf 里
                parts = re.split(r"[\r\n]+", buf)
                buf = parts.pop()
                for seg in parts:
                    if seg.strip():
                        last_step = seg.strip()
                if last_step and last_step != reported_step and on_progress:
                    reported_step = last_step
                    on_progress(last_step)

            if got:
                idle_deadline = time.time() + self.UPGRADE_IDLE_TIMEOUT

            if proc.poll() is not None and reader_done.is_set() and text_queue.empty():
                break
            if time.time() > idle_deadline:
                abort_reason = (f"winget 连续 {self.UPGRADE_IDLE_TIMEOUT} 秒没有任何输出, "
                                f"判定为卡死并已中止")
                break
            if time.time() > deadline:
                abort_reason = f"winget 超过 {self.UPGRADE_TIMEOUT // 60} 分钟仍未完成, 已中止"
                break
            time.sleep(0.2)

        if abort_reason:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            returncode = proc.wait(timeout=30)
        except Exception:
            returncode = -1
        try:
            proc.stdout.close()
        except Exception:
            pass

        output = "".join(chunks)
        if buf.strip():
            output += buf
        self._log_upgrade(args, returncode, output)

        manual_hint = "可手动在终端(cmd/PowerShell)执行:\n    winget " + " ".join(args)

        if abort_reason:
            tail = self._clean_tail(output, 300)
            return (False, abort_reason + (f"\n\n最后输出:\n{tail}" if tail else "")
                    + f"\n\n{manual_hint}")

        if returncode == 0:
            return (True, "升级成功")

        msg = f"winget 返回码 {returncode} (0x{returncode & 0xFFFFFFFF:08X})"
        hint = self._explain_code(returncode)
        if hint:
            msg += f"\n原因: {hint}"
        tail = self._clean_tail(output, 400)
        if tail:
            msg += f"\n\nwinget 输出:\n{tail}"
        return (False, msg + f"\n\n{manual_hint}")

    # ────────────── 参数表对账与自动补齐 ──────────────

    def auto_sync_params(self) -> dict:
        """启动时按 `llama-server --help` 对账参数表, 与上一次比对增删。

        返回 {"added": [flag, ...], "removed": [flag, ...]};
        help 取不到时返回空比对结果(不动参数表)。
        """
        help_text = self.get_server_help()
        if not help_text:
            return {"added": [], "removed": []}
        return sync_param_definitions(help_text)
