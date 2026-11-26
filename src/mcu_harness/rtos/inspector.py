"""纯标准库无停机 RTOS 内核透视与栈高水位扫描模块。

支持平台：
    * FreeRTOS (`pxCurrentTCB` / `pxReadyTasksLists` / `pxDelayedTaskList` / `xSuspendedTaskList`)
    * Zephyr (`_kernel` / `k_thread` / `stack_info` / `callee_saved`)
    * RT-Thread (`rt_current_thread` / `rt_object_container` / `rt_thread`)

设计原则：
    * 全程禁止调用 `probe.halt()` / `probe.resume()`，对运行中 MCU 零侵入。
    * 优先从 ELF DWARF 类型图动态解析结构体成员偏移与指针宽度（兼容 32 位 MCU 与 64 位宿主机 ELF），
      无 DWARF 调试信息时自动回退到 32 位 Cortex-M 标准偏移。
    * 链表遍历内置 `visited_nodes` / `visited_tcbs` 环路检测、`max_tasks` 上限保护与指针合法性校验，
      栈扫描内置 64 KB 单次读取上限保护。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from mcu_harness.dwarf.parser import (
    ArrayType,
    BaseType,
    DwarfError,
    DwarfIndex,
    DwarfType,
    PointerType,
    QualifiedType,
    StructMember,
    StructType,
    load_dwarf_index,
)
from mcu_harness.elf.symbols import ElfError, ElfSymbol, read_elf_symbols
from mcu_harness.probe.base import DebugProbe, ProbeError

MAX_STACK_READ_BYTES = 64 * 1024  # 64 KB 单次栈内存读取上限保护
STACK_CRITICAL_THRESHOLD_BYTES = 64

_WATERMARK_BYTES: dict[str, int] = {
    "freertos": 0xA5,
    "zephyr": 0xAA,
    "rtthread": 0x23,  # '#'
}

_SUPPORTED_RTOS = ("freertos", "zephyr", "rtthread")


class RtosError(RuntimeError):
    """RTOS 内核透视或符号/结构体解析错误。"""

    def to_dict(self) -> dict[str, Any]:
        return {"error": str(self)}


@dataclass
class RtosTaskInfo:
    """单个 RTOS 任务/线程运行快照与栈高水位信息。"""

    name: str
    tcb_address: str
    state: Literal["RUNNING", "READY", "BLOCKED", "SUSPENDED", "UNKNOWN"]
    priority: int | None = None
    sp: str | None = None
    stack_base: str | None = None
    stack_end: str | None = None
    stack_size_bytes: int | None = None
    stack_free_min_bytes: int | None = None
    stack_used_peak_pct: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tcb_address": self.tcb_address,
            "state": self.state,
            "priority": self.priority,
            "sp": self.sp,
            "stack_base": self.stack_base,
            "stack_end": self.stack_end,
            "stack_size_bytes": self.stack_size_bytes,
            "stack_free_min_bytes": self.stack_free_min_bytes,
            "stack_used_peak_pct": self.stack_used_peak_pct,
            "extra": dict(self.extra),
        }


@dataclass
class RtosHazard:
    """RTOS 内核透视与栈高水位扫描检出的风险告警。"""

    severity: Literal["critical", "warning", "info"]
    code: str  # "STACK_OVERFLOW" | "STACK_CRITICAL" | "CYCLE_DETECTED" | "INVALID_POINTER"
    task_name: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "task_name": self.task_name,
            "message": self.message,
        }


@dataclass
class RtosReport:
    """RTOS 内核透视汇总报告。"""

    rtos: str
    elf_path: str
    tick_count: int | None = None
    scheduler_running: bool | None = None
    current_task: str | None = None
    current_tcb_address: str | None = None
    reported_task_count: int | None = None
    tasks: list[RtosTaskInfo] = field(default_factory=list)
    hazards: list[RtosHazard] = field(default_factory=list)
    pointer_size: int = 4
    dwarf_resolved: bool = True
    truncated: bool = False

    @property
    def summary(self) -> dict[str, Any]:
        state_counts: dict[str, int] = {}
        for task in self.tasks:
            state_counts[task.state] = state_counts.get(task.state, 0) + 1
        return {
            "task_count": len(self.tasks),
            "state_counts": state_counts,
            "hazard_count": len(self.hazards),
            "critical_count": sum(1 for h in self.hazards if h.severity == "critical"),
            "warning_count": sum(1 for h in self.hazards if h.severity == "warning"),
            "info_count": sum(1 for h in self.hazards if h.severity == "info"),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "rtos": self.rtos,
            "elf_path": self.elf_path,
            "tick_count": self.tick_count,
            "scheduler_running": self.scheduler_running,
            "current_task": self.current_task,
            "current_tcb_address": self.current_tcb_address,
            "reported_task_count": self.reported_task_count,
            "pointer_size": self.pointer_size,
            "dwarf_resolved": self.dwarf_resolved,
            "truncated": self.truncated,
            "summary": self.summary,
            "tasks": [t.to_dict() for t in self.tasks],
            "hazards": [h.to_dict() for h in self.hazards],
        }

    def to_text(self) -> str:
        rtos_labels = {
            "freertos": "FreeRTOS",
            "zephyr": "Zephyr",
            "rtthread": "RT-Thread",
        }
        title = rtos_labels.get(self.rtos, self.rtos)
        sched_str = (
            "RUNNING"
            if self.scheduler_running is True
            else ("STOPPED" if self.scheduler_running is False else "N/A")
        )
        tick_str = str(self.tick_count) if self.tick_count is not None else "N/A"
        curr_str = (
            f"{self.current_task or '<unknown>'} ({self.current_tcb_address})"
            if self.current_tcb_address
            else (self.current_task or "N/A")
        )
        lines = [
            f"=== RTOS Inspection Report ({title}) ===",
            f"ELF          : {self.elf_path}",
            f"Arch/DWARF   : {self.pointer_size * 8}-bit | DWARF={'yes' if self.dwarf_resolved else 'fallback'}",
            f"Scheduler    : {sched_str} | Tick={tick_str} | Tasks={len(self.tasks)}"
            + (f" (reported={self.reported_task_count})" if self.reported_task_count is not None else "")
            + (" [TRUNCATED]" if self.truncated else ""),
            f"Current Task : {curr_str}",
            "",
        ]

        if not self.tasks:
            lines.append("Tasks: (none)")
        else:
            header = (
                f"{'Task':<18s} {'State':<10s} {'Prio':>4s}  {'TCB':<12s} "
                f"{'SP':<12s} {'StackSize':>9s} {'FreeMin':>8s} {'Peak%':>7s}"
            )
            lines.append(header)
            lines.append("-" * len(header))
            for t in self.tasks:
                prio_s = str(t.priority) if t.priority is not None else "-"
                sp_s = t.sp or "-"
                sz_s = f"{t.stack_size_bytes} B" if t.stack_size_bytes is not None else "-"
                free_s = f"{t.stack_free_min_bytes} B" if t.stack_free_min_bytes is not None else "-"
                pct_s = f"{t.stack_used_peak_pct:.1f}%" if t.stack_used_peak_pct is not None else "-"
                lines.append(
                    f"{t.name[:18]:<18s} {t.state:<10s} {prio_s:>4s}  {t.tcb_address:<12s} "
                    f"{sp_s:<12s} {sz_s:>9s} {free_s:>8s} {pct_s:>7s}"
                )

        lines.append("")
        if not self.hazards:
            lines.append("Hazards: None detected.")
        else:
            lines.append(f"Hazards ({len(self.hazards)}):")
            for h in self.hazards:
                who = h.task_name or "<kernel>"
                lines.append(f"  [{h.severity.upper()}] {h.code} ({who}): {h.message}")
        return "\n".join(lines)


# ==========================================================================
# 符号表与 DWARF 结构体偏移动态解析辅助工具
# ==========================================================================


def _format_hex(addr: int, ptr_size: int) -> str:
    width = 16 if (ptr_size > 4 or addr > 0xFFFFFFFF) else 8
    return f"0x{addr:0{width}X}"


def _is_valid_ptr(addr: int, ptr_size: int) -> bool:
    return addr > 0 and (addr % ptr_size) == 0


def _build_symbol_map(elf_path: str | os.PathLike) -> dict[str, ElfSymbol]:
    syms = read_elf_symbols(elf_path)
    out: dict[str, ElfSymbol] = {}
    for sym in syms:
        prev = out.get(sym.name)
        if prev is None:
            out[sym.name] = sym
            continue
        # 优先保留已分配地址的 object 符号
        prev_score = (1 if prev.sym_type == "object" else 0, 1 if prev.address > 0 else 0, prev.size)
        cur_score = (1 if sym.sym_type == "object" else 0, 1 if sym.address > 0 else 0, sym.size)
        if cur_score > prev_score:
            out[sym.name] = sym
    return out


def _safe_load_dwarf(elf_path: str | os.PathLike) -> DwarfIndex | None:
    try:
        return load_dwarf_index(elf_path)
    except DwarfError:
        return None


def _strip_struct_prefix(name: str) -> str:
    s = name.strip()
    if s.startswith("struct "):
        return s[len("struct ") :].strip()
    if s.startswith("union "):
        return s[len("union ") :].strip()
    return s


def _find_struct_type(index: DwarfIndex | None, candidate_names: tuple[str, ...]) -> StructType | None:
    if index is None or not index.types:
        return None

    normalized = {_strip_struct_prefix(n): idx for idx, n in enumerate(candidate_names)}
    best_rank: int | None = None
    best_struct: StructType | None = None

    for dtype in index.types.values():
        if isinstance(dtype, StructType) and not dtype.is_union:
            key = _strip_struct_prefix(dtype.name)
            if key in normalized and dtype.members:
                rank = normalized[key]
                if best_rank is None or rank < best_rank:
                    best_rank = rank
                    best_struct = dtype
        elif isinstance(dtype, QualifiedType) and dtype.qualifier == "typedef":
            key = _strip_struct_prefix(dtype.alias_name)
            if key in normalized:
                resolved = dtype.resolve()
                if isinstance(resolved, StructType) and not resolved.is_union and resolved.members:
                    rank = normalized[key]
                    if best_rank is None or rank < best_rank:
                        best_rank = rank
                        best_struct = resolved
    return best_struct


def _unwrap_pointee_struct(dtype: DwarfType | None) -> StructType | None:
    if dtype is None:
        return None
    cur = dtype.resolve()
    while isinstance(cur, (PointerType, ArrayType)):
        if isinstance(cur, PointerType):
            cur = cur.target_type.resolve()
        else:
            cur = cur.element_type.resolve()
    if isinstance(cur, StructType) and not cur.is_union and cur.members:
        return cur
    return None


def _resolve_member_path(st: StructType | None, path: str) -> tuple[int, StructMember] | None:
    if st is None:
        return None
    cur_struct: StructType | None = st
    total_off = 0
    parts = path.split(".")
    leaf_member: StructMember | None = None
    for idx, part in enumerate(parts):
        if cur_struct is None:
            return None
        arr_index = 0
        has_index = False
        if part.endswith("]") and "[" in part:
            field_name, idx_str = part[:-1].split("[", 1)
            arr_index = int(idx_str, 0)
            has_index = True
        else:
            field_name = part

        member = cur_struct.find_member(field_name)
        if member is None:
            return None
        total_off += member.byte_offset
        resolved_type = member.member_type.resolve()

        if has_index:
            if not isinstance(resolved_type, ArrayType):
                return None
            total_off += arr_index * resolved_type.element_stride()
            resolved_type = resolved_type.sub_element_type().resolve()

        if idx == len(parts) - 1:
            leaf_member = StructMember(
                name=member.name,
                member_type=resolved_type if has_index else member.member_type,
                byte_offset=total_off,
                bit_size=member.bit_size,
                bit_offset=member.bit_offset,
            )
            return total_off, leaf_member

        cur_struct = resolved_type if isinstance(resolved_type, StructType) else None
    return None


def _resolve_first_path(
    st: StructType | None,
    paths: tuple[str, ...],
) -> tuple[int, DwarfType] | None:
    for p in paths:
        res = _resolve_member_path(st, p)
        if res is not None:
            off, mem = res
            return off, mem.member_type
    return None


def _decode_int_bytes(
    buf: bytes,
    offset: int,
    size: int,
    endian: Literal["<", ">"],
    *,
    signed: bool = False,
) -> int | None:
    if offset < 0 or size <= 0 or offset + size > len(buf):
        return None
    byteorder: Literal["little", "big"] = "little" if endian == "<" else "big"
    return int.from_bytes(buf[offset : offset + size], byteorder, signed=signed)


def _field_int(
    buf: bytes,
    st: StructType | None,
    paths: tuple[str, ...],
    fallback_off: int | None,
    fallback_size: int,
    endian: Literal["<", ">"],
    *,
    signed: bool | None = None,
) -> int | None:
    resolved = _resolve_first_path(st, paths)
    if resolved is not None:
        off, mtype = resolved
        rtype = mtype.resolve()
        size = rtype.byte_size if rtype.byte_size in (1, 2, 4, 8) else fallback_size
        is_signed = rtype.is_signed if (signed is None and isinstance(rtype, BaseType)) else bool(signed)
        return _decode_int_bytes(buf, off, size, endian, signed=is_signed)
    if fallback_off is not None:
        return _decode_int_bytes(buf, fallback_off, fallback_size, endian, signed=bool(signed))
    return None


def _field_ptr(
    buf: bytes,
    st: StructType | None,
    paths: tuple[str, ...],
    fallback_off: int | None,
    ptr_size: int,
    endian: Literal["<", ">"],
) -> int | None:
    resolved = _resolve_first_path(st, paths)
    if resolved is not None:
        off, mtype = resolved
        rtype = mtype.resolve()
        size = rtype.byte_size if rtype.byte_size in (4, 8) else ptr_size
        return _decode_int_bytes(buf, off, size, endian, signed=False)
    if fallback_off is not None:
        return _decode_int_bytes(buf, fallback_off, ptr_size, endian, signed=False)
    return None


def _decode_cstr(raw: bytes) -> str:
    nul = raw.find(b"\x00")
    chunk = raw[:nul] if nul != -1 else raw
    return chunk.decode("utf-8", errors="replace").strip()


def _field_string(
    probe: DebugProbe,
    buf: bytes,
    st: StructType | None,
    paths: tuple[str, ...],
    fallback_off: int | None,
    fallback_len: int,
    ptr_size: int,
    endian: Literal["<", ">"],
) -> str:
    resolved = _resolve_first_path(st, paths)
    if resolved is not None:
        off, mtype = resolved
        rtype = mtype.resolve()
        if isinstance(rtype, PointerType):
            str_ptr = _decode_int_bytes(buf, off, ptr_size, endian, signed=False)
            if str_ptr and _is_valid_ptr(str_ptr, 1):
                try:
                    raw = probe.read_memory(str_ptr, 64)
                    return _decode_cstr(raw)
                except ProbeError:
                    return ""
            return ""
        arr_len = rtype.byte_size if rtype.byte_size > 0 else fallback_len
        if 0 <= off < len(buf):
            return _decode_cstr(buf[off : off + min(arr_len, len(buf) - off)])
        return ""
    if fallback_off is not None and 0 <= fallback_off < len(buf):
        return _decode_cstr(buf[fallback_off : fallback_off + min(fallback_len, len(buf) - fallback_off)])
    return ""


def _resolve_global(
    dwarf_index: DwarfIndex | None,
    sym_map: dict[str, ElfSymbol],
    name: str,
) -> tuple[int, int, DwarfType | None] | None:
    var = dwarf_index.get_variable(name) if dwarf_index is not None else None
    sym = sym_map.get(name)
    if var is not None and var.address > 0:
        bsize = var.byte_size or (sym.size if sym else 0)
        return var.address, bsize, var.dwarf_type
    if sym is not None and sym.address > 0:
        return sym.address, sym.size, (var.dwarf_type if var is not None else None)
    return None


def _read_global_uint(
    probe: DebugProbe,
    dwarf_index: DwarfIndex | None,
    sym_map: dict[str, ElfSymbol],
    name: str,
    ptr_size: int,
    endian: Literal["<", ">"],
    *,
    default_size: int = 4,
    is_pointer: bool = False,
) -> int | None:
    info = _resolve_global(dwarf_index, sym_map, name)
    if info is None:
        return None
    addr, bsize, dtype = info
    if is_pointer:
        read_sz = ptr_size
    else:
        resolved = dtype.resolve() if dtype is not None else None
        if resolved is not None and resolved.byte_size in (1, 2, 4, 8):
            read_sz = resolved.byte_size
        elif bsize in (1, 2, 4, 8):
            read_sz = bsize
        else:
            read_sz = default_size
    try:
        raw = probe.read_memory(addr, read_sz)
    except ProbeError:
        return None
    return _decode_int_bytes(raw, 0, read_sz, endian, signed=False)


def _scan_stack_watermark(
    probe: DebugProbe,
    *,
    rtos: str,
    task_name: str,
    stack_base: int | None,
    stack_size: int | None,
    ptr_size: int,
    hazards: list[RtosHazard],
) -> tuple[int | None, float | None]:
    if stack_base is None or stack_size is None:
        return None, None
    if not _is_valid_ptr(stack_base, ptr_size):
        hazards.append(
            RtosHazard(
                severity="warning",
                code="INVALID_POINTER",
                task_name=task_name,
                message=f"Invalid stack_base pointer {_format_hex(stack_base, ptr_size)}",
            )
        )
        return None, None
    if stack_size <= 0 or stack_size > MAX_STACK_READ_BYTES:
        hazards.append(
            RtosHazard(
                severity="warning",
                code="INVALID_POINTER",
                task_name=task_name,
                message=f"Abnormal stack_size {stack_size} B (allowed 1..{MAX_STACK_READ_BYTES} B), skipping stack scan",
            )
        )
        return None, None

    try:
        raw = probe.read_memory(stack_base, stack_size)
    except ProbeError as exc:
        hazards.append(
            RtosHazard(
                severity="warning",
                code="INVALID_POINTER",
                task_name=task_name,
                message=f"Failed to read stack memory at {_format_hex(stack_base, ptr_size)} ({stack_size} B): {exc}",
            )
        )
        return None, None

    fill = _WATERMARK_BYTES[rtos]
    free_min = 0
    for b in raw:
        if b == fill:
            free_min += 1
        else:
            break

    used_peak = stack_size - free_min
    used_pct = round(used_peak * 100.0 / stack_size, 2)

    if free_min == 0:
        hazards.append(
            RtosHazard(
                severity="critical",
                code="STACK_OVERFLOW",
                task_name=task_name,
                message=f"Stack high-water-mark exhausted (0 B free of {stack_size} B)",
            )
        )
    elif free_min < STACK_CRITICAL_THRESHOLD_BYTES:
        hazards.append(
            RtosHazard(
                severity="warning",
                code="STACK_CRITICAL",
                task_name=task_name,
                message=f"Critically low stack headroom: {free_min} B free of {stack_size} B ({used_pct:.1f}% peak used)",
            )
        )
    return free_min, used_pct


# ==========================================================================
# RTOS 自动识别
# ==========================================================================


def detect_rtos(elf_path: str | os.PathLike) -> str | None:
    """结合 ELF 符号表与 DWARF 类型索引自动识别目标固件所用的 RTOS。

    返回 ``"freertos"`` / ``"zephyr"`` / ``"rtthread"``，未能识别返回 ``None``。
    """
    p = Path(elf_path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"ELF file not found: {p}")

    sym_map = _build_symbol_map(p)
    dwarf_index = _safe_load_dwarf(p)

    def has_sym(name: str) -> bool:
        return name in sym_map or (dwarf_index is not None and name in dwarf_index.variables)

    # FreeRTOS: pxCurrentTCB 存在（且含 pxReadyTasksLists 或 uxCurrentNumberOfTasks）
    if has_sym("pxCurrentTCB") and (has_sym("pxReadyTasksLists") or has_sym("uxCurrentNumberOfTasks")):
        return "freertos"

    # Zephyr: _kernel 存在（且存在 k_thread 类型或 _kernel 符号大小 >= 16）
    if has_sym("_kernel"):
        has_kthread = _find_struct_type(dwarf_index, ("k_thread", "struct k_thread")) is not None
        ksym = sym_map.get("_kernel")
        kvar = dwarf_index.get_variable("_kernel") if dwarf_index is not None else None
        ksize = max(ksym.size if ksym else 0, kvar.byte_size if kvar else 0)
        if has_kthread or ksize >= 16:
            return "zephyr"

    # RT-Thread: rt_current_thread 与 rt_object_container 存在
    if has_sym("rt_current_thread") and has_sym("rt_object_container"):
        return "rtthread"

    return None


# ==========================================================================
# FreeRTOS 适配器
# ==========================================================================


def _inspect_freertos(
    probe: DebugProbe,
    elf_path: Path,
    dwarf_index: DwarfIndex | None,
    sym_map: dict[str, ElfSymbol],
    *,
    ptr_size: int,
    endian: Literal["<", ">"],
    max_tasks: int,
    check_stack: bool,
) -> RtosReport:
    if _resolve_global(dwarf_index, sym_map, "pxCurrentTCB") is None:
        raise RtosError("FreeRTOS symbol 'pxCurrentTCB' not found in ELF")

    current_tcb = _read_global_uint(probe, dwarf_index, sym_map, "pxCurrentTCB", ptr_size, endian, is_pointer=True)
    num_tasks = _read_global_uint(probe, dwarf_index, sym_map, "uxCurrentNumberOfTasks", ptr_size, endian, default_size=ptr_size)
    tick_count = _read_global_uint(probe, dwarf_index, sym_map, "xTickCount", ptr_size, endian, default_size=4)
    sched_val = _read_global_uint(probe, dwarf_index, sym_map, "xSchedulerRunning", ptr_size, endian, default_size=ptr_size)
    sched_running = (sched_val != 0) if sched_val is not None else None

    # 解析结构体（优先从全局变量类型沿用，再全局 DWARF 类型查找）
    cur_tcb_info = _resolve_global(dwarf_index, sym_map, "pxCurrentTCB")
    ready_info = _resolve_global(dwarf_index, sym_map, "pxReadyTasksLists")
    delayed_info = _resolve_global(dwarf_index, sym_map, "pxDelayedTaskList")

    tcb_struct = _unwrap_pointee_struct(cur_tcb_info[2] if cur_tcb_info else None) or _find_struct_type(
        dwarf_index, ("tskTaskControlBlock", "tskTCB", "TCB_t")
    )
    list_struct = _unwrap_pointee_struct(ready_info[2] if ready_info else None) or _unwrap_pointee_struct(
        delayed_info[2] if delayed_info else None
    ) or _find_struct_type(dwarf_index, ("xLIST", "List_t"))
    list_item_struct = None
    if list_struct is not None:
        px_idx = _resolve_first_path(list_struct, ("pxIndex",))
        if px_idx is not None:
            list_item_struct = _unwrap_pointee_struct(px_idx[1])
    if list_item_struct is None and tcb_struct is not None:
        state_item = _resolve_first_path(tcb_struct, ("xStateListItem",))
        if state_item is not None:
            list_item_struct = _unwrap_pointee_struct(state_item[1])
    if list_item_struct is None:
        list_item_struct = _find_struct_type(dwarf_index, ("xLIST_ITEM", "ListItem_t"))

    dwarf_resolved = tcb_struct is not None and list_struct is not None and list_item_struct is not None

    # 32 位 Cortex-M 标准偏移兜底
    list_size = list_struct.byte_size if (list_struct and list_struct.byte_size > 0) else (5 * ptr_size)
    list_item_size = list_item_struct.byte_size if (list_item_struct and list_item_struct.byte_size > 0) else (5 * ptr_size)
    tcb_read_size = max(tcb_struct.byte_size if (tcb_struct and tcb_struct.byte_size > 0) else 72, 72)

    xlistend_res = _resolve_first_path(list_struct, ("xListEnd",))
    xlistend_off = xlistend_res[0] if xlistend_res else (2 * ptr_size)
    xlistend_next_res = _resolve_first_path(list_struct, ("xListEnd.pxNext",))
    xlistend_next_off = xlistend_next_res[0] if xlistend_next_res else (xlistend_off + ptr_size)

    item_next_res = _resolve_first_path(list_item_struct, ("pxNext",))
    item_next_off = item_next_res[0] if item_next_res else ptr_size
    item_owner_res = _resolve_first_path(list_item_struct, ("pvOwner",))
    item_owner_off = item_owner_res[0] if item_owner_res else (3 * ptr_size)

    tasks: list[RtosTaskInfo] = []
    hazards: list[RtosHazard] = []
    visited_tcbs: set[int] = set()
    visited_nodes: set[int] = set()
    truncated = False

    def read_tcb(tcb_addr: int, default_state: Literal["RUNNING", "READY", "BLOCKED", "SUSPENDED", "UNKNOWN"], list_name: str) -> None:
        nonlocal truncated
        if tcb_addr in visited_tcbs:
            return
        if len(tasks) >= max_tasks:
            truncated = True
            return
        if not _is_valid_ptr(tcb_addr, ptr_size):
            hazards.append(
                RtosHazard(
                    severity="warning",
                    code="INVALID_POINTER",
                    task_name=list_name,
                    message=f"Invalid TCB pointer {_format_hex(tcb_addr, ptr_size)} in {list_name}",
                )
            )
            return
        visited_tcbs.add(tcb_addr)

        try:
            buf = probe.read_memory(tcb_addr, tcb_read_size)
        except ProbeError as exc:
            hazards.append(
                RtosHazard(
                    severity="warning",
                    code="INVALID_POINTER",
                    task_name=list_name,
                    message=f"Failed to read TCB at {_format_hex(tcb_addr, ptr_size)}: {exc}",
                )
            )
            return

        name = _field_string(
            probe, buf, tcb_struct, ("pcTaskName",), 52 if ptr_size == 4 else None, 16, ptr_size, endian
        ) or f"task_{_format_hex(tcb_addr, ptr_size)}"
        sp_val = _field_ptr(buf, tcb_struct, ("pxTopOfStack",), 0, ptr_size, endian)
        prio = _field_int(buf, tcb_struct, ("uxPriority",), 44 if ptr_size == 4 else None, ptr_size, endian, signed=False)
        stack_base = _field_ptr(buf, tcb_struct, ("pxStack",), 48 if ptr_size == 4 else None, ptr_size, endian)
        stack_end = _field_ptr(buf, tcb_struct, ("pxEndOfStack",), None, ptr_size, endian)

        stack_size: int | None = None
        if stack_base and stack_end and stack_end > stack_base:
            stack_size = stack_end - stack_base

        free_min: int | None = None
        used_pct: float | None = None
        if check_stack and stack_base is not None and stack_size is not None:
            free_min, used_pct = _scan_stack_watermark(
                probe,
                rtos="freertos",
                task_name=name,
                stack_base=stack_base,
                stack_size=stack_size,
                ptr_size=ptr_size,
                hazards=hazards,
            )

        state: Literal["RUNNING", "READY", "BLOCKED", "SUSPENDED", "UNKNOWN"] = (
            "RUNNING" if (current_tcb and tcb_addr == current_tcb) else default_state
        )

        tasks.append(
            RtosTaskInfo(
                name=name,
                tcb_address=_format_hex(tcb_addr, ptr_size),
                state=state,
                priority=prio,
                sp=_format_hex(sp_val, ptr_size) if sp_val else None,
                stack_base=_format_hex(stack_base, ptr_size) if stack_base else None,
                stack_end=_format_hex(stack_end, ptr_size) if stack_end else None,
                stack_size_bytes=stack_size,
                stack_free_min_bytes=free_min,
                stack_used_peak_pct=used_pct,
                extra={"list": list_name},
            )
        )

    def traverse_list(list_addr: int, default_state: Literal["RUNNING", "READY", "BLOCKED", "SUSPENDED", "UNKNOWN"], list_name: str) -> None:
        nonlocal truncated
        if not _is_valid_ptr(list_addr, ptr_size):
            hazards.append(
                RtosHazard(
                    severity="warning",
                    code="INVALID_POINTER",
                    task_name=list_name,
                    message=f"Invalid List_t pointer {_format_hex(list_addr, ptr_size)} for {list_name}",
                )
            )
            return

        try:
            list_buf = probe.read_memory(list_addr, list_size)
        except ProbeError as exc:
            hazards.append(
                RtosHazard(
                    severity="warning",
                    code="INVALID_POINTER",
                    task_name=list_name,
                    message=f"Failed to read List_t at {_format_hex(list_addr, ptr_size)}: {exc}",
                )
            )
            return

        ux_items = _field_int(list_buf, list_struct, ("uxNumberOfItems",), 0, ptr_size, endian, signed=False)
        first_node = _decode_int_bytes(list_buf, xlistend_next_off, ptr_size, endian, signed=False) or 0
        end_addr = list_addr + xlistend_off

        if (ux_items == 0) and (first_node == 0 or first_node == end_addr):
            return
        if first_node == end_addr:
            return
        if first_node == 0:
            hazards.append(
                RtosHazard(
                    severity="warning",
                    code="INVALID_POINTER",
                    task_name=list_name,
                    message=f"{list_name} reports uxNumberOfItems={ux_items} but xListEnd.pxNext is NULL",
                )
            )
            return

        cur = first_node
        while cur != end_addr:
            if len(tasks) >= max_tasks:
                truncated = True
                break
            if not _is_valid_ptr(cur, ptr_size):
                hazards.append(
                    RtosHazard(
                        severity="warning",
                        code="INVALID_POINTER",
                        task_name=list_name,
                        message=f"Invalid ListItem_t pointer {_format_hex(cur, ptr_size)} in {list_name}",
                    )
                )
                break
            if cur in visited_nodes:
                hazards.append(
                    RtosHazard(
                        severity="critical",
                        code="CYCLE_DETECTED",
                        task_name=list_name,
                        message=f"Cycle detected in {list_name} at ListItem_t {_format_hex(cur, ptr_size)}",
                    )
                )
                break
            visited_nodes.add(cur)

            try:
                item_buf = probe.read_memory(cur, list_item_size)
            except ProbeError as exc:
                hazards.append(
                    RtosHazard(
                        severity="warning",
                        code="INVALID_POINTER",
                        task_name=list_name,
                        message=f"Failed to read ListItem_t at {_format_hex(cur, ptr_size)}: {exc}",
                    )
                )
                break

            owner = _decode_int_bytes(item_buf, item_owner_off, ptr_size, endian, signed=False) or 0
            nxt = _decode_int_bytes(item_buf, item_next_off, ptr_size, endian, signed=False) or 0

            if owner == 0 or not _is_valid_ptr(owner, ptr_size):
                hazards.append(
                    RtosHazard(
                        severity="warning",
                        code="INVALID_POINTER",
                        task_name=list_name,
                        message=f"Invalid pvOwner {_format_hex(owner, ptr_size)} in ListItem_t {_format_hex(cur, ptr_size)}",
                    )
                )
            else:
                read_tcb(owner, default_state, list_name)

            cur = nxt

    # 1. pxReadyTasksLists[0..N-1]
    if ready_info is not None:
        ready_addr, ready_sym_size, ready_type = ready_info
        num_prios = 1
        stride = list_size
        if ready_type is not None:
            r_resolved = ready_type.resolve()
            if isinstance(r_resolved, ArrayType) and r_resolved.length > 0:
                num_prios = min(r_resolved.length, 256)
                stride = r_resolved.element_stride() or list_size
        elif ready_sym_size > 0 and list_size > 0:
            num_prios = min(max(ready_sym_size // list_size, 1), 256)
        for prio_idx in range(num_prios):
            if truncated:
                break
            traverse_list(ready_addr + prio_idx * stride, "READY", f"pxReadyTasksLists[{prio_idx}]")

    # 2. pxDelayedTaskList (解引用指针)
    if delayed_info is not None and not truncated:
        delayed_ptr = _read_global_uint(probe, dwarf_index, sym_map, "pxDelayedTaskList", ptr_size, endian, is_pointer=True)
        if delayed_ptr:
            traverse_list(delayed_ptr, "BLOCKED", "pxDelayedTaskList")

    # 3. pxOverflowDelayedTaskList (解引用指针)
    if _resolve_global(dwarf_index, sym_map, "pxOverflowDelayedTaskList") is not None and not truncated:
        ovf_ptr = _read_global_uint(probe, dwarf_index, sym_map, "pxOverflowDelayedTaskList", ptr_size, endian, is_pointer=True)
        if ovf_ptr:
            traverse_list(ovf_ptr, "BLOCKED", "pxOverflowDelayedTaskList")

    # 4. xSuspendedTaskList (若存在，直接为 List_t 实体)
    susp_info = _resolve_global(dwarf_index, sym_map, "xSuspendedTaskList")
    if susp_info is not None and not truncated:
        traverse_list(susp_info[0], "SUSPENDED", "xSuspendedTaskList")

    # 5. 若 pxCurrentTCB 未出现在各链表中，补充读取当前运行任务
    if current_tcb and _is_valid_ptr(current_tcb, ptr_size) and current_tcb not in visited_tcbs and not truncated:
        read_tcb(current_tcb, "RUNNING", "pxCurrentTCB")

    current_name: str | None = None
    current_hex = _format_hex(current_tcb, ptr_size) if current_tcb else None
    if current_hex:
        for t in tasks:
            if t.tcb_address == current_hex:
                current_name = t.name
                break

    return RtosReport(
        rtos="freertos",
        elf_path=str(elf_path),
        tick_count=tick_count,
        scheduler_running=sched_running,
        current_task=current_name,
        current_tcb_address=current_hex,
        reported_task_count=num_tasks,
        tasks=tasks,
        hazards=hazards,
        pointer_size=ptr_size,
        dwarf_resolved=dwarf_resolved,
        truncated=truncated,
    )


# ==========================================================================
# Zephyr 适配器
# ==========================================================================


def _inspect_zephyr(
    probe: DebugProbe,
    elf_path: Path,
    dwarf_index: DwarfIndex | None,
    sym_map: dict[str, ElfSymbol],
    *,
    ptr_size: int,
    endian: Literal["<", ">"],
    max_tasks: int,
    check_stack: bool,
) -> RtosReport:
    kernel_info = _resolve_global(dwarf_index, sym_map, "_kernel")
    if kernel_info is None:
        raise RtosError("Zephyr symbol '_kernel' not found in ELF")
    kernel_addr, kernel_sym_size, kernel_type = kernel_info

    kernel_struct: StructType | None = None
    if kernel_type is not None:
        kt = kernel_type.resolve()
        if isinstance(kt, StructType) and kt.members:
            kernel_struct = kt
    if kernel_struct is None:
        kernel_struct = _find_struct_type(dwarf_index, ("z_kernel", "struct z_kernel"))

    thread_struct = _find_struct_type(dwarf_index, ("k_thread", "struct k_thread"))
    if thread_struct is None and kernel_struct is not None:
        for p in ("cpus[0].current", "current", "threads", "ready_q.cache"):
            res = _resolve_first_path(kernel_struct, (p,))
            if res is not None:
                thread_struct = _unwrap_pointee_struct(res[1])
                if thread_struct is not None:
                    break

    dwarf_resolved = kernel_struct is not None and thread_struct is not None
    kernel_read_sz = (
        kernel_struct.byte_size
        if (kernel_struct and 0 < kernel_struct.byte_size <= 4096)
        else (kernel_sym_size if 0 < kernel_sym_size <= 4096 else 64)
    )
    thread_read_sz = max(
        thread_struct.byte_size if (thread_struct and 0 < thread_struct.byte_size <= 4096) else 128,
        64,
    )

    hazards: list[RtosHazard] = []
    tasks: list[RtosTaskInfo] = []
    visited_tcbs: set[int] = set()
    truncated = False

    try:
        kernel_buf = probe.read_memory(kernel_addr, kernel_read_sz)
    except ProbeError as exc:
        raise RtosError(f"Failed to read Zephyr _kernel at {_format_hex(kernel_addr, ptr_size)}: {exc}") from exc

    current_tcb = _field_ptr(
        kernel_buf,
        kernel_struct,
        ("cpus[0].current", "current"),
        8 if ptr_size == 4 else None,
        ptr_size,
        endian,
    )
    threads_head = _field_ptr(kernel_buf, kernel_struct, ("threads",), None, ptr_size, endian)
    ready_cache = _field_ptr(kernel_buf, kernel_struct, ("ready_q.cache",), None, ptr_size, endian)
    ready_q_direct: int | None = None
    ready_q_res = _resolve_first_path(kernel_struct, ("ready_q",))
    if ready_q_res is not None and isinstance(ready_q_res[1].resolve(), PointerType):
        ready_q_direct = _decode_int_bytes(kernel_buf, ready_q_res[0], ptr_size, endian, signed=False)

    # 若 _kernel.ready_q.runq 为 sys_dlist_t（head/tail），收集其中就绪线程指针
    ready_tcbs: set[int] = set()
    if ready_cache and _is_valid_ptr(ready_cache, ptr_size):
        ready_tcbs.add(ready_cache)
    if ready_q_direct and _is_valid_ptr(ready_q_direct, ptr_size):
        ready_tcbs.add(ready_q_direct)

    runq_res = _resolve_first_path(kernel_struct, ("ready_q.runq",))
    if runq_res is not None:
        runq_off, runq_type = runq_res
        runq_resolved = runq_type.resolve()
        if isinstance(runq_resolved, StructType):
            head_res = _resolve_first_path(runq_resolved, ("head",))
            if head_res is not None:
                dlist_addr = kernel_addr + runq_off
                qnode_res = _resolve_first_path(thread_struct, ("base.qnode_dlist",))
                qnode_off = qnode_res[0] if qnode_res else 0
                cur_dnode = _decode_int_bytes(kernel_buf, runq_off + head_res[0], ptr_size, endian, signed=False) or 0
                seen_dnodes: set[int] = {dlist_addr}
                while cur_dnode and cur_dnode != dlist_addr and len(seen_dnodes) <= max_tasks + 1:
                    if not _is_valid_ptr(cur_dnode, ptr_size):
                        hazards.append(
                            RtosHazard(
                                severity="warning",
                                code="INVALID_POINTER",
                                task_name="ready_q.runq",
                                message=f"Invalid dlist node {_format_hex(cur_dnode, ptr_size)} in ready_q.runq",
                            )
                        )
                        break
                    if cur_dnode in seen_dnodes:
                        hazards.append(
                            RtosHazard(
                                severity="critical",
                                code="CYCLE_DETECTED",
                                task_name="ready_q.runq",
                                message=f"Cycle detected in ready_q.runq at {_format_hex(cur_dnode, ptr_size)}",
                            )
                        )
                        break
                    seen_dnodes.add(cur_dnode)
                    ready_tcbs.add(cur_dnode - qnode_off)
                    try:
                        nxt_bytes = probe.read_memory(cur_dnode, ptr_size)
                        cur_dnode = _decode_int_bytes(nxt_bytes, 0, ptr_size, endian, signed=False) or 0
                    except ProbeError:
                        break

    def walk_thread_chain(start_addr: int | None, chain_name: str) -> None:
        nonlocal truncated
        cur = start_addr or 0
        while cur != 0:
            if len(tasks) >= max_tasks:
                truncated = True
                break
            if not _is_valid_ptr(cur, ptr_size):
                hazards.append(
                    RtosHazard(
                        severity="warning",
                        code="INVALID_POINTER",
                        task_name=chain_name,
                        message=f"Invalid k_thread pointer {_format_hex(cur, ptr_size)} in {chain_name}",
                    )
                )
                break
            if cur in visited_tcbs:
                # 仅在从该链表内部再次回到已访问节点时判定环路；
                # 若是跨入口（如 current 已经在 threads 链里遍历过），只需停止追踪
                if cur in chain_visited:
                    hazards.append(
                        RtosHazard(
                            severity="critical",
                            code="CYCLE_DETECTED",
                            task_name=chain_name,
                            message=f"Cycle detected in {chain_name} at k_thread {_format_hex(cur, ptr_size)}",
                        )
                    )
                break
            chain_visited.add(cur)
            visited_tcbs.add(cur)

            try:
                buf = probe.read_memory(cur, thread_read_sz)
            except ProbeError as exc:
                hazards.append(
                    RtosHazard(
                        severity="warning",
                        code="INVALID_POINTER",
                        task_name=chain_name,
                        message=f"Failed to read k_thread at {_format_hex(cur, ptr_size)}: {exc}",
                    )
                )
                break

            name = _field_string(
                probe, buf, thread_struct, ("name",), None, 32, ptr_size, endian
            ) or f"thread_{_format_hex(cur, ptr_size)}"
            thread_state = _field_int(buf, thread_struct, ("base.thread_state", "thread_state"), 12 if ptr_size == 4 else None, 1, endian, signed=False)
            prio = _field_int(buf, thread_struct, ("base.prio", "prio"), 14 if ptr_size == 4 else None, 1, endian, signed=True)
            sp_val = _field_ptr(buf, thread_struct, ("callee_saved.psp", "callee_saved.sp", "psp", "sp"), None, ptr_size, endian)
            stack_base = _field_ptr(buf, thread_struct, ("stack_info.start", "stack_start"), None, ptr_size, endian)
            stack_size = _field_int(buf, thread_struct, ("stack_info.size", "stack_size"), None, ptr_size, endian, signed=False)
            nxt = _field_ptr(buf, thread_struct, ("next_thread",), None, ptr_size, endian) or 0

            stack_end = (stack_base + stack_size) if (stack_base and stack_size and stack_size > 0) else None

            free_min: int | None = None
            used_pct: float | None = None
            if check_stack and stack_base is not None and stack_size is not None:
                free_min, used_pct = _scan_stack_watermark(
                    probe,
                    rtos="zephyr",
                    task_name=name,
                    stack_base=stack_base,
                    stack_size=stack_size,
                    ptr_size=ptr_size,
                    hazards=hazards,
                )

            # Zephyr thread_state bitmask:
            # 0x01=_THREAD_DUMMY, 0x02=_THREAD_PENDING, 0x04=_THREAD_PRESTART,
            # 0x08=_THREAD_DEAD, 0x10=_THREAD_SUSPENDED, 0x80=_THREAD_QUEUED
            state: Literal["RUNNING", "READY", "BLOCKED", "SUSPENDED", "UNKNOWN"]
            if current_tcb and cur == current_tcb:
                state = "RUNNING"
            elif thread_state is not None:
                if thread_state & 0x10:
                    state = "SUSPENDED"
                elif thread_state & 0x02:
                    state = "BLOCKED"
                elif thread_state & 0x80:
                    state = "READY"
                elif thread_state & (0x01 | 0x04 | 0x08):
                    state = "SUSPENDED"
                elif cur in ready_tcbs:
                    state = "READY"
                else:
                    state = "UNKNOWN"
            else:
                state = "READY" if cur in ready_tcbs else "UNKNOWN"

            extra: dict[str, Any] = {}
            if thread_state is not None:
                extra["thread_state"] = f"0x{thread_state:02X}"

            tasks.append(
                RtosTaskInfo(
                    name=name,
                    tcb_address=_format_hex(cur, ptr_size),
                    state=state,
                    priority=prio,
                    sp=_format_hex(sp_val, ptr_size) if sp_val else None,
                    stack_base=_format_hex(stack_base, ptr_size) if stack_base else None,
                    stack_end=_format_hex(stack_end, ptr_size) if stack_end else None,
                    stack_size_bytes=stack_size,
                    stack_free_min_bytes=free_min,
                    stack_used_peak_pct=used_pct,
                    extra=extra,
                )
            )
            cur = nxt

    chain_visited: set[int] = set()
    if threads_head:
        walk_thread_chain(threads_head, "_kernel.threads")

    # 兜底补充不在 _kernel.threads 中的 ready_q / current 线程
    for extra_ptr, label in [(ready_q_direct, "_kernel.ready_q"), (ready_cache, "_kernel.ready_q.cache"), (current_tcb, "_kernel.current")]:
        if extra_ptr and extra_ptr not in visited_tcbs and not truncated:
            chain_visited = set()
            walk_thread_chain(extra_ptr, label)

    for rtcb in sorted(ready_tcbs):
        if rtcb not in visited_tcbs and not truncated:
            chain_visited = set()
            walk_thread_chain(rtcb, "_kernel.ready_q.runq")

    current_name: str | None = None
    current_hex = _format_hex(current_tcb, ptr_size) if current_tcb else None
    if current_hex:
        for t in tasks:
            if t.tcb_address == current_hex:
                current_name = t.name
                break

    return RtosReport(
        rtos="zephyr",
        elf_path=str(elf_path),
        tick_count=None,
        scheduler_running=True if current_tcb else None,
        current_task=current_name,
        current_tcb_address=current_hex,
        reported_task_count=len(tasks),
        tasks=tasks,
        hazards=hazards,
        pointer_size=ptr_size,
        dwarf_resolved=dwarf_resolved,
        truncated=truncated,
    )


# ==========================================================================
# RT-Thread 适配器
# ==========================================================================


def _inspect_rtthread(
    probe: DebugProbe,
    elf_path: Path,
    dwarf_index: DwarfIndex | None,
    sym_map: dict[str, ElfSymbol],
    *,
    ptr_size: int,
    endian: Literal["<", ">"],
    max_tasks: int,
    check_stack: bool,
) -> RtosReport:
    if _resolve_global(dwarf_index, sym_map, "rt_current_thread") is None:
        raise RtosError("RT-Thread symbol 'rt_current_thread' not found in ELF")
    container_info = _resolve_global(dwarf_index, sym_map, "rt_object_container")
    if container_info is None:
        raise RtosError("RT-Thread symbol 'rt_object_container' not found in ELF")

    current_tcb = _read_global_uint(probe, dwarf_index, sym_map, "rt_current_thread", ptr_size, endian, is_pointer=True)
    rt_tick = _read_global_uint(probe, dwarf_index, sym_map, "rt_tick", ptr_size, endian, default_size=4)

    cur_info = _resolve_global(dwarf_index, sym_map, "rt_current_thread")
    thread_struct = _unwrap_pointee_struct(cur_info[2] if cur_info else None) or _find_struct_type(
        dwarf_index, ("rt_thread", "struct rt_thread")
    )
    obj_info_struct = _unwrap_pointee_struct(container_info[2]) or _find_struct_type(
        dwarf_index, ("rt_object_information", "struct rt_object_information")
    )
    list_node_struct = _find_struct_type(dwarf_index, ("rt_list_node", "rt_list_t", "struct rt_list_node"))
    if list_node_struct is None and obj_info_struct is not None:
        obj_list_res = _resolve_first_path(obj_info_struct, ("object_list",))
        if obj_list_res is not None:
            r = obj_list_res[1].resolve()
            if isinstance(r, StructType):
                list_node_struct = r

    dwarf_resolved = thread_struct is not None and obj_info_struct is not None

    # 32 位 Cortex-M 标准偏移兜底
    obj_list_res = _resolve_first_path(obj_info_struct, ("object_list",))
    obj_list_off = obj_list_res[0] if obj_list_res else (4 if ptr_size == 4 else ptr_size)
    head_addr = container_info[0] + obj_list_off

    node_next_res = _resolve_first_path(list_node_struct, ("next",))
    node_next_off = node_next_res[0] if node_next_res else 0
    node_size = list_node_struct.byte_size if (list_node_struct and list_node_struct.byte_size > 0) else (2 * ptr_size)

    thread_list_res = _resolve_first_path(thread_struct, ("list", "parent.list"))
    thread_list_off = thread_list_res[0] if thread_list_res else (12 if ptr_size == 4 else 16)
    thread_read_sz = max(
        thread_struct.byte_size if (thread_struct and 0 < thread_struct.byte_size <= 4096) else 128,
        80,
    )

    tasks: list[RtosTaskInfo] = []
    hazards: list[RtosHazard] = []
    visited_nodes: set[int] = set()
    visited_tcbs: set[int] = set()
    truncated = False

    def read_rt_thread(tcb_addr: int, source_label: str) -> None:
        nonlocal truncated
        if tcb_addr in visited_tcbs:
            return
        if len(tasks) >= max_tasks:
            truncated = True
            return
        if not _is_valid_ptr(tcb_addr, ptr_size):
            hazards.append(
                RtosHazard(
                    severity="warning",
                    code="INVALID_POINTER",
                    task_name=source_label,
                    message=f"Invalid rt_thread pointer {_format_hex(tcb_addr, ptr_size)}",
                )
            )
            return
        visited_tcbs.add(tcb_addr)

        try:
            buf = probe.read_memory(tcb_addr, thread_read_sz)
        except ProbeError as exc:
            hazards.append(
                RtosHazard(
                    severity="warning",
                    code="INVALID_POINTER",
                    task_name=source_label,
                    message=f"Failed to read rt_thread at {_format_hex(tcb_addr, ptr_size)}: {exc}",
                )
            )
            return

        name = _field_string(
            probe, buf, thread_struct, ("name", "parent.name"), 0, 8, ptr_size, endian
        ) or f"thread_{_format_hex(tcb_addr, ptr_size)}"
        sp_val = _field_ptr(buf, thread_struct, ("sp",), 28 if ptr_size == 4 else None, ptr_size, endian)
        stack_base = _field_ptr(buf, thread_struct, ("stack_addr",), 40 if ptr_size == 4 else None, ptr_size, endian)
        stack_size = _field_int(buf, thread_struct, ("stack_size",), 44 if ptr_size == 4 else None, 4, endian, signed=False)
        prio = _field_int(buf, thread_struct, ("current_priority",), 64 if ptr_size == 4 else None, 1, endian, signed=False)
        stat = _field_int(buf, thread_struct, ("stat",), 66 if ptr_size == 4 else None, 1, endian, signed=False)

        stack_end = (stack_base + stack_size) if (stack_base and stack_size and stack_size > 0) else None

        free_min: int | None = None
        used_pct: float | None = None
        if check_stack and stack_base is not None and stack_size is not None:
            free_min, used_pct = _scan_stack_watermark(
                probe,
                rtos="rtthread",
                task_name=name,
                stack_base=stack_base,
                stack_size=stack_size,
                ptr_size=ptr_size,
                hazards=hazards,
            )

        # RT-Thread stat 低 3 位：0x00=INIT, 0x01=READY, 0x02=SUSPEND, 0x03=RUNNING, 0x04=CLOSE
        state: Literal["RUNNING", "READY", "BLOCKED", "SUSPENDED", "UNKNOWN"]
        if current_tcb and tcb_addr == current_tcb:
            state = "RUNNING"
        elif stat is not None:
            low_stat = stat & 0x07
            if low_stat == 0x03:
                state = "RUNNING"
            elif low_stat == 0x01:
                state = "READY"
            elif low_stat == 0x02:
                state = "SUSPENDED"
            else:
                state = "UNKNOWN"
        else:
            state = "UNKNOWN"

        extra: dict[str, Any] = {}
        if stat is not None:
            extra["stat"] = f"0x{stat:02X}"

        tasks.append(
            RtosTaskInfo(
                name=name,
                tcb_address=_format_hex(tcb_addr, ptr_size),
                state=state,
                priority=prio,
                sp=_format_hex(sp_val, ptr_size) if sp_val else None,
                stack_base=_format_hex(stack_base, ptr_size) if stack_base else None,
                stack_end=_format_hex(stack_end, ptr_size) if stack_end else None,
                stack_size_bytes=stack_size,
                stack_free_min_bytes=free_min,
                stack_used_peak_pct=used_pct,
                extra=extra,
            )
        )

    # 读取 rt_object_container[0].object_list 双向循环链表头
    try:
        head_buf = probe.read_memory(head_addr, node_size)
        cur = _decode_int_bytes(head_buf, node_next_off, ptr_size, endian, signed=False) or 0
    except ProbeError as exc:
        hazards.append(
            RtosHazard(
                severity="warning",
                code="INVALID_POINTER",
                task_name="rt_object_container[0]",
                message=f"Failed to read object_list head at {_format_hex(head_addr, ptr_size)}: {exc}",
            )
        )
        cur = 0

    if cur == 0:
        hazards.append(
            RtosHazard(
                severity="warning",
                code="INVALID_POINTER",
                task_name="rt_object_container[0]",
                message="rt_object_container[0].object_list.next is NULL",
            )
        )
    else:
        while cur != head_addr:
            if len(tasks) >= max_tasks:
                truncated = True
                break
            if not _is_valid_ptr(cur, ptr_size):
                hazards.append(
                    RtosHazard(
                        severity="warning",
                        code="INVALID_POINTER",
                        task_name="rt_object_container[0]",
                        message=f"Invalid rt_list_t node pointer {_format_hex(cur, ptr_size)}",
                    )
                )
                break
            if cur in visited_nodes:
                hazards.append(
                    RtosHazard(
                        severity="critical",
                        code="CYCLE_DETECTED",
                        task_name="rt_object_container[0]",
                        message=f"Cycle detected in rt_object_container[0].object_list at {_format_hex(cur, ptr_size)}",
                    )
                )
                break
            visited_nodes.add(cur)

            tcb_addr = cur - thread_list_off
            read_rt_thread(tcb_addr, "rt_object_container[0]")

            try:
                node_buf = probe.read_memory(cur, node_size)
                cur = _decode_int_bytes(node_buf, node_next_off, ptr_size, endian, signed=False) or 0
            except ProbeError as exc:
                hazards.append(
                    RtosHazard(
                        severity="warning",
                        code="INVALID_POINTER",
                        task_name="rt_object_container[0]",
                        message=f"Failed to read rt_list_t node at {_format_hex(cur, ptr_size)}: {exc}",
                    )
                )
                break

    if current_tcb and _is_valid_ptr(current_tcb, ptr_size) and current_tcb not in visited_tcbs and not truncated:
        read_rt_thread(current_tcb, "rt_current_thread")

    current_name: str | None = None
    current_hex = _format_hex(current_tcb, ptr_size) if current_tcb else None
    if current_hex:
        for t in tasks:
            if t.tcb_address == current_hex:
                current_name = t.name
                break

    return RtosReport(
        rtos="rtthread",
        elf_path=str(elf_path),
        tick_count=rt_tick,
        scheduler_running=True if current_tcb else None,
        current_task=current_name,
        current_tcb_address=current_hex,
        reported_task_count=len(tasks),
        tasks=tasks,
        hazards=hazards,
        pointer_size=ptr_size,
        dwarf_resolved=dwarf_resolved,
        truncated=truncated,
    )


# ==========================================================================
# 统一对外入口
# ==========================================================================


def inspect_rtos(
    probe: DebugProbe,
    elf_path: str | os.PathLike,
    *,
    rtos: str = "auto",
    max_tasks: int = 64,
    check_stack: bool = True,
) -> RtosReport:
    """无停机透视目标固件的 RTOS 任务状态与栈高水位（绝不调用 ``probe.halt()``）。

    Args:
        probe: 已连接的 DebugProbe 实例。
        elf_path: 目标固件 .elf 文件路径。
        rtos: ``"auto"`` | ``"freertos"`` | ``"zephyr"`` | ``"rtthread"``。
        max_tasks: 最大遍历任务数（默认 64），防止异常内存导致无限遍历。
        check_stack: 是否执行全栈高水位水印字节扫描（单次上限 64 KB）。
    """
    if max_tasks <= 0:
        raise ValueError(f"max_tasks must be > 0, got {max_tasks}")

    p = Path(elf_path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"ELF file not found: {p}")

    rtos_norm = rtos.strip().lower()
    if rtos_norm == "auto":
        detected = detect_rtos(p)
        if detected is None:
            raise RtosError(f"Unable to auto-detect RTOS (FreeRTOS / Zephyr / RT-Thread) in {p}")
        rtos_norm = detected
    elif rtos_norm not in _SUPPORTED_RTOS:
        raise RtosError(f"Unsupported RTOS {rtos!r}; expected one of auto, {', '.join(_SUPPORTED_RTOS)}")

    sym_map = _build_symbol_map(p)
    dwarf_index = _safe_load_dwarf(p)

    # 从 ELF 头/DWARF 确定指针宽度与字节序
    if dwarf_index is not None:
        ptr_size = dwarf_index.pointer_size
        endian = dwarf_index.endian
    else:
        raw_hdr = p.read_bytes()[:6]
        if len(raw_hdr) < 6 or raw_hdr[:4] != b"\x7fELF":
            raise ElfError(f"Not a valid ELF file: {p}")
        ptr_size = 4 if raw_hdr[4] == 1 else 8
        endian = "<" if raw_hdr[5] == 1 else ">"

    if rtos_norm == "freertos":
        return _inspect_freertos(
            probe,
            p,
            dwarf_index,
            sym_map,
            ptr_size=ptr_size,
            endian=endian,
            max_tasks=max_tasks,
            check_stack=check_stack,
        )
    if rtos_norm == "zephyr":
        return _inspect_zephyr(
            probe,
            p,
            dwarf_index,
            sym_map,
            ptr_size=ptr_size,
            endian=endian,
            max_tasks=max_tasks,
            check_stack=check_stack,
        )
    return _inspect_rtthread(
        probe,
        p,
        dwarf_index,
        sym_map,
        ptr_size=ptr_size,
        endian=endian,
        max_tasks=max_tasks,
        check_stack=check_stack,
    )
