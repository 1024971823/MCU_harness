"""Phase 5B Cortex-M DWT 非侵入性能剖析与硬件数据观察点单元测试。

兼容运行方式：
    * `PYTHONPATH=src python3 tests/test_trace.py -v`
    * `PYTHONPATH=src python3 -m unittest discover -s tests -p "test_trace.py" -v`
    * `pytest`
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from mcu_harness.elf import find_symbol
from mcu_harness.probe.mock import MockProbe
from mcu_harness.safety.guard import default_safety_guard
from mcu_harness.trace import (
    DCB_DEMCR,
    DCB_DEMCR_TRCENA,
    DWT_CTRL,
    DWT_CTRL_CYCCNTENA,
    DWT_CYCCNT,
    DWT_FUNCTION_MATCHED_BIT,
    DWT_PCSR,
    SCB_CPUID,
    TraceError,
    clear_watchpoints,
    dwt_comp_addr,
    dwt_function_addr,
    dwt_mask_addr,
    list_watchpoints,
    measure_cycles,
    profile_pc_hotspots,
    set_watchpoint,
)
from mcu_harness.trace.cli import main as trace_cli_main

_CPUID_M4 = 0x410FC241
_CPUID_M33 = 0x410FD210
_CPUID_M0PLUS = 0x410CC601


class _DynamicCycleMockProbe(MockProbe):
    """动态推进 DWT_CYCCNT 的模拟探针，支持自定义起始周期（测试 32 位回绕）与停滞模式。"""

    def __init__(
        self,
        *,
        initial_cyccnt: int = 0,
        sim_core_hz: int = 100_000_000,
        stalled: bool = False,
        initial_demcr: int = 0,
        initial_ctrl: int = 4 << 28,
        cpuid: int = _CPUID_M33,
    ) -> None:
        super().__init__(
            memory_words={
                SCB_CPUID: cpuid,
                DCB_DEMCR: initial_demcr,
                DWT_CTRL: initial_ctrl,
                DWT_CYCCNT: initial_cyccnt & 0xFFFFFFFF,
            }
        )
        self._initial_cyccnt = initial_cyccnt & 0xFFFFFFFF
        self._sim_core_hz = sim_core_hz
        self._stalled = stalled
        self._start_t: float | None = None
        self.halt_calls = 0

    def halt(self) -> bool:
        self.halt_calls += 1
        return super().halt()

    def write_u32(self, address: int, values: list[int] | int) -> None:
        super().write_u32(address, values)
        if not isinstance(values, int) and address == DWT_CTRL and (values[0] & DWT_CTRL_CYCCNTENA):
            if self._start_t is None:
                self._start_t = time.monotonic()

    def read_u32(self, address: int, count: int = 1) -> list[int]:
        if count == 1 and address == DWT_CYCCNT:
            self._require_conn()
            if self._stalled:
                return [self._initial_cyccnt]
            demcr = super().read_u32(DCB_DEMCR, 1)[0]
            ctrl = super().read_u32(DWT_CTRL, 1)[0]
            if (demcr & DCB_DEMCR_TRCENA) and (ctrl & DWT_CTRL_CYCCNTENA):
                t0 = self._start_t if self._start_t is not None else time.monotonic()
                elapsed = max(0.0, time.monotonic() - t0)
                val = (self._initial_cyccnt + int(elapsed * self._sim_core_hz)) & 0xFFFFFFFF
                return [val]
            return [self._initial_cyccnt]
        return super().read_u32(address, count)


class _SequencePcsrMockProbe(MockProbe):
    """按预设序列返回 DWT_PCSR 的模拟探针，并验证全程未暂停 CPU。"""

    def __init__(self, pcsr_values: list[int], *, cpuid: int = _CPUID_M33) -> None:
        super().__init__(memory_words={SCB_CPUID: cpuid, DWT_CTRL: 4 << 28})
        self._pcsr_values = list(pcsr_values)
        self._idx = 0
        self.halt_calls = 0

    def halt(self) -> bool:
        self.halt_calls += 1
        return super().halt()

    def read_u32(self, address: int, count: int = 1) -> list[int]:
        if count == 1 and address == DWT_PCSR:
            self._require_conn()
            val = self._pcsr_values[self._idx % len(self._pcsr_values)]
            self._idx += 1
            return [val & 0xFFFFFFFF]
        return super().read_u32(address, count)


class TestCycleMeasure(unittest.TestCase):
    """测试 measure_cycles：非侵入开启 TRCENA/CYCCNTENA、频率估算、32 位回绕与停滞检测。"""

    def test_measure_cycles_frequency_estimation_and_preserves_demcr_bits(self) -> None:
        probe = _DynamicCycleMockProbe(
            initial_cyccnt=1000,
            sim_core_hz=120_000_000,
            initial_demcr=0x00000001,  # 保留已有 VC_CORERESET 位
            initial_ctrl=(4 << 28) | (1 << 12),
        )
        before_audit = len(default_safety_guard.recent_audit(500))
        with probe:
            report = measure_cycles(probe, duration_ms=40.0, core_hz=120_000_000)

        self.assertEqual(probe.halt_calls, 0)
        self.assertTrue(report.cyccnt_advancing)
        self.assertFalse(report.wrapped)
        self.assertGreater(report.delta_cycles, 1_000_000)
        self.assertAlmostEqual(report.estimated_mhz, 120.0, delta=30.0)
        self.assertIsNotNone(report.error_percent)
        self.assertIn("tolerance", report.window_precision_note)

        # 验证保留原有 DEMCR / DWT_CTRL 其余位且已置位 TRCENA / CYCCNTENA
        with probe:
            demcr_after = probe.read_u32(DCB_DEMCR, 1)[0]
            ctrl_after = probe.read_u32(DWT_CTRL, 1)[0]
        self.assertEqual(demcr_after & 0x01000001, DCB_DEMCR_TRCENA | 0x1)
        self.assertEqual(ctrl_after & ((4 << 28) | (1 << 12) | DWT_CTRL_CYCCNTENA), (4 << 28) | (1 << 12) | DWT_CTRL_CYCCNTENA)
        self.assertGreater(len(default_safety_guard.recent_audit(500)), before_audit)

    def test_measure_cycles_32bit_wraparound(self) -> None:
        """验证 DWT_CYCCNT 从 0xFFFFFFF0 回绕至 0x00000400 时的精确 32 位差分计算。"""
        probe = MockProbe(memory_words={DWT_CTRL: 4 << 28})
        reads = [0xFFFFFFF0, 0x00000400]

        def fake_read_u32(addr: int, count: int = 1) -> list[int]:
            if count == 1 and addr == DWT_CYCCNT:
                return [reads.pop(0)]
            return MockProbe.read_u32(probe, addr, count)

        with probe, mock.patch.object(probe, "read_u32", side_effect=fake_read_u32):
            report = measure_cycles(probe, duration_ms=10.0)

        self.assertTrue(report.wrapped)
        self.assertTrue(report.cyccnt_advancing)
        self.assertEqual(report.start_cycles, 0xFFFFFFF0)
        self.assertEqual(report.end_cycles, 0x00000400)
        self.assertEqual(report.delta_cycles, (0x00000400 - 0xFFFFFFF0) & 0xFFFFFFFF)
        self.assertEqual(report.delta_cycles, 0x410)

    def test_measure_cycles_stalled_detection(self) -> None:
        probe = _DynamicCycleMockProbe(initial_cyccnt=0x12345678, stalled=True)
        with probe:
            report = measure_cycles(probe, duration_ms=10.0, core_hz=250_000_000)

        self.assertFalse(report.cyccnt_advancing)
        self.assertEqual(report.delta_cycles, 0)
        self.assertEqual(report.estimated_core_hz, 0.0)
        self.assertIn("stalled", report.summary.lower())


class TestPcHotspots(unittest.TestCase):
    """测试 profile_pc_hotspots：真实 ELF + WFI 识别 + 唯一 PC 缓存 + 热点源码行号还原。"""

    _C_PROFILE_SRC = r"""
