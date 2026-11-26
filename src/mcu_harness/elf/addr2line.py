"""ELF/DWARF 地址转源码行号解析器（支持外部 addr2line 与纯标准库符号表优雅降级）。

核心特性：
    * 自动按优先级探测外部符号工具：arm-none-eabi-addr2line -> llvm-addr2line -> addr2line。
    * 强制以 LC_ALL=C 调用外部工具，避免系统本地化（如中文 locale）改变 `at` / `(inlined by)` 输出格式。
    * 支持内联展开链（`-i`）解析，并将源码路径相对 `project_dir` 归一化为 `/` 分隔。
    * 支持 `is_return_address=True`（如异常压栈 LR）：对齐 Thumb 最低位后减 2，定位发起 `BL` 调用的指令行。
    * 当宿主无 addr2line、ELF 被 strip 无 DWARF 行表或文件异常时，自动回退到 `.symtab` 函数符号 `func+0xoffset`，绝不抛出未捕获异常。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .symbols import ElfError, ElfSymbol, read_elf_symbols

_ADDR2LINE_CANDIDATES = (
    "arm-none-eabi-addr2line",
    "llvm-addr2line",
    "addr2line",
)

_RE_INLINED_PREFIX = re.compile(
    r"^\s*(?:\(inlined by\)|inlined by|inlined at)\s*",
    flags=re.IGNORECASE,
)
_RE_DISCRIMINATOR = re.compile(r"\s*\(discriminator\s+\d+\)\s*$", flags=re.IGNORECASE)


@dataclass
class CodeLocation:
    """代码地址对应的符号与源码行位置快照。"""

    address: str
    symbol: str | None = None
    symbol_offset: int | None = None
    file: str | None = None
    line: int | None = None
    inlined_chain: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def format_short(self) -> str:
        """生成紧凑单行可读位置描述（含 DWARF 行号或回退的 func+0xoffset）。"""
        if self.file:
            loc = f"{self.file}:{self.line}" if self.line else self.file
            base = f"{self.symbol} at {loc}" if self.symbol else loc
            if self.inlined_chain:
                return f"{base} (inlined at {' -> '.join(self.inlined_chain)})"
            return base
        if self.symbol:
            offset = self.symbol_offset if self.symbol_offset is not None else 0
            return f"{self.symbol}+0x{offset:x}"
        return self.address


def _format_addr(addr: int) -> str:
    if addr <= 0xFFFFFFFF:
        return f"0x{addr:08X}"
    return f"0x{addr:016X}"


def _discover_addr2line() -> str | None:
    """按优先级探测可用的 addr2line 可执行文件。"""
    local_bin = Path.home() / ".local" / "bin"
    for name in _ADDR2LINE_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
        cand = local_bin / name
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def _normalize_source_path(raw_path: str, project_dir: str | os.PathLike | None) -> str | None:
    """清理 addr2line 输出的文件路径，并相对 project_dir 归一化为 `/` 分隔。"""
    cleaned = raw_path.strip()
    if not cleaned or cleaned in ("??", "?"):
        return None

    posix_raw = cleaned.replace("\\", "/")
    if project_dir is not None:
        try:
            proj_root = Path(project_dir).expanduser().resolve()
            file_path = Path(posix_raw).expanduser()
            resolved_file = file_path.resolve() if file_path.is_absolute() else (proj_root / file_path).resolve()
            if resolved_file.is_relative_to(proj_root):
                return resolved_file.relative_to(proj_root).as_posix()
        except OSError:
            pass

    # 若本身是相对路径，消除多余的 ./ 前缀
    while posix_raw.startswith("./"):
        posix_raw = posix_raw[2:]
    return posix_raw


def _parse_file_and_line(loc_part: str, project_dir: str | os.PathLike | None) -> tuple[str | None, int | None]:
    """解析 `/path/to/file.c:42 (discriminator 1)` 为 `(norm_file, line_no)`。"""
    s = _RE_DISCRIMINATOR.sub("", loc_part.strip())
    if not s or s.startswith("??"):
        return None, None

    if ":" in s:
        raw_file, raw_line = s.rsplit(":", 1)
        raw_line = raw_line.strip()
        line_no = int(raw_line) if raw_line.isdigit() and int(raw_line) > 0 else None
    else:
        raw_file, line_no = s, None

    norm_file = _normalize_source_path(raw_file, project_dir)
    if norm_file is None:
        return None, None
    return norm_file, line_no


def _parse_pretty_frame(
    raw_line: str,
    project_dir: str | os.PathLike | None,
) -> tuple[str | None, str | None, int | None]:
    """解析单行 `addr2line -f -C -i -p` 输出帧，返回 `(func_name, file_path, line_no)`。"""
    stripped = _RE_INLINED_PREFIX.sub("", raw_line).strip()
    if not stripped:
        return None, None, None

    if " at " in stripped:
        func_raw, loc_raw = stripped.split(" at ", 1)
    elif " " in stripped and (":" in stripped.rsplit(" ", 1)[1]):
        func_raw, loc_raw = stripped.rsplit(" ", 1)
    else:
        func_raw, loc_raw = None, stripped

    func_name = func_raw.strip() if func_raw else None
    if func_name in ("??", "?", ""):
        func_name = None

    norm_file, line_no = _parse_file_and_line(loc_raw, project_dir)
    return func_name, norm_file, line_no


def find_containing_symbol(
    elf_path: str | os.PathLike,
    address: int,
    *,
    max_fallback_distance: int = 0x10000,
) -> tuple[ElfSymbol, int] | None:
    """在 ELF 符号表中查找包含 `address` 的函数符号，返回 `(ElfSymbol, offset)`。

    规则：
        1. 先将传入地址最低位清零（`addr & ~1`）以对齐 ARM Thumb 指令地址。
        2. 过滤已定义函数符号（`sym_type == "func"` 且 `shndx != 0`，排除未定义外部导入符号与 `$t` 映射符号）。
        3. 优先精确匹配 `sym.size > 0` 且 `sym.address <= addr < sym.address + sym.size` 的函数。
        4. 若无精确范围命中，检查距离 `addr` 最近且 `sym.address <= addr` 的函数符号：
           若该函数 `sym.size == 0`（如未标注 `.size` 的汇编启动/中断入口）且距离 `<= max_fallback_distance`，
           则回退匹配该符号。
    """
    addr = int(address) & ~1
    if addr < 0:
        return None

    symbols = read_elf_symbols(elf_path)
    funcs = [
        s
        for s in symbols
        if s.sym_type == "func" and s.shndx != 0 and s.address > 0 and not s.name.startswith("$")
    ]
    if not funcs:
        # 兼容部分极简链接产物中 address == 0 的起始段函数（仍要求 shndx != 0）
        funcs = [
            s
            for s in symbols
            if s.sym_type == "func" and s.shndx != 0 and not s.name.startswith("$")
        ]
    if not funcs:
        return None

    exact_hits: list[tuple[int, int, ElfSymbol]] = []
    for sym in funcs:
        if sym.size > 0 and sym.address <= addr < sym.address + sym.size:
            offset = addr - sym.address
            exact_hits.append((offset, sym.size, sym))

    if exact_hits:
        exact_hits.sort(key=lambda item: (item[0], item[1], item[2].name.startswith("_")))
        best_sym = exact_hits[0][2]
        return best_sym, addr - best_sym.address

    # 回退：检查最近的不大于 addr 的已定义函数符号；仅当最近函数本身 size == 0 时允许作为无大小符号回退
    preceding = [s for s in funcs if s.address <= addr]
    if not preceding:
        return None

    max_start = max(s.address for s in preceding)
    nearest_at_start = [s for s in preceding if s.address == max_start]
    # 若该起始地址处已有明确大小的函数且未覆盖 addr，说明 addr 已超出该函数末尾（处于填充/文字池），不跨函数误判
    if any(s.size > 0 for s in nearest_at_start):
        return None

    zero_sized = [s for s in nearest_at_start if s.size == 0]
    if zero_sized and (addr - max_start) <= max_fallback_distance:
        zero_sized.sort(key=lambda s: (s.name.startswith("_"), s.name))
        chosen = zero_sized[0]
        return chosen, addr - chosen.address

    return None


def resolve_address(
    elf_path: str | os.PathLike,
    address: int,
    *,
    project_dir: str | os.PathLike | None = None,
    is_return_address: bool = False,
    addr2line_bin: str | None = None,
) -> CodeLocation:
    """将代码地址解析为函数符号、文件名、行号与内联调用链（失败时优雅降级，不抛异常）。

    参数：
        elf_path: 固件 `.elf` 文件路径。
        address: 目标指令地址（支持带 Thumb 标志位 LSB=1 的 PC/LR）。
        project_dir: 可选工程根目录，用于将绝对路径转为相对路径。
        is_return_address: 为 True 时（例如栈帧 LR），先 `(address & ~1) - 2` 定位调用指令本身。
        addr2line_bin: 可选显式指定 addr2line 路径（默认自动探测；传 `""` 可在测试中模拟无外部工具）。
    """
    raw_int = int(address)
    if raw_int < 0:
        raw_int &= 0xFFFFFFFF

    aligned = raw_int & ~1
    lookup_addr = max(0, aligned - 2) if is_return_address else aligned
    addr_hex = _format_addr(lookup_addr)

    sym_name: str | None = None
    sym_offset: int | None = None
    try:
        hit = find_containing_symbol(elf_path, lookup_addr)
        if hit is not None:
            sym_name = hit[0].name
            sym_offset = hit[1]
    except (OSError, ElfError, ValueError):
        pass

    elf_p: Path | None = None
    try:
        cand_p = Path(elf_path).expanduser()
        if cand_p.is_file():
            elf_p = cand_p.resolve()
    except OSError:
        elf_p = None

    tool = _discover_addr2line() if addr2line_bin is None else (addr2line_bin or None)
    if elf_p is not None and tool:
        cmd = [tool, "-f", "-C", "-i", "-p", "-e", str(elf_p), f"0x{lookup_addr:x}"]
        env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5.0,
                env=env,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
                if lines:
                    top_func, top_file, top_line = _parse_pretty_frame(lines[0], project_dir)
                    inlined_chain: list[str] = []
                    for extra in lines[1:]:
                        c_func, c_file, c_line = _parse_pretty_frame(extra, project_dir)
                        if c_file and c_line:
                            inlined_chain.append(f"{c_func} at {c_file}:{c_line}" if c_func else f"{c_file}:{c_line}")
                        elif c_file:
                            inlined_chain.append(f"{c_func} at {c_file}" if c_func else c_file)
                        elif c_func:
                            inlined_chain.append(c_func)

                    chosen_sym = top_func or sym_name
                    chosen_off = sym_offset if (sym_name is not None and chosen_sym == sym_name) else sym_offset
                    if top_file is not None or chosen_sym is not None:
                        return CodeLocation(
                            address=addr_hex,
                            symbol=chosen_sym,
                            symbol_offset=chosen_off,
                            file=top_file,
                            line=top_line,
                            inlined_chain=inlined_chain,
                        )
        except (OSError, subprocess.SubprocessError, ValueError):
            pass

    return CodeLocation(
        address=addr_hex,
        symbol=sym_name,
        symbol_offset=sym_offset,
        file=None,
        line=None,
        inlined_chain=[],
    )
