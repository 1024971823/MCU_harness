"""编译输出解析器（纯函数，无副作用，方便单元测试）。

职责：把 GCC / Clang / GNU ld / CMake / Ninja / Make 混杂的终端输出，提炼成结构化的诊断列表。
这一步相当于给大模型做“信号滤波”：几千行编译日志 -> 十几条关键信息。

支持识别：
    1. GCC/Clang 诊断：   main.c:12:5: error: 'x' undeclared
    2. 函数上下文：       main.c: In function 'main':
    3. 源码片段（gcc 9+）：   12 |   x = 1;   /     |   ^
    4. 链接错误：         undefined reference / multiple definition / region `FLASH' overflowed
    5. CMake 配置错误：   CMake Error at CMakeLists.txt:12 (add_executable):
    6. Make 报错与目标：  make: *** [Makefile:184: build/main.o] Error 1 / missing separator
    7. 内存占用表：       -Wl,--print-memory-usage 及 arm-none-eabi-size 输出
    8. Ninja 失败目标：   FAILED: CMakeFiles/app.elf.dir/main.c.obj
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field

# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class Diagnostic:
    """一条编译/链接/配置诊断。"""

    severity: str  # "error" | "warning" | "note"
    message: str
    category: str = "compiler"  # "compiler" | "linker" | "cmake" | "make"
    file: str | None = None
    line: int | None = None
    column: int | None = None
    function: str | None = None  # 出错所在函数（来自 "In function 'xxx':"）
    flag: str | None = None  # 触发的告警选项，如 -Wunused-variable
    snippet: list[str] = field(default_factory=list)  # gcc 打印的源码行 + ^ 指示
    count: int = 1  # 相同诊断出现次数（头文件被多个 .c 包含时会重复）

    def location(self) -> str:
        if not self.file:
            return ""
        loc = self.file
        if self.line is not None:
            loc += f":{self.line}"
            if self.column is not None:
                loc += f":{self.column}"
        return loc

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MemoryRegion:
    """链接器 --print-memory-usage 输出的一行。"""

    name: str  # FLASH / RAM / CCMRAM ...
    used_bytes: int
    total_bytes: int
    percent: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BinarySize:
    """size / arm-none-eabi-size 的 Berkeley 格式段大小输出。"""

    file: str
    text_bytes: int
    data_bytes: int
    bss_bytes: int
    total_bytes: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ParsedOutput:
    errors: list[Diagnostic] = field(default_factory=list)
    warnings: list[Diagnostic] = field(default_factory=list)
    memory: list[MemoryRegion] = field(default_factory=list)
    sizes: list[BinarySize] = field(default_factory=list)
    failed_targets: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 正则表达式
# ---------------------------------------------------------------------------

# main.c:12:5: error: 'x' undeclared [-Werror=...]
# 注意 Windows 路径 C:\foo\main.c 里有冒号，所以文件名部分用非贪婪匹配 + 后面必须跟 :数字
_RE_GCC_DIAG = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?:(?P<col>\d+):)?\s*"
    r"(?P<sev>fatal error|error|warning|note):\s*(?P<msg>.*)$"
)
# main.c: In function 'main':
_RE_IN_FUNCTION = re.compile(r"^(?P<file>.+?):\s*In function [‘'`](?P<func>[^’']+)[’']:\s*$")
# 末尾的 [-Wunused-variable] / [-Werror=unused-variable]
_RE_FLAG = re.compile(r"\s*\[(?P<flag>-W[^\]]+)\]\s*$")
# gcc 9+ 源码片段：   12 |   int x;    或       |   ^~~
_RE_SNIPPET = re.compile(r"^\s*\d*\s+\|")

# 链接器：main.c:57:(.text.main+0x1a): undefined reference to `foo'
#         C:\proj\main.c:57:(.text.main+0x1a): undefined reference to `foo'
#         foo.o:(.bss+0x0): multiple definition of `bar'
_RE_LD_OBJ_LOC = re.compile(
    r"^(?P<file>(?:[A-Za-z]:[/\\])?[^\s:]+?):(?:(?P<line>\d+):(?:\([^)]*\):)?|\([^)]*\):)\s*(?P<msg>.*)$"
)
_RE_WIN_DRIVE = re.compile(r"^[A-Za-z]:[/\\]")
_LD_ERROR_KEYWORDS = (
    "undefined reference to",
    "multiple definition of",
    "will not fit in region",
    "overflowed by",
    "cannot find",
    "cannot open linker script",
    "no such file",
    "relocation truncated",
)
# ld / ld.exe / arm-none-eabi-ld 前缀
_RE_LD_PREFIX = re.compile(r"^(?:.*[/\\])?(?:[\w.-]+-)?ld(?:\.bfd|\.gold|\.lld)?(?:\.exe)?:\s*(?P<msg>.*)$")
_RE_COLLECT2 = re.compile(r"^collect2(?:\.exe)?:\s*error:")

# CMake Error at CMakeLists.txt:12 (add_executable):
_RE_CMAKE = re.compile(
    r"^CMake (?P<sev>Error|Warning)(?: \(dev\))?(?: at (?P<file>.+?):(?P<line>\d+)(?: \((?P<cmd>[^)]*)\))?)?:\s*(?P<msg>.*)$"
)

# 内存占用：      FLASH:       12345 B       512 KB      2.35%
_RE_MEM = re.compile(
    r"^\s*(?P<name>[\w.]+):\s+(?P<used>\d+(?:\.\d+)?)\s*(?P<uu>B|KB|MB|GB)\s+"
    r"(?P<total>\d+(?:\.\d+)?)\s*(?P<tu>B|KB|MB|GB)\s+(?P<pct>[\d.]+)%\s*$"
)
_UNIT = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}

# FAILED: CMakeFiles/app.elf.dir/src/main.c.obj
_RE_NINJA_FAILED = re.compile(r"^FAILED:\s*(?P<target>.+?)\s*$")

# Make: make: *** [Makefile:184: build/main.o] Error 1
_RE_MAKE_TARGET_ERR = re.compile(
    r"^(?:.*[/\\])?(?:mingw32-|g)?make(?:\.exe)?(?:\[\d+\])?:\s*\*\*\*\s*"
    r"\[(?P<file>[^:\]]+):(?P<line>\d+):\s*(?P<target>[^\]]+)\]\s*Error\s+(?P<code>\d+)\s*$"
)
# Make 自身规则错误：Makefile:42: *** missing separator.  Stop.
_RE_MAKE_FILE_ERR = re.compile(
    r"^(?P<file>[^:\s][^:]*):(?P<line>\d+):\s*\*\*\*\s*(?P<msg>.+?)(?:\s+Stop\.)?\s*$"
)
# Make 自身错误：make: *** No rule to make target 'foo'.  Stop.
_RE_MAKE_GENERIC_ERR = re.compile(
    r"^(?:.*[/\\])?(?:mingw32-|g)?make(?:\[\d+\])?:\s*\*\*\*\s*(?P<msg>.+?)(?:\s+Stop\.)?\s*$"
)
# Make 找不到命令：make: arm-none-eabi-gcc: No such file or directory / Command not found
_RE_MAKE_CMD_ERR = re.compile(
    r"^(?:.*[/\\])?(?:mingw32-|g)?make(?:\[\d+\])?:\s*"
    r"(?P<msg>.+?(?:No such file or directory|Command not found|Permission denied).*?)\s*$"
)

# arm-none-eabi-size Berkeley 格式：
#    text    data     bss     dec     hex filename
#   12340     128    2048   14516    38b4 build/app.elf
_RE_SIZE_HEADER = re.compile(r"^\s*text\s+data\s+bss\s+dec\s+hex\s+filename\s*$")
_RE_SIZE_ROW = re.compile(
    r"^\s*(?P<text>\d+)\s+(?P<data>\d+)\s+(?P<bss>\d+)\s+(?P<dec>\d+)\s+[0-9a-fA-F]+\s+(?P<file>.+?)\s*$"
)

_MAX_SNIPPET_LINES = 3
_MAX_CMAKE_MSG = 600

# 占位诊断：用于“这条不需要保留，但它后面的源码片段要一起吞掉”的场景
_SKIP = Diagnostic(severity="skip", message="", category="skip")


# ---------------------------------------------------------------------------
# 解析主函数
# ---------------------------------------------------------------------------


def parse_build_output(text: str, project_dir: str | None = None) -> ParsedOutput:
    """解析编译输出。

    Args:
        text: CMake/Ninja/GCC 的完整 stdout+stderr。
        project_dir: 工程根目录。若提供，会把绝对路径缩短为相对路径，进一步节省上下文。
    """
    result = ParsedOutput()
    seen: dict[tuple, Diagnostic] = {}
    current_function: dict[str, str] = {}  # file -> function
    last: Diagnostic | None = None  # 最近一条诊断，用于挂载源码片段 / CMake 续行
    in_cmake_block = False
    expect_size_row = False
    make_target_errors: list[Diagnostic] = []

    lines = text.splitlines()
    for raw in lines:
        line = raw.rstrip()

        # --- arm-none-eabi-size 数据行 ------------------------------------
        if expect_size_row:
            expect_size_row = False
            sm = _RE_SIZE_ROW.match(line)
            if sm:
                result.sizes.append(
                    BinarySize(
                        file=_norm(sm["file"], project_dir) or sm["file"].strip(),
                        text_bytes=int(sm["text"]),
                        data_bytes=int(sm["data"]),
                        bss_bytes=int(sm["bss"]),
                        total_bytes=int(sm["dec"]),
                    )
                )
                last = None
                continue

        # --- CMake 错误块的续行（缩进行属于正文；空行可能出现在正文中间） ---
        if in_cmake_block:
            if not line.strip():
                continue
            if line.startswith("  "):
                assert last is not None
                if len(last.message) < _MAX_CMAKE_MSG:
                    last.message = (last.message + " " + line.strip()).strip()
                continue
            in_cmake_block = False

        # --- 源码片段：紧跟在 gcc 诊断之后 -------------------------------
        if last is not None and _RE_SNIPPET.match(line):
            if last is not _SKIP and last.category == "compiler" and len(last.snippet) < _MAX_SNIPPET_LINES:
                last.snippet.append(line)
            continue

        # --- 函数上下文 ---------------------------------------------------
        m = _RE_IN_FUNCTION.match(line)
        if m:
            current_function[_norm(m["file"], project_dir)] = m["func"]
            last = None
            continue

        # --- GCC/Clang 标准诊断 -------------------------------------------
        m = _RE_GCC_DIAG.match(line)
        if m:
            sev = "error" if m["sev"] in ("error", "fatal error") else m["sev"]
            msg = m["msg"].strip()
            flag = None
            fm = _RE_FLAG.search(msg)
            if fm:
                flag = fm["flag"]
                msg = msg[: fm.start()].rstrip()
                # -Werror=xxx 会让 warning 显示为 error，这里保留原 severity
            file = _norm(m["file"], project_dir)
            diag = Diagnostic(
                severity=sev,
                message=msg,
                category="compiler",
                file=file,
                line=int(m["line"]),
                column=int(m["col"]) if m["col"] else None,
                function=current_function.get(file),
                flag=flag,
            )
            last = _add(result, seen, diag)
            continue

        # --- CMake 配置诊断 -----------------------------------------------
        m = _RE_CMAKE.match(line)
        if m:
            diag = Diagnostic(
                severity="error" if m["sev"] == "Error" else "warning",
                message=m["msg"].strip(),
                category="cmake",
                file=_norm(m["file"], project_dir) if m["file"] else None,
                line=int(m["line"]) if m["line"] else None,
                function=m["cmd"],
            )
            last = _add(result, seen, diag, merge=False)
            in_cmake_block = True
            continue

        # --- collect2 只是“链接失败”的总结行，最后再决定是否保留 -----------
        if _RE_COLLECT2.match(line):
            last = None
            continue

        # --- 链接器诊断 ---------------------------------------------------
        diag = _parse_linker_line(line, project_dir)
        if diag is not None:
            last = _add(result, seen, diag)
            continue

        # --- 内存占用表 ---------------------------------------------------
        m = _RE_MEM.match(line)
        if m:
            result.memory.append(
                MemoryRegion(
                    name=m["name"],
                    used_bytes=int(float(m["used"]) * _UNIT[m["uu"]]),
                    total_bytes=int(float(m["total"]) * _UNIT[m["tu"]]),
                    percent=float(m["pct"]),
                )
            )
            last = None
            continue

        # --- arm-none-eabi-size 表头 --------------------------------------
        if _RE_SIZE_HEADER.match(line):
            expect_size_row = True
            last = None
            continue

        # --- Ninja 失败目标 -----------------------------------------------
        m = _RE_NINJA_FAILED.match(line)
        if m:
            result.failed_targets.append(m["target"])
            last = None
            continue

        # --- Make 失败目标（先记录目标，没有更具体报错时再兜底展示） ------
        m = _RE_MAKE_TARGET_ERR.match(line)
        if m:
            tgt = m["target"].strip()
            if tgt not in result.failed_targets:
                result.failed_targets.append(tgt)
            make_target_errors.append(
                Diagnostic(
                    severity="error",
                    message=f"target '{tgt}' failed (Error {m['code']})",
                    category="make",
                    file=_norm(m["file"], project_dir),
                    line=int(m["line"]),
                )
            )
            last = None
            continue

        # --- Make 自身规则/命令报错 ---------------------------------------
        m = _RE_MAKE_FILE_ERR.match(line)
        if m:
            diag = Diagnostic(
                severity="error",
                message=m["msg"].strip(),
                category="make",
                file=_norm(m["file"], project_dir),
                line=int(m["line"]),
            )
            last = _add(result, seen, diag)
            continue

        m = _RE_MAKE_GENERIC_ERR.match(line) or _RE_MAKE_CMD_ERR.match(line)
        if m:
            diag = Diagnostic(
                severity="error",
                message=m["msg"].strip(),
                category="make",
            )
            last = _add(result, seen, diag)
            continue

        # 其他行（进度条、命令行回显等）全部丢弃 —— 这正是“降噪”的意义
        last = None

    # 只有 collect2 / make 目标失败，而没有任何具体编译/链接错误时，补兜底错误
    if not result.errors and any(_RE_COLLECT2.match(l) for l in lines):
        result.errors.append(
            Diagnostic(severity="error", message="ld returned 1 exit status", category="linker")
        )
    if not result.errors and make_target_errors:
        result.errors.extend(make_target_errors)
    return result


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------


def _parse_linker_line(line: str, project_dir: str | None) -> Diagnostic | None:
    msg = None
    file = None
    lineno = None

    m = _RE_LD_PREFIX.match(line)
    if m:
        msg = m["msg"].strip()
        # "ld: main.c.obj: in function `main':" 只是上下文，不算错误
        if msg.endswith("':") and "in function" in msg:
            return None
        # ld 前缀后面可能还嵌套 "file:(.text+0x0): undefined reference..."
        inner = _RE_LD_OBJ_LOC.match(msg)
        if inner and any(k in inner["msg"] for k in _LD_ERROR_KEYWORDS):
            file, msg = inner["file"], inner["msg"].strip()
            lineno = int(inner["line"]) if inner["line"] else None
    else:
        m = _RE_LD_OBJ_LOC.match(line)
        if m and any(k in m["msg"] for k in _LD_ERROR_KEYWORDS):
            file, msg = m["file"], m["msg"].strip()
            lineno = int(m["line"]) if m["line"] else None

    if msg is None:
        return None
    is_warning = msg.lower().startswith("warning")
    if not is_warning and not any(k in msg for k in _LD_ERROR_KEYWORDS) and "error" not in msg.lower():
        return None

    sev = "warning" if is_warning else "error"
    return Diagnostic(
        severity=sev,
        message=msg,
        category="linker",
        file=_norm(file, project_dir) if file else None,
        line=lineno,
    )


def _add(result: ParsedOutput, seen: dict, diag: Diagnostic, merge: bool = True) -> Diagnostic:
    """加入诊断列表并去重；返回实际存储的那条（便于挂载片段）。"""
    if diag.severity == "note":
        # note 是对上一条诊断的补充说明（如“声明在这里”），MVP 阶段先丢弃以节省上下文。
        # 返回“占位”对象：其后的源码片段行会被吞掉，而不会误挂到上一条诊断上。
        return _SKIP
    key = (diag.category, diag.severity, diag.file, diag.line, diag.column, diag.message)
    if merge and key in seen:
        seen[key].count += 1
        # 重复诊断：同样返回占位对象，避免重复的源码片段写进已有诊断
        return _SKIP
    seen[key] = diag
    (result.errors if diag.severity == "error" else result.warnings).append(diag)
    return diag


def _norm(path: str | None, project_dir: str | None) -> str | None:
    """绝对路径 -> 相对工程根目录的路径（统一用 / 分隔，兼容 Windows 盘符路径）。"""
    if not path:
        return path
    p = path.strip()
    if project_dir:
        if _RE_WIN_DRIVE.match(p) and _RE_WIN_DRIVE.match(project_dir.strip()):
            from pathlib import PureWindowsPath

            try:
                rel = PureWindowsPath(p).relative_to(PureWindowsPath(project_dir.strip()))
                return rel.as_posix()
            except ValueError:
                return p.replace("\\", "/")
        if os.path.isabs(p):
            try:
                rel = os.path.relpath(p, project_dir)
                if not rel.startswith(".."):
                    p = rel
            except ValueError:  # Windows 跨盘符
                pass
    return p.replace("\\", "/")
