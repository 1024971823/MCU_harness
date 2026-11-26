"""纯标准库 ELF + DWARF (v2/v3/v4/v5) 类型系统与全局/静态变量索引解析器。

支持特性：
    * 直接解析 ELF32 / ELF64（小端 / 大端）及可选 zlib 压缩调试节（SHF_COMPRESSED / .zdebug_*）。
    * 完整支持 DWARF v2 / v3 / v4 / v5 Compilation Unit Header（32-bit 与 64-bit DWARF 偏移）
      以及 `.debug_info`、`.debug_abbrev`、`.debug_str`、`.debug_line_str`、
      `.debug_str_offsets`、`.debug_addr`。
    * 核心 C 类型还原：
        - 基础标量类型（signed / unsigned / char / float / double / bool）
        - 类型别名与限定符穿透（typedef / volatile / const / restrict / atomic）
        - 枚举类型（`{value: enum_name}` 双向可读映射，兼容有符号负枚举值）
        - 结构体与联合体（含嵌套匿名 struct/union 成员展平、`DW_OP_plus_uconst`、
          DWARF v2~v4 `DW_AT_bit_offset` 与 DWARF v4/v5 `DW_AT_data_bit_offset` 位域）
        - 一维与多维数组（`DW_AT_count` / `DW_AT_upper_bound`）
        - 指针类型（自动适配 4 字节 MCU 指针与 8 字节宿主机指针，兼容 `void *`）
    * 全局/静态变量定位：优先解析 `DW_AT_location`（`DW_OP_addr` / `DW_OP_addrx`），
      缺失时自动联合 `read_elf_symbols()` 的 `.symtab` / `.dynsym` 补全地址；
      当固件完全无 `.debug_info`（无 `-g` 编译）时，自动按符号表 `size` 优雅降级。
"""

from __future__ import annotations

import fnmatch
import math
import os
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from mcu_harness.elf.symbols import ElfError, ElfSymbol, read_elf_symbols

_ELF_MAGIC = b"\x7fELF"
_SHT_PROGBITS = 1
_SHT_RELA = 4
_SHF_ALLOC = 0x2
_SHF_COMPRESSED = 0x800
_SHN_XINDEX = 0xFFFF
_ELFCOMPRESS_ZLIB = 1

# EM constants for optional PIE relative relocation fixup in host test ELFs
_EM_X86_64 = 62
_EM_ARM = 40
_EM_AARCH64 = 183
_R_X86_64_RELATIVE = 8
_R_ARM_RELATIVE = 23
_R_AARCH64_RELATIVE = 1027

# ---- DWARF Tags ----------------------------------------------------------
DW_TAG_array_type = 0x01
DW_TAG_class_type = 0x02
DW_TAG_enumeration_type = 0x04
DW_TAG_formal_parameter = 0x05
DW_TAG_lexical_block = 0x0B
DW_TAG_member = 0x0D
DW_TAG_pointer_type = 0x0F
DW_TAG_reference_type = 0x10
DW_TAG_compile_unit = 0x11
DW_TAG_structure_type = 0x13
DW_TAG_subroutine_type = 0x15
DW_TAG_typedef = 0x16
DW_TAG_union_type = 0x17
DW_TAG_inlined_subroutine = 0x1D
DW_TAG_subrange_type = 0x21
DW_TAG_base_type = 0x24
DW_TAG_const_type = 0x26
DW_TAG_enumerator = 0x28
DW_TAG_subprogram = 0x2E
DW_TAG_variable = 0x34
DW_TAG_volatile_type = 0x35
DW_TAG_restrict_type = 0x37
DW_TAG_namespace = 0x39
DW_TAG_partial_unit = 0x3C
DW_TAG_rvalue_reference_type = 0x42
DW_TAG_atomic_type = 0x47

# ---- DWARF Unit Types (v5) -----------------------------------------------
DW_UT_compile = 0x01
DW_UT_type = 0x02
DW_UT_partial = 0x03
DW_UT_skeleton = 0x04
DW_UT_split_compile = 0x05
DW_UT_split_type = 0x06

# ---- DWARF Attributes ----------------------------------------------------
DW_AT_location = 0x02
DW_AT_name = 0x03
DW_AT_byte_size = 0x0B
DW_AT_bit_offset = 0x0C
DW_AT_bit_size = 0x0D
DW_AT_const_value = 0x1C
DW_AT_lower_bound = 0x22
DW_AT_upper_bound = 0x2F
DW_AT_abstract_origin = 0x31
DW_AT_count = 0x37
DW_AT_data_member_location = 0x38
DW_AT_declaration = 0x3C
DW_AT_encoding = 0x3E
DW_AT_external = 0x3F
DW_AT_specification = 0x47
DW_AT_type = 0x49
DW_AT_data_bit_offset = 0x6B
DW_AT_linkage_name = 0x6E
DW_AT_str_offsets_base = 0x72
DW_AT_addr_base = 0x73
DW_AT_MIPS_linkage_name = 0x2007
DW_AT_GNU_addr_base = 0x2133

# ---- DWARF Forms ---------------------------------------------------------
DW_FORM_addr = 0x01
DW_FORM_block2 = 0x03
DW_FORM_block4 = 0x04
DW_FORM_data2 = 0x05
DW_FORM_data4 = 0x06
DW_FORM_data8 = 0x07
DW_FORM_string = 0x08
DW_FORM_block = 0x09
DW_FORM_block1 = 0x0A
DW_FORM_data1 = 0x0B
DW_FORM_flag = 0x0C
DW_FORM_sdata = 0x0D
DW_FORM_strp = 0x0E
DW_FORM_udata = 0x0F
DW_FORM_ref_addr = 0x10
DW_FORM_ref1 = 0x11
DW_FORM_ref2 = 0x12
DW_FORM_ref4 = 0x13
DW_FORM_ref8 = 0x14
DW_FORM_ref_udata = 0x15
DW_FORM_indirect = 0x16
DW_FORM_sec_offset = 0x17
DW_FORM_exprloc = 0x18
DW_FORM_flag_present = 0x19
DW_FORM_strx = 0x1A
DW_FORM_addrx = 0x1B
DW_FORM_ref_sup4 = 0x1C
DW_FORM_strp_sup = 0x1D
DW_FORM_data16 = 0x1E
DW_FORM_line_strp = 0x1F
DW_FORM_ref_sig8 = 0x20
DW_FORM_implicit_const = 0x21
DW_FORM_loclistx = 0x22
DW_FORM_rnglistx = 0x23
DW_FORM_ref_sup8 = 0x24
DW_FORM_strx1 = 0x25
DW_FORM_strx2 = 0x26
DW_FORM_strx3 = 0x27
DW_FORM_strx4 = 0x28
DW_FORM_addrx1 = 0x29
DW_FORM_addrx2 = 0x2A
DW_FORM_addrx3 = 0x2B
DW_FORM_addrx4 = 0x2C
DW_FORM_GNU_addr_index = 0x1F01
DW_FORM_GNU_str_index = 0x1F02
DW_FORM_GNU_ref_alt = 0x1F20
DW_FORM_GNU_strp_alt = 0x1F21

# ---- DWARF Base Type Encodings (DW_ATE_*) --------------------------------
DW_ATE_address = 0x01
DW_ATE_boolean = 0x02
DW_ATE_complex_float = 0x03
DW_ATE_float = 0x04
DW_ATE_signed = 0x05
DW_ATE_signed_char = 0x06
DW_ATE_unsigned = 0x07
DW_ATE_unsigned_char = 0x08
DW_ATE_UTF = 0x10

