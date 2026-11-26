"""基于 DWARF 的无停机 C 结构体/枚举/数组实时变量观测命令行工具（mcu-watch）。

用法示例：
    # 列出固件 ELF 中的所有全局/静态变量（支持关键字或通配符过滤）
    python -m mcu_harness.dwarf.cli list-vars --elf build/app.elf --filter g_ctrl

    # 离线模拟模式：自动将 ELF 已初始化数据节装入 MockProbe 并解码完整结构体
    python -m mcu_harness.dwarf.cli read g_ctrl --elf build/app.elf --mock
    python -m mcu_harness.dwarf.cli read g_ctrl.pid.kp --elf build/app.elf --mock --json

    # 实机连板高频趋势采样（全程不暂停 CPU）
    python -m mcu_harness.dwarf.cli sample g_ctrl.pid.kp --elf build/app.elf -n 20 --interval-ms 10
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from mcu_harness.elf.symbols import ElfError
from mcu_harness.probe import DebugProbe, JLinkProbe, MockProbe, OpenOCDProbe, ProbeError

from .live_watch import LiveWatchError, read_variable, read_variables, sample_variable
from .parser import DwarfError, extract_elf_initialized_memory, load_dwarf_index


def _add_elf_arg(ap: argparse.ArgumentParser) -> None:
    ap.add_argument(
        "--elf",
        default=os.environ.get("MCU_HARNESS_ELF_PATH"),
        required=not bool(os.environ.get("MCU_HARNESS_ELF_PATH")),
        help="固件 .elf 文件路径（或通过环境变量 MCU_HARNESS_ELF_PATH 指定）",
    )
    ap.add_argument("--json", action="store_true", help="输出结构化 JSON 结果")


def _add_probe_args(ap: argparse.ArgumentParser) -> None:
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


def _cmd_list_vars(args: argparse.Namespace) -> int:
    index = load_dwarf_index(args.elf)
    items = index.list_variables(args.filter)
    if args.json:
        payload = {
            "elf_path": index.elf_path,
            "dwarf_versions": list(index.dwarf_versions),
            "degraded_symtab_only": index.degraded_symtab_only,
            "count": len(items),
            "variables": [v.to_dict() for v in items],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    mode_str = (
        "symtab-fallback (no DWARF)"
        if index.degraded_symtab_only
        else f"DWARF v{'/v'.join(str(v) for v in index.dwarf_versions)}"
    )
    print(f"ELF: {index.elf_path} [{mode_str}] ({len(items)} variables)")
    for v in items:
        print(f"  {v.address_hex}  ({v.byte_size:4d} B)  {v.name:<28s} : {v.type_name} [{v.source}]")
    return 0


def _cmd_read(args: argparse.Namespace) -> int:
    with _make_probe(args) as probe:
        if len(args.expr) == 1:
            val = read_variable(
                probe,
                args.elf,
                args.expr[0],
                deref=args.deref,
                max_depth=args.max_depth,
            )
            if args.json:
                print(json.dumps(val.to_dict(), indent=2, ensure_ascii=False))
            else:
                print(val.to_text())
        else:
            batch = read_variables(
                probe,
                args.elf,
                args.expr,
                deref=args.deref,
                max_depth=args.max_depth,
            )
            if args.json:
                print(json.dumps({k: v.to_dict() for k, v in batch.items()}, indent=2, ensure_ascii=False))
            else:
                print("\n".join(v.to_text() for v in batch.values()))
    return 0


def _cmd_sample(args: argparse.Namespace) -> int:
    with _make_probe(args) as probe:
        report = sample_variable(
            probe,
            args.elf,
            args.expr,
            samples=args.samples,
            interval_ms=args.interval_ms,
        )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(report.to_text())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mcu-watch",
        description="Non-intrusive DWARF (v2-v5) Live Watch & high-frequency variable trend sampler",
    )
    sub = ap.add_subparsers(dest="subcmd", required=True)

    p_list = sub.add_parser("list-vars", help="列出 ELF 中的全局/静态变量及 C 类型")
    _add_elf_arg(p_list)
    p_list.add_argument("--filter", help="按变量名或类型名过滤（支持子串或通配符 *）")
    p_list.set_defaults(func=_cmd_list_vars)

    p_read = sub.add_parser("read", help="无停机读取 C 变量/结构体/数组/位域/枚举表达式")
    p_read.add_argument("expr", nargs="+", help="变量路径表达式，如 g_sys 或 g_sys.sensors[2].raw_mv")
    _add_elf_arg(p_read)
    _add_probe_args(p_read)
    p_read.add_argument("--deref", action="store_true", help="对非空指针解引用一层目标类型")
    p_read.add_argument("--max-depth", type=int, default=4, help="结构体/数组最大递归展开深度（默认 4）")
    p_read.set_defaults(func=_cmd_read)

    p_sample = sub.add_parser("sample", help="对数值/枚举/布尔标量字段执行高频趋势采样")
    p_sample.add_argument("expr", help="标量变量路径表达式，如 g_sys.rpm 或 g_sys.state")
    _add_elf_arg(p_sample)
    _add_probe_args(p_sample)
    p_sample.add_argument("-n", "--samples", type=int, default=20, help="采样次数（1..500，默认 20）")
    p_sample.add_argument("--interval-ms", type=float, default=10.0, help="采样间隔毫秒（默认 10.0）")
    p_sample.set_defaults(func=_cmd_sample)

    args = ap.parse_args(argv)
    try:
        return int(args.func(args))
    except (ProbeError, DwarfError, ElfError, LiveWatchError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
