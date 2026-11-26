"""动态硬件状态卡片（Status Card）、系统提示词构造与嵌入式斜杠工作流（Slash Playbooks）。

设计目标：
    1. `build_hardware_status_card`: 零 LLM Token 开销聚合工程画像、ELF/SVD/IOC 产物状态、
       探针实时连接态、RTT/UART 后台日志计数及最近 3 条安全审计事件。
    2. `build_system_prompt`: 将硬件状态卡片和激活的工作流 SOP 注入专业嵌入式工程师系统提示词。
    3. `PLAYBOOKS` 与 `expand_slash_command`: 提供 5 个嵌入式斜杠工作流（/crash, /doctor,
       /bringup, /refactor, /hil）与 8 个本地元命令（/status, /audit, /compact, /clear,
       /sessions, /help, /exit, /quit）。
"""

from __future__ import annotations

import difflib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcu_harness.config.manifest import ProjectProfile, discover_project
from mcu_harness.log.rtt_collector import RttLogCollector
from mcu_harness.log.uart_collector import UartLogCollector
from mcu_harness.probe.manager import ProbeManager
from mcu_harness.safety.guard import SafetyGuard


def _artifact_state(raw_path: str | None) -> dict[str, Any]:
    if not raw_path:
        return {"path": None, "exists": False}
    p = Path(raw_path)
    return {"path": str(p), "exists": p.is_file()}


def build_hardware_status_card(
    project_dir: str,
    *,
    probe_manager: ProbeManager,
    rtt_collector: RttLogCollector,
    uart_collector: UartLogCollector,
    safety_guard: SafetyGuard,
    profile: ProjectProfile | None = None,
) -> tuple[str, dict[str, Any]]:
    """零 LLM Token 开销聚合当前硬件与工程状态，返回 `(markdown_card, telemetry_dict)`。"""
    resolved_dir = str(Path(project_dir).expanduser().resolve()) if project_dir else "."
    eff_profile = profile
    if eff_profile is None and project_dir:
        try:
            eff_profile = discover_project(project_dir)
        except Exception:
            eff_profile = None

    device_cfg = eff_profile.target.device if eff_profile else None
    core_cfg = eff_profile.target.core if eff_profile else None
    build_sys = eff_profile.build.system if eff_profile else "unknown"
    backend_cfg = eff_profile.target.probe_backend if eff_profile else None

    elf_info = _artifact_state(eff_profile.paths.elf_path if eff_profile else None)
    svd_info = _artifact_state(eff_profile.paths.svd_path if eff_profile else None)
    ioc_info = _artifact_state(eff_profile.paths.ioc_path if eff_profile else None)

    probe_state: dict[str, Any] = {
        "connected": False,
        "halted": None,
        "device": device_cfg,
        "backend": backend_cfg,
        "interface": eff_profile.target.interface if eff_profile else None,
        "serial_no": None,
    }
    try:
        with probe_manager.session_if_connected() as probe:
            if probe is not None:
                pinfo = probe.info()
                probe_state = {
                    "connected": bool(pinfo.connected),
                    "halted": pinfo.halted,
                    "device": pinfo.device or device_cfg,
                    "backend": pinfo.backend or backend_cfg,
                    "interface": pinfo.interface,
                    "serial_no": pinfo.serial_no,
                }
    except Exception as exc:
        probe_state["error"] = str(exc)

    rtt_st = rtt_collector.status()
    uart_st = uart_collector.status()
    recent_audits = [entry.to_dict() for entry in safety_guard.recent_audit(limit=3)]

    telemetry: dict[str, Any] = {
        "project_dir": resolved_dir,
        "device": probe_state.get("device") or device_cfg,
        "core": core_cfg,
        "build_system": build_sys,
        "artifacts": {
            "elf": elf_info,
            "svd": svd_info,
            "ioc": ioc_info,
        },
        "probe": probe_state,
        "logs": {
            "rtt": {
                "running": rtt_st.running,
                "channel": rtt_st.channel,
                "buffered_lines": rtt_st.total_buffered,
                "bytes_received": rtt_st.bytes_received,
                "last_error": rtt_st.last_error,
            },
            "uart": {
                "running": uart_st.running,
                "port": uart_st.port,
                "baudrate": uart_st.baudrate,
                "buffered_lines": uart_st.total_buffered,
                "bytes_received": uart_st.bytes_received,
                "last_error": uart_st.last_error,
            },
        },
        "recent_audit": recent_audits,
    }

    # 构造紧凑可读的 Markdown 状态卡片
    def _fmt_art(name: str, info: dict[str, Any]) -> str:
        if not info["path"]:
            return f"{name}: missing"
        tag = "OK" if info["exists"] else "NOT FOUND"
        return f"{name}: {tag} (`{Path(info['path']).name}`)"

    conn_tag = "connected" if probe_state["connected"] else "disconnected"
    if probe_state["halted"] is True:
        halt_tag = "halted"
    elif probe_state["halted"] is False:
        halt_tag = "running"
    else:
        halt_tag = "unknown"

    lines = [
        "### Hardware & Project Status",
        f"- **Project**: `{resolved_dir}`",
        f"- **Target**: `{telemetry['device'] or '(unknown)'}` | Core: `{core_cfg or '(unknown)'}` | Build: `{build_sys}`",
        f"- **Artifacts**: {_fmt_art('ELF', elf_info)} | {_fmt_art('SVD', svd_info)} | {_fmt_art('IOC', ioc_info)}",
        (
            f"- **Probe**: `{conn_tag}` ({halt_tag}) | "
            f"backend=`{probe_state.get('backend') or '?'}` | device=`{probe_state.get('device') or '?'}`"
        ),
        (
            f"- **Debug Logs**: "
            f"RTT={'running' if rtt_st.running else 'stopped'} ({rtt_st.total_buffered} lines, {rtt_st.bytes_received}B) | "
            f"UART={'running' if uart_st.running else 'stopped'} "
            f"({uart_st.port or 'unassigned'}, {uart_st.total_buffered} lines, {uart_st.bytes_received}B)"
        ),
    ]

    if recent_audits:
        lines.append("- **Recent Safety Audit (last 3)**:")
        for item in recent_audits:
            ts_str = datetime.fromtimestamp(item["timestamp"], tz=timezone.utc).strftime("%H:%M:%S")
            verdict = "ALLOW" if item["allowed"] else "BLOCK"
            lines.append(
                f"  - `#{item['seq']}` [{ts_str}] `{item['operation']}` `{item['target']}` -> **{verdict}** ({item['level']})"
            )
    else:
        lines.append("- **Recent Safety Audit**: (none)")

    return "\n".join(lines), telemetry


