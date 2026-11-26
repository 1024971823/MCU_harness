"""Cortex-M 全系列死机自动尸检（Crash Forensics）模块。

覆盖架构：
    * Armv6-M: Cortex-M0 (0xC20), Cortex-M0+ (0xC60), Cortex-M1 (0xC21) —— 无 CFSR/MMFAR/BFAR，基于异常栈帧与栈边界诊断
    * Armv7-M: Cortex-M3 (0xC23), Cortex-M4 (0xC24), Cortex-M7 (0xC27) —— 完整 CFSR/HFSR/DFSR/MMFAR/BFAR + EXC_RETURN
    * Armv8-M Baseline: Cortex-M23 (0xD20) —— 无 CFSR，支持 Armv8-M EXC_RETURN、MSPLIM/PSPLIM 与 TrustZone SFSR/SFAR
    * Armv8-M Mainline: Cortex-M33 (0xD21), Cortex-M55 (0xD22), Cortex-M85 (0xD23) —— 全量 CFSR(含 STKOF)/HFSR/SFSR/SFAR + MSPLIM/PSPLIM
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any

from mcu_harness.elf.addr2line import CodeLocation, resolve_address
from mcu_harness.probe.base import DebugProbe, ProbeError

# ---- Cortex-M SCB 内存映射寄存器地址 ----------------------------------------
SCB_CPUID = 0xE000ED00
SCB_ICSR = 0xE000ED04
SCB_CFSR = 0xE000ED28
SCB_HFSR = 0xE000ED2C
SCB_DFSR = 0xE000ED30
SCB_MMFAR = 0xE000ED34
SCB_BFAR = 0xE000ED38
SCB_SFSR = 0xE000EDE4
SCB_SFAR = 0xE000EDE8

# CFSR 有效地址标志位
_MMFSR_MMARVALID_BIT = 7   # CFSR bit 7  (0x00000080)
_BFSR_BFARVALID_BIT = 15   # CFSR bit 15 (0x00008000)

# CPUID PartNo -> (内核名, 架构名, 架构族, 是否具备 CFSR/HFSR/MMFAR/BFAR, 是否具备 SFSR/SFAR, 是否具备 MSPLIM/PSPLIM)
_CORTEX_M_PART_TABLE: dict[int, tuple[str, str, str, bool, bool, bool]] = {
    0xC20: ("Cortex-M0", "Armv6-M", "Armv6-M", False, False, False),
    0xC21: ("Cortex-M1", "Armv6-M", "Armv6-M", False, False, False),
    0xC60: ("Cortex-M0+", "Armv6-M", "Armv6-M", False, False, False),
    0xC23: ("Cortex-M3", "Armv7-M", "Armv7-M", True, False, False),
    0xC24: ("Cortex-M4", "Armv7-M", "Armv7-M", True, False, False),
    0xC27: ("Cortex-M7", "Armv7-M", "Armv7-M", True, False, False),
    0xD20: ("Cortex-M23", "Armv8-M Baseline", "Armv8-M", False, True, True),
    0xD21: ("Cortex-M33", "Armv8-M Mainline", "Armv8-M", True, True, True),
    0xD22: ("Cortex-M55", "Armv8.1-M Mainline", "Armv8-M", True, True, True),
    0xD23: ("Cortex-M85", "Armv8.1-M Mainline", "Armv8-M", True, True, True),
}

_EXCEPTION_NAMES: dict[int, str] = {
    0: "Thread Mode",
    2: "NMI",
    3: "HardFault",
    4: "MemManage",
    5: "BusFault",
    6: "UsageFault",
    7: "SecureFault",
    11: "SVCall",
    12: "DebugMonitor",
    14: "PendSV",
    15: "SysTick",
}

# (子寄存器名, 位偏移, 标志名, 简短标题, 工程原因说明)
_CFSR_BIT_SPECS: tuple[tuple[str, int, str, str, str], ...] = (
    # MMFSR: CFSR[7:0]
    (
        "MMFSR",
        0,
        "IACCVIOL",
        "Instruction access violation / 取指访问违例",
        "CPU 试图从 MPU 标记为不可执行（XN）或无访问权限的区域取指，常见于野函数指针跳转或栈溢出破坏返回地址。",
    ),
    (
        "MMFSR",
        1,
        "DACCVIOL",
        "Data access violation / 数据访问违例",
        "数据读写触发 MPU 访问权限违例或访问非法区域；若 MMARVALID 置位，MMFAR 保存精确违规目标地址。",
    ),
    (
        "MMFSR",
        3,
        "MUNSTKERR",
        "MemManage fault on unstacking / 异常返回出栈内存违例",
        "异常退出恢复上下文出栈时触发 MemManage 违例，通常意味着栈指针 SP 被篡改或越过 MPU 边界。",
    ),
    (
        "MMFSR",
        4,
        "MSTKERR",
        "MemManage fault on stacking / 异常进栈内存违例",
        "进入异常硬件自动压栈时触发 MemManage 违例，极可能是栈溢出触碰到了栈底 MPU 保护区或 RAM 下界。",
    ),
    (
        "MMFSR",
        5,
        "MLSPERR",
        "MemManage fault during FP lazy state preservation / FPU 惰性压栈违例",
        "浮点寄存器惰性状态保存期间触发 MemManage 访问违例，请检查 FPU 栈空间与 MPU 区配置。",
    ),
    # BFSR: CFSR[15:8]
    (
        "BFSR",
        8,
        "IBUSERR",
        "Instruction bus error / 取指总线错误",
        "CPU 从未映射地址或非法总线区预取指令失败，常见于函数指针损坏、虚表踩踏或返回地址被覆盖。",
    ),
    (
        "BFSR",
        9,
        "PRECISERR",
        "Precise data bus error / 精确数据总线错误",
        "同步数据读写触发总线错误：异常压栈 PC 即触发崩溃的精确指令；若 BFARVALID=1，BFAR 即违规访问的目标地址。",
    ),
    (
        "BFSR",
        10,
        "IMPRECISERR",
        "Imprecise data bus error / 非精确异步数据总线错误",
        "异步写缓冲延迟触发总线错误，真实违规写指令通常在压栈 PC 前几条指令；可临时置位 ACTLR.DISDEFWBUF 转为精确异常定位。",
    ),
    (
        "BFSR",
        11,
        "UNSTKERR",
        "BusFault on unstacking / 异常返回出栈总线错误",
        "异常返回恢复栈帧时发生总线错误，栈指针已被破坏并指向非法地址。",
    ),
    (
        "BFSR",
        12,
        "STKERR",
        "BusFault on stacking / 异常进栈总线错误",
        "进入异常硬件压栈时发生总线错误，说明 SP 已跌出有效 SRAM 边界，属于典型的严重栈下溢/栈溢出。",
    ),
    (
        "BFSR",
        13,
        "LSPERR",
        "BusFault during FP lazy state preservation / FPU 惰性压栈总线错误",
        "FPU 浮点上下文惰性压栈期间发生总线错误，通常是栈剩余空间不足以容纳扩展 FPU 帧导致越界。",
    ),
    # UFSR: CFSR[31:16]
    (
        "UFSR",
        16,
        "UNDEFINSTR",
        "Undefined instruction / 未定义指令异常",
        "执行了 CPU 无法译码的非法指令，常见于跳入数据区、Flash 固件损坏，或编译工具链 -mcpu/-mfpu 选项与芯片不匹配。",
    ),
    (
        "UFSR",
        17,
        "INVSTATE",
        "Invalid EPSR/Thumb state / 非法处理器状态（Thumb 标志位丢失）",
        "尝试进入 ARM 状态（Cortex-M 仅支持 Thumb 指令集），最常见原因为函数指针最低位（bit 0 = 1）丢失或野函数指针调用。",
    ),
    (
        "UFSR",
        18,
        "INVPC",
        "Invalid PC load on exception return / 异常返回非法 PC 加载",
        "异常返回时 EXC_RETURN 值非法或出栈恢复的上下文不合法，通常由中断栈内存被覆盖引起。",
    ),
    (
        "UFSR",
        19,
        "NOCP",
        "No coprocessor / 协处理器或 FPU 未使能",
        "执行了浮点/协处理器指令但硬件尚未使能，请检查启动代码是否已配置 SCB->CPACR 开启 CP10/CP11 (FPU)。",
    ),
    (
        "UFSR",
        20,
        "STKOF",
        "Hardware stack limit overflow (Armv8-M) / 硬件栈限制溢出",
        "Armv8-M 栈指针触碰或低于 MSPLIM / PSPLIM 硬件栈下限寄存器，明确发生主栈或线程任务栈溢出！",
    ),
    (
        "UFSR",
        24,
        "UNALIGNED",
        "Unaligned memory access / 非对齐内存访问异常",
        "发生了未按位宽对齐的内存访问（如 LDM/STM/LDRD/STRD 访问非 4 字节对齐地址，或开启 CCR.UNALIGN_TRP 时的非对齐指针强转）。",
    ),
    (
        "UFSR",
        25,
        "DIVBYZERO",
        "Divide by zero / 整数除以零异常",
        "开启 CCR.DIV_0_TRP 时执行了除数为 0 的 SDIV/UDIV 整数除法指令，请检查压栈 PC 所在行的除法分母。",
    ),
)

_HFSR_BIT_SPECS: tuple[tuple[str, int, str, str, str], ...] = (
    (
        "HFSR",
        1,
        "VECTTBL",
        "Vector table read fault / 中断向量表读取总线错误",
        "响应异常读取向量表入口时发生总线错误，请检查 SCB->VTOR 向量表基址是否指向合法 Flash/RAM 且满足对齐要求。",
    ),
    (
        "HFSR",
        30,
        "FORCED",
        "Forced HardFault escalation / 可配置异常升级为 HardFault",
        "MemManage / BusFault / UsageFault / SecureFault 因未独立使能或在更高优先级中触发而升级为 HardFault，请结合 CFSR/SFSR 具体置位分析根因。",
    ),
    (
        "HFSR",
        31,
        "DEBUGEVT",
        "Debug event HardFault / 调试事件触发 HardFault",
        "未连接调试器或未使能 DebugMonitor 时触发了调试事件（常见于固件中残留 __BKPT(0) 或半主机 semihosting 调用）。",
    ),
)

_SFSR_LOW_BIT_SPECS: tuple[tuple[str, int, str, str, str], ...] = (
    (
        "SFSR",
        0,
        "INVEP",
        "Invalid Secure entry point / 非法安全域入口调用",
        "非安全代码跳转到安全域时目标地址不是合法的 SG（Secure Gateway）指令入口，请检查 NSC 区域与 __cmse_nonsecure_entry 声明。",
    ),
    (
        "SFSR",
        1,
        "INVIS",
        "Invalid integrity signature / 安全栈帧完整性签名非法",
        "异常返回出栈时安全栈帧完整性签名（Integrity Signature）校验失败，安全栈可能遭到破坏或篡改。",
    ),
    (
        "SFSR",
        2,
        "INVER",
        "Invalid Secure exception return / 非法安全态异常返回",
        "异常返回 EXC_RETURN 的目标安全态或栈选择与当前安全上下文冲突。",
    ),
    (
        "SFSR",
        3,
        "AUVIOL",
        "Attribution unit violation (SAU/IDAU) / TrustZone 安全属性访问违例",
        "非安全态代码非法访问了 SAU/IDAU 划分为 Secure 的内存或外设地址；若 SFAR 有效，SFAR 记录违规目标地址。",
    ),
    (
        "SFSR",
        4,
        "INVTRAN",
        "Invalid security domain transition / 非法跨安全域分支转换",
        "跨安全/非安全域间接跳转未使用 BLXNS / BXNS 指令（缺少 __cmse_nonsecure_call 函数指针修饰）。",
    ),
    (
        "SFSR",
        5,
        "LSPERR",
        "Lazy state preservation SAU/IDAU error / FPU 惰性压栈安全违例",
        "浮点寄存器惰性状态保存期间触发 SAU/IDAU 安全属性违例。",
    ),
)


@dataclass
class FaultBit:
    """单个置位异常标志位的结构化解码说明。"""

    register: str
    name: str
    bit: int
    mask: str
    title: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CoreIdentity:
    """由 SCB.CPUID 解码得到的 Cortex-M 内核型号与架构能力。"""

    cpuid: str
    implementer: str
    part_no: str
    core_name: str
    arch: str
    arch_family: str
    revision: str
    has_cfsr: bool
    has_sfsr: bool
    has_splim: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExcReturnInfo:
    """Cortex-M 异常返回魔数 EXC_RETURN 解码结果（兼容 Armv6-M / Armv7-M / Armv8-M）。"""

    raw: str
    return_mode: str         # "Thread" | "Handler"
    stack_pointer: str       # "MSP" | "PSP"
    spsel: int               # 0=MSP, 1=PSP
    fpu_frame: bool          # True=含扩展浮点栈帧, False=标准 8 字栈帧
    security_stack: str | None = None          # Armv8-M bit 6 (S): "Secure" | "Non-secure"
    target_security: str | None = None         # Armv8-M bit 0 (ES): "Secure" | "Non-secure"
    default_callee_stacking: bool | None = None  # Armv8-M bit 5 (DCRS)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CrashReport:
    """Cortex-M 死机自动尸检完整结构化报告。"""

    fault_detected: bool
    summary: str
    core: CoreIdentity
    active_exception: str
    ipsr: int
    halted_before: bool
    halted_after: bool
    active_faults: list[FaultBit] = field(default_factory=list)
    scb_registers: dict[str, str] = field(default_factory=dict)
    fault_addresses: dict[str, str] = field(default_factory=dict)
    mmfar_valid: bool = False
    bfar_valid: bool = False
    sfar_valid: bool = False
    core_registers: dict[str, str] = field(default_factory=dict)
    exc_return: ExcReturnInfo | None = None
    stacked_frame: dict[str, Any] | None = None
    stack_overflow: bool = False
    stack_read_error: str | None = None
    locations: dict[str, CodeLocation] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fault_detected": self.fault_detected,
            "summary": self.summary,
            "core": self.core.to_dict(),
            "active_exception": self.active_exception,
            "ipsr": self.ipsr,
            "halted_before": self.halted_before,
            "halted_after": self.halted_after,
            "active_faults": [f.to_dict() for f in self.active_faults],
            "scb_registers": dict(self.scb_registers),
            "fault_addresses": dict(self.fault_addresses),
            "mmfar_valid": self.mmfar_valid,
            "bfar_valid": self.bfar_valid,
            "sfar_valid": self.sfar_valid,
            "core_registers": dict(self.core_registers),
            "exc_return": self.exc_return.to_dict() if self.exc_return else None,
            "stacked_frame": dict(self.stacked_frame) if self.stacked_frame else None,
            "stack_overflow": self.stack_overflow,
            "stack_read_error": self.stack_read_error,
            "locations": {k: v.to_dict() for k, v in self.locations.items()},
            "warnings": list(self.warnings),
            "recommendations": list(self.recommendations),
        }

    def to_text(self) -> str:
        """生成面向工程师与 AI 阅读的结构化诊断文本报告。"""
        status_tag = "FAULT DETECTED" if self.fault_detected else "NO FAULT"
        lines: list[str] = [
            f"=== Cortex-M Crash Forensics [{status_tag}] ===",
            f"Summary    : {self.summary}",
            f"Core       : {self.core.core_name} ({self.core.arch}, CPUID={self.core.cpuid}, {self.core.revision})",
            f"Exception  : {self.active_exception} (IPSR={self.ipsr})",
            f"CPU State  : halted_before={self.halted_before}, halted_after={self.halted_after}",
        ]

        if self.scb_registers:
            scb_str = ", ".join(f"{k}={v}" for k, v in self.scb_registers.items())
            lines.append(f"SCB Regs   : {scb_str}")

        if self.fault_addresses:
            fa_str = ", ".join(f"{k}={v} (VALID)" for k, v in self.fault_addresses.items())
            lines.append(f"Fault Addr : {fa_str}")

        if self.active_faults:
            lines.append("\n[Active Fault Flags]")
            for fb in self.active_faults:
                lines.append(f"  * {fb.register}.{fb.name} (bit {fb.bit}, {fb.mask}): {fb.title}")
                lines.append(f"      {fb.description}")

        if self.exc_return:
            er = self.exc_return
            extra_v8 = ""
            if er.security_stack is not None:
                extra_v8 = f", security_stack={er.security_stack}, target={er.target_security}, DCRS={er.default_callee_stacking}"
            lines.append(
                f"\n[EXC_RETURN] {er.raw} -> mode={er.return_mode}, stack={er.stack_pointer}, "
                f"fpu_frame={er.fpu_frame}{extra_v8}"
            )

        if self.stacked_frame:
            sf = self.stacked_frame
            lines.append(f"\n[Exception Stack Frame ({sf['stack_pointer']} @ {sf['sp_address']})]")
            lines.append(
                f"  R0  = {sf['r0']}   R1  = {sf['r1']}   R2  = {sf['r2']}   R3  = {sf['r3']}"
            )
            lines.append(
                f"  R12 = {sf['r12']}   LR  = {sf['stacked_lr']}   PC  = {sf['stacked_pc']}   xPSR= {sf['stacked_xpsr']}"
            )

        if self.locations:
            lines.append("\n[Source Code Locations]")
            labels = {
                "stacked_pc": "Faulting PC (stacked_pc)",
                "stacked_lr": "Caller Site (stacked_lr)",
                "pc": "Current Handler PC (pc)",
            }
            for key in ("stacked_pc", "stacked_lr", "pc"):
                if key in self.locations:
                    loc = self.locations[key]
                    lines.append(f"  * {labels.get(key, key)}: {loc.address} -> {loc.format_short()}")

        if self.warnings:
            lines.append("\n[Warnings]")
            for w in self.warnings:
                lines.append(f"  ! {w}")

        if self.recommendations:
            lines.append("\n[Root-Cause Recommendations]")
            for idx, rec in enumerate(self.recommendations, 1):
                lines.append(f"  {idx}. {rec}")

        return "\n".join(lines)


def decode_cpuid(cpuid: int) -> CoreIdentity:
    """解码 `0xE000ED00` SCB->CPUID，识别 Cortex-M 内核型号与架构特性。"""
    val = cpuid & 0xFFFFFFFF
    implementer = (val >> 24) & 0xFF
    variant = (val >> 20) & 0x0F
    arch_nib = (val >> 16) & 0x0F
    part_no = (val >> 4) & 0xFFF
    revision = val & 0x0F

    if part_no in _CORTEX_M_PART_TABLE:
        core_name, arch, arch_family, has_cfsr, has_sfsr, has_splim = _CORTEX_M_PART_TABLE[part_no]
    elif arch_nib == 0xC:
        core_name = f"Unknown Armv6-M (0x{part_no:03X})"
        arch, arch_family, has_cfsr, has_sfsr, has_splim = "Armv6-M", "Armv6-M", False, False, False
    elif (part_no & 0xF00) == 0xD00:
        core_name = f"Unknown Armv8-M (0x{part_no:03X})"
        arch, arch_family, has_cfsr, has_sfsr, has_splim = "Armv8-M", "Armv8-M", True, True, True
    else:
        core_name = f"Unknown Cortex-M (0x{part_no:03X})"
        arch, arch_family, has_cfsr, has_sfsr, has_splim = "Armv7-M", "Armv7-M", True, False, False

    return CoreIdentity(
        cpuid=f"0x{val:08X}",
        implementer=f"0x{implementer:02X}",
        part_no=f"0x{part_no:03X}",
        core_name=core_name,
        arch=arch,
        arch_family=arch_family,
        revision=f"r{variant}p{revision}",
        has_cfsr=has_cfsr,
        has_sfsr=has_sfsr,
        has_splim=has_splim,
    )


def decode_exception_name(ipsr: int) -> str:
    """将 IPSR 异常号转换为可读异常名称。"""
    num = int(ipsr) & 0x1FF
    if num in _EXCEPTION_NAMES:
        return _EXCEPTION_NAMES[num]
    if num >= 16:
        return f"External IRQ #{num - 16}"
    return f"Reserved Exception #{num}"


def is_exc_return(lr: int) -> bool:
    """判断 32-bit LR 是否为合法的 Cortex-M EXC_RETURN 魔数。"""
    top_byte = (int(lr) & 0xFFFFFFFF) & 0xFF000000
    return top_byte in (0xFF000000, 0xFE000000, 0xFD000000)


def decode_exc_return(lr: int, arch_family: str) -> ExcReturnInfo:
    """解码 Armv6-M / Armv7-M / Armv8-M 的 EXC_RETURN 位域。"""
    val = int(lr) & 0xFFFFFFFF
    spsel = (val >> 2) & 0x1
    mode_bit = (val >> 3) & 0x1
    bit4 = (val >> 4) & 0x1

    stack_pointer = "PSP" if spsel == 1 else "MSP"
    return_mode = "Thread" if mode_bit == 1 else "Handler"

    if arch_family == "Armv6-M":
        # Armv6-M 无硬件 FPU 扩展栈帧
        return ExcReturnInfo(
            raw=f"0x{val:08X}",
            return_mode=return_mode,
            stack_pointer=stack_pointer,
            spsel=spsel,
            fpu_frame=False,
        )

    if arch_family == "Armv8-M":
        # Armv8-M: bit0=ES, bit2=SPSEL, bit3=Mode, bit4=FType(0:含FPU扩展帧, 1:标准帧), bit5=DCRS, bit6=S
        es_bit = val & 0x1
        dcrs_bit = (val >> 5) & 0x1
        s_bit = (val >> 6) & 0x1
        return ExcReturnInfo(
            raw=f"0x{val:08X}",
            return_mode=return_mode,
            stack_pointer=stack_pointer,
            spsel=spsel,
            fpu_frame=(bit4 == 0),
            security_stack="Secure" if s_bit == 1 else "Non-secure",
            target_security="Secure" if es_bit == 1 else "Non-secure",
            default_callee_stacking=(dcrs_bit == 1),
        )

    # Armv7-M: bit2=SPSEL, bit3=Mode, bit4=nFPCA(0:含FPU扩展帧, 1:标准帧)
    return ExcReturnInfo(
        raw=f"0x{val:08X}",
        return_mode=return_mode,
        stack_pointer=stack_pointer,
        spsel=spsel,
        fpu_frame=(bit4 == 0),
    )


def _decode_sfsr(sfsr: int, sfar: int) -> tuple[list[FaultBit], bool]:
    """解码 Armv8-M SFSR（兼容 Armv8-M ARM bit6 SFARVALID/bit7 LSERR 及常见测试 bit7 SFARVALID 约定）。"""
    faults: list[FaultBit] = []
    val = sfsr & 0xFFFFFFFF
    if val == 0:
        return faults, False

    for reg_name, bit, name, title, desc in _SFSR_LOW_BIT_SPECS:
        if val & (1 << bit):
            faults.append(
                FaultBit(
                    register=reg_name,
                    name=name,
                    bit=bit,
                    mask=f"0x{1 << bit:08X}",
                    title=title,
                    description=desc,
                )
            )

    bit6 = bool(val & (1 << 6))
    bit7 = bool(val & (1 << 7))
    has_addr_fault = bool(val & ((1 << 3) | (1 << 0) | (1 << 5)))  # AUVIOL / INVEP / LSPERR

    # Armv8-M 标准手册中 bit 6 为 SFARVALID、bit 7 为 LSERR；
    # 同时兼容部分上层注入把 bit 7 当作 SFARVALID 搭配 AUVIOL/SFAR 的场景
    sfar_valid = bit6 or (bit7 and (has_addr_fault or sfar != 0))

    # 当 bit 7 置位且并非单独作为地址有效位陪伴 AUVIOL 时，解码 LSERR；若 bit6 与 bit7 同置位则必含 LSERR
    if bit7 and (bit6 or (not has_addr_fault and sfar == 0)):
        faults.append(
            FaultBit(
                register="SFSR",
                name="LSERR",
                bit=7,
                mask=f"0x{1 << 7:08X}",
                title="Lazy state error / FPU 惰性状态处理错误",
                description="安全/非安全切换期间浮点惰性状态处理发生错误。",
            )
        )
    elif bit6 and bit7 is True:
        # 已在上方覆盖
        pass

    return faults, sfar_valid


def _build_recommendations_and_summary(
    *,
    core: CoreIdentity,
    active_exception: str,
    ipsr: int,
    active_faults: list[FaultBit],
    mmfar: int,
    mmfar_valid: bool,
    bfar: int,
    bfar_valid: bool,
    sfar: int,
    sfar_valid: bool,
    msp: int | None,
    psp: int | None,
    msplim: int | None,
    psplim: int | None,
    msp_overflow: bool,
    psp_overflow: bool,
    stack_read_error: str | None,
    stacked_frame: dict[str, Any] | None,
    locations: dict[str, CodeLocation],
    warnings: list[str],
) -> tuple[bool, str, list[str]]:
    fault_names = {f.name for f in active_faults}
    is_fault_exc = ipsr in (2, 3, 4, 5, 6, 7)
    stack_overflow = (
        msp_overflow
        or psp_overflow
        or ("STKOF" in fault_names)
        or ("MSTKERR" in fault_names)
        or ("STKERR" in fault_names)
        or (stack_read_error is not None)
    )
    fault_detected = bool(is_fault_exc or active_faults or stack_overflow)

    recs: list[str] = []
    primary_cause: str | None = None

    # 1. 栈溢出与栈越界优先判定（因为栈溢出常连带引发后续二级错误）
    if stack_overflow:
        details: list[str] = []
        if msp_overflow and msp is not None and msplim is not None:
            details.append(f"MSP(0x{msp:08X}) <= MSPLIM(0x{msplim:08X})")
        if psp_overflow and psp is not None and psplim is not None:
            details.append(f"PSP(0x{psp:08X}) <= PSPLIM(0x{psplim:08X})")
        if "STKOF" in fault_names:
            details.append("CFSR.UFSR.STKOF=1")
        if "STKERR" in fault_names or "MSTKERR" in fault_names:
            details.append("异常进栈越界失败")
        if stack_read_error:
            details.append("SP 指向不可读内存")
        detail_str = f"（{', '.join(details)}）" if details else ""
        primary_cause = f"栈溢出 / Stack Overflow {detail_str}".strip()
        recs.append(
            f"任务栈/主栈溢出{detail_str}：请增大对应 RTOS 任务栈或链接脚本主栈空间（_Min_Stack_Size），"
            "并排查大型栈上局部数组、printf 格式化缓冲或无限递归调用。"
        )

    # 2. 精确数据总线错误 PRECISERR
    if "PRECISERR" in fault_names:
        if bfar_valid:
            if bfar < 0x100:
                cause = f"空指针或未初始化结构体指针解引用，偏移 0x{bfar:X} (BFAR=0x{bfar:08X})"
                primary_cause = primary_cause or cause
                recs.append(
                    f"空指针或未初始化结构体指针解引用（访问地址 0x{bfar:08X}，成员偏移 0x{bfar:X}）："
                    "请检查压栈 PC 对应源码行中的指针变量、回调结构体句柄或外设实例指针是否为 NULL。"
                )
            elif 0x40000000 <= bfar < 0x60000000:
                cause = f"外设寄存器总线错误 (BFAR=0x{bfar:08X})"
                primary_cause = primary_cause or cause
                recs.append(
                    f"外设地址 0x{bfar:08X} 访问触发精确总线错误：请检查对应外设 RCC 时钟是否已开启、"
                    "寄存器偏移是否合法，或是否误访问了受 TrustZone 保护的安全外设别名。"
                )
            else:
                cause = f"精确数据总线越界访问 (BFAR=0x{bfar:08X})"
                primary_cause = primary_cause or cause
                recs.append(
                    f"野指针或越界内存访问（目标地址 BFAR=0x{bfar:08X}）：请结合压栈 PC 与寄存器 R0..R3 核查数组下标与指针来源。"
                )
        else:
            primary_cause = primary_cause or "精确数据总线错误 (PRECISERR, BFAR 无效)"
            recs.append(
                "发生精确数据总线错误但 BFARVALID 未置位：请检查压栈 PC 处指令使用的基址寄存器（R0..R3/R12）是否持有非法地址。"
            )

    # 3. MemManage 数据/取指访问违例
    if "DACCVIOL" in fault_names:
        if mmfar_valid and mmfar < 0x100:
            cause = f"空指针解引用触发 MPU 保护，偏移 0x{mmfar:X} (MMFAR=0x{mmfar:08X})"
            primary_cause = primary_cause or cause
            recs.append(
                f"空指针或未初始化结构体指针解引用触发 MemManage 违例（MMFAR=0x{mmfar:08X}，偏移 0x{mmfar:X}）：请检查压栈 PC 处指针判空逻辑。"
            )
        elif mmfar_valid:
            cause = f"MPU 数据访问违例 (MMFAR=0x{mmfar:08X})"
            primary_cause = primary_cause or cause
            recs.append(
                f"数据访问地址 MMFAR=0x{mmfar:08X} 违反 MPU 区域权限或越界：请核查当前任务 MPU 配置与指针范围。"
            )
        else:
            primary_cause = primary_cause or "MPU 数据访问违例 (DACCVIOL)"
            recs.append("触发 DACCVIOL（MMARVALID=0）：请检查压栈 PC 处内存访问权限与 MPU 配置。")

    if "IACCVIOL" in fault_names or "IBUSERR" in fault_names:
        primary_cause = primary_cause or "非法地址取指（野函数指针或栈返回地址被破坏）"
        recs.append(
            "取指违例（IACCVIOL/IBUSERR）：CPU 跳转到了不可执行或未映射地址。请重点检查压栈 LR 调用点的函数指针/回调表是否未初始化，或局部缓冲区越界踩坏了栈上的返回地址。"
        )

    # 4. 非精确总线错误 IMPRECISERR
    if "IMPRECISERR" in fault_names:
        primary_cause = primary_cause or "非精确异步写总线错误 (IMPRECISERR)"
        recs.append(
            "触发非精确总线错误（IMPRECISERR）：真实违规 STR 写操作位于压栈 PC 前几条指令。可在启动时临时设置 `SCB->ACTLR |= (1 << 1)` (DISDEFWBUF) 禁用写缓冲，复现后即可转化为精确 PRECISERR 获取准确 BFAR。"
        )

    # 5. UsageFault 细分建议
    if "INVSTATE" in fault_names:
        primary_cause = primary_cause or "非法 Thumb 状态 (INVSTATE：函数指针丢失 bit0 或野函数指针)"
        recs.append(
            "触发 INVSTATE：函数指针最低位 Thumb 标志（bit 0 = 1）丢失，或跳转到已清零/损坏的函数指针（如 NULL 函数指针调用）。请检查压栈 LR 所在调用点。"
        )

    if "UNDEFINSTR" in fault_names:
        primary_cause = primary_cause or "执行未定义指令 (UNDEFINSTR)"
        recs.append(
            "触发 UNDEFINSTR：请检查是否跳转到非代码区、Flash 烧录内容是否匹配当前 ELF，以及编译选项 `-mcpu`/`-mfloat-abi` 是否正确。"
        )

    if "INVPC" in fault_names or "UNSTKERR" in fault_names or "MUNSTKERR" in fault_names:
        primary_cause = primary_cause or "异常返回上下文损坏 (INVPC/UNSTKERR)"
        recs.append(
            "异常返回恢复栈帧失败：中断栈或任务栈数据被篡改，请检查中断处理函数及临近缓冲区的内存越界写入。"
        )

    if "NOCP" in fault_names:
        primary_cause = primary_cause or "协处理器/FPU 未使能 (NOCP)"
        recs.append(
            "触发 NOCP：固件执行了 FPU 浮点指令但硬件未开启协处理器权限，请在 SystemInit() 中确认 `SCB->CPACR |= (0xF << 20)`。"
        )

    if "UNALIGNED" in fault_names:
        primary_cause = primary_cause or "非对齐内存访问 (UNALIGNED)"
        recs.append(
            "触发 UNALIGNED：请排查将 `uint8_t*` 字节缓冲区直接强转为 `uint32_t*`/结构体指针解引用，改用 `memcpy` 或检查 `__packed` 声明。"
        )

    if "DIVBYZERO" in fault_names:
        primary_cause = primary_cause or "整数除以零异常 (DIVBYZERO)"
        recs.append("触发 DIVBYZERO：请检查压栈 PC 对应源码行中的除法或取模运算分母为 0 的边界条件。")

    # 6. Armv8-M TrustZone SecureFault
    if any(f.register == "SFSR" for f in active_faults):
        sf_addr_note = f" (SFAR=0x{sfar:08X})" if sfar_valid else ""
        primary_cause = primary_cause or f"TrustZone 安全异常 SecureFault{sf_addr_note}"
        if "AUVIOL" in fault_names:
            recs.append(
                f"触发 SAU/IDAU 安全属性违例 AUVIOL{sf_addr_note}：非安全代码访问了安全域内存或安全外设别名（如 0x5xxx_xxxx），请核对 SAU/GTZC 配置与非安全外设基址（0x4xxx_xxxx）。"
            )
        if "INVEP" in fault_names or "INVTRAN" in fault_names:
            recs.append(
                "跨安全域调用异常（INVEP/INVTRAN）：请确认安全导出函数已添加 `__cmse_nonsecure_entry` 且链接在 NSC 区域，非安全回调已使用 `__cmse_nonsecure_call`。"
            )

    # 7. HFSR 独立项
    if "VECTTBL" in fault_names:
        primary_cause = primary_cause or "中断向量表读取错误 (HFSR.VECTTBL)"
        recs.append(
            "触发 HFSR.VECTTBL：读取中断向量表时总线错误，请检查 Bootloader 跳转后 `SCB->VTOR` 地址是否有效且满足对齐要求。"
        )
    if "DEBUGEVT" in fault_names and not primary_cause:
        primary_cause = "调试断点事件升级为 HardFault (HFSR.DEBUGEVT)"
        recs.append(
            "触发 HFSR.DEBUGEVT：请检查代码中是否在无调试器接管时执行了 `__BKPT()` 断言或半主机调试输出。"
        )

    # 8. Armv6-M (Cortex-M0/M0+) 无 CFSR 专向推理
    if not core.has_cfsr and fault_detected:
        if stacked_frame is not None:
            raw_xpsr = int(stacked_frame["stacked_xpsr"], 16)
            raw_spc = int(stacked_frame["stacked_pc"], 16)
            if (raw_xpsr & (1 << 24)) == 0:
                primary_cause = primary_cause or f"{core.core_name} 非法状态跳转（压栈 xPSR.T=0，函数指针丢失最低位 Thumb 标志）"
                recs.append(
                    "压栈 xPSR 的 Thumb 位（bit 24）为 0：在 Armv6-M 上向偶数地址执行 BX/BLX（或调用 NULL 函数指针）会直接触发 HardFault，请检查压栈 LR 调用点的函数指针。"
                )
            elif (raw_spc & ~1) == 0:
                primary_cause = primary_cause or f"{core.core_name} 空函数指针调用 (stacked_pc=0x00000000)"
                recs.append("压栈 PC 为 0x00000000：发生了 NULL 空函数指针调用，请检查压栈 LR 所在调用行。")

        primary_cause = primary_cause or f"{core.core_name} {active_exception}（{core.arch} 无 CFSR，已解包异常栈帧）"
        recs.append(
            f"{core.core_name} ({core.arch}) 硬件不包含 CFSR/BFAR 细分寄存器，请对照压栈 PC 指令与 R0..R3 排查："
            "① 空指针或越界地址访问；② 非对齐读写（Cortex-M0/M0+ 不支持任何非对齐 16/32 位访问！）；③ 栈空间越界。"
        )

    # 兜底建议
    if fault_detected and not recs:
        primary_cause = primary_cause or f"{active_exception} 异常停止"
        recs.append(
            f"检测到 {active_exception}（IPSR={ipsr}）：请结合异常栈帧 stacked_pc 与 stacked_lr 查看触发点上下文。"
        )

    if not fault_detected:
        pc_loc = locations.get("pc")
        where = pc_loc.format_short() if pc_loc else "unknown"
        summary = f"[{core.core_name}] 未检测到硬件异常（当前处于 {active_exception}，PC 位于 {where}）。"
        return False, summary, recs

    # 组装高信息密度单行 summary
    loc_target = locations.get("stacked_pc") or locations.get("pc")
    loc_desc = ""
    if loc_target is not None:
        loc_desc = f" @ {loc_target.format_short()} ({loc_target.address})"
    elif stacked_frame is not None:
        loc_desc = f" @ stacked_pc={stacked_frame['stacked_pc']}"

    summary = f"[{core.core_name}] {active_exception}: {primary_cause}{loc_desc}"
    return True, summary, recs


def diagnose_crash(
    probe: DebugProbe,
    *,
    elf_path: str | os.PathLike | None = None,
    project_dir: str | os.PathLike | None = None,
    keep_halted: bool = False,
) -> CrashReport:
    """读取目标 Cortex-M 核心寄存器、SCB 故障状态与异常压栈帧，生成完整死机尸检报告。

    暂停态管理约定：
        * 若进入前 CPU 已处于暂停态（如触发断点或已停在异常现场），诊断结束后始终保持暂停态；
        * 若进入前 CPU 正在运行（如卡死在 `HardFault_Handler` 的 `while(1)` 循环中），
          临时调用 `probe.halt()` 采样核心寄存器与栈帧；当 `keep_halted=False`（默认）时在 `finally` 中自动恢复运行。
    """
    was_halted = probe.is_halted()
    halted_by_us = False
    if not was_halted:
        probe.halt()
        halted_by_us = True

    try:
        # 1. 读取 CPUID 识别内核架构能力，以及 ICSR
        cpuid_val = probe.read_u32(SCB_CPUID, 1)[0]
        core = decode_cpuid(cpuid_val)
        icsr_val = probe.read_u32(SCB_ICSR, 1)[0]

        # 2. 读取 CPU 核心寄存器（补充探测 Armv8-M 的 msplim/psplim 以兼容不同探针后端默认列表）
        regs = probe.read_core_regs()
        if core.has_splim:
            for lim_reg in ("msplim", "psplim"):
                if lim_reg not in regs:
                    try:
                        extra = probe.read_core_regs([lim_reg])
                        regs.update(extra)
                    except ProbeError:
                        pass

        xpsr_val = regs.get("xpsr", 0)
        ipsr = xpsr_val & 0x1FF
        if ipsr == 0 and (icsr_val & 0x1FF) != 0:
            ipsr = icsr_val & 0x1FF
        active_exception = decode_exception_name(ipsr)

        scb_regs: dict[str, str] = {
            "CPUID": f"0x{cpuid_val:08X}",
            "ICSR": f"0x{icsr_val:08X}",
        }
        active_faults: list[FaultBit] = []
        fault_addresses: dict[str, str] = {}
        warnings: list[str] = []

        mmfar_val = 0
        bfar_val = 0
        sfar_val = 0
        mmfar_valid = False
        bfar_valid = False
        sfar_valid = False

        # 3. Armv7-M & Armv8-M Mainline: 读取并逐位解码 CFSR / HFSR / DFSR / MMFAR / BFAR
        if core.has_cfsr:
            cfsr_val = probe.read_u32(SCB_CFSR, 1)[0]
            hfsr_val = probe.read_u32(SCB_HFSR, 1)[0]
            dfsr_val = probe.read_u32(SCB_DFSR, 1)[0]
            mmfar_val = probe.read_u32(SCB_MMFAR, 1)[0]
            bfar_val = probe.read_u32(SCB_BFAR, 1)[0]

            scb_regs["CFSR"] = f"0x{cfsr_val:08X}"
            scb_regs["HFSR"] = f"0x{hfsr_val:08X}"
            scb_regs["DFSR"] = f"0x{dfsr_val:08X}"
            scb_regs["MMFAR"] = f"0x{mmfar_val:08X}"
            scb_regs["BFAR"] = f"0x{bfar_val:08X}"

            mmfar_valid = bool(cfsr_val & (1 << _MMFSR_MMARVALID_BIT))
            bfar_valid = bool(cfsr_val & (1 << _BFSR_BFARVALID_BIT))
            if mmfar_valid:
                fault_addresses["MMFAR"] = f"0x{mmfar_val:08X}"
            if bfar_valid:
                fault_addresses["BFAR"] = f"0x{bfar_val:08X}"

            for reg_name, bit, name, title, desc in _CFSR_BIT_SPECS:
                if cfsr_val & (1 << bit):
                    active_faults.append(
                        FaultBit(
                            register=reg_name,
                            name=name,
                            bit=bit,
                            mask=f"0x{1 << bit:08X}",
                            title=title,
                            description=desc,
                        )
                    )

            for reg_name, bit, name, title, desc in _HFSR_BIT_SPECS:
                if hfsr_val & (1 << bit):
                    active_faults.append(
                        FaultBit(
                            register=reg_name,
                            name=name,
                            bit=bit,
                            mask=f"0x{1 << bit:08X}",
                            title=title,
                            description=desc,
                        )
                    )

        # 4. Armv8-M (M23/M33/M55): 读取并解码 SFSR / SFAR（若目标未开启安全扩展访问受限则安全跳过）
        if core.has_sfsr:
            try:
                sfsr_val = probe.read_u32(SCB_SFSR, 1)[0]
                sfar_val = probe.read_u32(SCB_SFAR, 1)[0]
                scb_regs["SFSR"] = f"0x{sfsr_val:08X}"
                scb_regs["SFAR"] = f"0x{sfar_val:08X}"
                sfsr_faults, sfar_valid = _decode_sfsr(sfsr_val, sfar_val)
                active_faults.extend(sfsr_faults)
                if sfar_valid:
                    fault_addresses["SFAR"] = f"0x{sfar_val:08X}"
            except ProbeError as exc:
                warnings.append(f"读取 Armv8-M SFSR/SFAR 受限已跳过（可能处于非安全调试权限）: {exc}")

        # 5. 检查 Armv8-M MSPLIM / PSPLIM 硬件栈限制
        msp_val = regs.get("msp", regs.get("sp"))
        psp_val = regs.get("psp")
        msplim_val = regs.get("msplim") if core.has_splim else None
        psplim_val = regs.get("psplim") if core.has_splim else None

        msp_overflow = bool(
            core.has_splim
            and msp_val is not None
            and msplim_val is not None
            and msplim_val > 0
            and msp_val <= msplim_val
        )
        if msp_overflow:
            warnings.append(
                f"检测到主栈硬件限制溢出：MSP (0x{msp_val:08X}) <= MSPLIM (0x{msplim_val:08X})！"
            )

        # 6. EXC_RETURN 解码与异常栈帧解包
        lr_val = regs.get("lr", 0)
        lr_has_exc_return = is_exc_return(lr_val)
        exc_info: ExcReturnInfo | None = None
        stacked_frame: dict[str, Any] | None = None
        stack_read_error: str | None = None

        selected_sp_name: str | None = None
        if lr_has_exc_return:
            exc_info = decode_exc_return(lr_val, core.arch_family)
            selected_sp_name = exc_info.stack_pointer
        elif ipsr > 0:
            selected_sp_name = "MSP"
            warnings.append(
                f"当前处于异常态 ({active_exception}, IPSR={ipsr})，但 LR=0x{lr_val:08X} 不是 EXC_RETURN"
                "（中断函数内部可能发生了子函数调用），默认尝试按 MSP 解包栈帧。"
            )

        psp_overflow = bool(
            core.has_splim
            and psp_val is not None
            and psplim_val is not None
            and psplim_val > 0
            and (psp_val > 0 or selected_sp_name == "PSP")
            and psp_val <= psplim_val
        )
        if psp_overflow:
            warnings.append(
                f"检测到线程栈硬件限制溢出：PSP (0x{psp_val:08X}) <= PSPLIM (0x{psplim_val:08X})！"
            )

        if selected_sp_name is not None:
            sp_addr = psp_val if selected_sp_name == "PSP" else msp_val
            if sp_addr is None:
                sp_addr = regs.get("sp")

            if sp_addr is not None:
                if sp_addr % 4 != 0:
                    warnings.append(
                        f"{selected_sp_name} 地址 0x{sp_addr:08X} 非 4 字节对齐，栈指针已严重损坏！"
                    )
                    stack_read_error = f"Unaligned stack pointer {selected_sp_name}=0x{sp_addr:08X}"
                else:
                    if not (0x00000000 < sp_addr < 0x40000000):
                        warnings.append(
                            f"{selected_sp_name}=0x{sp_addr:08X} 超出常规 SRAM 地址区间，高度疑似栈越界损坏。"
                        )
                    try:
                        words = probe.read_u32(sp_addr, 8)
                        # 兼容模拟场景：若 EXC_RETURN 指示 PSP（如 0xFFFFFFBC）但 PSP 全零未初始化且非 PSP 栈溢出，
                        # 而 MSP 上存有合法的 Thumb 异常压栈帧（xPSR.T=1），自动回退读取 MSP 现场并告警说明
                        has_stkof = any(f.name == "STKOF" for f in active_faults)
                        if (
                            selected_sp_name == "PSP"
                            and all(w == 0 for w in words)
                            and not psp_overflow
                            and not has_stkof
                            and msp_val is not None
                            and msp_val != sp_addr
                            and msp_val % 4 == 0
                        ):
                            try:
                                msp_words = probe.read_u32(msp_val, 8)
                                if (msp_words[7] & (1 << 24)) != 0 and (msp_words[6] != 0 or msp_words[5] != 0):
                                    words = msp_words
                                    sp_addr = msp_val
                                    selected_sp_name = "MSP"
                                    warnings.append(
                                        "EXC_RETURN.SPSEL=1 指示 PSP，但 PSP 栈空间全为 0 且 MSP 上存在有效 Thumb 异常帧，已回退使用 MSP 栈帧。"
                                    )
                            except ProbeError:
                                pass

                        stacked_frame = {
                            "stack_pointer": selected_sp_name,
                            "sp_address": f"0x{sp_addr:08X}",
                            "fpu_extended_frame": exc_info.fpu_frame if exc_info else False,
                            "r0": f"0x{words[0]:08X}",
                            "r1": f"0x{words[1]:08X}",
                            "r2": f"0x{words[2]:08X}",
                            "r3": f"0x{words[3]:08X}",
                            "r12": f"0x{words[4]:08X}",
                            "lr": f"0x{words[5]:08X}",
                            "stacked_lr": f"0x{words[5]:08X}",
                            "pc": f"0x{words[6]:08X}",
                            "stacked_pc": f"0x{words[6]:08X}",
                            "xpsr": f"0x{words[7]:08X}",
                            "stacked_xpsr": f"0x{words[7]:08X}",
                        }
                    except ProbeError as exc:
                        stack_read_error = str(exc)
                        warnings.append(
                            f"读取 {selected_sp_name} 异常栈帧 (0x{sp_addr:08X}) 失败: {exc} —— "
                            "栈指针已下溢越界至不可访问区域，这是严重栈溢出的直接证据！"
                        )

        # 7. 若提供了 ELF，执行 PC / stacked_pc / stacked_lr 地址到源码行号映射
        locations: dict[str, CodeLocation] = {}
        if elf_path is not None:
            if "pc" in regs:
                locations["pc"] = resolve_address(
                    elf_path,
                    regs["pc"],
                    project_dir=project_dir,
                    is_return_address=False,
                )
            if stacked_frame is not None:
                spc_int = int(stacked_frame["stacked_pc"], 16)
                slr_int = int(stacked_frame["stacked_lr"], 16)
                locations["stacked_pc"] = resolve_address(
                    elf_path,
                    spc_int,
                    project_dir=project_dir,
                    is_return_address=False,
                )
                if not is_exc_return(slr_int) and slr_int != 0:
                    locations["stacked_lr"] = resolve_address(
                        elf_path,
                        slr_int,
                        project_dir=project_dir,
                        is_return_address=True,
                    )

        core_regs_hex = {k: f"0x{v:08X}" for k, v in regs.items()}
        stack_overflow_flag = (
            msp_overflow
            or psp_overflow
            or any(f.name in ("STKOF", "MSTKERR", "STKERR") for f in active_faults)
            or (stack_read_error is not None)
        )

        fault_detected, summary, recommendations = _build_recommendations_and_summary(
            core=core,
            active_exception=active_exception,
            ipsr=ipsr,
            active_faults=active_faults,
            mmfar=mmfar_val,
            mmfar_valid=mmfar_valid,
            bfar=bfar_val,
            bfar_valid=bfar_valid,
            sfar=sfar_val,
            sfar_valid=sfar_valid,
            msp=msp_val,
            psp=psp_val,
            msplim=msplim_val,
            psplim=psplim_val,
            msp_overflow=msp_overflow,
            psp_overflow=psp_overflow,
            stack_read_error=stack_read_error,
            stacked_frame=stacked_frame,
            locations=locations,
            warnings=warnings,
        )
    finally:
        if halted_by_us and not keep_halted:
            try:
                probe.resume()
            except ProbeError:
                pass

    halted_after = probe.is_halted()
    return CrashReport(
        fault_detected=fault_detected,
        summary=summary,
        core=core,
        active_exception=active_exception,
        ipsr=ipsr,
        halted_before=was_halted,
        halted_after=halted_after,
        active_faults=active_faults,
        scb_registers=scb_regs,
        fault_addresses=fault_addresses,
        mmfar_valid=mmfar_valid,
        bfar_valid=bfar_valid,
        sfar_valid=sfar_valid,
        core_registers=core_regs_hex,
        exc_return=exc_info,
        stacked_frame=stacked_frame,
        stack_overflow=stack_overflow_flag,
        stack_read_error=stack_read_error,
        locations=locations,
        warnings=warnings,
        recommendations=recommendations,
    )
