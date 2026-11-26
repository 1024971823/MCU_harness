"""嵌入式架构守护（分层检查 + ISR 调用图 + volatile 并发扫描）与可移植脚手架完整单元测试。

支持两种运行方式：
    1. 直接通过标准库运行：`PYTHONPATH=src python3 tests/test_arch.py`
    2. 通过 pytest 运行：`pytest tests/test_arch.py`
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from mcu_harness.arch import (
    ArchError,
    ScaffoldError,
    _strip_comments_and_strings,
    check_architecture,
    scaffold_code,
)
from mcu_harness.arch.cli import main as arch_cli_main
from mcu_harness.config.manifest import ArchConfig, DEFAULT_FORBIDDEN_IN_ISR, LayerRule


class TestStripCommentsAndStrings(unittest.TestCase):
    """验证 C/C++ 保行号去注释与字符串字面量的精确性。"""

    def test_preserves_exact_newlines_and_strips_false_positives(self) -> None:
        raw = (
            "/* Line 1\n"
            '   #include "stm32h5xx_hal.h"\n'
            "   HAL_Delay(100);\n"
            "*/\n"
            "// single line comment with vTaskDelay(10);\n"
            'const char *s = "HAL_Delay(50); /* fake */ \\"escaped\\"";\n'
            "char brace = '{';\n"
            "int real_code(void) {\n"
            "    return 42;\n"
            "}\n"
        )
        stripped = _strip_comments_and_strings(raw)

        self.assertEqual(len(stripped), len(raw))
        self.assertEqual(
            [i for i, ch in enumerate(stripped) if ch == "\n"],
            [i for i, ch in enumerate(raw) if ch == "\n"],
        )
        self.assertNotIn("HAL_Delay", stripped)
        self.assertNotIn("vTaskDelay", stripped)
        self.assertNotIn("stm32h5xx_hal.h", stripped)
        self.assertIn("int real_code(void)", stripped)


class TestArchitectureAnalyzer(unittest.TestCase):
    """验证分层越权、子域隔离、跨文件 ISR 调用链与漏加 volatile 检出及修复闭环。"""

    def test_detects_all_violation_categories_and_passes_after_fix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)

            # 1. PAL 层：中断入口文件（含注释/字符串里的假违禁调用 + 递归环防死循环 + 真实跨文件调用）
            (proj / "pal").mkdir(parents=True)
            (proj / "pal" / "pal_uart.h").write_text(
                "#ifndef PAL_UART_H\n#define PAL_UART_H\n#include <stdint.h>\n#endif\n",
                encoding="utf-8",
            )
            (proj / "pal" / "stm32_it.c").write_text(
                '#include "pal_uart.h"\n'
                "#include <stdint.h>\n"
                "\n"
                "extern void parse_byte(uint8_t byte);\n"
                "uint8_t g_rx_ready = 0;\n"
                "volatile uint32_t g_safe_irq_cnt = 0;\n"
                "static const uint8_t k_lookup[2] = {1, 2};\n"
                "\n"
                "static void isr_cycle_b(void);\n"
                "static void isr_cycle_a(void) { isr_cycle_b(); }\n"
                "static void isr_cycle_b(void) { isr_cycle_a(); }\n"
                "\n"
                "void USART1_IRQHandler(void) {\n"
                "    // HAL_Delay(999); 注释里的阻塞调用绝不能误报\n"
                "    /* malloc(64); printf(\"bad\"); */\n"
                '    const char *tag = "HAL_Delay(123)";\n'
                "    (void)tag;\n"
                "    (void)k_lookup;\n"
                "    g_safe_irq_cnt++;\n"
                "    g_rx_ready = 1;\n"
                "    isr_cycle_a();\n"
                "    parse_byte(0xA5U);\n"
                "}\n",
                encoding="utf-8",
            )

            # 2. BSP 层：故意反向 include 上层 service 头文件（layer_violation）
            (proj / "bsp").mkdir(parents=True)
            (proj / "bsp" / "pump_drv.h").write_text(
                "#ifndef PUMP_DRV_H\n#define PUMP_DRV_H\nvoid pump_drv_init(void);\n#endif\n",
                encoding="utf-8",
            )
            (proj / "bsp" / "pump_drv.c").write_text(
                '#include "pump_drv.h"\n'
                '#include "water/water_ctrl.h"\n'  # 违规：BSP 反向依赖 Service
                "void pump_drv_init(void) {}\n",
                encoding="utf-8",
            )

            # 3. Service 层：
            #    - 包含违禁原厂 HAL 头文件 stm32h5xx_hal.h（forbidden_header）
            #    - 同层子域 water 直接 include motion（sibling_violation）
            #    - parse_byte 内部调用阻塞 HAL_Delay（被 USART1_IRQHandler 跨文件二级调用！）
            (proj / "service" / "water").mkdir(parents=True)
            (proj / "service" / "motion").mkdir(parents=True)

            (proj / "service" / "service_common.h").write_text(
                "#ifndef SERVICE_COMMON_H\n#define SERVICE_COMMON_H\n#define SVC_VER 1\n#endif\n",
                encoding="utf-8",
            )
            (proj / "service" / "motion" / "stepper.h").write_text(
                "#ifndef STEPPER_H\n#define STEPPER_H\nvoid stepper_stop(void);\n#endif\n",
                encoding="utf-8",
            )
            (proj / "service" / "water" / "water_ctrl.h").write_text(
                "#ifndef WATER_CTRL_H\n#define WATER_CTRL_H\n#include <stdint.h>\nvoid parse_byte(uint8_t b);\n#endif\n",
                encoding="utf-8",
            )
            (proj / "service" / "water" / "water_ctrl.c").write_text(
                '#include "water_ctrl.h"\n'
                '#include "service_common.h"\n'    # 合法：包含同层根级公共头文件
                '#include "stm32h5xx_hal.h"\n'     # 违规 1：Service 严禁包含原厂 HAL 头文件
                '#include "motion/stepper.h"\n'    # 违规 2：同层兄弟子域横向耦合
                "/* #include \"board_config.h\" 注释中的违禁头文件绝不误报 */\n"
                "\n"
                "extern void HAL_Delay(uint32_t ms);\n"
                "\n"
                "void parse_byte(uint8_t b) {\n"
                "    if (b == 0xA5U) {\n"
                "        HAL_Delay(5U);\n"         # 违规 3（第 11 行）：处于 USART1_IRQHandler 传递调用链上！
                "    }\n"
                "}\n",
                encoding="utf-8",
            )

            # 4. App 层：主循环读取 g_rx_ready（触发 missing_volatile 警告）
            (proj / "app").mkdir(parents=True)
            (proj / "app" / "main.c").write_text(
                "#include <stdint.h>\n"
                "extern uint8_t g_rx_ready;\n"
                "extern volatile uint32_t g_safe_irq_cnt;\n"
                "\n"
                "int main(void) {\n"
                "    while (!g_rx_ready) {\n"
                "    }\n"
                "    return (int)g_safe_irq_cnt;\n"
                "}\n",
                encoding="utf-8",
            )

            # 自定义开启 service 层 isolate_siblings=True 以同时验证子域隔离
            cfg = ArchConfig(
                preset="custom",
                layers=(
                    LayerRule(
                        name="app",
                        paths=("app",),
                        allow_depend_on=("app", "service", "bsp", "pal"),
                        forbid_headers=("stm32*hal*.h",),
                    ),
                    LayerRule(
                        name="service",
                        paths=("service",),
                        allow_depend_on=("service", "bsp", "pal"),
                        forbid_headers=("stm32*hal*.h", "board_config.h"),
                        isolate_siblings=True,
                    ),
                    LayerRule(
                        name="bsp",
                        paths=("bsp",),
                        allow_depend_on=("bsp", "pal"),
                        forbid_headers=("board_config.h",),
                    ),
                    LayerRule(
                        name="pal",
                        paths=("pal", "include/pal"),
                        allow_depend_on=("pal",),
                    ),
                ),
                forbidden_in_isr=DEFAULT_FORBIDDEN_IN_ISR,
            )

            report = check_architecture(proj, config=cfg)
            self.assertFalse(report.passed)
            self.assertIn("USART1_IRQHandler", report.isr_entries)

            by_cat: dict[str, list] = {}
            for v in report.violations:
                by_cat.setdefault(v.category, []).append(v)

            # 验证 1：forbidden_header 仅检出真实的 stm32h5xx_hal.h（注释里的 board_config.h 不误报）
            self.assertEqual(len(by_cat.get("forbidden_header", [])), 1)
            fh = by_cat["forbidden_header"][0]
            self.assertEqual(fh.file, "service/water/water_ctrl.c")
            self.assertEqual(fh.line, 3)
            self.assertEqual(fh.symbol, "stm32h5xx_hal.h")

            # 验证 2：layer_violation 精准抓出 bsp -> service 反向依赖
            self.assertEqual(len(by_cat.get("layer_violation", [])), 1)
            lv = by_cat["layer_violation"][0]
            self.assertEqual(lv.file, "bsp/pump_drv.c")
            self.assertEqual(lv.line, 2)
            self.assertEqual(lv.target_layer, "service")

            # 验证 3：sibling_violation 精准抓出 service/water -> service/motion（service_common.h 不误报）
            self.assertEqual(len(by_cat.get("sibling_violation", [])), 1)
            sv = by_cat["sibling_violation"][0]
            self.assertEqual(sv.file, "service/water/water_ctrl.c")
            self.assertEqual(sv.line, 4)

            # 验证 4：isr_unsafe_call 仅检出真实二级调用链 USART1_IRQHandler -> parse_byte -> HAL_Delay
            #         注释与字符串中的 HAL_Delay / malloc / printf 绝不误报！
            self.assertEqual(len(by_cat.get("isr_unsafe_call", [])), 1)
            iu = by_cat["isr_unsafe_call"][0]
            self.assertEqual(iu.call_chain, ("USART1_IRQHandler", "parse_byte", "HAL_Delay"))
            self.assertEqual(iu.file, "service/water/water_ctrl.c")
            self.assertEqual(iu.line, 11)
            self.assertIn("USART1_IRQHandler -> parse_byte -> HAL_Delay", iu.message)

            # 验证 5：missing_volatile 仅检出 g_rx_ready（volatile g_safe_irq_cnt 与 const k_lookup 不误报）
            self.assertEqual(len(by_cat.get("missing_volatile", [])), 1)
            mv = by_cat["missing_volatile"][0]
            self.assertEqual(mv.symbol, "g_rx_ready")
            self.assertEqual(mv.severity, "warning")
            self.assertEqual(mv.file, "pal/stm32_it.c")
            self.assertEqual(mv.line, 5)

            self.assertEqual(report.error_count, 4)
            self.assertEqual(report.warning_count, 1)

            # ---------------- 修复全部问题后复检 ----------------
            (proj / "pal" / "stm32_it.c").write_text(
                '#include "pal_uart.h"\n'
                "#include <stdint.h>\n"
                "\n"
                "extern void parse_byte(uint8_t byte);\n"
                "volatile uint8_t g_rx_ready = 0;\n"
                "volatile uint32_t g_safe_irq_cnt = 0;\n"
                "\n"
                "void USART1_IRQHandler(void) {\n"
                "    g_safe_irq_cnt++;\n"
                "    g_rx_ready = 1;\n"
                "    parse_byte(0xA5U);\n"
                "}\n",
                encoding="utf-8",
            )
            (proj / "bsp" / "pump_drv.c").write_text(
                '#include "pump_drv.h"\n'
                "void pump_drv_init(void) {}\n",
                encoding="utf-8",
            )
            (proj / "service" / "water" / "water_ctrl.c").write_text(
                '#include "water_ctrl.h"\n'
                '#include "service_common.h"\n'
                "\n"
                "static volatile uint32_t s_valid_frames = 0;\n"
                "\n"
                "void parse_byte(uint8_t b) {\n"
                "    if (b == 0xA5U) {\n"
                "        s_valid_frames++;\n"
                "    }\n"
                "}\n",
                encoding="utf-8",
            )
            (proj / "app" / "main.c").write_text(
                "#include <stdint.h>\n"
                "extern volatile uint8_t g_rx_ready;\n"
                "extern volatile uint32_t g_safe_irq_cnt;\n"
                "\n"
                "int main(void) {\n"
                "    while (!g_rx_ready) {\n"
                "    }\n"
                "    return (int)g_safe_irq_cnt;\n"
                "}\n",
                encoding="utf-8",
            )

            fixed_report = check_architecture(proj, config=cfg)
            self.assertTrue(fixed_report.passed, msg=fixed_report.to_text())
            self.assertEqual(fixed_report.error_count, 0)
            self.assertEqual(fixed_report.warning_count, 0)
            self.assertEqual(fixed_report.violations, [])


class TestScaffoldAndHostCompile(unittest.TestCase):
    """验证脚手架生成的四层 C 代码通过真实宿主机 gcc 与 cmake/ctest 严格编译运行且架构 0 违规。"""

    def test_scaffold_skeleton_bsp_service_gcc_cmake_and_arch_clean(self) -> None:
        gcc_bin = shutil.which("gcc")
        cmake_bin = shutil.which("cmake")
        ctest_bin = shutil.which("ctest")
        self.assertIsNotNone(gcc_bin, "Host gcc is required for verification")
        self.assertIsNotNone(cmake_bin, "Host cmake is required for verification")
        self.assertIsNotNone(ctest_bin, "Host ctest is required for verification")

        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "portable_fw"

            # 1. 生成完整四层骨架 + 新增 pressure_sensor BSP + 新增 fluid Service
            r_skel = scaffold_code(proj, "skeleton")
            self.assertIn("mcu-harness.toml", r_skel.created_files)
            self.assertIn("CMakeLists.txt", r_skel.created_files)
            self.assertIn("include/pal/pal_types.h", r_skel.created_files)
            self.assertIn("app/app_main.c", r_skel.created_files)

            r_bsp = scaffold_code(proj, "bsp", "pressure_sensor")
            self.assertIn("bsp/pressure_sensor.h", r_bsp.created_files)
            self.assertIn("bsp/pressure_sensor.c", r_bsp.created_files)
            self.assertIn("tests/host/test_bsp_pressure_sensor.c", r_bsp.created_files)
            # 已存在的 PAL 头文件应平滑复用而不报错
            self.assertIn("include/pal/pal_types.h", r_bsp.skipped_files)

            r_svc = scaffold_code(proj, "service", "fluid")
            self.assertIn("service/fluid_service.h", r_svc.created_files)
            self.assertIn("service/fluid_service.c", r_svc.created_files)
            self.assertIn("tests/host/test_service_fluid.c", r_svc.created_files)

            # 重复生成未开启 overwrite 应拦截报错，开启 overwrite=True 则成功覆盖
            with self.assertRaises(ScaffoldError):
                scaffold_code(proj, "bsp", "pressure_sensor", overwrite=False)
            r_overwrite = scaffold_code(proj, "bsp", "pressure_sensor", overwrite=True)
            self.assertIn("bsp/pressure_sensor.c", r_overwrite.created_files)

            # 2. 直接使用宿主机 gcc -Wall -Wextra -Werror -std=c11 编译全部 6 个 PAL 头文件及生成的单测可执行文件
            bin_dir = Path(tmp) / "raw_gcc_bins"
            bin_dir.mkdir(parents=True)

            all_pal_smoke_c = bin_dir / "test_all_pal_headers.c"
            all_pal_smoke_c.write_text(
                '#include "pal/pal_types.h"\n'
                '#include "pal/pal_gpio.h"\n'
                '#include "pal/pal_i2c.h"\n'
                '#include "pal/pal_spi.h"\n'
                '#include "pal/pal_pwm.h"\n'
                '#include "pal/pal_adc.h"\n'
                "int main(void) {\n"
                "    pal_i2c_t i2c = {0};\n"
                "    pal_spi_t spi = {0};\n"
                "    (void)i2c;\n"
                "    (void)spi;\n"
                "    return (int)PAL_OK;\n"
                "}\n",
                encoding="utf-8",
            )
            all_pal_bin = bin_dir / "test_all_pal_headers"
            proc_pal = subprocess.run(
                [
                    str(gcc_bin),
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-std=c11",
                    f"-I{proj / 'include'}",
                    str(all_pal_smoke_c),
                    "-o",
                    str(all_pal_bin),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_pal.returncode, 0, msg=proc_pal.stderr)
            self.assertEqual(subprocess.run([str(all_pal_bin)], check=False).returncode, 0)

            bsp_test_bin = bin_dir / "test_bsp_pressure_sensor"
            proc_gcc_bsp = subprocess.run(
                [
                    str(gcc_bin),
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-std=c11",
                    f"-I{proj / 'include'}",
                    f"-I{proj}",
                    f"-I{proj / 'bsp'}",
                    str(proj / "tests/host/test_bsp_pressure_sensor.c"),
                    str(proj / "bsp/pressure_sensor.c"),
                    "-o",
                    str(bsp_test_bin),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_gcc_bsp.returncode, 0, msg=proc_gcc_bsp.stderr)
            run_bsp = subprocess.run([str(bsp_test_bin)], capture_output=True, text=True, check=False)
            self.assertEqual(run_bsp.returncode, 0, msg=run_bsp.stderr)
            self.assertIn("[PASS] test_bsp_pressure_sensor", run_bsp.stdout)

            svc_test_bin = bin_dir / "test_service_fluid"
            proc_gcc_svc = subprocess.run(
                [
                    str(gcc_bin),
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-std=c11",
                    f"-I{proj}",
                    f"-I{proj / 'service'}",
                    str(proj / "tests/host/test_service_fluid.c"),
                    str(proj / "service/fluid_service.c"),
                    "-o",
                    str(svc_test_bin),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_gcc_svc.returncode, 0, msg=proc_gcc_svc.stderr)
            run_svc = subprocess.run([str(svc_test_bin)], capture_output=True, text=True, check=False)
            self.assertEqual(run_svc.returncode, 0, msg=run_svc.stderr)
            self.assertIn("[PASS] test_service_fluid", run_svc.stdout)

            # 3. 使用 CMake + CTest 构建整个四层工程与全部主机单元测试并运行
            cmake_build_dir = proj / "build_host"
            proc_cfg = subprocess.run(
                [str(cmake_bin), "-S", str(proj), "-B", str(cmake_build_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_cfg.returncode, 0, msg=proc_cfg.stderr + proc_cfg.stdout)

            proc_bld = subprocess.run(
                [str(cmake_bin), "--build", str(cmake_build_dir)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_bld.returncode, 0, msg=proc_bld.stderr + proc_bld.stdout)

            proc_ctest = subprocess.run(
                [str(ctest_bin), "--test-dir", str(cmake_build_dir), "--output-on-failure"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_ctest.returncode, 0, msg=proc_ctest.stderr + proc_ctest.stdout)
            self.assertIn("100% tests passed", proc_ctest.stdout)

            # 4. 对构建后的完整工程执行 check_architecture，验证自动跳过 build_host/ 与 tests/ 且 0 errors, 0 warnings
            arch_rep = check_architecture(proj)
            self.assertTrue(arch_rep.passed, msg=arch_rep.to_text())
            self.assertEqual(arch_rep.error_count, 0)
            self.assertEqual(arch_rep.warning_count, 0)
            self.assertIn("0 errors, 0 warnings", arch_rep.to_text())


class TestArchCli(unittest.TestCase):
    """验证 mcu-arch CLI 子命令行为与 JSON 输出格式。"""

    def test_cli_scaffold_and_check_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "cli_proj"

            out_buf = io.StringIO()
            with redirect_stdout(out_buf):
                rc = arch_cli_main(["scaffold", "pal", "--dir", str(proj), "--json"])
            self.assertEqual(rc, 0)
            pal_payload = json.loads(out_buf.getvalue())
            self.assertEqual(pal_payload["kind"], "pal")
            self.assertEqual(len(pal_payload["created_files"]), 6)

            out_buf = io.StringIO()
            with redirect_stdout(out_buf):
                rc = arch_cli_main(["scaffold", "bsp", "ina226", "--dir", str(proj), "--json"])
            self.assertEqual(rc, 0)

            out_buf = io.StringIO()
            with redirect_stdout(out_buf):
                rc = arch_cli_main(["check", str(proj), "--json"])
            self.assertEqual(rc, 0)
            chk_payload = json.loads(out_buf.getvalue())
            self.assertTrue(chk_payload["passed"])
            self.assertEqual(chk_payload["error_count"], 0)
            self.assertEqual(chk_payload["warning_count"], 0)

            # 注入一处分层违规，验证 mcu-arch check 返回非 0 退出码
            (proj / "bsp" / "bad_inc.c").write_text('#include "board_config.h"\n', encoding="utf-8")
            out_buf = io.StringIO()
            with redirect_stdout(out_buf):
                viol_rc = arch_cli_main(["check", str(proj)])
            self.assertEqual(viol_rc, 1)
            self.assertIn("FAIL", out_buf.getvalue())

            err_buf = io.StringIO()
            with redirect_stderr(err_buf):
                bad_rc = arch_cli_main(["check", str(Path(tmp) / "does_not_exist")])
            self.assertEqual(bad_rc, 1)

    def test_nonexistent_dir_raises_arch_error(self) -> None:
        with self.assertRaises(ArchError):
            check_architecture("/tmp/definitely_missing_mcu_harness_arch_dir_999999")


if __name__ == "__main__":
    unittest.main(verbosity=2)
