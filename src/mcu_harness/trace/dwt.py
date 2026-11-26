"""Cortex-M DWT 非侵入性能剖析与硬件数据观察点（Data Watchpoint）模块。

核心特性：
    * 全程严格非侵入：禁止调用 `probe.halt()` / `probe.resume()`，仅通过 CoreSight Debug AP 访问 DWT/DCB 寄存器。
    * 周期测速与 SYSCLK 估算：开启 `DCB_DEMCR.TRCENA` 与 `DWT_CTRL.CYCCNTENA`，安全处理 32 位 `DWT_CYCCNT` 回绕，
      结合采样窗口评估时钟频率精度与 CPU 停机/睡眠停表状态。
    * PC 采样热点图：连续无停机采样 `DWT_PCSR` (`0xE000101C`)，识别 WFI/WFE 睡眠占位符 (`0xFFFFFFFF` / `0x00000000`)，
      通过唯一 PC 缓存调用 `resolve_address` 聚合函数级与地址级热点排行榜。
    * 硬件数据观察点：结合 `decode_cpuid` 自动区分 Armv6-M/Armv7-M 与 Armv8-M 的 DWT 比较器编码，
      并支持通过 DWARF `resolve_watch_target` 按 C 变量/结构体成员名自动算址与对齐大小挂观察点。
"""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from mcu_harness.crash.cortex_m import decode_cpuid
from mcu_harness.dwarf.live_watch import LiveWatchError, resolve_watch_target
from mcu_harness.dwarf.parser import DwarfError, load_dwarf_index
from mcu_harness.elf.addr2line import CodeLocation, resolve_address
from mcu_harness.elf.symbols import ElfError
from mcu_harness.probe.base import DebugProbe
from mcu_harness.safety.guard import default_safety_guard

# ---- CoreSight DWT / DCB / SCB 寄存器常量 ----------------------------------
SCB_CPUID = 0xE000ED00
DCB_DEMCR = 0xE000EDFC
DCB_DEMCR_TRCENA = 1 << 24

DWT_CTRL = 0xE0001000
DWT_CTRL_CYCCNTENA = 1 << 0
DWT_CTRL_NUMCOMP_SHIFT = 28
DWT_CYCCNT = 0xE0001004
DWT_PCSR = 0xE000101C

DWT_COMP_BASE = 0xE0001020
DWT_COMP_STRIDE = 0x10
DWT_COMP_OFF = 0x00
DWT_MASK_OFF = 0x04
DWT_FUNCTION_OFF = 0x08
DWT_FUNCTION_MATCHED_BIT = 24

_VALID_ACCESS_MODES = ("write", "read", "rw")
_SIZE_TO_CODE = {1: 0, 2: 1, 4: 2}
_ARMV7_FUNC_BY_ACCESS = {"read": 0x5, "write": 0x6, "rw": 0x7}
_ARMV7_ACCESS_BY_FUNC = {v: k for k, v in _ARMV7_FUNC_BY_ACCESS.items()}
_ARMV8_MATCH_BY_ACCESS = {"rw": 4, "write": 5, "read": 6}
_ARMV8_ACCESS_BY_MATCH = {v: k for k, v in _ARMV8_MATCH_BY_ACCESS.items()}
_CODE_TO_SIZE = {v: k for k, v in _SIZE_TO_CODE.items()}
_PCSR_UNAVAILABLE_VALUES = (0x00000000, 0xFFFFFFFF)


class TraceError(RuntimeError):
    """DWT 追踪、PC 采样或硬件观察点配置错误。"""


def dwt_comp_addr(slot: int) -> int:
    """返回 Comparator `slot` 的 COMPn 寄存器地址。"""
    return DWT_COMP_BASE + slot * DWT_COMP_STRIDE + DWT_COMP_OFF


def dwt_mask_addr(slot: int) -> int:
    """返回 Comparator `slot` 的 MASKn 寄存器地址（Armv6-M/Armv7-M）。"""
    return DWT_COMP_BASE + slot * DWT_COMP_STRIDE + DWT_MASK_OFF


