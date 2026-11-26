"""分级硬件安全护栏（Option Bytes 防变砖硬保护 + HITL 人工确认门 + 环形审计日志）单元测试。

同时兼容 `pytest` 收集与 `PYTHONPATH=src python3 tests/test_safety.py` 直接运行。
"""

from __future__ import annotations

import unittest

from mcu_harness.config import SafetyConfig
from mcu_harness.safety import AuditEntry, SafetyDecision, SafetyError, SafetyGuard


class TestSafetyOptionBytesHardDeny(unittest.TestCase):
    """测试各系列 STM32 Option Bytes / Product State 即使 confirmed=True 也被硬拒绝。"""

    def test_stm32h5_option_bytes_and_product_state_hard_deny(self) -> None:
        guard = SafetyGuard()
        h5_critical_addrs = (
            0x08FFF000,  # Option Bytes / Product State Flash 映射扇区
            0x40022050,  # Non-Secure FLASH option/security 控制寄存器
            0x50022050,  # Secure FLASH option/security 控制寄存器
        )
        for addr in h5_critical_addrs:
            for confirmed in (False, True):
                with self.subTest(addr=hex(addr), confirmed=confirmed):
                    dec = guard.check_memory_write(
                        addr,
                        4,
                        device="STM32H563ZI",
                        confirmed=confirmed,
                    )
                    self.assertFalse(dec.allowed)
                    self.assertEqual(dec.level, "hard_deny")
                    self.assertEqual(dec.operation, "write_memory")
                    self.assertIn("HARD DENY", dec.reason)

        # 跨边界写入（0x08FFEFFC 起写 8 字节覆盖到 0x08FFF003）同样必须硬拦截
        cross_dec = guard.check_memory_write(0x08FFEFFC, 8, device="STM32H563ZI", confirmed=True)
        self.assertFalse(cross_dec.allowed)
        self.assertEqual(cross_dec.level, "hard_deny")

        # 全部硬拒绝尝试均应落入审计日志且标记 allowed=False
        audits = guard.recent_audit(limit=20)
        self.assertEqual(len(audits), 7)
        self.assertTrue(all((not a.allowed) and a.level == "hard_deny" for a in audits))

    def test_stm32f4_g4_f1_option_bytes_hard_deny(self) -> None:
        guard = SafetyGuard()
        matrix = [
            ("STM32F407VG", 0x40023C14, 4),  # STM32F4 FLASH_OPTCR
            ("STM32F407VG", 0x1FFFC000, 4),  # STM32F4 Option Bytes & OTP
            ("STM32G431CB", 0x1FFF7800, 4),  # STM32G4 Option Bytes
            ("STM32G474RE", 0x40022020, 4),  # STM32G4 FLASH_OPTR
            ("STM32F103C8", 0x1FFFF800, 2),  # STM32F1 Option Bytes
        ]
        for device, addr, nbytes in matrix:
            for confirmed in (False, True):
                with self.subTest(device=device, addr=hex(addr), confirmed=confirmed):
                    dec = guard.check_memory_write(
                        addr,
                        nbytes,
                        device=device,
                        confirmed=confirmed,
                    )
                    self.assertFalse(dec.allowed)
                    self.assertEqual(dec.level, "hard_deny")
                    self.assertIn("HARD DENY", dec.reason)

    def test_explicit_allow_option_bytes_still_requires_hitl_confirmation(self) -> None:
        guard = SafetyGuard()
        cfg = SafetyConfig(allow_option_bytes=True)

        # 即使显式开启 allow_option_bytes=True，未传 confirmed=True 仍受外设/Flash HITL 门保护
        unconfirmed = guard.check_memory_write(0x08FFF000, 4, device="STM32H563ZI", config=cfg, confirmed=False)
        self.assertFalse(unconfirmed.allowed)
        self.assertEqual(unconfirmed.level, "require_confirmation")

        confirmed = guard.check_memory_write(0x08FFF000, 4, device="STM32H563ZI", config=cfg, confirmed=True)
        self.assertTrue(confirmed.allowed)
        self.assertEqual(confirmed.level, "require_confirmation")


