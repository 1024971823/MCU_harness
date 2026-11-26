"""HIL 自动化测试与逻辑分析仪命令行工具（mcu-hil）。

用法示例：
    python -m mcu_harness.hil.cli run suite.toml --elf build/app.elf --svd board.svd --mock --json
    python -m mcu_harness.hil.cli logic --csv pwm_capture.csv --json
    python -m mcu_harness.hil.cli logic --driver fx2lafw --channels D0,D1 --samples 4096 --protocol i2c
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from mcu_harness.dwarf import DwarfError, extract_elf_initialized_memory
from mcu_harness.probe import JLinkProbe, MockProbe, OpenOCDProbe, ProbeError

from .logic_analyzer import HilError, decode_with_sigrok
from .runner import HilConfigError, run_hil_suite


def _build_probe(args: argparse.Namespace):
    speed = int(args.speed) if str(args.speed).isdigit() else args.speed
    if args.mock or args.backend == "mock":
        mock = MockProbe(device=args.device, interface=args.interface)
        if args.elf:
            for seg_addr, seg_bytes in extract_elf_initialized_memory(args.elf).items():
                for off, b in enumerate(seg_bytes):
                    mock._mem[seg_addr + off] = b
        return mock
    if args.backend == "openocd":
        return OpenOCDProbe(
            device=args.device,
            interface=args.interface,
            speed=speed,
            serial_no=args.serial,
        )
    return JLinkProbe(
        device=args.device,
        interface=args.interface,
        speed=speed,
        serial_no=args.serial,
    )


def _cmd_run(args: argparse.Namespace) -> int:
    probe = _build_probe(args)
    try:
        report = run_hil_suite(
            args.suite,
            probe=probe,
            project_dir=args.project_dir,
            elf_path=args.elf,
            svd_path=args.svd,
            confirmed=args.confirm,
        )
    finally:
        try:
            probe.close()
        except Exception:
            pass

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(report.to_text())
    return 0 if report.passed else 1


def _cmd_logic(args: argparse.Namespace) -> int:
    report = decode_with_sigrok(
        csv_path=args.csv,
        input_file=args.input,
        driver=args.driver,
        channels=args.channels,
        samplerate=args.samplerate,
        samples=args.samples,
        duration_ms=args.duration_ms,
        protocol=args.protocol,
        protocol_options=args.protocol_options,
        sigrok_bin=args.sigrok_bin,
        timeout_s=args.timeout,
        time_unit_s=args.time_unit_s,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(report.to_text())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mcu-hil",
        description="Declarative HIL test engine & logic analyzer / sigrok-cli bridge",
    )
    sub = ap.add_subparsers(dest="subcmd", required=True)

    p_run = sub.add_parser("run", help="执行声明式 HIL 测试套件 (.toml / .json / .yaml)")
    p_run.add_argument("suite", help="HIL 测试套件文件路径 (.toml / .json)")
    p_run.add_argument("--project-dir", help="固件工程目录（用于自动探测路径与安全策略）")
    p_run.add_argument("--elf", default=os.environ.get("MCU_HARNESS_ELF_PATH"), help="固件 .elf 文件路径")
    p_run.add_argument("--svd", default=os.environ.get("MCU_HARNESS_SVD_PATH"), help="芯片 .svd 文件路径")
    p_run.add_argument(
        "--backend",
        default=os.environ.get("MCU_HARNESS_BACKEND", "jlink"),
        choices=["jlink", "openocd", "mock"],
        help="调试探针后端（默认 jlink）",
    )
    p_run.add_argument("--device", default=os.environ.get("MCU_HARNESS_DEVICE", "STM32H563ZI"), help="目标芯片型号")
    p_run.add_argument("--interface", default="SWD", choices=["SWD", "JTAG"], help="调试接口")
    p_run.add_argument("--speed", default="auto", help="SWD/JTAG 速率")
    p_run.add_argument("--serial", help="探针序列号")
    p_run.add_argument("--mock", action="store_true", help="使用离线模拟探针运行 HIL 套件")
    p_run.add_argument("--confirm", action="store_true", help="人工批准高风险外设/Flash 写操作（不可绕过 Option Bytes）")
    p_run.add_argument("--json", action="store_true", help="输出结构化 JSON 报告")
    p_run.set_defaults(func=_cmd_run)

    p_logic = sub.add_parser("logic", help="数字波形 CSV 分析与 sigrok-cli 协议解码")
    p_logic.add_argument("--csv", help="数字波形 CSV 文件路径（纯 Python 零外部依赖）")
    p_logic.add_argument("--input", help="sigrok 采集文件 (.sr 或 .csv)")
    p_logic.add_argument("--driver", help="sigrok 硬件驱动名，如 fx2lafw")
    p_logic.add_argument("--channels", help="采样通道列表，如 D0,D1")
    p_logic.add_argument("--samplerate", default="4m", help="采样率（默认 4m）")
    p_logic.add_argument("--samples", type=int, help="采样点数")
    p_logic.add_argument("--duration-ms", type=float, help="采样时长（毫秒）")
    p_logic.add_argument("--time-unit-s", type=float, default=1.0, help="CSV 首列时间单位换算为秒的倍率（默认 1.0）")
    p_logic.add_argument("--protocol", help="协议解码器（如 spi / i2c / uart / pwm / can）")
    p_logic.add_argument("--protocol-options", help="协议解码器参数，如 clk=D0:mosi=D1")
    p_logic.add_argument("--sigrok-bin", help="显式指定 sigrok-cli 可执行文件路径")
    p_logic.add_argument("--timeout", type=float, default=15.0, help="sigrok-cli 超时秒数（默认 15.0）")
    p_logic.add_argument("--json", action="store_true", help="输出结构化 JSON 报告")
    p_logic.set_defaults(func=_cmd_logic)

    args = ap.parse_args(argv)
    try:
        return int(args.func(args))
    except (HilConfigError, HilError, ProbeError, DwarfError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, (HilConfigError, FileNotFoundError, ValueError)) else 1


if __name__ == "__main__":
    sys.exit(main())
