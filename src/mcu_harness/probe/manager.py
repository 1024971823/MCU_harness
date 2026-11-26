"""长驻探针会话管理器（线程安全，支持 J-Link / OpenOCD / Mock 多后端）。

设计要点（FIX-02 / FIX-03）：
    * 全局单向锁层级：外部模块锁（如 RttLogCollector._ctrl_lock）永远在 ProbeManager._lock 外层。
    * 严禁在持有 ProbeManager._lock 期间调用断连回调（disconnect hooks）或阻塞关闭旧探针；
      临界区内仅摘除引用，释放锁后再执行回调与底层 close()。
    * 连接失败时不残留未就绪的脏实例或期望配置。
    * session(device=..., backend=...) 会校验目标芯片型号与后端一致性，切换时自动触发重连。
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Callable, Iterator

from .base import DebugProbe, ProbeError, ProbeInfo
from .jlink import (
    JLinkProbe,
    normalize_device,
    normalize_interface,
    normalize_serial_no,
    normalize_speed,
)
from .mock import MockProbe
from .openocd import OpenOCDProbe


def _normalize_desired_config(
    *,
    backend: str | None = None,
    device: str | None = None,
    interface: str | None = None,
    speed: int | str | None = None,
    serial_no: int | str | None = None,
    openocd_interface: str | None = None,
    openocd_target: str | None = None,
    openocd_tcl_port: int | None = None,
) -> dict[str, str | int | None]:
    chosen_backend = (backend or os.environ.get("MCU_HARNESS_BACKEND") or "jlink").strip().lower()
    if chosen_backend not in ("jlink", "openocd", "mock"):
        raise ProbeError(f"Unknown probe backend: {chosen_backend!r} (expected jlink, openocd, or mock)")

    norm_sn: int | str | None
    if chosen_backend == "jlink":
        norm_sn = normalize_serial_no(serial_no)
    else:
        raw_sn = serial_no if serial_no not in (None, "") else os.environ.get("MCU_HARNESS_SERIAL")
        norm_sn = str(raw_sn).strip() if raw_sn not in (None, "") else None

    return {
        "backend": chosen_backend,
        "device": normalize_device(device),
        "interface": normalize_interface(interface),
        "speed": normalize_speed(speed),
        "serial_no": norm_sn,
        "openocd_interface": (openocd_interface or os.environ.get("MCU_HARNESS_OPENOCD_INTERFACE") or "cmsis-dap").strip(),
        "openocd_target": (openocd_target or os.environ.get("MCU_HARNESS_OPENOCD_TARGET") or "").strip() or None,
        "openocd_tcl_port": int(openocd_tcl_port or os.environ.get("MCU_HARNESS_OPENOCD_PORT") or 6666),
    }


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

    @staticmethod
    def _cleanup_outside_lock(old_probe: DebugProbe | None, hooks: list[Callable[[], None]]) -> None:
        for hook in hooks:
            try:
                hook()
            except Exception:
                pass
        if old_probe is not None:
            try:
                old_probe.close()
            except Exception:
                pass

    def _create_probe(self, cfg: dict[str, str | int | None]) -> DebugProbe:
        backend = str(cfg["backend"])
        device = str(cfg["device"])
        interface = str(cfg["interface"])
        speed = cfg["speed"] if cfg["speed"] is not None else "auto"
        serial_no = cfg["serial_no"]
        if backend == "mock":
            return MockProbe(device=device, interface=interface)
        if backend == "jlink":
            return JLinkProbe(
                device=device,
                interface=interface,
                speed=speed,
                serial_no=serial_no,
            )
        if backend == "openocd":
            return OpenOCDProbe(
                device=device,
                interface=interface,
                speed=speed,
                serial_no=serial_no,
                openocd_interface=str(cfg["openocd_interface"]) if cfg.get("openocd_interface") else None,
                openocd_target=str(cfg["openocd_target"]) if cfg.get("openocd_target") else None,
                tcl_port=int(cfg["openocd_tcl_port"]) if cfg.get("openocd_tcl_port") else 6666,
            )
        raise ProbeError(f"Unknown probe backend: {backend!r}")

    def set_probe(self, probe: DebugProbe | None) -> None:
        """替换当前探针实例（主要用于测试注入 MockProbe），锁外执行旧实例清理。"""
        with self._lock:
            old_probe = self._probe if self._probe is not probe else None
            hooks = list(self._disconnect_hooks) if old_probe is not None else []
            self._probe = probe
            self._config = {}
        if old_probe is not None:
            self._cleanup_outside_lock(old_probe, hooks)

    def connect(
        self,
        *,
        backend: str | None = None,
        device: str | None = None,
        interface: str | None = None,
        speed: int | str | None = None,
        serial_no: int | str | None = None,
        openocd_interface: str | None = None,
        openocd_target: str | None = None,
        openocd_tcl_port: int | None = None,
        reconnect: bool = False,
    ) -> ProbeInfo:
        desired = _normalize_desired_config(
            backend=backend,
            device=device,
            interface=interface,
            speed=speed,
            serial_no=serial_no,
            openocd_interface=openocd_interface,
            openocd_target=openocd_target,
            openocd_tcl_port=openocd_tcl_port,
        )

        old_probe: DebugProbe | None = None
        hooks: list[Callable[[], None]] = []
        with self._lock:
            if (
                self._probe is not None
                and self._probe.is_connected()
                and not reconnect
                and (not self._config or self._config == desired)
            ):
                return self._probe.info()

            if self._probe is not None:
                old_probe = self._probe
                hooks = list(self._disconnect_hooks)
                self._probe = None
                self._config = {}

        if old_probe is not None:
            self._cleanup_outside_lock(old_probe, hooks)

        with self._lock:
            if (
                self._probe is not None
                and self._probe.is_connected()
                and not reconnect
                and (not self._config or self._config == desired)
            ):
                return self._probe.info()

            candidate = self._create_probe(desired)
            try:
                info = candidate.connect()
            except Exception:
                try:
                    candidate.close()
                except Exception:
                    pass
                self._probe = None
                self._config = {}
                raise

            self._probe = candidate
            self._config = desired
            return info

    def disconnect(self) -> None:
        with self._lock:
            old_probe = self._probe
            hooks = list(self._disconnect_hooks) if old_probe is not None else []
            self._probe = None
            self._config = {}
        if old_probe is not None:
            self._cleanup_outside_lock(old_probe, hooks)

    _SESSION_CONNECT_ATTEMPTS = 3

    @contextmanager
    def session(
        self,
        *,
        device: str | None = None,
        backend: str | None = None,
        openocd_interface: str | None = None,
        openocd_target: str | None = None,
    ) -> Iterator[DebugProbe]:
        """获取已连接的探针句柄（未连接或切换芯片型号/后端时先在锁外完成连接/重连）。"""
        target_dev = normalize_device(device) if isinstance(device, str) and device.strip() else None
        target_backend = backend.strip().lower() if isinstance(backend, str) and backend.strip() else None

        for _ in range(self._SESSION_CONNECT_ATTEMPTS):
            need_reconnect = False
            prev_cfg: dict[str, str | int | None] = {}
            with self._lock:
                probe = self._probe
                connected = probe is not None and probe.is_connected()
                prev_cfg = dict(self._config)
                cur_dev = prev_cfg.get("device")
                cur_backend = prev_cfg.get("backend")
                dev_ok = target_dev is None or cur_dev in (None, target_dev)
                backend_ok = target_backend is None or cur_backend in (None, target_backend)
                if connected and dev_ok and backend_ok:
                    yield probe
                    return
                need_reconnect = connected
            self.connect(
                backend=target_backend or (str(prev_cfg["backend"]) if prev_cfg.get("backend") else None),
                device=target_dev or (str(prev_cfg["device"]) if prev_cfg.get("device") else None),
                interface=str(prev_cfg["interface"]) if prev_cfg.get("interface") else None,
                speed=prev_cfg.get("speed"),
                serial_no=prev_cfg.get("serial_no"),
                openocd_interface=openocd_interface or (str(prev_cfg["openocd_interface"]) if prev_cfg.get("openocd_interface") else None),
                openocd_target=openocd_target or (str(prev_cfg["openocd_target"]) if prev_cfg.get("openocd_target") else None),
                openocd_tcl_port=int(prev_cfg["openocd_tcl_port"]) if prev_cfg.get("openocd_tcl_port") else None,
                reconnect=need_reconnect,
            )

        raise ProbeError("Probe session unstable: connection was lost repeatedly while acquiring session")

    @contextmanager
    def session_if_connected(self) -> Iterator[DebugProbe | None]:
        """仅在当前已连接时返回探针句柄，不触发自动重连（供后台轮询线程使用）。"""
        with self._lock:
            if self._probe is not None and self._probe.is_connected():
                yield self._probe
            else:
                yield None


default_probe_manager = ProbeManager()