class TestSafetyHitlAndAudit(unittest.TestCase):
    """测试外设 MMIO、SCS、普通 SRAM、自定义区间及固件烧录 HITL 策略与审计记录。"""

    def test_mmio_and_scs_require_confirmation_then_allow_and_audit(self) -> None:
        guard = SafetyGuard()

        # 1. 外设 MMIO（GPIOA->ODR: 0x42020014）
        mmio_block = guard.check_memory_write(
            0x42020014,
            4,
            device="STM32H563ZI",
            confirmed=False,
            peripheral_hint="GPIOA",
        )
        self.assertFalse(mmio_block.allowed)
        self.assertEqual(mmio_block.level, "require_confirmation")
        self.assertIn("GPIOA", mmio_block.target)

        mmio_ok = guard.check_memory_write(
            0x42020014,
            4,
            device="STM32H563ZI",
            confirmed=True,
            peripheral_hint="GPIOA",
        )
        self.assertTrue(mmio_ok.allowed)
        self.assertEqual(mmio_ok.level, "require_confirmation")

        # 2. Cortex-M System Control Space（SCB->AIRCR: 0xE000ED0C）
        scs_block = guard.check_memory_write(0xE000ED0C, 4, device="STM32H563ZI", confirmed=False)
        self.assertFalse(scs_block.allowed)
        self.assertEqual(scs_block.level, "require_confirmation")
        self.assertIn("System Control Space", scs_block.reason)

        scs_ok = guard.check_memory_write(0xE000ED0C, 4, device="STM32H563ZI", confirmed=True)
        self.assertTrue(scs_ok.allowed)
        self.assertEqual(scs_ok.level, "require_confirmation")

        # 核对 4 条审计日志按序号严格递增记录了拦截与放行状态
        audits = guard.recent_audit(limit=10)
        self.assertEqual([a.seq for a in audits], [1, 2, 3, 4])
        self.assertEqual([a.allowed for a in audits], [False, True, False, True])
        self.assertTrue(all(a.level == "require_confirmation" for a in audits))

    def test_sram_default_allow_and_protected_peripherals_override(self) -> None:
        guard = SafetyGuard()

        # 普通 SRAM (0x20000100) 默认无需确认直接放行并写入审计
        sram_dec = guard.check_memory_write(0x20000100, 4, device="STM32H563ZI", confirmed=False)
        self.assertTrue(sram_dec.allowed)
        self.assertEqual(sram_dec.level, "allow")

        audits = guard.recent_audit(limit=1)
        self.assertEqual(len(audits), 1)
        self.assertTrue(audits[0].allowed)
        self.assertEqual(audits[0].level, "allow")
        self.assertIn("0x20000100", audits[0].target)

        # 开启 allow_peripheral_write_without_confirm 后，普通 GPIO 放行，但 TIM1 等受保护外设仍需确认
        relaxed_cfg = SafetyConfig(allow_peripheral_write_without_confirm=True)
        gpio_dec = guard.check_memory_write(
            0x42020014, 4, config=relaxed_cfg, confirmed=False, peripheral_hint="GPIOA"
        )
        self.assertTrue(gpio_dec.allowed)

        tim1_dec = guard.check_memory_write(
            0x40012C00, 4, config=relaxed_cfg, confirmed=False, peripheral_hint="TIM1"
        )
        self.assertFalse(tim1_dec.allowed)
        self.assertEqual(tim1_dec.level, "require_confirmation")
        self.assertIn("TIM1", tim1_dec.reason)

    def test_custom_deny_ranges_and_confirm_ranges(self) -> None:
        guard = SafetyGuard()
        cfg = SafetyConfig(
            deny_ranges=((0x2000F000, 0x2000FFFF, "safety-critical vector RAM"),),
            confirm_ranges=((0x20008000, 0x200080FF, "live PID gains"),),
        )

        # 自定义 deny_ranges 在 SRAM 中也必须硬拒绝（哪怕 confirmed=True）
        deny_dec = guard.check_memory_write(0x2000F100, 4, config=cfg, confirmed=True)
        self.assertFalse(deny_dec.allowed)
        self.assertEqual(deny_dec.level, "hard_deny")
        self.assertIn("safety-critical vector RAM", deny_dec.reason)

        # 自定义 confirm_ranges 在 SRAM 中未确认拦截，确认后放行
        conf_block = guard.check_memory_write(0x20008020, 4, config=cfg, confirmed=False)
        self.assertFalse(conf_block.allowed)
        self.assertEqual(conf_block.level, "require_confirmation")
        self.assertIn("live PID gains", conf_block.reason)

        conf_ok = guard.check_memory_write(0x20008020, 4, config=cfg, confirmed=True)
        self.assertTrue(conf_ok.allowed)
        self.assertEqual(conf_ok.level, "require_confirmation")

    def test_check_flash_hitl_and_ring_buffer_helpers(self) -> None:
        guard = SafetyGuard(max_audit_entries=3)

        f_block = guard.check_flash("build/app.elf", device="STM32H563ZI", confirmed=False)
        self.assertFalse(f_block.allowed)
        self.assertEqual(f_block.level, "require_confirmation")
        self.assertEqual(f_block.operation, "flash_firmware")

        err = SafetyError(f_block)
        self.assertIs(err.decision, f_block)
        self.assertIn("HITL confirmation required", str(err))
        self.assertIsInstance(f_block.to_dict(), dict)

        f_ok = guard.check_flash("build/app.elf", device="STM32H563ZI", confirmed=True)
        self.assertTrue(f_ok.allowed)
        self.assertEqual(f_ok.level, "require_confirmation")

        auto_cfg = SafetyConfig(allow_flash_without_confirm=True)
        f_auto1 = guard.check_flash("build/app1.elf", device="STM32H563ZI", config=auto_cfg, confirmed=False)
        f_auto2 = guard.check_flash("build/app2.elf", device="STM32H563ZI", config=auto_cfg, confirmed=False)
        self.assertTrue(f_auto1.allowed)
        self.assertTrue(f_auto2.allowed)

        # 验证环形缓冲区容量上限 (max_audit_entries=3) 与 clear_audit()
        recent = guard.recent_audit(limit=10)
        self.assertEqual(len(recent), 3)
        self.assertEqual([e.seq for e in recent], [2, 3, 4])
        self.assertIsInstance(recent[0], AuditEntry)
        self.assertEqual(recent[0].to_dict()["seq"], 2)

        cleared = guard.clear_audit()
        self.assertEqual(cleared, 3)
        self.assertEqual(guard.recent_audit(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
