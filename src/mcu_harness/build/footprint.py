"""ELF 节表/符号表与 GNU ld `.map` 固件内存版图、膨胀归因与 DMA 安全静态审计器。

核心能力（纯标准库实现，不依赖 readelf / nm / size 二进制）：
    1. 解析 ELF32 / ELF64 节表（Section Headers）与符号表（.symtab / .dynsym）：
       - 统计 `.text`、`.rodata`、`.ARM.exidx`、`.data`、`.bss`、`.ccmram`、
         `.dtcmram`、`.ram_d1/d2/d3`、`._user_heap_stack` 等关键节大小；
       - 汇总 Flash 总占用（代码 + 只读常量 + 初始化数据加载镜像）与 RAM 总占用（静态读写数据 + 零初始化段）；
       - 提取 Top N Flash 最大函数符号与 Top N RAM 最大全局/静态对象符号；
       - 自动识别双精度浮点 `printf`（`_dtoa_r` / `_svfprintf_r` / `__aeabi_ddiv`）、
         Newlib 动态堆（`_malloc_r`）、C++ 异常等固件膨胀元凶。
    2. 可选 GNU ld `.map` 深度归因：
       - 解析 `Memory Configuration` 分区（`FLASH`、`RAM`、`DTCMRAM`、`CCMRAM` 等）的
         Origin、Length、已用字节与真实使用百分比；
       - 解析 `Linker script and memory map` 中各链接单元（如 `build/service_water.o`、
         `libc_nano.a(...)`）贡献，并按工程目录层级（`app/`、`service/`、`bsp/`、
         `drivers/`、`toolchain_lib` 等）聚合 Flash / RAM 权重。
    3. 嵌入式专属内存风险静态审计（Hazards）：
       - DMA 缓冲区落入紧耦合不可达内存（CCMRAM `0x10000000..0x1000FFFF`、
         STM32H7 DTCM `0x20000000..0x2001FFFF`）告警；
       - DMA 缓冲区（`>= 32 B`）未按 4 字节 / 32 字节 Cache-Line 对齐告警；
       - 基于 `_estack` 与 RAM 最高已用地址估算主栈剩余净空，低于阈值发出警告。
"""

from __future__ import annotations

import os
import re
import stat
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_ELF_MAGIC = b"\x7fELF"

# ELF Section Types
_SHT_NULL = 0
_SHT_PROGBITS = 1
_SHT_SYMTAB = 2
_SHT_STRTAB = 3
_SHT_NOBITS = 8
_SHT_DYNSYM = 11
_SHT_INIT_ARRAY = 14
_SHT_FINI_ARRAY = 15
_SHT_PREINIT_ARRAY = 16
_SHT_ARM_EXIDX = 0x70000001

# ELF Section Flags
_SHF_WRITE = 0x1
_SHF_ALLOC = 0x2
_SHF_EXECINSTR = 0x4

# ELF Symbol Types & Special Section Indices
_STT_NOTYPE = 0
_STT_OBJECT = 1
_STT_FUNC = 2
_STT_SECTION = 3
_STT_FILE = 4
_STT_COMMON = 5
_SHN_UNDEF = 0
_SHN_ABS = 0xFFF1

_SYM_TYPE_NAMES = {
    _STT_NOTYPE: "notype",
    _STT_OBJECT: "object",
    _STT_FUNC: "func",
    _STT_SECTION: "section",
    _STT_FILE: "file",
    _STT_COMMON: "object",
}

_SKIP_SCAN_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "CMakeFiles",
    ".pytest_cache",
}
_ELF_SUFFIXES = (".elf", ".axf", ".out")
_SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".s", ".S", ".asm"}

_FAST_RAM_PREFIXES = (
    ".ccmram",
    ".dtcmram",
    ".itcmram",
    ".ram_d1",
    ".ram_d2",
    ".ram_d3",
    ".sram1",
    ".sram2",
    ".sram3",
    ".sram4",
    ".backup_sram",
    ".fast_ram",
)
_HEAP_STACK_NAMES = (
    "._user_heap_stack",
    ".heap",
    ".stack",
    ".stack_dummy",
    ".heap_stack",
)
_PURE_STACK_RESERVATION_NAMES = (
    "._user_heap_stack",
    ".stack",
    ".stack_dummy",
)
_NON_ALLOC_MAP_PREFIXES = (
    ".debug",
    ".comment",
    ".ARM.attributes",
    ".symtab",
    ".strtab",
    ".shstrtab",
    ".note.GNU-stack",
    "/DISCARD/",
)

_TOOLCHAIN_ARCHIVE_PREFIXES = (
    "libc.",
    "libc_",
    "libc-",
    "libg.",
    "libg_",
    "libm.",
    "libm_",
    "libgcc",
    "libnosys",
    "libstdc++",
    "libsupc++",
    "librdimon",
    "libgloss",
)
_CRT_OBJ_NAMES = {
    "crt0.o",
    "crt1.o",
    "scrt1.o",
    "gcrt1.o",
    "crti.o",
    "crtn.o",
    "crtbegin.o",
    "crtbegins.o",
    "crtbeginT.o".lower(),
    "crtend.o",
    "crtends.o",
}

_DEFAULT_MIN_STACK_HEADROOM = 1024


class FootprintError(RuntimeError):
    """ELF 或 .map 内存版图解析失败。"""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SectionInfo:
    name: str
    address: int
    size: int
    sec_type: str
    category: str  # "code" | "rodata" | "arm_exidx" | "data" | "bss" | "fast_ram" | "heap_stack" | "other"
    in_flash: bool
    in_ram: bool
    align: int = 1

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["address_hex"] = f"0x{self.address:08X}"
        return d


@dataclass(frozen=True)
class SymbolFootprint:
    name: str
    address: int
    size: int
    section: str
    sym_type: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["address_hex"] = f"0x{self.address:08X}"
        return d


@dataclass(frozen=True)
class BloatHint:
    category: str
    symbols: list[str]
    estimated_bytes: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MapRegionUsage:
    name: str
    origin: int
    length: int
    used_bytes: int
    free_bytes: int
    percent: float
    attributes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["origin_hex"] = f"0x{self.origin:08X}"
        d["length_hex"] = f"0x{self.length:08X}"
        return d


@dataclass(frozen=True)
class ModuleContribution:
    module: str
    layer: str
    flash_bytes: int
    ram_bytes: int
    text_bytes: int = 0
    rodata_bytes: int = 0
    data_bytes: int = 0
    bss_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LayerContribution:
    layer: str
    flash_bytes: int
    ram_bytes: int
    flash_percent: float
    ram_percent: float
    module_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MemoryHazard:
    code: str
    severity: str  # "error" | "warning"
    symbol: str
    address: int
    size: int
    section: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["address_hex"] = f"0x{self.address:08X}"
        return d


