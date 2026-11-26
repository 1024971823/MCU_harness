"""`mcu-desktop` 原生桌面端启动器与桌面集成工具（MCU Harness Studio）。

职责：
    1. 按优先级定位已编译的 `mcu-harness-studio` 原生二进制：
       `MCU_HARNESS_STUDIO_BIN` 环境变量 -> `<repo>/studio/build/release/mcu-harness-studio`
       -> `<repo>/studio/build/debug/mcu-harness-studio` -> `PATH` (`shutil.which`)。
    2. 支持 `--build` 在 `<repo>/studio` 下依次运行 `cmake --preset release` 与 `cmake --build --preset release`。
    3. 支持 `--install-desktop` 将应用图标与 `.desktop` 快捷方式安装到 `~/.local/share/`。
    4. 默认直接拉起原生桌面进程并透传工程目录、模型与安全互锁参数。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from mcu_harness.agent.interlock import VALID_INTERLOCK_MODES

_BINARY_NAME = "mcu-harness-studio"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def find_desktop_binary(*, repo_root: Path | None = None) -> Path | None:
    """按约定优先级查找已编译的原生桌面二进制路径。"""
    env_bin = os.environ.get("MCU_HARNESS_STUDIO_BIN", "").strip()
    if env_bin:
        cand = Path(env_bin).expanduser()
        if cand.is_file():
            return cand

    root = repo_root or _repo_root()
    exe_suffix = ".exe" if os.name == "nt" else ""
    for rel in (
        Path("studio") / "build" / "release" / f"{_BINARY_NAME}{exe_suffix}",
        Path("studio") / "build" / "debug" / f"{_BINARY_NAME}{exe_suffix}",
    ):
        cand = root / rel
        if cand.is_file():
            return cand

    which_bin = shutil.which(_BINARY_NAME)
    if which_bin:
        return Path(which_bin)
    return None


def build_desktop_binary(*, repo_root: Path | None = None) -> int:
    """调用 CMake Preset 编译 Release 版 MCU Harness Studio 原生桌面前端。"""
    root = repo_root or _repo_root()
    studio_dir = root / "studio"
    if not studio_dir.is_dir():
        sys.stderr.write(f"Studio directory not found: {studio_dir}\n")
        return 1
    if shutil.which("cmake") is None:
        sys.stderr.write("`cmake` not found in PATH; install CMake first.\n")
        return 1

    cfg_proc = subprocess.run(
        ["cmake", "--preset", "release"],
        cwd=str(studio_dir),
    )
    if cfg_proc.returncode != 0:
        return int(cfg_proc.returncode)

    build_proc = subprocess.run(
        ["cmake", "--build", "--preset", "release"],
        cwd=str(studio_dir),
    )
    return int(build_proc.returncode)


def install_desktop_entry(
    *,
    repo_root: Path | None = None,
    data_home: Path | None = None,
) -> tuple[Path, Path]:
    """安装 SVG 图标与 `.desktop` 文件到用户 XDG 目录，返回 `(icon_dest, desktop_dest)`。"""
    root = repo_root or _repo_root()
    xdg_data = data_home or Path.home() / ".local" / "share"

    icon_src = root / "studio" / "resources" / "app" / "mcu-harness-studio.svg"
    if not icon_src.is_file():
        raise FileNotFoundError(f"Desktop icon asset not found: {icon_src}")

    icon_dest = xdg_data / "icons" / "hicolor" / "scalable" / "apps" / "mcu-harness-studio.svg"
    icon_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon_src, icon_dest)

    built_bin = find_desktop_binary(repo_root=root)
    if built_bin is not None:
        exec_line = str(built_bin)
    else:
        launcher_bin = shutil.which("mcu-desktop")
        exec_line = launcher_bin if launcher_bin else f"{sys.executable} -m mcu_harness.agent.desktop_launcher"

    desktop_dest = xdg_data / "applications" / "mcu-harness-studio.desktop"
    desktop_dest.parent.mkdir(parents=True, exist_ok=True)
    desktop_content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=MCU Harness Studio\n"
        "Name[zh_CN]=MCU Harness Studio\n"
        "Comment=Embedded MCU Agent Harness Desktop Workbench\n"
        "Comment[zh_CN]=嵌入式 MCU Agent 调试与固件开发工作台\n"
        f"Exec={exec_line}\n"
        "Icon=mcu-harness-studio\n"
        "Terminal=false\n"
        "Categories=Development;Electronics;IDE;\n"
        "StartupWMClass=mcu-harness-studio\n"
    )
    desktop_dest.write_text(desktop_content, encoding="utf-8")
    return icon_dest, desktop_dest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcu-desktop",
        description="MCU Harness Studio 原生桌面端启动器",
    )
    parser.add_argument("--project-dir", default=None, help="目标嵌入式工程根目录")
    parser.add_argument(
        "--provider",
        default=None,
        help="LLM Provider (openai/deepseek/qwen/openrouter/ollama/vllm/anthropic/gemini/mock)",
    )
    parser.add_argument("--model", default=None, help="指定模型名称")
    parser.add_argument("--base-url", default=None, help="自定义 API Base URL")
    parser.add_argument("--api-key", default=None, help="自定义 API Key")
    parser.add_argument(
        "--mode",
        choices=list(VALID_INTERLOCK_MODES),
        default=None,
        help="硬件安全互锁模式",
    )
    parser.add_argument("--mock-llm", action="store_true", help="离线演示模式：使用内置 MockLlmProvider")
    parser.add_argument(
        "--build",
        action="store_true",
        help="编译 Release 版 MCU Harness Studio 原生桌面前端（cmake --preset release）",
    )
    parser.add_argument(
        "--install-desktop",
        action="store_true",
        help="安装桌面图标与 .desktop 快捷方式到 ~/.local/share/",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """`mcu-desktop` 命令行入口。"""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.build:
        return build_desktop_binary()

    if args.install_desktop:
        try:
            icon_dest, desktop_dest = install_desktop_entry()
        except OSError as exc:
            sys.stderr.write(f"Failed to install desktop entry: {exc}\n")
            return 1
        print(f"Installed icon: {icon_dest}")
        print(f"Installed desktop entry: {desktop_dest}")
        return 0

    binary = find_desktop_binary()
    if binary is None:
        sys.stderr.write(
            "Native desktop binary `mcu-harness-studio` is not built yet.\n"
            "Run `mcu-desktop --build` or "
            "`cmake --preset release && cmake --build --preset release` in `studio/` first.\n"
        )
        return 1

    cmd: list[str] = [str(binary)]
    if args.project_dir is not None:
        cmd.extend(["--project-dir", str(Path(args.project_dir).expanduser().resolve())])
    if args.provider is not None:
        cmd.extend(["--provider", args.provider])
    if args.model is not None:
        cmd.extend(["--model", args.model])
    if args.base_url is not None:
        cmd.extend(["--base-url", args.base_url])
    if args.api_key is not None:
        cmd.extend(["--api-key", args.api_key])
    if args.mode is not None:
        cmd.extend(["--mode", args.mode])
    if args.mock_llm:
        cmd.append("--mock-llm")

    proc = subprocess.run(cmd)
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
