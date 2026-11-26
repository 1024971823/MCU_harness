"""逻辑分析仪数字波形 CSV 分析与 sigrok-cli 协议解码桥接模块。

设计要点：
    * `analyze_digital_csv` 为纯 Python 零外部依赖数字波形分析器，无需安装 `sigrok-cli`
      即可精确计算频率、占空比、上升/下降沿计数、高低电平时长与空闲状态。
    * `decode_with_sigrok` 在传入 CSV 时直通纯 Python 分析；在传入 `.sr` 文件或
      `driver`（如 `fx2lafw`）时调用 `sigrok-cli` 执行协议解码（SPI/I2C/UART/PWM/CAN）
      并结构化解析 `decoder: channel: annotation` 输出行。
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


class HilError(RuntimeError):
    """HIL 测试或逻辑分析仪采集/解析错误。"""


@dataclass(frozen=True)
class ChannelWaveformMetrics:
    """单个数字通道的波形统计指标。"""

    channel: str
    samples: int
    rising_edges: int
    falling_edges: int
    frequency_hz: float | None
    duty_cycle_pct: float
    high_time_s: float
    low_time_s: float
    idle_state: str  # "HIGH" | "LOW" | "TOGGLING"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LogicCsvReport:
    """数字波形 CSV 完整分析报告。"""

    source: str
    time_unit_s: float
    sample_count: int
    duration_s: float
    channels: dict[str, ChannelWaveformMetrics] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "time_unit_s": self.time_unit_s,
            "sample_count": self.sample_count,
            "duration_s": self.duration_s,
            "channels": {name: ch.to_dict() for name, ch in self.channels.items()},
        }

    def to_text(self) -> str:
        lines = [
            f"=== Digital Waveform Analysis ({self.source}) ===",
            f"Samples: {self.sample_count}  Duration: {self.duration_s:.6g} s  TimeUnit: {self.time_unit_s:.6g} s",
        ]
        for ch in self.channels.values():
            freq_str = f"{ch.frequency_hz:.6g} Hz" if ch.frequency_hz is not None else "N/A"
            lines.append(
                f"  [{ch.channel}] state={ch.idle_state}  freq={freq_str}  "
                f"duty={ch.duty_cycle_pct:.2f}%  "
                f"edges(rising={ch.rising_edges}, falling={ch.falling_edges})  "
                f"high={ch.high_time_s:.6g}s  low={ch.low_time_s:.6g}s"
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class ProtocolAnnotation:
    """单行 sigrok-cli 协议解码注释。"""

    decoder: str
    channel: str
    annotation: str
    raw_line: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LogicCaptureReport:
    """逻辑分析仪采集与协议解码统一报告（支持 CSV 直通与 sigrok-cli）。"""

    mode: str  # "csv" | "sigrok"
    source: str
    protocol: str | None = None
    command: list[str] | None = None
    csv_report: LogicCsvReport | None = None
    annotations: list[ProtocolAnnotation] = field(default_factory=list)
    raw_output: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "source": self.source,
            "protocol": self.protocol,
            "command": list(self.command) if self.command is not None else None,
            "csv_report": self.csv_report.to_dict() if self.csv_report is not None else None,
            "annotation_count": len(self.annotations),
            "annotations": [a.to_dict() for a in self.annotations],
            "raw_output": self.raw_output,
        }

    def to_text(self) -> str:
        if self.mode == "csv" and self.csv_report is not None:
            return self.csv_report.to_text()

        lines = [f"=== sigrok-cli Protocol Decode ({self.source}) ==="]
        if self.protocol:
            lines.append(f"Protocol: {self.protocol}")
        if self.command:
            lines.append(f"Command : {' '.join(self.command)}")
        lines.append(f"Annotations ({len(self.annotations)}):")
        for ann in self.annotations:
            if ann.channel:
                lines.append(f"  {ann.decoder}: {ann.channel}: {ann.annotation}")
            else:
                lines.append(f"  {ann.decoder}: {ann.annotation}")
        if not self.annotations and self.raw_output.strip():
            lines.append(self.raw_output.strip())
        return "\n".join(lines)


def _read_csv_source(csv_source: str | os.PathLike) -> tuple[str, str]:
    if isinstance(csv_source, os.PathLike):
        p = Path(csv_source).expanduser()
        if not p.is_file():
            raise HilError(f"CSV file not found: {p}")
        return p.read_text(encoding="utf-8", errors="replace"), str(p)

    if not isinstance(csv_source, str):
        raise HilError(f"Unsupported csv_source type: {type(csv_source).__name__}")

    if "\n" not in csv_source and "\r" not in csv_source:
        p = Path(csv_source).expanduser()
        if p.is_file():
            return p.read_text(encoding="utf-8", errors="replace"), str(p)
        if "," not in csv_source:
            raise HilError(f"CSV file not found: {p}")
    return csv_source, "<inline>"


def _try_parse_float(token: str) -> float | None:
    try:
        return float(token.strip())
    except ValueError:
        return None


def _parse_digital_bit(token: str, *, row_num: int, col_name: str) -> int:
    s = token.strip()
    if s in ("0", "0.0"):
        return 0
    if s in ("1", "1.0"):
        return 1
    low = s.lower()
    if low in ("false", "low"):
        return 0
    if low in ("true", "high"):
        return 1
    raise HilError(f"Invalid digital logic value {token!r} at row {row_num}, column {col_name!r} (expected 0 or 1)")


def analyze_digital_csv(
    csv_source: str | os.PathLike,
    *,
    time_unit_s: float = 1.0,
) -> LogicCsvReport:
    """纯 Python 零外部依赖解析数字波形 CSV，精确计算频率/占空比/边沿/空闲状态。"""
    if time_unit_s <= 0:
        raise ValueError(f"time_unit_s must be > 0, got {time_unit_s}")

    raw_text, source_name = _read_csv_source(csv_source)

    filtered_lines: list[str] = []
    for raw_line in raw_text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        filtered_lines.append(stripped)

    if not filtered_lines:
        raise HilError(f"CSV source {source_name} contains no data rows")

    rows = list(csv.reader(io.StringIO("\n".join(filtered_lines))))
    if not rows:
        raise HilError(f"CSV source {source_name} contains no parseable CSV rows")

    first_row = [cell.strip() for cell in rows[0]]
    if len(first_row) < 2:
        raise HilError(f"CSV must have at least 2 columns (time/sample + >=1 digital channel), got {len(first_row)}")

    # 首列非浮点数（如 time/timestamp/sample）视为表头行
    is_header = _try_parse_float(first_row[0]) is None
    if is_header:
        channel_names = [
            name if name else f"ch{idx}"
            for idx, name in enumerate(first_row[1:])
        ]
        data_rows = rows[1:]
        row_offset = 2
    else:
        channel_names = [f"ch{idx}" for idx in range(len(first_row) - 1)]
        data_rows = rows
        row_offset = 1

    if not data_rows:
        raise HilError(f"CSV source {source_name} has header but no sample rows")

    num_channels = len(channel_names)
    times: list[float] = []
    ch_values: list[list[int]] = [[] for _ in range(num_channels)]

    prev_t: float | None = None
    for idx, raw_row in enumerate(data_rows):
        row_num = idx + row_offset
        cells = [c.strip() for c in raw_row]
        if len(cells) < 1 + num_channels:
            raise HilError(
                f"CSV row {row_num} has {len(cells)} columns, expected {1 + num_channels}"
            )
        t_raw = _try_parse_float(cells[0])
        if t_raw is None:
            raise HilError(f"Invalid timestamp/sample index {cells[0]!r} at row {row_num}")
        t_sec = t_raw * time_unit_s
        if prev_t is not None and t_sec < prev_t:
            raise HilError(f"Non-monotonic timestamp at row {row_num}: {t_sec} < {prev_t}")
        prev_t = t_sec
        times.append(t_sec)

        for ch_idx in range(num_channels):
            bit = _parse_digital_bit(cells[1 + ch_idx], row_num=row_num, col_name=channel_names[ch_idx])
            ch_values[ch_idx].append(bit)

    sample_count = len(times)
    duration_s = times[-1] - times[0] if sample_count >= 2 else 0.0

    metrics: dict[str, ChannelWaveformMetrics] = {}
    for ch_idx, ch_name in enumerate(channel_names):
        vals = ch_values[ch_idx]
        rising_ts: list[float] = []
        falling_ts: list[float] = []
        high_time = 0.0
        low_time = 0.0

        for i in range(sample_count - 1):
            dt = times[i + 1] - times[i]
            cur = vals[i]
            nxt = vals[i + 1]
            if cur == 1:
                high_time += dt
            else:
                low_time += dt
            if cur == 0 and nxt == 1:
                rising_ts.append(times[i + 1])
            elif cur == 1 and nxt == 0:
                falling_ts.append(times[i + 1])

        rising_edges = len(rising_ts)
        falling_edges = len(falling_ts)
        total_time = high_time + low_time

        if total_time > 0.0:
            duty_pct = (high_time / total_time) * 100.0
        else:
            duty_pct = 100.0 if vals[0] == 1 else 0.0

        freq_hz: float | None
        if rising_edges >= 2:
            span = rising_ts[-1] - rising_ts[0]
            freq_hz = (rising_edges - 1) / span if span > 0.0 else None
        elif falling_edges >= 2:
            span = falling_ts[-1] - falling_ts[0]
            freq_hz = (falling_edges - 1) / span if span > 0.0 else None
        elif rising_edges == 0 and falling_edges == 0:
            freq_hz = 0.0
        else:
            freq_hz = None

        if rising_edges == 0 and falling_edges == 0:
            idle_state = "HIGH" if vals[0] == 1 else "LOW"
        else:
            idle_state = "TOGGLING"

        metrics[ch_name] = ChannelWaveformMetrics(
            channel=ch_name,
            samples=sample_count,
            rising_edges=rising_edges,
            falling_edges=falling_edges,
            frequency_hz=round(freq_hz, 6) if freq_hz is not None else None,
            duty_cycle_pct=round(duty_pct, 6),
            high_time_s=round(high_time, 12),
            low_time_s=round(low_time, 12),
            idle_state=idle_state,
        )

    return LogicCsvReport(
        source=source_name,
        time_unit_s=time_unit_s,
        sample_count=sample_count,
        duration_s=round(duration_s, 12),
        channels=metrics,
    )


def _resolve_sigrok_bin(sigrok_bin: str | None) -> str:
    if sigrok_bin:
        cand = Path(sigrok_bin).expanduser()
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
        found = shutil.which(sigrok_bin)
        if found:
            return found
        raise HilError(
            f"Specified sigrok-cli executable not found: {sigrok_bin!r}. "
            "Install sigrok-cli (e.g. 'sudo apt install sigrok-cli' / 'brew install sigrok-cli') "
            "or pass csv_path=... to analyze digital CSV directly without sigrok-cli."
        )

    found = shutil.which("sigrok-cli")
    if not found:
        raise HilError(
            "sigrok-cli not found in PATH. Install it via 'sudo apt install sigrok-cli' (Ubuntu/Debian) "
            "or 'brew install sigrok-cli' (macOS), or pass csv_path=... for built-in pure-Python CSV analysis."
        )
    return found


def _parse_sigrok_annotations(stdout: str) -> list[ProtocolAnnotation]:
    annotations: list[ProtocolAnnotation] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(":", 2)
        if len(parts) == 3:
            annotations.append(
                ProtocolAnnotation(
                    decoder=parts[0].strip(),
                    channel=parts[1].strip(),
                    annotation=parts[2].strip(),
                    raw_line=line,
                )
            )
        elif len(parts) == 2:
            annotations.append(
                ProtocolAnnotation(
                    decoder=parts[0].strip(),
                    channel="",
                    annotation=parts[1].strip(),
                    raw_line=line,
                )
            )
        else:
            annotations.append(
                ProtocolAnnotation(
                    decoder="raw",
                    channel="",
                    annotation=line,
                    raw_line=line,
                )
            )
    return annotations


def decode_with_sigrok(
    *,
    csv_path: str | os.PathLike | None = None,
    input_file: str | os.PathLike | None = None,
    driver: str | None = None,
    channels: str | list[str] | tuple[str, ...] | None = None,
    samplerate: str | int = "4m",
    samples: int | None = None,
    duration_ms: float | int | None = None,
    protocol: str | None = None,
    protocol_options: dict[str, Any] | str | None = None,
    sigrok_bin: str | None = None,
    timeout_s: float = 15.0,
    time_unit_s: float = 1.0,
) -> LogicCaptureReport:
    """桥接数字波形分析与 sigrok-cli 协议解码（SPI/I2C/UART/PWM/CAN）。"""
    if csv_path is not None:
        csv_rep = analyze_digital_csv(csv_path, time_unit_s=time_unit_s)
        return LogicCaptureReport(
            mode="csv",
            source=csv_rep.source,
            protocol=protocol,
            csv_report=csv_rep,
        )

    if input_file is not None and str(input_file).lower().endswith(".csv"):
        csv_rep = analyze_digital_csv(input_file, time_unit_s=time_unit_s)
        return LogicCaptureReport(
            mode="csv",
            source=csv_rep.source,
            protocol=protocol,
            csv_report=csv_rep,
        )

    if input_file is None and not driver:
        raise HilError("Specify csv_path, input_file, or driver (e.g. 'fx2lafw') for logic capture")

    exe = _resolve_sigrok_bin(sigrok_bin)
    cmd: list[str] = [exe]

    if input_file is not None:
        in_path = Path(input_file).expanduser()
        if not in_path.is_file():
            raise HilError(f"Capture input file not found: {in_path}")
        cmd.extend(["-i", str(in_path)])
        source_label = str(in_path)
    else:
        assert driver is not None
        cmd.extend(["-d", str(driver)])
        if samplerate is not None and str(samplerate).strip():
            cmd.extend(["--config", f"samplerate={samplerate}"])
        if samples is not None:
            cmd.extend(["--samples", str(int(samples))])
        if duration_ms is not None:
            cmd.extend(["--time", str(int(round(float(duration_ms))))])
        source_label = f"driver:{driver}"

    if channels is not None:
        if isinstance(channels, (list, tuple)):
            ch_str = ",".join(str(c).strip() for c in channels if str(c).strip())
        else:
            ch_str = str(channels).strip()
        if ch_str:
            cmd.extend(["--channels", ch_str])

    if protocol:
        proto_str = str(protocol).strip()
        if protocol_options:
            if isinstance(protocol_options, dict):
                opts = ":".join(f"{k}={v}" for k, v in protocol_options.items())
            else:
                opts = str(protocol_options).strip().lstrip(":")
            if opts:
                proto_str = f"{proto_str}:{opts}"
        cmd.extend(["-P", proto_str])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError as exc:
        raise HilError(
            f"Failed to execute sigrok-cli ({exe!r}): {exc}. "
            "Install sigrok-cli via 'sudo apt install sigrok-cli' or pass csv_path=..."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise HilError(f"sigrok-cli timed out after {timeout_s}s: {' '.join(cmd)}") from exc

    if proc.returncode != 0:
        err_msg = (proc.stderr or proc.stdout or "").strip()
        raise HilError(f"sigrok-cli failed with exit code {proc.returncode}: {err_msg}")

    annotations = _parse_sigrok_annotations(proc.stdout or "")
    return LogicCaptureReport(
        mode="sigrok",
        source=source_label,
        protocol=protocol,
        command=cmd,
        annotations=annotations,
        raw_output=proc.stdout or "",
    )