@dataclass
class FootprintReport:
    elf_path: str | None
    map_path: str | None
    flash_bytes: int
    ram_bytes: int
    text_bytes: int
    rodata_bytes: int
    data_bytes: int
    bss_bytes: int
    key_sections: dict[str, int] = field(default_factory=dict)
    sections: list[SectionInfo] = field(default_factory=list)
    top_flash_symbols: list[SymbolFootprint] = field(default_factory=list)
    top_ram_symbols: list[SymbolFootprint] = field(default_factory=list)
    bloat_hints: list[BloatHint] = field(default_factory=list)
    map_regions: list[MapRegionUsage] = field(default_factory=list)
    module_contributions: list[ModuleContribution] = field(default_factory=list)
    layer_contributions: list[LayerContribution] = field(default_factory=list)
    hazards: list[MemoryHazard] = field(default_factory=list)
    estack_address: int | None = None
    ram_highest_used: int | None = None
    stack_headroom_bytes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "elf_path": self.elf_path,
            "map_path": self.map_path,
            "flash_bytes": self.flash_bytes,
            "ram_bytes": self.ram_bytes,
            "text_bytes": self.text_bytes,
            "rodata_bytes": self.rodata_bytes,
            "data_bytes": self.data_bytes,
            "bss_bytes": self.bss_bytes,
            "key_sections": dict(self.key_sections),
            "sections": [s.to_dict() for s in self.sections],
            "top_flash_symbols": [s.to_dict() for s in self.top_flash_symbols],
            "top_ram_symbols": [s.to_dict() for s in self.top_ram_symbols],
            "bloat_hints": [b.to_dict() for b in self.bloat_hints],
            "map_regions": [r.to_dict() for r in self.map_regions],
            "module_contributions": [m.to_dict() for m in self.module_contributions],
            "layer_contributions": [l.to_dict() for l in self.layer_contributions],
            "hazards": [h.to_dict() for h in self.hazards],
            "estack_address": f"0x{self.estack_address:08X}" if self.estack_address is not None else None,
            "ram_highest_used": f"0x{self.ram_highest_used:08X}" if self.ram_highest_used is not None else None,
            "stack_headroom_bytes": self.stack_headroom_bytes,
        }

    def to_text(self) -> str:
        """生成对齐美观、方便大模型与工程师快速阅读的内存版图与风险报告。"""
        out: list[str] = []
        target_label = Path(self.elf_path).name if self.elf_path else (Path(self.map_path).name if self.map_path else "unknown")
        out.append(f"[footprint] {target_label}")
        if self.elf_path:
            out.append(f"  ELF:   {self.elf_path}")
        if self.map_path:
            out.append(f"  MAP:   {self.map_path}")
        out.append(
            f"  Flash: {self.flash_bytes:>9} B  (text={self.text_bytes} B, rodata={self.rodata_bytes} B, init_data={self.data_bytes} B)"
        )
        out.append(
            f"  RAM:   {self.ram_bytes:>9} B  (data={self.data_bytes} B, bss/noinit={self.bss_bytes} B)"
        )
        if self.stack_headroom_bytes is not None and self.estack_address is not None:
            used_hi = f"0x{self.ram_highest_used:08X}" if self.ram_highest_used is not None else "?"
            out.append(
                f"  Stack: {self.stack_headroom_bytes:>9} B headroom  (ram_end={used_hi} .. _estack=0x{self.estack_address:08X})"
            )

        if self.map_regions:
            out.append("\nMemory Regions (.map):")
            out.append(f"  {'Region':<12} {'Origin':<12} {'Used / Length':>23}  {'Usage':>7}  {'Attr'}")
            for r in self.map_regions:
                pair = f"{r.used_bytes} / {r.length} B"
                out.append(
                    f"  {r.name:<12} 0x{r.origin:08X}   {pair:>23}  {r.percent:>6.2f}%  {r.attributes}"
                )

        if self.sections:
            out.append("\nAllocated ELF Sections:")
            out.append(f"  {'Section':<22} {'Address':<12} {'Size (B)':>10}  {'Domain':<11} {'Category'}")
            for s in self.sections:
                if s.size == 0:
                    continue
                if s.in_flash and s.in_ram:
                    dom = "FLASH+RAM"
                elif s.in_flash:
                    dom = "FLASH"
                elif s.in_ram:
                    dom = "RAM"
                else:
                    dom = "-"
                out.append(f"  {s.name:<22} 0x{s.address:08X}   {s.size:>10}  {dom:<11} {s.category}")

        if self.layer_contributions:
            out.append("\nLayer Attribution (.map):")
            out.append(f"  {'Layer':<16} {'Flash (B)':>11} {'Flash%':>8}  {'RAM (B)':>11} {'RAM%':>8}  {'Units':>5}")
            for lc in self.layer_contributions:
                out.append(
                    f"  {lc.layer:<16} {lc.flash_bytes:>11} {lc.flash_percent:>7.1f}%  "
                    f"{lc.ram_bytes:>11} {lc.ram_percent:>7.1f}%  {lc.module_count:>5}"
                )

        if self.top_flash_symbols:
            out.append(f"\nTop Flash Functions ({len(self.top_flash_symbols)}):")
            for sym in self.top_flash_symbols:
                out.append(f"  {sym.size:>8} B  0x{sym.address:08X}  {sym.section:<14} {sym.name}")

        if self.top_ram_symbols:
            out.append(f"\nTop RAM Objects ({len(self.top_ram_symbols)}):")
            for sym in self.top_ram_symbols:
                out.append(f"  {sym.size:>8} B  0x{sym.address:08X}  {sym.section:<14} {sym.name}")

        if self.bloat_hints:
            out.append(f"\nBloat Hints ({len(self.bloat_hints)}):")
            for bh in self.bloat_hints:
                syms = ", ".join(bh.symbols[:5])
                size_note = f" (~{bh.estimated_bytes} B)" if bh.estimated_bytes > 0 else ""
                out.append(f"  [{bh.category}]{size_note} symbols=[{syms}]: {bh.message}")

        if self.hazards:
            out.append(f"\nMemory Hazards ({len(self.hazards)}):")
            for hz in self.hazards:
                out.append(
                    f"  [{hz.severity.upper()}] {hz.code} ({hz.symbol} @ 0x{hz.address:08X}, {hz.size} B, {hz.section}): {hz.message}"
                )

        return "\n".join(out)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def analyze_footprint(
    elf_path: str | os.PathLike | None = None,
    *,
    map_path: str | os.PathLike | None = None,
    project_dir: str | os.PathLike | None = None,
    top_n: int = 15,
) -> FootprintReport:
    """分析固件 ELF 节表/符号表与可选 GNU ld `.map`，生成内存版图与 DMA/栈安全审计报告。"""
    if top_n <= 0:
        raise ValueError(f"top_n must be > 0, got {top_n}")

    proj = Path(project_dir).expanduser().resolve() if project_dir is not None else None
    resolved_elf, resolved_map = _resolve_inputs(elf_path=elf_path, map_path=map_path, project=proj)

    if proj is None and resolved_elf is not None:
        proj = _guess_project_root(resolved_elf.parent)
    elif proj is None and resolved_map is not None:
        proj = _guess_project_root(resolved_map.parent)

    sections: list[SectionInfo] = []
    raw_symbols: list[_RawSymbol] = []
    if resolved_elf is not None:
        sections, raw_symbols = _parse_elf_sections_and_symbols(resolved_elf)

    # 汇总 ELF 关键节与 Flash/RAM 占用
    text_bytes = 0
    rodata_bytes = 0
    data_bytes = 0
    bss_bytes = 0
    key_sections: dict[str, int] = {}

    for sec in sections:
        if not (sec.in_flash or sec.in_ram):
            continue
        bucket = _canonical_section_bucket(sec.name)
        key_sections[bucket] = key_sections.get(bucket, 0) + sec.size

        if sec.in_flash and sec.in_ram:
            data_bytes += sec.size
        elif sec.in_flash:
            if sec.category == "code":
                text_bytes += sec.size
            else:
                rodata_bytes += sec.size
        elif sec.in_ram:
            bss_bytes += sec.size

    flash_bytes = text_bytes + rodata_bytes + data_bytes
    ram_bytes = data_bytes + bss_bytes

    # Top N 符号与膨胀提示
    top_flash, top_ram = _extract_top_symbols(sections, raw_symbols, top_n=top_n)
    bloat_hints = _detect_bloat_hints(raw_symbols)

    # 可选 .map 深度解析
    map_regions: list[MapRegionUsage] = []
    module_contribs: list[ModuleContribution] = []
    layer_contribs: list[LayerContribution] = []
    if resolved_map is not None:
        elf_sec_lookup = {s.name: s for s in sections}
        map_regions, module_contribs, layer_contribs = _parse_gnu_map(
            resolved_map,
            project_dir=proj,
            elf_sections=elf_sec_lookup,
            top_modules=max(top_n * 2, 30),
        )
        if resolved_elf is None and module_contribs:
            text_bytes = sum(m.text_bytes for m in module_contribs)
            rodata_bytes = sum(m.rodata_bytes for m in module_contribs)
            data_bytes = sum(m.data_bytes for m in module_contribs)
            bss_bytes = sum(m.bss_bytes for m in module_contribs)
            flash_bytes = text_bytes + rodata_bytes + data_bytes
            ram_bytes = data_bytes + bss_bytes

    # 嵌入式静态内存风险审计（DMA + 栈净空）
    hazards, estack_addr, ram_hi, stack_headroom = _audit_hazards(
        sections=sections,
        symbols=raw_symbols,
        map_regions=map_regions,
    )

    return FootprintReport(
        elf_path=str(resolved_elf) if resolved_elf is not None else None,
        map_path=str(resolved_map) if resolved_map is not None else None,
        flash_bytes=flash_bytes,
        ram_bytes=ram_bytes,
        text_bytes=text_bytes,
        rodata_bytes=rodata_bytes,
        data_bytes=data_bytes,
        bss_bytes=bss_bytes,
        key_sections=key_sections,
        sections=sections,
        top_flash_symbols=top_flash,
        top_ram_symbols=top_ram,
        bloat_hints=bloat_hints,
        map_regions=map_regions,
        module_contributions=module_contribs,
        layer_contributions=layer_contribs,
        hazards=hazards,
        estack_address=estack_addr,
        ram_highest_used=ram_hi,
        stack_headroom_bytes=stack_headroom,
    )


