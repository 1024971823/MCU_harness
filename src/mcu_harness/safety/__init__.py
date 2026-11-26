"""分级硬件安全护栏与高危操作审计模块（HITL 防砖与物理保护）。"""

from .guard import (
    AuditEntry,
    SafetyDecision,
    SafetyError,
    SafetyGuard,
    default_safety_guard,
)

__all__ = [
    "AuditEntry",
    "SafetyDecision",
    "SafetyError",
    "SafetyGuard",
    "default_safety_guard",
]
