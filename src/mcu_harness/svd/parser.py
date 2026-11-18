"""CMSIS-SVD XML 解析器（纯标准库，支持 STM32 SVD 常见特性）。

支持：
    * `.svd` 与 `.svd.gz` 压缩文件
    * `<peripheral derivedFrom="...">`（如 GPIOB 继承 GPIOA）
    * `<register derivedFrom="...">` 与 `<enumeratedValues derivedFrom="...">`
    * `<dim>` / `<dimIncrement>` / `<dimIndex>` 数组展开（如 AFR[2] / BKP%sR）
    * `<cluster>` 嵌套寄存器组（如 STM32H5 GPDMA 通道）
    * 三种位域定位写法：<bitOffset>+<bitWidth>、<lsb>+<msb>、<bitRange>[msb:lsb]
    * 按文件绝对路径 + mtime 缓存，避免 MCP 反复调用时重复解析数 MB 的 XML
"""

from __future__ import annotations

import gzip
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .models import SvdDevice, SvdEnumeratedValue, SvdField, SvdRegister, SvdPeripheral

_RE_BIT_RANGE = re.compile(r"^\[(\d+)\s*:\s*(\d+)\]$")
_RE_WS = re.compile(r"\s+")

# 缓存：(resolved_path, mtime_ns) -> SvdDevice
_CACHE: dict[tuple[str, int], SvdDevice] = {}


def load_svd(path: str | os.PathLike) -> SvdDevice:
    """加载并解析 .svd（或 .svd.gz）文件，带内存缓存。"""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"SVD file not found: {p}")

    key = (str(p), p.stat().st_mtime_ns)
    if key in _CACHE:
        return _CACHE[key]

    if p.suffix.lower() == ".gz":
        with gzip.open(p, "rt", encoding="utf-8", errors="replace") as f:
            root = ET.fromstring(f.read())
    else:
        root = ET.parse(p).getroot()

    device = parse_svd_root(root)
    _CACHE[key] = device
    return device


def parse_svd_root(root: ET.Element) -> SvdDevice:
    """从 XML Element 根节点解析 SvdDevice（方便单元测试直接喂 XML 字符串）。"""
    dev_name = _text(root, "name") or "UnknownDevice"
    dev_desc = _clean_desc(_text(root, "description"))
    cpu_name = _text(root.find("cpu"), "name") if root.find("cpu") is not None else ""

    raw_periphs = root.findall("./peripherals/peripheral")
    by_name: dict[str, ET.Element] = {}
    for pe in raw_periphs:
        pname = _text(pe, "name")
        if pname:
            by_name[pname] = pe

    # 先按定义顺序解析，遇到 derivedFrom 时递归解析模板外设
    parsed_periphs: dict[str, SvdPeripheral] = {}
    ordered_names: list[str] = []

    def resolve_peripheral(pe: ET.Element, stack: set[str]) -> SvdPeripheral:
        pname = _text(pe, "name")
        if pname in parsed_periphs:
            return parsed_periphs[pname]
        if pname in stack:
            raise ValueError(f"Circular peripheral derivedFrom: {pname}")

        base_periph: SvdPeripheral | None = None
        derived = pe.attrib.get("derivedFrom")
        if derived:
            if derived not in by_name:
                raise ValueError(f"Peripheral {pname!r} derivedFrom unknown {derived!r}")
            stack.add(pname)
            base_periph = resolve_peripheral(by_name[derived], stack)
            stack.remove(pname)

        base_addr = _parse_int(_text(pe, "baseAddress"))
        if base_addr is None and base_periph is not None:
            base_addr = base_periph.base_address
        if base_addr is None:
            base_addr = 0

        desc = _clean_desc(_text(pe, "description")) or (base_periph.description if base_periph else "")
        group = _text(pe, "groupName") or (base_periph.group_name if base_periph else "")

        regs_el = pe.find("registers")
        if regs_el is not None:
            regs = _parse_registers_container(regs_el, prefix="")
        elif base_periph is not None:
            regs = base_periph.registers
        else:
            regs = ()

        p_obj = SvdPeripheral(
            name=pname,
            base_address=base_addr,
            description=desc,
            group_name=group,
            registers=regs,
        )
        parsed_periphs[pname] = p_obj
        return p_obj

    for pe in raw_periphs:
        pname = _text(pe, "name")
        if not pname:
            continue
        ordered_names.append(pname)
        resolve_peripheral(pe, set())

    return SvdDevice(
        name=dev_name,
        description=dev_desc,
        cpu_name=cpu_name,
        peripherals=tuple(parsed_periphs[n] for n in ordered_names),
    )