# ---------------------------------------------------------------------------
# 输入路径解析
# ---------------------------------------------------------------------------


def _resolve_inputs(
    *,
    elf_path: str | os.PathLike | None,
    map_path: str | os.PathLike | None,
    project: Path | None,
) -> tuple[Path | None, Path | None]:
    resolved_elf: Path | None = None
    resolved_map: Path | None = None

    if elf_path is not None:
        p = Path(elf_path).expanduser()
        if not p.is_absolute() and project is not None and (project / p).is_file():
            p = project / p
        resolved_elf = p.resolve()
        if not resolved_elf.is_file():
            raise FileNotFoundError(f"ELF file not found: {resolved_elf}")

    if map_path is not None:
        m = Path(map_path).expanduser()
        if not m.is_absolute() and project is not None and (project / m).is_file():
            m = project / m
        resolved_map = m.resolve()
        if not resolved_map.is_file():
            raise FileNotFoundError(f"Linker map file not found: {resolved_map}")

    if resolved_elf is None:
        if project is not None and project.is_dir():
            resolved_elf = _find_newest_file(project, _ELF_SUFFIXES)
        elif resolved_map is not None:
            for cand in (resolved_map.with_suffix(".elf"), resolved_map.with_suffix("")):
                if cand.is_file() and cand != resolved_map:
                    resolved_elf = cand.resolve()
                    break
        if resolved_elf is None:
            env_elf = os.environ.get("MCU_HARNESS_ELF_PATH")
            if env_elf and Path(env_elf).expanduser().is_file():
                resolved_elf = Path(env_elf).expanduser().resolve()

    if resolved_map is None and resolved_elf is not None:
        # 自动探测同名 .map（支持 fw.map 与 fw.elf.map）
        candidates = [
            resolved_elf.with_suffix(".map"),
            Path(f"{resolved_elf}.map"),
        ]
        for cand in candidates:
            if cand.is_file():
                resolved_map = cand.resolve()
                break
        if resolved_map is None and project is not None and project.is_dir():
            stem_map = _find_matching_map(project, resolved_elf.stem)
            if stem_map is not None:
                resolved_map = stem_map

    if resolved_elf is None and resolved_map is None:
        raise FileNotFoundError(
            "No ELF or .map file found (pass elf_path=..., map_path=..., or a built project_dir)"
        )
    return resolved_elf, resolved_map


def _find_newest_file(root: Path, suffixes: tuple[str, ...]) -> Path | None:
    best: tuple[float, Path] | None = None
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_SCAN_DIRS]
        for fname in filenames:
            p = Path(dirpath) / fname
            if p.suffix.lower() not in suffixes:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            if best is None or st.st_mtime > best[0]:
                best = (st.st_mtime, p.resolve())
    return best[1] if best is not None else None


def _find_matching_map(root: Path, elf_stem: str) -> Path | None:
    best: tuple[float, Path] | None = None
    target_names = {f"{elf_stem.lower()}.map", f"{elf_stem.lower()}.elf.map"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_SCAN_DIRS]
        for fname in filenames:
            if fname.lower() not in target_names:
                continue
            p = Path(dirpath) / fname
            try:
                st = p.stat()
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and (best is None or st.st_mtime > best[0]):
                best = (st.st_mtime, p.resolve())
    return best[1] if best is not None else None


def _guess_project_root(start_dir: Path) -> Path:
    cur = start_dir.resolve()
    for d in (cur, *cur.parents):
        if any((d / marker).exists() for marker in ("mcu-harness.toml", "CMakeLists.txt", "Makefile", ".git")):
            return d
    return cur


# ---------------------------------------------------------------------------
# 纯标准库 ELF 节表与符号表解析
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RawSymbol:
    name: str
    address: int
    raw_value: int
    size: int
    shndx: int
    section_name: str
    sym_type: str
    st_bind: int


