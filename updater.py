# -*- coding: utf-8 -*-
"""llama.cpp 自身的版本查询、更新检查与参数表对账。

包含三块互相独立的职责:
1. 本地版本 / --help 参数清单: get_version() / get_server_help()
2. 更新查询:                     check_update()  (比对 GitHub release 的 build 号)
3. 参数表对账(启动时自动补齐):   auto_sync_params() → sync_param_definitions()

更新本身(下载/校验/替换)在 llama_release.py; 这里只负责"有没有新版本"。
已彻底移除 winget: 便携目录里的版本 winget 根本不知道, 那句 `upgrade` 从来没生效过。

坑位记录(改动前务必先读):
- 解析 --help 必须按"定义块"来: 只有定义行上的写法才算参数本体。用宽松正则扫全文会把
  说明文字里提到的参数名(如 --n-ubatch)和小数点被截断的残片(如 --fim-qwen-1)当成新参数。
- 参数入口用 --fim-qwen-1.5b-default 这种带 '.' 的名字, 正则字符集里必须包含 '.'。
- help 里标注 "has been removed" / "DEPRECATED" 的条目是废弃占位, 不能补进面板。
- 短名正则必须允许内部 '-'(b11870 实测: --kv-unified 的负向短名写作 "-no-kvu",
  旧的 \\w* 会截断成 "-no"), 但要用负向 lookbehind 挡住 "--xxx" 的后半截。
- 版本比较**只能按 build 数字**: 字符串比较下 "b9999" 大于 "b12224", 会漏更新。
"""

import re
import subprocess
from datetime import datetime
from pathlib import Path

import llama_release
from llama_params import (
    PARAM_DEFINITIONS, PARAM_GROUP_LABELS, ParamDef,
    find_param_by_flag, rebuild_indexes,
)
from winenv import CREATE_NO_WINDOW

# --help 中没有实际参数含义的 flag, 自动补齐时跳过
_SKIP_FLAGS = {"--help", "--version", "--usage", "--props", "--list-devices"}

# 自动补齐的参数统一放在这个分组(标签页名字会带上数量)。
# 用户裁定: 这个页定位是「新增功能的介绍」, 不再叫「自动补齐」。
_AUTO_GROUP = "Auto"
_AUTO_GROUP_LABEL = "新增功能"


# ────────────────────────── --help 解析 ──────────────────────────

# --help 里每条参数定义以 '-' 起头, 续行(说明/默认值/环境变量)以空白起头
_HELP_FLAG_RE = re.compile(r"--[a-z0-9][-a-z0-9._]*")      # 含 '.' 才不会把 --fim-qwen-1.5b-default 截断
# 短名允许内部含 '-'(如 --kv-unified 的负向短名写作 "-no-kvu"); 负向 lookbehind 防止
# 把 "--help" 的后半截 "-help" 当成短名
_HELP_SHORT_RE = re.compile(r"(?<![\w.-])-[A-Za-z][\w-]*")  # 单横线短名, 不误吃 --xxx 内部
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
    """版本查询 + 更新检查 + 参数表同步"""

    REPO_URL = llama_release.REPO_URL

    def __init__(self, llama_cmd: str, log_dir: Path):
        self.llama_cmd = llama_cmd
        self.log_dir = log_dir

    # ────────────── 本地版本 / 参数清单 ──────────────

    def get_version(self) -> str:
        """获取 llama-server 版本号, 如 '0.9.0-dev (build 12224)'; 失败返回空串"""
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
                # 匹配 "version: 0.9.0-dev (build 12224, commit ...)"
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

    def install_dir(self) -> Path | None:
        """llama-server.exe 所在目录 —— 就是更新的目标目录。

        命令只是个裸名字(装在 PATH 里)时返回 None: 那种情况不归本程序管,
        硬去覆盖别人装的副本只会把两套文件搅在一起。
        """
        p = Path(self.llama_cmd)
        if p.is_file():
            return p.parent
        return None

    @staticmethod
    def defined_flags() -> set:
        """获取 PARAM_DEFINITIONS 中已定义的所有 flag(小写), 含短名/别名/负向与等价写法"""
        flags = set()
        for p in PARAM_DEFINITIONS:
            for f in (p.cli_flag, p.cli_short, p.alias, p.negation_flag, *p.extra_flags):
                if f:
                    flags.add(f.lower())
        return flags

    # ────────────── 更新查询 ──────────────

    def check_update(self) -> dict:
        """查 GitHub release 的最新 build, 与本地比数字。

        返回 {"ok", "has_update", "current", "latest", "current_build",
              "latest_build", "error"}。
        ok=False 表示查询本身失败(断网/限流), 界面应显示 error 而不是"已是最新"。
        """
        info = {"ok": False, "has_update": False, "current": "", "latest": "",
                "current_build": 0, "latest_build": 0, "error": "",
                # 本次查到的 release 原样带回去: 界面要在确认框里预演"会挑中哪个包",
                # 下载阶段也要用它, 避免两次查询之间上游又发了新 build
                "_release": None}
        if self.install_dir() is None:
            info["error"] = (f"当前命令 “{self.llama_cmd}” 不是可执行文件路径, "
                             "无法定位安装目录。请先在「安装」里装一份本程序管理的版本。")
            return info

        info["current"] = self.get_version() or "未知"
        info["current_build"] = llama_release.parse_build(info["current"])

        try:
            rel = llama_release.latest_release()
        except llama_release.ReleaseError as exc:
            info["error"] = str(exc)
            self._log_check(info)
            return info

        info["ok"] = True
        info["latest"] = rel["tag"]
        info["latest_build"] = rel["build"]
        info["_release"] = rel
        if info["current_build"] <= 0:
            info["error"] = (f"无法从本地版本号 “{info['current']}” 解析 build 编号, "
                             "为避免误报, 不判定为有更新。")
            self._log_check(info)
            return info
        info["has_update"] = rel["build"] > info["current_build"]
        self._log_check(info)
        return info

    def _log_check(self, info: dict) -> None:
        """把一次查询结果落盘(排查"为什么说已是最新")"""
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self.log_dir / "update_check.log", "a", encoding="utf-8") as fp:
                fp.write(f"[{stamp}] cmd={self.llama_cmd} "
                         f"local={info['current']!r}(build {info['current_build']}) "
                         f"remote={info['latest']}(build {info['latest_build']}) "
                         f"has_update={info['has_update']} err={info['error']!r}\n")
        except Exception:
            pass

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
