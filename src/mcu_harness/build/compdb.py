"""跨构建系统 `compile_commands.json` 编译数据库自动生成器。

支持两类嵌入式工程，让 `clangd` 与静态代码分析器零配置开箱即用：
    1. CMake 工程：
       调用 `cmake -S <proj> -B <build> -DCMAKE_EXPORT_COMPILE_COMMANDS=ON ...`
       生成 `<build>/compile_commands.json`，并自动同步软链接（或拷贝）至工程根目录。
    2. 传统 Makefile 工程（如 STM32CubeMX Makefile，无需安装外部 `bear` 工具）：
       执行无副作用干跑 `make -Bn`，毫秒级捕获完整编译命令行；
       使用 `shlex` 解析 `*gcc` / `*g++` / `*clang` 的 `-c` 编译动作，跟踪递归 Make 的
       `Entering directory` / `Leaving directory` 目录栈与行内 `cd`，将源文件与 `.o` 归一化为
       绝对路径后输出标准 JSON 编译数据库至 `<project_dir>/compile_commands.json`。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .runner import _detect_build_system, _find_make_executable

_RE_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_RE_MAKE_ENTER = re.compile(
    r"^(?:.*[/\\])?(?:mingw32-|g)?make(?:\.exe)?(?:\[(?P<lvl>\d+)\])?:\s+"
    r"Entering directory [`'\"‘“](?P<dir>.+?)[`'\"’”]\s*$"
)
_RE_MAKE_LEAVE = re.compile(
    r"^(?:.*[/\\])?(?:mingw32-|g)?make(?:\.exe)?(?:\[(?P<lvl>\d+)\])?:\s+"
    r"Leaving directory [`'\"‘“](?P<dir>.+?)[`'\"’”]\s*$"
)
_RE_COMPILER_BIN = re.compile(
    r"^(?:[A-Za-z0-9_.-]+-)?(?:gcc|g\+\+|clang|clang\+\+|cc|c\+\+|armclang)(?:-\d+(?:\.\d+)*)?(?:\.exe)?$",
    re.IGNORECASE,
)
_RE_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")

_COMPILER_WRAPPERS = {"ccache", "sccache", "distcc", "icecc"}
_SOURCE_EXTS = {".c", ".cc", ".cpp", ".cxx", ".c++", ".s", ".sx", ".asm"}

# 分离式写法时吞掉下一个参数（避免把宏值、头文件目录或依赖文件误判为源文件）
_FLAGS_TAKING_NEXT_ARG = {
    "-o",
    "-I",
    "-isystem",
    "-iquote",
    "-idirafter",
    "-include",
    "-imacros",
    "-iprefix",
    "-iwithprefix",
    "-iwithprefixbefore",
    "-isysroot",
    "--sysroot",
    "-D",
    "-U",
    "-MF",
    "-MT",
    "-MQ",
    "-x",
    "-target",
    "--target",
    "-specs",
    "-T",
    "-L",
    "-l",
    "-u",
    "-Xlinker",
    "-Xpreprocessor",
    "-Xassembler",
    "-Xclang",
    "-param",
    "--param",
    "-aux-info",
    "-dumpbase",
    "-arch",
}


@dataclass
class CompDbResult:
    success: bool
    build_system: str  # "cmake" | "make" | "unknown"
    output_path: str | None = None
    root_link_path: str | None = None
    entry_count: int = 0
    files: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    duration_s: float = 0.0
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_text(self) -> str:
        status = "SUCCESS" if self.success else "FAILED"
        lines = [
            f"[compdb] {status} (system={self.build_system}, entries={self.entry_count}, {self.duration_s:.2f}s)"
        ]
        if self.output_path:
            lines.append(f"  output: {self.output_path}")
        if self.root_link_path and self.root_link_path != self.output_path:
            lines.append(f"  root_link: {self.root_link_path}")
        if self.message:
            lines.append(f"  note: {self.message}")
        if self.files:
            shown = self.files[:10]
            more = f" (+{len(self.files) - len(shown)} more)" if len(self.files) > len(shown) else ""
            lines.append(f"  sources: {', '.join(Path(f).name for f in shown)}{more}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def generate_compile_commands(
    project_dir: str | os.PathLike,
    *,
    build_dir: str | os.PathLike | None = None,
    makefile: str | os.PathLike | None = None,
    extra_args: list[str] | None = None,
    build_system: str = "auto",
    toolchain_file: str | os.PathLike | None = None,
    generator: str | None = None,
    target: str | None = None,
    link_to_root: bool = True,
    timeout_s: float = 120.0,
) -> CompDbResult:
    """为 CMake 或传统 Makefile 工程生成标准 `compile_commands.json`。"""
    t0 = time.monotonic()
    project = Path(project_dir).expanduser().resolve()

    system, mf_path, err = _detect_build_system(project, build_system, makefile)
    if err is not None:
        return CompDbResult(
            success=False,
            build_system=system,
            duration_s=time.monotonic() - t0,
            message=err,
        )

    if system == "cmake":
        return _generate_cmake_compdb(
            project=project,
            build_dir=build_dir,
            extra_args=extra_args,
            toolchain_file=toolchain_file,
            generator=generator,
            link_to_root=link_to_root,
            timeout_s=timeout_s,
            t0=t0,
        )

    return _generate_make_compdb(
        project=project,
        build_dir=build_dir,
        makefile_path=mf_path,
        target=target,
        extra_args=extra_args,
        link_to_root=link_to_root,
        timeout_s=timeout_s,
        t0=t0,
    )


# ---------------------------------------------------------------------------
# CMake 后端
# ---------------------------------------------------------------------------


def _generate_cmake_compdb(
    *,
    project: Path,
    build_dir: str | os.PathLike | None,
    extra_args: list[str] | None,
    toolchain_file: str | os.PathLike | None,
    generator: str | None,
    link_to_root: bool,
    timeout_s: float,
    t0: float,
) -> CompDbResult:
    cmake_bin = shutil.which("cmake")
    if cmake_bin is None:
        return CompDbResult(
            success=False,
            build_system="cmake",
            duration_s=time.monotonic() - t0,
            message="cmake not found in PATH",
        )

    build = Path(build_dir).expanduser() if build_dir else project / "build"
    if not build.is_absolute():
        build = (project / build).resolve()
    else:
        build = build.resolve()

    cmd = [cmake_bin, "-S", str(project), "-B", str(build)]

    # 若构建目录尚无缓存则自动选择支持导出编译数据库的生成器（Ninja 优先，回退 Unix Makefiles）
    has_cache = (build / "CMakeCache.txt").is_file()
    chosen_gen = generator
    if chosen_gen is None and not has_cache:
        chosen_gen = "Ninja" if shutil.which("ninja") else "Unix Makefiles"
    if chosen_gen:
        cmd.extend(["-G", chosen_gen])

    cmd.append("-DCMAKE_EXPORT_COMPILE_COMMANDS=ON")
    if toolchain_file is not None:
        tc = Path(toolchain_file).expanduser()
        if not tc.is_absolute():
            tc = (project / tc).resolve()
        cmd.append(f"-DCMAKE_TOOLCHAIN_FILE={tc}")
    if extra_args:
        cmd.extend(extra_args)

    rc, out, timed_out = _run_clean_env(cmd, cwd=project, timeout_s=timeout_s)
    commands = [shlex.join(cmd)]
    if timed_out:
        return CompDbResult(
            success=False,
            build_system="cmake",
            commands=commands,
            duration_s=time.monotonic() - t0,
            message=f"cmake configure timed out after {timeout_s}s",
        )
    if rc != 0:
        tail = " | ".join(l.strip() for l in out.splitlines() if l.strip())[-400:]
        return CompDbResult(
            success=False,
            build_system="cmake",
            commands=commands,
            duration_s=time.monotonic() - t0,
            message=f"cmake configure failed (rc={rc}): {tail}",
        )

    compdb_file = build / "compile_commands.json"
    if not compdb_file.is_file():
        return CompDbResult(
            success=False,
            build_system="cmake",
            commands=commands,
            duration_s=time.monotonic() - t0,
            message=f"CMake succeeded but {compdb_file} was not generated (generator may not support it)",
        )

    try:
        entries = json.loads(compdb_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return CompDbResult(
            success=False,
            build_system="cmake",
            commands=commands,
            duration_s=time.monotonic() - t0,
            message=f"Failed to read generated {compdb_file}: {e}",
        )

    root_link: str | None = None
    if link_to_root:
        root_dst = project / "compile_commands.json"
        root_link = _sync_compdb_to_root(compdb_file, root_dst)

    files = [str(item.get("file", "")) for item in entries if isinstance(item, dict) and item.get("file")]
    return CompDbResult(
        success=True,
        build_system="cmake",
        output_path=str(compdb_file),
        root_link_path=root_link,
        entry_count=len(entries),
        files=files,
        commands=commands,
        duration_s=time.monotonic() - t0,
    )


# ---------------------------------------------------------------------------
# Makefile 后端（无副作用 `make -Bn` 解析）
# ---------------------------------------------------------------------------


def _generate_make_compdb(
    *,
    project: Path,
    build_dir: str | os.PathLike | None,
    makefile_path: Path | None,
    target: str | None,
    extra_args: list[str] | None,
    link_to_root: bool,
    timeout_s: float,
    t0: float,
) -> CompDbResult:
    make_bin = _find_make_executable()
    if make_bin is None:
        return CompDbResult(
            success=False,
            build_system="make",
            duration_s=time.monotonic() - t0,
            message="make (or gmake / mingw32-make) not found in PATH",
        )

    mf_args: list[str] = []
    if makefile_path is not None and makefile_path.parent == project and makefile_path.name in (
        "GNUmakefile",
        "makefile",
        "Makefile",
    ):
        pass
    elif makefile_path is not None:
        mf_args = ["-f", str(makefile_path)]

    # -B 强制视为全部过期（即使工程已编译过也能完整打印每条 -c 命令），-n 仅打印不执行，-w 打印子目录切换
    cmd = [make_bin, "-Bnw", *mf_args]
    if target:
        cmd.append(target)
    if extra_args:
        cmd.extend(extra_args)

    rc, out, timed_out = _run_clean_env(cmd, cwd=project, timeout_s=timeout_s)
    commands = [shlex.join(cmd)]
    if timed_out:
        return CompDbResult(
            success=False,
            build_system="make",
            commands=commands,
            duration_s=time.monotonic() - t0,
            message=f"make -Bn timed out after {timeout_s}s",
        )
    if rc != 0:
        tail = " | ".join(l.strip() for l in out.splitlines() if l.strip())[-400:]
        return CompDbResult(
            success=False,
            build_system="make",
            commands=commands,
            duration_s=time.monotonic() - t0,
            message=f"make -Bn failed (rc={rc}): {tail}",
        )

    entries = parse_make_dry_run(out, default_dir=project)

    # 决定输出位置：默认写入 <project>/compile_commands.json；若指定了 build_dir 则同步写入
    root_compdb = project / "compile_commands.json"
    if build_dir is not None:
        bpath = Path(build_dir).expanduser()
        if not bpath.is_absolute():
            bpath = (project / bpath).resolve()
        else:
            bpath = bpath.resolve()
        bpath.mkdir(parents=True, exist_ok=True)
        out_file = bpath / "compile_commands.json"
    else:
        out_file = root_compdb

    payload = json.dumps(entries, indent=2, ensure_ascii=False) + "\n"
    out_file.write_text(payload, encoding="utf-8")

    root_link: str | None = str(root_compdb) if out_file == root_compdb else None
    if out_file != root_compdb and link_to_root:
        root_link = _sync_compdb_to_root(out_file, root_compdb)

    files = [e["file"] for e in entries]
    note = "" if entries else "make -Bn succeeded, but no '-c' compiler invocations were found"
    return CompDbResult(
        success=True,
        build_system="make",
        output_path=str(out_file),
        root_link_path=root_link,
        entry_count=len(entries),
        files=files,
        commands=commands,
        duration_s=time.monotonic() - t0,
        message=note,
    )


def parse_make_dry_run(output: str, *, default_dir: Path) -> list[dict[str, Any]]:
    """解析 `make -Bn` 输出文本，提取标准 LLVM `compile_commands.json` 条目列表。"""
    # 合并 Makefile 输出中的反斜杠续行
    joined_lines = _join_line_continuations(output)

    dir_stack: list[Path] = [default_dir.resolve()]
    entries: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()

    for raw_line in joined_lines:
        line = raw_line.strip()
        if not line:
            continue

        m_enter = _RE_MAKE_ENTER.match(line)
        if m_enter:
            entered = Path(m_enter["dir"]).expanduser()
            if not entered.is_absolute():
                entered = (dir_stack[-1] / entered).resolve()
            else:
                entered = entered.resolve()
            dir_stack.append(entered)
            continue

        m_leave = _RE_MAKE_LEAVE.match(line)
        if m_leave:
            if len(dir_stack) > 1:
                dir_stack.pop()
            continue

        if line.startswith(("make:", "make[", "gmake:", "gmake[", "#")):
            continue

        # 剥离极少数回显残留的 @ / - / + 前缀
        line = line.lstrip("@-+ \t")
        if not line:
            continue

        cur_cwd = dir_stack[-1]
        for sub_tokens in _split_shell_subcommands(line):
            if not sub_tokens:
                continue
            # 支持同一行内 `cd subdir && gcc -c ...`
            if sub_tokens[0] == "cd" and len(sub_tokens) >= 2:
                next_dir = Path(sub_tokens[1]).expanduser()
                cur_cwd = (cur_cwd / next_dir).resolve() if not next_dir.is_absolute() else next_dir.resolve()
                continue

            parsed = _extract_compile_entry(sub_tokens, cwd=cur_cwd)
            for item in parsed:
                dedup_key = (item["file"], item.get("output", ""))
                if dedup_key not in seen_keys:
                    seen_keys.add(dedup_key)
                    entries.append(item)

    return entries


def _join_line_continuations(text: str) -> list[str]:
    result: list[str] = []
    buf = ""
    for raw in text.splitlines():
        if raw.endswith("\\"):
            buf += raw[:-1] + " "
        else:
            result.append(buf + raw)
            buf = ""
    if buf:
        result.append(buf)
    return result


def _split_shell_subcommands(line: str) -> list[list[str]]:
    """用 shlex 切分单行 shell 语句，按 `&&` / `;` / `||` 拆成独立子命令。"""
    try:
        lex = shlex.shlex(line, posix=True, punctuation_chars=";&|")
        lex.whitespace_split = True
        lex.commenters = "#"
        tokens = list(lex)
    except ValueError:
        try:
            tokens = shlex.split(line, posix=True)
        except ValueError:
            return []

    subcmds: list[list[str]] = []
    cur: list[str] = []
    for tok in tokens:
        if tok in ("&&", ";", "||", "|", "&"):
            if cur:
                subcmds.append(cur)
                cur = []
        else:
            cur.append(tok)
    if cur:
        subcmds.append(cur)
    return subcmds


def _extract_compile_entry(tokens: list[str], *, cwd: Path) -> list[dict[str, Any]]:
    idx = 0
    # 剥离前缀环境变量赋值（如 CCACHE_DISABLE=1）与 env/ccache 包装器
    while idx < len(tokens):
        tok = tokens[idx]
        if tok == "env" or _RE_ENV_ASSIGN.match(tok):
            idx += 1
            continue
        base = Path(tok.replace("\\", "/")).name.lower()
        if base.endswith(".exe"):
            base = base[:-4]
        if base in _COMPILER_WRAPPERS:
            idx += 1
            continue
        break

    if idx >= len(tokens):
        return []

    cmd_tokens = _expand_response_files(tokens[idx:], cwd=cwd)
    if not cmd_tokens:
        return []

    compiler_name = Path(cmd_tokens[0].replace("\\", "/")).name
    if not _RE_COMPILER_BIN.match(compiler_name):
        return []

    # 必须是包含 -c / --compile 的真实编译动作，排除纯链接、预处理 (-E) 或纯依赖生成 (-M / -MM)
    has_compile_flag = any(t in ("-c", "--compile") for t in cmd_tokens[1:])
    if not has_compile_flag:
        return []
    if any(t in ("-E", "-M", "-MM") for t in cmd_tokens[1:]):
        return []

    sources: list[str] = []
    output_obj: str | None = None
    i = 1
    while i < len(cmd_tokens):
        tok = cmd_tokens[i]
        if tok == "-o" and i + 1 < len(cmd_tokens):
            output_obj = cmd_tokens[i + 1]
            i += 2
            continue
        if tok.startswith("-o") and len(tok) > 2:
            output_obj = tok[2:]
            i += 1
            continue
        if tok in _FLAGS_TAKING_NEXT_ARG and i + 1 < len(cmd_tokens):
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue

        ext = Path(tok.replace("\\", "/")).suffix.lower()
        if ext in _SOURCE_EXTS:
            sources.append(tok)
        i += 1

    if not sources:
        return []

    resolved_out: str | None = None
    if output_obj:
        op = Path(output_obj)
        resolved_out = str((cwd / op).resolve() if not op.is_absolute() else op.resolve())

    results: list[dict[str, Any]] = []
    for src in sources:
        sp = Path(src)
        abs_src = str((cwd / sp).resolve() if not sp.is_absolute() else sp.resolve())
        norm_args = list(cmd_tokens)
        # 将参数列表中的源文件路径也替换为绝对路径，最大化 clangd 跨目录兼容度
        for k in range(1, len(norm_args)):
            if norm_args[k] == src:
                norm_args[k] = abs_src
        entry: dict[str, Any] = {
            "directory": str(cwd),
            "arguments": norm_args,
            "file": abs_src,
        }
        if resolved_out is not None and len(sources) == 1:
            entry["output"] = resolved_out
        results.append(entry)
    return results


def _expand_response_files(tokens: list[str], *, cwd: Path) -> list[str]:
    out: list[str] = []
    for tok in tokens:
        if tok.startswith("@") and len(tok) > 1:
            rsp = Path(tok[1:])
            rsp_path = (cwd / rsp) if not rsp.is_absolute() else rsp
            if rsp_path.is_file():
                try:
                    out.extend(shlex.split(rsp_path.read_text(encoding="utf-8", errors="replace"), posix=True))
                    continue
                except (OSError, ValueError):
                    pass
        out.append(tok)
    return out


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------


def _sync_compdb_to_root(src: Path, dst: Path) -> str:
    src_res = src.resolve()
    try:
        if dst.is_symlink() or dst.exists():
            if dst.exists() and not dst.is_symlink() and dst.resolve() == src_res:
                return str(dst)
            if dst.is_symlink():
                try:
                    if dst.resolve() == src_res:
                        return str(dst)
                except OSError:
                    pass
            dst.unlink()
        try:
            rel_target = os.path.relpath(src_res, dst.parent)
            dst.symlink_to(rel_target)
        except OSError:
            shutil.copy2(src_res, dst)
    except OSError:
        shutil.copy2(src_res, dst)
    return str(dst)


def _run_clean_env(cmd: list[str], *, cwd: Path, timeout_s: float) -> tuple[int | None, str, bool]:
    env = os.environ.copy()
    env["LC_ALL"] = "C.UTF-8"
    env["LANG"] = "C.UTF-8"
    env.pop("LANGUAGE", None)
    env["GCC_COLORS"] = ""
    env.pop("CLICOLOR_FORCE", None)
    env["CMAKE_COLOR_DIAGNOSTICS"] = "OFF"
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return None, out, True
    return proc.returncode, _RE_ANSI.sub("", proc.stdout), False
