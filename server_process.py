# -*- coding: utf-8 -*-
"""llama-server 子进程的启动 / 停止 / 状态查询 / 就绪轮询。"""

import os
import signal
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

CREATE_NO_WINDOW = 0x08000000


class ServerProcess:
    """llama-server 进程的启动/停止/状态检查"""

    def __init__(self, models_dir: Path, log_dir: Path):
        self.models_dir = models_dir
        self.log_dir = log_dir
        self.process: Optional[subprocess.Popen] = None
        self.log_path: Optional[Path] = None
        self.log_fp = None
        self.start_time: Optional[float] = None
        self.model_name: str = ""
        self.argv: list = []
        self._return_code: Optional[int] = None

    def start(self, argv: list, model_name: str) -> Path:
        """启动进程(会先停掉已在运行的), 返回日志文件路径"""
        if self.is_running():
            self.stop()

        self.model_name = model_name
        self.argv = argv
        self._return_code = None

        # 确保日志目录存在
        self.log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = self.log_dir / f"{model_name}_{timestamp}.log"
        self.log_fp = open(self.log_path, "w", encoding="utf-8", errors="replace", buffering=1)

        # Windows 进程组(便于 Ctrl+Break) + 隐藏控制台
        creationflags = 0
        if os.name == "nt":
            creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW

        self.process = subprocess.Popen(
            argv,
            stdout=self.log_fp,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(self.models_dir),
            creationflags=creationflags,
            text=True,
        )
        self.start_time = time.time()
        return self.log_path

    def stop(self, timeout: float = 5.0) -> None:
        if self.process is None:
            return
        if self.is_running():
            try:
                if os.name == "nt":
                    self.process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    self.process.terminate()
                self.process.wait(timeout=timeout)
            except Exception:
                try:
                    self.process.kill()
                    self.process.wait(timeout=3)
                except Exception:
                    pass
        self._return_code = self.process.returncode
        self._close_log()
        self.process = None

    def _close_log(self) -> None:
        if self.log_fp:
            try:
                self.log_fp.close()
            except Exception:
                pass
            self.log_fp = None

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def return_code(self) -> Optional[int]:
        if self.process is not None:
            return self.process.returncode
        return self._return_code

    def uptime_seconds(self) -> int:
        if self.start_time and self.is_running():
            return int(time.time() - self.start_time)
        return 0

    def get_host_port(self) -> tuple:
        """从启动参数中提取 host 和 port"""
        host, port = "127.0.0.1", 8080
        for i, a in enumerate(self.argv):
            if a == "--host" and i + 1 < len(self.argv):
                host = self.argv[i + 1]
            elif a == "--port" and i + 1 < len(self.argv):
                try:
                    port = int(self.argv[i + 1])
                except ValueError:
                    pass
        return host, port


def wait_until_ready(host: str, port: int, timeout: int = 60, callback=None) -> bool:
    """后台线程用: 轮询 /health 端点, 就绪后回调结果"""
    url = f"http://{host}:{port}/health"
    ok = False
    for _ in range(timeout):
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    ok = True
                    break
        except Exception:
            pass
        time.sleep(1)
    if callback:
        callback(ok)
    return ok
