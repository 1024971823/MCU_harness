"""非阻塞后台串口 UART 日志采集器与下行命令发送器。

设计要点：
    * 双后端打开策略：
      1. 若环境安装了 `pyserial`（`import serial`），默认优先采用 `serial.Serial`；
      2. Linux 零依赖原生 POSIX `termios` + `os.open(O_RDWR | O_NOCTTY | O_NONBLOCK)`
         + `select.select` 后端：无需任何第三方包即可配置 8N1 原始模式（关闭回显与行规程），
         直接读写 `/dev/ttyUSB*`、`/dev/ttyACM*` 及伪终端 `pty`。
    * 复用线程安全的 `RingLogBuffer`，完整支持跨包 UTF-8 多字节汉字拼接、`since_seq` 增量拉取、
      关键词/正则 + 上下文过滤。
    * 并发锁安全（与 `RttLogCollector` 保持一致）：
      - `stop()` 在 `_ctrl_lock` 内仅置位 `stop_event` 并摘除线程引用，释放锁后再 `join()`，
        线程退出后才关闭底层串口句柄，彻底杜绝死锁与 POSIX FD 复用竞态。
      - `write(data)` 通过独立 `_write_lock` 串行化下行发送，可与后台非阻塞读取并发工作。
"""

from __future__ import annotations

import errno
import glob
import os
import select
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from .ring_buffer import LogReadResult, RingLogBuffer

try:
    import serial  # type: ignore[import-not-found]
    import serial.tools.list_ports as _pyserial_list_ports  # type: ignore[import-not-found]
except ModuleNotFoundError:
    serial = None  # type: ignore[assignment]
    _pyserial_list_ports = None  # type: ignore[assignment]

try:
    import termios
except ModuleNotFoundError:  # pragma: no cover (Windows)
    termios = None  # type: ignore[assignment]


STANDARD_BAUDRATES: tuple[int, ...] = (
    9600,
    19200,
    38400,
    57600,
    115200,
    230400,
    460800,
    921600,
    1000000,
    2000000,
)


class UartError(RuntimeError):
    """串口打开、配置或读写失败。"""


@dataclass(frozen=True)
class SerialPortInfo:
    port: str
    description: str = ""
    hwid: str = ""
    by_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UartStatus:
    running: bool
    port: str | None
    baudrate: int
    backend: str | None
    bytes_received: int
    bytes_sent: int
    total_buffered: int
    oldest_seq: int | None
    newest_seq: int | None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# 串口枚举
# ---------------------------------------------------------------------------


def list_serial_ports() -> list[SerialPortInfo]:
    """枚举当前主机可用的串口设备（优先调用 `serial.tools.list_ports`，回退 Linux `/dev` 扫描）。"""
    by_id_map: dict[str, str] = {}
    by_id_dir = Path("/dev/serial/by-id")
    if by_id_dir.is_dir():
        for entry in sorted(by_id_dir.iterdir()):
            try:
                real_dev = str(entry.resolve())
                by_id_map.setdefault(real_dev, str(entry))
            except OSError:
                continue

    results: list[SerialPortInfo] = []
    seen_ports: set[str] = set()

    if _pyserial_list_ports is not None:
        try:
            for info in _pyserial_list_ports.comports():
                dev = str(info.device)
                real_dev = _safe_realpath(dev)
                seen_ports.add(real_dev)
                results.append(
                    SerialPortInfo(
                        port=dev,
                        description=str(getattr(info, "description", "") or ""),
                        hwid=str(getattr(info, "hwid", "") or ""),
                        by_id=by_id_map.get(real_dev),
                    )
                )
        except Exception:
            pass

    if sys.platform.startswith("linux") and not results:
        candidates: list[str] = []
        candidates.extend(sorted(glob.glob("/dev/ttyUSB*")))
        candidates.extend(sorted(glob.glob("/dev/ttyACM*")))
        for real_dev in sorted(by_id_map.keys()):
            if real_dev not in candidates:
                candidates.append(real_dev)

        for dev in candidates:
            real_dev = _safe_realpath(dev)
            if real_dev in seen_ports:
                continue
            seen_ports.add(real_dev)
            by_id_link = by_id_map.get(real_dev)
            desc = _read_linux_sysfs_tty_desc(Path(real_dev).name)
            if not desc and by_id_link:
                desc = Path(by_id_link).name
            results.append(
                SerialPortInfo(
                    port=dev,
                    description=desc,
                    hwid="",
                    by_id=by_id_link,
                )
            )

    results.sort(key=lambda p: p.port)
    return results