# ---- DWARF Expression Opcodes --------------------------------------------
DW_OP_addr = 0x03
DW_OP_const1u = 0x08
DW_OP_const2u = 0x0A
DW_OP_const4u = 0x0C
DW_OP_const8u = 0x0E
DW_OP_constu = 0x10
DW_OP_consts = 0x11
DW_OP_plus = 0x22
DW_OP_plus_uconst = 0x23
DW_OP_lit0 = 0x30
DW_OP_lit31 = 0x4F
DW_OP_stack_value = 0x9F
DW_OP_addrx = 0xA1
DW_OP_GNU_addr_index = 0xFB


class DwarfError(ElfError):
    """ELF / DWARF 调试信息解析异常。"""


# ==========================================================================
# C 类型系统模型
# ==========================================================================


class DwarfType:
    """DWARF 类型抽象基类。"""

    def resolve(self, _seen: set[int] | None = None) -> DwarfType:
        """穿透 typedef / const / volatile / restrict / atomic 限定符返回底层实体类型。"""
        return self

    @property
    def display_name(self) -> str:
        n = getattr(self, "name", "")
        return n or getattr(self, "kind", "unknown")

    @property
    def is_char_element(self) -> bool:
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": getattr(self, "kind", "unknown"),
            "name": self.display_name,
            "byte_size": getattr(self, "byte_size", 0),
        }


@dataclass(frozen=True)
class VoidType(DwarfType):
    kind: str = field(default="void", init=False)
    name: str = "void"
    byte_size: int = 0


@dataclass(frozen=True)
class BaseType(DwarfType):
    name: str
    byte_size: int
    encoding: int
    kind: str = field(default="base", init=False)

    @property
    def is_signed(self) -> bool:
        return self.encoding in (DW_ATE_signed, DW_ATE_signed_char)

    @property
    def is_float(self) -> bool:
        return self.encoding == DW_ATE_float

    @property
    def is_bool(self) -> bool:
        return self.encoding == DW_ATE_boolean

    @property
    def is_char_element(self) -> bool:
        if self.byte_size != 1:
            return False
        if self.encoding in (DW_ATE_signed_char, DW_ATE_unsigned_char, DW_ATE_UTF):
            return True
        low = self.name.lower()
        return "char" in low and "int8" not in low

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["encoding"] = self.encoding
        return d


@dataclass
class QualifiedType(DwarfType):
    """typedef / volatile / const / restrict / atomic 修饰或类型别名。"""

    qualifier: str  # "typedef" | "volatile" | "const" | "restrict" | "atomic"
    target: DwarfType
    alias_name: str = ""
    kind: str = field(default="qualified", init=False)

    @property
    def name(self) -> str:  # type: ignore[override]
        if self.qualifier == "typedef" and self.alias_name:
            return self.alias_name
        inner = self.target.display_name
        if isinstance(self.target, PointerType):
            return f"{inner} {self.qualifier}".strip()
        return f"{self.qualifier} {inner}".strip()

    @property
    def byte_size(self) -> int:  # type: ignore[override]
        return self.target.byte_size

    @property
    def is_char_element(self) -> bool:
        if self.qualifier == "typedef" and self.alias_name in ("int8_t", "uint8_t"):
            return False
        return self.target.is_char_element

    def resolve(self, _seen: set[int] | None = None) -> DwarfType:
        seen = _seen if _seen is not None else set()
        obj_id = id(self)
        if obj_id in seen:
            return VoidType()
        seen.add(obj_id)
        return self.target.resolve(seen)


@dataclass(frozen=True)
class EnumType(DwarfType):
    name: str
    byte_size: int
    signed: bool
    enumerators: dict[int, str]
    kind: str = field(default="enum", init=False)

    @property
    def display_name(self) -> str:
        return f"enum {self.name}" if self.name and not self.name.startswith("enum ") else (self.name or "enum <anon>")

    def enum_label(self, value: int) -> str | None:
        return self.enumerators.get(value)

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["signed"] = self.signed
        d["enumerators"] = dict(self.enumerators)
        return d


@dataclass
class StructMember:
    name: str
    member_type: DwarfType
    byte_offset: int = 0
    bit_size: int | None = None
    bit_offset: int | None = None  # 统一归一化为自外层结构体起始处的 data_bit_offset

    @property
    def is_bitfield(self) -> bool:
        return self.bit_size is not None and self.bit_offset is not None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "type": self.member_type.display_name,
            "byte_offset": self.byte_offset,
        }
        if self.is_bitfield:
            out["bit_size"] = self.bit_size
            out["bit_offset"] = self.bit_offset
        return out


@dataclass
class StructType(DwarfType):
    name: str
    byte_size: int
    members: tuple[StructMember, ...] = ()
    is_union: bool = False
    is_declaration: bool = False

    @property
    def kind(self) -> str:  # type: ignore[override]
        return "union" if self.is_union else "struct"

    @property
    def display_name(self) -> str:
        prefix = "union" if self.is_union else "struct"
        if self.name:
            return self.name if self.name.startswith((f"{prefix} ",)) else f"{prefix} {self.name}"
        return f"{prefix} <anon>"

    def find_member(self, member_name: str) -> StructMember | None:
        for m in self.members:
            if m.name == member_name:
                return m
        return None

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["members"] = [m.to_dict() for m in self.members]
        return d


@dataclass
class ArrayType(DwarfType):
    element_type: DwarfType
    dimensions: tuple[int, ...]
    explicit_byte_size: int | None = None
    kind: str = field(default="array", init=False)

    @property
    def byte_size(self) -> int:  # type: ignore[override]
        if self.explicit_byte_size is not None and self.explicit_byte_size > 0:
            return self.explicit_byte_size
        if not self.dimensions:
            return 0
        return math.prod(max(d, 0) for d in self.dimensions) * self.element_type.byte_size

    @property
    def length(self) -> int:
        return self.dimensions[0] if self.dimensions else 0

    @property
    def is_char_array(self) -> bool:
        return len(self.dimensions) == 1 and self.element_type.is_char_element

    @property
    def display_name(self) -> str:
        dims = "".join(f"[{d}]" if d > 0 else "[]" for d in self.dimensions)
        return f"{self.element_type.display_name}{dims}"

    def sub_element_type(self) -> DwarfType:
        """下标访问一层后的元素类型（多维数组剥离首维，一维数组返回元素类型）。"""
        if len(self.dimensions) > 1:
            return ArrayType(element_type=self.element_type, dimensions=self.dimensions[1:])
        return self.element_type

    def element_stride(self) -> int:
        sub = self.sub_element_type()
        return sub.byte_size


@dataclass
class PointerType(DwarfType):
    byte_size: int
    target_type: DwarfType
    kind: str = field(default="pointer", init=False)

    @property
    def display_name(self) -> str:
        base = self.target_type.display_name
        return f"{base}*" if base.endswith("*") else f"{base} *"


@dataclass(frozen=True)
class GlobalVariable:
    """全局或静态变量元数据。"""

    name: str
    address: int
    byte_size: int
    type_name: str
    dwarf_type: DwarfType
    source: str = "dwarf"  # "dwarf" | "dwarf+symtab" | "symtab"

    @property
    def address_hex(self) -> str:
        width = 16 if self.address > 0xFFFFFFFF else 8
        return f"0x{self.address:0{width}X}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "address": self.address,
            "address_hex": self.address_hex,
            "byte_size": self.byte_size,
            "type_name": self.type_name,
            "kind": self.dwarf_type.resolve().kind,
            "source": self.source,
        }