def build_system_prompt(status_card: str, *, playbook_prompt: str | None = None) -> str:
    """构造专业嵌入式工程 Agent 系统提示词。"""
    sections = [
        (
            "You are an expert embedded systems and Cortex-M firmware engineering agent operating inside MCU Harness.\n"
            "Follow these strict engineering and hardware safety rules:\n"
            "1. **Use Structured Hardware Tools First**: Always prefer structured inspection tools "
            "(`inspect_project`, `inspect_board`, `inspect_peripheral`, `inspect_svd`, `diagnose_peripheral`, "
            "`diagnose_crash`, `watch_variable`, `inspect_rtos`, `trace_target`, `read_debug_log`, "
            "`check_architecture`, `build_firmware`, `analyze_footprint`) instead of guessing register addresses, "
            "bitfields, or memory layouts.\n"
            "2. **Respect Hardware Safety Interlocks**: Never attempt to bypass HITL confirmation gates or "
            "Option Bytes / Product State protections. Explain register/firmware risks clearly before requesting approval.\n"
            "3. **Build, Verify & Provide Reproducible Evidence**: Whenever you modify firmware or configuration, "
            "you must rebuild (`build_firmware`), validate (`check_architecture` / tests / probe / logs), and report "
            "only directly observed, reproducible evidence (exact build output, register decode, log lines, or test results)."
        ),
        "## Current Workspace & Hardware Telemetry\n" + status_card.strip(),
    ]
    if playbook_prompt and playbook_prompt.strip():
        sections.append("## Active Embedded Playbook SOP\n" + playbook_prompt.strip())
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Embedded Slash Playbooks (5 workflows) & Local Meta Commands (8 commands)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlaybookSpec:
    name: str
    command: str
    title: str
    description: str
    usage: str
    recommended_tools: tuple[str, ...]
    prompt_template: str

    def render(self, args: str = "") -> str:
        clean_args = (args or "").strip()
        return self.prompt_template.format(
            args=clean_args or "(unspecified)",
            periph=clean_args or "(inspect from .ioc / board context)",
            spec=clean_args or "(discover HIL spec in project)",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "title": self.title,
            "description": self.description,
            "usage": self.usage,
            "recommended_tools": list(self.recommended_tools),
        }


