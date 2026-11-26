"""ELF/DWARF 地址解析与 Cortex-M 全系列死机自动尸检单元测试。

同时兼容 `python3 tests/test_crash.py`、`python3 -m unittest` 与 `pytest`。
"""

from __future__ import annotations

import io
import json
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from mcu_harness.crash import (
    CrashReport,
    decode_cpuid,
    decode_exc_return,
    decode_exception_name,
    diagnose_crash,
)
from mcu_harness.crash.cli import main as crash_cli_main
from mcu_harness.crash.cortex_m import SCB_CFSR, SCB_CPUID, SCB_HFSR, SCB_ICSR
from mcu_harness.elf import (
    find_containing_symbol,
    find_symbol,
    resolve_address,
)
from mcu_harness.probe.base import ProbeError
from mcu_harness.probe.mock import MockProbe


def _make_minimal_arm_elf32(symbols: list[tuple[str, int, int, int]]) -> bytes:
    """构造最小合法 ARM little-endian ELF32（无 DWARF 段，用于测试符号表回退）。

    symbols 元素：(name, st_value, st_size, st_info)
    """
    strtab = bytearray(b"\x00")
    sym_entries = bytearray(struct.pack("<IIIBBH", 0, 0, 0, 0, 0, 0))  # STN_UNDEF
    for name, addr, size, st_info in symbols:
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

    sh0 = struct.pack("<IIIIIIIIII", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    sh1 = struct.pack("<IIIIIIIIII", 0, 3, 0, 0, str_off, len(strtab), 0, 0, 1, 0)
    sh2 = struct.pack("<IIIIIIIIII", 0, 2, 0, 0, sym_off, len(sym_entries), 1, 1, 4, 16)
    return bytes(ehdr) + bytes(strtab) + bytes(sym_entries) + sh0 + sh1 + sh2


class _StackBoundaryMockProbe(MockProbe):
    """当访问低于 SRAM 基址 0x20000000 的越界栈空间时模拟硬件 AHB AP 读取错误。"""

    def read_u32(self, address: int, count: int = 1) -> list[int]:
        if 0x1FFF0000 <= address < 0x20000000:
            raise ProbeError(f"MEM-AP fault reading unmapped stack address 0x{address:08X}")
        return super().read_u32(address, count)


class TestCrashForensics(unittest.TestCase):
    """覆盖 Cortex-M0+/M3/M4/M7/M23/M33/M55 尸检与 ELF/DWARF 行号映射。"""

    def test_cpuid_and_exception_names(self) -> None:
        cases = [
            (0x410CC200, "Cortex-M0", "Armv6-M", False, False, False),
            (0x410CC601, "Cortex-M0+", "Armv6-M", False, False, False),
            (0x412FC231, "Cortex-M3", "Armv7-M", True, False, False),
            (0x410FC241, "Cortex-M4", "Armv7-M", True, False, False),
            (0x411FC272, "Cortex-M7", "Armv7-M", True, False, False),
            (0x410FD200, "Cortex-M23", "Armv8-M Baseline", False, True, True),
            (0x410FD210, "Cortex-M33", "Armv8-M Mainline", True, True, True),
            (0x411FD221, "Cortex-M55", "Armv8.1-M Mainline", True, True, True),
        ]
        for raw, expected_name, expected_arch, has_cfsr, has_sfsr, has_splim in cases:
            ident = decode_cpuid(raw)
            self.assertEqual(ident.core_name, expected_name)
            self.assertEqual(ident.arch, expected_arch)
            self.assertEqual(ident.has_cfsr, has_cfsr)
            self.assertEqual(ident.has_sfsr, has_sfsr)
            self.assertEqual(ident.has_splim, has_splim)

        self.assertEqual(decode_exception_name(0), "Thread Mode")
        self.assertEqual(decode_exception_name(3), "HardFault")
        self.assertEqual(decode_exception_name(7), "SecureFault")
        self.assertEqual(decode_exception_name(15), "SysTick")
        self.assertEqual(decode_exception_name(16), "External IRQ #0")
        self.assertEqual(decode_exception_name(42), "External IRQ #26")

    def test_cortex_m33_preciserr_null_deref_and_exc_return(self) -> None:
        """测试 1：Cortex-M33 PRECISERR 空指针结构体解引用（BFAR=0x00000004）与 EXC_RETURN 解包。"""
        with MockProbe() as probe:
            # Armv8-M EXC_RETURN 0xFFFFFFB8: S=0 (NS), DCRS=1, FType=1 (no FPU), Mode=1 (Thread), SPSEL=0 (MSP), ES=0
            probe.inject_cortex_m_fault(
                cfsr=0x00008200,       # BFSR.PRECISERR (bit 9) | BFSR.BFARVALID (bit 15)
                hfsr=0x40000000,       # HFSR.FORCED (bit 30)
                bfar=0x00000004,       # 空结构体指针偏移 0x04
                stacked_pc=0x08000420,
                stacked_lr=0x08000311,
                sp_addr=0x2001FF80,
                exc_return=0xFFFFFFB8,
                ipsr=3,
                cpuid=0x410FD210,
            )

            report = diagnose_crash(probe)
            self.assertIsInstance(report, CrashReport)
            self.assertTrue(report.fault_detected)
            self.assertEqual(report.core.core_name, "Cortex-M33")
            self.assertEqual(report.active_exception, "HardFault")
            self.assertTrue(report.bfar_valid)
            self.assertFalse(report.mmfar_valid)
            self.assertEqual(report.fault_addresses.get("BFAR"), "0x00000004")

            fault_names = {f.name for f in report.active_faults}
            self.assertIn("PRECISERR", fault_names)
            self.assertIn("FORCED", fault_names)

            self.assertIsNotNone(report.exc_return)
            assert report.exc_return is not None
            self.assertEqual(report.exc_return.stack_pointer, "MSP")
            self.assertEqual(report.exc_return.return_mode, "Thread")
            self.assertFalse(report.exc_return.fpu_frame)
            self.assertEqual(report.exc_return.security_stack, "Non-secure")

            self.assertIsNotNone(report.stacked_frame)
            assert report.stacked_frame is not None
            self.assertEqual(report.stacked_frame["stack_pointer"], "MSP")
            self.assertEqual(report.stacked_frame["stacked_pc"], "0x08000420")
            self.assertEqual(report.stacked_frame["stacked_lr"], "0x08000311")

            joined_recs = "\n".join(report.recommendations)
            self.assertIn("空指针", joined_recs)
            self.assertIn("0x4", joined_recs)
            self.assertIn("PRECISERR", report.to_text())

        # 同时验证直接无参调用 MockProbe.inject_cortex_m_fault() 默认行为也能正确恢复压栈帧，且 BFARVALID=0 时不标记有效
        with MockProbe() as probe2:
            probe2.inject_cortex_m_fault(cfsr=0x00000200, bfar=0xDEADBEEF)  # BFARVALID 未置位
            rep2 = diagnose_crash(probe2)
            self.assertTrue(rep2.fault_detected)
            self.assertFalse(rep2.bfar_valid)
            self.assertNotIn("BFAR", rep2.fault_addresses)
            assert rep2.stacked_frame is not None
            self.assertEqual(rep2.stacked_frame["stacked_pc"], "0x08000420")

    def test_cortex_m33_stkof_msplim_and_psp_thread_stack_selection(self) -> None:
        """测试 2：Cortex-M33 STKOF（MSP <= MSPLIM）栈溢出诊断 + PSP 线程栈选择与越界容错。"""
        # 2a. MSP <= MSPLIM 硬件栈限制溢出
        with MockProbe() as probe:
            probe.inject_cortex_m_fault(
                cfsr=1 << 20,          # UFSR.STKOF (bit 20)
                hfsr=0x40000000,       # FORCED
                bfar=0x00000000,
                stacked_pc=0x08001234,
                stacked_lr=0x08001201,
                sp_addr=0x20001000,
                exc_return=0xFFFFFFB8,
                ipsr=3,
                cpuid=0x410FD210,
            )
            probe.set_core_reg("msplim", 0x20001000)  # MSP == MSPLIM -> 触发硬件栈限制
            probe.set_core_reg("msp", 0x20001000)

            report = diagnose_crash(probe)
            self.assertTrue(report.fault_detected)
            self.assertTrue(report.stack_overflow)
            fault_names = {f.name for f in report.active_faults}
            self.assertIn("STKOF", fault_names)
            self.assertTrue(any("MSPLIM" in w for w in report.warnings))
            self.assertTrue(any("栈溢出" in r for r in report.recommendations))

        # 2b. RTOS 任务使用 PSP 线程栈（EXC_RETURN bit2 SPSEL=1: 0xFFFFFFAC 含 FPU 扩展栈帧）
        with MockProbe() as probe_psp:
            psp_addr = 0x20014F00
            msp_addr = 0x2001FF00
            # 在 MSP 放入干扰值，确保解包器真正从 PSP 读取而非 MSP
            for i in range(8):
                probe_psp.set_word(msp_addr + i * 4, 0xAAAAAAAA)
            psp_frame = [
                0x00000010,  # R0
                0x00000020,  # R1
                0x00000030,  # R2
                0x00000040,  # R3
                0x00000050,  # R12
                0x08002105,  # LR
                0x08002248,  # PC
                0x01000000,  # xPSR (Thumb)
            ]
            for i, w in enumerate(psp_frame):
                probe_psp.set_word(psp_addr + i * 4, w)

            # 0xFFFFFFEC: S=1(Secure), DCRS=1, FType=0(含FPU扩展帧!), Mode=1(Thread), SPSEL=1(PSP), ES=0
            probe_psp.set_word(SCB_CPUID, 0x410FD210)
            probe_psp.set_word(SCB_ICSR, 6)  # UsageFault
            probe_psp.set_word(SCB_CFSR, 1 << 17)  # UFSR.INVSTATE
            probe_psp.set_word(SCB_HFSR, 0)
            probe_psp.set_core_reg("msp", msp_addr)
            probe_psp.set_core_reg("psp", psp_addr)
            probe_psp.set_core_reg("psplim", 0x20014000)
            probe_psp.set_core_reg("lr", 0xFFFFFFEC)
            probe_psp.set_core_reg("pc", 0x080001A0)
            probe_psp.set_core_reg("xpsr", 0x01000006)
            probe_psp._halted = True

            rep_psp = diagnose_crash(probe_psp)
            self.assertTrue(rep_psp.fault_detected)
            self.assertEqual(rep_psp.active_exception, "UsageFault")
            assert rep_psp.exc_return is not None
            self.assertEqual(rep_psp.exc_return.stack_pointer, "PSP")
            self.assertTrue(rep_psp.exc_return.fpu_frame)
            self.assertEqual(rep_psp.exc_return.security_stack, "Secure")

            assert rep_psp.stacked_frame is not None
            self.assertEqual(rep_psp.stacked_frame["stack_pointer"], "PSP")
            self.assertEqual(rep_psp.stacked_frame["sp_address"], "0x20014F00")
            self.assertEqual(rep_psp.stacked_frame["stacked_pc"], "0x08002248")
            self.assertEqual(rep_psp.stacked_frame["r0"], "0x00000010")
            self.assertTrue(any("Thumb" in r or "INVSTATE" in r for r in rep_psp.recommendations))

        # 2c. 栈下溢跌破 SRAM 边界导致读取抛 ProbeError 时，捕获异常并给出栈溢出结论而不崩溃
        with _StackBoundaryMockProbe() as probe_oob:
            probe_oob.set_word(SCB_CPUID, 0x410FC241)  # Cortex-M4
            probe_oob.set_word(SCB_ICSR, 3)
            probe_oob.set_word(SCB_CFSR, 1 << 12)  # BFSR.STKERR
            probe_oob.set_word(SCB_HFSR, 0x40000000)
            probe_oob.set_core_reg("msp", 0x1FFFFFF0)  # 已跌破 0x20000000
            probe_oob.set_core_reg("lr", 0xFFFFFFF9)
            probe_oob.set_core_reg("xpsr", 0x01000003)
            probe_oob._halted = True

            rep_oob = diagnose_crash(probe_oob)
            self.assertTrue(rep_oob.fault_detected)
            self.assertTrue(rep_oob.stack_overflow)
            self.assertIsNone(rep_oob.stacked_frame)
            self.assertIsNotNone(rep_oob.stack_read_error)
            self.assertTrue(any("0x1FFFFFF0" in w for w in rep_oob.warnings))

    def test_cortex_m0_plus_without_cfsr_hardfault_unpacking(self) -> None:
        """测试 3：Cortex-M0+ (CPUID=0x410CC601) 无 CFSR 场景下跳过 CFSR 并解包 HardFault 异常栈帧。"""
        with MockProbe(device="STM32G071RB") as probe:
            # 故意在 CFSR/BFAR 地址预置非零垃圾数据，验证 Armv6-M 不会误读不存在的 CFSR/BFAR
            probe.inject_cortex_m_fault(
                cfsr=0x0303FFFF,
                hfsr=0x40000002,
                bfar=0xBAD0BEEF,
                stacked_pc=0x08000642,
                stacked_lr=0x08000511,
                sp_addr=0x20001FC0,
                exc_return=0xFFFFFFF9,  # Armv6-M: Thread mode, MSP
                ipsr=3,                 # HardFault
                cpuid=0x410CC601,       # Cortex-M0+ r0p1
            )

            report = diagnose_crash(probe)
            self.assertTrue(report.fault_detected)
            self.assertEqual(report.core.core_name, "Cortex-M0+")
            self.assertEqual(report.core.arch, "Armv6-M")
            self.assertFalse(report.core.has_cfsr)

            # 确认未读取 CFSR / HFSR / BFAR / SFSR
            self.assertNotIn("CFSR", report.scb_registers)
            self.assertNotIn("BFAR", report.scb_registers)
            self.assertNotIn("SFSR", report.scb_registers)
            self.assertEqual(report.active_faults, [])
            self.assertFalse(report.bfar_valid)

            # 确认异常栈帧完整解包
            assert report.stacked_frame is not None
            self.assertEqual(report.stacked_frame["stack_pointer"], "MSP")
            self.assertEqual(report.stacked_frame["stacked_pc"], "0x08000642")
            self.assertEqual(report.stacked_frame["stacked_lr"], "0x08000511")
            self.assertTrue(any("非对齐" in r for r in report.recommendations))

    @unittest.skipUnless(
        shutil.which("gcc") and shutil.which("addr2line"),
        "需要宿主机 /usr/bin/gcc 与 addr2line",
    )
    def test_real_gcc_dwarf_elf_resolve_and_crash_forensics(self) -> None:
        """测试 4：用宿主机 gcc -g -O0 真实编译带 DWARF 的 ELF，验证 resolve_address + diagnose_crash 源码行号还原。"""
        with tempfile.TemporaryDirectory() as tmp:
            proj_dir = Path(tmp) / "fw_project"
            src_dir = proj_dir / "src"
            build_dir = proj_dir / "build"
            src_dir.mkdir(parents=True)
            build_dir.mkdir(parents=True)

            c_file = src_dir / "fault_demo.c"
            elf_file = build_dir / "fault_demo.elf"

            # 逐行固定行号，确保断言精确到行
            c_lines = [
                "#include <stdint.h>",                                                 # line 1
                "",                                                                    # line 2
                "struct SensorHandle { uint32_t id; volatile uint32_t ctrl; };",      # line 3
                "",                                                                    # line 4
                "__attribute__((noinline, aligned(4)))",                              # line 5
                "void HardFault_Handler(void) {",                                      # line 6
                "    volatile int spin = 1;",                                          # line 7
                "    (void)spin;",                                                     # line 8
                "}",                                                                   # line 9
                "",                                                                    # line 10
                "__attribute__((noinline, aligned(4)))",                              # line 11
                "int crash_write_sensor(struct SensorHandle *dev, uint32_t val) {",   # line 12
                "    dev->ctrl = val;",                                                # line 13  <-- 崩溃触发行
                "    return 0;",                                                       # line 14
                "}",                                                                   # line 15
                "",                                                                    # line 16
                "__attribute__((noinline, aligned(4)))",                              # line 17
                "int poll_task_step(uint32_t tick) {",                                 # line 18
                "    int rc = crash_write_sensor((struct SensorHandle *)0, tick);",    # line 19  <-- 调用点行
                "    return rc + 1;",                                                  # line 20
                "}",                                                                   # line 21
                "",                                                                    # line 22
                "int main(void) {",                                                    # line 23
                "    HardFault_Handler();",                                            # line 24
                "    return poll_task_step(42);",                                      # line 25
                "}",                                                                   # line 26
            ]
            c_file.write_text("\n".join(c_lines) + "\n", encoding="utf-8")

            subprocess.run(
                [
                    "gcc",
                    "-g",
                    "-O0",
                    "-no-pie",
                    "-fno-pie",
                    "-fno-stack-protector",
                    str(c_file),
                    "-o",
                    str(elf_file),
                ],
                check=True,
            )

            crash_sym = find_symbol(elf_file, "crash_write_sensor")
            caller_sym = find_symbol(elf_file, "poll_task_step")
            handler_sym = find_symbol(elf_file, "HardFault_Handler")
            self.assertIsNotNone(crash_sym)
            self.assertIsNotNone(caller_sym)
            self.assertIsNotNone(handler_sym)
            assert crash_sym is not None and caller_sym is not None and handler_sym is not None

            # 在 crash_write_sensor 函数体内找到映射到第 13 行 `dev->ctrl = val;` 的真实机器码地址
            fault_pc_addr = None
            for off in range(0, crash_sym.size, 2):
                cand = crash_sym.address + off
                loc = resolve_address(elf_file, cand, project_dir=proj_dir)
                if loc.line == 13:
                    fault_pc_addr = cand
                    break
            self.assertIsNotNone(fault_pc_addr, "应在 crash_write_sensor 内定位到第 13 行指令地址")
            assert fault_pc_addr is not None

            # 在 poll_task_step 内找到调用 crash_write_sensor 之后的返回地址（模拟硬件压栈的 LR | 1）
            return_lr_addr = None
            objdump = shutil.which("objdump")
            if objdump:
                dis = subprocess.check_output(
                    [objdump, "-d", str(elf_file)],
                    text=True,
                    env={"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"},
                )
                lines = dis.splitlines()
                for idx, ln in enumerate(lines):
                    if "<crash_write_sensor>" in ln and "call" in ln and idx + 1 < len(lines):
                        m = re.match(r"^\s*([0-9a-fA-F]+):", lines[idx + 1])
                        if m:
                            cand_ret = int(m.group(1), 16)
                            if caller_sym.address <= cand_ret < caller_sym.address + caller_sym.size:
                                return_lr_addr = cand_ret | 1  # 置上 Thumb 标志位测试对齐减 2
                                break

            if return_lr_addr is None:
                # 兜底：扫描满足 (addr & ~1) - 2 落在第 19 行的地址
                for off in range(2, caller_sym.size, 2):
                    cand = (caller_sym.address + off) | 1
                    loc = resolve_address(elf_file, cand, project_dir=proj_dir, is_return_address=True)
                    if loc.line == 19:
                        return_lr_addr = cand
                        break
            self.assertIsNotNone(return_lr_addr, "应在 poll_task_step 内定位到第 19 行调用返回地址")
            assert return_lr_addr is not None

            # 将真实 ELF 地址注入 Cortex-M33 HardFault 现场，执行端到端尸检
            with MockProbe() as probe:
                probe.inject_cortex_m_fault(
                    cfsr=0x00008200,  # PRECISERR | BFARVALID
                    hfsr=0x40000000,  # FORCED
                    bfar=0x00000004,  # offsetof(SensorHandle, ctrl) == 4
                    stacked_pc=fault_pc_addr,
                    stacked_lr=return_lr_addr,
                    exc_return=0xFFFFFFB8,
                    ipsr=3,
                    cpuid=0x410FD210,
                )
                probe.set_core_reg("pc", handler_sym.address)

                report = diagnose_crash(probe, elf_path=elf_file, project_dir=proj_dir)

            self.assertTrue(report.fault_detected)
            spc_loc = report.locations["stacked_pc"]
            slr_loc = report.locations["stacked_lr"]
            pc_loc = report.locations["pc"]

            self.assertEqual(spc_loc.file, "src/fault_demo.c")
            self.assertEqual(spc_loc.symbol, "crash_write_sensor")
            self.assertEqual(spc_loc.line, 13)

            self.assertEqual(slr_loc.file, "src/fault_demo.c")
            self.assertEqual(slr_loc.symbol, "poll_task_step")
            self.assertEqual(slr_loc.line, 19)

            self.assertEqual(pc_loc.symbol, "HardFault_Handler")
            self.assertEqual(pc_loc.file, "src/fault_demo.c")
            self.assertIn("src/fault_demo.c:13", report.summary)

            # 同时验证无外部 addr2line 时，resolve_address 优雅降级返回 func+0xoffset
            fallback_loc = resolve_address(elf_file, fault_pc_addr | 1, addr2line_bin="")
            self.assertEqual(fallback_loc.symbol, "crash_write_sensor")
            self.assertIsNone(fallback_loc.file)
            self.assertIsNone(fallback_loc.line)
            self.assertTrue(fallback_loc.format_short().startswith("crash_write_sensor+0x"))

            # 验证 -O1 always_inline 内联调用链 (inlined_chain) 解析与路径归一化
            inline_c = src_dir / "inline_demo.c"
            inline_elf = build_dir / "inline_demo.elf"
            inline_c.write_text(
                "static inline __attribute__((always_inline)) void leaf_poke(int v) {\n"
                "    volatile int *p = (int *)0x8;\n"
                "    *p = v;\n"
                "}\n"
                "__attribute__((noinline, aligned(4))) int outer_entry(int x) {\n"
                "    leaf_poke(x + 1);\n"
                "    return 0;\n"
                "}\n"
                "int main(void) { return outer_entry(1); }\n",
                encoding="utf-8",
            )
            subprocess.run(["gcc", "-g", "-O1", "-no-pie", "-fno-pie", str(inline_c), "-o", str(inline_elf)], check=True)
            outer_sym = find_symbol(inline_elf, "outer_entry")
            assert outer_sym is not None
            inlined_hit = None
            for off in range(0, outer_sym.size, 2):
                cand_loc = resolve_address(inline_elf, outer_sym.address + off, project_dir=proj_dir)
                if cand_loc.symbol == "leaf_poke" and cand_loc.inlined_chain:
                    inlined_hit = cand_loc
                    break
            self.assertIsNotNone(inlined_hit, "应解析出 leaf_poke 内联到 outer_entry 的 inlined_chain")
            assert inlined_hit is not None
            self.assertEqual(inlined_hit.file, "src/inline_demo.c")
            self.assertTrue(any("outer_entry at src/inline_demo.c:6" in item for item in inlined_hit.inlined_chain))

    def test_elf32_symbol_fallback_and_zero_size_asm_handler(self) -> None:
        """测试无 DWARF 的 ARM ELF32 符号查找、Thumb 位清零与 size==0 汇编函数回退。"""
        with tempfile.TemporaryDirectory() as tmp:
            elf = Path(tmp) / "bare.elf"
            elf.write_bytes(
                _make_minimal_arm_elf32(
                    [
                        ("Reset_Handler", 0x08000101, 0, 0x12),     # STT_FUNC, size=0 汇编入口
                        ("motor_ctrl_step", 0x08000201, 64, 0x12),  # STT_FUNC, [0x08000200, 0x08000240)
                    ]
                )
            )

            # 精确范围匹配（传入奇数 Thumb PC）
            hit = find_containing_symbol(elf, 0x08000215)
            self.assertIsNotNone(hit)
            assert hit is not None
            self.assertEqual(hit[0].name, "motor_ctrl_step")
            self.assertEqual(hit[1], 0x14)

            # size == 0 的汇编函数回退匹配
            asm_loc = resolve_address(elf, 0x08000119, addr2line_bin="")
            self.assertEqual(asm_loc.symbol, "Reset_Handler")
            self.assertEqual(asm_loc.symbol_offset, 0x18)
            self.assertEqual(asm_loc.format_short(), "Reset_Handler+0x18")

            # 文件不存在时绝不抛异常
            missing_loc = resolve_address(Path(tmp) / "nonexistent.elf", 0x08000200)
            self.assertEqual(missing_loc.address, "0x08000200")
            self.assertIsNone(missing_loc.symbol)

    def test_halt_resume_state_preservation_and_securefault_and_cli(self) -> None:
        """测试运行态自动暂停恢复、死机暂停态保持、Armv8-M SFSR 解码与 mcu-crash CLI。"""
        # 1. 原本处于正常 Thread 运行态：keep_halted=False 默认自动恢复运行，且 fault_detected=False
        with MockProbe(halted=False) as running_probe:
            rep_ok = diagnose_crash(running_probe, keep_halted=False)
            self.assertFalse(rep_ok.fault_detected)
            self.assertFalse(rep_ok.halted_before)
            self.assertFalse(rep_ok.halted_after)
            self.assertFalse(running_probe.is_halted())

            # keep_halted=True 时采样后保持暂停
            rep_hold = diagnose_crash(running_probe, keep_halted=True)
            self.assertFalse(rep_hold.halted_before)
            self.assertTrue(rep_hold.halted_after)
            self.assertTrue(running_probe.is_halted())

        # 2. Armv8-M SecureFault (AUVIOL + SFARVALID) 且原本已暂停：keep_halted=False 仍保持暂停
        with MockProbe() as tz_probe:
            tz_probe.inject_cortex_m_fault(
                cfsr=0,
                hfsr=0,
                sfsr=(1 << 6) | (1 << 3),  # SFARVALID (bit 6) | AUVIOL (bit 3)
                sfar=0x52020014,           # 非安全态非法访问安全 GPIOA_S
                ipsr=7,                    # SecureFault
                cpuid=0x410FD210,
                exc_return=0xFFFFFFB8,
            )
            rep_tz = diagnose_crash(tz_probe, keep_halted=False)
            self.assertTrue(rep_tz.fault_detected)
            self.assertTrue(rep_tz.halted_before)
            self.assertTrue(rep_tz.halted_after)
            self.assertEqual(rep_tz.active_exception, "SecureFault")
            self.assertTrue(rep_tz.sfar_valid)
            self.assertEqual(rep_tz.fault_addresses.get("SFAR"), "0x52020014")
            self.assertIn("AUVIOL", {f.name for f in rep_tz.active_faults})

        # 3. 验证 mcu-crash CLI (--mock 文本与 --json 结构化输出)
        buf_json = io.StringIO()
        with redirect_stdout(buf_json):
            rc = crash_cli_main(["--mock", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf_json.getvalue())
        self.assertTrue(payload["fault_detected"])
        self.assertEqual(payload["core"]["core_name"], "Cortex-M33")
        self.assertEqual(payload["fault_addresses"]["BFAR"], "0x00000004")

        buf_txt = io.StringIO()
        with redirect_stdout(buf_txt):
            rc2 = crash_cli_main(["--mock"])
        self.assertEqual(rc2, 0)
        self.assertIn("FAULT DETECTED", buf_txt.getvalue())


if __name__ == "__main__":
    unittest.main()
