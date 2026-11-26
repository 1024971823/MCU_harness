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


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _format_crash_markdown(parsed: dict[str, Any], *, is_zh: bool) -> str:
    err = parsed.get("error")
    if err:
        if is_zh:
            return (
                "## Cortex-M 死机诊断报告\n\n"
                f"调用 `diagnose_crash` 时未能读取到目标板状态：{err}。\n\n"
                "| 寄存器 | 状态 | 说明 |\n"
                "|---|---|---|\n"
                "| `CPUID` (`0xE000ED00`) | 未连接探针 | 内核架构与版本标识 |\n"
                "| `ICSR` (`0xE000ED04`) | 未连接探针 | 当前激活异常号与挂起中断 |\n"
                "| `CFSR` (`0xE000ED28`) | 未连接探针 | MemManage / BusFault / UsageFault 状态 |\n"
                "| `HFSR` (`0xE000ED2C`) | 未连接探针 | HardFault 升级标志 (`FORCED`) |\n"
                "| `BFAR` (`0xE000ED38`) | 未连接探针 | 精确总线错误目标地址 |\n\n"
                "### 建议排查步骤\n"
                "1. 连接 SWD/JTAG 调试探针（或在右侧探针面板中启动 J-Link / OpenOCD 连接）。\n"
                "2. 重新执行 `/crash`，暂停内核并采集 SCB 异常寄存器与硬件压栈帧。\n"
                "3. 确保 `mcu-harness.toml` 已配置对应 `.elf` 路径，以便自动反解崩溃 `PC`/`LR` 源码位置。\n"
            )
        return (
            "## Cortex-M Crash Diagnosis\n\n"
            f"`diagnose_crash` could not access a connected target probe: {err}.\n\n"
            "| Register | Status | Description |\n"
            "|---|---|---|\n"
            "| `CPUID` (`0xE000ED00`) | Unavailable (no probe) | Core identity and revision |\n"
            "| `ICSR` (`0xE000ED04`) | Unavailable (no probe) | Active exception / IPSR vector |\n"
            "| `CFSR` (`0xE000ED28`) | Unavailable (no probe) | MemManage / BusFault / UsageFault flags |\n"
            "| `HFSR` (`0xE000ED2C`) | Unavailable (no probe) | HardFault escalation status (`FORCED`) |\n"
            "| `BFAR` (`0xE000ED38`) | Unavailable (no probe) | Faulting bus address (when `BFARVALID=1`) |\n\n"
            "### Next Steps\n"
            "1. Connect an SWD/JTAG debug probe (or start an OpenOCD / J-Link session in the Probe plugin).\n"
            "2. Re-run `/crash` to halt the core and decode live SCB fault registers and stacked frame.\n"
            "3. Ensure the project's `.elf` artifact is configured in `mcu-harness.toml` for PC/LR symbol resolution.\n"
        )

    summary = str(parsed.get("summary") or ("Crash analysis completed" if not is_zh else "死机诊断完成"))
    core_info = parsed.get("core") if isinstance(parsed.get("core"), dict) else {}
    core_name = str(core_info.get("core_name") or "Cortex-M")
    active_exc = str(parsed.get("active_exception") or "HardFault")
    scb_regs = parsed.get("scb_registers") if isinstance(parsed.get("scb_registers"), dict) else {}
    fault_addrs = parsed.get("fault_addresses") if isinstance(parsed.get("fault_addresses"), dict) else {}
    core_regs = parsed.get("core_registers") if isinstance(parsed.get("core_registers"), dict) else {}
    active_faults = parsed.get("active_faults") if isinstance(parsed.get("active_faults"), list) else []
    recs = parsed.get("recommendations") if isinstance(parsed.get("recommendations"), list) else []

    reg_rows: list[str] = []
    for k, v in scb_regs.items():
        reg_rows.append(f"| `{k}` | `{v}` | SCB |")
    for k, v in fault_addrs.items():
        reg_rows.append(f"| `{k}` | `{v}` | Fault Address |")
    for k in ("PC", "LR", "MSP", "PSP"):
        if k in core_regs:
            reg_rows.append(f"| `{k}` | `{core_regs[k]}` | Core Register |")
    if not reg_rows:
        reg_rows.append("| `Status` | `No registers returned` | Target snapshot |")

    table = (
        ("| 寄存器 | 数值 | 类别 |\n|---|---|---|\n" if is_zh else "| Register | Value | Category |\n|---|---|---|\n")
        + "\n".join(reg_rows)
    )
    fault_names = ", ".join(
        f"`{f.get('register')}.{f.get('name')}`" for f in active_faults if isinstance(f, dict)
    )
    if is_zh:
        steps_list = recs[:4] if recs else [
            "根据 `PC`/`LR` 与反汇编确认触发异常的具体指令及调用栈。",
            "检查 `BFAR`/`MMFAR` 对应指针或 DMA 缓冲区是否越界或为空指针。",
            "核对中断向量表与任务栈水位，确认未发生栈溢出。",
        ]
        steps_md = "\n".join(f"{i}. {s}" for i, s in enumerate(steps_list, 1))
        fault_line = f"- **激活故障标志**: {fault_names}\n" if fault_names else ""
        return (
            f"## Cortex-M 死机诊断报告（{core_name}）\n\n"
            f"- **异常状态**: `{active_exc}`\n"
            f"- **诊断摘要**: {summary}\n"
            f"{fault_line}\n"
            f"{table}\n\n"
            f"### 建议排查步骤\n{steps_md}\n"
        )

    steps_list = recs[:4] if recs else [
        "Resolve stacked `PC` and `LR` against the firmware ELF to inspect the faulting source line.",
        "Validate pointers and peripheral base addresses referenced around `BFAR`/`MMFAR`.",
        "Verify stack watermark margins (`MSP`/`PSP`) and MPU region permissions.",
    ]
    steps_md = "\n".join(f"{i}. {s}" for i, s in enumerate(steps_list, 1))
    fault_line = f"- **Active Faults**: {fault_names}\n" if fault_names else ""
    return (
        f"## Cortex-M Crash Diagnosis ({core_name})\n\n"
        f"- **Active Exception**: `{active_exc}`\n"
        f"- **Summary**: {summary}\n"
        f"{fault_line}\n"
        f"{table}\n\n"
        f"### Next Steps\n{steps_md}\n"
    )


