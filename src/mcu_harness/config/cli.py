"""工程配置与自动探测命令行工具（mcu-config）。

用法示例：
    python -m mcu_harness.config.cli inspect ./my_fw
    python -m mcu_harness.config.cli inspect ./my_fw --json
    python -m mcu_harness.config.cli init ./my_fw
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .manifest import ConfigError, discover_project, generate_manifest_toml


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mcu-config", description="Discover MCU project profile or generate mcu-harness.toml")
    sub = ap.add_subparsers(dest="cmd")

    p_insp = sub.add_parser("inspect", help="自动扫描并打印工程配置与硬件信息")
    p_insp.add_argument("project_dir", nargs="?", default=".", help="固件工程根目录，默认当前目录")
    p_insp.add_argument("--manifest", help="显式指定 mcu-harness.toml 路径")
    p_insp.add_argument("--json", action="store_true", help="输出 JSON 格式")

    p_init = sub.add_parser("init", help="根据扫描结果在工程根目录生成 mcu-harness.toml")
    p_init.add_argument("project_dir", nargs="?", default=".", help="固件工程根目录，默认当前目录")
    p_init.add_argument("--force", action="store_true", help="覆盖已存在的 mcu-harness.toml")

    args = ap.parse_args(argv)
    if args.cmd is None:
        args = ap.parse_args(["inspect", *(argv or [])])

    try:
        if args.cmd == "inspect":
            prof = discover_project(args.project_dir, manifest_path=getattr(args, "manifest", None))
            if args.json:
                print(json.dumps(prof.to_dict(), indent=2, ensure_ascii=False))
            else:
                print(prof.to_text())
            return 0

        if args.cmd == "init":
            prof = discover_project(args.project_dir)
            dst = Path(prof.project_dir) / "mcu-harness.toml"
            if dst.exists() and not args.force:
                print(f"error: {dst} already exists (pass --force to overwrite)", file=sys.stderr)
                return 1
            dst.write_text(generate_manifest_toml(prof), encoding="utf-8")
            print(f"Generated {dst}")
            return 0
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
