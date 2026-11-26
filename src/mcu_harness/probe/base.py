"""调试探针抽象层（相当于嵌入式工程里的驱动接口头文件）。

上层工具（inspect_peripheral / read_memory / write_memory / flash_firmware / diagnose_crash / RTT）
只依赖 DebugProbe 接口，不直接耦合具体探针后端（J-Link / OpenOCD / Mock）。
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass


class ProbeError(RuntimeError):
    """调试探针通信或参数错误。"""


@dataclass
class ProbeInfo:
    """已连接探针与目标芯片的状态快照。"""

    backend: str  # "jlink" | "openocd" | "mock"
    device: str
    interface: str  # "SWD" | "JTAG"
    connected: bool
    halted: bool | None = None
    serial_no: str | None = None
    core_id: str | None = None
    core_name: str | None = None
    speed_khz: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResetResult:
    """复位操作结果快照。"""

    halted: bool
    pc: str | None = None
    sp: str | None = None
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FlashResult:
    """固件烧录结果快照。"""

    success: bool
    firmware_path: str
    bytes_written: int | None = None
    address: str | None = None
    verified: bool = False
    reset_performed: bool = False
    running: bool = False
    duration_s: float = 0.0
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class DebugProbe(ABC):
    """硬件调试探针统一抽象接口。"""

    @abstractmethod
    def connect(self) -> ProbeInfo:
        """连接探针并附着到目标芯片（默认不复位，保留现场）。"""

    @abstractmethod
    def close(self) -> None:
        """断开与探针的连接并释放资源。"""

    @abstractmethod
    def is_connected(self) -> bool:
        """当前是否处于已连接状态。"""

    @abstractmethod
    def info(self) -> ProbeInfo:
        """获取当前探针与目标内核信息。"""

    @abstractmethod
    def read_u32(self, address: int, count: int = 1) -> list[int]:
        """按 32 位小端序读取连续内存/寄存器（地址需 4 字节对齐）。"""

    @abstractmethod
    def read_u16(self, address: int, count: int = 1) -> list[int]:
        """按 16 位读取连续内存/寄存器（地址需 2 字节对齐）。"""

    @abstractmethod
    def read_u8(self, address: int, count: int = 1) -> list[int]:
        """按字节读取连续内存。"""

    def write_u32(self, address: int, values: list[int]) -> None:
        """按 32 位小端序写入连续内存/寄存器（地址需 4 字节对齐）。"""
        raise ProbeError(f"{type(self).__name__} does not implement write_u32")

    def write_u16(self, address: int, values: list[int]) -> None:
        """按 16 位小端序写入连续内存/寄存器（地址需 2 字节对齐）。"""
        raise ProbeError(f"{type(self).__name__} does not implement write_u16")

    def write_u8(self, address: int, values: list[int]) -> None:
        """按字节写入连续内存。"""
        raise ProbeError(f"{type(self).__name__} does not implement write_u8")

    @abstractmethod
    def halt(self) -> bool:
        """暂停目标 CPU。"""

    @abstractmethod
    def resume(self) -> bool:
        """恢复目标 CPU 运行。"""

    @abstractmethod
    def is_halted(self) -> bool:
        """查询目标 CPU 当前是否处于暂停状态。"""

    def read_core_regs(self, names: list[str] | None = None) -> dict[str, int]:
        """读取 CPU 核心寄存器（如 r0..r12, sp, lr, pc, xpsr, msp, psp, msplim, psplim）。"""
        raise ProbeError(f"{type(self).__name__} does not implement read_core_regs")

    def reset(self, *, halt: bool = False) -> ResetResult:
        """复位目标芯片（halt=True 复位后停在入口，halt=False 复位后立即运行）。"""
        raise ProbeError(f"{type(self).__name__} does not implement reset")

    def flash(
        self,
        firmware_path: str | os.PathLike,
        *,
        address: int | None = None,
        verify: bool = True,
        reset_after: bool = True,
        run_after: bool = True,
    ) -> FlashResult:
        """烧录 .elf / .hex / .bin 固件到目标芯片 Flash。"""
        raise ProbeError(f"{type(self).__name__} does not implement flash")

    def read_memory(self, address: int, num_bytes: int) -> bytes:
        """按字节块读取任意长度内存。"""
        if num_bytes < 0:
            raise ProbeError(f"num_bytes must be >= 0, got {num_bytes}")
        if num_bytes == 0:
            return b""
        return bytes(self.read_u8(address, num_bytes))

    def write_memory(self, address: int, data: bytes) -> None:
        """按字节块写入任意长度内存。"""
        if not data:
            return
        self.write_u8(address, list(data))

    def read_register_sized(self, address: int, size_bits: int = 32) -> int:
        """按寄存器位宽（8 / 16 / 32）读取单个外设寄存器值。"""
        if size_bits == 32:
            return self.read_u32(address, 1)[0]
        if size_bits == 16:
            return self.read_u16(address, 1)[0]
        if size_bits == 8:
            return self.read_u8(address, 1)[0]
        raise ProbeError(f"Unsupported register width: {size_bits} bits")

    # ---- 可选 RTT 通道（默认不支持，由 JLinkProbe / OpenOCDProbe / MockProbe 覆写） ------

    def rtt_start(self, block_address: int | None = None) -> None:
        """启动 RTT 通道采集（block_address 可指定控制块地址，默认自动扫描 RAM）。"""
        raise ProbeError(f"{type(self).__name__} does not support RTT")

    def rtt_stop(self) -> None:
        """停止 RTT 通道采集。"""
        raise ProbeError(f"{type(self).__name__} does not support RTT")

    def rtt_read(self, channel: int = 0, max_bytes: int = 4096) -> bytes:
        """非阻塞读取 RTT 上行缓冲区中当前可用的字节。"""
        raise ProbeError(f"{type(self).__name__} does not support RTT")

    def rtt_write(self, data: bytes, channel: int = 0) -> int:
        """向 RTT 下行通道写入命令字节，返回实际写入长度。"""
        raise ProbeError(f"{type(self).__name__} does not support RTT write")

    def __enter__(self) -> DebugProbe:
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
