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
            calls.append(("halt",))
            self._halted = True
            return True

        def go(self):
            calls.append(("go",))
            self._halted = False

        def restart(self):
            calls.append(("restart_FORBIDDEN",))
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
        assert probe.halt() is False  # 已处于暂停态，再次 halt 不产生状态变更
        assert probe.resume() is True and probe.is_halted() is False
        assert probe.resume() is False  # 已处于运行态，再次 resume 返回 False

    # 确认没有调用过 reset / restart，保护芯片现场（FIX-01）
    assert [c[0] for c in calls] == ["open", "set_tif", "connect", "read32", "halt", "halt", "go", "close"]


def test_jlink_resume_falls_back_to_dll_go(monkeypatch):
    """验证无 .go() 方法的真实 pylink.JLink 实例走 _dll.JLINKARM_Go()，绝不复位（FIX-01）。"""
    calls = []

    class FakeDll:
        def JLINKARM_Go(self):
            calls.append("JLINKARM_Go")

    class FakePyLinkStyleJLink:
        _dll = FakeDll()

        def __init__(self, lib=None):
            self._halted = True

        def open(self, serial_no=None):
            pass

        def set_tif(self, interface):
            pass

        def connect(self, chip_name, speed="auto"):
            pass

        def opened(self):
            return True

        def connected(self):
            return True

        def halted(self):
            return self._halted

        def restart(self):
            calls.append("restart_FORBIDDEN")

        def close(self):
            pass

    fake_pylink = SimpleNamespace(
        JLink=FakePyLinkStyleJLink,
        Library=lambda dllpath=None: object(),
        enums=SimpleNamespace(JLinkInterfaces=SimpleNamespace(SWD=1, JTAG=0)),
    )
    monkeypatch.setattr("mcu_harness.probe.jlink._load_pylink_library", lambda dll_path=None: (fake_pylink, object()))

    with JLinkProbe() as probe:
        assert probe.resume() is True
        probe._jlink._halted = False
        assert probe.resume() is False

    assert calls == ["JLINKARM_Go"]


def test_probe_manager_clean_state_on_connect_failure_and_device_switch(monkeypatch):
    """验证连接失败不留脏状态、切换芯片型号自动重连、以及非法参数归一化（FIX-03）。"""
    mgr = ProbeManager()
    should_fail = {"flag": True}
    created = []

    class FlakyProbe(MockProbe):
        def connect(self):
            created.append(self.device)
            if should_fail["flag"]:
                raise ProbeError("target not powered")
            return super().connect()

    monkeypatch.setattr(mgr, "_create_probe", lambda cfg: FlakyProbe(device=str(cfg["device"])))

    with pytest.raises(ProbeError, match="not powered"):
        mgr.connect(backend="mock", device="STM32H563ZI")
    assert mgr._probe is None and mgr._config == {}

    # 上电后再次连接应直接成功
    should_fail["flag"] = False
    info = mgr.connect(backend="mock", device="STM32H563ZI")
    assert info.connected is True and info.device == "STM32H563ZI"

    # session() 指定不同芯片型号应自动触发重连切换
    with mgr.session(device="STM32H563RG") as p2:
        assert p2.info().device == "STM32H563RG"
    assert created == ["STM32H563ZI", "STM32H563ZI", "STM32H563RG"]
    mgr.disconnect()

    with pytest.raises(ProbeError, match="serial_no"):
        JLinkProbe(serial_no="not-a-number")
    with pytest.raises(ProbeError, match="speed"):
        JLinkProbe(speed="fast")
    with pytest.raises(ProbeError, match="interface"):
        JLinkProbe(interface="UART")
