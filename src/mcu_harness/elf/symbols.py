"""轻量级 ELF 符号表解析器（纯标准库，不依赖外部 nm 二进制）。

用途与设计要点（FIX-06）：
    * 从固件 .elf 中查找 `_SEGGER_RTT` 控制块地址，跳过全 RAM 扫描秒连 RTT。
    * 识别函数类型符号（STT_FUNC = 2），并将 ARM Thumb 函数地址最低位（LSB=1）清零，
      归一化为偶数对齐的真实指令起始地址，供 Phase 2 死机 PC 反查函数使用。
    * 校验节表、符号表与字符串表偏移及长度边界，防止损坏的 ELF 文件触发越界异常。
    * 按 (文件绝对路径, mtime_ns) 内存缓存解析结果，避免重复解析同一 .elf。
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path

_ELF_MAGIC = b"\x7fELF"
_SHT_SYMTAB = 2
_SHT_DYNSYM = 11

_STT_NOTYPE = 0
_STT_OBJECT = 1
_STT_FUNC = 2
_STT_SECTION = 3
_STT_FILE = 4

_SYM_TYPE_NAMES = {
    _STT_NOTYPE: "notype",
    _STT_OBJECT: "object",
    _STT_FUNC: "func",
    _STT_SECTION: "section",
    _STT_FILE: "file",
}

# 缓存：(resolved_path, mtime_ns) -> tuple[ElfSymbol, ...]
_CACHE: dict[tuple[str, int], tuple[ElfSymbol, ...]] = {}


class ElfError(RuntimeError):
    """ELF 解析失败。"""


@dataclass(frozen=True)
class ElfSymbol:
    name: str
    address: int
    size: int
    shndx: int
    sym_type: str = "notype"


def read_elf_symbols(elf_path: str | os.PathLike) -> list[ElfSymbol]:
    """读取 ELF 文件 .symtab（或回退 .dynsym）中的全部符号，带内存缓存。"""
    p = Path(elf_path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"ELF file not found: {p}")

    key = (str(p), p.stat().st_mtime_ns)
    if key in _CACHE:
        return list(_CACHE[key])

    data = p.read_bytes()
    if len(data) < 52 or data[:4] != _ELF_MAGIC:
        raise ElfError(f"Not a valid ELF file: {p}")

    ei_class = data[4]  # 1=ELF32, 2=ELF64
    ei_data = data[5]   # 1=little-endian, 2=big-endian
    if ei_class not in (1, 2) or ei_data not in (1, 2):
        raise ElfError(f"Unsupported ELF class/endian ({ei_class}, {ei_data}) in {p}")
    endian = "<" if ei_data == 1 else ">"

    if ei_class == 1:
        e_shoff = struct.unpack_from(f"{endian}I", data, 32)[0]
        e_shentsize, e_shnum = struct.unpack_from(f"{endian}HH", data, 46)
        sh_fmt = f"{endian}IIIIIIIIII"
        sym_fmt = f"{endian}IIIBBH"
    else:
        if len(data) < 64:
            raise ElfError(f"Truncated ELF64 header in {p}")
        e_shoff = struct.unpack_from(f"{endian}Q", data, 40)[0]
        e_shentsize, e_shnum = struct.unpack_from(f"{endian}HH", data, 58)
        sh_fmt = f"{endian}IIQQQQIIQQ"
        sym_fmt = f"{endian}IBBHQQ"

    if e_shoff == 0 or e_shnum == 0:
        _CACHE[key] = ()
        return []

    sh_struct_size = struct.calcsize(sh_fmt)
    if e_shentsize < sh_struct_size or e_shoff < 0 or e_shoff + e_shnum * e_shentsize > len(data):
        raise ElfError(f"Corrupted ELF section header table out of bounds in {p}")

    sections = []
    for idx in range(e_shnum):
        off = e_shoff + idx * e_shentsize
        fields = struct.unpack_from(sh_fmt, data, off)
        sections.append(fields)

    sym_secs = [s for s in sections if s[1] == _SHT_SYMTAB]
    if not sym_secs:
        sym_secs = [s for s in sections if s[1] == _SHT_DYNSYM]
    if not sym_secs:
        _CACHE[key] = ()
        return []

    sym_size = struct.calcsize(sym_fmt)
    symbols: list[ElfSymbol] = []
    for sec in sym_secs:
        _, _, _, _, sh_offset, sh_size, sh_link, _, _, sh_entsize = sec
        step = sh_entsize or sym_size
        if step < sym_size:
            raise ElfError(f"Corrupted ELF symbol entry size ({step} < {sym_size}) in {p}")
        if sh_offset < 0 or sh_size < 0 or sh_offset + sh_size > len(data):
            raise ElfError(f"Corrupted ELF symbol table section out of bounds in {p}")
        if sh_link >= len(sections):
            raise ElfError(f"Corrupted ELF symbol string table link ({sh_link}) in {p}")

        _, _, _, _, str_off, str_size, *_ = sections[sh_link]
        if str_off < 0 or str_size < 0 or str_off + str_size > len(data):
            raise ElfError(f"Corrupted ELF string table out of bounds in {p}")
        strtab = data[str_off : str_off + str_size]

        pos = sh_offset
        end = sh_offset + sh_size
        while pos + sym_size <= end:
            if ei_class == 1:
                st_name, st_value, st_size, st_info, _, st_shndx = struct.unpack_from(sym_fmt, data, pos)
            else:
                st_name, st_info, _, st_shndx, st_value, st_size = struct.unpack_from(sym_fmt, data, pos)
            pos += step
            if st_name == 0:
                continue
            if st_name < 0 or st_name >= len(strtab):
                raise ElfError(f"Corrupted ELF symbol name offset ({st_name}) out of string table bounds in {p}")
            nul = strtab.find(b"\x00", st_name)
            raw_name = strtab[st_name:nul] if nul != -1 else strtab[st_name:]
            if not raw_name:
                continue

            st_type = st_info & 0x0F
            type_name = _SYM_TYPE_NAMES.get(st_type, "other")
            # ARM Thumb 函数符号最低位置 1 表示 Thumb 状态，归一化为偶数对齐的真实指令地址
            norm_addr = (st_value & ~1) if st_type == _STT_FUNC else st_value
            symbols.append(
                ElfSymbol(
                    name=raw_name.decode("utf-8", errors="replace"),
                    address=norm_addr,
                    size=st_size,
                    shndx=st_shndx,
                    sym_type=type_name,
                )
            )

    _CACHE[key] = tuple(symbols)
    return list(symbols)


def find_symbol(elf_path: str | os.PathLike, name: str) -> ElfSymbol | None:
    """按精确名称查找符号，未找到返回 None。"""
    for sym in read_elf_symbols(elf_path):
        if sym.name == name:
            return sym
    return None
