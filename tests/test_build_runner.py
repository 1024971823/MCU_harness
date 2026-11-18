"""runner.py 的集成测试：用主机 gcc 真实编译 examples/demo_fw。"""

import shutil
from pathlib import Path

import pytest

from mcu_harness.build import build_firmware

DEMO = Path(__file__).parent.parent / "examples" / "demo_fw"
DEMO_MAKE = Path(__file__).parent.parent / "examples" / "demo_make_fw"

pytestmark = pytest.mark.skipif(
    not (shutil.which("cmake") and shutil.which("ninja") and shutil.which("gcc")),
    reason="需要 cmake + ninja + gcc",
)


@pytest.fixture
def project(tmp_path):
    dst = tmp_path / "demo_fw"
    shutil.copytree(DEMO, dst)
    return dst


@pytest.fixture
def make_project(tmp_path):
    dst = tmp_path / "demo_make_fw"
    shutil.copytree(DEMO_MAKE, dst)
    return dst


def test_build_success_with_warning(project):
    r = build_firmware(project)
    assert r.success, r.to_text()
    assert r.stage == "build"
    assert r.total_warnings == 1
    w = r.warnings[0]
    assert w.file == "main.c" and w.flag == "-Wunused-variable"
    assert any(a.path.endswith("demo_fw.elf") for a in r.artifacts)
    assert len(r.commands) == 2  # configure + build


def test_incremental_build_skips_configure(project):
    build_firmware(project)
    r = build_firmware(project)
    assert r.success and len(r.commands) == 1


def test_build_error(project):
    r = build_firmware(project, extra_cmake_args=["-DDEMO_INJECT_ERROR=ON"])
    assert not r.success and r.stage == "build"
    assert r.total_errors >= 1
    e = r.errors[0]
    assert e.file == "main.c" and "led_tggle" in e.message
    assert e.function == "main"
    assert r.artifacts == []
    assert "led_tggle" in r.to_text()


def test_configure_error(project):
    (project / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\nproject(x C)\nadd_executable(x nope.c)\n"
    )
    r = build_firmware(project)
    assert not r.success and r.stage == "configure"
    assert any(e.category == "cmake" for e in r.errors), r.to_text()


def test_missing_project(tmp_path):
    r = build_firmware(tmp_path)
    assert not r.success and r.stage == "precheck"
    assert "Neither CMakeLists.txt nor Makefile" in r.message


@pytest.mark.skipif(
    not (shutil.which("make") and shutil.which("gcc") and shutil.which("size")),
    reason="需要 make + gcc + size",
)
def test_make_auto_detect_and_size(make_project):
    r = build_firmware(make_project, jobs=2)
    assert r.success, r.to_text()
    assert r.build_system == "make"
    assert r.stage == "build"
    assert r.total_warnings == 1
    assert r.warnings[0].file == "main.c"
    assert any(a.path.endswith("demo_fw.elf") for a in r.artifacts)
    assert len(r.sizes) == 1
    assert r.sizes[0].total_bytes > 0
    assert "Binary size:" in r.to_text()


@pytest.mark.skipif(not (shutil.which("make") and shutil.which("gcc")), reason="需要 make + gcc")
def test_make_clean_and_error(make_project):
    r1 = build_firmware(make_project)
    assert r1.success

    r2 = build_firmware(make_project, clean=True, extra_args=["DEMO_INJECT_ERROR=1"])
    assert not r2.success
    assert r2.build_system == "make"
    assert len(r2.commands) == 2  # make clean + make
    assert r2.total_errors >= 1
    assert "led_tggle" in r2.errors[0].message
    assert r2.artifacts == []


@pytest.mark.skipif(not (shutil.which("make") and shutil.which("gcc")), reason="需要 make + gcc")
def test_make_custom_makefile_and_missing_target(make_project):
    custom = make_project / "Makefile.custom"
    (make_project / "Makefile").rename(custom)

    r_ok = build_firmware(make_project, makefile="Makefile.custom")
    assert r_ok.success and r_ok.build_system == "make"

    r_bad = build_firmware(make_project, makefile="Makefile.custom", target="no_such_target")
    assert not r_bad.success
    assert any(e.category == "make" and "No rule to make target" in e.message for e in r_bad.errors)
