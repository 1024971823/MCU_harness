"""Phase 5C 逻辑分析仪数字波形/sigrok 桥接与声明式 HIL 自动化测试引擎单元测试。

同时兼容：
    * `PYTHONPATH=src python3 tests/test_hil.py -v`
    * `PYTHONPATH=src python3 -m unittest discover -s tests -p "test_hil.py" -v`
    * `pytest`
"""

from __future__ import annotations

import io
import json
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock as umock

from mcu_harness.dwarf import (
    extract_elf_initialized_memory,
    load_dwarf_index,
    read_variable,
    resolve_watch_target,
)
from mcu_harness.hil import (
    HilConfigError,
    HilError,
    LogicCaptureReport,
    LogicCsvReport,
    analyze_digital_csv,
    decode_with_sigrok,
    load_hil_spec,
    run_hil_suite,
)
from mcu_harness.hil.cli import main as hil_cli_main
from mcu_harness.log import RttLogCollector, UartLogCollector
from mcu_harness.probe import MockProbe, ProbeManager

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MINI_SVD = _REPO_ROOT / "tests" / "fixtures" / "stm32h563_mini.svd"

_HIL_C_SRC = r"""
#include <stdint.h>

typedef enum {
    HIL_STATE_IDLE = 0,
    HIL_STATE_RUNNING = 1,
    HIL_STATE_FAULT = 2
} hil_mode_t;

typedef struct {
    uint32_t magic;
    uint32_t rpm;
    uint32_t heater_pwm;
    uint32_t safe_off_executed;
    hil_mode_t mode;
} hil_state_t;

volatile hil_state_t g_hil_state = {
    .magic = 0xCAFE5A5Au,
    .rpm = 0u,
    .heater_pwm = 80u,
    .safe_off_executed = 0u,
    .mode = HIL_STATE_IDLE,
};

int main(void) {
    return (int)(g_hil_state.magic ^ g_hil_state.rpm);
}
"""


def _load_elf_into_mock(probe: MockProbe, elf_path: Path) -> None:
    for seg_addr, seg_bytes in extract_elf_initialized_memory(elf_path).items():
        for off, b in enumerate(seg_bytes):
            probe._mem[seg_addr + off] = b


def _generate_pwm_csv(
    *,
    freq_hz: float = 10_000.0,
    duty_pct: float = 25.0,
    cycles: int = 10,
    samples_per_cycle: int = 20,
    time_unit_s: float = 1.0,
) -> str:
    """生成精确占空比与频率的方波 + 常高/常低通道 CSV（含注释与空行）。"""
    period_s = 1.0 / freq_hz
    dt_s = period_s / samples_per_cycle
    high_samples = round(samples_per_cycle * (duty_pct / 100.0))

    lines = [
        "# Digital logic analyzer capture",
        "# Channel mapping: pwm=TIM1_CH1, always_hi=EN, always_lo=FAULT",
        "",
        "time,pwm,always_hi,always_lo",
    ]
    total_steps = cycles * samples_per_cycle
    # 包含结尾边界采样点（总跨度恰好 cycles * period_s），每个周期均为 0 起跳 -> 精确上升/下降沿计数
    for idx in range(total_steps + 1):
        t_val = (idx * dt_s) / time_unit_s
        phase = idx % samples_per_cycle
        # 相位 1..high_samples 为高，确保 t=0 从 0 开始产生精确 cycles 个上升沿与下降沿
        if idx == total_steps:
            pwm_bit = 0
        else:
            pwm_bit = 1 if (1 <= phase <= high_samples) else 0
        lines.append(f"{t_val:.12g},{pwm_bit},1,0")
        if idx == 5:
            lines.append("")  # 中间穿插空行验证解析健壮性
    return "\n".join(lines) + "\n"