def _parse_elf_sections_and_symbols(elf_path: Path) -> tuple[list[SectionInfo], list[_RawSymbol]]:
    data = elf_path.read_bytes()
    if len(data) < 52 or data[:4] != _ELF_MAGIC:
        raise FootprintError(f"Not a valid ELF file: {elf_path}")

    ei_class = data[4]  # 1=ELF32, 2=ELF64
    ei_data = data[5]   # 1=little-endian, 2=big-endian
    if ei_class not in (1, 2) or ei_data not in (1, 2):
        raise FootprintError(f"Unsupported ELF class/endian ({ei_class}, {ei_data}) in {elf_path}")
    endian = "<" if ei_data == 1 else ">"

    if ei_class == 1:
        e_shoff = struct.unpack_from(f"{endian}I", data, 32)[0]
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from(f"{endian}HHH", data, 46)
        sh_fmt = f"{endian}IIIIIIIIII"
        sym_fmt = f"{endian}IIIBBH"
    else:
        if len(data) < 64:
            raise FootprintError(f"Truncated ELF64 header in {elf_path}")
        e_shoff = struct.unpack_from(f"{endian}Q", data, 40)[0]
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from(f"{endian}HHH", data, 58)
        sh_fmt = f"{endian}IIQQQQIIQQ"
        sym_fmt = f"{endian}IBBHQQ"

    if e_shoff == 0 or e_shnum == 0:
        return [], []

    sh_struct_size = struct.calcsize(sh_fmt)
    if e_shentsize < sh_struct_size or e_shoff < 0 or e_shoff + e_shnum * e_shentsize > len(data):
        raise FootprintError(f"Corrupted ELF section header table out of bounds in {elf_path}")

    raw_sections: list[tuple[int, ...]] = []
    for idx in range(e_shnum):
        off = e_shoff + idx * e_shentsize
        raw_sections.append(struct.unpack_from(sh_fmt, data, off))

    shstrtab = b""
    if 0 < e_shstrndx < len(raw_sections):
        _, _, _, _, str_off, str_size, *_ = raw_sections[e_shstrndx]
        if 0 <= str_off and 0 <= str_size and str_off + str_size <= len(data):
            shstrtab = data[str_off : str_off + str_size]

    section_names: list[str] = []
    alloc_sections: list[SectionInfo] = []
    for idx, sec in enumerate(raw_sections):
        sh_name, sh_type, sh_flags, sh_addr, _, sh_size, _, _, sh_addralign, _ = sec
        sec_name = _read_cstr(shstrtab, sh_name) if shstrtab else ""
        if not sec_name and idx > 0:
            sec_name = f"<sec#{idx}>"
        section_names.append(sec_name)

        if idx == 0 or not (sh_flags & _SHF_ALLOC):
            continue

        sec_type_name = _sec_type_str(sh_type)
        category, in_flash, in_ram = _classify_elf_section(sec_name, sh_type, sh_flags, sh_addr)
        alloc_sections.append(
            SectionInfo(
                name=sec_name,
                address=sh_addr,
                size=sh_size,
                sec_type=sec_type_name,
                category=category,
                in_flash=in_flash,
                in_ram=in_ram,
                align=max(1, int(sh_addralign or 1)),
            )
        )

    # 解析符号表：优先 .symtab，缺失时回退 .dynsym
    sym_secs = [s for s in raw_sections if s[1] == _SHT_SYMTAB]
    if not sym_secs:
        sym_secs = [s for s in raw_sections if s[1] == _SHT_DYNSYM]

    sym_struct_size = struct.calcsize(sym_fmt)
    symbols: list[_RawSymbol] = []
    for sec in sym_secs:
        _, _, _, _, sh_offset, sh_size, sh_link, _, _, sh_entsize = sec
        step = sh_entsize or sym_struct_size
        if step < sym_struct_size:
            raise FootprintError(f"Corrupted ELF symbol entry size ({step} < {sym_struct_size}) in {elf_path}")
        if sh_offset < 0 or sh_size < 0 or sh_offset + sh_size > len(data):
            raise FootprintError(f"Corrupted ELF symbol table out of bounds in {elf_path}")
        if sh_link >= len(raw_sections):
            raise FootprintError(f"Corrupted ELF string table link ({sh_link}) in {elf_path}")

        _, _, _, _, str_off, str_size, *_ = raw_sections[sh_link]
        if str_off < 0 or str_size < 0 or str_off + str_size > len(data):
            raise FootprintError(f"Corrupted ELF string table out of bounds in {elf_path}")
        strtab = data[str_off : str_off + str_size]

        pos = sh_offset
        end = sh_offset + sh_size
        while pos + sym_struct_size <= end:
            if ei_class == 1:
                st_name, st_value, st_size, st_info, _, st_shndx = struct.unpack_from(sym_fmt, data, pos)
            else:
                st_name, st_info, _, st_shndx, st_value, st_size = struct.unpack_from(sym_fmt, data, pos)
            pos += step

            if st_name == 0:
                continue
            sym_name = _read_cstr(strtab, st_name)
            if not sym_name:
                continue

            st_type = st_info & 0x0F
            st_bind = st_info >> 4
            type_name = _SYM_TYPE_NAMES.get(st_type, "other")
            norm_addr = (st_value & ~1) if st_type == _STT_FUNC else st_value
            if st_shndx == _SHN_ABS:
                sec_label = "ABS"
            elif 0 < st_shndx < len(section_names):
                sec_label = section_names[st_shndx]
            else:
                sec_label = "UNDEF"

            symbols.append(
                _RawSymbol(
                    name=sym_name,
                    address=norm_addr,
                    raw_value=st_value,
                    size=st_size,
                    shndx=st_shndx,
                    section_name=sec_label,
                    sym_type=type_name,
                    st_bind=st_bind,
                )
            )

    return alloc_sections, symbols


def _read_cstr(buf: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(buf):
        return ""
    nul = buf.find(b"\x00", offset)
    raw = buf[offset:nul] if nul != -1 else buf[offset:]
    return raw.decode("utf-8", errors="replace")


def _sec_type_str(sh_type: int) -> str:
    if sh_type == _SHT_PROGBITS:
        return "progbits"
    if sh_type == _SHT_NOBITS:
        return "nobits"
    if sh_type == _SHT_ARM_EXIDX:
        return "arm_exidx"
    if sh_type in (_SHT_INIT_ARRAY, _SHT_FINI_ARRAY, _SHT_PREINIT_ARRAY):
        return "init_array"
    return f"type_{sh_type}"


def _classify_elf_section(name: str, sh_type: int, sh_flags: int, sh_addr: int) -> tuple[str, bool, bool]:
    low = name.lower()
    is_write = bool(sh_flags & _SHF_WRITE)
    is_exec = bool(sh_flags & _SHF_EXECINSTR)

    if any(low.startswith(h) for h in _HEAP_STACK_NAMES):
        return "heap_stack", False, True

    if sh_type == _SHT_NOBITS or low.startswith((".bss", ".sbss", ".tbss", ".noinit")):
        cat = "fast_ram" if any(low.startswith(p) for p in _FAST_RAM_PREFIXES) else "bss"
        return cat, False, True

    if any(low.startswith(p) for p in _FAST_RAM_PREFIXES) or low.startswith(".ramfunc"):
        return "fast_ram", True, True

    if is_write:
        return "data", True, True

    if sh_type == _SHT_ARM_EXIDX or low.startswith((".arm.exidx", ".arm.extab")):
        return "arm_exidx", True, False

    if is_exec or low.startswith((".text", ".isr_vector", ".vectors", ".init", ".fini", ".plt")):
        return "code", True, False

    return "rodata", True, False


def _canonical_section_bucket(name: str) -> str:
    low = name.lower()
    for key in (
        ".isr_vector",
        ".text",
        ".rodata",
        ".ARM.exidx",
        ".data",
        ".bss",
        ".ccmram",
        ".dtcmram",
        ".itcmram",
        ".ram_d1",
        ".ram_d2",
        ".ram_d3",
        "._user_heap_stack",
    ):
        if low == key.lower() or low.startswith(key.lower() + "."):
            return key
    return name


# ---------------------------------------------------------------------------
# Top 符号提取与固件膨胀元凶检测
# ---------------------------------------------------------------------------


def _extract_top_symbols(
    sections: list[SectionInfo],
    symbols: list[_RawSymbol],
    *,
    top_n: int,
) -> tuple[list[SymbolFootprint], list[SymbolFootprint]]:
    sec_by_name = {s.name: s for s in sections}
    flash_funcs: list[SymbolFootprint] = []
    ram_objs: list[SymbolFootprint] = []
    seen_flash: set[tuple[str, int, int]] = set()
    seen_ram: set[tuple[str, int, int]] = set()

    for sym in symbols:
        if sym.shndx in (_SHN_UNDEF, _SHN_ABS) or sym.size <= 0:
            continue
        # 过滤 ARM ABI 映射伪符号（$t / $d / $a / $x）
        if sym.name.startswith(("$t", "$d", "$a", "$x")):
            continue

        sec = sec_by_name.get(sym.section_name)
        if sym.sym_type == "func":
            if sec is None or sec.in_flash:
                key = (sym.name, sym.address, sym.size)
                if key not in seen_flash:
                    seen_flash.add(key)
                    flash_funcs.append(
                        SymbolFootprint(
                            name=sym.name,
                            address=sym.address,
                            size=sym.size,
                            section=sym.section_name,
                            sym_type="func",
                        )
                    )
        elif sym.sym_type == "object":
            in_ram = sec.in_ram if sec is not None else (0x10000000 <= sym.address < 0x40000000)
            if in_ram:
                key = (sym.name, sym.address, sym.size)
                if key not in seen_ram:
                    seen_ram.add(key)
                    ram_objs.append(
                        SymbolFootprint(
                            name=sym.name,
                            address=sym.address,
                            size=sym.size,
                            section=sym.section_name,
                            sym_type="object",
                        )
                    )

    flash_funcs.sort(key=lambda s: (-s.size, s.address, s.name))
    ram_objs.sort(key=lambda s: (-s.size, s.address, s.name))
    return flash_funcs[:top_n], ram_objs[:top_n]


_BLOAT_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "float_printf",
        (
            "_dtoa_r",
            "__dtoa",
            "_svfprintf_r",
            "_vfprintf_r",
            "_printf_float",
            "_svfscanf_r",
            "_scanf_float",
            "__aeabi_ddiv",
            "__aeabi_dmul",
            "__aeabi_dadd",
            "__aeabi_dsub",
            "__divdf3",
        ),
        (
            "检测到双精度浮点 printf 格式化或软双精度运算符号（如 _dtoa_r / _svfprintf_r / __aeabi_ddiv），"
            "通常会额外引入 8~20 KB Flash 体积；建议使用 -specs=nano.specs（禁用 -u _printf_float）、"
            "改用定点整数格式化，并为浮点常量补上 f 后缀（配合 -Wdouble-promotion）。"
        ),
    ),
    (
        "newlib_heap",
        ("_malloc_r", "_free_r", "_realloc_r", "_calloc_r", "_sbrk_r"),
        (
            "检测到 Newlib 动态堆分配符号（如 _malloc_r），不仅增加数 KB Flash 与重入结构 RAM 开销，"
            "还可能引发运行期内存碎片；建议改用静态缓冲区/固定块内存池或启用 newlib-nano。"
        ),
    ),
    (
        "cpp_exceptions_rtti",
        ("__cxa_throw", "__cxa_allocate_exception", "__gxx_personality_v0", "_Unwind_Resume"),
        (
            "检测到 C++ 异常展开表与运行时支持符号，会显著增大 .ARM.exidx 与 .text；"
            "MCU 裸机/RTOS 工程建议开启 -fno-exceptions -fno-rtti。"
        ),
    ),
    (
        "stdio_streams",
        ("__sinit", "_fopen_r", "_fflush_r", "_fseek_r"),
        (
            "检测到完整 stdio 文件流缓冲初始化符号（__sinit / FILE 流），会拉入较大静态重入表（_impure_data）；"
            "建议仅通过轻量级 _write / SEGGER RTT / UART 环形缓冲输出日志。"
        ),
    ),
)


