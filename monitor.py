# -*- coding: utf-8 -*-
"""GPU/RAM 实时监控（nvidia-smi + psutil 后台线程）"""

import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional

try:
    import psutil
except ImportError:
    psutil = None


@dataclass
class MonitorSnapshot:
    """监控数据快照"""
    gpu_name: str = ""
    vram_total_mb: float = 0
    vram_used_mb: float = 0
    gpu_util_pct: float = 0
    gpu_temp_c: float = 0
    ram_total_gb: float = 0
    ram_used_gb: float = 0
    ram_pct: float = 0
    error: Optional[str] = None


class MonitorWorker:
    """后台线程，定期采集 GPU/RAM 数据"""

    def __init__(self, interval_ms: int = 1000):
        self.interval_s = interval_ms / 1000.0
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._latest = MonitorSnapshot()
        self._lock = threading.Lock()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def _loop(self):
        while not self._stop_event.is_set():
            snap = self._collect()
            with self._lock:
                self._latest = snap
            self._stop_event.wait(self.interval_s)

    def _collect(self) -> MonitorSnapshot:
        snap = MonitorSnapshot()

        # GPU via nvidia-smi
        try:
            out = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                timeout=5,
                text=True,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            ).strip()
            parts = [p.strip() for p in out.split(",")]
            if len(parts) >= 5:
                snap.gpu_name = parts[0]
                snap.vram_total_mb = float(parts[1])
                snap.vram_used_mb = float(parts[2])
                snap.gpu_util_pct = float(parts[3])
                snap.gpu_temp_c = float(parts[4])
        except FileNotFoundError:
            snap.error = "nvidia-smi 未找到"
        except Exception as e:
            snap.error = f"nvidia-smi: {e}"

        # RAM via psutil
        if psutil:
            try:
                vm = psutil.virtual_memory()
                snap.ram_total_gb = vm.total / (1024 ** 3)
                snap.ram_used_gb = vm.used / (1024 ** 3)
                snap.ram_pct = vm.percent
            except Exception as e:
                err = f"psutil: {e}"
                snap.error = f"{snap.error}; {err}" if snap.error else err

        return snap

    def latest(self) -> MonitorSnapshot:
        with self._lock:
            return self._latest
