"""DebugProbe / MockProbe / JLinkProbe / inspect_peripheral 单元测试。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mcu_harness.probe import (
    JLinkProbe,
    MockProbe,
    PeripheralSnapshot,
    ProbeError,
    ProbeManager,
    inspect_peripheral_registers,
)
from mcu_harness.probe.jlink import _discover_jlink_dll
from mcu_harness.svd import load_svd

SVD_FIX = Path(__file__).parent / "fixtures" / "stm32h563_mini.svd"


def test_mock_probe_memory_and_alignment():
    with MockProbe() as probe:
        info = probe.info()
        assert info.connected is True
        assert info.backend == "mock"
        assert probe.read_u32(0xE000ED00) == [0x410FD210]  # Cortex-M33 CPUID

        # 小端序 8 / 16 / 32 位一致
        assert probe.read_u8(0xE000ED00, 4) == [0x10, 0xD2, 0x0F, 0x41]
        assert probe.read_u16(0xE000ED00, 2) == [0xD210, 0x410F]

        with pytest.raises(ProbeError, match="Unaligned"):
            probe.read_u32(0xE000ED01)

        assert probe.halt() is True and probe.is_halted() is True
        assert probe.resume() is True and probe.is_halted() is False


def test_inspect_peripheral_single_and_batch():
    dev = load_svd(SVD_FIX)
    with MockProbe() as probe:
        # 1. 单个寄存器解码（GPIOA->MODER 默认预置为 0xAB000400）
        dec = inspect_peripheral_registers(probe, dev, "GPIOA", register_name="MODER")
        assert dec.address == "0x42020000"
        mode5 = next(f for f in dec.fields if f.name == "MODE5")
        assert mode5.value == 1
        assert "Output" in (mode5.meaning or "")

        # 2. 批量读取多个寄存器
        snap = inspect_peripheral_registers(probe, dev, "GPIOA", registers=["MODER", "ODR"])
        assert isinstance(snap, PeripheralSnapshot)
        assert [r.register for r in snap.registers] == ["MODER", "ODR"]
        odr = snap.registers[1]
        assert next(f for f in odr.fields if f.name == "OD5").value == 1

        # 3. read_all：读完整个外设，单个失败不影响其余
        snap_all = inspect_peripheral_registers(probe, dev, "GPIOB", read_all=True)
        assert any(r.register == "MODER" for r in snap_all.registers)
        assert not snap_all.errors


def test_probe_manager_reuses_session():
    mgr = ProbeManager()
    info1 = mgr.connect(backend="mock", device="STM32H563ZI")
    assert info1.connected is True

    with mgr.session() as p1:
        p1.write_u32(0x20000000, 0xDEADBEEF)
    with mgr.session() as p2:
        assert p2 is p1
        assert p2.read_u32(0x20000000) == [0xDEADBEEF]

    mgr.disconnect()


def test_discover_jlink_dll_from_path(tmp_path, monkeypatch):
    real_dir = tmp_path / "segger"
    real_dir.mkdir()
    so = real_dir / "libjlinkarm.so.9"
    so.write_text("stub")
    exe = real_dir / "JLinkExe"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "JLinkExe").symlink_to(exe)

    monkeypatch.delenv("MCU_HARNESS_JLINK_LIB", raising=False)
    monkeypatch.setenv("PATH", str(bin_dir))
    assert _discover_jlink_dll() == str(so)


def test_jlink_probe_with_fake_pylink(monkeypatch):
    """用桩对象验证 JLinkProbe 的连接流程、不复位约定与读内存调用。"""
    calls = []

    class FakeJLink:
        serial_number = 12345678
        speed = 4000

        def __init__(self, lib=None):
            self._open = False
            self._conn = False
            self._halted = False

        def open(self, serial_no=None):
            calls.append(("open", serial_no))
            self._open = True

        def set_tif(self, interface):
            calls.append(("set_tif", interface))

        def connect(self, chip_name, speed="auto"):
            calls.append(("connect", chip_name, speed))
            self._conn = True

        def opened(self):
            return self._open

        def connected(self):
            return self._conn

        def core_id(self):
            return 0x6BA02477

        def core_name(self):
            return "Cortex-M33"

        def halted(self):
            return self._halted

        def halt(self):
            self._halted = True
            return True

        def restart(self):
            self._halted = False
            return True

        def memory_read32(self, addr, count):
            calls.append(("read32", addr, count))
            return [0x12345678] * count

        def close(self):
            calls.append(("close",))
            self._open = False
            self._conn = False

    fake_pylink = SimpleNamespace(
        JLink=FakeJLink,
        Library=lambda dllpath=None: object(),
        enums=SimpleNamespace(JLinkInterfaces=SimpleNamespace(SWD=1, JTAG=0)),
    )
    monkeypatch.setattr("mcu_harness.probe.jlink._load_pylink_library", lambda dll_path=None: (fake_pylink, object()))

    with JLinkProbe(device="STM32H563ZI", interface="SWD") as probe:
        info = probe.info()
        assert info.core_name == "Cortex-M33"
        assert info.serial_no == "12345678"
        assert probe.read_u32(0xE000ED00, 1) == [0x12345678]
        assert probe.halt() is True and probe.is_halted() is True
        assert probe.resume() is True and probe.is_halted() is False

    # 确认没有调用过 reset，保护芯片现场
    assert [c[0] for c in calls] == ["open", "set_tif", "connect", "read32", "close"]
