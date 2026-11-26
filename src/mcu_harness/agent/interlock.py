"""不可被 LLM 绕过的外部 HITL 硬件安全互锁与人工审批引擎。

设计原则：
    1. 互锁判定完全由宿主 Python Runtime 在工具执行前强制拦截，不依赖大模型自我约束。
    2. 前置防砖硬检查（Hard Deny Gate）：`write_memory` 命中 Option Bytes / Product State /
       `safety.deny_ranges` 时直接 `hard_denied=True`，绝不弹窗也不调用 `approver`。
    3. SVD 上下文预览：`write_memory` 若可解析出芯片 SVD 寄存器，自动用 `decode_register`
       生成人类可读的位域变更预览（`svd_preview`），帮助工程师在审批弹窗中看清每个 bit 含义。
    4. `hardware_danger`（如 `flash_firmware`、`write_memory`、`run_hil_tests`）严禁按会话
       免确认缓存（`session_tool` 对其无效），每次高危操作都必须单独人工确认。
"""

from __future__ import annotations

import difflib
import os
import re
import shlex
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from mcu_harness.config.manifest import ProjectProfile, discover_project
from mcu_harness.safety.guard import SafetyGuard, default_safety_guard
from mcu_harness.svd.decoder import decode_register
from mcu_harness.svd.parser import load_svd

VALID_INTERLOCK_MODES: tuple[str, ...] = ("interactive", "auto_safe", "read_only")
VALID_RISK_LEVELS: tuple[str, ...] = (
    "read_only",
    "workspace_write",
    "hardware_state",
    "hardware_danger",
    "shell",
)
VALID_APPROVAL_SCOPES: tuple[str, ...] = ("once", "session_tool", "deny")

# 永远禁止按 session_tool 跳过后续确认的高危硬件工具
_HARDWARE_DANGER_TOOLS: frozenset[str] = frozenset(
    {
        "flash_firmware",
        "write_memory",
        "run_hil_tests",
    }
)

# `probe_control` 中只读与变更状态的动作划分
_PROBE_READONLY_ACTIONS: frozenset[str] = frozenset({"info", "regs", "audit"})
_PROBE_STATE_ACTIONS: frozenset[str] = frozenset({"connect", "disconnect", "halt", "resume", "reset"})

# Shell 安全只读命令白名单（在 auto_safe 模式下自动放行，严格禁止任何管道/重定向/链式符号）
_SHELL_META_RE = re.compile(r"[;&|`$><\r\n]")
_SAFE_READONLY_SHELL_BINS: frozenset[str] = frozenset(
    {
        "ls",
        "ll",
        "la",
        "dir",
        "pwd",
        "cat",
        "head",
        "tail",
        "wc",
        "file",
        "stat",
        "which",
        "uname",
        "whoami",
        "id",
        "tree",
        "grep",
        "rg",
    }
)
_SAFE_GIT_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "status",
        "diff",
        "log",
        "show",
        "rev-parse",
        "describe",
    }
)
_FIND_UNSAFE_FLAGS: frozenset[str] = frozenset({"-exec", "-execdir", "-ok", "-okdir", "-delete"})


@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str
    tool_name: str
    risk_level: str
    summary: str
    details: dict[str, Any]
    diff_preview: str | None = None
    svd_preview: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    scope: str = "once"  # "once" | "session_tool" | "deny"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InterlockOutcome:
    allowed: bool
    hard_denied: bool
    reason: str
    request: ApprovalRequest | None = None
    decision: ApprovalDecision | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "hard_denied": self.hard_denied,
            "reason": self.reason,
            "request": self.request.to_dict() if self.request is not None else None,
            "decision": self.decision.to_dict() if self.decision is not None else None,
        }


def _parse_int_like(raw: Any) -> int:
    if isinstance(raw, bool):
        raise ValueError(f"Boolean is not a valid integer: {raw!r}")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        return int(raw.strip().replace("_", ""), 0)
    raise ValueError(f"Invalid integer value: {raw!r}")


def _resolve_path_in_project(raw_path: str | os.PathLike, project_dir: str) -> Path:
    p = Path(raw_path).expanduser()
    if not p.is_absolute():
        p = Path(project_dir).expanduser().resolve() / p
    return p.resolve()


