"""MCP Server 端到端测试：通过 FastMCP Client 验证工具注册、Schema 与实际调用。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from mcu_harness.log import default_rtt_collector
from mcu_harness.probe import MockProbe, default_probe_manager
from mcu_harness.server import mcp

ROOT = Path(__file__).parent.parent
DEMO = ROOT / "examples" / "demo_fw"
DEMO_MAKE = ROOT / "examples" / "demo_make_fw"
SVD_FIX = ROOT / "tests" / "fixtures" / "stm32h563_mini.svd"


def _tool_data(call_result) -> dict:
    """从 MCP CallToolResult 中提取结构化字典（兼容 structured_content 与文本 JSON）。"""
    if call_result.structured_content is not None:
        return call_result.structured_content
    return json.loads(call_result.content[0].text)


def test_list_tools_and_schema():
    async def _run():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            names = [t.name for t in tools]
            assert "build_firmware" in names
            tool = next(t for t in tools if t.name == "build_firmware")
            props = tool.input_schema["properties"]
            assert "project_dir" in props
            assert "project_dir" in tool.input_schema.get("required", [])

    asyncio.run(_run())


@pytest.mark.skipif(
    not (shutil.which("cmake") and shutil.which("ninja") and shutil.which("gcc")),
    reason="需要 cmake + ninja + gcc",
)
def test_call_build_firmware_via_mcp(tmp_path):
    proj = tmp_path / "demo_fw"
    shutil.copytree(DEMO, proj)

    async def _run():
        async with Client(mcp) as client:
            ok = await client.call_tool("build_firmware", {"project_dir": str(proj)})
            ok_data = _tool_data(ok)
            assert ok_data["success"] is True
            assert ok_data["total_warnings"] == 1
            assert any(a["path"].endswith("demo_fw.elf") for a in ok_data["artifacts"])

            bad = await client.call_tool(
                "build_firmware",
                {
                    "project_dir": str(proj),
                    "reconfigure": True,
                    "extra_cmake_args": ["-DDEMO_INJECT_ERROR=ON"],
                },
            )
            bad_data = _tool_data(bad)
            assert bad_data["success"] is False
            assert bad_data["total_errors"] >= 1
            assert "led_tggle" in bad_data["errors"][0]["message"]

    asyncio.run(_run())


def test_stdio_transport_handshake():
    """真实起一个子进程走 stdio JSON-RPC 握手，确保不会有 banner/print 污染 stdout。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "mcu_harness.server"],
        env=env,
        cwd=str(ROOT),
    )

    async def _run():
        async with Client(transport) as client:
            tools = await client.list_tools()
            assert any(t.name == "build_firmware" for t in tools)
            res = await client.call_tool("build_firmware", {"project_dir": "/nonexistent/fw"})
            data = _tool_data(res)
            assert data["success"] is False
            assert data["stage"] == "precheck"

    asyncio.run(_run())


@pytest.mark.skipif(
    not (shutil.which("make") and shutil.which("gcc") and shutil.which("size")),
    reason="需要 make + gcc + size",
)
def test_call_build_firmware_makefile_via_mcp(tmp_path):
    proj = tmp_path / "demo_make_fw"
    shutil.copytree(DEMO_MAKE, proj)

    async def _run():
        async with Client(mcp) as client:
            res = await client.call_tool("build_firmware", {"project_dir": str(proj)})
            data = _tool_data(res)
            assert data["success"] is True
            assert data["build_system"] == "make"
            assert any(a["path"].endswith("demo_fw.elf") for a in data["artifacts"])
            assert len(data["sizes"]) == 1

    asyncio.run(_run())


def test_call_inspect_svd_via_mcp(monkeypatch):
    monkeypatch.setenv("MCU_HARNESS_SVD_PATH", str(SVD_FIX))

    async def _run():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            assert any(t.name == "inspect_svd" for t in tools)

            # 1. 列出外设
            periphs = _tool_data(await client.call_tool("inspect_svd", {"name_filter": "GPIO"}))
            assert periphs["device"] == "STM32H563"
            assert [p["name"] for p in periphs["peripherals"]] == ["GPIOA", "GPIOB"]

            # 2. 解码指定寄存器值
            dec = _tool_data(
                await client.call_tool(
                    "inspect_svd",
                    {"peripheral": "GPIOB", "register": "MODER", "raw_value": "0x00000400"},
                )
            )
            assert dec["address"] == "0x42020400"
            mode5 = next(f for f in dec["fields"] if f["name"] == "MODE5")
            assert mode5["value"] == 1
            assert "Output" in (mode5["meaning"] or "")

    asyncio.run(_run())


