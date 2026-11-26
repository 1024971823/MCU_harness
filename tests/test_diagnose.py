"""跨外设因果体检医生（Peripheral Causal Doctor）与板级配置核验器（Board Context Inspector）单元测试。"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path

from mcu_harness.diagnose import (
    diagnose_peripheral,
    inspect_board_context,
    scan_configured_gpio_pins,
)
from mcu_harness.diagnose.cli import main as doctor_cli_main
from mcu_harness.probe.mock import MockProbe
from mcu_harness.svd import load_svd, parse_svd_root

SVD_FIXTURE = Path(__file__).parent / "fixtures" / "stm32h563_mini.svd"

# 寄存器地址常量（对应 tests/fixtures/stm32h563_mini.svd）
GPIOA_MODER = 0x42020000
GPIOA_ODR = 0x42020014
GPIOA_AFRL = 0x42020020
GPIOA_AFRH = 0x42020024
GPIOB_MODER = 0x42020400
RCC_AHB2ENR = 0x44020C8C
RCC_APB2ENR = 0x44020CA4
USART1_CR1 = 0x40013800
USART1_ISR = 0x4001381C


class PeripheralDoctorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svd = load_svd(SVD_FIXTURE)

    def test_scenario_a_clock_disabled_critical_root_cause(self) -> None:
        """场景 A：RCC->APB2ENR 未置位 USART1EN，立即产出 critical 时钟未开启根因结论。"""
        with MockProbe() as probe:
            probe.set_word(RCC_APB2ENR, 0x00000000)  # USART1EN (bit 14) = 0

            report = diagnose_peripheral(probe, self.svd, "USART1")

        self.assertFalse(report.healthy)
        self.assertEqual(report.highest_severity, "critical")
        self.assertTrue(report.clock_gate.found_in_svd)
        self.assertEqual(report.clock_gate.clock_peripheral, "RCC")
        self.assertEqual(report.clock_gate.register, "APB2ENR")
        self.assertEqual(report.clock_gate.field, "USART1EN")
        self.assertIs(report.clock_gate.enabled, False)
        self.assertTrue(report.self_status.skipped_due_to_clock_disabled)

        crit_findings = [f for f in report.findings if f.severity == "critical" and f.code == "CLOCK_DISABLED"]
        self.assertEqual(len(crit_findings), 1)
        self.assertIn("RCC->APB2ENR.USART1EN = 0", crit_findings[0].detail)
        self.assertIn("忽略", crit_findings[0].detail)

    def test_scenario_b_usart_ore_error_and_nvic_pending_but_disabled(self) -> None:
        """场景 B：开启 USART1EN=1、CR1=0x0D(UE/TE/RE)、ISR 注入 ORE=1 且 NVIC 挂起 IRQ 61 但未使能。"""
        irqn = 61  # USART1_IRQn = 61 -> reg_idx=1 (+0x04), bit=29
        nvic_iser1 = 0xE000E104
        nvic_ispr1 = 0xE000E204
        nvic_ipr_word = 0xE000E400 + (irqn // 4) * 4

        with MockProbe() as probe:
            probe.set_word(RCC_APB2ENR, 1 << 14)               # USART1EN = 1
            probe.set_word(USART1_CR1, 0x0000000D)             # UE=1, RE=1, TE=1
            probe.set_word(USART1_ISR, 0x000000C0 | (1 << 3))  # TXE=1, TC=1, ORE=1 (bit 3)
            probe.set_word(nvic_iser1, 0x00000000)             # NVIC 未使能
            probe.set_word(nvic_ispr1, 1 << (irqn % 32))       # IRQ 61 已挂起
            probe.set_word(nvic_ipr_word, 0x50 << ((irqn % 4) * 8))

            report = diagnose_peripheral(probe, self.svd, "USART1")

        self.assertFalse(report.healthy)
        self.assertIs(report.clock_gate.enabled, True)
        self.assertTrue(report.self_status.master_enable.enabled)
        self.assertEqual(report.self_status.master_enable.field, "UE")

        # 验证 NVIC 状态解码
        self.assertEqual(len(report.nvic_irqs), 1)
        irq_stat = report.nvic_irqs[0]
        self.assertEqual(irq_stat.irqn, 61)
        self.assertFalse(irq_stat.enabled)
        self.assertTrue(irq_stat.pending)
        self.assertEqual(irq_stat.priority_raw, 0x50)

        # 验证同时抓出 ORE 溢出错误与 NVIC 挂起未使能告警
        codes = {f.code for f in report.findings}
        self.assertIn("FLAG_ORE", codes)
        self.assertIn("NVIC_PENDING_BUT_DISABLED", codes)

        nvic_finding = next(f for f in report.findings if f.code == "NVIC_PENDING_BUT_DISABLED")
        self.assertIn(nvic_finding.severity, ("warning", "error"))
        self.assertIn("HAL_NVIC_EnableIRQ", nvic_finding.detail + nvic_finding.suggestion)

        # 验证 changed_registers 仅收集偏离 resetValue 的寄存器（CR1, ISR）
        changed_names = [c.register for c in report.self_status.changed_registers]
        self.assertIn("CR1", changed_names)
        self.assertIn("ISR", changed_names)
        text_out = report.to_text()
        self.assertIn("FLAG_ORE", text_out)
        self.assertIn("NVIC_PENDING_BUT_DISABLED", text_out)

    def test_scenario_c_scan_configured_gpio_pins_output_and_afrl_decode(self) -> None:
        """场景 C：验证 scan_configured_gpio_pins 识别 PA5=Output(ODR=1)、支持 AFRL/AFRH 复用解码并跳过未开时钟端口。"""
        with MockProbe() as probe:
            # 仅开启 GPIOA 时钟（GPIOBEN=0，即使 GPIOB_MODER 被置 0 也不应被误扫成 16 个 Input）
            probe.set_word(RCC_AHB2ENR, 0x00000001)
            probe.set_word(GPIOB_MODER, 0x00000000)

            # 基于 GPIOA->MODER 复位值 0xABFFFFFF：
            #   PA2 (bits 5:4)   -> 0b10 Alternate, AFRL[11:8] = AF7
            #   PA5 (bits 11:10) -> 0b01 Output, ODR[5] = 1
            #   PA9 (bits 19:18) -> 0b10 Alternate, AFRH[7:4] = AF7
            moder = 0xABFFFFFF
            moder = (moder & ~(0x3 << (2 * 2))) | (0x2 << (2 * 2))
            moder = (moder & ~(0x3 << (5 * 2))) | (0x1 << (5 * 2))
            moder = (moder & ~(0x3 << (9 * 2))) | (0x2 << (9 * 2))

            probe.set_word(GPIOA_MODER, moder)
            probe.set_word(GPIOA_ODR, 1 << 5)
            probe.set_word(GPIOA_AFRL, 7 << (2 * 4))
            probe.set_word(GPIOA_AFRH, 7 << ((9 - 8) * 4))

            pins = scan_configured_gpio_pins(probe, self.svd)

        by_pin = {p.pin: p for p in pins}
        # 未改动过的 SWD 复位脚 PA13/14/15 与未开时钟的 GPIOB 均不应混入结果
        self.assertEqual(sorted(by_pin.keys()), ["PA2", "PA5", "PA9"])

        pa5 = by_pin["PA5"]
        self.assertEqual(pa5.mode, "Output")
        self.assertEqual(pa5.output_type, "Push-Pull")
        self.assertEqual(pa5.odr, 1)
        self.assertIn("PA5: Output, Push-Pull, ODR=1", pa5.summary)

        pa2 = by_pin["PA2"]
        self.assertEqual(pa2.mode, "Alternate")
        self.assertEqual(pa2.af, 7)
        self.assertEqual(pa2.af_label, "AF7")
        self.assertIn("Alternate(AF7)", pa2.summary)

        pa9 = by_pin["PA9"]
        self.assertEqual(pa9.mode, "Alternate")
        self.assertEqual(pa9.af, 7)
        self.assertEqual(pa9.af_label, "AF7")

    def test_i2c_push_pull_warning_and_internal_ie_without_nvic(self) -> None:
        """补充验证：I2C 引脚误配为 Push-Pull 推挽输出提醒，以及外设内部开启 *IE 但 NVIC 未使能提醒。"""
        i2c_svd_xml = """<?xml version="1.0" encoding="utf-8"?>
        <device schemaVersion="1.1">
          <name>STM32H5_I2C_TEST</name>
          <peripherals>
            <peripheral>
              <name>GPIOB</name>
              <baseAddress>0x42020400</baseAddress>
              <registers>
                <register><name>MODER</name><addressOffset>0x00</addressOffset><size>32</size><resetValue>0xFFFFFFFF</resetValue></register>
                <register><name>OTYPER</name><addressOffset>0x04</addressOffset><size>32</size><resetValue>0x00000000</resetValue></register>
                <register><name>AFRL</name><addressOffset>0x20</addressOffset><size>32</size><resetValue>0x00000000</resetValue></register>
              </registers>
            </peripheral>
            <peripheral>
              <name>RCC</name>
              <baseAddress>0x44020C00</baseAddress>
              <registers>
                <register>
                  <name>AHB2ENR</name><addressOffset>0x8C</addressOffset><size>32</size><resetValue>0</resetValue>
                  <fields><field><name>GPIOBEN</name><bitOffset>1</bitOffset><bitWidth>1</bitWidth></field></fields>
                </register>
                <register>
                  <name>APB1LENR</name><addressOffset>0x9C</addressOffset><size>32</size><resetValue>0</resetValue>
                  <fields><field><name>I2C1EN</name><bitOffset>21</bitOffset><bitWidth>1</bitWidth></field></fields>
                </register>
              </registers>
            </peripheral>
            <peripheral>
              <name>I2C1</name>
              <baseAddress>0x40005400</baseAddress>
              <interrupt><name>I2C1_EV</name><value>31</value></interrupt>
              <registers>
                <register>
                  <name>CR1</name><addressOffset>0x00</addressOffset><size>32</size><resetValue>0</resetValue>
                  <fields>
                    <field><name>PE</name><bitOffset>0</bitOffset><bitWidth>1</bitWidth></field>
                    <field><name>ERRIE</name><bitOffset>7</bitOffset><bitWidth>1</bitWidth></field>
                  </fields>
                </register>
                <register>
                  <name>ISR</name><addressOffset>0x18</addressOffset><size>32</size><resetValue>0x00000001</resetValue>
                  <fields>
                    <field><name>NACKF</name><bitOffset>4</bitOffset><bitWidth>1</bitWidth></field>
                    <field><name>BUSY</name><bitOffset>15</bitOffset><bitWidth>1</bitWidth></field>
                  </fields>
                </register>
              </registers>
            </peripheral>
          </peripherals>
        </device>
        """
        dev = parse_svd_root(ET.fromstring(i2c_svd_xml))
        with MockProbe() as probe:
            probe.set_word(0x44020C8C, 1 << 1)   # GPIOBEN = 1
            probe.set_word(0x44020C9C, 1 << 21)  # I2C1EN = 1
            # PB6/PB7 设为 Alternate(AF4)，但 OTYPER=0 (Push-Pull 错误配置！)
            moder = (0xFFFFFFFF & ~(0x3 << 12) & ~(0x3 << 14)) | (0x2 << 12) | (0x2 << 14)
            probe.set_word(0x42020400, moder)
            probe.set_word(0x42020404, 0x00000000)  # OTYPER = Push-Pull
            probe.set_word(0x42020420, (4 << 24) | (4 << 28))  # PB6=AF4, PB7=AF4
            # I2C1->CR1: PE=1, ERRIE=1；ISR: NACKF=1, BUSY=1；NVIC IRQ 31 未开启且未挂起
            probe.set_word(0x40005400, (1 << 0) | (1 << 7))
            probe.set_word(0x40005418, (1 << 4) | (1 << 15))

            report = diagnose_peripheral(probe, dev, "I2C1")

        codes = {f.code for f in report.findings}
        self.assertIn("I2C_PIN_PUSH_PULL", codes)
        self.assertIn("IRQ_SOURCE_ENABLED_NVIC_DISABLED", codes)
        self.assertIn("FLAG_NACKF", codes)
        self.assertIn("FLAG_BUSY", codes)


class BoardContextInspectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.svd = load_svd(SVD_FIXTURE)

    def test_board_context_static_and_live_drift_check(self) -> None:
        """构造含 .ioc 与 mcu-harness.toml [[board.devices]] 的临时工程，结合 MockProbe 验证 PA5 匹配、PA9 漂移报警。"""
        ioc_content = """#MicroXplorer Configuration settings
