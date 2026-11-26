"""工程配置清单（mcu-harness.toml）与零配置硬件/构建自动探测单元测试。

同时兼容 `pytest` 收集与 `PYTHONPATH=src python3 tests/test_config.py` 直接运行。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcu_harness.config import (
    ConfigError,
    discover_project,
    generate_manifest_toml,
    infer_mcu_traits,
    normalize_cubemx_mcu_name,
)
from mcu_harness.config.cli import main as config_cli_main
from mcu_harness.config.manifest import _RE_DEFINE_MCU


def _clean_env(extra: dict[str, str] | None = None, *, home: str | None = None) -> dict[str, str]:
    """构造隔离了宿主 MCU_HARNESS_* 环境变量与 HOME 缓存目录的测试环境字典。"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("MCU_HARNESS_")}
    if home is not None:
        env["HOME"] = home
    if extra:
        env.update(extra)
    return env


class TestCubeMxAndTraitInference(unittest.TestCase):
    """测试 CubeMX .ioc 扫描、型号归一化与内核/Flash/OpenOCD 属性推导。"""

    def test_cubemx_normalization_and_traits_matrix(self) -> None:
        cases = [
            # (ioc_key, raw_name, expected_device, expected_core, expected_ocd, expected_flash)
            ("Mcu.Name", "STM32H563ZITx", "STM32H563ZI", "Cortex-M33", "stm32h5x", 0x08000000),
            ("Mcu.UserName", "STM32F407VGT6", "STM32F407VG", "Cortex-M4", "stm32f4x", 0x08000000),
            ("Mcu.CPN", "STM32G071RBTx", "STM32G071RB", "Cortex-M0+", "stm32g0x", 0x08000000),
        ]
        for ioc_key, raw_name, exp_dev, exp_core, exp_ocd, exp_flash in cases:
            with self.subTest(raw_name=raw_name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                ioc_path = root / f"{exp_dev}.ioc"
                ioc_path.write_text(
                    f"#MicroXplorer Configuration settings\n{ioc_key}={raw_name}\nProjectManager.ProjectName=demo\n",
                    encoding="utf-8",
                )
                with patch.dict(os.environ, _clean_env(home=tmp), clear=True):
                    prof = discover_project(root)

                self.assertEqual(prof.target.device, exp_dev)
                self.assertEqual(prof.target.core, exp_core)
                self.assertEqual(prof.target.openocd_target, exp_ocd)
                self.assertEqual(prof.target.flash_base, exp_flash)
                self.assertEqual(prof.paths.ioc_path, str(ioc_path.resolve()))
                self.assertIn("ioc", prof.detected_sources.get("device", ""))

    def test_rp2040_and_additional_mcu_traits(self) -> None:
        self.assertEqual(normalize_cubemx_mcu_name("STM32H563ZITx(BGA)"), "STM32H563ZI")
        self.assertEqual(normalize_cubemx_mcu_name("RP2040"), "RP2040")

        rp_traits = infer_mcu_traits("RP2040")
        self.assertEqual(rp_traits["core"], "Cortex-M0+")
        self.assertEqual(rp_traits["openocd_target"], "rp2040")
        self.assertEqual(rp_traits["flash_base"], 0x10000000)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CMakeLists.txt").write_text(
                "cmake_minimum_required(VERSION 3.20)\nadd_definitions(-DRP2040)\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, _clean_env(home=tmp), clear=True):
                prof = discover_project(root)

            self.assertEqual(prof.target.device, "RP2040")
            self.assertEqual(prof.target.core, "Cortex-M0+")
            self.assertEqual(prof.target.openocd_target, "rp2040")
            self.assertEqual(prof.target.flash_base, 0x10000000)
            self.assertEqual(prof.target.to_dict()["flash_base_hex"], "0x10000000")
            self.assertEqual(prof.build.system, "cmake")

        # 未知芯片型号回退使用 -mcpu 提示推导内核
        custom_traits = infer_mcu_traits("CUSTOM_SOC_V1", mcpu_hint="cortex-m0plus")
        self.assertEqual(custom_traits["core"], "Cortex-M0+")
        self.assertIsNone(custom_traits["openocd_target"])
        self.assertIsNone(custom_traits["flash_base"])

    def test_subdirectory_ioc_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sub = root / "cubemx"
            sub.mkdir()
            (sub / "board.ioc").write_text("Mcu.Name=STM32F407VGT6\n", encoding="utf-8")

            with patch.dict(os.environ, _clean_env(home=tmp), clear=True):
                prof = discover_project(root)

            self.assertEqual(prof.target.device, "STM32F407VG")
            self.assertEqual(prof.target.core, "Cortex-M4")
            self.assertEqual(prof.target.openocd_target, "stm32f4x")


class TestBuildScanAndArtifactMatching(unittest.TestCase):
    """测试 Makefile / CMake 宏提取与最新 .elf / .map / .svd 自动匹配。"""

    def test_makefile_macro_mcpu_and_latest_elf_svd_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Makefile").write_text(
                "CFLAGS := -O2 -g -mcpu=cortex-m3 -mthumb -DSTM32F103xB -DUSE_HAL_DRIVER\n",
                encoding="utf-8",
            )

            # 创建新旧两个 .elf 与两个 .map，验证按最新修改时间选择，且跳过 .git 目录
            build_old = root / "build_debug"
            build_new = root / "build_release"
            git_dir = root / ".git"
            svd_dir = root / "svd"
            for d in (build_old, build_new, git_dir, svd_dir):
                d.mkdir(parents=True)

            old_elf = build_old / "firmware_old.elf"
            new_elf = build_new / "firmware_latest.elf"
            ignored_git_elf = git_dir / "should_ignore.elf"
            old_map = build_old / "firmware_old.map"
            new_map = build_new / "firmware_latest.map"

            for f in (old_elf, new_elf, ignored_git_elf, old_map, new_map):
                f.write_bytes(b"\x7fELF" + b"\x00" * 60)

            os.utime(old_elf, (1_700_000_100, 1_700_000_100))
            os.utime(new_elf, (1_700_000_900, 1_700_000_900))
            os.utime(ignored_git_elf, (1_700_009_999, 1_700_009_999))
            os.utime(old_map, (1_700_000_100, 1_700_000_100))
            os.utime(new_map, (1_700_000_900, 1_700_000_900))

            svd_f4 = svd_dir / "STM32F407.svd"
            svd_f1 = svd_dir / "STM32F103.svd"
            svd_f4.write_text("<device><name>STM32F407</name></device>", encoding="utf-8")
            svd_f1.write_text("<device><name>STM32F103</name></device>", encoding="utf-8")
            compdb = root / "compile_commands.json"
            compdb.write_text("[]", encoding="utf-8")

            with patch.dict(os.environ, _clean_env(home=tmp), clear=True):
                prof = discover_project(root)

            self.assertEqual(prof.build.system, "make")
            self.assertEqual(prof.target.core, "Cortex-M3")
            self.assertEqual(prof.paths.elf_path, str(new_elf.resolve()))
            self.assertEqual(prof.paths.map_path, str(new_map.resolve()))
            self.assertEqual(prof.paths.compile_commands_path, str(compdb.resolve()))
            self.assertIsNotNone(prof.paths.svd_path)

            # 若 _RE_DEFINE_MCU 已支持小写 'x'（如 STM32F103xB），进一步校验型号与最佳 SVD 匹配
            if _RE_DEFINE_MCU.search("-DSTM32F103xB") is not None:
                self.assertEqual((prof.target.device or "").upper(), "STM32F103XB")
                self.assertEqual(prof.target.openocd_target, "stm32f1x")
                self.assertEqual(prof.target.flash_base, 0x08000000)
                self.assertEqual(prof.paths.svd_path, str(svd_f1.resolve()))

    def test_cmake_define_macro_and_best_matching_svd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CMakeLists.txt").write_text(
                "add_compile_options(-mcpu=cortex-m3)\nadd_compile_definitions(-DSTM32F103XB)\n",
                encoding="utf-8",
            )
            svd_dir = root / "cmsis_svd"
            svd_dir.mkdir()
            svd_h5 = svd_dir / "STM32H563.svd"
            svd_f1 = svd_dir / "STM32F103.svd"
            svd_g0 = svd_dir / "STM32G071.svd"
            for p in (svd_h5, svd_f1, svd_g0):
                p.write_text("<device/>", encoding="utf-8")

            with patch.dict(os.environ, _clean_env(home=tmp), clear=True):
                prof = discover_project(root)

            self.assertEqual(prof.build.system, "cmake")
            self.assertEqual(prof.target.device, "STM32F103XB")
            self.assertEqual(prof.target.core, "Cortex-M3")
            self.assertEqual(prof.target.openocd_target, "stm32f1x")
            self.assertEqual(prof.target.flash_base, 0x08000000)
            self.assertEqual(prof.detected_sources.get("device"), "build_flags")
            self.assertEqual(prof.paths.svd_path, str(svd_f1.resolve()))

    def test_env_overrides_when_manifest_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = _clean_env(
                {
                    "MCU_HARNESS_DEVICE": "STM32G431CB",
                    "MCU_HARNESS_BACKEND": "openocd",
                    "MCU_HARNESS_SPEED": "8000",
                    "MCU_HARNESS_SERIAL": "SN-9001",
                },
                home=tmp,
            )
            with patch.dict(os.environ, env, clear=True):
                prof = discover_project(root)

            self.assertEqual(prof.target.device, "STM32G431CB")
            self.assertEqual(prof.target.core, "Cortex-M4")
            self.assertEqual(prof.target.probe_backend, "openocd")
            self.assertEqual(prof.target.speed, 8000)
            self.assertEqual(prof.target.serial_no, "SN-9001")
            self.assertEqual(prof.detected_sources.get("device"), "env:MCU_HARNESS_DEVICE")


