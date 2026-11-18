"""ELF 符号表解析单元测试（覆盖真实 ELF64 与构造的 ARM ELF32）。"""

from __future__ import annotations

import shutil
import struct
from pathlib import Path

import pytest

from mcu_harness.build import build_firmware
from mcu_harness.elf import ElfError, find_symbol, read_elf_symbols

DEMO = Path(__file__).parent.parent / "examples" / "demo_fw"


def _make_minimal_elf32(symbols: list[tuple[str, int, int] | tuple[str, int, int, int]]) -> bytes:
    """构造一个最小合法的 ARM little-endian ELF32（含 .strtab 与 .symtab）。

    元素可为 (name, addr, size) 或 (name, addr, size, st_info)。
    """
    strtab = bytearray(b"\x00")
    sym_entries = bytearray(struct.pack("<IIIBBH", 0, 0, 0, 0, 0, 0))  # STN_UNDEF
    for item in symbols:
        if len(item) == 4:
            name, addr, size, st_info = item
        else:
            name, addr, size = item
            st_info = 0x11  # STB_GLOBAL | STT_OBJECT
        name_off = len(strtab)
        strtab.extend(name.encode("utf-8") + b"\x00")
        sym_entries.extend(struct.pack("<IIIBBH", name_off, addr, size, st_info, 0, 1))

    ehdr_size = 52
    str_off = ehdr_size
    sym_off = str_off + len(strtab)
    shoff = sym_off + len(sym_entries)

    ehdr = bytearray(ehdr_size)
    ehdr[0:4] = b"\x7fELF"
    ehdr[4] = 1  # ELFCLASS32
    ehdr[5] = 1  # ELFDATA2LSB
    ehdr[6] = 1  # EV_CURRENT
    struct.pack_into("<HHIIIIIHHHHHH", ehdr, 16, 2, 40, 1, 0, 0, shoff, 0, ehdr_size, 0, 0, 40, 3, 0)

    # 3 个 Section Header: NULL (0), SHT_STRTAB (1), SHT_SYMTAB (2 -> sh_link=1)
    sh0 = struct.pack("<IIIIIIIIII", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    sh1 = struct.pack("<IIIIIIIIII", 0, 3, 0, 0, str_off, len(strtab), 0, 0, 1, 0)
    sh2 = struct.pack("<IIIIIIIIII", 0, 2, 0, 0, sym_off, len(sym_entries), 1, 1, 4, 16)
    return bytes(ehdr) + bytes(strtab) + bytes(sym_entries) + sh0 + sh1 + sh2


def test_parse_elf32_segger_rtt_symbol(tmp_path):
    elf = tmp_path / "fw.elf"
    elf.write_bytes(_make_minimal_elf32([("_SEGGER_RTT", 0x20005170, 168), ("main", 0x08000401, 64)]))

    sym = find_symbol(elf, "_SEGGER_RTT")
    assert sym is not None
    assert sym.address == 0x20005170
    assert sym.size == 168
    assert find_symbol(elf, "nonexistent") is None


@pytest.mark.skipif(
    not (shutil.which("cmake") and shutil.which("ninja") and shutil.which("gcc")),
    reason="需要 cmake + ninja + gcc",
)
def test_parse_real_gcc_elf64(tmp_path):
    proj = tmp_path / "demo_fw"
    shutil.copytree(DEMO, proj)
    r = build_firmware(proj)
    assert r.success
    elf_path = proj / r.artifacts[0].path
    sym = find_symbol(elf_path, "main")
    assert sym is not None and sym.address > 0


def test_invalid_elf_raises(tmp_path):
    bad = tmp_path / "not_an_elf.bin"
    bad.write_bytes(b"NOT_ELF" * 16)
    with pytest.raises(ElfError):
        read_elf_symbols(bad)


def test_thumb_function_lsb_cleared_and_cache_and_bounds(tmp_path, monkeypatch):
    """验证 Thumb 函数符号地址最低位清零、缓存命中与节表越界保护（FIX-06）。"""
    elf = tmp_path / "thumb.elf"
    elf.write_bytes(
        _make_minimal_elf32(
            [
                ("HardFault_Handler", 0x08000401, 32, 0x12),  # STT_FUNC -> 清零最低位
                ("odd_const", 0x20000001, 4, 0x11),           # STT_OBJECT -> 保持原值
            ]
        )
    )

    fn = find_symbol(elf, "HardFault_Handler")
    obj = find_symbol(elf, "odd_const")
    assert fn is not None and fn.address == 0x08000400 and fn.sym_type == "func"
    assert obj is not None and obj.address == 0x20000001 and obj.sym_type == "object"

    # 再次查询同一未修改的文件应直接命中缓存，不再读盘
    read_calls = 0
    orig_read_bytes = Path.read_bytes

    def counting_read_bytes(self):
        nonlocal read_calls
        read_calls += 1
        return orig_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counting_read_bytes)
    assert find_symbol(elf, "HardFault_Handler") == fn
    assert read_calls == 0

    # 节表偏移越界应抛出 ElfError
    corrupted = tmp_path / "corrupt.elf"
    raw = bytearray(elf.read_bytes())
    struct.pack_into("<I", raw, 32, 0x0FFFFFFF)  # e_shoff 越界
    corrupted.write_bytes(bytes(raw))
    with pytest.raises(ElfError, match="out of bounds"):
        read_elf_symbols(corrupted)
