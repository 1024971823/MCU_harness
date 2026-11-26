"""SEGGER J-Link 调试探针实现（基于 pylink-square）。

特点：
    * 附着连接时只调用 open -> set_tif(SWD) -> connect，不触发 reset，保护芯片现场。
    * 读外设寄存器 / RAM 时无需暂停 CPU（走 AHB-AP 非侵入式访问），降低对 Ozone 和实时业务的干扰。
    * 恢复运行走 JLINKARM_Go 继续执行而不复位；烧录与显式 reset 单独提供受安全护栏保护的接口。
    * 自动查找 libjlinkarm.so（除标准路径外，还会顺着 JLinkExe / JLinkGDBServerCLExe 的真实目录寻找）。
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

from .base import DebugProbe, FlashResult, ProbeError, ProbeInfo, ResetResult

# 标准小写核心寄存器名 -> J-Link 可能使用的寄存器名候选
_JLINK_REG_ALIASES: dict[str, tuple[str, ...]] = {
    "r0": ("R0",),
    "r1": ("R1",),
    "r2": ("R2",),
    "r3": ("R3",),
    "r4": ("R4",),
    "r5": ("R5",),
    "r6": ("R6",),
    "r7": ("R7",),
    "r8": ("R8",),
    "r9": ("R9",),
    "r10": ("R10",),
    "r11": ("R11",),
    "r12": ("R12",),
    "sp": ("R13 (SP)", "SP", "R13"),
    "lr": ("R14", "R14 (LR)", "LR"),
    "pc": ("R15 (PC)", "PC", "R15"),
    "xpsr": ("XPSR", "CPSR"),
    "msp": ("MSP", "MSP_NS", "MSP_S"),
    "psp": ("PSP", "PSP_NS", "PSP_S"),
    "primask": ("PRIMASK", "PRIMASK_NS"),
    "basepri": ("BASEPRI", "BASEPRI_NS"),
    "faultmask": ("FAULTMASK", "FAULTMASK_NS"),
    "control": ("CONTROL", "CONTROL_NS"),
    "msplim": ("MSPLIM", "MSPLIM_NS", "MSPLIM_S"),
    "psplim": ("PSPLIM", "PSPLIM_NS", "PSPLIM_S"),
}

_DEFAULT_REG_QUERY = (
    "r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7",
    "r8", "r9", "r10", "r11", "r12", "sp", "lr", "pc",
    "xpsr", "msp", "psp", "msplim", "psplim", "control", "primask",
)


def normalize_device(device: str | None = None) -> str:
    val = (device or os.environ.get("MCU_HARNESS_DEVICE") or "STM32H563ZI").strip()
    if not val:
        raise ProbeError("Device name cannot be empty")
    return val


def normalize_interface(interface: str | None = None) -> str:
    val = (interface or os.environ.get("MCU_HARNESS_INTERFACE") or "SWD").strip().upper()
    if val not in ("SWD", "JTAG"):
        raise ProbeError(f"Unsupported interface {interface!r} (expected SWD or JTAG)")
    return val


def normalize_speed(speed: int | str | None = None) -> int | str:
    raw = speed if speed is not None else os.environ.get("MCU_HARNESS_SPEED", "auto")
    if isinstance(raw, int) and not isinstance(raw, bool):
        if raw <= 0:
            raise ProbeError(f"Invalid speed {raw!r}: must be > 0 kHz or 'auto'")
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s or s.lower() == "auto":
            return "auto"
        try:
            khz = int(s, 10)
        except ValueError as e:
            raise ProbeError(f"Invalid speed {speed!r}: expected integer kHz or 'auto'") from e
        if khz <= 0:
            raise ProbeError(f"Invalid speed {speed!r}: must be > 0 kHz")
        return khz
    raise ProbeError(f"Invalid speed {speed!r}")


def normalize_serial_no(serial_no: int | str | None = None) -> int | None:
    raw = serial_no if serial_no not in (None, "") else os.environ.get("MCU_HARNESS_SERIAL")
    if raw in (None, ""):
        return None
    if isinstance(raw, bool):
        raise ProbeError(f"Invalid J-Link serial_no: {serial_no!r}")
    try:
        sn = int(str(raw).strip(), 10)
    except ValueError as e:
        raise ProbeError(f"Invalid J-Link serial_no {raw!r}: expected decimal digits") from e
    if sn <= 0:
        raise ProbeError(f"Invalid J-Link serial_no {raw!r}: must be positive")
    return sn


def _discover_jlink_dll(explicit_path: str | os.PathLike | None = None) -> str | None:
    """按优先级定位 libjlinkarm.so（或 Windows 下的 JLink_x64.dll）。"""
    env_or_arg = explicit_path or os.environ.get("MCU_HARNESS_JLINK_LIB")
    if env_or_arg:
        p = Path(env_or_arg).expanduser()
        if not p.is_file():
            raise ProbeError(f"Configured J-Link library not found: {p}")
        return str(p)

    candidates: list[Path] = []
    for exe in ("JLinkExe", "JLinkGDBServerCLExe"):
        found = shutil.which(exe)
        if found:
            candidates.append(Path(found).resolve().parent)

    for opt_dir in (Path("/opt/SEGGER/JLink"), Path("/opt/SEGGER")):
        if opt_dir.is_dir():
            candidates.append(opt_dir)
            for sub in sorted(opt_dir.glob("JLink*"), reverse=True):
                if sub.is_dir():
                    candidates.append(sub)

    for directory in candidates:
        exact = directory / "libjlinkarm.so"
        if exact.is_file():
            return str(exact)
        for match in sorted(directory.glob("libjlinkarm.so*"), reverse=True):
            if match.is_file():
                return str(match)
    return None


def _load_pylink_library(dll_path: str | None = None):
    try:
        import pylink
    except ImportError as e:
        raise ProbeError("pylink-square is not installed (run: pip install pylink-square)") from e

    discovered = _discover_jlink_dll(dll_path)
    if discovered:
        try:
            return pylink, pylink.Library(dllpath=discovered)
        except Exception as e:
            raise ProbeError(f"Failed to load J-Link library {discovered}: {e}") from e

    try:
        return pylink, pylink.Library()
    except Exception as e:
        raise ProbeError(
            "Could not find SEGGER J-Link shared library (libjlinkarm.so). "
            "Install J-Link Software Pack or set MCU_HARNESS_JLINK_LIB."
        ) from e


class JLinkProbe(DebugProbe):
    """通过 SEGGER J-Link 访问目标 MCU。"""

    def __init__(
        self,
        *,
        device: str | None = None,
        interface: str | None = None,
        speed: int | str | None = None,
        serial_no: int | str | None = None,
        lib_path: str | os.PathLike | None = None,
    ) -> None:
        self.device = normalize_device(device)
        self.interface = normalize_interface(interface)
        self.speed = normalize_speed(speed)
        self.serial_no = normalize_serial_no(serial_no)
        self.lib_path = lib_path
        self._jlink: Any = None

    @property
    def raw_jlink(self) -> Any:
        """暴露底层 pylink.JLink 实例，供后续模块复用同一连接。"""
        self._require_connected()
        return self._jlink

    def connect(self) -> ProbeInfo:
        if self.is_connected():
            return self.info()

        pylink, lib = _load_pylink_library(str(self.lib_path) if self.lib_path else None)
        tif_map = {
            "SWD": pylink.enums.JLinkInterfaces.SWD,
            "JTAG": pylink.enums.JLinkInterfaces.JTAG,
        }
        if self.interface not in tif_map:
            raise ProbeError(f"Unsupported interface {self.interface!r} (expected SWD or JTAG)")

        jl = pylink.JLink(lib=lib)
        try:
            jl.open(serial_no=self.serial_no)
            jl.set_tif(tif_map[self.interface])
            jl.connect(self.device, speed=self.speed)
            self._jlink = jl
            return self.info()
        except Exception as e:
            try:
                jl.close()
            except Exception:
                pass
            self._jlink = None
            raise ProbeError(f"J-Link connect failed (device={self.device}, if={self.interface}): {e}") from e

    def close(self) -> None:
        if self._jlink is not None:
            try:
                self._jlink.close()
            except Exception:
                pass
            finally:
                self._jlink = None

    def is_connected(self) -> bool:
        if self._jlink is None:
            return False
        try:
            return bool(self._jlink.opened() and self._jlink.connected())
        except Exception:
            return False

    def info(self) -> ProbeInfo:
        self._require_connected()
        jl = self._jlink
        try:
            core_id = f"0x{jl.core_id():08X}"
        except Exception:
            core_id = None
        try:
            core_name = jl.core_name()
        except Exception:
            core_name = None
        try:
            speed_khz = int(jl.speed) if jl.speed else None
        except Exception:
            speed_khz = None
        try:
            sn = str(jl.serial_number)
        except Exception:
            sn = str(self.serial_no) if self.serial_no else None
        try:
            halted = bool(jl.halted())
        except Exception:
            halted = None

        return ProbeInfo(
            backend="jlink",
            device=self.device,
            interface=self.interface,
            connected=True,
            halted=halted,
            serial_no=sn,
            core_id=core_id,
            core_name=core_name,
            speed_khz=speed_khz,
        )

    def read_u32(self, address: int, count: int = 1) -> list[int]:
        self._require_connected()
        if address % 4 != 0:
            raise ProbeError(f"Unaligned 32-bit read at 0x{address:08X}")
        if count <= 0:
            raise ProbeError(f"count must be > 0, got {count}")
        try:
            return [int(v) & 0xFFFFFFFF for v in self._jlink.memory_read32(address, count)]
        except Exception as e:
            raise ProbeError(f"Failed to read {count}x32-bit at 0x{address:08X}: {e}") from e

    def read_u16(self, address: int, count: int = 1) -> list[int]:
        self._require_connected()
        if address % 2 != 0:
            raise ProbeError(f"Unaligned 16-bit read at 0x{address:08X}")
        if count <= 0:
            raise ProbeError(f"count must be > 0, got {count}")
        try:
            return [int(v) & 0xFFFF for v in self._jlink.memory_read16(address, count)]
        except Exception as e:
            raise ProbeError(f"Failed to read {count}x16-bit at 0x{address:08X}: {e}") from e

    def read_u8(self, address: int, count: int = 1) -> list[int]:
        self._require_connected()
        if count <= 0:
            raise ProbeError(f"count must be > 0, got {count}")
        try:
            return [int(v) & 0xFF for v in self._jlink.memory_read8(address, count)]
        except Exception as e:
            raise ProbeError(f"Failed to read {count} bytes at 0x{address:08X}: {e}") from e

    def write_u32(self, address: int, values: list[int]) -> None:
        self._require_connected()
        if address % 4 != 0:
            raise ProbeError(f"Unaligned 32-bit write at 0x{address:08X}")
        if not values:
            return
        try:
            self._jlink.memory_write32(address, [int(v) & 0xFFFFFFFF for v in values])
        except Exception as e:
            raise ProbeError(f"Failed to write {len(values)}x32-bit at 0x{address:08X}: {e}") from e

    def write_u16(self, address: int, values: list[int]) -> None:
        self._require_connected()
        if address % 2 != 0:
            raise ProbeError(f"Unaligned 16-bit write at 0x{address:08X}")
        if not values:
            return
        try:
            self._jlink.memory_write16(address, [int(v) & 0xFFFF for v in values])
        except Exception as e:
            raise ProbeError(f"Failed to write {len(values)}x16-bit at 0x{address:08X}: {e}") from e

    def write_u8(self, address: int, values: list[int]) -> None:
        self._require_connected()
        if not values:
            return
        try:
            self._jlink.memory_write8(address, [int(v) & 0xFF for v in values])
        except Exception as e:
            raise ProbeError(f"Failed to write {len(values)} bytes at 0x{address:08X}: {e}") from e

    def halt(self) -> bool:
        self._require_connected()
        try:
            was_halted = bool(self._jlink.halted())
            ok = bool(self._jlink.halt())
            return ok and (not was_halted)
        except Exception as e:
            raise ProbeError(f"halt failed: {e}") from e

    def _go_without_reset(self) -> None:
        if hasattr(self._jlink, "go"):
            self._jlink.go()
        elif hasattr(getattr(self._jlink, "_dll", None), "JLINKARM_Go"):
            self._jlink._dll.JLINKARM_Go()
        else:
            self._jlink.restart()

    def resume(self) -> bool:
        self._require_connected()
        try:
            was_halted = bool(self._jlink.halted())
            if not was_halted:
                return False
            self._go_without_reset()
            return was_halted
        except Exception as e:
            raise ProbeError(f"resume failed: {e}") from e

    def is_halted(self) -> bool:
        self._require_connected()
        try:
            return bool(self._jlink.halted())
        except Exception as e:
            raise ProbeError(f"halted() query failed: {e}") from e

    def read_core_regs(self, names: list[str] | None = None) -> dict[str, int]:
        self._require_connected()
        jl = self._jlink
        try:
            idx_list = jl.register_list()
            by_upper: dict[str, int] = {}
            for reg_idx in idx_list:
                rname = str(jl.register_name(reg_idx)).strip()
                by_upper[rname.upper()] = reg_idx
        except Exception as e:
            raise ProbeError(f"Failed to query J-Link register list: {e}") from e

        wanted = [n.strip().lower() for n in names] if names else list(_DEFAULT_REG_QUERY)
        out: dict[str, int] = {}
        for canonical in wanted:
            candidates = _JLINK_REG_ALIASES.get(canonical, (canonical.upper(),))
            matched_idx = next((by_upper[c.upper()] for c in candidates if c.upper() in by_upper), None)
            if matched_idx is None:
                if names is not None:
                    raise ProbeError(f"Core register {canonical!r} not supported on target {self.device}")
                continue
            try:
                out[canonical] = int(jl.register_read(matched_idx)) & 0xFFFFFFFF
            except Exception as e:
                if names is not None:
                    raise ProbeError(f"Failed to read core register {canonical!r}: {e}") from e
        return out

    def reset(self, *, halt: bool = False) -> ResetResult:
        self._require_connected()
        try:
            self._jlink.reset(ms=0, halt=halt)
            if not halt and bool(self._jlink.halted()):
                self._go_without_reset()
            halted_now = bool(self._jlink.halted())
            pc_str = None
            sp_str = None
            if halted_now:
                regs = self.read_core_regs()
                if "pc" in regs:
                    pc_str = f"0x{regs['pc']:08X}"
                if "sp" in regs:
                    sp_str = f"0x{regs['sp']:08X}"
            return ResetResult(
                halted=halted_now,
                pc=pc_str,
                sp=sp_str,
                message="Target reset" + (" and halted" if halted_now else " and running"),
            )
        except Exception as e:
            raise ProbeError(f"reset(halt={halt}) failed: {e}") from e

    def flash(
        self,
        firmware_path: str | os.PathLike,
        *,
        address: int | None = None,
        verify: bool = True,
        reset_after: bool = True,
        run_after: bool = True,
    ) -> FlashResult:
        self._require_connected()
        t0 = time.monotonic()
        p = Path(firmware_path).expanduser().resolve()
        if not p.is_file():
            raise ProbeError(f"Firmware file not found: {p}")

        suffix = p.suffix.lower()
        if suffix == ".bin" and address is None:
            raise ProbeError("Flashing a raw .bin file requires specifying a base address (e.g. 0x08000000)")

        load_addr = address if address is not None else 0
        try:
            self._jlink.halt()
            # pylink.flash_file 对 .elf / .hex 会忽略 addr 并使用文件内嵌段地址；对 .bin 使用 addr
            written = self._jlink.flash_file(str(p), addr=load_addr)
            if isinstance(written, int) and written < 0:
                raise ProbeError(f"J-Link flash_file returned error code {written}")
            if reset_after:
                self.reset(halt=not run_after)
            elif run_after:
                self.resume()
            halted_now = bool(self._jlink.halted())
            return FlashResult(
                success=True,
                firmware_path=str(p),
                bytes_written=int(written) if isinstance(written, int) and written >= 0 else p.stat().st_size,
                address=f"0x{address:08X}" if address is not None else "auto(.elf/.hex)",
                verified=verify,
                reset_performed=reset_after,
                running=not halted_now,
                duration_s=time.monotonic() - t0,
                message="J-Link flash succeeded",
            )
        except Exception as e:
            raise ProbeError(f"J-Link flash failed for {p}: {e}") from e

    # ---- SEGGER RTT ------------------------------------------------------

    def rtt_start(self, block_address: int | None = None) -> None:
        self._require_connected()
        try:
            self._jlink.rtt_start(block_address=block_address)
        except Exception as e:
            raise ProbeError(f"rtt_start failed: {e}") from e

    def rtt_stop(self) -> None:
        if self._jlink is None:
            return
        try:
            self._jlink.rtt_stop()
        except Exception as e:
            raise ProbeError(f"rtt_stop failed: {e}") from e

    def rtt_read(self, channel: int = 0, max_bytes: int = 4096) -> bytes:
        self._require_connected()
        if max_bytes <= 0:
            raise ProbeError(f"max_bytes must be > 0, got {max_bytes}")
        try:
            data = self._jlink.rtt_read(channel, max_bytes)
            return bytes(data)
        except Exception as e:
            raise ProbeError(f"rtt_read(ch={channel}) failed: {e}") from e

    def rtt_write(self, data: bytes, channel: int = 0) -> int:
        self._require_connected()
        if not data:
            return 0
        try:
            return int(self._jlink.rtt_write(channel, list(data)))
        except Exception as e:
            raise ProbeError(f"rtt_write(ch={channel}) failed: {e}") from e

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise ProbeError("J-Link is not connected (call connect() first)")