def _format_doctor_markdown(parsed: dict[str, Any], *, is_zh: bool) -> str:
    err = parsed.get("error")
    if err:
        if is_zh:
            return (
                "## USART1 外设因果体检报告\n\n"
                f"执行 `diagnose_peripheral(peripheral=\"USART1\")` 时无法访问硬件状态：{err}。\n\n"
                "### 建议排查步骤\n"
                "1. 连接调试探针并确认 SVD 文件路径有效，以读取 RCC 时钟门控、NVIC 与 USART1 寄存器。\n"
                "2. 重新执行 `/doctor USART1` 获取完整的时钟、中断与引脚复用诊断。\n"
            )
        return (
            "## Peripheral Diagnosis (`USART1`)\n\n"
            f"`diagnose_peripheral` for `USART1` could not access live hardware: {err}.\n\n"
            "### Next Steps\n"
            "1. Connect a debug probe and verify the project's `.svd` path to read RCC, NVIC, and USART1 registers.\n"
            "2. Re-run `/doctor USART1` to inspect clock gating, interrupt enablement, and GPIO mux settings.\n"
        )

    periph = str(parsed.get("peripheral") or "USART1")
    severity = str(parsed.get("highest_severity") or "ok")
    summary = str(parsed.get("summary") or "Diagnosis completed")
    findings = parsed.get("findings") if isinstance(parsed.get("findings"), list) else []
    finding_lines = [
        f"- **[{f.get('severity', 'info')}]** {f.get('message', '')}"
        for f in findings[:5]
        if isinstance(f, dict)
    ]
    findings_md = "\n".join(finding_lines) if finding_lines else ("- 未发现异常告警。" if is_zh else "- No causal anomalies flagged.")
    if is_zh:
        return (
            f"## 外设因果体检报告（{periph}）\n\n"
            f"- **健康等级**: `{severity}`\n"
            f"- **摘要**: {summary}\n\n"
            f"### 诊断发现\n{findings_md}\n"
        )
    return (
        f"## Peripheral Diagnosis ({periph})\n\n"
        f"- **Severity**: `{severity}`\n"
        f"- **Summary**: {summary}\n\n"
        f"### Findings\n{findings_md}\n"
    )


