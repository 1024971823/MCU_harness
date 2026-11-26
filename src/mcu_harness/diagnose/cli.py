"""跨外设因果体检与板级配置对账命令行工具（mcu-doctor）。

用法示例：
    mcu-doctor peripheral USART1 --svd tests/fixtures/stm32h563_mini.svd --mock
    mcu-doctor peripheral USART1 --svd STM32H563.svd --json
    mcu-doctor pins --svd tests/fixtures/stm32h563_mini.svd --mock
    mcu-doctor board . --svd STM32H563.svd --live
    mcu-doctor board /path/to/project --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from mcu_harness.config import ConfigError, discover_project
from mcu_harness.probe import DebugProbe, JLinkProbe, MockProbe, OpenOCDProbe, ProbeError
from mcu_harness.svd import SvdDevice, load_svd

from .board_context import inspect_board_context
from .peripheral_doctor import diagnose_peripheral, scan_configured_gpio_pins


def _add_common_options(parser: argparse.ArgumentParser, *, suppress_defaults: bool) -> None:
    d_none = argparse.SUPPRESS if suppress_defaults else None
    d_false = argparse.SUPPRESS if suppress_defaults else False

    parser.add_argument(
        "--svd",
        default=argparse.SUPPRESS if suppress_defaults else os.environ.get("MCU_HARNESS_SVD_PATH"),
        help="CMSIS-SVD (.svd / .svd.gz) 文件路径（未指定时自动从工程配置探测）",
    )
    parser.add_argument(
        "--backend",
        choices=["jlink", "openocd", "mock"],
        default=argparse.SUPPRESS if suppress_defaults else os.environ.get("MCU_HARNESS_BACKEND", "jlink"),
        help="调试探针后端：jlink / openocd / mock",
    )
    parser.add_argument(
        "--device",
        default=argparse.SUPPRESS if suppress_defaults else os.environ.get("MCU_HARNESS_DEVICE", "STM32H563ZI"),
        help="目标芯片型号，默认 STM32H563ZI",
    )
    parser.add_argument(
        "--interface",
        choices=["SWD", "JTAG"],
        default=argparse.SUPPRESS if suppress_defaults else "SWD",
        help="调试接口，默认 SWD",
    )
    parser.add_argument(
        "--speed",
        default=argparse.SUPPRESS if suppress_defaults else "auto",
        help="SWD/JTAG 速率（kHz 或 auto）",
    )
    parser.add_argument("--serial", default=d_none, help="探针序列号（多探针时指定）")
    parser.add_argument("--jlink-lib", default=d_none, help="显式指定 libjlinkarm.so 路径")
    parser.add_argument(
        "--openocd-if",
        default=argparse.SUPPRESS if suppress_defaults else "cmsis-dap",
        help="OpenOCD 调试器脚本名，如 cmsis-dap / stlink / jlink",
    )
    parser.add_argument("--openocd-target", default=d_none, help="OpenOCD 目标芯片脚本名，如 stm32h5x / stm32f4x")
    parser.add_argument("--mock", action="store_true", default=d_false, help="使用内存模拟探针（离线自检/演示）")
    parser.add_argument("--json", action="store_true", default=d_false, help="输出结构化 JSON")


def _resolve_svd_device(svd_arg: str | None, project_dir: str | None = None) -> SvdDevice:
    svd_path = svd_arg
    if not svd_path:
        try:
            prof = discover_project(project_dir or os.getcwd())
            svd_path = prof.paths.svd_path
        except ConfigError:
            svd_path = None
    if not svd_path:
        raise ValueError("SVD file is required: pass --svd <path>, set MCU_HARNESS_SVD_PATH, or configure [paths].svd in mcu-harness.toml")
    return load_svd(svd_path)


def _prepare_mock_gpio_demo(mock: MockProbe, svd_dev: SvdDevice) -> None:
    """离线 --mock 演示 pins / board 时，预置 GPIOA 时钟开启且仅 PA5 为 Output(ODR=1)，其余保持 Analog 复位态。"""
    for p in svd_dev.peripherals:
        if p.name.upper() == "RCC":
            for r in p.registers:
                for f in r.fields:
                    if f.name.upper() == "GPIOAEN":
                        mock.set_word(p.reg_address(r), 1 << f.bit_offset)
        elif p.name.upper() == "GPIOA":
            for r in p.registers:
                if r.name.upper() in ("MODER", "GPIOA_MODER"):
                    rst = r.reset_value if r.reset_value is not None else 0xABFFFFFF
                    # PA5 = Output (0b01)，其余引脚保持 resetValue（如 PA9 = 0b11 Analog）
                    val = (rst & ~(0x3 << 10)) | (0x1 << 10)
                    mock.set_word(p.reg_address(r), val)
                elif r.name.upper() in ("ODR", "GPIOA_ODR"):
                    mock.set_word(p.reg_address(r), 1 << 5)


def _make_probe(args: argparse.Namespace, *, svd_dev: SvdDevice | None = None, seed_gpio: bool = False) -> DebugProbe:
    speed = int(args.speed) if str(args.speed).isdigit() else args.speed
    if args.mock or args.backend == "mock":
        mock = MockProbe(device=args.device, interface=args.interface)
        if seed_gpio and svd_dev is not None:
            _prepare_mock_gpio_demo(mock, svd_dev)
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


def build_parser() -> argparse.ArgumentParser:
    common_sub = argparse.ArgumentParser(add_help=False)
    _add_common_options(common_sub, suppress_defaults=True)

    ap = argparse.ArgumentParser(
        prog="mcu-doctor",
        description="Cross-peripheral causal doctor & board hardware context inspector CLI",
    )
    _add_common_options(ap, suppress_defaults=False)

    sub = ap.add_subparsers(dest="cmd", required=True)

    p_periph = sub.add_parser(
        "peripheral",
        parents=[common_sub],
        help="一站式跨外设因果体检（RCC 时钟门控 + NVIC 中断通路 + 自身标志位 + GPIO 复用）",
    )
    p_periph.add_argument("peripheral_name", metavar="PERIPH", help="外设名称（如 USART1 / SPI1 / I2C1 / TIM1 / GPIOA）")
    p_periph.add_argument("--no-gpio", action="store_true", help="跳过全片 GPIO 引脚复用快照扫描")

    sub.add_parser(
        "pins",
        parents=[common_sub],
        help="扫描全片已使能时钟 GPIO 端口的非默认 Analog 引脚配置与复用号",
    )

    p_board = sub.add_parser(
        "board",
        parents=[common_sub],
        help="解析 STM32CubeMX .ioc 与 mcu-harness.toml 板载器件真相，支持 --live 与真机寄存器对账",
    )
    p_board.add_argument("project_dir", nargs="?", default=".", help="工程目录路径（默认当前目录 .）")
    p_board.add_argument("--ioc", help="显式指定 .ioc 文件路径")
    p_board.add_argument("--manifest", help="显式指定 mcu-harness.toml 路径")
    p_board.add_argument("--live", action="store_true", help="连接探针并读取真机 GPIO 寄存器执行实时漂移对账")

    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    try:
        if args.cmd == "peripheral":
            svd_dev = _resolve_svd_device(args.svd)
            with _make_probe(args, svd_dev=svd_dev, seed_gpio=False) as probe:
                report = diagnose_peripheral(
                    probe,
                    svd_dev,
                    args.peripheral_name,
                    include_gpio_mux=not args.no_gpio,
                )
            if args.json:
                print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
            else:
                print(report.to_text())
            return 0

        if args.cmd == "pins":
            svd_dev = _resolve_svd_device(args.svd)
            with _make_probe(args, svd_dev=svd_dev, seed_gpio=True) as probe:
                pins = scan_configured_gpio_pins(probe, svd_dev)
            if args.json:
                print(
                    json.dumps(
                        {
                            "device": svd_dev.name,
                            "count": len(pins),
                            "pins": [p.to_dict() for p in pins],
                        },
                        indent=2,
                        ensure_ascii=False,
                    )
                )
            else:
                print(f"[{svd_dev.name}] Configured GPIO pins ({len(pins)}):")
                for p in pins:
                    print(f"  - {p.summary}")
            return 0

        if args.cmd == "board":
            want_live = bool(args.live or args.mock)
            if want_live:
                svd_dev = _resolve_svd_device(args.svd, project_dir=args.project_dir)
                with _make_probe(args, svd_dev=svd_dev, seed_gpio=True) as probe:
                    report = inspect_board_context(
                        args.project_dir,
                        ioc_path=args.ioc,
                        manifest_path=args.manifest,
                        probe=probe,
                        svd_device=svd_dev,
                    )
            else:
                report = inspect_board_context(
                    args.project_dir,
                    ioc_path=args.ioc,
                    manifest_path=args.manifest,
                )

            if args.json:
                print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
            else:
                print(report.to_text())
            return 0

    except (ProbeError, ConfigError, FileNotFoundError, KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
