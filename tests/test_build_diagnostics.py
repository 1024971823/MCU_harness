"""diagnostics.py 的单元测试：用真实风格的 arm-none-eabi-gcc 日志验证解析效果。"""

from pathlib import Path

from mcu_harness.build.diagnostics import parse_build_output

FIX = Path(__file__).parent / "fixtures"


def test_compile_errors_and_warnings():
    text = (FIX / "arm_compile_error.log").read_text()
    r = parse_build_output(text, project_dir="/home/dev/blinky")

    # 两条错误：未声明标识符 + 头文件缺失（fatal error 归为 error）
    assert [(e.file, e.line) for e in r.errors] == [
        ("Core/Src/main.c", 42),
        ("Core/Src/uart.c", 88),
    ]
    e0 = r.errors[0]
    assert e0.function == "main"
    assert e0.column == 3
    assert "GPIO_InitStruct" in e0.message
    assert len(e0.snippet) == 2 and "^~~" in e0.snippet[1]

    # 告警：unused variable 带 flag；board.h 的重定义被两个 .c 包含，应去重并计数
    assert len(r.warnings) == 2
    unused = r.warnings[0]
    assert unused.flag == "-Wunused-variable"
    assert unused.message == "unused variable 'tick'"
    redef = r.warnings[1]
    assert redef.file == "Core/Inc/board.h" and redef.count == 2

    # note 行不应进入任何列表，也不应把片段挂到上一条诊断上
    assert all("each undeclared" not in d.message for d in r.errors + r.warnings)
    assert len(r.failed_targets) == 2


def test_link_errors_and_memory():
    text = (FIX / "arm_link_error.log").read_text()
    r = parse_build_output(text, project_dir="/home/dev/blinky")

    msgs = [e.message for e in r.errors]
    assert any("undefined reference to `MX_USART2_UART_Init'" in m for m in msgs)
    assert any("will not fit in region `RAM'" in m for m in msgs)
    assert any("overflowed by 2048 bytes" in m for m in msgs)
    # collect2 总结行在已有具体错误时应被忽略
    assert not any("ld returned" in m for m in msgs)

    undef = next(e for e in r.errors if "undefined" in e.message)
    assert undef.category == "linker"
    assert undef.file == "Core/Src/main.c" and undef.line == 57

    mem = {m.name: m for m in r.memory}
    assert mem["RAM"].used_bytes == 133120
    assert mem["RAM"].total_bytes == 128 * 1024
    assert mem["RAM"].percent > 100
    assert mem["FLASH"].percent == 4.71


def test_cmake_error_block():
    text = (
        "-- Configuring incomplete, errors occurred!\n"
        "CMake Error at CMakeLists.txt:12 (add_executable):\n"
        "  Cannot find source file:\n"
        "\n"
        "    Core/Src/missing.c\n"
        "\n"
        "-- Generating done\n"
    )
    r = parse_build_output(text)
    assert len(r.errors) == 1
    e = r.errors[0]
    assert e.category == "cmake" and e.file == "CMakeLists.txt" and e.line == 12
    assert "missing.c" in e.message


def test_only_collect2():
    r = parse_build_output("collect2: error: ld returned 1 exit status\n")
    assert len(r.errors) == 1 and r.errors[0].category == "linker"


def test_noise_is_dropped():
    text = "[1/3] Building C object foo.c.obj\n-- The C compiler identification is GNU 13.2.1\n"
    r = parse_build_output(text)
    assert not r.errors and not r.warnings and not r.memory


def test_make_target_error_suppressed_when_gcc_error_present():
    text = (
        "arm-none-eabi-gcc -c Core/Src/main.c -o build/main.o\n"
        "Core/Src/main.c:12:3: error: 'x' undeclared (first use in this function)\n"
        "   12 |   x = 1;\n"
        "      |   ^\n"
        "make: *** [Makefile:184: build/main.o] Error 1\n"
    )
    r = parse_build_output(text)
    assert len(r.errors) == 1
    assert r.errors[0].category == "compiler"
    assert r.failed_targets == ["build/main.o"]


def test_make_own_errors():
    text = (
        "Makefile:42: *** missing separator.  Stop.\n"
        "make: *** No rule to make target 'flash', needed by 'all'.  Stop.\n"
        "make: arm-none-eabi-gcc: No such file or directory\n"
    )
    r = parse_build_output(text)
    assert len(r.errors) == 3
    assert all(e.category == "make" for e in r.errors)
    assert r.errors[0].file == "Makefile" and r.errors[0].line == 42
    assert "No rule to make target" in r.errors[1].message
    assert "arm-none-eabi-gcc" in r.errors[2].message

    # 没有其他具体错误时，make 目标失败应作为兜底错误保留
    only_target = parse_build_output("make: *** [Makefile:184: build/main.o] Error 1\n")
    assert len(only_target.errors) == 1
    assert only_target.errors[0].category == "make"
    assert only_target.errors[0].file == "Makefile" and only_target.errors[0].line == 184


def test_arm_size_berkeley_output():
    text = (
        "arm-none-eabi-size build/stm32h563.elf\n"
        "   text\t   data\t    bss\t    dec\t    hex\tfilename\n"
        "  12340\t    128\t   2048\t  14516\t   38b4\tbuild/stm32h563.elf\n"
    )
    r = parse_build_output(text)
    assert len(r.sizes) == 1
    s = r.sizes[0]
    assert s.file == "build/stm32h563.elf"
    assert s.text_bytes == 12340
    assert s.data_bytes == 128
    assert s.bss_bytes == 2048
    assert s.total_bytes == 14516
