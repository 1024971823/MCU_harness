"""Cortex-M 死机自动尸检（Crash Forensics）模块导出。"""

from .cortex_m import (
    CoreIdentity,
    CrashReport,
    ExcReturnInfo,
    FaultBit,
    decode_cpuid,
    decode_exc_return,
    decode_exception_name,
    diagnose_crash,
    is_exc_return,
)

analyze_cortex_m_crash = diagnose_crash

__all__ = [
    "CoreIdentity",
    "CrashReport",
    "ExcReturnInfo",
    "FaultBit",
    "analyze_cortex_m_crash",
    "decode_cpuid",
    "decode_exc_return",
    "decode_exception_name",
    "diagnose_crash",
    "is_exc_return",
]