@dataclass
class DwarfIndex:
    """单个 ELF 固件解析完成后的类型与全局变量缓存索引。"""

    elf_path: str
    endian: Literal["<", ">"]
    pointer_size: int
    dwarf_versions: tuple[int, ...]
    degraded_symtab_only: bool
    variables: dict[str, GlobalVariable]
    types: dict[int, DwarfType] = field(default_factory=dict, repr=False)

    def get_variable(self, name: str) -> GlobalVariable | None:
        return self.variables.get(name)

    def list_variables(self, pattern: str | None = None) -> list[GlobalVariable]:
        items = list(self.variables.values())
        if pattern:
            pat = pattern.strip()
            has_glob = any(ch in pat for ch in "*?[")
            low_pat = pat.lower()
            filtered: list[GlobalVariable] = []
            for v in items:
                if has_glob:
                    if fnmatch.fnmatch(v.name.lower(), low_pat) or fnmatch.fnmatch(v.type_name.lower(), low_pat):
                        filtered.append(v)
                elif low_pat in v.name.lower() or low_pat in v.type_name.lower():
                    filtered.append(v)
            items = filtered
        items.sort(key=lambda v: (v.address, v.name))
        return items


# ==========================================================================
# 底层二进制与 LEB128 解码工具
# ==========================================================================


