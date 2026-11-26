"""调试探针模块：DebugProbe 抽象 + SEGGER J-Link + OpenOCD + 模拟探针 + 外设现场解码。"""

from .base import DebugProbe, FlashResult, ProbeError, ProbeInfo, ResetResult
from .jlink import JLinkProbe
from .manager import ProbeManager, default_probe_manager
from .mock import MockProbe
from .openocd import OpenOCDProbe
from .peripheral import PeripheralSnapshot, inspect_peripheral_registers

__all__ = [
    "DebugProbe",
    "FlashResult",
    "JLinkProbe",
    "MockProbe",
    "OpenOCDProbe",
    "PeripheralSnapshot",
    "ProbeError",
    "ProbeInfo",
    "ProbeManager",
    "ResetResult",
    "default_probe_manager",
    "inspect_peripheral_registers",
]