def dwt_function_addr(slot: int) -> int:
    """返回 Comparator `slot` 的 FUNCTIONn 寄存器地址。"""
    return DWT_COMP_BASE + slot * DWT_COMP_STRIDE + DWT_FUNCTION_OFF


def _enable_dwt_trace(probe: DebugProbe, *, enable_cyccnt: bool = False) -> tuple[int, int]:
    """保留其余位的前提下置位 DCB_DEMCR.TRCENA（及可选 DWT_CTRL.CYCCNTENA）。"""
    demcr = probe.read_u32(DCB_DEMCR, 1)[0]
    if (demcr & DCB_DEMCR_TRCENA) == 0:
        demcr |= DCB_DEMCR_TRCENA
        probe.write_u32(DCB_DEMCR, [demcr])

    ctrl = probe.read_u32(DWT_CTRL, 1)[0]
    if enable_cyccnt and (ctrl & DWT_CTRL_CYCCNTENA) == 0:
        ctrl |= DWT_CTRL_CYCCNTENA
        probe.write_u32(DWT_CTRL, [ctrl])

    return demcr, ctrl


# ============================================================================
# 1. 周期测速与 SYSCLK 估算
# ============================================================================


@dataclass
class CycleMeasureReport:
    """DWT_CYCCNT 非侵入周期测速与主频估算报告。"""

    start_cycles: int
    end_cycles: int
    delta_cycles: int
    wrapped: bool
    requested_duration_ms: float
    elapsed_ms: float
    estimated_core_hz: float
    estimated_mhz: float
    cyccnt_advancing: bool
    expected_core_hz: int | None = None
    error_percent: float | None = None
    window_precision_note: str = ""
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_text(self) -> str:
        lines = [
            "=== DWT Cycle Counter Measurement ===",
            f"Summary     : {self.summary}",
            f"CYCCNT      : 0x{self.start_cycles:08X} -> 0x{self.end_cycles:08X} "
            f"(delta={self.delta_cycles}, wrapped={self.wrapped}, advancing={self.cyccnt_advancing})",
            f"Window      : requested={self.requested_duration_ms:.2f} ms, elapsed={self.elapsed_ms:.3f} ms",
            f"Estimated   : {self.estimated_mhz:.3f} MHz ({self.estimated_core_hz:.1f} Hz)",
        ]
        if self.expected_core_hz is not None and self.error_percent is not None:
            lines.append(
                f"Reference   : {self.expected_core_hz / 1_000_000.0:.3f} MHz ({self.expected_core_hz} Hz), "
                f"error={self.error_percent:+.2f}%"
            )
        if self.window_precision_note:
            lines.append(f"Precision   : {self.window_precision_note}")
        return "\n".join(lines)


def _build_window_precision_note(
    *,
    requested_duration_ms: float,
    elapsed_ms: float,
    cyccnt_advancing: bool,
    core_hz: int | None,
    error_percent: float | None,
) -> str:
    if not cyccnt_advancing:
        return (
            "DWT_CYCCNT did not advance during the measurement window: "
            "CPU may be halted, stuck in WFI/WFE/STOP sleep, or core clock gated."
        )
    jitter_ms = elapsed_ms - requested_duration_ms
    parts = [
        f"Host measurement window elapsed {elapsed_ms:.3f} ms vs requested {requested_duration_ms:.2f} ms "
        f"(host sleep/SWD overhead {jitter_ms:+.3f} ms)."
    ]
    if requested_duration_ms < 20.0:
        parts.append("Short window (<20 ms) is sensitive to host OS timer jitter and SWD round-trip latency.")
    if core_hz is not None and error_percent is not None:
        parts.append(
            f"Relative deviation against {core_hz} Hz reference is {error_percent:+.2f}% "
            f"({'within' if abs(error_percent) <= 5.0 else 'exceeds'} 5% tolerance)."
        )
    return " ".join(parts)


