"""探针命令行工具 —— 用于直接验证 J-Link 连板读芯片 ID、读内存与现场解码外设寄存器。

用法示例：
    # 查看当前已连接 J-Link 与目标芯片信息（STM32H563ZI）
    python -m mcu_harness.probe.cli info

    # 读取 Cortex-M33 CPUID (0xE000ED00)
    python -m mcu_harness.probe.cli read 0xE000ED00 --count 4

    # 结合 SVD 现场读取并解码外设寄存器
    python -m mcu_harness.probe.cli inspect --svd STM32H563.svd GPIOA MODER

    # 无板卡离线自测（--mock）
    python -m mcu_harness.probe.cli --mock inspect --svd tests/fixtures/stm32h563_mini.svd GPIOA MODER
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from mcu_harness.svd import load_svd

from .base import DebugProbe, ProbeError
from .jlink import JLinkProbe
from .mock import MockProbe
from .peripheral import inspect_peripheral_registers


def _make_probe(args: argparse.Namespace) -> DebugProbe:
    if args.mock:
        return MockProbe(device=args.device, interface=args.interface)
    return JLinkProbe(
        device=args.device,
        interface=args.interface,
        speed=args.speed,
        serial_no=args.serial,
        lib_path=args.jlink_lib,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mcu-probe", description="J-Link hardware probe & peripheral inspector")
    ap.add_argument("--device", default=os.environ.get("MCU_HARNESS_DEVICE", "STM32H563ZI"), help="目标芯片型号")
    ap.add_argument("--interface", default="SWD", choices=["SWD", "JTAG"], help="调试接口，默认 SWD")
    ap.add_argument("--speed", default="auto", help="SWD/JTAG 速率（kHz 或 auto）")
    ap.add_argument("--serial", help="J-Link 序列号（多探针时指定）")
    ap.add_argument("--jlink-lib", help="显式指定 libjlinkarm.so 路径")
    ap.add_argument("--mock", action="store_true", help="使用内存模拟探针（无硬件测试）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 格式")

    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="连接并打印探针与目标芯片信息")

    p_read = sub.add_parser("read", help="读取指定地址内存")
    p_read.add_argument("address", help="十六进制或十进制地址（如 0xE000ED00）")
    p_read.add_argument("-n", "--count", type=int, default=1, help="读取个数，默认 1")
    p_read.add_argument("-w", "--width", type=int, choices=[8, 16, 32], default=32, help="位宽，默认 32")

    p_insp = sub.add_parser("inspect", help="通过 SVD 现场读取并解码外设寄存器")
    p_insp.add_argument("--svd", default=os.environ.get("MCU_HARNESS_SVD_PATH"), help=".svd 文件路径")
    p_insp.add_argument("peripheral", help="外设名（如 GPIOA / RCC）")
    p_insp.add_argument("registers", nargs="*", help="一个或多个寄存器名（为空且加 --all 时读取全部）")
    p_insp.add_argument("--all", dest="read_all", action="store_true", help="读取该外设全部非 write-only 寄存器")

    args = ap.parse_args(argv)
    speed_val: int | str = int(args.speed) if str(args.speed).isdigit() else args.speed
    args.speed = speed_val

    try:
        with _make_probe(args) as probe:
            if args.cmd == "info":
                info = probe.info()
                if args.json:
                    print(json.dumps(info.to_dict(), indent=2, ensure_ascii=False))
                else:
                    print(
                        f"[{info.backend}] connected={info.connected} device={info.device} "
                        f"if={info.interface} sn={info.serial_no} core={info.core_name} "
                        f"core_id={info.core_id} halted={info.halted} speed={info.speed_khz}kHz"
                    )
                return 0

            if args.cmd == "read":
                addr = int(args.address, 0)
                if args.width == 32:
                    vals = probe.read_u32(addr, args.count)
                    fmt = lambda v: f"0x{v:08X}"
                elif args.width == 16:
                    vals = probe.read_u16(addr, args.count)
                    fmt = lambda v: f"0x{v:04X}"
                else:
                    vals = probe.read_u8(addr, args.count)
                    fmt = lambda v: f"0x{v:02X}"

                if args.json:
                    print(
                        json.dumps(
                            {
                                "address": f"0x{addr:08X}",
                                "width": args.width,
                                "values": [fmt(v) for v in vals],
                                "raw": vals,
                            },
                            indent=2,
                            ensure_ascii=False,
                        )
                    )
                else:
                    step = args.width // 8
                    for i, v in enumerate(vals):
                        print(f"0x{addr + i * step:08X}: {fmt(v)} ({v})")
                return 0

            if args.cmd == "inspect":
                if not args.svd:
                    ap.error("inspect requires --svd (or MCU_HARNESS_SVD_PATH)")
                dev = load_svd(args.svd)
                if len(args.registers) == 1 and not args.read_all:
                    res = inspect_peripheral_registers(probe, dev, args.peripheral, register_name=args.registers[0])
                elif args.registers:
                    res = inspect_peripheral_registers(probe, dev, args.peripheral, registers=args.registers)
                elif args.read_all:
                    res = inspect_peripheral_registers(probe, dev, args.peripheral, read_all=True)
                else:
                    ap.error("specify at least one register name or pass --all")

                if args.json:
                    print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
                else:
                    print(res.to_text())
                return 0
    except (ProbeError, FileNotFoundError, KeyError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
