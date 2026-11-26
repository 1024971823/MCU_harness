"""MCU Agent Harness 的 MCP Server 入口（面向大模型的统一嵌入式“HAL 接口层”）。

职责：
    只做薄封装——把底层核心模块（config / build / svd / probe / safety / diagnose / dwarf / log / crash / arch）
    注册成标准 MCP 工具，支持通过 `mcu-harness.toml` 或工程目录自动推导芯片型号、SVD 与 ELF 路径。

启动方式（stdio 协议，供 Antigravity / Claude / MCP Inspector 连接）：
    PYTHONPATH=src .venv/bin/python -m mcu_harness.server
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

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
from mcu_harness.log import default_rtt_collector, default_uart_collector, list_serial_ports
from mcu_harness.probe import PeripheralSnapshot, default_probe_manager, inspect_peripheral_registers
from mcu_harness.rtos import RtosError, inspect_rtos as _run_inspect_rtos
from mcu_harness.safety import default_safety_guard
from mcu_harness.svd import DecodedRegister, decode_register, find_peripheral, find_register, load_svd
from mcu_harness.trace import (
    TraceError,
    clear_watchpoints as _run_clear_watchpoints,
    list_watchpoints as _run_list_watchpoints,
    measure_cycles as _run_measure_cycles,
    profile_pc_hotspots as _run_profile_pc_hotspots,
    set_watchpoint as _run_set_watchpoint,
)

mcp = FastMCP(
    name="mcu-harness",
    instructions=(
        "通用 MCU 嵌入式开发、深度硬件感知、RTOS/DWT 剖析、HIL 闭环与代码框架工具集（Cortex-M 全系 + J-Link / OpenOCD + RTT/UART + SVD + DWARF）。\n"
        "- 工程与构建：inspect_project 自动识别芯片/SVD/ELF；generate_compile_commands 导出编译库；build_firmware 增量编译；analyze_footprint 审计 Flash/RAM/DMA 段；\n"
        "- 代码与框架：check_architecture 检查分层边界与 ISR 违禁调用/漏写 volatile；scaffold_code 生成跨芯片 PAL/BSP(含 safe_off)/Service 与主机单测；\n"
        "- 深度硬件感知：inspect_board 核对 .ioc/板级接线与真机引脚漂移；diagnose_peripheral 一键联查 RCC 时钟/NVIC/GPIO 复用与错误位；inspect_peripheral / inspect_svd 查解码寄存器；\n"
        "- 无停机变量与内核透视：watch_variable 读取或采样 C 结构体/枚举；inspect_rtos 透视 FreeRTOS/Zephyr/RT-Thread 任务状态与栈高水位；\n"
        "- 性能剖析与死机诊断：trace_target 非侵入测主频/采样 PC 热点/管理 DWT 硬件观察点；diagnose_crash 自动破案 HardFault 并还原 C 源码行；\n"
        "- 仪器与 HIL 闭环：capture_logic 解析数字波形或 sigrok-cli 协议；run_hil_tests 执行声明式硬件在环回归；\n"
        "- 内存/烧录/日志：read_memory / write_memory / flash_firmware（高危写与烧录受 HITL 护栏保护，须人工同意后传 confirm=True）；read_debug_log 非阻塞读写 RTT 或 UART 串口日志。"
    ),
)


def _try_profile(project_dir: str | None) -> ProjectProfile | None:
    cand = project_dir or os.environ.get("MCU_HARNESS_PROJECT_DIR")
    if not cand:
        return None
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


@mcp.tool(
    name="inspect_project",
    description=(
        "自动扫描固件工程目录（识别 mcu-harness.toml、CubeMX .ioc、CMake/Makefile、最新 .elf/.map/.svd），"
        "返回目标芯片型号、内核代数、探针配置、安全策略与代码分层规则；也可通过 action='init' 生成 mcu-harness.toml。"
    ),
)
def inspect_project(
    project_dir: Annotated[str, Field(description="固件工程根目录路径")] = ".",
    action: Annotated[str, Field(description="操作：inspect（扫描查看，默认）/ init（生成 mcu-harness.toml）")] = "inspect",
    manifest_path: Annotated[str | None, Field(description="可选显式指定 mcu-harness.toml 路径")] = None,
    force: Annotated[bool, Field(description="action='init' 时是否允许覆盖已存在的 mcu-harness.toml")] = False,
) -> dict[str, Any]:
    act = action.strip().lower()
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
        return {"error": f"Unknown action {action!r} (expected 'inspect' or 'init')"}
    except (ConfigError, OSError) as e:
        return {"error": str(e)}


@mcp.tool(
    name="build_firmware",
    description=(
        "编译固件工程（自动识别 CMakeLists.txt 或 Makefile 工程并执行增量编译），"
        "过滤冗余终端输出，返回结构化的错误/警告列表、内存/段大小占用与产物路径。"
    ),
)
def build_firmware(
    project_dir: Annotated[str, Field(description="含 CMakeLists.txt 或 Makefile 的固件工程根目录路径")],
    build_system: Annotated[str, Field(description="构建系统：auto（自动识别）/ cmake / make")] = "auto",
    makefile: Annotated[str | None, Field(description="Makefile 路径（可选，仅 Makefile 工程需要自定义文件名时传）")] = None,
    build_dir: Annotated[str | None, Field(description="构建/产物目录（CMake 默认 <project_dir>/build；Make 默认自动扫描工程目录）")] = None,
    target: Annotated[str | None, Field(description="仅编译指定目标名称，默认全部/默认目标")] = None,
    build_type: Annotated[str | None, Field(description="CMake 构建类型：Debug / Release / RelWithDebInfo / MinSizeRel")] = None,
    toolchain_file: Annotated[str | None, Field(description="CMake 交叉编译工具链文件路径，如 cmake/arm-none-eabi.cmake")] = None,
    generator: Annotated[str | None, Field(description="CMake 生成器，默认 Ninja")] = None,
    extra_args: Annotated[list[str] | None, Field(description="额外构建参数：Make 如 ['DEBUG=1', 'V=1']，CMake 如 ['-DBOARD=xxx']")] = None,
    extra_cmake_args: Annotated[list[str] | None, Field(description="额外 CMake 参数（兼容字段，可改用 extra_args）")] = None,
    clean: Annotated[bool, Field(description="是否先 clean 再编译")] = False,
    reconfigure: Annotated[bool, Field(description="CMake 是否强制重新执行 configure")] = False,
    jobs: Annotated[int | None, Field(description="并行编译任务数（-j）")] = None,
    max_errors: Annotated[int, Field(description="最多返回的错误条数（total_errors 仍统计总数）")] = 10,
    max_warnings: Annotated[int, Field(description="最多返回的警告条数（total_warnings 仍统计总数）")] = 10,
) -> dict[str, Any]:
    try:
        prof = _try_profile(project_dir)
        b_cfg = prof.build if prof and prof.manifest_path else None
        merged_extra = list(b_cfg.extra_args) if b_cfg and extra_args is None else extra_args

        result = _run_build_firmware(
            project_dir=project_dir,
            build_dir=build_dir or (b_cfg.build_dir if b_cfg else None),
            target=target or (b_cfg.target if b_cfg else None),
            build_type=build_type or (b_cfg.build_type if b_cfg else "Debug"),
            toolchain_file=toolchain_file or (b_cfg.toolchain_file if b_cfg else None),
            generator=generator or (b_cfg.generator if b_cfg else "Ninja"),
            extra_cmake_args=extra_cmake_args,
            clean=clean,
            reconfigure=reconfigure,
            jobs=jobs,
            max_errors=max_errors,
            max_warnings=max_warnings,
            build_system=build_system if build_system != "auto" or not b_cfg else b_cfg.system,
            makefile=makefile or (b_cfg.makefile if b_cfg else None),
            extra_args=merged_extra,
        )
        return result.to_dict()
    except Exception as e:
        return {"success": False, "stage": "precheck", "error": str(e), "message": str(e)}


@mcp.tool(
    name="generate_compile_commands",
    description=(
        "为 CMake 或传统 Makefile（如 STM32CubeMX 工程，无需安装 bear）自动生成标准 compile_commands.json，"
        "让 clangd 与代码分析工具准确识别芯片宏定义、条件编译分支与交叉工具链头文件路径。"
    ),
)
def generate_compile_commands(
    project_dir: Annotated[str, Field(description="固件工程根目录路径")],
    build_system: Annotated[str, Field(description="构建系统：auto / cmake / make")] = "auto",
    build_dir: Annotated[str | None, Field(description="可选 CMake 构建目录")] = None,
    makefile: Annotated[str | None, Field(description="可选自定义 Makefile 路径")] = None,
    toolchain_file: Annotated[str | None, Field(description="可选 CMake 工具链文件")] = None,
    extra_args: Annotated[list[str] | None, Field(description="额外构建参数")] = None,
) -> dict[str, Any]:
    try:
        res = _run_generate_compdb(
            project_dir=project_dir,
            build_dir=build_dir,
            makefile=makefile,
            extra_args=extra_args,
            build_system=build_system,
            toolchain_file=toolchain_file,
        )
        return res.to_dict()
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(
    name="analyze_footprint",
    description=(
        "深度分析固件 .elf 与 GNU ld .map 内存版图：统计 Flash/RAM 各节与架构分层权重、列出 Top N 最大函数/缓冲区、"
        "识别双精度浮点 printf / malloc 等膨胀元凶，并静态审计 DMA 缓冲区误放 CCMRAM/DTCM、未对齐及主栈净空不足风险。"
    ),
)
def analyze_footprint(
    elf_path: Annotated[str | None, Field(description=".elf 文件路径；不传则按 project_dir 自动扫描最新 .elf")] = None,
    map_path: Annotated[str | None, Field(description="可选 .map 链接映射文件路径（不传则自动查找同名或最新 .map）")] = None,
    project_dir: Annotated[str | None, Field(description="可选固件工程根目录")] = None,
    top_n: Annotated[int, Field(description="返回最大函数与对象的排行条数（1..100）")] = 15,
) -> dict[str, Any]:
    if top_n < 1 or top_n > 100:
        return {"error": "top_n must be between 1 and 100"}
    prof = _try_profile(project_dir)
    eff_elf = elf_path or os.environ.get("MCU_HARNESS_ELF_PATH") or (prof.paths.elf_path if prof else None)
    eff_map = map_path or (prof.paths.map_path if prof else None)
    try:
        rep = _run_analyze_footprint(elf_path=eff_elf, map_path=eff_map, project_dir=project_dir, top_n=top_n)
        return rep.to_dict()
    except (FootprintError, FileNotFoundError, ValueError) as e:
        return {"error": str(e)}


@mcp.tool(
    name="inspect_svd",
    description=(
        "基于 CMSIS-SVD 文件查询芯片外设地址、寄存器位域定义，或将读到的十六进制寄存器值解码为可读位域。"
        "不指定 register 时列出外设下所有寄存器；指定 register 和 raw_value 时解码每个位域的含义。"
    ),
)
def inspect_svd(
    peripheral: Annotated[str | None, Field(description="外设名（如 GPIOA / RCC / USART1）；不传则列出所有外设")] = None,
    register: Annotated[str | None, Field(description="寄存器名（如 MODER / CR1）；不传则列出该外设的所有寄存器")] = None,
    raw_value: Annotated[str | int | None, Field(description="要解码的寄存器值（如 '0x00000400' 或 1024）；不传则按复位值展示定义")] = None,
    svd_path: Annotated[str | None, Field(description=".svd 或 .svd.gz 文件路径；不传则查环境变量或 project_dir 配置")] = None,
    name_filter: Annotated[str | None, Field(description="列出外设或寄存器时按名称子串过滤")] = None,
    project_dir: Annotated[str | None, Field(description="可选固件工程目录（用于自动发现 svd_path）")] = None,
) -> dict[str, Any]:
    prof = _try_profile(project_dir)
    resolved_svd = svd_path or os.environ.get("MCU_HARNESS_SVD_PATH") or (prof.paths.svd_path if prof else None)
    if not resolved_svd:
        return {"error": "请提供 svd_path / project_dir 参数，或设置环境变量 MCU_HARNESS_SVD_PATH"}

    try:
        device = load_svd(resolved_svd)
    except Exception as e:
        return {"error": str(e)}

    if not peripheral:
        flt = (name_filter or "").upper()
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
        periph = find_peripheral(device, peripheral)
    except KeyError as e:
        return {"error": str(e)}

    if not register:
        flt = (name_filter or "").upper()
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
        reg = find_register(periph, register)
        val = raw_value if raw_value is not None else (reg.reset_value if reg.reset_value is not None else 0)
        decoded = decode_register(periph, reg, val)
    except (KeyError, ValueError) as e:
        return {"error": str(e)}

    return decoded.to_dict()


@mcp.tool(
    name="probe_control",
    description=(
        "管理调试探针（J-Link / OpenOCD / Mock）长驻会话：查看连接状态（info）、附着连接目标芯片（connect，不复位）、"
        "断开连接（disconnect）、暂停/恢复 CPU（halt / resume）、复位芯片（reset）、读取核心寄存器（regs）或查看安全审计日志（audit）。"
    ),
)
def probe_control(
    action: Annotated[str, Field(description="操作：info / connect / disconnect / halt / resume / reset / regs / audit")] = "info",
    backend: Annotated[str | None, Field(description="探针后端：jlink / openocd / mock（默认读取配置或 jlink）")] = None,
    device: Annotated[str | None, Field(description="目标芯片型号（默认读取 mcu-harness.toml / MCU_HARNESS_DEVICE）")] = None,
    interface: Annotated[str | None, Field(description="调试接口：SWD 或 JTAG")] = None,
    speed: Annotated[str | int | None, Field(description="接口速率 kHz，或 'auto'")] = None,
    serial_no: Annotated[str | None, Field(description="探针序列号")] = None,
    openocd_interface: Annotated[str | None, Field(description="OpenOCD 调试器配置名，如 cmsis-dap / stlink / jlink")] = None,
    openocd_target: Annotated[str | None, Field(description="OpenOCD 目标芯片配置名，如 stm32h5x / stm32f4x")] = None,
    openocd_tcl_port: Annotated[int | None, Field(description="OpenOCD TCL RPC 端口，默认 6666")] = None,
    reconnect: Annotated[bool, Field(description="action=connect 时是否强制重新连接")] = False,
    halt_on_reset: Annotated[bool, Field(description="action=reset 时复位后是否暂停在入口点")] = False,
    reg_names: Annotated[list[str] | None, Field(description="action=regs 时指定寄存器列表（如 ['pc', 'lr', 'sp']），不传读全部")] = None,
    project_dir: Annotated[str | None, Field(description="可选固件工程目录（用于自动读取探针与芯片配置）")] = None,
) -> dict[str, Any]:
    act = action.strip().lower()
    prof = _try_profile(project_dir)
    t_cfg = prof.target if prof else None
    eff_dev = device or (t_cfg.device if t_cfg else None)
    eff_backend = backend or (t_cfg.probe_backend if prof and prof.manifest_path else None)
    eff_ocd_if = openocd_interface or (t_cfg.openocd_interface if t_cfg else None)
    eff_ocd_tgt = openocd_target or (t_cfg.openocd_target if t_cfg else None)

    try:
        if act == "audit":
            entries = [e.to_dict() for e in default_safety_guard.recent_audit(limit=50)]
            return {"count": len(entries), "entries": entries}
        if act == "disconnect":
            default_probe_manager.disconnect()
            return {"connected": False, "message": "Probe disconnected"}
        if act == "connect":
            info = default_probe_manager.connect(
                backend=eff_backend,
                device=eff_dev,
                interface=interface or (t_cfg.interface if t_cfg else None),
                speed=speed if speed is not None else (t_cfg.speed if t_cfg else None),
                serial_no=serial_no or (t_cfg.serial_no if t_cfg else None),
                openocd_interface=eff_ocd_if,
                openocd_target=eff_ocd_tgt,
                openocd_tcl_port=openocd_tcl_port or (t_cfg.openocd_tcl_port if t_cfg else None),
                reconnect=reconnect,
            )
            return info.to_dict()
        if act == "info":
            with default_probe_manager.session(
                device=eff_dev, backend=eff_backend, openocd_interface=eff_ocd_if, openocd_target=eff_ocd_tgt
            ) as probe:
                return probe.info().to_dict()
        if act == "halt":
            with default_probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
                ok = probe.halt()
                default_safety_guard.record_audit("halt", probe.info().device, detail=f"changed={ok}")
                return {"action": "halt", "halted": probe.is_halted(), "changed": ok}
        if act == "resume":
            with default_probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
                ok = probe.resume()
                default_safety_guard.record_audit("resume", probe.info().device, detail=f"changed={ok}")
                return {"action": "resume", "halted": probe.is_halted(), "changed": ok}
        if act == "reset":
            with default_probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
                res = probe.reset(halt=halt_on_reset)
                default_safety_guard.record_audit("reset", probe.info().device, detail=f"halt={halt_on_reset}")
                return res.to_dict()
        if act == "regs":
            with default_probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
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
        return {"error": f"Unknown action {action!r} (expected info/connect/disconnect/halt/resume/reset/regs/audit)"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(
    name="read_memory",
    description=(
        "通过探针读取目标 MCU 内存或寄存器原始值（不复位、J-Link 下无需暂停 CPU）。"
        "address 支持十六进制字符串（如 '0xE000ED00'）或整数。"
    ),
)
def read_memory(
    address: Annotated[str | int, Field(description="起始地址，如 '0xE000ED00' 或 '0x20000000'")],
    count: Annotated[int, Field(description="读取元素个数（1..256）")] = 1,
    width: Annotated[int, Field(description="位宽：8 / 16 / 32")] = 32,
    device: Annotated[str | None, Field(description="目标芯片型号（未连接时用于自动连接）")] = None,
    backend: Annotated[str | None, Field(description="可选探针后端：jlink / openocd / mock")] = None,
    project_dir: Annotated[str | None, Field(description="可选固件工程目录")] = None,
) -> dict[str, Any]:
    if count < 1 or count > 256:
        return {"error": "count must be between 1 and 256"}
    if width not in (8, 16, 32):
        return {"error": "width must be 8, 16, or 32"}
    try:
        addr = _parse_address(address, "address")
    except ValueError as e:
        return {"error": str(e)}

    step = width // 8
    if (addr + count * step - 1) > 0xFFFFFFFF:
        return {"error": f"Address range out of 32-bit bounds: {address!r} + {count}*{step}B"}
    if width == 32 and addr % 4 != 0:
        return {"error": f"Unaligned 32-bit address 0x{addr:08X} (must be 4-byte aligned)"}
    if width == 16 and addr % 2 != 0:
        return {"error": f"Unaligned 16-bit address 0x{addr:08X} (must be 2-byte aligned)"}

    prof = _try_profile(project_dir)
    eff_dev = device or (prof.target.device if prof else None)
    eff_backend = backend or (prof.target.probe_backend if prof and prof.manifest_path else None)

    try:
        with default_probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
            if width == 32:
                vals = probe.read_u32(addr, count)
                hex_vals = [f"0x{v:08X}" for v in vals]
            elif width == 16:
                vals = probe.read_u16(addr, count)
                hex_vals = [f"0x{v:04X}" for v in vals]
            else:
                vals = probe.read_u8(addr, count)
                hex_vals = [f"0x{v:02X}" for v in vals]
    except Exception as e:
        return {"error": str(e)}

    return {
        "address": f"0x{addr:08X}",
        "width": width,
        "count": count,
        "hex": hex_vals,
        "values": vals,
    }


@mcp.tool(
    name="write_memory",
    description=(
        "通过探针向目标 MCU 内存或寄存器写入数值（受分级硬件安全护栏保护）。"
        "Option Bytes / Product State 默认硬拒绝防锁芯片；写外设 MMIO / SCS / Flash 须先征得人工批准后传 confirm=True。"
    ),
)
def write_memory(
    address: Annotated[str | int, Field(description="起始地址，如 '0x20000100' 或 '0x42020014'")],
    values: Annotated[list[str | int], Field(description="待写入的 1..256 个整数或十六进制字符串列表")],
    width: Annotated[int, Field(description="位宽：8 / 16 / 32")] = 32,
    confirm: Annotated[bool, Field(description="是否已获得人工批准（外设寄存器等高风险写入必填 True）")] = False,
    peripheral_hint: Annotated[str | None, Field(description="可选外设名提示（如 'TIM1' / 'GPIOA'），用于精准匹配保护规则")] = None,
    device: Annotated[str | None, Field(description="目标芯片型号")] = None,
    backend: Annotated[str | None, Field(description="可选探针后端")] = None,
    project_dir: Annotated[str | None, Field(description="可选固件工程目录（用于加载 safety 策略）")] = None,
) -> dict[str, Any]:
    if not values or len(values) > 256:
        return {"error": "values length must be between 1 and 256"}
    if width not in (8, 16, 32):
        return {"error": "width must be 8, 16, or 32"}

    try:
        addr = _parse_address(address, "address")
        parsed_vals: list[int] = []
        for raw_v in values:
            if isinstance(raw_v, bool):
                raise ValueError(f"Invalid boolean value in values: {raw_v!r}")
            parsed_vals.append(int(raw_v.strip().replace("_", ""), 0) if isinstance(raw_v, str) else int(raw_v))
    except ValueError as e:
        return {"error": str(e)}

    step = width // 8
    total_bytes = len(parsed_vals) * step
    if (addr + total_bytes - 1) > 0xFFFFFFFF:
        return {"error": f"Write range exceeds 32-bit address space: 0x{addr:08X} + {total_bytes}B"}
    if width == 32 and addr % 4 != 0:
        return {"error": f"Unaligned 32-bit write address 0x{addr:08X}"}
    if width == 16 and addr % 2 != 0:
        return {"error": f"Unaligned 16-bit write address 0x{addr:08X}"}

    prof = _try_profile(project_dir)
    eff_dev = device or (prof.target.device if prof else None)
    eff_backend = backend or (prof.target.probe_backend if prof and prof.manifest_path else None)
    safety_cfg = prof.safety if prof else None

    dec = default_safety_guard.check_memory_write(
        addr,
        total_bytes,
        device=eff_dev,
        config=safety_cfg,
        confirmed=confirm,
        peripheral_hint=peripheral_hint,
    )
    if not dec.allowed:
        return {"allowed": False, "safety": dec.to_dict(), "error": dec.reason}

    try:
        with default_probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
            if width == 32:
                probe.write_u32(addr, parsed_vals)
            elif width == 16:
                probe.write_u16(addr, parsed_vals)
            else:
                probe.write_u8(addr, parsed_vals)
    except Exception as e:
        return {"error": str(e)}

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


@mcp.tool(
    name="flash_firmware",
    description=(
        "通过 J-Link 或 OpenOCD 将 .elf / .hex / .bin 固件烧录到目标 MCU Flash（受 HITL 安全确认保护）。"
        "未传 firmware_path 时可结合 project_dir 自动选取最新编译的 .elf；默认要求人工批准后传 confirm=True。"
    ),
)
def flash_firmware(
    firmware_path: Annotated[str | None, Field(description=".elf / .hex / .bin 文件路径；不传则按 project_dir 自动查找最新 .elf")] = None,
    project_dir: Annotated[str | None, Field(description="可选固件工程目录（用于自动定位 .elf、芯片型号与安全策略）")] = None,
    address: Annotated[str | int | None, Field(description="烧录纯 .bin 文件时必填的起始地址，如 '0x08000000'")] = None,
    verify: Annotated[bool, Field(description="烧录后是否校验 Flash 内容")] = True,
    reset_after: Annotated[bool, Field(description="烧录后是否复位芯片")] = True,
    run_after: Annotated[bool, Field(description="复位后是否立即启动运行（False 则停在入口点）")] = True,
    confirm: Annotated[bool, Field(description="是否已获得人工烧录批准（HITL 确认开关）")] = False,
    device: Annotated[str | None, Field(description="目标芯片型号")] = None,
    backend: Annotated[str | None, Field(description="可选探针后端：jlink / openocd / mock")] = None,
) -> dict[str, Any]:
    prof = _try_profile(project_dir)
    resolved_fw = firmware_path or os.environ.get("MCU_HARNESS_ELF_PATH") or (prof.paths.elf_path if prof else None)
    if not resolved_fw:
        return {"error": "请提供 firmware_path 参数，或传入已编译出 .elf 的 project_dir"}

    eff_dev = device or (prof.target.device if prof else None)
    eff_backend = backend or (prof.target.probe_backend if prof and prof.manifest_path else None)
    safety_cfg = prof.safety if prof else None

    try:
        load_addr = _parse_address(address, "address") if address is not None else None
    except ValueError as e:
        return {"error": str(e)}

    if load_addr is None and str(resolved_fw).lower().endswith(".bin") and prof and prof.target.flash_base is not None:
        load_addr = prof.target.flash_base

    dec = default_safety_guard.check_flash(
        str(resolved_fw),
        device=eff_dev,
        config=safety_cfg,
        confirmed=confirm,
    )
    if not dec.allowed:
        return {"allowed": False, "safety": dec.to_dict(), "error": dec.reason}

    if default_rtt_collector.is_running():
        default_rtt_collector.stop()

    try:
        with default_probe_manager.session(
            device=eff_dev,
            backend=eff_backend,
            openocd_interface=prof.target.openocd_interface if prof else None,
            openocd_target=prof.target.openocd_target if prof else None,
        ) as probe:
            res = probe.flash(
                resolved_fw,
                address=load_addr,
                verify=verify,
                reset_after=reset_after,
                run_after=run_after,
            )
            return {"allowed": True, "safety": dec.to_dict(), **res.to_dict()}
    except Exception as e:
        return {"success": False, "error": str(e)}


@mcp.tool(
    name="inspect_peripheral",
    description=(
        "通过探针实时读取目标芯片外设寄存器，并按 CMSIS-SVD 自动解码每个位域的数值与含义。"
        "支持指定单个 register、多个 registers 列表，或 read_all=True（可加 diff_only=True 仅保留偏离复位值的寄存器以节省上下文）。"
    ),
)
def inspect_peripheral(
    peripheral: Annotated[str, Field(description="外设名称，如 GPIOA / RCC / USART1")],
    register: Annotated[str | None, Field(description="单个寄存器名，如 MODER / CR1")] = None,
    registers: Annotated[list[str] | None, Field(description="一次读取多个寄存器名，如 ['MODER', 'ODR']")] = None,
    read_all: Annotated[bool, Field(description="为 True 时读取该外设全部非 write-only 寄存器")] = False,
    diff_only: Annotated[bool, Field(description="批量读取时仅返回与 reset_value 不同的寄存器")] = False,
    svd_path: Annotated[str | None, Field(description=".svd 文件路径（不传则读环境变量或 project_dir 配置）")] = None,
    device: Annotated[str | None, Field(description="目标芯片型号（未连接时用于自动连接）")] = None,
    backend: Annotated[str | None, Field(description="可选探针后端：jlink / openocd / mock")] = None,
    project_dir: Annotated[str | None, Field(description="可选固件工程目录")] = None,
) -> dict[str, Any]:
    prof = _try_profile(project_dir)
    resolved_svd = svd_path or os.environ.get("MCU_HARNESS_SVD_PATH") or (prof.paths.svd_path if prof else None)
    if not resolved_svd:
        return {"error": "请提供 svd_path / project_dir 参数，或设置环境变量 MCU_HARNESS_SVD_PATH"}

    eff_dev = device or (prof.target.device if prof else None)
    eff_backend = backend or (prof.target.probe_backend if prof and prof.manifest_path else None)

    try:
        svd_dev = load_svd(resolved_svd)
        with default_probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
            res = inspect_peripheral_registers(
                probe,
                svd_dev,
                peripheral,
                register_name=register,
                registers=registers,
                read_all=read_all,
            )
    except Exception as e:
        return {"error": str(e)}

    if diff_only and isinstance(res, PeripheralSnapshot):
        filtered = [r for r in res.registers if r.is_reset_value is not True]
        out = res.to_dict()
        out["total_read"] = len(res.registers)
        out["registers"] = [r.to_dict() for r in filtered]
        return out

    if isinstance(res, DecodedRegister):
        return res.to_dict()
    return res.to_dict()


@mcp.tool(
    name="diagnose_peripheral",
    description=(
        "一站式跨外设因果体检医生（排查‘为什么 USART/SPI/I2C/TIM/ADC 不工作’）："
        "自动串联核查 1) RCC 外设时钟使能门控；2) Cortex-M NVIC 中断使能/挂起状态；"
        "3) 外设自身主使能位、偏离复位值寄存器与错误/忙标志位（如 ORE/NACKF/BUSY）；4) 全片已配置 GPIO 引脚复用与开漏检查。"
        "当 peripheral='GPIO_ALL' 时直接返回全片已配置 GPIO 引脚复用快照。"
    ),
)
def diagnose_peripheral(
    peripheral: Annotated[str, Field(description="目标外设名称（如 'USART1' / 'I2C1' / 'SPI1'），或 'GPIO_ALL' 查看全片引脚复用")],
    include_gpio_mux: Annotated[bool, Field(description="是否附带已开启时钟端口的非默认 GPIO 引脚复用快照")] = True,
    svd_path: Annotated[str | None, Field(description=".svd 文件路径（不传则读环境变量或 project_dir 配置）")] = None,
    device: Annotated[str | None, Field(description="目标芯片型号")] = None,
    backend: Annotated[str | None, Field(description="可选探针后端：jlink / openocd / mock")] = None,
    project_dir: Annotated[str | None, Field(description="可选固件工程目录")] = None,
) -> dict[str, Any]:
    prof = _try_profile(project_dir)
    resolved_svd = svd_path or os.environ.get("MCU_HARNESS_SVD_PATH") or (prof.paths.svd_path if prof else None)
    if not resolved_svd:
        return {"error": "请提供 svd_path / project_dir 参数，或设置环境变量 MCU_HARNESS_SVD_PATH"}

    eff_dev = device or (prof.target.device if prof else None)
    eff_backend = backend or (prof.target.probe_backend if prof and prof.manifest_path else None)

    try:
        svd_dev = load_svd(resolved_svd)
        with default_probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
            if peripheral.strip().upper() == "GPIO_ALL":
                pins = _run_scan_gpio_pins(probe, svd_dev)
                return {"device": svd_dev.name, "configured_pin_count": len(pins), "pins": [p.to_dict() for p in pins]}
            rep = _run_diagnose_peripheral(probe, svd_dev, peripheral, include_gpio_mux=include_gpio_mux)
            return rep.to_dict()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(
    name="inspect_board",
    description=(
        "解析工程内 STM32CubeMX .ioc 与 mcu-harness.toml [board] 板级外部器件接线定义（传感器型号、总线地址、极性、安全限幅）；"
        "当 live_check=True 时还会连板实时对比 .ioc 期望引脚模式与真机 GPIOx->MODER/AFR/OTYPER 实测状态，抓出漏初始化或被覆盖的引脚漂移。"
    ),
)
def inspect_board(
    project_dir: Annotated[str, Field(description="固件工程根目录")] = ".",
    live_check: Annotated[bool, Field(description="是否通过探针连板执行真机引脚模式漂移对账")] = False,
    ioc_path: Annotated[str | None, Field(description="可选显式指定 .ioc 文件路径")] = None,
    svd_path: Annotated[str | None, Field(description="live_check=True 时使用的 .svd 文件路径（不传自动发现）")] = None,
    device: Annotated[str | None, Field(description="目标芯片型号")] = None,
    backend: Annotated[str | None, Field(description="可选探针后端")] = None,
) -> dict[str, Any]:
    prof = _try_profile(project_dir)
    try:
        if not live_check:
            rep = _run_inspect_board_context(project_dir, ioc_path=ioc_path)
            return rep.to_dict()

        resolved_svd = svd_path or os.environ.get("MCU_HARNESS_SVD_PATH") or (prof.paths.svd_path if prof else None)
        if not resolved_svd:
            return {"error": "live_check=True 需要提供 svd_path 或在工程/环境变量中配置 .svd"}
        svd_dev = load_svd(resolved_svd)
        eff_dev = device or (prof.target.device if prof else None)
        eff_backend = backend or (prof.target.probe_backend if prof and prof.manifest_path else None)
        with default_probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
            rep = _run_inspect_board_context(project_dir, ioc_path=ioc_path, probe=probe, svd_device=svd_dev)
            return rep.to_dict()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(
    name="watch_variable",
    description=(
        "基于固件 .elf 的 DWARF (v2~v5) 调试信息**无需暂停 CPU**直接读取或高频采样运行中的 C 变量（Live Watch）："
        "- action='read'：支持整棵嵌套结构体、枚举（自动翻译枚举名）、数组、位域与指针表达式（如 'g_ctrl.pid.kp' 或 'g_sensors[1]'），也可传 expressions 列表批量读取；"
        "- action='sample'：对数值/枚举标量连续采样 samples 次，统计 min/max/mean/delta 与 changing 趋势；"
        "- action='list'：列出固件中的全局/静态变量及类型摘要。"
    ),
)
def watch_variable(
    action: Annotated[str, Field(description="操作：read（默认）/ sample / list")] = "read",
    expression: Annotated[str | None, Field(description="单个 C 变量表达式，如 'g_motor_ctrl' 或 'g_motor_ctrl.state'")] = None,
    expressions: Annotated[list[str] | None, Field(description="action='read' 时可一次读取多个变量表达式")] = None,
    deref: Annotated[bool, Field(description="action='read' 遇到指针时是否自动解引用一层目标类型")] = False,
    max_depth: Annotated[int, Field(description="结构体最大递归展开深度（1..8）")] = 4,
    samples: Annotated[int, Field(description="action='sample' 的连续采样次数（2..500）")] = 20,
    interval_ms: Annotated[float, Field(description="action='sample' 的采样间隔毫秒数")] = 10.0,
    name_filter: Annotated[str | None, Field(description="action='list' 时按变量名子串过滤")] = None,
    elf_path: Annotated[str | None, Field(description="带调试符号的固件 .elf 路径（不传则自动从 project_dir 或环境变量发现）")] = None,
    project_dir: Annotated[str | None, Field(description="可选固件工程目录")] = None,
    device: Annotated[str | None, Field(description="目标芯片型号")] = None,
    backend: Annotated[str | None, Field(description="可选探针后端：jlink / openocd / mock")] = None,
) -> dict[str, Any]:
    act = action.strip().lower()
    prof = _try_profile(project_dir)
    eff_elf = elf_path or os.environ.get("MCU_HARNESS_ELF_PATH") or (prof.paths.elf_path if prof else None)
    if not eff_elf:
        return {"error": "请提供 elf_path 参数，或传入已编译出 .elf 的 project_dir"}

    try:
        if act == "list":
            idx = load_dwarf_index(eff_elf)
            flt = (name_filter or "").lower()
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

        eff_dev = device or (prof.target.device if prof else None)
        eff_backend = backend or (prof.target.probe_backend if prof and prof.manifest_path else None)

        if act == "read":
            if expressions:
                with default_probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
                    batch = _run_read_variables(probe, eff_elf, expressions, deref=deref, max_depth=max_depth)
                return {"variables": {k: val.to_dict() for k, val in batch.items()}}
            if not expression:
                return {"error": "expression (or expressions) is required when action='read'"}
            with default_probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
                val = _run_read_variable(probe, eff_elf, expression, deref=deref, max_depth=max_depth)
            return val.to_dict()

        if act == "sample":
            if not expression:
                return {"error": "expression is required when action='sample'"}
            with default_probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
                rep = _run_sample_variable(probe, eff_elf, expression, samples=samples, interval_ms=interval_ms)
            return rep.to_dict()

        return {"error": f"Unknown action {action!r} (expected 'read', 'sample', or 'list')"}
    except (DwarfError, LiveWatchError, FileNotFoundError, ValueError) as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(
    name="read_debug_log",
    description=(
        "从后台非阻塞环形缓冲区读取固件实时日志或发送下行命令，统一支持 **SEGGER RTT**（source='rtt'，默认）与 **串口 UART**（source='uart'）。"
        "支持 tail_lines 取最新 N 行、since_seq 增量拉取、pattern+context_lines 关键词上下文过滤，"
        "以及 action='read'|'write'|'start'|'stop'|'clear'|'status'|'list_ports'。"
    ),
)
def read_debug_log(
    action: Annotated[str, Field(description="操作：read（默认）/ write / start / stop / clear / status / list_ports")] = "read",
    source: Annotated[str, Field(description="日志源通道：rtt（默认 SEGGER RTT）或 uart（串口 /dev/ttyUSB* / /dev/ttyACM*）")] = "rtt",
    tail_lines: Annotated[int, Field(description="最多返回的日志行数（1..500）")] = 50,
    since_seq: Annotated[int | None, Field(description="仅返回 seq 大于该值的增量新日志")] = None,
    pattern: Annotated[str | None, Field(description="按关键词或正则过滤日志（如 'HardFault' 或 'ERROR'）")] = None,
    regex: Annotated[bool, Field(description="为 True 时把 pattern 当作正则表达式")] = False,
    context_lines: Annotated[int, Field(description="命中 pattern 时附带的前后上下文行数")] = 2,
    channel: Annotated[int, Field(description="SEGGER RTT 通道号，默认 0")] = 0,
    elf_path: Annotated[str | None, Field(description="固件 .elf 路径（用于自动定位 _SEGGER_RTT 控制块地址，跳过全 RAM 扫描）")] = None,
    block_address: Annotated[str | int | None, Field(description="可选 RTT 控制块地址，如 '0x20005170'；不传则优先查 elf_path")] = None,
    port: Annotated[str | None, Field(description="source='uart' 时的串口设备路径，如 '/dev/ttyACM0'")] = None,
    baudrate: Annotated[int, Field(description="source='uart' 时的波特率，默认 115200")] = 115200,
    include_partial: Annotated[bool, Field(description="是否附带末尾尚未换行的半行日志")] = True,
    command: Annotated[str | None, Field(description="action='write' 时发送到 RTT 或 UART 下行通道的文本指令")] = None,
    device: Annotated[str | None, Field(description="目标芯片型号（RTT 模式下未连接时用于自动连接）")] = None,
    project_dir: Annotated[str | None, Field(description="可选固件工程目录（用于自动发现 elf_path 与芯片型号）")] = None,
) -> dict[str, Any]:
    act = action.strip().lower()
    src = source.strip().lower()
    if act == "list_ports":
        ports = [p.to_dict() for p in list_serial_ports()]
        return {"count": len(ports), "ports": ports}

    if src not in ("rtt", "uart"):
        return {"error": f"Unknown source {source!r} (expected 'rtt' or 'uart')"}
    if tail_lines < 1 or tail_lines > 500:
        return {"error": "tail_lines must be between 1 and 500"}
    if context_lines < 0 or context_lines > 50:
        return {"error": "context_lines must be between 0 and 50"}

    # ---- UART 分支 -------------------------------------------------------
    if src == "uart":
        try:
            if act == "status":
                return {"status": default_uart_collector.status().to_dict()}
            if act == "start":
                st_u = default_uart_collector.start(port=port, baudrate=baudrate)
                return {"status": st_u.to_dict()}
            if act == "stop":
                st_u = default_uart_collector.stop()
                return {"status": st_u.to_dict()}
            if act == "clear":
                cleared = default_uart_collector.clear()
                return {"cleared": cleared, "status": default_uart_collector.status().to_dict()}
            if act == "write":
                if not command:
                    return {"error": "command is required when action='write'"}
                if port:
                    default_uart_collector.start(port=port, baudrate=baudrate)
                sent = default_uart_collector.write(command)
                default_safety_guard.record_audit("uart_write", port or (default_uart_collector.status().port or "uart"), detail=command[:80])
                return {"written_bytes": sent, "source": "uart", "status": default_uart_collector.status().to_dict()}
            if act == "read":
                res_u, st_u = default_uart_collector.read(
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
            return {"error": f"Unknown action {action!r} (expected read/write/start/stop/clear/status/list_ports)"}
        except Exception as e:
            return {"error": str(e)}

    # ---- SEGGER RTT 分支 -------------------------------------------------
    try:
        addr = _parse_address(block_address, "block_address") if block_address is not None else None
    except ValueError as e:
        return {"error": str(e)}

    prof = _try_profile(project_dir)
    eff_elf = elf_path or (prof.paths.elf_path if prof else None)
    eff_dev = device or (prof.target.device if prof else None)

    try:
        if act == "status":
            return {"status": default_rtt_collector.status().to_dict()}
        if act == "start":
            st = default_rtt_collector.start(
                channel=channel, block_address=addr, elf_path=eff_elf, device=eff_dev
            )
            return {"status": st.to_dict()}
        if act == "stop":
            st = default_rtt_collector.stop()
            return {"status": st.to_dict()}
        if act == "clear":
            cleared = default_rtt_collector.clear()
            return {"cleared": cleared, "status": default_rtt_collector.status().to_dict()}
        if act == "write":
            if not command:
                return {"error": "command is required when action='write'"}
            default_rtt_collector.start(channel=channel, block_address=addr, elf_path=eff_elf, device=eff_dev)
            raw_cmd = command.encode("utf-8")
            with default_probe_manager.session(device=eff_dev) as probe:
                written = probe.rtt_write(raw_cmd, channel=channel)
            default_safety_guard.record_audit("rtt_write", f"ch{channel}", detail=command[:80])
            return {"written_bytes": written, "channel": channel}
        if act == "read":
            res, st = default_rtt_collector.read(
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
        return {"error": f"Unknown action {action!r} (expected read/write/start/stop/clear/status/list_ports)"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(
    name="diagnose_crash",
    description=(
        "一键诊断 Cortex-M 全系列（M0+/M3/M4/M7/M23/M33/M55）死机/HardFault/卡死现场："
        "自动识别 CPUID、解码 CFSR/HFSR/SFSR/MMFAR/BFAR（含 M33 STKOF 栈限制溢出）、解包 EXC_RETURN 异常压栈帧，"
        "并结合固件 .elf 与 DWARF 还原触发崩溃的精确 C 文件、函数、代码行号与修复建议。"
    ),
)
def diagnose_crash(
    elf_path: Annotated[str | None, Field(description="固件 .elf 路径（不传则读取环境变量或按 project_dir 自动扫描最新 .elf）")] = None,
    project_dir: Annotated[str | None, Field(description="可选固件工程根目录（用于自动发现 .elf 并把源码绝对路径缩短为相对路径）")] = None,
    keep_halted: Annotated[bool, Field(description="若采样前 CPU 正在运行，采样后是否保持暂停（默认 False 自动恢复运行；已暂停现场始终保持暂停）")] = False,
    device: Annotated[str | None, Field(description="目标芯片型号")] = None,
    backend: Annotated[str | None, Field(description="可选探针后端：jlink / openocd / mock")] = None,
) -> dict[str, Any]:
    prof = _try_profile(project_dir)
    eff_elf = elf_path or os.environ.get("MCU_HARNESS_ELF_PATH") or (prof.paths.elf_path if prof else None)
    eff_proj = project_dir or (prof.project_dir if prof else None)
    eff_dev = device or (prof.target.device if prof else None)
    eff_backend = backend or (prof.target.probe_backend if prof and prof.manifest_path else None)

    try:
        with default_probe_manager.session(
            device=eff_dev,
            backend=eff_backend,
            openocd_interface=prof.target.openocd_interface if prof else None,
            openocd_target=prof.target.openocd_target if prof else None,
        ) as probe:
            report = _run_diagnose_crash(
                probe,
                elf_path=eff_elf,
                project_dir=eff_proj,
                keep_halted=keep_halted,
            )
            return report.to_dict()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(
    name="check_architecture",
    description=(
        "对嵌入式 C/C++ 工程执行分层架构与中断并发静态体检："
        "1) 检查分层边界违规与违禁原厂头文件（如 App/Service 越级调 HAL）；"
        "2) 从所有 ISR 中断入口做传递调用图扫描，揪出直接或跨文件间接调用的阻塞/不可重入函数（如 HAL_Delay / malloc / printf）；"
        "3) 检测 ISR 与主循环共享但漏加 volatile 的全局变量。"
    ),
)
def check_architecture(
    project_dir: Annotated[str, Field(description="待扫描的嵌入式工程根目录")] = ".",
    manifest_path: Annotated[str | None, Field(description="可选显式指定含 [architecture] 规则的 mcu-harness.toml")] = None,
) -> dict[str, Any]:
    try:
        report = _run_check_architecture(project_dir, manifest_path=manifest_path)
        return report.to_dict()
    except (ArchError, ConfigError, OSError) as e:
        return {"error": str(e)}


@mcp.tool(
    name="scaffold_code",
    description=(
        "生成跨芯片可移植 C11 嵌入式模块、四层工程骨架与零硬件主机单元测试代码："
        "kind='pal' 生成硬件抽象接口头文件；kind='bsp' 生成标准 cfg/ctx/data/ops + safe_off 器件驱动与 Mock 单测；"
        "kind='service' 生成平台无关非阻塞状态机与单测；kind='skeleton' 初始化完整四层可编译测试工程。"
    ),
)
def scaffold_code(
    project_dir: Annotated[str, Field(description="目标工程根目录")],
    kind: Annotated[str, Field(description="生成类型：pal / bsp / service / skeleton")],
    name: Annotated[str | None, Field(description="模块名称（kind 为 bsp 或 service 时必填，如 'ina226' 或 'thermal'）")] = None,
    overwrite: Annotated[bool, Field(description="是否覆盖已存在的同名目标文件")] = False,
) -> dict[str, Any]:
    try:
        res = _run_scaffold_code(project_dir, kind=kind, name=name, overwrite=overwrite)
        return res.to_dict()
    except (ScaffoldError, OSError) as e:
        return {"error": str(e)}


@mcp.tool(
    name="inspect_rtos",
    description=(
        "无需暂停 CPU 透视正在运行的 RTOS 内核状态（自动识别 FreeRTOS / Zephyr / RT-Thread）："
        "列出所有任务状态（RUNNING/READY/BLOCKED/SUSPENDED）、优先级、栈指针及栈高水位（Stack High-Water-Mark）溢出告警。"
    ),
)
def inspect_rtos(
    elf_path: Annotated[str | None, Field(description="带调试符号的固件 .elf 路径（不传则自动从 project_dir 或环境变量发现）")] = None,
    project_dir: Annotated[str | None, Field(description="可选固件工程根目录")] = None,
    rtos: Annotated[str, Field(description="RTOS 类型：auto（自动探测）/ freertos / zephyr / rtthread")] = "auto",
    max_tasks: Annotated[int, Field(description="最多遍历任务数上限（1..256）")] = 64,
    check_stack: Annotated[bool, Field(description="是否扫描栈底高水位水印字节并检测栈溢出")] = True,
    device: Annotated[str | None, Field(description="目标芯片型号")] = None,
    backend: Annotated[str | None, Field(description="可选探针后端：jlink / openocd / mock")] = None,
) -> dict[str, Any]:
    if max_tasks < 1 or max_tasks > 256:
        return {"error": "max_tasks must be between 1 and 256"}
    prof = _try_profile(project_dir)
    eff_elf = elf_path or os.environ.get("MCU_HARNESS_ELF_PATH") or (prof.paths.elf_path if prof else None)
    if not eff_elf:
        return {"error": "请提供 elf_path 参数，或传入已编译出 .elf 的 project_dir"}

    eff_dev = device or (prof.target.device if prof else None)
    eff_backend = backend or (prof.target.probe_backend if prof and prof.manifest_path else None)
    try:
        with default_probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
            rep = _run_inspect_rtos(probe, eff_elf, rtos=rtos, max_tasks=max_tasks, check_stack=check_stack)
            return rep.to_dict()
    except (RtosError, FileNotFoundError, ValueError) as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(
    name="trace_target",
    description=(
        "Cortex-M CoreSight DWT 非侵入性能剖析与硬件数据观察点："
        "- action='measure_cycles'：不暂停 CPU 测算 DWT_CYCCNT 差分与真实 SYSCLK 主频；"
        "- action='profile_pc'：不暂停 CPU 周期采样 DWT_PCSR 生成函数级/行号级热点图；"
        "- action='set_watchpoint' / 'list_watchpoints' / 'clear_watchpoints'：支持按地址或 DWARF C 变量表达式挂硬件写/读观察点，抓野指针踩内存。"
    ),
)
def trace_target(
    action: Annotated[str, Field(description="操作：profile_pc（默认）/ measure_cycles / set_watchpoint / list_watchpoints / clear_watchpoints")] = "profile_pc",
    duration_ms: Annotated[float, Field(description="action='measure_cycles' 测速时间窗（毫秒）")] = 50.0,
    core_hz: Annotated[int | None, Field(description="action='measure_cycles' 可选预期主频 Hz，用于核算误差")] = None,
    samples: Annotated[int, Field(description="action='profile_pc' 采样次数（1..1000）")] = 100,
    interval_ms: Annotated[float, Field(description="action='profile_pc' 采样间隔毫秒")] = 1.0,
    watch_target: Annotated[str | int | None, Field(description="action='set_watchpoint' 的地址或 C 变量名（如 'g_ctrl.rpm' 或 '0x20000100'）")] = None,
    watch_access: Annotated[str, Field(description="观察点触发方式：write / read / rw")] = "write",
    watch_size: Annotated[int | None, Field(description="观察点宽度：1 / 2 / 4 字节（传变量名时可自动推断）")] = None,
    slot: Annotated[int | None, Field(description="可选指定 DWT 比较器槽位（不传则自动分配或清空全部）")] = None,
    elf_path: Annotated[str | None, Field(description="固件 .elf 路径（用于 PC 热点映射源码与解析变量观察点）")] = None,
    project_dir: Annotated[str | None, Field(description="可选固件工程目录")] = None,
    device: Annotated[str | None, Field(description="目标芯片型号")] = None,
    backend: Annotated[str | None, Field(description="可选探针后端：jlink / openocd / mock")] = None,
) -> dict[str, Any]:
    act = action.strip().lower()
    prof = _try_profile(project_dir)
    eff_elf = elf_path or os.environ.get("MCU_HARNESS_ELF_PATH") or (prof.paths.elf_path if prof else None)
    eff_proj = project_dir or (prof.project_dir if prof else None)
    eff_dev = device or (prof.target.device if prof else None)
    eff_backend = backend or (prof.target.probe_backend if prof and prof.manifest_path else None)

    try:
        with default_probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
            if act == "measure_cycles":
                return _run_measure_cycles(probe, duration_ms=duration_ms, core_hz=core_hz).to_dict()
            if act == "profile_pc":
                return _run_profile_pc_hotspots(
                    probe, elf_path=eff_elf, project_dir=eff_proj, samples=samples, interval_ms=interval_ms
                ).to_dict()
            if act == "set_watchpoint":
                if watch_target is None or watch_target == "":
                    return {"error": "watch_target is required when action='set_watchpoint'"}
                return _run_set_watchpoint(
                    probe,
                    watch_target,
                    access=watch_access,
                    size_bytes=watch_size,
                    slot=slot,
                    elf_path=eff_elf,
                ).to_dict()
            if act == "list_watchpoints":
                return _run_list_watchpoints(probe).to_dict()
            if act == "clear_watchpoints":
                return _run_clear_watchpoints(probe, slot=slot)
            return {
                "error": (
                    f"Unknown action {action!r} (expected measure_cycles/profile_pc/"
                    "set_watchpoint/list_watchpoints/clear_watchpoints)"
                )
            }
    except (TraceError, FileNotFoundError, ValueError) as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(
    name="capture_logic",
    description=(
        "逻辑分析仪数字波形分析与总线协议解码桥接："
        "- 传入 csv_path（或 .csv 后缀 input_file）时纯 Python 计算频率、占空比、边沿数与空闲电平；"
        "- 传入 .sr 采集文件或 driver（如 'fx2lafw'）时调用 sigrok-cli 解码 SPI / I2C / UART / PWM / CAN 协议帧。"
    ),
)
def capture_logic(
    csv_path: Annotated[str | None, Field(description="数字波形 CSV 文件路径")] = None,
    input_file: Annotated[str | None, Field(description="sigrok .sr 采集文件或 .csv 文件路径")] = None,
    driver: Annotated[str | None, Field(description="sigrok 硬件采集驱动名，如 'fx2lafw' / 'saleae-logic16'")] = None,
    channels: Annotated[str | None, Field(description="采样通道列表，如 'D0,D1,D2'")] = None,
    samplerate: Annotated[str, Field(description="采样率，默认 '4m'")] = "4m",
    samples: Annotated[int | None, Field(description="采样点数")] = None,
    duration_ms: Annotated[float | None, Field(description="采样持续毫秒数")] = None,
    protocol: Annotated[str | None, Field(description="协议解码器名称，如 'spi' / 'i2c' / 'uart' / 'pwm' / 'can'")] = None,
    protocol_options: Annotated[dict[str, Any] | str | None, Field(description="协议参数字典或冒号串，如 {'clk':'D0','mosi':'D1'}")] = None,
    time_unit_s: Annotated[float, Field(description="CSV 时间戳单位换算为秒的倍率（如微秒 CSV 传 1e-6）")] = 1.0,
) -> dict[str, Any]:
    try:
        rep = _run_decode_with_sigrok(
            csv_path=csv_path,
            input_file=input_file,
            driver=driver,
            channels=channels,
            samplerate=samplerate,
            samples=samples,
            duration_ms=duration_ms,
            protocol=protocol,
            protocol_options=protocol_options,
            time_unit_s=time_unit_s,
        )
        return rep.to_dict()
    except (HilError, ValueError, FileNotFoundError) as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(
    name="run_hil_tests",
    description=(
        "执行声明式硬件在环（HIL）自动化测试套件（支持 .toml / .json）："
        "编排 reset / rtt_write / uart_write / write_memory(受安全护栏拦截) 与 "
        "assert_variable(异步轮询) / assert_register / assert_memory / assert_log / assert_no_crash；"
        "无论测试成功还是失败，均保证无条件执行 teardown 安全停机步骤。"
    ),
)
def run_hil_tests(
    spec_path: Annotated[str, Field(description="HIL 测试套件文件（.toml / .json）路径")],
    project_dir: Annotated[str | None, Field(description="可选固件工程目录（用于自动发现 elf/svd/探针/安全策略）")] = None,
    elf_path: Annotated[str | None, Field(description="可选固件 .elf 路径")] = None,
    svd_path: Annotated[str | None, Field(description="可选芯片 .svd 路径")] = None,
    confirm: Annotated[bool, Field(description="若套件内含 write_memory 写外设步骤，须经人工批准传 True")] = False,
    device: Annotated[str | None, Field(description="目标芯片型号")] = None,
    backend: Annotated[str | None, Field(description="可选探针后端：jlink / openocd / mock")] = None,
) -> dict[str, Any]:
    prof = _try_profile(project_dir)
    eff_dev = device or (prof.target.device if prof else None)
    eff_backend = backend or (prof.target.probe_backend if prof and prof.manifest_path else None)
    eff_elf = elf_path or os.environ.get("MCU_HARNESS_ELF_PATH") or (prof.paths.elf_path if prof else None)
    eff_svd = svd_path or os.environ.get("MCU_HARNESS_SVD_PATH") or (prof.paths.svd_path if prof else None)

    try:
        with default_probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
            rep = _run_hil_suite(
                spec_path,
                probe=probe,
                project_dir=project_dir,
                elf_path=eff_elf,
                svd_path=eff_svd,
                rtt_collector=default_rtt_collector,
                uart_collector=default_uart_collector,
                confirmed=confirm,
            )
            return rep.to_dict()
    except (HilError, FileNotFoundError, ValueError) as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


def main() -> None:
    """以 stdio 模式启动 MCP Server（关闭 banner 避免污染标准输出协议帧）。"""
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