def measure_cycles(
    probe: DebugProbe,
    *,
    duration_ms: float = 50.0,
    core_hz: int | None = None,
) -> CycleMeasureReport:
    """不暂停 CPU 读取 DWT_CYCCNT 差分，估算当前 SYSCLK 主频并判断 CPU 是否停表。"""
    if duration_ms <= 0 or duration_ms > 60_000:
        raise TraceError(f"duration_ms must be in (0, 60000], got {duration_ms}")
    if core_hz is not None and core_hz <= 0:
        raise TraceError(f"core_hz must be > 0, got {core_hz}")

    _enable_dwt_trace(probe, enable_cyccnt=True)
    default_safety_guard.record_audit(
        "dwt_measure_cycles",
        f"DCB_DEMCR=0x{DCB_DEMCR:08X}, DWT_CTRL=0x{DWT_CTRL:08X}",
        level="allow",
        allowed=True,
        detail=f"duration_ms={duration_ms}, core_hz={core_hz}",
    )

    c0 = probe.read_u32(DWT_CYCCNT, 1)[0] & 0xFFFFFFFF
    t0 = time.monotonic()
    time.sleep(duration_ms / 1000.0)
    c1 = probe.read_u32(DWT_CYCCNT, 1)[0] & 0xFFFFFFFF
    t1 = time.monotonic()

    delta_cycles = (c1 - c0) & 0xFFFFFFFF
    wrapped = c1 < c0
    elapsed_s = max(t1 - t0, 1e-9)
    elapsed_ms = elapsed_s * 1000.0
    estimated_core_hz = delta_cycles / elapsed_s
    estimated_mhz = estimated_core_hz / 1_000_000.0
    cyccnt_advancing = delta_cycles > 0

    error_percent: float | None = None
    if core_hz is not None:
        error_percent = ((estimated_core_hz - core_hz) / core_hz) * 100.0

    precision_note = _build_window_precision_note(
        requested_duration_ms=duration_ms,
        elapsed_ms=elapsed_ms,
        cyccnt_advancing=cyccnt_advancing,
        core_hz=core_hz,
        error_percent=error_percent,
    )

    if not cyccnt_advancing:
        summary = f"DWT_CYCCNT stalled (0 cycles in {elapsed_ms:.2f} ms); CPU halted or sleeping."
    elif error_percent is not None:
        summary = (
            f"Estimated SYSCLK {estimated_mhz:.3f} MHz "
            f"(ref {core_hz / 1_000_000.0:.3f} MHz, error {error_percent:+.2f}%, {delta_cycles} cycles)."
        )
    else:
        summary = f"Estimated SYSCLK {estimated_mhz:.3f} MHz ({delta_cycles} cycles in {elapsed_ms:.2f} ms)."

    return CycleMeasureReport(
        start_cycles=c0,
        end_cycles=c1,
        delta_cycles=delta_cycles,
        wrapped=wrapped,
        requested_duration_ms=duration_ms,
        elapsed_ms=elapsed_ms,
        estimated_core_hz=estimated_core_hz,
        estimated_mhz=estimated_mhz,
        cyccnt_advancing=cyccnt_advancing,
        expected_core_hz=core_hz,
        error_percent=error_percent,
        window_precision_note=precision_note,
        summary=summary,
    )


# ============================================================================
# 2. PC 采样热点图
# ============================================================================