def _format_project_markdown(parsed: dict[str, Any], *, is_zh: bool) -> str:
    target = parsed.get("target") if isinstance(parsed.get("target"), dict) else {}
    build = parsed.get("build") if isinstance(parsed.get("build"), dict) else {}
    paths = parsed.get("paths") if isinstance(parsed.get("paths"), dict) else {}
    dev = str(target.get("device") or "unknown")
    core = str(target.get("core") or "Cortex-M")
    bs = str(build.get("system") or "unknown")
    elf = str(paths.get("elf_path") or ("未检测到" if is_zh else "not detected"))
    svd = str(paths.get("svd_path") or ("未检测到" if is_zh else "not detected"))

    if is_zh:
        return (
            "## 工程概览\n\n"
            "| 属性 | 配置 |\n"
            "|---|---|\n"
            f"| 目标芯片 | `{dev}` ({core}) |\n"
            f"| 构建系统 | `{bs}` |\n"
            f"| ELF 产物 | `{elf}` |\n"
            f"| SVD 描述 | `{svd}` |\n\n"
            "### 推荐技能命令\n"
            "- `/crash`：Cortex-M HardFault / BusFault 现场尸检与源码定位\n"
            "- `/doctor`：外设因果体检（RCC 时钟门控 → NVIC → 寄存器 → GPIO 复用）\n"
            "- `/bringup`：新板级外设联调与寄存器核验\n"
            "- `/refactor`：嵌入式四层架构检查与代码重构\n"
            "- `/hil`：硬件在环自动化验证\n"
        )
    return (
        "## Project Overview\n\n"
        "| Field | Value |\n"
        "|---|---|\n"
        f"| Target Device | `{dev}` ({core}) |\n"
        f"| Build System | `{bs}` |\n"
        f"| ELF Artifact | `{elf}` |\n"
        f"| SVD File | `{svd}` |\n\n"
        "### Suggested Skills\n"
        "- `/crash` — Decode Cortex-M HardFault/SCB state and stacked PC/LR\n"
        "- `/doctor` — Causal peripheral check (RCC clock gate, NVIC, registers, GPIO mux)\n"
        "- `/bringup` — Guided peripheral bring-up and live register verification\n"
        "- `/refactor` — Firmware architecture check and layered refactoring\n"
        "- `/hil` — Run hardware-in-the-loop verification suites\n"
    )


