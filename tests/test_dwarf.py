"""纯标准库 DWARF (v2~v5) 类型解析、无停机 Live Watch 与高频趋势采样单元测试。

同时兼容：
    * `PYTHONPATH=src python3 tests/test_dwarf.py -v`
    * `PYTHONPATH=src python3 -m unittest discover -s tests -p "test_dwarf.py" -v`
    * `pytest`
"""

from __future__ import annotations

import io
import json
import math
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from mcu_harness.dwarf import (
    DwarfError,
    LiveWatchError,
    extract_elf_initialized_memory,
    load_dwarf_index,
    read_variable,
    read_variables,
    sample_variable,
)
from mcu_harness.dwarf.cli import main as watch_cli_main
from mcu_harness.probe.mock import MockProbe

_C_FIXTURE_SRC = r"""
#include <stdint.h>
#include <stdbool.h>

typedef enum {
    CTRL_IDLE  = 0,
    CTRL_RUN   = 1,
    CTRL_FAULT = 2,
    CTRL_ESTOP = -2
} ctrl_mode_t;

typedef struct {
    float kp;
    float ki;
    float kd;
} pid_cfg_t;

typedef struct {
    int16_t raw_mv;
    uint8_t valid;
} sensor_ch_t;

typedef union {
    uint32_t raw;
    struct {
        uint16_t lo;
        uint16_t hi;
    };
} reg_word_t;

typedef struct {
    ctrl_mode_t mode;
    pid_cfg_t pid;
    int16_t channels[4];
    sensor_ch_t sensors[3];
    char name[16];
    uint32_t ready : 1;
    uint32_t err_code : 5;
    int32_t signed_trim : 6;
    reg_word_t status_reg;
    const pid_cfg_t *active_pid;
    const pid_cfg_t *backup_pid;
    bool enabled;
} ctrl_state_t;

static uint32_t s_loop_cnt = 1000;

volatile ctrl_state_t g_ctrl = {
    .mode = CTRL_FAULT,
    .pid = { 1.5f, 0.25f, -0.125f },
    .channels = { -10, 200, 305, -4096 },
    .sensors = {
        { 1200, 1 },
        { 2450, 1 },
        { 3295, 0 }
    },
    .name = { 'S','T','M','3','2','H','5','-','F','O','C','\0','X','Y','Z','!' },
    .ready = 1,
    .err_code = 19,
    .signed_trim = -11,
    .status_reg = { .raw = 0xCAFE1234u },
    .active_pid = (const pid_cfg_t *)&g_ctrl.pid,
    .backup_pid = 0,
    .enabled = true,
};

uint32_t g_plain_u32 = 0x13572468u;
uint16_t g_plain_u16 = 0xBEEFu;
uint8_t  g_plain_u8  = 0xA5u;

int main(void) {
    return (int)s_loop_cnt + g_ctrl.channels[0] + (int)g_plain_u8;
}
"""


