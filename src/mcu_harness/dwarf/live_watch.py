"""基于 DWARF 类型系统的无停机实时变量读取（Live Watch）与高频趋势采样模块。

核心原则：
    * 全程禁止调用 `probe.halt()` / `probe.resume()`，对运行中的电机/电源控制回路零侵入。
    * 结构体/数组/位域路径先在宿主机侧根据 DWARF 静态计算出精确 `(address, byte_size)`，
      单次调用 `probe.read_memory(addr, size)` 取回整块字节后再本地递归解包，最小化 SWD/JTAG 总线事务。
"""

from __future__ import annotations

import os
import re
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from mcu_harness.probe.base import DebugProbe, ProbeError

from .parser import (
    ArrayType,
    BaseType,
    DwarfIndex,
    DwarfType,
    EnumType,
    PointerType,
    StructType,
    VoidType,
    load_dwarf_index,
)


class LiveWatchError(ValueError):
    """实时变量寻址表达式解析或类型不支持错误。"""


@dataclass(frozen=True)
class ResolvedTarget:
    """表达式静态寻址结果（对应一次连续内存读取窗口）。"""

    expr: str
    root_name: str
    address: int
    read_size: int
    dwarf_type: DwarfType
    endian: Literal["<", ">"]
    pointer_size: int
    bit_size: int | None = None
    rel_bit_offset: int | None = None  # 相对于 address 首字节的位偏移 (0..7)


@dataclass
class VariableValue:
    """变量/表达式实时读取与类型化解码结果。"""

    expr: str
    address: int
    byte_size: int
    type_name: str
    kind: str  # "int" | "float" | "bool" | "enum" | "struct" | "union" | "array" | "pointer" | "void"
    value: Any
    raw_value: int | float | bool | None = None
    hex_value: str | None = None
    display: str = ""
    enum_name: str | None = None
    string_value: str | None = None
    is_null: bool = False
    bit_size: int | None = None
    bit_offset: int | None = None
    fields: dict[str, VariableValue] = field(default_factory=dict)
    elements: list[VariableValue] = field(default_factory=list)
    deref: VariableValue | None = None
    deref_error: str | None = None
    truncated: bool = False
    pointer_size: int = 4

    @property
    def address_hex(self) -> str:
        width = 16 if (self.pointer_size > 4 or self.address > 0xFFFFFFFF) else 8
        return f"0x{self.address:0{width}X}"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "expr": self.expr,
            "address": self.address,
            "address_hex": self.address_hex,
            "byte_size": self.byte_size,
            "type_name": self.type_name,
            "kind": self.kind,
            "value": self.value,
            "display": self.display,
        }
        if self.raw_value is not None:
            out["raw_value"] = self.raw_value
        if self.hex_value is not None:
            out["hex"] = self.hex_value
        if self.enum_name is not None:
            out["enum"] = self.enum_name
        if self.string_value is not None:
            out["string"] = self.string_value
        if self.bit_size is not None:
            out["bit_size"] = self.bit_size
            out["bit_offset"] = self.bit_offset
        if self.kind == "pointer":
            out["is_null"] = self.is_null
            if self.deref is not None:
                out["deref"] = self.deref.to_dict()
            if self.deref_error is not None:
                out["deref_error"] = self.deref_error
        if self.fields:
            out["fields"] = {k: v.to_dict() for k, v in self.fields.items()}
        if self.elements:
            out["elements"] = [e.to_dict() for e in self.elements]
        if self.truncated:
            out["truncated"] = True
        return out

    def to_text(self, indent: int = 0) -> str:
        pad = "  " * indent
        header = f"{pad}{self.expr} ({self.type_name}) @ {self.address_hex}"
        if self.kind in ("struct", "union") and not self.truncated:
            lines = [f"{header} = {{"]
            for fval in self.fields.values():
                lines.append(fval.to_text(indent + 1))
            lines.append(f"{pad}}}")
            return "\n".join(lines)
        if self.kind == "array" and self.string_value is not None:
            return f"{header} = {self.string_value!r} ({self.value})"
        if self.kind == "pointer" and self.deref is not None:
            return f"{header} = {self.display} ->\n{self.deref.to_text(indent + 1)}"
        return f"{header} = {self.display}"


