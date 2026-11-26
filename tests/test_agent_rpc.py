"""NDJSON stdio RPC 桥接（`mcu-rpc`）与原生桌面启动器（`mcu-desktop`）单元测试与 E2E 测试。

覆盖：
1. 内存流 NDJSON RPC：`ready` 帧、stdout 隔离、`ping`、`get_state`、`telemetry`、`watch`、
   `probe`（info/connect/disconnect/halt/resume/reset/rtt_start/rtt_write/uart_start/uart_write + 审计）、
   `sessions`（list/new/load）、`configure`（热更新 + TOML 转义保存 + `0o600` 权限）、无效 JSON 容错与 `shutdown`。
2. 异步 `chat` + `approval_required` + `resolve_approval`（`once` 批准写入、`approved=False` 强制 `scope="deny"`）
   + STM32H5 Option Bytes (`0x08FFF000`) `hard_denied` 防砖拦截 + 并发对话拒绝。
3. 真实子进程管道端到端：`python3 -m mcu_harness.agent.rpc --mock-llm` 全流程 NDJSON 校验。
4. `desktop_launcher` 二进制探测优先级、未构建提示退出码 1 与 `--install-desktop` 逻辑。

同时兼容 `pytest` 与 `PYTHONPATH=src python3 tests/test_agent_rpc.py -v`。
"""

from __future__ import annotations

import io
import json
import os
import pty
import queue
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from typing import Any
from unittest import mock

from mcu_harness import __version__
from mcu_harness.agent import RpcBridge
from mcu_harness.agent.desktop_launcher import (
    build_parser as build_desktop_parser,
    find_desktop_binary,
    install_desktop_entry,
    main as desktop_main,
)
from mcu_harness.agent.interlock import HarnessInterlock
from mcu_harness.agent.llm import LlmResponse, MockLlmProvider, ToolCall
from mcu_harness.agent.rpc import serve_stdio
from mcu_harness.agent.runtime import AgentRuntime
from mcu_harness.log import RttLogCollector, UartLogCollector
from mcu_harness.probe import MockProbe, ProbeManager
from mcu_harness.safety import SafetyGuard

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

_FIXTURE_SVD = Path(__file__).resolve().parent / "fixtures" / "stm32h563_mini.svd"
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _create_temp_mcu_project(root: Path, *, name: str = "STM32H563ZI") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    svd_dest = root / "stm32h563_mini.svd"
    shutil.copyfile(_FIXTURE_SVD, svd_dest)
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "main.c").write_text("int g_counter = 0;\n", encoding="utf-8")
    (root / "mcu-harness.toml").write_text(
        f'[target]\ndevice = "{name}"\ncore = "Cortex-M33"\nprobe_backend = "mock"\n\n'
        f'[paths]\nsvd_path = "{svd_dest.name}"\n',
        encoding="utf-8",
    )
    return root


class _QueueTextIn:
    """线程安全的阻塞式行输入流，供内存级 `serve_stdio` 双向交互测试使用。"""

    def __init__(self) -> None:
        self._q: queue.Queue[str] = queue.Queue()

    def push_line(self, obj: dict[str, Any] | str) -> None:
        if isinstance(obj, str):
            line = obj if obj.endswith("\n") else obj + "\n"
        else:
            line = json.dumps(obj, ensure_ascii=False) + "\n"
        self._q.put(line)

    def close(self) -> None:
        self._q.put("")

    def readline(self) -> str:
        return self._q.get()