def test_call_probe_and_inspect_peripheral_via_mcp(monkeypatch):
    monkeypatch.setenv("MCU_HARNESS_SVD_PATH", str(SVD_FIX))
    mock_probe = MockProbe()
    mock_probe.connect()
    default_probe_manager.set_probe(mock_probe)

    async def _run():
        try:
            async with Client(mcp) as client:
                tools = {t.name for t in await client.list_tools()}
                assert {"probe_control", "read_memory", "inspect_peripheral"} <= tools

                info = _tool_data(await client.call_tool("probe_control", {"action": "info"}))
                assert info["backend"] == "mock" and info["connected"] is True

                mem = _tool_data(await client.call_tool("read_memory", {"address": "0xE000ED00"}))
                assert mem["hex"] == ["0x410FD210"]

                reg = _tool_data(
                    await client.call_tool(
                        "inspect_peripheral",
                        {"peripheral": "GPIOA", "register": "MODER"},
                    )
                )
                assert reg["raw_hex"] == "0xAB000400"
                assert next(f for f in reg["fields"] if f["name"] == "MODE5")["value"] == 1

                halted = _tool_data(await client.call_tool("probe_control", {"action": "halt"}))
                assert halted["halted"] is True
        finally:
            default_probe_manager.set_probe(None)

    asyncio.run(_run())


def test_call_read_debug_log_via_mcp():
    mock_probe = MockProbe()
    mock_probe.connect()
    mock_probe.push_rtt("[INFO] boot ok\n[ERROR] spi timeout\n")
    default_probe_manager.set_probe(mock_probe)
    default_rtt_collector.clear()

    async def _run():
        try:
            async with Client(mcp) as client:
                tools = {t.name for t in await client.list_tools()}
                assert "read_debug_log" in tools

                first = _tool_data(await client.call_tool("read_debug_log", {"tail_lines": 10}))
                assert first["status"]["running"] is True
                assert [e["text"] for e in first["entries"]] == ["[INFO] boot ok", "[ERROR] spi timeout"]

                mock_probe.push_rtt("[INFO] recovered\n")
                await asyncio.sleep(0.08)
                inc = _tool_data(
                    await client.call_tool("read_debug_log", {"since_seq": first["newest_seq"]})
                )
                assert [e["text"] for e in inc["entries"]] == ["[INFO] recovered"]

                stopped = _tool_data(await client.call_tool("read_debug_log", {"action": "stop"}))
                assert stopped["status"]["running"] is False
        finally:
            default_rtt_collector.stop()
            default_rtt_collector.clear()
            default_probe_manager.set_probe(None)

    asyncio.run(_run())


def test_mcp_input_validation_and_error_fallbacks(tmp_path):
    """验证 MCP 层入参边界校验与统一异常兜底（FIX-04）。"""
    bad_svd = tmp_path / "broken.svd"
    bad_svd.write_text("<device><broken", encoding="utf-8")

    mock_probe = MockProbe()
    mock_probe.connect()
    default_probe_manager.set_probe(mock_probe)

    async def _run():
        try:
            async with Client(mcp) as client:
                # 1. read_memory 负数、越界、未对齐、布尔值拦截
                for payload in (
                    {"address": -4},
                    {"address": "0x100000000"},
                    {"address": "0xFFFFFFFD", "width": 32, "count": 1},
                    {"address": "0x20000001", "width": 32},
                    {"address": "0x20000001", "width": 16},
                    {"address": True},
                ):
                    res = _tool_data(await client.call_tool("read_memory", payload))
                    assert "error" in res

                # 2. inspect_peripheral 遇到损坏 SVD 返回统一 error 字段
                res_svd = _tool_data(
                    await client.call_tool(
                        "inspect_peripheral",
                        {"peripheral": "GPIOA", "register": "MODER", "svd_path": str(bad_svd)},
                    )
                )
                assert "error" in res_svd

                # 3. read_debug_log context_lines 越界校验
                res_ctx = _tool_data(await client.call_tool("read_debug_log", {"context_lines": 99}))
                assert "error" in res_ctx

                # 4. probe_control 非法 serial_no 返回结构化错误
                res_sn = _tool_data(
                    await client.call_tool("probe_control", {"action": "connect", "serial_no": "invalid"})
                )
                assert "error" in res_sn
        finally:
            default_probe_manager.set_probe(None)

    asyncio.run(_run())
