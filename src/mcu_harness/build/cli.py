"""构建模块的命令行入口 —— 不经过 AI，直接在终端验证 build_firmware 的效果。

用法示例：
    python -m mcu_harness.build.cli ./my_fw
    python -m mcu_harness.build.cli ./my_fw --arg DEBUG=1 -j 8
    python -m mcu_harness.build.cli ./my_fw --toolchain cmake/arm-none-eabi.cmake --json
    python -m mcu_harness.build.cli --parse-log build.log      # 只解析已有日志，不执行编译
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .compdb import generate_compile_commands
from .diagnostics import parse_build_output
from .footprint import analyze_footprint
from .runner import build_firmware


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mcu-build",
        description="Headless CMake/Makefile firmware build with condensed diagnostics",
    )
    ap.add_argument("project_dir", nargs="?", help="工程根目录（含 CMakeLists.txt 或 Makefile）")
    ap.add_argument("--build-system", choices=["auto", "cmake", "make"], default="auto", help="构建系统，默认自动识别")
    ap.add_argument("-f", "--makefile", help="指定 Makefile 路径（用于 Makefile 工程）")
    ap.add_argument("-B", "--build-dir", help="构建/产物目录（CMake 默认 <project>/build）")
    ap.add_argument("-t", "--target", help="只编译指定目标")
    ap.add_argument("--build-type", default="Debug")
    ap.add_argument("--toolchain", help="CMake 工具链文件")
    ap.add_argument("-G", "--generator", default="Ninja")
    ap.add_argument("-D", dest="defines", action="append", default=[], help="额外 CMake 定义，如 -D BOARD=xxx")
    ap.add_argument("--arg", dest="extra_args", action="append", default=[], help="额外构建参数，如 --arg DEBUG=1")
    ap.add_argument("--clean", action="store_true", help="先 clean 再编译")
    ap.add_argument("--reconfigure", action="store_true", help="CMake 强制重新 configure")
    ap.add_argument("-j", "--jobs", type=int)
    ap.add_argument("--json", action="store_true", help="输出 JSON（AI/程序使用的格式）")
    ap.add_argument("--parse-log", metavar="FILE", help="仅解析已有的编译日志文件")
    ap.add_argument("--compdb", action="store_true", help="仅生成 compile_commands.json（支持 CMake 与 Makefile 干跑）")
    ap.add_argument("--footprint", nargs="?", const="AUTO", metavar="ELF", help="分析固件 .elf/.map 内存版图与 DMA/栈风险")
    ap.add_argument("--map", dest="map_file", metavar="MAP", help="配合 --footprint 显式指定 .map 文件路径")
    args = ap.parse_args(argv)

    if args.footprint is not None:
        elf_arg = None if args.footprint == "AUTO" else args.footprint
        try:
            rep = analyze_footprint(elf_path=elf_arg, map_path=args.map_file, project_dir=args.project_dir)
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(json.dumps(rep.to_dict(), indent=2, ensure_ascii=False) if args.json else rep.to_text())
        return 0

    if args.compdb:
        if not args.project_dir:
            ap.error("project_dir is required with --compdb")
        res_db = generate_compile_commands(
            args.project_dir,
            build_dir=args.build_dir,
            makefile=args.makefile,
            extra_args=args.extra_args,
            build_system=args.build_system,
            toolchain_file=args.toolchain,
            generator=args.generator,
            target=args.target,
        )
        if args.json:
            print(json.dumps(res_db.to_dict(), indent=2, ensure_ascii=False))
        else:
            status = "OK" if res_db.success else "FAILED"
            print(f"[compdb] {status} ({res_db.entry_count} entries) -> {res_db.output_path or res_db.message}")
        return 0 if res_db.success else 1

    if args.parse_log:
        text = Path(args.parse_log).read_text(encoding="utf-8", errors="replace")
        parsed = parse_build_output(text, project_dir=args.project_dir)
        data = {
            "errors": [d.to_dict() for d in parsed.errors],
            "warnings": [d.to_dict() for d in parsed.warnings],
            "memory": [m.to_dict() for m in parsed.memory],
            "sizes": [s.to_dict() for s in parsed.sizes],
            "failed_targets": parsed.failed_targets,
        }
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    if not args.project_dir:
        ap.error("project_dir is required (or use --parse-log)")

    result = build_firmware(
        args.project_dir,
        build_dir=args.build_dir,
        target=args.target,
        build_type=args.build_type,
        toolchain_file=args.toolchain,
        generator=args.generator,
        extra_cmake_args=[f"-D{d}" for d in args.defines],
        clean=args.clean,
        reconfigure=args.reconfigure,
        jobs=args.jobs,
        build_system=args.build_system,
        makefile=args.makefile,
        extra_args=args.extra_args,
    )
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(result.to_text())
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