def _detect_bloat_hints(symbols: list[_RawSymbol]) -> list[BloatHint]:
    by_name: dict[str, int] = {}
    for sym in symbols:
        if sym.name in by_name:
            by_name[sym.name] = max(by_name[sym.name], sym.size)
        else:
            by_name[sym.name] = sym.size

    hints: list[BloatHint] = []
    for category, triggers, message in _BLOAT_RULES:
        matched = [t for t in triggers if t in by_name]
        if matched:
            est = sum(by_name[m] for m in matched)
            hints.append(
                BloatHint(
                    category=category,
                    symbols=matched,
                    estimated_bytes=est,
                    message=message,
                )
            )
    return hints


# ---------------------------------------------------------------------------
# GNU ld `.map` 深度解析（Memory Configuration + 模块与分层归因）
# ---------------------------------------------------------------------------

_RE_MAP_REGION = re.compile(
    r"^\s*(?P<name>\S+)\s+0x(?P<origin>[0-9a-fA-F]+)\s+0x(?P<length>[0-9a-fA-F]+)(?:\s+(?P<attr>[A-Za-z!]+))?\s*$"
)
_RE_OUT_SEC_INLINE = re.compile(
    r"^(?P<sec>\.[A-Za-z0-9_.$+-]+|[A-Za-z_][A-Za-z0-9_.$+-]*)\s+"
    r"0x(?P<vma>[0-9a-fA-F]+)\s+0x(?P<size>[0-9a-fA-F]+)"
    r"(?:\s+(?:load address|加载地址|載入位址)\s+0x(?P<lma>[0-9a-fA-F]+))?\s*$"
)
_RE_OUT_SEC_NAME_ONLY = re.compile(r"^(?P<sec>\.[A-Za-z0-9_.$+-]+)\s*$")
_RE_CONT_OUT_SEC = re.compile(
    r"^\s+0x(?P<vma>[0-9a-fA-F]+)\s+0x(?P<size>[0-9a-fA-F]+)"
    r"(?:\s+(?:load address|加载地址|載入位址)\s+0x(?P<lma>[0-9a-fA-F]+))?\s*$"
)
_RE_IN_SEC_INLINE = re.compile(
    r"^ (?P<isec>\.[A-Za-z0-9_.$+-]+|COMMON)\s+"
    r"0x(?P<addr>[0-9a-fA-F]+)\s+0x(?P<size>[0-9a-fA-F]+)\s+(?P<obj>.+?)\s*$"
)
_RE_IN_SEC_NAME_ONLY = re.compile(r"^ (?P<isec>\.[A-Za-z0-9_.$+-]+|COMMON)\s*$")
_RE_CONT_IN_SEC = re.compile(
    r"^ {10,}0x(?P<addr>[0-9a-fA-F]+)\s+0x(?P<size>[0-9a-fA-F]+)\s+(?P<obj>.+?)\s*$"
)