@dataclass
class PcAddressHotspot:
    """单个采样 PC 地址的热点统计。"""

    address: str
    address_int: int
    hits: int
    percent: float
    active_percent: float
    symbol: str | None = None
    symbol_offset: int | None = None
    file: str | None = None
    line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PcFunctionHotspot:
    """按函数符号聚合的热点统计。"""

    symbol: str
    hits: int
    percent: float
    active_percent: float
    file: str | None = None
    line: int | None = None
    top_address: str | None = None
    addresses: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PcProfileReport:
    """DWT_PCSR 非侵入 PC 采样热点图报告。"""

    total_samples: int
    valid_samples: int
    sleeping_or_unavailable_samples: int
    unique_pcs: int
    resolve_calls: int
    interval_ms: float
    duration_ms: float
    elf_path: str | None
    function_hotspots: list[PcFunctionHotspot] = field(default_factory=list)
    address_hotspots: list[PcAddressHotspot] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_samples": self.total_samples,
            "valid_samples": self.valid_samples,
            "sleeping_or_unavailable_samples": self.sleeping_or_unavailable_samples,
            "unique_pcs": self.unique_pcs,
            "resolve_calls": self.resolve_calls,
            "interval_ms": self.interval_ms,
            "duration_ms": self.duration_ms,
            "elf_path": self.elf_path,
            "function_hotspots": [item.to_dict() for item in self.function_hotspots],
            "address_hotspots": [item.to_dict() for item in self.address_hotspots],
            "summary": self.summary,
        }

    def to_text(self) -> str:
        lines = [
            "=== DWT PC Sampling Hotspot Report ===",
            f"Summary  : {self.summary}",
            f"Samples  : total={self.total_samples}, valid={self.valid_samples}, "
            f"sleeping_or_unavailable={self.sleeping_or_unavailable_samples}, "
            f"unique_pcs={self.unique_pcs}, duration={self.duration_ms:.2f} ms",
        ]
        if self.function_hotspots:
            lines.append("\n[Function Hotspots]")
            for idx, fn in enumerate(self.function_hotspots[:20], 1):
                loc = f"{fn.file}:{fn.line}" if fn.file and fn.line else (fn.file or fn.top_address or "")
                lines.append(
                    f"  {idx:2d}. {fn.symbol:<28s} {fn.hits:4d} hits ({fn.percent:5.1f}% total, "
                    f"{fn.active_percent:5.1f}% active)  [{loc}]"
                )
        if self.address_hotspots:
            lines.append("\n[Address Hotspots]")
            for idx, addr in enumerate(self.address_hotspots[:20], 1):
                sym_part = addr.symbol or "<unknown>"
                loc = f"{addr.file}:{addr.line}" if addr.file and addr.line else (addr.file or "-")
                lines.append(
                    f"  {idx:2d}. {addr.address}  {addr.hits:4d} hits ({addr.percent:5.1f}%)  "
                    f"{sym_part} @ {loc}"
                )
        return "\n".join(lines)


