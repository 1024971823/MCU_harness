"""ELF/.map 固件内存版图与 DMA 审计、compile_commands.json 生成器、原生 POSIX UART 采集器的实测套件。

兼容运行方式：
    * PYTHONPATH=src python3 tests/test_footprint_and_uart.py -v
    * pytest tests/test_footprint_and_uart.py -v
"""

from __future__ import annotations

import json
import os
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from mcu_harness.build.compdb import generate_compile_commands, parse_make_dry_run
from mcu_harness.build.footprint import FootprintError, analyze_footprint
from mcu_harness.log.uart_collector import (
    STANDARD_BAUDRATES,
    UartError,
    UartLogCollector,
    default_uart_collector,
    list_serial_ports,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_CMAKE_DIR = REPO_ROOT / "examples" / "demo_fw"
DEMO_MAKE_DIR = REPO_ROOT / "examples" / "demo_make_fw"

# ELF 常量（用于构造标准 ARM Cortex-M ELF32 固件样本）
_SHT_PROGBITS = 1
_SHT_SYMTAB = 2
_SHT_STRTAB = 3
_SHT_NOBITS = 8
_SHT_ARM_EXIDX = 0x70000001
_SHF_WRITE = 0x1
_SHF_ALLOC = 0x2
_SHF_EXECINSTR = 0x4
_SHN_ABS = 0xFFF1


def _build_arm_elf32(
    *,
    sections: list[tuple[str, int, int, int, int, int]],
    symbols: list[tuple[str, int, int, int, int]],
) -> bytes:
    """构造合法的 ARM little-endian ELF32 文件。

    Args:
        sections: [(name, sh_type, sh_flags, sh_addr, sh_size, sh_addralign), ...]
                  索引从 1 开始（0 号固定为 NULL 节）。
        symbols:  [(name, st_value, st_size, st_info, st_shndx), ...]
    """
    shstrtab = bytearray(b"\x00")
    sec_name_offsets: list[int] = []
    for name, *_ in sections:
        sec_name_offsets.append(len(shstrtab))
        shstrtab.extend(name.encode("utf-8") + b"\x00")

    shstr_name_off = len(shstrtab)
    shstrtab.extend(b".shstrtab\x00")
    strtab_name_off = len(shstrtab)
    shstrtab.extend(b".strtab\x00")
    symtab_name_off = len(shstrtab)
    shstrtab.extend(b".symtab\x00")

    strtab = bytearray(b"\x00")
    sym_blob = bytearray(struct.pack("<IIIBBH", 0, 0, 0, 0, 0, 0))  # STN_UNDEF
    for name, val, size, st_info, shndx in symbols:
        noff = len(strtab)
        strtab.extend(name.encode("utf-8") + b"\x00")
        sym_blob.extend(struct.pack("<IIIBBH", noff, val, size, st_info, 0, shndx))

    ehdr_size = 52
    cur_off = ehdr_size

    # 为 PROGBITS / EXIDX 分配真实字节占位，NOBITS 不占文件空间
    sec_headers: list[bytes] = [struct.pack("<IIIIIIIIII", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)]
    payload = bytearray()

    for idx, (_, sh_type, sh_flags, sh_addr, sh_size, sh_align) in enumerate(sections):
        file_off = cur_off
        if sh_type != _SHT_NOBITS and sh_size > 0:
            payload.extend(b"\x00" * sh_size)
            cur_off += sh_size
        sec_headers.append(
            struct.pack(
                "<IIIIIIIIII",
                sec_name_offsets[idx],
                sh_type,
                sh_flags,
                sh_addr,
                file_off,
                sh_size,
                0,
                0,
                sh_align,
                0,
            )
        )

    shstr_idx = len(sec_headers)
    shstr_off = cur_off
    payload.extend(shstrtab)
    cur_off += len(shstrtab)
    sec_headers.append(
        struct.pack("<IIIIIIIIII", shstr_name_off, _SHT_STRTAB, 0, 0, shstr_off, len(shstrtab), 0, 0, 1, 0)
    )

    strtab_idx = len(sec_headers)
    str_off = cur_off
    payload.extend(strtab)
    cur_off += len(strtab)
    sec_headers.append(
        struct.pack("<IIIIIIIIII", strtab_name_off, _SHT_STRTAB, 0, 0, str_off, len(strtab), 0, 0, 1, 0)
    )

    sym_off = cur_off
    payload.extend(sym_blob)
    cur_off += len(sym_blob)
    sec_headers.append(
        struct.pack("<IIIIIIIIII", symtab_name_off, _SHT_SYMTAB, 0, 0, sym_off, len(sym_blob), strtab_idx, 1, 4, 16)
    )

    shoff = cur_off
    sh_table = b"".join(sec_headers)

    ehdr = bytearray(ehdr_size)
    ehdr[0:4] = b"\x7fELF"
    ehdr[4] = 1  # ELFCLASS32
    ehdr[5] = 1  # ELFDATA2LSB
    ehdr[6] = 1  # EV_CURRENT
    struct.pack_into(
        "<HHIIIIIHHHHHH",
        ehdr,
        16,
        2,          # ET_EXEC
        40,         # EM_ARM
        1,
        0x08000101,
        0,
        shoff,
        0x05000400,
        ehdr_size,
        0,
        0,
        40,
        len(sec_headers),
        shstr_idx,
    )
    return bytes(ehdr) + bytes(payload) + sh_table


# ===========================================================================
# 1. ELF/.map 内存版图与 DMA / 膨胀审计测试
# ===========================================================================


class FootprintAnalyzerTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("gcc"), "需要宿主机 /usr/bin/gcc")
    def test_real_gcc_elf_and_map_attribution(self) -> None:
        """使用真实 gcc -Wl,-Map=fw.map 编译分层 C 源码，验证节统计、Top 符号、模块与分层归因。"""
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            (proj / "app").mkdir()
            (proj / "service").mkdir()
            (proj / "bsp").mkdir()
            (proj / "build").mkdir()

            (proj / "bsp" / "uart_dma.c").write_text(
                """
                #include <stdint.h>
                /* 故意偏移到 16 字节对齐而非 32 字节对齐以验证缓存行对齐审计，或验证正常 RAM 统计 */
                uint8_t __attribute__((aligned(32))) g_uart_dma_rx_buf[256] = {0xAA, 0x55};
                uint8_t bsp_tx_ring[512];
                int bsp_uart_transfer(int len) {
                    int s = 0;
                    for (int i = 0; i < len && i < 256; i++) {
                        bsp_tx_ring[i] = g_uart_dma_rx_buf[i] ^ (uint8_t)i;
                        s += bsp_tx_ring[i];
                    }
                    return s;
                }
                """,
                encoding="utf-8",
            )
            (proj / "service" / "water_ctrl.c").write_text(
                """
                #include <stdint.h>
                const uint32_t g_pid_lut[128] = {10, 20, 30, 40, 50};
                uint32_t g_service_telemetry[1024];
                extern int bsp_uart_transfer(int len);
                int service_water_step(int target) {
                    int acc = bsp_uart_transfer(64);
                    for (int i = 0; i < 128; i++) {
                        g_service_telemetry[i] = (uint32_t)(acc + target) * g_pid_lut[i];
                        acc ^= (int)g_service_telemetry[i];
                    }
                    return acc;
                }
                """,
                encoding="utf-8",
            )
            (proj / "app" / "main.c").write_text(
                """
                extern int service_water_step(int target);
                int main(void) {
                    return service_water_step(42) == 123456 ? 1 : 0;
                }
                """,
                encoding="utf-8",
            )

            objs = []
            for rel_src, obj_sub in (
                ("bsp/uart_dma.c", "build/bsp/uart_dma.o"),
                ("service/water_ctrl.c", "build/service/water_ctrl.o"),
                ("app/main.c", "build/app/main.o"),
            ):
                out_obj = proj / obj_sub
                out_obj.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["gcc", "-O1", "-g", "-ffunction-sections", "-fdata-sections", "-c", str(proj / rel_src), "-o", str(out_obj)],
                    check=True,
                )
                objs.append(str(out_obj))

            elf_file = proj / "build" / "fw.elf"
            map_file = proj / "build" / "fw.map"
            subprocess.run(
                ["gcc", *objs, f"-Wl,-Map={map_file}", "-Wl,--gc-sections", "-o", str(elf_file)],
                check=True,
            )

            # 不显式传 map_path：应自动发现同目录同名 fw.map
            report = analyze_footprint(elf_file, project_dir=proj, top_n=10)

            self.assertEqual(Path(report.elf_path).resolve(), elf_file.resolve())
            self.assertEqual(Path(report.map_path).resolve(), map_file.resolve())
            self.assertGreater(report.text_bytes, 0)
            self.assertGreaterEqual(report.rodata_bytes, 128 * 4)
            self.assertGreaterEqual(report.data_bytes, 256)
            self.assertGreaterEqual(report.bss_bytes, 1024 * 4 + 512)
            self.assertEqual(report.flash_bytes, report.text_bytes + report.rodata_bytes + report.data_bytes)
            self.assertEqual(report.ram_bytes, report.data_bytes + report.bss_bytes)

            self.assertIn(".text", report.key_sections)
            self.assertIn(".rodata", report.key_sections)
            self.assertIn(".data", report.key_sections)
            self.assertIn(".bss", report.key_sections)

            # 验证 Top N 函数与 RAM 对象符号排序与识别
            func_names = [s.name for s in report.top_flash_symbols]
            self.assertIn("service_water_step", func_names)
            self.assertIn("bsp_uart_transfer", func_names)

            ram_names = [s.name for s in report.top_ram_symbols]
            self.assertEqual(ram_names[0], "g_service_telemetry")
            self.assertIn("bsp_tx_ring", ram_names)
            self.assertIn("g_uart_dma_rx_buf", ram_names)

            # 验证 .map 链接单元贡献与分层权重聚合
            layers = {lc.layer: lc for lc in report.layer_contributions}
            self.assertIn("service/", layers)
            self.assertIn("bsp/", layers)
            self.assertIn("app/", layers)
            self.assertGreaterEqual(layers["service/"].ram_bytes, 4096)
            self.assertGreaterEqual(layers["bsp/"].ram_bytes, 256 + 512)

            # 验证序列化与文本报告
            as_dict = report.to_dict()
            self.assertEqual(as_dict["flash_bytes"], report.flash_bytes)
            txt = report.to_text()
            self.assertIn("[footprint] fw.elf", txt)
            self.assertIn("Layer Attribution (.map):", txt)
            self.assertIn("g_service_telemetry", txt)

    def test_arm_elf32_ccmram_dtcm_dma_hazards_bloat_and_stm32_map(self) -> None:
        """构造含 CCMRAM/DTCM DMA 缓冲区、_dtoa_r/_malloc_r 符号及低栈净空的 ARM ELF32 + STM32 .map。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            elf_path = root / "stm32_fw.elf"
            map_path = root / "stm32_fw.map"

            # Section 索引:
            # 1: .text, 2: .rodata, 3: .ARM.exidx, 4: .data, 5: .bss,
            # 6: .ccmram, 7: .ram_d1, 8: ._user_heap_stack
            elf_bytes = _build_arm_elf32(
                sections=[
                    (".text", _SHT_PROGBITS, _SHF_ALLOC | _SHF_EXECINSTR, 0x08000000, 0x4800, 4),
                    (".rodata", _SHT_PROGBITS, _SHF_ALLOC, 0x08004800, 0x0600, 4),
                    (".ARM.exidx", _SHT_ARM_EXIDX, _SHF_ALLOC, 0x08004E00, 0x0020, 4),
                    (".data", _SHT_PROGBITS, _SHF_ALLOC | _SHF_WRITE, 0x20000000, 0x0200, 4),
                    (".bss", _SHT_NOBITS, _SHF_ALLOC | _SHF_WRITE, 0x20000200, 0x1FB00, 4),
                    (".ccmram", _SHT_NOBITS, _SHF_ALLOC | _SHF_WRITE, 0x10000000, 0x0400, 4),
                    (".ram_d1", _SHT_NOBITS, _SHF_ALLOC | _SHF_WRITE, 0x24000000, 0x0800, 32),
                    ("._user_heap_stack", _SHT_NOBITS, _SHF_ALLOC | _SHF_WRITE, 0x2001FD00, 0x0300, 8),
                ],
                symbols=[
                    # STT_FUNC = 0x12 (STB_GLOBAL | STT_FUNC)，Thumb 奇数地址测试清零最低位
                    ("_dtoa_r", 0x08001001, 3420, 0x12, 1),
                    ("_svfprintf_r", 0x08002001, 4800, 0x12, 1),
                    ("__aeabi_ddiv", 0x08003301, 960, 0x12, 1),
                    ("_malloc_r", 0x08003701, 1120, 0x12, 1),
                    ("app_control_loop", 0x08003C01, 640, 0x12, 1),
                    # STT_OBJECT = 0x11 (STB_GLOBAL | STT_OBJECT)
                    ("g_uart_dma_rx_buf", 0x10000100, 256, 0x11, 6),   # CCMRAM -> DMA_IN_CCMRAM
                    ("g_spi_dma_tx_buf", 0x20000208, 128, 0x11, 5),    # H7 DTCM + 非 32B 对齐
                    ("g_adc_dma_samples", 0x24000102, 64, 0x11, 7),    # 非 4B 对齐 -> DMA_UNALIGNED_4B
                    ("g_frame_cache", 0x24000200, 1024, 0x11, 7),
                    # 链接脚本 ABS 符号：_estack = 0x20020000，已用到 0x2001FD00 -> 剩余 768 B (< 1024 B)
                    ("_estack", 0x20020000, 0, 0x10, _SHN_ABS),
                ],
            )
            elf_path.write_bytes(elf_bytes)

            # 模拟真实 arm-none-eabi-ld 生成的 STM32 .map（含折行节名、Discarded 节与归档库）
            map_path.write_text(
                """Archive member included to satisfy reference by file (symbol)