@dataclass(frozen=True)
class SamplePoint:
    """单次高频趋势采样点。"""

    index: int
    timestamp_ms: float
    value: int | float
    display: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "index": self.index,
            "timestamp_ms": self.timestamp_ms,
            "value": self.value,
        }
        if self.display is not None:
            d["display"] = self.display
        return d


@dataclass
class VariableSampleReport:
    """标量变量高频非侵入采样统计报告。"""

    expr: str
    address: int
    address_hex: str
    type_name: str
    kind: str
    samples_requested: int
    sample_count: int
    interval_ms: float
    duration_ms: float
    min: int | float
    max: int | float
    mean: float
    first: int | float
    last: int | float
    delta: int | float
    changing: bool
    unique_count: int
    points: list[SamplePoint]

    def to_dict(self) -> dict[str, Any]:
        return {
            "expr": self.expr,
            "address": self.address,
            "address_hex": self.address_hex,
            "type_name": self.type_name,
            "kind": self.kind,
            "samples_requested": self.samples_requested,
            "sample_count": self.sample_count,
            "interval_ms": self.interval_ms,
            "duration_ms": self.duration_ms,
            "min": self.min,
            "max": self.max,
            "mean": self.mean,
            "first": self.first,
            "last": self.last,
            "delta": self.delta,
            "changing": self.changing,
            "unique_count": self.unique_count,
            "points": [p.to_dict() for p in self.points],
        }

    def to_text(self) -> str:
        state = "CHANGING" if self.changing else "STATIC"
        lines = [
            f"Sample Report: {self.expr} ({self.type_name}) @ {self.address_hex} [{state}]",
            f"  samples={self.sample_count}  duration={self.duration_ms:.2f}ms  unique={self.unique_count}",
            f"  first={self.first}  last={self.last}  delta={self.delta}  "
            f"min={self.min}  max={self.max}  mean={self.mean:.4g}",
        ]
        preview = ", ".join(f"{p.timestamp_ms:.1f}ms:{p.display or p.value}" for p in self.points[:12])
        if len(self.points) > 12:
            preview += f", ... (+{len(self.points) - 12} more)"
        lines.append(f"  series: [{preview}]")
        return "\n".join(lines)


# ==========================================================================
# 表达式词法分析与寻址计算
# ==========================================================================

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)*")
_INDEX_RE = re.compile(r"\[\s*([+-]?(?:0[xX][0-9A-Fa-f]+|\d+))\s*\]")


@dataclass(frozen=True)
class _FieldOp:
    name: str
    via_pointer: bool = False


@dataclass(frozen=True)
class _IndexOp:
    index: int


def _parse_expression(expr: str) -> tuple[str, list[_FieldOp | _IndexOp]]:
    s = expr.strip()
    if not s:
        raise LiveWatchError("Watch expression must not be empty")

    m = _IDENT_RE.match(s)
    if not m:
        raise LiveWatchError(f"Invalid root variable identifier in expression: {expr!r}")

    root = m.group(0)
    pos = m.end()
    ops: list[_FieldOp | _IndexOp] = []
    n = len(s)

    while pos < n:
        while pos < n and s[pos].isspace():
            pos += 1
        if pos >= n:
            break
        if s.startswith("->", pos):
            pos += 2
            while pos < n and s[pos].isspace():
                pos += 1
            fm = _IDENT_RE.match(s, pos)
            if not fm or "::" in fm.group(0):
                raise LiveWatchError(f"Expected member name after '->' in expression: {expr!r}")
            ops.append(_FieldOp(name=fm.group(0), via_pointer=True))
            pos = fm.end()
        elif s[pos] == ".":
            pos += 1
            while pos < n and s[pos].isspace():
                pos += 1
            fm = _IDENT_RE.match(s, pos)
            if not fm or "::" in fm.group(0):
                raise LiveWatchError(f"Expected member name after '.' in expression: {expr!r}")
            ops.append(_FieldOp(name=fm.group(0), via_pointer=False))
            pos = fm.end()
        elif s[pos] == "[":
            im = _INDEX_RE.match(s, pos)
            if not im:
                raise LiveWatchError(f"Malformed array subscript in expression: {expr!r}")
            idx = int(im.group(1), 0)
            ops.append(_IndexOp(index=idx))
            pos = im.end()
        else:
            raise LiveWatchError(f"Unexpected syntax near {s[pos:]!r} in expression: {expr!r}")

    return root, ops


