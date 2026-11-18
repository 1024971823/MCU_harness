"""内存模拟探针（用于无板卡单元测试与离线演示）。"""

from __future__ import annotations

import threading

from .base import DebugProbe, ProbeError, ProbeInfo


class MockProbe(DebugProbe):
    """基于字典内存映射的模拟探针，默认预置 Cortex-M33 CPUID 与示例寄存器。"""

    def __init__(
        self,
        *,
        device: str = "STM32H563ZI",
        interface: str = "SWD",
        memory_words: dict[int, int] | None = None,
        halted: bool = False,
    ) -> None:
        self.device = device
        self.interface = interface
        self._connected = False
        self._halted = halted
        # 以字节粒度存储，方便 8/16/32 位统一读取（小端序）
        self._mem: dict[int, int] = {}
        self._rtt_lock = threading.Lock()
        self._rtt_started = False
        self._rtt_buffers: dict[int, bytearray] = {}
        # 默认写入 Cortex-M33 CPUID (0xE000ED00) 与测试用 GPIOA/GPIOB
        defaults = {
            0xE000ED00: 0x410FD210,  # Cortex-M33 CPUID
            0x42020000: 0xAB000400,  # GPIOA->MODER (pin5 Output)
            0x42020014: 0x00000020,  # GPIOA->ODR   (pin5 High)
            0x42020400: 0xABFFFFFF,  # GPIOB->MODER (reset)
        }
        for addr, val in defaults.items():
            self.write_u32(addr, val)
        if memory_words:
            for addr, val in memory_words.items():
                self.write_u32(addr, val)

    def write_u32(self, address: int, value: int) -> None:
        raw = (value & 0xFFFFFFFF).to_bytes(4, "little")
        for i, b in enumerate(raw):
            self._mem[address + i] = b

    def connect(self) -> ProbeInfo:
        self._connected = True
        return self.info()

    def close(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def info(self) -> ProbeInfo:
        return ProbeInfo(
            backend="mock",
            device=self.device,
            interface=self.interface,
            connected=self._connected,
            halted=self._halted if self._connected else None,
            serial_no="MOCK-0001",
            core_id="0x410FD210",
            core_name="Cortex-M33 (mock)",
            speed_khz=4000,
        )

    def _require_conn(self) -> None:
        if not self._connected:
            raise ProbeError("MockProbe is not connected")

    def read_u32(self, address: int, count: int = 1) -> list[int]:
        self._require_conn()
        if address % 4 != 0:
            raise ProbeError(f"Unaligned 32-bit read at 0x{address:08X}")
        if count <= 0:
            raise ProbeError(f"count must be > 0, got {count}")
        out: list[int] = []
        for i in range(count):
            base = address + i * 4
            b = bytes(self._mem.get(base + k, 0) for k in range(4))
            out.append(int.from_bytes(b, "little"))
        return out

    def read_u16(self, address: int, count: int = 1) -> list[int]:
        self._require_conn()
        if address % 2 != 0:
            raise ProbeError(f"Unaligned 16-bit read at 0x{address:08X}")
        if count <= 0:
            raise ProbeError(f"count must be > 0, got {count}")
        out: list[int] = []
        for i in range(count):
            base = address + i * 2
            b = bytes(self._mem.get(base + k, 0) for k in range(2))
            out.append(int.from_bytes(b, "little"))
        return out

    def read_u8(self, address: int, count: int = 1) -> list[int]:
        self._require_conn()
        if count <= 0:
            raise ProbeError(f"count must be > 0, got {count}")
        return [self._mem.get(address + i, 0) for i in range(count)]

    def halt(self) -> bool:
        self._require_conn()
        was_halted = self._halted
        self._halted = True
        return not was_halted

    def resume(self) -> bool:
        self._require_conn()
        was_halted = self._halted
        self._halted = False
        return was_halted

    def is_halted(self) -> bool:
        self._require_conn()
        return self._halted

    # ---- 模拟 SEGGER RTT -------------------------------------------------

    def push_rtt(self, data: bytes | str, channel: int = 0) -> None:
        """向模拟 RTT 上行通道注入日志数据，供后台采集线程读取。"""
        raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        with self._rtt_lock:
            buf = self._rtt_buffers.setdefault(channel, bytearray())
            buf.extend(raw)

    def rtt_start(self, block_address: int | None = None) -> None:
        self._require_conn()
        self._rtt_started = True

    def rtt_stop(self) -> None:
        self._rtt_started = False

    def rtt_read(self, channel: int = 0, max_bytes: int = 4096) -> bytes:
        self._require_conn()
        if not self._rtt_started:
            raise ProbeError("Mock RTT is not started")
        with self._rtt_lock:
            buf = self._rtt_buffers.get(channel)
            if not buf:
                return b""
            chunk = bytes(buf[:max_bytes])
            del buf[:max_bytes]
            return chunk