Discarded input sections

 .text.unused_dead_code
                0x00000000      0x999 build/app_dead.o

Memory Configuration

Name             Origin             Length             Attributes
CCMRAM           0x0000000010000000 0x0000000000010000 xrw
DTCMRAM          0x0000000020000000 0x0000000000020000 xrw
RAM_D1           0x0000000024000000 0x0000000000080000 xrw
FLASH            0x0000000008000000 0x0000000000080000 xr
*default*        0x0000000000000000 0xffffffffffffffff

Linker script and memory map

.text           0x0000000008000000     0x4800
 .text.app_control_loop
                0x0000000008000000      0x280 build/app_main.o
 .text.service_water_update_pid
                0x0000000008000280      0x900 build/service_water.o
 .text.bsp_uart_init
                0x0000000008000b80      0x300 build/bsp_uart.o
 .text.HAL_UART_Init
                0x0000000008000e80      0x500 build/stm32h7xx_hal_uart.o
 .text._svfprintf_r
                0x0000000008001380     0x12c0 /usr/arm-none-eabi/lib/libc_nano.a(lib_a-svfprintf.o)
 .text._dtoa_r  0x0000000008002640      0xd5c /usr/arm-none-eabi/lib/libc_nano.a(lib_a-dtoa.o)

.rodata         0x0000000008004800      0x600
 .rodata        0x0000000008004800      0x600 build/service_water.o

