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
            # 迁移: 配置里的模型目录在本机不存在时(如从其他电脑拷贝而来),
            # 回退到默认相对路径
            if not resolve_path(cfg["models_dir"]).exists():
                cfg["models_dir"] = DEFAULT_MODELS_DIR
                save_config(cfg)
            # 历史目录自清理: 不存在的(如拔掉的移动盘/临时目录)从历史剔除
            hist = [d for d in cfg.get("models_dir_history", [])
                    if resolve_path(d).exists()]
            if len(hist) != len(cfg.get("models_dir_history", [])):
                cfg["models_dir_history"] = hist
                save_config(cfg)
            return cfg
        except Exception:
            pass
    cfg = dict(DEFAULT_CONFIG)
    save_config(cfg)
    return cfg


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
