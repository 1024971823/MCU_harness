"""工程配置清单（mcu-harness.toml）与零配置硬件/构建自动探测模块。"""

from .manifest import (
    ArchConfig,
    BuildConfig,
    ConfigError,
    LayerRule,
    PathsConfig,
    ProjectProfile,
    SafetyConfig,
    TargetConfig,
    discover_project,
    generate_manifest_toml,
    infer_mcu_traits,
    normalize_cubemx_mcu_name,
)

__all__ = [
    "ArchConfig",
    "BuildConfig",
    "ConfigError",
    "LayerRule",
    "PathsConfig",
    "ProjectProfile",
    "SafetyConfig",
    "TargetConfig",
    "discover_project",
    "generate_manifest_toml",
    "infer_mcu_traits",
    "normalize_cubemx_mcu_name",
]
