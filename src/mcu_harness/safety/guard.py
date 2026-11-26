"""硬件安全护栏（HITL 分级拦截 + Option Bytes / Product State 硬保护 + 审计日志）。

安全分级策略：
    1. HARD_DENY（硬拒绝）：
       - 覆盖 STM32H5 Option Bytes / Product State（防止芯片永久 Locked 变砖）
       - 覆盖 STM32F1/F4/F7/G0/G4/L4/H7 Option Bytes / OTP 锁定位及自定义 deny_ranges
       - 除非在 mcu-harness.toml 显式开启 `allow_option_bytes = true`，即使传 `confirm=True` 也一律拦截。
    2. REQUIRE_CONFIRMATION（人工确认门 HITL）：
       - 擦除烧录 Flash（`flash_firmware`）
       - 写外设寄存器空间（0x40000000..0x5FFFFFFF，如 PWM / 电源 / 时钟 / 看门狗）
       - 写内核控制寄存器空间（0xE000E000..0xE000EFFF，如 AIRCR / VTOR / NVIC）
       - 写 Flash 地址区（0x08000000..0x1FFFFFFF）
       - 未携带 `confirmed=True` 时拦截并返回清晰风险摘要，交由人工批准后再执行。
    3. ALLOW（放行并审计）：
       - 普通 SRAM（0x20000000..0x3FFFFFFF）调试变量微调及所有已获批变更，均写入环形审计日志。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any

from mcu_harness.config.manifest import SafetyConfig


class SafetyError(RuntimeError):
    """操作被安全护栏拦截（硬拒绝或缺少 HITL 人工确认）。"""

    def __init__(self, decision: SafetyDecision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    level: str  # "allow" | "require_confirmation" | "hard_deny"
    operation: str
    target: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    timestamp: float
    operation: str
    target: str
    level: str
    allowed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# 内置高危不可逆寄存器与 Option Bytes 保护表：(device_prefix_or_ALL, start, end, reason)
_BUILTIN_OPTION_BYTE_RANGES: tuple[tuple[str, int, int, str], ...] = (
    # STM32H5: Option Bytes Flash 映射区 + FLASH Non-Secure / Secure OPTSR/OPTCR/SECWM/BOOTR
    ("STM32H5", 0x08FFF000, 0x08FFFFFF, "STM32H5 Option Bytes / Product State flash sector (irreversible lock risk)"),
    ("STM32H5", 0x4002201C, 0x400220FF, "STM32H5 FLASH option/security control registers (Product State / TZEN / WRP)"),
    ("STM32H5", 0x5002201C, 0x500220FF, "STM32H5 Secure FLASH option/security control registers"),
    # STM32U5 / L5 TrustZone Option Bytes
    ("STM32U5", 0x40022020, 0x400220FF, "STM32U5 FLASH option control registers"),
    ("STM32U5", 0x50022020, 0x500220FF, "STM32U5 Secure FLASH option control registers"),
    ("STM32L5", 0x40022020, 0x400220FF, "STM32L5 FLASH option control registers"),
    ("STM32L5", 0x50022020, 0x500220FF, "STM32L5 Secure FLASH option control registers"),
    # STM32H7 Option Bytes & FLASH_OPTCR
    ("STM32H7", 0x52002018, 0x5200203F, "STM32H7 FLASH_OPTCR / option byte control registers"),
    ("STM32H7", 0x52002118, 0x5200213F, "STM32H7 Bank2 FLASH_OPTCR registers"),
    # STM32F4 / F2 / F7 Option Bytes & FLASH_OPTCR
    ("STM32F4", 0x1FFFC000, 0x1FFFFFFF, "STM32F4 Option Bytes & OTP lock region"),
    ("STM32F4", 0x40023C14, 0x40023C1B, "STM32F4 FLASH_OPTCR / OPTCR1 registers (RDP / BOR / WRP)"),
    ("STM32F2", 0x1FFFC000, 0x1FFFFFFF, "STM32F2 Option Bytes region"),
    ("STM32F2", 0x40023C14, 0x40023C1B, "STM32F2 FLASH_OPTCR registers"),
    ("STM32F7", 0x1FFF0000, 0x1FFFFFFF, "STM32F7 Option Bytes & OTP region"),
    ("STM32F7", 0x40023C14, 0x40023C1B, "STM32F7 FLASH_OPTCR registers"),
    # STM32G4 / L4 / G0 Option Bytes & FLASH_OPTR
    ("STM32G4", 0x1FFF7800, 0x1FFF78FF, "STM32G4 Option Bytes & OTP region"),
    ("STM32G4", 0x40022020, 0x40022053, "STM32G4 FLASH_OPTR / PCROP / WRP registers"),
    ("STM32L4", 0x1FFF7800, 0x1FFF78FF, "STM32L4 Option Bytes & OTP region"),
    ("STM32L4", 0x40022020, 0x40022053, "STM32L4 FLASH_OPTR / PCROP / WRP registers"),
    ("STM32G0", 0x1FFF7800, 0x1FFF78FF, "STM32G0 Option Bytes & OTP region"),
    ("STM32G0", 0x40022020, 0x40022053, "STM32G0 FLASH_OPTR / PCROP / WRP registers"),
    # STM32F1 / GD32F1 / AT32 Option Bytes
    ("STM32F1", 0x1FFFF800, 0x1FFFFFFF, "STM32F1 Option Bytes (RDP / User / WRP)"),
    ("GD32", 0x1FFFF800, 0x1FFFFFFF, "GD32 Option Bytes region"),
    ("AT32", 0x1FFFF800, 0x1FFFFFFF, "AT32 User System Data / Option Bytes"),
)


def _ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


class SafetyGuard:
    """硬件写操作与烧录操作的统一策略守卫 + 环形审计记录器。"""

    def __init__(self, *, max_audit_entries: int = 500) -> None:
        self._lock = threading.Lock()
        self._audit: deque[AuditEntry] = deque(maxlen=max_audit_entries)
        self._next_seq = 1

    def record_audit(
        self,
        operation: str,
        target: str,
        *,
        level: str = "allow",
        allowed: bool = True,
        detail: str = "",
    ) -> AuditEntry:
        with self._lock:
            entry = AuditEntry(
                seq=self._next_seq,
                timestamp=time.time(),
                operation=operation,
                target=target,
                level=level,
                allowed=allowed,
                detail=detail,
            )
            self._next_seq += 1
            self._audit.append(entry)
            return entry

    def recent_audit(self, limit: int = 50) -> list[AuditEntry]:
        with self._lock:
            items = list(self._audit)
        return items[-max(1, limit) :]

    def clear_audit(self) -> int:
        with self._lock:
            n = len(self._audit)
            self._audit.clear()
            return n

    def check_memory_write(
        self,
        address: int,
        num_bytes: int,
        *,
        device: str | None = None,
        config: SafetyConfig | None = None,
        confirmed: bool = False,
        peripheral_hint: str | None = None,
    ) -> SafetyDecision:
        """评估一次内存/寄存器写入的安全性，并自动写入审计日志。"""
        cfg = config or SafetyConfig()
        end_addr = address + max(1, num_bytes) - 1
        target_str = f"0x{address:08X}..0x{end_addr:08X} ({num_bytes}B)"
        if peripheral_hint:
            target_str = f"{peripheral_hint} @ {target_str}"

        dev_upper = ((device or "").strip() or "STM32H563ZI").upper()

        # 1. 自定义硬拒绝区间
        for s_addr, e_addr, reason in cfg.deny_ranges:
            if _ranges_overlap(address, end_addr, s_addr, e_addr):
                dec = SafetyDecision(
                    allowed=False,
                    level="hard_deny",
                    operation="write_memory",
                    target=target_str,
                    reason=f"Blocked by safety.deny_ranges ({reason}): writing {target_str} is forbidden.",
                )
                self.record_audit("write_memory", target_str, level=dec.level, allowed=False, detail=dec.reason)
                return dec

        # 2. 内置 Option Bytes / Product State 硬拒绝保护
        if not cfg.allow_option_bytes:
            for prefix, s_addr, e_addr, reason in _BUILTIN_OPTION_BYTE_RANGES:
                if (prefix == "ALL" or dev_upper.startswith(prefix)) and _ranges_overlap(
                    address, end_addr, s_addr, e_addr
                ):
                    dec = SafetyDecision(
                        allowed=False,
                        level="hard_deny",
                        operation="write_memory",
                        target=target_str,
                        reason=(
                            f"HARD DENY: {reason}. Writing {target_str} is blocked to prevent bricking the MCU "
                            f"(set safety.allow_option_bytes=true in mcu-harness.toml only if intentional)."
                        ),
                    )
                    self.record_audit("write_memory", target_str, level=dec.level, allowed=False, detail=dec.reason)
                    return dec

        # 3. 需 HITL 确认的高风险地址段（自定义 confirm_ranges / Flash / 外设 MMIO / System Control Space）
        confirm_reason: str | None = None
        for s_addr, e_addr, reason in cfg.confirm_ranges:
            if _ranges_overlap(address, end_addr, s_addr, e_addr):
                confirm_reason = f"Custom protected range: {reason}"
                break

        if confirm_reason is None and peripheral_hint and peripheral_hint.upper() in cfg.protected_peripherals:
            confirm_reason = f"Protected peripheral '{peripheral_hint.upper()}' (power/clock/PWM/watchdog)"

        if confirm_reason is None:
            if _ranges_overlap(address, end_addr, 0x08000000, 0x1FFFFFFF):
                confirm_reason = "Direct write to Flash/OTP address space (use flash_firmware or confirm explicitly)"
            elif _ranges_overlap(address, end_addr, 0xE000E000, 0xE000EFFF):
                confirm_reason = "Write to Cortex-M System Control Space (SCS/NVIC/AIRCR)"
            elif _ranges_overlap(address, end_addr, 0x40000000, 0x5FFFFFFF) and not cfg.allow_peripheral_write_without_confirm:
                confirm_reason = "Write to hardware peripheral MMIO register space (0x4000_0000..0x5FFF_FFFF)"

        if confirm_reason is not None and not confirmed:
            dec = SafetyDecision(
                allowed=False,
                level="require_confirmation",
                operation="write_memory",
                target=target_str,
                reason=(
                    f"HITL confirmation required before writing {target_str}: {confirm_reason}. "
                    f"Ask the user for approval, then re-call with confirm=True."
                ),
            )
            self.record_audit("write_memory", target_str, level=dec.level, allowed=False, detail=confirm_reason)
            return dec

        lvl = "require_confirmation" if confirm_reason is not None else "allow"
        dec = SafetyDecision(
            allowed=True,
            level=lvl,
            operation="write_memory",
            target=target_str,
            reason="Approved (confirmed)" if confirm_reason else "Allowed (RAM write)",
        )
        self.record_audit("write_memory", target_str, level=lvl, allowed=True, detail=dec.reason)
        return dec

    def check_flash(
        self,
        firmware_path: str,
        *,
        device: str | None = None,
        config: SafetyConfig | None = None,
        confirmed: bool = False,
    ) -> SafetyDecision:
        """评估固件烧录操作的 HITL 权限。"""
        cfg = config or SafetyConfig()
        target_str = f"{firmware_path} -> {device or 'target'}"
        if not confirmed and not cfg.allow_flash_without_confirm:
            dec = SafetyDecision(
                allowed=False,
                level="require_confirmation",
                operation="flash_firmware",
                target=target_str,
                reason=(
                    f"HITL confirmation required before flashing {target_str} "
                    f"(will erase/reprogram Flash and reset MCU). Ask the user for approval, then pass confirm=True."
                ),
            )
            self.record_audit("flash_firmware", target_str, level=dec.level, allowed=False, detail="awaiting confirm")
            return dec

        dec = SafetyDecision(
            allowed=True,
            level="require_confirmation",
            operation="flash_firmware",
            target=target_str,
            reason="Flash approved",
        )
        self.record_audit("flash_firmware", target_str, level=dec.level, allowed=True, detail="approved")
        return dec


default_safety_guard = SafetyGuard()
