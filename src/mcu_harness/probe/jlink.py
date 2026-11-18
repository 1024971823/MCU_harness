"""SEGGER J-Link 调试探针实现（基于 pylink-square）。

特点：
    * 附着连接时只调用 open -> set_tif(SWD) -> connect，不触发 reset，保护芯片现场。
    * 读外设寄存器 / RAM 时无需暂停 CPU（走 AHB-AP 非侵入式访问），降低对 Ozone 和实时业务的干扰。
    * 自动查找 libjlinkarm.so（除标准路径外，还会顺着 JLinkExe / JLinkGDBServerCLExe 的真实目录寻找）。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from .base import DebugProbe, ProbeError, ProbeInfo


def _discover_jlink_dll(explicit_path: str | os.PathLike | None = None) -> str | None:
    """按优先级定位 libjlinkarm.so（或 Windows 下的 JLink_x64.dll）。"""
    env_or_arg = explicit_path or os.environ.get("MCU_HARNESS_JLINK_LIB")
    if env_or_arg:
        p = Path(env_or_arg).expanduser()
        if not p.is_file():
            raise ProbeError(f"Configured J-Link library not found: {p}")
        return str(p)

    # 顺着 PATH 中的 JLinkExe / JLinkGDBServerCLExe 解析符号链接，定位其同目录下的 libjlinkarm.so*
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

    # 1. 若明确找到或指定了路径，直接加载；否则交给 pylink 默认探测
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
        interface: str = "SWD",
        speed: int | str = "auto",
        serial_no: int | str | None = None,
        lib_path: str | os.PathLike | None = None,
    ) -> None:
        self.device = device or os.environ.get("MCU_HARNESS_DEVICE", "STM32H563ZI")
        self.interface = interface.upper()
        self.speed = speed
        self.serial_no = int(serial_no) if serial_no not in (None, "") else None
        self.lib_path = lib_path
        self._jlink: Any = None

    @property
    def raw_jlink(self) -> Any:
        """暴露底层 pylink.JLink 实例，供后续 RTT 等模块复用同一连接。"""
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

    def halt(self) -> bool:
        self._require_connected()
        try:
            return bool(self._jlink.halt())
        except Exception as e:
            raise ProbeError(f"halt failed: {e}") from e

    def resume(self) -> bool:
        self._require_connected()
        try:
            return bool(self._jlink.restart())
        except Exception as e:
            raise ProbeError(f"resume failed: {e}") from e

    def is_halted(self) -> bool:
        self._require_connected()
        try:
            return bool(self._jlink.halted())
        except Exception as e:
            raise ProbeError(f"halted() query failed: {e}") from e

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

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise ProbeError("J-Link is not connected (call connect() first)")