def _build_demo_mock_provider(*, model: str | None = None) -> MockLlmProvider:
    """构造 `--mock-llm` 离线演示专用智能 MockLlmProvider。"""

    def _respond(
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]] | None,
    ) -> LlmResponse:
        del system, tools
        last_user_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user_text = str(msg.get("content") or "")
                break
        is_zh = _has_cjk(last_user_text)

        if not messages:
            return LlmResponse(
                reasoning=(
                    "No prior messages are present in this conversation context yet. "
                    "I should confirm readiness concisely and list available embedded playbooks. "
                    "Once the user sends a prompt, I will inspect the workspace or target state first."
                ),
                text="Ready. Try `/crash`, `/doctor`, `/bringup`, `/refactor`, or `/hil`.",
            )

        last = messages[-1]
        role = last.get("role")

        if role == "tool":
            tool_name = str(last.get("name") or "tool")
            raw_content = str(last.get("content") or "{}")
            try:
                parsed = json.loads(raw_content)
            except json.JSONDecodeError:
                parsed = {"raw": raw_content}
            parsed_dict = parsed if isinstance(parsed, dict) else {"raw": parsed}

            if tool_name == "diagnose_crash":
                if is_zh:
                    reasoning = (
                        "已收到 `diagnose_crash` 返回的现场数据，需要核对核心状态与探针是否就绪。"
                        "如果返回中包含探针连接错误，我必须如实说明原因，并给出需要核查的 SCB 寄存器清单。"
                        "如果成功读到 SCB 寄存器与压栈帧，则按寄存器表格整理 CFSR/HFSR/BFAR 与 PC/LR 信息。"
                        "最后补充可执行的下一步排查建议，帮助工程师快速定位根因。"
                    )
                else:
                    reasoning = (
                        "I have received the structured output from `diagnose_crash` and need to check whether a probe snapshot was captured. "
                        "If the probe reported a connection error, I should explain the missing hardware link clearly instead of fabricating register values. "
                        "If live SCB and core registers were returned, I will tabulate them cleanly and highlight active fault bits. "
                        "Finally, I will provide concrete numbered next steps for fault localization."
                    )
                return LlmResponse(
                    reasoning=reasoning,
                    text=_format_crash_markdown(parsed_dict, is_zh=is_zh),
                    finish_reason="stop",
                )

            if tool_name == "diagnose_peripheral":
                if is_zh:
                    reasoning = (
                        "已取得 `diagnose_peripheral` 对 USART1 的体检结果，正在检查时钟门控、NVIC 中断和引脚配置。"
                        "若未连接调试探针导致工具返回错误，应明确提示硬件未就绪并说明补救方法。"
                        "否则将健康等级、摘要及主要发现整理为结构化 Markdown 报告。"
                        "同时确保所有表述与工具返回字段严格一致。"
                    )
                else:
                    reasoning = (
                        "I received the `diagnose_peripheral` report for `USART1` and am reviewing clock gating, NVIC IRQ state, and findings. "
                        "If hardware access failed because no probe is connected, I will report that constraint transparently. "
                        "Otherwise, I will summarize the severity, causal findings, and register status clearly in Markdown. "
                        "Let me format the final summary and actionable next steps."
                    )
                return LlmResponse(
                    reasoning=reasoning,
                    text=_format_doctor_markdown(parsed_dict, is_zh=is_zh),
                    finish_reason="stop",
                )

            if tool_name == "inspect_project":
                if is_zh:
                    reasoning = (
                        "已通过 `inspect_project` 获取当前工作区的目标芯片、构建系统及 ELF/SVD 路径信息。"
                        "接下来需要将这些核心配置整理为清晰的 Markdown 表格，方便工程师核对环境。"
                        "同时结合当前工程上下文，推荐常用的嵌入式调试技能命令如 `/crash` 与 `/doctor`。"
                        "保持结论简洁准确，不捏造未扫描到的构建产物。"
                    )
                else:
                    reasoning = (
                        "I have gathered the workspace profile from `inspect_project`, including target device, build system, and artifact paths. "
                        "I should present these discovered project attributes in a concise Markdown overview table. "
                        "I will also suggest the most relevant built-in skills such as `/crash`, `/doctor`, `/bringup`, `/refactor`, and `/hil`. "
                        "Let me write out the final project summary."
                    )
                return LlmResponse(
                    reasoning=reasoning,
                    text=_format_project_markdown(parsed_dict, is_zh=is_zh),
                    finish_reason="stop",
                )

            if is_zh:
                reasoning = (
                    f"已获得工具 `{tool_name}` 的返回结果，正在解析关键字段。"
                    "我会保留与当前问题最相关的状态和错误信息。"
                    "随后以清晰的 Markdown 形式向用户总结结果并给出后续建议。"
                )
                txt = f"已收到 `{tool_name}` 返回结果：\n\n```json\n{raw_content[:240]}\n```"
            else:
                reasoning = (
                    f"I received the output from `{tool_name}` and am extracting the relevant fields. "
                    "I will summarize the observed status accurately without guessing missing values. "
                    "Let me present the concise result to the user."
                )
                txt = f"Observed result from `{tool_name}`:\n\n```json\n{raw_content[:240]}\n```"
            return LlmResponse(reasoning=reasoning, text=txt, finish_reason="stop")

        user_text = str(last.get("content") or "").strip()
        lower = user_text.lower()
        if user_text == "Reply with OK.":
            return LlmResponse(
                reasoning=(
                    "This is a minimal connectivity health check requesting a literal acknowledgement. "
                    "No hardware or workspace tool invocation is needed here. "
                    "I can respond directly with OK."
                ),
                text="OK",
                finish_reason="stop",
            )

        if lower.startswith("/crash") or "crash" in lower or "hardfault" in lower or "死机" in user_text or "崩溃" in user_text:
            if is_zh:
                reasoning = (
                    "用户正在排查 Cortex-M 死机或 HardFault 问题，应先读取真实硬件异常现场而非直接猜测。"
                    "按照标准排障流程，第一步需要调用只读工具 `diagnose_crash` 检查目标内核与 SCB 故障寄存器。"
                    "该工具会尝试读取 CPUID、ICSR、CFSR、HFSR、BFAR 以及栈帧中的 PC/LR。"
                    "我将立即发起 `diagnose_crash` 工具调用并等待返回结果。"
                )
                step_text = "正在调用 `diagnose_crash` 采集内核故障寄存器与堆栈现场…"
            else:
                reasoning = (
                    "The user is asking to investigate a Cortex-M crash or HardFault condition. "
                    "Before forming any hypothesis, I need to inspect live core state and SCB fault registers via `diagnose_crash`. "
                    "This read-only diagnostic tool checks CPUID, ICSR, CFSR, HFSR, BFAR/MMFAR, and stacked exception frames when a probe is attached. "
                    "Let me invoke `diagnose_crash` first and examine its structured report."
                )
                step_text = "Running Cortex-M crash diagnostics (`diagnose_crash`)..."
            return LlmResponse(
                reasoning=reasoning,
                text=step_text,
                tool_calls=[ToolCall(id="mock_crash_1", name="diagnose_crash", arguments={})],
                finish_reason="tool_calls",
            )

        if lower.startswith("/doctor") or "doctor" in lower or "外设诊断" in user_text:
            if is_zh:
                reasoning = (
                    "用户触发了 `/doctor` 外设因果体检流程，需要从时钟门控、NVIC 到寄存器逐级排查。"
                    "按照标准诊断顺序，先以 `USART1` 为例调用只读工具 `diagnose_peripheral`。"
                    "这样可以验证 RCC 使能位、中断配置以及 GPIO 复用状态是否存在因果断链。"
                    "现在发起 `diagnose_peripheral` 调用。"
                )
                step_text = "正在调用 `diagnose_peripheral` 检查 `USART1` 时钟与寄存器状态…"
            else:
                reasoning = (
                    "The user requested a causal peripheral check via `/doctor`. "
                    "I should inspect the peripheral chain from RCC clock gating through NVIC enablement and control registers. "
                    "Calling `diagnose_peripheral` on `USART1` is a safe read-only first step to check for misconfigurations or missing probe connectivity. "
                    "Let me run `diagnose_peripheral` now."
                )
                step_text = "Running causal peripheral diagnostics on `USART1` (`diagnose_peripheral`)..."
            return LlmResponse(
                reasoning=reasoning,
                text=step_text,
                tool_calls=[ToolCall(id="mock_doctor_1", name="diagnose_peripheral", arguments={"peripheral": "USART1"})],
                finish_reason="tool_calls",
            )

        if is_zh:
            reasoning = (
                "用户的请求属于常规工程咨询或状态概览，首先应当摸清本地工作区配置。"
                "调用只读工具 `inspect_project` 可以获取目标芯片型号、构建系统以及 ELF/SVD 文件路径。"
                "拿到真实工程画像后，再向用户汇总配置并推荐对应的嵌入式技能命令。"
                "下面先执行 `inspect_project`。"
            )
            step_text = "正在扫描本地 MCU 工程配置（`inspect_project`）…"
        else:
            reasoning = (
                "The user's request asks for general assistance or project context without naming a specific fault or peripheral. "
                "I should inspect the local workspace manifest and build artifacts first using the read-only `inspect_project` tool. "
                "That will give me accurate target chip, build system, ELF, and SVD details to anchor the response. "
                "Let me run `inspect_project` now."
            )
            step_text = "Inspecting local MCU workspace (`inspect_project`)..."
        return LlmResponse(
            reasoning=reasoning,
            text=step_text,
            tool_calls=[ToolCall(id="mock_proj_1", name="inspect_project", arguments={})],
            finish_reason="tool_calls",
        )

    return MockLlmProvider(
        _respond,
        model=model or "mock-demo-v1",
        provider_name="mock",
        stream_chunk_chars=4,
        stream_chunk_delay_s=0.015,
    )