PLAYBOOKS: dict[str, PlaybookSpec] = {
    "crash": PlaybookSpec(
        name="crash",
        command="/crash",
        title="HardFault & Crash Root-Cause SOP",
        description="Automated Cortex-M fault decoding, RTOS task inspection, RTT/UART log correlation, and C line attribution.",
        usage="/crash [optional context]",
        recommended_tools=("diagnose_crash", "inspect_rtos", "read_debug_log", "read_memory"),
        prompt_template=(
            "Execute the **HardFault / Crash Root-Cause SOP** (context: {args}):\n"
            "1. Call `diagnose_crash` to capture SCB fault registers (CFSR/HFSR/MMFAR/BFAR/SFSR), stacked frame, and DWARF C source line.\n"
            "2. Call `inspect_rtos` to check active task states, stack high-water marks, and whether an overflow or deadlock occurred.\n"
            "3. Call `read_debug_log` (RTT and/or UART) around the failure timestamp to correlate firmware trace messages.\n"
            "4. Pinpoint the exact C file/line and root cause (e.g. null deref, stack overflow, unaligned access, ISR illegal API), "
            "propose a minimal fix, and verify with `build_firmware`."
        ),
    ),
    "doctor": PlaybookSpec(
        name="doctor",
        command="/doctor",
        title="4-Step Peripheral Doctor",
        description="Four-step peripheral diagnostic linking board wiring, RCC clock, NVIC, GPIO AF, and live SVD diff.",
        usage="/doctor [PERIPH]",
        recommended_tools=("inspect_board", "diagnose_peripheral", "inspect_peripheral", "read_debug_log"),
        prompt_template=(
            "Execute the **4-Step Peripheral Doctor SOP** for target peripheral `{periph}`:\n"
            "1. Call `inspect_board(live_check=True)` to verify `.ioc` pin mapping, GPIO MODER/AFR configuration, and detect pin drift.\n"
            "2. Call `diagnose_peripheral` for `{periph}` to check RCC clock enable bits, NVIC enable/pending/priority, and error flags.\n"
            "3. Call `inspect_peripheral(diff_only=True)` for `{periph}` to inspect all registers deviating from reset values.\n"
            "4. Summarize root cause (missing clock, wrong AF mux, disabled NVIC IRQ, or peripheral error bit) and verify any fix."
        ),
    ),
    "bringup": PlaybookSpec(
        name="bringup",
        command="/bringup",
        title="New Board Bring-Up Checkup",
        description="End-to-end initial board checkup: project profile, board wiring, probe attachment, clock frequency, and boot logs.",
        usage="/bringup [optional focus]",
        recommended_tools=("inspect_project", "inspect_board", "probe_control", "trace_target", "read_debug_log"),
        prompt_template=(
            "Execute the **New Board Bring-Up Checkup SOP** (focus: {args}):\n"
            "1. Call `inspect_project` to confirm detected MCU device, ELF/SVD/IOC paths, and build system.\n"
            "2. Call `inspect_board` to audit pinout and peripheral bindings.\n"
            "3. Call `probe_control(action='info')` to verify SWD/JTAG probe connection and target CPUID/core state.\n"
            "4. Call `trace_target` (`measure_cycles`) to confirm SYSCLK / DWT cycle counter behavior and clock tree sanity.\n"
            "5. Call `read_debug_log` to inspect boot/startup RTT or UART output and report bring-up health."
        ),
    ),
    "refactor": PlaybookSpec(
        name="refactor",
        command="/refactor",
        title="Legacy Firmware Architecture Audit & Refactoring",
        description="Analyze compile database, 4-layer architecture & ISR rules, memory footprint, and scaffold clean layers.",
        usage="/refactor [optional scope]",
        recommended_tools=("generate_compile_commands", "check_architecture", "analyze_footprint", "scaffold_code"),
        prompt_template=(
            "Execute the **Firmware Architecture Audit & Refactoring SOP** (scope: {args}):\n"
            "1. Call `generate_compile_commands` to ensure an up-to-date `compile_commands.json`.\n"
            "2. Call `check_architecture` to identify App/Service/BSP/PAL layering violations, illegal ISR calls, and missing `volatile` qualifiers.\n"
            "3. Call `analyze_footprint` to audit Flash/RAM/DMA section usage and heavy symbols.\n"
            "4. Use `scaffold_code` (or targeted edits) to decouple hardware dependencies cleanly, then run `build_firmware` and tests to verify."
        ),
    ),
    "hil": PlaybookSpec(
        name="hil",
        command="/hil",
        title="Build-Flash-Test HIL Regression Loop",
        description="Closed-loop compile, HITL-approved firmware flash, and declarative Hardware-in-the-Loop test suite execution.",
        usage="/hil <spec>",
        recommended_tools=("build_firmware", "flash_firmware", "run_hil_tests", "read_debug_log"),
        prompt_template=(
            "Execute the **Build-Flash-Test HIL Regression Closed-Loop SOP** (spec: `{spec}`):\n"
            "1. Call `build_firmware` to compile the latest firmware image and verify zero build errors.\n"
            "2. Call `flash_firmware` (requires interactive HITL approval) to program the updated `.elf` onto the target MCU.\n"
            "3. Call `run_hil_tests` with spec `{spec}` to execute stimulus/assertions and ensure teardown safety.\n"
            "4. Inspect any failures with `read_debug_log` / `diagnose_crash` and report full pass/fail evidence."
        ),
    ),
}