Mcu.Family=STM32H5
Mcu.Name=STM32H563ZITx
Mcu.UserName=STM32H563ZIT6
Mcu.Package=LQFP144
Mcu.IP0=GPDMA1
Mcu.IP1=GPIO
Mcu.IP2=NVIC
Mcu.IP3=RCC
Mcu.IP4=USART1
RCC.HSE_VALUE=8000000
RCC.SYSCLKFreq_VALUE=250000000
PA5.Signal=GPIO_Output
PA5.GPIO_Label=VALVE_EN
PA5.GPIO_ModeDefaultOutputPP=GPIO_MODE_OUTPUT_PP
PA5.GPIO_PuPd=GPIO_NOPULL
PA9.Signal=USART1_TX
PA9.GPIO_AF=GPIO_AF7_USART1
GPDMA1.CH0.Request=USART1_RX
GPDMA1.CH0.Direction=DMA_PERIPH_TO_MEMORY
NVIC.USART1_IRQn=true\\:5\\:0\\:false\\:false\\:true
"""
        toml_content = """
[target]
device = "STM32H563ZI"
probe_backend = "mock"

[board]
name = "Lannuo-Fluid-Control-RevB"

[[board.devices]]
name = "solenoid_valve"
part_number = "SMC-VQ20"
bus = "GPIO"
pin = "PA5"
active_level = "high"
max_duty_percent = 80.0
description = "24V solenoid valve driven via low-side MOSFET"

