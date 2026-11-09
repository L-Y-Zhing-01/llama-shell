# -*- coding: utf-8 -*-
"""自检共用桩: 让 App 能在无人值守下起来, 且不碰真实配置/模型目录。"""

import tempfile

import main as app_main


def stub_config_io(block_dialogs: bool = True) -> str:
    """替换配置读写与启动弹窗, 返回临时模型目录。

    block_dialogs=True 时连"未检测到 llama.cpp"弹窗也一起屏蔽
    (它会 grab_set 抢占输入, 换肤自检不需要它)。
    """
    tmpdir = tempfile.mkdtemp(prefix="llama_selftest_")
    cfg = {
        "theme": "bootstrap-dark",
        "models_dir": tmpdir,
        "models_dir_history": [],
        "llama_server_cmd": "llama-server-not-installed-for-test",
        "monitor_interval_ms": 1000,
        "context_presets": [{"label": "8k", "value": 8192},
                            {"label": "32k", "value": 32768}],
        "help_url": "",
    }

    app_main.load_config = lambda: dict(cfg)
    app_main.save_config = lambda c: None
    app_main.load_models_config = lambda: {"models": {}, "current_model": ""}
    app_main.save_models_config = lambda c: None
    app_main.init_models_config = lambda d: {"models": {}, "current_model": ""}
    if block_dialogs:
        app_main.App._show_llama_missing_dialog = lambda self, notice="": None
        app_main.App._show_param_change_report = lambda self: None
        app_main.App._log_param_changes = lambda self: None
    return tmpdir
