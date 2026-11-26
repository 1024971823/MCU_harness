"""探针命令行工具 —— 支持 J-Link / OpenOCD / Mock 连板信息、读写内存、核心寄存器、烧录复位与 SVD 解码。

用法示例：
    python -m mcu_harness.probe.cli info
    python -m mcu_harness.probe.cli --backend openocd --openocd-if stlink --device STM32F407VG info
    python -m mcu_harness.probe.cli read 0xE000ED00 --count 4
    python -m mcu_harness.probe.cli write 0x20000000 0x12345678 --confirm
    python -m mcu_harness.probe.cli regs
    python -m mcu_harness.probe.cli flash build/app.elf --confirm
    python -m mcu_harness.probe.cli --mock inspect --svd tests/fixtures/stm32h563_mini.svd GPIOA MODER
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from mcu_harness.safety import default_safety_guard
from mcu_harness.svd import load_svd

from .base import DebugProbe, ProbeError
from .jlink import JLinkProbe
from .mock import MockProbe
from .openocd import OpenOCDProbe
from .peripheral import inspect_peripheral_registers


def _make_probe(args: argparse.Namespace) -> DebugProbe:
    if args.mock or args.backend == "mock":
        return MockProbe(device=args.device, interface=args.interface)
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
    ap = argparse.ArgumentParser(prog="mcu-probe", description="Hardware debug probe CLI (J-Link / OpenOCD / Mock)")
    ap.add_argument("--backend", default=os.environ.get("MCU_HARNESS_BACKEND", "jlink"), choices=["jlink", "openocd", "mock"])
    ap.add_argument("--device", default=os.environ.get("MCU_HARNESS_DEVICE", "STM32H563ZI"), help="目标芯片型号")
    ap.add_argument("--interface", default="SWD", choices=["SWD", "JTAG"], help="调试接口，默认 SWD")
    ap.add_argument("--speed", default="auto", help="SWD/JTAG 速率（kHz 或 auto）")
    ap.add_argument("--serial", help="探针序列号（多探针时指定）")
    ap.add_argument("--jlink-lib", help="显式指定 libjlinkarm.so 路径")
    ap.add_argument("--openocd-if", default="cmsis-dap", help="OpenOCD 调试器脚本名，如 cmsis-dap / stlink / jlink")
    ap.add_argument("--openocd-target", help="OpenOCD 目标芯片脚本名，如 stm32h5x / stm32f4x（默认按型号推导）")
    ap.add_argument("--mock", action="store_true", help="使用内存模拟探针（等同 --backend mock）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 格式")

    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="连接并打印探针与目标芯片信息")

    p_read = sub.add_parser("read", help="读取指定地址内存")
    p_read.add_argument("address", help="十六进制或十进制地址（如 0xE000ED00）")
    p_read.add_argument("-n", "--count", type=int, default=1, help="读取个数，默认 1")
    p_read.add_argument("-w", "--width", type=int, choices=[8, 16, 32], default=32, help="位宽，默认 32")

    p_write = sub.add_parser("write", help="向指定地址写入数值（受安全护栏保护）")
    p_write.add_argument("address", help="目标地址")
    p_write.add_argument("values", nargs="+", help="一个或多个整数值（如 0x12345678）")
    p_write.add_argument("-w", "--width", type=int, choices=[8, 16, 32], default=32, help="位宽，默认 32")
    p_write.add_argument("--confirm", action="store_true", help="确认执行外设/高风险寄存器写入")

    p_regs = sub.add_parser("regs", help="读取核心寄存器（必要时临时暂停再恢复）")
    p_regs.add_argument("names", nargs="*", help="指定寄存器名（如 pc lr sp），不传则读全部常用寄存器")

    p_rst = sub.add_parser("reset", help="复位目标芯片")
    p_rst.add_argument("--halt", action="store_true", help="复位后停留在入口点")

    p_flash = sub.add_parser("flash", help="烧录固件（受 HITL 安全确认保护）")
    p_flash.add_argument("firmware", help=".elf / .hex / .bin 固件路径")
    p_flash.add_argument("--address", help=".bin 文件加载起始地址，如 0x08000000")
    p_flash.add_argument("--halt", action="store_true", help="烧录复位后保持暂停")
    p_flash.add_argument("--confirm", action="store_true", help="确认执行 Flash 擦除烧录")

    p_insp = sub.add_parser("inspect", help="通过 SVD 现场读取并解码外设寄存器")
    p_insp.add_argument("--svd", default=os.environ.get("MCU_HARNESS_SVD_PATH"), help=".svd 文件路径")
    p_insp.add_argument("peripheral", help="外设名（如 GPIOA / RCC）")
    p_insp.add_argument("registers", nargs="*", help="一个或多个寄存器名（为空且加 --all 时读取全部）")
    p_insp.add_argument("--all", dest="read_all", action="store_true", help="读取该外设全部非 write-only 寄存器")

    args = ap.parse_args(argv)
    args.speed = int(args.speed) if str(args.speed).isdigit() else args.speed

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
                    print(json.dumps({"address": f"0x{addr:08X}", "width": args.width, "values": [fmt(v) for v in vals], "raw": vals}, indent=2))
                else:
                    step = args.width // 8
                    for i, v in enumerate(vals):
                        print(f"0x{addr + i * step:08X}: {fmt(v)} ({v})")
                return 0

            if args.cmd == "write":
                addr = int(args.address, 0)
                vals = [int(v, 0) for v in args.values]
                dec = default_safety_guard.check_memory_write(
                    addr, len(vals) * (args.width // 8), device=args.device, confirmed=args.confirm
                )
                if not dec.allowed:
                    print(f"error: {dec.reason}", file=sys.stderr)
                    return 2
                if args.width == 32:
                    probe.write_u32(addr, vals)
                elif args.width == 16:
                    probe.write_u16(addr, vals)
                else:
                    probe.write_u8(addr, vals)
                print(json.dumps({"written": len(vals), "address": f"0x{addr:08X}"}, indent=2) if args.json else f"Wrote {len(vals)}x{args.width}-bit at 0x{addr:08X}")
                return 0

            if args.cmd == "regs":
                halted_temporarily = probe.halt()
                try:
                    regs = probe.read_core_regs(args.names or None)
                finally:
                    if halted_temporarily:
                        probe.resume()
                hex_regs = {k: f"0x{v:08X}" for k, v in regs.items()}
                if args.json:
                    print(json.dumps(hex_regs, indent=2))
                else:
                    for k, hv in hex_regs.items():
                        print(f"  {k:<10} = {hv}")
                return 0

            if args.cmd == "reset":
                res = probe.reset(halt=args.halt)
                print(json.dumps(res.to_dict(), indent=2) if args.json else res.message)
                return 0

            if args.cmd == "flash":
                dec = default_safety_guard.check_flash(args.firmware, device=args.device, confirmed=args.confirm)
                if not dec.allowed:
                    print(f"error: {dec.reason}", file=sys.stderr)
                    return 2
                f_addr = int(args.address, 0) if args.address else None
                res = probe.flash(args.firmware, address=f_addr, run_after=not args.halt)
                print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False) if args.json else f"{res.message}: {res.firmware_path} ({res.bytes_written} B)")
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

                print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False) if args.json else res.to_text())
                return 0
    except (ProbeError, FileNotFoundError, KeyError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