def _parse_gnu_map(
    map_path: Path,
    *,
    project_dir: Path | None,
    elf_sections: dict[str, SectionInfo],
    top_modules: int = 30,
) -> tuple[list[MapRegionUsage], list[ModuleContribution], list[LayerContribution]]:
    text = map_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    raw_regions: list[tuple[str, int, int, str]] = []
    region_used: dict[str, int] = {}

    # 建立工程源文件 stem -> 相对目录索引，供 CubeMX 扁平 build/xxx.o 反查目录层
    stem_to_rel_dir = _index_project_source_stems(project_dir) if project_dir is not None else {}

    state = "preamble"  # "preamble" | "mem_config" | "mem_map"
    cur_out_sec: str | None = None
    cur_in_flash = False
    cur_in_ram = False
    cur_bucket = "other"  # "text" | "rodata" | "data" | "bss" | "none"

    # module_key -> [flash, ram, text, rodata, data, bss, layer]
    mod_acc: dict[str, list[Any]] = {}

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].rstrip("\r\n")
        stripped = line.strip()

        if stripped in ("Memory Configuration", "内存配置", "記憶體配置"):
            state = "mem_config"
            i += 1
            continue
        if stripped in (
            "Linker script and memory map",
            "链结器命令稿和内存映射",
            "链接器脚本和内存映射",
            "鏈結器指令稿和記憶體映射",
        ):
            state = "mem_map"
            i += 1
            continue
        if state != "mem_map" and line.startswith(("LOAD ", "START GROUP")):
            state = "mem_map"
            i += 1
            continue
        if state == "mem_map" and (
            stripped.startswith(("Cross Reference Table", "交叉引用表", "OUTPUT("))
        ):
            break

        if state == "mem_config":
            if not stripped or stripped.startswith(("Name", "名称", "名稱")):
                i += 1
                continue
            # 若某些极简脚本省略了标题直接进入顶层输出节（以 . 开头），自动切入 mem_map
            if line.startswith("."):
                state = "mem_map"
            else:
                m = _RE_MAP_REGION.match(line)
                if m:
                    r_name = m["name"]
                    origin = int(m["origin"], 16)
                    length = int(m["length"], 16)
                    attr = m["attr"] or ""
                    if r_name != "*default*" and length not in (0, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
                        raw_regions.append((r_name, origin, length, attr))
                        region_used.setdefault(r_name, 0)
                i += 1
                continue

        if state != "mem_map":
            i += 1
            continue

        # --- Linker script and memory map 内部 ---
        if not stripped:
            i += 1
            continue

        # 1. 顶层输出节（位于第 0 列，支持单行与长节名双行折行）
        if not line[0].isspace():
            if line.startswith(("LOAD ", "START GROUP", "END GROUP", "Address of section ")):
                i += 1
                continue

            out_match = _RE_OUT_SEC_INLINE.match(line)
            step = 1
            sec_name: str | None = None
            vma = 0
            sec_size = 0
            lma: int | None = None

            if out_match:
                sec_name = out_match["sec"]
                vma = int(out_match["vma"], 16)
                sec_size = int(out_match["size"], 16)
                lma = int(out_match["lma"], 16) if out_match["lma"] else None
            else:
                nm = _RE_OUT_SEC_NAME_ONLY.match(line)
                if nm:
                    if i + 1 < n:
                        cm = _RE_CONT_OUT_SEC.match(lines[i + 1].rstrip("\r\n"))
                        if cm:
                            sec_name = nm["sec"]
                            vma = int(cm["vma"], 16)
                            sec_size = int(cm["size"], 16)
                            lma = int(cm["lma"], 16) if cm["lma"] else None
                            step = 2
                        else:
                            # 空输出节（如 .rodata1 后没有地址大小）
                            cur_out_sec = nm["sec"]
                            cur_in_flash = False
                            cur_in_ram = False
                            cur_bucket = "none"
                            i += 1
                            continue

            if sec_name is not None:
                cur_out_sec = sec_name
                cur_in_flash, cur_in_ram, cur_bucket = _classify_map_output_section(
                    sec_name=sec_name,
                    vma=vma,
                    sec_size=sec_size,
                    lma=lma,
                    raw_regions=raw_regions,
                    elf_sections=elf_sections,
                )
                if sec_size > 0 and not _is_non_alloc_map_section(sec_name):
                    _accumulate_region_usage(raw_regions, region_used, sec_name, vma, sec_size, lma)
                i += step
                continue

            i += 1
            continue

        # 2. 输入节贡献行（以单个空格缩进，支持单行与长节名双行折行）
        if cur_out_sec and (cur_in_flash or cur_in_ram):
            in_match = _RE_IN_SEC_INLINE.match(line)
            step = 1
            isec_name: str | None = None
            isec_size = 0
            raw_obj: str | None = None

            if in_match:
                isec_name = in_match["isec"]
                isec_size = int(in_match["size"], 16)
                raw_obj = in_match["obj"].strip()
            else:
                nm = _RE_IN_SEC_NAME_ONLY.match(line)
                if nm and i + 1 < n:
                    cm = _RE_CONT_IN_SEC.match(lines[i + 1].rstrip("\r\n"))
                    if cm:
                        isec_name = nm["isec"]
                        isec_size = int(cm["size"], 16)
                        raw_obj = cm["obj"].strip()
                        step = 2

            if isec_name is not None and raw_obj and isec_size > 0 and raw_obj != "linker stubs":
                mod_display, layer = _classify_object_layer(
                    raw_obj,
                    project_dir=project_dir,
                    stem_to_rel_dir=stem_to_rel_dir,
                )
                entry = mod_acc.get(mod_display)
                if entry is None:
                    entry = [0, 0, 0, 0, 0, 0, layer]
                    mod_acc[mod_display] = entry

                if cur_in_flash:
                    entry[0] += isec_size
                if cur_in_ram:
                    entry[1] += isec_size

                if cur_bucket == "text":
                    entry[2] += isec_size
                elif cur_bucket == "rodata":
                    entry[3] += isec_size
                elif cur_bucket == "data":
                    entry[4] += isec_size
                elif cur_bucket == "bss":
                    entry[5] += isec_size

                i += step
                continue

        i += 1

    # 组装 Memory Configuration 使用率
    map_regions: list[MapRegionUsage] = []
    for r_name, origin, length, attr in raw_regions:
        used = region_used.get(r_name, 0)
        free = max(0, length - used)
        pct = round((used * 100.0) / length, 2) if length > 0 else 0.0
        map_regions.append(
            MapRegionUsage(
                name=r_name,
                origin=origin,
                length=length,
                used_bytes=used,
                free_bytes=free,
                percent=pct,
                attributes=attr,
            )
        )

    # 组装模块贡献列表
    all_modules = [
        ModuleContribution(
            module=mod,
            layer=vals[6],
            flash_bytes=vals[0],
            ram_bytes=vals[1],
            text_bytes=vals[2],
            rodata_bytes=vals[3],
            data_bytes=vals[4],
            bss_bytes=vals[5],
        )
        for mod, vals in mod_acc.items()
    ]
    all_modules.sort(key=lambda m: (-(m.flash_bytes + m.ram_bytes), -m.flash_bytes, m.module))

    # 按工程目录层级聚合权重
    total_mod_flash = sum(m.flash_bytes for m in all_modules)
    total_mod_ram = sum(m.ram_bytes for m in all_modules)
    layer_acc: dict[str, list[int]] = {}
    for m in all_modules:
        acc = layer_acc.setdefault(m.layer, [0, 0, 0])
        acc[0] += m.flash_bytes
        acc[1] += m.ram_bytes
        acc[2] += 1

    layer_contribs = [
        LayerContribution(
            layer=layer,
            flash_bytes=vals[0],
            ram_bytes=vals[1],
            flash_percent=round((vals[0] * 100.0) / total_mod_flash, 2) if total_mod_flash > 0 else 0.0,
            ram_percent=round((vals[1] * 100.0) / total_mod_ram, 2) if total_mod_ram > 0 else 0.0,
            module_count=vals[2],
        )
        for layer, vals in layer_acc.items()
    ]
    layer_contribs.sort(key=lambda l: (-(l.flash_bytes + l.ram_bytes), -l.flash_bytes, l.layer))

    return map_regions, all_modules[:top_modules], layer_contribs


def _is_non_alloc_map_section(sec_name: str) -> bool:
    return any(sec_name.startswith(p) for p in _NON_ALLOC_MAP_PREFIXES)


def _find_region_for_addr(
    raw_regions: list[tuple[str, int, int, str]],
    addr: int,
    size: int,
) -> tuple[str, int, int, str] | None:
    for reg in raw_regions:
        _, origin, length, _ = reg
        if origin <= addr and addr + size <= origin + length:
            return reg
        if origin <= addr < origin + length:
            return reg
    return None


def _is_flash_region(name: str, attr: str) -> bool:
    up = name.upper()
    if any(k in up for k in ("FLASH", "ROM", "QSPI", "OSPI")):
        return True
    if any(k in up for k in ("RAM", "SRAM", "DTCM", "CCMRAM", "ITCM")):
        return False
    return ("w" not in attr.lower()) and any(c in attr.lower() for c in ("r", "x"))


def _classify_map_output_section(
    *,
    sec_name: str,
    vma: int,
    sec_size: int,
    lma: int | None,
    raw_regions: list[tuple[str, int, int, str]],
    elf_sections: dict[str, SectionInfo],
) -> tuple[bool, bool, str]:
    if _is_non_alloc_map_section(sec_name):
        return False, False, "none"

    if sec_name in elf_sections:
        es = elf_sections[sec_name]
        if es.in_flash and es.in_ram:
            return True, True, "data"
        if es.in_flash:
            return True, False, ("text" if es.category == "code" else "rodata")
        if es.in_ram:
            return False, True, "bss"
        return False, False, "none"

    low = sec_name.lower()
    if raw_regions and sec_size >= 0:
        vma_reg = _find_region_for_addr(raw_regions, vma, max(sec_size, 1))
        lma_reg = _find_region_for_addr(raw_regions, lma, max(sec_size, 1)) if lma is not None else None
        if vma_reg is not None:
            v_is_flash = _is_flash_region(vma_reg[0], vma_reg[3])
            if v_is_flash:
                bucket = "text" if low.startswith((".text", ".isr_vector", ".vectors", ".init", ".fini")) else "rodata"
                return True, False, bucket
            # VMA 在 RAM 中：若 LMA 落在 Flash 且非 bss/heap_stack，则同时占 Flash 镜像与 RAM
            if (
                lma_reg is not None
                and lma != vma
                and _is_flash_region(lma_reg[0], lma_reg[3])
                and not low.startswith((".bss", ".sbss", ".noinit", *_HEAP_STACK_NAMES))
            ):
                return True, True, "data"
            return False, True, "bss"

    if low.startswith((".bss", ".sbss", ".tbss", ".noinit", *_HEAP_STACK_NAMES)):
        return False, True, "bss"
    if low.startswith((".data", ".sdata", ".tdata", ".got", ".ramfunc")):
        return True, True, "data"
    if any(low.startswith(p) for p in _FAST_RAM_PREFIXES):
        if lma is not None and lma != vma:
            return True, True, "data"
        return False, True, "bss"
    if low.startswith((".text", ".isr_vector", ".vectors", ".init", ".fini", ".plt")):
        return True, False, "text"
    if low.startswith((".rodata", ".arm.exidx", ".arm.extab", ".eh_frame", ".init_array", ".fini_array", ".note", ".rela", ".dynsym", ".dynstr", ".interp", ".gnu")):
        return True, False, "rodata"
    return False, False, "none"


def _accumulate_region_usage(
    raw_regions: list[tuple[str, int, int, str]],
    region_used: dict[str, int],
    sec_name: str,
    vma: int,
    sec_size: int,
    lma: int | None,
) -> None:
    if not raw_regions or sec_size <= 0:
        return
    vma_reg = _find_region_for_addr(raw_regions, vma, sec_size)
    if vma_reg is not None:
        region_used[vma_reg[0]] = region_used.get(vma_reg[0], 0) + sec_size

    low = sec_name.lower()
    if lma is not None and lma != vma and not low.startswith((".bss", ".sbss", ".noinit", *_HEAP_STACK_NAMES)):
        lma_reg = _find_region_for_addr(raw_regions, lma, sec_size)
        if lma_reg is not None and (vma_reg is None or lma_reg[0] != vma_reg[0]):
            region_used[lma_reg[0]] = region_used.get(lma_reg[0], 0) + sec_size


def _index_project_source_stems(project_dir: Path) -> dict[str, str]:
    """扫描工程目录建立唯一源文件 stem -> 顶层相对目录映射（辅助扁平 build/xxx.o 归因）。"""
    if not project_dir.is_dir():
        return {}
    found: dict[str, str | None] = {}
    for dirpath, dirnames, filenames in os.walk(project_dir):
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_SCAN_DIRS and d.lower() not in ("build", "debug", "release", "out")
        ]
        for fname in filenames:
            p = Path(dirpath) / fname
            if p.suffix not in _SOURCE_SUFFIXES:
                continue
            try:
                rel_dir = p.parent.relative_to(project_dir).as_posix()
            except ValueError:
                continue
            stem = p.stem.lower()
            if stem not in found:
                found[stem] = rel_dir
            elif found[stem] != rel_dir:
                found[stem] = None  # 重名文件不强行唯一映射
    return {k: v for k, v in found.items() if v is not None}