def is_safe_readonly_shell_command(command: str) -> bool:
    """严格判断一条 shell 命令是否为无副作用的只读命令（拒绝任何链式拼接、重定向或命令替换）。"""
    cmd = (command or "").strip()
    if not cmd:
        return False
    if _SHELL_META_RE.search(cmd):
        return False
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False
    if not tokens:
        return False

    exe = Path(tokens[0]).name
    if exe == "git":
        if len(tokens) < 2:
            return False
        sub = tokens[1]
        if sub in _SAFE_GIT_SUBCOMMANDS:
            return True
        if sub == "branch":
            # 仅允许不带修改参数（如 -d/-D/-m/-M/-c/-C/-f/--delete/--move/--copy）的只读查询
            unsafe_branch_flags = {"-d", "-D", "-m", "-M", "-c", "-C", "-f", "--delete", "--move", "--copy"}
            return not any(tok in unsafe_branch_flags for tok in tokens[2:])
        return False

    if exe == "find":
        return not any(tok in _FIND_UNSAFE_FLAGS for tok in tokens[1:])

    return exe in _SAFE_READONLY_SHELL_BINS


class HarnessInterlock:
    """不可被 LLM 绕过的外部硬件安全互锁引擎。"""

    def __init__(
        self,
        *,
        mode: str = "interactive",
        safety_guard: SafetyGuard | None = None,
    ) -> None:
        norm_mode = (mode or "interactive").strip().lower()
        if norm_mode not in VALID_INTERLOCK_MODES:
            raise ValueError(f"Unsupported interlock mode {mode!r}; expected one of {VALID_INTERLOCK_MODES}")
        self.mode = norm_mode
        self.safety_guard = safety_guard or default_safety_guard
        self._lock = threading.Lock()
        self._session_approved_tools: set[str] = set()

    def session_approved_tools(self) -> set[str]:
        with self._lock:
            return set(self._session_approved_tools)

    def clear_session_approvals(self) -> None:
        with self._lock:
            self._session_approved_tools.clear()

    def _effective_risk_level(self, tool_name: str, risk_level: str, args: dict[str, Any]) -> str:
        norm_risk = (risk_level or "read_only").strip().lower()
        if tool_name == "read_debug_log":
            act = str(args.get("action") or "read").strip().lower()
            if act == "write":
                return "hardware_state"
            return "read_only"
        if tool_name == "probe_control":
            act = str(args.get("action") or "info").strip().lower()
            if act in _PROBE_READONLY_ACTIONS:
                return "read_only"
            if act in _PROBE_STATE_ACTIONS:
                return "hardware_state"
            return "hardware_state"
        if tool_name in _HARDWARE_DANGER_TOOLS:
            return "hardware_danger"
        return norm_risk

    def _check_write_memory_gate(
        self,
        args: dict[str, Any],
        *,
        project_dir: str,
        profile: ProjectProfile | None,
    ) -> tuple[InterlockOutcome | None, dict[str, Any], str | None]:
        """前置硬件防砖硬检查 + SVD 寄存器位域预览。"""
        if "address" not in args:
            return (
                InterlockOutcome(
                    allowed=False,
                    hard_denied=False,
                    reason="write_memory missing required 'address' argument.",
                ),
                {},
                None,
            )

        try:
            addr = _parse_int_like(args["address"])
        except ValueError as exc:
            return (
                InterlockOutcome(allowed=False, hard_denied=False, reason=f"Invalid write_memory address: {exc}"),
                {},
                None,
            )

        try:
            width = _parse_int_like(args.get("width", 32))
        except ValueError as exc:
            return (
                InterlockOutcome(allowed=False, hard_denied=False, reason=f"Invalid write_memory width: {exc}"),
                {},
                None,
            )
        if width not in (8, 16, 32):
            return (
                InterlockOutcome(allowed=False, hard_denied=False, reason=f"Unsupported write_memory width: {width}"),
                {},
                None,
            )

        step = width // 8
        parsed_vals: list[int] = []
        raw_values = args.get("values")
        if raw_values is None and "value" in args:
            raw_values = [args["value"]]

        if isinstance(raw_values, (list, tuple)):
            try:
                parsed_vals = [_parse_int_like(v) for v in raw_values]
            except ValueError as exc:
                return (
                    InterlockOutcome(allowed=False, hard_denied=False, reason=f"Invalid write_memory values: {exc}"),
                    {},
                    None,
                )
            num_bytes = max(1, len(parsed_vals) * step)
        elif isinstance(args.get("data"), (bytes, bytearray)):
            raw_bytes = bytes(args["data"])
            num_bytes = max(1, len(raw_bytes))
            if raw_bytes:
                parsed_vals = [int.from_bytes(raw_bytes[:step].ljust(step, b"\x00"), "little")]
        elif "num_bytes" in args:
            try:
                num_bytes = max(1, _parse_int_like(args["num_bytes"]))
            except ValueError as exc:
                return (
                    InterlockOutcome(allowed=False, hard_denied=False, reason=f"Invalid num_bytes: {exc}"),
                    {},
                    None,
                )
        else:
            num_bytes = step

        eff_dev = args.get("device") or (profile.target.device if profile else None)
        safety_cfg = profile.safety if profile else None
        peripheral_hint = args.get("peripheral_hint")

        # 关键防绕过点：此处强制传入 confirmed=False，先过 SafetyGuard 防砖硬拒绝门
        dec = self.safety_guard.check_memory_write(
            addr,
            num_bytes,
            device=eff_dev,
            config=safety_cfg,
            confirmed=False,
            peripheral_hint=peripheral_hint,
        )
        if dec.level == "hard_deny":
            return (
                InterlockOutcome(
                    allowed=False,
                    hard_denied=True,
                    reason=dec.reason,
                    request=None,
                    decision=None,
                ),
                {},
                None,
            )

        svd_preview: str | None = None
        matched_reg_label: str | None = None
        svd_path_raw = args.get("svd_path") or (profile.paths.svd_path if profile else None)
        if svd_path_raw and parsed_vals:
            svd_preview, matched_reg_label = self._build_svd_preview(
                svd_path_raw,
                addr=addr,
                first_val=parsed_vals[0],
                project_dir=project_dir,
            )

        details: dict[str, Any] = {
            "address": f"0x{addr:08X}",
            "width": width,
            "num_bytes": num_bytes,
            "device": eff_dev,
            "peripheral_hint": peripheral_hint,
            "values_hex": [f"0x{(v & ((1 << width) - 1)):0{width // 4}X}" for v in parsed_vals],
            "safety_level": dec.level,
            "safety_reason": dec.reason,
        }
        if matched_reg_label:
            details["matched_register"] = matched_reg_label
        return None, details, svd_preview

    @staticmethod
    def _build_svd_preview(
        svd_path_raw: str | os.PathLike,
        *,
        addr: int,
        first_val: int,
        project_dir: str,
    ) -> tuple[str | None, str | None]:
        try:
            svd_file = _resolve_path_in_project(svd_path_raw, project_dir)
            if not svd_file.is_file():
                return None, None
            svd_dev = load_svd(svd_file)
            for periph in svd_dev.peripherals:
                for reg in periph.registers:
                    if periph.reg_address(reg) == addr:
                        decoded = decode_register(periph, reg, first_val)
                        label = f"{periph.name}->{reg.name}"
                        return decoded.to_text(), label
        except Exception:
            return None, None
        return None, None

    @staticmethod
    def _build_workspace_diff_preview(
        tool_name: str,
        args: dict[str, Any],
        *,
        project_dir: str,
    ) -> tuple[str | None, dict[str, Any]]:
        raw_path = args.get("path") or args.get("file_path") or args.get("target_file")
        if not raw_path:
            return None, {}
        target = _resolve_path_in_project(str(raw_path), project_dir)
        proj_root = Path(project_dir).expanduser().resolve()
        try:
            rel_name = target.relative_to(proj_root).as_posix()
        except ValueError:
            rel_name = str(target)

        if tool_name == "edit_file":
            old_text = ""
            if target.is_file():
                try:
                    old_text = target.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    old_text = ""

            new_text = _compute_edited_text(old_text, args)
            if new_text is None:
                return f"[edit_file] {rel_name} (unable to compute diff preview from args)", {
                    "path": str(target),
                    "relative_path": rel_name,
                }
            old_lines = old_text.splitlines(keepends=True)
            new_lines = new_text.splitlines(keepends=True)
            diff_lines = list(
                difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile=f"a/{rel_name}",
                    tofile=f"b/{rel_name}",
                    lineterm="\n",
                )
            )
            diff_str = "".join(diff_lines) if diff_lines else f"(no textual diff for {rel_name})"
            return diff_str, {
                "path": str(target),
                "relative_path": rel_name,
                "exists": target.is_file(),
            }

        if tool_name == "write_file":
            content = str(args.get("content") if args.get("content") is not None else args.get("text", ""))
            lines = content.splitlines()
            max_preview_lines = 40
            preview_body = "\n".join(lines[:max_preview_lines])
            if len(lines) > max_preview_lines:
                preview_body += f"\n... (+{len(lines) - max_preview_lines} more lines)"
            action_tag = "overwrite" if target.is_file() else "create"
            preview = f"[{action_tag}] {rel_name} ({len(lines)} lines, {len(content.encode('utf-8'))} bytes)\n{preview_body}"
            return preview, {
                "path": str(target),
                "relative_path": rel_name,
                "exists": target.is_file(),
                "total_lines": len(lines),
                "total_bytes": len(content.encode("utf-8")),
            }

        return None, {"path": str(target), "relative_path": rel_name}

    @staticmethod
    def _build_flash_details(
        args: dict[str, Any],
        *,
        project_dir: str,
        profile: ProjectProfile | None,
    ) -> dict[str, Any]:
        raw_fw = (
            args.get("firmware_path")
            or args.get("elf_path")
            or os.environ.get("MCU_HARNESS_ELF_PATH")
            or (profile.paths.elf_path if profile else None)
        )
        eff_dev = args.get("device") or (profile.target.device if profile else None)
        eff_backend = args.get("backend") or (profile.target.probe_backend if profile else None)

        details: dict[str, Any] = {
            "device": eff_dev,
            "backend": eff_backend,
            "firmware_path": None,
            "exists": False,
            "file_size_bytes": None,
            "mtime_iso": None,
            "mtime_epoch": None,
            "verify": args.get("verify", True),
            "reset_after": args.get("reset_after", True),
            "run_after": args.get("run_after", True),
        }
        if raw_fw:
            fw_path = _resolve_path_in_project(str(raw_fw), project_dir)
            details["firmware_path"] = str(fw_path)
            if fw_path.is_file():
                st = fw_path.stat()
                details["exists"] = True
                details["file_size_bytes"] = st.st_size
                details["mtime_epoch"] = st.st_mtime
                details["mtime_iso"] = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        return details

    @staticmethod
    def _build_summary(tool_name: str, effective_risk: str, args: dict[str, Any], details: dict[str, Any]) -> str:
        if tool_name == "write_memory":
            addr_s = details.get("address", str(args.get("address", "?")))
            nbytes = details.get("num_bytes", "?")
            reg_lbl = details.get("matched_register")
            dev = details.get("device") or "target"
            if reg_lbl:
                return f"Write {nbytes}B to {reg_lbl} ({addr_s}) on {dev}"
            return f"Write {nbytes}B to {addr_s} on {dev}"
        if tool_name == "flash_firmware":
            fw = details.get("firmware_path") or "(unresolved)"
            sz = details.get("file_size_bytes")
            dev = details.get("device") or "target"
            sz_txt = f"{sz} bytes" if sz is not None else "unknown size"
            return f"Flash {Path(fw).name if fw != '(unresolved)' else fw} ({sz_txt}) to {dev}"
        if tool_name in ("edit_file", "write_file"):
            rel = details.get("relative_path") or args.get("path") or args.get("file_path") or "?"
            return f"{tool_name}: {rel}"
        if effective_risk == "shell":
            cmd = str(args.get("command") or args.get("cmd") or "").strip()
            short_cmd = cmd if len(cmd) <= 80 else cmd[:77] + "..."
            return f"Run shell command: {short_cmd}"
        if tool_name == "probe_control":
            act = args.get("action", "?")
            return f"Probe control action: {act}"
        if tool_name == "read_debug_log":
            src = args.get("source", "rtt")
            cmd = str(args.get("command") or "")
            return f"Write command to {src} debug log: {cmd[:60]}"
        return f"Execute {tool_name} (risk={effective_risk})"

    def authorize(
        self,
        tool_name: str,
        risk_level: str,
        args: dict[str, Any],
        *,
        project_dir: str,
        profile: ProjectProfile | None = None,
        approver: Callable[[ApprovalRequest], ApprovalDecision] | None = None,
    ) -> InterlockOutcome:
        """对单次工具调用执行不可绕过的安全互锁检查与人工 HITL 审批。"""
        safe_args = dict(args or {})
        eff_profile = profile
        if eff_profile is None and project_dir:
            try:
                if Path(project_dir).expanduser().is_dir():
                    eff_profile = discover_project(project_dir)
            except Exception:
                eff_profile = None

        eff_risk = self._effective_risk_level(tool_name, risk_level, safe_args)
        is_danger = (eff_risk == "hardware_danger") or (tool_name in _HARDWARE_DANGER_TOOLS)

        # 1. 前置硬件防砖硬检查（Hard Deny Gate）——无论何种模式或 approver 都最先拦截
        extra_details: dict[str, Any] = {}
        svd_preview: str | None = None
        diff_preview: str | None = None

        if tool_name == "write_memory":
            gate_outcome, mem_details, svd_preview = self._check_write_memory_gate(
                safe_args,
                project_dir=project_dir,
                profile=eff_profile,
            )
            if gate_outcome is not None:
                return gate_outcome
            extra_details.update(mem_details)

        # 2. 只读动作直接放行（含 probe_control(info/regs/audit) 与 read_debug_log 非 write 动作）
        if eff_risk == "read_only":
            return InterlockOutcome(
                allowed=True,
                hard_denied=False,
                reason=f"Read-only operation '{tool_name}' allowed automatically.",
            )

        # 3. read_only 模式：一律禁止任何写入、硬件状态变更或 shell 执行
        if self.mode == "read_only":
            reason = (
                f"Blocked by interlock mode='read_only': {tool_name} "
                f"(risk={eff_risk}) is not permitted."
            )
            self.safety_guard.record_audit(
                tool_name,
                str(extra_details.get("address") or safe_args.get("path") or project_dir),
                level=eff_risk,
                allowed=False,
                detail=reason,
            )
            return InterlockOutcome(
                allowed=False,
                hard_denied=False,
                reason=reason,
            )

        # 4. auto_safe 模式：自动放行 workspace_write 与严格只读的 shell 命令
        if self.mode == "auto_safe":
            if eff_risk == "workspace_write":
                return InterlockOutcome(
                    allowed=True,
                    hard_denied=False,
                    reason=f"Workspace write '{tool_name}' auto-approved under mode='auto_safe'.",
                )
            if eff_risk == "shell":
                cmd = str(safe_args.get("command") or safe_args.get("cmd") or "")
                if is_safe_readonly_shell_command(cmd):
                    return InterlockOutcome(
                        allowed=True,
                        hard_denied=False,
                        reason=f"Read-only shell command auto-approved under mode='auto_safe': {cmd}",
                    )

        # 5. 会话级免确认缓存检查（严格排除 hardware_danger！）
        if not is_danger:
            with self._lock:
                if tool_name in self._session_approved_tools:
                    return InterlockOutcome(
                        allowed=True,
                        hard_denied=False,
                        reason=f"Tool '{tool_name}' allowed by session_tool approval.",
                    )

        # 6. 组装人类可读的审批详情（Diff / Flash / SVD）
        if tool_name in ("edit_file", "write_file"):
            diff_preview, ws_details = self._build_workspace_diff_preview(
                tool_name,
                safe_args,
                project_dir=project_dir,
            )
            extra_details.update(ws_details)
        elif tool_name == "flash_firmware":
            extra_details.update(
                self._build_flash_details(
                    safe_args,
                    project_dir=project_dir,
                    profile=eff_profile,
                )
            )

        req_details: dict[str, Any] = {
            "mode": self.mode,
            "project_dir": project_dir,
            "args": safe_args,
        }
        req_details.update(extra_details)

        summary = self._build_summary(tool_name, eff_risk, safe_args, req_details)
        req = ApprovalRequest(
            request_id=f"req-{uuid.uuid4().hex[:12]}",
            tool_name=tool_name,
            risk_level=eff_risk,
            summary=summary,
            details=req_details,
            diff_preview=diff_preview,
            svd_preview=svd_preview,
        )

        # 7. 无交互式审批器时安全拒绝
        if approver is None:
            reason = (
                f"HITL approval required for '{tool_name}' (risk={eff_risk}), "
                "but no interactive approver channel is configured."
            )
            self.safety_guard.record_audit(
                tool_name,
                summary,
                level=eff_risk,
                allowed=False,
                detail=reason,
            )
            return InterlockOutcome(
                allowed=False,
                hard_denied=False,
                reason=reason,
                request=req,
                decision=None,
            )

        decision = approver(req)
        if decision.scope not in VALID_APPROVAL_SCOPES:
            reason = f"Invalid approval scope {decision.scope!r}; treating as deny."
            self.safety_guard.record_audit(tool_name, summary, level=eff_risk, allowed=False, detail=reason)
            return InterlockOutcome(
                allowed=False,
                hard_denied=False,
                reason=reason,
                request=req,
                decision=decision,
            )

        if decision.approved and decision.scope != "deny":
            # 关键约束：hardware_danger 绝对禁止加入 _session_approved_tools！
            if decision.scope == "session_tool" and not is_danger:
                with self._lock:
                    self._session_approved_tools.add(tool_name)
            reason = decision.reason or f"Approved by human operator (scope={decision.scope})"
            self.safety_guard.record_audit(tool_name, summary, level=eff_risk, allowed=True, detail=reason)
            return InterlockOutcome(
                allowed=True,
                hard_denied=False,
                reason=reason,
                request=req,
                decision=decision,
            )

        deny_reason = decision.reason or f"Rejected by human operator for '{tool_name}'"
        self.safety_guard.record_audit(tool_name, summary, level=eff_risk, allowed=False, detail=deny_reason)
        return InterlockOutcome(
            allowed=False,
            hard_denied=False,
            reason=deny_reason,
            request=req,
            decision=decision,
        )


