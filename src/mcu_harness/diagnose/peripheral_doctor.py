"""跨外设因果体检医生（Peripheral Causal Doctor）。

解决嵌入式调试最常见的“为什么外设不工作”跨模块盲区，一站式串联 4 层硬件真相：
    1. RCC（或 CRM/RCU）外设时钟门控使能核查（Clock Gate Check）
    2. Cortex-M 架构级 NVIC 中断使能/挂起/活跃/优先级通路核查（Interrupt Path Check）
    3. 外设自身主使能位、偏离复位值寄存器与典型死锁/错误标志位扫描（Self Status & Error Flags）
    4. 全片已使能时钟 GPIO 端口的引脚模式与 AF 复用快照（Configured GPIO Pin Mux Snapshot）
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field as dc_field
from typing import Any

from mcu_harness.probe.base import DebugProbe, ProbeError
from mcu_harness.probe.peripheral import PeripheralSnapshot, inspect_peripheral_registers
from mcu_harness.svd import (
    DecodedField,
    DecodedRegister,
    SvdDevice,
    SvdField,
    SvdPeripheral,
    SvdRegister,
    find_peripheral,
)

# ---- Cortex-M NVIC 架构标准内存映射基址（通吃所有 Armv6-M / Armv7-M / Armv8-M） ----
NVIC_ISER_BASE = 0xE000E100  # Interrupt Set-Enable Registers
NVIC_ISPR_BASE = 0xE000E200  # Interrupt Set-Pending Registers
NVIC_IABR_BASE = 0xE000E300  # Interrupt Active Bit Registers (Armv7-M / Armv8-M; Armv6-M 读回 0)
NVIC_IPR_BASE = 0xE000E400   # Interrupt Priority Registers

_CLOCK_PERIPH_NAMES = ("RCC", "RCC_S", "RCC_NS", "CRM", "RCU")
_RE_GPIO_PORT = re.compile(r"^GPIO([A-K])$", re.IGNORECASE)

# 常见外设控制寄存器主使能位（按优先级匹配）
_MASTER_ENABLE_FIELD_NAMES = ("UE", "SPE", "PE", "CEN", "ADEN", "EN")

# 状态寄存器典型错误 / 阻塞标志知识库：(标准码, 严重级别, 简短标题, 因果解释, 排障建议)
_STATUS_ERROR_FLAG_KB: dict[str, tuple[str, str, str, str, str]] = {
    "ORE": (
        "FLAG_ORE",
        "error",
        "接收溢出错误 (ORE=1)",
        "新接收字节移入时前一个数据尚未从 RDR/DR 读走，硬件置位 ORE=1。"
        "在 STM32 UART/USART 中，ORE 未清除会直接阻塞后续 RXNE 中断或 DMA 请求，导致外设看似‘死锁收不到数据’！",
        "排查接收中断是否被高优先级任务/断点阻塞或漏读 RDR；在 ErrorCallback 中显式清除 ORE（如写入 ICR.ORECF=1 或 __HAL_UART_CLEAR_OREFLAG）。",
    ),
    "FE": (
        "FLAG_FE",
        "error",
        "帧格式错误 (FE=1)",
        "接收端未在预期采样点检测到高电平停止位（FE=1），常见于通信双方波特率不匹配（如 HSE_VALUE 与板载晶振不符）、"
        "字长/停止位配置不一致，或 RX 引脚悬空被拉低。",
        "核对时钟树（HSE_VALUE / PLL 分频）与 BRR 波特率寄存器，确认对端波特率及 RX 空闲高电平。",
    ),
    "NE": (
        "FLAG_NE",
        "warning",
        "物理层采样噪声标志 (NE=1)",
        "接收位采样期间检测到毛刺跳变（NE=1），常见于线束串扰、共地不良或 RX 引脚未开启上拉导致浮空抖动。",
        "检查双方 GND 共地，给 RX 引脚配置内部上拉（Pull-Up），并写入 ICR.NECF 清除噪声标志。",
    ),
    "NF": (
        "FLAG_NE",
        "warning",
        "物理层采样噪声标志 (NF=1)",
        "接收位采样期间检测到毛刺跳变（NF=1），常见于线束串扰、共地不良或 RX 引脚浮空。",
        "检查双方 GND 共地，开启 RX 引脚上拉电阻并清除标志位。",
    ),
    "PE": (
        "FLAG_PE",
        "error",
        "奇偶校验错误 (PE=1)",
        "状态寄存器奇偶校验错误标志置 1，接收数据的奇偶性与本地配置不符。",
        "核对双方 Parity 设置；注意 STM32 开启奇偶校验时数据位宽（WordLength）通常需包含校验位设为 9 bits。",
    ),
    "NACKF": (
        "FLAG_NACKF",
        "error",
        "I2C 无应答错误 (NACKF=1)",
        "主机发出从机地址或数据后第 9 个时钟周期未收到从机拉低 SDA 的 ACK 应答（NACKF=1）。",
        "确认从机 7-bit 地址是否已左移 1 位（HAL 接口需传 addr << 1）、从机供电与复位脚是否正常、SDA/SCL 是否有外部上拉电阻。",
    ),
    "AF": (
        "FLAG_NACKF",
        "error",
        "I2C 应答失败 (AF=1)",
        "I2C 总线未收到从机 ACK 应答（AF=1）。",
        "核对从机 7-bit I2C 地址（注意左移 1 位）、从机上电状态及总线外部上拉电阻。",
    ),
    "BERR": (
        "FLAG_BERR",
        "error",
        "总线时序错误 (BERR=1)",
        "字节传输期间出现非法的 START/STOP 跳变（BERR=1），常见于外部干扰、引脚未先配成 Open-Drain 就开外设，或多主机冲突。",
        "检查 SDA/SCL 上拉与初始化顺序（先配 GPIO AF Open-Drain + Pull-Up，再使能 I2C），并清除 BERR 标志。",
    ),
    "ARLO": (
        "FLAG_ARLO",
        "error",
        "总线仲裁丢失 (ARLO=1)",
        "I2C 试图释放高电平时检测到总线实际为低（ARLO=1）；单主机场景下通常意味着 SDA 被从机死锁拉低或缺少上拉电阻！",
        "检查 GPIO IDR 实测电平；若从机卡死拉低 SDA，可临时切为 GPIO 翻转 9 个 SCL 脉冲恢复总线。",
    ),
    "OVR": (
        "FLAG_OVR",
        "error",
        "数据溢出错误 (OVR=1)",
        "新采样/接收完成时旧数据尚未被 CPU 或 DMA 取走（OVR=1），后续传输/转换可能被硬件中止或丢弃。",
        "检查接收 DMA 是否正常启动、缓冲区计数是否耗尽，并按手册顺序清除 OVR 标志。",
    ),
    "UDR": (
        "FLAG_UDR",
        "error",
        "发送下溢错误 (UDR=1)",
        "外部时钟/触发到来时发送寄存器或 FIFO 为空（UDR=1）。",
        "提高 TX DMA 通道优先级或在启动传输前预先写入首帧数据，随后清除 UDR 标志。",
    ),
    "MODF": (
        "FLAG_MODF",
        "error",
        "SPI 主从模式故障 (MODF=1)",
        "SPI 主模式下硬件 NSS 引脚被拉低触发模式冲突（MODF=1），硬件会自动清零 SPE 关闭 SPI！",
        "使用软件片选时请确保置位 SSM=1 且 SSI=1；使用硬件 NSS 时请检查外部上拉电平。",
    ),
    "BUSY": (
        "FLAG_BUSY",
        "warning",
        "外设/总线处于忙状态 (BUSY=1)",
        "状态寄存器 BUSY=1：外设正在收发中，或因外部引脚被持续拉低（如 I2C 总线死锁、UART RX 持续低电平）卡在忙状态。",
        "若当前并无正常业务收发而 BUSY 恒为 1，请检查对应物理引脚（SDA/SCL 或 RX）空闲电平是否为高。",
    ),
    "BSY": (
        "FLAG_BUSY",
        "warning",
        "外设/总线处于忙状态 (BSY=1)",
        "状态寄存器 BSY=1：外设正在传输中，或因总线物理电平被异常拉低陷入忙死锁（如 STM32 I2C 经典 BSY 锁死）。",
        "检查对应 GPIO IDR 物理电平是否为高、I2C 引脚是否已配为 Open-Drain；必要时执行 SWRST 软复位外设。",
    ),
}


# ---------------------------------------------------------------------------
# 数据结构定义
# ---------------------------------------------------------------------------


@dataclass
class DiagnosticFinding:
    """单条因果诊断结论。"""

    severity: str  # "critical" | "error" | "warning" | "info"
    stage: str     # "clock" | "nvic" | "self_status" | "gpio_mux" | "board_drift"
    code: str
    title: str
    detail: str
    suggestion: str = ""
    evidence: dict[str, Any] = dc_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClockGateStatus:
    """RCC（或 CRM/RCU）外设时钟门控核查结果。"""

    found_in_svd: bool
    clock_peripheral: str | None = None
    register: str | None = None
    address: str | None = None
    field: str | None = None
    bit_range: str | None = None
    raw_register_value: str | None = None
    enabled: bool | None = None
    read_error: str | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NvicIrqStatus:
    """Cortex-M NVIC 单个外部中断通道实时状态。"""

    name: str
    irqn: int
    description: str = ""
    enabled: bool = False
    pending: bool = False
    active: bool = False
    priority_raw: int = 0
    priority_hex: str = "0x00"
    iser_address: str = ""
    ispr_address: str = ""
    read_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MasterEnableStatus:
    """外设控制寄存器主使能位状态（如 CR1.UE / CR1.SPE / CR1.PE / CR1.CEN / CR.ADEN）。"""

    found: bool
    register: str | None = None
    field: str | None = None
    value: int | None = None
    enabled: bool | None = None
    extra_enables: dict[str, int] = dc_field(default_factory=dict)  # 如 TE/RE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StatusFlagAlert:
    """状态寄存器中置 1 的异常或忙标志。"""

    register: str
    field: str
    bit_range: str
    value: int
    severity: str
    code: str
    title: str
    detail: str
    suggestion: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChangedRegisterSummary:
    """偏离 SVD resetValue 的寄存器精简摘要（抑制未改动寄存器噪音）。"""

    register: str
    address: str
    raw_hex: str
    reset_value: str | None
    active_fields: list[dict[str, Any]] = dc_field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SelfStatusSummary:
    """阶段 3：外设自身控制与状态扫描汇总。"""

    registers_read: int = 0
    skipped_due_to_clock_disabled: bool = False
    master_enable: MasterEnableStatus = dc_field(default_factory=lambda: MasterEnableStatus(found=False))
    enabled_interrupt_fields: list[str] = dc_field(default_factory=list)
    active_error_or_busy_flags: list[StatusFlagAlert] = dc_field(default_factory=list)
    changed_registers: list[ChangedRegisterSummary] = dc_field(default_factory=list)
    read_errors: dict[str, str] = dc_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GpioPinSnapshot:
    """单个非复位 Analog GPIO 引脚的硬件实测配置。"""

    pin: str                 # 如 "PA5", "PA9"
    port: str                # 如 "GPIOA"
    pin_number: int          # 0..15
    mode: str                # "Input" | "Output" | "Alternate" | "Analog"
    output_type: str | None = None  # "Push-Pull" | "Open-Drain" | None
    pull: str | None = None         # "NoPull" | "PullUp" | "PullDown" | None
    speed: str | None = None        # "Low" | "Medium" | "High" | "VeryHigh" | None
    af: int | None = None           # 0..15（仅 Alternate 模式）
    af_label: str | None = None     # 如 "AF7"
    idr: int | None = None          # 0 / 1（若 SVD 提供 IDR）
    odr: int | None = None          # 0 / 1（若 SVD 提供 ODR）
    otyper_in_svd: bool = False
    summary: str = ""
    warnings: list[str] = dc_field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PeripheralDiagnosisReport:
    """跨外设因果体检完整报告。"""

    peripheral: str
    base_address: str
    svd_device: str
    healthy: bool
    highest_severity: str  # "critical" | "error" | "warning" | "info" | "ok"
    summary: str
    clock_gate: ClockGateStatus
    nvic_irqs: list[NvicIrqStatus] = dc_field(default_factory=list)
    self_status: SelfStatusSummary = dc_field(default_factory=SelfStatusSummary)
    configured_gpio_pins: list[GpioPinSnapshot] = dc_field(default_factory=list)
    findings: list[DiagnosticFinding] = dc_field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_text(self) -> str:
        lines = [
            f"=== Peripheral Causal Diagnosis: {self.peripheral} @ {self.base_address} ({self.svd_device}) ===",
            f"Status: {self.highest_severity.upper()} | {self.summary}",
            "",
        ]

        # Stage 1: Clock
        cg = self.clock_gate
        if cg.found_in_svd:
            clk_state = "ENABLED (1)" if cg.enabled else ("DISABLED (0)" if cg.enabled is False else f"ERROR ({cg.read_error})")
            lines.append(
                f"[1. Clock Gate] {cg.clock_peripheral}->{cg.register}.{cg.field}{cg.bit_range or ''} "
                f"@ {cg.address} = {clk_state} (reg={cg.raw_register_value})"
            )
        else:
            lines.append(f"[1. Clock Gate] (not found in SVD: {cg.note})")

        # Stage 2: NVIC
        if self.nvic_irqs:
            lines.append("[2. NVIC Interrupt Path]")
            for irq in self.nvic_irqs:
                lines.append(
                    f"  - IRQ {irq.irqn} ({irq.name}): enabled={irq.enabled}, "
                    f"pending={irq.pending}, active={irq.active}, prio={irq.priority_hex}"
                )
        else:
            lines.append("[2. NVIC Interrupt Path] (no <interrupt> declared on peripheral in SVD)")

        # Stage 3: Self Status
        ss = self.self_status
        if ss.skipped_due_to_clock_disabled:
            lines.append("[3. Self Status] (skipped peripheral register scan because peripheral clock is OFF)")
        else:
            me = ss.master_enable
            me_txt = (
                f"{me.register}.{me.field}={me.value} ({'ON' if me.enabled else 'OFF'})"
                if me.found
                else "(no single master-enable bit)"
            )
            lines.append(f"[3. Self Status] read={ss.registers_read} reg(s), master_enable={me_txt}")
            for cr in ss.changed_registers:
                f_txt = ", ".join(f"{af['name']}={af['hex']}" for af in cr.active_fields[:8])
                lines.append(f"  - {cr.register} @ {cr.address} = {cr.raw_hex} (reset={cr.reset_value}) [{f_txt}]")

        # Stage 4: GPIO Mux
        if self.configured_gpio_pins:
            lines.append(f"[4. Configured GPIO Pins] ({len(self.configured_gpio_pins)} active pin(s))")
            for gp in self.configured_gpio_pins:
                lines.append(f"  - {gp.summary}")

        # Findings
        if self.findings:
            lines.append("\n[Causal Findings & Recommendations]")
            for idx, fd in enumerate(self.findings, 1):
                lines.append(f"  {idx}. [{fd.severity.upper()}] ({fd.stage}/{fd.code}) {fd.title}")
                lines.append(f"     原因: {fd.detail}")
                if fd.suggestion:
                    lines.append(f"     建议: {fd.suggestion}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 核心阶段实现
# ---------------------------------------------------------------------------


def check_peripheral_clock(
    probe: DebugProbe,
    svd_device: SvdDevice,
    peripheral_name: str,
) -> ClockGateStatus:
    """阶段 1：在 SVD 的 RCC / CRM / RCU 等时钟控制器中定位 `<PERIPH>EN` 并实测是否开启。"""
    match = _find_clock_enable_bit(svd_device, peripheral_name)
    if match is None:
        return ClockGateStatus(
            found_in_svd=False,
            note=f"No {peripheral_name.upper()}EN clock gate bit found in SVD clock peripheral",
        )

    clk_periph, reg, fld = match
    addr = clk_periph.reg_address(reg)
    hex_digits = max(2, (reg.size + 3) // 4)
    try:
        raw_val = probe.read_register_sized(addr, reg.size)
    except ProbeError as exc:
        return ClockGateStatus(
            found_in_svd=True,
            clock_peripheral=clk_periph.name,
            register=reg.name,
            address=f"0x{addr:08X}",
            field=fld.name,
            bit_range=fld.bit_range_str,
            enabled=None,
            read_error=str(exc),
        )

    bit_val = fld.extract(raw_val)
    return ClockGateStatus(
        found_in_svd=True,
        clock_peripheral=clk_periph.name,
        register=reg.name,
        address=f"0x{addr:08X}",
        field=fld.name,
        bit_range=fld.bit_range_str,
        raw_register_value=f"0x{raw_val:0{hex_digits}X}",
        enabled=bool(bit_val != 0),
    )


def check_peripheral_nvic(
    probe: DebugProbe,
    peripheral: SvdPeripheral,
) -> list[NvicIrqStatus]:
    """阶段 2：直接按 Cortex-M 标准 SCS 地址读取目标外设关联的 NVIC 中断状态。"""
    results: list[NvicIrqStatus] = []
    for intr in peripheral.interrupts:
        irqn = intr.value
        if irqn < 0:
            # 负数异常号属于内核系统异常（如 SysTick=-1），不在外部 NVIC_ISER 位图中
            continue

        reg_idx = irqn // 32
        bit_mask = 1 << (irqn % 32)
        iser_addr = NVIC_ISER_BASE + reg_idx * 4
        ispr_addr = NVIC_ISPR_BASE + reg_idx * 4
        iabr_addr = NVIC_IABR_BASE + reg_idx * 4
        ipr_word_addr = NVIC_IPR_BASE + (irqn // 4) * 4
        ipr_byte_shift = (irqn % 4) * 8

        try:
            iser_val = probe.read_u32(iser_addr, 1)[0]
            ispr_val = probe.read_u32(ispr_addr, 1)[0]
            iabr_val = probe.read_u32(iabr_addr, 1)[0]
            # 使用 32 位对齐读取 NVIC_IPR 并提取对应字节，兼顾 Armv6-M (Cortex-M0/M0+) 对齐限制与小端字节序
            ipr_word = probe.read_u32(ipr_word_addr, 1)[0]
            prio_raw = (ipr_word >> ipr_byte_shift) & 0xFF
            results.append(
                NvicIrqStatus(
                    name=intr.name,
                    irqn=irqn,
                    description=intr.description,
                    enabled=bool(iser_val & bit_mask),
                    pending=bool(ispr_val & bit_mask),
                    active=bool(iabr_val & bit_mask),
                    priority_raw=prio_raw,
                    priority_hex=f"0x{prio_raw:02X}",
                    iser_address=f"0x{iser_addr:08X}",
                    ispr_address=f"0x{ispr_addr:08X}",
                )
            )
        except ProbeError as exc:
            results.append(
                NvicIrqStatus(
                    name=intr.name,
                    irqn=irqn,
                    description=intr.description,
                    iser_address=f"0x{iser_addr:08X}",
                    ispr_address=f"0x{ispr_addr:08X}",
                    read_error=str(exc),
                )
            )
    return results


def scan_configured_gpio_pins(
    probe: DebugProbe,
    svd_device: SvdDevice,
    *,
    include_analog: bool = False,
    include_reset_debug_pins: bool = False,
) -> list[GpioPinSnapshot]:
    """阶段 4：遍历 `GPIOA`..`GPIOK`（若存在 RCC 时钟位则仅扫时钟已开启的端口），解码非默认 Analog 引脚配置。"""
    gpio_ports: list[tuple[str, SvdPeripheral]] = []
    for p in svd_device.peripherals:
        m = _RE_GPIO_PORT.match(p.name)
        if m:
            gpio_ports.append((m.group(1).upper(), p))
    gpio_ports.sort(key=lambda item: item[0])

    snapshots: list[GpioPinSnapshot] = []
    for port_letter, port_periph in gpio_ports:
        clk = check_peripheral_clock(probe, svd_device, port_periph.name)
        if clk.found_in_svd and clk.enabled is False:
            # 端口时钟未开启：跳过读取，避免触发未开时钟总线错误或读回 0x00000000 被误判为 16 个 Input 引脚
            continue

        port_pins = read_gpio_port_pins(
            probe,
            port_periph,
            port_letter=port_letter,
            include_analog=include_analog,
            include_reset_debug_pins=include_reset_debug_pins,
        )
        snapshots.extend(port_pins)

    return snapshots


def read_gpio_port_pins(
    probe: DebugProbe,
    port_periph: SvdPeripheral,
    *,
    port_letter: str | None = None,
    include_analog: bool = False,
    include_reset_debug_pins: bool = False,
) -> list[GpioPinSnapshot]:
    """读取单个已开时钟 GPIO 端口的寄存器并解码 0..15 号引脚配置。"""
    if port_letter is None:
        m = _RE_GPIO_PORT.match(port_periph.name)
        port_letter = m.group(1).upper() if m else port_periph.name[-1:].upper()

    moder_reg = _try_find_reg(port_periph, "MODER")
    if moder_reg is None:
        return []

    try:
        moder_val = probe.read_register_sized(port_periph.reg_address(moder_reg), moder_reg.size)
    except ProbeError:
        return []

    otyper_reg = _try_find_reg(port_periph, "OTYPER")
    ospeedr_reg = _try_find_reg(port_periph, "OSPEEDR")
    pupdr_reg = _try_find_reg(port_periph, "PUPDR")
    idr_reg = _try_find_reg(port_periph, "IDR")
    odr_reg = _try_find_reg(port_periph, "ODR")
    afrl_reg = _try_find_reg(port_periph, "AFRL") or _try_find_reg(port_periph, "AFR0")
    afrh_reg = _try_find_reg(port_periph, "AFRH") or _try_find_reg(port_periph, "AFR1")

    otyper_val = _safe_read_opt_reg(probe, port_periph, otyper_reg)
    ospeedr_val = _safe_read_opt_reg(probe, port_periph, ospeedr_reg)
    pupdr_val = _safe_read_opt_reg(probe, port_periph, pupdr_reg)
    idr_val = _safe_read_opt_reg(probe, port_periph, idr_reg)
    odr_val = _safe_read_opt_reg(probe, port_periph, odr_reg)
    afrl_val = _safe_read_opt_reg(probe, port_periph, afrl_reg)
    afrh_val = _safe_read_opt_reg(probe, port_periph, afrh_reg)

    mode_names = {0: "Input", 1: "Output", 2: "Alternate", 3: "Analog"}
    pull_names = {0: "NoPull", 1: "PullUp", 2: "PullDown", 3: "Reserved"}
    speed_names = {0: "Low", 1: "Medium", 2: "High", 3: "VeryHigh"}

    pins: list[GpioPinSnapshot] = []
    for pin_no in range(16):
        mode_bits = (moder_val >> (pin_no * 2)) & 0x3
        mode = mode_names[mode_bits]

        if mode == "Analog" and not include_analog:
            continue

        af_num: int | None = None
        af_label: str | None = None
        if mode == "Alternate":
            if pin_no < 8 and afrl_val is not None:
                af_num = (afrl_val >> (pin_no * 4)) & 0xF
                af_label = f"AF{af_num}"
            elif pin_no >= 8 and afrh_val is not None:
                af_num = (afrh_val >> ((pin_no - 8) * 4)) & 0xF
                af_label = f"AF{af_num}"

        # 跳过上电默认处于 JTAG/SWD AF0 复位状态且未被用户重配的调试引脚（PA13/PA14/PA15/PB3/PB4）
        if (
            not include_reset_debug_pins
            and mode_bits == 0x2
            and moder_reg.reset_value is not None
            and ((moder_reg.reset_value >> (pin_no * 2)) & 0x3) == 0x2
            and (af_num in (None, 0))
        ):
            continue

        output_type: str | None = None
        if mode in ("Output", "Alternate"):
            if otyper_val is not None:
                output_type = "Open-Drain" if ((otyper_val >> pin_no) & 0x1) else "Push-Pull"
            else:
                output_type = "Push-Pull"

        pull = pull_names[(pupdr_val >> (pin_no * 2)) & 0x3] if pupdr_val is not None else None
        speed = speed_names[(ospeedr_val >> (pin_no * 2)) & 0x3] if ospeedr_val is not None else None
        idr_bit = ((idr_val >> pin_no) & 0x1) if idr_val is not None else None
        odr_bit = ((odr_val >> pin_no) & 0x1) if odr_val is not None else None

        pin_name = f"P{port_letter}{pin_no}"
        summary_parts: list[str] = []
        if mode == "Alternate":
            summary_parts.append(f"Alternate({af_label})" if af_label else "Alternate")
        else:
            summary_parts.append(mode)

        if output_type:
            summary_parts.append(output_type)
        if pull and pull != "NoPull":
            summary_parts.append(pull)
        if mode == "Output" and odr_bit is not None:
            summary_parts.append(f"ODR={odr_bit}")
        elif mode == "Input" and idr_bit is not None:
            summary_parts.append(f"IDR={idr_bit}")

        summary = f"{pin_name}: {', '.join(summary_parts)}"
        pins.append(
            GpioPinSnapshot(
                pin=pin_name,
                port=port_periph.name,
                pin_number=pin_no,
                mode=mode,
                output_type=output_type,
                pull=pull,
                speed=speed,
                af=af_num,
                af_label=af_label,
                idr=idr_bit,
                odr=odr_bit,
                otyper_in_svd=(otyper_reg is not None),
                summary=summary,
            )
        )

    return pins


def diagnose_peripheral(
    probe: DebugProbe,
    svd_device: SvdDevice,
    peripheral_name: str,
    *,
    include_gpio_mux: bool = True,
) -> PeripheralDiagnosisReport:
    """对目标外设执行一站式跨外设因果体检（时钟 -> NVIC -> 自身控制/错误标志 -> GPIO 复用）。"""
    periph = find_peripheral(svd_device, peripheral_name)
    findings: list[DiagnosticFinding] = []

    # ---- 阶段 1：RCC 外设时钟门控核查 ----------------------------------------
    clock_gate = check_peripheral_clock(probe, svd_device, periph.name)
    if clock_gate.found_in_svd and clock_gate.enabled is False:
        findings.append(
            DiagnosticFinding(
                severity="critical",
                stage="clock",
                code="CLOCK_DISABLED",
                title=f"外设时钟未开启 ({clock_gate.clock_peripheral}->{clock_gate.register}.{clock_gate.field} = 0)",
                detail=(
                    f"外设时钟未开启（{clock_gate.clock_peripheral}->{clock_gate.register}.{clock_gate.field} = 0），"
                    f"写入 {periph.name} 寄存器的配置会被硬件直接忽略，且读取全部返回 0！"
                ),
                suggestion=(
                    f"必须在配置 {periph.name} 寄存器之前开启外设时钟（例如调用 "
                    f"__HAL_RCC_{periph.name}_CLK_ENABLE() 或置位 "
                    f"{clock_gate.clock_peripheral}->{clock_gate.register}.{clock_gate.field} = 1）。"
                ),
                evidence={
                    "clock_peripheral": clock_gate.clock_peripheral,
                    "register": clock_gate.register,
                    "address": clock_gate.address,
                    "field": clock_gate.field,
                    "raw_register_value": clock_gate.raw_register_value,
                },
            )
        )

    # ---- 阶段 2：Cortex-M NVIC 中断通路核查 ----------------------------------
    nvic_irqs = check_peripheral_nvic(probe, periph)
    for irq in nvic_irqs:
        if irq.read_error:
            continue
        if irq.pending and not irq.enabled:
            findings.append(
                DiagnosticFinding(
                    severity="error",
                    stage="nvic",
                    code="NVIC_PENDING_BUT_DISABLED",
                    title=f"中断已在硬件挂起但 NVIC 未使能 ({irq.name}, IRQn={irq.irqn})",
                    detail=(
                        f"中断 {irq.name} (IRQn={irq.irqn}) 已在硬件挂起 (NVIC_ISPR=1)，"
                        f"但 NVIC 未使能 (NVIC_ISER=0, enabled=False)，CPU 无法响应中断（常见于漏调 HAL_NVIC_EnableIRQ）！"
                    ),
                    suggestion=(
                        f"请调用 HAL_NVIC_SetPriority({irq.name}_IRQn, ...) 与 "
                        f"HAL_NVIC_EnableIRQ({irq.name}_IRQn)，并确保向量表中已注册对应 IRQHandler。"
                    ),
                    evidence=irq.to_dict(),
                )
            )

    # ---- 阶段 3：外设自身主使能位、偏离复位值寄存器与错误/忙标志扫描 -----------
    self_status = SelfStatusSummary()
    if clock_gate.found_in_svd and clock_gate.enabled is False:
        # 时钟未开时外设寄存器读回值全 0 或触发总线异常，跳过自身寄存器扫描避免误报假标志
        self_status.skipped_due_to_clock_disabled = True
    else:
        snap = inspect_peripheral_registers(probe, svd_device, periph.name, read_all=True)
        assert isinstance(snap, PeripheralSnapshot)
        self_status = _analyze_self_registers(periph, snap, findings)

        # 结合外设内部 *IE 中断使能位与阶段 2 NVIC 使能状态做联合因果推理
        if self_status.enabled_interrupt_fields and nvic_irqs:
            valid_irqs = [q for q in nvic_irqs if not q.read_error]
            if valid_irqs and not any(q.enabled for q in valid_irqs) and not any(q.pending for q in valid_irqs):
                irq_labels = ", ".join(f"{q.name}(IRQn={q.irqn})" for q in valid_irqs)
                ie_labels = ", ".join(self_status.enabled_interrupt_fields)
                findings.append(
                    DiagnosticFinding(
                        severity="warning",
                        stage="nvic",
                        code="IRQ_SOURCE_ENABLED_NVIC_DISABLED",
                        title=f"{periph.name} 内部已开启中断源 ({ie_labels}) 但 NVIC 未使能",
                        detail=(
                            f"{periph.name} 控制寄存器已置位中断使能位 ({ie_labels})，"
                            f"但关联的 NVIC 通道 [{irq_labels}] 均处于未使能状态 (NVIC_ISER=0)，中断永远不会触发！"
                        ),
                        suggestion="检查 MSP 初始化中是否漏调 HAL_NVIC_EnableIRQ(...)。",
                        evidence={
                            "enabled_interrupt_fields": list(self_status.enabled_interrupt_fields),
                            "nvic_irqs": [q.to_dict() for q in valid_irqs],
                        },
                    )
                )

    # ---- 阶段 4：全片已配置 GPIO 引脚复用快照与 I2C 开漏检查 -----------------
    gpio_pins: list[GpioPinSnapshot] = []
    if include_gpio_mux:
        gpio_pins = scan_configured_gpio_pins(probe, svd_device)
        _check_i2c_gpio_open_drain(periph.name, gpio_pins, findings)

    highest = _pick_highest_severity(findings)
    healthy = highest in ("ok", "info")
    summary = _build_summary(periph.name, highest, findings, clock_gate, self_status)

    return PeripheralDiagnosisReport(
        peripheral=periph.name,
        base_address=f"0x{periph.base_address:08X}",
        svd_device=svd_device.name,
        healthy=healthy,
        highest_severity=highest,
        summary=summary,
        clock_gate=clock_gate,
        nvic_irqs=nvic_irqs,
        self_status=self_status,
        configured_gpio_pins=gpio_pins,
        findings=findings,
    )


# ---------------------------------------------------------------------------
# 内部辅助逻辑
# ---------------------------------------------------------------------------


def _find_clock_enable_bit(
    svd_device: SvdDevice,
    peripheral_name: str,
) -> tuple[SvdPeripheral, SvdRegister, SvdField] | None:
    target = peripheral_name.strip().upper()
    clock_periphs = [
        p
        for p in svd_device.peripherals
        if p.name.upper() in _CLOCK_PERIPH_NAMES or p.group_name.upper() in _CLOCK_PERIPH_NAMES
    ]
    if not clock_periphs:
        return None

    # 候选位域名称：优先精确匹配 <PERIPH>EN（如 USART1EN / GPIOAEN / GPDMA1EN），再兼容共享时钟位（如 ADC12EN / ADCEN / PAEN）
    candidate_field_names = [f"{target}EN"]
    if target.startswith(("ADC", "DAC", "FDCAN")) and target[-1:].isdigit():
        base = re.sub(r"\d+$", "", target)
        if target in (f"{base}1", f"{base}2"):
            candidate_field_names.append(f"{base}12EN")
        candidate_field_names.append(f"{base}EN")
    m_gpio = _RE_GPIO_PORT.match(target)
    if m_gpio:
        # 兼容 GD32 RCU 命名（如 PAEN / PBEN）
        candidate_field_names.append(f"P{m_gpio.group(1).upper()}EN")

    for clk_p in clock_periphs:
        # 优先搜索标准运行态 *ENR* 寄存器（严格排除睡眠态 *LPENR* / *SMENR* 与复位寄存器 *RSTR*）
        enr_regs = [
            r
            for r in clk_p.registers
            if "ENR" in r.name.upper()
            and "LPENR" not in r.name.upper()
            and "SMENR" not in r.name.upper()
            and "RSTR" not in r.name.upper()
        ]
        # 兼容 GD32/AT32 的 AHBEN / APB1EN / APB2EN 命名
        fallback_en_regs = [
            r
            for r in clk_p.registers
            if r not in enr_regs
            and "EN" in r.name.upper()
            and "LPEN" not in r.name.upper()
            and "SMEN" not in r.name.upper()
            and "RST" not in r.name.upper()
        ]
        ordered_regs = enr_regs + fallback_en_regs

        for want_fname in candidate_field_names:
            for reg in ordered_regs:
                for fld in reg.fields:
                    if fld.name.upper() == want_fname:
                        return clk_p, reg, fld

    return None


def _strip_periph_prefix(periph_name: str, reg_name: str) -> str:
    up_p = periph_name.upper() + "_"
    if reg_name.upper().startswith(up_p):
        return reg_name[len(up_p):]
    return reg_name


def _try_find_reg(periph: SvdPeripheral, short_name: str) -> SvdRegister | None:
    target = short_name.upper()
    prefixed = f"{periph.name.upper()}_{target}"
    for r in periph.registers:
        ru = r.name.upper()
        if ru == target or ru == prefixed:
            return r
    return None


def _safe_read_opt_reg(
    probe: DebugProbe,
    periph: SvdPeripheral,
    reg: SvdRegister | None,
) -> int | None:
    if reg is None or reg.access == "write-only":
        return None
    try:
        return probe.read_register_sized(periph.reg_address(reg), reg.size)
    except ProbeError:
        return None


def _is_status_register(periph_name: str, reg_name: str) -> bool:
    short = _strip_periph_prefix(periph_name, reg_name).upper()
    if any(excl in short for excl in ("CR", "ICR", "IFCR", "IER", "DIER", "ENR", "RSTR", "BRR", "PRESC")):
        return False
    return short in ("SR", "ISR", "CSR", "ESR", "SSR") or "ISR" in short or "SR" in short


def _is_control_register(periph_name: str, reg_name: str) -> bool:
    short = _strip_periph_prefix(periph_name, reg_name).upper()
    if _is_status_register(periph_name, reg_name):
        return False
    if short in ("ICR", "IFCR", "RDR", "TDR", "DR", "TXDR", "RXDR", "ARR", "CCR1", "CCR2", "CCR3", "CCR4"):
        return False
    return short in ("CR", "CR1", "CR2", "CR3", "CFGR", "CCR") or short.endswith("_CR") or "CR" in short


def _is_irq_enable_field(field_name: str) -> bool:
    up = field_name.upper()
    if up in ("UE", "PE", "SPE", "TE", "RE", "DE", "CEN", "ADEN"):
        return False
    return (up.endswith("IE") and len(up) >= 3) or up in ("ITERREN", "ITEVTEN", "ITBUFEN", "CIE")


def _analyze_self_registers(
    periph: SvdPeripheral,
    snap: PeripheralSnapshot,
    findings: list[DiagnosticFinding],
) -> SelfStatusSummary:
    summary = SelfStatusSummary(
        registers_read=len(snap.registers),
        read_errors=dict(snap.errors),
    )

    # 1. 查找主使能位（仅在控制寄存器中查找，防止将 USART->ISR.PE 奇偶校验错误误认成 I2C->CR1.PE 外设使能）
    master_candidates: list[tuple[int, DecodedRegister, DecodedField]] = []
    extra_enables: dict[str, int] = {}

    for dec in snap.registers:
        is_ctrl = _is_control_register(periph.name, dec.register)
        is_stat = _is_status_register(periph.name, dec.register)

        # 收集偏离 reset_value 的寄存器摘要
        if dec.is_reset_value is False or (dec.reset_value is None and dec.raw_value != 0):
            active_f = [
                {
                    "name": f.name,
                    "bit_range": f.bit_range,
                    "value": f.value,
                    "hex": f.hex_value,
                    "meaning": f.meaning,
                }
                for f in dec.fields
                if f.value != 0
            ]
            summary.changed_registers.append(
                ChangedRegisterSummary(
                    register=dec.register,
                    address=dec.address,
                    raw_hex=dec.raw_hex,
                    reset_value=dec.reset_value,
                    active_fields=active_f,
                )
            )

        if is_ctrl:
            for f in dec.fields:
                f_up = f.name.upper()
                if f.width == 1 and f_up in _MASTER_ENABLE_FIELD_NAMES:
                    # 主控制寄存器 CR1 / CR 优先级高于通道寄存器 C0_CR
                    short_r = _strip_periph_prefix(periph.name, dec.register).upper()
                    prio = _MASTER_ENABLE_FIELD_NAMES.index(f_up) + (0 if short_r in ("CR1", "CR") else 10)
                    master_candidates.append((prio, dec, f))
                if f.width == 1 and f_up in ("TE", "RE"):
                    extra_enables[f_up] = f.value
                if f.width == 1 and _is_irq_enable_field(f_up) and f.value == 1:
                    summary.enabled_interrupt_fields.append(f"{dec.register}.{f.name}")
        elif not is_stat:
            # 独立中断使能寄存器（如 IER / DIER）
            short_r = _strip_periph_prefix(periph.name, dec.register).upper()
            if short_r in ("IER", "DIER", "INTEN"):
                for f in dec.fields:
                    if f.width == 1 and _is_irq_enable_field(f.name) and f.value == 1:
                        summary.enabled_interrupt_fields.append(f"{dec.register}.{f.name}")

        # 2. 扫描状态寄存器中的典型错误 / 忙标志位
        if is_stat:
            for f in dec.fields:
                f_up = f.name.upper()
                if f.value == 0 or f_up not in _STATUS_ERROR_FLAG_KB:
                    continue
                code, sev, title, detail, suggestion = _STATUS_ERROR_FLAG_KB[f_up]
                alert = StatusFlagAlert(
                    register=dec.register,
                    field=f.name,
                    bit_range=f.bit_range,
                    value=f.value,
                    severity=sev,
                    code=code,
                    title=f"{periph.name}->{dec.register}.{f.name} — {title}",
                    detail=detail,
                    suggestion=suggestion,
                )
                summary.active_error_or_busy_flags.append(alert)
                findings.append(
                    DiagnosticFinding(
                        severity=sev,
                        stage="self_status",
                        code=code,
                        title=alert.title,
                        detail=detail,
                        suggestion=suggestion,
                        evidence={
                            "peripheral": periph.name,
                            "register": dec.register,
                            "address": dec.address,
                            "field": f.name,
                            "raw_register_value": dec.raw_hex,
                        },
                    )
                )

    if master_candidates:
        # 若有任一通道已使能则优先选 enabled=1 的主位（适配多通道 DMA C0_CR / C1_CR），否则按控制寄存器优先级取第一个
        master_candidates.sort(key=lambda item: (0 if item[2].value != 0 else 1, item[0]))
        _, m_reg, m_fld = master_candidates[0]
        summary.master_enable = MasterEnableStatus(
            found=True,
            register=m_reg.register,
            field=m_fld.name,
            value=m_fld.value,
            enabled=bool(m_fld.value != 0),
            extra_enables=extra_enables,
        )
        if not summary.master_enable.enabled:
            findings.append(
                DiagnosticFinding(
                    severity="warning",
                    stage="self_status",
                    code="PERIPHERAL_DISABLED",
                    title=f"{periph.name} 主使能位未置位 ({m_reg.register}.{m_fld.name} = 0)",
                    detail=(
                        f"{periph.name} 外设时钟已开启，但控制寄存器主使能位 {m_reg.register}.{m_fld.name} = 0，"
                        f"外设硬件状态机尚未启动。"
                    ),
                    suggestion=f"检查初始化函数（如 HAL_*_Init / __HAL_*_ENABLE）是否已执行并成功置位 {m_fld.name}=1。",
                    evidence=summary.master_enable.to_dict(),
                )
            )
        elif extra_enables and set(extra_enables.keys()) >= {"TE", "RE"} and extra_enables["TE"] == 0 and extra_enables["RE"] == 0:
            findings.append(
                DiagnosticFinding(
                    severity="warning",
                    stage="self_status",
                    code="TRANSCEIVER_DIRECTIONS_DISABLED",
                    title=f"{periph.name} 已开启 {m_fld.name}=1，但发送(TE)与接收(RE)使能均为 0",
                    detail=f"{periph.name}->{m_reg.register} 中 TE=0 且 RE=0，收发通道均处于关闭态。",
                    suggestion="检查 UART/USART Mode 是否正确配置为 UART_MODE_TX_RX / UART_MODE_TX / UART_MODE_RX。",
                    evidence=summary.master_enable.to_dict(),
                )
            )

    return summary


def _check_i2c_gpio_open_drain(
    peripheral_name: str,
    gpio_pins: list[GpioPinSnapshot],
    findings: list[DiagnosticFinding],
) -> None:
    if not peripheral_name.upper().startswith(("I2C", "FMPI2C")):
        return

    # STM32 I2C 引脚绝大多数位于 AF4（部分型号也有 AF1/AF6/AF8/AF9）；优先检查明确含 OTYPER 寄存器的 Alternate Push-Pull 引脚
    i2c_af_candidates = [p for p in gpio_pins if p.mode == "Alternate" and p.af == 4]
    if not i2c_af_candidates:
        i2c_af_candidates = [p for p in gpio_pins if p.mode == "Alternate"]

    offending = [
        p for p in i2c_af_candidates if p.otyper_in_svd and p.output_type == "Push-Pull"
    ]
    for pin in offending:
        warn_msg = f"{pin.pin} 处于 Alternate({pin.af_label or '?'}) Push-Pull 推挽模式，I2C 总线要求配置为 Open-Drain 开漏输出！"
        if warn_msg not in pin.warnings:
            pin.warnings.append(warn_msg)
        findings.append(
            DiagnosticFinding(
                severity="warning",
                stage="gpio_mux",
                code="I2C_PIN_PUSH_PULL",
                title=f"I2C 疑似关联引脚 {pin.pin} 配成了 Push-Pull 而非 Open-Drain",
                detail=(
                    f"检测到 {pin.pin} 配置为 {pin.summary}（OTYPER=0 推挽输出）。"
                    f"I2C SCL/SDA 协议依赖线与（Wired-AND），配置为 Push-Pull 会导致从机无法拉低 SDA 回应 ACK 并引发电平冲突！"
                ),
                suggestion=f"请将 {pin.pin} 的 GPIO Mode 改为 GPIO_MODE_AF_OD（Open-Drain），并开启上拉电阻。",
                evidence=pin.to_dict(),
            )
        )


def _pick_highest_severity(findings: list[DiagnosticFinding]) -> str:
    rank = {"critical": 4, "error": 3, "warning": 2, "info": 1}
    if not findings:
        return "ok"
    best = max(findings, key=lambda f: rank.get(f.severity, 0))
    return best.severity


def _build_summary(
    periph_name: str,
    highest: str,
    findings: list[DiagnosticFinding],
    clock_gate: ClockGateStatus,
    self_status: SelfStatusSummary,
) -> str:
    if not findings:
        clk_note = "时钟已开启" if clock_gate.enabled else "时钟位未在 SVD 声明"
        me_note = (
            f"，{self_status.master_enable.register}.{self_status.master_enable.field}=1"
            if self_status.master_enable.enabled
            else ""
        )
        return f"{periph_name} 体检正常（{clk_note}{me_note}，无挂起错误标志或 NVIC 异常）"
    top = findings[0]
    return f"{periph_name} 发现 {len(findings)} 项异常（最高级别 {highest}）：{top.title}"
