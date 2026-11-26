"""Subagent 6C 端到端全链路集成测试：`AgentRuntime` + `HarnessInterlock` + `MockLlmProvider` + `MockProbe` + `tui`。

覆盖全部核心场景：
1. `/crash` 斜杠工作流端到端（`MockProbe` 注入 Cortex-M33 `PRECISERR`，`diagnose_crash` 还原根因）
   + 验证本地 `/status` 与 `/help` 消耗 0 次 LLM 调用。
2. LLM 幻觉尝试写 STM32H5 Option Bytes (`0x08FFF000`) 被外部互锁硬拒绝（不弹窗、不触碰探针、`denied_tools` 含 `write_memory`）。
3. `write_memory` 写 `GPIOA->ODR` (`0x42020014`, `[0x20]`) 不可绕过 HITL 检验（忽略恶意 `"confirm": true`，
   SVD 预览含 `GPIOA->ODR`，拒绝不变、批准更新且审计、`hardware_danger` 永不缓存）。
4. `edit_file` 审批 + Unified Diff 预览 + 真实写盘验证。
5. 长会话 JSONL 持久化 + `.mcu-harness/.gitignore` + 新 `AgentRuntime` 重载 + `/compact` 压缩 + `mcu-agent -p "/status" --mock-llm` CLI 冒烟测试。

同时兼容 `pytest` 与 `PYTHONPATH=src python3 tests/test_agent_runtime.py -v`。
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from mcu_harness.agent.interlock import ApprovalDecision, ApprovalRequest, HarnessInterlock
from mcu_harness.agent.llm import LlmResponse, MockLlmProvider, ToolCall
from mcu_harness.agent.runtime import AgentRuntime, list_sessions, load_session
from mcu_harness.agent import tui
from mcu_harness.log import RttLogCollector, UartLogCollector
from mcu_harness.probe import MockProbe, ProbeManager
from mcu_harness.safety import SafetyGuard

_FIXTURE_SVD = Path(__file__).resolve().parent / "fixtures" / "stm32h563_mini.svd"


def _create_temp_mcu_project(root: Path) -> Path:
    """在临时目录中初始化最小 STM32H563 工程（含 mcu-harness.toml 与 mini SVD）。"""
    svd_dest = root / "stm32h563_mini.svd"
    shutil.copyfile(_FIXTURE_SVD, svd_dest)
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "main.c").write_text(
        "int led_state = 0;\nvoid app_tick(void) {\n    led_state ^= 1;\n}\n",
        encoding="utf-8",
    )
    (root / "mcu-harness.toml").write_text(
        '[target]\n'
        'device = "STM32H563ZI"\n'
        'core = "Cortex-M33"\n'
        'probe_backend = "mock"\n'
        'interface = "SWD"\n\n'
        '[paths]\n'
        'svd_path = "stm32h563_mini.svd"\n',
        encoding="utf-8",
    )
    return root


def _make_isolated_runtime(
    project_dir: Path,
    provider: MockLlmProvider,
    *,
    mode: str = "interactive",
    session_id: str | None = None,
    probe: MockProbe | None = None,
    safety_guard: SafetyGuard | None = None,
    max_steps: int = 20,
) -> tuple[AgentRuntime, ProbeManager, MockProbe, SafetyGuard]:
    pm = ProbeManager()
    mock_probe = probe or MockProbe(device="STM32H563ZI")
    if not mock_probe.is_connected():
        mock_probe.connect()
    pm.set_probe(mock_probe)

    guard = safety_guard or SafetyGuard()
    interlock = HarnessInterlock(mode=mode, safety_guard=guard)
    rtt = RttLogCollector(pm)
    uart = UartLogCollector()
    rt = AgentRuntime(
        project_dir=project_dir,
        provider=provider,
        interlock=interlock,
        probe_manager=pm,
        rtt_collector=rtt,
        uart_collector=uart,
        safety_guard=guard,
        session_id=session_id,
        max_steps=max_steps,
    )
    return rt, pm, mock_probe, guard


class TestAgentRuntimeEndToEnd(unittest.TestCase):
    """独立第三方 Harness 运行时与 TUI 全链路端到端测试。"""

    def test_01_crash_slash_workflow_and_zero_token_local_meta_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = _create_temp_mcu_project(Path(tmp))
            probe = MockProbe(device="STM32H563ZI")
            probe.connect()
            probe.inject_cortex_m_fault(
                cfsr=0x00008200,  # PRECISERR + BFARVALID
                hfsr=0x40000000,
                bfar=0x00000004,
                stacked_pc=0x08000420,
            )

            def crash_llm_cb(
                messages: list[dict[str, Any]],
                system: str,
                tools: list[dict[str, Any]] | None,
            ) -> LlmResponse:
                self.assertIn("Active Embedded Playbook SOP", system)
                self.assertIn("diagnose_crash", system)
                self.assertTrue(tools and any(t["name"] == "diagnose_crash" for t in tools))

                last = messages[-1]
                if last["role"] == "user":
                    return LlmResponse(
                        text="Invoking diagnose_crash...",
                        tool_calls=[ToolCall(id="call_crash_1", name="diagnose_crash", arguments={})],
                        finish_reason="tool_calls",
                    )

                self.assertEqual(last["role"], "tool")
                self.assertEqual(last["name"], "diagnose_crash")
                report = json.loads(last["content"])
                self.assertIn("PRECISERR", json.dumps(report))
                self.assertIn("0x00000004", json.dumps(report))
                return LlmResponse(
                    text=(
                        f"Root cause: Cortex-M33 HardFault escalated from BusFault PRECISERR "
                        f"with BFAR=0x00000004 (null-pointer struct offset dereference at PC=0x08000420)."
                    ),
                    finish_reason="stop",
                )

            provider = MockLlmProvider(crash_llm_cb)
            rt, _pm, _probe, _guard = _make_isolated_runtime(proj, provider, probe=probe)

            # 本地 /status 与 /help 消耗 0 次 LLM 调用
            status_res = rt.run_turn("/status")
            help_res = rt.run_turn("/help")
            self.assertEqual(len(provider.call_history), 0)
            self.assertIn("STM32H563ZI", status_res.text)
            self.assertIn("/crash", help_res.text)
            self.assertIn("/status", help_res.text)

            # /crash 端到端 ReAct 调用 diagnose_crash 并归因根因
            events: list[str] = []
            unsub = rt.subscribe(lambda ev: events.append(ev.kind))
            try:
                crash_res = rt.run_turn("/crash")
            finally:
                unsub()

            self.assertIsNone(crash_res.error)
            self.assertEqual(crash_res.steps, 2)
            self.assertEqual(crash_res.tool_calls_executed, 1)
            self.assertEqual(len(provider.call_history), 2)
            self.assertIn("PRECISERR", crash_res.text)
            self.assertIn("0x00000004", crash_res.text)
            self.assertIn("tool_call", events)
            self.assertIn("tool_result", events)
            self.assertIn("turn_completed", events)

    def test_02_option_bytes_hallucination_hard_denied_without_approver_or_probe_touch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = _create_temp_mcu_project(Path(tmp))
            probe = MockProbe(device="STM32H563ZI")
            probe.connect()
            probe.set_word(0x08FFF000, 0xCAFEBABE)

            write_u32_spy = mock.MagicMock(wraps=probe.write_u32)
            probe.write_u32 = write_u32_spy  # type: ignore[method-assign]

            approver_calls: list[ApprovalRequest] = []

            def spy_approver(req: ApprovalRequest) -> ApprovalDecision:
                approver_calls.append(req)
                return ApprovalDecision(approved=True, scope="once", reason="should never be called")

            provider = MockLlmProvider(
                [
                    LlmResponse(
                        text="Writing STM32H5 Option Bytes...",
                        tool_calls=[
                            ToolCall(
                                id="call_ob_1",
                                name="write_memory",
                                arguments={
                                    "address": "0x08FFF000",
                                    "values": ["0x000000AA"],
                                    "width": 32,
                                    "confirm": True,
                                },
                            )
                        ],
                        finish_reason="tool_calls",
                    ),
                    LlmResponse(
                        text="Blocked by hardware safety interlock; Option Bytes untouched.",
                        finish_reason="stop",
                    ),
                ]
            )
            rt, _pm, _probe, guard = _make_isolated_runtime(proj, provider, probe=probe)
            res = rt.run_turn("Modify STM32H5 option bytes", approver=spy_approver)

            self.assertEqual(len(approver_calls), 0, "Hard deny must never invoke approver")
            self.assertEqual(write_u32_spy.call_count, 0, "Hard deny must never touch MockProbe")
            self.assertEqual(probe.read_u32(0x08FFF000, 1), [0xCAFEBABE])
            self.assertIn("write_memory", res.denied_tools)
            self.assertEqual(res.tool_calls_executed, 0)

            tool_msgs = [m for m in rt.messages if m.get("role") == "tool"]
            self.assertEqual(len(tool_msgs), 1)
            payload = json.loads(tool_msgs[0]["content"])
            self.assertFalse(payload["allowed"])
            self.assertTrue(payload["hard_denied"])
            self.assertIn("HARD DENY", payload["error"])
            self.assertTrue(any(not e.allowed and e.level == "hard_deny" for e in guard.recent_audit(10)))

    def test_03_write_memory_gpioa_odr_hitl_unbypassable_and_never_cached(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = _create_temp_mcu_project(Path(tmp))
            probe = MockProbe(device="STM32H563ZI")
            probe.connect()
            probe.set_word(0x42020014, 0x00000000)

            seen_requests: list[ApprovalRequest] = []

            def reject_approver(req: ApprovalRequest) -> ApprovalDecision:
                seen_requests.append(req)
                return ApprovalDecision(approved=False, scope="deny", reason="Operator rejected GPIOA->ODR write")

            def approve_session_tool(req: ApprovalRequest) -> ApprovalDecision:
                seen_requests.append(req)
                return ApprovalDecision(approved=True, scope="session_tool", reason="Operator approved")

            def _make_gpio_write_provider(val: int, tag: str) -> MockLlmProvider:
                return MockLlmProvider(
                    [
                        LlmResponse(
                            text="",
                            tool_calls=[
                                ToolCall(
                                    id=f"call_gpio_{tag}",
                                    name="write_memory",
                                    arguments={
                                        "address": "0x42020014",
                                        "values": [val],
                                        "width": 32,
                                        "peripheral_hint": "GPIOA",
                                        "confirm": True,  # 恶意伪造确认参数，必须被忽略
                                    },
                                )
                            ],
                            finish_reason="tool_calls",
                        ),
                        LlmResponse(text=f"Done {tag}.", finish_reason="stop"),
                    ]
                )

            # 1) 拒绝分支：即使参数夹带 confirm=True，仍弹 approver；拒绝后内存不变
            rt, _pm, _probe, guard = _make_isolated_runtime(
                proj, _make_gpio_write_provider(0x20, "reject"), probe=probe
            )
            res_reject = rt.run_turn("Set PA5 high", approver=reject_approver)
            self.assertEqual(len(seen_requests), 1)
            self.assertIsNotNone(seen_requests[0].svd_preview)
            assert seen_requests[0].svd_preview is not None
            self.assertIn("GPIOA->ODR", seen_requests[0].svd_preview)
            self.assertIn("OD5", seen_requests[0].svd_preview)
            self.assertEqual(probe.read_u32(0x42020014, 1), [0x00000000])
            self.assertIn("write_memory", res_reject.denied_tools)
            self.assertEqual(res_reject.tool_calls_executed, 0)

            # 2) 批准分支（返回 scope="session_tool"）：内存真实写入 0x00000020，并写入 SafetyGuard 审计
            rt.provider = _make_gpio_write_provider(0x20, "allow1")
            res_allow1 = rt.run_turn("Set PA5 high again", approver=approve_session_tool)
            self.assertEqual(len(seen_requests), 2)
            self.assertEqual(res_allow1.tool_calls_executed, 1)
            self.assertEqual(probe.read_u32(0x42020014, 1), [0x00000020])
            audits = guard.recent_audit(10)
            self.assertTrue(
                any(a.allowed and ("0x42020014" in a.target or "GPIOA->ODR" in a.target) for a in audits)
            )

            # 3) 再次调用 write_memory：即使前一次返回了 scope="session_tool"，hardware_danger 绝不缓存！
            rt.provider = _make_gpio_write_provider(0x00, "allow2")
            res_allow2 = rt.run_turn("Clear PA5", approver=approve_session_tool)
            self.assertEqual(len(seen_requests), 3, "hardware_danger must prompt on every single call")
            self.assertEqual(res_allow2.tool_calls_executed, 1)
            self.assertEqual(probe.read_u32(0x42020014, 1), [0x00000000])
            self.assertNotIn("write_memory", rt.interlock.session_approved_tools())

    def test_04_edit_file_approval_diff_preview_and_disk_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = _create_temp_mcu_project(Path(tmp))
            target_file = proj / "src" / "main.c"
            seen: list[ApprovalRequest] = []

            def approve_edit(req: ApprovalRequest) -> ApprovalDecision:
                seen.append(req)
                return ApprovalDecision(approved=True, scope="once", reason="Diff reviewed and approved")

            provider = MockLlmProvider(
                [
                    LlmResponse(
                        text="Updating led_state initializer...",
                        tool_calls=[
                            ToolCall(
                                id="call_edit_1",
                                name="edit_file",
                                arguments={
                                    "path": "src/main.c",
                                    "old_text": "int led_state = 0;",
                                    "new_text": "int led_state = 1;",
                                },
                            )
                        ],
                        finish_reason="tool_calls",
                    ),
                    LlmResponse(text="Updated src/main.c successfully.", finish_reason="stop"),
                ]
            )
            rt, _pm, _probe, _guard = _make_isolated_runtime(proj, provider, mode="interactive")
            res = rt.run_turn("Initialize led_state to 1", approver=approve_edit)

            self.assertIsNone(res.error)
            self.assertEqual(res.tool_calls_executed, 1)
            self.assertEqual(len(seen), 1)
            req = seen[0]
            self.assertEqual(req.tool_name, "edit_file")
            self.assertIsNotNone(req.diff_preview)
            assert req.diff_preview is not None
            self.assertIn("--- a/src/main.c", req.diff_preview)
            self.assertIn("+++ b/src/main.c", req.diff_preview)
            self.assertIn("-int led_state = 0;", req.diff_preview)
            self.assertIn("+int led_state = 1;", req.diff_preview)

            disk_content = target_file.read_text(encoding="utf-8")
            self.assertIn("int led_state = 1;", disk_content)
            self.assertNotIn("int led_state = 0;", disk_content)

    def test_05_session_persistence_reload_compact_and_cli_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = _create_temp_mcu_project(Path(tmp))
            session_id = "e2e-session-001"
            p1 = MockLlmProvider(
                [
                    LlmResponse(text="Reply 1"),
                    LlmResponse(text="Reply 2"),
                    LlmResponse(text="Reply 3"),
                    LlmResponse(text="Reply 4"),
                ]
            )
            rt1, _pm1, _probe1, _guard1 = _make_isolated_runtime(proj, p1, session_id=session_id)
            for idx in range(1, 5):
                r = rt1.run_turn(f"Question {idx}")
                self.assertEqual(r.text, f"Reply {idx}")

            self.assertEqual(len(rt1.messages), 8)
            gi_path = proj / ".mcu-harness" / ".gitignore"
            sess_path = proj / ".mcu-harness" / "sessions" / f"{session_id}.jsonl"
            self.assertTrue(gi_path.is_file())
            self.assertEqual(gi_path.read_text(encoding="utf-8").strip(), "*")
            self.assertTrue(sess_path.is_file())

            sessions = list_sessions(proj)
            self.assertEqual(len(sessions), 1)
            self.assertEqual(sessions[0]["session_id"], session_id)
            self.assertEqual(sessions[0]["message_count"], 8)

            loaded = load_session(proj, session_id)
            self.assertEqual(len(loaded["messages"]), 8)

            # 新建 AgentRuntime 并加载同一 session_id
            p2 = MockLlmProvider([])
            rt2, _pm2, _probe2, _guard2 = _make_isolated_runtime(proj, p2, session_id=session_id)
            self.assertEqual(len(rt2.messages), 8)
            self.assertEqual(rt2.messages[0]["content"], "Question 1")
            self.assertEqual(rt2.messages[-1]["content"], "Reply 4")

            # /compact 压缩消息长度且消耗 0 次 LLM 调用
            compact_res = rt2.run_turn("/compact")
            self.assertIn("8 -> 5", compact_res.text)
            self.assertEqual(len(rt2.messages), 5)
            self.assertIn("[Compacted conversation digest]", rt2.messages[0]["content"])
            self.assertEqual(len(p2.call_history), 0)

            # CLI 冒烟测试：mcu-agent -p "/status" --mock-llm
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                rc = tui.main(["-p", "/status", "--mock-llm", "--no-color", str(proj)])
            self.assertEqual(rc, 0, msg=stderr_buf.getvalue())
            out_txt = stdout_buf.getvalue()
            self.assertIn("Hardware & Project Status", out_txt)
            self.assertIn("STM32H563ZI", out_txt)
            self.assertEqual(stderr_buf.getvalue(), "")

            # 验证 --mock-llm 运行一般问题也会走只读工具并返回干净结果
            stdout_buf2 = io.StringIO()
            stderr_buf2 = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf2), contextlib.redirect_stderr(stderr_buf2):
                rc2 = tui.main(["-p", "Summarize this project", "--mock-llm", "--no-color", str(proj)])
            self.assertEqual(rc2, 0, msg=stderr_buf2.getvalue())
            self.assertIn("[tool] inspect_project", stdout_buf2.getvalue())
            self.assertIn("STM32H563ZI", stdout_buf2.getvalue())

    def test_06_tui_terminal_approver_behaviors(self) -> None:
        # 1) 非交互且 stdin 非 TTY 时，安全拒绝
        req_danger = ApprovalRequest(
            request_id="req-1",
            tool_name="write_memory",
            risk_level="hardware_danger",
            summary="Write 4B to GPIOA->ODR (0x42020014)",
            details={"address": "0x42020014", "values_hex": ["0x00000020"]},
            svd_preview="GPIOA->ODR = 0x00000020\n  OD5 [5] = 1",
        )
        buf = io.StringIO()
        dec_non_int = tui.terminal_approver(
            req_danger,
            ansi=tui._Ansi(enabled=False),
            non_interactive=True,
            out=buf,
        )
        self.assertFalse(dec_non_int.approved)
        self.assertEqual(dec_non_int.scope, "deny")
        self.assertEqual(dec_non_int.reason, "Non-interactive mode")

        # 2) hardware_danger 即使输入 'a'，也降级为 scope="once" 并显示警示与 SVD 预览
        buf_danger = io.StringIO()
        dec_danger = tui.terminal_approver(
            req_danger,
            ansi=tui._Ansi(enabled=False),
            non_interactive=False,
            input_fn=lambda _prompt: "a",
            out=buf_danger,
        )
        self.assertTrue(dec_danger.approved)
        self.assertEqual(dec_danger.scope, "once")
        rendered = buf_danger.getvalue()
        self.assertIn("高危硬件操作不支持会话免确认", rendered)
        self.assertIn("GPIOA->ODR", rendered)

        # 3) workspace_write 允许 'a' -> scope="session_tool" 并渲染 Unified Diff
        req_edit = ApprovalRequest(
            request_id="req-2",
            tool_name="edit_file",
            risk_level="workspace_write",
            summary="edit_file: src/main.c",
            details={"relative_path": "src/main.c"},
            diff_preview="--- a/src/main.c\n+++ b/src/main.c\n@@ -1 +1 @@\n-old\n+new\n",
        )
        buf_edit = io.StringIO()
        dec_edit = tui.terminal_approver(
            req_edit,
            ansi=tui._Ansi(enabled=False),
            non_interactive=False,
            input_fn=lambda _prompt: "a",
            out=buf_edit,
        )
        self.assertTrue(dec_edit.approved)
        self.assertEqual(dec_edit.scope, "session_tool")
        self.assertIn("+new", buf_edit.getvalue())

    def test_07_turn_cancellation_mid_stream_and_between_tool_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = _create_temp_mcu_project(Path(tmp))

            # 1) 流式 delta 回调期间触发取消
            class _StreamingCancelProvider(MockLlmProvider):
                def complete(
                    self,
                    messages: list[dict[str, Any]],
                    *,
                    system: str = "",
                    tools: list[dict[str, Any]] | None = None,
                    temperature: float = 0.2,
                    max_tokens: int = 4096,
                    on_text_delta: Any = None,
                ) -> LlmResponse:
                    del messages, system, tools, temperature, max_tokens
                    assert on_text_delta is not None
                    on_text_delta("Hello ")
                    rt.request_cancel()
                    on_text_delta("world")
                    return LlmResponse(text="Hello world", finish_reason="stop")

            rt, _pm, _probe, _guard = _make_isolated_runtime(proj, _StreamingCancelProvider())
            events: list[Any] = []
            unsub = rt.subscribe(events.append)
            try:
                res1 = rt.run_turn("Stream and cancel")
            finally:
                unsub()

            self.assertTrue(res1.aborted)
            self.assertIsNone(res1.error)
            self.assertEqual(res1.text, "Hello ")
            completed_evs = [e for e in events if e.kind == "turn_completed"]
            self.assertEqual(len(completed_evs), 1)
            self.assertTrue(completed_evs[0].data["aborted"])
            self.assertTrue(completed_evs[0].data["cancelled"])

            # 2) 多工具调用之间取消：仅执行第一个工具，第二个工具跳过
            multi_tool_provider = MockLlmProvider(
                [
                    LlmResponse(
                        text="Running two tools",
                        tool_calls=[
                            ToolCall(id="tc_1", name="list_files", arguments={"path": "."}),
                            ToolCall(id="tc_2", name="read_file", arguments={"path": "src/main.c"}),
                        ],
                        finish_reason="tool_calls",
                    ),
                    LlmResponse(text="Should not reach step 2", finish_reason="stop"),
                ]
            )
            rt2, _pm2, _probe2, _guard2 = _make_isolated_runtime(proj, multi_tool_provider, mode="auto_safe")
            events2: list[Any] = []

            def _cancel_after_first_tool(ev: Any) -> None:
                events2.append(ev)
                if ev.kind == "tool_result" and ev.data.get("id") == "tc_1":
                    rt2.request_cancel()

            unsub2 = rt2.subscribe(_cancel_after_first_tool)
            try:
                res2 = rt2.run_turn("Run two tools")
            finally:
                unsub2()

            self.assertTrue(res2.aborted)
            self.assertIsNone(res2.error)
            self.assertEqual(res2.tool_calls_executed, 1)
            self.assertEqual(len(multi_tool_provider.call_history), 1)
            self.assertEqual([e.data["id"] for e in events2 if e.kind == "tool_call"], ["tc_1"])
            done2 = next(e for e in events2 if e.kind == "turn_completed")
            self.assertTrue(done2.data["cancelled"])


if __name__ == "__main__":
    unittest.main()
