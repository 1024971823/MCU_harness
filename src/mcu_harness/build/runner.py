"""构建执行器：负责“跑命令”，把解析交给 diagnostics.py。

支持两类嵌入式工程：
    1. CMake + Ninja / Make：
       cmake -S <工程> -B <构建目录> -G Ninja ...
       cmake --build <构建目录> [--target xxx]
    2. Makefile（STM32CubeMX 等传统工程）：
       make [clean] [target] [-j N] [VAR=VAL...]

设计要点：
    * CMake 构建目录里还没有 CMakeCache.txt 时才自动执行 configure，避免每次全量重配。
    * Make 执行 clean 时先单独跑 `make clean`，再跑目标构建，避免 `-j` 并行时互相删文件。
    * 强制英文 locale：中文系统下 gcc/make 会输出中文前缀，导致解析失败。
    * 一切异常（找不到工具、超时）都转成 BuildResult 返回，而不是抛异常。
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .diagnostics import BinarySize, Diagnostic, MemoryRegion, parse_build_output

_RE_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_ARTIFACT_SUFFIXES = (".elf", ".hex", ".bin", ".axf", ".out")
_SKIP_ARTIFACT_DIRS = {"CMakeFiles", ".git", ".venv", "node_modules"}
_DEFAULT_MAKEFILES = ("GNUmakefile", "makefile", "Makefile")
_RAW_TAIL_LINES = 30


@dataclass
class Artifact:
    path: str
    size_bytes: int
    modified: float  # Unix 时间戳

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BuildResult:
    success: bool
    stage: str  # "precheck" | "configure" | "clean" | "build"
    return_code: int | None
    duration_s: float
    build_system: str = "cmake"  # "cmake" | "make" | "unknown"
    commands: list[str] = field(default_factory=list)
    errors: list[Diagnostic] = field(default_factory=list)
    warnings: list[Diagnostic] = field(default_factory=list)
    total_errors: int = 0
    total_warnings: int = 0
    memory: list[MemoryRegion] = field(default_factory=list)
    sizes: list[BinarySize] = field(default_factory=list)
    failed_targets: list[str] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    message: str = ""  # 补充说明（如超时、找不到工具）
    raw_tail: list[str] = field(default_factory=list)  # 解析不出诊断时的兜底：原始输出最后几行

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    def to_text(self) -> str:
        """给大模型阅读的精简文本报告。"""
        out: list[str] = []
        status = "SUCCESS" if self.success else "FAILED"
        out.append(
            f"[build] {status} (system={self.build_system}, stage={self.stage}, "
            f"rc={self.return_code}, {self.duration_s:.1f}s)"
        )
        if self.message:
            out.append(f"note: {self.message}")

        if self.errors:
            more = f", showing {len(self.errors)}" if self.total_errors > len(self.errors) else ""
            out.append(f"\nErrors ({self.total_errors}{more}):")
            out.extend(_fmt_diag(d) for d in self.errors)
        if self.warnings:
            more = f", showing {len(self.warnings)}" if self.total_warnings > len(self.warnings) else ""
            out.append(f"\nWarnings ({self.total_warnings}{more}):")
            out.extend(_fmt_diag(d) for d in self.warnings)

        if self.memory:
            out.append("\nMemory usage:")
            for m in self.memory:
                out.append(f"  {m.name:<10} {m.used_bytes:>9} / {m.total_bytes:<9} B  ({m.percent:.2f}%)")

        if self.sizes:
            out.append("\nBinary size:")
            for s in self.sizes:
                out.append(
                    f"  {s.file}: text={s.text_bytes} B, data={s.data_bytes} B, "
                    f"bss={s.bss_bytes} B (total={s.total_bytes} B)"
                )

        if self.artifacts:
            out.append("\nArtifacts:")
            for a in self.artifacts:
                out.append(f"  {a.path} ({a.size_bytes} B)")

        if self.raw_tail:
            out.append("\nRaw output tail (no structured diagnostics found):")
            out.extend(f"  {l}" for l in self.raw_tail)
        return "\n".join(out)


def _fmt_diag(d: Diagnostic) -> str:
    loc = d.location() or f"<{d.category}>"
    s = f"  {loc}: {d.message}"
    if d.flag:
        s += f" [{d.flag}]"
    if d.function:
        s += f"  (in {d.function})"
    if d.count > 1:
        s += f"  x{d.count}"
    for sn in d.snippet:
        s += f"\n      {sn}"
    return s


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def build_firmware(
    project_dir: str | os.PathLike,
    build_dir: str | os.PathLike | None = None,
    target: str | None = None,
    build_type: str = "Debug",
    toolchain_file: str | os.PathLike | None = None,
    generator: str = "Ninja",
    extra_cmake_args: list[str] | None = None,
    clean: bool = False,
    reconfigure: bool = False,
    jobs: int | None = None,
    timeout_s: float = 600,
    max_errors: int = 10,
    max_warnings: int = 10,
    *,
    build_system: str = "auto",
    makefile: str | os.PathLike | None = None,
    extra_args: list[str] | None = None,
) -> BuildResult:
    """编译固件工程（自动支持 CMake 与 Makefile），返回精简后的结构化结果。

    Args:
        project_dir: 含 CMakeLists.txt 或 Makefile 的工程根目录。
        build_dir: 构建/产物目录。CMake 默认 <project_dir>/build；Make 不传时自动扫描工程目录。
        target: 只编译某个目标（CMake 默认 all，Make 默认第一个目标）。
        build_type: CMake 构建类型（Debug / Release / RelWithDebInfo / MinSizeRel）。
        toolchain_file: CMake 交叉编译工具链文件。
        generator: CMake 生成器，默认 Ninja。
        extra_cmake_args: 额外的 CMake configure 参数（兼容保留，与 extra_args 合并）。
        clean: 先 clean 再编译。
        reconfigure: CMake 强制重新执行 configure。
        jobs: 并行编译任务数（-j）。
        timeout_s: 单个命令超时秒数。
        max_errors / max_warnings: 返回给 AI 的最大条数（总数仍会统计）。
        build_system: "auto"（自动识别）| "cmake" | "make"。
        makefile: 指定 Makefile 路径（默认在工程目录下查找 Makefile）。
        extra_args: 传给构建命令的额外参数（如 Make 的 ["DEBUG=1", "V=1"] 或 CMake 参数）。
    """
    t0 = time.monotonic()
    project = Path(project_dir).expanduser().resolve()
    explicit_build = build_dir is not None
    build = Path(build_dir).expanduser() if build_dir else project / "build"
    if not build.is_absolute():
        build = (project / build).resolve()

    system, mf_path, err = _detect_build_system(project, build_system, makefile)
    if err is not None:
        return _fail(system, "precheck", err, t0)

    if system == "make":
        return _build_make(
            project=project,
            artifact_root=build if explicit_build else project,
            makefile_path=mf_path,
            target=target,
            clean=clean,
            jobs=jobs,
            extra_args=extra_args,
            timeout_s=timeout_s,
            max_errors=max_errors,
            max_warnings=max_warnings,
            t0=t0,
        )

    merged_cmake_args = [*(extra_cmake_args or []), *(extra_args or [])]
    return _build_cmake(
        project=project,
        build=build,
        target=target,
        build_type=build_type,
        toolchain_file=toolchain_file,
        generator=generator,
        extra_cmake_args=merged_cmake_args,
        clean=clean,
        reconfigure=reconfigure,
        jobs=jobs,
        timeout_s=timeout_s,
        max_errors=max_errors,
        max_warnings=max_warnings,
        t0=t0,
    )


# ---------------------------------------------------------------------------
# 构建系统识别与后端实现
# ---------------------------------------------------------------------------


def _detect_build_system(
    project: Path,
    build_system: str,
    makefile: str | os.PathLike | None,
) -> tuple[str, Path | None, str | None]:
    if build_system not in ("auto", "cmake", "make"):
        return "unknown", None, f"unsupported build_system: {build_system!r} (expected auto, cmake, make)"
    if not project.is_dir():
        return "unknown", None, f"project_dir does not exist: {project}"

    if makefile is not None:
        if build_system == "cmake":
            return "cmake", None, "makefile parameter cannot be used with build_system='cmake'"
        mf = Path(makefile).expanduser()
        if not mf.is_absolute():
            mf = (project / mf).resolve()
        if not mf.is_file():
            return "make", None, f"Makefile not found: {mf}"
        return "make", mf, None

    has_cmake = (project / "CMakeLists.txt").is_file()
    found_mf = next((project / name for name in _DEFAULT_MAKEFILES if (project / name).is_file()), None)

    if build_system == "cmake":
        if not has_cmake:
            return "cmake", None, f"CMakeLists.txt not found in {project}"
        return "cmake", None, None

    if build_system == "make":
        if found_mf is None:
            return "make", None, f"Makefile not found in {project}"
        return "make", found_mf, None

    # auto：如果同时存在，优先 CMake（可通过 build_system="make" 显式覆盖）
    if has_cmake:
        return "cmake", None, None
    if found_mf is not None:
        return "make", found_mf, None
    return "unknown", None, f"Neither CMakeLists.txt nor Makefile found in {project}"


def _build_cmake(
    *,
    project: Path,
    build: Path,
    target: str | None,
    build_type: str,
    toolchain_file: str | os.PathLike | None,
    generator: str,
    extra_cmake_args: list[str],
    clean: bool,
    reconfigure: bool,
    jobs: int | None,
    timeout_s: float,
    max_errors: int,
    max_warnings: int,
    t0: float,
) -> BuildResult:
    cmake = shutil.which("cmake")
    if cmake is None:
        return _fail("cmake", "precheck", "cmake not found in PATH", t0)
    if generator == "Ninja" and shutil.which("ninja") is None:
        return _fail("cmake", "precheck", "ninja not found in PATH (install it or pass generator='Unix Makefiles')", t0)

    commands: list[str] = []

    need_configure = reconfigure or not (build / "CMakeCache.txt").is_file()
    if need_configure:
        cfg = [
            cmake, "-S", str(project), "-B", str(build), "-G", generator,
            f"-DCMAKE_BUILD_TYPE={build_type}",
            # 生成 compile_commands.json，后续代码索引 / clangd 会用到
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        ]
        if toolchain_file:
            tc = Path(toolchain_file).expanduser()
            if not tc.is_absolute():
                tc = project / tc
            cfg.append(f"-DCMAKE_TOOLCHAIN_FILE={tc}")
        cfg.extend(extra_cmake_args)

        rc, out, timed_out = _run(cfg, cwd=project, timeout_s=timeout_s)
        commands.append(shlex.join(cfg))
        if timed_out:
            return _fail("cmake", "configure", f"configure timed out after {timeout_s}s", t0, commands=commands)
        if rc != 0:
            return _finish(
                False, "cmake", "configure", rc, t0, commands, out, project, build, max_errors, max_warnings
            )

    bld = [cmake, "--build", str(build)]
    if target:
        bld += ["--target", target]
    if clean:
        bld.append("--clean-first")
    if jobs:
        bld += ["-j", str(jobs)]

    rc, out, timed_out = _run(bld, cwd=project, timeout_s=timeout_s)
    commands.append(shlex.join(bld))
    if timed_out:
        return _fail("cmake", "build", f"build timed out after {timeout_s}s", t0, commands=commands)

    return _finish(rc == 0, "cmake", "build", rc, t0, commands, out, project, build, max_errors, max_warnings)


def _build_make(
    *,
    project: Path,
    artifact_root: Path,
    makefile_path: Path | None,
    target: str | None,
    clean: bool,
    jobs: int | None,
    extra_args: list[str] | None,
    timeout_s: float,
    max_errors: int,
    max_warnings: int,
    t0: float,
) -> BuildResult:
    make_bin = shutil.which("make")
    if make_bin is None:
        return _fail("make", "precheck", "make not found in PATH", t0)

    mf_args: list[str] = []
    if makefile_path is not None and not (
        makefile_path.parent == project and makefile_path.name in _DEFAULT_MAKEFILES
    ):
        try:
            shown_mf = makefile_path.relative_to(project).as_posix()
        except ValueError:
            shown_mf = str(makefile_path)
        mf_args = ["-f", shown_mf]

    extra = list(extra_args or [])
    commands: list[str] = []

    if clean:
        cln = [make_bin, *mf_args, "clean", *extra]
        rc, out, timed_out = _run(cln, cwd=project, timeout_s=timeout_s)
        commands.append(shlex.join(cln))
        if timed_out:
            return _fail("make", "clean", f"make clean timed out after {timeout_s}s", t0, commands=commands)
        if rc != 0:
            return _finish(
                False, "make", "clean", rc, t0, commands, out, project, artifact_root, max_errors, max_warnings
            )

    bld = [make_bin, *mf_args]
    if target:
        bld.append(target)
    if jobs:
        bld += ["-j", str(jobs)]
    bld.extend(extra)

    rc, out, timed_out = _run(bld, cwd=project, timeout_s=timeout_s)
    commands.append(shlex.join(bld))
    if timed_out:
        return _fail("make", "build", f"build timed out after {timeout_s}s", t0, commands=commands)

    return _finish(
        rc == 0, "make", "build", rc, t0, commands, out, project, artifact_root, max_errors, max_warnings
    )


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------


def _fail(
    build_system: str,
    stage: str,
    msg: str,
    t0: float,
    *,
    rc: int | None = None,
    commands: list[str] | None = None,
) -> BuildResult:
    return BuildResult(
        success=False,
        stage=stage,
        return_code=rc,
        duration_s=time.monotonic() - t0,
        build_system=build_system,
        commands=commands or [],
        message=msg,
    )


def _run(cmd: list[str], cwd: Path, timeout_s: float) -> tuple[int | None, str, bool]:
    env = os.environ.copy()
    # 强制英文输出，保证正则能匹配；C.UTF-8 保留对中文路径的支持
    env["LC_ALL"] = "C.UTF-8"
    env["LANG"] = "C.UTF-8"
    env.pop("LANGUAGE", None)
    # 关闭彩色输出（ANSI 转义码会污染解析和上下文）
    env["GCC_COLORS"] = ""
    env.pop("CLICOLOR_FORCE", None)
    env["CMAKE_COLOR_DIAGNOSTICS"] = "OFF"
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout_s, text=True, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return None, out, True
    return proc.returncode, _RE_ANSI.sub("", proc.stdout), False


def _finish(
    success: bool,
    build_system: str,
    stage: str,
    rc: int | None,
    t0: float,
    commands: list[str],
    output: str,
    project: Path,
    artifact_root: Path,
    max_errors: int,
    max_warnings: int,
) -> BuildResult:
    parsed = parse_build_output(output, project_dir=str(project))
    result = BuildResult(
        success=success,
        stage=stage,
        return_code=rc,
        duration_s=time.monotonic() - t0,
        build_system=build_system,
        commands=commands,
        errors=parsed.errors[:max_errors],
        warnings=parsed.warnings[:max_warnings],
        total_errors=len(parsed.errors),
        total_warnings=len(parsed.warnings),
        memory=parsed.memory,
        sizes=parsed.sizes,
        failed_targets=parsed.failed_targets,
        artifacts=_find_artifacts(artifact_root, project) if success else [],
    )
    # 失败了却一条诊断都没解析出来 —— 不能让 AI 两眼一抹黑，附上原始输出尾部
    if not success and not parsed.errors:
        tail = [l for l in output.splitlines() if l.strip()][-_RAW_TAIL_LINES:]
        result.raw_tail = tail
    return result


def _find_artifacts(root: Path, project: Path) -> list[Artifact]:
    found: list[Artifact] = []
    if not root.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_ARTIFACT_DIRS]
        for fname in filenames:
            p = Path(dirpath) / fname
            if p.suffix.lower() not in _ARTIFACT_SUFFIXES:
                continue
            st = p.stat()
            try:
                shown = p.relative_to(project).as_posix()
            except ValueError:
                shown = str(p)
            found.append(Artifact(path=shown, size_bytes=st.st_size, modified=st.st_mtime))
    found.sort(key=lambda a: a.modified, reverse=True)
    return found