def profile_pc_hotspots(
    probe: DebugProbe,
    *,
    elf_path: str | os.PathLike | None = None,
    project_dir: str | os.PathLike | None = None,
    samples: int = 100,
    interval_ms: float = 1.0,
) -> PcProfileReport:
    """不暂停 CPU 连续采集 DWT_PCSR，聚合函数级与地址级热点排行榜。"""
    if not (1 <= samples <= 1000):
        raise TraceError(f"samples must be between 1 and 1000, got {samples}")
    if interval_ms < 0 or interval_ms > 60_000:
        raise TraceError(f"interval_ms must be between 0 and 60000, got {interval_ms}")

    _enable_dwt_trace(probe, enable_cyccnt=False)
    default_safety_guard.record_audit(
        "dwt_profile_pc",
        f"DWT_PCSR=0x{DWT_PCSR:08X}",
        level="allow",
        allowed=True,
        detail=f"samples={samples}, interval_ms={interval_ms}",
    )

    pc_counts: dict[int, int] = {}
    sleeping_or_unavailable = 0
    interval_s = interval_ms / 1000.0

    t0 = time.monotonic()
    for idx in range(samples):
        pcsr = probe.read_u32(DWT_PCSR, 1)[0] & 0xFFFFFFFF
        if pcsr in _PCSR_UNAVAILABLE_VALUES:
            sleeping_or_unavailable += 1
        else:
            pc_counts[pcsr] = pc_counts.get(pcsr, 0) + 1
        if idx + 1 < samples and interval_s > 0:
            time.sleep(interval_s)
    duration_ms = (time.monotonic() - t0) * 1000.0

    valid_samples = samples - sleeping_or_unavailable
    resolved_cache: dict[int, CodeLocation] = {}
    resolve_calls = 0
    if elf_path is not None:
        for pc_addr in pc_counts:
            resolved_cache[pc_addr] = resolve_address(
                elf_path,
                pc_addr,
                project_dir=project_dir,
                is_return_address=False,
            )
            resolve_calls += 1

    address_hotspots: list[PcAddressHotspot] = []
    func_groups: dict[str, dict[str, Any]] = {}

    for pc_addr, hits in sorted(pc_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        loc = resolved_cache.get(pc_addr)
        addr_hex = f"0x{pc_addr:08X}"
        pct = (hits / samples) * 100.0
        act_pct = (hits / valid_samples) * 100.0 if valid_samples > 0 else 0.0

        sym = loc.symbol if loc else None
        sym_off = loc.symbol_offset if loc else None
        src_file = loc.file if loc else None
        src_line = loc.line if loc else None

        address_hotspots.append(
            PcAddressHotspot(
                address=addr_hex,
                address_int=pc_addr,
                hits=hits,
                percent=round(pct, 3),
                active_percent=round(act_pct, 3),
                symbol=sym,
                symbol_offset=sym_off,
                file=src_file,
                line=src_line,
            )
        )

        group_key = sym if sym else addr_hex
        grp = func_groups.get(group_key)
        if grp is None:
            grp = {
                "symbol": group_key,
                "hits": 0,
                "best_hits": -1,
                "file": src_file,
                "line": src_line,
                "top_address": addr_hex,
                "addresses": [],
            }
            func_groups[group_key] = grp
        grp["hits"] += hits
        grp["addresses"].append(addr_hex)
        if hits > grp["best_hits"]:
            grp["best_hits"] = hits
            grp["top_address"] = addr_hex
            if src_file is not None:
                grp["file"] = src_file
            if src_line is not None:
                grp["line"] = src_line

    function_hotspots: list[PcFunctionHotspot] = []
    for grp in sorted(func_groups.values(), key=lambda g: (-g["hits"], g["symbol"])):
        hits = grp["hits"]
        function_hotspots.append(
            PcFunctionHotspot(
                symbol=grp["symbol"],
                hits=hits,
                percent=round((hits / samples) * 100.0, 3),
                active_percent=round((hits / valid_samples) * 100.0, 3) if valid_samples > 0 else 0.0,
                file=grp["file"],
                line=grp["line"],
                top_address=grp["top_address"],
                addresses=grp["addresses"],
            )
        )

    if function_hotspots:
        top = function_hotspots[0]
        summary = (
            f"Top hotspot: {top.symbol} ({top.hits}/{samples} samples, {top.percent:.1f}% total); "
            f"{sleeping_or_unavailable} sleeping/unavailable."
        )
    else:
        summary = f"All {samples} samples were sleeping/unavailable (WFI/WFE or PCSR unavailable)."

    return PcProfileReport(
        total_samples=samples,
        valid_samples=valid_samples,
        sleeping_or_unavailable_samples=sleeping_or_unavailable,
        unique_pcs=len(pc_counts),
        resolve_calls=resolve_calls,
        interval_ms=interval_ms,
        duration_ms=round(duration_ms, 3),
        elf_path=str(elf_path) if elf_path is not None else None,
        function_hotspots=function_hotspots,
        address_hotspots=address_hotspots,
        summary=summary,
    )


# ============================================================================
# 3. 硬件数据观察点（Data Watchpoint）
# ============================================================================


@dataclass
class WatchpointInfo:
    """已配置的 DWT 硬件数据观察点快照。"""

    slot: int
    active: bool
    address: int
    address_hex: str
    access: str
    size_bytes: int
    matched: bool
    arch_family: str
    core_name: str
    num_comparators: int
    target_expr: str | None = None
    comp_raw: str = ""
    mask_raw: str | None = None
    function_raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_text(self) -> str:
        expr_note = f" ({self.target_expr})" if self.target_expr else ""
        return (
            f"Watchpoint slot {self.slot}/{self.num_comparators - 1} [{self.core_name} / {self.arch_family}]: "
            f"addr={self.address_hex}{expr_note}, access={self.access}, size={self.size_bytes}B, "
            f"matched={self.matched}, FUNCTION={self.function_raw}"
        )


@dataclass
class WatchpointSlotStatus:
    """单个 DWT 比较器槽位状态（用于 list_watchpoints）。"""

    slot: int
    active: bool
    address: int | None
    address_hex: str | None
    access: str | None
    size_bytes: int | None
    matched: bool
    comp_raw: str
    mask_raw: str | None
    function_raw: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WatchpointListReport:
    """所有 DWT 观察点槽位列表状态报告。"""

    core_name: str
    arch_family: str
    num_comparators: int
    active_count: int
    matched_count: int
    watchpoints: list[WatchpointSlotStatus] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "core_name": self.core_name,
            "arch_family": self.arch_family,
            "num_comparators": self.num_comparators,
            "active_count": self.active_count,
            "matched_count": self.matched_count,
            "watchpoints": [w.to_dict() for w in self.watchpoints],
        }

    def to_text(self) -> str:
        lines = [
            f"=== DWT Watchpoints [{self.core_name} ({self.arch_family}), NUMCOMP={self.num_comparators}] ===",
            f"Active: {self.active_count}/{self.num_comparators}, Matched: {self.matched_count}",
        ]
        for wp in self.watchpoints:
            if wp.active:
                hit_tag = " [MATCHED]" if wp.matched else ""
                lines.append(
                    f"  slot {wp.slot}: ACTIVE  addr={wp.address_hex}  access={wp.access}  "
                    f"size={wp.size_bytes}B  FUNC={wp.function_raw}{hit_tag}"
                )
            else:
                lines.append(f"  slot {wp.slot}: FREE    FUNC={wp.function_raw}")
        return "\n".join(lines)