# ---------------------------------------------------------------------------
# 寄存器 / 簇 / 位域解析
# ---------------------------------------------------------------------------


def _parse_registers_container(container: ET.Element, prefix: str, base_offset: int = 0) -> tuple[SvdRegister, ...]:
    """解析 <registers> 或 <cluster>，支持嵌套 <cluster> 与 <dim> 展开。"""
    regs: list[SvdRegister] = []
    by_name: dict[str, SvdRegister] = {}
    enum_registry: dict[str, tuple[SvdEnumeratedValue, ...]] = {}

    for child in container:
        if child.tag == "register":
            for reg in _parse_register_element(child, prefix, base_offset, by_name, enum_registry):
                regs.append(reg)
                by_name[reg.name] = reg
        elif child.tag == "cluster":
            regs.extend(_parse_cluster_element(child, prefix, base_offset))

    regs.sort(key=lambda r: (r.address_offset, r.name))
    return tuple(regs)


def _parse_cluster_element(el: ET.Element, prefix: str, base_offset: int) -> list[SvdRegister]:
    cname_tpl = _text(el, "name") or ""
    offset = base_offset + (_parse_int(_text(el, "addressOffset")) or 0)
    indices, dim_inc = _expand_dim(el)
    out: list[SvdRegister] = []
    for i, idx in enumerate(indices):
        cname = _apply_dim_name(cname_tpl, idx)
        sub_prefix = f"{prefix}{cname}_" if cname else prefix
        out.extend(_parse_registers_container(el, prefix=sub_prefix, base_offset=offset + i * dim_inc))
    return out


def _parse_register_element(
    el: ET.Element,
    prefix: str,
    base_offset: int,
    by_name: dict[str, SvdRegister],
    enum_registry: dict[str, tuple[SvdEnumeratedValue, ...]],
) -> list[SvdRegister]:
    name_tpl = _text(el, "name")
    if not name_tpl:
        return []

    derived = el.attrib.get("derivedFrom")
    tmpl = by_name.get(f"{prefix}{derived}") or by_name.get(derived or "") if derived else None

    offset = _parse_int(_text(el, "addressOffset"))
    if offset is None:
        offset = tmpl.address_offset if tmpl else 0
    else:
        offset += base_offset

    size = _parse_int(_text(el, "size")) or (tmpl.size if tmpl else 32)
    desc = _clean_desc(_text(el, "description")) or (tmpl.description if tmpl else "")
    access = _text(el, "access") or (tmpl.access if tmpl else None)
    reset_val = _parse_int(_text(el, "resetValue"))
    if reset_val is None and tmpl is not None:
        reset_val = tmpl.reset_value

    fields_el = el.find("fields")
    if fields_el is not None:
        fields = _parse_fields(fields_el, enum_registry)
    elif tmpl is not None:
        fields = tmpl.fields
    else:
        fields = ()

    indices, dim_inc = _expand_dim(el)
    out: list[SvdRegister] = []
    for i, idx in enumerate(indices):
        rname = f"{prefix}{_apply_dim_name(name_tpl, idx)}"
        out.append(
            SvdRegister(
                name=rname,
                address_offset=offset + i * dim_inc,
                size=size,
                description=desc,
                access=access,
                reset_value=reset_val,
                fields=fields,
            )
        )
    return out