def resolve_watch_target(
    index: DwarfIndex,
    expr: str,
    *,
    probe: DebugProbe | None = None,
) -> ResolvedTarget:
    """将 C 变量路径表达式解析为目标内存地址与精确读取长度。"""
    root_name, ops = _parse_expression(expr)
    var = index.get_variable(root_name)
    if var is None:
        raise LiveWatchError(f"Variable {root_name!r} not found in ELF {index.elf_path}")

    cur_addr = var.address
    cur_type: DwarfType = var.dwarf_type
    cur_bit_size: int | None = None
    cur_rel_bit_off: int | None = None
    cur_read_size = var.byte_size if var.byte_size > 0 else cur_type.byte_size

    for step in ops:
        if cur_bit_size is not None:
            raise LiveWatchError(f"Cannot apply member/index operation to bitfield in expression {expr!r}")

        concrete = cur_type.resolve()
        if isinstance(step, _FieldOp):
            if step.via_pointer:
                if not isinstance(concrete, PointerType):
                    raise LiveWatchError(
                        f"Operator '->' requires pointer type, got {cur_type.display_name!r} in {expr!r}"
                    )
                if probe is None:
                    raise LiveWatchError(f"Expression {expr!r} requires a connected probe to follow '->'")
                ptr_raw = probe.read_memory(cur_addr, concrete.byte_size)
                byteorder: Literal["little", "big"] = "little" if index.endian == "<" else "big"
                ptr_addr = int.from_bytes(ptr_raw, byteorder, signed=False)
                if ptr_addr == 0:
                    raise LiveWatchError(f"Cannot dereference NULL pointer in expression {expr!r}")
                cur_addr = ptr_addr
                cur_type = concrete.target_type
                concrete = cur_type.resolve()
            elif isinstance(concrete, PointerType):
                raise LiveWatchError(
                    f"Cannot use '.' on pointer type {cur_type.display_name!r} in {expr!r}; "
                    f"use '->' or pass deref=True"
                )

            if not isinstance(concrete, StructType):
                raise LiveWatchError(
                    f"Cannot access member {step.name!r} on non-struct type {cur_type.display_name!r} in {expr!r}"
                )
            member = concrete.find_member(step.name)
            if member is None:
                avail = ", ".join(m.name for m in concrete.members) or "<none>"
                raise LiveWatchError(
                    f"Member {step.name!r} not found in {cur_type.display_name!r} (available: {avail})"
                )
            if member.is_bitfield and member.bit_offset is not None and member.bit_size is not None:
                first_byte = member.bit_offset // 8
                last_byte_excl = (member.bit_offset + member.bit_size + 7) // 8
                cur_addr += first_byte
                cur_read_size = last_byte_excl - first_byte
                cur_type = member.member_type
                cur_bit_size = member.bit_size
                cur_rel_bit_off = member.bit_offset % 8
            else:
                cur_addr += member.byte_offset
                cur_type = member.member_type
                cur_read_size = cur_type.byte_size

        elif isinstance(step, _IndexOp):
            if not isinstance(concrete, ArrayType):
                raise LiveWatchError(
                    f"Cannot index non-array type {cur_type.display_name!r} in expression {expr!r}"
                )
            arr_len = concrete.length
            if step.index < 0 or (arr_len > 0 and step.index >= arr_len):
                raise LiveWatchError(
                    f"Array index {step.index} out of bounds for {cur_type.display_name!r} (length {arr_len})"
                )
            stride = concrete.element_stride()
            cur_addr += step.index * stride
            cur_type = concrete.sub_element_type()
            cur_read_size = cur_type.byte_size

    return ResolvedTarget(
        expr=expr.strip(),
        root_name=root_name,
        address=cur_addr,
        read_size=max(cur_read_size, 0),
        dwarf_type=cur_type,
        endian=index.endian,
        pointer_size=index.pointer_size,
        bit_size=cur_bit_size,
        rel_bit_offset=cur_rel_bit_off,
    )


# ==========================================================================
# 本地内存块解包（最小化总线访问）
# ==========================================================================


def _safe_char_string(raw: bytes) -> str:
    head = raw.split(b"\x00", 1)[0]
    decoded = head.decode("utf-8", errors="replace")
    out_chars: list[str] = []
    for ch in decoded:
        code = ord(ch)
        if code < 0x20 and ch not in ("\t", "\n", "\r"):
            out_chars.append(f"\\x{code:02x}")
        elif code == 0x7F:
            out_chars.append("\\x7f")
        else:
            out_chars.append(ch)
    return "".join(out_chars)


