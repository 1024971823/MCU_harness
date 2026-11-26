"""统一 27 工具注册表（6 个工作区工具 + 21 个嵌入式 MCU 工具）。

设计约束：
    1. 绝不导入 `mcu_harness.server` 或 `fastmcp`，直接调用底层核心模块。
    2. 严格执行硬件安全隔离：`write_memory`、`flash_firmware`、`run_hil_tests` 的 LLM JSON Schema
       绝对不暴露 `confirm` 参数；它们的 handler 强制使用 `ctx.confirmed_by_interlock`
       并交由 `ctx.safety_guard` / `run_hil_suite` 判定。
    3. 所有 27 个工具按 `read_only` / `workspace_write` / `hardware_state` / `hardware_danger` / `shell`
       五级风险分类，供安全联锁（Interlock）策略消费。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from mcu_harness.agent.workspace_tools import (
    tool_edit_file,
    tool_list_files,
    tool_read_file,
    tool_run_shell,
    tool_search_files,
    tool_write_file,
)
from mcu_harness.arch import ArchError, ScaffoldError
from mcu_harness.arch import check_architecture as _run_check_architecture
from mcu_harness.arch import scaffold_code as _run_scaffold_code
from mcu_harness.build import (
    FootprintError,
    analyze_footprint as _run_analyze_footprint,
    build_firmware as _run_build_firmware,
    generate_compile_commands as _run_generate_compdb,
)
from mcu_harness.config import ConfigError, ProjectProfile, discover_project, generate_manifest_toml
from mcu_harness.crash import diagnose_crash as _run_diagnose_crash
from mcu_harness.diagnose import (
    diagnose_peripheral as _run_diagnose_peripheral,
    inspect_board_context as _run_inspect_board_context,
    scan_configured_gpio_pins as _run_scan_gpio_pins,
)
from mcu_harness.dwarf import (
    DwarfError,
    LiveWatchError,
    load_dwarf_index,
    read_variable as _run_read_variable,
    read_variables as _run_read_variables,
    sample_variable as _run_sample_variable,
)
from mcu_harness.hil import (
    HilError,
    decode_with_sigrok as _run_decode_with_sigrok,
    run_hil_suite as _run_hil_suite,
)
from mcu_harness.log import RttLogCollector, UartLogCollector, list_serial_ports
from mcu_harness.probe import PeripheralSnapshot, ProbeManager, inspect_peripheral_registers
from mcu_harness.rtos import RtosError, inspect_rtos as _run_inspect_rtos
from mcu_harness.safety import SafetyGuard
from mcu_harness.svd import DecodedRegister, decode_register, find_peripheral, find_register, load_svd
from mcu_harness.trace import (
    TraceError,
    clear_watchpoints as _run_clear_watchpoints,
    list_watchpoints as _run_list_watchpoints,
    measure_cycles as _run_measure_cycles,
    profile_pc_hotspots as _run_profile_pc_hotspots,
    set_watchpoint as _run_set_watchpoint,
)

VALID_RISK_LEVELS: frozenset[str] = frozenset(
    {"read_only", "workspace_write", "hardware_state", "hardware_danger", "shell"}
)


@dataclass
class ToolContext:
    project_dir: str
    probe_manager: ProbeManager
    rtt_collector: RttLogCollector
    uart_collector: UartLogCollector
    safety_guard: SafetyGuard
    confirmed_by_interlock: bool = False
    profile: ProjectProfile | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    risk_level: str
    handler: Callable[[dict[str, Any], ToolContext], dict[str, Any]]


class ToolRegistry:
    """Agent 工具规格与处理器注册表。"""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.risk_level not in VALID_RISK_LEVELS:
            raise ValueError(f"Invalid risk_level {spec.risk_level!r} for tool {spec.name!r}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def list_specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def to_llm_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            }
            for spec in self._specs.values()
        ]


# ---------------------------------------------------------------------------
# 通用参数解析辅助函数
# ---------------------------------------------------------------------------


def _resolve_project_dir(args: dict[str, Any], ctx: ToolContext) -> str:
    raw = args.get("project_dir")
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    return ctx.project_dir


def _try_profile(ctx: ToolContext, project_dir: str | None = None) -> ProjectProfile | None:
    explicit_dir = str(project_dir).strip() if project_dir and str(project_dir).strip() else None
    if explicit_dir is None and ctx.profile is not None:
        return ctx.profile
    cand = explicit_dir or ctx.project_dir or os.environ.get("MCU_HARNESS_PROJECT_DIR")
    if not cand:
        return None
    if ctx.profile is not None and str(Path(cand).expanduser().resolve()) == ctx.profile.project_dir:
        return ctx.profile
    try:
        return discover_project(cand)
    except Exception:
        return None


def _parse_address(raw: str | int, field_name: str = "address") -> int:
    if isinstance(raw, bool):
        raise ValueError(f"Invalid {field_name}: {raw!r}")
    addr = int(raw.strip().replace("_", ""), 0) if isinstance(raw, str) else int(raw)
    if addr < 0 or addr > 0xFFFFFFFF:
        raise ValueError(f"{field_name} out of 32-bit range (0x00000000..0xFFFFFFFF): {raw!r}")
    return addr


def _resolve_elf(args: dict[str, Any], prof: ProjectProfile | None) -> str | None:
    return (
        args.get("elf_path")
        or os.environ.get("MCU_HARNESS_ELF_PATH")
        or (prof.paths.elf_path if prof else None)
    )


def _resolve_svd(args: dict[str, Any], prof: ProjectProfile | None) -> str | None:
    return (
        args.get("svd_path")
        or os.environ.get("MCU_HARNESS_SVD_PATH")
        or (prof.paths.svd_path if prof else None)
    )


def _resolve_device_and_backend(args: dict[str, Any], prof: ProjectProfile | None) -> tuple[str | None, str | None]:
    eff_dev = args.get("device") or (prof.target.device if prof else None)
    eff_backend = args.get("backend") or (prof.target.probe_backend if prof and prof.manifest_path else None)
    return eff_dev, eff_backend


# ---------------------------------------------------------------------------
# 6 个工作区工具 Handler
# ---------------------------------------------------------------------------


def _handle_read_file(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    try:
        return tool_read_file(
            ctx.project_dir,
            str(args["path"]),
            start_line=int(args.get("start_line", 1)),
            end_line=int(args.get("end_line", 200)),
        )
    except Exception as exc:
        return {"error": str(exc)}


def _handle_edit_file(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    try:
        return tool_edit_file(
            ctx.project_dir,
            str(args["path"]),
            str(args["old_text"]),
            str(args["new_text"]),
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _handle_write_file(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    try:
        return tool_write_file(
            ctx.project_dir,
            str(args["path"]),
            str(args["content"]),
            overwrite=bool(args.get("overwrite", False)),
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _handle_list_files(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    try:
        return tool_list_files(
            ctx.project_dir,
            path=str(args.get("path", ".")),
            max_depth=int(args.get("max_depth", 3)),
            max_entries=int(args.get("max_entries", 200)),
        )
    except Exception as exc:
        return {"error": str(exc)}


def _handle_search_files(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    try:
        return tool_search_files(
            ctx.project_dir,
            str(args["pattern"]),
            file_glob=str(args.get("file_glob", "*")),
            max_results=int(args.get("max_results", 50)),
        )
    except Exception as exc:
        return {"error": str(exc)}


def _handle_run_shell(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    try:
        return tool_run_shell(
            ctx.project_dir,
            str(args["command"]),
            timeout_s=float(args.get("timeout_s", 60.0)),
        )
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# 21 个嵌入式 MCU 工具 Handler
# ---------------------------------------------------------------------------


def _handle_inspect_project(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    project_dir = _resolve_project_dir(args, ctx)
    act = str(args.get("action", "inspect")).strip().lower()
    manifest_path = args.get("manifest_path")
    force = bool(args.get("force", False))
    try:
        prof = discover_project(project_dir, manifest_path=manifest_path)
        if act == "inspect":
            return prof.to_dict()
        if act == "init":
            dst = Path(prof.project_dir) / "mcu-harness.toml"
            if dst.exists() and not force:
                return {"error": f"{dst} already exists (pass force=True to overwrite)"}
            dst.write_text(generate_manifest_toml(prof), encoding="utf-8")
            return {"created": str(dst), "profile": discover_project(prof.project_dir).to_dict()}
        return {"error": f"Unknown action {act!r} (expected 'inspect' or 'init')"}
    except (ConfigError, OSError) as exc:
        return {"error": str(exc)}


def _handle_build_firmware(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    project_dir = _resolve_project_dir(args, ctx)
    build_system = str(args.get("build_system", "auto"))
    extra_args = args.get("extra_args")
    try:
        prof = _try_profile(ctx, project_dir)
        b_cfg = prof.build if prof and prof.manifest_path else None
        merged_extra = list(b_cfg.extra_args) if b_cfg and extra_args is None else extra_args

        result = _run_build_firmware(
            project_dir=project_dir,
            build_dir=args.get("build_dir") or (b_cfg.build_dir if b_cfg else None),
            target=args.get("target") or (b_cfg.target if b_cfg else None),
            build_type=args.get("build_type") or (b_cfg.build_type if b_cfg else "Debug"),
            toolchain_file=args.get("toolchain_file") or (b_cfg.toolchain_file if b_cfg else None),
            generator=args.get("generator") or (b_cfg.generator if b_cfg else "Ninja"),
            extra_cmake_args=args.get("extra_cmake_args"),
            clean=bool(args.get("clean", False)),
            reconfigure=bool(args.get("reconfigure", False)),
            jobs=args.get("jobs"),
            max_errors=int(args.get("max_errors", 10)),
            max_warnings=int(args.get("max_warnings", 10)),
            build_system=build_system if build_system != "auto" or not b_cfg else b_cfg.system,
            makefile=args.get("makefile") or (b_cfg.makefile if b_cfg else None),
            extra_args=merged_extra,
        )
        return result.to_dict()
    except Exception as exc:
        return {"success": False, "stage": "precheck", "error": str(exc), "message": str(exc)}


def _handle_generate_compile_commands(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    project_dir = _resolve_project_dir(args, ctx)
    try:
        res = _run_generate_compdb(
            project_dir=project_dir,
            build_dir=args.get("build_dir"),
            makefile=args.get("makefile"),
            extra_args=args.get("extra_args"),
            build_system=str(args.get("build_system", "auto")),
            toolchain_file=args.get("toolchain_file"),
        )
        return res.to_dict()
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _handle_analyze_footprint(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    top_n = int(args.get("top_n", 15))
    if top_n < 1 or top_n > 100:
        return {"error": "top_n must be between 1 and 100"}
    project_dir = _resolve_project_dir(args, ctx)
    prof = _try_profile(ctx, project_dir)
    eff_elf = _resolve_elf(args, prof)
    eff_map = args.get("map_path") or (prof.paths.map_path if prof else None)
    try:
        rep = _run_analyze_footprint(elf_path=eff_elf, map_path=eff_map, project_dir=project_dir, top_n=top_n)
        return rep.to_dict()
    except (FootprintError, FileNotFoundError, ValueError) as exc:
        return {"error": str(exc)}


def _handle_inspect_svd(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    project_dir = _resolve_project_dir(args, ctx)
    prof = _try_profile(ctx, project_dir)
    resolved_svd = _resolve_svd(args, prof)
    if not resolved_svd:
        return {"error": "请提供 svd_path / project_dir 参数，或设置环境变量 MCU_HARNESS_SVD_PATH"}

    try:
        device = load_svd(resolved_svd)
    except Exception as exc:
        return {"error": str(exc)}

    peripheral = args.get("peripheral")
    name_filter = args.get("name_filter")
    if not peripheral:
        flt = str(name_filter or "").upper()
        items = [p for p in device.peripherals if flt in p.name.upper()]
        return {
            "device": device.name,
            "total": len(items),
            "peripherals": [
                {"name": p.name, "base_address": f"0x{p.base_address:08X}", "description": p.description}
                for p in items
            ],
        }

    try:
        periph = find_peripheral(device, str(peripheral))
    except KeyError as exc:
        return {"error": str(exc)}

    register = args.get("register")
    if not register:
        flt = str(name_filter or "").upper()
        regs = [r for r in periph.registers if flt in r.name.upper()]
        return {
            "device": device.name,
            "peripheral": periph.name,
            "base_address": f"0x{periph.base_address:08X}",
            "total": len(regs),
            "registers": [
                {
                    "name": r.name,
                    "address": f"0x{periph.reg_address(r):08X}",
                    "offset": f"0x{r.address_offset:04X}",
                    "reset_value": f"0x{r.reset_value:08X}" if r.reset_value is not None else None,
                    "description": r.description,
                }
                for r in regs
            ],
        }

    try:
        reg = find_register(periph, str(register))
        raw_val = args.get("raw_value")
        val = raw_val if raw_val is not None else (reg.reset_value if reg.reset_value is not None else 0)
        decoded = decode_register(periph, reg, val)
    except (KeyError, ValueError) as exc:
        return {"error": str(exc)}

    return decoded.to_dict()


def _handle_probe_control(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    act = str(args.get("action", "info")).strip().lower()
    project_dir = _resolve_project_dir(args, ctx)
    prof = _try_profile(ctx, project_dir)
    t_cfg = prof.target if prof else None
    eff_dev, eff_backend = _resolve_device_and_backend(args, prof)
    eff_ocd_if = args.get("openocd_interface") or (t_cfg.openocd_interface if t_cfg else None)
    eff_ocd_tgt = args.get("openocd_target") or (t_cfg.openocd_target if t_cfg else None)

    try:
        if act == "audit":
            entries = [e.to_dict() for e in ctx.safety_guard.recent_audit(limit=50)]
            return {"count": len(entries), "entries": entries}
        if act == "disconnect":
            ctx.probe_manager.disconnect()
            return {"connected": False, "message": "Probe disconnected"}
        if act == "connect":
            speed_val = args.get("speed")
            info = ctx.probe_manager.connect(
                backend=eff_backend,
                device=eff_dev,
                interface=args.get("interface") or (t_cfg.interface if t_cfg else None),
                speed=speed_val if speed_val is not None else (t_cfg.speed if t_cfg else None),
                serial_no=args.get("serial_no") or (t_cfg.serial_no if t_cfg else None),
                openocd_interface=eff_ocd_if,
                openocd_target=eff_ocd_tgt,
                openocd_tcl_port=args.get("openocd_tcl_port") or (t_cfg.openocd_tcl_port if t_cfg else None),
                reconnect=bool(args.get("reconnect", False)),
            )
            return info.to_dict()
        if act == "info":
            with ctx.probe_manager.session(
                device=eff_dev, backend=eff_backend, openocd_interface=eff_ocd_if, openocd_target=eff_ocd_tgt
            ) as probe:
                return probe.info().to_dict()
        if act == "halt":
            with ctx.probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
                ok = probe.halt()
                ctx.safety_guard.record_audit("halt", probe.info().device, detail=f"changed={ok}")
                return {"action": "halt", "halted": probe.is_halted(), "changed": ok}
        if act == "resume":
            with ctx.probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
                ok = probe.resume()
                ctx.safety_guard.record_audit("resume", probe.info().device, detail=f"changed={ok}")
                return {"action": "resume", "halted": probe.is_halted(), "changed": ok}
        if act == "reset":
            halt_on_reset = bool(args.get("halt_on_reset", False))
            with ctx.probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
                res = probe.reset(halt=halt_on_reset)
                ctx.safety_guard.record_audit("reset", probe.info().device, detail=f"halt={halt_on_reset}")
                return res.to_dict()
        if act == "regs":
            reg_names = args.get("reg_names")
            with ctx.probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
                halted_temp = probe.halt()
                try:
                    regs = probe.read_core_regs(reg_names)
                finally:
                    if halted_temp:
                        probe.resume()
                return {
                    "registers": {k: f"0x{v:08X}" for k, v in regs.items()},
                    "raw": regs,
                }
        return {"error": f"Unknown action {act!r} (expected info/connect/disconnect/halt/resume/reset/regs/audit)"}
    except Exception as exc:
        return {"error": str(exc)}


def _handle_read_memory(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    count = int(args.get("count", 1))
    width = int(args.get("width", 32))
    if count < 1 or count > 256:
        return {"error": "count must be between 1 and 256"}
    if width not in (8, 16, 32):
        return {"error": "width must be 8, 16, or 32"}
    try:
        addr = _parse_address(args["address"], "address")
    except (KeyError, ValueError) as exc:
        return {"error": str(exc)}

    step = width // 8
    if (addr + count * step - 1) > 0xFFFFFFFF:
        return {"error": f"Address range out of 32-bit bounds: {args['address']!r} + {count}*{step}B"}
    if width == 32 and addr % 4 != 0:
        return {"error": f"Unaligned 32-bit address 0x{addr:08X} (must be 4-byte aligned)"}
    if width == 16 and addr % 2 != 0:
        return {"error": f"Unaligned 16-bit address 0x{addr:08X} (must be 2-byte aligned)"}

    project_dir = _resolve_project_dir(args, ctx)
    prof = _try_profile(ctx, project_dir)
    eff_dev, eff_backend = _resolve_device_and_backend(args, prof)

    try:
        with ctx.probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
            if width == 32:
                vals = probe.read_u32(addr, count)
                hex_vals = [f"0x{v:08X}" for v in vals]
            elif width == 16:
                vals = probe.read_u16(addr, count)
                hex_vals = [f"0x{v:04X}" for v in vals]
            else:
                vals = probe.read_u8(addr, count)
                hex_vals = [f"0x{v:02X}" for v in vals]
    except Exception as exc:
        return {"error": str(exc)}

    return {
        "address": f"0x{addr:08X}",
        "width": width,
        "count": count,
        "hex": hex_vals,
        "values": vals,
    }


def _handle_write_memory(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    values = args.get("values")
    width = int(args.get("width", 32))
    if not isinstance(values, list) or not values or len(values) > 256:
        return {"error": "values length must be between 1 and 256"}
    if width not in (8, 16, 32):
        return {"error": "width must be 8, 16, or 32"}

    try:
        addr = _parse_address(args["address"], "address")
        parsed_vals: list[int] = []
        for raw_v in values:
            if isinstance(raw_v, bool):
                raise ValueError(f"Invalid boolean value in values: {raw_v!r}")
            parsed_vals.append(int(raw_v.strip().replace("_", ""), 0) if isinstance(raw_v, str) else int(raw_v))
    except (KeyError, ValueError) as exc:
        return {"error": str(exc)}

    step = width // 8
    total_bytes = len(parsed_vals) * step
    if (addr + total_bytes - 1) > 0xFFFFFFFF:
        return {"error": f"Write range exceeds 32-bit address space: 0x{addr:08X} + {total_bytes}B"}
    if width == 32 and addr % 4 != 0:
        return {"error": f"Unaligned 32-bit write address 0x{addr:08X}"}
    if width == 16 and addr % 2 != 0:
        return {"error": f"Unaligned 16-bit write address 0x{addr:08X}"}

    project_dir = _resolve_project_dir(args, ctx)
    prof = _try_profile(ctx, project_dir)
    eff_dev, eff_backend = _resolve_device_and_backend(args, prof)
    safety_cfg = prof.safety if prof else None

    # 安全铁律：绝对不读取模型入参中的 confirm，强制使用 ctx.confirmed_by_interlock
    dec = ctx.safety_guard.check_memory_write(
        addr,
        total_bytes,
        device=eff_dev,
        config=safety_cfg,
        confirmed=ctx.confirmed_by_interlock,
        peripheral_hint=args.get("peripheral_hint"),
    )
    if not dec.allowed:
        return {"allowed": False, "safety": dec.to_dict(), "error": dec.reason}

    try:
        with ctx.probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
            if width == 32:
                probe.write_u32(addr, parsed_vals)
            elif width == 16:
                probe.write_u16(addr, parsed_vals)
            else:
                probe.write_u8(addr, parsed_vals)
    except Exception as exc:
        return {"error": str(exc)}

    mask = (1 << width) - 1
    hex_digits = width // 4
    return {
        "allowed": True,
        "address": f"0x{addr:08X}",
        "width": width,
        "count": len(parsed_vals),
        "written_hex": [f"0x{(v & mask):0{hex_digits}X}" for v in parsed_vals],
        "safety": dec.to_dict(),
    }


def _handle_flash_firmware(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    project_dir = _resolve_project_dir(args, ctx)
    prof = _try_profile(ctx, project_dir)
    resolved_fw = (
        args.get("firmware_path")
        or os.environ.get("MCU_HARNESS_ELF_PATH")
        or (prof.paths.elf_path if prof else None)
    )
    if not resolved_fw:
        return {"error": "请提供 firmware_path 参数，或传入已编译出 .elf 的 project_dir"}

    eff_dev, eff_backend = _resolve_device_and_backend(args, prof)
    safety_cfg = prof.safety if prof else None

    raw_addr = args.get("address")
    try:
        load_addr = _parse_address(raw_addr, "address") if raw_addr is not None else None
    except ValueError as exc:
        return {"error": str(exc)}

    if load_addr is None and str(resolved_fw).lower().endswith(".bin") and prof and prof.target.flash_base is not None:
        load_addr = prof.target.flash_base

    # 安全铁律：强制使用 ctx.confirmed_by_interlock
    dec = ctx.safety_guard.check_flash(
        str(resolved_fw),
        device=eff_dev,
        config=safety_cfg,
        confirmed=ctx.confirmed_by_interlock,
    )
    if not dec.allowed:
        return {"allowed": False, "safety": dec.to_dict(), "error": dec.reason}

    if ctx.rtt_collector.is_running():
        ctx.rtt_collector.stop()

    try:
        with ctx.probe_manager.session(
            device=eff_dev,
            backend=eff_backend,
            openocd_interface=prof.target.openocd_interface if prof else None,
            openocd_target=prof.target.openocd_target if prof else None,
        ) as probe:
            res = probe.flash(
                resolved_fw,
                address=load_addr,
                verify=bool(args.get("verify", True)),
                reset_after=bool(args.get("reset_after", True)),
                run_after=bool(args.get("run_after", True)),
            )
            return {"allowed": True, "safety": dec.to_dict(), **res.to_dict()}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _handle_inspect_peripheral(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    project_dir = _resolve_project_dir(args, ctx)
    prof = _try_profile(ctx, project_dir)
    resolved_svd = _resolve_svd(args, prof)
    if not resolved_svd:
        return {"error": "请提供 svd_path / project_dir 参数，或设置环境变量 MCU_HARNESS_SVD_PATH"}

    eff_dev, eff_backend = _resolve_device_and_backend(args, prof)
    try:
        svd_dev = load_svd(resolved_svd)
        with ctx.probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
            res = inspect_peripheral_registers(
                probe,
                svd_dev,
                str(args["peripheral"]),
                register_name=args.get("register"),
                registers=args.get("registers"),
                read_all=bool(args.get("read_all", False)),
            )
    except Exception as exc:
        return {"error": str(exc)}

    if bool(args.get("diff_only", False)) and isinstance(res, PeripheralSnapshot):
        filtered = [r for r in res.registers if r.is_reset_value is not True]
        out = res.to_dict()
        out["total_read"] = len(res.registers)
        out["registers"] = [r.to_dict() for r in filtered]
        return out

    if isinstance(res, DecodedRegister):
        return res.to_dict()
    return res.to_dict()


def _handle_diagnose_peripheral(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    project_dir = _resolve_project_dir(args, ctx)
    prof = _try_profile(ctx, project_dir)
    resolved_svd = _resolve_svd(args, prof)
    if not resolved_svd:
        return {"error": "请提供 svd_path / project_dir 参数，或设置环境变量 MCU_HARNESS_SVD_PATH"}

    eff_dev, eff_backend = _resolve_device_and_backend(args, prof)
    peripheral = str(args["peripheral"])
    try:
        svd_dev = load_svd(resolved_svd)
        with ctx.probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
            if peripheral.strip().upper() == "GPIO_ALL":
                pins = _run_scan_gpio_pins(probe, svd_dev)
                return {"device": svd_dev.name, "configured_pin_count": len(pins), "pins": [p.to_dict() for p in pins]}
            rep = _run_diagnose_peripheral(
                probe,
                svd_dev,
                peripheral,
                include_gpio_mux=bool(args.get("include_gpio_mux", True)),
            )
            return rep.to_dict()
    except Exception as exc:
        return {"error": str(exc)}


def _handle_inspect_board(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    project_dir = _resolve_project_dir(args, ctx)
    prof = _try_profile(ctx, project_dir)
    live_check = bool(args.get("live_check", False))
    ioc_path = args.get("ioc_path")
    try:
        if not live_check:
            rep = _run_inspect_board_context(project_dir, ioc_path=ioc_path)
            return rep.to_dict()

        resolved_svd = _resolve_svd(args, prof)
        if not resolved_svd:
            return {"error": "live_check=True 需要提供 svd_path 或在工程/环境变量中配置 .svd"}
        svd_dev = load_svd(resolved_svd)
        eff_dev, eff_backend = _resolve_device_and_backend(args, prof)
        with ctx.probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
            rep = _run_inspect_board_context(project_dir, ioc_path=ioc_path, probe=probe, svd_device=svd_dev)
            return rep.to_dict()
    except Exception as exc:
        return {"error": str(exc)}


def _handle_watch_variable(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    act = str(args.get("action", "read")).strip().lower()
    project_dir = _resolve_project_dir(args, ctx)
    prof = _try_profile(ctx, project_dir)
    eff_elf = _resolve_elf(args, prof)
    if not eff_elf:
        return {"error": "请提供 elf_path 参数，或传入已编译出 .elf 的 project_dir"}

    try:
        if act == "list":
            idx = load_dwarf_index(eff_elf)
            flt = str(args.get("name_filter") or "").lower()
            vars_out = []
            for v in idx.variables.values():
                if flt and flt not in v.name.lower():
                    continue
                vars_out.append(
                    {
                        "name": v.name,
                        "address": f"0x{v.address:08X}" if v.address is not None else None,
                        "byte_size": v.byte_size,
                        "type": v.dwarf_type.display_name if v.dwarf_type else "unknown",
                    }
                )
            vars_out.sort(key=lambda x: x["name"])
            return {
                "elf_path": str(eff_elf),
                "degraded_symtab_only": idx.degraded_symtab_only,
                "count": len(vars_out),
                "variables": vars_out,
            }

        eff_dev, eff_backend = _resolve_device_and_backend(args, prof)
        deref = bool(args.get("deref", False))
        max_depth = int(args.get("max_depth", 4))
        expression = args.get("expression")
        expressions = args.get("expressions")

        if act == "read":
            if expressions:
                with ctx.probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
                    batch = _run_read_variables(probe, eff_elf, expressions, deref=deref, max_depth=max_depth)
                return {"variables": {k: val.to_dict() for k, val in batch.items()}}
            if not expression:
                return {"error": "expression (or expressions) is required when action='read'"}
            with ctx.probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
                val = _run_read_variable(probe, eff_elf, str(expression), deref=deref, max_depth=max_depth)
            return val.to_dict()

        if act == "sample":
            if not expression:
                return {"error": "expression is required when action='sample'"}
            with ctx.probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
                rep = _run_sample_variable(
                    probe,
                    eff_elf,
                    str(expression),
                    samples=int(args.get("samples", 20)),
                    interval_ms=float(args.get("interval_ms", 10.0)),
                )
            return rep.to_dict()

        return {"error": f"Unknown action {act!r} (expected 'read', 'sample', or 'list')"}
    except (DwarfError, LiveWatchError, FileNotFoundError, ValueError) as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": str(exc)}


def _handle_read_debug_log(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    act = str(args.get("action", "read")).strip().lower()
    src = str(args.get("source", "rtt")).strip().lower()
    if act == "list_ports":
        ports = [p.to_dict() for p in list_serial_ports()]
        return {"count": len(ports), "ports": ports}

    if src not in ("rtt", "uart"):
        return {"error": f"Unknown source {src!r} (expected 'rtt' or 'uart')"}
    tail_lines = int(args.get("tail_lines", 50))
    context_lines = int(args.get("context_lines", 2))
    if tail_lines < 1 or tail_lines > 500:
        return {"error": "tail_lines must be between 1 and 500"}
    if context_lines < 0 or context_lines > 50:
        return {"error": "context_lines must be between 0 and 50"}

    since_seq = args.get("since_seq")
    pattern = args.get("pattern")
    regex = bool(args.get("regex", False))
    include_partial = bool(args.get("include_partial", True))
    port = args.get("port")
    baudrate = int(args.get("baudrate", 115200))
    command = args.get("command")

    if src == "uart":
        try:
            if act == "status":
                return {"status": ctx.uart_collector.status().to_dict()}
            if act == "start":
                st_u = ctx.uart_collector.start(port=port, baudrate=baudrate)
                return {"status": st_u.to_dict()}
            if act == "stop":
                st_u = ctx.uart_collector.stop()
                return {"status": st_u.to_dict()}
            if act == "clear":
                cleared = ctx.uart_collector.clear()
                return {"cleared": cleared, "status": ctx.uart_collector.status().to_dict()}
            if act == "write":
                if not command:
                    return {"error": "command is required when action='write'"}
                if port:
                    ctx.uart_collector.start(port=port, baudrate=baudrate)
                sent = ctx.uart_collector.write(str(command))
                ctx.safety_guard.record_audit(
                    "uart_write", port or (ctx.uart_collector.status().port or "uart"), detail=str(command)[:80]
                )
                return {"written_bytes": sent, "source": "uart", "status": ctx.uart_collector.status().to_dict()}
            if act == "read":
                res_u, st_u = ctx.uart_collector.read(
                    tail_lines=tail_lines,
                    since_seq=since_seq,
                    pattern=pattern,
                    regex=regex,
                    context_lines=context_lines,
                    include_partial=include_partial,
                    port=port,
                    baudrate=baudrate,
                )
                return {"status": st_u.to_dict(), **res_u.to_dict()}
            return {"error": f"Unknown action {act!r} (expected read/write/start/stop/clear/status/list_ports)"}
        except Exception as exc:
            return {"error": str(exc)}

    block_address = args.get("block_address")
    try:
        addr = _parse_address(block_address, "block_address") if block_address is not None else None
    except ValueError as exc:
        return {"error": str(exc)}

    project_dir = _resolve_project_dir(args, ctx)
    prof = _try_profile(ctx, project_dir)
    eff_elf = args.get("elf_path") or (prof.paths.elf_path if prof else None)
    eff_dev = args.get("device") or (prof.target.device if prof else None)
    channel = int(args.get("channel", 0))

    try:
        if act == "status":
            return {"status": ctx.rtt_collector.status().to_dict()}
        if act == "start":
            st = ctx.rtt_collector.start(channel=channel, block_address=addr, elf_path=eff_elf, device=eff_dev)
            return {"status": st.to_dict()}
        if act == "stop":
            st = ctx.rtt_collector.stop()
            return {"status": st.to_dict()}
        if act == "clear":
            cleared = ctx.rtt_collector.clear()
            return {"cleared": cleared, "status": ctx.rtt_collector.status().to_dict()}
        if act == "write":
            if not command:
                return {"error": "command is required when action='write'"}
            ctx.rtt_collector.start(channel=channel, block_address=addr, elf_path=eff_elf, device=eff_dev)
            raw_cmd = str(command).encode("utf-8")
            with ctx.probe_manager.session(device=eff_dev) as probe:
                written = probe.rtt_write(raw_cmd, channel=channel)
            ctx.safety_guard.record_audit("rtt_write", f"ch{channel}", detail=str(command)[:80])
            return {"written_bytes": written, "channel": channel}
        if act == "read":
            res, st = ctx.rtt_collector.read(
                tail_lines=tail_lines,
                since_seq=since_seq,
                pattern=pattern,
                regex=regex,
                context_lines=context_lines,
                include_partial=include_partial,
                channel=channel,
                block_address=addr,
                elf_path=eff_elf,
                device=eff_dev,
            )
            return {"status": st.to_dict(), **res.to_dict()}
        return {"error": f"Unknown action {act!r} (expected read/write/start/stop/clear/status/list_ports)"}
    except Exception as exc:
        return {"error": str(exc)}


def _handle_diagnose_crash(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    project_dir = _resolve_project_dir(args, ctx)
    prof = _try_profile(ctx, project_dir)
    eff_elf = _resolve_elf(args, prof)
    eff_proj = project_dir or (prof.project_dir if prof else None)
    eff_dev, eff_backend = _resolve_device_and_backend(args, prof)

    try:
        with ctx.probe_manager.session(
            device=eff_dev,
            backend=eff_backend,
            openocd_interface=prof.target.openocd_interface if prof else None,
            openocd_target=prof.target.openocd_target if prof else None,
        ) as probe:
            report = _run_diagnose_crash(
                probe,
                elf_path=eff_elf,
                project_dir=eff_proj,
                keep_halted=bool(args.get("keep_halted", False)),
            )
            return report.to_dict()
    except Exception as exc:
        return {"error": str(exc)}


def _handle_check_architecture(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    project_dir = _resolve_project_dir(args, ctx)
    try:
        report = _run_check_architecture(project_dir, manifest_path=args.get("manifest_path"))
        return report.to_dict()
    except (ArchError, ConfigError, OSError) as exc:
        return {"error": str(exc)}


def _handle_scaffold_code(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    project_dir = _resolve_project_dir(args, ctx)
    try:
        res = _run_scaffold_code(
            project_dir,
            kind=str(args["kind"]),
            name=args.get("name"),
            overwrite=bool(args.get("overwrite", False)),
        )
        return res.to_dict()
    except (ScaffoldError, OSError, KeyError) as exc:
        return {"error": str(exc)}


def _handle_inspect_rtos(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    max_tasks = int(args.get("max_tasks", 64))
    if max_tasks < 1 or max_tasks > 256:
        return {"error": "max_tasks must be between 1 and 256"}
    project_dir = _resolve_project_dir(args, ctx)
    prof = _try_profile(ctx, project_dir)
    eff_elf = _resolve_elf(args, prof)
    if not eff_elf:
        return {"error": "请提供 elf_path 参数，或传入已编译出 .elf 的 project_dir"}

    eff_dev, eff_backend = _resolve_device_and_backend(args, prof)
    try:
        with ctx.probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
            rep = _run_inspect_rtos(
                probe,
                eff_elf,
                rtos=str(args.get("rtos", "auto")),
                max_tasks=max_tasks,
                check_stack=bool(args.get("check_stack", True)),
            )
            return rep.to_dict()
    except (RtosError, FileNotFoundError, ValueError) as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": str(exc)}


def _handle_trace_target(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    act = str(args.get("action", "profile_pc")).strip().lower()
    project_dir = _resolve_project_dir(args, ctx)
    prof = _try_profile(ctx, project_dir)
    eff_elf = _resolve_elf(args, prof)
    eff_proj = project_dir or (prof.project_dir if prof else None)
    eff_dev, eff_backend = _resolve_device_and_backend(args, prof)

    try:
        with ctx.probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
            if act == "measure_cycles":
                return _run_measure_cycles(
                    probe,
                    duration_ms=float(args.get("duration_ms", 50.0)),
                    core_hz=args.get("core_hz"),
                ).to_dict()
            if act == "profile_pc":
                return _run_profile_pc_hotspots(
                    probe,
                    elf_path=eff_elf,
                    project_dir=eff_proj,
                    samples=int(args.get("samples", 100)),
                    interval_ms=float(args.get("interval_ms", 1.0)),
                ).to_dict()
            if act == "set_watchpoint":
                watch_target = args.get("watch_target")
                if watch_target is None or watch_target == "":
                    return {"error": "watch_target is required when action='set_watchpoint'"}
                return _run_set_watchpoint(
                    probe,
                    watch_target,
                    access=str(args.get("watch_access", "write")),
                    size_bytes=args.get("watch_size"),
                    slot=args.get("slot"),
                    elf_path=eff_elf,
                ).to_dict()
            if act == "list_watchpoints":
                return _run_list_watchpoints(probe).to_dict()
            if act == "clear_watchpoints":
                return _run_clear_watchpoints(probe, slot=args.get("slot"))
            return {
                "error": (
                    f"Unknown action {act!r} (expected measure_cycles/profile_pc/"
                    "set_watchpoint/list_watchpoints/clear_watchpoints)"
                )
            }
    except (TraceError, FileNotFoundError, ValueError) as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": str(exc)}


def _handle_capture_logic(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    del ctx
    try:
        rep = _run_decode_with_sigrok(
            csv_path=args.get("csv_path"),
            input_file=args.get("input_file"),
            driver=args.get("driver"),
            channels=args.get("channels"),
            samplerate=str(args.get("samplerate", "4m")),
            samples=args.get("samples"),
            duration_ms=args.get("duration_ms"),
            protocol=args.get("protocol"),
            protocol_options=args.get("protocol_options"),
            time_unit_s=float(args.get("time_unit_s", 1.0)),
        )
        return rep.to_dict()
    except (HilError, ValueError, FileNotFoundError) as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": str(exc)}


def _handle_run_hil_tests(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    project_dir = _resolve_project_dir(args, ctx)
    prof = _try_profile(ctx, project_dir)
    eff_dev, eff_backend = _resolve_device_and_backend(args, prof)
    eff_elf = _resolve_elf(args, prof)
    eff_svd = _resolve_svd(args, prof)

    try:
        # 安全铁律：强制使用 ctx.confirmed_by_interlock，绝不接受入参 confirm
        with ctx.probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
            rep = _run_hil_suite(
                args["spec_path"],
                probe=probe,
                project_dir=project_dir,
                elf_path=eff_elf,
                svd_path=eff_svd,
                rtt_collector=ctx.rtt_collector,
                uart_collector=ctx.uart_collector,
                confirmed=ctx.confirmed_by_interlock,
            )
            return rep.to_dict()
    except (HilError, FileNotFoundError, ValueError, KeyError) as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# JSON Schema 构造辅助与默认 27 工具注册表构建
# ---------------------------------------------------------------------------


def _obj_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def build_default_registry() -> ToolRegistry:
    """构建包含 6 个工作区工具与 21 个嵌入式 MCU 工具的完整 27 工具注册表。"""
    reg = ToolRegistry()

    # ---- 6 个工作区工具 ----------------------------------------------------

    reg.register(
        ToolSpec(
            name="read_file",
            description="读取工作区内文本文件的指定行范围（带行号输出、总行数统计与二进制保护）。",
            risk_level="read_only",
            parameters=_obj_schema(
                {
                    "path": {"type": "string", "description": "工作区相对路径或工作区内绝对路径"},
                    "start_line": {"type": "integer", "description": "起始行号（从 1 开始，默认 1）"},
                    "end_line": {"type": "integer", "description": "结束行号（默认 200）"},
                },
                required=["path"],
            ),
            handler=_handle_read_file,
        )
    )

    reg.register(
        ToolSpec(
            name="edit_file",
            description="对工作区文本文件执行唯一精确字符串替换，并返回 unified diff 摘要。",
            risk_level="workspace_write",
            parameters=_obj_schema(
                {
                    "path": {"type": "string", "description": "目标文件路径"},
                    "old_text": {"type": "string", "description": "必须唯一匹配的原始文本片段"},
                    "new_text": {"type": "string", "description": "替换后的新文本片段"},
                },
                required=["path", "old_text", "new_text"],
            ),
            handler=_handle_edit_file,
        )
    )

    reg.register(
        ToolSpec(
            name="write_file",
            description="在工作区内创建或写入文件（自动建父目录；默认拒绝覆盖已存在文件，除非 overwrite=true）。",
            risk_level="workspace_write",
            parameters=_obj_schema(
                {
                    "path": {"type": "string", "description": "目标文件路径"},
                    "content": {"type": "string", "description": "写入的完整文本内容"},
                    "overwrite": {"type": "boolean", "description": "已存在时是否允许覆盖（默认 false）"},
                },
                required=["path", "content"],
            ),
            handler=_handle_write_file,
        )
    )

    reg.register(
        ToolSpec(
            name="list_files",
            description="列出工作区目录树（自动跳过 .git / build / __pycache__ / .venv / node_modules）。",
            risk_level="read_only",
            parameters=_obj_schema(
                {
                    "path": {"type": "string", "description": "相对工作区根目录的路径（默认 '.'）"},
                    "max_depth": {"type": "integer", "description": "最大递归深度（默认 3）"},
                    "max_entries": {"type": "integer", "description": "最多返回条目数（默认 200）"},
                }
            ),
            handler=_handle_list_files,
        )
    )

    reg.register(
        ToolSpec(
            name="search_files",
            description="在工作区文本文件中按正则表达式检索匹配行，返回 file:line: text 列表。",
            risk_level="read_only",
            parameters=_obj_schema(
                {
                    "pattern": {"type": "string", "description": "正则表达式"},
                    "file_glob": {"type": "string", "description": "文件名通配符（如 '*.c'、'*.h'，默认 '*'）"},
                    "max_results": {"type": "integer", "description": "最多返回匹配行数（默认 50）"},
                },
                required=["pattern"],
            ),
            handler=_handle_search_files,
        )
    )

    reg.register(
        ToolSpec(
            name="run_shell",
            description="在工作区目录下执行 Shell 命令并返回 return_code、stdout 与 stderr。",
            risk_level="shell",
            parameters=_obj_schema(
                {
                    "command": {"type": "string", "description": "要执行的 Shell 命令"},
                    "timeout_s": {"type": "number", "description": "超时时间秒数（默认 60）"},
                },
                required=["command"],
            ),
            handler=_handle_run_shell,
        )
    )

    # ---- 21 个嵌入式 MCU 工具 ----------------------------------------------

    reg.register(
        ToolSpec(
            name="inspect_project",
            description=(
                "自动扫描固件工程目录（识别 mcu-harness.toml、CubeMX .ioc、CMake/Makefile、最新 .elf/.map/.svd），"
                "返回目标芯片型号、内核代数、探针配置、安全策略与代码分层规则；也可通过 action='init' 生成 mcu-harness.toml。"
            ),
            risk_level="read_only",
            parameters=_obj_schema(
                {
                    "project_dir": {"type": "string", "description": "固件工程根目录路径（默认工作区根目录）"},
                    "action": {"type": "string", "enum": ["inspect", "init"], "description": "操作类型"},
                    "manifest_path": {"type": "string", "description": "可选显式指定 mcu-harness.toml 路径"},
                    "force": {"type": "boolean", "description": "action='init' 时是否允许覆盖已存在配置"},
                }
            ),
            handler=_handle_inspect_project,
        )
    )

    reg.register(
        ToolSpec(
            name="build_firmware",
            description="编译固件工程（自动识别 CMakeLists.txt 或 Makefile 并执行增量编译），返回结构化错误/警告与产物路径。",
            risk_level="workspace_write",
            parameters=_obj_schema(
                {
                    "project_dir": {"type": "string", "description": "含 CMakeLists.txt 或 Makefile 的工程根目录"},
                    "build_system": {"type": "string", "enum": ["auto", "cmake", "make"], "description": "构建系统"},
                    "makefile": {"type": "string", "description": "自定义 Makefile 路径"},
                    "build_dir": {"type": "string", "description": "构建目录"},
                    "target": {"type": "string", "description": "仅编译指定目标名称"},
                    "build_type": {"type": "string", "description": "CMake 构建类型：Debug / Release / RelWithDebInfo / MinSizeRel"},
                    "toolchain_file": {"type": "string", "description": "CMake 交叉编译工具链文件路径"},
                    "generator": {"type": "string", "description": "CMake 生成器，默认 Ninja"},
                    "extra_args": {"type": "array", "items": {"type": "string"}, "description": "额外构建参数"},
                    "extra_cmake_args": {"type": "array", "items": {"type": "string"}, "description": "额外 CMake 参数"},
                    "clean": {"type": "boolean", "description": "是否先 clean 再编译"},
                    "reconfigure": {"type": "boolean", "description": "CMake 是否强制重新 configure"},
                    "jobs": {"type": "integer", "description": "并行编译任务数（-j）"},
                    "max_errors": {"type": "integer", "description": "最多返回的错误条数"},
                    "max_warnings": {"type": "integer", "description": "最多返回的警告条数"},
                }
            ),
            handler=_handle_build_firmware,
        )
    )

    reg.register(
        ToolSpec(
            name="generate_compile_commands",
            description="为 CMake 或 Makefile 固件工程生成标准 compile_commands.json 供 clangd 与静态分析使用。",
            risk_level="workspace_write",
            parameters=_obj_schema(
                {
                    "project_dir": {"type": "string", "description": "固件工程根目录路径"},
                    "build_system": {"type": "string", "enum": ["auto", "cmake", "make"], "description": "构建系统"},
                    "build_dir": {"type": "string", "description": "可选 CMake 构建目录"},
                    "makefile": {"type": "string", "description": "可选自定义 Makefile 路径"},
                    "toolchain_file": {"type": "string", "description": "可选 CMake 工具链文件"},
                    "extra_args": {"type": "array", "items": {"type": "string"}, "description": "额外构建参数"},
                }
            ),
            handler=_handle_generate_compile_commands,
        )
    )

    reg.register(
        ToolSpec(
            name="analyze_footprint",
            description="深度分析固件 .elf 与 .map 内存版图，统计 Flash/RAM 分区权重、Top N 函数/对象及 DMA/栈风险。",
            risk_level="read_only",
            parameters=_obj_schema(
                {
                    "elf_path": {"type": "string", "description": ".elf 文件路径"},
                    "map_path": {"type": "string", "description": ".map 链接映射文件路径"},
                    "project_dir": {"type": "string", "description": "可选固件工程根目录"},
                    "top_n": {"type": "integer", "description": "返回最大函数与对象条数（1..100，默认 15）"},
                }
            ),
            handler=_handle_analyze_footprint,
        )
    )

    reg.register(
        ToolSpec(
            name="inspect_svd",
            description="基于 CMSIS-SVD 查询芯片外设基地址、寄存器定义，或将十六进制寄存器值解码为可读位域。",
            risk_level="read_only",
            parameters=_obj_schema(
                {
                    "peripheral": {"type": "string", "description": "外设名（如 GPIOA / RCC / USART1）"},
                    "register": {"type": "string", "description": "寄存器名（如 MODER / CR1）"},
                    "raw_value": {"type": ["string", "integer"], "description": "待解码的寄存器值"},
                    "svd_path": {"type": "string", "description": ".svd 或 .svd.gz 文件路径"},
                    "name_filter": {"type": "string", "description": "按名称子串过滤外设或寄存器"},
                    "project_dir": {"type": "string", "description": "可选固件工程目录"},
                }
            ),
            handler=_handle_inspect_svd,
        )
    )

    reg.register(
        ToolSpec(
            name="probe_control",
            description="管理调试探针（J-Link / OpenOCD / Mock）会话：info / connect / disconnect / halt / resume / reset / regs / audit。",
            risk_level="hardware_state",
            parameters=_obj_schema(
                {
                    "action": {
                        "type": "string",
                        "enum": ["info", "connect", "disconnect", "halt", "resume", "reset", "regs", "audit"],
                        "description": "探针操作类型",
                    },
                    "backend": {"type": "string", "enum": ["jlink", "openocd", "mock"], "description": "探针后端"},
                    "device": {"type": "string", "description": "目标芯片型号"},
                    "interface": {"type": "string", "description": "调试接口：SWD 或 JTAG"},
                    "speed": {"type": ["string", "integer"], "description": "接口速率 kHz 或 'auto'"},
                    "serial_no": {"type": "string", "description": "探针序列号"},
                    "openocd_interface": {"type": "string", "description": "OpenOCD 调试器配置名"},
                    "openocd_target": {"type": "string", "description": "OpenOCD 目标芯片配置名"},
                    "openocd_tcl_port": {"type": "integer", "description": "OpenOCD TCL RPC 端口"},
                    "reconnect": {"type": "boolean", "description": "connect 时是否强制重新连接"},
                    "halt_on_reset": {"type": "boolean", "description": "reset 时复位后是否暂停在入口点"},
                    "reg_names": {"type": "array", "items": {"type": "string"}, "description": "regs 时指定寄存器列表"},
                    "project_dir": {"type": "string", "description": "可选固件工程目录"},
                }
            ),
            handler=_handle_probe_control,
        )
    )

    reg.register(
        ToolSpec(
            name="read_memory",
            description="通过探针读取目标 MCU 内存或寄存器原始值（不复位）。",
            risk_level="read_only",
            parameters=_obj_schema(
                {
                    "address": {"type": ["string", "integer"], "description": "起始地址，如 '0xE000ED00'"},
                    "count": {"type": "integer", "description": "读取元素个数（1..256，默认 1）"},
                    "width": {"type": "integer", "enum": [8, 16, 32], "description": "位宽：8 / 16 / 32"},
                    "device": {"type": "string", "description": "目标芯片型号"},
                    "backend": {"type": "string", "description": "可选探针后端：jlink / openocd / mock"},
                    "project_dir": {"type": "string", "description": "可选固件工程目录"},
                },
                required=["address"],
            ),
            handler=_handle_read_memory,
        )
    )

    # 安全约束：write_memory schema 严禁暴露 confirm 参数！
    reg.register(
        ToolSpec(
            name="write_memory",
            description=(
                "通过探针向目标 MCU 内存或寄存器写入数值（受分级硬件安全护栏保护；"
                "Option Bytes 默认硬拒绝，高危写操作须由本地安全联锁批准）。"
            ),
            risk_level="hardware_danger",
            parameters=_obj_schema(
                {
                    "address": {"type": ["string", "integer"], "description": "起始地址，如 '0x20000100'"},
                    "values": {
                        "type": "array",
                        "items": {"type": ["string", "integer"]},
                        "description": "待写入的 1..256 个整数或十六进制字符串列表",
                    },
                    "width": {"type": "integer", "enum": [8, 16, 32], "description": "位宽：8 / 16 / 32"},
                    "peripheral_hint": {"type": "string", "description": "可选外设名提示（如 'TIM1' / 'GPIOA'）"},
                    "device": {"type": "string", "description": "目标芯片型号"},
                    "backend": {"type": "string", "description": "可选探针后端：jlink / openocd / mock"},
                    "project_dir": {"type": "string", "description": "可选固件工程目录"},
                },
                required=["address", "values"],
            ),
            handler=_handle_write_memory,
        )
    )

    # 安全约束：flash_firmware schema 严禁暴露 confirm 参数！
    reg.register(
        ToolSpec(
            name="flash_firmware",
            description="通过 J-Link 或 OpenOCD 将 .elf / .hex / .bin 固件烧录到目标 MCU Flash（受本地安全联锁保护）。",
            risk_level="hardware_danger",
            parameters=_obj_schema(
                {
                    "firmware_path": {"type": "string", "description": ".elf / .hex / .bin 文件路径"},
                    "project_dir": {"type": "string", "description": "可选固件工程目录"},
                    "address": {"type": ["string", "integer"], "description": "烧录纯 .bin 文件时的起始地址"},
                    "verify": {"type": "boolean", "description": "烧录后是否校验 Flash 内容"},
                    "reset_after": {"type": "boolean", "description": "烧录后是否复位芯片"},
                    "run_after": {"type": "boolean", "description": "复位后是否立即启动运行"},
                    "device": {"type": "string", "description": "目标芯片型号"},
                    "backend": {"type": "string", "description": "可选探针后端：jlink / openocd / mock"},
                }
            ),
            handler=_handle_flash_firmware,
        )
    )

    reg.register(
        ToolSpec(
            name="inspect_peripheral",
            description="通过探针实时读取目标芯片外设寄存器，并按 CMSIS-SVD 自动解码每个位域的数值与含义。",
            risk_level="read_only",
            parameters=_obj_schema(
                {
                    "peripheral": {"type": "string", "description": "外设名称，如 GPIOA / RCC / USART1"},
                    "register": {"type": "string", "description": "单个寄存器名"},
                    "registers": {"type": "array", "items": {"type": "string"}, "description": "多个寄存器名列表"},
                    "read_all": {"type": "boolean", "description": "是否读取外设全部非 write-only 寄存器"},
                    "diff_only": {"type": "boolean", "description": "批量读取时仅返回偏离复位值的寄存器"},
                    "svd_path": {"type": "string", "description": ".svd 文件路径"},
                    "device": {"type": "string", "description": "目标芯片型号"},
                    "backend": {"type": "string", "description": "可选探针后端"},
                    "project_dir": {"type": "string", "description": "可选固件工程目录"},
                },
                required=["peripheral"],
            ),
            handler=_handle_inspect_peripheral,
        )
    )

    reg.register(
        ToolSpec(
            name="diagnose_peripheral",
            description="跨外设因果体检：串联核查 RCC 时钟门控、NVIC 中断、外设使能/错误标志及全片 GPIO 引脚复用。",
            risk_level="read_only",
            parameters=_obj_schema(
                {
                    "peripheral": {"type": "string", "description": "目标外设名，或 'GPIO_ALL'"},
                    "include_gpio_mux": {"type": "boolean", "description": "是否附带全片已配置 GPIO 引脚复用快照"},
                    "svd_path": {"type": "string", "description": ".svd 文件路径"},
                    "device": {"type": "string", "description": "目标芯片型号"},
                    "backend": {"type": "string", "description": "可选探针后端"},
                    "project_dir": {"type": "string", "description": "可选固件工程目录"},
                },
                required=["peripheral"],
            ),
            handler=_handle_diagnose_peripheral,
        )
    )

    reg.register(
        ToolSpec(
            name="inspect_board",
            description="解析 STM32CubeMX .ioc 与 mcu-harness.toml [board] 板级接线定义；live_check=true 时连板核查引脚漂移。",
            risk_level="read_only",
            parameters=_obj_schema(
                {
                    "project_dir": {"type": "string", "description": "固件工程根目录"},
                    "live_check": {"type": "boolean", "description": "是否连板对比真机 GPIO 引脚模式"},
                    "ioc_path": {"type": "string", "description": "可选显式指定 .ioc 文件路径"},
                    "svd_path": {"type": "string", "description": "live_check=true 时使用的 .svd 文件路径"},
                    "device": {"type": "string", "description": "目标芯片型号"},
                    "backend": {"type": "string", "description": "可选探针后端"},
                }
            ),
            handler=_handle_inspect_board,
        )
    )

    reg.register(
        ToolSpec(
            name="watch_variable",
            description="基于 .elf DWARF 调试信息无需暂停 CPU 读取或高频采样运行中的 C 变量（read / sample / list）。",
            risk_level="read_only",
            parameters=_obj_schema(
                {
                    "action": {"type": "string", "enum": ["read", "sample", "list"], "description": "操作类型"},
                    "expression": {"type": "string", "description": "单个 C 变量表达式"},
                    "expressions": {"type": "array", "items": {"type": "string"}, "description": "批量读取的变量表达式列表"},
                    "deref": {"type": "boolean", "description": "遇到指针时是否自动解引用一层"},
                    "max_depth": {"type": "integer", "description": "结构体最大展开深度（1..8）"},
                    "samples": {"type": "integer", "description": "sample 采样次数"},
                    "interval_ms": {"type": "number", "description": "sample 采样间隔毫秒数"},
                    "name_filter": {"type": "string", "description": "list 时按变量名子串过滤"},
                    "elf_path": {"type": "string", "description": "带调试符号的 .elf 路径"},
                    "project_dir": {"type": "string", "description": "可选固件工程目录"},
                    "device": {"type": "string", "description": "目标芯片型号"},
                    "backend": {"type": "string", "description": "可选探针后端"},
                }
            ),
            handler=_handle_watch_variable,
        )
    )

    reg.register(
        ToolSpec(
            name="read_debug_log",
            description="非阻塞读写 SEGGER RTT 或 UART 串口环形日志缓冲区，支持增量拉取、正则过滤及下行指令写入。",
            risk_level="read_only",
            parameters=_obj_schema(
                {
                    "action": {
                        "type": "string",
                        "enum": ["read", "write", "start", "stop", "clear", "status", "list_ports"],
                        "description": "日志操作类型",
                    },
                    "source": {"type": "string", "enum": ["rtt", "uart"], "description": "日志通道"},
                    "tail_lines": {"type": "integer", "description": "最多返回的日志行数（1..500）"},
                    "since_seq": {"type": "integer", "description": "仅返回 seq 大于该值的增量新日志"},
                    "pattern": {"type": "string", "description": "按关键词或正则过滤日志"},
                    "regex": {"type": "boolean", "description": "为 true 时把 pattern 当作正则表达式"},
                    "context_lines": {"type": "integer", "description": "命中 pattern 时附带的前后上下文行数"},
                    "channel": {"type": "integer", "description": "SEGGER RTT 通道号"},
                    "elf_path": {"type": "string", "description": "用于定位 _SEGGER_RTT 控制块的 .elf 路径"},
                    "block_address": {"type": ["string", "integer"], "description": "可选 RTT 控制块地址"},
                    "port": {"type": "string", "description": "source='uart' 时的串口设备路径"},
                    "baudrate": {"type": "integer", "description": "source='uart' 时的波特率"},
                    "include_partial": {"type": "boolean", "description": "是否附带末尾半行日志"},
                    "command": {"type": "string", "description": "action='write' 时发送的下行文本指令"},
                    "device": {"type": "string", "description": "目标芯片型号"},
                    "project_dir": {"type": "string", "description": "可选固件工程目录"},
                }
            ),
            handler=_handle_read_debug_log,
        )
    )

    reg.register(
        ToolSpec(
            name="diagnose_crash",
            description="自动诊断 Cortex-M 死机/HardFault 现场，解码 CFSR/HFSR/SFSR/EXC_RETURN 并还原 C 源码行号。",
            risk_level="read_only",
            parameters=_obj_schema(
                {
                    "elf_path": {"type": "string", "description": "固件 .elf 路径"},
                    "project_dir": {"type": "string", "description": "可选固件工程根目录"},
                    "keep_halted": {"type": "boolean", "description": "采样后是否保持 CPU 暂停"},
                    "device": {"type": "string", "description": "目标芯片型号"},
                    "backend": {"type": "string", "description": "可选探针后端"},
                }
            ),
            handler=_handle_diagnose_crash,
        )
    )

    reg.register(
        ToolSpec(
            name="check_architecture",
            description="对嵌入式 C/C++ 工程执行四层架构边界、ISR 传递调用阻塞函数与共享变量漏加 volatile 静态检查。",
            risk_level="read_only",
            parameters=_obj_schema(
                {
                    "project_dir": {"type": "string", "description": "待扫描的嵌入式工程根目录"},
                    "manifest_path": {"type": "string", "description": "可选显式指定含 [architecture] 规则的配置文件"},
                }
            ),
            handler=_handle_check_architecture,
        )
    )

    reg.register(
        ToolSpec(
            name="scaffold_code",
            description="生成跨芯片可移植 C11 嵌入式模块、四层工程骨架与零硬件主机单元测试代码（pal/bsp/service/skeleton）。",
            risk_level="workspace_write",
            parameters=_obj_schema(
                {
                    "project_dir": {"type": "string", "description": "目标工程根目录"},
                    "kind": {"type": "string", "enum": ["pal", "bsp", "service", "skeleton"], "description": "生成类型"},
                    "name": {"type": "string", "description": "模块名称（kind 为 bsp 或 service 时必填）"},
                    "overwrite": {"type": "boolean", "description": "是否覆盖已存在的同名文件"},
                },
                required=["kind"],
            ),
            handler=_handle_scaffold_code,
        )
    )

    reg.register(
        ToolSpec(
            name="inspect_rtos",
            description="无需暂停 CPU 透视 FreeRTOS / Zephyr / RT-Thread 任务状态与栈高水位溢出告警。",
            risk_level="read_only",
            parameters=_obj_schema(
                {
                    "elf_path": {"type": "string", "description": "带调试符号的固件 .elf 路径"},
                    "project_dir": {"type": "string", "description": "可选固件工程根目录"},
                    "rtos": {"type": "string", "enum": ["auto", "freertos", "zephyr", "rtthread"], "description": "RTOS 类型"},
                    "max_tasks": {"type": "integer", "description": "最多遍历任务数上限（1..256）"},
                    "check_stack": {"type": "boolean", "description": "是否扫描栈底高水位水印字节"},
                    "device": {"type": "string", "description": "目标芯片型号"},
                    "backend": {"type": "string", "description": "可选探针后端"},
                }
            ),
            handler=_handle_inspect_rtos,
        )
    )

    reg.register(
        ToolSpec(
            name="trace_target",
            description="Cortex-M CoreSight DWT 非侵入性能剖析与硬件数据观察点（measure_cycles / profile_pc / set_watchpoint / list_watchpoints / clear_watchpoints）。",
            risk_level="hardware_state",
            parameters=_obj_schema(
                {
                    "action": {
                        "type": "string",
                        "enum": ["profile_pc", "measure_cycles", "set_watchpoint", "list_watchpoints", "clear_watchpoints"],
                        "description": "剖析或观察点操作",
                    },
                    "duration_ms": {"type": "number", "description": "measure_cycles 测速时间窗（毫秒）"},
                    "core_hz": {"type": "integer", "description": "measure_cycles 可选预期主频 Hz"},
                    "samples": {"type": "integer", "description": "profile_pc 采样次数（1..1000）"},
                    "interval_ms": {"type": "number", "description": "profile_pc 采样间隔毫秒"},
                    "watch_target": {"type": ["string", "integer"], "description": "set_watchpoint 的地址或 C 变量名"},
                    "watch_access": {"type": "string", "enum": ["write", "read", "rw"], "description": "观察点触发方式"},
                    "watch_size": {"type": "integer", "description": "观察点宽度：1 / 2 / 4 字节"},
                    "slot": {"type": "integer", "description": "DWT 比较器槽位"},
                    "elf_path": {"type": "string", "description": "固件 .elf 路径"},
                    "project_dir": {"type": "string", "description": "可选固件工程目录"},
                    "device": {"type": "string", "description": "目标芯片型号"},
                    "backend": {"type": "string", "description": "可选探针后端"},
                }
            ),
            handler=_handle_trace_target,
        )
    )

    reg.register(
        ToolSpec(
            name="capture_logic",
            description="逻辑分析仪数字波形 CSV 频率/占空比分析与 sigrok-cli 总线协议（SPI/I2C/UART/PWM/CAN）解码。",
            risk_level="read_only",
            parameters=_obj_schema(
                {
                    "csv_path": {"type": "string", "description": "数字波形 CSV 文件路径"},
                    "input_file": {"type": "string", "description": "sigrok .sr 或 .csv 文件路径"},
                    "driver": {"type": "string", "description": "sigrok 硬件采集驱动名"},
                    "channels": {"type": "string", "description": "采样通道列表，如 'D0,D1,D2'"},
                    "samplerate": {"type": "string", "description": "采样率，默认 '4m'"},
                    "samples": {"type": "integer", "description": "采样点数"},
                    "duration_ms": {"type": "number", "description": "采样持续毫秒数"},
                    "protocol": {"type": "string", "description": "协议解码器名称"},
                    "protocol_options": {"type": ["object", "string"], "description": "协议参数字典或冒号串"},
                    "time_unit_s": {"type": "number", "description": "CSV 时间戳换算秒倍率"},
                }
            ),
            handler=_handle_capture_logic,
        )
    )

    # 安全约束：run_hil_tests schema 严禁暴露 confirm 参数！
    reg.register(
        ToolSpec(
            name="run_hil_tests",
            description="执行声明式硬件在环（HIL）自动化测试套件（.toml / .json），无论成功或失败均保证执行 teardown 安全停机。",
            risk_level="hardware_danger",
            parameters=_obj_schema(
                {
                    "spec_path": {"type": "string", "description": "HIL 测试套件文件（.toml / .json）路径"},
                    "project_dir": {"type": "string", "description": "可选固件工程目录"},
                    "elf_path": {"type": "string", "description": "可选固件 .elf 路径"},
                    "svd_path": {"type": "string", "description": "可选芯片 .svd 路径"},
                    "device": {"type": "string", "description": "目标芯片型号"},
                    "backend": {"type": "string", "description": "可选探针后端"},
                },
                required=["spec_path"],
            ),
            handler=_handle_run_hil_tests,
        )
    )

    return reg