def _safe_realpath(path_str: str) -> str:
    try:
        return str(Path(path_str).resolve())
    except OSError:
        return path_str


def _read_linux_sysfs_tty_desc(tty_name: str) -> str:
    dev_dir = Path("/sys/class/tty") / tty_name / "device"
    for rel in ("interface", "../product", "../../product"):
        cand = dev_dir / rel
        try:
            if cand.is_file():
                txt = cand.read_text(encoding="utf-8", errors="replace").strip()
                if txt:
                    return txt
        except OSError:
            continue
    return ""


# ---------------------------------------------------------------------------
# 底层串口传输后端（pyserial 后端 + Linux 原生 POSIX termios 后端）
# ---------------------------------------------------------------------------


class _SerialTransport(Protocol):
    name: str

    def read_nonblocking(self, max_bytes: int, timeout_s: float) -> bytes: ...
    def write_all(self, data: bytes, timeout_s: float) -> int: ...
    def close(self) -> None: ...


class _PySerialTransport:
    name = "pyserial"

    def __init__(self, port: str, baudrate: int) -> None:
        assert serial is not None
        try:
            self._ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0,
                write_timeout=1.0,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            )
        except Exception as e:
            raise UartError(f"Failed to open serial port {port!r} @ {baudrate} via pyserial: {e}") from e

    def read_nonblocking(self, max_bytes: int, timeout_s: float) -> bytes:
        try:
            fd = self._ser.fileno()
        except Exception:
            fd = None

        if fd is not None and sys.platform != "win32":
            ready, _, _ = select.select([fd], [], [], max(0.0, timeout_s))
            if not ready:
                return b""
            data = self._ser.read(max_bytes)
            if not data:
                # EOF（例如对端 pty 关闭），短暂退避防止空转
                time.sleep(min(0.02, max(0.005, timeout_s)))
            return bytes(data)

        waiting = getattr(self._ser, "in_waiting", 0)
        if not waiting:
            time.sleep(max(0.001, timeout_s))
            waiting = getattr(self._ser, "in_waiting", 0)
            if not waiting:
                return b""
        return bytes(self._ser.read(min(max_bytes, max(1, waiting))))

    def write_all(self, data: bytes, timeout_s: float) -> int:
        if not data:
            return 0
        self._ser.write_timeout = timeout_s
        try:
            written = self._ser.write(data)
            self._ser.flush()
            return int(written or 0)
        except Exception as e:
            raise UartError(f"Serial write failed: {e}") from e

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass


class _PosixTermiosTransport:
    """Linux 零依赖原生 POSIX `termios` 8N1 原始模式串口传输实现。"""

    name = "posix-termios"

    def __init__(self, port: str, baudrate: int) -> None:
        if termios is None or not hasattr(os, "O_NOCTTY"):
            raise UartError("POSIX termios backend is only supported on Unix/Linux")

        baud_const = _resolve_termios_baud(baudrate)
        flags = os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(port, flags)
        except OSError as e:
            raise UartError(f"Failed to open serial port {port!r}: {e}") from e

        try:
            _configure_termios_8n1_raw(fd, baud_const)
        except Exception as e:
            try:
                os.close(fd)
            except OSError:
                pass
            raise UartError(f"Failed to configure 8N1 baudrate {baudrate} on {port!r}: {e}") from e

        self._fd: int | None = fd

    def read_nonblocking(self, max_bytes: int, timeout_s: float) -> bytes:
        fd = self._fd
        if fd is None:
            return b""
        try:
            ready, _, _ = select.select([fd], [], [], max(0.0, timeout_s))
        except (OSError, ValueError) as e:
            raise UartError(f"select() failed on UART fd: {e}") from e

        if not ready:
            return b""

        try:
            chunk = os.read(fd, max_bytes)
        except BlockingIOError:
            return b""
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                return b""
            raise UartError(f"UART read error (errno={e.errno}): {e.strerror}") from e

        if chunk == b"":
            # EOF：短暂退避，防止对端挂断后 select 立即就绪引发空转
            time.sleep(min(0.02, max(0.005, timeout_s)))
        return chunk

    def write_all(self, data: bytes, timeout_s: float) -> int:
        if not data:
            return 0
        fd = self._fd
        if fd is None:
            raise UartError("UART port is closed")

        view = memoryview(data)
        total = 0
        deadline = time.monotonic() + max(0.05, timeout_s)

        while total < len(view):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise UartError(f"UART write timed out after {timeout_s}s ({total}/{len(view)} bytes sent)")
            try:
                _, writable, _ = select.select([], [fd], [], remaining)
            except (OSError, ValueError) as e:
                raise UartError(f"select() for UART write failed: {e}") from e
            if not writable:
                raise UartError(f"UART write timed out after {timeout_s}s ({total}/{len(view)} bytes sent)")
            try:
                n = os.write(fd, view[total:])
            except BlockingIOError:
                continue
            except OSError as e:
                if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                    continue
                raise UartError(f"UART write error (errno={e.errno}): {e.strerror}") from e
            if n <= 0:
                raise UartError("UART write returned 0 bytes")
            total += n
        return total

    def close(self) -> None:
        fd = self._fd
        self._fd = None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _resolve_termios_baud(baudrate: int) -> int:
    assert termios is not None
    if baudrate <= 0:
        raise UartError(f"Invalid baudrate: {baudrate}")
    attr = getattr(termios, f"B{baudrate}", None)
    if attr is None:
        supported = ", ".join(str(b) for b in STANDARD_BAUDRATES)
        raise UartError(f"Unsupported baudrate {baudrate} for POSIX termios (standard: {supported})")
    return int(attr)


def _configure_termios_8n1_raw(fd: int, baud_const: int) -> None:
    """配置 8N1 无流控原始模式（等价 cfmakeraw + CS8 + CLOCAL + CREAD，彻底关闭回显与换行转换）。"""
    assert termios is not None
    attrs = termios.tcgetattr(fd)
    iflag, oflag, cflag, lflag, _, _, cc = attrs

    iflag &= ~(
        termios.IGNBRK
        | termios.BRKINT
        | termios.PARMRK
        | termios.ISTRIP
        | termios.INLCR
        | termios.IGNCR
        | termios.ICRNL
        | termios.IXON
        | getattr(termios, "IXOFF", 0)
        | getattr(termios, "IXANY", 0)
    )
    oflag &= ~termios.OPOST
    lflag &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG | termios.IEXTEN)
    cflag &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB | getattr(termios, "CRTSCTS", 0))
    cflag |= termios.CS8 | termios.CREAD | termios.CLOCAL

    cc[termios.VMIN] = 0
    cc[termios.VTIME] = 0

    termios.tcsetattr(fd, termios.TCSANOW, [iflag, oflag, cflag, lflag, baud_const, baud_const, cc])


