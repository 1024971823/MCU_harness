"""纯标准库 C/C++ 嵌入式分层架构边界与中断并发安全静态扫描器。

核心检查项：
    1. 检查项 A（分层边界与头文件防腐）：
       - 违禁原厂/板级头文件（`forbid_headers`，如 `service` 直接包含 `stm32*hal*.h`）
       - 跨层反向或越级依赖（`allow_depend_on`，如 `bsp` 反向包含 `service` 或 `app`）
       - 同层子域横向隔离（`isolate_siblings=True`，如 `service/water/` 直接包含 `service/motion/`）
    2. 检查项 B（ISR 中断上下文传递调用链扫描）：
       - 从所有中断入口（`*_IRQHandler`、`HAL_*_Callback`、`*_ISR` 等）做 BFS 调用图遍历
       - 捕获直接或跨文件间接调用阻塞/不可重入 API（如 `HAL_Delay`、`vTaskDelay`、`malloc`、`printf`）
    3. 检查项 C（ISR ↔ 主循环共享全局变量漏加 `volatile` 扫描）：
       - 识别在 ISR 调用树内被赋值修改、同时在非 ISR 函数中被读取等待的非 `volatile`/非原子全局变量
"""

from __future__ import annotations

import bisect
import fnmatch
import os
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config.manifest import ArchConfig, ConfigError, LayerRule, discover_project


class ArchError(RuntimeError):
    """架构扫描输入或配置异常。"""


_SOURCE_EXTS = {".c", ".h", ".cpp", ".hpp"}
_HEADER_EXTS = {".h", ".hpp"}

_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "CMakeFiles",
    ".pytest_cache",
    ".cache",
    "tests",
    "test",
    "Testing",
    "host_tests",
}

_C_CONTROL_AND_OPERATORS = {
    "if",
    "else",
    "for",
    "while",
    "do",
    "switch",
    "case",
    "default",
    "return",
    "sizeof",
    "alignof",
    "_Alignof",
    "typeof",
    "__typeof__",
    "__attribute__",
    "asm",
    "__asm",
    "__asm__",
    "defined",
    "_Static_assert",
    "static_assert",
    "catch",
}

_C_DECL_NON_VAR_NAMES = _C_CONTROL_AND_OPERATORS | {
    "void",
    "char",
    "short",
    "int",
    "long",
    "float",
    "double",
    "signed",
    "unsigned",
    "bool",
    "_Bool",
    "struct",
    "union",
    "enum",
    "typedef",
    "static",
    "extern",
    "const",
    "volatile",
    "inline",
    "__inline",
    "__inline__",
    "register",
    "auto",
    "restrict",
    "_Atomic",
}

_RE_INCLUDE = re.compile(r"^[ \t]*#[ \t]*include[ \t]*([\"<])([^\"'>\r\n]+)[>\"]", re.MULTILINE)
_RE_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RE_CALL_CANDIDATE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_RE_TRAILING_ATTR = re.compile(
    r"(?:\s+(?:const|noexcept|override|final|INTERRUPT|__irq)\b|\s*__attribute__\s*\(\([^)]*\)\))*\s*$"
)
_RE_VOLATILE_QUAL = re.compile(r"\b(?:volatile|__IO|__IOM|__IM|__OM|_Atomic|atomic_[A-Za-z0-9_]+)\b")
_RE_LOCAL_SHADOW_TYPES = re.compile(
    r"\b(?:int|char|short|long|float|double|bool|_Bool|unsigned|signed|size_t|ssize_t|"
    r"u?int(?:8|16|32|64|ptr|_fast8|_fast16|_fast32|_least8|_least16|_least32)_t)\b"
)


@dataclass(frozen=True)
class ArchViolation:
    """单条架构或并发安全违规记录。"""

    category: str  # "forbidden_header" | "layer_violation" | "sibling_violation" | "isr_unsafe_call" | "missing_volatile"
    severity: str  # "error" | "warning"
    file: str
    line: int
    message: str
    symbol: str | None = None
    layer: str | None = None
    target_layer: str | None = None
    call_chain: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "category": self.category,
            "severity": self.severity,
            "file": self.file,
            "line": self.line,
            "message": self.message,
        }
        if self.symbol is not None:
            out["symbol"] = self.symbol
        if self.layer is not None:
            out["layer"] = self.layer
        if self.target_layer is not None:
            out["target_layer"] = self.target_layer
        if self.call_chain:
            out["call_chain"] = list(self.call_chain)
        return out