class TestLogicAnalyzer(unittest.TestCase):
    """纯 Python 数字波形 CSV 分析与 sigrok-cli 协议解码桥接测试。"""

    def test_analyze_digital_csv_10khz_25pct_and_idle_channels(self) -> None:
        csv_text = _generate_pwm_csv(freq_hz=10_000.0, duty_pct=25.0, cycles=10, samples_per_cycle=20)
        report = analyze_digital_csv(csv_text)
        self.assertIsInstance(report, LogicCsvReport)
        self.assertEqual(report.sample_count, 201)
        self.assertAlmostEqual(report.duration_s, 0.001, places=9)

        pwm = report.channels["pwm"]
        self.assertEqual(pwm.samples, 201)
        self.assertEqual(pwm.rising_edges, 10)
        self.assertEqual(pwm.falling_edges, 10)
        self.assertAlmostEqual(pwm.frequency_hz or 0.0, 10_000.0, places=3)
        self.assertAlmostEqual(pwm.duty_cycle_pct, 25.0, places=4)
        self.assertAlmostEqual(pwm.high_time_s, 0.00025, places=9)
        self.assertAlmostEqual(pwm.low_time_s, 0.00075, places=9)
        self.assertEqual(pwm.idle_state, "TOGGLING")

        hi = report.channels["always_hi"]
        self.assertEqual(hi.rising_edges, 0)
        self.assertEqual(hi.falling_edges, 0)
        self.assertEqual(hi.frequency_hz, 0.0)
        self.assertAlmostEqual(hi.duty_cycle_pct, 100.0, places=4)
        self.assertEqual(hi.idle_state, "HIGH")

        lo = report.channels["always_lo"]
        self.assertEqual(lo.rising_edges, 0)
        self.assertEqual(lo.falling_edges, 0)
        self.assertEqual(lo.frequency_hz, 0.0)
        self.assertAlmostEqual(lo.duty_cycle_pct, 0.0, places=4)
        self.assertEqual(lo.idle_state, "LOW")

        d = report.to_dict()
        self.assertEqual(d["channels"]["pwm"]["idle_state"], "TOGGLING")
        txt = report.to_text()
        self.assertIn("10000", txt)
        self.assertIn("25.00%", txt)

    def test_csv_file_microseconds_and_cli_logic(self) -> None:
        csv_us = _generate_pwm_csv(freq_hz=10_000.0, duty_pct=25.0, cycles=5, samples_per_cycle=20, time_unit_s=1e-6)
        with tempfile.TemporaryDirectory() as td:
            csv_path = Path(td) / "pwm_us.csv"
            csv_path.write_text(csv_us, encoding="utf-8")

            # 通过 decode_with_sigrok 直通 CSV（无需 sigrok-cli）
            cap = decode_with_sigrok(csv_path=csv_path, time_unit_s=1e-6)
            self.assertIsInstance(cap, LogicCaptureReport)
            self.assertEqual(cap.mode, "csv")
            assert cap.csv_report is not None
            self.assertAlmostEqual(cap.csv_report.channels["pwm"].frequency_hz or 0.0, 10_000.0, places=3)
            self.assertAlmostEqual(cap.csv_report.channels["pwm"].duty_cycle_pct, 25.0, places=4)

            # 通过 input_file=.csv 也应直通纯 Python 分析
            cap2 = decode_with_sigrok(input_file=csv_path, time_unit_s=1e-6)
            self.assertEqual(cap2.mode, "csv")

            # CLI: mcu-hil logic --csv <file> --time-unit-s 1e-6 --json
            out_buf = io.StringIO()
            with redirect_stdout(out_buf):
                rc = hil_cli_main(["logic", "--csv", str(csv_path), "--time-unit-s", "1e-6", "--json"])
            self.assertEqual(rc, 0)
            payload = json.loads(out_buf.getvalue())
            self.assertEqual(payload["mode"], "csv")
            self.assertAlmostEqual(payload["csv_report"]["channels"]["pwm"]["frequency_hz"], 10_000.0, places=3)
            self.assertAlmostEqual(payload["csv_report"]["channels"]["pwm"]["duty_cycle_pct"], 25.0, places=4)

    def test_decode_with_sigrok_spi_and_i2c_mocked_subprocess(self) -> None:
        spi_stdout = (
            "spi-1: mosi-data: 0x9F\n"
            "spi-1: miso-data: 0xEF\n"
            "spi-1: mosi-transfer: 9F 00 00\n"
        )
        i2c_stdout = (
            "i2c-1: start: Start\n"
            "i2c-1: address-write: 0x48\n"
            "i2c-1: ack: ACK\n"
            "i2c-1: data-write: 0x01\n"
            "i2c-1: stop: Stop\n"
        )

        with tempfile.TemporaryDirectory() as td:
            sr_file = Path(td) / "bus_capture.sr"
            sr_file.write_bytes(b"MOCK_SR")

            with (
                umock.patch("shutil.which", return_value="/usr/bin/sigrok-cli"),
                umock.patch("subprocess.run") as mock_run,
            ):
                # 1) Live driver + SPI decode
                mock_run.return_value = subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=spi_stdout, stderr=""
                )
                spi_rep = decode_with_sigrok(
                    driver="fx2lafw",
                    channels=["D0", "D1", "D2"],
                    samplerate="8m",
                    samples=4096,
                    duration_ms=50,
                    protocol="spi",
                    protocol_options={"clk": "D0", "mosi": "D1", "miso": "D2"},
                )
                called_cmd = mock_run.call_args.args[0]
                self.assertEqual(called_cmd[0], "/usr/bin/sigrok-cli")
                self.assertIn("-d", called_cmd)
                self.assertIn("fx2lafw", called_cmd)
                self.assertIn("--config", called_cmd)
                self.assertIn("samplerate=8m", called_cmd)
                self.assertIn("--samples", called_cmd)
                self.assertIn("4096", called_cmd)
                self.assertIn("--time", called_cmd)
                self.assertIn("50", called_cmd)
                self.assertIn("--channels", called_cmd)
                self.assertIn("D0,D1,D2", called_cmd)
                self.assertIn("-P", called_cmd)
                self.assertIn("spi:clk=D0:mosi=D1:miso=D2", called_cmd)

                self.assertEqual(spi_rep.mode, "sigrok")
                self.assertEqual(len(spi_rep.annotations), 3)
                self.assertEqual(spi_rep.annotations[0].decoder, "spi-1")
                self.assertEqual(spi_rep.annotations[0].channel, "mosi-data")
                self.assertEqual(spi_rep.annotations[0].annotation, "0x9F")

                # 2) Input .sr file + I2C decode
                mock_run.return_value = subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=i2c_stdout, stderr=""
                )
                i2c_rep = decode_with_sigrok(
                    input_file=sr_file,
                    channels="D0,D1",
                    protocol="i2c",
                    protocol_options="scl=D0:sda=D1",
                )
                called_cmd2 = mock_run.call_args.args[0]
                self.assertEqual(called_cmd2[:3], ["/usr/bin/sigrok-cli", "-i", str(sr_file)])
                self.assertIn("-P", called_cmd2)
                self.assertIn("i2c:scl=D0:sda=D1", called_cmd2)
                self.assertEqual(len(i2c_rep.annotations), 5)
                self.assertEqual(i2c_rep.annotations[1].decoder, "i2c-1")
                self.assertEqual(i2c_rep.annotations[1].channel, "address-write")
                self.assertEqual(i2c_rep.annotations[1].annotation, "0x48")

            # 3) sigrok-cli 缺失时给出清晰安装提示
            with umock.patch("shutil.which", return_value=None):
                with self.assertRaises(HilError) as ctx:
                    decode_with_sigrok(input_file=sr_file, protocol="i2c")
                self.assertIn("sigrok-cli", str(ctx.exception))