#include <stdint.h>

typedef struct {
    uint8_t flag;
    uint16_t mode;
    uint32_t counter;
} device_state_t;

volatile device_state_t g_state = { .flag = 1, .mode = 0xAA55, .counter = 42 };

__attribute__((noinline)) uint32_t cold_func(uint32_t x) {
    g_state.counter += x;
    return g_state.counter;
}

__attribute__((noinline)) uint32_t hot_loop(uint32_t n) {
    uint32_t acc = 0;
    for (uint32_t i = 0; i < n; i++) {
        acc += (i ^ 0x5A5A5A5Au);
    }
    return acc;
}

int main(void) {
    return (int)(hot_loop(10) + cold_func(3));
}
"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.proj_dir = Path(cls._tmp.name)
        cls.src_path = cls.proj_dir / "firmware.c"
        cls.elf_path = cls.proj_dir / "firmware.elf"
        cls.src_path.write_text(cls._C_PROFILE_SRC, encoding="utf-8")

        cls.has_gcc = bool(shutil.which("gcc"))
        cls.has_addr2line = bool(shutil.which("addr2line"))
        if cls.has_gcc:
            subprocess.run(
                ["gcc", "-g", "-O0", "-no-pie", "-fno-pie", str(cls.src_path), "-o", str(cls.elf_path)],
                check=True,
                capture_output=True,
                text=True,
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    @unittest.skipUnless(shutil.which("gcc") and shutil.which("addr2line"), "需要宿主机 gcc 与 addr2line")
    def test_profile_pc_hotspots_with_real_elf_and_unique_pc_cache(self) -> None:
        hot_sym = find_symbol(self.elf_path, "hot_loop")
        cold_sym = find_symbol(self.elf_path, "cold_func")
        self.assertIsNotNone(hot_sym)
        self.assertIsNotNone(cold_sym)
        assert hot_sym is not None and cold_sym is not None

        hot_pc1 = hot_sym.address + 4
        hot_pc2 = hot_sym.address + 8
        cold_pc = cold_sym.address + 4

        # 构造 20 次采样：12 次 hot_loop（含 2 个唯一地址）、3 次 cold_func、5 次 WFI/不可用
        seq = (
            [hot_pc1] * 7
            + [hot_pc2] * 5
            + [cold_pc] * 3
            + [0xFFFFFFFF] * 4
            + [0x00000000] * 1
        )
        probe = _SequencePcsrMockProbe(seq)

        from mcu_harness.trace import dwt as dwt_mod

        with probe, mock.patch.object(dwt_mod, "resolve_address", wraps=dwt_mod.resolve_address) as spy_resolve:
            report = profile_pc_hotspots(
                probe,
                elf_path=self.elf_path,
                project_dir=self.proj_dir,
                samples=20,
                interval_ms=0.0,
            )

        self.assertEqual(probe.halt_calls, 0)
        self.assertEqual(report.total_samples, 20)
        self.assertEqual(report.sleeping_or_unavailable_samples, 5)
        self.assertEqual(report.valid_samples, 15)
        self.assertEqual(report.unique_pcs, 3)
        # 20 次采样仅有 3 个唯一有效 PC，resolve_address 只应被调用 3 次！
        self.assertEqual(report.resolve_calls, 3)
        self.assertEqual(spy_resolve.call_count, 3)

        self.assertEqual(len(report.function_hotspots), 2)
        top_fn = report.function_hotspots[0]
        second_fn = report.function_hotspots[1]
        self.assertEqual(top_fn.symbol, "hot_loop")
        self.assertEqual(top_fn.hits, 12)
        self.assertAlmostEqual(top_fn.percent, 60.0, places=2)
        self.assertEqual(top_fn.file, "firmware.c")
        self.assertIsNotNone(top_fn.line)
        self.assertGreater(top_fn.line or 0, 15)

        self.assertEqual(second_fn.symbol, "cold_func")
        self.assertEqual(second_fn.hits, 3)
        self.assertAlmostEqual(second_fn.percent, 15.0, places=2)
        self.assertEqual(second_fn.file, "firmware.c")

        # 验证地址级热点排行
        self.assertEqual(report.address_hotspots[0].address_int, hot_pc1)
        self.assertEqual(report.address_hotspots[0].hits, 7)

    def test_profile_samples_bounds(self) -> None:
        probe = MockProbe()
        with probe:
            with self.assertRaises(TraceError):
                profile_pc_hotspots(probe, samples=0)
            with self.assertRaises(TraceError):
                profile_pc_hotspots(probe, samples=1001)


class TestWatchpoints(unittest.TestCase):
    """测试 Armv7-M / Armv8-M 硬件观察点编码、DWARF 表达式寻址、列表解码与清除。"""

    def test_armv7m_m4_watchpoint_encoding_list_matched_and_clear(self) -> None:
        probe = MockProbe(
            memory_words={
                SCB_CPUID: _CPUID_M4,
                DWT_CTRL: 4 << 28,
            }
        )
        with probe:
            wp_w = set_watchpoint(probe, "0x20001000", access="write", size_bytes=4)
            wp_r = set_watchpoint(probe, 0x20001012, access="read", size_bytes=2)
            wp_rw = set_watchpoint(probe, 0x20001021, access="rw", size_bytes=1)

            self.assertEqual(wp_w.slot, 0)
            self.assertEqual(wp_w.arch_family, "Armv7-M")
            self.assertEqual(probe.read_u32(dwt_comp_addr(0), 1)[0], 0x20001000)
            self.assertEqual(probe.read_u32(dwt_mask_addr(0), 1)[0], 2)  # 4B -> MASK=2
            self.assertEqual(probe.read_u32(dwt_function_addr(0), 1)[0], 0x6)  # write

            self.assertEqual(wp_r.slot, 1)
            self.assertEqual(probe.read_u32(dwt_mask_addr(1), 1)[0], 1)  # 2B -> MASK=1
            self.assertEqual(probe.read_u32(dwt_function_addr(1), 1)[0], 0x5)  # read

            self.assertEqual(wp_rw.slot, 2)
            self.assertEqual(probe.read_u32(dwt_mask_addr(2), 1)[0], 0)  # 1B -> MASK=0
            self.assertEqual(probe.read_u32(dwt_function_addr(2), 1)[0], 0x7)  # rw

            # 模拟槽位 1 硬件命中（MATCHED bit 24 置位）
            probe.write_u32(dwt_function_addr(1), [0x5 | (1 << DWT_FUNCTION_MATCHED_BIT)])
            listed = list_watchpoints(probe)
            self.assertEqual(listed.arch_family, "Armv7-M")
            self.assertEqual(listed.num_comparators, 4)
            self.assertEqual(listed.active_count, 3)
            self.assertEqual(listed.matched_count, 1)
            self.assertTrue(listed.watchpoints[1].matched)
            self.assertEqual(listed.watchpoints[1].access, "read")
            self.assertEqual(listed.watchpoints[1].size_bytes, 2)
            self.assertFalse(listed.watchpoints[3].active)

            # 清除单个槽位与清除全部槽位
            c1 = clear_watchpoints(probe, slot=1)
            self.assertEqual(c1["cleared_slots"], [1])
            self.assertEqual(probe.read_u32(dwt_function_addr(1), 1)[0], 0)

            c_all = clear_watchpoints(probe)
            self.assertEqual(c_all["cleared_slots"], [0, 1, 2, 3])
            listed_after = list_watchpoints(probe)
            self.assertEqual(listed_after.active_count, 0)

    def test_armv8m_m33_watchpoint_encoding_and_matched(self) -> None:
        probe = MockProbe(
            memory_words={
                SCB_CPUID: _CPUID_M33,
                DWT_CTRL: 2 << 28,
            }
        )
        with probe:
            wp0 = set_watchpoint(probe, 0x20002000, access="rw", size_bytes=4)
            wp1 = set_watchpoint(probe, 0x20002006, access="write", size_bytes=2)

            # Armv8-M FUNCTIONn = (datavsize << 10) | (1 << 4) | match_code
            expected_fn0 = (2 << 10) | (1 << 4) | 4  # 4B, ACTION=1, MATCH=4(rw)
            expected_fn1 = (1 << 10) | (1 << 4) | 5  # 2B, ACTION=1, MATCH=5(write)
            self.assertEqual(wp0.arch_family, "Armv8-M")
            self.assertIsNone(wp0.mask_raw)
            self.assertEqual(probe.read_u32(dwt_comp_addr(0), 1)[0], 0x20002000)
            self.assertEqual(probe.read_u32(dwt_function_addr(0), 1)[0], expected_fn0)
            self.assertEqual(probe.read_u32(dwt_comp_addr(1), 1)[0], 0x20002006)
            self.assertEqual(probe.read_u32(dwt_function_addr(1), 1)[0], expected_fn1)

            # 槽位已满再添加应抛 TraceError
            with self.assertRaises(TraceError):
                set_watchpoint(probe, 0x20002008, access="read", size_bytes=4)

            # 注入 MATCHED bit 24
            probe.write_u32(dwt_function_addr(0), [expected_fn0 | (1 << DWT_FUNCTION_MATCHED_BIT)])
            listed = list_watchpoints(probe)
            self.assertEqual(listed.arch_family, "Armv8-M")
            self.assertEqual(listed.matched_count, 1)
            self.assertTrue(listed.watchpoints[0].matched)
            self.assertEqual(listed.watchpoints[0].access, "rw")
            self.assertEqual(listed.watchpoints[0].size_bytes, 4)
            self.assertEqual(listed.watchpoints[1].access, "write")
            self.assertEqual(listed.watchpoints[1].size_bytes, 2)

    @unittest.skipUnless(shutil.which("gcc"), "需要宿主机 gcc")
    def test_watchpoint_by_dwarf_struct_member_expression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "vars.c"
            elf = Path(tmp) / "vars.elf"
            src.write_text(TestPcHotspots._C_PROFILE_SRC, encoding="utf-8")
            subprocess.run(
                ["gcc", "-g", "-O0", "-no-pie", "-fno-pie", str(src), "-o", str(elf)],
                check=True,
                capture_output=True,
                text=True,
            )

            g_sym = find_symbol(elf, "g_state")
            self.assertIsNotNone(g_sym)
            assert g_sym is not None

            probe = MockProbe(memory_words={SCB_CPUID: _CPUID_M33, DWT_CTRL: 4 << 28})
            with probe:
                wp_counter = set_watchpoint(probe, "g_state.counter", access="write", elf_path=elf)
                wp_flag = set_watchpoint(probe, "g_state.flag", access="rw", elf_path=elf)

            self.assertEqual(wp_counter.target_expr, "g_state.counter")
            self.assertEqual(wp_counter.size_bytes, 4)
            self.assertEqual(wp_counter.address, g_sym.address + 4)  # flag(1)+pad(1)+mode(2)+counter(4)

            self.assertEqual(wp_flag.target_expr, "g_state.flag")
            self.assertEqual(wp_flag.size_bytes, 1)
            self.assertEqual(wp_flag.address, g_sym.address)

    def test_armv6m_m0plus_watchpoint_and_audit_log(self) -> None:
        probe = MockProbe(memory_words={SCB_CPUID: _CPUID_M0PLUS, DWT_CTRL: 2 << 28})
        before_audit = len(default_safety_guard.recent_audit(500))
        with probe:
            wp = set_watchpoint(probe, 0x20000400, access="write", size_bytes=4, slot=1)
        self.assertEqual(wp.arch_family, "Armv6-M")
        self.assertEqual(wp.slot, 1)
        with probe:
            self.assertEqual(probe.read_u32(dwt_comp_addr(1), 1)[0], 0x20000400)
            self.assertEqual(probe.read_u32(dwt_mask_addr(1), 1)[0], 2)
            self.assertEqual(probe.read_u32(dwt_function_addr(1), 1)[0], 0x6)
        self.assertGreater(len(default_safety_guard.recent_audit(500)), before_audit)

    def test_watchpoint_validation_errors(self) -> None:
        probe_zero = MockProbe(memory_words={DWT_CTRL: 0})
        with probe_zero:
            with self.assertRaises(TraceError):
                set_watchpoint(probe_zero, 0x20000000)

        probe = MockProbe(memory_words={SCB_CPUID: _CPUID_M0PLUS, DWT_CTRL: 2 << 28})
        with probe:
            with self.assertRaises(TraceError):
                set_watchpoint(probe, 0x20000002, size_bytes=4)  # 非 4 字节对齐
            with self.assertRaises(TraceError):
                set_watchpoint(probe, 0x20000000, access="exec")  # 非法 access
            with self.assertRaises(TraceError):
                set_watchpoint(probe, 0x20000000, size_bytes=8)  # 非法 size_bytes
            with self.assertRaises(TraceError):
                set_watchpoint(probe, 0x20000000, slot=5)  # 越界 slot


class TestTraceCli(unittest.TestCase):
    """测试 `mcu-trace --mock` 全部子命令。"""

    def _run_cli(self, argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = trace_cli_main(argv)
        return rc, buf.getvalue()

    def test_cli_mock_subcommands(self) -> None:
        rc, out = self._run_cli(["cycles", "--mock", "--duration-ms", "15", "--core-hz", "250000000", "--json"])
        self.assertEqual(rc, 0)
        cyc_json = json.loads(out)
        self.assertTrue(cyc_json["cyccnt_advancing"])
        self.assertEqual(cyc_json["expected_core_hz"], 250000000)

        rc, out = self._run_cli(["profile", "--mock", "-n", "12", "--interval-ms", "0", "--json"])
        self.assertEqual(rc, 0)
        prof_json = json.loads(out)
        self.assertEqual(prof_json["total_samples"], 12)
        self.assertEqual(prof_json["valid_samples"], 9)
        self.assertEqual(prof_json["sleeping_or_unavailable_samples"], 3)

        rc, out = self._run_cli(["watch-set", "0x20000100", "--access", "write", "--size", "4", "--mock", "--json"])
        self.assertEqual(rc, 0)
        wset_json = json.loads(out)
        self.assertEqual(wset_json["address_hex"], "0x20000100")
        self.assertEqual(wset_json["slot"], 0)

        rc, out = self._run_cli(["watch-list", "--mock", "--json"])
        self.assertEqual(rc, 0)
        wlist_json = json.loads(out)
        self.assertEqual(wlist_json["num_comparators"], 4)

        rc, out = self._run_cli(["watch-clear", "--mock", "--json"])
        self.assertEqual(rc, 0)
        wclear_json = json.loads(out)
        self.assertEqual(wclear_json["cleared_slots"], [0, 1, 2, 3])

        # 测试前置 `--mock` 与文本模式输出
        rc, txt = self._run_cli(["--mock", "cycles", "--duration-ms", "10"])
        self.assertEqual(rc, 0)
        self.assertIn("DWT Cycle Counter Measurement", txt)

        rc, txt = self._run_cli(["--mock", "watch-list"])
        self.assertEqual(rc, 0)
        self.assertIn("NUMCOMP=4", txt)


if __name__ == "__main__":
    unittest.main()