class _CountingMockProbe(MockProbe):
    """统计 read_memory 总线调用次数并监控 halt 是否被误调。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.read_memory_calls = 0
        self.halt_calls = 0

    def read_memory(self, address: int, num_bytes: int) -> bytes:
        self.read_memory_calls += 1
        return super().read_memory(address, num_bytes)

    def halt(self) -> bool:
        self.halt_calls += 1
        return super().halt()


def _load_elf_into_mock(probe: MockProbe, elf_path: Path) -> None:
    for seg_addr, seg_bytes in extract_elf_initialized_memory(elf_path).items():
        for off, b in enumerate(seg_bytes):
            probe._mem[seg_addr + off] = b


def _make_minimal_symtab_elf32(symbols: list[tuple[str, int, int]]) -> bytes:
    """构造无任何 DWARF 节的纯 .symtab ARM ELF32 固件镜像。"""
    strtab = bytearray(b"\x00")
    sym_entries = bytearray(struct.pack("<IIIBBH", 0, 0, 0, 0, 0, 0))
    for name, addr, size in symbols:
        name_off = len(strtab)
        strtab.extend(name.encode("utf-8") + b"\x00")
        sym_entries.extend(struct.pack("<IIIBBH", name_off, addr, size, 0x11, 0, 1))

    shstrtab = b"\x00.strtab\x00.symtab\x00.shstrtab\x00"
    ehdr_size = 52
    str_off = ehdr_size
    sym_off = str_off + len(strtab)
    shstr_off = sym_off + len(sym_entries)
    shoff = shstr_off + len(shstrtab)

    ehdr = bytearray(ehdr_size)
    ehdr[0:4] = b"\x7fELF"
    ehdr[4] = 1  # ELF32
    ehdr[5] = 1  # little-endian
    ehdr[6] = 1
    struct.pack_into("<HHIIIIIHHHHHH", ehdr, 16, 2, 40, 1, 0, 0, shoff, 0, ehdr_size, 0, 0, 40, 4, 3)

    sh0 = struct.pack("<IIIIIIIIII", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    sh1 = struct.pack("<IIIIIIIIII", 1, 3, 0, 0, str_off, len(strtab), 0, 0, 1, 0)
    sh2 = struct.pack("<IIIIIIIIII", 9, 2, 0, 0, sym_off, len(sym_entries), 1, 1, 4, 16)
    sh3 = struct.pack("<IIIIIIIIII", 17, 3, 0, 0, shstr_off, len(shstrtab), 0, 0, 1, 0)
    return bytes(ehdr) + bytes(strtab) + bytes(sym_entries) + shstrtab + sh0 + sh1 + sh2 + sh3


def _make_dwarf5_strx_addrx_elf32() -> bytes:
    """构造带 .debug_str_offsets (DW_FORM_strx1) + .debug_addr (DW_OP_addrx) + .symtab 补全的 32-bit MCU ELF32。"""
    # .debug_str: [0]="mcu.c", [6]="uint32_t", [15]="g_addrx_var", [27]="g_symtab_var", [40]="g_mcu_ptr"
    debug_str = b"mcu.c\x00uint32_t\x00g_addrx_var\x00g_symtab_var\x00g_mcu_ptr\x00"
    str_offsets_entries = struct.pack("<IIIII", 0, 6, 15, 27, 40)
    debug_str_offsets = struct.pack("<IHBB", len(str_offsets_entries) + 4, 5, 0, 0) + str_offsets_entries

    # .debug_addr: DWARF v5 header (length, version=5, addr_size=4, seg_size=0) + entry[0] = 0x20000100, entry[1] = 0x20000108
    addr_entries = struct.pack("<II", 0x20000100, 0x20000108)
    debug_addr = struct.pack("<IHBB", len(addr_entries) + 4, 5, 4, 0) + addr_entries

    # .debug_abbrev:
    # 1: DW_TAG_compile_unit (children=1): DW_AT_name(strx1), DW_AT_str_offsets_base(sec_offset), DW_AT_addr_base(sec_offset)
    # 2: DW_TAG_base_type (children=0): DW_AT_name(strx1), DW_AT_byte_size(implicit_const=4), DW_AT_encoding(data1)
    # 3: DW_TAG_pointer_type (children=0): DW_AT_type(ref1)  (省略 byte_size 验证默认取 CU address_size=4)
    # 4: DW_TAG_variable (children=0): DW_AT_name(strx1), DW_AT_type(ref1), DW_AT_location(exprloc)
    # 5: DW_TAG_variable (children=0): DW_AT_name(strx1), DW_AT_type(ref1), DW_AT_external(flag_present)  (无 DW_AT_location -> 走 .symtab 补全!)
    debug_abbrev = bytes([
        1, 0x11, 1,
        0x03, 0x25,        # DW_AT_name, DW_FORM_strx1
        0x72, 0x17,        # DW_AT_str_offsets_base, DW_FORM_sec_offset
        0x73, 0x17,        # DW_AT_addr_base, DW_FORM_sec_offset
        0, 0,
        2, 0x24, 0,
        0x03, 0x25,        # DW_AT_name, DW_FORM_strx1
        0x0B, 0x21, 4,     # DW_AT_byte_size, DW_FORM_implicit_const = 4
        0x3E, 0x0B,        # DW_AT_encoding, DW_FORM_data1
        0, 0,
        3, 0x0F, 0,
        0x49, 0x11,        # DW_AT_type, DW_FORM_ref1
        0, 0,
        4, 0x34, 0,
        0x03, 0x25,        # DW_AT_name, DW_FORM_strx1
        0x49, 0x11,        # DW_AT_type, DW_FORM_ref1
        0x02, 0x18,        # DW_AT_location, DW_FORM_exprloc
        0, 0,
        5, 0x34, 0,
        0x03, 0x25,        # DW_AT_name, DW_FORM_strx1
        0x49, 0x11,        # DW_AT_type, DW_FORM_ref1
        0x3F, 0x19,        # DW_AT_external, DW_FORM_flag_present
        0, 0,
        0,
    ])

    # .debug_info (DWARF v5 CU header = 12 bytes: u32 len, u16 ver=5, u8 ut=1, u8 addr_sz=4, u32 abbrev_off=0)
    # DIE <0x0c>: code=1 (CU): name=strx1(0), str_offsets_base=8, addr_base=8  -> 1 + 1 + 4 + 4 = 10 bytes -> next 0x16
    # DIE <0x16>: code=2 (base_type uint32_t): name=strx1(1), encoding=7       -> 1 + 1 + 1 = 3 bytes -> next 0x19
    # DIE <0x19>: code=3 (pointer_type -> 0x16): type=ref1(0x16)               -> 1 + 1 = 2 bytes -> next 0x1b
    # DIE <0x1b>: code=4 (var g_addrx_var): name=strx1(2), type=ref1(0x16), loc=[len=2, DW_OP_addrx(0xa1), 0] -> next 0x21
    # DIE <0x21>: code=5 (var g_symtab_var): name=strx1(3), type=ref1(0x16)    -> 1 + 1 + 1 = 3 bytes -> next 0x24
    # DIE <0x24>: code=4 (var g_mcu_ptr): name=strx1(4), type=ref1(0x19), loc=[len=2, DW_OP_addrx(0xa1), 1]
    # DIE: 0 (end children)
    cu_body = bytearray()
    cu_body += struct.pack("<BBII", 1, 0, 8, 8)         # 0x0c..0x15
    cu_body += bytes([2, 1, 7])                         # 0x16..0x18 (uint32_t)
    cu_body += bytes([3, 0x16])                         # 0x19..0x1a (uint32_t *)
    cu_body += bytes([4, 2, 0x16, 2, 0xA1, 0])          # 0x1b..0x20 (g_addrx_var @ addrx[0]=0x20000100)
    cu_body += bytes([5, 3, 0x16])                      # 0x21..0x23 (g_symtab_var -> symtab 0x20000104)
    cu_body += bytes([4, 4, 0x19, 2, 0xA1, 1])          # 0x24..0x29 (g_mcu_ptr @ addrx[1]=0x20000108)
    cu_body += b"\x00"
    cu_hdr_rest = struct.pack("<HBBI", 5, 1, 4, 0)
    debug_info = struct.pack("<I", len(cu_hdr_rest) + len(cu_body)) + cu_hdr_rest + bytes(cu_body)

    # .symtab 提供 g_symtab_var = 0x20000104
    strtab = b"\x00g_symtab_var\x00"
    symtab = struct.pack("<IIIBBH", 0, 0, 0, 0, 0, 0) + struct.pack("<IIIBBH", 1, 0x20000104, 4, 0x11, 0, 1)

    sec_defs = [
        (".strtab", 3, strtab, 0, 0, 0),
        (".symtab", 2, symtab, 1, 1, 16),
        (".debug_info", 1, debug_info, 0, 0, 0),
        (".debug_abbrev", 1, debug_abbrev, 0, 0, 0),
        (".debug_str", 1, debug_str, 0, 0, 0),
        (".debug_str_offsets", 1, debug_str_offsets, 0, 0, 0),
        (".debug_addr", 1, debug_addr, 0, 0, 0),
    ]
    shstr = bytearray(b"\x00")
    name_offs: list[int] = []
    for sname, *_ in sec_defs:
        name_offs.append(len(shstr))
        shstr.extend(sname.encode("ascii") + b"\x00")
    shstr_name_off = len(shstr)
    shstr.extend(b".shstrtab\x00")

    ehdr_size = 52
    blob = bytearray()
    sec_headers = [struct.pack("<IIIIIIIIII", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)]
    for idx, (sname, stype, sbytes, slink, sinfo, sentsz) in enumerate(sec_defs):
        off = ehdr_size + len(blob)
        blob.extend(sbytes)
        sec_headers.append(struct.pack("<IIIIIIIIII", name_offs[idx], stype, 0, 0, off, len(sbytes), slink, sinfo, 1, sentsz))

    shstr_off = ehdr_size + len(blob)
    blob.extend(shstr)
    sec_headers.append(struct.pack("<IIIIIIIIII", shstr_name_off, 3, 0, 0, shstr_off, len(shstr), 0, 0, 1, 0))

    shoff = ehdr_size + len(blob)
    ehdr = bytearray(ehdr_size)
    ehdr[0:4] = b"\x7fELF"
    ehdr[4] = 1
    ehdr[5] = 1
    ehdr[6] = 1
    struct.pack_into(
        "<HHIIIIIHHHHHH",
        ehdr,
        16,
        2,
        40,
        1,
        0,
        0,
        shoff,
        0,
        ehdr_size,
        0,
        0,
        40,
        len(sec_headers),
        len(sec_headers) - 1,
    )
    return bytes(ehdr) + bytes(blob) + b"".join(sec_headers)


def _make_big_endian_dwarf4_elf32() -> bytes:
    """构造大端序 (ELFDATA2MSB) DWARF v4 ELF32，含结构体、有符号位域、枚举与 float。"""
    debug_str = b"\x00be_sys_t\x00flt\x00bf_signed\x00g_be\x00float\x00int\x00"
    # offsets: 1="be_sys_t", 10="flt", 14="bf_signed", 24="g_be", 29="float", 35="int"
    debug_abbrev = bytes([
        1, 0x11, 1, 0, 0,                                  # CU (children=1)
        2, 0x24, 0, 0x03, 0x0E, 0x0B, 0x0B, 0x3E, 0x0B, 0, 0,  # base_type: name(strp), size(data1), enc(data1)
        3, 0x13, 1, 0x03, 0x0E, 0x0B, 0x0B, 0, 0,          # struct (children=1): name(strp), byte_size(data1)
        4, 0x0D, 0, 0x03, 0x0E, 0x49, 0x11, 0x38, 0x0B, 0, 0,  # member: name(strp), type(ref1), loc(data1)
        5, 0x0D, 0,                                        # bitfield member (DWARF v4 DW_AT_bit_offset)
        0x03, 0x0E, 0x49, 0x11, 0x0B, 0x0B, 0x0D, 0x0B, 0x0C, 0x0B, 0x38, 0x0B, 0, 0,
        6, 0x34, 0, 0x03, 0x0E, 0x49, 0x11, 0x02, 0x18, 0, 0,  # variable: name(strp), type(ref1), loc(exprloc)
        0,
    ])

    # CU header (DWARF v4, big-endian, 11 bytes): u32 len, u16 ver=4, u32 abbrev_off=0, u8 addr_sz=4
    # 0x0b: code=1 (CU) -> 1 byte -> 0x0c
    # 0x0c: code=2 (float): strp(29), size=4, enc=4 -> 1+4+1+1=7 -> 0x13
    # 0x13: code=2 (int): strp(35), size=4, enc=5 -> 1+4+1+1=7 -> 0x1a
    # 0x1a: code=3 (struct be_sys_t, size=8): strp(1), 8 -> 1+4+1=6 -> 0x20
    # 0x20: code=4 (member flt @ 0): strp(10), ref1(0x0c), loc=0 -> 1+4+1+1=7 -> 0x27
    # 0x27: code=5 (member bf_signed: 6 bits @ bit_offset=2 from MSB of 4B @ loc=4):
    #       strp(14), ref1(0x13), byte_size=4, bit_size=6, bit_offset=2, loc=4 -> 1+4+1+1+1+1+1=10 -> 0x31
    # 0x31: 0 (end struct children) -> 0x32
    # 0x32: code=6 (var g_be @ 0x20001000): strp(24), ref1(0x1a), expr=[5, 0x03, 0x20, 0x00, 0x10, 0x00] -> 1+4+1+6=12
    # 0x3e: 0 (end CU children)
    body = bytearray()
    body += bytes([1])
    body += bytes([2]) + struct.pack(">IBB", 29, 4, 4)
    body += bytes([2]) + struct.pack(">IBB", 35, 4, 5)
    body += bytes([3]) + struct.pack(">IB", 1, 8)
    body += bytes([4]) + struct.pack(">IBB", 10, 0x0C, 0)
    body += bytes([5]) + struct.pack(">IBBBBB", 14, 0x13, 4, 6, 2, 4)
    body += b"\x00"
    body += bytes([6]) + struct.pack(">IB", 24, 0x1A) + bytes([5, 0x03, 0x20, 0x00, 0x10, 0x00])
    body += b"\x00"

    cu_hdr_after_len = struct.pack(">HIB", 4, 0, 4)
    debug_info = struct.pack(">I", len(cu_hdr_after_len) + len(body)) + cu_hdr_after_len + bytes(body)

    shstr = b"\x00.debug_info\x00.debug_abbrev\x00.debug_str\x00.shstrtab\x00"
    ehdr_size = 52
    off_info = ehdr_size
    off_abbrev = off_info + len(debug_info)
    off_str = off_abbrev + len(debug_abbrev)
    off_shstr = off_str + len(debug_str)
    shoff = off_shstr + len(shstr)

    ehdr = bytearray(ehdr_size)
    ehdr[0:4] = b"\x7fELF"
    ehdr[4] = 1  # ELF32
    ehdr[5] = 2  # Big-Endian (ELFDATA2MSB)
    ehdr[6] = 1
    struct.pack_into(">HHIIIIIHHHHHH", ehdr, 16, 2, 40, 1, 0, 0, shoff, 0, ehdr_size, 0, 0, 40, 5, 4)

    sh0 = struct.pack(">IIIIIIIIII", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    sh1 = struct.pack(">IIIIIIIIII", 1, 1, 0, 0, off_info, len(debug_info), 0, 0, 1, 0)
    sh2 = struct.pack(">IIIIIIIIII", 13, 1, 0, 0, off_abbrev, len(debug_abbrev), 0, 0, 1, 0)
    sh3 = struct.pack(">IIIIIIIIII", 27, 1, 0, 0, off_str, len(debug_str), 0, 0, 1, 0)
    sh4 = struct.pack(">IIIIIIIIII", 38, 3, 0, 0, off_shstr, len(shstr), 0, 0, 1, 0)
    return bytes(ehdr) + debug_info + debug_abbrev + debug_str + shstr + sh0 + sh1 + sh2 + sh3 + sh4


class TestDwarfSyntheticElf32(unittest.TestCase):
    """不依赖外部编译器的 32-bit MCU ELF32（小端/大端、DWARF v5 间接表、纯符号表）测试。"""

    def test_dwarf5_strx_addrx_symtab_completion_and_mcu_32bit_pointers(self) -> None:
        """验证 DWARF v5 .debug_str_offsets/.debug_addr 间接索引、.symtab 地址补全与 4 字节 MCU 指针。"""
        with tempfile.TemporaryDirectory() as td:
            elf32 = Path(td) / "mcu_v5_indirect.elf"
            elf32.write_bytes(_make_dwarf5_strx_addrx_elf32())

            idx = load_dwarf_index(elf32)
            self.assertEqual(idx.pointer_size, 4)
            self.assertEqual(idx.dwarf_versions, (5,))
            self.assertEqual(idx.variables["g_addrx_var"].address, 0x20000100)
            self.assertEqual(idx.variables["g_addrx_var"].source, "dwarf")
            self.assertEqual(idx.variables["g_symtab_var"].address, 0x20000104)
            self.assertEqual(idx.variables["g_symtab_var"].source, "dwarf+symtab")
            self.assertEqual(idx.variables["g_mcu_ptr"].byte_size, 4)

            with MockProbe() as probe:
                probe.set_word(0x20000100, 0xDEADBEEF)
                probe.set_word(0x20000104, 123456)
                probe.set_word(0x20000108, 0x00000000)

                v1 = read_variable(probe, elf32, "g_addrx_var")
                v2 = read_variable(probe, elf32, "g_symtab_var")
                vp_null = read_variable(probe, elf32, "g_mcu_ptr", deref=True)
                self.assertEqual(v1.value, 0xDEADBEEF)
                self.assertEqual(v2.value, 123456)
                self.assertTrue(vp_null.is_null)
                self.assertEqual(vp_null.display, "0x00000000 (NULL)")
                self.assertIsNone(vp_null.deref)

                probe.set_word(0x20000108, 0x20000104)
                vp_ok = read_variable(probe, elf32, "g_mcu_ptr", deref=True)
                self.assertFalse(vp_ok.is_null)
                self.assertEqual(vp_ok.hex_value, "0x20000104")
                self.assertIsNotNone(vp_ok.deref)
                assert vp_ok.deref is not None
                self.assertEqual(vp_ok.deref.value, 123456)

    def test_big_endian_elf32_struct_float_and_signed_bitfield(self) -> None:
        """验证大端序 ELF32 下浮点数与有符号位域（DW_AT_bit_offset）解码。"""
        with tempfile.TemporaryDirectory() as td:
            be_elf = Path(td) / "be_v4.elf"
            be_elf.write_bytes(_make_big_endian_dwarf4_elf32())
            idx = load_dwarf_index(be_elf)
            self.assertEqual(idx.endian, ">")

            with MockProbe() as probe:
                # 偏移 0..3: 大端 float -3.5f; 偏移 4: MSB 起跳过 2 bit 放 6-bit 有符号 -5 (0b111011 = 0x3B) -> byte4 = 0x3B
                probe.write_memory(0x20001000, struct.pack(">f", -3.5) + bytes([0x3B, 0x00, 0x00, 0x00]))
                whole = read_variable(probe, be_elf, "g_be")
                self.assertAlmostEqual(whole.value["flt"], -3.5, places=6)
                self.assertEqual(whole.value["bf_signed"], -5)
                bf_direct = read_variable(probe, be_elf, "g_be.bf_signed")
                self.assertEqual(bf_direct.value, -5)


@unittest.skipUnless(shutil.which("gcc"), "Requires host /usr/bin/gcc")
class TestDwarfLiveWatchRealGcc(unittest.TestCase):
    """使用宿主机真实 gcc 分别编译验证 DWARF v2 / v3 / v4 / v5 与无 -g 降级。"""

    tmpdir: tempfile.TemporaryDirectory[str]
    elf_by_ver: dict[int, Path]
    elf_no_debug: Path
    elf_stripped_dyn: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory(prefix="mcu_dwarf_test_")
        root = Path(cls.tmpdir.name)
        c_file = root / "firmware_vars.c"
        c_file.write_text(_C_FIXTURE_SRC, encoding="utf-8")

        cls.elf_by_ver = {}
        for ver in (2, 3, 4, 5):
            out_elf = root / f"fw_dwarf_v{ver}.elf"
            subprocess.run(
                ["gcc", "-g", f"-gdwarf-{ver}", "-O0", str(c_file), "-o", str(out_elf)],
                check=True,
            )
            cls.elf_by_ver[ver] = out_elf

        cls.elf_no_debug = root / "fw_no_g.elf"
        subprocess.run(["gcc", "-O0", str(c_file), "-o", str(cls.elf_no_debug)], check=True)

        cls.elf_stripped_dyn = root / "fw_stripped.elf"
        subprocess.run(["gcc", "-O0", "-s", "-rdynamic", str(c_file), "-o", str(cls.elf_stripped_dyn)], check=True)

        cls.elf_zlib: Path | None = None
        zlib_out = root / "fw_dwarf_v5_zlib.elf"
        res = subprocess.run(["gcc", "-g", "-gdwarf-5", "-gz=zlib", "-O0", str(c_file), "-o", str(zlib_out)])
        if res.returncode == 0 and zlib_out.is_file():
            cls.elf_zlib = zlib_out

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmpdir.cleanup()

    def _verify_full_decode_for_elf(self, elf_path: Path, expected_ver: int) -> None:
        idx = load_dwarf_index(elf_path)
        self.assertIn(expected_ver, idx.dwarf_versions)
        self.assertFalse(idx.degraded_symtab_only)
        self.assertIn("g_ctrl", idx.variables)
        self.assertIn("s_loop_cnt", idx.variables)

        with _CountingMockProbe(halted=False) as probe:
            _load_elf_into_mock(probe, elf_path)

            # 1) 整棵结构体单次总线读取（不暂停 CPU、无多余总线交互）
            probe.read_memory_calls = 0
            tree = read_variable(probe, elf_path, "g_ctrl", deref=False)
            self.assertEqual(probe.read_memory_calls, 1)
            self.assertEqual(probe.halt_calls, 0)
            self.assertFalse(probe.is_halted())

            self.assertEqual(tree.kind, "struct")
            self.assertEqual(tree.value["mode"], {"value": 2, "enum": "CTRL_FAULT", "display": "CTRL_FAULT (2)"})
            self.assertAlmostEqual(tree.value["pid"]["kp"], 1.5, places=6)
            self.assertAlmostEqual(tree.value["pid"]["ki"], 0.25, places=6)
            self.assertAlmostEqual(tree.value["pid"]["kd"], -0.125, places=6)
            self.assertEqual(tree.value["channels"], [-10, 200, 305, -4096])
            self.assertEqual(tree.value["sensors"][2]["raw_mv"], 3295)
            self.assertEqual(tree.value["sensors"][2]["valid"], 0)
            self.assertEqual(tree.fields["name"].string_value, "STM32H5-FOC")
            self.assertEqual(tree.value["ready"], 1)
            self.assertEqual(tree.value["err_code"], 19)
            self.assertEqual(tree.value["signed_trim"], -11)
            self.assertEqual(tree.value["enabled"], True)

            # 验证联合体与 C11 匿名嵌套结构体展平
            self.assertEqual(tree.value["status_reg"]["raw"], 0xCAFE1234)
            self.assertEqual(tree.value["status_reg"]["lo"], 0x1234)
            self.assertEqual(tree.value["status_reg"]["hi"], 0xCAFE)

            # 验证空指针标注 NULL
            self.assertTrue(tree.fields["backup_pid"].is_null)
            self.assertIn("NULL", tree.fields["backup_pid"].display)

            # 2) 子路径精确寻址（枚举、浮点、数组元素、有符号/无符号位域）
            mode_val = read_variable(probe, elf_path, "g_ctrl.mode")
            self.assertEqual(mode_val.kind, "enum")
            self.assertEqual(mode_val.value, {"value": 2, "enum": "CTRL_FAULT", "display": "CTRL_FAULT (2)"})

            # 动态写入负枚举值 CTRL_ESTOP = -2 验证有符号枚举解码
            probe.write_memory(mode_val.address, struct.pack("<i", -2))
            estop_val = read_variable(probe, elf_path, "g_ctrl.mode")
            self.assertEqual(estop_val.value, {"value": -2, "enum": "CTRL_ESTOP", "display": "CTRL_ESTOP (-2)"})

            kp_val = read_variable(probe, elf_path, "g_ctrl.pid.kp")
            self.assertEqual(kp_val.kind, "float")
            self.assertAlmostEqual(kp_val.value, 1.5, places=6)
            self.assertEqual(kp_val.hex_value, "0x3FC00000")

            ch1_val = read_variable(probe, elf_path, "g_ctrl.channels[1]")
            self.assertEqual(ch1_val.value, 200)
            self.assertEqual(ch1_val.hex_value, "0x00C8")

            sens2_val = read_variable(probe, elf_path, "g_ctrl.sensors[2].raw_mv")
            self.assertEqual(sens2_val.value, 3295)

            bf_ready = read_variable(probe, elf_path, "g_ctrl.ready")
            bf_err = read_variable(probe, elf_path, "g_ctrl.err_code")
            bf_trim = read_variable(probe, elf_path, "g_ctrl.signed_trim")
            self.assertEqual(bf_ready.value, 1)
            self.assertEqual(bf_err.value, 19)
            self.assertEqual(bf_trim.value, -11)

            # 3) 指针 deref=True 解引用一层目标结构体 + `->` 运算符寻址
            ptr_val = read_variable(probe, elf_path, "g_ctrl.active_pid", deref=True)
            self.assertEqual(ptr_val.kind, "pointer")
            self.assertFalse(ptr_val.is_null)
            self.assertIsNotNone(ptr_val.deref)
            assert ptr_val.deref is not None
            self.assertAlmostEqual(ptr_val.deref.value["kp"], 1.5, places=6)
            self.assertAlmostEqual(ptr_val.deref.value["ki"], 0.25, places=6)

            arrow_ki = read_variable(probe, elf_path, "g_ctrl.active_pid->ki")
            self.assertAlmostEqual(arrow_ki.value, 0.25, places=6)

            # 4) 静态变量读取与批量读取
            batch = read_variables(probe, elf_path, ["s_loop_cnt", "g_ctrl.enabled", "g_ctrl.name"])
            self.assertEqual(batch["s_loop_cnt"].value, 1000)
            self.assertIs(batch["g_ctrl.enabled"].value, True)
            self.assertEqual(batch["g_ctrl.name"].string_value, "STM32H5-FOC")

            # 5) max_depth 截断保护
            shallow = read_variable(probe, elf_path, "g_ctrl", max_depth=1)
            self.assertFalse(shallow.truncated)
            self.assertTrue(shallow.fields["pid"].truncated)

    def test_gcc_dwarf_v4_and_v5_and_legacy_v2_v3(self) -> None:
        """分别验证真实 gcc -gdwarf-4 与 -gdwarf-5（及 v2/v3、SHF_COMPRESSED zlib）完整解码。"""
        for ver in (4, 5, 2, 3):
            with self.subTest(dwarf_version=ver):
                self._verify_full_decode_for_elf(self.elf_by_ver[ver], ver)
        if self.elf_zlib is not None:
            with self.subTest(dwarf_version="5+zlib"):
                self._verify_full_decode_for_elf(self.elf_zlib, 5)

    def test_background_thread_dynamic_trend_sampling(self) -> None:
        """后台线程模拟运行中计数器递增与正弦信号波动，验证 sample_variable 统计准确性。"""
        elf_path = self.elf_by_ver[5]
        idx = load_dwarf_index(elf_path)
        cnt_addr = idx.variables["s_loop_cnt"].address
        kp_addr = idx.variables["g_ctrl"].address + 4  # g_ctrl.pid.kp

        with _CountingMockProbe(halted=False) as probe:
            _load_elf_into_mock(probe, elf_path)
            stop_evt = threading.Event()

            def mutator() -> None:
                step = 0
                while not stop_evt.is_set():
                    step += 1
                    probe.set_word(cnt_addr, 1000 + step * 5)
                    sine_val = 2.0 + math.sin(step * 0.4)
                    raw_f = struct.pack("<f", sine_val)
                    for i, b in enumerate(raw_f):
                        probe._mem[kp_addr + i] = b
                    time.sleep(0.002)

            worker = threading.Thread(target=mutator, daemon=True)
            worker.start()
            try:
                cnt_rep = sample_variable(probe, elf_path, "s_loop_cnt", samples=20, interval_ms=5.0)
                kp_rep = sample_variable(probe, elf_path, "g_ctrl.pid.kp", samples=20, interval_ms=5.0)
            finally:
                stop_evt.set()
                worker.join(timeout=1.0)

            self.assertEqual(probe.halt_calls, 0)
            self.assertTrue(cnt_rep.changing)
            self.assertGreater(cnt_rep.unique_count, 2)
            self.assertEqual(cnt_rep.delta, cnt_rep.last - cnt_rep.first)
            self.assertGreater(cnt_rep.delta, 0)
            self.assertEqual(cnt_rep.min, cnt_rep.first)
            self.assertEqual(cnt_rep.max, cnt_rep.last)
            self.assertEqual(len(cnt_rep.points), 20)

            self.assertTrue(kp_rep.changing)
            self.assertGreater(kp_rep.max, kp_rep.min)
            self.assertLessEqual(kp_rep.min, kp_rep.mean)
            self.assertLessEqual(kp_rep.mean, kp_rep.max)

            # 静态不变标量应报告 changing=False, delta=0
            static_rep = sample_variable(probe, elf_path, "g_plain_u16", samples=8, interval_ms=1.0)
            self.assertFalse(static_rep.changing)
            self.assertEqual(static_rep.unique_count, 1)
            self.assertEqual(static_rep.delta, 0)
            self.assertEqual(static_rep.first, 0xBEEF)

            # 对结构体或数组直接采样必须友好报错并提示标量成员
            with self.assertRaisesRegex(LiveWatchError, r"g_ctrl\.mode"):
                sample_variable(probe, elf_path, "g_ctrl", samples=5, interval_ms=1.0)
            with self.assertRaisesRegex(LiveWatchError, r"g_ctrl\.channels\[0\]"):
                sample_variable(probe, elf_path, "g_ctrl.channels", samples=5, interval_ms=1.0)

    def test_symtab_graceful_degradation_without_debug_info(self) -> None:
        """验证无 -g 编译、gcc -s 剥离与纯 .symtab ELF32 的符号表优雅降级。"""
        # 1) gcc 无 -g 编译
        idx_nog = load_dwarf_index(self.elf_no_debug)
        self.assertTrue(idx_nog.degraded_symtab_only)
        with MockProbe() as probe:
            _load_elf_into_mock(probe, self.elf_no_debug)
            self.assertEqual(read_variable(probe, self.elf_no_debug, "g_plain_u32").value, 0x13572468)
            self.assertEqual(read_variable(probe, self.elf_no_debug, "g_plain_u16").value, 0xBEEF)
            self.assertEqual(read_variable(probe, self.elf_no_debug, "g_plain_u8").value, 0xA5)
            ctrl_bytes = read_variable(probe, self.elf_no_debug, "g_ctrl")
            self.assertEqual(ctrl_bytes.kind, "array")
            self.assertGreater(len(ctrl_bytes.value), 32)

        # 2) gcc -s -rdynamic 仅剩 .dynsym
        idx_strip = load_dwarf_index(self.elf_stripped_dyn)
        self.assertTrue(idx_strip.degraded_symtab_only)
        with MockProbe() as probe:
            _load_elf_into_mock(probe, self.elf_stripped_dyn)
            self.assertEqual(read_variable(probe, self.elf_stripped_dyn, "g_plain_u32").value, 0x13572468)

        # 3) 构造的纯 .symtab ARM ELF32 裸机镜像
        with tempfile.TemporaryDirectory() as td:
            arm_elf = Path(td) / "baremetal_symtab.elf"
            arm_elf.write_bytes(
                _make_minimal_symtab_elf32(
                    [
                        ("g_rpm", 0x20000010, 4),
                        ("g_adc_mv", 0x20000014, 2),
                        ("g_flags", 0x20000016, 1),
                        ("g_rx_buf", 0x20000020, 6),
                    ]
                )
            )
            idx_arm = load_dwarf_index(arm_elf)
            self.assertTrue(idx_arm.degraded_symtab_only)
            self.assertEqual(idx_arm.pointer_size, 4)

            with MockProbe() as probe:
                probe.set_word(0x20000010, 3600)
                probe.write_u16(0x20000014, [3300])
                probe.write_u8(0x20000016, [0x7F])
                probe.write_memory(0x20000020, b"\x11\x22\x33\x44\x55\x66")

                self.assertEqual(read_variable(probe, arm_elf, "g_rpm").value, 3600)
                self.assertEqual(read_variable(probe, arm_elf, "g_adc_mv").value, 3300)
                self.assertEqual(read_variable(probe, arm_elf, "g_flags").value, 0x7F)
                self.assertEqual(read_variable(probe, arm_elf, "g_rx_buf").value, [0x11, 0x22, 0x33, 0x44, 0x55, 0x66])
                self.assertEqual(read_variable(probe, arm_elf, "g_rx_buf[3]").value, 0x44)

    def test_index_cache_and_cli_commands(self) -> None:
        """验证 (resolved_path, mtime_ns) 内存缓存命中及 mcu-watch CLI 全子命令。"""
        elf_path = self.elf_by_ver[5]
        first_idx = load_dwarf_index(elf_path)
        second_idx = load_dwarf_index(elf_path)
        self.assertIs(first_idx, second_idx)

        # CLI 1: list-vars --json
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = watch_cli_main(["list-vars", "--elf", str(elf_path), "--filter", "g_ctrl", "--json"])
        self.assertEqual(rc, 0)
        listed = json.loads(buf.getvalue())
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["variables"][0]["name"], "g_ctrl")

        # CLI 2: read --mock --deref --json
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = watch_cli_main(["read", "g_ctrl", "--elf", str(elf_path), "--mock", "--deref", "--json"])
        self.assertEqual(rc, 0)
        read_doc = json.loads(buf.getvalue())
        self.assertEqual(read_doc["fields"]["mode"]["enum"], "CTRL_FAULT")
        self.assertEqual(read_doc["fields"]["name"]["string"], "STM32H5-FOC")
        self.assertAlmostEqual(read_doc["fields"]["active_pid"]["deref"]["value"]["kp"], 1.5, places=6)

        # CLI 3: sample --mock --json
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = watch_cli_main(
                ["sample", "g_ctrl.pid.kp", "--elf", str(elf_path), "--mock", "-n", "5", "--interval-ms", "1", "--json"]
            )
        self.assertEqual(rc, 0)
        sample_doc = json.loads(buf.getvalue())
        self.assertEqual(sample_doc["sample_count"], 5)
        self.assertAlmostEqual(sample_doc["first"], 1.5, places=6)

        # CLI 4: 文本输出模式（list-vars / read / sample）与参数越界校验
        txt_buf = io.StringIO()
        with redirect_stdout(txt_buf):
            self.assertEqual(watch_cli_main(["list-vars", "--elf", str(elf_path), "--filter", "g_ctrl"]), 0)
            self.assertEqual(watch_cli_main(["read", "g_ctrl", "--elf", str(elf_path), "--mock", "--deref"]), 0)
            self.assertEqual(
                watch_cli_main(["sample", "g_ctrl.mode", "--elf", str(elf_path), "--mock", "-n", "3", "--interval-ms", "1"]),
                0,
            )
        rendered = txt_buf.getvalue()
        self.assertIn("CTRL_FAULT (2)", rendered)
        self.assertIn("'STM32H5-FOC'", rendered)
        self.assertIn("Sample Report:", rendered)

        err_buf = io.StringIO()
        with redirect_stderr(err_buf):
            rc_err = watch_cli_main(["read", "g_ctrl.nonexistent", "--elf", str(elf_path), "--mock"])
            rc_oob = watch_cli_main(["sample", "g_ctrl.pid.kp", "--elf", str(elf_path), "--mock", "-n", "501"])
        self.assertEqual(rc_err, 1)
        self.assertEqual(rc_oob, 1)
        self.assertIn("nonexistent", err_buf.getvalue())


if __name__ == "__main__":
    unittest.main()