def _classify_object_layer(
    raw_obj: str,
    *,
    project_dir: Path | None,
    stem_to_rel_dir: dict[str, str],
) -> tuple[str, str]:
    norm = raw_obj.strip().replace("\\", "/")

    # 1. 静态库成员：path/to/libc_nano.a(lib_a-dtoa.o)
    if "(" in norm and norm.endswith(")"):
        arc_part, member_part = norm.split("(", 1)
        arc_name = Path(arc_part).name
        low_arc = arc_name.lower()
        if any(low_arc.startswith(p) for p in _TOOLCHAIN_ARCHIVE_PREFIXES) or _is_system_toolchain_path(arc_part, project_dir):
            return f"{arc_name}({member_part}", "toolchain_lib"
        disp = _shorten_to_project(arc_part, project_dir) + f"({member_part}"
        return disp, _layer_from_rel_path(disp)

    obj_name = Path(norm).name
    if obj_name.lower() in _CRT_OBJ_NAMES or _is_system_toolchain_path(norm, project_dir):
        return obj_name, "toolchain_lib"

    disp = _shorten_to_project(norm, project_dir)

    # 剥离构建产物前缀（build/、CMakeFiles/xxx.dir/）还原真实源码层级路径
    cleaned = _strip_build_prefixes(disp)
    if "/" in cleaned:
        return disp, _layer_from_rel_path(cleaned)

    # 扁平目标文件（如 build/service_water.o 或 main.o）：优先查工程源码目录索引
    obj_stem = Path(cleaned).stem
    # 兼容 main.c.o / main.c.obj 双重后缀
    if obj_stem.lower().endswith((".c", ".cc", ".cpp", ".s")):
        obj_stem = Path(obj_stem).stem
    low_stem = obj_stem.lower()

    if low_stem in stem_to_rel_dir:
        rel_dir = stem_to_rel_dir[low_stem]
        if rel_dir and rel_dir != ".":
            return disp, _layer_from_rel_path(f"{rel_dir}/{cleaned}")

    # 命名约定回退（如 service_water.o / bsp_uart.o / stm32h5xx_hal_dma.o）
    if low_stem.startswith(("service_", "svc_")):
        return disp, "service/"
    if low_stem.startswith("bsp_"):
        return disp, "bsp/"
    if low_stem.startswith("pal_"):
        return disp, "pal/"
    if low_stem.startswith(("app_", "main")):
        return disp, "app/"
    if low_stem.startswith(("stm32", "gd32", "at32", "nrf", "hal_", "ll_", "system_", "startup_")):
        return disp, "drivers/"
    if low_stem.startswith(("freertos", "cmsis_os", "tasks", "queue", "timers", "event_groups", "port")):
        return disp, "middlewares/"

    return disp, "app/"


def _is_system_toolchain_path(path_str: str, project_dir: Path | None) -> bool:
    norm = path_str.replace("\\", "/")
    if project_dir is not None:
        try:
            Path(path_str).resolve().relative_to(project_dir)
            return False
        except Exception:
            pass
    low = norm.lower()
    return any(
        seg in low
        for seg in (
            "/usr/lib",
            "/lib/",
            "arm-none-eabi",
            "riscv64-unknown-elf",
            "/gcc/",
            "/clang/",
            "newlib",
        )
    )


def _shorten_to_project(path_str: str, project_dir: Path | None) -> str:
    if project_dir is not None:
        p = Path(path_str)
        if p.is_absolute():
            try:
                return p.resolve().relative_to(project_dir).as_posix()
            except ValueError:
                pass
    return path_str.replace("\\", "/")


def _strip_build_prefixes(rel_path: str) -> str:
    parts = [p for p in rel_path.split("/") if p and p != "."]
    idx = 0
    while idx < len(parts) - 1:
        low = parts[idx].lower()
        if low in ("build", "out", "debug", "release", "obj", "objects", "src", "source", "sources"):
            idx += 1
            continue
        if low == "cmakefiles" and idx + 1 < len(parts) - 1 and parts[idx + 1].endswith(".dir"):
            idx += 2
            continue
        break
    return "/".join(parts[idx:])