def _compute_edited_text(old_text: str, args: dict[str, Any]) -> str | None:
    """根据 edit_file 参数计算修改后文本，用于生成 unified diff 预览。"""
    if isinstance(args.get("new_content"), str):
        return args["new_content"]
    if isinstance(args.get("content"), str) and "old_text" not in args and "target" not in args:
        return args["content"]

    edits = args.get("edits")
    if isinstance(edits, list):
        cur = old_text
        for item in edits:
            if not isinstance(item, dict):
                return None
            old_seg = _pick_first_str(item, ("old_text", "old_str", "old_string", "target"))
            new_seg = _pick_first_str(item, ("new_text", "new_str", "new_string", "replacement"))
            if old_seg is None or new_seg is None:
                return None
            if old_seg and old_seg in cur:
                cur = cur.replace(old_seg, new_seg, 1)
            elif not old_seg:
                cur = cur + new_seg
        return cur

    old_seg = _pick_first_str(args, ("old_text", "old_str", "old_string", "target"))
    new_seg = _pick_first_str(args, ("new_text", "new_str", "new_string", "replacement"))
    if old_seg is not None and new_seg is not None:
        if old_seg == "":
            return old_text + new_seg
        if old_seg in old_text:
            return old_text.replace(old_seg, new_seg, 1)
        # 若旧文本暂未精确命中，仍给出追加/替换预览以便审阅
        return old_text + ("\n" if old_text and not old_text.endswith("\n") else "") + new_seg

    return None


def _pick_first_str(mapping: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        val = mapping.get(k)
        if isinstance(val, str):
            return val
    return None
