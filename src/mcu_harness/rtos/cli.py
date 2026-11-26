"""无停机 RTOS 内核透视与栈高水位扫描命令行工具（mcu-rtos）。

用法示例：
    # 离线模拟模式：从 ELF 预载内存并自动识别 FreeRTOS / Zephyr / RT-Thread
    python -m mcu_harness.rtos.cli --elf build/app.elf --mock
    python -m mcu_harness.rtos.cli --elf build/app.elf --mock --json

    # 实机连板透视（全程不暂停 CPU）
    python -m mcu_harness.rtos.cli --elf build/app.elf --device STM32H563ZI --rtos freertos
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from mcu_harness.dwarf.parser import DwarfError, extract_elf_initialized_memory
from mcu_harness.elf.symbols import ElfError
from mcu_harness.probe import DebugProbe, JLinkProbe, MockProbe, OpenOCDProbe, ProbeError

from .inspector import RtosError, inspect_rtos


def _apply_mock_poke(mock: MockProbe, spec: str) -> None:
    if "=" not in spec:
        raise ValueError(f"Invalid --mock-poke format {spec!r}, expected ADDR=VALUE")
    lhs, rhs = spec.split("=", 1)
    addr = int(lhs.strip(), 0)
    rhs_clean = rhs.strip()
    if rhs_clean.startswith("bytes:"):
        raw = bytes.fromhex(rhs_clean[len("bytes:") :].strip())
        for idx, b in enumerate(raw):
            mock._mem[addr + idx] = b
    else:
        val = int(rhs_clean, 0)
        mock.set_word(addr, val)


def _make_probe(args: argparse.Namespace) -> DebugProbe:
    speed = int(args.speed) if str(args.speed).isdigit() else args.speed
    if args.mock or args.backend == "mock":
        mock = MockProbe(device=args.device, interface=args.interface)
        for seg_addr, seg_bytes in extract_elf_initialized_memory(args.elf).items():
            for off, b in enumerate(seg_bytes):
                mock._mem[seg_addr + off] = b
        for poke_spec in args.mock_poke:
            _apply_mock_poke(mock, poke_spec)
        return mock
    if args.backend == "openocd":
        return OpenOCDProbe(
            device=args.device,
            interface=args.interface,
            speed=speed,
            serial_no=args.serial,
            openocd_interface=args.openocd_if,
            openocd_target=args.openocd_target,
        )
    return JLinkProbe(
        device=args.device,
        interface=args.interface,
        speed=speed,
        serial_no=args.serial,
        lib_path=args.jlink_lib,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mcu-rtos",
        description="Non-intrusive RTOS (FreeRTOS / Zephyr / RT-Thread) kernel & stack high-water-mark inspector",
    )
    ap.add_argument(
        "--elf",
        default=os.environ.get("MCU_HARNESS_ELF_PATH"),
        required=not bool(os.environ.get("MCU_HARNESS_ELF_PATH")),
        help="固件 .elf 文件路径（或通过环境变量 MCU_HARNESS_ELF_PATH 指定）",
    )
    ap.add_argument(
        "--rtos",
        default="auto",
        choices=["auto", "freertos", "zephyr", "rtthread"],
        help="目标 RTOS 类型（默认 auto 自动识别）",
    )
    ap.add_argument(
        "--no-stack",
        action="store_true",
        help="跳过栈内存高水位水印字节扫描，仅读取任务列表与状态",
    )
    ap.add_argument(
        "--max-tasks",
        type=int,
        default=64,
        help="最大遍历任务数（默认 64）",
    )
    ap.add_argument(
        "--backend",
        default=os.environ.get("MCU_HARNESS_BACKEND", "jlink"),
        choices=["jlink", "openocd", "mock"],
        help="调试探针后端：jlink / openocd / mock（默认 jlink）",
    )
    ap.add_argument(
        "--device",
        default=os.environ.get("MCU_HARNESS_DEVICE", "STM32H563ZI"),
        help="目标芯片型号，默认 STM32H563ZI",
    )
    ap.add_argument("--interface", default="SWD", choices=["SWD", "JTAG"], help="调试接口，默认 SWD")
    ap.add_argument("--speed", default="auto", help="SWD/JTAG 速率（kHz 或 auto）")
    ap.add_argument("--serial", help="探针序列号")
    ap.add_argument("--jlink-lib", help="显式指定 libjlinkarm.so 路径")
    ap.add_argument("--openocd-if", default="cmsis-dap", help="OpenOCD 调试器脚本名，如 cmsis-dap / stlink")
    ap.add_argument("--openocd-target", help="OpenOCD 目标芯片脚本名，如 stm32h5x / stm32f4x")
    ap.add_argument(
        "--mock",
        action="store_true",
        help="使用离线模拟探针（默认自动从 --elf 预载 .data/.rodata 初始字节）",
    )
    ap.add_argument(
        "--mock-poke",
        action="append",
        default=[],
        metavar="ADDR=VALUE",
        help="离线模拟模式下向指定地址写入 32 位值或十六进制字节（如 0x20000000=0x12345678）",
    )
    ap.add_argument("--json", action="store_true", help="以 JSON 格式输出结构化内核透视报告")

    args = ap.parse_args(argv)
    try:
        with _make_probe(args) as probe:
            report = inspect_rtos(
                probe,
                args.elf,
                rtos=args.rtos,
                max_tasks=args.max_tasks,
                check_stack=not args.no_stack,
            )
        if args.json:
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(report.to_text())
        return 0
    except (ProbeError, RtosError, DwarfError, ElfError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
