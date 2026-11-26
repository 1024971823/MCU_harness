"""独立第三方 MCU Agent Harness 终端交互界面（`mcu-agent` TUI，纯 Python 标准库）。

职责：
    1. 提供 ANSI 终端美化（`_Ansi`），在非 TTY 输出或 `--no-color` 下自动降级为纯文本。
    2. 每次提示输入前零 Token 刷新并渲染顶部硬件实时状态条。
    3. 订阅 `AgentRuntime` 事件流，实时流式输出 `text_delta`，清晰展示 `tool_call` 与 `tool_result`。
    4. 提供不可绕过的交互式 HITL 安全互锁审批弹窗 `terminal_approver(req)`，支持 SVD 位域解码
       与 Unified Diff 红绿高亮展示；在 `-p` 非交互且非 TTY 环境下安全拒绝。
    5. 支持 `--mock-llm` 离线演示与 `--session` 会话恢复。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Sequence, TextIO

from mcu_harness.agent.context import format_slash_help
from mcu_harness.agent.interlock import ApprovalDecision, ApprovalRequest, HarnessInterlock
from mcu_harness.agent.llm import LlmError, LlmProvider, LlmResponse, MockLlmProvider, ToolCall, create_provider
from mcu_harness.agent.runtime import AgentRuntime, HarnessEvent, TurnResult, load_session


class _Ansi:
    """轻量 ANSI 终端样式封装，非 TTY 或 `--no-color` 时自动退化为无转义纯文本。"""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"

    def __init__(self, enabled: bool | None = None, *, stream: TextIO | None = None) -> None:
        target = stream if stream is not None else sys.stdout
        if enabled is None:
            is_tty = bool(getattr(target, "isatty", lambda: False)())
            enabled = is_tty and not bool(os.environ.get("NO_COLOR"))
        self.enabled = bool(enabled)

    def wrap(self, text: str, *codes: str) -> str:
        if not self.enabled or not codes:
            return text
        return f"{''.join(codes)}{text}{self.RESET}"

    def bold(self, text: str) -> str:
        return self.wrap(text, self.BOLD)

    def dim(self, text: str) -> str:
        return self.wrap(text, self.DIM)

    def red(self, text: str) -> str:
        return self.wrap(text, self.RED)

    def green(self, text: str) -> str:
        return self.wrap(text, self.GREEN)

    def yellow(self, text: str) -> str:
        return self.wrap(text, self.YELLOW)

    def blue(self, text: str) -> str:
        return self.wrap(text, self.BLUE)

    def cyan(self, text: str) -> str:
        return self.wrap(text, self.CYAN)

    def magenta(self, text: str) -> str:
        return self.wrap(text, self.MAGENTA)

    def gray(self, text: str) -> str:
        return self.wrap(text, self.GRAY)


def _format_args_summary(arguments: dict[str, Any], max_len: int = 100) -> str:
    if not arguments:
        return ""
    parts: list[str] = []
    for k, v in arguments.items():
        if isinstance(v, str):
            val_s = v if len(v) <= 40 else v[:37] + "..."
            parts.append(f"{k}={val_s!r}")
        else:
            try:
                val_s = json.dumps(v, ensure_ascii=False)
            except TypeError:
                val_s = repr(v)
            if len(val_s) > 40:
                val_s = val_s[:37] + "..."
            parts.append(f"{k}={val_s}")
    joined = ", ".join(parts)
    if len(joined) > max_len:
        return joined[: max_len - 3] + "..."
    return joined


def _summarize_tool_result(data: dict[str, Any]) -> str:
    if data.get("denied"):
        prefix = "HARD DENY" if data.get("hard_denied") else "DENIED"
        err = str(data.get("error") or "Blocked by interlock")
        return f"{prefix}: {err}"
    if not data.get("ok"):
        res = data.get("result")
        err = data.get("error")
        if not err and isinstance(res, dict):
            err = res.get("error")
        return str(err or "Tool execution failed")
    res = data.get("result")
    if isinstance(res, dict):
        if "summary" in res and isinstance(res["summary"], str):
            return res["summary"]
        compact = json.dumps(res, ensure_ascii=False)
        return compact if len(compact) <= 140 else compact[:137] + "..."
    return "ok"


def render_status_bar(runtime: AgentRuntime, ansi: _Ansi) -> str:
    """渲染顶部紧凑一行实时硬件状态条（零 LLM Token 开销）。"""
    try:
        snap = runtime.get_hardware_snapshot()
        telemetry = snap.get("telemetry") or {}
        probe = telemetry.get("probe") or {}
        logs = telemetry.get("logs") or {}
        rtt_lines = (logs.get("rtt") or {}).get("buffered_lines", 0)
        device = telemetry.get("device") or "?"
        backend = probe.get("backend") or "?"
        connected = bool(probe.get("connected"))
        halted = probe.get("halted")
        if connected:
            probe_state = "halted" if halted is True else ("running" if halted is False else "connected")
        else:
            probe_state = "disconnected"
        provider = snap.get("provider") or runtime.provider.provider_name
        model = snap.get("model") or runtime.provider.model
        mode = snap.get("interlock_mode") or runtime.interlock.mode
    except Exception:
        device = "?"
        backend = "?"
        probe_state = "unknown"
        rtt_lines = 0
        provider = runtime.provider.provider_name
        model = runtime.provider.model
        mode = runtime.interlock.mode

    raw = f"[MCU_harness] {device} | {backend}:{probe_state} | RTT:{rtt_lines} | {provider}:{model} | mode={mode}"
    if not ansi.enabled:
        return raw
    state_colored = ansi.green(f"{backend}:{probe_state}") if probe_state != "disconnected" else ansi.gray(f"{backend}:{probe_state}")
    return (
        f"{ansi.wrap('[MCU_harness]', ansi.BOLD, ansi.CYAN)} "
        f"{ansi.bold(str(device))} | "
        f"{state_colored} | "
        f"{ansi.yellow(f'RTT:{rtt_lines}')} | "
        f"{ansi.blue(f'{provider}:{model}')} | "
        f"{ansi.magenta(f'mode={mode}')}"
    )


def _highlight_diff(diff_text: str, ansi: _Ansi) -> list[str]:
    out: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            out.append(ansi.bold(line))
        elif line.startswith("@@"):
            out.append(ansi.cyan(line))
        elif line.startswith("+"):
            out.append(ansi.green(line))
        elif line.startswith("-"):
            out.append(ansi.red(line))
        else:
            out.append(line)
    return out


def terminal_approver(
    req: ApprovalRequest,
    *,
    ansi: _Ansi | None = None,
    non_interactive: bool = False,
    input_fn: Callable[[str], str] = input,
    out: TextIO | None = None,
) -> ApprovalDecision:
    """交互式终端 HITL 安全互锁弹窗，支持 SVD 位域预览与 Unified Diff 高亮。"""
    stream = out if out is not None else sys.stdout
    styler = ansi if ansi is not None else _Ansi(stream=stream)

    if non_interactive:
        return ApprovalDecision(approved=False, scope="deny", reason="Non-interactive mode")

    width = 72
    border_top = "+" + "=" * (width - 2) + "+"
    border_mid = "+" + "-" * (width - 2) + "+"
    is_danger = req.risk_level == "hardware_danger"

    header_title = f"[HITL SAFETY INTERLOCK] {req.tool_name} (risk={req.risk_level})"
    lines: list[str] = [
        "",
        styler.wrap(border_top, styler.BOLD, styler.RED if is_danger else styler.YELLOW),
        styler.wrap(f"| {header_title}", styler.BOLD, styler.RED if is_danger else styler.YELLOW),
        styler.wrap(border_mid, styler.YELLOW if not is_danger else styler.RED),
        f"| Summary : {styler.bold(req.summary)}",
        f"| Risk    : {styler.red(req.risk_level) if is_danger else styler.yellow(req.risk_level)}",
    ]

    details = req.details or {}
    for key in ("address", "matched_register", "values_hex", "firmware_path", "relative_path", "command"):
        if details.get(key) not in (None, "", []):
            val = details[key]
            lines.append(f"| {key:<8}: {val}")

    if req.svd_preview:
        lines.append(styler.wrap(border_mid, styler.CYAN))
        lines.append(styler.wrap("| SVD Register Bitfield Decode:", styler.BOLD, styler.CYAN))
        for sline in req.svd_preview.splitlines():
            lines.append(f"|   {styler.cyan(sline)}")

    if req.diff_preview:
        lines.append(styler.wrap(border_mid, styler.BLUE))
        lines.append(styler.wrap("| Unified Diff Preview:", styler.BOLD, styler.BLUE))
        for dline in _highlight_diff(req.diff_preview, styler):
            lines.append(f"|   {dline}")

    if is_danger:
        lines.append(styler.wrap(border_mid, styler.RED))
        lines.append(
            styler.wrap(
                "| [!] 高危硬件操作不支持会话免确认（仅本次有效 / session_tool disabled）",
                styler.BOLD,
                styler.RED,
            )
        )

    lines.append(styler.wrap(border_top, styler.BOLD, styler.RED if is_danger else styler.YELLOW))
    stream.write("\n".join(lines) + "\n")
    stream.flush()

    prompt_txt = (
        "[y] 允许一次 / [n] 拒绝 (高危硬件操作仅本次有效): "
        if is_danger
        else "[y] 允许一次 / [a] 本次会话允许此类工具 / [n] 拒绝: "
    )

    try:
        choice = input_fn(prompt_txt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return ApprovalDecision(approved=False, scope="deny", reason="Non-interactive mode")

    if choice in ("y", "yes"):
        return ApprovalDecision(approved=True, scope="once", reason="Approved once by terminal operator")
    if choice in ("a", "always", "session"):
        if is_danger:
            return ApprovalDecision(
                approved=True,
                scope="once",
                reason="Approved once (hardware_danger does not allow session_tool caching)",
            )
        return ApprovalDecision(
            approved=True,
            scope="session_tool",
            reason=f"Approved tool '{req.tool_name}' for session by terminal operator",
        )
    return ApprovalDecision(approved=False, scope="deny", reason="Rejected by terminal operator")


def _build_demo_mock_provider(*, model: str | None = None) -> MockLlmProvider:
    """构造 `--mock-llm` 离线演示专用智能 MockLlmProvider。"""

    def _respond(
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]] | None,
    ) -> LlmResponse:
        del system, tools
        if not messages:
            return LlmResponse(text="[mock-llm] Ready. Try /status, /crash, or ask about the project.")

        last = messages[-1]
        role = last.get("role")

        if role == "tool":
            tool_name = str(last.get("name") or "tool")
            raw_content = str(last.get("content") or "{}")
            try:
                parsed = json.loads(raw_content)
            except json.JSONDecodeError:
                parsed = {"raw": raw_content}

            if tool_name == "diagnose_crash":
                if isinstance(parsed, dict) and parsed.get("error"):
                    summary = (
                        f"[mock-llm] Executed `diagnose_crash`, probe reported: {parsed['error']}.\n"
                        "Connect a target probe (or run with MockProbe) to decode live CFSR/BFAR fault frames."
                    )
                else:
                    fault_summary = parsed.get("summary") or parsed.get("fault_type") or "Crash analysis completed"
                    summary = f"[mock-llm] Crash diagnosis result from `diagnose_crash`:\n{fault_summary}"
                return LlmResponse(text=summary, finish_reason="stop")

            if tool_name == "inspect_project":
                dev = (parsed.get("target") or {}).get("device") if isinstance(parsed, dict) else None
                bs = (parsed.get("build") or {}).get("system") if isinstance(parsed, dict) else None
                return LlmResponse(
                    text=(
                        f"[mock-llm] Offline project inspection via `inspect_project`: "
                        f"device={dev or 'unknown'}, build_system={bs or 'unknown'}."
                    ),
                    finish_reason="stop",
                )

            return LlmResponse(
                text=f"[mock-llm] Observed result from `{tool_name}`: {raw_content[:240]}",
                finish_reason="stop",
            )

        user_text = str(last.get("content") or "").strip()
        lower = user_text.lower()
        if lower.startswith("/crash") or "crash" in lower or "hardfault" in lower:
            return LlmResponse(
                text="Running Cortex-M crash diagnostics (`diagnose_crash`)...",
                tool_calls=[ToolCall(id="mock_crash_1", name="diagnose_crash", arguments={})],
                finish_reason="tool_calls",
            )

        return LlmResponse(
            text="Inspecting local MCU workspace (`inspect_project`)...",
            tool_calls=[ToolCall(id="mock_proj_1", name="inspect_project", arguments={})],
            finish_reason="tool_calls",
        )

    return MockLlmProvider(_respond, model=model or "mock-demo-v1", provider_name="mock")


def _attach_event_printer(
    runtime: AgentRuntime,
    ansi: _Ansi,
    out: TextIO,
) -> tuple[Callable[[], None], dict[str, Any]]:
    """订阅运行时实时事件并输出到终端，返回 `(unsubscribe_fn, turn_state_dict)`。"""
    state: dict[str, Any] = {"streamed": False, "ended_with_nl": True}

    def _ensure_newline() -> None:
        if state["streamed"] and not state["ended_with_nl"]:
            out.write("\n")
            out.flush()
            state["ended_with_nl"] = True

    def _on_event(ev: HarnessEvent) -> None:
        kind = ev.kind
        data = ev.data
        if kind == "turn_started":
            state["streamed"] = False
            state["ended_with_nl"] = True
            return
        if kind == "text_delta":
            delta = str(data.get("delta") or "")
            if delta:
                out.write(delta)
                out.flush()
                state["streamed"] = True
                state["ended_with_nl"] = delta.endswith("\n")
            return
        if kind == "tool_call":
            _ensure_newline()
            name = str(data.get("name") or "?")
            args = data.get("arguments")
            arg_str = _format_args_summary(args if isinstance(args, dict) else {})
            line = f"[tool] {name}({arg_str})"
            out.write(ansi.wrap(line, ansi.BOLD, ansi.BLUE) + "\n")
            out.flush()
            return
        if kind == "tool_result":
            _ensure_newline()
            name = str(data.get("name") or "?")
            summary = _summarize_tool_result(data)
            if data.get("denied"):
                tag = ansi.wrap(f"[denied] {name}: {summary}", ansi.BOLD, ansi.RED)
            elif data.get("ok"):
                tag = ansi.green(f"[ok] {name}: {summary}")
            else:
                tag = ansi.red(f"[error] {name}: {summary}")
            out.write(tag + "\n")
            out.flush()
            return

    unsub = runtime.subscribe(_on_event)
    return unsub, state


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcu-agent",
        description="MCU Harness 独立第三方嵌入式 Agent 终端交互界面",
    )
    parser.add_argument("project_dir", nargs="?", default=".", help="目标嵌入式工程根目录（默认当前目录）")
    parser.add_argument("--provider", default=None, help="LLM Provider (openai/deepseek/qwen/openrouter/ollama/vllm/anthropic/gemini/mock)")
    parser.add_argument("--model", default=None, help="指定模型名称")
    parser.add_argument("--base-url", default=None, help="自定义 API Base URL")
    parser.add_argument("--api-key", default=None, help="自定义 API Key")
    parser.add_argument(
        "--mode",
        choices=["interactive", "auto_safe", "read_only"],
        default="interactive",
        help="硬件安全互锁模式（默认 interactive）",
    )
    parser.add_argument("-p", "--prompt", default=None, help="单次无交互模式：执行指定提示词/斜杠命令后退出")
    parser.add_argument("--session", default=None, help="恢复指定 ID 的历史会话（.mcu-harness/sessions/<id>.jsonl）")
    parser.add_argument("--mock-llm", action="store_true", help="离线演示模式：使用内置智能 MockLlmProvider")
    parser.add_argument("--no-color", action="store_true", help="禁用 ANSI 终端颜色输出")
    parser.add_argument("--max-steps", type=int, default=20, help="单轮 ReAct 最大步数（默认 20）")
    return parser


def _print_turn_output(res: TurnResult, state: dict[str, Any], ansi: _Ansi, out: TextIO, err: TextIO) -> None:
    if state.get("streamed"):
        if not state.get("ended_with_nl"):
            out.write("\n")
        if res.text is None:
            out.write("\n")
    elif res.text:
        out.write(res.text.rstrip("\n") + "\n")
    if res.error:
        err.write(ansi.red(f"[error] {res.error}\n"))
        err.flush()
    out.flush()


def main(argv: Sequence[str] | None = None) -> int:
    """`mcu-agent` TUI 命令行入口。"""
    parser = _build_arg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    out = sys.stdout
    err = sys.stderr
    ansi = _Ansi(enabled=False if args.no_color else None, stream=out)
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
            err.write(ansi.red(f"LLM configuration error: {exc}\n"))
            return 1

    interlock = HarnessInterlock(mode=args.mode)
    runtime = AgentRuntime(
        project_dir=project_dir,
        provider=provider,
        interlock=interlock,
        session_id=args.session,
        max_steps=args.max_steps,
    )

    unsub, state = _attach_event_printer(runtime, ansi, out)
    try:
        if args.prompt is not None:
            res = runtime.run_turn(
                args.prompt,
                approver=lambda req: terminal_approver(req, ansi=ansi, non_interactive=True, out=out),
            )
            _print_turn_output(res, state, ansi, out, err)
            return 1 if res.error else 0

        # 交互式 REPL 模式
        out.write(ansi.bold(f"MCU Harness Agent (session={runtime.session_id})\n"))
        if args.session:
            try:
                loaded = load_session(project_dir, args.session)
                out.write(ansi.dim(f"Resumed session '{args.session}' ({len(loaded['messages'])} messages)\n"))
            except FileNotFoundError:
                out.write(ansi.dim(f"Started new session '{args.session}'\n"))
        out.write(ansi.dim("Type /help for embedded playbooks & local commands, /exit or Ctrl+D to quit.\n"))
        out.flush()

        def _interactive_approver(req: ApprovalRequest) -> ApprovalDecision:
            return terminal_approver(req, ansi=ansi, non_interactive=False, out=out)

        while True:
            out.write(render_status_bar(runtime, ansi) + "\n")
            out.flush()
            prompt_str = ansi.wrap("mcu-agent> ", ansi.BOLD, ansi.GREEN)
            try:
                line = input(prompt_str)
            except EOFError:
                out.write("\nBye.\n")
                out.flush()
                return 0
            except KeyboardInterrupt:
                out.write("\n" + ansi.dim("(Use /exit or Ctrl+D to quit)\n"))
                out.flush()
                continue

            stripped = line.strip()
            if not stripped:
                continue

            res = runtime.run_turn(stripped, approver=_interactive_approver)
            _print_turn_output(res, state, ansi, out, err)
            if res.aborted:
                return 0
    finally:
        unsub()


if __name__ == "__main__":
    raise SystemExit(main())
