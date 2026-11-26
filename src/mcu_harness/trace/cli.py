"""Cortex-M DWT 非侵入性能剖析与硬件数据观察点命令行工具（mcu-trace）。

用法示例：
    # 离线模拟：周期测速 / SYSCLK 估算
    python -m mcu_harness.trace.cli cycles --mock --core-hz 250000000
    python -m mcu_harness.trace.cli cycles --mock --json

    # 离线模拟：PC 采样热点图（可配合 --elf 还原函数名与源码行号）
    python -m mcu_harness.trace.cli profile --mock --samples 50 --interval-ms 0
    python -m mcu_harness.trace.cli profile --mock --elf build/app.elf --json

    # 硬件数据观察点：按地址或 C 变量名挂起、查看、清除
    python -m mcu_harness.trace.cli watch-set 0x20000100 --access write --size 4 --mock
    python -m mcu_harness.trace.cli watch-set g_state.counter --elf build/app.elf --mock
    python -m mcu_harness.trace.cli watch-list --mock --json
    python -m mcu_harness.trace.cli watch-clear --mock --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from mcu_harness.dwarf.live_watch import LiveWatchError
from mcu_harness.dwarf.parser import DwarfError
from mcu_harness.elf.symbols import ElfError, read_elf_symbols
from mcu_harness.probe import DebugProbe, JLinkProbe, MockProbe, OpenOCDProbe, ProbeError

from .dwt import (
    DCB_DEMCR,
    DCB_DEMCR_TRCENA,
    DWT_CTRL,
    DWT_CTRL_CYCCNTENA,
    DWT_CYCCNT,
    DWT_PCSR,
    TraceError,
    clear_watchpoints,
    list_watchpoints,
    measure_cycles,
    profile_pc_hotspots,
    set_watchpoint,
)


class _CliMockTraceProbe(MockProbe):
    """CLI `--mock` 演示探针：预置 NUMCOMP=4，并动态模拟 DWT_CYCCNT 推进与 DWT_PCSR 采样序列。"""

    def __init__(
        self,
        *,
        device: str = "STM32H563ZI",
        interface: str = "SWD",
        sim_core_hz: int = 250_000_000,
        pcsr_sequence: list[int] | None = None,
    ) -> None:
        super().__init__(
            device=device,
            interface=interface,
            memory_words={DWT_CTRL: 4 << 28},
        )
        self._sim_core_hz = sim_core_hz
        self._t_origin = time.monotonic()
        self._pcsr_sequence = list(pcsr_sequence or [0x08000400, 0x08000400, 0x08000420, 0xFFFFFFFF])
        self._pcsr_idx = 0

    def read_u32(self, address: int, count: int = 1) -> list[int]:
        if count == 1 and address == DWT_CYCCNT:
            self._require_conn()
            demcr = super().read_u32(DCB_DEMCR, 1)[0]
            ctrl = super().read_u32(DWT_CTRL, 1)[0]
            if (demcr & DCB_DEMCR_TRCENA) and (ctrl & DWT_CTRL_CYCCNTENA):
                elapsed = max(0.0, time.monotonic() - self._t_origin)
                return [int(elapsed * self._sim_core_hz) & 0xFFFFFFFF]
        if count == 1 and address == DWT_PCSR:
            self._require_conn()
            val = self._pcsr_sequence[self._pcsr_idx % len(self._pcsr_sequence)]
            self._pcsr_idx += 1
            return [val & 0xFFFFFFFF]
        return super().read_u32(address, count)


def _add_common_probe_args(ap: argparse.ArgumentParser, *, suppress_defaults: bool = False) -> None:
    d_backend = argparse.SUPPRESS if suppress_defaults else os.environ.get("MCU_HARNESS_BACKEND", "jlink")
    d_device = argparse.SUPPRESS if suppress_defaults else os.environ.get("MCU_HARNESS_DEVICE", "STM32H563ZI")
    d_iface = argparse.SUPPRESS if suppress_defaults else "SWD"
    d_speed = argparse.SUPPRESS if suppress_defaults else "auto"
    d_none = argparse.SUPPRESS if suppress_defaults else None
    d_oc_if = argparse.SUPPRESS if suppress_defaults else "cmsis-dap"
    d_false = argparse.SUPPRESS if suppress_defaults else False

    ap.add_argument(
        "--backend",
        default=d_backend,
        choices=["jlink", "openocd", "mock"],
        help="调试探针后端：jlink / openocd / mock（默认 jlink）",
    )
    ap.add_argument("--device", default=d_device, help="目标芯片型号，默认 STM32H563ZI")
    ap.add_argument("--interface", default=d_iface, choices=["SWD", "JTAG"], help="调试接口，默认 SWD")
    ap.add_argument("--speed", default=d_speed, help="SWD/JTAG 速率（kHz 或 auto）")
    ap.add_argument("--serial", default=d_none, help="探针序列号")
    ap.add_argument("--jlink-lib", default=d_none, help="显式指定 libjlinkarm.so 路径")
    ap.add_argument("--openocd-if", default=d_oc_if, help="OpenOCD 调试器脚本名，如 cmsis-dap / stlink")
    ap.add_argument("--openocd-target", default=d_none, help="OpenOCD 目标芯片脚本名，如 stm32h5x / stm32f4x")
    ap.add_argument(
        "--elf",
        default=argparse.SUPPRESS if suppress_defaults else os.environ.get("MCU_HARNESS_ELF_PATH"),
        help="固件 .elf 文件路径（用于 DWARF 源码定位与变量观察点算址）",
    )
    ap.add_argument("--mock", action="store_true", default=d_false, help="使用离线模拟探针")
    ap.add_argument("--json", action="store_true", default=d_false, help="以 JSON 格式输出结构化结果")


def _build_mock_pcsr_sequence(elf_path: str | None) -> list[int] | None:
    if not elf_path:
        return None
    try:
        syms = [
            s for s in read_elf_symbols(elf_path)
            if s.sym_type == "func" and s.shndx != 0 and 0 < s.address <= 0xFFFFFFFF and not s.name.startswith("$")
        ]
        if syms:
            addrs = [s.address for s in syms[:3]]
            return [addrs[0], addrs[0], addrs[-1], 0xFFFFFFFF]
    except (OSError, ElfError, ValueError):
        pass
    return None


def _make_probe(args: argparse.Namespace) -> DebugProbe:
    speed = int(args.speed) if str(args.speed).isdigit() else args.speed
    if args.mock or args.backend == "mock":
        sim_hz = getattr(args, "core_hz", None) or 250_000_000
        pcsr_seq = _build_mock_pcsr_sequence(getattr(args, "elf", None))
        return _CliMockTraceProbe(
            device=args.device,
            interface=args.interface,
            sim_core_hz=sim_hz,
            pcsr_sequence=pcsr_seq,
        )
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


def _cmd_cycles(args: argparse.Namespace) -> int:
    with _make_probe(args) as probe:
        report = measure_cycles(probe, duration_ms=args.duration_ms, core_hz=args.core_hz)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(report.to_text())
    return 0


def _cmd_profile(args: argparse.Namespace) -> int:
    with _make_probe(args) as probe:
        report = profile_pc_hotspots(
            probe,
            elf_path=args.elf,
            project_dir=args.project_dir,
            samples=args.samples,
            interval_ms=args.interval_ms,
        )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(report.to_text())
    return 0


def _cmd_watch_set(args: argparse.Namespace) -> int:
    with _make_probe(args) as probe:
        info = set_watchpoint(
            probe,
            args.target,
            access=args.access,
            size_bytes=args.size,
            slot=args.slot,
            elf_path=args.elf,
        )
    if args.json:
        print(json.dumps(info.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(info.to_text())
    return 0


def _cmd_watch_list(args: argparse.Namespace) -> int:
    with _make_probe(args) as probe:
        report = list_watchpoints(probe)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(report.to_text())
    return 0


def _cmd_watch_clear(args: argparse.Namespace) -> int:
    with _make_probe(args) as probe:
        res = clear_watchpoints(probe, slot=args.slot)
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print(
            f"Cleared DWT watchpoint slot(s) {res['cleared_slots']} "
            f"(NUMCOMP={res['num_comparators']}, previously_active={res['previously_active_slots']})"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mcu-trace",
        description="Non-intrusive Cortex-M DWT profiling (cycles / PC hotspots) and hardware data watchpoints",
    )
    _add_common_probe_args(ap, suppress_defaults=False)
    sub = ap.add_subparsers(dest="subcmd", required=True)

    p_cycles = sub.add_parser("cycles", help="通过 DWT_CYCCNT 测量周期数并估算 SYSCLK 主频")
    _add_common_probe_args(p_cycles, suppress_defaults=True)
    p_cycles.add_argument("--duration-ms", type=float, default=50.0, help="采样窗口毫秒数（默认 50.0）")
    p_cycles.add_argument("--core-hz", type=int, default=None, help="期望内核主频（Hz），用于计算误差百分比")
    p_cycles.set_defaults(func=_cmd_cycles)

    p_profile = sub.add_parser("profile", help="连续无停机采样 DWT_PCSR 生成 PC/函数热点排行")
    _add_common_probe_args(p_profile, suppress_defaults=True)
    p_profile.add_argument("--project-dir", default=None, help="固件工程根目录（用于归一化相对源码路径）")
    p_profile.add_argument("-n", "--samples", type=int, default=100, help="采样次数（1..1000，默认 100）")
    p_profile.add_argument("--interval-ms", type=float, default=1.0, help="采样间隔毫秒（默认 1.0）")
    p_profile.set_defaults(func=_cmd_profile)

    p_wset = sub.add_parser("watch-set", help="设置 DWT 硬件数据观察点（支持地址或 C 变量/成员表达式）")
    _add_common_probe_args(p_wset, suppress_defaults=True)
    p_wset.add_argument("target", help="目标地址（如 0x20000100）或 C 变量表达式（如 g_state.counter）")
    p_wset.add_argument("--access", choices=["write", "read", "rw"], default="write", help="监视访问类型（默认 write）")
    p_wset.add_argument("--size", type=int, choices=[1, 2, 4], default=None, help="监视字节宽度（1/2/4；变量表达式默认自动推断）")
    p_wset.add_argument("--slot", type=int, default=None, help="显式指定比较器槽位索引（默认自动选择空闲槽位）")
    p_wset.set_defaults(func=_cmd_watch_set)

    p_wlist = sub.add_parser("watch-list", help="查看当前所有 DWT 数据观察点状态与命中标志")
    _add_common_probe_args(p_wlist, suppress_defaults=True)
    p_wlist.set_defaults(func=_cmd_watch_list)

    p_wclear = sub.add_parser("watch-clear", help="清除指定或全部 DWT 数据观察点")
    _add_common_probe_args(p_wclear, suppress_defaults=True)
    p_wclear.add_argument("--slot", type=int, default=None, help="指定清除的槽位索引（省略则清除全部）")
    p_wclear.set_defaults(func=_cmd_watch_clear)

    args = ap.parse_args(argv)
    try:
        return int(args.func(args))
    except (TraceError, ProbeError, ElfError, DwarfError, LiveWatchError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
