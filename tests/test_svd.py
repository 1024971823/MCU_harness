"""CMSIS-SVD 解析与寄存器位域解码单元测试。"""

from __future__ import annotations

import gzip
from pathlib import Path

import pytest

from mcu_harness.svd import (
    decode_register,
    find_peripheral,
    find_register,
    load_svd,
)

SVD_FIX = Path(__file__).parent / "fixtures" / "stm32h563_mini.svd"


def test_load_and_derived_peripheral():
    dev = load_svd(SVD_FIX)
    assert dev.name == "STM32H563"
    assert dev.cpu_name == "CM33"

    gpioa = find_peripheral(dev, "gpioa")  # 大小写不敏感
    gpiob = find_peripheral(dev, "GPIOB")
    assert gpioa.base_address == 0x42020000
    assert gpiob.base_address == 0x42020400

    # GPIOB 继承 GPIOA 的寄存器，但绝对地址必须是 GPIOB 的基址
    moder_b = find_register(gpiob, "MODER")
    odr_b = find_register(gpiob, "ODR")
    assert gpiob.reg_address(moder_b) == 0x42020400
    assert gpiob.reg_address(odr_b) == 0x42020414


def test_dim_expansion_and_clusters():
    dev = load_svd(SVD_FIX)
    gpioa = find_peripheral(dev, "GPIOA")

    # AFR[2] + dimIndex=L,H -> AFRL (0x20), AFRH (0x24)
    afrl = find_register(gpioa, "AFRL")
    afrh = find_register(gpioa, "AFRH")
    assert afrl.address_offset == 0x20
    assert afrh.address_offset == 0x24

    # ODR 中 OD%s dim=4 -> OD0..OD3
    odr = find_register(gpioa, "ODR")
    assert [f.name for f in odr.fields] == ["OD0", "OD1", "OD2", "OD3", "OD5"]

    # GPDMA1 cluster C0/C1 -> C0_CR (0x50+0x14=0x64), C1_CR (0xA0+0x14=0xB4)
    dma = find_peripheral(dev, "GPDMA1")
    c0_cr = find_register(dma, "C0_CR")
    c1_cr = find_register(dma, "C1_CR")
    assert dma.reg_address(c0_cr) == 0x40020064
    assert dma.reg_address(c1_cr) == 0x400200B4


def test_decode_register_enums_and_bit_formats():
    dev = load_svd(SVD_FIX)
    gpioa = find_peripheral(dev, "GPIOA")
    moder = find_register(gpioa, "MODER")

    # pin5 设为 Output (0b01 << 10 = 0x400)，pin0 为 Input (0b00)
    dec = decode_register(gpioa, moder, "0x00000400")
    assert dec.address == "0x42020000"
    assert dec.is_reset_value is False
    by_name = {f.name: f for f in dec.fields}
    assert by_name["MODE5"].value == 1
    assert by_name["MODE5"].bit_range == "[11:10]"
    assert "Output" in (by_name["MODE5"].meaning or "")
    assert by_name["MODE0"].value == 0
    assert "Input" in (by_name["MODE0"].meaning or "")

    # bitRange 与 lsb/msb 写法（USART1->CR1）
    usart1 = find_peripheral(dev, "USART1")
    cr1 = find_register(usart1, "CR1")
    dec_u = decode_register(usart1, cr1, 0x0D)  # UE=1, RE=1, TE=1
    u_fields = {f.name: f.value for f in dec_u.fields}
    assert u_fields == {"TE": 1, "RE": 1, "UE": 1}
    assert "USART1->CR1" in dec_u.to_text()


def test_fuzzy_hint_on_missing_symbol():
    dev = load_svd(SVD_FIX)
    with pytest.raises(KeyError, match="GPIOA"):
        find_peripheral(dev, "GPIOZ")
    gpioa = find_peripheral(dev, "GPIOA")
    with pytest.raises(KeyError, match="MODER"):
        find_register(gpioa, "MODE")


def test_load_gzipped_svd(tmp_path):
    gz = tmp_path / "mini.svd.gz"
    gz.write_bytes(gzip.compress(SVD_FIX.read_bytes()))
    dev = load_svd(gz)
    assert dev.name == "STM32H563"
    assert find_peripheral(dev, "USART1").base_address == 0x40013800