.ARM.exidx      0x0000000008004e00       0x20
 .ARM.exidx     0x0000000008004e00       0x20 /usr/arm-none-eabi/lib/libgcc.a(_udivmoddi4.o)

.data           0x0000000020000000      0x200 load address 0x0000000008004e20
 .data.state    0x0000000020000000      0x200 build/service_water.o

.bss            0x0000000020000200    0x1fb00
 .bss.buf       0x0000000020000200    0x1fb00 build/bsp_uart.o

.ccmram         0x0000000010000000      0x400
 .ccmram.dma    0x0000000010000000      0x400 build/bsp_uart.o

.ram_d1         0x0000000024000000      0x800
 .ram_d1.cache  0x0000000024000000      0x800 build/service_water.o

._user_heap_stack
                0x000000002001fd00      0x300
""",
                encoding="utf-8",
            )

            report = analyze_footprint(elf_path, top_n=5)

            # 1. 关键节大小校验
            self.assertEqual(report.key_sections[".text"], 0x4800)
            self.assertEqual(report.key_sections[".rodata"], 0x0600)
            self.assertEqual(report.key_sections[".ARM.exidx"], 0x0020)
            self.assertEqual(report.key_sections[".data"], 0x0200)
            self.assertEqual(report.key_sections[".bss"], 0x1FB00)
            self.assertEqual(report.key_sections[".ccmram"], 0x0400)
            self.assertEqual(report.key_sections[".ram_d1"], 0x0800)
            self.assertEqual(report.key_sections["._user_heap_stack"], 0x0300)

            self.assertEqual(report.flash_bytes, 0x4800 + 0x0600 + 0x0020 + 0x0200)
            self.assertEqual(report.ram_bytes, 0x0200 + 0x1FB00 + 0x0400 + 0x0800 + 0x0300)

            # 2. Bloat 膨胀元凶识别（浮点 printf + newlib 堆）
            bloat_cats = {b.category: b for b in report.bloat_hints}
            self.assertIn("float_printf", bloat_cats)
            self.assertIn("_dtoa_r", bloat_cats["float_printf"].symbols)
            self.assertIn("_svfprintf_r", bloat_cats["float_printf"].symbols)
            self.assertIn("__aeabi_ddiv", bloat_cats["float_printf"].symbols)
            self.assertIn("newlib_heap", bloat_cats)
            self.assertIn("_malloc_r", bloat_cats["newlib_heap"].symbols)

            # 3. .map Memory Configuration 分区占用与百分比（含 .data 的 LMA 计入 FLASH）
            regions = {r.name: r for r in report.map_regions}
            self.assertEqual(regions["FLASH"].origin, 0x08000000)
            self.assertEqual(regions["FLASH"].length, 0x00080000)
            self.assertEqual(regions["FLASH"].used_bytes, 0x4800 + 0x600 + 0x20 + 0x200)
            self.assertAlmostEqual(regions["FLASH"].percent, round((0x5020 * 100.0) / 0x80000, 2))
            self.assertEqual(regions["CCMRAM"].used_bytes, 0x400)
            self.assertEqual(regions["DTCMRAM"].used_bytes, 0x200 + 0x1FB00 + 0x300)

            # 4. Discarded input sections 不应混入模块贡献；各层级准确归因
            mod_names = {m.module for m in report.module_contributions}
            self.assertNotIn("build/app_dead.o", mod_names)
            self.assertIn("build/service_water.o", mod_names)
            self.assertIn("libc_nano.a(lib_a-dtoa.o)", mod_names)

            layers = {lc.layer: lc for lc in report.layer_contributions}
            self.assertIn("service/", layers)
            self.assertIn("bsp/", layers)
            self.assertIn("drivers/", layers)
            self.assertIn("toolchain_lib", layers)
            self.assertIn("app/", layers)

            # 5. DMA 不可达内存、对齐风险及栈净空局促告警
            hazard_codes = {(h.code, h.symbol) for h in report.hazards}
            self.assertIn(("DMA_IN_CCMRAM", "g_uart_dma_rx_buf"), hazard_codes)
            self.assertIn(("DMA_IN_DTCM", "g_spi_dma_tx_buf"), hazard_codes)
            self.assertIn(("DMA_UNALIGNED_32B", "g_spi_dma_tx_buf"), hazard_codes)
            self.assertIn(("DMA_UNALIGNED_4B", "g_adc_dma_samples"), hazard_codes)
            self.assertIn(("LOW_STACK_HEADROOM", "_estack"), hazard_codes)
            self.assertEqual(report.stack_headroom_bytes, 0x300)  # 768 B < 1024 B

    def test_corrupted_elf_raises_footprint_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "broken.elf"
            bad.write_bytes(b"NOT_AN_ELF_BINARY")
            with self.assertRaises(FootprintError):
                analyze_footprint(bad)


# ===========================================================================
# 2. compile_commands.json 自动生成器测试（Make -Bn 无 bear + CMake）
# ===========================================================================


class CompDbGeneratorTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("make") and shutil.which("gcc"), "需要 make + gcc")
    def test_makefile_demo_compdb_without_bear(self) -> None:
        """对 examples/demo_make_fw 真实调用 make -Bn 生成合法的 compile_commands.json。"""
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "demo_make_fw"
            shutil.copytree(DEMO_MAKE_DIR, proj)

            res = generate_compile_commands(proj)
            self.assertTrue(res.success, res.to_text())
            self.assertEqual(res.build_system, "make")
            self.assertEqual(res.entry_count, 1)

            compdb_path = proj / "compile_commands.json"
            self.assertTrue(compdb_path.is_file())
            # 确认干跑模式没有产生真实编译副作用
            self.assertFalse((proj / "build" / "demo_fw.elf").exists())

            data = json.loads(compdb_path.read_text(encoding="utf-8"))
            self.assertEqual(len(data), 1)
            entry = data[0]
            self.assertEqual(Path(entry["file"]), (proj / "main.c").resolve())
            self.assertEqual(Path(entry["directory"]), proj.resolve())
            self.assertEqual(Path(entry["output"]), (proj / "build" / "main.o").resolve())
            args = entry["arguments"]
            self.assertIn("-Wall", args)
            self.assertIn("-DDEMO_INJECT_WARNING", args)
            self.assertIn("-c", args)

            # 即使先真实编译出 main.o，再次执行仍因 -B 完整捕获条目
            subprocess.run(["make", "-C", str(proj)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            res_after_build = generate_compile_commands(proj, extra_args=["DEMO_INJECT_ERROR=1"])
            self.assertTrue(res_after_build.success)
            data2 = json.loads(compdb_path.read_text(encoding="utf-8"))
            self.assertIn("-DDEMO_INJECT_ERROR", data2[0]["arguments"])

    def test_parse_make_dry_run_entering_directory_and_multiline(self) -> None:
        """验证递归子目录切换 Entering/Leaving directory 与反斜杠续行的精准解析。"""
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            sub = proj / "firmware" / "core"
            sub.mkdir(parents=True)
            dry_output = f"""
