"""线程安全的非阻塞日志环形缓冲区。

设计要点：
    * 用 collections.deque(maxlen=...) 限制内存上限，防止长时间跑机撑爆进程。
    * 增量 UTF-8 解码 + 行拼接缓存：一次 rtt_read 可能在汉字中间或行中间截断，
      这里会暂存未闭合的尾部字节/字符，等收到换行符再入队。
    * 单调递增序号 seq：AI 每次调用可带上上次拿到的 newest_seq（since_seq），
      只拉取增量新日志；若环形缓冲发生覆盖，会自动计算 dropped_since_requested。
    * 支持关键词/正则过滤 + 上下文行数（类似 grep -C）。
"""

from __future__ import annotations

import codecs
import re
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field

_RE_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@dataclass(frozen=True)
class LogEntry:
    seq: int
    timestamp: float
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LogReadResult:
    entries: list[LogEntry] = field(default_factory=list)
    total_buffered: int = 0
    oldest_seq: int | None = None
    newest_seq: int | None = None
    dropped_since_requested: int = 0
    partial_line: str = ""
    matched_count: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_text(self) -> str:
        lines: list[str] = []
        if self.dropped_since_requested > 0:
            lines.append(f"[warn] {self.dropped_since_requested} older line(s) overwritten by ring buffer")
        for e in self.entries:
            lines.append(f"[{e.seq}] {e.text}")
        if self.partial_line:
            lines.append(f"[partial] {self.partial_line}")
        if not self.entries and not self.partial_line:
            lines.append("(no log lines)")
        return "\n".join(lines)


class RingLogBuffer:
    """带增量行组装与条件切片的线程安全环形缓冲。"""

    def __init__(self, max_lines: int = 2000, strip_ansi: bool = True) -> None:
        if max_lines <= 0:
            raise ValueError(f"max_lines must be > 0, got {max_lines}")
        self.max_lines = max_lines
        self.strip_ansi = strip_ansi
        self._lock = threading.Lock()
        self._entries: deque[LogEntry] = deque(maxlen=max_lines)
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._partial = ""
        self._next_seq = 1

    def feed_bytes(self, data: bytes, *, timestamp: float | None = None) -> int:
        """写入原始字节流（自动处理跨包截断的 UTF-8 与未换行片段），返回新增行数。"""
        if not data:
            return 0
        with self._lock:
            text = self._decoder.decode(data, final=False)
            return self._append_text_locked(text, timestamp=timestamp)

    def feed_text(self, text: str, *, timestamp: float | None = None) -> int:
        """直接写入字符串。"""
        if not text:
            return 0
        with self._lock:
            return self._append_text_locked(text, timestamp=timestamp)

    def _append_text_locked(self, text: str, *, timestamp: float | None) -> int:
        if self.strip_ansi:
            text = _RE_ANSI.sub("", text)
        # 统一 CRLF / 单独 CR 为 \n 方便按行切片
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        combined = self._partial + text
        parts = combined.split("\n")
        self._partial = parts.pop()  # 最后一段还没有换行符，暂存等下一批数据

        ts = timestamp if timestamp is not None else time.time()
        added = 0
        for line in parts:
            self._entries.append(LogEntry(seq=self._next_seq, timestamp=ts, text=line))
            self._next_seq += 1
            added += 1
        return added

    def clear(self) -> int:
        """清空已缓冲的日志与未闭合残余行，返回清除条数。"""
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            self._partial = ""
            self._decoder.reset()
            return count

    def read(
        self,
        *,
        tail_lines: int = 50,
        since_seq: int | None = None,
        pattern: str | None = None,
        regex: bool = False,
        case_sensitive: bool = False,
        context_lines: int = 2,
        include_partial: bool = True,
    ) -> LogReadResult:
        """按尾行、增量序号或关键词上下文拉取日志。"""
        if tail_lines <= 0:
            raise ValueError(f"tail_lines must be > 0, got {tail_lines}")
        if context_lines < 0:
            raise ValueError(f"context_lines must be >= 0, got {context_lines}")

        matcher = _compile_matcher(pattern, regex=regex, case_sensitive=case_sensitive) if pattern else None

        with self._lock:
            all_entries = list(self._entries)
            partial = self._partial if include_partial else ""

        oldest_seq = all_entries[0].seq if all_entries else None
        newest_seq = all_entries[-1].seq if all_entries else None

        dropped = 0
        if since_seq is not None:
            if oldest_seq is not None and since_seq < oldest_seq - 1:
                dropped = (oldest_seq - 1) - since_seq
            pool = [e for e in all_entries if e.seq > since_seq]
        else:
            pool = all_entries

        matched_count: int | None = None
        if matcher is not None:
            hit_indices = [i for i, e in enumerate(pool) if matcher(e.text)]
            matched_count = len(hit_indices)
            if not hit_indices:
                selected: list[LogEntry] = []
            else:
                keep: set[int] = set()
                n = len(pool)
                for idx in hit_indices:
                    lo = max(0, idx - context_lines)
                    hi = min(n - 1, idx + context_lines)
                    keep.update(range(lo, hi + 1))
                selected = [pool[i] for i in sorted(keep)][-tail_lines:]
        else:
            selected = pool if since_seq is not None and len(pool) <= tail_lines else pool[-tail_lines:]

        return LogReadResult(
            entries=selected,
            total_buffered=len(all_entries),
            oldest_seq=oldest_seq,
            newest_seq=newest_seq,
            dropped_since_requested=dropped,
            partial_line=partial,
            matched_count=matched_count,
        )


def _compile_matcher(pattern: str, *, regex: bool, case_sensitive: bool):
    if regex:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            compiled = re.compile(pattern, flags)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern {pattern!r}: {e}") from e
        return lambda text: compiled.search(text) is not None

    if case_sensitive:
        return lambda text: pattern in text
    needle = pattern.lower()
    return lambda text: needle in text.lower()