def _extract_bitfield_int(
    buf: bytes,
    bit_offset: int,
    bit_size: int,
    signed: bool,
    endian: Literal["<", ">"],
) -> tuple[int, int]:
    """从缓冲区的任意位偏移处提取位域整数，返回 (decoded_val, raw_unsigned_bits)。"""
    first_byte = bit_offset // 8
    last_byte_excl = (bit_offset + bit_size + 7) // 8
    chunk = buf[first_byte:last_byte_excl]
    if len(chunk) < (last_byte_excl - first_byte):
        chunk = chunk + b"\x00" * ((last_byte_excl - first_byte) - len(chunk))

    mask = (1 << bit_size) - 1
    rel_bit = bit_offset % 8
    if endian == "<":
        raw_int = (int.from_bytes(chunk, "little", signed=False) >> rel_bit) & mask
    else:
        total_bits = len(chunk) * 8
        shift = total_bits - rel_bit - bit_size
        raw_int = (int.from_bytes(chunk, "big", signed=False) >> max(shift, 0)) & mask

    val = raw_int
    if signed and bit_size > 0 and (raw_int & (1 << (bit_size - 1))) != 0:
        val = raw_int - (1 << bit_size)
    return val, raw_int


def _decode_from_buffer(
    *,
    expr: str,
    address: int,
    dtype: DwarfType,
    buf: bytes,
    buf_offset: int,
    endian: Literal["<", ">"],
    pointer_size: int,
    probe: DebugProbe | None,
    deref: bool,
    depth: int,
    max_depth: int,
    bit_size: int | None = None,
    bit_offset_in_buf: int | None = None,
    abs_bit_offset: int | None = None,
) -> VariableValue:
    concrete = dtype.resolve()
    byteorder: Literal["little", "big"] = "little" if endian == "<" else "big"

    # ---- 1. 位域标量解码 -------------------------------------------------
    if bit_size is not None and bit_offset_in_buf is not None:
        is_signed = False
        is_bool = False
        enum_t: EnumType | None = None
        if isinstance(concrete, BaseType):
            is_signed = concrete.is_signed
            is_bool = concrete.is_bool
        elif isinstance(concrete, EnumType):
            is_signed = concrete.signed
            enum_t = concrete

        val_int, raw_bits = _extract_bitfield_int(buf, bit_offset_in_buf, bit_size, is_signed, endian)
        hex_digits = max((bit_size + 3) // 4, 1)
        hex_str = f"0x{raw_bits:0{hex_digits}X}"
        span_bytes = (bit_offset_in_buf % 8 + bit_size + 7) // 8

        if is_bool:
            bval = bool(raw_bits)
            return VariableValue(
                expr=expr,
                address=address,
                byte_size=span_bytes,
                type_name=f"{dtype.display_name}:{bit_size}",
                kind="bool",
                value=bval,
                raw_value=bval,
                hex_value=hex_str,
                display="true" if bval else "false",
                bit_size=bit_size,
                bit_offset=abs_bit_offset,
                pointer_size=pointer_size,
            )
        if enum_t is not None:
            ename = enum_t.enum_label(val_int)
            disp = f"{ename} ({val_int})" if ename else f"UNKNOWN ({val_int})"
            eobj = {"value": val_int, "enum": ename, "display": disp}
            return VariableValue(
                expr=expr,
                address=address,
                byte_size=span_bytes,
                type_name=f"{dtype.display_name}:{bit_size}",
                kind="enum",
                value=eobj,
                raw_value=val_int,
                hex_value=hex_str,
                display=disp,
                enum_name=ename,
                bit_size=bit_size,
                bit_offset=abs_bit_offset,
                pointer_size=pointer_size,
            )
        return VariableValue(
            expr=expr,
            address=address,
            byte_size=span_bytes,
            type_name=f"{dtype.display_name}:{bit_size}",
            kind="int",
            value=val_int,
            raw_value=val_int,
            hex_value=hex_str,
            display=f"{val_int} ({hex_str})",
            bit_size=bit_size,
            bit_offset=abs_bit_offset,
            pointer_size=pointer_size,
        )

    bsize = dtype.byte_size
    raw_slice = buf[buf_offset : buf_offset + bsize]
    if len(raw_slice) < bsize:
        raw_slice = raw_slice + b"\x00" * (bsize - len(raw_slice))

    # ---- 2. 基础标量类型 -------------------------------------------------
    if isinstance(concrete, BaseType):
        raw_u = int.from_bytes(raw_slice, byteorder, signed=False) if bsize > 0 else 0
        hex_str = f"0x{raw_u:0{max(bsize * 2, 2)}X}"

        if concrete.is_bool:
            bval = raw_u != 0
            return VariableValue(
                expr=expr,
                address=address,
                byte_size=bsize,
                type_name=dtype.display_name,
                kind="bool",
                value=bval,
                raw_value=bval,
                hex_value=hex_str,
                display=f"{'true' if bval else 'false'} ({hex_str})",
                pointer_size=pointer_size,
            )

        if concrete.is_float:
            if bsize == 4:
                fval = struct.unpack(f"{endian}f", raw_slice)[0]
            elif bsize == 8:
                fval = struct.unpack(f"{endian}d", raw_slice)[0]
            elif bsize == 2:
                fval = struct.unpack(f"{endian}e", raw_slice)[0]
            else:
                fval = float(raw_u)
            return VariableValue(
                expr=expr,
                address=address,
                byte_size=bsize,
                type_name=dtype.display_name,
                kind="float",
                value=fval,
                raw_value=fval,
                hex_value=hex_str,
                display=f"{fval:.6g} ({hex_str})",
                pointer_size=pointer_size,
            )

        ival = int.from_bytes(raw_slice, byteorder, signed=concrete.is_signed) if bsize > 0 else 0
        return VariableValue(
            expr=expr,
            address=address,
            byte_size=bsize,
            type_name=dtype.display_name,
            kind="int",
            value=ival,
            raw_value=ival,
            hex_value=hex_str,
            display=f"{ival} ({hex_str})",
            pointer_size=pointer_size,
        )

    # ---- 3. 枚举类型 -----------------------------------------------------
    if isinstance(concrete, EnumType):
        ebytes = concrete.byte_size or 4
        eslice = buf[buf_offset : buf_offset + ebytes]
        if len(eslice) < ebytes:
            eslice = eslice + b"\x00" * (ebytes - len(eslice))
        raw_u = int.from_bytes(eslice, byteorder, signed=False)
        ival = int.from_bytes(eslice, byteorder, signed=concrete.signed)
        hex_str = f"0x{raw_u:0{ebytes * 2}X}"
        ename = concrete.enum_label(ival)
        disp = f"{ename} ({ival})" if ename is not None else f"UNKNOWN ({ival})"
        eobj = {"value": ival, "enum": ename, "display": disp}
        return VariableValue(
            expr=expr,
            address=address,
            byte_size=ebytes,
            type_name=dtype.display_name,
            kind="enum",
            value=eobj,
            raw_value=ival,
            hex_value=hex_str,
            display=disp,
            enum_name=ename,
            pointer_size=pointer_size,
        )

    # ---- 4. 指针类型（含 NULL 标注与一层安全解引用） ---------------------
    if isinstance(concrete, PointerType):
        psize = concrete.byte_size or pointer_size
        pslice = buf[buf_offset : buf_offset + psize]
        if len(pslice) < psize:
            pslice = pslice + b"\x00" * (psize - len(pslice))
        ptr_addr = int.from_bytes(pslice, byteorder, signed=False)
        hex_addr = f"0x{ptr_addr:0{psize * 2}X}"
        is_null = ptr_addr == 0
        disp = f"{hex_addr} (NULL)" if is_null else hex_addr

        deref_val: VariableValue | None = None
        deref_err: str | None = None
        target_concrete = concrete.target_type.resolve()
        if deref and not is_null and probe is not None and not isinstance(target_concrete, VoidType):
            tsize = concrete.target_type.byte_size
            if tsize > 0:
                try:
                    target_bytes = probe.read_memory(ptr_addr, tsize)
                    deref_val = _decode_from_buffer(
                        expr=f"*({expr})",
                        address=ptr_addr,
                        dtype=concrete.target_type,
                        buf=target_bytes,
                        buf_offset=0,
                        endian=endian,
                        pointer_size=pointer_size,
                        probe=probe,
                        deref=False,  # 严格限制只解引用一层目标类型
                        depth=depth + 1,
                        max_depth=max_depth,
                    )
                except ProbeError as exc:
                    deref_err = str(exc)

        return VariableValue(
            expr=expr,
            address=address,
            byte_size=psize,
            type_name=dtype.display_name,
            kind="pointer",
            value=disp,
            raw_value=ptr_addr,
            hex_value=hex_addr,
            display=disp,
            is_null=is_null,
            deref=deref_val,
            deref_error=deref_err,
            pointer_size=pointer_size,
        )

    # ---- 5. 结构体与联合体递归展开 ---------------------------------------
    if isinstance(concrete, StructType):
        kind_name = "union" if concrete.is_union else "struct"
        if depth >= max_depth:
            return VariableValue(
                expr=expr,
                address=address,
                byte_size=concrete.byte_size,
                type_name=dtype.display_name,
                kind=kind_name,
                value="<max_depth reached>",
                display=f"<{kind_name} max_depth={max_depth} reached>",
                truncated=True,
                pointer_size=pointer_size,
            )

        fields_map: dict[str, VariableValue] = {}
        plain_dict: dict[str, Any] = {}
        for m in concrete.members:
            sub_expr = f"{expr}.{m.name}"
            if m.is_bitfield and m.bit_offset is not None and m.bit_size is not None:
                m_addr = address + (m.bit_offset // 8)
                f_val = _decode_from_buffer(
                    expr=sub_expr,
                    address=m_addr,
                    dtype=m.member_type,
                    buf=buf,
                    buf_offset=buf_offset + (m.bit_offset // 8),
                    endian=endian,
                    pointer_size=pointer_size,
                    probe=probe,
                    deref=deref,
                    depth=depth + 1,
                    max_depth=max_depth,
                    bit_size=m.bit_size,
                    bit_offset_in_buf=buf_offset * 8 + m.bit_offset,
                    abs_bit_offset=m.bit_offset,
                )
            else:
                m_addr = address + m.byte_offset
                f_val = _decode_from_buffer(
                    expr=sub_expr,
                    address=m_addr,
                    dtype=m.member_type,
                    buf=buf,
                    buf_offset=buf_offset + m.byte_offset,
                    endian=endian,
                    pointer_size=pointer_size,
                    probe=probe,
                    deref=deref,
                    depth=depth + 1,
                    max_depth=max_depth,
                )
            fields_map[m.name] = f_val
            plain_dict[m.name] = f_val.value

        return VariableValue(
            expr=expr,
            address=address,
            byte_size=concrete.byte_size,
            type_name=dtype.display_name,
            kind=kind_name,
            value=plain_dict,
            display=f"{{{', '.join(f'{k}: {v.display}' for k, v in fields_map.items())}}}",
            fields=fields_map,
            pointer_size=pointer_size,
        )

    # ---- 6. 数组与 char[] 字符串预览 -------------------------------------
    if isinstance(concrete, ArrayType):
        total_size = concrete.byte_size
        arr_len = concrete.length
        sub_type = concrete.sub_element_type()
        stride = concrete.element_stride()
        str_preview = _safe_char_string(raw_slice) if concrete.is_char_array else None

        if depth >= max_depth:
            return VariableValue(
                expr=expr,
                address=address,
                byte_size=total_size,
                type_name=dtype.display_name,
                kind="array",
                value="<max_depth reached>",
                string_value=str_preview,
                display=repr(str_preview) if str_preview is not None else f"<array[{arr_len}] max_depth reached>",
                truncated=True,
                pointer_size=pointer_size,
            )

        elems: list[VariableValue] = []
        plain_elems: list[Any] = []
        for idx in range(arr_len):
            elem_off = buf_offset + idx * stride
            elem_addr = address + idx * stride
            ev = _decode_from_buffer(
                expr=f"{expr}[{idx}]",
                address=elem_addr,
                dtype=sub_type,
                buf=buf,
                buf_offset=elem_off,
                endian=endian,
                pointer_size=pointer_size,
                probe=probe,
                deref=deref,
                depth=depth + 1,
                max_depth=max_depth,
            )
            elems.append(ev)
            plain_elems.append(ev.value)

        if str_preview is not None:
            disp = f"{str_preview!r}"
        else:
            disp = f"[{', '.join(e.display for e in elems[:8])}{', ...' if len(elems) > 8 else ''}]"

        return VariableValue(
            expr=expr,
            address=address,
            byte_size=total_size,
            type_name=dtype.display_name,
            kind="array",
            value=plain_elems,
            string_value=str_preview,
            display=disp,
            elements=elems,
            pointer_size=pointer_size,
        )

    return VariableValue(
        expr=expr,
        address=address,
        byte_size=bsize,
        type_name=dtype.display_name,
        kind="void",
        value=None,
        display="<void>",
        pointer_size=pointer_size,
    )


# ==========================================================================
# 对外公开 API：单变量读取、批量读取与高频采样
# ==========================================================================


def read_variable(
    probe: DebugProbe,
    elf_path: str | os.PathLike,
    expr: str,
    *,
    deref: bool = False,
    max_depth: int = 4,
) -> VariableValue:
    """不停机读取单个 C 全局/静态变量、嵌套结构体字段、数组元素、枚举、位域或指针。"""
    if max_depth < 1:
        raise ValueError(f"max_depth must be >= 1, got {max_depth}")

    index = load_dwarf_index(elf_path)
    target = resolve_watch_target(index, expr, probe=probe)
    raw = probe.read_memory(target.address, target.read_size) if target.read_size > 0 else b""

    return _decode_from_buffer(
        expr=target.expr,
        address=target.address,
        dtype=target.dwarf_type,
        buf=raw,
        buf_offset=0,
        endian=target.endian,
        pointer_size=target.pointer_size,
        probe=probe,
        deref=deref,
        depth=0,
        max_depth=max_depth,
        bit_size=target.bit_size,
        bit_offset_in_buf=target.rel_bit_offset,
        abs_bit_offset=target.rel_bit_offset,
    )


def read_variables(
    probe: DebugProbe,
    elf_path: str | os.PathLike,
    exprs: list[str],
    *,
    deref: bool = False,
    max_depth: int = 4,
) -> dict[str, VariableValue]:
    """批量不停机读取多个 C 变量表达式，返回 `{expr: VariableValue}` 字典。"""
    if max_depth < 1:
        raise ValueError(f"max_depth must be >= 1, got {max_depth}")

    index = load_dwarf_index(elf_path)
    results: dict[str, VariableValue] = {}
    for expr in exprs:
        target = resolve_watch_target(index, expr, probe=probe)
        raw = probe.read_memory(target.address, target.read_size) if target.read_size > 0 else b""
        results[expr] = _decode_from_buffer(
            expr=target.expr,
            address=target.address,
            dtype=target.dwarf_type,
            buf=raw,
            buf_offset=0,
            endian=target.endian,
            pointer_size=target.pointer_size,
            probe=probe,
            deref=deref,
            depth=0,
            max_depth=max_depth,
            bit_size=target.bit_size,
            bit_offset_in_buf=target.rel_bit_offset,
            abs_bit_offset=target.rel_bit_offset,
        )
    return results


def _validate_sample_scalar(target: ResolvedTarget) -> str:
    concrete = target.dwarf_type.resolve()
    if target.bit_size is not None:
        if isinstance(concrete, EnumType):
            return "enum"
        if isinstance(concrete, BaseType) and concrete.is_bool:
            return "bool"
        return "int"

    if isinstance(concrete, BaseType):
        if concrete.is_bool:
            return "bool"
        if concrete.is_float:
            return "float"
        return "int"
    if isinstance(concrete, EnumType):
        return "enum"

    if isinstance(concrete, StructType):
        hint_member = concrete.members[0].name if concrete.members else "rpm"
        raise LiveWatchError(
            f"Cannot sample composite {concrete.kind} expression {target.expr!r} ({target.dwarf_type.display_name}); "
            f"please specify a scalar member such as '{target.expr}.{hint_member}'"
        )
    if isinstance(concrete, ArrayType):
        raise LiveWatchError(
            f"Cannot sample array expression {target.expr!r} ({target.dwarf_type.display_name}); "
            f"please specify a scalar element such as '{target.expr}[0]'"
        )
    raise LiveWatchError(
        f"Cannot sample non-scalar expression {target.expr!r} of type {target.dwarf_type.display_name!r}; "
        f"only numeric, enum, or boolean scalar fields are supported"
    )


def _decode_scalar_sample(target: ResolvedTarget, raw: bytes) -> tuple[int | float, str | None]:
    concrete = target.dwarf_type.resolve()
    byteorder: Literal["little", "big"] = "little" if target.endian == "<" else "big"

    if target.bit_size is not None and target.rel_bit_offset is not None:
        is_signed = False
        if isinstance(concrete, BaseType):
            is_signed = concrete.is_signed
        elif isinstance(concrete, EnumType):
            is_signed = concrete.signed
        val_int, raw_bits = _extract_bitfield_int(
            raw, target.rel_bit_offset, target.bit_size, is_signed, target.endian
        )
        if isinstance(concrete, BaseType) and concrete.is_bool:
            b = bool(raw_bits)
            return int(b), "true" if b else "false"
        if isinstance(concrete, EnumType):
            lbl = concrete.enum_label(val_int)
            return val_int, f"{lbl} ({val_int})" if lbl else f"UNKNOWN ({val_int})"
        return val_int, None

    if isinstance(concrete, BaseType):
        if concrete.is_bool:
            b = int.from_bytes(raw, byteorder, signed=False) != 0
            return int(b), "true" if b else "false"
        if concrete.is_float:
            if target.read_size == 4:
                return struct.unpack(f"{target.endian}f", raw)[0], None
            if target.read_size == 8:
                return struct.unpack(f"{target.endian}d", raw)[0], None
            if target.read_size == 2:
                return struct.unpack(f"{target.endian}e", raw)[0], None
        return int.from_bytes(raw, byteorder, signed=concrete.is_signed), None

    if isinstance(concrete, EnumType):
        ival = int.from_bytes(raw, byteorder, signed=concrete.signed)
        lbl = concrete.enum_label(ival)
        return ival, f"{lbl} ({ival})" if lbl else f"UNKNOWN ({ival})"

    raise LiveWatchError(f"Unsupported scalar sample type for {target.expr!r}")


def sample_variable(
    probe: DebugProbe,
    elf_path: str | os.PathLike,
    expr: str,
    *,
    samples: int = 20,
    interval_ms: float = 10.0,
) -> VariableSampleReport:
    """对数值/枚举/布尔标量字段执行连续无停机高频趋势采样并输出统计指标。"""
    if not (1 <= samples <= 500):
        raise ValueError(f"samples must be between 1 and 500, got {samples}")
    if interval_ms < 0 or interval_ms > 60_000:
        raise ValueError(f"interval_ms must be between 0 and 60000, got {interval_ms}")

    index = load_dwarf_index(elf_path)
    target = resolve_watch_target(index, expr, probe=probe)
    kind = _validate_sample_scalar(target)
    if target.read_size <= 0:
        raise LiveWatchError(f"Scalar target {target.expr!r} resolved to 0-byte read size")

    points: list[SamplePoint] = []
    numeric_values: list[int | float] = []
    interval_s = interval_ms / 1000.0

    t0 = time.perf_counter()
    for idx in range(samples):
        raw = probe.read_memory(target.address, target.read_size)
        ts_ms = round((time.perf_counter() - t0) * 1000.0, 3)
        val, disp = _decode_scalar_sample(target, raw)
        points.append(SamplePoint(index=idx, timestamp_ms=ts_ms, value=val, display=disp))
        numeric_values.append(val)
        if idx + 1 < samples and interval_s > 0:
            time.sleep(interval_s)
    duration_ms = round((time.perf_counter() - t0) * 1000.0, 3)

    v_min = min(numeric_values)
    v_max = max(numeric_values)
    v_first = numeric_values[0]
    v_last = numeric_values[-1]
    v_delta = v_last - v_first
    v_mean = sum(numeric_values) / len(numeric_values)
    unique_cnt = len(set(numeric_values))

    addr_width = 16 if (target.pointer_size > 4 or target.address > 0xFFFFFFFF) else 8
    return VariableSampleReport(
        expr=target.expr,
        address=target.address,
        address_hex=f"0x{target.address:0{addr_width}X}",
        type_name=target.dwarf_type.display_name,
        kind=kind,
        samples_requested=samples,
        sample_count=len(points),
        interval_ms=interval_ms,
        duration_ms=duration_ms,
        min=v_min,
        max=v_max,
        mean=v_mean,
        first=v_first,
        last=v_last,
        delta=v_delta,
        changing=unique_cnt > 1,
        unique_count=unique_cnt,
        points=points,
    )