META_COMMANDS: dict[str, str] = {
    "status": "Show live hardware & project status card (zero LLM tokens).",
    "audit": "Display recent hardware safety audit log entries.",
    "compact": "Compact conversation history to free context window.",
    "clear": "Clear current conversation messages.",
    "sessions": "List or switch saved agent sessions.",
    "help": "Show available embedded playbooks and local meta commands.",
    "exit": "Exit the MCU Harness interactive session.",
    "quit": "Exit the MCU Harness interactive session.",
}


@dataclass(frozen=True)
class SlashResolution:
    kind: str  # "playbook" | "meta" | "prompt" | "unknown"
    raw_input: str
    command: str | None = None
    args: str = ""
    playbook: PlaybookSpec | None = None
    expanded_prompt: str | None = None
    meta_action: str | None = None
    error: str | None = None

    @property
    def is_local_meta(self) -> bool:
        return self.kind == "meta"

    @property
    def requires_llm(self) -> bool:
        return self.kind in ("playbook", "prompt")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "raw_input": self.raw_input,
            "command": self.command,
            "args": self.args,
            "playbook": self.playbook.to_dict() if self.playbook is not None else None,
            "expanded_prompt": self.expanded_prompt,
            "meta_action": self.meta_action,
            "error": self.error,
            "is_local_meta": self.is_local_meta,
            "requires_llm": self.requires_llm,
        }


def format_slash_help() -> str:
    """格式化所有内置斜杠工作流与本地元命令说明。"""
    lines = ["Embedded Slash Playbooks (expand to structured SOPs):"]
    for spec in PLAYBOOKS.values():
        lines.append(f"  {spec.usage:<22} - {spec.description}")
    lines.append("")
    lines.append("Local Meta Commands (handled locally without LLM call):")
    for cmd_name, desc in META_COMMANDS.items():
        lines.append(f"  /{cmd_name:<21} - {desc}")
    return "\n".join(lines)


def expand_slash_command(user_input: str) -> SlashResolution:
    """解析用户输入：区分嵌入式工作流 Playbooks、本地元命令或普通自然语言提问。"""
    raw = user_input or ""
    stripped = raw.strip()
    if not stripped.startswith("/"):
        return SlashResolution(
            kind="prompt",
            raw_input=raw,
            command=None,
            args="",
            playbook=None,
            expanded_prompt=stripped,
            meta_action=None,
            error=None,
        )

    parts = stripped.split(None, 1)
    cmd_token = parts[0].lower()
    cmd_key = cmd_token[1:]
    arg_text = parts[1].strip() if len(parts) > 1 else ""

    if cmd_key in PLAYBOOKS:
        spec = PLAYBOOKS[cmd_key]
        return SlashResolution(
            kind="playbook",
            raw_input=raw,
            command=spec.command,
            args=arg_text,
            playbook=spec,
            expanded_prompt=spec.render(arg_text),
            meta_action=None,
            error=None,
        )

    if cmd_key in META_COMMANDS:
        return SlashResolution(
            kind="meta",
            raw_input=raw,
            command=f"/{cmd_key}",
            args=arg_text,
            playbook=None,
            expanded_prompt=None,
            meta_action=cmd_key,
            error=None,
        )

    all_cmds = [f"/{k}" for k in PLAYBOOKS] + [f"/{k}" for k in META_COMMANDS]
    hints = difflib.get_close_matches(cmd_token, all_cmds, n=3, cutoff=0.5)
    hint_msg = f" Did you mean: {', '.join(hints)}?" if hints else ""
    return SlashResolution(
        kind="unknown",
        raw_input=raw,
        command=cmd_token,
        args=arg_text,
        playbook=None,
        expanded_prompt=None,
        meta_action=None,
        error=f"Unknown slash command {cmd_token!r}.{hint_msg} Run /help to list available commands.",
    )
