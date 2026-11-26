"""OpenOCD 调试探针后端（兼容 ST-Link、CMSIS-DAP/DAPLink、J-Link 及通用 Cortex-M 目标）。

原理与优势：
    * 通过 OpenOCD 原生 TCL RPC 协议（默认 127.0.0.1:6666，帧结束符 0x1A）通信，
      纯标准库 socket 实现，零外部 Python 依赖，返回结构清晰且无需解析 GDB 文本。
    * 既可附着到工程师已手动启动的 OpenOCD 进程，也可由 Harness 自动拉起托管后台子进程。
    * 连接时只执行 `init`（不触发 `reset`），与 JLinkProbe 保持相同的非侵入现场保护约定。
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from mcu_harness.config.manifest import infer_mcu_traits

from .base import DebugProbe, FlashResult, ProbeError, ProbeInfo, ResetResult
from .jlink import normalize_device, normalize_interface, normalize_speed

_TCL_END = b"\x1a"
_RE_HEX_OR_INT = re.compile(r"0x[0-9a-fA-F]+|\d+")
_RE_REG_VAL = re.compile(r":\s*(0x[0-9a-fA-F]+)\b")

_DEFAULT_OPENOCD_REGS = (
    "r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7",
    "r8", "r9", "r10", "r11", "r12", "sp", "lr", "pc",
    "xpsr", "msp", "psp", "primask", "basepri", "faultmask", "control",
)


def _resolve_openocd_cfg(raw: str, category: str) -> str:
    """将简写名（如 'cmsis-dap' / 'stm32h5x'）规范为 OpenOCD 脚本路径（'interface/cmsis-dap.cfg'）。"""
    s = raw.strip()
    if s.endswith(".cfg") or "/" in s or "\\" in s:
        return s
    return f"{category}/{s}.cfg"


class OpenOCDProbe(DebugProbe):
    """基于 OpenOCD TCL RPC 的通用调试探针实现。"""

    def __init__(
        self,
        *,
        device: str | None = None,
        interface: str | None = None,
        speed: int | str | None = None,
        serial_no: str | int | None = None,
        openocd_interface: str | None = None,
        openocd_target: str | None = None,
        host: str = "127.0.0.1",
        tcl_port: int | None = None,
        openocd_bin: str | None = None,
        spawn_server: bool = True,
        timeout_s: float = 5.0,
    ) -> None:
        self.device = normalize_device(device)
        self.interface = normalize_interface(interface)
        self.speed = normalize_speed(speed)
        self.serial_no = str(serial_no).strip() if serial_no not in (None, "") else None
        self.openocd_interface = (
            openocd_interface or os.environ.get("MCU_HARNESS_OPENOCD_INTERFACE") or "cmsis-dap"
        ).strip()
        traits = infer_mcu_traits(self.device)
        self.openocd_target = (
            openocd_target
            or os.environ.get("MCU_HARNESS_OPENOCD_TARGET")
            or traits.get("openocd_target")
            or "stm32f4x"
        )
        self.host = host
        self.tcl_port = int(tcl_port if tcl_port is not None else (os.environ.get("MCU_HARNESS_OPENOCD_PORT") or 6666))
        self.openocd_bin = openocd_bin or os.environ.get("MCU_HARNESS_OPENOCD_BIN")
        self.spawn_server = spawn_server
        self.timeout_s = timeout_s

        self._sock: socket.socket | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._daemon_log_tail: deque[str] = deque(maxlen=40)

    # ---- TCL RPC 底层通信 ------------------------------------------------

    def _tcl_cmd(self, command: str) -> str:
        """发送一条 TCL 命令并返回去尾后的文本结果。"""
        if self._sock is None:
            raise ProbeError("OpenOCD is not connected")
        payload = command.encode("utf-8") + _TCL_END
        try:
            self._sock.sendall(payload)
            chunks = bytearray()
            while True:
                data = self._sock.recv(4096)
                if not data:
                    raise ProbeError("OpenOCD closed TCL RPC connection unexpectedly")
                if _TCL_END in data:
                    idx = data.index(_TCL_END)
                    chunks.extend(data[:idx])
                    break
                chunks.extend(data)
        except socket.timeout as e:
            raise ProbeError(f"OpenOCD command timed out ({self.timeout_s}s): {command!r}") from e
        except OSError as e:
            raise ProbeError(f"OpenOCD socket error on {command!r}: {e}") from e

        resp = chunks.decode("utf-8", errors="replace").strip()
        return resp

    def _try_connect_socket(self) -> socket.socket | None:
        try:
            sock = socket.create_connection((self.host, self.tcl_port), timeout=0.6)
            sock.settimeout(self.timeout_s)
            return sock
        except OSError:
            return None

    def _start_openocd_daemon(self) -> subprocess.Popen[bytes]:
        exe = self.openocd_bin or shutil.which("openocd")
        if not exe:
            # 额外探测 ~/.local/bin/openocd
            local_cand = Path.home() / ".local" / "bin" / "openocd"
            if local_cand.is_file():
                exe = str(local_cand)
        if not exe:
            raise ProbeError("openocd binary not found in PATH (install OpenOCD or set MCU_HARNESS_OPENOCD_BIN)")

        if_cfg = _resolve_openocd_cfg(self.openocd_interface, "interface")
        tgt_cfg = _resolve_openocd_cfg(self.openocd_target, "target")
        cmd = [
            exe,
            "-c", f"tcl_port {self.tcl_port}",
            "-c", "gdb_port disabled",
            "-c", "telnet_port disabled",
            "-f", if_cfg,
            "-c", f"transport select {self.interface.lower()}",
        ]
        if self.serial_no:
            cmd.extend(["-c", f"adapter serial {self.serial_no}"])
        if isinstance(self.speed, int):
            cmd.extend(["-c", f"adapter speed {self.speed}"])
        cmd.extend(["-f", tgt_cfg, "-c", "init"])

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except OSError as e:
            raise ProbeError(f"Failed to start OpenOCD ({cmd[0]}): {e}") from e

        self._daemon_log_tail.clear()

        def _drain_stdout() -> None:
            if proc.stdout is None:
                return
            try:
                for raw_line in proc.stdout:
                    self._daemon_log_tail.append(raw_line.decode("utf-8", errors="replace").rstrip())
            except Exception:
                pass

        threading.Thread(target=_drain_stdout, name="openocd-stdout-drain", daemon=True).start()
        return proc

    # ---- DebugProbe 标准接口实现 -----------------------------------------

    def connect(self) -> ProbeInfo:
        if self.is_connected():
            return self.info()

        sock = self._try_connect_socket()
        if sock is None:
            if not self.spawn_server:
                raise ProbeError(f"Could not connect to OpenOCD TCL server at {self.host}:{self.tcl_port}")
            proc = self._start_openocd_daemon()
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    time.sleep(0.02)
                    out = "\n".join(self._daemon_log_tail)
                    raise ProbeError(f"OpenOCD exited early (rc={proc.returncode}): {out.strip()[-400:]}")
                sock = self._try_connect_socket()
                if sock is not None:
                    break
                time.sleep(0.05)
            if sock is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except Exception:
                    proc.kill()
                raise ProbeError(f"Timed out waiting for OpenOCD TCL port {self.tcl_port}")
            self._proc = proc

        self._sock = sock
        return self.info()

    def close(self) -> None:
        if self._sock is not None:
            try:
                if self._proc is not None:
                    try:
                        self._tcl_cmd("shutdown")
                    except Exception:
                        pass
                self._sock.close()
            except Exception:
                pass
            finally:
                self._sock = None

        if self._proc is not None:
            proc = self._proc
            self._proc = None
            try:
                proc.terminate()
                proc.wait(timeout=1.5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def is_connected(self) -> bool:
        return self._sock is not None and (self._proc is None or self._proc.poll() is None)

    def info(self) -> ProbeInfo:
        self._require_connected()
        halted: bool | None = None
        try:
            halted = self.is_halted()
        except Exception:
            halted = None

        core_id: str | None = None
        try:
            cpuid = self.read_u32(0xE000ED00, 1)[0]
            core_id = f"0x{cpuid:08X}"
        except Exception:
            core_id = None

        return ProbeInfo(
            backend="openocd",
            device=self.device,
            interface=self.interface,
            connected=True,
            halted=halted,
            serial_no=self.serial_no,
            core_id=core_id,
            core_name=f"OpenOCD ({self.openocd_target})",
            speed_khz=self.speed if isinstance(self.speed, int) else None,
        )

    def _read_mem_width(self, address: int, width: int, count: int) -> list[int]:
        self._require_connected()
        if count <= 0:
            raise ProbeError(f"count must be > 0, got {count}")
        step = width // 8
        if step > 1 and address % step != 0:
            raise ProbeError(f"Unaligned {width}-bit read at 0x{address:08X}")
        resp = self._tcl_cmd(f"read_memory 0x{address:08X} {width} {count}")
        tokens = _RE_HEX_OR_INT.findall(resp)
        if len(tokens) < count:
            raise ProbeError(f"OpenOCD read_memory failed at 0x{address:08X}: {resp or 'empty response'}")
        mask = (1 << width) - 1
        return [int(tok, 0) & mask for tok in tokens[:count]]

    def read_u32(self, address: int, count: int = 1) -> list[int]:
        return self._read_mem_width(address, 32, count)

    def read_u16(self, address: int, count: int = 1) -> list[int]:
        return self._read_mem_width(address, 16, count)

    def read_u8(self, address: int, count: int = 1) -> list[int]:
        return self._read_mem_width(address, 8, count)

    def _write_mem_width(self, address: int, width: int, values: list[int]) -> None:
        self._require_connected()
        if not values:
            return
        step = width // 8
        if step > 1 and address % step != 0:
            raise ProbeError(f"Unaligned {width}-bit write at 0x{address:08X}")
        mask = (1 << width) - 1
        tcl_list = " ".join(f"0x{(int(v) & mask):X}" for v in values)
        resp = self._tcl_cmd(f"write_memory 0x{address:08X} {width} {{{tcl_list}}}")
        if resp and ("error" in resp.lower() or "failed" in resp.lower()):
            raise ProbeError(f"OpenOCD write_memory failed at 0x{address:08X}: {resp}")

    def write_u32(self, address: int, values: list[int]) -> None:
        self._write_mem_width(address, 32, values)

    def write_u16(self, address: int, values: list[int]) -> None:
        self._write_mem_width(address, 16, values)

    def write_u8(self, address: int, values: list[int]) -> None:
        self._write_mem_width(address, 8, values)

    def is_halted(self) -> bool:
        self._require_connected()
        state = self._tcl_cmd("[target current] curstate").strip().lower()
        return state == "halted"

    def halt(self) -> bool:
        self._require_connected()
        was_halted = self.is_halted()
        self._tcl_cmd("halt 500")
        return (not was_halted) and self.is_halted()

    def resume(self) -> bool:
        self._require_connected()
        was_halted = self.is_halted()
        if not was_halted:
            return False
        self._tcl_cmd("resume")
        return True

    def read_core_regs(self, names: list[str] | None = None) -> dict[str, int]:
        self._require_connected()
        wanted = [n.strip().lower() for n in names] if names else list(_DEFAULT_OPENOCD_REGS)
        out: dict[str, int] = {}
        for rname in wanted:
            resp = self._tcl_cmd(f"capture {{reg {rname}}}")
            m = _RE_REG_VAL.search(resp)
            if m:
                out[rname] = int(m.group(1), 16) & 0xFFFFFFFF
            elif names is not None:
                raise ProbeError(f"OpenOCD failed to read register {rname!r}: {resp}")
        return out

    def reset(self, *, halt: bool = False) -> ResetResult:
        self._require_connected()
        mode = "halt" if halt else "run"
        resp = self._tcl_cmd(f"capture {{reset {mode}}}")
        if "error" in resp.lower() or "failed" in resp.lower():
            raise ProbeError(f"OpenOCD reset {mode} failed: {resp}")
        halted_now = self.is_halted()
        pc_str = None
        sp_str = None
        if halted_now:
            regs = self.read_core_regs(["pc", "sp"])
            pc_str = f"0x{regs['pc']:08X}" if "pc" in regs else None
            sp_str = f"0x{regs['sp']:08X}" if "sp" in regs else None
        return ResetResult(
            halted=halted_now,
            pc=pc_str,
            sp=sp_str,
            message=f"OpenOCD reset {mode} complete",
        )

    def flash(
        self,
        firmware_path: str | os.PathLike,
        *,
        address: int | None = None,
        verify: bool = True,
        reset_after: bool = True,
        run_after: bool = True,
    ) -> FlashResult:
        self._require_connected()
        t0 = time.monotonic()
        p = Path(firmware_path).expanduser().resolve()
        if not p.is_file():
            raise ProbeError(f"Firmware file not found: {p}")
        if p.suffix.lower() == ".bin" and address is None:
            raise ProbeError("Flashing a raw .bin file requires specifying a base address (e.g. 0x08000000)")

        escaped_path = p.as_posix()
        parts = [f"flash write_image erase {{{escaped_path}}}"]
        if address is not None:
            parts[0] += f" 0x{address:08X}"
        self._tcl_cmd("halt 500")
        w_resp = self._tcl_cmd(f"capture {{{parts[0]}}}")
        if "error" in w_resp.lower() or "failed" in w_resp.lower():
            raise ProbeError(f"OpenOCD flash failed: {w_resp.strip()}")

        if verify:
            v_cmd = f"verify_image {{{escaped_path}}}" + (f" 0x{address:08X}" if address is not None else "")
            v_resp = self._tcl_cmd(f"capture {{{v_cmd}}}")
            if "error" in v_resp.lower() or "checksum mismatch" in v_resp.lower():
                raise ProbeError(f"OpenOCD flash verify failed: {v_resp.strip()}")

        if reset_after:
            self.reset(halt=not run_after)
        elif run_after:
            self.resume()

        halted_now = self.is_halted()
        return FlashResult(
            success=True,
            firmware_path=str(p),
            bytes_written=p.stat().st_size,
            address=f"0x{address:08X}" if address is not None else "auto(.elf/.hex)",
            verified=verify,
            reset_performed=reset_after,
            running=not halted_now,
            duration_s=time.monotonic() - t0,
            message="OpenOCD flash succeeded",
        )

    def _require_connected(self) -> None:
        if not self.is_connected():
            raise ProbeError("OpenOCD is not connected (call connect() first)")