def _attach_event_printer(
    runtime: AgentRuntime,
    ansi: _Ansi,
    out: TextIO,
) -> tuple[Callable[[], None], dict[str, Any]]:
    """订阅运行时实时事件并输出到终端，返回 `(unsubscribe_fn, turn_state_dict)`。"""
    state: dict[str, Any] = {
        "streamed": False,
        "streamed_text": False,
        "ended_with_nl": True,
        "in_reasoning": False,
        "in_text": False,
    }

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
            state["streamed_text"] = False
            state["ended_with_nl"] = True
            state["in_reasoning"] = False
            state["in_text"] = False
            return
        if kind == "reasoning_delta":
            delta = str(data.get("delta") or "")
            if delta:
                if not state["in_reasoning"] and state["streamed"] and not state["ended_with_nl"]:
                    out.write("\n")
                state["in_reasoning"] = True
                state["in_text"] = False
                out.write(ansi.dim(delta))
                out.flush()
                state["streamed"] = True
                state["ended_with_nl"] = delta.endswith("\n")
            return
        if kind == "text_delta":
            delta = str(data.get("delta") or "")
            if delta:
                if state["in_reasoning"]:
                    if not state["ended_with_nl"]:
                        out.write("\n")
                    state["in_reasoning"] = False
                state["in_text"] = True
                state["streamed_text"] = True
                out.write(delta)
                out.flush()
                state["streamed"] = True
                state["ended_with_nl"] = delta.endswith("\n")
            return
        if kind == "tool_call":
            _ensure_newline()
            state["in_reasoning"] = False
            state["in_text"] = False
            name = str(data.get("name") or "?")
            args = data.get("arguments")
            arg_str = _format_args_summary(args if isinstance(args, dict) else {})
            line = f"[tool] {name}({arg_str})"
            out.write(ansi.wrap(line, ansi.BOLD, ansi.BLUE) + "\n")
            out.flush()
            return
        if kind == "tool_result":
            _ensure_newline()
            state["in_reasoning"] = False
            state["in_text"] = False
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
    if state.get("streamed_text"):
        if not state.get("ended_with_nl"):
            out.write("\n")
        if res.text is None:
            out.write("\n")
    elif res.text:
        if state.get("streamed") and not state.get("ended_with_nl"):
            out.write("\n")
        out.write(res.text.rstrip("\n") + "\n")
    elif state.get("streamed") and not state.get("ended_with_nl"):
        out.write("\n")
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
