# -*- coding: utf-8 -*-
"""llama-server 子进程的启动 / 停止 / 状态查询 / 就绪轮询。"""

import errno
import os
import signal
import socket
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
        """停止进程(兼容旧调用点): 内部走 unload, 不关心是否确认退出。"""
        self.unload(timeout=timeout)

    def unload(self, timeout: float = 0.8, force_timeout: float = 5.0) -> bool:
        """卸载: 优雅停止 → 强杀兜底 → 确认进程真的退出。返回是否确认已退出。

        只有确认退出才丢弃 self.process 引用。否则保留引用, 让 is_running() 继续为真,
        调用方(重启/退出)才有机会发现"进程还活着"而不是误以为卸载成功——
        残留的 llama-server 会一直占着端口和显存。
        """
        p = self.process
        if p is None:
            return True

        if p.poll() is None:
            # 1) 优雅退出: Windows 走 CTRL_BREAK(等价于用户按 Ctrl+C)
            try:
                if os.name == "nt":
                    p.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    p.terminate()
            except Exception:
                pass
            # 轮询而不是 wait(timeout): 本程序的子进程是 CREATE_NO_WINDOW 起的,
            # 没有控制台可投递 CTRL_BREAK, 死等满超时纯属白等, 所以用短超时轮询。
            deadline = time.time() + timeout
            while time.time() < deadline and p.poll() is None:
                time.sleep(0.05)

        # 2) 兜底强杀
        if p.poll() is None:
            try:
                p.kill()
                p.wait(timeout=force_timeout)
            except Exception:
                pass

        exited = p.poll() is not None
        if exited:
            self._return_code = p.returncode
            self._close_log()
            self.process = None
        return exited

    def pid(self) -> Optional[int]:
        return self.process.pid if self.process is not None else None

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


def _port_bindable(port: int) -> bool:
    """端口当前能否被独占绑定(= 没有其它进程在监听)。

    Windows 实测两个坑, 缺一就会误判"已释放":
    1. 普通 bind 会"抢绑"成功——旧套接字还监听着也能绑上, 必须设 SO_EXCLUSIVEADDRUSE
       (实测: 旧套接字占 127.0.0.1:port 时, 普通 bind 0.0.0.0:port 照样成功, 加 EXCLUSIVE 后报 10048);
    2. 绑 0.0.0.0 覆盖不了"只绑某个具体地址"的旧实例, 所以 IPv4/IPv6 通配各探一次。
    其它 bind 错误(如系统禁用 IPv6)不代表端口被占用, 忽略。
    """
    families = [(socket.AF_INET, "0.0.0.0")]
    if socket.has_ipv6:
        families.append((socket.AF_INET6, "::"))
    for fam, addr in families:
        s = socket.socket(fam, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            s.bind((addr, port))
        except OSError as e:
            if getattr(e, "winerror", None) == 10048 or e.errno == errno.EADDRINUSE:
                return False
        finally:
            s.close()
    return True


def wait_port_released(host: str, port: int, timeout: float = 10.0) -> bool:
    """后台线程用: 等监听端口彻底释放后再拉起新进程。

    进程退出到端口真正可再次绑定之间有间隙(套接字关闭 + 显存回收),
    直接重启容易撞上 "address already in use" 或新进程握不到显存。
    host 仅作记录, 探测走 0.0.0.0(见 _port_bindable)。
    """
    deadline = time.time() + timeout
    while True:
        if _port_bindable(port):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.25)


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
