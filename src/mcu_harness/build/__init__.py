"""无头构建、编译数据库生成与固件内存版图审计模块。"""

from .compdb import CompDbResult, generate_compile_commands, parse_make_dry_run
from .diagnostics import BinarySize, Diagnostic, MemoryRegion, ParsedOutput, parse_build_output
from .footprint import (
    BloatHint,
    FootprintError,
    FootprintReport,
    LayerContribution,
    MapRegionUsage,
    MemoryHazard,
    ModuleContribution,
    SectionInfo,
    SymbolFootprint,
    analyze_footprint,
)
from .runner import BuildResult, build_firmware

__all__ = [
    "BinarySize",
    "BloatHint",
    "BuildResult",
    "CompDbResult",
    "Diagnostic",
    "FootprintError",
    "FootprintReport",
    "LayerContribution",
    "MapRegionUsage",
    "MemoryHazard",
    "MemoryRegion",
    "ModuleContribution",
    "ParsedOutput",
    "SectionInfo",
    "SymbolFootprint",
    "analyze_footprint",
    "build_firmware",
    "generate_compile_commands",
    "parse_build_output",
    "parse_make_dry_run",
]