def _infer_dwarf_watch_size(address: int, read_size: int) -> int:
    """根据 DWARF 表达式目标字节宽度与地址对齐推断硬件观察点监视长度 (1/2/4)。"""
    if read_size in _SIZE_TO_CODE:
        return read_size
    if read_size > 4:
        for cand in (4, 2, 1):
            if address % cand == 0:
                return cand
    raise TraceError(f"Resolved DWARF target has unsupported watchpoint size {read_size} bytes")


def _resolve_target_address_and_size(
    probe: DebugProbe,
    target: str | int,
    size_bytes: int | None,
    elf_path: str | os.PathLike | None,
) -> tuple[int, int, str | None]:
    if isinstance(target, bool):
        raise TraceError(f"Invalid watchpoint target: {target!r}")

    if isinstance(target, int):
        addr = target
        expr_label: str | None = None
        inferred_size = 4
    else:
        raw_str = str(target).strip()
        if not raw_str:
            raise TraceError("Watchpoint target must not be empty")
        try:
            addr = int(raw_str, 0)
            expr_label = None
            inferred_size = 4
        except ValueError:
            if elf_path is None:
                raise TraceError(
                    f"Watchpoint target {target!r} is not a numeric address and elf_path was not provided"
                )
            try:
                index = load_dwarf_index(elf_path)
                resolved = resolve_watch_target(index, raw_str, probe=probe)
            except (DwarfError, ElfError, LiveWatchError, FileNotFoundError, OSError) as exc:
                raise TraceError(f"Failed to resolve watchpoint target {raw_str!r}: {exc}") from exc
            addr = resolved.address
            expr_label = resolved.expr
            inferred_size = _infer_dwarf_watch_size(addr, resolved.read_size)

    if addr < 0 or addr > 0xFFFFFFFF:
        raise TraceError(f"Watchpoint address 0x{addr:X} is out of 32-bit Cortex-M address range")

    effective_size = inferred_size if size_bytes is None else size_bytes
    if effective_size not in _SIZE_TO_CODE:
        raise TraceError(f"size_bytes must be one of (1, 2, 4), got {effective_size}")
    if addr % effective_size != 0:
        raise TraceError(
            f"Watchpoint address 0x{addr:08X} is not aligned to {effective_size}-byte boundary"
        )
    return addr, effective_size, expr_label


