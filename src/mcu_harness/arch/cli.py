"""嵌入式代码框架守护与可移植脚手架命令行工具（mcu-arch）。

用法示例：
    python -m mcu_harness.arch.cli check ./my_fw
    python -m mcu_harness.arch.cli check ./my_fw --json
    python -m mcu_harness.arch.cli scaffold skeleton --dir ./my_portable_fw
    python -m mcu_harness.arch.cli scaffold bsp pressure_sensor --dir ./my_portable_fw
    python -m mcu_harness.arch.cli scaffold service fluid --dir ./my_portable_fw
"""

from __future__ import annotations

import argparse
import json
import sys

from .analyzer import ArchError, check_architecture
from .scaffold import ScaffoldError, scaffold_code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mcu-arch",
        description="Check embedded C/C++ layered architecture & ISR safety, or scaffold portable PAL/BSP/Service code",
    )
    sub = ap.add_subparsers(dest="cmd")

    p_chk = sub.add_parser("check", help="扫描分层头文件边界、ISR 传递阻塞调用链与共享全局变量 volatile 隐患")
    p_chk.add_argument("project_dir", nargs="?", default=".", help="固件工程根目录，默认当前目录")
    p_chk.add_argument("--manifest", help="显式指定 mcu-harness.toml 配置文件路径")
    p_chk.add_argument("--json", action="store_true", help="输出结构化 JSON 报告")

    p_scf = sub.add_parser("scaffold", help="生成跨芯片可移植 PAL / BSP / Service / Skeleton 及主机单元测试")
    p_scf.add_argument(
        "kind",
        choices=("pal", "bsp", "service", "skeleton"),
        help="生成类型：pal | bsp | service | skeleton",
    )
    p_scf.add_argument("name", nargs="?", default=None, help="模块名称（bsp / service 必填，如 ina226 或 thermal）")
    p_scf.add_argument("--dir", dest="project_dir", default=".", help="目标工程根目录，默认当前目录")
    p_scf.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的目标文件")
    p_scf.add_argument("--json", action="store_true", help="输出结构化 JSON 结果")

    args = ap.parse_args(argv)
    if args.cmd is None:
        args = ap.parse_args(["check", *(argv or [])])

    try:
        if args.cmd == "check":
            report = check_architecture(args.project_dir, manifest_path=getattr(args, "manifest", None))
            if args.json:
                print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
            else:
                print(report.to_text())
            return 0 if report.passed else 1

        if args.cmd == "scaffold":
            res = scaffold_code(
                args.project_dir,
                args.kind,
                args.name,
                overwrite=args.overwrite,
            )
            if args.json:
                print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
            else:
                print(res.to_text())
            return 0
    except (ArchError, ScaffoldError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
