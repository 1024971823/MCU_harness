"""Cortex-M 死机自动尸检命令行工具（mcu-crash）。

用法示例：
    # 离线模拟模式：自动注入 Cortex-M33 空指针 HardFault 现场并输出诊断报告
    python -m mcu_harness.crash.cli --mock
    python -m mcu_harness.crash.cli --mock --json

    # 实机连板诊断（J-Link + ELF 源码行号还原）
    python -m mcu_harness.crash.cli --device STM32H563ZI --elf build/app.elf --project-dir .

    # 使用 OpenOCD 后端诊断
    python -m mcu_harness.crash.cli --backend openocd --openocd-if stlink --device STM32F407VG --elf build/app.elf
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from mcu_harness.probe import DebugProbe, JLinkProbe, MockProbe, OpenOCDProbe, ProbeError

from .cortex_m import diagnose_crash


def _make_probe(args: argparse.Namespace) -> DebugProbe:
    if args.mock or args.backend == "mock":
        mock = MockProbe(device=args.device, interface=args.interface)
        # 离线演示默认自动注入示例 HardFault（空指针结构体成员偏移 0x04 解引用）
        mock.inject_cortex_m_fault(exc_return=0xFFFFFFB8)
        return mock
    if args.backend == "openocd":
        return OpenOCDProbe(
            device=args.device,
            interface=args.interface,
            speed=args.speed,
            serial_no=args.serial,
            openocd_interface=args.openocd_if,
            openocd_target=args.openocd_target,
        )
    return JLinkProbe(
        device=args.device,
        interface=args.interface,
        speed=args.speed,
        serial_no=args.serial,
        lib_path=args.jlink_lib,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mcu-crash",
        description="Cortex-M (Armv6-M / Armv7-M / Armv8-M) automatic crash forensics CLI",
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
    ap.add_argument("--serial", help="探针序列号（多探针时指定）")
    ap.add_argument("--jlink-lib", help="显式指定 libjlinkarm.so 路径")
    ap.add_argument("--openocd-if", default="cmsis-dap", help="OpenOCD 调试器脚本名，如 cmsis-dap / stlink")
    ap.add_argument("--openocd-target", help="OpenOCD 目标芯片脚本名，如 stm32h5x / stm32f4x")
    ap.add_argument(
        "--elf",
        default=os.environ.get("MCU_HARNESS_ELF_PATH"),
        help="固件 .elf 路径（用于 DWARF 源码行号与函数符号还原）",
    )
    ap.add_argument("--project-dir", help="固件工程根目录（用于归一化相对源码路径）")
    ap.add_argument("--keep-halted", action="store_true", help="若芯片原本在运行，采样后保持暂停不恢复")
    ap.add_argument("--mock", action="store_true", help="启用离线模拟探针并自动注入示例 HardFault 现场")
    ap.add_argument("--json", action="store_true", help="以 JSON 格式输出结构化尸检报告")

    args = ap.parse_args(argv)
    args.speed = int(args.speed) if str(args.speed).isdigit() else args.speed

    try:
        with _make_probe(args) as probe:
            report = diagnose_crash(
                probe,
                elf_path=args.elf,
                project_dir=args.project_dir,
                keep_halted=args.keep_halted,
            )
        if args.json:
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(report.to_text())
        return 0
    except (ProbeError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
