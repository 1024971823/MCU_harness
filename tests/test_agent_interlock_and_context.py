"""Agent 安全互锁引擎（HarnessInterlock）、硬件状态卡片（Status Card）与斜杠工作流单元测试。

同时兼容 `pytest` 与 `PYTHONPATH=src python3 tests/test_agent_interlock_and_context.py -v` 直接运行。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mcu_harness.agent.context import (
    META_COMMANDS,
    PLAYBOOKS,
    PlaybookSpec,
    SlashResolution,
    build_hardware_status_card,
    build_system_prompt,
    expand_slash_command,
    format_slash_help,
)
from mcu_harness.agent.interlock import (
    ApprovalDecision,
    ApprovalRequest,
    HarnessInterlock,
    InterlockOutcome,
    is_safe_readonly_shell_command,
)
from mcu_harness.config.manifest import (
    ArchConfig,
    BuildConfig,
    PathsConfig,
    ProjectProfile,
    SafetyConfig,
    TargetConfig,
    discover_project,
)
from mcu_harness.log.rtt_collector import RttLogCollector
from mcu_harness.log.uart_collector import UartLogCollector
from mcu_harness.probe.manager import ProbeManager
from mcu_harness.probe.mock import MockProbe
from mcu_harness.safety.guard import SafetyGuard

_FIXTURE_SVD = Path(__file__).resolve().parent / "fixtures" / "stm32h563_mini.svd"


def _make_profile(project_dir: Path, *, svd_path: str | None = None, elf_path: str | None = None) -> ProjectProfile:
    return ProjectProfile(
        project_dir=str(project_dir),
        manifest_path=None,
        target=TargetConfig(device="STM32H563ZI", core="Cortex-M33", probe_backend="mock"),
        build=BuildConfig(system="cmake"),
        paths=PathsConfig(svd_path=svd_path, elf_path=elf_path),
        safety=SafetyConfig(),
        arch=ArchConfig(),
    )


class TestHarnessInterlock(unittest.TestCase):
    """验证不可绕过的硬件防砖门、SVD 预览、Diff 预览与模式策略。"""

    def test_stm32h5_option_bytes_hard_deny_never_calls_approver(self) -> None:
        calls: list[ApprovalRequest] = []

        def always_approve(req: ApprovalRequest) -> ApprovalDecision:
            calls.append(req)
            return ApprovalDecision(approved=True, scope="session_tool", reason="yes to everything")

        with tempfile.TemporaryDirectory() as tmp:
            prof = _make_profile(Path(tmp), svd_path=str(_FIXTURE_SVD))
            for mode in ("interactive", "auto_safe", "read_only"):
                guard = SafetyGuard()
                interlock = HarnessInterlock(mode=mode, safety_guard=guard)
                for addr in (0x08FFF000, 0x40022050, 0x50022050):
                    with self.subTest(mode=mode, addr=hex(addr)):
                        outcome = interlock.authorize(
                            "write_memory",
                            "hardware_danger",
                            {"address": hex(addr), "values": ["0x12345678"], "width": 32},
                            project_dir=tmp,
                            profile=prof,
                            approver=always_approve,
                        )
                        self.assertIsInstance(outcome, InterlockOutcome)
                        self.assertFalse(outcome.allowed)
                        self.assertTrue(outcome.hard_denied)
                        self.assertIsNone(outcome.request)
                        self.assertIn("HARD DENY", outcome.reason)
                        self.assertEqual(outcome.to_dict()["hard_denied"], True)

        self.assertEqual(len(calls), 0, "Hard deny must block before ever invoking approver")

    def test_write_memory_gpioa_odr_svd_preview_and_no_session_cache_for_danger(self) -> None:
        calls: list[ApprovalRequest] = []

        def approve_session(req: ApprovalRequest) -> ApprovalDecision:
            calls.append(req)
            return ApprovalDecision(approved=True, scope="session_tool", reason="approved in test")

        with tempfile.TemporaryDirectory() as tmp:
            guard = SafetyGuard()
            interlock = HarnessInterlock(mode="interactive", safety_guard=guard)
            prof = _make_profile(Path(tmp), svd_path=str(_FIXTURE_SVD))

            args = {
                "address": "0x42020014",
                "values": [0x20],
                "width": 32,
                "peripheral_hint": "GPIOA",
            }

            first = interlock.authorize(
                "write_memory",
                "hardware_danger",
                args,
                project_dir=tmp,
                profile=prof,
                approver=approve_session,
            )
            self.assertTrue(first.allowed)
            self.assertFalse(first.hard_denied)
            self.assertIsNotNone(first.request)
            assert first.request is not None
            self.assertIsNotNone(first.request.svd_preview)
            assert first.request.svd_preview is not None
            self.assertIn("GPIOA->ODR", first.request.svd_preview)
            self.assertIn("OD5", first.request.svd_preview)
            self.assertIn("0x00000020", first.request.svd_preview)
            self.assertEqual(first.request.details.get("matched_register"), "GPIOA->ODR")

            # 第二次调用 write_memory 即使上次批准了 scope="session_tool"，也绝不允许跳过确认
            second = interlock.authorize(
                "write_memory",
                "hardware_danger",
                args,
                project_dir=tmp,
                profile=prof,
                approver=approve_session,
            )
            self.assertTrue(second.allowed)
            self.assertEqual(len(calls), 2)
            self.assertNotIn("write_memory", interlock.session_approved_tools())

            # 同理 flash_firmware 与 run_hil_tests 也绝不允许按 session_tool 跳过
            fw = Path(tmp) / "build" / "app.elf"
            fw.parent.mkdir(parents=True, exist_ok=True)
            fw.write_bytes(b"\x7fELF" + b"\x00" * 60)
            prof_fw = _make_profile(Path(tmp), svd_path=str(_FIXTURE_SVD), elf_path=str(fw))

            f1 = interlock.authorize(
                "flash_firmware",
                "hardware_danger",
                {},
                project_dir=tmp,
                profile=prof_fw,
                approver=approve_session,
            )
            f2 = interlock.authorize(
                "flash_firmware",
                "hardware_danger",
                {},
                project_dir=tmp,
                profile=prof_fw,
                approver=approve_session,
            )
            self.assertTrue(f1.allowed)
            self.assertTrue(f2.allowed)
            assert f1.request is not None
            self.assertEqual(f1.request.details["firmware_path"], str(fw.resolve()))
            self.assertEqual(f1.request.details["file_size_bytes"], 64)
            self.assertIsNotNone(f1.request.details["mtime_iso"])
            self.assertEqual(f1.request.details["device"], "STM32H563ZI")

            h1 = interlock.authorize(
                "run_hil_tests",
                "hardware_danger",
                {"spec_path": "tests/hil.toml"},
                project_dir=tmp,
                profile=prof_fw,
                approver=approve_session,
            )
            h2 = interlock.authorize(
                "run_hil_tests",
                "hardware_danger",
                {"spec_path": "tests/hil.toml"},
                project_dir=tmp,
                profile=prof_fw,
                approver=approve_session,
            )
            self.assertTrue(h1.allowed)
            self.assertTrue(h2.allowed)
            self.assertEqual(len(calls), 6)

    def test_edit_file_diff_preview_and_auto_safe_vs_read_only_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src_file = root / "src" / "main.c"
            src_file.parent.mkdir(parents=True, exist_ok=True)
            src_file.write_text("int led_state = 0;\nvoid app_tick(void) {}\n", encoding="utf-8")

            seen_requests: list[ApprovalRequest] = []

            def interactive_approver(req: ApprovalRequest) -> ApprovalDecision:
                seen_requests.append(req)
                if req.tool_name == "shell":
                    return ApprovalDecision(approved=False, scope="deny", reason="dangerous shell denied")
                return ApprovalDecision(approved=True, scope="once")

            # 1. interactive 模式：edit_file 生成 unified diff，write_file 生成行预览
            guard_i = SafetyGuard()
            inter_i = HarnessInterlock(mode="interactive", safety_guard=guard_i)
            out_edit = inter_i.authorize(
                "edit_file",
                "workspace_write",
                {
                    "path": "src/main.c",
                    "old_text": "int led_state = 0;",
                    "new_text": "int led_state = 1;",
                },
                project_dir=tmp,
                approver=interactive_approver,
            )
            self.assertTrue(out_edit.allowed)
            assert out_edit.request is not None
            assert out_edit.request.diff_preview is not None
            self.assertIn("--- a/src/main.c", out_edit.request.diff_preview)
            self.assertIn("+++ b/src/main.c", out_edit.request.diff_preview)
            self.assertIn("-int led_state = 0;", out_edit.request.diff_preview)
            self.assertIn("+int led_state = 1;", out_edit.request.diff_preview)

            long_content = "\n".join(f"// line {i}" for i in range(60))
            out_write = inter_i.authorize(
                "write_file",
                "workspace_write",
                {"path": "src/new_driver.c", "content": long_content},
                project_dir=tmp,
                approver=interactive_approver,
            )
            self.assertTrue(out_write.allowed)
            assert out_write.request is not None and out_write.request.diff_preview is not None
            self.assertIn("[create] src/new_driver.c", out_write.request.diff_preview)
            self.assertIn("more lines", out_write.request.diff_preview)

            # 2. auto_safe 模式：放行 edit_file 与安全只读 shell，拦截 write_memory 与危险 shell
            guard_a = SafetyGuard()
            inter_a = HarnessInterlock(mode="auto_safe", safety_guard=guard_a)
            auto_calls: list[ApprovalRequest] = []

            def auto_approver(req: ApprovalRequest) -> ApprovalDecision:
                auto_calls.append(req)
                return ApprovalDecision(approved=False, scope="deny", reason="blocked by test policy")

            self.assertTrue(
                inter_a.authorize(
                    "edit_file",
                    "workspace_write",
                    {"path": "src/main.c", "old_text": "0", "new_text": "1"},
                    project_dir=tmp,
                    approver=auto_approver,
                ).allowed
            )
            for safe_cmd in ("ls -la", "cat src/main.c", "git status", "git diff HEAD", "git log -n 5", "pwd"):
                self.assertTrue(is_safe_readonly_shell_command(safe_cmd), msg=safe_cmd)
                res = inter_a.authorize(
                    "run_shell",
                    "shell",
                    {"command": safe_cmd},
                    project_dir=tmp,
                    approver=auto_approver,
                )
                self.assertTrue(res.allowed, msg=f"Expected auto_safe to allow {safe_cmd!r}")
            self.assertEqual(len(auto_calls), 0)

            for unsafe_cmd in (
                "rm -rf /tmp/something",
                "git status && rm -rf build",
                "cat src/main.c > out.txt",
                "git reset --hard",
                "ls; whoami",
                "echo $(id)",
            ):
                self.assertFalse(is_safe_readonly_shell_command(unsafe_cmd), msg=unsafe_cmd)
                res = inter_a.authorize(
                    "run_shell",
                    "shell",
                    {"command": unsafe_cmd},
                    project_dir=tmp,
                    approver=auto_approver,
                )
                self.assertFalse(res.allowed, msg=f"Expected auto_safe to gate {unsafe_cmd!r}")
            self.assertEqual(len(auto_calls), 6)

            mem_res = inter_a.authorize(
                "write_memory",
                "hardware_danger",
                {"address": "0x42020014", "values": [1]},
                project_dir=tmp,
                approver=auto_approver,
            )
            self.assertFalse(mem_res.allowed)
            self.assertEqual(len(auto_calls), 7)

            # 3. read_only 模式：放行只读工具，拒绝一切变更
            inter_ro = HarnessInterlock(mode="read_only", safety_guard=SafetyGuard())
            self.assertTrue(
                inter_ro.authorize("inspect_peripheral", "read_only", {}, project_dir=tmp).allowed
            )
            self.assertTrue(
                inter_ro.authorize("probe_control", "hardware_state", {"action": "info"}, project_dir=tmp).allowed
            )
            for tname, risk, args in (
                ("edit_file", "workspace_write", {"path": "src/main.c"}),
                ("write_file", "workspace_write", {"path": "x.c", "content": "a"}),
                ("run_shell", "shell", {"command": "ls"}),
                ("probe_control", "hardware_state", {"action": "halt"}),
                ("read_debug_log", "read_only", {"action": "write", "command": "help"}),
                ("write_memory", "hardware_danger", {"address": "0x20000000", "values": [1]}),
            ):
                ro_res = inter_ro.authorize(tname, risk, args, project_dir=tmp, approver=interactive_approver)
                self.assertFalse(ro_res.allowed, msg=f"read_only should deny {tname} {args}")

    def test_tool_specific_risk_refinements_and_missing_approver(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            interlock = HarnessInterlock(mode="interactive", safety_guard=SafetyGuard())

            # probe_control 只读子动作无需 approver 自动放行
            for ro_act in ("info", "regs", "audit"):
                out = interlock.authorize(
                    "probe_control",
                    "hardware_state",
                    {"action": ro_act},
                    project_dir=tmp,
                    approver=None,
                )
                self.assertTrue(out.allowed, msg=ro_act)

            # probe_control 状态变更子动作在无 approver 时拒绝，批准 session_tool 后可缓存
            no_app = interlock.authorize(
                "probe_control",
                "read_only",
                {"action": "halt"},
                project_dir=tmp,
                approver=None,
            )
            self.assertFalse(no_app.allowed)
            self.assertIsNotNone(no_app.request)

            probe_calls = 0

            def approve_probe(req: ApprovalRequest) -> ApprovalDecision:
                nonlocal probe_calls
                probe_calls += 1
                return ApprovalDecision(approved=True, scope="session_tool")

            p1 = interlock.authorize(
                "probe_control", "hardware_state", {"action": "halt"}, project_dir=tmp, approver=approve_probe
            )
            p2 = interlock.authorize(
                "probe_control", "hardware_state", {"action": "reset"}, project_dir=tmp, approver=approve_probe
            )
            self.assertTrue(p1.allowed and p2.allowed)
            self.assertEqual(probe_calls, 1)

            # read_debug_log(action="write") 按 hardware_state 请求确认
            log_r = interlock.authorize(
                "read_debug_log", "read_only", {"action": "read"}, project_dir=tmp, approver=None
            )
            self.assertTrue(log_r.allowed)

            log_w = interlock.authorize(
                "read_debug_log",
                "read_only",
                {"action": "write", "source": "uart", "command": "reboot\n"},
                project_dir=tmp,
                approver=None,
            )
            self.assertFalse(log_w.allowed)
            assert log_w.request is not None
            self.assertEqual(log_w.request.risk_level, "hardware_state")


class TestContextStatusCardAndPlaybooks(unittest.TestCase):
    """验证动态硬件状态卡片、系统提示词与嵌入式斜杠工作流解析。"""

    def test_build_hardware_status_card_and_system_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CMakeLists.txt").write_text(
                "add_compile_definitions(-DSTM32H563xx)\nset(CPU -mcpu=cortex-m33)\n",
                encoding="utf-8",
            )
            (root / "board.ioc").write_text("Mcu.UserName=STM32H563ZITx\n", encoding="utf-8")
            svd_dst = root / "stm32h563_mini.svd"
            svd_dst.write_text(_FIXTURE_SVD.read_text(encoding="utf-8"), encoding="utf-8")
            elf_dst = root / "build" / "fw.elf"
            elf_dst.parent.mkdir(parents=True, exist_ok=True)
            elf_dst.write_bytes(b"\x7fELF" + b"\x00" * 32)

            profile = discover_project(root)

            pm = ProbeManager()
            mock_probe = MockProbe(device="STM32H563ZI", halted=True)
            mock_probe.connect()
            pm.set_probe(mock_probe)

            rtt = RttLogCollector(pm)
            rtt.buffer.feed_bytes(b"boot ok\ninit done\n")
            uart = UartLogCollector(port="/dev/ttyACM0", baudrate=115200)
            uart.buffer.feed_bytes(b"UART ready\n")

            guard = SafetyGuard()
            for i in range(5):
                guard.record_audit("write_memory", f"0x2000000{i}", level="allow", allowed=True)

            card, telemetry = build_hardware_status_card(
                str(root),
                probe_manager=pm,
                rtt_collector=rtt,
                uart_collector=uart,
                safety_guard=guard,
                profile=profile,
            )

            self.assertIn("STM32H563ZI", card)
            self.assertIn("Cortex-M33", card)
            self.assertIn("connected", card)
            self.assertIn("halted", card)
            self.assertIn("ELF: OK", card)
            self.assertIn("SVD: OK", card)
            self.assertIn("IOC: OK", card)
            self.assertIn("2 lines", card)
            self.assertIn("1 lines", card)

            self.assertEqual(telemetry["device"], "STM32H563ZI")
            self.assertTrue(telemetry["probe"]["connected"])
            self.assertTrue(telemetry["probe"]["halted"])
            self.assertTrue(telemetry["artifacts"]["elf"]["exists"])
            self.assertTrue(telemetry["artifacts"]["svd"]["exists"])
            self.assertTrue(telemetry["artifacts"]["ioc"]["exists"])
            self.assertEqual(telemetry["logs"]["rtt"]["buffered_lines"], 2)
            self.assertEqual(telemetry["logs"]["uart"]["buffered_lines"], 1)
            self.assertEqual(len(telemetry["recent_audit"]), 3)
            self.assertEqual([a["seq"] for a in telemetry["recent_audit"]], [3, 4, 5])

            prompt = build_system_prompt(card, playbook_prompt="Step 1: inspect_board")
            self.assertIn("Structured Hardware Tools", prompt)
            self.assertIn("Hardware Safety Interlocks", prompt)
            self.assertIn("STM32H563ZI", prompt)
            self.assertIn("Active Embedded Playbook SOP", prompt)
            self.assertIn("Step 1: inspect_board", prompt)

    def test_slash_playbooks_and_meta_commands(self) -> None:
        self.assertEqual(set(PLAYBOOKS.keys()), {"crash", "doctor", "bringup", "refactor", "hil"})
        self.assertEqual(
            set(META_COMMANDS.keys()),
            {"status", "audit", "compact", "clear", "sessions", "help", "exit", "quit"},
        )

        # 5 个嵌入式工作流解析验证
        r_crash = expand_slash_command("/crash")
        self.assertIsInstance(r_crash, SlashResolution)
        self.assertEqual(r_crash.kind, "playbook")
        self.assertTrue(r_crash.requires_llm)
        self.assertFalse(r_crash.is_local_meta)
        assert r_crash.expanded_prompt is not None
        self.assertIn("diagnose_crash", r_crash.expanded_prompt)
        self.assertIn("inspect_rtos", r_crash.expanded_prompt)
        self.assertIn("read_debug_log", r_crash.expanded_prompt)

        r_doc = expand_slash_command("/doctor USART1")
        self.assertEqual(r_doc.kind, "playbook")
        self.assertEqual(r_doc.command, "/doctor")
        self.assertEqual(r_doc.args, "USART1")
        assert r_doc.expanded_prompt is not None
        self.assertIn("USART1", r_doc.expanded_prompt)
        self.assertIn("inspect_board(live_check=True)", r_doc.expanded_prompt)
        self.assertIn("diagnose_peripheral", r_doc.expanded_prompt)
        self.assertIn("inspect_peripheral(diff_only=True)", r_doc.expanded_prompt)

        r_bringup = expand_slash_command("/bringup")
        self.assertEqual(r_bringup.kind, "playbook")
        assert r_bringup.expanded_prompt is not None
        for tool_name in ("inspect_project", "inspect_board", "probe_control", "trace_target", "read_debug_log"):
            self.assertIn(tool_name, r_bringup.expanded_prompt)

        r_refactor = expand_slash_command("/refactor")
        self.assertEqual(r_refactor.kind, "playbook")
        assert r_refactor.expanded_prompt is not None
        for tool_name in ("generate_compile_commands", "check_architecture", "analyze_footprint", "scaffold_code"):
            self.assertIn(tool_name, r_refactor.expanded_prompt)

        r_hil = expand_slash_command("/hil tests/hil_motor.toml")
        self.assertEqual(r_hil.kind, "playbook")
        self.assertEqual(r_hil.args, "tests/hil_motor.toml")
        assert r_hil.expanded_prompt is not None
        self.assertIn("tests/hil_motor.toml", r_hil.expanded_prompt)
        for tool_name in ("build_firmware", "flash_firmware", "run_hil_tests"):
            self.assertIn(tool_name, r_hil.expanded_prompt)

        # 8 个本地元命令解析验证
        for meta in ("status", "audit", "compact", "clear", "sessions", "help", "exit", "quit"):
            res = expand_slash_command(f"/{meta}")
            self.assertEqual(res.kind, "meta")
            self.assertTrue(res.is_local_meta)
            self.assertFalse(res.requires_llm)
            self.assertEqual(res.meta_action, meta)
            self.assertEqual(res.command, f"/{meta}")
            self.assertIsInstance(res.to_dict(), dict)

        # 普通自然语言与未知命令
        r_plain = expand_slash_command("Check GPIOAMODER register")
        self.assertEqual(r_plain.kind, "prompt")
        self.assertTrue(r_plain.requires_llm)
        self.assertEqual(r_plain.expanded_prompt, "Check GPIOAMODER register")

        r_unk = expand_slash_command("/stats")
        self.assertEqual(r_unk.kind, "unknown")
        self.assertFalse(r_unk.requires_llm)
        assert r_unk.error is not None
        self.assertIn("/status", r_unk.error)

        help_txt = format_slash_help()
        self.assertIn("/crash", help_txt)
        self.assertIn("/doctor", help_txt)
        self.assertIn("/status", help_txt)
        self.assertIsInstance(PLAYBOOKS["crash"], PlaybookSpec)


if __name__ == "__main__":
    unittest.main(verbosity=2)
