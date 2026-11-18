"""调试探针模块：DebugProbe 抽象 + SEGGER J-Link 后端 + 模拟探针 + 外设现场解码。"""

from .base import DebugProbe, ProbeError, ProbeInfo
from .jlink import JLinkProbe
from .manager import ProbeManager, default_probe_manager
from .mock import MockProbe
from .peripheral import PeripheralSnapshot, inspect_peripheral_registers

__all__ = [
    "DebugProbe",
    "JLinkProbe",
    "MockProbe",
    "PeripheralSnapshot",
    "ProbeError",
    "ProbeInfo",
    "ProbeManager",
    "default_probe_manager",
    "inspect_peripheral_registers",
]
