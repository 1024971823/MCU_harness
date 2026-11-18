"""SEGGER RTT 后台非阻塞采集器。

职责与并发约束（FIX-02 / FIX-05）：
    * 全局单向锁层级：RttLogCollector._ctrl_lock 始终位于 ProbeManager._lock 外层。
    * stop() 仅在 _ctrl_lock 临界区内置位 _stop_event 并摘除线程引用，
      释放 _ctrl_lock 后再执行 thread.join() 与底层 rtt_stop()，严禁持锁等待线程退出。
    * read(auto_start=True) 的运行态检查与自动启动完全收敛在 _ctrl_lock 内原子执行，消除多线程重复启动竞态。
    * _poll_loop 捕获全部异常（含非预期运行时异常）并写入 _last_error，保证后台线程不静默退出。
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict, dataclass

from mcu_harness.elf import ElfError, find_symbol, read_elf_symbols
from mcu_harness.probe import ProbeError, ProbeManager, default_probe_manager

from .ring_buffer import LogReadResult, RingLogBuffer


def resolve_rtt_block_address(
    *,
    block_address: int | None = None,
    elf_path: str | os.PathLike | None = None,
) -> int | None:
    """解析 RTT 控制块地址：显式地址 > elf_path > MCU_HARNESS_ELF_PATH > 自动扫描。"""
    if block_address is not None:
        return block_address

    resolved_elf = elf_path or os.environ.get("MCU_HARNESS_ELF_PATH")
    if not resolved_elf:
        return None

    try:
        sym = find_symbol(resolved_elf, "_SEGGER_RTT")
        if sym is not None:
            return sym.address
        # 若 .elf 带符号表却找不到 _SEGGER_RTT，大概率固件没有启用 RTT；剥离符号表的则回退自动扫描
        if read_elf_symbols(resolved_elf):
            raise ProbeError(f"Symbol '_SEGGER_RTT' not found in {resolved_elf} (is SEGGER RTT compiled in?)")
        return None
    except (FileNotFoundError, ElfError) as e:
        raise ProbeError(str(e)) from e


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
            running = self._thread is not None and self._thread.is_alive()
            return RttStatus(
                running=running,
                channel=self._channel,
                block_address=f"0x{self._block_address:08X}" if self._block_address is not None else None,
                bytes_received=self._bytes_received,
                total_buffered=snap.total_buffered,
                oldest_seq=snap.oldest_seq,
                newest_seq=snap.newest_seq,
                last_error=self._last_error,
            )

    def _start_or_reuse(
        self,
        *,
        channel: int = 0,
        block_address: int | None = None,
        elf_path: str | os.PathLike | None = None,
        device: str | None = None,
    ) -> tuple[RttStatus, bool]:
        """原子检查或启动 RTT 采集；返回 (状态, 本次是否新建了采集会话)。

        锁序：_ctrl_lock（外） -> ProbeManager._lock（内）。旧线程的 join 一律在锁外进行。
        """
        resolved_addr = resolve_rtt_block_address(block_address=block_address, elf_path=elf_path)

        while True:
            old_thread: threading.Thread | None = None
            with self._ctrl_lock:
                if self._thread is not None and self._thread.is_alive():
                    if self._channel == channel and self._block_address == resolved_addr:
                        return self.status(), False
                    # 通道或地址变化：临界区内只摘除旧线程并通知其退出，锁外 join
                    old_thread = self._thread
                    self._thread = None
                    self._stop_event.set()
                else:
                    with self._pm.session(device=device) as probe:
                        probe.rtt_start(block_address=resolved_addr)

                    self._channel = channel
                    self._block_address = resolved_addr
                    self._last_error = None
                    # 每个采集线程使用独立的停止事件：即便旧线程 join 超时尚未退出，
                    # 新线程的 clear() 也不会把旧线程“复活”
                    stop_event = threading.Event()
                    self._stop_event = stop_event
                    self._thread = threading.Thread(
                        target=self._poll_loop,
                        args=(stop_event, channel),
                        name=f"rtt-collector-ch{channel}",
                        daemon=True,
                    )
                    self._thread.start()
                    return self.status(), True

            if old_thread is not None and old_thread is not threading.current_thread():
                old_thread.join(timeout=1.5)
            try:
                with self._pm.session_if_connected() as probe:
                    if probe is not None:
                        probe.rtt_stop()
            except Exception:
                pass

    def start(
        self,
        *,
        channel: int = 0,
        block_address: int | None = None,
        elf_path: str | os.PathLike | None = None,
        device: str | None = None,
    ) -> RttStatus:
        """启动后台 RTT 采集；若已在相同通道运行则直接返回当前状态。"""
        st, _ = self._start_or_reuse(
            channel=channel,
            block_address=block_address,
            elf_path=elf_path,
            device=device,
        )
        return st

    def stop(self) -> RttStatus:
        """停止后台 RTT 采集线程（锁外等待线程退出），并关闭目标探针的 RTT 会话。"""
        with self._ctrl_lock:
            thread = self._thread
            self._thread = None
            self._stop_event.set()

        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.5)

        try:
            with self._pm.session_if_connected() as probe:
                if probe is not None:
                    probe.rtt_stop()
        except Exception:
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
        elf_path: str | os.PathLike | None = None,
        device: str | None = None,
    ) -> tuple[LogReadResult, RttStatus]:
        """读取最新日志（若尚未采集且 auto_start=True，则原子启动 RTT 并短暂抓取首批数据）。"""
        if auto_start:
            _, newly_started = self._start_or_reuse(
                channel=channel,
                block_address=block_address,
                elf_path=elf_path,
                device=device,
            )
            if newly_started:
                # 给后台线程一个短暂窗口，把目标 RTT 缓冲区里已有的积压拉进来
                time.sleep(0.15)

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

    def _poll_loop(self, stop_event: threading.Event, channel: int) -> None:
        while not stop_event.is_set():
            got_full_chunk = False
            delay = self.poll_interval_s

            try:
                with self._pm.session_if_connected() as probe:
                    if probe is None:
                        break  # 探针已断开：采集线程随之退出
                    chunk = probe.rtt_read(channel=channel, max_bytes=4096)
                if chunk and not stop_event.is_set():
                    self.buffer.feed_bytes(chunk)
                    with self._ctrl_lock:
                        self._bytes_received += len(chunk)
                        self._last_error = None
                    got_full_chunk = len(chunk) >= 4096
            except ProbeError as e:
                # 例如固件刚复位还没跑到 SEGGER_RTT_Init()：记录错误、退避，不退出线程
                with self._ctrl_lock:
                    self._last_error = str(e)
                delay = 0.1
            except Exception as e:
                # 通用异常兜底（FIX-05）：记录异常类名与信息，退避后继续保持轮询存活
                with self._ctrl_lock:
                    self._last_error = f"{type(e).__name__}: {e}"
                delay = 0.1

            if not got_full_chunk:
                stop_event.wait(delay)


default_rtt_collector = RttLogCollector(default_probe_manager)
