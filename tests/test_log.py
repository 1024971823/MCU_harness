"""环形日志缓冲区与 RTT 后台采集器的单元测试。"""

from __future__ import annotations

import threading
import time

from mcu_harness.log import RingLogBuffer, RttLogCollector
from mcu_harness.probe import MockProbe, ProbeError, ProbeManager
from tests.test_elf import _make_minimal_elf32


def test_ring_buffer_partial_lines_utf8_and_ansi():
    buf = RingLogBuffer(max_lines=100)
    # 模拟“你好”被拆在两次 rtt_read 里 + ANSI 颜色 + 未换行残余片段
    text_bytes = "\x1b[32m[INFO]\x1b[0m 你好世界\r\nADC=".encode("utf-8")
    cut = text_bytes.index("好".encode("utf-8")) + 1  # 从多字节汉字中间切开

    assert buf.feed_bytes(text_bytes[:cut]) == 0
    assert buf.feed_bytes(text_bytes[cut:]) == 1

    res = buf.read()
    assert len(res.entries) == 1
    assert res.entries[0].text == "[INFO] 你好世界"
    assert res.partial_line == "ADC="

    buf.feed_bytes(b"1234\n")
    res2 = buf.read(since_seq=res.newest_seq)
    assert [e.text for e in res2.entries] == ["ADC=1234"]
    assert res2.partial_line == ""


def test_ring_buffer_wrap_and_pattern_context():
    buf = RingLogBuffer(max_lines=5)
    for i in range(1, 9):
        tag = "ERROR" if i == 6 else "INFO"
        buf.feed_text(f"[{tag}] line {i}\n")

    # 缓冲只保留最近 5 条：seq 4..8
    res = buf.read(since_seq=1)
    assert [e.seq for e in res.entries] == [4, 5, 6, 7, 8]
    assert res.dropped_since_requested == 2  # seq 2、3 被覆盖

    # 关键词 + 上下文 1 行（不重复）
    filt = buf.read(pattern="error", context_lines=1)
    assert filt.matched_count == 1
    assert [e.seq for e in filt.entries] == [5, 6, 7]


def test_rtt_collector_background_streaming_and_disconnect_hook():
    pm = ProbeManager()
    mock = MockProbe()
    mock.connect()
    pm.set_probe(mock)

    collector = RttLogCollector(pm, max_lines=50, poll_interval_s=0.005)
    try:
        st = collector.start(channel=0)
        assert st.running is True

        mock.push_rtt("boot\r\n")
        mock.push_rtt("[ERROR] hardfault test\n")

        # 轮询等待后台线程收完两行（上限 1s，通常几毫秒）
        deadline = time.monotonic() + 1.0
        entries = []
        while time.monotonic() < deadline:
            res, _ = collector.read(auto_start=False)
            if len(res.entries) >= 2:
                entries = res.entries
                break
            time.sleep(0.01)

        assert [e.text for e in entries] == ["boot", "[ERROR] hardfault test"]
        assert collector.status().bytes_received > 0

        # 断开探针应自动停止采集线程
        pm.disconnect()
        assert collector.is_running() is False
    finally:
        collector.stop()
        pm.disconnect()


def test_rtt_collector_survives_transient_probe_error():
    class FlakyMockProbe(MockProbe):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def rtt_read(self, channel: int = 0, max_bytes: int = 4096) -> bytes:
            self.calls += 1
            if self.calls <= 2:
                raise ProbeError("RTT control block not found yet")
            return super().rtt_read(channel, max_bytes)

    pm = ProbeManager()
    mock = FlakyMockProbe()
    mock.connect()
    mock.push_rtt("ready\n")
    pm.set_probe(mock)

    collector = RttLogCollector(pm, poll_interval_s=0.005)
    try:
        collector.start()
        deadline = time.monotonic() + 1.5
        got = []
        while time.monotonic() < deadline:
            res, _ = collector.read(auto_start=False)
            if res.entries:
                got = res.entries
                break
            time.sleep(0.02)
        assert [e.text for e in got] == ["ready"]
    finally:
        collector.stop()
        pm.disconnect()


