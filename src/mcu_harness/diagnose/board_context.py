"""板级硬件/引脚配置核验器（Board Context Inspector）。

把静态工程设计意图与物理世界负载、真机寄存器实测态打通：
    1. 解析 STM32CubeMX `.ioc` 全量板级真相：
       - 芯片型号、封装、HSE/SYSCLK/HCLK 时钟参数
       - 引脚分配表（Signal、GPIO_Label、PuPd、Output 推挽/开漏、AF 复用号）
       - 已启用外设清单（`Mcu.IP*`）、DMA/GPDMA 通道映射与 `NVIC.*` 中断配置
    2. 解析 `mcu-harness.toml` 中的可选 `[board]`、`[[board.pins]]`、`[[board.devices]]`：
       - 外部传感器型号、I2C 地址、继电器/阀门有效电平极性、PWM 最大占空比安全上限等物理负载说明
    3. 静态意图 vs 真机实测实时对账（Live Drift Check）：
       - 当传入在线 `probe` 与 `svd_device` 时，逐脚比对 `.ioc`/`board` 期望模式与真机 `GPIOx->MODER`（及 `OTYPER`/`AFR`），
         精准抓出“`.ioc` 里配了 `USART1_TX (PA9)`，但真机寄存器仍停留在 `Analog` 复位态”的初始化脱节问题！
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from mcu_harness.config.manifest import normalize_cubemx_mcu_name
from mcu_harness.probe.base import DebugProbe
from mcu_harness.svd import SvdDevice

from .peripheral_doctor import (
    DiagnosticFinding,
    GpioPinSnapshot,
    check_peripheral_clock,
    read_gpio_port_pins,
)

_MANIFEST_NAMES = ("mcu-harness.toml", ".mcu-harness.toml")
_SKIP_SCAN_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "CMakeFiles", ".pytest_cache"}

# 兼容 PA5.Signal / PH0-OSC_IN(PH0).Signal / PA0-WKUP.GPIO_Label / PC3_C.Signal 等 CubeMX 引脚键格式
_RE_IOC_PIN_KEY = re.compile(r"^P([A-Z])(\d{1,2})(?:_C)?(?:[-(\[][^.]*)?\.(.+)$")
_RE_IOC_IP_KEY = re.compile(r"^Mcu\.IP(\d+)$")
_RE_ANALOG_SIGNAL = re.compile(r"^(?:GPIO_Analog|ADC\d*_|DAC\d*_|OPAMP\d*_|COMP\d*_IN|VREF|RCC_OSC)", re.IGNORECASE)
_RE_AF_NUM = re.compile(r"(?:^|[^A-Za-z0-9])AF(\d{1,2})(?:$|[^0-9])", re.IGNORECASE)


# ---------------------------------------------------------------------------
# 数据结构定义
# ---------------------------------------------------------------------------


@dataclass
class IocMcuInfo:
    """来自 `.ioc` 的芯片与时钟树关键参数。"""

    raw_name: str | None = None
    normalized_device: str | None = None
    family: str | None = None
    package: str | None = None
    hse_value_hz: int | None = None
    hsi_value_hz: int | None = None
    sysclk_hz: int | None = None
    hclk_hz: int | None = None
    clock_params: dict[str, int | str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BoardPinSpec:
    """单个引脚的静态配置意图（融合 `.ioc` 引脚定义与 `mcu-harness.toml` 物理负载说明）。"""

    pin: str                         # 如 "PA5", "PA9"
    port: str                        # 如 "GPIOA"
    pin_number: int                  # 0..15
    signal: str = ""                 # 如 "GPIO_Output", "USART1_TX"
    label: str | None = None         # 如 "VALVE_EN", "PUMP_RELAY"
    expected_mode: str | None = None  # "Input" | "Output" | "Alternate" | "Analog"
    expected_output_type: str | None = None  # "Push-Pull" | "Open-Drain" | None
    expected_pull: str | None = None
    expected_af: int | None = None
    initial_level: str | None = None
    active_level: str | None = None
    max_duty_percent: float | None = None
    connected_devices: list[str] = field(default_factory=list)
    notes: str = ""
    raw_ioc_props: dict[str, str] = field(default_factory=dict)
    extra_toml_props: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DmaChannelSpec:
    """`.ioc` 中配置的 DMA / GPDMA 请求映射。"""

    controller: str       # 如 "DMA1" / "GPDMA1"
    channel: str          # 如 "DMA1_Stream0" / "CH0"
    request: str          # 如 "USART1_RX"
    direction: str = ""
    mode: str = ""
    priority: str = ""
    raw_props: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class IocNvicSpec:
    """`.ioc` 中配置的 NVIC 中断项。"""

    irq_name: str
    enabled: bool
    preempt_priority: int | None = None
    sub_priority: int | None = None
    raw_value: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BoardDeviceSpec:
    """来自 `mcu-harness.toml` `[[board.devices]]` 的板载外部器件硬件说明。"""

    name: str
    part_number: str | None = None
    bus: str | None = None             # 如 "I2C1", "SPI1", "USART1", "GPIO", "TIM1"
    address: str | None = None         # 如 "0x48"
    pins: list[str] = field(default_factory=list)
    pin_roles: dict[str, str] = field(default_factory=dict)
    active_level: str | None = None    # 如 "high" / "low"
    max_duty_percent: float | None = None
    description: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PinDriftRecord:
    """单个引脚的静态声明 vs 真机实测对账结果。"""

    pin: str
    signal: str
    label: str | None
    expected_mode: str
    live_mode: str                     # "Input" | "Output" | "Alternate" | "Analog" | "Unclocked" | "Unknown"
    matched: bool
    expected_af: int | None = None
    live_af: int | None = None
    expected_output_type: str | None = None
    live_output_type: str | None = None
    live_odr: int | None = None
    live_idr: int | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BoardContextReport:
    """板级配置真相与实时漂移对账完整报告。"""

    project_dir: str
    ioc_path: str | None
    manifest_path: str | None
    board_name: str | None = None
    board_notes: str = ""
    mcu: IocMcuInfo = field(default_factory=IocMcuInfo)
    pins: list[BoardPinSpec] = field(default_factory=list)
    enabled_peripherals: list[str] = field(default_factory=list)
    dma_mappings: list[DmaChannelSpec] = field(default_factory=list)
    nvic_configs: list[IocNvicSpec] = field(default_factory=list)
    board_devices: list[BoardDeviceSpec] = field(default_factory=list)
    live_checked: bool = False
    pin_checks: list[PinDriftRecord] = field(default_factory=list)
    matched_pins: list[str] = field(default_factory=list)
    drifted_pins: list[PinDriftRecord] = field(default_factory=list)
    findings: list[DiagnosticFinding] = field(default_factory=list)

    @property
    def pins_by_name(self) -> dict[str, BoardPinSpec]:
        return {p.pin: p for p in self.pins}

    @property
    def checks_by_pin(self) -> dict[str, PinDriftRecord]:
        return {c.pin: c for c in self.pin_checks}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_text(self) -> str:
        lines = [
            f"=== Board Context Report: {self.project_dir} ===",
            f"  .ioc file:     {self.ioc_path or '(none)'}",
            f"  manifest:      {self.manifest_path or '(none)'}",
            f"  MCU:           {self.mcu.raw_name or '(unknown)'} "
            f"[device={self.mcu.normalized_device or '?'}, pkg={self.mcu.package or '?'}, HSE={self.mcu.hse_value_hz or '?'} Hz]",
        ]
        if self.enabled_peripherals:
            lines.append(f"  Enabled IPs:   {', '.join(self.enabled_peripherals)}")

        if self.pins:
            lines.append(f"\n[Declared Pins ({len(self.pins)})]")
            for p in self.pins:
                lbl = f" [label={p.label}]" if p.label else ""
                devs = f" -> loads={', '.join(p.connected_devices)}" if p.connected_devices else ""
                lines.append(f"  - {p.pin:<5} -> {p.signal or p.expected_mode or '?'}{lbl} (expect={p.expected_mode}){devs}")

        if self.board_devices:
            lines.append(f"\n[External Board Devices ({len(self.board_devices)})]")
            for d in self.board_devices:
                addr_txt = f" @ {d.address}" if d.address else ""
                part_txt = f" ({d.part_number})" if d.part_number else ""
                bus_txt = f" on {d.bus}" if d.bus else ""
                pins_txt = f" pins={','.join(d.pins)}" if d.pins else ""
                pol_txt = f" active={d.active_level}" if d.active_level else ""
                duty_txt = f" max_duty={d.max_duty_percent}%" if d.max_duty_percent is not None else ""
                lines.append(f"  - {d.name}{part_txt}{bus_txt}{addr_txt}{pins_txt}{pol_txt}{duty_txt}")

        if self.dma_mappings:
            lines.append(f"\n[DMA Mappings ({len(self.dma_mappings)})]")
            for dm in self.dma_mappings:
                lines.append(f"  - {dm.request} -> {dm.channel} ({dm.direction or 'dir?'}, {dm.mode or 'mode?'})")

        if self.nvic_configs:
            en_nvic = [n for n in self.nvic_configs if n.enabled]
            lines.append(f"\n[NVIC in .ioc ({len(en_nvic)}/{len(self.nvic_configs)} enabled)]")
            for nv in en_nvic:
                lines.append(f"  - {nv.irq_name}: enabled={nv.enabled}, prio=({nv.preempt_priority}, {nv.sub_priority})")

        if self.live_checked:
            lines.append(
                f"\n[Live Hardware Drift Check] matched={len(self.matched_pins)}, drifted={len(self.drifted_pins)}"
            )
            for chk in self.pin_checks:
                mark = "OK" if chk.matched else "DRIFT"
                detail = f" — {chk.reason}" if chk.reason else ""
                lines.append(
                    f"  - [{mark}] {chk.pin:<5} ({chk.signal}): expected={chk.expected_mode}, live={chk.live_mode}{detail}"
                )

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主入口：板级解析 + 外部器件关联 + 真机实时对账
# ---------------------------------------------------------------------------


def inspect_board_context(
    project_dir: str | os.PathLike,
    *,
    ioc_path: str | os.PathLike | None = None,
    manifest_path: str | os.PathLike | None = None,
    probe: DebugProbe | None = None,
    svd_device: SvdDevice | None = None,
) -> BoardContextReport:
    """解析工程 `.ioc` 与 `mcu-harness.toml` 板级硬件上下文，并在提供 `probe`+`svd_device` 时执行真机引脚对账。"""
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Project directory does not exist: {root}")

    resolved_manifest = _find_manifest(root, manifest_path)
    raw_toml: dict[str, Any] = {}
    if resolved_manifest is not None:
        raw_toml = tomllib.loads(resolved_manifest.read_text(encoding="utf-8"))

    # 查找 .ioc：显式参数 -> mcu-harness.toml [paths].ioc -> 自动扫描工程根目录与一级子目录
    paths_sec = raw_toml.get("paths") if isinstance(raw_toml.get("paths"), dict) else {}
    explicit_ioc = ioc_path or paths_sec.get("ioc") or paths_sec.get("ioc_path")
    resolved_ioc = _find_ioc(root, explicit_ioc)

    mcu_info = IocMcuInfo()
    pin_map: dict[str, BoardPinSpec] = {}
    enabled_periphs: list[str] = []
    dma_mappings: list[DmaChannelSpec] = []
    nvic_configs: list[IocNvicSpec] = []
    findings: list[DiagnosticFinding] = []

    if resolved_ioc is not None:
        mcu_info, ioc_pins, enabled_periphs, dma_mappings, nvic_configs = parse_ioc_file(resolved_ioc)
        for p in ioc_pins:
            pin_map[p.pin] = p

    # 解析 mcu-harness.toml [board] / [[board.pins]] / [[board.devices]]
    board_name, board_notes, board_devices = _merge_board_toml(raw_toml, pin_map)

    ordered_pins = sorted(pin_map.values(), key=lambda x: (x.port, x.pin_number))

    # 静态检查：I2C 引脚若在 .ioc 中显式配成了推挽输出则提前告警
    for p in ordered_pins:
        if p.signal.upper().startswith(("I2C", "FMPI2C")) and p.expected_output_type == "Push-Pull":
            findings.append(
                DiagnosticFinding(
                    severity="warning",
                    stage="board_drift",
                    code="IOC_I2C_PUSH_PULL",
                    title=f".ioc 中 {p.pin} ({p.signal}) 配置成了 Push-Pull 推挽模式",
                    detail=f"I2C 引脚 {p.pin} ({p.signal}) 必须使用 Open-Drain 开漏模式，否则无法正常通信。",
                    suggestion=f"在 CubeMX 中确认 {p.pin} 为 Alternate Function Open Drain。",
                    evidence=p.to_dict(),
                )
            )

    # 实时真机引脚模式对账（Live Drift Check）
    live_checked = False
    pin_checks: list[PinDriftRecord] = []
    matched_pins: list[str] = []
    drifted_pins: list[PinDriftRecord] = []

    if probe is not None and svd_device is not None:
        live_checked = True
        pin_checks = _run_live_pin_drift_check(probe, svd_device, ordered_pins, findings)
        for chk in pin_checks:
            if chk.matched:
                matched_pins.append(chk.pin)
            else:
                drifted_pins.append(chk)

    return BoardContextReport(
        project_dir=str(root),
        ioc_path=str(resolved_ioc) if resolved_ioc else None,
        manifest_path=str(resolved_manifest) if resolved_manifest else None,
        board_name=board_name,
        board_notes=board_notes,
        mcu=mcu_info,
        pins=ordered_pins,
        enabled_peripherals=enabled_periphs,
        dma_mappings=dma_mappings,
        nvic_configs=nvic_configs,
        board_devices=board_devices,
        live_checked=live_checked,
        pin_checks=pin_checks,
        matched_pins=matched_pins,
        drifted_pins=drifted_pins,
        findings=findings,
    )


# ---------------------------------------------------------------------------
# STM32CubeMX `.ioc` 解析器
# ---------------------------------------------------------------------------


def parse_ioc_file(
    ioc_path: str | os.PathLike,
) -> tuple[IocMcuInfo, list[BoardPinSpec], list[str], list[DmaChannelSpec], list[IocNvicSpec]]:
    """解析单个 STM32CubeMX `.ioc` 配置文件。"""
    p = Path(ioc_path).expanduser().resolve()
    text = p.read_text(encoding="utf-8", errors="replace")

    kv: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        kv[_unescape_ioc(k.strip())] = _unescape_ioc(v.strip())

    # 1. MCU 与时钟信息
    raw_mcu = kv.get("Mcu.UserName") or kv.get("Mcu.CPN") or kv.get("Mcu.Name")
    norm_dev = normalize_cubemx_mcu_name(raw_mcu) if raw_mcu else None
    clock_params: dict[str, int | str] = {}
    for k, v in kv.items():
        ku = k.upper()
        if k.startswith("RCC.") and ("VALUE" in ku or "FREQ" in ku):
            short_k = k[4:]
            clock_params[short_k] = _try_int(v)
        elif ku in ("HSE_VALUE", "HSI_VALUE", "LSE_VALUE", "LSI_VALUE"):
            clock_params[k] = _try_int(v)

    hse_val = _pick_int(clock_params, "HSE_VALUE")
    hsi_val = _pick_int(clock_params, "HSI_VALUE")
    sysclk_val = _pick_int(clock_params, "SYSCLKFreq_VALUE", "SYSCLK_VALUE")
    hclk_val = _pick_int(clock_params, "HCLKFreq_VALUE", "HCLK_VALUE")

    mcu_info = IocMcuInfo(
        raw_name=raw_mcu,
        normalized_device=norm_dev,
        family=kv.get("Mcu.Family"),
        package=kv.get("Mcu.Package"),
        hse_value_hz=hse_val,
        hsi_value_hz=hsi_val,
        sysclk_hz=sysclk_val,
        hclk_hz=hclk_val,
        clock_params=clock_params,
    )

    # 2. CubeMX 共享信号映射表（如 SH.S_TIM1_CH2.0=TIM1_CH2,PWM Generation2 CH2）
    shared_signals: dict[str, str] = {}
    for k, v in kv.items():
        if k.startswith("SH.") and k.endswith(".0"):
            sig_alias = k[3:-2]
            real_sig = v.split(",", 1)[0].strip()
            if sig_alias and real_sig:
                shared_signals[sig_alias] = real_sig

    # 3. 引脚分配表
    raw_pin_props: dict[tuple[str, int], dict[str, str]] = {}
    for k, v in kv.items():
        m = _RE_IOC_PIN_KEY.match(k)
        if not m:
            continue
        port_letter = m.group(1).upper()
        pin_no = int(m.group(2), 10)
        if pin_no < 0 or pin_no > 15:
            continue
        attr = m.group(3)
        raw_pin_props.setdefault((port_letter, pin_no), {})[attr] = v

    pins: list[BoardPinSpec] = []
    inferred_ips: set[str] = set()
    for (port_letter, pin_no), props in sorted(raw_pin_props.items()):
        raw_sig = props.get("Signal", "").strip()
        signal = shared_signals.get(raw_sig, raw_sig)
        if not signal and "GPIO_Label" not in props:
            continue

        label = props.get("GPIO_Label") or None
        exp_mode, exp_otype, exp_pull, exp_af = _infer_pin_expectations(signal, props)
        init_lvl = props.get("PinState") or None

        if "_" in signal and not signal.startswith(("GPIO_", "GPXTI")):
            ip_prefix = signal.split("_", 1)[0]
            if ip_prefix:
                inferred_ips.add(ip_prefix)

        pins.append(
            BoardPinSpec(
                pin=f"P{port_letter}{pin_no}",
                port=f"GPIO{port_letter}",
                pin_number=pin_no,
                signal=signal,
                label=label,
                expected_mode=exp_mode,
                expected_output_type=exp_otype,
                expected_pull=exp_pull,
                expected_af=exp_af,
                initial_level=init_lvl,
                raw_ioc_props=props,
            )
        )

    # 4. 已启用外设清单 Mcu.IP0..IPn
    ip_items: list[tuple[int, str]] = []
    for k, v in kv.items():
        m_ip = _RE_IOC_IP_KEY.match(k)
        if m_ip and v:
            ip_items.append((int(m_ip.group(1), 10), v))
    ip_items.sort(key=lambda x: x[0])
    enabled_ips: list[str] = []
    for _, ip_name in ip_items:
        if ip_name not in enabled_ips:
            enabled_ips.append(ip_name)
    if not enabled_ips and inferred_ips:
        enabled_ips = sorted(inferred_ips)

    # 5. DMA / GPDMA 请求映射
    dma_mappings = _parse_ioc_dma(kv)

    # 6. NVIC 中断配置表
    nvic_configs = _parse_ioc_nvic(kv)

    return mcu_info, pins, enabled_ips, dma_mappings, nvic_configs


def _infer_pin_expectations(
    signal: str,
    props: dict[str, str],
) -> tuple[str | None, str | None, str | None, int | None]:
    all_vals_up = " ".join(props.values()).upper()
    sig_up = signal.upper()

    # Pull-up / Pull-down
    exp_pull: str | None = None
    pupd = props.get("GPIO_PuPd", "").upper()
    if "PULLUP" in pupd:
        exp_pull = "PullUp"
    elif "PULLDOWN" in pupd:
        exp_pull = "PullDown"
    elif "NOPULL" in pupd:
        exp_pull = "NoPull"

    # AF 编号（如 GPIO_AF7_USART1）
    exp_af: int | None = None
    af_raw = props.get("GPIO_AF", "")
    m_af = _RE_AF_NUM.search(af_raw)
    if m_af:
        exp_af = int(m_af.group(1), 10)

    # Output Type
    exp_otype: str | None = None
    if "_OD" in all_vals_up or "OPENDRAIN" in all_vals_up:
        exp_otype = "Open-Drain"
    elif "_PP" in all_vals_up or "PUSHPULL" in all_vals_up:
        exp_otype = "Push-Pull"
    elif sig_up.startswith(("I2C", "FMPI2C")):
        exp_otype = "Open-Drain"

    # Mode
    if sig_up == "GPIO_OUTPUT" or "GPIO_MODE_OUTPUT" in all_vals_up:
        return "Output", exp_otype or "Push-Pull", exp_pull, None
    if (
        sig_up == "GPIO_INPUT"
        or sig_up.startswith(("GPIO_EXTI", "GPXTI"))
        or "GPIO_MODE_INPUT" in all_vals_up
        or "GPIO_MODE_IT_" in all_vals_up
        or "GPIO_MODE_EVT_" in all_vals_up
    ):
        return "Input", None, exp_pull, None
    if _RE_ANALOG_SIGNAL.match(signal) or "GPIO_MODE_ANALOG" in all_vals_up:
        return "Analog", None, exp_pull, None
    if signal:
        return "Alternate", exp_otype, exp_pull, exp_af
    return None, exp_otype, exp_pull, exp_af


def _parse_ioc_dma(kv: dict[str, str]) -> list[DmaChannelSpec]:
    out: list[DmaChannelSpec] = []

    # 1. 经典 CubeMX Dma.<Request>.<idx>.<Field>
    dma_groups: dict[tuple[str, str], dict[str, str]] = {}
    for k, v in kv.items():
        if k.startswith("Dma."):
            parts = k.split(".")
            if len(parts) == 4 and parts[2].isdigit():
                dma_groups.setdefault((parts[1], parts[2]), {})[parts[3]] = v

    for (req, _idx), props in sorted(dma_groups.items()):
        inst = props.get("Instance", "")
        ctrl = inst.split("_", 1)[0] if "_" in inst else (inst or "DMA")
        out.append(
            DmaChannelSpec(
                controller=ctrl,
                channel=inst or req,
                request=req,
                direction=props.get("Direction", ""),
                mode=props.get("Mode", ""),
                priority=props.get("Priority", ""),
                raw_props=props,
            )
        )

    # 2. STM32H5/U5 GPDMA / LPDMA 通道键（如 GPDMA1.CH0.Request=USART1_RX 或 GPDMA1.REQUEST_0=USART1_RX）
    gpdma_groups: dict[tuple[str, str], dict[str, str]] = {}
    for k, v in kv.items():
        if k.startswith(("GPDMA", "LPDMA", "BDMA")):
            parts = k.split(".")
            if len(parts) == 3:
                gpdma_groups.setdefault((parts[0], parts[1]), {})[parts[2]] = v
            elif len(parts) == 2 and parts[1].upper().startswith("REQUEST"):
                ch_suffix = parts[1].split("_")[-1]
                ch_name = f"CH{ch_suffix}" if ch_suffix.isdigit() else parts[1]
                gpdma_groups.setdefault((parts[0], ch_name), {})["Request"] = v

    for (ctrl, ch), props in sorted(gpdma_groups.items()):
        req = props.get("Request") or props.get("REQUEST") or ""
        if not req:
            continue
        out.append(
            DmaChannelSpec(
                controller=ctrl,
                channel=f"{ctrl}_{ch}",
                request=req,
                direction=props.get("Direction", ""),
                mode=props.get("Mode", ""),
                priority=props.get("Priority", ""),
                raw_props=props,
            )
        )

    return out


def _parse_ioc_nvic(kv: dict[str, str]) -> list[IocNvicSpec]:
    out: list[IocNvicSpec] = []
    skip_keys = {"PriorityGroup", "ForceEnableDMAVector", "TimeBaseIP"}
    for k, v in sorted(kv.items()):
        if not k.startswith("NVIC."):
            continue
        irq_name = k[5:]
        if irq_name in skip_keys or not irq_name:
            continue
        parts = v.split(":")
        enabled = parts[0].strip().lower() in ("true", "1", "enable", "enabled")
        preempt = int(parts[1], 10) if len(parts) > 1 and parts[1].strip().lstrip("-").isdigit() else None
        sub = int(parts[2], 10) if len(parts) > 2 and parts[2].strip().lstrip("-").isdigit() else None
        out.append(
            IocNvicSpec(
                irq_name=irq_name,
                enabled=enabled,
                preempt_priority=preempt,
                sub_priority=sub,
                raw_value=v,
            )
        )
    return out


# ---------------------------------------------------------------------------
# `mcu-harness.toml` 板载硬件说明融合
# ---------------------------------------------------------------------------


def _merge_board_toml(
    raw_toml: dict[str, Any],
    pin_map: dict[str, BoardPinSpec],
) -> tuple[str | None, str, list[BoardDeviceSpec]]:
    board_sec = raw_toml.get("board")
    if not isinstance(board_sec, dict):
        return None, "", []

    board_name = str(board_sec["name"]).strip() if board_sec.get("name") else None
    board_notes = str(board_sec.get("description") or board_sec.get("notes") or "").strip()

    # 1. [[board.pins]]
    raw_pins = board_sec.get("pins")
    if isinstance(raw_pins, list):
        for item in raw_pins:
            if not isinstance(item, dict) or not item.get("pin"):
                continue
            pin_id = str(item["pin"]).strip().upper()
            spec = _ensure_pin_spec(pin_map, pin_id)
            if spec is None:
                continue

            if item.get("label") or item.get("name"):
                spec.label = str(item.get("label") or item.get("name"))
            if item.get("signal") and not spec.signal:
                spec.signal = str(item["signal"])
            if item.get("mode"):
                spec.expected_mode = _normalize_mode_name(str(item["mode"])) or spec.expected_mode
            if item.get("active_level") or item.get("polarity"):
                spec.active_level = str(item.get("active_level") or item.get("polarity"))
            if item.get("max_duty") is not None or item.get("max_duty_percent") is not None:
                raw_duty = item.get("max_duty_percent") if item.get("max_duty_percent") is not None else item.get("max_duty")
                spec.max_duty_percent = float(raw_duty)
            if item.get("load") or item.get("device") or item.get("connected_to"):
                load_str = str(item.get("load") or item.get("device") or item.get("connected_to"))
                if load_str not in spec.connected_devices:
                    spec.connected_devices.append(load_str)
            if item.get("notes") or item.get("description"):
                spec.notes = str(item.get("notes") or item.get("description"))

            known_keys = {
                "pin", "label", "name", "signal", "mode", "active_level", "polarity",
                "max_duty", "max_duty_percent", "load", "device", "connected_to", "notes", "description",
            }
            for ek, ev in item.items():
                if ek not in known_keys:
                    spec.extra_toml_props[ek] = ev

    # 2. [[board.devices]]
    devices: list[BoardDeviceSpec] = []
    raw_devs = board_sec.get("devices")
    if isinstance(raw_devs, list):
        for idx, item in enumerate(raw_devs):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("id") or f"device_{idx}").strip()
            part = str(item.get("part_number") or item.get("model") or item.get("chip") or "").strip() or None
            bus = str(item.get("bus") or item.get("peripheral") or item.get("interface") or "").strip() or None

            raw_addr = item.get("address") if item.get("address") is not None else item.get("i2c_address")
            addr_str: str | None = None
            if isinstance(raw_addr, int) and not isinstance(raw_addr, bool):
                addr_str = f"0x{raw_addr:02X}"
            elif raw_addr not in (None, ""):
                addr_str = str(raw_addr).strip()

            dev_pins: list[str] = []
            pin_roles: dict[str, str] = {}
            if item.get("pin"):
                p_one = str(item["pin"]).strip().upper()
                dev_pins.append(p_one)
            raw_p = item.get("pins")
            if isinstance(raw_p, list):
                for pv in raw_p:
                    pu = str(pv).strip().upper()
                    if pu and pu not in dev_pins:
                        dev_pins.append(pu)
            elif isinstance(raw_p, dict):
                for role, pv in raw_p.items():
                    pu = str(pv).strip().upper()
                    if pu:
                        pin_roles[str(role)] = pu
                        if pu not in dev_pins:
                            dev_pins.append(pu)
            elif isinstance(raw_p, str) and raw_p.strip():
                pu = raw_p.strip().upper()
                if pu not in dev_pins:
                    dev_pins.append(pu)

            active_lvl = str(item.get("active_level") or item.get("polarity") or "").strip() or None
            max_duty: float | None = None
            if item.get("max_duty_percent") is not None:
                max_duty = float(item["max_duty_percent"])
            elif item.get("max_duty") is not None:
                val_d = float(item["max_duty"])
                max_duty = val_d * 100.0 if 0.0 < val_d <= 1.0 else val_d

            desc = str(item.get("description") or item.get("notes") or "").strip()
            known_dev_keys = {
                "name", "id", "part_number", "model", "chip", "bus", "peripheral", "interface",
                "address", "i2c_address", "pin", "pins", "active_level", "polarity",
                "max_duty", "max_duty_percent", "description", "notes",
            }
            extra = {ek: ev for ek, ev in item.items() if ek not in known_dev_keys}

            dev_spec = BoardDeviceSpec(
                name=name,
                part_number=part,
                bus=bus,
                address=addr_str,
                pins=dev_pins,
                pin_roles=pin_roles,
                active_level=active_lvl,
                max_duty_percent=max_duty,
                description=desc,
                extra=extra,
            )
            devices.append(dev_spec)

            # 反向挂载到关联的引脚规格上，让 Agent 查引脚即刻看到外部物理器件限制
            dev_tag = f"{name}({part})" if part else name
            for p_id in dev_pins:
                p_spec = _ensure_pin_spec(pin_map, p_id)
                if p_spec is None:
                    continue
                if dev_tag not in p_spec.connected_devices:
                    p_spec.connected_devices.append(dev_tag)
                if active_lvl and not p_spec.active_level:
                    p_spec.active_level = active_lvl
                if max_duty is not None and p_spec.max_duty_percent is None:
                    p_spec.max_duty_percent = max_duty

    return board_name, board_notes, devices


def _ensure_pin_spec(pin_map: dict[str, BoardPinSpec], pin_id: str) -> BoardPinSpec | None:
    if pin_id in pin_map:
        return pin_map[pin_id]
    m = re.match(r"^P([A-Z])(\d{1,2})$", pin_id)
    if not m:
        return None
    port_letter = m.group(1)
    pin_no = int(m.group(2), 10)
    if pin_no < 0 or pin_no > 15:
        return None
    spec = BoardPinSpec(pin=pin_id, port=f"GPIO{port_letter}", pin_number=pin_no)
    pin_map[pin_id] = spec
    return spec


def _normalize_mode_name(raw: str) -> str | None:
    low = raw.strip().lower()
    if low in ("output", "gpio_output", "out", "pp", "od"):
        return "Output"
    if low in ("input", "gpio_input", "in", "exti"):
        return "Input"
    if low in ("alternate", "af", "pwm", "uart", "usart", "spi", "i2c", "can"):
        return "Alternate"
    if low in ("analog", "adc", "dac"):
        return "Analog"
    return None


# ---------------------------------------------------------------------------
# 静态意图 vs 真机实测实时对账（Live Drift Check）
# ---------------------------------------------------------------------------


def _run_live_pin_drift_check(
    probe: DebugProbe,
    svd_device: SvdDevice,
    declared_pins: list[BoardPinSpec],
    findings: list[DiagnosticFinding],
) -> list[PinDriftRecord]:
    svd_ports = {p.name.upper(): p for p in svd_device.peripherals}
    port_cache: dict[str, tuple[str, dict[int, GpioPinSnapshot]]] = {}

    records: list[PinDriftRecord] = []
    for spec in declared_pins:
        if not spec.expected_mode:
            continue

        port_up = spec.port.upper()
        if port_up not in svd_ports:
            continue

        if port_up not in port_cache:
            port_periph = svd_ports[port_up]
            clk = check_peripheral_clock(probe, svd_device, port_periph.name)
            if clk.found_in_svd and clk.enabled is False:
                port_cache[port_up] = (
                    f"{port_periph.name} 时钟未开启 ({clk.clock_peripheral}->{clk.register}.{clk.field}=0)",
                    {},
                )
            else:
                all_16 = read_gpio_port_pins(
                    probe,
                    port_periph,
                    include_analog=True,
                    include_reset_debug_pins=True,
                )
                port_cache[port_up] = ("ok", {gp.pin_number: gp for gp in all_16})

        port_state, pin_snapshots = port_cache[port_up]
        sig_desc = spec.signal or spec.label or spec.expected_mode

        if port_state != "ok":
            reason = f".ioc 声明 {spec.pin} 为 {sig_desc} (期望 {spec.expected_mode})，但真机 {port_state}！"
            rec = PinDriftRecord(
                pin=spec.pin,
                signal=spec.signal,
                label=spec.label,
                expected_mode=spec.expected_mode,
                live_mode="Unclocked",
                matched=False,
                expected_af=spec.expected_af,
                expected_output_type=spec.expected_output_type,
                reason=reason,
            )
            records.append(rec)
            findings.append(
                DiagnosticFinding(
                    severity="error",
                    stage="board_drift",
                    code="PIN_PORT_UNCLOCKED",
                    title=f"{spec.pin} ({sig_desc}) 所在端口 {spec.port} 时钟未开启",
                    detail=reason,
                    suggestion=f"检查 MX_GPIO_Init() 或外设 MSP 初始化中是否已调用 __HAL_RCC_{spec.port}_CLK_ENABLE()。",
                    evidence=rec.to_dict(),
                )
            )
            continue

        live_pin = pin_snapshots.get(spec.pin_number)
        if live_pin is None:
            continue

        matched = True
        reason = ""
        if live_pin.mode != spec.expected_mode:
            matched = False
            if live_pin.mode == "Analog" and spec.expected_mode != "Analog":
                reason = (
                    f".ioc 配置 {spec.pin} 为 {sig_desc} (期望 {spec.expected_mode})，"
                    f"但真机 {spec.port}->MODER 仍停留在 Analog 复位态（初始化代码未执行或被后续代码覆盖）！"
                )
            else:
                reason = (
                    f".ioc 配置 {spec.pin} 为 {sig_desc} (期望 {spec.expected_mode})，"
                    f"但真机 {spec.port}->MODER 当前实测为 {live_pin.mode}！"
                )
        elif (
            spec.expected_mode == "Alternate"
            and spec.expected_af is not None
            and live_pin.af is not None
            and spec.expected_af != live_pin.af
        ):
            matched = False
            reason = (
                f"{spec.pin} ({sig_desc}) 虽为 Alternate 模式，但复用号漂移："
                f".ioc 期望 AF{spec.expected_af}，真机实测 AF{live_pin.af}！"
            )
        elif (
            spec.expected_output_type == "Open-Drain"
            and live_pin.otyper_in_svd
            and live_pin.output_type == "Push-Pull"
        ):
            matched = False
            reason = (
                f"{spec.pin} ({sig_desc}) 期望为 Open-Drain 开漏输出，但真机 {spec.port}->OTYPER 实测为 Push-Pull 推挽！"
            )

        rec = PinDriftRecord(
            pin=spec.pin,
            signal=spec.signal,
            label=spec.label,
            expected_mode=spec.expected_mode,
            live_mode=live_pin.mode,
            matched=matched,
            expected_af=spec.expected_af,
            live_af=live_pin.af,
            expected_output_type=spec.expected_output_type,
            live_output_type=live_pin.output_type,
            live_odr=live_pin.odr,
            live_idr=live_pin.idr,
            reason=reason,
        )
        records.append(rec)

        if not matched:
            findings.append(
                DiagnosticFinding(
                    severity="error",
                    stage="board_drift",
                    code="PIN_MODE_DRIFT",
                    title=f"引脚 {spec.pin} ({sig_desc}) 真机状态与 .ioc 设计意图不符",
                    detail=reason,
                    suggestion=(
                        f"核对 CubeMX 是否已重新 Generate Code，并确认初始化流程已实际执行到 {spec.pin} 的 HAL_GPIO_Init()。"
                    ),
                    evidence=rec.to_dict(),
                )
            )

    return records


# ---------------------------------------------------------------------------
# 文件定位与字符串反转义工具
# ---------------------------------------------------------------------------


def _find_manifest(root: Path, explicit: str | os.PathLike | None) -> Path | None:
    if explicit is not None:
        mp = Path(explicit).expanduser()
        if not mp.is_absolute():
            mp = (root / mp).resolve()
        if not mp.is_file():
            raise FileNotFoundError(f"Manifest file not found: {mp}")
        return mp
    for name in _MANIFEST_NAMES:
        cand = root / name
        if cand.is_file():
            return cand
    return None


def _find_ioc(root: Path, explicit: Any) -> Path | None:
    if explicit not in (None, ""):
        ip = Path(str(explicit).strip()).expanduser()
        if not ip.is_absolute():
            ip = (root / ip).resolve()
        if not ip.is_file():
            raise FileNotFoundError(f".ioc file not found: {ip}")
        return ip

    cands = sorted(root.glob("*.ioc"))
    if not cands:
        for sub in sorted(root.iterdir()):
            if sub.is_dir() and sub.name not in _SKIP_SCAN_DIRS and not sub.name.startswith("."):
                cands.extend(sorted(sub.glob("*.ioc")))
    return cands[0] if cands else None


def _unescape_ioc(s: str) -> str:
    return s.replace(r"\:", ":").replace(r"\=", "=").replace(r"\ ", " ").replace(r"\\", "\\")


def _try_int(raw: str) -> int | str:
    s = raw.strip().replace("_", "")
    try:
        return int(s, 0)
    except ValueError:
        return raw


def _pick_int(params: dict[str, int | str], *keys: str) -> int | None:
    for k in keys:
        v = params.get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            return v
    return None
