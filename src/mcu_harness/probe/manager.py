"""长驻探针会话管理器（线程安全）。

目的：
    避免每次 MCP 工具调用都重新 open/connect J-Link 探针，既节省耗时，
    也防止反复握手干扰运行中的目标芯片或 Ozone。
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Callable, Iterator

from .base import DebugProbe, ProbeError, ProbeInfo
from .jlink import JLinkProbe
from .mock import MockProbe


class ProbeManager:
    """管理全局唯一调试探针实例与并发访问锁。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._probe: DebugProbe | None = None
        self._config: dict[str, str | int | None] = {}
        self._disconnect_hooks: list[Callable[[], None]] = []

    def add_disconnect_hook(self, hook: Callable[[], None]) -> None:
        """注册断开连接前的回调（例如先停掉后台 RTT 采集线程）。"""
        with self._lock:
            self._disconnect_hooks.append(hook)

    def _run_disconnect_hooks(self) -> None:
        for hook in self._disconnect_hooks:
            try:
                hook()
            except Exception:
                pass

    def _create_probe(
        self,
        *,
        backend: str | None = None,
        device: str | None = None,
        interface: str = "SWD",
        speed: int | str = "auto",
        serial_no: str | None = None,
    ) -> DebugProbe:
        chosen_backend = (backend or os.environ.get("MCU_HARNESS_BACKEND", "jlink")).lower()
        chosen_device = device or os.environ.get("MCU_HARNESS_DEVICE", "STM32H563ZI")
        if chosen_backend == "mock":
            return MockProbe(device=chosen_device, interface=interface)
        if chosen_backend == "jlink":
            return JLinkProbe(
                device=chosen_device,
                interface=interface,
                speed=speed,
                serial_no=serial_no,
            )
        raise ProbeError(f"Unknown probe backend: {chosen_backend!r}")

    def set_probe(self, probe: DebugProbe | None) -> None:
        """替换当前探针实例（主要用于测试注入 MockProbe）。"""
        with self._lock:
            self._run_disconnect_hooks()
            if self._probe is not None and self._probe is not probe:
                self._probe.close()
            self._probe = probe
            self._config = {}

    def connect(
        self,
        *,
        backend: str | None = None,
        device: str | None = None,
        interface: str = "SWD",
        speed: int | str = "auto",
        serial_no: str | None = None,
        reconnect: bool = False,
    ) -> ProbeInfo:
        with self._lock:
            desired = {
                "backend": (backend or os.environ.get("MCU_HARNESS_BACKEND", "jlink")).lower(),
                "device": device or os.environ.get("MCU_HARNESS_DEVICE", "STM32H563ZI"),
                "interface": interface.upper(),
                "speed": speed,
                "serial_no": serial_no,
            }
            if (
                self._probe is not None
                and self._probe.is_connected()
                and not reconnect
                and (not self._config or self._config == desired)
            ):
                return self._probe.info()

            if self._probe is not None:
                self._run_disconnect_hooks()
                self._probe.close()
            self._probe = self._create_probe(
                backend=backend,
                device=device,
                interface=interface,
                speed=speed,
                serial_no=serial_no,
            )
            self._config = desired
            return self._probe.connect()

    def disconnect(self) -> None:
        with self._lock:
            self._run_disconnect_hooks()
            if self._probe is not None:
                self._probe.close()
                self._probe = None
                self._config = {}

    @contextmanager
    def session(self, *, device: str | None = None) -> Iterator[DebugProbe]:
        """获取已连接的探针句柄（若尚未连接则自动附着连接），全程加锁。"""
        with self._lock:
            if self._probe is None or not self._probe.is_connected():
                self.connect(device=device)
            assert self._probe is not None
            yield self._probe

    @contextmanager
    def session_if_connected(self) -> Iterator[DebugProbe | None]:
        """仅在当前已连接时返回探针句柄，不触发自动重连（供后台轮询线程使用）。"""
        with self._lock:
            if self._probe is not None and self._probe.is_connected():
                yield self._probe
            else:
                yield None


default_probe_manager = ProbeManager()
