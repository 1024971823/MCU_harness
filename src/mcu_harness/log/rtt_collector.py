"""SEGGER RTT 后台非阻塞采集器。

职责：
    * 复用 ProbeManager 的长驻探针连接，起后台线程周期性轮询 probe.rtt_read()。
    * 通过 ProbeManager.session_if_connected() 拿锁读 RTT，避免和读寄存器并发访问 J-Link DLL。
    * 当 ProbeManager 断开或切换探针时，通过 disconnect hook 自动停止采集线程。
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass

from mcu_harness.probe import ProbeError, ProbeManager, default_probe_manager

from .ring_buffer import LogReadResult, RingLogBuffer


@dataclass
class RttStatus:
    running: bool
    channel: int
    block_address: str | None
    bytes_received: int
    total_buffered: int
    oldest_seq: int | None
    newest_seq: int | None
    last_error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class RttLogCollector:
    """后台线程持续读取 RTT 上行通道并写入 RingLogBuffer。"""

    def __init__(
        self,
        probe_manager: ProbeManager,
        *,
        max_lines: int = 2000,
        poll_interval_s: float = 0.02,
    ) -> None:
        self._pm = probe_manager
        self.buffer = RingLogBuffer(max_lines=max_lines)
        self.poll_interval_s = poll_interval_s

        self._ctrl_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._channel = 0
        self._block_address: int | None = None
        self._bytes_received = 0
        self._last_error: str | None = None

        self._pm.add_disconnect_hook(self.stop)

    def is_running(self) -> bool:
        with self._ctrl_lock:
            return self._thread is not None and self._thread.is_alive()

    def status(self) -> RttStatus:
        snap = self.buffer.read(tail_lines=1, include_partial=False)
        with self._ctrl_lock:
            return RttStatus(
                running=self.is_running(),
                channel=self._channel,
                block_address=f"0x{self._block_address:08X}" if self._block_address is not None else None,
                bytes_received=self._bytes_received,
                total_buffered=snap.total_buffered,
                oldest_seq=snap.oldest_seq,
                newest_seq=snap.newest_seq,
                last_error=self._last_error,
            )

    def start(
        self,
        *,
        channel: int = 0,
        block_address: int | None = None,
        device: str | None = None,
    ) -> RttStatus:
        """启动后台 RTT 采集；若已在相同通道运行则直接返回当前状态。"""
        with self._ctrl_lock:
            if self.is_running():
                if self._channel == channel and self._block_address == block_address:
                    return self.status()
                self.stop()

            with self._pm.session(device=device) as probe:
                probe.rtt_start(block_address=block_address)

            self._channel = channel
            self._block_address = block_address
            self._last_error = None
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._poll_loop,
                name=f"rtt-collector-ch{channel}",
                daemon=True,
            )
            self._thread.start()
            return self.status()

    def stop(self) -> RttStatus:
        """停止后台 RTT 采集线程，并尽量关闭目标探针的 RTT 会话。"""
        with self._ctrl_lock:
            thread = self._thread
            self._stop_event.set()
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=1.5)
            self._thread = None

            try:
                with self._pm.session_if_connected() as probe:
                    if probe is not None:
                        probe.rtt_stop()
            except ProbeError:
                pass
            return self.status()

    def clear(self) -> int:
        return self.buffer.clear()

    def read(
        self,
        *,
        tail_lines: int = 50,
        since_seq: int | None = None,
        pattern: str | None = None,
        regex: bool = False,
        case_sensitive: bool = False,
        context_lines: int = 2,
        include_partial: bool = True,
        auto_start: bool = True,
        channel: int = 0,
        block_address: int | None = None,
        device: str | None = None,
    ) -> tuple[LogReadResult, RttStatus]:
        """读取最新日志（若尚未采集且 auto_start=True，则自动启动 RTT 并短暂抓取首批数据）。"""
        if auto_start and (not self.is_running() or channel != self._channel or block_address != self._block_address):
            self.start(channel=channel, block_address=block_address, device=device)
            # 刚启动 RTT 时给后台线程一个极短窗口把缓冲区已有积压拉进来
            self._stop_event.wait(0.05)

        result = self.buffer.read(
            tail_lines=tail_lines,
            since_seq=since_seq,
            pattern=pattern,
            regex=regex,
            case_sensitive=case_sensitive,
            context_lines=context_lines,
            include_partial=include_partial,
        )
        return result, self.status()

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            got_full_chunk = False
            delay = self.poll_interval_s

            try:
                with self._pm.session_if_connected() as probe:
                    if probe is None:
                        break
                    chunk = probe.rtt_read(channel=self._channel, max_bytes=4096)
                if chunk:
                    self.buffer.feed_bytes(chunk)
                    with self._ctrl_lock:
                        self._bytes_received += len(chunk)
                        self._last_error = None
                    got_full_chunk = len(chunk) >= 4096
            except ProbeError as e:
                # 例如固件刚复位还没跑到 SEGGER_RTT_Init()，记录错误并稍微退避，不直接退出线程
                with self._ctrl_lock:
                    self._last_error = str(e)
                delay = 0.1

            if not got_full_chunk:
                self._stop_event.wait(delay)


default_rtt_collector = RttLogCollector(default_probe_manager)