@dataclass(frozen=True)
class ArchReport:
    """工程架构与中断并发静态检查完整报告。"""

    project_dir: str
    preset: str
    scanned_files: int
    layer_file_counts: dict[str, int]
    unclassified_files: int
    isr_entries: list[str]
    violations: list[ArchViolation]
    error_count: int
    warning_count: int
    passed: bool
    isr_locations: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_dir": self.project_dir,
            "preset": self.preset,
            "scanned_files": self.scanned_files,
            "layer_file_counts": dict(self.layer_file_counts),
            "unclassified_files": self.unclassified_files,
            "isr_entries": list(self.isr_entries),
            "isr_locations": dict(self.isr_locations),
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "passed": self.passed,
            "violations": [v.to_dict() for v in self.violations],
        }

    def to_text(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [
            f"[arch] {status} — {self.project_dir}",
            f"  preset:        {self.preset}",
            f"  scanned_files: {self.scanned_files} (unclassified: {self.unclassified_files})",
            f"  layer_counts:  {', '.join(f'{k}={v}' for k, v in self.layer_file_counts.items()) or '(none)'}",
            f"  isr_entries:   {', '.join(self.isr_entries) if self.isr_entries else '(none)'}",
            f"  summary:       {self.error_count} errors, {self.warning_count} warnings",
        ]
        if self.violations:
            lines.append("  violations:")
            for v in self.violations:
                lines.append(f"    - [{v.severity.upper()}][{v.category}] {v.file}:{v.line}: {v.message}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 内部数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ScannedFile:
    abs_path: Path
    rel_path: str
    is_header: bool
    layer_rule: LayerRule | None
    matched_prefix: str | None
    subdomain: str | None
    raw_source: str
    no_comments: str
    cleaned_c: str
    newline_offsets: list[int]

    def line_at(self, offset: int) -> int:
        return bisect.bisect_right(self.newline_offsets, offset) + 1


@dataclass(frozen=True)
class _FunctionDef:
    name: str
    file: str
    start_line: int
    end_line: int
    is_static: bool
    is_header: bool
    params_text: str
    body_text: str
    body_offset: int
    calls: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class _GlobalVarDecl:
    name: str
    file: str
    line: int
    is_volatile: bool
    is_extern: bool
    is_static: bool


# ---------------------------------------------------------------------------
# C 源码保行号去注释/字符串/预处理行
# ---------------------------------------------------------------------------


def _strip_comments_impl(source: str, *, strip_strings: bool) -> str:
    """单趟状态机去注释（及可选去字符串/字符字面量），100% 保留换行符位置与总长度。"""
    n = len(source)
    out = list(source)
    i = 0

    while i < n:
        ch = source[i]

        # 单行注释 // ...
        if ch == "/" and i + 1 < n and source[i + 1] == "/":
            out[i] = " "
            out[i + 1] = " "
            i += 2
            while i < n:
                c = source[i]
                if c == "\\" and i + 1 < n and source[i + 1] == "\n":
                    out[i] = " "
                    i += 2
                    continue
                if c == "\\" and i + 2 < n and source[i + 1] == "\r" and source[i + 2] == "\n":
                    out[i] = " "
                    i += 3
                    continue
                if c == "\n":
                    i += 1
                    break
                if c != "\r":
                    out[i] = " "
                i += 1
            continue

        # 块注释 /* ... */
        if ch == "/" and i + 1 < n and source[i + 1] == "*":
            out[i] = " "
            out[i + 1] = " "
            i += 2
            while i < n:
                c = source[i]
                if c == "*" and i + 1 < n and source[i + 1] == "/":
                    out[i] = " "
                    out[i + 1] = " "
                    i += 2
                    break
                if c not in ("\n", "\r"):
                    out[i] = " "
                i += 1
            continue

        # 字符串或字符字面量 "..." / '...'
        if ch in ('"', "'"):
            quote = ch
            if strip_strings:
                out[i] = " "
            i += 1
            while i < n:
                c = source[i]
                if c == "\\" and i + 1 < n:
                    if strip_strings:
                        out[i] = " "
                        if source[i + 1] not in ("\n", "\r"):
                            out[i + 1] = " "
                    i += 2
                    continue
                if c == quote:
                    if strip_strings:
                        out[i] = " "
                    i += 1
                    break
                if c == "\n":
                    i += 1
                    break
                if strip_strings and c != "\r":
                    out[i] = " "
                i += 1
            continue

        i += 1

    return "".join(out)


def _strip_comments_and_strings(source: str) -> str:
    """将 C/C++ 源码中的 `/* ... */`、`// ...` 以及 `"..."`/`'...'` 替换为空格，严格保留所有 `\\n` 换行符。"""
    return _strip_comments_impl(source, strip_strings=True)


def _strip_preprocessor_lines(cleaned: str) -> str:
    """将预处理指令行（`#include`/`#define`/`#if` 等，含反斜杠续行）替换为空格并保留换行符。"""
    n = len(cleaned)
    out = list(cleaned)
    i = 0

    while i < n:
        line_start = i
        while i < n and cleaned[i] in (" ", "\t"):
            i += 1
        if i < n and cleaned[i] == "#":
            # 抹除整条预处理指令（包含 \\\n 续行）
            j = line_start
            while j < n:
                if cleaned[j] == "\\" and j + 1 < n and cleaned[j + 1] == "\n":
                    out[j] = " "
                    j += 2
                    continue
                if cleaned[j] == "\\" and j + 2 < n and cleaned[j + 1] == "\r" and cleaned[j + 2] == "\n":
                    out[j] = " "
                    j += 3
                    continue
                if cleaned[j] == "\n":
                    j += 1
                    break
                if cleaned[j] != "\r":
                    out[j] = " "
                j += 1
            i = j
        else:
            nl = cleaned.find("\n", i)
            i = n if nl == -1 else nl + 1

    return "".join(out)


# ---------------------------------------------------------------------------
# 主入口：check_architecture
# ---------------------------------------------------------------------------


def check_architecture(
    project_dir: str | os.PathLike,
    *,
    config: ArchConfig | None = None,
    manifest_path: str | os.PathLike | None = None,
) -> ArchReport:
    """执行嵌入式工程分层架构边界、ISR 传递调用链与共享全局变量并发静态检查。"""
    root = Path(project_dir).expanduser().resolve()
    if not root.is_dir():
        raise ArchError(f"Project directory does not exist: {root}")

    if config is None:
        try:
            config = discover_project(root, manifest_path=manifest_path).arch
        except ConfigError as e:
            raise ArchError(str(e)) from e

    scanned = _collect_firmware_files(root, config)
    layer_counts: dict[str, int] = {rule.name: 0 for rule in config.layers}
    unclassified = 0
    for sf in scanned:
        if sf.layer_rule is not None:
            layer_counts[sf.layer_rule.name] = layer_counts.get(sf.layer_rule.name, 0) + 1
        else:
            unclassified += 1

    violations: list[ArchViolation] = []

    # 检查项 A：分层架构边界与头文件防腐检查
    if config.preset != "none" and config.layers:
        violations.extend(_check_layers_and_headers(root, scanned))

    # 解析所有 C/C++ 函数定义与顶层全局变量声明
    all_funcs: list[_FunctionDef] = []
    all_globals: list[_GlobalVarDecl] = []
    sf_by_rel: dict[str, _ScannedFile] = {sf.rel_path: sf for sf in scanned}

    for sf in scanned:
        f_defs, g_decls = _parse_functions_and_globals(sf)
        all_funcs.extend(f_defs)
        all_globals.extend(g_decls)

    # 检查项 B：ISR 传递调用链违禁阻塞扫描
    isr_roots, isr_tree_funcs, isr_violations = _check_isr_call_graph(all_funcs, sf_by_rel, config)
    violations.extend(isr_violations)

    # 检查项 C：ISR ↔ 主循环/任务共享全局变量漏加 volatile 扫描
    violations.extend(_check_missing_volatile(all_funcs, isr_tree_funcs, all_globals, sf_by_rel))

    # 按文件、行号、类别稳定排序，保证确定性输出
    violations.sort(key=lambda v: (v.file, v.line, v.category, v.symbol or ""))

    error_count = sum(1 for v in violations if v.severity == "error")
    warning_count = sum(1 for v in violations if v.severity == "warning")
    isr_locations = {fn.name: f"{fn.file}:{fn.start_line}" for fn in isr_roots}
    isr_entries = sorted(isr_locations.keys())

    return ArchReport(
        project_dir=str(root),
        preset=config.preset,
        scanned_files=len(scanned),
        layer_file_counts=layer_counts,
        unclassified_files=unclassified,
        isr_entries=isr_entries,
        isr_locations=isr_locations,
        violations=violations,
        error_count=error_count,
        warning_count=warning_count,
        passed=(error_count == 0),
    )


# ---------------------------------------------------------------------------
# 文件收集与层级归属
# ---------------------------------------------------------------------------


def _should_skip_dir(dirname: str) -> bool:
    if dirname in _SKIP_DIRS or dirname.startswith("."):
        return True
    low = dirname.lower()
    if low == "build" or low.startswith(("build_", "build-", "cmake-build")):
        return True
    return False


def _classify_layer(rel_posix: str, layers: tuple[LayerRule, ...]) -> tuple[LayerRule | None, str | None, str | None]:
    """返回 `(matched_rule, matched_prefix, subdomain)`。"""
    if not layers:
        return None, None, None

    best_rule: LayerRule | None = None
    best_prefix: str | None = None
    best_score = -1

    # 第一优先级：精确匹配 rule.paths；第二优先级：include/<rule_path> 头文件目录映射
    for pass_idx in (0, 1):
        for rule in layers:
            for raw_p in rule.paths:
                norm_p = raw_p.strip("/")
                if not norm_p:
                    continue
                candidates = [norm_p] if pass_idx == 0 else [f"include/{norm_p}", f"inc/{norm_p}"]
                for cand in candidates:
                    if "*" in cand or "?" in cand:
                        if fnmatch.fnmatchcase(rel_posix, cand) or fnmatch.fnmatchcase(rel_posix, cand + "/*"):
                            score = len(cand)
                            if score > best_score:
                                best_score = score
                                best_rule = rule
                                best_prefix = cand.rstrip("/*")
                    else:
                        if rel_posix == cand or rel_posix.startswith(cand + "/"):
                            score = len(cand)
                            if score > best_score:
                                best_score = score
                                best_rule = rule
                                best_prefix = cand
        if best_rule is not None:
            break

    if best_rule is None or not best_prefix:
        return None, None, None

    subdomain: str | None = None
    if rel_posix.startswith(best_prefix + "/"):
        rem = rel_posix[len(best_prefix) + 1 :]
        parts = rem.split("/")
        if len(parts) >= 2:
            subdomain = parts[0]

    return best_rule, best_prefix, subdomain


def _collect_firmware_files(root: Path, config: ArchConfig) -> list[_ScannedFile]:
    found_paths: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _should_skip_dir(d))
        for fname in sorted(filenames):
            p = Path(dirpath) / fname
            if p.suffix.lower() not in _SOURCE_EXTS or not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            found_paths.append((rel, p))

    found_paths.sort(key=lambda item: item[0])
    scanned: list[_ScannedFile] = []
    for rel, p in found_paths:
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        no_comments = _strip_comments_impl(raw, strip_strings=False)
        no_cs = _strip_comments_and_strings(raw)
        cleaned_c = _strip_preprocessor_lines(no_cs)
        nl_offsets = [idx for idx, ch in enumerate(raw) if ch == "\n"]
        rule, prefix, subdomain = _classify_layer(rel, config.layers)
        scanned.append(
            _ScannedFile(
                abs_path=p,
                rel_path=rel,
                is_header=(p.suffix.lower() in _HEADER_EXTS),
                layer_rule=rule,
                matched_prefix=prefix,
                subdomain=subdomain,
                raw_source=raw,
                no_comments=no_comments,
                cleaned_c=cleaned_c,
                newline_offsets=nl_offsets,
            )
        )
    return scanned


# ---------------------------------------------------------------------------
# 检查项 A：分层架构边界与头文件防腐
# ---------------------------------------------------------------------------


def _check_layers_and_headers(root: Path, scanned: list[_ScannedFile]) -> list[ArchViolation]:
    violations: list[ArchViolation] = []

    by_abs: dict[Path, _ScannedFile] = {sf.abs_path.resolve(): sf for sf in scanned}
    headers_by_name: dict[str, list[_ScannedFile]] = {}
    all_headers: list[_ScannedFile] = []

    for sf in scanned:
        if sf.is_header:
            all_headers.append(sf)
            headers_by_name.setdefault(Path(sf.rel_path).name, []).append(sf)

    def _resolve_included_header(src_sf: _ScannedFile, inc_norm: str) -> _ScannedFile | None:
        # 1. 相对当前源文件目录或工程常见头文件根目录解析
        search_bases = [
            src_sf.abs_path.parent,
            root,
            root / "include",
            root / "inc",
            root / "src",
        ]
        for base in search_bases:
            cand = (base / inc_norm).resolve()
            if cand in by_abs:
                return by_abs[cand]

        # 2. 按后缀路径匹配（如 `#include "water/pump.h"` -> `service/water/pump.h`）
        if "/" in inc_norm:
            suffix = "/" + inc_norm.lstrip("/")
            matches = [h for h in all_headers if h.rel_path == inc_norm or h.rel_path.endswith(suffix)]
            if len(matches) == 1:
                return matches[0]
            if matches:
                # 优先选择同层匹配（若合法），否则返回首个确定性匹配
                return matches[0]

        # 3. 按唯一文件名匹配（如 `CMakeLists` 添加了各层目录后直接 `#include "app_main.h"`）
        base_name = Path(inc_norm).name
        same_name = headers_by_name.get(base_name, [])
        if len(same_name) == 1:
            return same_name[0]
        if len(same_name) > 1 and src_sf.layer_rule is not None:
            same_layer = [h for h in same_name if h.layer_rule and h.layer_rule.name == src_sf.layer_rule.name]
            if len(same_layer) == 1:
                return same_layer[0]
        return None

    for sf in scanned:
        src_rule = sf.layer_rule
        for m in _RE_INCLUDE.finditer(sf.no_comments):
            inc_raw = m.group(2).strip()
            inc_norm = inc_raw.replace("\\", "/")
            inc_base = Path(inc_norm).name
            line_no = sf.line_at(m.start())

            # 1. forbid_headers 通配符检查
            if src_rule is not None and src_rule.forbid_headers:
                matched_pat: str | None = None
                for pat in src_rule.forbid_headers:
                    pat_low = pat.lower()
                    if fnmatch.fnmatch(inc_base.lower(), pat_low) or fnmatch.fnmatch(inc_norm.lower(), pat_low):
                        matched_pat = pat
                        break
                if matched_pat is not None:
                    violations.append(
                        ArchViolation(
                            category="forbidden_header",
                            severity="error",
                            file=sf.rel_path,
                            line=line_no,
                            symbol=inc_raw,
                            layer=src_rule.name,
                            message=(
                                f"Layer '{src_rule.name}' forbids including header '{inc_raw}' "
                                f"(matched pattern '{matched_pat}')"
                            ),
                        )
                    )
                    continue

            # 2. 跨层依赖与同层子域隔离检查
            if src_rule is None:
                continue

            target_sf = _resolve_included_header(sf, inc_norm)
            if target_sf is None or target_sf.layer_rule is None:
                continue

            target_rule = target_sf.layer_rule
            if target_rule.name != src_rule.name:
                if target_rule.name not in src_rule.allow_depend_on:
                    allowed_desc = ", ".join(src_rule.allow_depend_on) or "(none)"
                    violations.append(
                        ArchViolation(
                            category="layer_violation",
                            severity="error",
                            file=sf.rel_path,
                            line=line_no,
                            symbol=inc_raw,
                            layer=src_rule.name,
                            target_layer=target_rule.name,
                            message=(
                                f"Layer violation: '{src_rule.name}' ({sf.rel_path}) cannot include "
                                f"'{inc_raw}' from layer '{target_rule.name}' ({target_sf.rel_path}); "
                                f"allowed layers: [{allowed_desc}]"
                            ),
                        )
                    )
            elif src_rule.isolate_siblings:
                if sf.subdomain and target_sf.subdomain and sf.subdomain != target_sf.subdomain:
                    violations.append(
                        ArchViolation(
                            category="sibling_violation",
                            severity="error",
                            file=sf.rel_path,
                            line=line_no,
                            symbol=inc_raw,
                            layer=src_rule.name,
                            target_layer=target_rule.name,
                            message=(
                                f"Sibling isolation violation in layer '{src_rule.name}': "
                                f"subdomain '{sf.subdomain}' ({sf.rel_path}) cannot directly include "
                                f"subdomain '{target_sf.subdomain}' ({target_sf.rel_path})"
                            ),
                        )
                    )

    return violations


# ---------------------------------------------------------------------------
# C 函数定义与顶层全局变量解析
# ---------------------------------------------------------------------------


def _find_matching_close_brace(code: str, open_idx: int) -> int:
    depth = 1
    n = len(code)
    i = open_idx + 1
    while i < n:
        c = code[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n - 1


def _try_parse_func_header(header_text: str, header_base_offset: int) -> tuple[str, int, bool, str] | None:
    """若 `header_text` 为函数定义头部，返回 `(func_name, name_abs_offset, is_static, params_text)`。"""
    stripped = _RE_TRAILING_ATTR.sub("", header_text)
    if not stripped or stripped[-1] != ")":
        return None

    # 从末尾 ')' 反向配对找到参数列表起始 '('
    close_paren = len(stripped) - 1
    depth = 0
    open_paren = -1
    for idx in range(close_paren, -1, -1):
        c = stripped[idx]
        if c == ")":
            depth += 1
        elif c == "(":
            depth -= 1
            if depth == 0:
                open_paren = idx
                break
    if open_paren <= 0:
        return None

    before_paren = stripped[:open_paren].rstrip()
    if not before_paren or "=" in before_paren:
        return None

    # 提取函数名（兼容 C++ Class::Method）
    m_name = re.search(r"(?:[A-Za-z_][A-Za-z0-9_]*::)?([A-Za-z_][A-Za-z0-9_]*)$", before_paren)
    if not m_name:
        return None

    func_name = m_name.group(1)
    if func_name in _C_DECL_NON_VAR_NAMES:
        return None

    prefix = before_paren[: m_name.start()].strip()
    # C99/C11 函数定义必须包含返回类型标识
    if not prefix or not _RE_IDENT.search(prefix):
        return None

    prefix_words = set(_RE_IDENT.findall(prefix))
    is_static = "static" in prefix_words
    name_abs_offset = header_base_offset + m_name.start(1)
    params_text = stripped[open_paren + 1 : close_paren]
    return func_name, name_abs_offset, is_static, params_text


def _extract_calls_in_body(sf: _ScannedFile, body_text: str, body_offset: int) -> tuple[tuple[str, int], ...]:
    calls: list[tuple[str, int]] = []
    for m in _RE_CALL_CANDIDATE.finditer(body_text):
        callee = m.group(1)
        if callee in _C_CONTROL_AND_OPERATORS:
            continue
        # 排除结构体成员函数指针调用（如 ops->write(...) 或 dev.read(...)）
        pos = m.start(1) - 1
        while pos >= 0 and body_text[pos] in (" ", "\t", "\r", "\n"):
            pos -= 1
        if pos >= 0 and body_text[pos] == ".":
            continue
        if pos >= 1 and body_text[pos - 1 : pos + 1] == "->":
            continue
        line_no = sf.line_at(body_offset + m.start(1))
        calls.append((callee, line_no))
    return tuple(calls)


def _blank_interior_braces(text: str) -> str:
    """将顶层声明内部的花括号内容（如结构体成员或初始化列表）替换为空格，保留外层 `{}` 与偏移量。"""
    out = list(text)
    depth = 0
    for idx, ch in enumerate(text):
        if ch == "{":
            if depth >= 1:
                out[idx] = " "
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
            if depth >= 1:
                out[idx] = " "
        elif depth >= 1 and ch not in ("\n", "\r"):
            out[idx] = " "
    return "".join(out)


def _split_top_level_commas(text: str) -> list[tuple[int, str]]:
    """按圆括号/花括号/方括号深度为 0 的逗号切分声明子项，返回 `(relative_offset, chunk)`。"""
    chunks: list[tuple[int, str]] = []
    p_depth = 0
    b_depth = 0
    s_depth = 0
    start = 0
    for idx, ch in enumerate(text):
        if ch == "(":
            p_depth += 1
        elif ch == ")":
            p_depth = max(0, p_depth - 1)
        elif ch == "{":
            b_depth += 1
        elif ch == "}":
            b_depth = max(0, b_depth - 1)
        elif ch == "[":
            s_depth += 1
        elif ch == "]":
            s_depth = max(0, s_depth - 1)
        elif ch == "," and p_depth == 0 and b_depth == 0 and s_depth == 0:
            chunks.append((start, text[start:idx]))
            start = idx + 1
    chunks.append((start, text[start:]))
    return chunks


def _split_before_assign(chunk: str) -> str:
    """截取声明子项中顶层 `=` 初始化器之前的左侧声明部分。"""
    p_depth = 0
    b_depth = 0
    s_depth = 0
    for idx, ch in enumerate(chunk):
        if ch == "(":
            p_depth += 1
        elif ch == ")":
            p_depth = max(0, p_depth - 1)
        elif ch == "{":
            b_depth += 1
        elif ch == "}":
            b_depth = max(0, b_depth - 1)
        elif ch == "[":
            s_depth += 1
        elif ch == "]":
            s_depth = max(0, s_depth - 1)
        elif ch == "=" and p_depth == 0 and b_depth == 0 and s_depth == 0:
            return chunk[:idx]
    return chunk


def _is_const_binding(lhs_spec: str) -> bool:
    """判断变量自身绑定是否为只读 `const`（区分 `const int x` 与可变指针 `const char *p`）。"""
    words = _RE_IDENT.findall(lhs_spec)
    if "const" not in words:
        return False
    last_star = lhs_spec.rfind("*")
    if last_star == -1:
        return True
    after_star_words = _RE_IDENT.findall(lhs_spec[last_star + 1 :])
    return "const" in after_star_words


def _strip_decl_attributes(text: str) -> str:
    """移除声明中的 `__attribute__((...))` 与 `_Alignas(...)` 并保持字符长度不变，避免圆括号干扰函数原型识别。"""
    rx = re.compile(r"(?:__attribute__\s*\(\([^;{}]*?\)\)|(?:_Alignas|alignas)\s*\([^;{}]*?\))")
    return rx.sub(lambda m: " " * len(m.group(0)), text)


def _parse_global_decl_stmt(sf: _ScannedFile, stmt_raw: str, stmt_offset: int) -> list[_GlobalVarDecl]:
    blanked = _strip_decl_attributes(_blank_interior_braces(stmt_raw))
    if not blanked.strip():
        return []

    chunks = _split_top_level_commas(blanked)
    if not chunks:
        return []

    first_off, first_chunk = chunks[0]
    first_lhs = _split_before_assign(first_chunk)
    first_words = _RE_IDENT.findall(first_lhs)
    if not first_words or "typedef" in first_words:
        return []

    # 排除函数原型声明（保留 `void (*g_fn)(int)` 形式的函数指针变量）
    if "(" in first_lhs and not re.search(r"\(\s*\*\s*[A-Za-z_][A-Za-z0-9_]*\s*\)", first_lhs):
        return []

    is_extern = "extern" in first_words
    is_static = "static" in first_words
    base_volatile = bool(_RE_VOLATILE_QUAL.search(first_lhs))

    results: list[_GlobalVarDecl] = []
    for idx, (c_off, chunk) in enumerate(chunks):
        lhs = _split_before_assign(chunk)
        if not lhs.strip() or lhs.rstrip().endswith("}"):
            continue

        # 排除单独混入的函数声明
        if "(" in lhs:
            m_fp = re.search(r"\(\s*\*\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", lhs)
            if not m_fp:
                continue
            var_name = m_fp.group(1)
            name_in_chunk = m_fp.start(1)
        else:
            # 去掉末尾数组维度 `[N][M]`
            m_arr = re.search(r"([A-Za-z_][A-Za-z0-9_]*)(\s*\[[^\]]*\])*\s*$", lhs)
            if not m_arr:
                continue
            var_name = m_arr.group(1)
            name_in_chunk = m_arr.start(1)
            if idx == 0:
                before_var = lhs[:name_in_chunk]
                if not _RE_IDENT.search(before_var):
                    continue

        if var_name in _C_DECL_NON_VAR_NAMES:
            continue

        full_decl_for_var = first_lhs if idx == 0 else (first_lhs[: first_lhs.rfind(" ") + 1] + lhs)
        if _is_const_binding(full_decl_for_var):
            continue

        is_vol = base_volatile or bool(_RE_VOLATILE_QUAL.search(lhs))
        line_no = sf.line_at(stmt_offset + c_off + name_in_chunk)
        results.append(
            _GlobalVarDecl(
                name=var_name,
                file=sf.rel_path,
                line=line_no,
                is_volatile=is_vol,
                is_extern=is_extern,
                is_static=is_static,
            )
        )
    return results


def _parse_functions_and_globals(sf: _ScannedFile) -> tuple[list[_FunctionDef], list[_GlobalVarDecl]]:
    code = sf.cleaned_c
    n = len(code)
    funcs: list[_FunctionDef] = []
    globals_out: list[_GlobalVarDecl] = []

    stmt_start = 0
    paren_depth = 0
    i = 0

    while i < n:
        ch = code[i]
        if ch == "(":
            paren_depth += 1
            i += 1
            continue
        if ch == ")":
            paren_depth = max(0, paren_depth - 1)
            i += 1
            continue

        if ch == "{" and paren_depth == 0:
            header_slice = code[stmt_start:i]
            header_stripped = header_slice.strip()

            # 兼容头文件/C++ 中的 `extern "C" {`（字符串清空后变为 `extern {`）与 `namespace xxx {`
            if header_stripped == "extern" or header_stripped.startswith("namespace "):
                i += 1
                stmt_start = i
                continue

            close_brace = _find_matching_close_brace(code, i)
            fn_parsed = _try_parse_func_header(header_slice, stmt_start)
            if fn_parsed is not None:
                fn_name, name_abs_off, is_static, params_text = fn_parsed
                body_offset = i + 1
                body_text = code[body_offset:close_brace]
                calls = _extract_calls_in_body(sf, body_text, body_offset)
                funcs.append(
                    _FunctionDef(
                        name=fn_name,
                        file=sf.rel_path,
                        start_line=sf.line_at(name_abs_off),
                        end_line=sf.line_at(close_brace),
                        is_static=is_static,
                        is_header=sf.is_header,
                        params_text=params_text,
                        body_text=body_text,
                        body_offset=body_offset,
                        calls=calls,
                    )
                )
                i = close_brace + 1
                stmt_start = i
                continue

            # 结构体/枚举定义体或全局初始化列表 `{ ... }`，跳过内部继续寻找 `;`
            i = close_brace + 1
            continue

        if ch == "}" and paren_depth == 0:
            # 对应 `extern "C" { ... }` 或 `namespace { ... }` 的闭合括号
            i += 1
            stmt_start = i
            continue

        if ch == ";" and paren_depth == 0:
            stmt_raw = code[stmt_start:i]
            globals_out.extend(_parse_global_decl_stmt(sf, stmt_raw, stmt_start))
            i += 1
            stmt_start = i
            continue

        i += 1

    return funcs, globals_out


# ---------------------------------------------------------------------------
# 检查项 B：ISR 传递调用链违禁阻塞扫描
# ---------------------------------------------------------------------------


def _check_isr_call_graph(
    all_funcs: list[_FunctionDef],
    sf_by_rel: dict[str, _ScannedFile],
    config: ArchConfig,
) -> tuple[list[_FunctionDef], set[tuple[str, str, int]], list[ArchViolation]]:
    isr_regexes = [re.compile(pat) for pat in config.isr_patterns]
    forbidden_exact = {f for f in config.forbidden_in_isr if "*" not in f and "?" not in f}
    forbidden_globs = [f for f in config.forbidden_in_isr if "*" in f or "?" in f]

    def _is_forbidden_callee(name: str) -> bool:
        if name in forbidden_exact:
            return True
        return any(fnmatch.fnmatchcase(name, pat) for pat in forbidden_globs)

    funcs_by_name: dict[str, list[_FunctionDef]] = {}
    for fn in all_funcs:
        funcs_by_name.setdefault(fn.name, []).append(fn)

    def _resolve_callees(caller: _FunctionDef, callee_name: str) -> list[_FunctionDef]:
        cands = funcs_by_name.get(callee_name)
        if not cands:
            return []
        same_file = [f for f in cands if f.file == caller.file]
        if same_file:
            return same_file
        extern_defs = [f for f in cands if not f.is_static]
        if extern_defs:
            return extern_defs
        header_inlines = [f for f in cands if f.is_header]
        return header_inlines

    isr_roots = [fn for fn in all_funcs if any(rx.search(fn.name) for rx in isr_regexes)]
    isr_tree_keys: set[tuple[str, str, int]] = set()
    violations: list[ArchViolation] = []
    reported: set[tuple[str, str, int, str]] = set()

    for root_fn in isr_roots:
        root_key = (root_fn.file, root_fn.name, root_fn.start_line)
        isr_tree_keys.add(root_key)
        visited_in_bfs: set[tuple[str, str, int]] = {root_key}
        queue: deque[tuple[_FunctionDef, tuple[str, ...]]] = deque([(root_fn, (root_fn.name,))])

        while queue:
            cur_fn, chain = queue.popleft()
            for callee_name, call_line in cur_fn.calls:
                if _is_forbidden_callee(callee_name):
                    rep_key = (root_fn.name, cur_fn.file, call_line, callee_name)
                    if rep_key not in reported:
                        reported.add(rep_key)
                        full_chain = (*chain, callee_name)
                        chain_str = " -> ".join(full_chain)
                        violations.append(
                            ArchViolation(
                                category="isr_unsafe_call",
                                severity="error",
                                file=cur_fn.file,
                                line=call_line,
                                symbol=callee_name,
                                layer=(
                                    sf_by_rel[cur_fn.file].layer_rule.name
                                    if cur_fn.file in sf_by_rel and sf_by_rel[cur_fn.file].layer_rule
                                    else None
                                ),
                                call_chain=full_chain,
                                message=(
                                    f"ISR unsafe call to blocking/non-reentrant API '{callee_name}' "
                                    f"via call chain: {chain_str} "
                                    f"(ISR entry at {root_fn.file}:{root_fn.start_line}, "
                                    f"call site at {cur_fn.file}:{call_line})"
                                ),
                            )
                        )
                    continue

                for target_fn in _resolve_callees(cur_fn, callee_name):
                    t_key = (target_fn.file, target_fn.name, target_fn.start_line)
                    isr_tree_keys.add(t_key)
                    if t_key not in visited_in_bfs:
                        visited_in_bfs.add(t_key)
                        queue.append((target_fn, (*chain, target_fn.name)))

    return isr_roots, isr_tree_keys, violations


# ---------------------------------------------------------------------------
# 检查项 C：ISR ↔ 主循环共享全局变量漏加 volatile 扫描
# ---------------------------------------------------------------------------


def _is_shadowed_in_func(fn: _FunctionDef, var_name: str) -> bool:
    esc = re.escape(var_name)
    pat = re.compile(rf"(?:{_RE_LOCAL_SHADOW_TYPES.pattern}|[A-Za-z_][A-Za-z0-9_]*_t)\s*\*?\s*\b{esc}\b")
    return bool(pat.search(fn.params_text) or pat.search(fn.body_text))


def _find_var_write_line(sf: _ScannedFile, fn: _FunctionDef, var_name: str) -> int | None:
    if var_name not in fn.body_text or _is_shadowed_in_func(fn, var_name):
        return None
    esc = re.escape(var_name)
    access = rf"(?<![.>])\b{esc}\b(?:\s*(?:\.\s*[A-Za-z_]\w*|\[[^\]\n]*\]))*"
    rx_write = re.compile(
        rf"(?:(?:\+\+|--)\s*{access}|{access}\s*(?:\+\+|--|=(?!=)|\+=|-=|\*=|/=|%=|\|=|&=|\^=|<<=|>>=))"
    )
    m = rx_write.search(fn.body_text)
    if not m:
        return None
    return sf.line_at(fn.body_offset + m.start())


def _find_var_read_line(sf: _ScannedFile, fn: _FunctionDef, var_name: str) -> int | None:
    if var_name not in fn.body_text or _is_shadowed_in_func(fn, var_name):
        return None
    esc = re.escape(var_name)
    rx_access = re.compile(rf"(?<![.>])\b{esc}\b(?:\s*(?:\.\s*[A-Za-z_]\w*|\[[^\]\n]*\]))*")
    rx_plain_assign = re.compile(r"^\s*=(?!=)")
    for m in rx_access.finditer(fn.body_text):
        tail = fn.body_text[m.end() :]
        # 单纯覆盖写入（如 `g_flag = 0;`）不计为读取；轮询等待、条件判断、复合赋值均计为读取
        if rx_plain_assign.match(tail):
            continue
        return sf.line_at(fn.body_offset + m.start())
    return None


def _check_missing_volatile(
    all_funcs: list[_FunctionDef],
    isr_tree_keys: set[tuple[str, str, int]],
    all_globals: list[_GlobalVarDecl],
    sf_by_rel: dict[str, _ScannedFile],
) -> list[ArchViolation]:
    if not isr_tree_keys or not all_globals:
        return []

    isr_funcs = [fn for fn in all_funcs if (fn.file, fn.name, fn.start_line) in isr_tree_keys]
    non_isr_funcs = [fn for fn in all_funcs if (fn.file, fn.name, fn.start_line) not in isr_tree_keys]
    if not isr_funcs or not non_isr_funcs:
        return []

    # 聚合同名全局变量声明：优先定位非 extern 定义；若任一可见声明漏写 volatile 则作为候选检查
    decls_by_key: dict[tuple[str, str | None], list[_GlobalVarDecl]] = {}
    for gd in all_globals:
        scope_key = (gd.name, gd.file if gd.is_static else None)
        decls_by_key.setdefault(scope_key, []).append(gd)

    violations: list[ArchViolation] = []
    for (var_name, static_file), decl_list in decls_by_key.items():
        if all(d.is_volatile for d in decl_list):
            continue

        # 选定报告锚点：优先选未加 volatile 的实体定义，其次选未加 volatile 的 extern 声明
        non_vol_defs = [d for d in decl_list if not d.is_extern and not d.is_volatile]
        non_vol_any = [d for d in decl_list if not d.is_volatile]
        anchor = non_vol_defs[0] if non_vol_defs else non_vol_any[0]

        isr_hit: tuple[_FunctionDef, int] | None = None
        for fn in isr_funcs:
            if static_file is not None and fn.file != static_file:
                continue
            w_line = _find_var_write_line(sf_by_rel[fn.file], fn, var_name)
            if w_line is not None:
                isr_hit = (fn, w_line)
                break
        if isr_hit is None:
            continue

        main_hit: tuple[_FunctionDef, int] | None = None
        for fn in non_isr_funcs:
            if static_file is not None and fn.file != static_file:
                continue
            r_line = _find_var_read_line(sf_by_rel[fn.file], fn, var_name)
            if r_line is not None:
                main_hit = (fn, r_line)
                break
        if main_hit is None:
            continue

        isr_fn, isr_line = isr_hit
        reader_fn, reader_line = main_hit
        anchor_sf = sf_by_rel.get(anchor.file)
        violations.append(
            ArchViolation(
                category="missing_volatile",
                severity="warning",
                file=anchor.file,
                line=anchor.line,
                symbol=var_name,
                layer=anchor_sf.layer_rule.name if anchor_sf and anchor_sf.layer_rule else None,
                message=(
                    f"Shared global variable '{var_name}' is modified in ISR context "
                    f"({isr_fn.name} at {isr_fn.file}:{isr_line}) and read in non-ISR context "
                    f"({reader_fn.name} at {reader_fn.file}:{reader_line}) without 'volatile' or atomic "
                    f"qualification — compiler -O2 optimization may cache stale values and hang loops!"
                ),
            )
        )

    return violations
