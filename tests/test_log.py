"""环形日志缓冲区与 RTT 后台采集器的单元测试。"""

from __future__ import annotations

import time

from mcu_harness.log import RingLogBuffer, RttLogCollector
from mcu_harness.probe import MockProbe, ProbeError, ProbeManager


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