def set_watchpoint(
    probe: DebugProbe,
    target: str | int,
    *,
    access: str = "write",
    size_bytes: int | None = None,
    slot: int | None = None,
    elf_path: str | os.PathLike | None = None,
) -> WatchpointInfo:
    """在空闲或指定 DWT 比较器槽位配置硬件数据观察点（不暂停 CPU）。"""
    access_norm = access.strip().lower()
    if access_norm not in _VALID_ACCESS_MODES:
        raise TraceError(f"access must be one of {_VALID_ACCESS_MODES}, got {access!r}")

    address, eff_size, target_expr = _resolve_target_address_and_size(probe, target, size_bytes, elf_path)

    _enable_dwt_trace(probe, enable_cyccnt=False)
    cpuid = probe.read_u32(SCB_CPUID, 1)[0]
    core = decode_cpuid(cpuid)

    ctrl = probe.read_u32(DWT_CTRL, 1)[0]
    numcomp = (ctrl >> DWT_CTRL_NUMCOMP_SHIFT) & 0xF
    if numcomp == 0:
        raise TraceError("Target DWT reports NUMCOMP=0 (no hardware data watchpoint comparators available)")

    if slot is not None:
        if not (0 <= slot < numcomp):
            raise TraceError(f"Watchpoint slot {slot} out of range 0..{numcomp - 1}")
        chosen_slot = slot
    else:
        chosen_slot = -1
        for idx in range(numcomp):
            fn_val = probe.read_u32(dwt_function_addr(idx), 1)[0]
            if (fn_val & 0x1F) == 0:
                chosen_slot = idx
                break
        if chosen_slot < 0:
            raise TraceError(f"All {numcomp} DWT comparator slots are already in use")

    size_code = _SIZE_TO_CODE[eff_size]
    comp_val = address & 0xFFFFFFFF
    mask_val: int | None = None

    if core.arch_family == "Armv8-M":
        match_code = _ARMV8_MATCH_BY_ACCESS[access_norm]
        func_val = (size_code << 10) | (1 << 4) | match_code
        probe.write_u32(dwt_comp_addr(chosen_slot), [comp_val])
        probe.write_u32(dwt_function_addr(chosen_slot), [func_val])
    else:
        # Armv7-M (M3/M4/M7) & Armv6-M (M0/M0+/M1)
        mask_val = size_code
        func_val = _ARMV7_FUNC_BY_ACCESS[access_norm]
        probe.write_u32(dwt_comp_addr(chosen_slot), [comp_val])
        probe.write_u32(dwt_mask_addr(chosen_slot), [mask_val])
        probe.write_u32(dwt_function_addr(chosen_slot), [func_val])

    default_safety_guard.record_audit(
        "dwt_set_watchpoint",
        f"slot={chosen_slot} @ 0x{comp_val:08X}",
        level="allow",
        allowed=True,
        detail=(
            f"arch={core.arch_family}, access={access_norm}, size={eff_size}, "
            f"expr={target_expr or 'raw'}, FUNC=0x{func_val:08X}"
        ),
    )

    return WatchpointInfo(
        slot=chosen_slot,
        active=True,
        address=comp_val,
        address_hex=f"0x{comp_val:08X}",
        access=access_norm,
        size_bytes=eff_size,
        matched=False,
        arch_family=core.arch_family,
        core_name=core.core_name,
        num_comparators=numcomp,
        target_expr=target_expr,
        comp_raw=f"0x{comp_val:08X}",
        mask_raw=f"0x{mask_val:08X}" if mask_val is not None else None,
        function_raw=f"0x{func_val:08X}",
    )