class TestManifestRoundTripAndValidation(unittest.TestCase):
    """测试 mcu-config init 往返一致性、CLI JSON/文本输出以及非法配置报错。"""

    def test_cli_init_and_discover_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "demo.ioc").write_text("Mcu.Name=STM32H563ZITx\n", encoding="utf-8")
            (root / "CMakeLists.txt").write_text("project(demo C)\n", encoding="utf-8")
            build_dir = root / "build"
            build_dir.mkdir()
            elf_file = build_dir / "demo.elf"
            elf_file.write_bytes(b"\x7fELF" + b"\x00" * 60)
            svd_file = root / "STM32H563.svd"
            svd_file.write_text("<device/>", encoding="utf-8")

            with patch.dict(os.environ, _clean_env(home=tmp), clear=True):
                before = discover_project(root)
                self.assertIsNone(before.manifest_path)

                out_buf = io.StringIO()
                with contextlib.redirect_stdout(out_buf):
                    rc = config_cli_main(["init", str(root)])
                self.assertEqual(rc, 0)

                manifest_file = root / "mcu-harness.toml"
                self.assertTrue(manifest_file.is_file())

                # 未传 --force 再次 init 应拒绝覆盖并返回 1
                err_buf = io.StringIO()
                with contextlib.redirect_stderr(err_buf):
                    rc_dup = config_cli_main(["init", str(root)])
                self.assertEqual(rc_dup, 1)
                self.assertIn("already exists", err_buf.getvalue())

                # 传入 --force 可正常覆盖
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(config_cli_main(["init", str(root), "--force"]), 0)

                after = discover_project(root)
                self.assertEqual(after.manifest_path, str(manifest_file.resolve()))
                self.assertEqual(after.target.device, before.target.device)
                self.assertEqual(after.target.core, before.target.core)
                self.assertEqual(after.target.probe_backend, before.target.probe_backend)
                self.assertEqual(after.target.interface, before.target.interface)
                self.assertEqual(after.target.speed, before.target.speed)
                self.assertEqual(after.target.openocd_interface, before.target.openocd_interface)
                self.assertEqual(after.target.openocd_target, before.target.openocd_target)
                self.assertEqual(after.target.flash_base, before.target.flash_base)
                self.assertEqual(after.build.system, before.build.system)
                self.assertEqual(after.paths.svd_path, before.paths.svd_path)
                self.assertEqual(after.paths.elf_path, before.paths.elf_path)
                self.assertEqual(after.safety, before.safety)
                self.assertEqual(after.arch.preset, before.arch.preset)

                # 验证 inspect --json 与文本输出
                json_buf = io.StringIO()
                with contextlib.redirect_stdout(json_buf):
                    self.assertEqual(config_cli_main(["inspect", str(root), "--json"]), 0)
                parsed = json.loads(json_buf.getvalue())
                self.assertEqual(parsed["target"]["device"], "STM32H563ZI")
                self.assertEqual(parsed["target"]["flash_base_hex"], "0x08000000")

                txt_buf = io.StringIO()
                with contextlib.redirect_stdout(txt_buf):
                    self.assertEqual(config_cli_main(["inspect", str(root)]), 0)
                self.assertIn("STM32H563ZI", txt_buf.getvalue())

    def test_custom_safety_and_arch_toml_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mcu-harness.toml").write_text(
                """
[target]
device = "STM32G071RB"
probe_backend = "openocd"
flash_base = 0x08000000

[safety]
deny_ranges = [{ start = "0x2000F000", end = "0x2000FFFF", reason = "motor ISR table" }]
confirm_ranges = [{ start = 0x20008000, end = 0x200080FF, reason = "PID params" }]

[architecture]
preset = "custom"
layers = [
    { name = "app", paths = ["App"], allow_depend_on = ["bsp"], forbid_headers = ["stm32*.h"] },
    { name = "bsp", paths = ["Bsp"], allow_depend_on = ["bsp"] },
]
""",
                encoding="utf-8",
            )
            with patch.dict(os.environ, _clean_env(home=tmp), clear=True):
                prof = discover_project(root)

            self.assertEqual(prof.safety.deny_ranges, ((0x2000F000, 0x2000FFFF, "motor ISR table"),))
            self.assertEqual(prof.safety.confirm_ranges, ((0x20008000, 0x200080FF, "PID params"),))
            self.assertEqual(prof.arch.preset, "custom")
            self.assertEqual(len(prof.arch.layers), 2)
            self.assertEqual(prof.arch.layers[0].name, "app")
            self.assertEqual(prof.to_dict()["safety"]["deny_ranges"][0]["start"], "0x2000F000")

    def test_invalid_toml_and_invalid_ranges_raise_config_error(self) -> None:
        bad_manifests = [
            ("syntax_error", "[target\ndevice = 'STM32H563ZI'\n", "Failed to parse TOML"),
            ("non_table_section", 'target = "not-a-table"\n', r"\[target\].*must be a table"),
            ("bad_backend", '[target]\nprobe_backend = "invalid_probe"\n', "Unsupported target.probe_backend"),
            ("bool_flash_base", "[target]\nflash_base = true\n", "cannot be boolean"),
            ("bad_hex_flash_base", '[target]\nflash_base = "0xZZZ"\n', "Invalid integer"),
            (
                "inverted_deny_range",
                '[safety]\ndeny_ranges = [{ start = "0x20001000", end = "0x20000000" }]\n',
                "requires valid start <= end",
            ),
            (
                "missing_end_in_confirm_range",
                '[safety]\nconfirm_ranges = [{ start = "0x20000000" }]\n',
                "requires valid start <= end",
            ),
            (
                "range_not_list",
                '[safety]\ndeny_ranges = "0x20000000..0x20001000"\n',
                "must be a list of tables",
            ),
            (
                "bad_arch_preset",
                '[architecture]\npreset = "hexagonal"\n',
                "Unsupported architecture.preset",
            ),
        ]

        for label, content, err_regex in bad_manifests:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "mcu-harness.toml").write_text(content, encoding="utf-8")
                with patch.dict(os.environ, _clean_env(home=tmp), clear=True):
                    with self.assertRaisesRegex(ConfigError, err_regex):
                        discover_project(root)

                    err_buf = io.StringIO()
                    with contextlib.redirect_stderr(err_buf):
                        rc = config_cli_main(["inspect", str(root)])
                    self.assertEqual(rc, 1)

        with self.assertRaisesRegex(ConfigError, "does not exist"):
            discover_project("/nonexistent/mcu_harness_path_xyz")

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ConfigError, "Manifest file not found"):
                discover_project(tmp, manifest_path="missing_manifest.toml")


if __name__ == "__main__":
    unittest.main(verbosity=2)