def _read_uleb128(buf: bytes, pos: int, limit: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while pos < limit:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            return result, pos
        shift += 7
        if shift > 128:
            raise DwarfError("Malformed ULEB128 integer in DWARF section")
    raise DwarfError("Truncated ULEB128 integer at end of DWARF buffer")


def _read_sleb128(buf: bytes, pos: int, limit: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while pos < limit:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        shift += 7
        if (b & 0x80) == 0:
            if shift < 128 and (b & 0x40) != 0:
                result |= -(1 << shift)
            return result, pos
        if shift > 128:
            raise DwarfError("Malformed SLEB128 integer in DWARF section")
    raise DwarfError("Truncated SLEB128 integer at end of DWARF buffer")


def _read_cstring(buf: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(buf):
        return ""
    nul = buf.find(b"\x00", offset)
    raw = buf[offset:nul] if nul != -1 else buf[offset:]
    return raw.decode("utf-8", errors="replace")


def _read_uint(buf: bytes, pos: int, size: int, endian: Literal["<", ">"]) -> int:
    if pos < 0 or pos + size > len(buf):
        raise DwarfError(f"Unexpected EOF reading {size} bytes at offset 0x{pos:X}")
    byteorder: Literal["little", "big"] = "little" if endian == "<" else "big"
    return int.from_bytes(buf[pos : pos + size], byteorder, signed=False)


@dataclass(frozen=True)
class _StrxRef:
    index: int


@dataclass(frozen=True)
class _AddrxRef:
    index: int


@dataclass(frozen=True)
class _DieRef:
    info_offset: int


@dataclass(frozen=True)
class _RawUnsignedConst:
    value: int
    byte_width: int


@dataclass
class _AbbrevEntry:
    code: int
    tag: int
    has_children: bool
    attrs: tuple[tuple[int, int, int | None], ...]  # (attr_name, form, implicit_const)


@dataclass
class _RawDie:
    offset: int
    cu_idx: int
    tag: int
    attrs: dict[int, Any]
    children: list[_RawDie] = field(default_factory=list)
    parent_tag: int = 0
    scope_name: str = ""


@dataclass
class _CompilationUnit:
    cu_start: int
    version: int
    address_size: int
    offset_size: int
    str_offsets_base: int = 0
    addr_base: int = 0


# ==========================================================================
# ELF 节表解析（含 zlib SHF_COMPRESSED 与初始化内存提取）
# ==========================================================================


@dataclass
class _ElfImage:
    path: Path
    ei_class: int  # 1=ELF32, 2=ELF64
    endian: Literal["<", ">"]
    e_machine: int
    raw: bytes
    sections_by_name: dict[str, bytes]
    raw_sections: list[tuple[str, int, int, int, int, int]]  # (name, sh_type, sh_flags, sh_addr, sh_offset, sh_size)


def _decompress_section(
    sec_name: str,
    sec_bytes: bytes,
    sh_flags: int,
    ei_class: int,
    endian: Literal["<", ">"],
) -> tuple[str, bytes]:
    if sh_flags & _SHF_COMPRESSED:
        hdr_fmt = f"{endian}III" if ei_class == 1 else f"{endian}IIQQ"
        hdr_sz = struct.calcsize(hdr_fmt)
        if len(sec_bytes) < hdr_sz:
            raise DwarfError(f"Truncated SHF_COMPRESSED header in section {sec_name}")
        fields = struct.unpack_from(hdr_fmt, sec_bytes, 0)
        ch_type = fields[0]
        if ch_type != _ELFCOMPRESS_ZLIB:
            raise DwarfError(f"Unsupported ELF compression type {ch_type} in section {sec_name}")
        return sec_name, zlib.decompress(sec_bytes[hdr_sz:])
    if sec_name.startswith(".zdebug_") and sec_bytes.startswith(b"ZLIB") and len(sec_bytes) >= 12:
        return "." + sec_name[2:], zlib.decompress(sec_bytes[12:])
    return sec_name, sec_bytes


def _parse_elf_image(elf_path: str | os.PathLike) -> _ElfImage:
    p = Path(elf_path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"ELF file not found: {p}")

    data = p.read_bytes()
    if len(data) < 52 or data[:4] != _ELF_MAGIC:
        raise ElfError(f"Not a valid ELF file: {p}")

    ei_class = data[4]
    ei_data = data[5]
    if ei_class not in (1, 2) or ei_data not in (1, 2):
        raise ElfError(f"Unsupported ELF class/endian ({ei_class}, {ei_data}) in {p}")
    endian: Literal["<", ">"] = "<" if ei_data == 1 else ">"

    e_machine = struct.unpack_from(f"{endian}H", data, 18)[0]
    if ei_class == 1:
        e_shoff = struct.unpack_from(f"{endian}I", data, 32)[0]
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from(f"{endian}HHH", data, 46)
        sh_fmt = f"{endian}IIIIIIIIII"
    else:
        if len(data) < 64:
            raise ElfError(f"Truncated ELF64 header in {p}")
        e_shoff = struct.unpack_from(f"{endian}Q", data, 40)[0]
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from(f"{endian}HHH", data, 58)
        sh_fmt = f"{endian}IIQQQQIIQQ"

    if e_shoff == 0:
        return _ElfImage(p, ei_class, endian, e_machine, data, {}, [])

    sh_struct_size = struct.calcsize(sh_fmt)
    if e_shentsize < sh_struct_size or e_shoff < 0:
        raise ElfError(f"Corrupted ELF section header table in {p}")

    if e_shnum == 0:
        if e_shoff + sh_struct_size > len(data):
            raise ElfError(f"Corrupted ELF initial section header in {p}")
        first_sec = struct.unpack_from(sh_fmt, data, e_shoff)
        e_shnum = int(first_sec[5])

    if e_shoff + e_shnum * e_shentsize > len(data):
        raise ElfError(f"Corrupted ELF section header table out of bounds in {p}")

    sec_tuples = [struct.unpack_from(sh_fmt, data, e_shoff + i * e_shentsize) for i in range(e_shnum)]
    if e_shstrndx == _SHN_XINDEX and sec_tuples:
        e_shstrndx = int(sec_tuples[0][6])

    shstrtab = b""
    if 0 <= e_shstrndx < len(sec_tuples):
        _, _, _, _, str_off, str_sz, *_ = sec_tuples[e_shstrndx]
        if 0 <= str_off and str_off + str_sz <= len(data):
            shstrtab = data[str_off : str_off + str_sz]

    sections_by_name: dict[str, bytes] = {}
    raw_sections: list[tuple[str, int, int, int, int, int]] = []
    for sh in sec_tuples:
        sh_name_off, sh_type, sh_flags, sh_addr, sh_offset, sh_size, *_ = sh
        sec_name = _read_cstring(shstrtab, int(sh_name_off)) if shstrtab else ""
        raw_sections.append((sec_name, int(sh_type), int(sh_flags), int(sh_addr), int(sh_offset), int(sh_size)))
        if not sec_name or sh_type == 8:  # SHT_NOBITS (.bss)
            continue
        if sec_name.startswith((".debug_", ".zdebug_")):
            if sh_offset < 0 or sh_size < 0 or sh_offset + sh_size > len(data):
                raise DwarfError(f"Corrupted DWARF section {sec_name} out of bounds in {p}")
            norm_name, content = _decompress_section(
                sec_name,
                data[sh_offset : sh_offset + sh_size],
                int(sh_flags),
                ei_class,
                endian,
            )
            sections_by_name[norm_name] = content

    return _ElfImage(p, ei_class, endian, e_machine, data, sections_by_name, raw_sections)


def extract_elf_initialized_memory(elf_path: str | os.PathLike) -> dict[int, bytes]:
    """提取 ELF 中带 SHF_ALLOC 的已初始化节（如 .data / .rodata）初始字节映射。

    同时对宿主机 PIE ELF64/ELF32 中基址为 0 的 RELATIVE 重定位（如编译期初始化的全局结构体指针）
    做就地修补，方便单元测试与 CLI `--mock` 直接加载真实初始值到 MockProbe。
    """
    img = _parse_elf_image(elf_path)
    segments: dict[int, bytearray] = {}
    for sec_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size in img.raw_sections:
        if sh_type == _SHT_PROGBITS and (sh_flags & _SHF_ALLOC) and sh_size > 0:
            if 0 <= sh_offset and sh_offset + sh_size <= len(img.raw):
                segments[sh_addr] = bytearray(img.raw[sh_offset : sh_offset + sh_size])

    # 修补 PIE R_*_RELATIVE 重定位（例如 const pid_t *active_pid = &g_ctrl.pid）
    ptr_sz = 4 if img.ei_class == 1 else 8
    rela_fmt = f"{img.endian}IIi" if img.ei_class == 1 else f"{img.endian}QQq"
    rela_sz = struct.calcsize(rela_fmt)
    byteorder: Literal["little", "big"] = "little" if img.endian == "<" else "big"
    for _, sh_type, _, _, sh_offset, sh_size in img.raw_sections:
        if sh_type != _SHT_RELA or sh_size < rela_sz:
            continue
        if sh_offset < 0 or sh_offset + sh_size > len(img.raw):
            continue
        for pos in range(sh_offset, sh_offset + sh_size - rela_sz + 1, rela_sz):
            r_offset, r_info, r_addend = struct.unpack_from(rela_fmt, img.raw, pos)
            r_type = r_info & (0xFF if img.ei_class == 1 else 0xFFFFFFFF)
            is_rel = (
                (img.e_machine == _EM_X86_64 and r_type == _R_X86_64_RELATIVE)
                or (img.e_machine == _EM_ARM and r_type == _R_ARM_RELATIVE)
                or (img.e_machine == _EM_AARCH64 and r_type == _R_AARCH64_RELATIVE)
            )
            if not is_rel:
                continue
            for seg_addr, seg_buf in segments.items():
                if seg_addr <= r_offset and r_offset + ptr_sz <= seg_addr + len(seg_buf):
                    rel_off = r_offset - seg_addr
                    masked = r_addend & ((1 << (ptr_sz * 8)) - 1)
                    seg_buf[rel_off : rel_off + ptr_sz] = masked.to_bytes(ptr_sz, byteorder)
                    break

    return {addr: bytes(buf) for addr, buf in segments.items()}


# ==========================================================================
# DWARF Abbrev / Form / DIE 解析器
# ==========================================================================


def _parse_abbrev_table(debug_abbrev: bytes, abbrev_offset: int) -> dict[int, _AbbrevEntry]:
    if abbrev_offset < 0 or abbrev_offset >= len(debug_abbrev):
        raise DwarfError(f"Abbrev offset 0x{abbrev_offset:X} out of bounds")
    table: dict[int, _AbbrevEntry] = {}
    pos = abbrev_offset
    limit = len(debug_abbrev)
    while pos < limit:
        code, pos = _read_uleb128(debug_abbrev, pos, limit)
        if code == 0:
            break
        tag, pos = _read_uleb128(debug_abbrev, pos, limit)
        if pos >= limit:
            raise DwarfError("Truncated abbrev entry (missing has_children)")
        has_children = debug_abbrev[pos] != 0
        pos += 1
        attrs: list[tuple[int, int, int | None]] = []
        while True:
            at_name, pos = _read_uleb128(debug_abbrev, pos, limit)
            at_form, pos = _read_uleb128(debug_abbrev, pos, limit)
            if at_name == 0 and at_form == 0:
                break
            implicit_val: int | None = None
            if at_form == DW_FORM_implicit_const:
                implicit_val, pos = _read_sleb128(debug_abbrev, pos, limit)
            attrs.append((at_name, at_form, implicit_val))
        table[code] = _AbbrevEntry(code=code, tag=tag, has_children=has_children, attrs=tuple(attrs))
    return table


def _decode_form(
    form: int,
    implicit_val: int | None,
    info: bytes,
    pos: int,
    cu_end: int,
    cu: _CompilationUnit,
    endian: Literal["<", ">"],
    debug_str: bytes,
    debug_line_str: bytes,
) -> tuple[Any, int]:
    while form == DW_FORM_indirect:
        form, pos = _read_uleb128(info, pos, cu_end)

    if form == DW_FORM_addr:
        val = _read_uint(info, pos, cu.address_size, endian)
        return val, pos + cu.address_size
    if form == DW_FORM_data1:
        val = _read_uint(info, pos, 1, endian)
        return _RawUnsignedConst(val, 1), pos + 1
    if form == DW_FORM_data2:
        val = _read_uint(info, pos, 2, endian)
        return _RawUnsignedConst(val, 2), pos + 2
    if form == DW_FORM_data4:
        val = _read_uint(info, pos, 4, endian)
        return _RawUnsignedConst(val, 4), pos + 4
    if form == DW_FORM_data8:
        val = _read_uint(info, pos, 8, endian)
        return _RawUnsignedConst(val, 8), pos + 8
    if form == DW_FORM_data16:
        if pos + 16 > cu_end:
            raise DwarfError("Truncated DW_FORM_data16")
        return info[pos : pos + 16], pos + 16
    if form == DW_FORM_udata:
        return _read_uleb128(info, pos, cu_end)
    if form == DW_FORM_sdata:
        return _read_sleb128(info, pos, cu_end)
    if form == DW_FORM_implicit_const:
        return implicit_val, pos
    if form == DW_FORM_flag:
        val = _read_uint(info, pos, 1, endian)
        return bool(val), pos + 1
    if form == DW_FORM_flag_present:
        return True, pos
    if form == DW_FORM_string:
        nul = info.find(b"\x00", pos, cu_end)
        if nul == -1:
            raise DwarfError("Unterminated inline DW_FORM_string")
        s = info[pos:nul].decode("utf-8", errors="replace")
        return s, nul + 1
    if form == DW_FORM_strp:
        off = _read_uint(info, pos, cu.offset_size, endian)
        return _read_cstring(debug_str, off), pos + cu.offset_size
    if form == DW_FORM_line_strp:
        off = _read_uint(info, pos, cu.offset_size, endian)
        return _read_cstring(debug_line_str, off), pos + cu.offset_size
    if form in (DW_FORM_strx, DW_FORM_GNU_str_index):
        idx, pos = _read_uleb128(info, pos, cu_end)
        return _StrxRef(idx), pos
    if form == DW_FORM_strx1:
        return _StrxRef(_read_uint(info, pos, 1, endian)), pos + 1
    if form == DW_FORM_strx2:
        return _StrxRef(_read_uint(info, pos, 2, endian)), pos + 2
    if form == DW_FORM_strx3:
        return _StrxRef(_read_uint(info, pos, 3, endian)), pos + 3
    if form == DW_FORM_strx4:
        return _StrxRef(_read_uint(info, pos, 4, endian)), pos + 4
    if form in (DW_FORM_addrx, DW_FORM_GNU_addr_index):
        idx, pos = _read_uleb128(info, pos, cu_end)
        return _AddrxRef(idx), pos
    if form == DW_FORM_addrx1:
        return _AddrxRef(_read_uint(info, pos, 1, endian)), pos + 1
    if form == DW_FORM_addrx2:
        return _AddrxRef(_read_uint(info, pos, 2, endian)), pos + 2
    if form == DW_FORM_addrx3:
        return _AddrxRef(_read_uint(info, pos, 3, endian)), pos + 3
    if form == DW_FORM_addrx4:
        return _AddrxRef(_read_uint(info, pos, 4, endian)), pos + 4
    if form == DW_FORM_ref1:
        return _DieRef(cu.cu_start + _read_uint(info, pos, 1, endian)), pos + 1
    if form == DW_FORM_ref2:
        return _DieRef(cu.cu_start + _read_uint(info, pos, 2, endian)), pos + 2
    if form == DW_FORM_ref4:
        return _DieRef(cu.cu_start + _read_uint(info, pos, 4, endian)), pos + 4
    if form == DW_FORM_ref8:
        return _DieRef(cu.cu_start + _read_uint(info, pos, 8, endian)), pos + 8
    if form == DW_FORM_ref_udata:
        rel, pos = _read_uleb128(info, pos, cu_end)
        return _DieRef(cu.cu_start + rel), pos
    if form == DW_FORM_ref_addr:
        ref_sz = cu.address_size if cu.version == 2 else cu.offset_size
        return _DieRef(_read_uint(info, pos, ref_sz, endian)), pos + ref_sz
    if form in (DW_FORM_sec_offset, DW_FORM_strp_sup, DW_FORM_GNU_ref_alt, DW_FORM_GNU_strp_alt):
        val = _read_uint(info, pos, cu.offset_size, endian)
        return val, pos + cu.offset_size
    if form in (DW_FORM_ref_sig8, DW_FORM_ref_sup8):
        val = _read_uint(info, pos, 8, endian)
        return val, pos + 8
    if form == DW_FORM_ref_sup4:
        val = _read_uint(info, pos, 4, endian)
        return val, pos + 4
    if form in (DW_FORM_loclistx, DW_FORM_rnglistx):
        return _read_uleb128(info, pos, cu_end)
    if form in (DW_FORM_exprloc, DW_FORM_block):
        blen, pos = _read_uleb128(info, pos, cu_end)
        if pos + blen > cu_end:
            raise DwarfError("Truncated DWARF block/exprloc")
        return info[pos : pos + blen], pos + blen
    if form == DW_FORM_block1:
        blen = _read_uint(info, pos, 1, endian)
        pos += 1
        if pos + blen > cu_end:
            raise DwarfError("Truncated DW_FORM_block1")
        return info[pos : pos + blen], pos + blen
    if form == DW_FORM_block2:
        blen = _read_uint(info, pos, 2, endian)
        pos += 2
        if pos + blen > cu_end:
            raise DwarfError("Truncated DW_FORM_block2")
        return info[pos : pos + blen], pos + blen
    if form == DW_FORM_block4:
        blen = _read_uint(info, pos, 4, endian)
        pos += 4
        if pos + blen > cu_end:
            raise DwarfError("Truncated DW_FORM_block4")
        return info[pos : pos + blen], pos + blen

    raise DwarfError(f"Unsupported DWARF attribute form 0x{form:X} at .debug_info+0x{pos:X}")


def _as_int(val: Any, default: int | None = None) -> int | None:
    if val is None:
        return default
    if isinstance(val, _RawUnsignedConst):
        return val.value
    if isinstance(val, bool):
        return int(val)
    if isinstance(val, int):
        return val
    return default


def _as_signed_const(val: Any, signed: bool, byte_size: int) -> int | None:
    if val is None:
        return None
    if isinstance(val, _RawUnsignedConst):
        raw = val.value
        if signed:
            bits = max(byte_size, val.byte_width) * 8
            if bits > 0 and raw >= (1 << (bits - 1)):
                raw -= 1 << bits
        return raw
    if isinstance(val, int):
        if signed and byte_size > 0 and val >= (1 << (byte_size * 8 - 1)):
            return val - (1 << (byte_size * 8))
        return val
    return None


def _resolve_strx(
    idx: int,
    cu: _CompilationUnit,
    endian: Literal["<", ">"],
    debug_str_offsets: bytes,
    debug_str: bytes,
) -> str:
    if not debug_str_offsets:
        return ""
    base = cu.str_offsets_base
    if base == 0 and cu.version >= 5:
        # DWARF v5 .debug_str_offsets 单贡献默认头部：32-bit DWARF 为 8 字节，64-bit 为 16 字节
        base = 8 if cu.offset_size == 4 else 16
    entry_pos = base + idx * cu.offset_size
    if entry_pos < 0 or entry_pos + cu.offset_size > len(debug_str_offsets):
        return ""
    str_off = _read_uint(debug_str_offsets, entry_pos, cu.offset_size, endian)
    return _read_cstring(debug_str, str_off)


def _resolve_addrx(
    idx: int,
    cu: _CompilationUnit,
    endian: Literal["<", ">"],
    debug_addr: bytes,
) -> int | None:
    if not debug_addr:
        return None
    base = cu.addr_base
    if base == 0 and cu.version >= 5:
        base = 8 if cu.offset_size == 4 else 16
    entry_pos = base + idx * cu.address_size
    if entry_pos < 0 or entry_pos + cu.address_size > len(debug_addr):
        return None
    return _read_uint(debug_addr, entry_pos, cu.address_size, endian)


def _parse_member_byte_offset(val: Any) -> int:
    """解析 DW_AT_data_member_location（支持整数常量与 DW_OP_plus_uconst / DW_OP_constu 表达式）。"""
    direct = _as_int(val)
    if direct is not None:
        return direct
    if isinstance(val, (bytes, bytearray)) and len(val) > 0:
        op = val[0]
        if op == DW_OP_plus_uconst:
            off, _ = _read_uleb128(val, 1, len(val))
            return off
        if DW_OP_lit0 <= op <= DW_OP_lit31:
            return op - DW_OP_lit0
        if op == DW_OP_constu:
            off, _ = _read_uleb128(val, 1, len(val))
            return off
        if op == DW_OP_consts:
            off, _ = _read_sleb128(val, 1, len(val))
            return off
    return 0


def _parse_location_address(
    loc_val: Any,
    cu: _CompilationUnit,
    endian: Literal["<", ">"],
    debug_addr: bytes,
) -> int | None:
    """从 DW_AT_location 表达式块提取静态内存地址（支持 DW_OP_addr / DW_OP_addrx + 偏移）。"""
    if isinstance(loc_val, _AddrxRef):
        return _resolve_addrx(loc_val.index, cu, endian, debug_addr)
    if not isinstance(loc_val, (bytes, bytearray)) or not loc_val:
        return None

    pos = 0
    limit = len(loc_val)
    op = loc_val[pos]
    pos += 1
    addr: int | None = None

    if op == DW_OP_addr:
        if pos + cu.address_size > limit:
            return None
        addr = _read_uint(loc_val, pos, cu.address_size, endian)
        pos += cu.address_size
    elif op in (DW_OP_addrx, DW_OP_GNU_addr_index):
        idx, pos = _read_uleb128(loc_val, pos, limit)
        addr = _resolve_addrx(idx, cu, endian, debug_addr)
    else:
        return None

    if addr is None:
        return None

    # 支持紧跟在地址后面的常量偏移运算（如 &arr[2] 或全局子对象表达式）
    while pos < limit:
        next_op = loc_val[pos]
        pos += 1
        if next_op == DW_OP_plus_uconst:
            addend, pos = _read_uleb128(loc_val, pos, limit)
            addr += addend
        elif next_op == DW_OP_stack_value:
            break
        elif DW_OP_lit0 <= next_op <= DW_OP_lit31 and pos < limit and loc_val[pos] == DW_OP_plus:
            addr += next_op - DW_OP_lit0
            pos += 1
        elif next_op == DW_OP_const1u and pos + 2 <= limit and loc_val[pos + 1] == DW_OP_plus:
            addr += loc_val[pos]
            pos += 2
        elif next_op == DW_OP_const2u and pos + 3 <= limit and loc_val[pos + 2] == DW_OP_plus:
            addr += _read_uint(loc_val, pos, 2, endian)
            pos += 3
        elif next_op == DW_OP_const4u and pos + 5 <= limit and loc_val[pos + 4] == DW_OP_plus:
            addr += _read_uint(loc_val, pos, 4, endian)
            pos += 5
        else:
            # 含有寄存器/帧基址等动态位置操作码，不作为纯静态全局地址处理
            return None

    return addr


# ==========================================================================
# DWARF 全量构建与无 DWARF 符号表优雅降级
# ==========================================================================

_INDEX_CACHE: dict[tuple[str, int], DwarfIndex] = {}


def _build_symtab_fallback_type(size: int) -> DwarfType:
    u8 = BaseType(name="uint8_t", byte_size=1, encoding=DW_ATE_unsigned)
    if size == 1:
        return u8
    if size == 2:
        return BaseType(name="uint16_t", byte_size=2, encoding=DW_ATE_unsigned)
    if size == 4 or size <= 0:
        return BaseType(name="uint32_t", byte_size=4, encoding=DW_ATE_unsigned)
    if size == 8:
        return BaseType(name="uint64_t", byte_size=8, encoding=DW_ATE_unsigned)
    return ArrayType(element_type=u8, dimensions=(size,), explicit_byte_size=size)


def _collect_object_symbols(elf_path: Path) -> dict[str, ElfSymbol]:
    """收集 ELF .symtab / .dynsym 中的有效数据对象符号。"""
    by_name: dict[str, ElfSymbol] = {}
    for sym in read_elf_symbols(elf_path):
        if sym.shndx == 0 or sym.address == 0:
            continue
        if sym.sym_type not in ("object", "notype"):
            continue
        # 跳过编译器内部局部标号（如 $d / .L*）
        if sym.name.startswith(("$", ".L")):
            continue
        prev = by_name.get(sym.name)
        if prev is None or (prev.sym_type != "object" and sym.sym_type == "object") or sym.size > prev.size:
            by_name[sym.name] = sym
    return by_name


def _build_symtab_only_index(img: _ElfImage, obj_syms: dict[str, ElfSymbol]) -> DwarfIndex:
    ptr_size = 4 if img.ei_class == 1 else 8
    variables: dict[str, GlobalVariable] = {}
    for name, sym in obj_syms.items():
        dtype = _build_symtab_fallback_type(sym.size)
        eff_size = dtype.byte_size
        variables[name] = GlobalVariable(
            name=name,
            address=sym.address,
            byte_size=eff_size,
            type_name=f"{dtype.display_name} (symtab)",
            dwarf_type=dtype,
            source="symtab",
        )
    return DwarfIndex(
        elf_path=str(img.path),
        endian=img.endian,
        pointer_size=ptr_size,
        dwarf_versions=(),
        degraded_symtab_only=True,
        variables=variables,
        types={},
    )


def load_dwarf_index(elf_path: str | os.PathLike) -> DwarfIndex:
    """解析 ELF + DWARF (v2~v5) 类型与全局变量表，按 (resolved_path, mtime_ns) 内存缓存。"""
    p = Path(elf_path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"ELF file not found: {p}")

    cache_key = (str(p), p.stat().st_mtime_ns)
    cached = _INDEX_CACHE.get(cache_key)
    if cached is not None:
        return cached

    img = _parse_elf_image(p)
    obj_syms = _collect_object_symbols(p)

    debug_info = img.sections_by_name.get(".debug_info", b"")
    debug_abbrev = img.sections_by_name.get(".debug_abbrev", b"")
    if not debug_info or not debug_abbrev:
        index = _build_symtab_only_index(img, obj_syms)
        _INDEX_CACHE[cache_key] = index
        return index

    debug_str = img.sections_by_name.get(".debug_str", b"")
    debug_line_str = img.sections_by_name.get(".debug_line_str", b"")
    debug_str_offsets = img.sections_by_name.get(".debug_str_offsets", b"")
    debug_addr = img.sections_by_name.get(".debug_addr", b"")

    abbrev_cache: dict[int, dict[int, _AbbrevEntry]] = {}
    cus: list[_CompilationUnit] = []
    dies_by_offset: dict[int, _RawDie] = {}
    cu_roots: list[_RawDie] = []
    versions_seen: list[int] = []

    pos = 0
    info_len = len(debug_info)
    while pos < info_len:
        cu_start = pos
        if pos + 4 > info_len:
            break
        u32_len = _read_uint(debug_info, pos, 4, img.endian)
        pos += 4
        if u32_len == 0:
            continue
        if u32_len == 0xFFFFFFFF:
            offset_size = 8
            unit_length = _read_uint(debug_info, pos, 8, img.endian)
            pos += 8
        else:
            offset_size = 4
            unit_length = u32_len

        cu_end = pos + unit_length
        if cu_end > info_len:
            raise DwarfError(f"Compilation unit at 0x{cu_start:X} exceeds .debug_info bounds")

        version = _read_uint(debug_info, pos, 2, img.endian)
        pos += 2
        if version not in (2, 3, 4, 5):
            raise DwarfError(f"Unsupported DWARF version {version} at .debug_info+0x{cu_start:X}")
        if version not in versions_seen:
            versions_seen.append(version)

        if version <= 4:
            abbrev_off = _read_uint(debug_info, pos, offset_size, img.endian)
            pos += offset_size
            address_size = _read_uint(debug_info, pos, 1, img.endian)
            pos += 1
        else:
            unit_type = _read_uint(debug_info, pos, 1, img.endian)
            pos += 1
            address_size = _read_uint(debug_info, pos, 1, img.endian)
            pos += 1
            abbrev_off = _read_uint(debug_info, pos, offset_size, img.endian)
            pos += offset_size
            if unit_type in (DW_UT_skeleton, DW_UT_split_compile):
                pos += 8
            elif unit_type in (DW_UT_type, DW_UT_split_type):
                pos += 8 + offset_size

        abbrev_table = abbrev_cache.get(abbrev_off)
        if abbrev_table is None:
            abbrev_table = _parse_abbrev_table(debug_abbrev, abbrev_off)
            abbrev_cache[abbrev_off] = abbrev_table

        cu_idx = len(cus)
        cu = _CompilationUnit(
            cu_start=cu_start,
            version=version,
            address_size=address_size,
            offset_size=offset_size,
        )
        cus.append(cu)

        stack: list[_RawDie] = []
        cu_dies: list[_RawDie] = []
        while pos < cu_end:
            die_offset = pos
            code, pos = _read_uleb128(debug_info, pos, cu_end)
            if code == 0:
                if stack:
                    stack.pop()
                continue
            entry = abbrev_table.get(code)
            if entry is None:
                raise DwarfError(f"Unknown abbrev code {code} at .debug_info+0x{die_offset:X}")

            attrs: dict[int, Any] = {}
            for at_name, at_form, implicit_val in entry.attrs:
                val, pos = _decode_form(
                    at_form,
                    implicit_val,
                    debug_info,
                    pos,
                    cu_end,
                    cu,
                    img.endian,
                    debug_str,
                    debug_line_str,
                )
                attrs[at_name] = val

            if not stack and entry.tag in (DW_TAG_compile_unit, DW_TAG_partial_unit):
                str_base = _as_int(attrs.get(DW_AT_str_offsets_base))
                if str_base is not None:
                    cu.str_offsets_base = str_base
                addr_base = _as_int(attrs.get(DW_AT_addr_base))
                if addr_base is None:
                    addr_base = _as_int(attrs.get(DW_AT_GNU_addr_base))
                if addr_base is not None:
                    cu.addr_base = addr_base

            parent_tag = stack[-1].tag if stack else 0
            parent_scope = stack[-1].scope_name if stack else ""
            raw_die = _RawDie(
                offset=die_offset,
                cu_idx=cu_idx,
                tag=entry.tag,
                attrs=attrs,
                parent_tag=parent_tag,
                scope_name=parent_scope,
            )
            dies_by_offset[die_offset] = raw_die
            cu_dies.append(raw_die)
            if stack:
                stack[-1].children.append(raw_die)
            else:
                cu_roots.append(raw_die)
            if entry.has_children:
                stack.append(raw_die)

        # 回填延迟解析的 DW_FORM_strx* 字符串与函数作用域名称
        for d in cu_dies:
            for k, v in list(d.attrs.items()):
                if isinstance(v, _StrxRef):
                    d.attrs[k] = _resolve_strx(v.index, cu, img.endian, debug_str_offsets, debug_str)
            if d.tag in (DW_TAG_subprogram, DW_TAG_namespace):
                own = str(d.attrs.get(DW_AT_name) or "")
                if own:
                    sub_scope = f"{d.scope_name}::{own}" if d.scope_name else own
                    for ch in d.children:
                        _propagate_scope(ch, sub_scope)

        pos = cu_end

    # ---- 构建类型图（支持自引用链表结构体与前向声明自动归并） ------------
    complete_structs_by_name: dict[tuple[bool, str], _RawDie] = {}
    for d in dies_by_offset.values():
        if d.tag in (DW_TAG_structure_type, DW_TAG_union_type):
            sname = str(d.attrs.get(DW_AT_name) or "")
            is_decl = bool(d.attrs.get(DW_AT_declaration))
            if sname and not is_decl and d.children:
                complete_structs_by_name[(d.tag == DW_TAG_union_type, sname)] = d

    resolved_types: dict[int, DwarfType] = {}

    def get_die_attr(d: _RawDie, attr: int, _depth: int = 0) -> Any:
        if attr in d.attrs:
            return d.attrs[attr]
        if _depth > 4:
            return None
        for ref_attr in (DW_AT_specification, DW_AT_abstract_origin):
            ref = d.attrs.get(ref_attr)
            if isinstance(ref, _DieRef) and ref.info_offset in dies_by_offset:
                found = get_die_attr(dies_by_offset[ref.info_offset], attr, _depth + 1)
                if found is not None:
                    return found
        return None

    def build_type(type_ref: Any) -> DwarfType:
        if not isinstance(type_ref, _DieRef):
            return VoidType()
        off = type_ref.info_offset
        if off in resolved_types:
            return resolved_types[off]
        d = dies_by_offset.get(off)
        if d is None:
            return VoidType()

        cu = cus[d.cu_idx]
        tag = d.tag

        if tag == DW_TAG_base_type:
            bname = str(d.attrs.get(DW_AT_name) or "int")
            bsize = _as_int(d.attrs.get(DW_AT_byte_size), 0) or 0
            benc = _as_int(d.attrs.get(DW_AT_encoding), DW_ATE_unsigned) or DW_ATE_unsigned
            res: DwarfType = BaseType(name=bname, byte_size=bsize, encoding=benc)
            resolved_types[off] = res
            return res

        if tag in (DW_TAG_typedef, DW_TAG_const_type, DW_TAG_volatile_type, DW_TAG_restrict_type, DW_TAG_atomic_type):
            qual_map = {
                DW_TAG_typedef: "typedef",
                DW_TAG_const_type: "const",
                DW_TAG_volatile_type: "volatile",
                DW_TAG_restrict_type: "restrict",
                DW_TAG_atomic_type: "atomic",
            }
            placeholder = QualifiedType(
                qualifier=qual_map[tag],
                target=VoidType(),
                alias_name=str(d.attrs.get(DW_AT_name) or ""),
            )
            resolved_types[off] = placeholder
            placeholder.target = build_type(d.attrs.get(DW_AT_type))
            return placeholder

        if tag in (DW_TAG_pointer_type, DW_TAG_reference_type, DW_TAG_rvalue_reference_type):
            psize = _as_int(d.attrs.get(DW_AT_byte_size), cu.address_size) or cu.address_size
            ptr = PointerType(byte_size=psize, target_type=VoidType())
            resolved_types[off] = ptr
            ptr.target_type = build_type(d.attrs.get(DW_AT_type))
            return ptr

        if tag == DW_TAG_enumeration_type:
            ename = str(d.attrs.get(DW_AT_name) or "")
            underlying = build_type(d.attrs.get(DW_AT_type)).resolve()
            esize = _as_int(d.attrs.get(DW_AT_byte_size))
            if esize is None:
                esize = underlying.byte_size if underlying.byte_size > 0 else 4
            enc = _as_int(d.attrs.get(DW_AT_encoding))
            if enc is not None:
                signed = enc in (DW_ATE_signed, DW_ATE_signed_char)
            elif isinstance(underlying, BaseType):
                signed = underlying.is_signed
            else:
                signed = True

            enums: dict[int, str] = {}
            for ch in d.children:
                if ch.tag != DW_TAG_enumerator:
                    continue
                ev_name = str(ch.attrs.get(DW_AT_name) or "")
                ev_val = _as_signed_const(ch.attrs.get(DW_AT_const_value), signed, esize)
                if ev_name and ev_val is not None:
                    enums[ev_val] = ev_name
            res = EnumType(name=ename, byte_size=esize, signed=signed, enumerators=enums)
            resolved_types[off] = res
            return res

        if tag in (DW_TAG_structure_type, DW_TAG_union_type, DW_TAG_class_type):
            is_union = tag == DW_TAG_union_type
            sname = str(d.attrs.get(DW_AT_name) or "")
            is_decl = bool(d.attrs.get(DW_AT_declaration))
            if is_decl and sname and (is_union, sname) in complete_structs_by_name:
                full_die = complete_structs_by_name[(is_union, sname)]
                if full_die.offset != off:
                    res = build_type(_DieRef(full_die.offset))
                    resolved_types[off] = res
                    return res

            ssize = _as_int(d.attrs.get(DW_AT_byte_size), 0) or 0
            st = StructType(name=sname, byte_size=ssize, is_union=is_union, is_declaration=is_decl)
            resolved_types[off] = st
            st.members = tuple(_collect_struct_members(d, build_type, img.endian, base_byte_offset=0))
            return st

        if tag == DW_TAG_array_type:
            arr = ArrayType(
                element_type=VoidType(),
                dimensions=(),
                explicit_byte_size=_as_int(d.attrs.get(DW_AT_byte_size)),
            )
            resolved_types[off] = arr
            arr.element_type = build_type(d.attrs.get(DW_AT_type))
            dims: list[int] = []
            for ch in d.children:
                if ch.tag != DW_TAG_subrange_type:
                    continue
                cnt = _as_int(ch.attrs.get(DW_AT_count))
                if cnt is None:
                    ub = _as_int(ch.attrs.get(DW_AT_upper_bound))
                    lb = _as_int(ch.attrs.get(DW_AT_lower_bound), 0) or 0
                    cnt = (ub - lb + 1) if ub is not None and ub >= lb else 0
                dims.append(max(cnt, 0))
            arr.dimensions = tuple(dims) if dims else (0,)
            return arr

        # 函数指针底层 DW_TAG_subroutine_type 等归为 0 字节可指向符号类型
        sub_fn = VoidType(name=str(d.attrs.get(DW_AT_name) or "fn()"))
        resolved_types[off] = sub_fn
        return sub_fn

    # ---- 提取全局与静态变量 ----------------------------------------------
    variables: dict[str, GlobalVariable] = {}
    all_dies_list = list(dies_by_offset.values())

    for d in all_dies_list:
        if d.tag != DW_TAG_variable:
            continue
        vname = str(get_die_attr(d, DW_AT_name) or "")
        if not vname:
            continue

        cu = cus[d.cu_idx]
        is_top_level = d.parent_tag in (DW_TAG_compile_unit, DW_TAG_partial_unit, DW_TAG_namespace)
        loc_attr = d.attrs.get(DW_AT_location)
        dwarf_addr = _parse_location_address(loc_attr, cu, img.endian, debug_addr)
        is_decl = bool(d.attrs.get(DW_AT_declaration))
        is_external = bool(get_die_attr(d, DW_AT_external))

        # 栈帧局部变量无静态 DW_OP_addr 且非顶层/external，必须过滤
        if not is_top_level and dwarf_addr is None and not is_external:
            continue

        linkage_name = str(
            get_die_attr(d, DW_AT_linkage_name)
            or get_die_attr(d, DW_AT_MIPS_linkage_name)
            or vname
        )
        sym = obj_syms.get(linkage_name) or obj_syms.get(vname)

        addr: int | None = None
        source = "dwarf"
        if dwarf_addr is not None and dwarf_addr > 0:
            addr = dwarf_addr
        elif sym is not None and sym.address > 0:
            addr = sym.address
            source = "dwarf+symtab"
        elif dwarf_addr == 0 and loc_attr is not None and not is_decl:
            # 兼容链接在地址 0 的特殊裸机段或可重定位对象测试
            addr = 0

        if addr is None:
            continue

        dtype = build_type(get_die_attr(d, DW_AT_type))
        bsize = dtype.byte_size
        if bsize <= 0 and sym is not None and sym.size > 0:
            bsize = sym.size

        var_obj = GlobalVariable(
            name=vname,
            address=addr,
            byte_size=bsize,
            type_name=dtype.display_name,
            dwarf_type=dtype,
            source=source,
        )

        if is_top_level:
            prev = variables.get(vname)
            # 真实定义优先于前向 extern 声明
            if prev is None or (prev.source != "dwarf" and source == "dwarf"):
                variables[vname] = var_obj
        else:
            scoped_key = f"{d.scope_name}::{vname}" if d.scope_name else vname
            variables[scoped_key] = var_obj
            if vname not in variables:
                variables[vname] = var_obj

    # 补充仅出现在 .symtab 中（如汇编文件或无 -g 库目标）的全局对象符号
    for sym_name, sym in obj_syms.items():
        if sym_name not in variables and "." not in sym_name:
            fb_type = _build_symtab_fallback_type(sym.size)
            variables[sym_name] = GlobalVariable(
                name=sym_name,
                address=sym.address,
                byte_size=fb_type.byte_size,
                type_name=f"{fb_type.display_name} (symtab)",
                dwarf_type=fb_type,
                source="symtab",
            )

    ptr_size = cus[0].address_size if cus else (4 if img.ei_class == 1 else 8)
    index = DwarfIndex(
        elf_path=str(p),
        endian=img.endian,
        pointer_size=ptr_size,
        dwarf_versions=tuple(versions_seen),
        degraded_symtab_only=False,
        variables=variables,
        types=resolved_types,
    )
    _INDEX_CACHE[cache_key] = index
    return index


def _propagate_scope(node: _RawDie, scope: str) -> None:
    node.scope_name = scope
    if node.tag in (DW_TAG_subprogram, DW_TAG_namespace):
        own = str(node.attrs.get(DW_AT_name) or "")
        next_scope = f"{scope}::{own}" if own else scope
    else:
        next_scope = scope
    for ch in node.children:
        _propagate_scope(ch, next_scope)


def _collect_struct_members(
    struct_die: _RawDie,
    build_type: Any,
    endian: Literal["<", ">"],
    base_byte_offset: int = 0,
) -> list[StructMember]:
    members: list[StructMember] = []
    for ch in struct_die.children:
        if ch.tag != DW_TAG_member:
            continue
        mname = str(ch.attrs.get(DW_AT_name) or "")
        mtype = build_type(ch.attrs.get(DW_AT_type))
        rel_byte_off = _parse_member_byte_offset(ch.attrs.get(DW_AT_data_member_location))
        abs_byte_off = base_byte_offset + rel_byte_off

        bit_size = _as_int(ch.attrs.get(DW_AT_bit_size))
        data_bit_off = _as_int(ch.attrs.get(DW_AT_data_bit_offset))
        legacy_bit_off = _as_int(ch.attrs.get(DW_AT_bit_offset))

        norm_bit_off: int | None = None
        if bit_size is not None and bit_size > 0:
            if data_bit_off is not None:
                norm_bit_off = base_byte_offset * 8 + data_bit_off
            elif legacy_bit_off is not None:
                container_bytes = _as_int(ch.attrs.get(DW_AT_byte_size))
                if not container_bytes:
                    container_bytes = mtype.byte_size or 4
                if endian == "<":
                    norm_bit_off = abs_byte_off * 8 + (container_bytes * 8 - legacy_bit_off - bit_size)
                else:
                    norm_bit_off = abs_byte_off * 8 + legacy_bit_off
            else:
                norm_bit_off = abs_byte_off * 8
            abs_byte_off = norm_bit_off // 8

        # C11 匿名嵌套 struct / union 成员自动展平到外层命名空间
        resolved_mtype = mtype.resolve()
        if not mname and isinstance(resolved_mtype, StructType):
            for sub in resolved_mtype.members:
                sub_bit_off = (abs_byte_off * 8 + sub.bit_offset) if sub.bit_offset is not None else None
                members.append(
                    StructMember(
                        name=sub.name,
                        member_type=sub.member_type,
                        byte_offset=abs_byte_off + sub.byte_offset if sub_bit_off is None else sub_bit_off // 8,
                        bit_size=sub.bit_size,
                        bit_offset=sub_bit_off,
                    )
                )
            continue

        if mname:
            members.append(
                StructMember(
                    name=mname,
                    member_type=mtype,
                    byte_offset=abs_byte_off,
                    bit_size=bit_size if norm_bit_off is not None else None,
                    bit_offset=norm_bit_off,
                )
            )
    return members