def list_watchpoints(probe: DebugProbe) -> WatchpointListReport:
    """读取所有 DWT 比较器槽位，解码激活状态、观察地址、访问类型、宽度与命中标志。"""
    _enable_dwt_trace(probe, enable_cyccnt=False)
    cpuid = probe.read_u32(SCB_CPUID, 1)[0]
    core = decode_cpuid(cpuid)

    ctrl = probe.read_u32(DWT_CTRL, 1)[0]
    numcomp = (ctrl >> DWT_CTRL_NUMCOMP_SHIFT) & 0xF

    slots: list[WatchpointSlotStatus] = []
    active_count = 0
    matched_count = 0

    for idx in range(numcomp):
        comp_val = probe.read_u32(dwt_comp_addr(idx), 1)[0] & 0xFFFFFFFF
        func_val = probe.read_u32(dwt_function_addr(idx), 1)[0] & 0xFFFFFFFF
        mask_val: int | None = None
        if core.arch_family != "Armv8-M":
            mask_val = probe.read_u32(dwt_mask_addr(idx), 1)[0] & 0xFFFFFFFF

        matched = bool(func_val & (1 << DWT_FUNCTION_MATCHED_BIT))
        active = (func_val & 0x1F) != 0

        if matched:
            matched_count += 1

        access_str: str | None = None
        size_val: int | None = None
        addr_val: int | None = None
        addr_hex: str | None = None

        if active:
            active_count += 1
            addr_val = comp_val
            addr_hex = f"0x{comp_val:08X}"
            if core.arch_family == "Armv8-M":
                match_code = func_val & 0xF
                datavsize = (func_val >> 10) & 0x3
                access_str = _ARMV8_ACCESS_BY_MATCH.get(match_code, f"unknown(0x{match_code:X})")
                size_val = _CODE_TO_SIZE.get(datavsize)
            else:
                func_code = func_val & 0xF
                access_str = _ARMV7_ACCESS_BY_FUNC.get(func_code, f"unknown(0x{func_code:X})")
                if mask_val is not None:
                    size_val = _CODE_TO_SIZE.get(mask_val & 0x1F)

        slots.append(
            WatchpointSlotStatus(
                slot=idx,
                active=active,
                address=addr_val,
                address_hex=addr_hex,
                access=access_str,
                size_bytes=size_val,
                matched=matched,
                comp_raw=f"0x{comp_val:08X}",
                mask_raw=f"0x{mask_val:08X}" if mask_val is not None else None,
                function_raw=f"0x{func_val:08X}",
            )
        )

    return WatchpointListReport(
        core_name=core.core_name,
        arch_family=core.arch_family,
        num_comparators=numcomp,
        active_count=active_count,
        matched_count=matched_count,
        watchpoints=slots,
    )


def clear_watchpoints(probe: DebugProbe, *, slot: int | None = None) -> dict[str, Any]:
    """清零指定或全部 DWT 比较器槽位的 FUNCTIONn 寄存器。"""
    _enable_dwt_trace(probe, enable_cyccnt=False)
    ctrl = probe.read_u32(DWT_CTRL, 1)[0]
    numcomp = (ctrl >> DWT_CTRL_NUMCOMP_SHIFT) & 0xF

    if slot is not None:
        if numcomp == 0 or not (0 <= slot < numcomp):
            raise TraceError(f"Watchpoint slot {slot} out of range (NUMCOMP={numcomp})")
        target_slots = [slot]
    else:
        target_slots = list(range(numcomp))

    cleared_active: list[int] = []
    for idx in target_slots:
        prev_fn = probe.read_u32(dwt_function_addr(idx), 1)[0]
        if (prev_fn & 0x1F) != 0:
            cleared_active.append(idx)
        probe.write_u32(dwt_function_addr(idx), [0])

    default_safety_guard.record_audit(
        "dwt_clear_watchpoints",
        f"slots={target_slots}",
        level="allow",
        allowed=True,
        detail=f"numcomp={numcomp}, previously_active={cleared_active}",
    )

    return {
        "num_comparators": numcomp,
        "cleared_slots": target_slots,
        "cleared_count": len(target_slots),
        "previously_active_slots": cleared_active,
    }
