"""SVD 模块命令行工具 —— 不连接硬件即可查询外设寄存器定义、或解码十六进制数值。

用法示例：
    python -m mcu_harness.svd.cli --svd STM32H563.svd --list
    python -m mcu_harness.svd.cli --svd STM32H563.svd GPIOA
    python -m mcu_harness.svd.cli --svd STM32H563.svd GPIOA MODER
    python -m mcu_harness.svd.cli --svd STM32H563.svd GPIOA MODER 0xAB000400
"""

from __future__ import annotations

import argparse
import json
import sys

from .decoder import decode_register, find_peripheral, find_register
from .parser import load_svd


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mcu-svd", description="Inspect CMSIS-SVD peripherals and decode register values")
    ap.add_argument("--svd", required=True, help=".svd 或 .svd.gz 文件路径")
    ap.add_argument("peripheral", nargs="?", help="外设名称（如 GPIOA / RCC / USART1）")
    ap.add_argument("register", nargs="?", help="寄存器名称（如 MODER / CR / ISR）")
    ap.add_argument("value", nargs="?", help="要解码的寄存器值（如 0x400 或 1024）")
    ap.add_argument("--list", action="store_true", help="列出所有外设及基地址")
    ap.add_argument("--filter", help="按名称子串过滤外设或寄存器")
    ap.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = ap.parse_args(argv)

    try:
        dev = load_svd(args.svd)
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # 1. 列出外设
    if args.list or not args.peripheral:
        flt = (args.filter or "").upper()
        periphs = [p for p in dev.peripherals if flt in p.name.upper()]
        if args.json:
            data = {
                "device": dev.name,
                "peripherals": [
                    {"name": p.name, "base_address": f"0x{p.base_address:08X}", "description": p.description}
                    for p in periphs
                ],
            }
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print(f"[{dev.name}] Peripherals ({len(periphs)}):")
            for p in periphs:
                print(f"  {p.name:<16} 0x{p.base_address:08X}  {p.description}")
        return 0

    try:
        periph = find_peripheral(dev, args.peripheral)
    except KeyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # 2. 列出外设下所有寄存器
    if not args.register:
        flt = (args.filter or "").upper()
        regs = [r for r in periph.registers if flt in r.name.upper()]
        if args.json:
            data = {
                "peripheral": periph.name,
                "base_address": f"0x{periph.base_address:08X}",
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
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print(f"[{periph.name}] @ 0x{periph.base_address:08X}  ({len(regs)} registers)")
            for r in regs:
                rst = f"reset=0x{r.reset_value:08X}" if r.reset_value is not None else ""
                print(f"  +0x{r.address_offset:04X} (0x{periph.reg_address(r):08X})  {r.name:<18} {rst:<18} {r.description}")
        return 0

    try:
        reg = find_register(periph, args.register)
    except KeyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    # 3. 查看定义或解码指定值（未传 value 时用 reset_value 或 0 展示位域定义）
    raw_val = args.value if args.value is not None else (reg.reset_value if reg.reset_value is not None else 0)
    try:
        decoded = decode_register(periph, reg, raw_val)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(decoded.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(decoded.to_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
