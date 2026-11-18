"""寄存器查找与位域解码（把十六进制硬件值翻译成可读语义）。"""

from __future__ import annotations

import difflib
from dataclasses import asdict, dataclass, field

from .models import SvdDevice, SvdPeripheral, SvdRegister


@dataclass
class DecodedField:
    name: str
    bit_range: str  # 例如 "[11:10]" 或 "[5]"
    lsb: int
    width: int
    value: int
    hex_value: str
    meaning: str | None = None
    access: str | None = None
    description: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DecodedRegister:
    peripheral: str
    register: str
    address: str  # "0x42020000"
    raw_value: int
    raw_hex: str
    size_bits: int
    description: str = ""
    access: str | None = None
    reset_value: str | None = None
    is_reset_value: bool | None = None
    fields: list[DecodedField] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_text(self) -> str:
        """给大模型 / 终端阅读的位域解码表格。"""
        hdr = f"[{self.peripheral}->{self.register}] @ {self.address} = {self.raw_hex}"
        if self.reset_value is not None:
            tag = " (reset)" if self.is_reset_value else f" (reset={self.reset_value})"
            hdr += tag
        lines = [hdr]
        if self.description:
            lines.append(f"  {self.description}")
        if not self.fields:
            lines.append("  (no bitfields defined in SVD)")
            return "\n".join(lines)

        lines.append("")
        lines.append(f"  {'Bits':<9} {'Field':<16} {'Val':<10} {'Meaning / Description'}")
        lines.append(f"  {'-' * 8:<9} {'-' * 15:<16} {'-' * 9:<10} {'-' * 36}")
        for f in self.fields:
            if f.meaning:
                # 已有明确枚举含义时，仅附带简短说明，避免 ST SVD 长段模板描述刷屏
                detail = f.meaning
                if f.description and len(f.description) <= 45 and f.meaning.lower() not in f.description.lower():
                    detail = f"{f.meaning} — {f.description}"
            else:
                desc = f.description or ""
                detail = desc if len(desc) <= 90 else desc[:87] + "..."
            val_str = f"{f.hex_value} ({f.value})" if f.value >= 10 else f"{f.value} ({f.hex_value})"
            lines.append(f"  {f.bit_range:<9} {f.name:<16} {val_str:<10} {detail}")
        return "\n".join(lines)


def parse_int_value(raw: int | str) -> int:
    """支持整数或 '0x...' / '0b...' / 十进制字符串。"""
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        s = raw.strip().replace("_", "")
        return int(s, 0)
    raise ValueError(f"Invalid register value: {raw!r}")


def find_peripheral(device: SvdDevice, name: str) -> SvdPeripheral:
    target = name.strip().upper()
    for p in device.peripherals:
        if p.name.upper() == target:
            return p
    names = [p.name for p in device.peripherals]
    hints = difflib.get_close_matches(target, names, n=5, cutoff=0.4)
    hint_msg = f" Did you mean: {', '.join(hints)}?" if hints else ""
    raise KeyError(f"Peripheral {name!r} not found in SVD {device.name}.{hint_msg}")


def find_register(peripheral: SvdPeripheral, name: str) -> SvdRegister:
    target = name.strip().upper()
    for r in peripheral.registers:
        if r.name.upper() == target:
            return r

    # 兼容 ST 新版 SVD 命名习惯：外设 GPIOA 下的寄存器叫 GPIOA_MODER，允许用户直接查 MODER
    prefixed = f"{peripheral.name.upper()}_{target}"
    for r in peripheral.registers:
        if r.name.upper() == prefixed:
            return r

    # 若剥去单个外设前缀（如 USART_CR1 -> CR1）后唯一命中，也直接返回
    suffix = f"_{target}"
    suffix_matches = [
        r for r in peripheral.registers
        if "_" in r.name and r.name.upper().endswith(suffix)
        and r.name.upper().split("_", 1)[1] == target
    ]
    if len(suffix_matches) == 1:
        return suffix_matches[0]

    names = [r.name for r in peripheral.registers]
    hints = difflib.get_close_matches(target, names, n=5, cutoff=0.4)
    if not hints:
        hints = difflib.get_close_matches(prefixed, names, n=5, cutoff=0.4)
    hint_msg = f" Did you mean: {', '.join(hints)}?" if hints else ""
    raise KeyError(f"Register {name!r} not found in {peripheral.name}.{hint_msg}")


def decode_register(
    peripheral: SvdPeripheral,
    register: SvdRegister,
    raw_value: int | str,
) -> DecodedRegister:
    """按 SVD 位域定义解码一个寄存器数值（高位在前，符合数据手册习惯）。"""
    val = parse_int_value(raw_value)
    mask_width = (1 << register.size) - 1
    val_masked = val & mask_width
    hex_digits = max(2, (register.size + 3) // 4)

    decoded_fields: list[DecodedField] = []
    # 从高位到低位排序，和参考手册里的寄存器位图顺序一致
    for f in sorted(register.fields, key=lambda x: x.bit_offset, reverse=True):
        fv = f.extract(val_masked)
        ev = f.lookup_enum(fv)
        meaning = None
        if ev is not None:
            meaning = f"{ev.name}: {ev.description}" if ev.description and ev.description != ev.name else ev.name
        f_hex_digits = max(1, (f.bit_width + 3) // 4)
        decoded_fields.append(
            DecodedField(
                name=f.name,
                bit_range=f.bit_range_str,
                lsb=f.bit_offset,
                width=f.bit_width,
                value=fv,
                hex_value=f"0x{fv:0{f_hex_digits}X}",
                meaning=meaning,
                access=f.access or register.access,
                description=f.description,
            )
        )

    reset_hex = f"0x{register.reset_value:0{hex_digits}X}" if register.reset_value is not None else None
    is_reset = (val_masked == register.reset_value) if register.reset_value is not None else None

    return DecodedRegister(
        peripheral=peripheral.name,
        register=register.name,
        address=f"0x{peripheral.reg_address(register):08X}",
        raw_value=val_masked,
        raw_hex=f"0x{val_masked:0{hex_digits}X}",
        size_bits=register.size,
        description=register.description,
        access=register.access,
        reset_value=reset_hex,
        is_reset_value=is_reset,
        fields=decoded_fields,
    )