def test_rtt_collector_resolves_block_address_from_elf(tmp_path):
    elf = tmp_path / "fw.elf"
    elf.write_bytes(_make_minimal_elf32([("_SEGGER_RTT", 0x20005170, 168)]))

    seen_block_addrs = []

    class RecordingMockProbe(MockProbe):
        def rtt_start(self, block_address=None):
            seen_block_addrs.append(block_address)
            super().rtt_start(block_address=block_address)

    pm = ProbeManager()
    mock = RecordingMockProbe()
    mock.connect()
    mock.push_rtt("from elf addr\n")
    pm.set_probe(mock)

    collector = RttLogCollector(pm, poll_interval_s=0.005)
    try:
        res, st = collector.read(elf_path=elf)
        assert seen_block_addrs == [0x20005170]
        assert st.block_address == "0x20005170"
        assert [e.text for e in res.entries] == ["from elf addr"]
    finally:
        collector.stop()
        pm.disconnect()


def test_rtt_collector_survives_unexpected_exception():
    """后台线程捕获通用异常并写入 last_error 后继续存活（FIX-05）。"""

    class BrokenReadMock(MockProbe):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def rtt_read(self, channel: int = 0, max_bytes: int = 4096) -> bytes:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("unexpected driver glitch")
            return super().rtt_read(channel, max_bytes)

    pm = ProbeManager()
    mock = BrokenReadMock()
    mock.connect()
    mock.push_rtt("after glitch\n")
    pm.set_probe(mock)

    collector = RttLogCollector(pm, poll_interval_s=0.005)
    try:
        collector.start()
        deadline = time.monotonic() + 1.5
        got = []
        saw_error = False
        while time.monotonic() < deadline:
            st = collector.status()
            if st.last_error and "RuntimeError" in st.last_error:
                saw_error = True
            res, _ = collector.read(auto_start=False)
            if res.entries:
                got = res.entries
                break
            time.sleep(0.02)
        assert [e.text for e in got] == ["after glitch"]
        assert collector.is_running() is True
        assert saw_error or mock.calls >= 2
    finally:
        collector.stop()
        pm.disconnect()


def test_rtt_collector_concurrent_read_and_disconnect_no_deadlock(monkeypatch):
    """多线程并发首次 read 与 connect/disconnect 切换，不触发死锁且不重复 rtt_start（FIX-02 / FIX-05）。"""
    monkeypatch.setenv("MCU_HARNESS_BACKEND", "mock")
    start_calls = []

    class CountingMockProbe(MockProbe):
        def rtt_start(self, block_address=None):
            start_calls.append(block_address)
            super().rtt_start(block_address=block_address)

    pm = ProbeManager()
    monkeypatch.setattr(
        pm,
        "_create_probe",
        lambda cfg: CountingMockProbe(device=str(cfg["device"]), interface=str(cfg["interface"])),
    )
    pm.connect(backend="mock")
    collector = RttLogCollector(pm, poll_interval_s=0.005)
    errors = []

    # 1. 并发首次自动启动读取：rtt_start 应仅被触发一次
    barrier = threading.Barrier(4)

    def first_reader():
        try:
            barrier.wait(timeout=2.0)
            collector.read(tail_lines=10)
        except Exception as e:
            errors.append(e)

    readers = [threading.Thread(target=first_reader) for _ in range(4)]
    for t in readers:
        t.start()
    for t in readers:
        t.join(timeout=3.0)
        assert not t.is_alive()
    assert not errors
    assert len(start_calls) == 1

    # 2. stop() 应毫秒级返回，不卡 1.5s 超时
    t0 = time.monotonic()
    collector.stop()
    assert time.monotonic() - t0 < 0.5

    # 3. 并发读日志 + 重连/断连压力测试（验证无 AB-BA 死锁）
    stop_flag = threading.Event()

    def loop_read():
        while not stop_flag.is_set():
            try:
                collector.read(tail_lines=5)
            except Exception as e:
                errors.append(e)

    def loop_toggle():
        for _ in range(8):
            try:
                pm.connect(backend="mock", reconnect=True)
                pm.disconnect()
                pm.connect(backend="mock")
            except Exception as e:
                errors.append(e)
        stop_flag.set()

    t_r = threading.Thread(target=loop_read)
    t_t = threading.Thread(target=loop_toggle)
    t_r.start()
    t_t.start()
    t_t.join(timeout=5.0)
    stop_flag.set()
    t_r.join(timeout=5.0)
    assert not t_t.is_alive() and not t_r.is_alive()
    assert not errors

    collector.stop()
    pm.disconnect()
