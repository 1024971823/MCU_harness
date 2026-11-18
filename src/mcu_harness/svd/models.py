"""CMSIS-SVD 数据模型（与具体 XML 解析库解耦）。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class SvdEnumeratedValue:
    """位域的枚举含义（例如 MODER=0b01 -> General purpose output mode）。"""

    name: str
    value: int
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SvdField:
    """寄存器中的一个位域。"""

    name: str
    bit_offset: int  # LSB 位置 (0..31)
    bit_width: int
    description: str = ""
    access: str | None = None
    enumerated_values: tuple[SvdEnumeratedValue, ...] = ()

    @property
    def msb(self) -> int:
        return self.bit_offset + self.bit_width - 1

    @property
    def bit_range_str(self) -> str:
        if self.bit_width == 1:
            return f"[{self.bit_offset}]"
        return f"[{self.msb}:{self.bit_offset}]"

    @property
    def mask(self) -> int:
        return ((1 << self.bit_width) - 1) << self.bit_offset

    def extract(self, reg_value: int) -> int:
        return (reg_value >> self.bit_offset) & ((1 << self.bit_width) - 1)

    def lookup_enum(self, field_value: int) -> SvdEnumeratedValue | None:
        for ev in self.enumerated_values:
            if ev.value == field_value:
                return ev
        return None


@dataclass(frozen=True)
class SvdRegister:
    """外设中的一个寄存器（不含外设基地址，支持 derivedFrom 复用）。"""

    name: str
    address_offset: int
    size: int = 32
    description: str = ""
    access: str | None = None
    reset_value: int | None = None
    fields: tuple[SvdField, ...] = ()


@dataclass(frozen=True)
class SvdPeripheral:
    """片上外设（例如 GPIOA / RCC / USART1）。"""

    name: str
    base_address: int
    description: str = ""
    group_name: str = ""
    registers: tuple[SvdRegister, ...] = ()

    def reg_address(self, reg: SvdRegister) -> int:
        return self.base_address + reg.address_offset


@dataclass(frozen=True)
class SvdDevice:
    """整颗芯片的 SVD 描述。"""

    name: str
    description: str = ""
    cpu_name: str = ""
    peripherals: tuple[SvdPeripheral, ...] = ()
