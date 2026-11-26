"""工程配置清单解析与零配置自动探测（Auto-Discovery）。

职责：
    1. 读取工程根目录下的 `mcu-harness.toml`（或 `.mcu-harness.toml`）。
    2. 当未提供配置文件或部分字段留空时，自动扫描工程中的：
       - STM32CubeMX `.ioc`（提取芯片型号并归一化，如 STM32H563ZITx -> STM32H563ZI）
       - `CMakeLists.txt` / `Makefile`（提取构建系统、`-mcpu=cortex-m*`、`-DSTM32*` 芯片宏）
       - 构建目录与工程树中的最新 `.elf`、`.map`、`compile_commands.json`、`.svd`/`.svd.gz`
       - 环境变量 `MCU_HARNESS_*`
    3. 自动推导 Cortex-M 内核代数、Flash 起始地址与 OpenOCD target 脚本名称。
    4. 支持一键导出 `mcu-harness.toml` 模板，让任意嵌入式仓库即刻接入 Harness。
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


class ConfigError(RuntimeError):
    """配置文件语法或字段取值错误。"""


_MANIFEST_NAMES = ("mcu-harness.toml", ".mcu-harness.toml")
_SKIP_SCAN_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "CMakeFiles", ".pytest_cache"}

# STM32CubeMX 常见封装尾缀：STM32H563ZITx / STM32F407VGT6 / STM32WBA52CGUx / STM32H7A3ZIT6Q -> 保留到 Flash 容量位
_RE_STM32_CUBEMX = re.compile(
    r"^(STM32(?:WBA|[A-Z0-9]{2})[A-Z0-9]\d[A-Z][A-Z0-9])(?:[A-Z][0-9xX]?[A-Z]?)?$",
    re.IGNORECASE,
)
_RE_DEFINE_MCU = re.compile(
    r"-D\s*(STM32[A-Za-z0-9_]+|GD32[A-Za-z0-9_]+|AT32[A-Za-z0-9_]+|NRF5[23][A-Za-z0-9_]*|RP2040|RP2350)\b"
)
_RE_MCPU = re.compile(r"-mcpu=(cortex-m\d+\+?|cortex-m\d+)", re.IGNORECASE)


def normalize_cubemx_mcu_name(raw_name: str) -> str:
    """将 CubeMX 型号（如 STM32H563ZITx / STM32F407VGT6）归一化为探针通用短名（STM32H563ZI / STM32F407VG）。"""
    cleaned = raw_name.strip().split("(")[0].strip()
    m = _RE_STM32_CUBEMX.match(cleaned)
    if m:
        return m.group(1).upper()
    return cleaned


def infer_mcu_traits(device: str | None, mcpu_hint: str | None = None) -> dict[str, Any]:
    """根据芯片型号推导内核名称、OpenOCD target 配置名与默认 Flash 起始地址。"""
    dev = (device or "").strip().upper()
    core: str | None = None
    openocd_target: str | None = None
    flash_base: int | None = None

    # 优先根据系列前缀推断
    stm32_series_map: list[tuple[str, str, str, int]] = [
        ("STM32H5", "Cortex-M33", "stm32h5x", 0x08000000),
        ("STM32U5", "Cortex-M33", "stm32u5x", 0x08000000),
        ("STM32L5", "Cortex-M33", "stm32l5x", 0x08000000),
        ("STM32WBA", "Cortex-M33", "stm32wbax", 0x08000000),
        ("STM32H7", "Cortex-M7", "stm32h7x", 0x08000000),
        ("STM32F7", "Cortex-M7", "stm32f7x", 0x08000000),
        ("STM32G4", "Cortex-M4", "stm32g4x", 0x08000000),
        ("STM32F4", "Cortex-M4", "stm32f4x", 0x08000000),
        ("STM32L4", "Cortex-M4", "stm32l4x", 0x08000000),
        ("STM32F3", "Cortex-M4", "stm32f3x", 0x08000000),
        ("STM32WB", "Cortex-M4", "stm32wbx", 0x08000000),
        ("STM32WL", "Cortex-M4", "stm32wlx", 0x08000000),
        ("STM32F2", "Cortex-M3", "stm32f2x", 0x08000000),
        ("STM32F1", "Cortex-M3", "stm32f1x", 0x08000000),
        ("STM32L1", "Cortex-M3", "stm32l1", 0x08000000),
        ("STM32G0", "Cortex-M0+", "stm32g0x", 0x08000000),
        ("STM32C0", "Cortex-M0+", "stm32c0x", 0x08000000),
        ("STM32L0", "Cortex-M0+", "stm32l0", 0x08000000),
        ("STM32U0", "Cortex-M0+", "stm32u0x", 0x08000000),
        ("STM32F0", "Cortex-M0", "stm32f0x", 0x08000000),
        ("GD32F4", "Cortex-M4", "stm32f4x", 0x08000000),
        ("GD32F3", "Cortex-M4", "stm32f1x", 0x08000000),
        ("GD32F1", "Cortex-M3", "stm32f1x", 0x08000000),
        ("AT32F4", "Cortex-M4", "at32f4xx", 0x08000000),
        ("RP2040", "Cortex-M0+", "rp2040", 0x10000000),
        ("RP2350", "Cortex-M33", "rp2350", 0x10000000),
        ("NRF52", "Cortex-M4", "nrf52", 0x00000000),
        ("NRF53", "Cortex-M33", "nrf53", 0x00000000),
        ("MIMXRT", "Cortex-M7", "imxrt", 0x60000000),
    ]
    for prefix, c_name, ocd_tgt, f_base in stm32_series_map:
        if dev.startswith(prefix):
            core = c_name
            openocd_target = ocd_tgt
            flash_base = f_base
            break

    if core is None and mcpu_hint:
        low = mcpu_hint.lower()
        mapping = {
            "cortex-m0": "Cortex-M0",
            "cortex-m0+": "Cortex-M0+",
            "cortex-m0plus": "Cortex-M0+",
            "cortex-m23": "Cortex-M23",
            "cortex-m3": "Cortex-M3",
            "cortex-m4": "Cortex-M4",
            "cortex-m7": "Cortex-M7",
            "cortex-m33": "Cortex-M33",
            "cortex-m55": "Cortex-M55",
            "cortex-m85": "Cortex-M85",
        }
        core = mapping.get(low, mcpu_hint)

    return {
        "core": core,
        "openocd_target": openocd_target,
        "flash_base": flash_base,
    }


@dataclass(frozen=True)
class TargetConfig:
    device: str | None = None
    core: str | None = None
    probe_backend: str = "jlink"  # "jlink" | "openocd" | "mock"
    interface: str = "SWD"
    speed: int | str = "auto"
    serial_no: str | None = None
    openocd_interface: str = "cmsis-dap"  # "cmsis-dap" | "stlink" | "jlink" | 自定义 .cfg
    openocd_target: str | None = None     # 如 "stm32h5x" / "stm32f4x"
    openocd_tcl_port: int = 6666
    flash_base: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.flash_base is not None:
            d["flash_base_hex"] = f"0x{self.flash_base:08X}"
        return d


@dataclass(frozen=True)
class BuildConfig:
    system: str = "auto"
    build_dir: str | None = None
    target: str | None = None
    build_type: str = "Debug"
    toolchain_file: str | None = None
    generator: str = "Ninja"
    makefile: str | None = None
    extra_args: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["extra_args"] = list(self.extra_args)
        return d


@dataclass(frozen=True)
class PathsConfig:
    svd_path: str | None = None
    elf_path: str | None = None
    map_path: str | None = None
    ioc_path: str | None = None
    compile_commands_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SafetyConfig:
    allow_flash_without_confirm: bool = False
    allow_peripheral_write_without_confirm: bool = False
    allow_option_bytes: bool = False
    # (start_addr, end_addr_inclusive, reason)
    deny_ranges: tuple[tuple[int, int, str], ...] = ()
    confirm_ranges: tuple[tuple[int, int, str], ...] = ()
    protected_peripherals: tuple[str, ...] = (
        "FLASH",
        "PWR",
        "RCC",
        "TIM1",
        "TIM8",
        "HRTIM1",
        "IWDG",
        "WWDG",
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "allow_flash_without_confirm": self.allow_flash_without_confirm,
            "allow_peripheral_write_without_confirm": self.allow_peripheral_write_without_confirm,
            "allow_option_bytes": self.allow_option_bytes,
            "deny_ranges": [
                {"start": f"0x{s:08X}", "end": f"0x{e:08X}", "reason": r}
                for s, e, r in self.deny_ranges
            ],
            "confirm_ranges": [
                {"start": f"0x{s:08X}", "end": f"0x{e:08X}", "reason": r}
                for s, e, r in self.confirm_ranges
            ],
            "protected_peripherals": list(self.protected_peripherals),
        }


@dataclass(frozen=True)
class LayerRule:
    """代码分层边界规则。"""

    name: str
    paths: tuple[str, ...]
    allow_depend_on: tuple[str, ...] = ()
    forbid_headers: tuple[str, ...] = ()
    isolate_siblings: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "paths": list(self.paths),
            "allow_depend_on": list(self.allow_depend_on),
            "forbid_headers": list(self.forbid_headers),
            "isolate_siblings": self.isolate_siblings,
        }


DEFAULT_FOUR_LAYER_RULES: tuple[LayerRule, ...] = (
    LayerRule(
        name="app",
        paths=("app", "App", "src/app"),
        allow_depend_on=("app", "service", "bsp", "pal"),
        forbid_headers=("stm32*hal*.h", "stm32*ll*.h", "gd32*.h"),
    ),
    LayerRule(
        name="service",
        paths=("service", "Service", "services", "domain", "src/service"),
        allow_depend_on=("service", "bsp", "pal"),
        forbid_headers=("stm32*hal*.h", "stm32*ll*.h", "gd32*.h", "board_config.h"),
        isolate_siblings=False,
    ),
    LayerRule(
        name="bsp",
        paths=("bsp", "Bsp", "drivers/bsp", "src/bsp"),
        allow_depend_on=("bsp", "pal", "hal"),
        forbid_headers=("board_config.h",),
    ),
    LayerRule(
        name="pal",
        paths=("pal", "Pal", "include/pal", "hal", "Hal", "Core", "Drivers", "src/pal", "src/hal"),
        allow_depend_on=("pal", "hal"),
    ),
)

DEFAULT_FORBIDDEN_IN_ISR: tuple[str, ...] = (
    "HAL_Delay",
    "osDelay",
    "vTaskDelay",
    "vTaskDelayUntil",
    "sleep",
    "usleep",
    "malloc",
    "calloc",
    "realloc",
    "free",
    "pvPortMalloc",
    "vPortFree",
    "printf",
    "fprintf",
    "xSemaphoreTake",
    "xQueueSend",
    "xQueueSendToBack",
    "xQueueSendToFront",
    "xQueueReceive",
    "osMutexAcquire",
    "osSemaphoreAcquire",
)


@dataclass(frozen=True)
class ArchConfig:
    preset: str = "layered-4"  # "layered-4" | "custom" | "none"
    layers: tuple[LayerRule, ...] = DEFAULT_FOUR_LAYER_RULES
    isr_patterns: tuple[str, ...] = (
        r".*_IRQHandler$",
        r"^HAL_.*_Callback$",
        r".*_ISR$",
        r"^(SysTick|PendSV|NMI|HardFault|MemManage|BusFault|UsageFault|SecureFault)_Handler$",
    )
    forbidden_in_isr: tuple[str, ...] = DEFAULT_FORBIDDEN_IN_ISR

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset": self.preset,
            "layers": [l.to_dict() for l in self.layers],
            "isr_patterns": list(self.isr_patterns),
            "forbidden_in_isr": list(self.forbidden_in_isr),
        }


@dataclass(frozen=True)
class ProjectProfile:
    project_dir: str
    manifest_path: str | None
    target: TargetConfig
    build: BuildConfig
    paths: PathsConfig
    safety: SafetyConfig
    arch: ArchConfig
    detected_sources: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_dir": self.project_dir,
            "manifest_path": self.manifest_path,
            "target": self.target.to_dict(),
            "build": self.build.to_dict(),
            "paths": self.paths.to_dict(),
            "safety": self.safety.to_dict(),
            "arch": self.arch.to_dict(),
            "detected_sources": dict(self.detected_sources),
        }

    def to_text(self) -> str:
        lines = [
            f"[project] {self.project_dir}",
            f"  manifest:      {self.manifest_path or '(auto-discovered, no mcu-harness.toml)'}",
            f"  device:        {self.target.device or '(unknown)'}  [core={self.target.core or '?'}]",
            f"  probe:         backend={self.target.probe_backend}, if={self.target.interface}, speed={self.target.speed}",
            f"  build_system:  {self.build.system}",
            f"  elf_path:      {self.paths.elf_path or '(not built yet)'}",
            f"  svd_path:      {self.paths.svd_path or '(not configured)'}",
            f"  ioc_path:      {self.paths.ioc_path or '(none)'}",
            f"  arch_preset:   {self.arch.preset} ({len(self.arch.layers)} layers)",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 自动扫描与 TOML 合并
# ---------------------------------------------------------------------------


def discover_project(
    project_dir: str | os.PathLike | None = None,
    *,
    manifest_path: str | os.PathLike | None = None,
) -> ProjectProfile:
    """自动扫描工程目录并融合 `mcu-harness.toml` 与环境变量，生成完整 `ProjectProfile`。"""
    root = Path(project_dir or os.getcwd()).expanduser().resolve()
    if not root.is_dir():
        raise ConfigError(f"Project directory does not exist: {root}")

    sources: dict[str, str] = {}
    raw_toml: dict[str, Any] = {}
    resolved_manifest: Path | None = None

    if manifest_path is not None:
        mp = Path(manifest_path).expanduser()
        if not mp.is_absolute():
            mp = (root / mp).resolve()
        if not mp.is_file():
            raise ConfigError(f"Manifest file not found: {mp}")
        resolved_manifest = mp
    else:
        for cand_name in _MANIFEST_NAMES:
            cand = root / cand_name
            if cand.is_file():
                resolved_manifest = cand
                break

    if resolved_manifest is not None:
        try:
            raw_toml = tomllib.loads(resolved_manifest.read_text(encoding="utf-8"))
        except Exception as e:
            raise ConfigError(f"Failed to parse TOML manifest {resolved_manifest}: {e}") from e

    # 1. 扫描硬件与构建特征文件
    ioc_file, ioc_device = _scan_ioc(root)
    build_sys, cm_or_mk_device, mcpu_hint = _scan_build_files(root)
    artifacts = _scan_project_files(root, device_hint=ioc_device or cm_or_mk_device)

    # 2. 合并 TargetConfig
    t_sec = _expect_table(raw_toml, "target")
    device: str | None = None
    if t_sec.get("device"):
        device = str(t_sec["device"]).strip()
        sources["device"] = "manifest"
    elif ioc_device:
        device = ioc_device
        sources["device"] = f"ioc ({_rel(ioc_file, root)})"
    elif cm_or_mk_device:
        device = cm_or_mk_device
        sources["device"] = "build_flags"
    elif os.environ.get("MCU_HARNESS_DEVICE"):
        device = os.environ["MCU_HARNESS_DEVICE"].strip()
        sources["device"] = "env:MCU_HARNESS_DEVICE"

    traits = infer_mcu_traits(device, mcpu_hint=mcpu_hint)

    backend = str(
        t_sec.get("probe_backend")
        or t_sec.get("backend")
        or os.environ.get("MCU_HARNESS_BACKEND")
        or "jlink"
    ).strip().lower()
    if backend not in ("jlink", "openocd", "mock"):
        raise ConfigError(f"Unsupported target.probe_backend {backend!r} (expected jlink, openocd, or mock)")

    interface = str(t_sec.get("interface") or os.environ.get("MCU_HARNESS_INTERFACE") or "SWD").strip().upper()
    speed: int | str = t_sec.get("speed") if "speed" in t_sec else os.environ.get("MCU_HARNESS_SPEED", "auto")
    if isinstance(speed, str) and speed.strip().isdigit():
        speed = int(speed.strip(), 10)
    serial_raw = t_sec.get("serial_no") if "serial_no" in t_sec else os.environ.get("MCU_HARNESS_SERIAL")
    serial_no = str(serial_raw).strip() if serial_raw not in (None, "") else None

    flash_base = _parse_opt_int(t_sec.get("flash_base"), "target.flash_base")
    if flash_base is None:
        flash_base = traits["flash_base"]

    ocd_port = _parse_opt_int(t_sec.get("openocd_tcl_port"), "target.openocd_tcl_port")
    if ocd_port is not None and (ocd_port <= 0 or ocd_port > 65535):
        raise ConfigError(f"target.openocd_tcl_port out of range (1..65535): {ocd_port}")

    target_cfg = TargetConfig(
        device=device,
        core=str(t_sec["core"]).strip() if t_sec.get("core") else traits["core"],
        probe_backend=backend,
        interface=interface,
        speed=speed,
        serial_no=serial_no,
        openocd_interface=str(t_sec.get("openocd_interface") or "cmsis-dap").strip(),
        openocd_target=str(t_sec["openocd_target"]).strip() if t_sec.get("openocd_target") else traits["openocd_target"],
        openocd_tcl_port=ocd_port if ocd_port is not None else 6666,
        flash_base=flash_base,
    )

    # 3. 合并 BuildConfig
    b_sec = _expect_table(raw_toml, "build")
    cfg_build_sys = str(b_sec.get("system") or "auto").strip().lower()
    if cfg_build_sys == "auto" and build_sys != "unknown":
        resolved_sys = build_sys
        sources["build_system"] = "auto_scan"
    else:
        resolved_sys = cfg_build_sys
        if "system" in b_sec:
            sources["build_system"] = "manifest"

    extra_args_raw = b_sec.get("extra_args", [])
    if not isinstance(extra_args_raw, list):
        raise ConfigError("build.extra_args must be a list of strings")

    build_cfg = BuildConfig(
        system=resolved_sys,
        build_dir=_resolve_opt_path(root, b_sec.get("build_dir")),
        target=str(b_sec["target"]).strip() if b_sec.get("target") else None,
        build_type=str(b_sec.get("build_type") or "Debug").strip(),
        toolchain_file=_resolve_opt_path(root, b_sec.get("toolchain_file")),
        generator=str(b_sec.get("generator") or "Ninja").strip(),
        makefile=_resolve_opt_path(root, b_sec.get("makefile")),
        extra_args=tuple(str(x) for x in extra_args_raw),
    )

    # 4. 合并 PathsConfig
    p_sec = _expect_table(raw_toml, "paths")
    svd_path = _pick_path(
        root,
        explicit=p_sec.get("svd") or p_sec.get("svd_path"),
        env_var="MCU_HARNESS_SVD_PATH",
        scanned=artifacts.get("svd"),
        key="svd_path",
        sources=sources,
    )
    elf_path = _pick_path(
        root,
        explicit=p_sec.get("elf") or p_sec.get("elf_path"),
        env_var="MCU_HARNESS_ELF_PATH",
        scanned=artifacts.get("elf"),
        key="elf_path",
        sources=sources,
    )
    map_path = _pick_path(
        root,
        explicit=p_sec.get("map") or p_sec.get("map_path"),
        env_var=None,
        scanned=artifacts.get("map"),
        key="map_path",
        sources=sources,
    )
    compdb_path = _pick_path(
        root,
        explicit=p_sec.get("compile_commands") or p_sec.get("compile_commands_path"),
        env_var=None,
        scanned=artifacts.get("compile_commands"),
        key="compile_commands_path",
        sources=sources,
    )
    ioc_resolved = _resolve_opt_path(root, p_sec.get("ioc") or p_sec.get("ioc_path")) or (
        str(ioc_file) if ioc_file else None
    )

    paths_cfg = PathsConfig(
        svd_path=svd_path,
        elf_path=elf_path,
        map_path=map_path,
        ioc_path=ioc_resolved,
        compile_commands_path=compdb_path,
    )

    # 5. 合并 SafetyConfig 与 ArchConfig
    safety_cfg = _parse_safety_section(_expect_table(raw_toml, "safety"))
    arch_cfg = _parse_arch_section(_expect_table(raw_toml, "architecture"))

    return ProjectProfile(
        project_dir=str(root),
        manifest_path=str(resolved_manifest) if resolved_manifest else None,
        target=target_cfg,
        build=build_cfg,
        paths=paths_cfg,
        safety=safety_cfg,
        arch=arch_cfg,
        detected_sources=sources,
    )


def generate_manifest_toml(profile: ProjectProfile) -> str:
    """根据已探测到的 ProjectProfile 生成带清晰中文注释的 `mcu-harness.toml` 模板。"""
    proj = Path(profile.project_dir)

    def _rel_or_empty(p: str | None) -> str:
        if not p:
            return ""
        try:
            return Path(p).resolve().relative_to(proj).as_posix()
        except ValueError:
            return p

    dev = profile.target.device or "STM32H563ZI"
    lines = [
        "# MCU Agent Harness 工程配置文件",
        "# 文档说明：未填写的路径会自动按工程目录扫描最新产物",
        "",
        "[target]",
        f'device = "{dev}"',
        f'probe_backend = "{profile.target.probe_backend}"   # "jlink" | "openocd" | "mock"',
        f'interface = "{profile.target.interface}"           # "SWD" | "JTAG"',
        f'speed = "{profile.target.speed}"',
    ]
    if profile.target.openocd_target:
        lines.append(f'openocd_interface = "{profile.target.openocd_interface}"')
        lines.append(f'openocd_target = "{profile.target.openocd_target}"')
    if profile.target.flash_base is not None:
        lines.append(f'flash_base = "0x{profile.target.flash_base:08X}"')

    lines.extend(
        [
            "",
            "[build]",
            f'system = "{profile.build.system}"                  # "auto" | "cmake" | "make"',
            f'build_type = "{profile.build.build_type}"',
            f'generator = "{profile.build.generator}"',
            "",
            "[paths]",
            f'svd = "{_rel_or_empty(profile.paths.svd_path)}"',
            f'elf = "{_rel_or_empty(profile.paths.elf_path)}"',
            "",
            "[safety]",
            "allow_flash_without_confirm = false",
            "allow_peripheral_write_without_confirm = false",
            "allow_option_bytes = false                 # 严禁开启，防止误写 Option Bytes / Product State 锁芯片",
            'protected_peripherals = ["FLASH", "PWR", "RCC", "TIM1", "TIM8", "HRTIM1", "IWDG", "WWDG"]',
            "",
            "[architecture]",
            f'preset = "{profile.arch.preset}"                # "layered-4" (App/Service/BSP/PAL) | "custom" | "none"',
            "",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------


def _expect_table(root_dict: dict[str, Any], key: str) -> dict[str, Any]:
    val = root_dict.get(key, {})
    if not isinstance(val, dict):
        raise ConfigError(f"[{key}] in mcu-harness.toml must be a table")
    return val


def _parse_opt_int(val: Any, field_name: str) -> int | None:
    if val in (None, ""):
        return None
    if isinstance(val, bool):
        raise ConfigError(f"{field_name} cannot be boolean")
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        try:
            return int(val.strip().replace("_", ""), 0)
        except ValueError as e:
            raise ConfigError(f"Invalid integer in {field_name}: {val!r}") from e
    raise ConfigError(f"Invalid integer in {field_name}: {val!r}")


def _resolve_opt_path(root: Path, raw: Any) -> str | None:
    if raw in (None, ""):
        return None
    s = str(raw).strip()
    if not s:
        return None
    p = Path(s).expanduser()
    if not p.is_absolute():
        p = (root / p).resolve()
    return str(p)


def _pick_path(
    root: Path,
    *,
    explicit: Any,
    env_var: str | None,
    scanned: Path | None,
    key: str,
    sources: dict[str, str],
) -> str | None:
    resolved_exp = _resolve_opt_path(root, explicit)
    if resolved_exp:
        sources[key] = "manifest"
        return resolved_exp
    if env_var and os.environ.get(env_var):
        sources[key] = f"env:{env_var}"
        return str(Path(os.environ[env_var]).expanduser().resolve())
    if scanned is not None:
        sources[key] = "auto_scan"
        return str(scanned)
    return None


def _rel(p: Path | None, root: Path) -> str:
    if p is None:
        return ""
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return str(p)


def _scan_ioc(root: Path) -> tuple[Path | None, str | None]:
    candidates = sorted(root.glob("*.ioc"))
    if not candidates:
        for sub in sorted(root.iterdir()):
            if sub.is_dir() and sub.name not in _SKIP_SCAN_DIRS and not sub.name.startswith("."):
                candidates.extend(sorted(sub.glob("*.ioc")))
    if not candidates:
        return None, None

    ioc = candidates[0]
    try:
        text = ioc.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ioc, None

    kv: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        kv[k.strip()] = v.strip()

    raw_mcu = kv.get("Mcu.UserName") or kv.get("Mcu.CPN") or kv.get("Mcu.Name")
    return ioc, normalize_cubemx_mcu_name(raw_mcu) if raw_mcu else None


def _scan_build_files(root: Path) -> tuple[str, str | None, str | None]:
    cmake_file = root / "CMakeLists.txt"
    makefile = next((root / n for n in ("GNUmakefile", "makefile", "Makefile") if (root / n).is_file()), None)

    system = "cmake" if cmake_file.is_file() else ("make" if makefile is not None else "unknown")
    dev_hint: str | None = None
    mcpu_hint: str | None = None

    for bf in [cmake_file, makefile]:
        if bf is None or not bf.is_file():
            continue
        try:
            content = bf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if dev_hint is None:
            m_dev = _RE_DEFINE_MCU.search(content)
            if m_dev:
                # 例如 STM32H563xx -> STM32H563, STM32F103xB -> STM32F103XB
                token = m_dev.group(1)
                dev_hint = re.sub(r"[xX_]+$", "", token).upper()
        if mcpu_hint is None:
            m_cpu = _RE_MCPU.search(content)
            if m_cpu:
                mcpu_hint = m_cpu.group(1)

    return system, dev_hint, mcpu_hint


def _scan_project_files(root: Path, *, device_hint: str | None = None) -> dict[str, Path]:
    found_elfs: list[tuple[float, Path]] = []
    found_maps: list[tuple[float, Path]] = []
    found_svds: list[Path] = []
    compdb: Path | None = None

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_SCAN_DIRS]
        rel_depth = len(Path(dirpath).relative_to(root).parts)
        if rel_depth > 4:
            dirnames.clear()
            continue

        for fname in filenames:
            p = Path(dirpath) / fname
            try:
                st = p.stat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue

            low = fname.lower()
            if fname == "compile_commands.json" and compdb is None:
                compdb = p
            elif low.endswith((".elf", ".axf")):
                found_elfs.append((st.st_mtime, p))
            elif low.endswith(".map"):
                found_maps.append((st.st_mtime, p))
            elif low.endswith((".svd", ".svd.gz")):
                found_svds.append(p)

    # 额外检查用户缓存目录 ~/.cache/mcu-harness/svd/
    if not found_svds and device_hint:
        cache_dir = Path.home() / ".cache" / "mcu-harness" / "svd"
        if cache_dir.is_dir():
            for cand in sorted(cache_dir.glob("*.svd*")):
                found_svds.append(cand)

    out: dict[str, Path] = {}
    if found_elfs:
        found_elfs.sort(key=lambda x: x[0], reverse=True)
        out["elf"] = found_elfs[0][1]
    if found_maps:
        found_maps.sort(key=lambda x: x[0], reverse=True)
        out["map"] = found_maps[0][1]
    if compdb is not None:
        out["compile_commands"] = compdb
    if found_svds:
        out["svd"] = _best_matching_svd(found_svds, device_hint)
    return out


def _best_matching_svd(svds: list[Path], device_hint: str | None) -> Path:
    if not device_hint or len(svds) == 1:
        return svds[0]
    hint = device_hint.upper()
    # 按最长公共前缀挑选最匹配的 SVD（例如 STM32H563ZI 优先命中 STM32H563.svd）
    scored: list[tuple[int, Path]] = []
    for p in svds:
        stem = p.name.upper().split(".")[0]
        common = 0
        for a, b in zip(hint, stem):
            if a == b:
                common += 1
            else:
                break
        scored.append((common, p))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _parse_range_list(raw_list: Any, field_name: str) -> tuple[tuple[int, int, str], ...]:
    if not raw_list:
        return ()
    if not isinstance(raw_list, list):
        raise ConfigError(f"{field_name} must be a list of tables")
    parsed: list[tuple[int, int, str]] = []
    for idx, item in enumerate(raw_list):
        if not isinstance(item, dict):
            raise ConfigError(f"{field_name}[{idx}] must be a table with start/end")
        start = _parse_opt_int(item.get("start"), f"{field_name}[{idx}].start")
        end = _parse_opt_int(item.get("end"), f"{field_name}[{idx}].end")
        if start is None or end is None or end < start:
            raise ConfigError(f"{field_name}[{idx}] requires valid start <= end")
        reason = str(item.get("reason") or "custom rule")
        parsed.append((start, end, reason))
    return tuple(parsed)


def _parse_safety_section(sec: dict[str, Any]) -> SafetyConfig:
    prot = sec.get("protected_peripherals")
    if prot is not None and not isinstance(prot, list):
        raise ConfigError("safety.protected_peripherals must be a list of strings")
    default_prot = SafetyConfig().protected_peripherals
    return SafetyConfig(
        allow_flash_without_confirm=bool(sec.get("allow_flash_without_confirm", False)),
        allow_peripheral_write_without_confirm=bool(sec.get("allow_peripheral_write_without_confirm", False)),
        allow_option_bytes=bool(sec.get("allow_option_bytes", False)),
        deny_ranges=_parse_range_list(sec.get("deny_ranges"), "safety.deny_ranges"),
        confirm_ranges=_parse_range_list(sec.get("confirm_ranges"), "safety.confirm_ranges"),
        protected_peripherals=tuple(str(x).upper() for x in prot) if prot is not None else default_prot,
    )


def _parse_arch_section(sec: dict[str, Any]) -> ArchConfig:
    preset = str(sec.get("preset") or "layered-4").strip().lower()
    if preset not in ("layered-4", "custom", "none"):
        raise ConfigError(f"Unsupported architecture.preset {preset!r} (expected layered-4, custom, or none)")

    raw_layers = sec.get("layers")
    if raw_layers is not None:
        if not isinstance(raw_layers, list):
            raise ConfigError("architecture.layers must be a list of tables")
        layers: list[LayerRule] = []
        for idx, item in enumerate(raw_layers):
            if not isinstance(item, dict) or not item.get("name") or not item.get("paths"):
                raise ConfigError(f"architecture.layers[{idx}] must contain 'name' and 'paths'")
            layers.append(
                LayerRule(
                    name=str(item["name"]),
                    paths=tuple(str(x) for x in item["paths"]),
                    allow_depend_on=tuple(str(x) for x in item.get("allow_depend_on", [])),
                    forbid_headers=tuple(str(x) for x in item.get("forbid_headers", [])),
                    isolate_siblings=bool(item.get("isolate_siblings", False)),
                )
            )
        resolved_layers = tuple(layers)
    elif preset == "none":
        resolved_layers = ()
    else:
        resolved_layers = DEFAULT_FOUR_LAYER_RULES

    isr_pats = sec.get("isr_patterns")
    forb_isr = sec.get("forbidden_in_isr")
    return ArchConfig(
        preset=preset,
        layers=resolved_layers,
        isr_patterns=tuple(str(x) for x in isr_pats) if isinstance(isr_pats, list) else ArchConfig().isr_patterns,
        forbidden_in_isr=(
            tuple(str(x) for x in forb_isr) if isinstance(forb_isr, list) else DEFAULT_FORBIDDEN_IN_ISR
        ),
    )