def _parse_fields(
    fields_el: ET.Element,
    enum_registry: dict[str, tuple[SvdEnumeratedValue, ...]],
) -> tuple[SvdField, ...]:
    fields: list[SvdField] = []
    for fe in fields_el.findall("field"):
        name_tpl = _text(fe, "name")
        if not name_tpl:
            continue
        bit_offset, bit_width = _parse_bit_pos(fe)
        desc = _clean_desc(_text(fe, "description"))
        access = _text(fe, "access")
        enums = _parse_enums(fe, enum_registry)

        indices, dim_inc = _expand_dim(fe)
        for i, idx in enumerate(indices):
            fname = _apply_dim_name(name_tpl, idx)
            fields.append(
                SvdField(
                    name=fname,
                    bit_offset=bit_offset + i * dim_inc,
                    bit_width=bit_width,
                    description=desc,
                    access=access,
                    enumerated_values=enums,
                )
            )
    fields.sort(key=lambda f: f.bit_offset)
    return tuple(fields)


def _parse_bit_pos(fe: ET.Element) -> tuple[int, int]:
    bo = _parse_int(_text(fe, "bitOffset"))
    bw = _parse_int(_text(fe, "bitWidth"))
    if bo is not None:
        return bo, bw if bw is not None else 1

    lsb = _parse_int(_text(fe, "lsb"))
    msb = _parse_int(_text(fe, "msb"))
    if lsb is not None and msb is not None and msb >= lsb:
        return lsb, msb - lsb + 1

    br = _text(fe, "bitRange")
    m = _RE_BIT_RANGE.match(br)
    if m:
        msb_i, lsb_i = int(m.group(1)), int(m.group(2))
        return lsb_i, max(1, msb_i - lsb_i + 1)

    return 0, 1


def _parse_enums(
    fe: ET.Element,
    enum_registry: dict[str, tuple[SvdEnumeratedValue, ...]],
) -> tuple[SvdEnumeratedValue, ...]:
    ev_container = fe.find("enumeratedValues")
    if ev_container is None:
        return ()

    derived = ev_container.attrib.get("derivedFrom")
    if derived and derived in enum_registry:
        return enum_registry[derived]

    items: list[SvdEnumeratedValue] = []
    for ev in ev_container.findall("enumeratedValue"):
        val = _parse_int(_text(ev, "value"))
        if val is None:
            continue
        ename = _text(ev, "name") or f"0x{val:X}"
        edesc = _clean_desc(_text(ev, "description"))
        items.append(SvdEnumeratedValue(name=ename, value=val, description=edesc))

    items.sort(key=lambda x: x.value)
    result = tuple(items)
    ev_name = _text(ev_container, "name")
    if ev_name and result:
        enum_registry[ev_name] = result
    return result


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _expand_dim(el: ET.Element) -> tuple[list[str], int]:
    dim = _parse_int(_text(el, "dim"))
    if not dim or dim <= 0:
        return [""], 0

    dim_inc = _parse_int(_text(el, "dimIncrement")) or 0
    raw_idx = _text(el, "dimIndex")
    if not raw_idx:
        return [str(i) for i in range(dim)], dim_inc

    indices: list[str] = []
    for part in raw_idx.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            if lo.isdigit() and hi.isdigit():
                a, b = int(lo), int(hi)
                indices.extend(str(k) for k in range(a, b + 1))
                continue
            if len(lo) == 1 and len(hi) == 1:
                indices.extend(chr(c) for c in range(ord(lo), ord(hi) + 1))
                continue
        if part:
            indices.append(part)
    while len(indices) < dim:
        indices.append(str(len(indices)))
    return indices[:dim], dim_inc


def _apply_dim_name(template: str, idx: str) -> str:
    if not idx:
        return template.replace("[%s]", "").replace("%s", "")
    if "[%s]" in template:
        return template.replace("[%s]", idx)
    return template.replace("%s", idx)


def _text(parent: ET.Element | None, tag: str) -> str:
    if parent is None:
        return ""
    child = parent.find(tag)
    return child.text.strip() if child is not None and child.text else ""


def _clean_desc(s: str) -> str:
    return _RE_WS.sub(" ", s).strip()


def _parse_int(s: str) -> int | None:
    if not s:
        return None
    s = s.strip()
    if s.startswith("#"):
        # CMSIS-SVD 允许 #0101 二进制写法（若含通配符 x，无法转成单一整数）
        bits = s[1:].replace("_", "")
        if set(bits) <= {"0", "1"} and bits:
            return int(bits, 2)
        return None
    try:
        return int(s, 0)
    except ValueError:
        return None
