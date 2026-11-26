"""实时调试日志模块：非阻塞环形缓冲区 + SEGGER RTT 后台采集 + 串口 UART 后台采集。"""

from .ring_buffer import LogEntry, LogReadResult, RingLogBuffer
from .rtt_collector import RttLogCollector, RttStatus, default_rtt_collector
from .uart_collector import (
    STANDARD_BAUDRATES,
    SerialPortInfo,
    UartError,
    UartLogCollector,
    UartStatus,
    default_uart_collector,
    list_serial_ports,
)

__all__ = [
    "LogEntry",
    "LogReadResult",
    "RingLogBuffer",
    "RttLogCollector",
    "RttStatus",
    "STANDARD_BAUDRATES",
    "SerialPortInfo",
    "UartError",
    "UartLogCollector",
    "UartStatus",
    "default_rtt_collector",
    "default_uart_collector",
    "list_serial_ports",
]
