"""硬件在环（HIL）自动化测试引擎与逻辑分析仪数字波形/协议解码桥接模块。"""

from .logic_analyzer import (
    ChannelWaveformMetrics,
    HilError,
    LogicCaptureReport,
    LogicCsvReport,
    ProtocolAnnotation,
    analyze_digital_csv,
    decode_with_sigrok,
)
from .runner import (
    HilConfigError,
    HilStepResult,
    HilSuiteReport,
    HilTestReport,
    load_hil_spec,
    run_hil_suite,
)

__all__ = [
    "ChannelWaveformMetrics",
    "HilConfigError",
    "HilError",
    "HilStepResult",
    "HilSuiteReport",
    "HilTestReport",
    "LogicCaptureReport",
    "LogicCsvReport",
    "ProtocolAnnotation",
    "analyze_digital_csv",
    "decode_with_sigrok",
    "load_hil_spec",
    "run_hil_suite",
]
