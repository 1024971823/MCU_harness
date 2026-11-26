"""独立第三方 MCU Agent Harness 的核心 ReAct 运行时、事件流与会话持久化引擎。

职责：
    1. 驱动标准 ReAct 工具调用循环，在每轮开始前刷新零 Token 硬件状态卡片并注入系统提示词。
    2. 强制所有工具调用经由 `HarnessInterlock` 审批互锁门，由宿主设置 `confirmed_by_interlock`，
       大模型无法伪造确认参数。
    3. 维护实时事件订阅流（供 TUI 与 NDJSON RPC 消费），并将对话与事件持久化到
       `<project_dir>/.mcu-harness/sessions/<session_id>.jsonl`。
    4. 提供零 LLM Token 消耗的实时硬件快照 `get_hardware_snapshot()` 与上下文压缩 `compact_history()`。
"""

from __future__ import annotations

import inspect
import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from mcu_harness.agent.context import (
    build_hardware_status_card,
    build_system_prompt,
    expand_slash_command,
    format_slash_help,
)
from mcu_harness.agent.interlock import ApprovalDecision, ApprovalRequest, HarnessInterlock, InterlockOutcome
from mcu_harness.agent.llm import LlmError, LlmProvider
from mcu_harness.agent.registry import ToolContext, ToolRegistry, build_default_registry
from mcu_harness.config.manifest import ProjectProfile, discover_project
from mcu_harness.dwarf import read_variable
from mcu_harness.log import RttLogCollector, UartLogCollector, default_rtt_collector, default_uart_collector
from mcu_harness.probe import ProbeManager, default_probe_manager
from mcu_harness.rtos import detect_rtos, inspect_rtos
from mcu_harness.safety import SafetyGuard, default_safety_guard

_MAX_TOOL_OUTPUT_CHARS = 16384


class _TurnCancelled(Exception):
    """内部异常：在流式回调中检测到取消标志时立即中断 LLM 请求。"""


@dataclass(frozen=True)
class HarnessEvent:
    """运行时对外广播的实时事件（供 TUI / NDJSON RPC 订阅）。"""

    seq: int
    timestamp: float
    kind: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TurnResult:
    """单轮用户对话（含所有工具调用）执行结果。"""

    text: str
    steps: int = 0
    tool_calls_executed: int = 0
    denied_tools: list[str] = field(default_factory=list)
    aborted: bool = False
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# 会话持久化辅助函数（JSONL）
# ---------------------------------------------------------------------------


def _sessions_dir(project_dir: str | Path) -> Path:
    root = Path(project_dir).expanduser().resolve()
    harness_dir = root / ".mcu-harness"
    sessions_dir = harness_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    gi = harness_dir / ".gitignore"
    if not gi.exists():
        try:
            gi.write_text("*\n", encoding="utf-8")
        except OSError:
            pass
    return sessions_dir


def list_sessions(project_dir: str | os.PathLike) -> list[dict[str, Any]]:
    """列出 `<project_dir>/.mcu-harness/sessions/` 下已保存的会话，按最近更新时间倒序。"""
    root = Path(project_dir).expanduser().resolve()
    sessions_dir = root / ".mcu-harness" / "sessions"
    if not sessions_dir.is_dir():
        return []

    out: list[dict[str, Any]] = []
    for fpath in sessions_dir.glob("*.jsonl"):
        try:
            st = fpath.stat()
            msg_count = 0
            preview = ""
            for line in fpath.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                if rec.get("type") == "message":
                    msg_count += 1
                    payload = rec.get("payload", {})
                    if not preview and payload.get("role") == "user":
                        preview = str(payload.get("content", ""))[:80]
            out.append(
                {
                    "session_id": fpath.stem,
                    "path": str(fpath),
                    "message_count": msg_count,
                    "updated_at": st.st_mtime,
                    "preview": preview,
                }
            )
        except Exception:
            continue
    out.sort(key=lambda item: item["updated_at"], reverse=True)
    return out


