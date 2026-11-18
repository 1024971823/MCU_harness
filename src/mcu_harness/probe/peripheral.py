"""把 DebugProbe 硬件读取与 CMSIS-SVD 位域解码桥接在一起。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from mcu_harness.svd import DecodedRegister, SvdDevice, decode_register, find_peripheral, find_register

from .base import DebugProbe, ProbeError


@dataclass
class PeripheralSnapshot:
    """一次读取多个寄存器的结构化快照。"""

    peripheral: str
    base_address: str
    registers: list[DecodedRegister] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)  # 某些寄存器读取失败（如权限/时钟未使能）

    def to_dict(self) -> dict:
        return asdict(self)

    def to_text(self) -> str:
        lines = [f"[{self.peripheral}] @ {self.base_address} (read {len(self.registers)} register(s))"]
        for dec in self.registers:
            lines.append("")
            lines.append(dec.to_text())
        if self.errors:
            lines.append("\nRead errors:")
            for rname, err in self.errors.items():
                lines.append(f"  {rname}: {err}")
        return "\n".join(lines)


def inspect_peripheral_registers(
    probe: DebugProbe,
    device: SvdDevice,
    peripheral_name: str,
    *,
    register_name: str | None = None,
    registers: list[str] | None = None,
    read_all: bool = False,
) -> DecodedRegister | PeripheralSnapshot:
    """读取目标板外设寄存器并用 SVD 解码。

    说明：
        * 指定单个 register_name 时，直接返回 DecodedRegister。
        * 指定 registers 列表或 read_all=True 时，批量读取并返回 PeripheralSnapshot。
        * read_all=True 时会自动跳过 write-only 寄存器，且单个寄存器读取失败不中断整批读取。
    """
    periph = find_peripheral(device, peripheral_name)

    if register_name and not registers and not read_all:
        reg = find_register(periph, register_name)
        if reg.access == "write-only":
            raise ProbeError(f"Register {periph.name}->{reg.name} is write-only in SVD")
        raw_val = probe.read_register_sized(periph.reg_address(reg), reg.size)
        return decode_register(periph, reg, raw_val)

    target_regs = []
    if registers:
        for rn in registers:
            target_regs.append(find_register(periph, rn))
    elif register_name:
        target_regs.append(find_register(periph, register_name))
    elif read_all:
        target_regs = [r for r in periph.registers if r.access != "write-only"]
    else:
        raise ValueError("Specify register_name, registers, or read_all=True")

    snap = PeripheralSnapshot(
        peripheral=periph.name,
        base_address=f"0x{periph.base_address:08X}",
    )
    for reg in target_regs:
        if reg.access == "write-only":
            snap.errors[reg.name] = "skipped write-only register"
            continue
        try:
            raw_val = probe.read_register_sized(periph.reg_address(reg), reg.size)
            snap.registers.append(decode_register(periph, reg, raw_val))
        except ProbeError as e:
            snap.errors[reg.name] = str(e)
    return snap