def _open_serial_transport(port: str, baudrate: int, backend: str = "auto") -> _SerialTransport:
    mode = backend.strip().lower()
    if mode not in ("auto", "pyserial", "posix", "termios", "posix-termios"):
        raise UartError(f"Unknown UART backend {backend!r} (expected 'auto', 'pyserial', or 'posix')")

    if mode == "pyserial":
        if serial is None:
            raise UartError("pyserial is not installed; use backend='auto' or 'posix'")
        return _PySerialTransport(port, baudrate)

    if mode in ("posix", "termios", "posix-termios"):
        return _PosixTermiosTransport(port, baudrate)

    # auto：优先使用 pyserial（若已安装），否则使用 Linux 原生 POSIX termios 后端
    if serial is not None:
        return _PySerialTransport(port, baudrate)
    return _PosixTermiosTransport(port, baudrate)


# ---------------------------------------------------------------------------
# UartLogCollector 主类
# ---------------------------------------------------------------------------


class UartLogCollector:
    """后台线程持续非阻塞读取串口日志并支持下行指令发送的采集器。"""

    def __init__(
        self,
        port: str | None = None,
        baudrate: int = 115200,
        *,
        max_lines: int = 2000,
        poll_interval_s: float = 0.02,
        backend: str = "auto",
    ) -> None:
        self.buffer = RingLogBuffer(max_lines=max_lines)
        self.poll_interval_s = poll_interval_s

        self._ctrl_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._transport: _SerialTransport | None = None

        self._port: str | None = port
        self._baudrate: int = baudrate
        self._backend_pref: str = backend
        self._active_backend: str | None = None
        self._bytes_received = 0
        self._bytes_sent = 0
        self._last_error: str | None = None

    def is_running(self) -> bool:
        with self._ctrl_lock:
            return self._thread is not None and self._thread.is_alive()

    def status(self) -> UartStatus:
        snap = self.buffer.read(tail_lines=1, include_partial=False)
        with self._ctrl_lock:
            running = self._thread is not None and self._thread.is_alive()
            return UartStatus(
                running=running,
                port=self._port,
                baudrate=self._baudrate,
                backend=self._active_backend if running else None,
                bytes_received=self._bytes_received,
                bytes_sent=self._bytes_sent,
                total_buffered=snap.total_buffered,
                oldest_seq=snap.oldest_seq,
                newest_seq=snap.newest_seq,
                last_error=self._last_error,
            )

    def _resolve_config(
        self,
        port: str | None,
        baudrate: int | None,
        backend: str | None,
    ) -> tuple[str, int, str]:
        with self._ctrl_lock:
            eff_port = port or self._port or os.environ.get("MCU_HARNESS_UART_PORT")
            if baudrate is not None:
                eff_baud = int(baudrate)
            elif self._baudrate:
                eff_baud = self._baudrate
            else:
                eff_baud = int(os.environ.get("MCU_HARNESS_UART_BAUD", "115200"))
            eff_backend = backend or self._backend_pref or "auto"

        if not eff_port:
            raise UartError(
                "No serial port specified (pass port='/dev/ttyACM0' or set MCU_HARNESS_UART_PORT)"
            )
        return eff_port, eff_baud, eff_backend

    def _start_or_reuse(
        self,
        *,
        port: str | None = None,
        baudrate: int | None = None,
        backend: str | None = None,
    ) -> tuple[UartStatus, bool]:
        eff_port, eff_baud, eff_backend = self._resolve_config(port, baudrate, backend)

        while True:
            old_thread: threading.Thread | None = None
            old_transport: _SerialTransport | None = None

            with self._ctrl_lock:
                if self._thread is not None and self._thread.is_alive():
                    same_backend = backend is None or eff_backend == self._backend_pref
                    if self._port == eff_port and self._baudrate == eff_baud and same_backend:
                        return self.status(), False
                    # 端口或波特率变更：先通知旧线程退出并摘除引用，锁外 join + 关闭旧句柄
                    old_thread = self._thread
                    old_transport = self._transport
                    self._thread = None
                    self._transport = None
                    self._stop_event.set()
                else:
                    transport = _open_serial_transport(eff_port, eff_baud, backend=eff_backend)
                    self._port = eff_port
                    self._baudrate = eff_baud
                    self._backend_pref = eff_backend
                    self._active_backend = transport.name
                    self._transport = transport
                    self._last_error = None

                    stop_event = threading.Event()
                    self._stop_event = stop_event
                    self._thread = threading.Thread(
                        target=self._poll_loop,
                        args=(stop_event, transport),
                        name=f"uart-collector-{Path(eff_port).name}",
                        daemon=True,
                    )
                    self._thread.start()
                    return self.status(), True

            if old_thread is not None and old_thread is not threading.current_thread():
                old_thread.join(timeout=1.5)
            if old_transport is not None:
                with self._write_lock:
                    old_transport.close()

    def start(
        self,
        port: str | None = None,
        baudrate: int | None = None,
        *,
        backend: str | None = None,
    ) -> UartStatus:
        """启动后台串口采集线程；若已在相同端口和波特率下运行则直接返回当前状态。"""
        st, _ = self._start_or_reuse(port=port, baudrate=baudrate, backend=backend)
        return st

    def stop(self) -> UartStatus:
        """干净停止后台采集线程（锁外等待退出）并关闭串口文件描述符，零句柄泄漏。"""
        with self._ctrl_lock:
            thread = self._thread
            transport = self._transport
            self._thread = None
            self._transport = None
            self._active_backend = None
            self._stop_event.set()

        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.5)

        if transport is not None:
            with self._write_lock:
                transport.close()

        return self.status()

    def clear(self) -> int:
        """清空环形日志缓冲与未换行残余字节。"""
        return self.buffer.clear()

    def write(
        self,
        data: bytes | str,
        *,
        encoding: str = "utf-8",
        timeout_s: float = 1.0,
        auto_start: bool = True,
    ) -> int:
        """线程安全地向串口发送下行命令或字节流，返回实际写入字节数。"""
        payload = data.encode(encoding) if isinstance(data, str) else bytes(data)
        if not payload:
            return 0

        with self._ctrl_lock:
            transport = self._transport

        if transport is None:
            if not auto_start:
                raise UartError("UART collector is not running (call start() first)")
            self._start_or_reuse()
            with self._ctrl_lock:
                transport = self._transport
            if transport is None:
                raise UartError("Failed to open UART transport for write()")

        with self._write_lock:
            sent = transport.write_all(payload, timeout_s=timeout_s)

        with self._ctrl_lock:
            self._bytes_sent += sent
        return sent

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
        auto_start: bool = True,
        port: str | None = None,
        baudrate: int | None = None,
        backend: str | None = None,
    ) -> tuple[LogReadResult, UartStatus]:
        """读取最新串口日志（若尚未启动且 `auto_start=True`，自动打开串口并抓取首批到达数据）。"""
        if auto_start:
            _, newly_started = self._start_or_reuse(port=port, baudrate=baudrate, backend=backend)
            if newly_started:
                time.sleep(0.08)

        result = self.buffer.read(
            tail_lines=tail_lines,
            since_seq=since_seq,
            pattern=pattern,
            regex=regex,
            case_sensitive=case_sensitive,
            context_lines=context_lines,
            include_partial=include_partial,
        )
        return result, self.status()

    def _poll_loop(self, stop_event: threading.Event, transport: _SerialTransport) -> None:
        while not stop_event.is_set():
            try:
                chunk = transport.read_nonblocking(max_bytes=4096, timeout_s=self.poll_interval_s)
                if chunk and not stop_event.is_set():
                    self.buffer.feed_bytes(chunk)
                    with self._ctrl_lock:
                        self._bytes_received += len(chunk)
                        self._last_error = None
            except UartError as e:
                with self._ctrl_lock:
                    self._last_error = str(e)
                stop_event.wait(0.05)
            except Exception as e:
                with self._ctrl_lock:
                    self._last_error = f"{type(e).__name__}: {e}"
                stop_event.wait(0.05)


default_uart_collector = UartLogCollector()