[[board.devices]]
name = "inlet_temp_sensor"
part_number = "TMP117"
bus = "I2C1"
address = "0x48"
pins = ["PB6", "PB7"]
"""
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            (proj / "fluid_station.ioc").write_text(ioc_content, encoding="utf-8")
            (proj / "mcu-harness.toml").write_text(toml_content, encoding="utf-8")

            with MockProbe() as probe:
                # 开启 GPIOA 时钟；PA5 已配置为 Output (0b01)，但 PA9 仍停留在 MODER=0b11 Analog 复位态
                probe.set_word(RCC_AHB2ENR, 0x00000001)
                moder = (0xABFFFFFF & ~(0x3 << (5 * 2))) | (0x1 << (5 * 2))
                # 确认 PA9 bits[19:18] 为 0b11 Analog
                self.assertEqual((moder >> (9 * 2)) & 0x3, 0b11)
                probe.set_word(GPIOA_MODER, moder)
                probe.set_word(GPIOA_ODR, 1 << 5)

                report = inspect_board_context(proj, probe=probe, svd_device=self.svd)

        # 1. 验证 .ioc 基础信息与外设/DMA/NVIC 解析
        self.assertEqual(report.mcu.normalized_device, "STM32H563ZI")
        self.assertEqual(report.mcu.package, "LQFP144")
        self.assertEqual(report.mcu.hse_value_hz, 8_000_000)
        self.assertEqual(report.mcu.sysclk_hz, 250_000_000)
        self.assertIn("USART1", report.enabled_peripherals)
        self.assertEqual(len(report.dma_mappings), 1)
        self.assertEqual(report.dma_mappings[0].request, "USART1_RX")
        self.assertEqual(len(report.nvic_configs), 1)
        self.assertEqual(report.nvic_configs[0].irq_name, "USART1_IRQn")
        self.assertTrue(report.nvic_configs[0].enabled)
        self.assertEqual(report.nvic_configs[0].preempt_priority, 5)

        # 2. 验证引脚标签与外部物理负载绑定
        pins = report.pins_by_name
        self.assertEqual(pins["PA5"].signal, "GPIO_Output")
        self.assertEqual(pins["PA5"].label, "VALVE_EN")
        self.assertEqual(pins["PA5"].expected_mode, "Output")
        self.assertEqual(pins["PA5"].active_level, "high")
        self.assertEqual(pins["PA5"].max_duty_percent, 80.0)
        self.assertIn("solenoid_valve(SMC-VQ20)", pins["PA5"].connected_devices)

        self.assertEqual(pins["PA9"].signal, "USART1_TX")
        self.assertEqual(pins["PA9"].expected_mode, "Alternate")
        self.assertEqual(pins["PA9"].expected_af, 7)

        self.assertEqual(len(report.board_devices), 2)
        self.assertEqual(report.board_devices[1].address, "0x48")

        # 3. 验证实时对账：PA5 匹配，PA9 真机模式 Analog 与 .ioc Alternate 漂移不符
        self.assertTrue(report.live_checked)
        checks = report.checks_by_pin
        self.assertTrue(checks["PA5"].matched)
        self.assertEqual(checks["PA5"].expected_mode, "Output")
        self.assertEqual(checks["PA5"].live_mode, "Output")
        self.assertIn("PA5", report.matched_pins)

        self.assertFalse(checks["PA9"].matched)
        self.assertEqual(checks["PA9"].expected_mode, "Alternate")
        self.assertEqual(checks["PA9"].live_mode, "Analog")
        self.assertIn("Analog", checks["PA9"].reason)
        self.assertEqual([d.pin for d in report.drifted_pins], ["PA9"])

        drift_findings = [f for f in report.findings if f.code == "PIN_MODE_DRIFT"]
        self.assertEqual(len(drift_findings), 1)
        self.assertIn("PA9", drift_findings[0].title)


class DoctorCliTests(unittest.TestCase):
    def test_cli_peripheral_pins_and_board_subcommands(self) -> None:
        """验证 mcu-doctor peripheral / pins / board 三个 CLI 子命令与 --json 输出。"""
        # 1. mcu-doctor peripheral USART1 --svd ... --mock --json
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = doctor_cli_main(["peripheral", "USART1", "--svd", str(SVD_FIXTURE), "--mock", "--json"])
        self.assertEqual(rc, 0)
        periph_payload = json.loads(buf.getvalue())
        self.assertEqual(periph_payload["peripheral"], "USART1")
        self.assertEqual(periph_payload["highest_severity"], "critical")

        # 2. mcu-doctor pins --svd ... --mock --json
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = doctor_cli_main(["pins", "--svd", str(SVD_FIXTURE), "--mock", "--json"])
        self.assertEqual(rc, 0)
        pins_payload = json.loads(buf.getvalue())
        self.assertEqual(pins_payload["count"], 1)
        self.assertEqual(pins_payload["pins"][0]["pin"], "PA5")

        # 3. mcu-doctor board <proj> --svd ... --live --mock --json
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            (proj / "demo.ioc").write_text(
                "Mcu.Name=STM32H563ZITx\nPA5.Signal=GPIO_Output\nPA9.Signal=USART1_TX\n",
                encoding="utf-8",
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = doctor_cli_main(
                    ["board", str(proj), "--svd", str(SVD_FIXTURE), "--live", "--mock", "--json"]
                )
            self.assertEqual(rc, 0)
            board_payload = json.loads(buf.getvalue())
            self.assertTrue(board_payload["live_checked"])
            self.assertEqual(board_payload["matched_pins"], ["PA5"])
            self.assertEqual([d["pin"] for d in board_payload["drifted_pins"]], ["PA9"])


if __name__ == "__main__":
    unittest.main()
