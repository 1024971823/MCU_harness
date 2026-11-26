"""OpenOCD 真实本地 TCP TCL RPC 协议端到端集成测试、JLinkProbe 扩展能力、ProbeManager 多后端切换与 CLI 安全护栏测试。

同时兼容 `pytest` 收集与 `PYTHONPATH=src python3 tests/test_openocd_and_flash.py` 直接运行。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mcu_harness.probe import (
    JLinkProbe,
    MockProbe,
    OpenOCDProbe,
    ProbeError,
    ProbeManager,
)
from mcu_harness.probe.cli import main as probe_cli_main

_TCL_END = b"\x1a"


class FakeOpenOCDTclServer:
    """监听 127.0.0.1:0 随机空闲端口的真实多线程 TCP Socket OpenOCD TCL RPC 模拟服务器。"""

    def __init__(self) -> None:
        self._srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv_sock.bind(("127.0.0.1", 0))
        self._srv_sock.listen(8)
        self._srv_sock.settimeout(0.2)
        self.port: int = self._srv_sock.getsockname()[1]

        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._client_threads: list[threading.Thread] = []
        self._accept_thread = threading.Thread(target=self._accept_loop, name="fake-openocd-accept", daemon=True)

        self.commands: list[str] = []
        self.flashed_commands: list[str] = []
        self.verified_commands: list[str] = []
        self.fail_next_verify: bool = False
        self.fail_next_flash: bool = False

        self.curstate: str = "running"
        self.mem: dict[int, int] = {}
        self.regs: dict[str, int] = {
            "r0": 0x00000001,
            "r1": 0x00000002,
            "r2": 0x00000003,
            "r3": 0x00000004,
            "r4": 0x00000005,
            "r5": 0x00000006,
            "r6": 0x00000007,
            "r7": 0x00000008,
            "r8": 0x00000009,
            "r9": 0x0000000A,
            "r10": 0x0000000B,
            "r11": 0x0000000C,
            "r12": 0x0000000D,
            "sp": 0x20020000,
            "lr": 0x08000101,
            "pc": 0x08000200,
            "xpsr": 0x01000000,
            "msp": 0x20020000,
            "psp": 0x20018000,
            "primask": 0,
            "basepri": 0,
            "faultmask": 0,
            "control": 0,
        }
        # 预置 Cortex-M33 CPUID (0xE000ED00 = 0x410FD210)
        self._put_u32(0xE000ED00, 0x410FD210)
        self._put_u32(0x20000000, 0xAABBCCDD)

    def _put_u32(self, addr: int, val: int) -> None:
        for idx, b in enumerate((val & 0xFFFFFFFF).to_bytes(4, "little")):
            self.mem[addr + idx] = b

    def start(self) -> FakeOpenOCDTclServer:
        self._accept_thread.start()
        return self

    def stop(self) -> None:
        self._stop_event.set()
        try:
            self._srv_sock.close()
        except OSError:
            pass
        self._accept_thread.join(timeout=2.0)
        for t in self._client_threads:
            t.join(timeout=1.0)

    def __enter__(self) -> FakeOpenOCDTclServer:
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    def _accept_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                conn, _ = self._srv_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(target=self._handle_client, args=(conn,), daemon=True)
            self._client_threads.append(t)
            t.start()

    def _handle_client(self, conn: socket.socket) -> None:
        conn.settimeout(0.5)
        buf = bytearray()
        try:
            while not self._stop_event.is_set():
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buf.extend(chunk)
                while _TCL_END in buf:
                    idx = buf.index(_TCL_END)
                    raw_cmd = bytes(buf[:idx]).decode("utf-8", errors="replace").strip()
                    del buf[: idx + 1]
                    resp = self._dispatch_tcl(raw_cmd)
                    conn.sendall(resp.encode("utf-8") + _TCL_END)
                    if raw_cmd == "shutdown":
                        return
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _dispatch_tcl(self, cmd: str) -> str:
        with self._lock:
            self.commands.append(cmd)

            if cmd == "[target current] curstate":
                return self.curstate

            if cmd.startswith("halt"):
                self.curstate = "halted"
                return ""

            if cmd == "resume":
                self.curstate = "running"
                return ""

            m_read = re.match(r"^read_memory\s+(0x[0-9a-fA-F]+|\d+)\s+(8|16|32)\s+(\d+)$", cmd)
            if m_read:
                addr = int(m_read.group(1), 0)
                width = int(m_read.group(2), 10)
                count = int(m_read.group(3), 10)
                step = width // 8
                vals: list[str] = []
                for i in range(count):
                    base = addr + i * step
                    raw = bytes(self.mem.get(base + k, 0) for k in range(step))
                    vals.append(f"0x{int.from_bytes(raw, 'little'):x}")
                return " ".join(vals)

            m_write = re.match(r"^write_memory\s+(0x[0-9a-fA-F]+|\d+)\s+(8|16|32)\s+\{([^}]*)\}$", cmd)
            if m_write:
                addr = int(m_write.group(1), 0)
                width = int(m_write.group(2), 10)
                step = width // 8
                tokens = m_write.group(3).split()
                for i, tok in enumerate(tokens):
                    v = int(tok, 0) & ((1 << width) - 1)
                    raw = v.to_bytes(step, "little")
                    for k, b in enumerate(raw):
                        self.mem[addr + i * step + k] = b
                return ""

            m_reg = re.match(r"^capture\s+\{reg\s+([a-zA-Z0-9_]+)\}$", cmd)
            if m_reg:
                rname = m_reg.group(1).lower()
                if rname in self.regs:
                    return f"{rname} (/32): 0x{self.regs[rname]:08X}\n"
                return f"Register {rname} not found"

            m_reset = re.match(r"^capture\s+\{reset\s+(halt|run)\}$", cmd)
            if m_reset:
                mode = m_reset.group(1)
                if mode == "halt":
                    self.curstate = "halted"
                    self.regs["pc"] = 0x08000180
                    self.regs["sp"] = 0x20020000
                else:
                    self.curstate = "running"
                return ""

            if cmd.startswith("capture {flash write_image erase"):
                self.flashed_commands.append(cmd)
                if self.fail_next_flash:
                    self.fail_next_flash = False
                    return "Error: flash write failed"
                return "wrote 1024 bytes from file in 0.02s"

            if cmd.startswith("capture {verify_image"):
                self.verified_commands.append(cmd)
                if self.fail_next_verify:
                    self.fail_next_verify = False
                    return "Error: checksum mismatch - attempting binary compare"
                return "verified 1024 bytes in 0.01s"

            if cmd == "shutdown":
                return ""

            return f"Error: unknown command {cmd}"


def _build_minimal_arm_elf(entry_addr: int = 0x08000181) -> bytes:
    """构造最小合法 32-bit 小端 ARM ELF 头（52 字节），方便真实文件烧录测试。"""
    e_ident = b"\x7fELF\x01\x01\x01" + b"\x00" * 9
    return e_ident + struct.pack("<HHIIIIIHHHHHH", 2, 40, 1, entry_addr, 0, 0, 0x05000000, 52, 0, 0, 0, 0, 0)


class TestOpenOCDProbeRealSocket(unittest.TestCase):
    """通过真实本地 TCP Socket 验证 OpenOCDProbe 全链路 TCL RPC 协议交互。"""

    def test_openocd_end_to_end_over_real_tcp_socket(self) -> None:
        with FakeOpenOCDTclServer() as srv, tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            elf_file = root / "app.elf"
            elf_file.write_bytes(_build_minimal_arm_elf())
            bin_file = root / "app.bin"
            bin_file.write_bytes(b"\x00\x00\x02\x20\x81\x01\x00\x08" + b"\xAA" * 56)

            with OpenOCDProbe(
                device="STM32H563ZI",
                interface="SWD",
                speed=4000,
                host="127.0.0.1",
                tcl_port=srv.port,
                spawn_server=False,
            ) as probe:
                # 1. connect() 读取 curstate 与 0xE000ED00 CPUID，且绝不触发 reset
                info = probe.info()
                self.assertTrue(info.connected)
                self.assertEqual(info.backend, "openocd")
                self.assertEqual(info.device, "STM32H563ZI")
                self.assertEqual(info.core_id, "0x410FD210")
                self.assertEqual(info.core_name, "OpenOCD (stm32h5x)")
                self.assertEqual(info.speed_khz, 4000)
                self.assertFalse(info.halted)
                self.assertFalse(any("reset" in c for c in srv.commands))

                # 2. 32 / 16 / 8 位读写与小端序一致性 + 对齐校验
                probe.write_u32(0x20000100, [0xCAFEBABE, 0x01020304])
                self.assertEqual(probe.read_u32(0x20000100, 2), [0xCAFEBABE, 0x01020304])
                self.assertEqual(probe.read_u16(0x20000100, 2), [0xBABE, 0xCAFE])
                self.assertEqual(probe.read_u8(0x20000104, 4), [0x04, 0x03, 0x02, 0x01])

                probe.write_u16(0x20000200, [0x1234, 0xABCD])
                self.assertEqual(probe.read_u32(0x20000200, 1), [0xABCD1234])

                probe.write_u8(0x20000300, [0xEF, 0xBE, 0xAD, 0xDE])
                self.assertEqual(probe.read_u32(0x20000300, 1), [0xDEADBEEF])
                self.assertEqual(probe.read_memory(0x20000300, 4), b"\xef\xbe\xad\xde")

                with self.assertRaisesRegex(ProbeError, "Unaligned 32-bit read"):
                    probe.read_u32(0x20000102)
                with self.assertRaisesRegex(ProbeError, "Unaligned 16-bit write"):
                    probe.write_u16(0x20000101, [0x1234])

                # 3. halt() 与 resume() 状态转换
                self.assertTrue(probe.halt())
                self.assertTrue(probe.is_halted())
                self.assertFalse(probe.halt())  # 已暂停时再次 halt 返回 False

                # 4. read_core_regs() 读取全量与指定核心寄存器
                all_regs = probe.read_core_regs()
                self.assertEqual(all_regs["pc"], 0x08000200)
                self.assertEqual(all_regs["sp"], 0x20020000)
                self.assertEqual(all_regs["lr"], 0x08000101)
                sub_regs = probe.read_core_regs(["pc", "r0"])
                self.assertEqual(sub_regs, {"pc": 0x08000200, "r0": 0x00000001})
                with self.assertRaisesRegex(ProbeError, "failed to read register"):
                    probe.read_core_regs(["bad_reg_xyz"])

                self.assertTrue(probe.resume())
                self.assertFalse(probe.is_halted())
                self.assertFalse(probe.resume())  # 已运行时再次 resume 返回 False

                # 5. reset(halt=True) 停在复位向量并返回 PC/SP
                rst = probe.reset(halt=True)
                self.assertTrue(rst.halted)
                self.assertEqual(rst.pc, "0x08000180")
                self.assertEqual(rst.sp, "0x20020000")

                # 6. flash()：.bin 缺少基地址报错；.elf 正常走 flash write_image erase + verify_image + reset run
                with self.assertRaisesRegex(ProbeError, "requires specifying a base address"):
                    probe.flash(bin_file)

                elf_res = probe.flash(elf_file, verify=True, reset_after=True, run_after=True)
                self.assertTrue(elf_res.success)
                self.assertEqual(elf_res.bytes_written, elf_file.stat().st_size)
                self.assertEqual(elf_res.address, "auto(.elf/.hex)")
                self.assertTrue(elf_res.verified)
                self.assertTrue(elf_res.reset_performed)
                self.assertTrue(elf_res.running)
                self.assertEqual(len(srv.flashed_commands), 1)
                self.assertIn(elf_file.resolve().as_posix(), srv.flashed_commands[0])
                self.assertEqual(len(srv.verified_commands), 1)
                self.assertIn("capture {reset run}", srv.commands)

                # .bin 显式传入基地址可成功烧录，且校验失败能抛出 ProbeError
                bin_res = probe.flash(bin_file, address=0x08000000, verify=True, reset_after=True, run_after=False)
                self.assertTrue(bin_res.success)
                self.assertEqual(bin_res.address, "0x08000000")
                self.assertFalse(bin_res.running)
                self.assertIn("0x08000000", srv.flashed_commands[-1])

                srv.fail_next_verify = True
                with self.assertRaisesRegex(ProbeError, "verify failed"):
                    probe.flash(elf_file, verify=True)

            self.assertFalse(probe.is_connected())

    def test_openocd_connect_unreachable_without_spawn_raises(self) -> None:
        # 获取一个已关闭监听的空闲端口号
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            closed_port = s.getsockname()[1]

        probe = OpenOCDProbe(host="127.0.0.1", tcl_port=closed_port, spawn_server=False)
        with self.assertRaisesRegex(ProbeError, "Could not connect to OpenOCD TCL server"):
            probe.connect()


class TestJLinkProbeExtendedCapabilities(unittest.TestCase):
    """使用 FakeJLink 桩对象验证 JLinkProbe 的写内存、核心寄存器别名映射、复位、烧录与 RTT 下行写入。"""

    def test_jlink_write_regs_reset_flash_and_rtt_write(self) -> None:
        events: list[tuple] = []
        mem32: dict[int, int] = {}
        rtt_down: bytearray = bytearray()

        reg_table = [
            (0, "R0", 0x00000010),
            (13, "R13 (SP)", 0x20020000),
            (14, "R14", 0x08000101),
            (15, "R15 (PC)", 0x08000240),
            (16, "XPSR", 0x01000000),
            (17, "MSP_NS", 0x20020000),
            (18, "MSPLIM_NS", 0x20000000),
        ]

        class FakeJLink:
            serial_number = 87654321
            speed = 4000

            def __init__(self, lib=None) -> None:
                self._open = False
                self._conn = False
                self._halted = False

            def open(self, serial_no=None) -> None:
                self._open = True
                events.append(("open", serial_no))

            def set_tif(self, tif) -> None:
                events.append(("set_tif", tif))

            def connect(self, chip_name, speed="auto") -> None:
                self._conn = True
                events.append(("connect", chip_name, speed))

            def opened(self) -> bool:
                return self._open

            def connected(self) -> bool:
                return self._conn

            def core_id(self) -> int:
                return 0x410FD210

            def core_name(self) -> str:
                return "Cortex-M33"

            def halted(self) -> bool:
                return self._halted

            def halt(self) -> bool:
                self._halted = True
                events.append(("halt",))
                return True

            def go(self) -> None:
                self._halted = False
                events.append(("go",))

            def memory_write32(self, addr: int, vals: list[int]) -> None:
                events.append(("write32", addr, list(vals)))
                for i, v in enumerate(vals):
                    mem32[addr + i * 4] = v

            def memory_read32(self, addr: int, count: int) -> list[int]:
                return [mem32.get(addr + i * 4, 0) for i in range(count)]

            def memory_write16(self, addr: int, vals: list[int]) -> None:
                events.append(("write16", addr, list(vals)))

            def memory_write8(self, addr: int, vals: list[int]) -> None:
                events.append(("write8", addr, list(vals)))

            def register_list(self) -> list[int]:
                return [idx for idx, _, _ in reg_table]

            def register_name(self, reg_idx: int) -> str:
                return next(name for idx, name, _ in reg_table if idx == reg_idx)

            def register_read(self, reg_idx: int) -> int:
                return next(val for idx, _, val in reg_table if idx == reg_idx)

            def reset(self, ms: int = 0, halt: bool = False) -> None:
                events.append(("reset", ms, halt))
                self._halted = True  # 模拟 J-Link reset 后默认停住，halt=False 时由 JLinkProbe 调 go()

            def flash_file(self, path: str, addr: int = 0) -> int:
                events.append(("flash_file", path, addr))
                return Path(path).stat().st_size

            def rtt_start(self, block_address=None) -> None:
                events.append(("rtt_start", block_address))

            def rtt_stop(self) -> None:
                events.append(("rtt_stop",))

            def rtt_read(self, channel: int, max_bytes: int) -> list[int]:
                return list(b"boot ok\n"[:max_bytes])

            def rtt_write(self, channel: int, data: list[int]) -> int:
                events.append(("rtt_write", channel, bytes(data)))
                rtt_down.extend(data)
                return len(data)

            def close(self) -> None:
                self._open = False
                self._conn = False
                events.append(("close",))

        fake_pylink = SimpleNamespace(
            JLink=FakeJLink,
            Library=lambda dllpath=None: object(),
            enums=SimpleNamespace(JLinkInterfaces=SimpleNamespace(SWD=1, JTAG=0)),
        )

        with tempfile.TemporaryDirectory() as tmp, patch(
            "mcu_harness.probe.jlink._load_pylink_library",
            return_value=(fake_pylink, object()),
        ):
            elf_path = Path(tmp) / "fw.elf"
            elf_path.write_bytes(_build_minimal_arm_elf())
            bin_path = Path(tmp) / "fw.bin"
            bin_path.write_bytes(b"\x11\x22\x33\x44" * 8)

            with JLinkProbe(device="STM32H563ZI", interface="SWD") as probe:
                # 1. write_u32 / u16 / u8 及对齐检查
                probe.write_u32(0x20000100, [0x12345678, 0x9ABCDEF0])
                self.assertEqual(probe.read_u32(0x20000100, 2), [0x12345678, 0x9ABCDEF0])
                probe.write_u16(0x20000200, [0xBEEF])
                probe.write_u8(0x20000300, [0x42])
                with self.assertRaisesRegex(ProbeError, "Unaligned 32-bit write"):
                    probe.write_u32(0x20000102, [1])

                # 2. read_core_regs：验证 R13 (SP) / R15 (PC) / MSP_NS / MSPLIM_NS 等别名归一化
                regs = probe.read_core_regs(["pc", "sp", "lr", "msp", "msplim"])
                self.assertEqual(regs["pc"], 0x08000240)
                self.assertEqual(regs["sp"], 0x20020000)
                self.assertEqual(regs["lr"], 0x08000101)
                self.assertEqual(regs["msp"], 0x20020000)
                self.assertEqual(regs["msplim"], 0x20000000)
                with self.assertRaisesRegex(ProbeError, "not supported"):
                    probe.read_core_regs(["nonexistent_reg"])

                # 3. reset(halt=True) 与 reset(halt=False)
                rst_halt = probe.reset(halt=True)
                self.assertTrue(rst_halt.halted)
                self.assertEqual(rst_halt.pc, "0x08000240")
                self.assertEqual(rst_halt.sp, "0x20020000")

                rst_run = probe.reset(halt=False)
                self.assertFalse(rst_run.halted)
                self.assertIsNone(rst_run.pc)

                # 4. flash()：.bin 缺地址拦截、.elf 自动地址烧录并运行、.bin 指定地址烧录
                with self.assertRaisesRegex(ProbeError, "requires specifying a base address"):
                    probe.flash(bin_path)

                f_elf = probe.flash(elf_path, reset_after=True, run_after=True)
                self.assertTrue(f_elf.success)
                self.assertEqual(f_elf.bytes_written, elf_path.stat().st_size)
                self.assertEqual(f_elf.address, "auto(.elf/.hex)")
                self.assertTrue(f_elf.running)

                f_bin = probe.flash(bin_path, address=0x08000000, reset_after=True, run_after=False)
                self.assertTrue(f_bin.success)
                self.assertEqual(f_bin.address, "0x08000000")
                self.assertFalse(f_bin.running)

                # 5. RTT 双向读写
                probe.rtt_start()
                self.assertEqual(probe.rtt_write(b"", channel=0), 0)
                written_n = probe.rtt_write(b"status\n", channel=0)
                self.assertEqual(written_n, 7)
                self.assertEqual(bytes(rtt_down), b"status\n")
                self.assertEqual(probe.rtt_read(channel=0, max_bytes=64), b"boot ok\n")
                probe.rtt_stop()


class TestProbeManagerAndCliIntegration(unittest.TestCase):
    """验证 ProbeManager 在 mock 与 openocd 后端间自动切换，以及 probe.cli 写入与烧录护栏。"""

    def test_probe_manager_switches_between_mock_and_openocd(self) -> None:
        with FakeOpenOCDTclServer() as srv:
            mgr = ProbeManager()
            hook_count = {"n": 0}
            mgr.add_disconnect_hook(lambda: hook_count.__setitem__("n", hook_count["n"] + 1))

            env = {
                k: v for k, v in os.environ.items() if not k.startswith("MCU_HARNESS_")
            }
            env["MCU_HARNESS_OPENOCD_PORT"] = str(srv.port)

            with patch.dict(os.environ, env, clear=True):
                # 1. 先以 mock 后端建立会话
                with mgr.session(backend="mock", device="STM32H563ZI") as p_mock:
                    self.assertIsInstance(p_mock, MockProbe)
                    p_mock.write_u32(0x20000010, [0x55AA55AA])
                    self.assertEqual(p_mock.read_u32(0x20000010), [0x55AA55AA])

                # 2. 请求 openocd 后端会话：应自动触发断连回调、关闭旧 MockProbe，并连上真实 TCP OpenOCD
                with mgr.session(backend="openocd", device="STM32H563ZI") as p_ocd:
                    self.assertIsInstance(p_ocd, OpenOCDProbe)
                    self.assertFalse(p_mock.is_connected())
                    self.assertEqual(hook_count["n"], 1)
                    self.assertEqual(p_ocd.read_u32(0xE000ED00), [0x410FD210])

                # 3. 再次切回 mock 后端会话：应关闭 OpenOCDProbe 并创建新 MockProbe
                with mgr.session(backend="mock", device="STM32H563ZI") as p_mock2:
                    self.assertIsInstance(p_mock2, MockProbe)
                    self.assertIsNot(p_mock2, p_mock)
                    self.assertFalse(p_ocd.is_connected())
                    self.assertEqual(hook_count["n"], 2)

                mgr.disconnect()
                self.assertEqual(hook_count["n"], 3)

    def test_probe_cli_write_and_flash_safety_enforcement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            elf_file = Path(tmp) / "demo.elf"
            elf_file.write_bytes(_build_minimal_arm_elf())
            bin_file = Path(tmp) / "demo.bin"
            bin_file.write_bytes(b"\x00\x00\x02\x20\x81\x01\x00\x08")

            # 1. 写外设 MMIO (0x42020014) 未加 --confirm：被安全护栏拦截，返回码 2
            err_buf = io.StringIO()
            with contextlib.redirect_stderr(err_buf):
                rc_block = probe_cli_main(["--mock", "write", "0x42020014", "0x00000000"])
            self.assertEqual(rc_block, 2)
            self.assertIn("HITL confirmation required", err_buf.getvalue())

            # 2. 写外设 MMIO (0x42020014) 加 --confirm：放行并执行写入，返回码 0
            out_buf = io.StringIO()
            with contextlib.redirect_stdout(out_buf):
                rc_ok = probe_cli_main(["--mock", "--json", "write", "0x42020014", "0x00000000", "--confirm"])
            self.assertEqual(rc_ok, 0)
            self.assertEqual(json.loads(out_buf.getvalue())["written"], 1)

            # 3. 写 STM32H5 Option Bytes (0x08FFF000) 即使加 --confirm 也硬拒绝，返回码 2
            ob_err = io.StringIO()
            with contextlib.redirect_stderr(ob_err):
                rc_ob = probe_cli_main(
                    ["--mock", "--device", "STM32H563ZI", "write", "0x08FFF000", "0xDEADBEEF", "--confirm"]
                )
            self.assertEqual(rc_ob, 2)
            self.assertIn("HARD DENY", ob_err.getvalue())

            # 4. 写普通 SRAM (0x20000100) 无需 --confirm 直接成功，返回码 0
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(probe_cli_main(["--mock", "write", "0x20000100", "0x12345678"]), 0)

            # 5. flash 未加 --confirm：被 HITL 拦截，返回码 2
            f_err = io.StringIO()
            with contextlib.redirect_stderr(f_err):
                rc_flash_block = probe_cli_main(["--mock", "flash", str(elf_file)])
            self.assertEqual(rc_flash_block, 2)
            self.assertIn("HITL confirmation required", f_err.getvalue())

            # 6. flash 加 --confirm：成功烧录 .elf，返回码 0
            f_out = io.StringIO()
            with contextlib.redirect_stdout(f_out):
                rc_flash_ok = probe_cli_main(["--mock", "--json", "flash", str(elf_file), "--confirm"])
            self.assertEqual(rc_flash_ok, 0)
            flash_payload = json.loads(f_out.getvalue())
            self.assertTrue(flash_payload["success"])
            self.assertTrue(flash_payload["running"])

            # 7. flash .bin 未传 --address 返回码 1，传 --address 0x08000000 --confirm 返回码 0
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(probe_cli_main(["--mock", "flash", str(bin_file), "--confirm"]), 1)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    probe_cli_main(["--mock", "flash", str(bin_file), "--address", "0x08000000", "--confirm"]),
                    0,
                )

            # 8. CLI info / read / regs / reset 冒烟验证
            info_out = io.StringIO()
            with contextlib.redirect_stdout(info_out):
                self.assertEqual(probe_cli_main(["--mock", "--json", "info"]), 0)
            self.assertEqual(json.loads(info_out.getvalue())["backend"], "mock")

            regs_out = io.StringIO()
            with contextlib.redirect_stdout(regs_out):
                self.assertEqual(probe_cli_main(["--mock", "--json", "regs", "pc", "sp"]), 0)
            self.assertIn("pc", json.loads(regs_out.getvalue()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