@unittest.skipUnless(shutil.which("gcc"), "Requires host /usr/bin/gcc")
class TestHilRunnerE2E(unittest.TestCase):
    """端到端声明式 HIL 测试套件（真实 .toml + MockProbe + gcc -g ELF + SVD + RttLogCollector）。"""

    tmpdir: tempfile.TemporaryDirectory[str]
    elf_path: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = tempfile.TemporaryDirectory(prefix="mcu_hil_e2e_")
        root = Path(cls.tmpdir.name)
        c_file = root / "hil_fw.c"
        c_file.write_text(_HIL_C_SRC, encoding="utf-8")
        cls.elf_path = root / "hil_fw.elf"
        subprocess.run(
            ["gcc", "-g", "-gdwarf-4", "-O0", str(c_file), "-o", str(cls.elf_path)],
            check=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmpdir.cleanup()

    def _make_probe_and_rtt(self) -> tuple[MockProbe, ProbeManager, RttLogCollector]:
        mock = MockProbe(device="STM32H563ZI", interface="SWD")
        mock.connect()
        _load_elf_into_mock(mock, self.elf_path)
        pm = ProbeManager()
        pm.set_probe(mock)
        rtt = RttLogCollector(pm, poll_interval_s=0.01)
        return mock, pm, rtt

    def test_case1_full_green_pass_with_async_poll_svd_rtt_and_crash(self) -> None:
        """用例 1（全绿 PASS）：rtt_write -> 后台异步更新变量与 RTT -> assert_variable 轮询 -> assert_register -> assert_log -> assert_no_crash -> teardown。"""
        mock, pm, rtt = self._make_probe_and_rtt()
        idx = load_dwarf_index(self.elf_path)
        rpm_target = resolve_watch_target(idx, "g_hil_state.rpm")
        mode_target = resolve_watch_target(idx, "g_hil_state.mode")
        pwm_target = resolve_watch_target(idx, "g_hil_state.heater_pwm")

        stop_sim = threading.Event()

        def _firmware_sim() -> None:
            while not stop_sim.is_set():
                if mock._rtt_started:
                    down = mock.pop_rtt_down(channel=0)
                    if b"start" in down:
                        time.sleep(0.04)
                        mock.write_memory(rpm_target.address, struct.pack("<I", 1500))
                        mock.write_memory(mode_target.address, struct.pack("<I", 1))
                        mock.push_rtt("[INFO] motor started\n", channel=0)
                        return
                time.sleep(0.01)

        sim_thread = threading.Thread(target=_firmware_sim, daemon=True)
        sim_thread.start()

        try:
            with tempfile.TemporaryDirectory() as td:
                suite_toml = Path(td) / "motor_pass.toml"
                suite_toml.write_text(
                    f'''
name = "Motor HIL Pass Suite"

[[tests]]
name = "Start motor and verify telemetry"

[[tests.steps]]
op = "rtt_write"
command = "start\\n"

[[tests.steps]]
op = "assert_variable"
expr = "g_hil_state.rpm"
ge = 1200
le = 1800
timeout_ms = 500
poll_ms = 10

[[tests.steps]]
op = "assert_variable"
expr = "g_hil_state.mode"
enum = "HIL_STATE_RUNNING"

[[tests.steps]]
op = "assert_register"
peripheral = "GPIOA"
register = "ODR"
field = "OD5"
eq = 1

[[tests.steps]]
op = "assert_memory"
address = "0x42020014"
mask = "0x20"
eq = "0x20"

[[tests.steps]]
op = "assert_log"
source = "rtt"
contains = "[INFO] motor started"
forbid = "ERROR"
timeout_ms = 500

[[tests.steps]]
op = "assert_no_crash"

[[tests.teardown]]
op = "write_memory"
address = "0x{pwm_target.address:08X}"
values = [0]
''',
                    encoding="utf-8",
                )

                report = run_hil_suite(
                    suite_toml,
                    probe=mock,
                    elf_path=self.elf_path,
                    svd_path=_MINI_SVD,
                    rtt_collector=rtt,
                )

                self.assertTrue(report.passed, msg=report.to_text())
                self.assertEqual(report.total_tests, 1)
                self.assertEqual(report.passed_tests, 1)
                self.assertEqual(report.failed_tests, 0)
                self.assertTrue(report.tests[0].passed)
                self.assertGreaterEqual(report.tests[0].steps[1].details["attempts"], 1)
                self.assertEqual(len(report.tests[0].teardown), 1)
                self.assertTrue(report.tests[0].teardown[0].passed)
                # 确认 teardown 已将 heater_pwm 清零
                self.assertEqual(read_variable(mock, self.elf_path, "g_hil_state.heater_pwm").value, 0)
        finally:
            stop_sim.set()
            sim_thread.join(timeout=1.0)
            rtt.stop()
            mock.close()

    def test_case2_assertion_failure_still_executes_safe_off_teardown(self) -> None:
        """用例 2（断言失败仍强制执行 teardown Safe-Off）：assert_variable 故意失败，验证仍置位 safe_off_executed=1。"""
        mock, _, rtt = self._make_probe_and_rtt()
        idx = load_dwarf_index(self.elf_path)
        safe_off_target = resolve_watch_target(idx, "g_hil_state.safe_off_executed")
        pwm_target = resolve_watch_target(idx, "g_hil_state.heater_pwm")

        try:
            with tempfile.TemporaryDirectory() as td:
                suite_toml = Path(td) / "safe_off_on_fail.toml"
                suite_toml.write_text(
                    f'''
name = "Safe-Off Guarantee Suite"

[[tests]]
name = "Intentional failure must trigger safe-off teardown"

[[tests.steps]]
op = "assert_variable"
expr = "g_hil_state.rpm"
eq = 99999
timeout_ms = 30
poll_ms = 10

[[tests.steps]]
op = "assert_no_crash"

[[tests.teardown]]
op = "write_memory"
address = "0x{safe_off_target.address:08X}"
values = [1]

[[teardown]]
op = "write_memory"
address = "0x{pwm_target.address:08X}"
values = [0]
''',
                    encoding="utf-8",
                )

                report = run_hil_suite(
                    suite_toml,
                    probe=mock,
                    elf_path=self.elf_path,
                    svd_path=_MINI_SVD,
                    rtt_collector=rtt,
                )

                self.assertFalse(report.passed)
                self.assertEqual(report.passed_tests, 0)
                self.assertEqual(report.failed_tests, 1)

                tc = report.tests[0]
                self.assertFalse(tc.passed)
                # 仅执行到失败步，后续步骤跳过
                self.assertEqual(len(tc.steps), 1)
                self.assertFalse(tc.steps[0].passed)

                # 但用例级 teardown 与套件级 teardown 均无条件执行且 PASS
                self.assertEqual(len(tc.teardown), 1)
                self.assertTrue(tc.teardown[0].passed)
                self.assertEqual(len(report.teardown), 1)
                self.assertTrue(report.teardown[0].passed)

                self.assertEqual(read_variable(mock, self.elf_path, "g_hil_state.safe_off_executed").value, 1)
                self.assertEqual(read_variable(mock, self.elf_path, "g_hil_state.heater_pwm").value, 0)
        finally:
            rtt.stop()
            mock.close()

    def test_case3_safety_guard_blocks_option_bytes_even_when_confirmed(self) -> None:
        """用例 3（安全护栏不可绕过）：写 STM32H5 Option Bytes (0x08FFF000) 即使 confirmed=True 仍被拦截失败。"""
        mock, _, _ = self._make_probe_and_rtt()
        idx = load_dwarf_index(self.elf_path)
        safe_off_target = resolve_watch_target(idx, "g_hil_state.safe_off_executed")
        sentinel = 0x11223344
        mock.set_word(0x08FFF000, sentinel)

        try:
            with tempfile.TemporaryDirectory() as td:
                suite_toml = Path(td) / "option_bytes_deny.toml"
                suite_toml.write_text(
                    f'''
name = "SafetyGuard Enforcement Suite"

[[tests]]
name = "Attempt Option Bytes write must be hard-denied"

[[tests.steps]]
op = "write_memory"
address = "0x08FFF000"
values = [0xDEADBEEF]
width = 32
peripheral_hint = "FLASH"

[[tests.teardown]]
op = "write_memory"
address = "0x{safe_off_target.address:08X}"
values = [1]
''',
                    encoding="utf-8",
                )

                # 即使传入 confirmed=True，Option Bytes 仍必须 HARD DENY！
                report = run_hil_suite(
                    suite_toml,
                    probe=mock,
                    elf_path=self.elf_path,
                    confirmed=True,
                )

                self.assertFalse(report.passed)
                tc = report.tests[0]
                self.assertFalse(tc.passed)
                step = tc.steps[0]
                self.assertFalse(step.passed)
                self.assertIn("HARD DENY", step.message)
                self.assertEqual(step.details["safety_decision"]["level"], "hard_deny")
                # 确认模拟内存中 0x08FFF000 未被修改
                self.assertEqual(mock.read_u32(0x08FFF000, 1)[0], sentinel)
                # 确认安全护栏拦截后仍执行 teardown
                self.assertTrue(tc.teardown[0].passed)
                self.assertEqual(read_variable(mock, self.elf_path, "g_hil_state.safe_off_executed").value, 1)

                # 额外验证：写普通外设寄存器未传 confirmed=True 会被拦截，传 confirmed=True 放行
                periph_spec = {
                    "name": "Peripheral HITL Gate",
                    "tests": [
                        {
                            "name": "Write GPIOA ODR",
                            "steps": [
                                {"op": "write_memory", "address": "0x42020014", "values": [0x00000021]}
                            ],
                        }
                    ],
                }
                rep_unconfirmed = run_hil_suite(periph_spec, probe=mock, confirmed=False)
                self.assertFalse(rep_unconfirmed.passed)
                self.assertIn("HITL confirmation required", rep_unconfirmed.tests[0].steps[0].message)

                rep_confirmed = run_hil_suite(periph_spec, probe=mock, confirmed=True)
                self.assertTrue(rep_confirmed.passed)
                self.assertEqual(mock.read_u32(0x42020014, 1)[0], 0x00000021)
        finally:
            mock.close()

    def test_json_spec_crash_detection_uart_and_cli_run(self) -> None:
        """验证 JSON 套件、assert_no_crash 检出故障、UART 日志链路、YAML 缺失提示及 mcu-hil run CLI。"""
        with tempfile.TemporaryDirectory() as td:
            # 1) JSON suite + mcu-hil run CLI
            json_suite = Path(td) / "cli_suite.json"
            json_suite.write_text(
                json.dumps(
                    {
                        "name": "CLI JSON HIL Suite",
                        "setup": [{"op": "reset", "halt": False}],
                        "tests": [
                            {
                                "name": "Verify GPIOA ODR & CPU health",
                                "steps": [
                                    {"op": "sleep_ms", "ms": 5},
                                    {"op": "assert_register", "peripheral": "GPIOA", "register": "ODR", "field": "OD5", "eq": 1},
                                    {"op": "assert_no_crash"},
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            out_buf = io.StringIO()
            with redirect_stdout(out_buf):
                rc = hil_cli_main(
                    ["run", str(json_suite), "--elf", str(self.elf_path), "--svd", str(_MINI_SVD), "--mock", "--json"]
                )
            self.assertEqual(rc, 0)
            payload = json.loads(out_buf.getvalue())
            self.assertTrue(payload["passed"])
            self.assertEqual(payload["passed_tests"], 1)

            # 2) YAML 缺失 PyYAML 报错提示
            yaml_suite = Path(td) / "suite.yaml"
            yaml_suite.write_text("name: yaml\ntests: []\n", encoding="utf-8")
            with umock.patch.dict("sys.modules", {"yaml": None}):
                with self.assertRaises(HilConfigError) as y_ctx:
                    load_hil_spec(yaml_suite)
                self.assertIn(".toml", str(y_ctx.exception))

        # 3) assert_no_crash 捕获死机注入
        with MockProbe() as fault_probe:
            fault_probe.inject_cortex_m_fault()
            rep = run_hil_suite(
                {"name": "Crash Check", "tests": [{"name": "No crash", "steps": [{"op": "assert_no_crash"}]}]},
                probe=fault_probe,
            )
            self.assertFalse(rep.passed)
            self.assertIn("HardFault", rep.tests[0].steps[0].message)

        # 4) UART write + assert_log(source="uart") 通过 PTY 验证
        master_fd, slave_fd = os.openpty()
        slave_name = os.ttyname(slave_fd)
        uart = UartLogCollector(port=slave_name, baudrate=115200, backend="posix", poll_interval_s=0.01)

        def _uart_echo_device() -> None:
            buf = b""
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    chunk = os.read(master_fd, 256)
                except OSError:
                    break
                if chunk:
                    buf += chunk
                    if b"ping" in buf:
                        os.write(master_fd, b"[UART] pong ok\n")
                        break
                time.sleep(0.01)

        t_uart = threading.Thread(target=_uart_echo_device, daemon=True)
        t_uart.start()
        try:
            with MockProbe() as p_uart:
                uart.start()
                rep_uart = run_hil_suite(
                    {
                        "name": "UART HIL",
                        "tests": [
                            {
                                "name": "UART ping-pong",
                                "steps": [
                                    {"op": "uart_write", "command": "ping\n"},
                                    {"op": "assert_log", "source": "uart", "regex": r"pong\s+ok", "timeout_ms": 800},
                                ],
                            }
                        ],
                    },
                    probe=p_uart,
                    uart_collector=uart,
                )
                self.assertTrue(rep_uart.passed, msg=rep_uart.to_text())
        finally:
            uart.stop()
            t_uart.join(timeout=1.0)
            os.close(master_fd)
            os.close(slave_fd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
