# -*- coding: utf-8 -*-
"""路径常量与配置文件读写(config.json / models_config.json)。"""

import json
from pathlib import Path

from llama_params import get_default_params

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
MODELS_CONFIG_PATH = SCRIPT_DIR / "models_config.json"
LOG_DIR = SCRIPT_DIR / "logs"

# 模型目录默认值: 程序上一级目录(存放 .gguf 的位置)。
# 用相对路径, 保证整个文件夹拷贝到其他电脑后仍然可用。
DEFAULT_MODELS_DIR = ".."

DEFAULT_CONFIG = {
    # 界面主题: ttkbootstrap 主题名(如 bootstrap-dark / nord-light), 见 ui_theme.py。
    # 字面量与 ui_theme.DEFAULT_THEME 保持一致 —— 这里不 import ui_theme,
    # 避免把 ttkbootstrap 拖进配置层(app_config 要能被纯标准库环境使用)。
    "theme": "bootstrap-dark",
    "llama_server_cmd": "llama-server",
    "models_dir": DEFAULT_MODELS_DIR,
    "monitor_interval_ms": 1000,
    "context_presets": [
        {"label": "4k",  "value": 4096},
        {"label": "8k",  "value": 8192},
        {"label": "16k", "value": 16384},
        {"label": "32k", "value": 32768},
        {"label": "64k", "value": 65536},
    ],
    # 帮助页地址: 相对路径基于程序所在目录; 部署在线站点后改为 http(s) 完整网址
    "help_url": "help/index.html",
    # 已确认"接管"的安装目录(非本程序安装、但用户同意让本程序覆盖更新);
    # 按目录记一次, 之后同一目录不再重复弹确认
    "update_owner_ack": "",
    # 浏览过的模型目录历史(绝对路径, 最新在前); models_dir 本身即"上次使用的目录",
    # 下次启动自动从这里扫描
    "models_dir_history": [],
}

# 模型目录历史最多记录条数
MAX_DIR_HISTORY = 10


def resolve_path(p: str) -> Path:
    """解析配置中的路径: 绝对路径原样使用, 相对路径基于程序所在目录"""
    path = Path(p)
    if not path.is_absolute():
        path = SCRIPT_DIR / path
    return path.resolve()


def resolve_help_url(raw: str) -> "str | None":
    """把 config 的 help_url 解析成可直接打开的地址。

    - http(s):// 或 file:// 开头: 原样返回(在线站点/显式地址)
    - 其余视为本地路径(相对路径基于程序所在目录), 转成 file:/// URI
      (as_uri 会处理盘符、空格、中文的百分号编码)
    - 本地文件不存在或值为空: 返回 None
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith(("http://", "https://", "file://")):
        return raw
    p = resolve_path(raw)
    return p.as_uri() if p.is_file() else None


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            # 补全缺失键
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            # 注意: 这里**不**因为目录不存在就改写 models_dir / 删历史条目。
            # 移动盘没插、网络盘断线、换台电脑打开, 都会让路径"暂时"不存在,
            # 一旦就地抹掉就再也想不起上次用的是哪 —— 由 pick_models_dir()
            # 在启动时按"可用性"挑选, 配置本身保持原样。
            if len(cfg.get("models_dir_history", [])) > MAX_DIR_HISTORY:
                cfg["models_dir_history"] = cfg["models_dir_history"][:MAX_DIR_HISTORY]
                save_config(cfg)
            return cfg
        except Exception:
            pass
    cfg = dict(DEFAULT_CONFIG)
    save_config(cfg)
    return cfg


def dir_has_gguf(p: Path) -> bool:
    """目录里是否有 .gguf（扫描失败/无权限一律当没有）"""
    try:
        return next(iter(p.glob("*.gguf")), None) is not None
    except OSError:
        return False


def pick_models_dir(cfg: dict) -> tuple[Path, str]:
    """启动时决定扫描哪个目录, 返回 (路径, 来源说明)。

    优先级（每档都要求目录真实存在）:
      1. 上次使用的 models_dir, 且里面**有** .gguf;
      2. 历史目录里第一个有 .gguf 的（上次那个盘没插时接着用次新的）;
      3. 上次使用的目录（存在但没模型 —— 如实显示空列表, 由界面提示去选）;
      4. 历史里第一个存在的目录;
      5. 默认相对路径 ".."。
    全都不可用时返回配置值本身, 让界面能显示"目录不存在"而不是空白。

    来源说明只用于状态栏与日志, 便于排查"为什么列表是空的"。
    """
    last = resolve_path(cfg.get("models_dir") or DEFAULT_MODELS_DIR)
    hist = [resolve_path(d) for d in cfg.get("models_dir_history", [])]
    # 去重（大小写不敏感, Windows 语义）
    seen, cands = set(), []
    for p in [last] + hist:
        key = str(p).casefold()
        if key not in seen:
            seen.add(key)
            cands.append(p)

    for p in cands:
        if p.is_dir() and dir_has_gguf(p):
            return p, ("上次使用" if p == last else "历史目录")
    for p in cands:
        if p.is_dir():
            return p, ("上次使用(无模型)" if p == last else "历史目录(无模型)")
    return last, "目录不存在"


def remember_models_dir(cfg: dict, dir_str: str) -> list[str]:
    """把目录记为当前模型目录并写入历史(去重、最新在前、限量), 返回历史列表。

    - models_dir 同步更新为该目录的绝对路径 → 下次启动自动从这里扫描;
    - 去重不区分大小写(Windows 路径大小写不敏感);
    - 默认相对路径 ".." 这类可移植写法一旦被浏览替换, 就以绝对路径记录。
    """
    p = str(Path(dir_str).resolve())
    hist = [d for d in cfg.get("models_dir_history", [])
            if d.casefold() != p.casefold()]
    hist.insert(0, p)
    cfg["models_dir_history"] = hist[:MAX_DIR_HISTORY]
    cfg["models_dir"] = p
    save_config(cfg)
    return cfg["models_dir_history"]


def load_models_config() -> dict:
    if MODELS_CONFIG_PATH.exists():
        try:
            return json.loads(MODELS_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"models": {}, "current_model": None}


def save_models_config(cfg: dict) -> None:
    MODELS_CONFIG_PATH.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def init_models_config(models_dir: Path) -> dict:
    """首次运行: 扫描 .gguf 文件, 用默认参数初始化"""
    cfg = {"models": {}, "current_model": None}
    defaults = get_default_params()

    for gguf in sorted(models_dir.glob("*.gguf")):
        params = dict(defaults)
        params["model"] = str(gguf.resolve())
        cfg["models"][gguf.stem] = {
            "gguf_file": gguf.name,
            "params": dict(params),
            "original_params": dict(params),
        }
    save_models_config(cfg)
    return cfg
