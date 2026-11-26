"""独立第三方 MCU Agent Harness NDJSON stdio RPC 桥接器（`mcu-rpc`，纯 Python 标准库）。

职责：
    1. 为 MCU Harness Studio 原生 Qt 6 桌面前端及其他本地客户端提供按行分隔的 NDJSON 双向通信协议。
    2. 在 `serve_stdio()` 启动时执行 stdout 隔离：保存真实协议通道 `proto_out`，将 `sys.stdout`
       重定向至 `sys.stderr`，确保任何意外 `print()` 不会污染 NDJSON 帧流；`finally` 恢复。
    3. 订阅 `AgentRuntime` 实时事件流并异步推送事件帧；支持后台线程执行 ReAct 对话与
       可挂起/唤醒的 HITL 安全审批弹窗（`_PendingApproval`），以及零 Token 硬件控制与配置热更新。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence, TextIO

from mcu_harness import __version__
from mcu_harness.agent.context import PLAYBOOKS
from mcu_harness.agent.interlock import (
    VALID_APPROVAL_SCOPES,
    VALID_INTERLOCK_MODES,
    ApprovalDecision,
    ApprovalRequest,
    HarnessInterlock,
)
from mcu_harness.agent.llm import LlmError, LlmProvider, create_provider
from mcu_harness.agent.runtime import AgentRuntime, HarnessEvent, list_sessions, load_session
from mcu_harness.agent.tui import _build_demo_mock_provider
from mcu_harness.log.uart_collector import list_serial_ports

_MAX_DIAGNOSTIC_ATTACHMENT_BYTES = 20 * 1024 * 1024

_RE_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_\-]{12,}"), "***REDACTED***"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), "***REDACTED***"),
    (re.compile(r'(?i)("?api_key"?\s*[=:]\s*)(["\']?)([^"\'\n,}]+)'), r"\1\2***REDACTED***"),
    (re.compile(r'(?i)(Authorization"?\s*:\s*"?Bearer\s+)[^\s"]+'), r"\1***REDACTED***"),
)


def _redact_secrets(text: str) -> str:
    """对诊断导出的文本与 JSON 中的常见 API Key / Bearer Token 进行脱敏替换。"""
    out = text
    for pattern, repl in _RE_REDACT_PATTERNS:
        out = pattern.sub(repl, out)
    return out


@dataclass
class _PendingApproval:
    request: ApprovalRequest
    event: threading.Event = field(default_factory=threading.Event)
    decision: ApprovalDecision | None = None
    created_at: float = field(default_factory=time.time)


class RpcError(RuntimeError):
    """RPC 请求参数、状态或执行错误。"""


def _toml_escape(value: str) -> str:
    """将字符串安全转义为 TOML 基本字符串内容（不含外层双引号）。"""
    out: list[str] = []
    for ch in value:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\f":
            out.append("\\f")
        elif ch == "\r":
            out.append("\\r")
        elif code < 0x20 or code == 0x7F:
            out.append(f"\\u{code:04X}")
        else:
            out.append(ch)
    return "".join(out)


def _write_user_agent_config(
    target_path: Path,
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> Path:
    """将 `[agent]` 配置写入 TOML 文件并强制设置 `0o600` 权限。"""
    resolved = target_path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)

    lines = ["[agent]"]
    for key, val in (
        ("provider", provider),
        ("model", model),
        ("base_url", base_url),
        ("api_key", api_key),
    ):
        if val is not None:
            sval = str(val).strip()
            if sval:
                lines.append(f'{key} = "{_toml_escape(sval)}"')

    content = "\n".join(lines) + "\n"
    fd = os.open(str(resolved), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
    finally:
        try:
            os.chmod(resolved, 0o600)
        except OSError:
            pass
    return resolved


class RpcBridge:
    """将 `AgentRuntime` 封装为线程安全的 NDJSON RPC 控制器。"""

    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        approval_timeout_s: float = 300.0,
    ) -> None:
        self.runtime = runtime
        self.approval_timeout_s = float(approval_timeout_s)

        self._state_lock = threading.Lock()
        self._watch_exprs: list[str] = []
        self._pending_approvals: dict[str, _PendingApproval] = {}

        self._turn_running = False
        self._turn_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._shutdown_requested = False

        self._event_sink: Callable[[dict[str, Any]], None] | None = None
        self._unsubscribe_runtime: Callable[[], None] | None = None

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_requested or self._stop_event.is_set()

    def attach_event_sink(self, sink: Callable[[dict[str, Any]], None]) -> None:
        """绑定异步事件发送回调并订阅 `AgentRuntime` 事件流。"""
        with self._state_lock:
            self._event_sink = sink
            if self._unsubscribe_runtime is None:
                self._unsubscribe_runtime = self.runtime.subscribe(self._on_runtime_event)

    def detach_event_sink(self) -> None:
        """取消订阅 `AgentRuntime` 事件流并清除事件发送回调。"""
        with self._state_lock:
            unsub = self._unsubscribe_runtime
            self._unsubscribe_runtime = None
            self._event_sink = None
        if unsub is not None:
            try:
                unsub()
            except Exception:
                pass

    def _on_runtime_event(self, ev: HarnessEvent) -> None:
        with self._state_lock:
            sink = self._event_sink
        if sink is not None:
            sink(
                {
                    "event": ev.kind,
                    "seq": ev.seq,
                    "timestamp": ev.timestamp,
                    "data": ev.data,
                }
            )

    def _build_telemetry(self, watch_exprs_override: Any = None) -> dict[str, Any]:
        with self._state_lock:
            if isinstance(watch_exprs_override, list):
                cleaned = [str(x).strip() for x in watch_exprs_override if str(x).strip()]
                self._watch_exprs = cleaned
            watch_exprs = list(self._watch_exprs)
            turn_running = self._turn_running
            pending_count = sum(1 for pa in self._pending_approvals.values() if pa.decision is None)

        snap = self.runtime.get_hardware_snapshot(watch_exprs=watch_exprs)
        snap["watch_exprs"] = watch_exprs
        snap["turn_running"] = turn_running
        snap["pending_approvals_count"] = pending_count
        return snap

    def _list_pending_approvals(self) -> list[dict[str, Any]]:
        with self._state_lock:
            return [
                {
                    **pa.request.to_dict(),
                    "created_at": pa.created_at,
                }
                for pa in self._pending_approvals.values()
                if pa.decision is None
            ]

    # ------------------------------------------------------------------
    # RPC 请求分发
    # ------------------------------------------------------------------

    def dispatch(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """执行单个 RPC 方法并返回结果字典；参数或状态错误时抛出 `RpcError` / `ValueError`。"""
        clean_params = dict(params) if params is not None else {}
        handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "ping": self._rpc_ping,
            "get_state": self._rpc_get_state,
            "telemetry": self._rpc_telemetry,
            "snapshot": self._rpc_telemetry,
            "get_snapshot": self._rpc_telemetry,
            "playbooks": self._rpc_playbooks,
            "set_mode": self._rpc_set_mode,
            "chat": self._rpc_chat,
            "cancel": self._rpc_cancel,
            "logs_since": self._rpc_logs_since,
            "list_serial_ports": self._rpc_list_serial_ports,
            "resolve_approval": self._rpc_resolve_approval,
            "approve": self._rpc_resolve_approval,
            "list_approvals": self._rpc_list_approvals,
            "watch": self._rpc_watch,
            "probe": self._rpc_probe,
            "configure": self._rpc_configure,
            "test_llm": self._rpc_test_llm,
            "export_diagnostics": self._rpc_export_diagnostics,
            "sessions": self._rpc_sessions,
            "shutdown": self._rpc_shutdown,
        }
        handler = handlers.get(method)
        if handler is None:
            raise RpcError(f"Unknown RPC method: {method!r}")
        return handler(clean_params)

    def handle_request(self, raw_req: Any) -> dict[str, Any]:
        """校验并执行已解析的 JSON 请求对象，返回标准响应字典。"""
        if not isinstance(raw_req, dict):
            return {"id": None, "ok": False, "error": "RPC request must be a JSON object"}

        req_id = raw_req.get("id")
        method = raw_req.get("method")
        if not isinstance(method, str) or not method.strip():
            return {"id": req_id, "ok": False, "error": "Missing or invalid 'method'"}

        params = raw_req.get("params")
        if params is None:
            params = {}
        elif not isinstance(params, dict):
            return {"id": req_id, "ok": False, "error": "'params' must be a JSON object"}

        try:
            result = self.dispatch(method.strip(), params)
        except Exception as exc:
            return {"id": req_id, "ok": False, "error": str(exc)}
        return {"id": req_id, "ok": True, "result": result}

    def _rpc_ping(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        return {"pong": True, "version": __version__}

    def _rpc_get_state(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        with self._state_lock:
            watch_exprs = list(self._watch_exprs)
            turn_running = self._turn_running
        return {
            "project_dir": self.runtime.project_dir,
            "session_id": self.runtime.session_id,
            "provider": self.runtime.provider.provider_name,
            "model": self.runtime.provider.model,
            "interlock_mode": self.runtime.interlock.mode,
            "playbooks": [spec.to_dict() for spec in PLAYBOOKS.values()],
            "watch_exprs": watch_exprs,
            "turn_running": turn_running,
            "pending_approvals": self._list_pending_approvals(),
            "messages": list(self.runtime.messages),
            "sessions": list_sessions(self.runtime.project_dir),
            "telemetry": self._build_telemetry(),
        }

    def _rpc_telemetry(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._build_telemetry(watch_exprs_override=params.get("watch_exprs"))

    def _rpc_playbooks(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        return {"playbooks": [spec.to_dict() for spec in PLAYBOOKS.values()]}

    def _rpc_set_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        raw_mode = params.get("mode") if params.get("mode") is not None else params.get("interlock_mode")
        if raw_mode is None or not str(raw_mode).strip():
            raise RpcError("Missing 'mode'")
        norm_mode = str(raw_mode).strip().lower()
        if norm_mode not in VALID_INTERLOCK_MODES:
            raise RpcError(
                f"Unsupported interlock mode {raw_mode!r}; expected one of {VALID_INTERLOCK_MODES}"
            )
        self.runtime.interlock.mode = norm_mode
        return {"ok": True, "mode": norm_mode, "interlock_mode": norm_mode}

    def _rpc_chat(self, params: dict[str, Any]) -> dict[str, Any]:
        message = str(params.get("message") or "").strip()
        if not message:
            raise RpcError("Missing or empty 'message'")

        with self._state_lock:
            if self._stop_event.is_set():
                raise RpcError("RPC bridge is shutting down")
            if self._turn_running:
                raise RpcError("A chat turn is already running")
            self._turn_running = True
            t = threading.Thread(
                target=self._run_chat_turn,
                args=(message,),
                name="mcu-rpc-turn",
                daemon=True,
            )
            self._turn_thread = t
            t.start()

        return {
            "accepted": True,
            "session_id": self.runtime.session_id,
            "message": message,
        }

    def _run_chat_turn(self, message: str) -> None:
        def rpc_approver(req: ApprovalRequest) -> ApprovalDecision:
            if self._stop_event.is_set():
                return ApprovalDecision(approved=False, scope="deny", reason="RPC bridge stopped")
            pending = _PendingApproval(request=req)
            with self._state_lock:
                self._pending_approvals[req.request_id] = pending
            signaled = pending.event.wait(timeout=self.approval_timeout_s)
            with self._state_lock:
                self._pending_approvals.pop(req.request_id, None)
                dec = pending.decision
            if not signaled or dec is None:
                return ApprovalDecision(
                    approved=False,
                    scope="deny",
                    reason="HITL approval timed out in RPC bridge",
                )
            return dec

        try:
            self.runtime.run_turn(message, approver=rpc_approver)
        except Exception as exc:
            self.runtime._emit("turn_error", {"error": f"{type(exc).__name__}: {exc}"})
        finally:
            with self._state_lock:
                self._turn_running = False
                if self._turn_thread is threading.current_thread():
                    self._turn_thread = None

    def _rpc_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        self.runtime.request_cancel()
        with self._state_lock:
            turn_running = self._turn_running
            pending_items = list(self._pending_approvals.values())
            self._pending_approvals.clear()
            for pa in pending_items:
                if pa.decision is None:
                    pa.decision = ApprovalDecision(
                        approved=False,
                        scope="deny",
                        reason="Cancelled by user",
                    )
                pa.event.set()
        return {"cancelled": bool(turn_running)}

    def _rpc_logs_since(self, params: dict[str, Any]) -> dict[str, Any]:
        source = str(params.get("source") or "rtt").strip().lower()
        if source not in ("rtt", "uart"):
            raise RpcError(f"Unknown log source {source!r} (expected 'rtt' or 'uart')")
        raw_after = params.get("after_seq")
        after_seq = int(raw_after) if raw_after is not None else 0
        raw_limit = params.get("limit")
        limit = int(raw_limit) if raw_limit is not None else 2000
        limit = max(1, min(5000, limit))

        collector = self.runtime.rtt_collector if source == "rtt" else self.runtime.uart_collector
        res, st = collector.read(tail_lines=limit, since_seq=after_seq, auto_start=False)
        return {
            "source": source,
            "entries": [e.to_dict() for e in res.entries],
            "last_seq": res.newest_seq if res.newest_seq is not None else after_seq,
            "dropped": res.dropped_since_requested,
            "status": st.to_dict(),
        }

    def _rpc_list_serial_ports(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        return {"ports": [p.to_dict() for p in list_serial_ports()]}

    def _rpc_resolve_approval(self, params: dict[str, Any]) -> dict[str, Any]:
        req_id = str(params.get("request_id") or "").strip()
        if not req_id:
            raise RpcError("Missing 'request_id'")
        if "approved" not in params:
            raise RpcError("Missing required field 'approved'")

        approved = bool(params.get("approved"))
        default_scope = "once" if approved else "deny"
        scope = str(params.get("scope") or default_scope).strip()
        if scope not in VALID_APPROVAL_SCOPES:
            raise RpcError(f"Invalid scope {scope!r}; expected one of {VALID_APPROVAL_SCOPES}")
        if not approved:
            scope = "deny"
        reason = str(params.get("reason") or "")

        decision = ApprovalDecision(approved=approved, scope=scope, reason=reason)
        with self._state_lock:
            pending = self._pending_approvals.pop(req_id, None)
            if pending is None:
                raise RpcError(f"Approval request not found or already resolved: {req_id}")
            pending.decision = decision
            pending.event.set()

        return {
            "ok": True,
            "request_id": req_id,
            "decision": decision.to_dict(),
        }

    def _rpc_list_approvals(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        items = self._list_pending_approvals()
        return {"count": len(items), "approvals": items}

    def _rpc_watch(self, params: dict[str, Any]) -> dict[str, Any]:
        action = str(params.get("action") or "list").strip().lower()
        expr = str(params.get("expression") or "").strip()

        with self._state_lock:
            if action == "list":
                return {"action": "list", "watch_exprs": list(self._watch_exprs)}
            if action == "add":
                if not expr:
                    raise RpcError("Missing 'expression' for watch add")
                if expr not in self._watch_exprs:
                    self._watch_exprs.append(expr)
                return {
                    "ok": True,
                    "action": "add",
                    "expression": expr,
                    "watch_exprs": list(self._watch_exprs),
                }
            if action == "remove":
                if not expr:
                    raise RpcError("Missing 'expression' for watch remove")
                removed = expr in self._watch_exprs
                if removed:
                    self._watch_exprs.remove(expr)
                return {
                    "ok": True,
                    "action": "remove",
                    "expression": expr,
                    "removed": removed,
                    "watch_exprs": list(self._watch_exprs),
                }
            if action == "clear":
                count = len(self._watch_exprs)
                self._watch_exprs.clear()
                return {
                    "ok": True,
                    "action": "clear",
                    "cleared": count,
                    "watch_exprs": [],
                }

        raise RpcError(f"Unknown watch action {action!r} (expected list/add/remove/clear)")

    def _rpc_probe(self, params: dict[str, Any]) -> dict[str, Any]:
        action = str(params.get("action") or "info").strip().lower()
        prof = self.runtime.profile
        t_cfg = prof.target if prof else None
        eff_dev = params.get("device") or (t_cfg.device if t_cfg else None)
        eff_backend = params.get("backend") or (
            t_cfg.probe_backend if prof and prof.manifest_path else None
        )
        eff_elf = prof.paths.elf_path if prof else None

        if action == "info":
            with self.runtime.probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
                info = probe.info().to_dict()
            self.runtime.safety_guard.record_audit(
                "probe_info",
                str(info.get("device") or eff_dev or "target"),
                level="read_only",
                allowed=True,
                detail="rpc probe info",
            )
            return {"action": "info", **info}

        if action == "connect":
            info = self.runtime.probe_manager.connect(
                backend=eff_backend,
                device=eff_dev,
                interface=params.get("interface") or (t_cfg.interface if t_cfg else None),
                speed=params.get("speed") if params.get("speed") is not None else (t_cfg.speed if t_cfg else None),
                serial_no=params.get("serial_no") or (t_cfg.serial_no if t_cfg else None),
                reconnect=bool(params.get("reconnect", False)),
            ).to_dict()
            self.runtime.safety_guard.record_audit(
                "probe_connect",
                str(info.get("device") or eff_dev or "target"),
                level="hardware_state",
                allowed=True,
                detail=f"rpc probe connect backend={info.get('backend')}",
            )
            return {"action": "connect", **info}

        if action == "disconnect":
            self.runtime.probe_manager.disconnect()
            self.runtime.safety_guard.record_audit(
                "probe_disconnect",
                str(eff_dev or "target"),
                level="hardware_state",
                allowed=True,
                detail="rpc probe disconnect",
            )
            return {"action": "disconnect", "connected": False}

        if action == "halt":
            with self.runtime.probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
                changed = probe.halt()
                halted = probe.is_halted()
                dev_name = probe.info().device
            self.runtime.safety_guard.record_audit(
                "halt",
                dev_name,
                level="hardware_state",
                allowed=True,
                detail=f"rpc probe halt changed={changed}",
            )
            return {"action": "halt", "halted": halted, "changed": changed}

        if action == "resume":
            with self.runtime.probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
                changed = probe.resume()
                halted = probe.is_halted()
                dev_name = probe.info().device
            self.runtime.safety_guard.record_audit(
                "resume",
                dev_name,
                level="hardware_state",
                allowed=True,
                detail=f"rpc probe resume changed={changed}",
            )
            return {"action": "resume", "halted": halted, "changed": changed}

        if action == "reset":
            halt_on_reset = bool(params.get("halt_on_reset", False))
            with self.runtime.probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
                res = probe.reset(halt=halt_on_reset)
                dev_name = probe.info().device
            self.runtime.safety_guard.record_audit(
                "reset",
                dev_name,
                level="hardware_state",
                allowed=True,
                detail=f"rpc probe reset halt={halt_on_reset}",
            )
            return {"action": "reset", **res.to_dict()}

        if action == "rtt_start":
            channel = int(params.get("channel", 0))
            if eff_backend is not None:
                with self.runtime.probe_manager.session(device=eff_dev, backend=eff_backend):
                    pass
            st = self.runtime.rtt_collector.start(
                channel=channel,
                elf_path=eff_elf,
                device=eff_dev,
            )
            self.runtime.safety_guard.record_audit(
                "rtt_start",
                f"ch{channel}",
                level="hardware_state",
                allowed=True,
                detail=f"device={eff_dev or ''}",
            )
            return {"action": "rtt_start", **st.to_dict()}

        if action == "rtt_stop":
            st = self.runtime.rtt_collector.stop()
            self.runtime.safety_guard.record_audit(
                "rtt_stop",
                f"ch{st.channel}",
                level="hardware_state",
                allowed=True,
                detail="rpc probe rtt_stop",
            )
            return {"action": "rtt_stop", **st.to_dict()}

        if action == "rtt_write":
            command = str(params.get("command") or "")
            if not command:
                raise RpcError("Missing 'command' for rtt_write")
            channel = int(params.get("channel", 0))
            if eff_backend is not None:
                with self.runtime.probe_manager.session(device=eff_dev, backend=eff_backend):
                    pass
            self.runtime.rtt_collector.start(channel=channel, elf_path=eff_elf, device=eff_dev)
            with self.runtime.probe_manager.session(device=eff_dev, backend=eff_backend) as probe:
                written = probe.rtt_write(command.encode("utf-8"), channel=channel)
            self.runtime.safety_guard.record_audit(
                "rtt_write",
                f"ch{channel}",
                level="hardware_state",
                allowed=True,
                detail=command[:80],
            )
            return {
                "action": "rtt_write",
                "written_bytes": written,
                "channel": channel,
            }

        if action == "uart_start":
            port = params.get("port")
            baudrate = int(params.get("baudrate", 115200))
            st = self.runtime.uart_collector.start(
                port=str(port) if port else None,
                baudrate=baudrate,
            )
            target_port = str(port or (st.port or "uart"))
            self.runtime.safety_guard.record_audit(
                "uart_start",
                target_port,
                level="hardware_state",
                allowed=True,
                detail=f"baudrate={baudrate}",
            )
            return {"action": "uart_start", **st.to_dict()}

        if action == "uart_stop":
            st = self.runtime.uart_collector.stop()
            self.runtime.safety_guard.record_audit(
                "uart_stop",
                str(st.port or "uart"),
                level="hardware_state",
                allowed=True,
                detail="rpc probe uart_stop",
            )
            return {"action": "uart_stop", **st.to_dict()}

        if action == "uart_write":
            command = str(params.get("command") or "")
            if not command:
                raise RpcError("Missing 'command' for uart_write")
            port = params.get("port")
            baudrate = int(params.get("baudrate", 115200))
            if port:
                self.runtime.uart_collector.start(port=str(port), baudrate=baudrate)
            written = self.runtime.uart_collector.write(command)
            target_port = str(port or (self.runtime.uart_collector.status().port or "uart"))
            self.runtime.safety_guard.record_audit(
                "uart_write",
                target_port,
                level="hardware_state",
                allowed=True,
                detail=command[:80],
            )
            return {
                "action": "uart_write",
                "written_bytes": written,
                "source": "uart",
            }

        raise RpcError(
            f"Unknown probe action {action!r} "
            "(expected info/connect/disconnect/halt/resume/reset/"
            "rtt_start/rtt_stop/rtt_write/uart_start/uart_stop/uart_write)"
        )

    def _rpc_configure(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._state_lock:
            if self._turn_running:
                raise RpcError("Cannot configure while a chat turn is running")

        raw_project_dir = params.get("project_dir")
        target_project_dir = self.runtime.project_dir
        if raw_project_dir is not None and str(raw_project_dir).strip():
            resolved = Path(str(raw_project_dir)).expanduser().resolve()
            if not resolved.is_dir():
                raise RpcError(f"Project directory does not exist: {resolved}")
            target_project_dir = str(resolved)

        raw_mode = params.get("interlock_mode")
        new_mode: str | None = None
        if raw_mode is not None:
            norm_mode = str(raw_mode).strip().lower()
            if norm_mode not in VALID_INTERLOCK_MODES:
                raise RpcError(
                    f"Unsupported interlock mode {raw_mode!r}; expected one of {VALID_INTERLOCK_MODES}"
                )
            new_mode = norm_mode

        mock_llm = params.get("mock_llm")
        provider_arg = params.get("provider")
        model_arg = params.get("model")
        base_url_arg = params.get("base_url")
        api_key_arg = params.get("api_key")

        new_provider: LlmProvider | None = None
        if mock_llm is True:
            new_provider = _build_demo_mock_provider(model=str(model_arg).strip() if model_arg else None)
        elif (
            mock_llm is False
            or any(val is not None for val in (provider_arg, model_arg, base_url_arg, api_key_arg))
        ):
            new_provider = create_provider(
                str(provider_arg).strip() if provider_arg else None,
                model=str(model_arg).strip() if model_arg else None,
                api_key=str(api_key_arg).strip() if api_key_arg else None,
                base_url=str(base_url_arg).strip() if base_url_arg else None,
                project_dir=target_project_dir,
            )

        save_cfg = bool(params.get("save_user_config", False))
        saved_path: Path | None = None
        if save_cfg:
            raw_cfg_path = params.get("config_path")
            cfg_target = (
                Path(str(raw_cfg_path))
                if raw_cfg_path is not None and str(raw_cfg_path).strip()
                else Path.home() / ".config" / "mcu-harness" / "agent.toml"
            )
            eff_provider_obj = new_provider or self.runtime.provider
            eff_provider_name = (
                str(provider_arg).strip()
                if provider_arg
                else eff_provider_obj.provider_name
            )
            eff_model_name = (
                str(model_arg).strip()
                if model_arg
                else eff_provider_obj.model
            )
            eff_base_url = (
                str(base_url_arg).strip()
                if base_url_arg
                else getattr(eff_provider_obj, "base_url", None)
            )
            eff_api_key = (
                str(api_key_arg).strip()
                if api_key_arg
                else getattr(eff_provider_obj, "api_key", None)
            )
            saved_path = _write_user_agent_config(
                cfg_target,
                provider=eff_provider_name,
                model=eff_model_name,
                base_url=eff_base_url,
                api_key=eff_api_key,
            )

        if target_project_dir != self.runtime.project_dir:
            self.runtime.project_dir = target_project_dir
            self.runtime._refresh_profile()
        if new_mode is not None:
            self.runtime.interlock.mode = new_mode
        if new_provider is not None:
            self.runtime.provider = new_provider

        res: dict[str, Any] = {
            "ok": True,
            "project_dir": self.runtime.project_dir,
            "provider": self.runtime.provider.provider_name,
            "model": self.runtime.provider.model,
            "interlock_mode": self.runtime.interlock.mode,
        }
        if saved_path is not None:
            res["saved_config_path"] = str(saved_path)
        return res

    def _rpc_test_llm(self, params: dict[str, Any]) -> dict[str, Any]:
        t0 = time.monotonic()
        try:
            mock_llm = bool(params.get("mock_llm", False))
            provider_arg = params.get("provider")
            model_arg = params.get("model")
            base_url_arg = params.get("base_url")
            api_key_arg = params.get("api_key")

            if mock_llm:
                tmp_provider: LlmProvider = _build_demo_mock_provider(
                    model=str(model_arg).strip() if model_arg else None
                )
            else:
                tmp_provider = create_provider(
                    str(provider_arg).strip() if provider_arg else None,
                    model=str(model_arg).strip() if model_arg else None,
                    api_key=str(api_key_arg).strip() if api_key_arg else None,
                    base_url=str(base_url_arg).strip() if base_url_arg else None,
                    project_dir=self.runtime.project_dir,
                )

            resp = tmp_provider.complete(
                [{"role": "user", "content": "Reply with OK."}],
                system="",
                tools=None,
                max_tokens=8,
            )
            latency_ms = int((time.monotonic() - t0) * 1000)
            return {
                "ok": True,
                "latency_ms": latency_ms,
                "provider": tmp_provider.provider_name,
                "model": tmp_provider.model,
                "reply": (resp.text or "")[:80],
            }
        except Exception as exc:
            latency_ms = int((time.monotonic() - t0) * 1000)
            return {
                "ok": False,
                "error": str(exc),
                "latency_ms": latency_ms,
            }

    def _rpc_export_diagnostics(self, params: dict[str, Any]) -> dict[str, Any]:
        raw_target = str(params.get("target_path") or "").strip()
        if not raw_target:
            raise RpcError("Missing 'target_path'")
        target = Path(raw_target).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        raw_app_info = params.get("app_info")
        app_info = dict(raw_app_info) if isinstance(raw_app_info, dict) else {}

        raw_files = params.get("files")
        file_paths = [str(f) for f in raw_files] if isinstance(raw_files, list) else []

        prof = self.runtime.profile
        profile_summary = {
            "device": prof.target.device if prof else None,
            "core": prof.target.core if prof else None,
            "build_system": prof.build.system if prof else None,
            "elf_path": prof.paths.elf_path if prof else None,
            "svd_path": prof.paths.svd_path if prof else None,
        }
        engine_info = {
            "version": __version__,
            "python_version": sys.version,
            "platform": platform.platform(),
            "project_dir": self.runtime.project_dir,
            "profile": profile_summary,
            "interlock_mode": self.runtime.interlock.mode,
            "provider": self.runtime.provider.provider_name,
            "model": self.runtime.provider.model,
            "session_count": len(list_sessions(self.runtime.project_dir)),
        }
        audit_entries = [e.to_dict() for e in self.runtime.safety_guard.recent_audit(limit=200)]

        files_included: list[str] = []
        used_arcnames: set[str] = set()
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for member_name, obj in (
                ("app_info.json", app_info),
                ("engine_info.json", engine_info),
                ("audit.json", audit_entries),
            ):
                payload_text = _redact_secrets(json.dumps(obj, indent=2, ensure_ascii=False))
                zf.writestr(member_name, payload_text.encode("utf-8"))
                files_included.append(member_name)

            for idx, raw_file in enumerate(file_paths):
                fpath = Path(raw_file).expanduser()
                if not fpath.is_file():
                    continue
                try:
                    fsize = fpath.stat().st_size
                except OSError:
                    continue
                if fsize > _MAX_DIAGNOSTIC_ATTACHMENT_BYTES:
                    continue

                base_name = fpath.name
                arcname = f"attachments/{base_name}"
                if arcname in used_arcnames:
                    arcname = f"attachments/{idx}_{base_name}"
                used_arcnames.add(arcname)

                raw_bytes = fpath.read_bytes()
                if b"\x00" not in raw_bytes:
                    try:
                        decoded = raw_bytes.decode("utf-8")
                        zf.writestr(arcname, _redact_secrets(decoded).encode("utf-8"))
                        files_included.append(arcname)
                        continue
                    except UnicodeDecodeError:
                        pass
                zf.writestr(arcname, raw_bytes)
                files_included.append(arcname)

        return {
            "path": str(target),
            "size_bytes": target.stat().st_size,
            "files_included": files_included,
        }

    def _rpc_sessions(self, params: dict[str, Any]) -> dict[str, Any]:
        action = str(params.get("action") or "list").strip().lower()
        if action == "list":
            return {
                "action": "list",
                "current_session_id": self.runtime.session_id,
                "sessions": list_sessions(self.runtime.project_dir),
            }

        if action == "load":
            with self._state_lock:
                if self._turn_running:
                    raise RpcError("Cannot switch session while a chat turn is running")
            session_id = str(params.get("session_id") or "").strip()
            if not session_id:
                raise RpcError("Missing 'session_id' for sessions load")
            loaded = load_session(self.runtime.project_dir, session_id)
            self.runtime.session_id = loaded["session_id"]
            self.runtime.messages = list(loaded["messages"])
            return {
                "action": "load",
                "session_id": self.runtime.session_id,
                "message_count": len(self.runtime.messages),
                "messages": list(self.runtime.messages),
                "events": list(loaded["events"]),
            }

        if action == "new":
            with self._state_lock:
                if self._turn_running:
                    raise RpcError("Cannot create new session while a chat turn is running")
            custom_id = str(params.get("session_id") or "").strip()
            new_id = custom_id or f"sess-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
            self.runtime.session_id = new_id
            self.runtime.messages = []
            return {
                "action": "new",
                "session_id": self.runtime.session_id,
                "message_count": 0,
            }

        raise RpcError(f"Unknown sessions action {action!r} (expected list/load/new)")

    def _rpc_shutdown(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        self.shutdown()
        return {"shutdown": True}

    def shutdown(self) -> None:
        """优雅关闭 RPC 桥接：拒绝并唤醒所有挂起审批、取消订阅并回收活跃对话线程。"""
        self._shutdown_requested = True
        self._stop_event.set()

        with self._state_lock:
            pending_items = list(self._pending_approvals.values())
            self._pending_approvals.clear()
            for pa in pending_items:
                if pa.decision is None:
                    pa.decision = ApprovalDecision(
                        approved=False,
                        scope="deny",
                        reason="RPC bridge shutting down",
                    )
                pa.event.set()

            unsub = self._unsubscribe_runtime
            self._unsubscribe_runtime = None
            self._event_sink = None
            turn_thread = self._turn_thread

        if unsub is not None:
            try:
                unsub()
            except Exception:
                pass

        cur = threading.current_thread()
        if turn_thread is not None and turn_thread is not cur and turn_thread.is_alive():
            turn_thread.join(timeout=3.0)

        with self._state_lock:
            self._turn_running = False
            if self._turn_thread is None or not self._turn_thread.is_alive():
                self._turn_thread = None


# ---------------------------------------------------------------------------
# Stdio NDJSON 主循环与 CLI 入口
# ---------------------------------------------------------------------------


def serve_stdio(
    bridge: RpcBridge,
    *,
    in_stream: TextIO | None = None,
    out_stream: TextIO | None = None,
    redirect_stdout: bool = True,
) -> int:
    """运行 NDJSON stdio 事件/请求循环，支持严格的 stdout 隔离。"""
    prev_stdout = sys.stdout
    proto_out = out_stream if out_stream is not None else prev_stdout
    proto_in = in_stream if in_stream is not None else sys.stdin

    if redirect_stdout:
        sys.stdout = sys.stderr

    write_lock = threading.Lock()

    def _emit_line(obj: dict[str, Any]) -> None:
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        with write_lock:
            proto_out.write(line)
            proto_out.flush()

    bridge.attach_event_sink(_emit_line)
    try:
        _emit_line(
            {
                "event": "ready",
                "seq": 0,
                "timestamp": time.time(),
                "data": {
                    "session_id": bridge.runtime.session_id,
                    "project_dir": bridge.runtime.project_dir,
                    "provider": bridge.runtime.provider.provider_name,
                    "model": bridge.runtime.provider.model,
                    "interlock_mode": bridge.runtime.interlock.mode,
                },
            }
        )

        while not bridge.shutdown_requested:
            raw_line = proto_in.readline()
            if raw_line == "":
                break
            stripped = raw_line.strip()
            if not stripped:
                continue

            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as exc:
                _emit_line({"id": None, "ok": False, "error": f"Invalid JSON: {exc}"})
                continue

            resp = bridge.handle_request(parsed)
            _emit_line(resp)
    finally:
        bridge.shutdown()
        if redirect_stdout:
            sys.stdout = prev_stdout

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcu-rpc",
        description="MCU Harness NDJSON stdio RPC 桥接后端（供原生桌面端与本地脚本调用）",
    )
    parser.add_argument(
        "project_dir",
        nargs="?",
        default=".",
        help="目标嵌入式工程根目录（默认当前目录）",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="LLM Provider (openai/deepseek/qwen/openrouter/ollama/vllm/anthropic/gemini/mock)",
    )
    parser.add_argument("--model", default=None, help="指定模型名称")
    parser.add_argument("--base-url", default=None, help="自定义 API Base URL")
    parser.add_argument("--api-key", default=None, help="自定义 API Key")
    parser.add_argument(
        "--mode",
        choices=list(VALID_INTERLOCK_MODES),
        default="interactive",
        help="硬件安全互锁模式（默认 interactive）",
    )
    parser.add_argument("--session", default=None, help="恢复指定 ID 的历史会话")
    parser.add_argument("--mock-llm", action="store_true", help="离线演示模式：使用内置智能 MockLlmProvider")
    parser.add_argument("--max-steps", type=int, default=20, help="单轮 ReAct 最大步数（默认 20）")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """`mcu-rpc` 命令行入口。"""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    project_dir = str(Path(args.project_dir).expanduser().resolve())

    if args.mock_llm:
        provider: LlmProvider = _build_demo_mock_provider(model=args.model)
    else:
        try:
            provider = create_provider(
                args.provider,
                model=args.model,
                api_key=args.api_key,
                base_url=args.base_url,
                project_dir=project_dir,
            )
        except LlmError as exc:
            sys.stderr.write(f"LLM configuration error: {exc}\n")
            sys.stderr.flush()
            return 1

    interlock = HarnessInterlock(mode=args.mode)
    runtime = AgentRuntime(
        project_dir=project_dir,
        provider=provider,
        interlock=interlock,
        session_id=args.session,
        max_steps=args.max_steps,
    )
    bridge = RpcBridge(runtime)
    return serve_stdio(bridge)


if __name__ == "__main__":
    raise SystemExit(main())
