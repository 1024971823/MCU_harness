"""内存模拟探针（用于无板卡单元测试、Crash 故障注入演示与离线闭环验证）。"""

from __future__ import annotations

import os
import struct
import threading
import time
from pathlib import Path

from .base import DebugProbe, FlashResult, ProbeError, ProbeInfo, ResetResult

_DEFAULT_CORE_REGS: dict[str, int] = {
    "r0": 0x00000000,
    "r1": 0x00000000,
    "r2": 0x00000000,
    "r3": 0x00000000,
    "r4": 0x00000000,
    "r5": 0x00000000,
    "r6": 0x00000000,
    "r7": 0x00000000,
    "r8": 0x00000000,
    "r9": 0x00000000,
    "r10": 0x00000000,
    "r11": 0x00000000,
    "r12": 0x00000000,
    "sp": 0x20020000,
    "lr": 0x08000101,
    "pc": 0x08000200,
    "xpsr": 0x01000000,
    "msp": 0x20020000,
    "psp": 0x20018000,
    "primask": 0,
    "basepri": 0,
    "faultmask": 0,
    "control": 0,
    "msplim": 0x20000000,
    "psplim": 0x20010000,
}


class MockProbe(DebugProbe):
    """基于字典内存映射的模拟探针，默认预置 Cortex-M33 CPUID、示例寄存器与完整读写/烧录模拟。"""

    def __init__(
        self,
        *,
        device: str = "STM32H563ZI",
        interface: str = "SWD",
        memory_words: dict[int, int] | None = None,
        core_regs: dict[str, int] | None = None,
        halted: bool = False,
    ) -> None:
        self.device = device
        self.interface = interface
        self._connected = False
        self._halted = halted
        # 以字节粒度存储，方便 8/16/32 位统一读写（小端序）
        self._mem: dict[int, int] = {}
        self._regs: dict[str, int] = dict(_DEFAULT_CORE_REGS)
        if core_regs:
            for k, v in core_regs.items():
                self._regs[k.lower()] = v & 0xFFFFFFFF

        self._rtt_lock = threading.Lock()
        self._rtt_started = False
        self._rtt_buffers: dict[int, bytearray] = {}
        self._rtt_down_buffers: dict[int, bytearray] = {}
        self.flashed_images: list[tuple[str, int, int]] = []  # (path, addr, size)

        # 默认写入 Cortex-M33 CPUID (0xE000ED00) 与测试用 GPIOA/GPIOB
        defaults = {
            0xE000ED00: 0x410FD210,  # Cortex-M33 CPUID
            0x42020000: 0xAB000400,  # GPIOA->MODER (pin5 Output)
            0x42020014: 0x00000020,  # GPIOA->ODR   (pin5 High)
            0x42020400: 0xABFFFFFF,  # GPIOB->MODER (reset)
        }
        for addr, val in defaults.items():
            self._put_u32_raw(addr, val)
        if memory_words:
            for addr, val in memory_words.items():
                self._put_u32_raw(addr, val)

    def _put_u32_raw(self, address: int, value: int) -> None:
        raw = (value & 0xFFFFFFFF).to_bytes(4, "little")
        for i, b in enumerate(raw):
            self._mem[address + i] = b

    def set_word(self, address: int, value: int) -> None:
        """测试辅助：不检查探针连接态直接预置一个 32 位内存字。"""
        self._put_u32_raw(address, value)

    def set_core_reg(self, name: str, value: int) -> None:
        """测试辅助：设置单个模拟核心寄存器。"""
        key = name.strip().lower()
        self._regs[key] = value & 0xFFFFFFFF
        if key == "msp" and (self._regs.get("control", 0) & 0x2) == 0:
            self._regs["sp"] = self._regs["msp"]

    def inject_cortex_m_fault(
        self,
        *,
        cfsr: int = 0x00008200,  # BFSR.PRECISERR (bit 9) | BFSR.BFARVALID (bit 15)
        hfsr: int = 0x40000000,  # FORCED
        bfar: int = 0x00000004,  # 空指针结构体偏移 0x04
        mmfar: int = 0x00000000,
        sfsr: int = 0x00000000,
        sfar: int = 0x00000000,
        stacked_pc: int = 0x08000420,
        stacked_lr: int = 0x08000311,
        sp_addr: int = 0x2001FF80,
        exc_return: int = 0xFFFFFFBC,  # Armv8-M: Non-secure/Secure Thread, MSP, no FPU
        ipsr: int = 3,                 # HardFault
        cpuid: int = 0x410FD210,       # Cortex-M33
    ) -> None:
        """注入一帧标准的 Cortex-M HardFault / BusFault / UsageFault 死机现场供单测与演示。"""
        self._halted = True
        self._put_u32_raw(0xE000ED00, cpuid)
        self._put_u32_raw(0xE000ED04, ipsr & 0x1FF)   # ICSR.VECTACTIVE
        self._put_u32_raw(0xE000ED28, cfsr)           # CFSR
        self._put_u32_raw(0xE000ED2C, hfsr)           # HFSR
        self._put_u32_raw(0xE000ED34, mmfar)          # MMFAR
        self._put_u32_raw(0xE000ED38, bfar)           # BFAR
        self._put_u32_raw(0xE000EDE4, sfsr)           # SFSR (Armv8-M)
        self._put_u32_raw(0xE000EDE8, sfar)           # SFAR (Armv8-M)

        # 写入硬件自动压栈的 8 个寄存器帧：R0, R1, R2, R3, R12, LR, ReturnAddress(PC), xPSR
        frame = [0x00000000, 0x00000001, 0x20000100, 0x00000000, 0x0000000C, stacked_lr, stacked_pc, 0x01000000]
        for idx, word in enumerate(frame):
            self._put_u32_raw(sp_addr + idx * 4, word)

        self._regs["msp"] = sp_addr
        self._regs["sp"] = sp_addr
        self._regs["lr"] = exc_return
        self._regs["pc"] = 0x08000180  # HardFault_Handler while(1)
        self._regs["xpsr"] = 0x01000000 | (ipsr & 0x1FF)

    def connect(self) -> ProbeInfo:
        self._connected = True
        return self.info()

    def close(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def info(self) -> ProbeInfo:
        cpuid = int.from_bytes(bytes(self._mem.get(0xE000ED00 + k, 0) for k in range(4)), "little")
        return ProbeInfo(
            backend="mock",
            device=self.device,
            interface=self.interface,
            connected=self._connected,
            halted=self._halted if self._connected else None,
            serial_no="MOCK-0001",
            core_id=f"0x{cpuid:08X}",
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

    def write_u32(self, address: int, values: list[int] | int) -> None:
        """兼容单值 `write_u32(addr, 0x1234)`（历史测试辅助）与标准批量 `write_u32(addr, [0x1234])`。"""
        if isinstance(values, int):
            self._put_u32_raw(address, values)
            return
        self._require_conn()
        if address % 4 != 0:
            raise ProbeError(f"Unaligned 32-bit write at 0x{address:08X}")
        for idx, v in enumerate(values):
            self._put_u32_raw(address + idx * 4, int(v))

    def write_u16(self, address: int, values: list[int]) -> None:
        self._require_conn()
        if address % 2 != 0:
            raise ProbeError(f"Unaligned 16-bit write at 0x{address:08X}")
        for idx, v in enumerate(values):
            raw = (int(v) & 0xFFFF).to_bytes(2, "little")
            base = address + idx * 2
            self._mem[base] = raw[0]
            self._mem[base + 1] = raw[1]

    def write_u8(self, address: int, values: list[int]) -> None:
        self._require_conn()
        for idx, v in enumerate(values):
            self._mem[address + idx] = int(v) & 0xFF

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

    def read_core_regs(self, names: list[str] | None = None) -> dict[str, int]:
        self._require_conn()
        if not names:
            return dict(self._regs)
        out: dict[str, int] = {}
        for n in names:
            k = n.strip().lower()
            if k not in self._regs:
                raise ProbeError(f"Unknown core register {n!r}")
            out[k] = self._regs[k]
        return out

    def reset(self, *, halt: bool = False) -> ResetResult:
        self._require_conn()
        self._halted = halt
        # 若 Flash 起始处已装入中断向量表，从中恢复初始 MSP 与 Reset_Handler PC
        sp_vec = int.from_bytes(bytes(self._mem.get(0x08000000 + k, 0) for k in range(4)), "little")
        pc_vec = int.from_bytes(bytes(self._mem.get(0x08000004 + k, 0) for k in range(4)), "little")
        if sp_vec:
            self._regs["msp"] = sp_vec
            self._regs["sp"] = sp_vec
        if pc_vec:
            self._regs["pc"] = pc_vec & ~1
        self._regs["xpsr"] = 0x01000000
        return ResetResult(
            halted=self._halted,
            pc=f"0x{self._regs['pc']:08X}",
            sp=f"0x{self._regs['sp']:08X}",
            message="Mock target reset" + (" and halted" if halt else " and running"),
        )

    def flash(
        self,
        firmware_path: str | os.PathLike,
        *,
        address: int | None = None,
        verify: bool = True,
        reset_after: bool = True,
        run_after: bool = True,
    ) -> FlashResult:
        self._require_conn()
        t0 = time.monotonic()
        p = Path(firmware_path).expanduser().resolve()
        if not p.is_file():
            raise ProbeError(f"Firmware file not found: {p}")

        raw = p.read_bytes()
        suffix = p.suffix.lower()
        if suffix == ".bin" and address is None:
            raise ProbeError("Flashing a raw .bin file requires specifying a base address (e.g. 0x08000000)")

        load_addr = address if address is not None else 0x08000000
        if suffix == ".bin" and len(raw) >= 8:
            # 将 .bin 写入模拟 Flash 空间，方便随后验证向量表复位
            for off, b in enumerate(raw[:65536]):
                self._mem[load_addr + off] = b
        elif len(raw) >= 52 and raw[:4] == b"\x7fELF" and raw[4] == 1:
            e_entry = struct.unpack_from("<I", raw, 24)[0]
            if e_entry:
                self._regs["pc"] = e_entry & ~1

        self.flashed_images.append((str(p), load_addr, len(raw)))
        if reset_after:
            self.reset(halt=not run_after)
        else:
            self._halted = not run_after

        return FlashResult(
            success=True,
            firmware_path=str(p),
            bytes_written=len(raw),
            address=f"0x{load_addr:08X}",
            verified=verify,
            reset_performed=reset_after,
            running=not self._halted,
            duration_s=time.monotonic() - t0,
            message="Mock flash succeeded",
        )

    # ---- 模拟 SEGGER RTT -------------------------------------------------

    def push_rtt(self, data: bytes | str, channel: int = 0) -> None:
        """向模拟 RTT 上行通道注入日志数据，供后台采集线程读取。"""
        raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        with self._rtt_lock:
            buf = self._rtt_buffers.setdefault(channel, bytearray())
            buf.extend(raw)

    def pop_rtt_down(self, channel: int = 0) -> bytes:
        """测试辅助：读取已写入模拟 RTT 下行通道的数据。"""
        with self._rtt_lock:
            buf = self._rtt_down_buffers.get(channel)
            if not buf:
                return b""
            out = bytes(buf)
            buf.clear()
            return out

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

    def rtt_write(self, data: bytes, channel: int = 0) -> int:
        self._require_conn()
        if not self._rtt_started:
            raise ProbeError("Mock RTT is not started")
        with self._rtt_lock:
            buf = self._rtt_down_buffers.setdefault(channel, bytearray())
            buf.extend(data)
            return len(data)
