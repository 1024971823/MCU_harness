"""声明式硬件在环（HIL）自动化测试引擎。

核心特性：
    * 零第三方依赖配置加载：原生支持 `.toml`（标准库 `tomllib`）与 `.json`，可选支持 `.yaml`/`.yml`。
    * 完整硬件控制与观测原语：reset / halt / resume / sleep_ms / write_memory / rtt_write / uart_write /
      assert_variable（支持带超时的异步轮询） / assert_register / assert_memory / assert_log / assert_no_crash。
    * 不可绕过安全护栏：所有 `write_memory` 均经过 `default_safety_guard.check_memory_write(..., confirmed=confirmed)`
      拦截，Option Bytes 永拒、未确认外设写入直接拦截失败。
    * 强制安全收尾契约（Safe-Off Guarantee）：无论测试步骤断言失败还是发生异常，用例级与套件级 `teardown`
      始终放在 `try ... finally` 中无条件执行，确保安全停机动作（如关闭加热器/PWM）必定落盘。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from mcu_harness.config import SafetyConfig, discover_project
from mcu_harness.crash import diagnose_crash
from mcu_harness.dwarf import DwarfError, LiveWatchError, VariableValue, read_variable
from mcu_harness.elf import ElfError
from mcu_harness.log import RingLogBuffer, RttLogCollector, UartError, UartLogCollector
from mcu_harness.probe import (
    DebugProbe,
    JLinkProbe,
    MockProbe,
    OpenOCDProbe,
    ProbeError,
    inspect_peripheral_registers,
)
from mcu_harness.safety import default_safety_guard
from mcu_harness.svd import SvdDevice, load_svd, parse_int_value

from .logic_analyzer import HilError

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


class HilConfigError(HilError):
    """HIL 测试套件配置文件加载或结构校验错误。"""


class _HilStepFailure(AssertionError):
    """单步测试断言或安全护栏未通过。"""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


@dataclass
class HilStepResult:
    """单个 HIL 测试步骤的执行结果。"""

    op: str
    phase: str  # "setup" | "test" | "teardown"
    index: int
    passed: bool
    duration_ms: float
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HilTestReport:
    """单个测试用例的执行报告（含强制执行的 teardown）。"""

    name: str
    passed: bool
    duration_ms: float
    steps: list[HilStepResult] = field(default_factory=list)
    teardown: list[HilStepResult] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "duration_ms": self.duration_ms,
            "steps": [s.to_dict() for s in self.steps],
            "teardown": [t.to_dict() for t in self.teardown],
            "error": self.error,
        }


@dataclass
class HilSuiteReport:
    """整套 HIL 测试执行总报告。"""

    name: str
    passed: bool
    total_tests: int
    passed_tests: int
    failed_tests: int
    duration_ms: float
    setup: list[HilStepResult] = field(default_factory=list)
    tests: list[HilTestReport] = field(default_factory=list)
    teardown: list[HilStepResult] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "failed_tests": self.failed_tests,
            "duration_ms": self.duration_ms,
            "setup": [s.to_dict() for s in self.setup],
            "tests": [t.to_dict() for t in self.tests],
            "teardown": [t.to_dict() for t in self.teardown],
            "error": self.error,
        }

    def to_text(self) -> str:
        badge = "PASS" if self.passed else "FAIL"
        lines = [
            f"=== HIL Suite [{badge}] {self.name} ({self.passed_tests}/{self.total_tests} passed, {self.duration_ms:.1f} ms) ===",
        ]
        if self.error:
            lines.append(f"Suite Error: {self.error}")
        if self.setup:
            lines.append("Suite Setup:")
            for s in self.setup:
                tag = "PASS" if s.passed else "FAIL"
                lines.append(f"  [{tag}] #{s.index} {s.op}: {s.message}")
        for tc in self.tests:
            tc_tag = "PASS" if tc.passed else "FAIL"
            lines.append(f"\nTest [{tc_tag}] {tc.name} ({tc.duration_ms:.1f} ms)")
            for s in tc.steps:
                tag = "PASS" if s.passed else "FAIL"
                lines.append(f"  [{tag}] #{s.index} {s.op}: {s.message}")
            if tc.teardown:
                lines.append("  Teardown (Safe-Off):")
                for td in tc.teardown:
                    tag = "PASS" if td.passed else "FAIL"
                    lines.append(f"    [{tag}] #{td.index} {td.op}: {td.message}")
        if self.teardown:
            lines.append("\nSuite Teardown (Safe-Off):")
            for td in self.teardown:
                tag = "PASS" if td.passed else "FAIL"
                lines.append(f"  [{tag}] #{td.index} {td.op}: {td.message}")
        return "\n".join(lines)


def _validate_steps_list(raw: Any, label: str) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise HilConfigError(f"HIL spec field '{label}' must be a list of step objects")
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise HilConfigError(f"HIL step {label}[{idx}] must be a table/dict")
        op = item.get("op")
        if not isinstance(op, str) or not op.strip():
            raise HilConfigError(f"HIL step {label}[{idx}] is missing a valid 'op' string")
        out.append(dict(item))
    return out


def load_hil_spec(spec: str | os.PathLike | dict[str, Any]) -> tuple[dict[str, Any], Path | None]:
    """加载并校验 HIL 测试套件规格（支持 dict / .toml / .json / 可选 .yaml）。"""
    base_dir: Path | None = None
    if isinstance(spec, dict):
        data = dict(spec)
        default_name = "Inline HIL Suite"
    elif isinstance(spec, (str, os.PathLike)):
        p = Path(spec).expanduser().resolve()
        if not p.is_file():
            raise HilConfigError(f"HIL suite file not found: {p}")
        base_dir = p.parent
        default_name = p.stem
        text = p.read_text(encoding="utf-8")
        suffix = p.suffix.lower()
        if suffix == ".toml":
            try:
                data = tomllib.loads(text)
            except Exception as exc:
                raise HilConfigError(f"Failed to parse TOML HIL suite {p}: {exc}") from exc
        elif suffix == ".json":
            try:
                data = json.loads(text)
            except Exception as exc:
                raise HilConfigError(f"Failed to parse JSON HIL suite {p}: {exc}") from exc
        elif suffix in (".yaml", ".yml"):
            try:
                import yaml  # type: ignore[import-not-found]
            except ModuleNotFoundError as exc:
                raise HilConfigError(
                    "PyYAML is not installed; please use a .toml (recommended) or .json HIL suite file."
                ) from exc
            try:
                data = yaml.safe_load(text)
            except Exception as exc:
                raise HilConfigError(f"Failed to parse YAML HIL suite {p}: {exc}") from exc
        else:
            raise HilConfigError(
                f"Unsupported HIL suite extension {suffix!r} for {p} (supported: .toml, .json, .yaml/.yml)"
            )
    else:
        raise HilConfigError(f"Unsupported HIL spec type: {type(spec).__name__}")

    if not isinstance(data, dict):
        raise HilConfigError("HIL suite root must be a table/dict")

    name = str(data.get("name") or default_name).strip()
    setup_steps = _validate_steps_list(data.get("setup"), "setup")
    teardown_steps = _validate_steps_list(data.get("teardown"), "teardown")

    raw_tests = data.get("tests")
    if not isinstance(raw_tests, list):
        raise HilConfigError("HIL suite must define a 'tests' list")

    tests: list[dict[str, Any]] = []
    for idx, tc in enumerate(raw_tests):
        if not isinstance(tc, dict):
            raise HilConfigError(f"HIL tests[{idx}] must be a table/dict")
        tc_name = str(tc.get("name") or f"test_{idx + 1}").strip()
        tc_setup = _validate_steps_list(tc.get("setup"), f"tests[{idx}].setup")
        tc_steps = _validate_steps_list(tc.get("steps"), f"tests[{idx}].steps")
        tc_teardown = _validate_steps_list(tc.get("teardown"), f"tests[{idx}].teardown")
        tests.append(
            {
                "name": tc_name,
                "setup": tc_setup,
                "steps": tc_steps,
                "teardown": tc_teardown,
            }
        )

    normalized = dict(data)
    normalized["name"] = name
    normalized["setup"] = setup_steps
    normalized["teardown"] = teardown_steps
    normalized["tests"] = tests
    return normalized, base_dir


def _resolve_opt_path(raw: Any, base_dir: Path | None) -> str | None:
    if raw in (None, ""):
        return None
    p = Path(str(raw)).expanduser()
    if not p.is_absolute() and base_dir is not None:
        p = base_dir / p
    return str(p.resolve())


def _parse_int(val: Any, field_name: str) -> int:
    if isinstance(val, bool):
        raise HilConfigError(f"Field '{field_name}' expected integer, got bool")
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        try:
            return int(val.strip().replace("_", ""), 0)
        except ValueError as exc:
            raise HilConfigError(f"Invalid integer for '{field_name}': {val!r}") from exc
    raise HilConfigError(f"Invalid integer for '{field_name}': {val!r}")


def _values_equal(actual: Any, expected: Any) -> bool:
    if isinstance(expected, bool):
        return bool(actual) is expected
    if isinstance(actual, bool) and isinstance(expected, (int, float)):
        return int(actual) == expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return actual == expected
    return actual == expected


def _eval_variable_assertions(val: VariableValue, step: dict[str, Any]) -> tuple[bool, str]:
    checks: list[str] = []
    failures: list[str] = []

    has_cond = False
    if "enum" in step:
        has_cond = True
        expected_enum = str(step["enum"])
        checks.append(f"enum=={expected_enum!r}")
        if val.enum_name is None or val.enum_name != expected_enum:
            failures.append(f"expected enum {expected_enum!r}, got {val.enum_name!r} ({val.display})")

    if "eq" in step:
        has_cond = True
        exp = step["eq"]
        checks.append(f"eq={exp!r}")
        if val.kind == "enum" and isinstance(exp, str):
            ok = val.enum_name == exp
            act_repr = val.enum_name
        elif val.kind == "array" and val.string_value is not None and isinstance(exp, str):
            ok = val.string_value == exp
            act_repr = val.string_value
        else:
            act = val.raw_value if val.raw_value is not None else val.value
            ok = _values_equal(act, exp)
            act_repr = act
        if not ok:
            failures.append(f"expected == {exp!r}, got {act_repr!r}")

    if "ne" in step:
        has_cond = True
        exp = step["ne"]
        checks.append(f"ne={exp!r}")
        if val.kind == "enum" and isinstance(exp, str):
            ok = val.enum_name != exp
            act_repr = val.enum_name
        else:
            act = val.raw_value if val.raw_value is not None else val.value
            ok = not _values_equal(act, exp)
            act_repr = act
        if not ok:
            failures.append(f"expected != {exp!r}, got {act_repr!r}")

    numeric_ops = (
        ("gt", lambda a, b: a > b, ">"),
        ("ge", lambda a, b: a >= b, ">="),
        ("min", lambda a, b: a >= b, ">="),
        ("lt", lambda a, b: a < b, "<"),
        ("le", lambda a, b: a <= b, "<="),
        ("max", lambda a, b: a <= b, "<="),
    )
    for key, fn, sym in numeric_ops:
        if key in step:
            has_cond = True
            bound = step[key]
            checks.append(f"{key}={bound!r}")
            act = val.raw_value
            if isinstance(act, bool) or not isinstance(act, (int, float)):
                failures.append(f"cannot apply numeric '{key}' to non-numeric value {val.display!r}")
            elif not fn(act, bound):
                failures.append(f"expected {act} {sym} {bound}")

    if not has_cond:
        raise HilConfigError("assert_variable requires at least one assertion (eq/ne/gt/ge/lt/le/min/max/enum)")

    if failures:
        return False, f"{val.expr}={val.display} failed ({'; '.join(failures)})"
    return True, f"{val.expr}={val.display} matched ({', '.join(checks)})"


class _HilRunnerContext:
    """单次 HIL 套件执行上下文（缓存 SVD、管理日志缓冲与安全收尾）。"""

    def __init__(
        self,
        *,
        probe: DebugProbe,
        project_dir: str | None,
        elf_path: str | None,
        svd_path: str | None,
        rtt_collector: RttLogCollector | None,
        uart_collector: UartLogCollector | None,
        confirmed: bool,
        safety_config: SafetyConfig,
    ) -> None:
        self.probe = probe
        self.project_dir = project_dir
        self.elf_path = elf_path
        self.svd_path = svd_path
        self.rtt_collector = rtt_collector
        self.uart_collector = uart_collector
        self.confirmed = confirmed
        self.safety_config = safety_config
        self._svd_cache: dict[str, SvdDevice] = {}
        self._local_rtt_buf: RingLogBuffer | None = None
        self._local_rtt_started = False

    def _get_svd(self) -> SvdDevice:
        if not self.svd_path:
            raise HilConfigError("assert_register requires svd_path (pass svd_path=... or --svd)")
        if self.svd_path not in self._svd_cache:
            self._svd_cache[self.svd_path] = load_svd(self.svd_path)
        return self._svd_cache[self.svd_path]

    def _ensure_rtt_started(self, channel: int = 0) -> None:
        if self.rtt_collector is not None:
            if self.rtt_collector.is_running():
                return
            try:
                self.rtt_collector.start(channel=channel, elf_path=self.elf_path)
            except ProbeError as exc:
                # 宿主机测试 ELF 未编译进 _SEGGER_RTT 时，在 MockProbe 上回退无地址自动扫描模式
                if self.probe.info().backend == "mock" and "_SEGGER_RTT" in str(exc):
                    self.rtt_collector.start(channel=channel, elf_path=None)
                else:
                    raise
            return

        if not self._local_rtt_started:
            self.probe.rtt_start()
            self._local_rtt_started = True
        if self._local_rtt_buf is None:
            self._local_rtt_buf = RingLogBuffer()

    def _read_logs(self, source: str, *, since_seq: int | None) -> tuple[list[str], int | None]:
        src = source.strip().lower()
        if src == "rtt":
            if self.rtt_collector is not None:
                self._ensure_rtt_started(channel=0)
                res, _ = self.rtt_collector.read(
                    tail_lines=2000,
                    since_seq=since_seq,
                    include_partial=True,
                    auto_start=False,
                )
            else:
                self._ensure_rtt_started(channel=0)
                assert self._local_rtt_buf is not None
                chunk = self.probe.rtt_read(channel=0, max_bytes=4096)
                if chunk:
                    self._local_rtt_buf.feed_bytes(chunk)
                res = self._local_rtt_buf.read(
                    tail_lines=2000,
                    since_seq=since_seq,
                    include_partial=True,
                )
        elif src == "uart":
            if self.uart_collector is None:
                raise HilConfigError("assert_log(source='uart') requires uart_collector")
            if not self.uart_collector.is_running():
                self.uart_collector.start()
            res, _ = self.uart_collector.read(
                tail_lines=2000,
                since_seq=since_seq,
                include_partial=True,
                auto_start=False,
            )
        else:
            raise HilConfigError(f"Unsupported assert_log source {source!r} (expected 'rtt' or 'uart')")

        lines = [e.text for e in res.entries]
        if res.partial_line:
            lines.append(res.partial_line)
        return lines, res.newest_seq

    def execute_step(self, step: dict[str, Any], *, phase: str, index: int) -> HilStepResult:
        op = str(step["op"]).strip()
        t0 = time.perf_counter()
        try:
            message, details = self._dispatch_step(op, step)
            dt_ms = round((time.perf_counter() - t0) * 1000.0, 3)
            return HilStepResult(
                op=op,
                phase=phase,
                index=index,
                passed=True,
                duration_ms=dt_ms,
                message=message,
                details=details,
            )
        except _HilStepFailure as exc:
            dt_ms = round((time.perf_counter() - t0) * 1000.0, 3)
            return HilStepResult(
                op=op,
                phase=phase,
                index=index,
                passed=False,
                duration_ms=dt_ms,
                message=str(exc),
                details=exc.details,
            )
        except (ProbeError, LiveWatchError, DwarfError, ElfError, UartError, HilError, KeyError, ValueError) as exc:
            dt_ms = round((time.perf_counter() - t0) * 1000.0, 3)
            return HilStepResult(
                op=op,
                phase=phase,
                index=index,
                passed=False,
                duration_ms=dt_ms,
                message=f"{type(exc).__name__}: {exc}",
            )
        except Exception as exc:
            dt_ms = round((time.perf_counter() - t0) * 1000.0, 3)
            return HilStepResult(
                op=op,
                phase=phase,
                index=index,
                passed=False,
                duration_ms=dt_ms,
                message=f"Unexpected {type(exc).__name__}: {exc}",
            )

    def _dispatch_step(self, op: str, step: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if op == "reset":
            halt = bool(step.get("halt", False))
            res = self.probe.reset(halt=halt)
            return res.message or f"reset(halt={halt})", res.to_dict()

        if op == "halt":
            changed = self.probe.halt()
            return f"target halted (changed={changed})", {"halted": True, "changed": changed}

        if op == "resume":
            changed = self.probe.resume()
            return f"target resumed (changed={changed})", {"halted": False, "changed": changed}

        if op == "sleep_ms":
            ms = float(step["ms"])
            if ms < 0:
                raise HilConfigError(f"sleep_ms requires ms >= 0, got {ms}")
            time.sleep(ms / 1000.0)
            return f"slept {ms} ms", {"ms": ms}

        if op == "write_memory":
            addr = _parse_int(step["address"], "address")
            width = int(step.get("width", 32))
            if width not in (8, 16, 32):
                raise HilConfigError(f"write_memory width must be 8, 16, or 32, got {width}")
            raw_vals = step["values"]
            if not isinstance(raw_vals, list):
                raw_vals = [raw_vals]
            if not raw_vals:
                raise HilConfigError("write_memory 'values' must not be empty")
            vals = [_parse_int(v, "values") for v in raw_vals]
            num_bytes = len(vals) * (width // 8)
            device = self.probe.info().device if self.probe.is_connected() else None
            periph_hint = step.get("peripheral_hint")

            decision = default_safety_guard.check_memory_write(
                addr,
                num_bytes,
                device=device,
                config=self.safety_config,
                confirmed=self.confirmed,
                peripheral_hint=str(periph_hint) if periph_hint is not None else None,
            )
            if not decision.allowed:
                raise _HilStepFailure(
                    decision.reason,
                    details={"safety_decision": decision.to_dict()},
                )

            if width == 32:
                self.probe.write_u32(addr, vals)
            elif width == 16:
                self.probe.write_u16(addr, vals)
            else:
                self.probe.write_u8(addr, vals)
            return (
                f"wrote {len(vals)}x{width}-bit value(s) to 0x{addr:08X}",
                {
                    "address": f"0x{addr:08X}",
                    "width": width,
                    "count": len(vals),
                    "safety_decision": decision.to_dict(),
                },
            )

        if op == "rtt_write":
            cmd = str(step.get("command") if "command" in step else step["data"])
            channel = int(step.get("channel", 0))
            encoding = str(step.get("encoding", "utf-8"))
            self._ensure_rtt_started(channel=channel)
            written = self.probe.rtt_write(cmd.encode(encoding), channel=channel)
            return f"wrote {written} byte(s) to RTT ch{channel}", {"channel": channel, "bytes_written": written}

        if op == "uart_write":
            if self.uart_collector is None:
                raise HilConfigError("uart_write requires uart_collector")
            cmd = str(step.get("command") if "command" in step else step["data"])
            encoding = str(step.get("encoding", "utf-8"))
            timeout_s = float(step.get("timeout_s", 1.0))
            written = self.uart_collector.write(cmd, encoding=encoding, timeout_s=timeout_s)
            return f"wrote {written} byte(s) to UART", {"bytes_written": written}

        if op == "assert_variable":
            if not self.elf_path:
                raise HilConfigError("assert_variable requires elf_path (pass elf_path=... or --elf)")
            expr = str(step["expr"])
            timeout_ms = float(step.get("timeout_ms", 0.0))
            poll_ms = float(step.get("poll_ms", 10.0))
            poll_s = max(0.001, poll_ms / 1000.0)
            t_start = time.monotonic()
            deadline = t_start + max(0.0, timeout_ms) / 1000.0
            attempts = 0
            last_msg = ""
            last_val: VariableValue | None = None

            while True:
                attempts += 1
                last_val = read_variable(self.probe, self.elf_path, expr)
                ok, last_msg = _eval_variable_assertions(last_val, step)
                if ok:
                    return last_msg, {
                        "expr": expr,
                        "attempts": attempts,
                        "value": last_val.to_dict(),
                    }
                now = time.monotonic()
                if now >= deadline:
                    break
                time.sleep(min(poll_s, max(0.001, deadline - now)))

            raise _HilStepFailure(
                f"{last_msg} after {attempts} attempt(s) (timeout={timeout_ms}ms)",
                details={
                    "expr": expr,
                    "attempts": attempts,
                    "value": last_val.to_dict() if last_val is not None else None,
                },
            )

        if op == "assert_register":
            periph_name = str(step["peripheral"])
            reg_name = str(step["register"])
            field_name = str(step["field"]) if step.get("field") else None
            if "eq" not in step and "ne" not in step:
                raise HilConfigError("assert_register requires 'eq' and/or 'ne'")

            svd = self._get_svd()
            timeout_ms = float(step.get("timeout_ms", 0.0))
            poll_ms = float(step.get("poll_ms", 10.0))
            poll_s = max(0.001, poll_ms / 1000.0)
            deadline = time.monotonic() + max(0.0, timeout_ms) / 1000.0
            attempts = 0

            while True:
                attempts += 1
                dec = inspect_peripheral_registers(self.probe, svd, periph_name, register_name=reg_name)
                if field_name is not None:
                    matched_field = None
                    target_f = field_name.strip().upper()
                    for f in dec.fields:
                        if f.name.upper() == target_f:
                            matched_field = f
                            break
                    if matched_field is None:
                        avail = ", ".join(f.name for f in dec.fields) or "<none>"
                        raise HilConfigError(
                            f"Field {field_name!r} not found in {dec.peripheral}->{dec.register} (available: {avail})"
                        )
                    actual_int = matched_field.value
                    actual_desc = f"{dec.peripheral}->{dec.register}.{matched_field.name}={actual_int} ({matched_field.hex_value})"
                else:
                    actual_int = dec.raw_value
                    actual_desc = f"{dec.peripheral}->{dec.register}={dec.raw_hex} ({actual_int})"

                failures: list[str] = []
                if "eq" in step:
                    exp_raw = step["eq"]
                    if field_name is not None and isinstance(exp_raw, str) and not _looks_numeric(exp_raw):
                        meaning = matched_field.meaning or ""
                        if exp_raw.lower() not in meaning.lower():
                            failures.append(f"expected enum {exp_raw!r}, got {meaning or actual_desc}")
                    else:
                        exp_int = parse_int_value(exp_raw)
                        if actual_int != exp_int:
                            failures.append(f"expected == {exp_int} (0x{exp_int:X}), got {actual_int} (0x{actual_int:X})")

                if "ne" in step:
                    exp_raw = step["ne"]
                    if field_name is not None and isinstance(exp_raw, str) and not _looks_numeric(exp_raw):
                        meaning = matched_field.meaning or ""
                        if exp_raw.lower() in meaning.lower():
                            failures.append(f"expected enum != {exp_raw!r}, got {meaning}")
                    else:
                        exp_int = parse_int_value(exp_raw)
                        if actual_int == exp_int:
                            failures.append(f"expected != {exp_int} (0x{exp_int:X}), got {actual_int} (0x{actual_int:X})")

                if not failures:
                    return f"{actual_desc} passed", {
                        "peripheral": dec.peripheral,
                        "register": dec.register,
                        "field": field_name,
                        "raw_hex": dec.raw_hex,
                        "actual": actual_int,
                        "attempts": attempts,
                    }

                now = time.monotonic()
                if now >= deadline:
                    raise _HilStepFailure(
                        f"{actual_desc} failed ({'; '.join(failures)})",
                        details={
                            "peripheral": dec.peripheral,
                            "register": dec.register,
                            "field": field_name,
                            "raw_hex": dec.raw_hex,
                            "actual": actual_int,
                            "attempts": attempts,
                        },
                    )
                time.sleep(min(poll_s, max(0.001, deadline - now)))

        if op == "assert_memory":
            addr = _parse_int(step["address"], "address")
            width = int(step.get("width", 32))
            if width not in (8, 16, 32):
                raise HilConfigError(f"assert_memory width must be 8, 16, or 32, got {width}")
            default_mask = (1 << width) - 1
            mask = _parse_int(step.get("mask", default_mask), "mask")
            if "eq" not in step and "ne" not in step:
                raise HilConfigError("assert_memory requires 'eq' and/or 'ne'")

            raw_val = self.probe.read_register_sized(addr, width)
            masked = raw_val & mask
            failures = []
            if "eq" in step:
                eq_int = _parse_int(step["eq"], "eq")
                if masked != eq_int:
                    failures.append(f"expected (0x{raw_val:X} & 0x{mask:X}) == 0x{eq_int:X}, got 0x{masked:X}")
            if "ne" in step:
                ne_int = _parse_int(step["ne"], "ne")
                if masked == ne_int:
                    failures.append(f"expected (0x{raw_val:X} & 0x{mask:X}) != 0x{ne_int:X}, got 0x{masked:X}")

            if failures:
                raise _HilStepFailure(
                    f"memory @ 0x{addr:08X} failed ({'; '.join(failures)})",
                    details={"address": f"0x{addr:08X}", "raw": raw_val, "masked": masked, "mask": mask},
                )
            return f"memory @ 0x{addr:08X} = 0x{raw_val:X} (masked 0x{masked:X}) passed", {
                "address": f"0x{addr:08X}",
                "raw": raw_val,
                "masked": masked,
            }

        if op == "assert_log":
            source = str(step.get("source", "rtt")).strip().lower()
            contains_raw = step.get("contains")
            if contains_raw is None:
                contains_list: list[str] = []
            elif isinstance(contains_raw, list):
                contains_list = [str(x) for x in contains_raw]
            else:
                contains_list = [str(contains_raw)]

            forbid_raw = step.get("forbid")
            if forbid_raw is None:
                forbid_list: list[str] = []
            elif isinstance(forbid_raw, list):
                forbid_list = [str(x) for x in forbid_raw]
            else:
                forbid_list = [str(forbid_raw)]

            regex_str = str(step["regex"]) if step.get("regex") is not None else None
            compiled_re = re.compile(regex_str) if regex_str is not None else None
            since_seq = int(step["since_seq"]) if step.get("since_seq") is not None else None
            timeout_ms = float(step.get("timeout_ms", 500.0))
            poll_ms = float(step.get("poll_ms", 20.0))
            poll_s = max(0.001, poll_ms / 1000.0)
            case_sensitive = bool(step.get("case_sensitive", True))

            if not contains_list and compiled_re is None and not forbid_list:
                raise HilConfigError("assert_log requires at least one of 'contains', 'regex', or 'forbid'")

            deadline = time.monotonic() + max(0.0, timeout_ms) / 1000.0
            last_lines: list[str] = []
            newest_seq: int | None = None

            while True:
                lines, newest_seq = self._read_logs(source, since_seq=since_seq)
                last_lines = lines
                joined = "\n".join(lines)

                for bad in forbid_list:
                    hit = (bad in joined) if case_sensitive else (bad.lower() in joined.lower())
                    if hit:
                        raise _HilStepFailure(
                            f"forbidden log substring {bad!r} found in {source} output",
                            details={"source": source, "forbid": bad, "newest_seq": newest_seq, "lines": lines[-20:]},
                        )

                contains_ok = True
                missing_substrings: list[str] = []
                for needle in contains_list:
                    hit = (needle in joined) if case_sensitive else (needle.lower() in joined.lower())
                    if not hit:
                        contains_ok = False
                        missing_substrings.append(needle)

                regex_ok = True
                if compiled_re is not None:
                    regex_ok = compiled_re.search(joined) is not None

                has_positive = bool(contains_list or compiled_re is not None)
                now = time.monotonic()
                if has_positive and contains_ok and regex_ok:
                    return f"{source} log assertion passed (newest_seq={newest_seq})", {
                        "source": source,
                        "newest_seq": newest_seq,
                        "matched_lines_count": len(lines),
                    }
                if not has_positive and now >= deadline:
                    return f"{source} log forbid check passed (no forbidden patterns in {len(lines)} line(s))", {
                        "source": source,
                        "newest_seq": newest_seq,
                    }
                if now >= deadline:
                    reasons: list[str] = []
                    if missing_substrings:
                        reasons.append(f"missing contains={missing_substrings!r}")
                    if not regex_ok and regex_str is not None:
                        reasons.append(f"regex {regex_str!r} not matched")
                    raise _HilStepFailure(
                        f"{source} log assertion timed out after {timeout_ms}ms ({'; '.join(reasons)})",
                        details={
                            "source": source,
                            "newest_seq": newest_seq,
                            "tail_lines": last_lines[-10:],
                        },
                    )
                time.sleep(min(poll_s, max(0.001, deadline - now)))

        if op == "assert_no_crash":
            keep_halted = bool(step.get("keep_halted", False))
            report = diagnose_crash(
                self.probe,
                elf_path=self.elf_path,
                project_dir=self.project_dir,
                keep_halted=keep_halted,
            )
            if report.fault_detected:
                raise _HilStepFailure(
                    f"Cortex-M fault detected: {report.summary}",
                    details={"crash_report": report.to_dict()},
                )
            return report.summary, {"crash_report": report.to_dict()}

        raise HilConfigError(f"Unsupported HIL step op: {op!r}")


def _looks_numeric(s: str) -> bool:
    try:
        parse_int_value(s)
        return True
    except ValueError:
        return False


def _create_probe_from_profile(profile_target: Any) -> DebugProbe:
    backend = profile_target.probe_backend
    if backend == "mock":
        return MockProbe(device=profile_target.device or "STM32H563ZI", interface=profile_target.interface)
    if backend == "openocd":
        return OpenOCDProbe(
            device=profile_target.device or "STM32H563ZI",
            interface=profile_target.interface,
            speed=profile_target.speed,
            serial_no=profile_target.serial_no,
            openocd_interface=profile_target.openocd_interface,
            openocd_target=profile_target.openocd_target,
            tcl_port=profile_target.openocd_tcl_port,
        )
    return JLinkProbe(
        device=profile_target.device or "STM32H563ZI",
        interface=profile_target.interface,
        speed=profile_target.speed,
        serial_no=profile_target.serial_no,
    )


def run_hil_suite(
    spec: str | os.PathLike | dict[str, Any],
    *,
    probe: DebugProbe | None = None,
    project_dir: str | os.PathLike | None = None,
    elf_path: str | os.PathLike | None = None,
    svd_path: str | os.PathLike | None = None,
    rtt_collector: RttLogCollector | None = None,
    uart_collector: UartLogCollector | None = None,
    confirmed: bool = False,
) -> HilSuiteReport:
    """加载并执行声明式 HIL 自动化测试套件，保证用例级与套件级 teardown 强制执行。"""
    t_suite_start = time.perf_counter()
    parsed, spec_dir = load_hil_spec(spec)

    profile = None
    eff_project_dir: str | None = None
    if project_dir is not None:
        profile = discover_project(project_dir)
        eff_project_dir = profile.project_dir
    elif parsed.get("project_dir"):
        pdir = _resolve_opt_path(parsed["project_dir"], spec_dir)
        if pdir:
            profile = discover_project(pdir)
            eff_project_dir = profile.project_dir

    eff_elf = (
        str(Path(elf_path).expanduser().resolve())
        if elf_path is not None
        else (
            _resolve_opt_path(parsed.get("elf_path") or parsed.get("elf"), spec_dir)
            or (profile.paths.elf_path if profile else None)
            or os.environ.get("MCU_HARNESS_ELF_PATH")
        )
    )
    eff_svd = (
        str(Path(svd_path).expanduser().resolve())
        if svd_path is not None
        else (
            _resolve_opt_path(parsed.get("svd_path") or parsed.get("svd"), spec_dir)
            or (profile.paths.svd_path if profile else None)
            or os.environ.get("MCU_HARNESS_SVD_PATH")
        )
    )
    safety_cfg = profile.safety if profile else SafetyConfig()

    owns_probe = probe is None
    owns_conn = False
    active_probe: DebugProbe
    if probe is not None:
        active_probe = probe
        if not active_probe.is_connected():
            active_probe.connect()
            owns_conn = True
    else:
        if profile is not None:
            active_probe = _create_probe_from_profile(profile.target)
        else:
            backend = os.environ.get("MCU_HARNESS_BACKEND", "jlink").strip().lower()
            device = str(parsed.get("device") or os.environ.get("MCU_HARNESS_DEVICE", "STM32H563ZI"))
            if backend == "mock":
                active_probe = MockProbe(device=device)
            elif backend == "openocd":
                active_probe = OpenOCDProbe(device=device)
            else:
                active_probe = JLinkProbe(device=device)
        active_probe.connect()
        owns_conn = True

    ctx = _HilRunnerContext(
        probe=active_probe,
        project_dir=eff_project_dir,
        elf_path=eff_elf,
        svd_path=eff_svd,
        rtt_collector=rtt_collector,
        uart_collector=uart_collector,
        confirmed=confirmed,
        safety_config=safety_cfg,
    )

    suite_setup_results: list[HilStepResult] = []
    suite_teardown_results: list[HilStepResult] = []
    test_reports: list[HilTestReport] = []
    suite_error: str | None = None

    try:
        try:
            for idx, step in enumerate(parsed["setup"], 1):
                res = ctx.execute_step(step, phase="setup", index=idx)
                suite_setup_results.append(res)
                if not res.passed:
                    suite_error = f"Suite setup step #{idx} ({res.op}) failed: {res.message}"
                    break

            if suite_error is None:
                for tc in parsed["tests"]:
                    t_tc_start = time.perf_counter()
                    tc_steps: list[HilStepResult] = []
                    tc_teardown: list[HilStepResult] = []
                    tc_error: str | None = None

                    try:
                        for idx, s_step in enumerate(tc["setup"], 1):
                            res = ctx.execute_step(s_step, phase="setup", index=idx)
                            tc_steps.append(res)
                            if not res.passed:
                                tc_error = res.message
                                break

                        if tc_error is None:
                            for idx, t_step in enumerate(tc["steps"], 1):
                                res = ctx.execute_step(t_step, phase="test", index=idx)
                                tc_steps.append(res)
                                if not res.passed:
                                    tc_error = res.message
                                    break
                    finally:
                        # 用例级 Safe-Off teardown 无条件执行全部收尾步骤
                        for idx, td_step in enumerate(tc["teardown"], 1):
                            td_res = ctx.execute_step(td_step, phase="teardown", index=idx)
                            tc_teardown.append(td_res)
                            if not td_res.passed and tc_error is None:
                                tc_error = f"Teardown failed: {td_res.message}"

                    tc_passed = all(s.passed for s in tc_steps) and all(t.passed for t in tc_teardown)
                    tc_dur = round((time.perf_counter() - t_tc_start) * 1000.0, 3)
                    test_reports.append(
                        HilTestReport(
                            name=tc["name"],
                            passed=tc_passed,
                            duration_ms=tc_dur,
                            steps=tc_steps,
                            teardown=tc_teardown,
                            error=tc_error,
                        )
                    )
        finally:
            # 套件级 Safe-Off teardown 无条件执行全部收尾步骤
            for idx, td_step in enumerate(parsed["teardown"], 1):
                td_res = ctx.execute_step(td_step, phase="teardown", index=idx)
                suite_teardown_results.append(td_res)
                if not td_res.passed and suite_error is None:
                    suite_error = f"Suite teardown failed: {td_res.message}"
    finally:
        if owns_probe or owns_conn:
            try:
                active_probe.close()
            except Exception:
                pass

    passed_cnt = sum(1 for t in test_reports if t.passed)
    failed_cnt = len(test_reports) - passed_cnt
    suite_passed = (
        suite_error is None
        and all(s.passed for s in suite_setup_results)
        and all(t.passed for t in test_reports)
        and all(td.passed for td in suite_teardown_results)
    )
    total_dur = round((time.perf_counter() - t_suite_start) * 1000.0, 3)

    return HilSuiteReport(
        name=parsed["name"],
        passed=suite_passed,
        total_tests=len(test_reports),
        passed_tests=passed_cnt,
        failed_tests=failed_cnt,
        duration_ms=total_dur,
        setup=suite_setup_results,
        tests=test_reports,
        teardown=suite_teardown_results,
        error=suite_error,
    )