def _layer_from_rel_path(rel_path: str) -> str:
    parts = [p for p in rel_path.replace("\\", "/").split("/") if p and p != "."]
    for seg in parts[:-1]:
        low = seg.lower()
        if low in ("src", "source", "sources"):
            continue
        if low in ("app", "application", "applications"):
            return "app/"
        if low in ("service", "services", "domain"):
            return "service/"
        if low == "bsp":
            return "bsp/"
        if low == "pal":
            return "pal/"
        if low in ("drivers", "driver", "hal", "cmsis", "core", "device"):
            return "drivers/"
        if low in ("middlewares", "middleware", "rtos", "freertos", "third_party", "external"):
            return "middlewares/"
        return f"{low}/"
    return "app/"


# ---------------------------------------------------------------------------
# 嵌入式内存风险静态审计（DMA 不可达紧耦合 RAM / 对齐 / 栈剩余净空）
# ---------------------------------------------------------------------------


def _audit_hazards(
    *,
    sections: list[SectionInfo],
    symbols: list[_RawSymbol],
    map_regions: list[MapRegionUsage],
    min_stack_bytes: int = _DEFAULT_MIN_STACK_HEADROOM,
) -> tuple[list[MemoryHazard], int | None, int | None, int | None]:
    hazards: list[MemoryHazard] = []

    sec_names_low = {s.name.lower() for s in sections}
    reg_names_low = {r.name.lower() for r in map_regions}
    has_h7_domains = any(
        any(tag in name for tag in ("ram_d1", "ram_d2", "ram_d3", "dtcm"))
        for name in (sec_names_low | reg_names_low)
    )

    seen_dma_syms: set[tuple[str, int]] = set()
    sym_lookup: dict[str, _RawSymbol] = {}
    for sym in symbols:
        sym_lookup.setdefault(sym.name, sym)

        if sym.shndx in (_SHN_UNDEF, _SHN_ABS):
            continue
        if sym.sym_type != "object" or "dma" not in sym.name.lower():
            continue
        if (sym.name, sym.address) in seen_dma_syms:
            continue
        seen_dma_syms.add((sym.name, sym.address))

        addr = sym.address
        size = sym.size
        sec_low = sym.section_name.lower()

        # 1a. CCMRAM (0x10000000..0x1000FFFF) 或显式 .ccmram 节
        if (0x10000000 <= addr <= 0x1000FFFF) or ("ccmram" in sec_low):
            hazards.append(
                MemoryHazard(
                    code="DMA_IN_CCMRAM",
                    severity="error",
                    symbol=sym.name,
                    address=addr,
                    size=size,
                    section=sym.section_name,
                    message=(
                        f"DMA 缓冲区 '{sym.name}' 位于紧耦合 CCMRAM (0x{addr:08X})，常规通用 DMA 控制器无法访问该总线，"
                        "运行期将触发 DMA 传输错误或读出全 0；请移至主 SRAM 节。"
                    ),
                )
            )
        # 1b. STM32H7 DTCM (0x20000000..0x2001FFFF 且存在 RAM_D1/D2/DTCM 分区)
        elif (0x20000000 <= addr <= 0x2001FFFF and has_h7_domains) or ("dtcm" in sec_low):
            hazards.append(
                MemoryHazard(
                    code="DMA_IN_DTCM",
                    severity="error",
                    symbol=sym.name,
                    address=addr,
                    size=size,
                    section=sym.section_name,
                    message=(
                        f"DMA 缓冲区 '{sym.name}' 位于紧耦合 DTCM (0x{addr:08X})，STM32H7 通用 DMA1/DMA2/BDMA 无法直接访问 DTCM；"
                        "请使用 __attribute__((section(\".ram_d2\"), aligned(32))) 迁移至 AXI SRAM / SRAM1-2。"
                    ),
                )
            )

        # 1c. >= 32 字节 DMA 缓冲区的 4/32 字节对齐检查
        if size >= 32:
            if addr % 4 != 0:
                hazards.append(
                    MemoryHazard(
                        code="DMA_UNALIGNED_4B",
                        severity="error",
                        symbol=sym.name,
                        address=addr,
                        size=size,
                        section=sym.section_name,
                        message=(
                            f"DMA 缓冲区 '{sym.name}' (0x{addr:08X}, {size} B) 未按 4 字节对齐，"
                            "字宽 AHB/DMA 传输将触发对齐故障；请添加 __attribute__((aligned(32)))。"
                        ),
                    )
                )
            elif addr % 32 != 0:
                hazards.append(
                    MemoryHazard(
                        code="DMA_UNALIGNED_32B",
                        severity="warning",
                        symbol=sym.name,
                        address=addr,
                        size=size,
                        section=sym.section_name,
                        message=(
                            f"DMA 缓冲区 '{sym.name}' (0x{addr:08X}, {size} B) 未按 32 字节 Cache-Line 对齐；"
                            "在开启 D-Cache 的 Cortex-M7 等内核上执行 Cache Invalidate 会污染相邻变量，建议 aligned(32)。"
                        ),
                    )
                )

    # 2. 主栈剩余净空估算（基于 _estack 与同段 RAM 最高已用地址）
    estack_sym = sym_lookup.get("_estack") or sym_lookup.get("__StackTop")
    if estack_sym is None or estack_sym.raw_value <= 0:
        return hazards, None, None, None

    estack = estack_sym.raw_value
    ram_candidates: list[int] = []

    for sec in sections:
        if not sec.in_ram or sec.size <= 0:
            continue
        if sec.name.lower() in _PURE_STACK_RESERVATION_NAMES:
            continue
        sec_end = sec.address + sec.size
        # 仅统计与 _estack 处于同一 SRAM 地址窗口（64 MB 内）且不超过 _estack 的静态段
        if 0 < sec.address <= estack and sec_end <= estack and (estack - sec.address) <= 0x04000000:
            ram_candidates.append(sec_end)

    for sym in symbols:
        if sym.shndx in (_SHN_UNDEF, _SHN_ABS) or sym.sym_type != "object" or sym.size <= 0:
            continue
        if sym.section_name.lower() in _PURE_STACK_RESERVATION_NAMES:
            continue
        sym_end = sym.address + sym.size
        if 0 < sym.address <= estack and sym_end <= estack and (estack - sym.address) <= 0x04000000:
            ram_candidates.append(sym_end)

    if not ram_candidates:
        return hazards, estack, None, None

    ram_highest = max(ram_candidates)
    # 若链接脚本导出了最小堆保留常量 _Min_Heap_Size，计入非栈占用以保守估算栈可用净空
    heap_sym = sym_lookup.get("_Min_Heap_Size")
    if heap_sym is not None and 0 < heap_sym.raw_value <= 0x01000000:
        if ram_highest + heap_sym.raw_value <= estack:
            ram_highest += heap_sym.raw_value

    headroom = estack - ram_highest
    if headroom < min_stack_bytes:
        hazards.append(
            MemoryHazard(
                code="LOW_STACK_HEADROOM",
                severity="error" if headroom <= 0 else "warning",
                symbol=estack_sym.name,
                address=estack,
                size=headroom,
                section="STACK",
                message=(
                    f"主栈最大可用净空仅 {headroom} B (< {min_stack_bytes} B，RAM 已用至 0x{ram_highest:08X}，"
                    f"_estack=0x{estack:08X})，深层调用或中断嵌套极易发生栈溢出踩踏 .bss！"
                ),
            )
        )

    return hazards, estack, ram_highest, headroom