class _QueueTextOut:
    """线程安全的行输出捕获器，每写入一行 NDJSON 立即入队供测试断言。"""

    def __init__(self) -> None:
        self._q: queue.Queue[dict[str, Any]] = queue.Queue()
        self.raw_lines: list[str] = []
        self._buf = ""
        self._lock = threading.Lock()

    def write(self, data: str) -> int:
        with self._lock:
            self._buf += data
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if line.strip():
                    self.raw_lines.append(line)
                    self._q.put(json.loads(line))
        return len(data)

    def flush(self) -> None:
        pass

    def next_frame(self, *, timeout: float = 5.0) -> dict[str, Any]:
        return self._q.get(timeout=timeout)

    def wait_for(
        self,
        predicate: Any,
        *,
        timeout: float = 5.0,
        collected: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Timed out waiting for matching NDJSON frame; collected={collected}")
            frame = self._q.get(timeout=remaining)
            if collected is not None:
                collected.append(frame)
            if predicate(frame):
                return frame


class TestAgentRpcInMemory(unittest.TestCase):
    def _create_env(
        self,
        tmp_dir: str,
        responses: list[LlmResponse] | None = None,
        *,
        approval_timeout_s: float = 30.0,
    ) -> tuple[MockProbe, ProbeManager, MockLlmProvider, SafetyGuard, AgentRuntime, RpcBridge]:
        proj = _create_temp_mcu_project(Path(tmp_dir))
        probe = MockProbe(device="STM32H563ZI")
        probe.connect()
        probe.set_word(0x42020014, 0x00000000)
        probe.set_word(0x08FFF000, 0xCAFEBABE)

        pm = ProbeManager()
        pm.set_probe(probe)
        rtt = RttLogCollector(pm)
        uart = UartLogCollector()
        guard = SafetyGuard()
        provider = MockLlmProvider(responses=responses)
        interlock = HarnessInterlock(mode="interactive", safety_guard=guard)
        runtime = AgentRuntime(
            project_dir=str(proj),
            provider=provider,
            interlock=interlock,
            probe_manager=pm,
            rtt_collector=rtt,
            uart_collector=uart,
            safety_guard=guard,
            persist_sessions=True,
        )
        bridge = RpcBridge(runtime, approval_timeout_s=approval_timeout_s)
        return probe, pm, provider, guard, runtime, bridge

    def test_01_ndjson_basic_methods_watch_probe_sessions_configure_and_stdout_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            probe, pm, provider, guard, runtime, bridge = self._create_env(tmp)
            proj2 = _create_temp_mcu_project(Path(tmp) / "proj2", name="STM32H573II")

            in_stream = _QueueTextIn()
            out_stream = _QueueTextOut()
            err_capture = io.StringIO()
            orig_stdout = sys.stdout
            orig_stderr = sys.stderr

            rc_holder: list[int] = []

            def _runner() -> None:
                # redirect_stdout=True 时，sys.stdout 会被重定向至 sys.stderr
                rc_holder.append(
                    serve_stdio(bridge, in_stream=in_stream, out_stream=out_stream, redirect_stdout=True)
                )

            sys.stderr = err_capture
            t = threading.Thread(target=_runner, name="test-rpc-stdio", daemon=True)
            t.start()
            master_fd: int | None = None
            slave_fd: int | None = None
            try:
                # 1. 启动即发送 ready 异步事件
                ready = out_stream.next_frame()
                self.assertEqual(ready["event"], "ready")
                self.assertEqual(ready["seq"], 0)
                self.assertEqual(ready["data"]["session_id"], runtime.session_id)
                self.assertEqual(ready["data"]["interlock_mode"], "interactive")

                # 2. 验证 stdout 隔离：此时向 sys.stdout 打印必须落入 stderr 而非 proto out_stream
                time.sleep(0.05)
                print("stray diagnostic print must not break NDJSON")

                # 3. ping
                in_stream.push_line({"id": 1, "method": "ping"})
                resp = out_stream.next_frame()
                self.assertEqual(resp, {"id": 1, "ok": True, "result": {"pong": True, "version": __version__}})

                # 4. 无效 JSON 与未知方法容错
                in_stream.push_line("{not-valid-json")
                bad_json = out_stream.next_frame()
                self.assertFalse(bad_json["ok"])
                self.assertIn("Invalid JSON", bad_json["error"])

                in_stream.push_line({"id": "u1", "method": "nonexistent_method"})
                unk = out_stream.next_frame()
                self.assertEqual(unk["id"], "u1")
                self.assertFalse(unk["ok"])
                self.assertIn("Unknown RPC method", unk["error"])

                # 5. get_state & telemetry（零 LLM 调用）
                in_stream.push_line({"id": 2, "method": "get_state"})
                st_resp = out_stream.next_frame()
                self.assertTrue(st_resp["ok"])
                st = st_resp["result"]
                self.assertEqual(st["session_id"], runtime.session_id)
                self.assertGreaterEqual(len(st["playbooks"]), 5)
                self.assertIn("telemetry", st)
                self.assertEqual(len(provider.call_history), 0)

                # 6. watch CRUD
                in_stream.push_line({"id": 3, "method": "watch", "params": {"action": "add", "expression": "g_counter"}})
                w_add = out_stream.next_frame()
                self.assertEqual(w_add["result"]["watch_exprs"], ["g_counter"])

                in_stream.push_line({"id": 4, "method": "telemetry"})
                tel = out_stream.next_frame()["result"]
                self.assertEqual(tel["watch_exprs"], ["g_counter"])
                self.assertIn("g_counter", tel["watch"])
                self.assertFalse(tel["turn_running"])
                self.assertEqual(tel["pending_approvals_count"], 0)

                in_stream.push_line({"id": "alias-pb", "method": "playbooks", "params": {}})
                pb_res = out_stream.next_frame()
                self.assertTrue(pb_res["ok"], msg=pb_res)
                self.assertGreaterEqual(len(pb_res["result"]["playbooks"]), 5)

                in_stream.push_line({"id": "alias-mode", "method": "set_mode", "params": {"mode": "auto_safe"}})
                mode_res = out_stream.next_frame()
                self.assertTrue(mode_res["ok"], msg=mode_res)
                self.assertEqual(mode_res["result"]["mode"], "auto_safe")

                in_stream.push_line({"id": "alias-snap", "method": "snapshot", "params": {"watch_exprs": ["g_counter"]}})
                snap_res = out_stream.next_frame()
                self.assertTrue(snap_res["ok"], msg=snap_res)
                self.assertEqual(snap_res["result"]["interlock_mode"], "auto_safe")
                self.assertEqual(snap_res["result"]["watch_exprs"], ["g_counter"])

                in_stream.push_line({"id": "alias-mode-restore", "method": "set_mode", "params": {"mode": "interactive"}})
                self.assertTrue(out_stream.next_frame()["ok"])

                in_stream.push_line({"id": 5, "method": "watch", "params": {"action": "remove", "expression": "g_counter"}})
                self.assertEqual(out_stream.next_frame()["result"]["watch_exprs"], [])

                in_stream.push_line({"id": 6, "method": "watch", "params": {"action": "add", "expression": "g_x"}})
                out_stream.next_frame()
                in_stream.push_line({"id": 7, "method": "watch", "params": {"action": "clear"}})
                w_clr = out_stream.next_frame()["result"]
                self.assertEqual(w_clr["cleared"], 1)
                self.assertEqual(w_clr["watch_exprs"], [])

                # 7. probe 控制：info / halt / resume / reset / rtt_start / rtt_write / uart_start / uart_write / disconnect / connect
                for rid, act, extra in (
                    ("p1", "info", {}),
                    ("p2", "halt", {}),
                    ("p3", "resume", {}),
                    ("p4", "reset", {"halt_on_reset": True}),
                    ("p5", "rtt_start", {"channel": 0}),
                    ("p6", "rtt_write", {"channel": 0, "command": "hello\n"}),
                ):
                    in_stream.push_line({"id": rid, "method": "probe", "params": {"action": act, **extra}})
                    pr = out_stream.next_frame()
                    self.assertTrue(pr["ok"], msg=f"probe {act} failed: {pr}")
                    self.assertEqual(pr["result"]["action"], act)

                self.assertEqual(probe.pop_rtt_down(), b"hello\n")

                # 使用真实 POSIX PTY 测试 uart_start / uart_write
                master_fd, slave_fd = pty.openpty()
                slave_path = os.ttyname(slave_fd)
                in_stream.push_line({"id": "p7", "method": "probe", "params": {"action": "uart_start", "port": slave_path, "baudrate": 115200}})
                u_st = out_stream.next_frame()
                self.assertTrue(u_st["ok"], msg=u_st)
                self.assertTrue(u_st["result"]["running"])

                in_stream.push_line({"id": "p8", "method": "probe", "params": {"action": "uart_write", "command": "ping\n"}})
                u_wr = out_stream.next_frame()
                self.assertTrue(u_wr["ok"], msg=u_wr)
                self.assertEqual(u_wr["result"]["written_bytes"], 5)
                self.assertEqual(os.read(master_fd, 64), b"ping\n")

                runtime.uart_collector.stop()

                in_stream.push_line({"id": "p9", "method": "probe", "params": {"action": "disconnect"}})
                self.assertFalse(out_stream.next_frame()["result"]["connected"])
                in_stream.push_line({"id": "p10", "method": "probe", "params": {"action": "connect", "backend": "mock", "device": "STM32H563ZI"}})
                c_res = out_stream.next_frame()
                self.assertTrue(c_res["ok"], msg=c_res)
                self.assertTrue(c_res["result"]["connected"])

                audits = guard.recent_audit(limit=20)
                audit_ops = {a.operation for a in audits}
                self.assertTrue({"probe_info", "halt", "resume", "reset", "rtt_start", "rtt_write", "uart_start", "uart_write", "probe_disconnect", "probe_connect"}.issubset(audit_ops))

                # 8. sessions: new -> list -> load
                first_sid = runtime.session_id
                runtime._record_message({"role": "user", "content": "saved msg 1"})
                in_stream.push_line({"id": "s1", "method": "sessions", "params": {"action": "new"}})
                s_new = out_stream.next_frame()["result"]
                self.assertNotEqual(s_new["session_id"], first_sid)
                self.assertEqual(runtime.messages, [])

                in_stream.push_line({"id": "s2", "method": "sessions", "params": {"action": "list"}})
                s_list = out_stream.next_frame()["result"]
                self.assertTrue(any(item["session_id"] == first_sid for item in s_list["sessions"]))

                in_stream.push_line({"id": "s3", "method": "sessions", "params": {"action": "load", "session_id": first_sid}})
                s_load = out_stream.next_frame()["result"]
                self.assertEqual(s_load["session_id"], first_sid)
                self.assertEqual(s_load["message_count"], 1)

                # 9. configure 热更新 + TOML 转义保存 + 0o600 权限
                cfg_path = Path(tmp) / "user_agent.toml"
                special_key = 'sk-test-"quote"\\slash\nnext'
                in_stream.push_line(
                    {
                        "id": "cfg1",
                        "method": "configure",
                        "params": {
                            "project_dir": str(proj2),
                            "provider": "deepseek",
                            "model": "deepseek-chat",
                            "api_key": special_key,
                            "interlock_mode": "auto_safe",
                            "save_user_config": True,
                            "config_path": str(cfg_path),
                        },
                    }
                )
                cfg_resp = out_stream.next_frame()
                self.assertTrue(cfg_resp["ok"], msg=cfg_resp)
                self.assertEqual(cfg_resp["result"]["interlock_mode"], "auto_safe")
                self.assertEqual(cfg_resp["result"]["provider"], "deepseek")
                self.assertEqual(runtime.interlock.mode, "auto_safe")
                self.assertTrue(cfg_path.is_file())
                mode_bits = stat.S_IMODE(cfg_path.stat().st_mode)
                self.assertEqual(mode_bits, 0o600)
                parsed_toml = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
                self.assertEqual(parsed_toml["agent"]["provider"], "deepseek")
                self.assertEqual(parsed_toml["agent"]["api_key"], special_key)

                # 10. shutdown
                in_stream.push_line({"id": 99, "method": "shutdown"})
                sd = out_stream.next_frame()
                self.assertEqual(sd, {"id": 99, "ok": True, "result": {"shutdown": True}})
                t.join(timeout=3.0)
                self.assertEqual(rc_holder, [0])
                self.assertIn("stray diagnostic print must not break NDJSON", err_capture.getvalue())
            finally:
                in_stream.close()
                t.join(timeout=2.0)
                sys.stdout = orig_stdout
                sys.stderr = orig_stderr
                for fd in (master_fd, slave_fd):
                    if fd is not None:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                runtime.uart_collector.stop()
                pm.disconnect()

    def test_02_async_hitl_approval_once_deny_and_option_bytes_hard_deny(self) -> None:
        responses = [
            # Turn 1: write_memory GPIOA->ODR 0x20 (approve once)
            LlmResponse(
                text="Setting GPIOA->ODR bit 5.",
                tool_calls=[
                    ToolCall(
                        id="tc_approve",
                        name="write_memory",
                        arguments={
                            "address": "0x42020014",
                            "values": [0x20],
                            "width": 32,
                            "peripheral_hint": "GPIOA",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            LlmResponse(text="Set to 0x20.", finish_reason="stop"),
            # Turn 2: write_memory GPIOA->ODR 0x40 (deny even if scope="once" sent)
            LlmResponse(
                text="Trying 0x40.",
                tool_calls=[
                    ToolCall(
                        id="tc_deny",
                        name="write_memory",
                        arguments={"address": "0x42020014", "values": [0x40], "width": 32},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LlmResponse(text="Denied by operator.", finish_reason="stop"),
            # Turn 3: Option Bytes 0x08FFF000 -> hard_denied
            LlmResponse(
                text="Trying Option Bytes.",
                tool_calls=[
                    ToolCall(
                        id="tc_ob",
                        name="write_memory",
                        arguments={"address": "0x08FFF000", "values": [0xAA], "width": 32},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LlmResponse(text="Hard denied.", finish_reason="stop"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            probe, pm, _provider, _guard, _runtime, bridge = self._create_env(tmp, responses=responses)
            in_stream = _QueueTextIn()
            out_stream = _QueueTextOut()
            t = threading.Thread(
                target=lambda: serve_stdio(bridge, in_stream=in_stream, out_stream=out_stream, redirect_stdout=False),
                name="test-rpc-hitl",
                daemon=True,
            )
            t.start()
            try:
                ready = out_stream.next_frame()
                self.assertEqual(ready["event"], "ready")

                # --- Turn 1: approve once ---
                in_stream.push_line({"id": 1, "method": "chat", "params": {"message": "Set PA5 high"}})
                chat1 = out_stream.wait_for(lambda f: f.get("id") == 1)
                self.assertTrue(chat1["ok"])
                self.assertTrue(chat1["result"]["accepted"])

                ap_ev = out_stream.wait_for(lambda f: f.get("event") == "approval_required")
                req_id = ap_ev["data"]["request_id"]
                self.assertEqual(ap_ev["data"]["tool_name"], "write_memory")
                self.assertIn("GPIOA->ODR", ap_ev["data"]["svd_preview"])

                # 挂起审批期间并发 chat 与 configure 应被拒绝
                in_stream.push_line({"id": 10, "method": "chat", "params": {"message": "concurrent"}})
                c_rej = out_stream.wait_for(lambda f: f.get("id") == 10)
                self.assertFalse(c_rej["ok"])

                in_stream.push_line({"id": 11, "method": "configure", "params": {"interlock_mode": "auto_safe"}})
                cfg_rej = out_stream.wait_for(lambda f: f.get("id") == 11)
                self.assertFalse(cfg_rej["ok"])

                # list_approvals 应看到挂起项
                in_stream.push_line({"id": 12, "method": "list_approvals"})
                la = out_stream.wait_for(lambda f: f.get("id") == 12)
                self.assertEqual(la["result"]["count"], 1)

                # resolve_approval (approved=True, scope="once")
                in_stream.push_line(
                    {
                        "id": 2,
                        "method": "resolve_approval",
                        "params": {"request_id": req_id, "approved": True, "scope": "once", "reason": "ok"},
                    }
                )
                r_ap = out_stream.wait_for(lambda f: f.get("id") == 2)
                self.assertTrue(r_ap["ok"])

                done1 = out_stream.wait_for(lambda f: f.get("event") == "turn_completed")
                self.assertEqual(done1["data"]["tool_calls_executed"], 1)
                self.assertEqual(probe.read_u32(0x42020014, 1), [0x00000020])

                # --- Turn 2: deny (approved=False, 传入 scope="once" 也必须强制变为 "deny") ---
                in_stream.push_line({"id": 3, "method": "chat", "params": {"message": "Set PA6 high"}})
                out_stream.wait_for(lambda f: f.get("id") == 3)
                ap_ev2 = out_stream.wait_for(lambda f: f.get("event") == "approval_required")
                req_id2 = ap_ev2["data"]["request_id"]

                in_stream.push_line(
                    {
                        "id": 4,
                        "method": "resolve_approval",
                        "params": {"request_id": req_id2, "approved": False, "scope": "once", "reason": "no"},
                    }
                )
                r_deny = out_stream.wait_for(lambda f: f.get("id") == 4)
                self.assertTrue(r_deny["ok"])
                self.assertEqual(r_deny["result"]["decision"]["scope"], "deny")

                done2 = out_stream.wait_for(lambda f: f.get("event") == "turn_completed")
                self.assertIn("write_memory", done2["data"]["denied_tools"])
                self.assertEqual(probe.read_u32(0x42020014, 1), [0x00000020])

                # --- Turn 3: Option Bytes hard_denied ---
                in_stream.push_line({"id": 5, "method": "chat", "params": {"message": "Write Option Bytes"}})
                out_stream.wait_for(lambda f: f.get("id") == 5)
                frames3: list[dict[str, Any]] = []
                done3 = out_stream.wait_for(lambda f: f.get("event") == "turn_completed", collected=frames3)
                self.assertIn("write_memory", done3["data"]["denied_tools"])
                tr3 = next(f for f in frames3 if f.get("event") == "tool_result")
                self.assertTrue(tr3["data"]["hard_denied"])
                self.assertFalse(any(f.get("event") == "approval_required" for f in frames3))
                self.assertEqual(probe.read_u32(0x08FFF000, 1), [0xCAFEBABE])

                in_stream.push_line({"id": 99, "method": "shutdown"})
                out_stream.wait_for(lambda f: f.get("id") == 99)
                t.join(timeout=3.0)
            finally:
                in_stream.close()
                t.join(timeout=2.0)
                pm.disconnect()

    def test_03_new_rpc_methods_cancel_logs_since_serial_stop_test_llm_and_diagnostics(self) -> None:
        responses = [
            LlmResponse(
                text="Writing PA5",
                tool_calls=[
                    ToolCall(
                        id="tc_cancel_1",
                        name="write_memory",
                        arguments={"address": "0x42020014", "values": [0x80], "width": 32},
                    )
                ],
                finish_reason="tool_calls",
            ),
            LlmResponse(text="Should not execute step 2", finish_reason="stop"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            probe, pm, _provider, guard, runtime, bridge = self._create_env(tmp, responses=responses)
            in_stream = _QueueTextIn()
            out_stream = _QueueTextOut()
            t = threading.Thread(
                target=lambda: serve_stdio(bridge, in_stream=in_stream, out_stream=out_stream, redirect_stdout=False),
                name="test-rpc-phase1",
                daemon=True,
            )
            t.start()
            master_fd: int | None = None
            slave_fd: int | None = None
            try:
                ready = out_stream.next_frame()
                self.assertEqual(ready["event"], "ready")

                # 1. cancel when idle
                in_stream.push_line({"id": "c0", "method": "cancel"})
                c0 = out_stream.wait_for(lambda f: f.get("id") == "c0")
                self.assertTrue(c0["ok"])
                self.assertFalse(c0["result"]["cancelled"])

                # 2. cancel while waiting on approval
                in_stream.push_line({"id": "c1", "method": "chat", "params": {"message": "Write PA5"}})
                self.assertTrue(out_stream.wait_for(lambda f: f.get("id") == "c1")["ok"])
                out_stream.wait_for(lambda f: f.get("event") == "approval_required")

                in_stream.push_line({"id": "c2", "method": "cancel"})
                c2 = out_stream.wait_for(lambda f: f.get("id") == "c2")
                self.assertTrue(c2["ok"])
                self.assertTrue(c2["result"]["cancelled"])

                done = out_stream.wait_for(lambda f: f.get("event") == "turn_completed")
                self.assertTrue(done["data"]["aborted"])
                self.assertTrue(done["data"]["cancelled"])
                self.assertIsNone(done["data"]["error"])
                self.assertEqual(probe.read_u32(0x42020014, 1), [0x00000000])

                # 3. logs_since (RTT + UART) and rtt_stop / uart_stop
                runtime.rtt_collector.buffer.feed_bytes(b"rtt line 1\nrtt line 2\nrtt line 3\n")
                in_stream.push_line({"id": "l1", "method": "logs_since", "params": {"source": "rtt", "after_seq": 0, "limit": 99999}})
                l1 = out_stream.wait_for(lambda f: f.get("id") == "l1")
                self.assertTrue(l1["ok"], msg=l1)
                self.assertEqual(l1["result"]["source"], "rtt")
                self.assertEqual(len(l1["result"]["entries"]), 3)
                self.assertEqual(l1["result"]["last_seq"], 3)
                self.assertEqual(l1["result"]["dropped"], 0)

                in_stream.push_line({"id": "l2", "method": "logs_since", "params": {"source": "rtt", "after_seq": 2, "limit": 0}})
                l2 = out_stream.wait_for(lambda f: f.get("id") == "l2")
                self.assertTrue(l2["ok"], msg=l2)
                self.assertEqual([e["seq"] for e in l2["result"]["entries"]], [3])

                runtime.uart_collector.buffer.feed_text("uart line 1\nuart line 2\n")
                in_stream.push_line({"id": "l3", "method": "logs_since", "params": {"source": "uart", "after_seq": 1}})
                l3 = out_stream.wait_for(lambda f: f.get("id") == "l3")
                self.assertTrue(l3["ok"], msg=l3)
                self.assertEqual([e["text"] for e in l3["result"]["entries"]], ["uart line 2"])

                in_stream.push_line({"id": "rs1", "method": "probe", "params": {"action": "rtt_start", "channel": 0}})
                self.assertTrue(out_stream.wait_for(lambda f: f.get("id") == "rs1")["ok"])
                in_stream.push_line({"id": "rs2", "method": "probe", "params": {"action": "rtt_stop"}})
                rs2 = out_stream.wait_for(lambda f: f.get("id") == "rs2")
                self.assertTrue(rs2["ok"], msg=rs2)
                self.assertEqual(rs2["result"]["action"], "rtt_stop")
                self.assertFalse(rs2["result"]["running"])

                master_fd, slave_fd = pty.openpty()
                slave_path = os.ttyname(slave_fd)
                in_stream.push_line({"id": "us1", "method": "probe", "params": {"action": "uart_start", "port": slave_path, "baudrate": 115200}})
                self.assertTrue(out_stream.wait_for(lambda f: f.get("id") == "us1")["ok"])
                in_stream.push_line({"id": "us2", "method": "probe", "params": {"action": "uart_stop"}})
                us2 = out_stream.wait_for(lambda f: f.get("id") == "us2")
                self.assertTrue(us2["ok"], msg=us2)
                self.assertEqual(us2["result"]["action"], "uart_stop")
                self.assertFalse(us2["result"]["running"])

                audit_ops = {a.operation for a in guard.recent_audit(limit=20)}
                self.assertTrue({"rtt_stop", "uart_stop"}.issubset(audit_ops))

                # 4. list_serial_ports
                with mock.patch("mcu_harness.agent.rpc.list_serial_ports", return_value=[]) as lsp_mock:
                    in_stream.push_line({"id": "sp1", "method": "list_serial_ports"})
                    sp1 = out_stream.wait_for(lambda f: f.get("id") == "sp1")
                self.assertTrue(sp1["ok"])
                self.assertEqual(sp1["result"], {"ports": []})
                self.assertEqual(lsp_mock.call_count, 1)

                # 5. test_llm (mock_llm=True and error path without mutating runtime.provider)
                orig_provider = runtime.provider
                in_stream.push_line({"id": "tl1", "method": "test_llm", "params": {"mock_llm": True, "model": "probe-check"}})
                tl1 = out_stream.wait_for(lambda f: f.get("id") == "tl1")
                self.assertTrue(tl1["ok"])
                self.assertTrue(tl1["result"]["ok"])
                self.assertEqual(tl1["result"]["provider"], "mock")
                self.assertEqual(tl1["result"]["model"], "probe-check")
                self.assertIs(runtime.provider, orig_provider)

                in_stream.push_line({"id": "tl2", "method": "test_llm", "params": {"provider": "unknown_provider_xyz"}})
                tl2 = out_stream.wait_for(lambda f: f.get("id") == "tl2")
                self.assertTrue(tl2["ok"])
                self.assertFalse(tl2["result"]["ok"])
                self.assertIn("Unknown LLM provider", tl2["result"]["error"])

                # 6. export_diagnostics + redaction + >20MB skipping
                log_file = Path(tmp) / "secrets.log"
                log_file.write_text(
                    "openai=sk-1234567890abcdef\n"
                    "google=AIzaSyDummyKey1234567890abcd\n"
                    'api_key = "my_toml_secret"\n'
                    "Authorization: Bearer super_secret_token_999\n",
                    encoding="utf-8",
                )
                huge_file = Path(tmp) / "huge_dump.bin"
                with huge_file.open("wb") as fh:
                    fh.seek(21 * 1024 * 1024)
                    fh.write(b"\x00")

                zip_path = Path(tmp) / "diag.zip"
                in_stream.push_line(
                    {
                        "id": "ed1",
                        "method": "export_diagnostics",
                        "params": {
                            "target_path": str(zip_path),
                            "files": [str(log_file), str(huge_file), str(Path(tmp) / "missing.txt")],
                            "app_info": {
                                "studio_version": "1.0.0",
                                "api_key": "should_be_redacted",
                                "token": "sk-appinfo1234567890",
                            },
                        },
                    }
                )
                ed1 = out_stream.wait_for(lambda f: f.get("id") == "ed1")
                self.assertTrue(ed1["ok"], msg=ed1)
                self.assertTrue(zip_path.is_file())
                self.assertGreater(ed1["result"]["size_bytes"], 0)
                self.assertIn("attachments/secrets.log", ed1["result"]["files_included"])
                self.assertNotIn("attachments/huge_dump.bin", ed1["result"]["files_included"])

                with zipfile.ZipFile(zip_path, "r") as zf:
                    names = set(zf.namelist())
                    self.assertTrue({"app_info.json", "engine_info.json", "audit.json", "attachments/secrets.log"}.issubset(names))
                    eng = json.loads(zf.read("engine_info.json").decode("utf-8"))
                    self.assertNotIn("api_key", eng)
                    self.assertEqual(eng["version"], __version__)
                    self.assertEqual(eng["profile"]["device"], "STM32H563ZI")
                    app_txt = zf.read("app_info.json").decode("utf-8")
                    sec_txt = zf.read("attachments/secrets.log").decode("utf-8")
                    for secret in (
                        "sk-1234567890abcdef",
                        "AIzaSyDummyKey1234567890abcd",
                        "my_toml_secret",
                        "super_secret_token_999",
                        "should_be_redacted",
                        "sk-appinfo1234567890",
                    ):
                        self.assertNotIn(secret, app_txt)
                        self.assertNotIn(secret, sec_txt)
                    self.assertIn("***REDACTED***", sec_txt)

                in_stream.push_line({"id": 99, "method": "shutdown"})
                out_stream.wait_for(lambda f: f.get("id") == 99)
                t.join(timeout=3.0)
            finally:
                in_stream.close()
                t.join(timeout=2.0)
                for fd in (master_fd, slave_fd):
                    if fd is not None:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
                runtime.uart_collector.stop()
                pm.disconnect()


class TestAgentRpcSubprocessE2E(unittest.TestCase):
    def test_subprocess_pipe_mock_llm_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = _create_temp_mcu_project(Path(tmp))
            env = dict(os.environ)
            src_dir = str(_REPO_ROOT / "src")
            env["PYTHONPATH"] = f"{src_dir}:{env['PYTHONPATH']}" if env.get("PYTHONPATH") else src_dir

            proc = subprocess.Popen(
                [sys.executable, "-m", "mcu_harness.agent.rpc", "--mock-llm", str(proj)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
                bufsize=1,
            )
            assert proc.stdin is not None
            assert proc.stdout is not None
            try:
                ready_line = proc.stdout.readline()
                self.assertTrue(ready_line, "Expected ready NDJSON line on stdout")
                ready = json.loads(ready_line)
                self.assertEqual(ready["event"], "ready")
                self.assertEqual(ready["data"]["provider"], "mock")

                def _send(obj: dict[str, Any]) -> None:
                    proc.stdin.write(json.dumps(obj) + "\n")
                    proc.stdin.flush()

                def _read_until(pred: Any) -> dict[str, Any]:
                    while True:
                        line = proc.stdout.readline()
                        self.assertTrue(line, "Unexpected EOF from mcu-rpc stdout")
                        frame = json.loads(line)
                        if pred(frame):
                            return frame

                _send({"id": 1, "method": "ping"})
                pong = _read_until(lambda f: f.get("id") == 1)
                self.assertTrue(pong["ok"])
                self.assertTrue(pong["result"]["pong"])

                _send({"id": 2, "method": "get_state"})
                state = _read_until(lambda f: f.get("id") == 2)
                self.assertTrue(state["ok"])
                self.assertEqual(state["result"]["telemetry"]["telemetry"]["device"], "STM32H563ZI")

                _send({"id": 3, "method": "chat", "params": {"message": "Summarize this project"}})
                c_ack = _read_until(lambda f: f.get("id") == 3)
                self.assertTrue(c_ack["result"]["accepted"])

                done = _read_until(lambda f: f.get("event") == "turn_completed")
                self.assertIsNone(done["data"]["error"])
                self.assertIn("STM32H563ZI", done["data"]["text"])

                _send({"id": 4, "method": "shutdown"})
                sd = _read_until(lambda f: f.get("id") == 4)
                self.assertEqual(sd["result"], {"shutdown": True})

                rc = proc.wait(timeout=5.0)
                self.assertEqual(rc, 0, msg=proc.stderr.read() if proc.stderr else "")
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=2.0)
                for pipe in (proc.stdin, proc.stdout, proc.stderr):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except Exception:
                            pass


class TestDesktopLauncher(unittest.TestCase):
    def test_binary_discovery_missing_binary_and_install_desktop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_repo = Path(tmp) / "repo"
            (fake_repo / "studio" / "resources" / "app").mkdir(parents=True, exist_ok=True)
            (fake_repo / "studio" / "resources" / "app" / "mcu-harness-studio.svg").write_text("<svg/>", encoding="utf-8")

            # 1. 未构建时返回 None（环境变量覆盖为空）
            prev_env = os.environ.get("MCU_HARNESS_STUDIO_BIN")
            os.environ.pop("MCU_HARNESS_STUDIO_BIN", None)
            try:
                self.assertIsNone(find_desktop_binary(repo_root=fake_repo))

                # debug < release < env var 优先级
                dbg = fake_repo / "studio" / "build" / "debug" / "mcu-harness-studio"
                rel = fake_repo / "studio" / "build" / "release" / "mcu-harness-studio"
                env_bin = Path(tmp) / "custom-bin"
                dbg.parent.mkdir(parents=True, exist_ok=True)
                rel.parent.mkdir(parents=True, exist_ok=True)
                dbg.write_text("#!/bin/sh\n", encoding="utf-8")
                self.assertEqual(find_desktop_binary(repo_root=fake_repo), dbg)

                rel.write_text("#!/bin/sh\n", encoding="utf-8")
                self.assertEqual(find_desktop_binary(repo_root=fake_repo), rel)

                env_bin.write_text("#!/bin/sh\n", encoding="utf-8")
                os.environ["MCU_HARNESS_STUDIO_BIN"] = str(env_bin)
                self.assertEqual(find_desktop_binary(repo_root=fake_repo), env_bin)
                os.environ.pop("MCU_HARNESS_STUDIO_BIN", None)

                # install_desktop_entry 写入图标与 .desktop
                data_home = Path(tmp) / "xdg_data"
                icon_dest, desktop_dest = install_desktop_entry(repo_root=fake_repo, data_home=data_home)
                self.assertTrue(icon_dest.is_file())
                self.assertEqual(icon_dest.name, "mcu-harness-studio.svg")
                self.assertTrue(desktop_dest.is_file())
                self.assertEqual(desktop_dest.name, "mcu-harness-studio.desktop")
                desktop_txt = desktop_dest.read_text(encoding="utf-8")
                self.assertIn(f"Exec={rel}", desktop_txt)
                self.assertIn("Name=MCU Harness Studio", desktop_txt)
                self.assertIn("Name[zh_CN]=MCU Harness Studio", desktop_txt)
                self.assertIn("Icon=mcu-harness-studio", desktop_txt)
                self.assertIn("Categories=Development;Electronics;IDE;", desktop_txt)
                self.assertIn("StartupWMClass=mcu-harness-studio", desktop_txt)
            finally:
                if prev_env is not None:
                    os.environ["MCU_HARNESS_STUDIO_BIN"] = prev_env
                else:
                    os.environ.pop("MCU_HARNESS_STUDIO_BIN", None)

        # 未找到二进制时 default main 返回 1 并提示 --build
        parser = build_desktop_parser()
        parsed = parser.parse_args(["--mock-llm", "--mode", "auto_safe"])
        self.assertTrue(parsed.mock_llm)
        self.assertEqual(parsed.mode, "auto_safe")

        stderr_buf = io.StringIO()
        prev_stderr = sys.stderr
        sys.stderr = stderr_buf
        try:
            with mock.patch("mcu_harness.agent.desktop_launcher.find_desktop_binary", return_value=None):
                rc = desktop_main(["--mock-llm"])
        finally:
            sys.stderr = prev_stderr
        self.assertEqual(rc, 1)
        self.assertIn("mcu-desktop --build", stderr_buf.getvalue())

        # --build 调用 cmake --preset release 与 cmake --build --preset release
        with (
            mock.patch("mcu_harness.agent.desktop_launcher.shutil.which", return_value="/usr/bin/cmake"),
            mock.patch("mcu_harness.agent.desktop_launcher.subprocess.run", return_value=mock.Mock(returncode=0)) as build_mock,
        ):
            rc_build = desktop_main(["--build"])
        self.assertEqual(rc_build, 0)
        self.assertEqual(build_mock.call_count, 2)
        self.assertEqual(build_mock.call_args_list[0][0][0], ["cmake", "--preset", "release"])
        self.assertEqual(build_mock.call_args_list[1][0][0], ["cmake", "--build", "--preset", "release"])
        self.assertEqual(build_mock.call_args_list[0][1]["cwd"], str(_REPO_ROOT / "studio"))

        # 二进制存在时将标准参数转发给原生进程
        fake_bin = Path("/tmp/fake-mcu-harness-studio")
        with (
            mock.patch("mcu_harness.agent.desktop_launcher.find_desktop_binary", return_value=fake_bin),
            mock.patch("mcu_harness.agent.desktop_launcher.subprocess.run", return_value=mock.Mock(returncode=0)) as run_mock,
        ):
            rc2 = desktop_main(["--provider", "deepseek", "--model", "deepseek-chat", "--mode", "auto_safe", "--mock-llm"])
        self.assertEqual(rc2, 0)
        called_cmd = run_mock.call_args[0][0]
        self.assertEqual(called_cmd[0], str(fake_bin))
        self.assertIn("--provider", called_cmd)
        self.assertIn("deepseek", called_cmd)
        self.assertIn("--mock-llm", called_cmd)


if __name__ == "__main__":
    unittest.main()