make: Entering directory '{proj}'
make[1]: Entering directory '{sub}'
mkdir -p build && arm-none-eabi-gcc -mcpu=cortex-m33 \\
    -Wall -DSTM32H563xx -I../inc \\
    -c src/board.c -o build/board.o
arm-none-eabi-gcc build/board.o -o build/fw.elf
make[1]: Leaving directory '{sub}'
gcc -Wall -c app.c -o build/app.o
make: Leaving directory '{proj}'
"""
            entries = parse_make_dry_run(dry_output, default_dir=proj)
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["directory"], str(sub.resolve()))
            self.assertEqual(entries[0]["file"], str((sub / "src" / "board.c").resolve()))
            self.assertEqual(entries[0]["output"], str((sub / "build" / "board.o").resolve()))
            self.assertIn("-DSTM32H563xx", entries[0]["arguments"])

            self.assertEqual(entries[1]["directory"], str(proj.resolve()))
            self.assertEqual(entries[1]["file"], str((proj / "app.c").resolve()))

    @unittest.skipUnless(shutil.which("cmake") and shutil.which("gcc"), "需要 cmake + gcc")
    def test_cmake_demo_compdb_and_root_link(self) -> None:
        """对 CMake 工程生成 <build>/compile_commands.json 并自动同步工程根目录软链接/副本。"""
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "demo_fw"
            shutil.copytree(DEMO_CMAKE_DIR, proj)

            res = generate_compile_commands(proj)
            self.assertTrue(res.success, res.to_text())
            self.assertEqual(res.build_system, "cmake")
            self.assertGreaterEqual(res.entry_count, 1)

            build_db = proj / "build" / "compile_commands.json"
            root_db = proj / "compile_commands.json"
            self.assertTrue(build_db.is_file())
            self.assertTrue(root_db.is_file())

            entries = json.loads(root_db.read_text(encoding="utf-8"))
            self.assertTrue(any(Path(e["file"]).name == "main.c" for e in entries))


# ===========================================================================
# 3. 非阻塞后台串口 UART 日志采集器测试（真实 Linux 内核 os.openpty() + 原生 termios）
# ===========================================================================


@unittest.skipUnless(sys.platform.startswith("linux") and hasattr(os, "openpty"), "需要 Linux pty 支持")
class UartCollectorPtyTests(unittest.TestCase):
    def test_pty_termios_split_utf8_filtering_and_bidirectional_write(self) -> None:
        """使用真实内核伪终端验证原生 termios 采集跨包中文 UTF-8、过滤与下行 write('reboot\\n')。"""
        fd_dir = Path("/proc/self/fd")
        fds_before = len(list(fd_dir.iterdir())) if fd_dir.is_dir() else None

        master_fd, slave_fd = os.openpty()
        slave_name = os.ttyname(slave_fd)
        collector = UartLogCollector(
            port=slave_name,
            baudrate=115200,
            poll_interval_s=0.005,
            backend="posix",
        )
        try:
            st = collector.start()
            self.assertTrue(st.running)
            self.assertEqual(st.port, slave_name)
            self.assertEqual(st.baudrate, 115200)
            self.assertEqual(st.backend, "posix-termios")

            # 1. 将 UTF-8 多字节汉字从中间拆成两包从 master_fd 写入
            msg1 = "[INFO] 水路温度传感器校准正常\r\n[WARN] 流量波动\n[ERROR] 泵压超限报警\r\n".encode("utf-8")
            split_pos = msg1.index("传".encode("utf-8")) + 1  # 切在三字节汉字“传”的第 1 字节之后
            os.write(master_fd, msg1[:split_pos])
            time.sleep(0.02)
            os.write(master_fd, msg1[split_pos:])

            deadline = time.monotonic() + 1.5
            entries = []
            newest_seq = None
            while time.monotonic() < deadline:
                read_res, cur_st = collector.read(auto_start=False)
                if len(read_res.entries) >= 3:
                    entries = read_res.entries
                    newest_seq = read_res.newest_seq
                    break
                time.sleep(0.01)

            self.assertEqual(
                [e.text for e in entries],
                [
                    "[INFO] 水路温度传感器校准正常",
                    "[WARN] 流量波动",
                    "[ERROR] 泵压超限报警",
                ],
            )

            # 2. 验证 pattern + context_lines 过滤
            err_res, _ = collector.read(pattern="error", context_lines=1, auto_start=False)
            self.assertEqual(err_res.matched_count, 1)
            self.assertEqual(
                [e.text for e in err_res.entries],
                ["[WARN] 流量波动", "[ERROR] 泵压超限报警"],
            )

            # 3. 验证增量 since_seq 拉取
            os.write(master_fd, "[INFO] 已恢复待机\n".encode("utf-8"))
            deadline = time.monotonic() + 1.0
            inc_entries = []
            while time.monotonic() < deadline:
                inc_res, _ = collector.read(since_seq=newest_seq, auto_start=False)
                if inc_res.entries:
                    inc_entries = inc_res.entries
                    break
                time.sleep(0.01)
            self.assertEqual([e.text for e in inc_entries], ["[INFO] 已恢复待机"])

            # 4. 验证线程安全下行 write("reboot\n")：从 master_fd 真实读出字节，且不会回显污染 RX 缓冲
            sent_n = collector.write("reboot\n")
            self.assertEqual(sent_n, 7)

            readable, _, _ = select.select([master_fd], [], [], 1.0)
            self.assertTrue(readable, "master_fd 应收到下行串口字节")
            tx_received = os.read(master_fd, 64)
            self.assertEqual(tx_received, b"reboot\n")
            self.assertEqual(collector.status().bytes_sent, 7)

            # 短暂等待确认 raw 8N1 关闭了 ECHO，TX 数据没有回环进日志缓冲
            time.sleep(0.04)
            after_tx, _ = collector.read(since_seq=inc_entries[-1].seq, auto_start=False)
            self.assertEqual(after_tx.entries, [])

            # 5. 并发读写 + 干净 stop() 无死锁验证
            t0 = time.monotonic()
            stop_st = collector.stop()
            elapsed = time.monotonic() - t0
            self.assertFalse(stop_st.running)
            self.assertLess(elapsed, 0.5)
        finally:
            collector.stop()
            os.close(slave_fd)
            os.close(master_fd)

        # 验证无文件描述符泄漏
        if fds_before is not None:
            fds_after = len(list(fd_dir.iterdir()))
            self.assertEqual(fds_after, fds_before)

    def test_standard_baudrates_and_singleton_and_port_listing(self) -> None:
        """验证标准波特率表、默认单例与串口枚举接口均可正常工作。"""
        self.assertIn(115200, STANDARD_BAUDRATES)
        self.assertIn(921600, STANDARD_BAUDRATES)
        self.assertIn(2000000, STANDARD_BAUDRATES)
        self.assertFalse(default_uart_collector.is_running())

        ports = list_serial_ports()
        self.assertIsInstance(ports, list)

        # 非法波特率应抛出清晰的 UartError
        master_fd, slave_fd = os.openpty()
        try:
            bad_collector = UartLogCollector(port=os.ttyname(slave_fd), baudrate=12345, backend="posix")
            with self.assertRaises(UartError):
                bad_collector.start()
        finally:
            os.close(slave_fd)
            os.close(master_fd)

    def test_concurrent_read_and_write_stress(self) -> None:
        """多线程并发读取日志与发送下行指令无竞争无死锁。"""
        master_fd, slave_fd = os.openpty()
        collector = UartLogCollector(port=os.ttyname(slave_fd), baudrate=921600, poll_interval_s=0.005, backend="posix")
        errors: list[Exception] = []
        tx_collected = bytearray()
        done = threading.Event()

        def master_pump() -> None:
            idx = 0
            while not done.is_set():
                r, _, _ = select.select([master_fd], [], [], 0.01)
                if r:
                    tx_collected.extend(os.read(master_fd, 4096))
                if idx < 20:
                    os.write(master_fd, f"tick={idx}\n".encode("utf-8"))
                    idx += 1

        pump_thread = threading.Thread(target=master_pump, daemon=True)
        try:
            collector.start()
            pump_thread.start()

            def worker(tid: int) -> None:
                try:
                    for i in range(5):
                        collector.write(f"cmd_{tid}_{i}\n")
                        collector.read(tail_lines=10, auto_start=False)
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=3.0)
                self.assertFalse(t.is_alive())

            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                cur_res, _ = collector.read(tail_lines=30, auto_start=False)
                if len(cur_res.entries) >= 20 and tx_collected.count(b"\n") >= 20:
                    break
                time.sleep(0.01)

            done.set()
            pump_thread.join(timeout=1.0)

            self.assertEqual(errors, [])
            self.assertEqual(tx_collected.count(b"\n"), 20)
            res, _ = collector.read(tail_lines=30, auto_start=False)
            self.assertEqual(len(res.entries), 20)
        finally:
            done.set()
            collector.stop()
            os.close(slave_fd)
            os.close(master_fd)


if __name__ == "__main__":
    unittest.main()
