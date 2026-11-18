"""无头构建模块：调用 CMake/Ninja 或 Make 编译固件，并把冗长输出压缩成 AI 友好的结构化结果。"""

from .diagnostics import BinarySize, Diagnostic, MemoryRegion, ParsedOutput, parse_build_output
from .runner import BuildResult, build_firmware

__all__ = [
    "BinarySize",
    "Diagnostic",
    "MemoryRegion",
    "ParsedOutput",
    "parse_build_output",
    "BuildResult",
    "build_firmware",
]