def load_session(project_dir: str | os.PathLike, session_id: str) -> dict[str, Any]:
    """从 JSONL 加载历史会话的消息与事件列表。"""
    root = Path(project_dir).expanduser().resolve()
    clean_id = Path(session_id).name
    if clean_id.endswith(".jsonl"):
        clean_id = clean_id[:-6]
    fpath = root / ".mcu-harness" / "sessions" / f"{clean_id}.jsonl"
    if not fpath.is_file():
        raise FileNotFoundError(f"Session file not found: {fpath}")

    messages: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for line in fpath.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        rtype = rec.get("type")
        if rtype == "message":
            messages.append(rec.get("payload", {}))
        elif rtype == "event":
            events.append(rec.get("payload", {}))
    return {"session_id": clean_id, "path": str(fpath), "messages": messages, "events": events}


def _truncate_tool_output(raw_json: str, max_chars: int = _MAX_TOOL_OUTPUT_CHARS) -> str:
    if len(raw_json) <= max_chars:
        return raw_json
    head_len = max_chars // 2 - 200
    tail_len = max_chars // 2 - 200
    omitted = len(raw_json) - head_len - tail_len
    return json.dumps(
        {
            "truncated": True,
            "total_chars": len(raw_json),
            "omitted_chars": omitted,
            "head": raw_json[:head_len],
            "tail": raw_json[-tail_len:],
        },
        ensure_ascii=False,
    )


