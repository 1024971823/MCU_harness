"""嵌入式四层代码架构守护、ISR 并发安全静态扫描与跨芯片 PAL/BSP/Service 脚手架模块。"""

from .analyzer import (
    ArchError,
    ArchReport,
    ArchViolation,
    _strip_comments_and_strings,
    check_architecture,
)
from .scaffold import (
    ScaffoldError,
    ScaffoldResult,
    scaffold_code,
)

__all__ = [
    "ArchError",
    "ArchReport",
    "ArchViolation",
    "ScaffoldError",
    "ScaffoldResult",
    "_strip_comments_and_strings",
    "check_architecture",
    "scaffold_code",
]