def test_find_register_strips_st_peripheral_prefix():
    import xml.etree.ElementTree as ET
    from mcu_harness.svd import parse_svd_root

    xml_text = """
    <device><name>STM32H563</name><peripherals>
      <peripheral>
        <name>GPIOA</name><baseAddress>0x42020000</baseAddress>
        <registers>
          <register><name>GPIOA_MODER</name><addressOffset>0x00</addressOffset></register>
        </registers>
      </peripheral>
      <peripheral>
        <name>USART1</name><baseAddress>0x40013800</baseAddress>
        <registers>
          <register><name>USART_CR1</name><addressOffset>0x00</addressOffset></register>
        </registers>
      </peripheral>
    </peripherals></device>
    """
    dev = parse_svd_root(ET.fromstring(xml_text))
    assert find_register(find_peripheral(dev, "GPIOA"), "MODER").name == "GPIOA_MODER"
    assert find_register(find_peripheral(dev, "GPIOA"), "GPIOA_MODER").name == "GPIOA_MODER"
    assert find_register(find_peripheral(dev, "USART1"), "CR1").name == "USART_CR1"


def test_to_text_trims_long_boilerplate_when_enum_matched():
    dev = load_svd(SVD_FIX)
    gpioa = find_peripheral(dev, "GPIOA")
    moder = find_register(gpioa, "MODER")
    dec = decode_register(gpioa, moder, "0x00000400")
    txt = dec.to_text()
    assert "Output: General purpose output mode" in txt
    assert "Port x configuration I/O pin 5" in txt  # 短描述(<45字符)仍保留

    # 超长模板描述在已有枚举含义时应被省略，避免刷屏
    long_desc = "Port x configuration I/O pin y (y = 15 to 0) " + ("boilerplate " * 10)
    from mcu_harness.svd.decoder import DecodedField, DecodedRegister

    long_dec = DecodedRegister(
        peripheral="GPIOA",
        register="MODER",
        address="0x42020000",
        raw_value=0x400,
        raw_hex="0x00000400",
        size_bits=32,
        fields=[
            DecodedField(
                name="MODE5",
                bit_range="[11:10]",
                lsb=10,
                width=2,
                value=1,
                hex_value="0x1",
                meaning="B_0x1: Output",
                description=long_desc,
            )
        ],
    )
    rendered = long_dec.to_text()
    assert "B_0x1: Output" in rendered
    assert "boilerplate" not in rendered


def test_derived_peripheral_incremental_merge_and_default_access():
    """验证派生外设增量合并寄存器以及外设级默认 access 继承（FIX-08）。"""
    import xml.etree.ElementTree as ET
    from mcu_harness.probe import MockProbe, inspect_peripheral_registers
    from mcu_harness.svd import parse_svd_root

    xml_text = """
    <device><name>TEST</name><peripherals>
      <peripheral>
        <name>BASE_TIM</name>
        <baseAddress>0x40000000</baseAddress>
        <access>read-write</access>
        <registers>
          <register><name>CR1</name><addressOffset>0x00</addressOffset></register>
          <register><name>WO_TRIGGER</name><addressOffset>0x04</addressOffset><access>write-only</access></register>
        </registers>
      </peripheral>
      <peripheral derivedFrom="BASE_TIM">
        <name>EXT_TIM</name>
        <baseAddress>0x40001000</baseAddress>
        <registers>
          <register><name>CR1</name><addressOffset>0x00</addressOffset><description>Overridden CR1</description></register>
          <register><name>EXTRA</name><addressOffset>0x08</addressOffset></register>
        </registers>
      </peripheral>
      <peripheral>
        <name>WO_PORT</name>
        <baseAddress>0x40002000</baseAddress>
        <access>write-only</access>
        <registers>
          <register><name>SET</name><addressOffset>0x00</addressOffset></register>
          <register><name>STAT</name><addressOffset>0x04</addressOffset><access>read-only</access></register>
        </registers>
      </peripheral>
    </peripherals></device>
    """
    dev = parse_svd_root(ET.fromstring(xml_text))
    ext = find_peripheral(dev, "EXT_TIM")
    names = [r.name for r in ext.registers]
    assert names == ["CR1", "WO_TRIGGER", "EXTRA"]
    assert find_register(ext, "CR1").description == "Overridden CR1"
    assert find_register(ext, "EXTRA").access == "read-write"  # 继承外设默认 access

    wo_port = find_peripheral(dev, "WO_PORT")
    assert find_register(wo_port, "SET").access == "write-only"
    assert find_register(wo_port, "STAT").access == "read-only"

    with MockProbe() as probe:
        snap = inspect_peripheral_registers(probe, dev, "WO_PORT", read_all=True)
        assert [r.register for r in snap.registers] == ["STAT"]