class AgentRuntime:
    """独立第三方 MCU Agent 主循环运行时。"""

    def __init__(
        self,
        *,
        project_dir: str | os.PathLike = ".",
        provider: LlmProvider,
        registry: ToolRegistry | None = None,
        interlock: HarnessInterlock | None = None,
        probe_manager: ProbeManager | None = None,
        rtt_collector: RttLogCollector | None = None,
        uart_collector: UartLogCollector | None = None,
        safety_guard: SafetyGuard | None = None,
        session_id: str | None = None,
        max_steps: int = 20,
        persist_sessions: bool = True,
    ) -> None:
        self.project_dir = str(Path(project_dir).expanduser().resolve())
        self.provider = provider
        self.registry = registry or build_default_registry()
        self.safety_guard = safety_guard or default_safety_guard
        self.interlock = interlock or HarnessInterlock(safety_guard=self.safety_guard)
        self.probe_manager = probe_manager or default_probe_manager
        if rtt_collector is not None:
            self.rtt_collector = rtt_collector
        elif self.probe_manager is default_probe_manager:
            self.rtt_collector = default_rtt_collector
        else:
            self.rtt_collector = RttLogCollector(self.probe_manager)
        self.uart_collector = uart_collector or default_uart_collector
        self.max_steps = max(1, int(max_steps))
        self.persist_sessions = persist_sessions

        self.session_id = session_id or f"sess-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self.messages: list[dict[str, Any]] = []
        self._events: list[HarnessEvent] = []
        self._next_seq = 1
        self._sub_lock = threading.Lock()
        self._subscribers: list[Callable[[HarnessEvent], None]] = []
        self._turn_lock = threading.Lock()
        self._cancel_event = threading.Event()
        self.profile: ProjectProfile | None = self._refresh_profile()

        if session_id:
            try:
                loaded = load_session(self.project_dir, self.session_id)
                self.messages = list(loaded["messages"])
            except FileNotFoundError:
                pass

    def _refresh_profile(self) -> ProjectProfile | None:
        try:
            self.profile = discover_project(self.project_dir)
        except Exception:
            self.profile = None
        return self.profile

    def subscribe(self, callback: Callable[[HarnessEvent], None]) -> Callable[[], None]:
        """订阅运行时实时事件，返回取消订阅函数。"""
        with self._sub_lock:
            self._subscribers.append(callback)

        def _unsubscribe() -> None:
            with self._sub_lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return _unsubscribe

    def recent_events(self, limit: int = 100) -> list[HarnessEvent]:
        with self._sub_lock:
            return list(self._events[-max(1, limit) :])

    def _append_jsonl(self, record_type: str, payload: dict[str, Any]) -> None:
        if not self.persist_sessions:
            return
        try:
            sdir = _sessions_dir(self.project_dir)
            fpath = sdir / f"{self.session_id}.jsonl"
            line = json.dumps({"type": record_type, "payload": payload}, ensure_ascii=False)
            with fpath.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass

    def _emit(self, kind: str, data: dict[str, Any] | None = None) -> HarnessEvent:
        with self._sub_lock:
            ev = HarnessEvent(
                seq=self._next_seq,
                timestamp=time.time(),
                kind=kind,
                data=dict(data or {}),
            )
            self._next_seq += 1
            self._events.append(ev)
            subs = list(self._subscribers)
        self._append_jsonl("event", ev.to_dict())
        for cb in subs:
            try:
                cb(ev)
            except Exception:
                pass
        return ev

    def _record_message(self, msg: dict[str, Any]) -> None:
        self.messages.append(msg)
        self._append_jsonl("message", msg)

    def compact_history(self) -> dict[str, Any]:
        """压缩历史对话以释放上下文窗口，保留最近 4 条消息。"""
        if len(self.messages) <= 4:
            return {
                "compacted": False,
                "previous_count": len(self.messages),
                "message_count": len(self.messages),
            }

        older = self.messages[:-4]
        recent = self.messages[-4:]
        digest_lines: list[str] = []
        for idx, m in enumerate(older, 1):
            role = m.get("role", "unknown")
            if role == "tool":
                tname = m.get("name", "tool")
                snippet = str(m.get("content", ""))[:120].replace("\n", " ")
                digest_lines.append(f"{idx}. [tool:{tname}] {snippet}")
            elif role == "assistant":
                tcs = [tc.get("name") for tc in m.get("tool_calls", []) if isinstance(tc, dict)]
                txt = str(m.get("content", ""))[:140].replace("\n", " ")
                tc_part = f" calls={tcs}" if tcs else ""
                digest_lines.append(f"{idx}. [assistant]{tc_part} {txt}")
            else:
                txt = str(m.get("content", ""))[:140].replace("\n", " ")
                digest_lines.append(f"{idx}. [{role}] {txt}")

        digest_msg = {
            "role": "user",
            "content": "[Compacted conversation digest]\n" + "\n".join(digest_lines),
        }
        prev_count = len(self.messages)
        self.messages = [digest_msg, *recent]
        self._append_jsonl("message", {"role": "system", "content": f"Compacted {prev_count} -> {len(self.messages)}"})
        return {
            "compacted": True,
            "previous_count": prev_count,
            "message_count": len(self.messages),
        }

    def _handle_meta_command(self, cmd: str, args: str) -> TurnResult:
        if cmd == "status":
            card, _ = build_hardware_status_card(
                self.project_dir,
                probe_manager=self.probe_manager,
                rtt_collector=self.rtt_collector,
                uart_collector=self.uart_collector,
                safety_guard=self.safety_guard,
                profile=self._refresh_profile(),
            )
            return TurnResult(text=card)
        if cmd == "audit":
            entries = self.safety_guard.recent_audit(limit=20)
            if not entries:
                return TurnResult(text="No safety audit entries recorded yet.")
            lines = ["Recent hardware safety audit entries:"]
            for e in entries:
                flag = "ALLOW" if e.allowed else "BLOCK"
                lines.append(f"  #{e.seq} [{flag}/{e.level}] {e.operation}: {e.target} ({e.detail})")
            return TurnResult(text="\n".join(lines))
        if cmd == "compact":
            res = self.compact_history()
            return TurnResult(
                text=f"Compacted history: {res['previous_count']} -> {res['message_count']} messages."
            )
        if cmd == "clear":
            self.messages.clear()
            return TurnResult(text="Conversation history cleared.")
        if cmd == "sessions":
            items = list_sessions(self.project_dir)
            if not items:
                return TurnResult(text="No saved sessions found.")
            lines = [f"Saved sessions in {self.project_dir}:"]
            for item in items[:20]:
                lines.append(
                    f"  - {item['session_id']} ({item['message_count']} msgs) {item['preview']!r}"
                )
            return TurnResult(text="\n".join(lines))
        if cmd == "help":
            return TurnResult(text=format_slash_help())
        if cmd in ("exit", "quit"):
            return TurnResult(text="Bye.", aborted=True)
        return TurnResult(text=f"Unsupported meta command: /{cmd}")

    def _provider_supports_reasoning_delta(self, provider: LlmProvider) -> bool:
        cached = getattr(self, "_cached_reasoning_cap", None)
        if cached is not None and cached[0] is provider:
            return bool(cached[1])
        supports = False
        try:
            sig = inspect.signature(provider.complete)
            for name, param in sig.parameters.items():
                if name == "on_reasoning_delta" or param.kind == inspect.Parameter.VAR_KEYWORD:
                    supports = True
                    break
        except (TypeError, ValueError):
            supports = False
        self._cached_reasoning_cap = (provider, supports)
        return supports

    def request_cancel(self) -> None:
        """请求取消当前正在执行的对话轮次。"""
        self._cancel_event.set()

    def run_turn(
        self,
        user_input: str,
        *,
        approver: Callable[[ApprovalRequest], ApprovalDecision] | None = None,
    ) -> TurnResult:
        """执行单轮用户输入（支持斜杠命令、多轮 ReAct 工具调用与 HITL 安全互锁）。"""
        with self._turn_lock:
            self._cancel_event.clear()
            slash = expand_slash_command(user_input)
            self._emit("turn_started", {"input": user_input, "kind": slash.kind, "command": slash.command})

            if slash.kind == "unknown":
                res = TurnResult(text=slash.error or "Unknown command", error=slash.error)
                self._emit("turn_completed", res.to_dict())
                return res

            if slash.is_local_meta and slash.meta_action is not None:
                res = self._handle_meta_command(slash.meta_action, slash.args)
                self._emit("turn_completed", res.to_dict())
                return res

            self._refresh_profile()
            status_card, _ = build_hardware_status_card(
                self.project_dir,
                probe_manager=self.probe_manager,
                rtt_collector=self.rtt_collector,
                uart_collector=self.uart_collector,
                safety_guard=self.safety_guard,
                profile=self.profile,
            )
            playbook_prompt = slash.expanded_prompt if slash.kind == "playbook" else None
            system_prompt = build_system_prompt(status_card, playbook_prompt=playbook_prompt)

            user_content = user_input.strip() if slash.kind == "playbook" else (slash.expanded_prompt or user_input)
            self._record_message({"role": "user", "content": user_content})

            llm_tools = self.registry.to_llm_schemas()
            steps = 0
            tool_calls_executed = 0
            denied_tools: list[str] = []
            total_usage: dict[str, int] = {}
            final_text = ""

            cancelled = False
            while steps < self.max_steps:
                if self._cancel_event.is_set():
                    cancelled = True
                    break
                steps += 1
                self._emit("llm_request", {"step": steps, "provider": self.provider.provider_name, "model": self.provider.model})

                partial_deltas: list[str] = []
                partial_reasoning: list[str] = []

                def _on_reasoning_delta(
                    delta: str,
                    _step: int = steps,
                    _buf: list[str] = partial_reasoning,
                ) -> None:
                    if self._cancel_event.is_set():
                        raise _TurnCancelled()
                    _buf.append(delta)
                    self._emit("reasoning_delta", {"step": _step, "delta": delta})

                def _on_text_delta(delta: str, _step: int = steps, _buf: list[str] = partial_deltas) -> None:
                    if self._cancel_event.is_set():
                        raise _TurnCancelled()
                    _buf.append(delta)
                    self._emit("text_delta", {"step": _step, "delta": delta})

                try:
                    complete_kwargs: dict[str, Any] = {
                        "system": system_prompt,
                        "tools": llm_tools,
                        "on_text_delta": _on_text_delta,
                    }
                    if self._provider_supports_reasoning_delta(self.provider):
                        complete_kwargs["on_reasoning_delta"] = _on_reasoning_delta
                    resp = self.provider.complete(
                        self.messages,
                        **complete_kwargs,
                    )
                except _TurnCancelled:
                    if partial_deltas or partial_reasoning:
                        final_text = "".join(partial_deltas)
                        cancel_msg: dict[str, Any] = {"role": "assistant", "content": final_text}
                        if partial_reasoning:
                            cancel_msg["reasoning"] = "".join(partial_reasoning)
                        self._record_message(cancel_msg)
                    cancelled = True
                    break
                except LlmError as exc:
                    res = TurnResult(
                        text=final_text,
                        steps=steps,
                        tool_calls_executed=tool_calls_executed,
                        denied_tools=denied_tools,
                        error=str(exc),
                        usage=total_usage,
                    )
                    self._emit("turn_error", {"error": str(exc)})
                    return res

                for k, v in (resp.usage or {}).items():
                    if isinstance(v, int):
                        total_usage[k] = total_usage.get(k, 0) + v

                if self._cancel_event.is_set():
                    reasoning_val = getattr(resp, "reasoning", "") or "".join(partial_reasoning)
                    reasoning_blocks_val = getattr(resp, "reasoning_blocks", None) or []
                    if resp.text or reasoning_val or reasoning_blocks_val:
                        final_text = resp.text or ""
                        cancel_msg = {"role": "assistant", "content": final_text}
                        if reasoning_val:
                            cancel_msg["reasoning"] = reasoning_val
                        if reasoning_blocks_val:
                            cancel_msg["reasoning_blocks"] = [dict(b) for b in reasoning_blocks_val]
                        self._record_message(cancel_msg)
                    cancelled = True
                    break

                assistant_msg: dict[str, Any] = {"role": "assistant", "content": resp.text or ""}
                if resp.tool_calls:
                    assistant_msg["tool_calls"] = [tc.to_dict() for tc in resp.tool_calls]
                reasoning_val = getattr(resp, "reasoning", "") or "".join(partial_reasoning)
                if reasoning_val:
                    assistant_msg["reasoning"] = reasoning_val
                reasoning_blocks_val = getattr(resp, "reasoning_blocks", None) or []
                if reasoning_blocks_val:
                    assistant_msg["reasoning_blocks"] = [dict(b) for b in reasoning_blocks_val]
                self._record_message(assistant_msg)
                final_text = resp.text or ""

                if not resp.tool_calls:
                    res = TurnResult(
                        text=final_text,
                        steps=steps,
                        tool_calls_executed=tool_calls_executed,
                        denied_tools=denied_tools,
                        usage=total_usage,
                    )
                    self._emit("turn_completed", res.to_dict())
                    return res

                for tc in resp.tool_calls:
                    if self._cancel_event.is_set():
                        cancelled = True
                        break
                    self._emit("tool_call", {"step": steps, "id": tc.id, "name": tc.name, "arguments": tc.arguments})
                    spec = self.registry.get(tc.name)
                    if spec is None:
                        err_payload = json.dumps({"error": f"Unknown tool: {tc.name}"}, ensure_ascii=False)
                        self._emit("tool_result", {"id": tc.id, "name": tc.name, "ok": False, "error": f"Unknown tool: {tc.name}"})
                        self._record_message({"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": err_payload})
                        continue

                    def _wrapped_approver(req: ApprovalRequest) -> ApprovalDecision:
                        self._emit("approval_required", req.to_dict())
                        if approver is None:
                            dec = ApprovalDecision(approved=False, scope="deny", reason="No interactive approver")
                        else:
                            dec = approver(req)
                        self._emit("approval_resolved", {"request_id": req.request_id, **dec.to_dict()})
                        return dec

                    outcome: InterlockOutcome = self.interlock.authorize(
                        spec.name,
                        spec.risk_level,
                        tc.arguments,
                        project_dir=self.project_dir,
                        profile=self.profile,
                        approver=_wrapped_approver if approver is not None else None,
                    )

                    if not outcome.allowed:
                        denied_tools.append(spec.name)
                        if outcome.request is not None and approver is None:
                            self._emit("approval_required", outcome.request.to_dict())
                        deny_payload = json.dumps(
                            {
                                "allowed": False,
                                "hard_denied": outcome.hard_denied,
                                "error": outcome.reason,
                            },
                            ensure_ascii=False,
                        )
                        self._emit(
                            "tool_result",
                            {
                                "id": tc.id,
                                "name": tc.name,
                                "ok": False,
                                "denied": True,
                                "hard_denied": outcome.hard_denied,
                                "error": outcome.reason,
                            },
                        )
                        self._record_message(
                            {"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": deny_payload}
                        )
                        continue

                    confirmed_flag = bool(
                        outcome.decision is not None and outcome.decision.approved and outcome.decision.scope != "deny"
                    )
                    ctx = ToolContext(
                        project_dir=self.project_dir,
                        probe_manager=self.probe_manager,
                        rtt_collector=self.rtt_collector,
                        uart_collector=self.uart_collector,
                        safety_guard=self.safety_guard,
                        confirmed_by_interlock=confirmed_flag,
                        profile=self._refresh_profile(),
                    )
                    try:
                        raw_result = spec.handler(tc.arguments, ctx)
                        tool_calls_executed += 1
                        serialized = _truncate_tool_output(json.dumps(raw_result, ensure_ascii=False))
                        ok_flag = "error" not in raw_result
                    except Exception as exc:
                        raw_result = {"error": f"{type(exc).__name__}: {exc}"}
                        serialized = json.dumps(raw_result, ensure_ascii=False)
                        ok_flag = False

                    self._emit(
                        "tool_result",
                        {
                            "id": tc.id,
                            "name": tc.name,
                            "ok": ok_flag,
                            "result": raw_result if len(serialized) <= 4096 else {"summary": "truncated"},
                        },
                    )
                    self._record_message(
                        {"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": serialized}
                    )

                if cancelled or self._cancel_event.is_set():
                    cancelled = True
                    break

            if cancelled:
                res = TurnResult(
                    text=final_text,
                    steps=steps,
                    tool_calls_executed=tool_calls_executed,
                    denied_tools=denied_tools,
                    aborted=True,
                    error=None,
                    usage=total_usage,
                )
                self._emit("turn_completed", {**res.to_dict(), "cancelled": True})
                return res

            res = TurnResult(
                text=final_text,
                steps=steps,
                tool_calls_executed=tool_calls_executed,
                denied_tools=denied_tools,
                error=f"Reached max_steps limit ({self.max_steps})",
                usage=total_usage,
            )
            self._emit("turn_completed", res.to_dict())
            return res

    def get_hardware_snapshot(self, watch_exprs: list[str] | None = None) -> dict[str, Any]:
        """零 LLM Token 开销获取当前完整硬件实时遥测快照（供 TUI 状态栏与桌面工作台使用）。"""
        prof = self._refresh_profile()
        card_md, telemetry = build_hardware_status_card(
            self.project_dir,
            probe_manager=self.probe_manager,
            rtt_collector=self.rtt_collector,
            uart_collector=self.uart_collector,
            safety_guard=self.safety_guard,
            profile=prof,
        )

        rtt_res, rtt_st = self.rtt_collector.read(tail_lines=30, auto_start=False)
        uart_res, uart_st = self.uart_collector.read(tail_lines=30, auto_start=False)

        watch_values: dict[str, Any] = {}
        rtos_snapshot: dict[str, Any] | None = None
        elf_path = prof.paths.elf_path if prof else None

        try:
            with self.probe_manager.session_if_connected() as probe:
                if probe is not None and elf_path and Path(elf_path).is_file():
                    if watch_exprs:
                        for expr in watch_exprs:
                            try:
                                val = read_variable(probe, elf_path, expr)
                                watch_values[expr] = val.to_dict()
                            except Exception as exc:
                                watch_values[expr] = {"error": str(exc)}
                    try:
                        rtos_kind = detect_rtos(elf_path)
                        if rtos_kind is not None:
                            rtos_snapshot = inspect_rtos(probe, elf_path, rtos=rtos_kind).to_dict()
                    except Exception as exc:
                        rtos_snapshot = {"error": str(exc)}
                elif watch_exprs:
                    for expr in watch_exprs:
                        watch_values[expr] = {"error": "Probe disconnected or ELF unavailable"}
        except Exception as exc:
            telemetry["probe_snapshot_error"] = str(exc)

        return {
            "session_id": self.session_id,
            "provider": self.provider.provider_name,
            "model": self.provider.model,
            "interlock_mode": self.interlock.mode,
            "status_card": card_md,
            "telemetry": telemetry,
            "logs": {
                "rtt": {"status": rtt_st.to_dict(), **rtt_res.to_dict()},
                "uart": {"status": uart_st.to_dict(), **uart_res.to_dict()},
            },
            "watch": watch_values,
            "rtos": rtos_snapshot,
            "audit": [e.to_dict() for e in self.safety_guard.recent_audit(limit=15)],
        }
