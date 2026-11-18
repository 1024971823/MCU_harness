"""MCU Agent Harness 的 MCP Server 入口（相当于面向大模型的“HAL 接口层”）。

职责：
    只做薄封装——把底层模块（build / probe / svd / log）注册成标准 MCP 工具，
    不在这里堆砌业务逻辑。

启动方式（stdio 协议，供 Antigravity / Claude / MCP Inspector 连接）：
    PYTHONPATH=src .venv/bin/python -m mcu_harness.server
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from mcu_harness.build.runner import build_firmware as _run_build_firmware
from mcu_harness.log import default_rtt_collector
from mcu_harness.probe import ProbeError, default_probe_manager, inspect_peripheral_registers
from mcu_harness.svd import decode_register, find_peripheral, find_register, load_svd

mcp = FastMCP(
    name="mcu-harness",
    instructions=(
        "MCU 嵌入式调试辅助工具集（目标硬件：STM32H563 + SEGGER J-Link + SEGGER RTT）。"
        "编译固件请调用 build_firmware；离线查寄存器定义请调用 inspect_svd；"
        "实时连板读外设寄存器请调用 inspect_peripheral；读任意内存请调用 read_memory；"
        "非阻塞读取 SEGGER RTT 调试日志请调用 read_debug_log；"
        "管理探针连接与暂停/恢复 CPU 请调用 probe_control。"
    ),
)


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
    build_type: Annotated[str, Field(description="CMake 构建类型：Debug / Release / RelWithDebInfo / MinSizeRel")] = "Debug",
    toolchain_file: Annotated[str | None, Field(description="CMake 交叉编译工具链文件路径，如 cmake/arm-none-eabi.cmake")] = None,
    generator: Annotated[str, Field(description="CMake 生成器，默认 Ninja")] = "Ninja",
    extra_args: Annotated[list[str] | None, Field(description="额外构建参数：Make 如 ['DEBUG=1', 'V=1']，CMake 如 ['-DBOARD=xxx']")] = None,
    extra_cmake_args: Annotated[list[str] | None, Field(description="额外 CMake 参数（兼容字段，可改用 extra_args）")] = None,
    clean: Annotated[bool, Field(description="是否先 clean 再编译")] = False,
    reconfigure: Annotated[bool, Field(description="CMake 是否强制重新执行 configure")] = False,
    jobs: Annotated[int | None, Field(description="并行编译任务数（-j）")] = None,
    max_errors: Annotated[int, Field(description="最多返回的错误条数（total_errors 仍统计总数）")] = 10,
    max_warnings: Annotated[int, Field(description="最多返回的警告条数（total_warnings 仍统计总数）")] = 10,
) -> dict[str, Any]:
    try:
        result = _run_build_firmware(
            project_dir=project_dir,
            build_dir=build_dir,
            target=target,
            build_type=build_type,
            toolchain_file=toolchain_file,
            generator=generator,
            extra_cmake_args=extra_cmake_args,
            clean=clean,
            reconfigure=reconfigure,
            jobs=jobs,
            max_errors=max_errors,
            max_warnings=max_warnings,
            build_system=build_system,
            makefile=makefile,
            extra_args=extra_args,
        )
        return result.to_dict()
    except Exception as e:
        return {"success": False, "stage": "precheck", "error": str(e), "message": str(e)}


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
    svd_path: Annotated[str | None, Field(description=".svd 或 .svd.gz 文件路径；不传则读取环境变量 MCU_HARNESS_SVD_PATH")] = None,
    name_filter: Annotated[str | None, Field(description="列出外设或寄存器时按名称子串过滤")] = None,
) -> dict[str, Any]:
    resolved_svd = svd_path or os.environ.get("MCU_HARNESS_SVD_PATH")
    if not resolved_svd:
        return {"error": "请提供 svd_path 参数，或设置环境变量 MCU_HARNESS_SVD_PATH"}

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
        "管理 J-Link 调试探针的长驻会话：查看连接状态（info）、附着连接目标芯片（connect，不复位）、"
        "断开连接（disconnect）、暂停 CPU（halt）或恢复运行（resume）。"
    ),
)
def probe_control(
    action: Annotated[str, Field(description="操作：info / connect / disconnect / halt / resume")] = "info",
    device: Annotated[str | None, Field(description="目标芯片型号（默认读取 MCU_HARNESS_DEVICE 或 STM32H563ZI）")] = None,
    interface: Annotated[str | None, Field(description="调试接口：SWD 或 JTAG（默认读取 MCU_HARNESS_INTERFACE 或 SWD）")] = None,
    speed: Annotated[str | int | None, Field(description="接口速率 kHz，或 'auto'（默认读取 MCU_HARNESS_SPEED 或 'auto'）")] = None,
    serial_no: Annotated[str | None, Field(description="J-Link 序列号（默认读取 MCU_HARNESS_SERIAL）")] = None,
    reconnect: Annotated[bool, Field(description="action=connect 时是否强制重新连接")] = False,
) -> dict[str, Any]:
    act = action.strip().lower()
    try:
        if act == "disconnect":
            default_probe_manager.disconnect()
            return {"connected": False, "message": "Probe disconnected"}
        if act == "connect":
            info = default_probe_manager.connect(
                device=device,
                interface=interface,
                speed=speed,
                serial_no=serial_no,
                reconnect=reconnect,
            )
            return info.to_dict()
        if act == "info":
            with default_probe_manager.session(device=device) as probe:
                return probe.info().to_dict()
        if act == "halt":
            with default_probe_manager.session(device=device) as probe:
                ok = probe.halt()
                return {"action": "halt", "halted": probe.is_halted(), "changed": ok}
        if act == "resume":
            with default_probe_manager.session(device=device) as probe:
                ok = probe.resume()
                return {"action": "resume", "halted": probe.is_halted(), "changed": ok}
        return {"error": f"Unknown action {action!r} (expected info/connect/disconnect/halt/resume)"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(
    name="read_memory",
    description=(
        "通过 J-Link 读取目标 MCU 内存或寄存器原始值（不复位、无需暂停 CPU）。"
        "address 支持十六进制字符串（如 '0xE000ED00'）或整数。"
    ),
)
def read_memory(
    address: Annotated[str | int, Field(description="起始地址，如 '0xE000ED00' 或 '0x20000000'")],
    count: Annotated[int, Field(description="读取元素个数（1..256）")] = 1,
    width: Annotated[int, Field(description="位宽：8 / 16 / 32")] = 32,
    device: Annotated[str | None, Field(description="目标芯片型号（未连接时用于自动连接）")] = None,
) -> dict[str, Any]:
    if count < 1 or count > 256:
        return {"error": "count must be between 1 and 256"}
    if width not in (8, 16, 32):
        return {"error": "width must be 8, 16, or 32"}
    if isinstance(address, bool):
        return {"error": f"Invalid address {address!r}"}
    try:
        addr = int(address, 0) if isinstance(address, str) else int(address)
    except ValueError as e:
        return {"error": f"Invalid address {address!r}: {e}"}

    step = width // 8
    if addr < 0 or addr > 0xFFFFFFFF or (addr + count * step - 1) > 0xFFFFFFFF:
        return {"error": f"Address out of 32-bit range (0x00000000..0xFFFFFFFF): {address!r}"}
    if width == 32 and addr % 4 != 0:
        return {"error": f"Unaligned 32-bit address 0x{addr:08X} (must be 4-byte aligned)"}
    if width == 16 and addr % 2 != 0:
        return {"error": f"Unaligned 16-bit address 0x{addr:08X} (must be 2-byte aligned)"}

    try:
        with default_probe_manager.session(device=device) as probe:
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
    name="inspect_peripheral",
    description=(
        "通过 J-Link 实时读取目标芯片外设寄存器，并按 CMSIS-SVD 自动解码每个位域的数值与含义。"
        "支持指定单个 register、多个 registers 列表，或 read_all=True（自动跳过 write-only 寄存器）。"
    ),
)
def inspect_peripheral(
    peripheral: Annotated[str, Field(description="外设名称，如 GPIOA / RCC / USART1")],
    register: Annotated[str | None, Field(description="单个寄存器名，如 MODER / CR1")] = None,
    registers: Annotated[list[str] | None, Field(description="一次读取多个寄存器名，如 ['MODER', 'ODR']")] = None,
    read_all: Annotated[bool, Field(description="为 True 时读取该外设全部非 write-only 寄存器")] = False,
    svd_path: Annotated[str | None, Field(description=".svd 文件路径（不传则读环境变量 MCU_HARNESS_SVD_PATH）")] = None,
    device: Annotated[str | None, Field(description="目标芯片型号（未连接时用于自动连接）")] = None,
) -> dict[str, Any]:
    resolved_svd = svd_path or os.environ.get("MCU_HARNESS_SVD_PATH")
    if not resolved_svd:
        return {"error": "请提供 svd_path 参数，或设置环境变量 MCU_HARNESS_SVD_PATH"}

    try:
        svd_dev = load_svd(resolved_svd)
        with default_probe_manager.session(device=device) as probe:
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

    return res.to_dict()


@mcp.tool(
    name="read_debug_log",
    description=(
        "从后台非阻塞 SEGGER RTT 环形缓冲区读取固件日志（默认自动连接探针并启动后台采集）。"
        "支持 tail_lines 取最新 N 行、since_seq 增量拉取、pattern+context_lines 关键词上下文过滤，"
        "以及 action='start'|'stop'|'clear'|'status' 控制生命周期。"
    ),
)
def read_debug_log(
    action: Annotated[str, Field(description="操作：read（默认）/ start / stop / clear / status")] = "read",
    tail_lines: Annotated[int, Field(description="最多返回的日志行数（1..500）")] = 50,
    since_seq: Annotated[int | None, Field(description="仅返回 seq 大于该值的增量新日志")] = None,
    pattern: Annotated[str | None, Field(description="按关键词或正则过滤日志（如 'HardFault' 或 'ERROR'）")] = None,
    regex: Annotated[bool, Field(description="为 True 时把 pattern 当作正则表达式")] = False,
    context_lines: Annotated[int, Field(description="命中 pattern 时附带的前后上下文行数")] = 2,
    channel: Annotated[int, Field(description="SEGGER RTT 上行通道号，默认 0")] = 0,
    elf_path: Annotated[str | None, Field(description="固件 .elf 路径（用于自动定位 _SEGGER_RTT 控制块地址，跳过全 RAM 扫描）")] = None,
    block_address: Annotated[str | int | None, Field(description="可选 RTT 控制块地址，如 '0x20005170'；不传则优先查 elf_path")] = None,
    include_partial: Annotated[bool, Field(description="是否附带末尾尚未换行的半行日志")] = True,
    device: Annotated[str | None, Field(description="目标芯片型号（未连接时用于自动连接）")] = None,
) -> dict[str, Any]:
    act = action.strip().lower()
    if tail_lines < 1 or tail_lines > 500:
        return {"error": "tail_lines must be between 1 and 500"}
    if context_lines < 0 or context_lines > 50:
        return {"error": "context_lines must be between 0 and 50"}
    try:
        addr = int(block_address, 0) if isinstance(block_address, str) else block_address
    except ValueError as e:
        return {"error": f"Invalid block_address {block_address!r}: {e}"}

    try:
        if act == "status":
            return {"status": default_rtt_collector.status().to_dict()}
        if act == "start":
            st = default_rtt_collector.start(
                channel=channel, block_address=addr, elf_path=elf_path, device=device
            )
            return {"status": st.to_dict()}
        if act == "stop":
            st = default_rtt_collector.stop()
            return {"status": st.to_dict()}
        if act == "clear":
            cleared = default_rtt_collector.clear()
            return {"cleared": cleared, "status": default_rtt_collector.status().to_dict()}
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
                elf_path=elf_path,
                device=device,
            )
            return {"status": st.to_dict(), **res.to_dict()}
        return {"error": f"Unknown action {action!r} (expected read/start/stop/clear/status)"}
    except Exception as e:
        return {"error": str(e)}


def main() -> None:
    """以 stdio 模式启动 MCP Server（关闭 banner 避免污染标准输出协议帧）。"""
    mcp.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
