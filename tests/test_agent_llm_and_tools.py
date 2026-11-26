"""Subagent 6A 单元测试：多厂商纯标准库 LLM 路由、工作区工具与统一 27 工具注册表。"""

from __future__ import annotations

import ast
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest import mock

from mcu_harness.agent.llm import (
    AnthropicProvider,
    GeminiProvider,
    LlmError,
    LlmResponse,
    MockLlmProvider,
    OpenAICompatProvider,
    ToolCall,
    _ThinkTagSplitter,
    create_provider,
)
from mcu_harness.agent.registry import ToolContext, build_default_registry
from mcu_harness.agent.workspace_tools import (
    _resolve_workspace_path,
    preview_edit_diff,
    tool_edit_file,
    tool_list_files,
    tool_read_file,
    tool_run_shell,
    tool_search_files,
    tool_write_file,
)
from mcu_harness.log import RttLogCollector, UartLogCollector
from mcu_harness.probe import MockProbe, ProbeManager
from mcu_harness.safety import SafetyGuard


class _MockHttpState:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.handler_fn: Any = None


def _start_local_http_server(state: _MockHttpState) -> tuple[ThreadingHTTPServer, str]:
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            del format, args

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length).decode("utf-8")
            body = json.loads(raw_body) if raw_body else {}
            req_record = {
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body,
            }
            state.requests.append(req_record)
            status, resp_headers, resp_bytes = state.handler_fn(req_record)
            self.send_response(status)
            for hk, hv in resp_headers.items():
                self.send_header(hk, hv)
            self.end_headers()
            self.wfile.write(resp_bytes)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


class TestLlmProvidersAndFactory(unittest.TestCase):
    def test_openai_json_tool_call_and_roundtrip(self) -> None:
        state = _MockHttpState()

        def handle(req: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
            if len(state.requests) == 1:
                payload = {
                    "id": "chatcmpl-1",
                    "model": "gpt-4o",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_abc",
                                        "type": "function",
                                        "function": {
                                            "name": "read_memory",
                                            "arguments": '{"address": "0xE000ED00", "count": 1}',
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
                }
            else:
                payload = {
                    "id": "chatcmpl-2",
                    "model": "gpt-4o",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "CPUID is 0x411FC231."},
                        }
                    ],
                    "usage": {"prompt_tokens": 35, "completion_tokens": 8, "total_tokens": 43},
                }
            return 200, {"Content-Type": "application/json"}, json.dumps(payload).encode("utf-8")

        state.handler_fn = handle
        server, base = _start_local_http_server(state)
        try:
            prov = OpenAICompatProvider(
                provider_name="openai",
                model="gpt-4o",
                api_key="sk-test-123",
                base_url=f"{base}/v1",
            )
            tools = [
                {
                    "name": "read_memory",
                    "description": "Read target memory",
                    "parameters": {"type": "object", "properties": {"address": {"type": "string"}}},
                }
            ]
            r1 = prov.complete(
                [{"role": "user", "content": "Read CPUID"}],
                system="You are an MCU agent.",
                tools=tools,
            )
            self.assertEqual(r1.finish_reason, "tool_calls")
            self.assertEqual(len(r1.tool_calls), 1)
            self.assertEqual(r1.tool_calls[0].id, "call_abc")
            self.assertEqual(r1.tool_calls[0].name, "read_memory")
            self.assertEqual(r1.tool_calls[0].arguments, {"address": "0xE000ED00", "count": 1})
            self.assertEqual(r1.usage["total_tokens"], 30)

            req1 = state.requests[0]
            self.assertEqual(req1["path"], "/v1/chat/completions")
            self.assertEqual(req1["headers"]["authorization"], "Bearer sk-test-123")
            self.assertEqual(req1["body"]["messages"][0], {"role": "system", "content": "You are an MCU agent."})
            self.assertEqual(req1["body"]["tools"][0]["type"], "function")
            self.assertEqual(req1["body"]["tools"][0]["function"]["name"], "read_memory")

            # Roundtrip with assistant tool_calls + tool result
            r2 = prov.complete(
                [
                    {"role": "user", "content": "Read CPUID"},
                    {"role": "assistant", "content": "", "tool_calls": [r1.tool_calls[0].to_dict()]},
                    {"role": "tool", "tool_call_id": "call_abc", "name": "read_memory", "content": '{"hex":["0x411FC231"]}'},
                ],
                tools=tools,
            )
            self.assertEqual(r2.text, "CPUID is 0x411FC231.")
            self.assertEqual(r2.finish_reason, "stop")
            req2_msgs = state.requests[1]["body"]["messages"]
            self.assertEqual(req2_msgs[1]["tool_calls"][0]["id"], "call_abc")
            self.assertEqual(req2_msgs[2]["role"], "tool")
            self.assertEqual(req2_msgs[2]["tool_call_id"], "call_abc")
        finally:
            server.shutdown()
            server.server_close()

    def test_openai_sse_streaming_and_fragmented_tool_calls(self) -> None:
        state = _MockHttpState()

        sse_chunks = [
            {"id": "1", "model": "deepseek-chat", "choices": [{"index": 0, "delta": {"content": "Checking "}}]},
            {"id": "1", "model": "deepseek-chat", "choices": [{"index": 0, "delta": {"content": "target..."}}]},
            {
                "id": "1",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "id": "call_0", "function": {"name": "read_fi", "arguments": '{"path":'}}
                            ]
                        },
                    }
                ],
            },
            {
                "id": "1",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {"index": 1, "id": "call_1", "function": {"name": "list_files", "arguments": '{"path": "."}'}},
                                {"index": 0, "function": {"name": "le", "arguments": ' "src/main.c"}'}},
                            ]
                        },
                    }
                ],
            },
            {
                "id": "1",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 18, "total_tokens": 30},
            },
        ]
        sse_body = "".join(f"data: {json.dumps(c)}\n\n" for c in sse_chunks) + "data: [DONE]\n\n"

        state.handler_fn = lambda _req: (200, {"Content-Type": "text/event-stream"}, sse_body.encode("utf-8"))
        server, base = _start_local_http_server(state)
        try:
            prov = OpenAICompatProvider(
                provider_name="deepseek",
                api_key="ds-key",
                base_url=f"{base}/v1",
            )
            deltas: list[str] = []
            resp = prov.complete(
                [{"role": "user", "content": "Check main.c"}],
                on_text_delta=deltas.append,
            )
            self.assertTrue(state.requests[0]["body"]["stream"])
            self.assertEqual(deltas, ["Checking ", "target..."])
            self.assertEqual(resp.text, "Checking target...")
            self.assertEqual(resp.finish_reason, "tool_calls")
            self.assertEqual(len(resp.tool_calls), 2)
            self.assertEqual(resp.tool_calls[0], ToolCall(id="call_0", name="read_file", arguments={"path": "src/main.c"}))
            self.assertEqual(resp.tool_calls[1], ToolCall(id="call_1", name="list_files", arguments={"path": "."}))
            self.assertEqual(resp.usage["total_tokens"], 30)
        finally:
            server.shutdown()
            server.server_close()

    def test_anthropic_messages_tools_and_tool_results(self) -> None:
        state = _MockHttpState()

        def handle(req: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
            if len(state.requests) == 1:
                body = {
                    "id": "msg_1",
                    "model": "claude-sonnet-4-20250514",
                    "stop_reason": "tool_use",
                    "content": [
                        {"type": "text", "text": "Let me inspect."},
                        {"type": "tool_use", "id": "toolu_1", "name": "inspect_svd", "input": {"peripheral": "RCC"}},
                        {"type": "tool_use", "id": "toolu_2", "name": "read_memory", "input": {"address": "0x40021000"}},
                    ],
                    "usage": {"input_tokens": 50, "output_tokens": 25},
                }
            else:
                body = {
                    "id": "msg_2",
                    "model": "claude-sonnet-4-20250514",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "Done inspecting RCC."}],
                    "usage": {"input_tokens": 80, "output_tokens": 10},
                }
            return 200, {"Content-Type": "application/json"}, json.dumps(body).encode("utf-8")

        state.handler_fn = handle
        server, base = _start_local_http_server(state)
        try:
            prov = AnthropicProvider(api_key="anth-secret", base_url=base)
            tools = [
                {
                    "name": "inspect_svd",
                    "description": "Inspect SVD",
                    "parameters": {"type": "object", "properties": {"peripheral": {"type": "string"}}},
                }
            ]
            r1 = prov.complete(
                [{"role": "user", "content": "Inspect RCC"}],
                system="Embedded expert",
                tools=tools,
            )
            req1 = state.requests[0]
            self.assertEqual(req1["path"], "/v1/messages")
            self.assertEqual(req1["headers"]["x-api-key"], "anth-secret")
            self.assertEqual(req1["headers"]["anthropic-version"], "2023-06-01")
            self.assertEqual(req1["body"]["system"], "Embedded expert")
            self.assertIn("input_schema", req1["body"]["tools"][0])

            self.assertEqual(r1.text, "Let me inspect.")
            self.assertEqual(r1.finish_reason, "tool_calls")
            self.assertEqual(len(r1.tool_calls), 2)
            self.assertEqual(r1.tool_calls[0].id, "toolu_1")
            self.assertEqual(r1.tool_calls[1].name, "read_memory")
            self.assertEqual(r1.usage, {"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75})

            # Verify consecutive tool messages merge into single user content block list
            r2 = prov.complete(
                [
                    {"role": "user", "content": "Inspect RCC"},
                    {
                        "role": "assistant",
                        "content": r1.text,
                        "tool_calls": [tc.to_dict() for tc in r1.tool_calls],
                    },
                    {"role": "tool", "tool_call_id": "toolu_1", "name": "inspect_svd", "content": '{"base":"0x40021000"}'},
                    {"role": "tool", "tool_call_id": "toolu_2", "name": "read_memory", "content": '{"values":[1]}'},
                ],
                tools=tools,
            )
            self.assertEqual(r2.text, "Done inspecting RCC.")
            req2_msgs = state.requests[1]["body"]["messages"]
            self.assertEqual(req2_msgs[1]["role"], "assistant")
            self.assertEqual(req2_msgs[1]["content"][1]["type"], "tool_use")
            self.assertEqual(req2_msgs[2]["role"], "user")
            self.assertEqual(len(req2_msgs[2]["content"]), 2)
            self.assertEqual(req2_msgs[2]["content"][0]["type"], "tool_result")
            self.assertEqual(req2_msgs[2]["content"][0]["tool_use_id"], "toolu_1")
            self.assertEqual(req2_msgs[2]["content"][1]["tool_use_id"], "toolu_2")
        finally:
            server.shutdown()
            server.server_close()

    def test_gemini_generate_content_and_function_call(self) -> None:
        state = _MockHttpState()

        def handle(req: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
            if len(state.requests) == 1:
                body = {
                    "candidates": [
                        {
                            "finishReason": "STOP",
                            "content": {
                                "role": "model",
                                "parts": [
                                    {"functionCall": {"name": "search_files", "args": {"pattern": "HardFault"}}}
                                ],
                            },
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 15, "candidatesTokenCount": 7, "totalTokenCount": 22},
                }
            else:
                body = {
                    "candidates": [
                        {
                            "finishReason": "STOP",
                            "content": {"role": "model", "parts": [{"text": "Found HardFault handler."}]},
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 25, "candidatesTokenCount": 5, "totalTokenCount": 30},
                }
            return 200, {"Content-Type": "application/json"}, json.dumps(body).encode("utf-8")

        state.handler_fn = handle
        server, base = _start_local_http_server(state)
        try:
            prov = GeminiProvider(model="gemini-2.5-flash", api_key="gem-key", base_url=base)
            tools = [
                {
                    "name": "search_files",
                    "description": "Search workspace",
                    "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}},
                }
            ]
            r1 = prov.complete(
                [{"role": "user", "content": "Find HardFault"}],
                system="Be concise",
                tools=tools,
            )
            req1 = state.requests[0]
            self.assertEqual(req1["path"], "/v1beta/models/gemini-2.5-flash:generateContent")
            self.assertEqual(req1["headers"]["x-goog-api-key"], "gem-key")
            self.assertEqual(req1["body"]["systemInstruction"], {"parts": [{"text": "Be concise"}]})
            self.assertEqual(req1["body"]["tools"][0]["functionDeclarations"][0]["name"], "search_files")

            self.assertEqual(r1.finish_reason, "tool_calls")
            self.assertEqual(len(r1.tool_calls), 1)
            self.assertEqual(r1.tool_calls[0].name, "search_files")
            self.assertEqual(r1.tool_calls[0].arguments, {"pattern": "HardFault"})
            self.assertEqual(r1.usage, {"prompt_tokens": 15, "completion_tokens": 7, "total_tokens": 22})

            r2 = prov.complete(
                [
                    {"role": "user", "content": "Find HardFault"},
                    {"role": "assistant", "content": "", "tool_calls": [r1.tool_calls[0].to_dict()]},
                    {"role": "tool", "tool_call_id": r1.tool_calls[0].id, "name": "search_files", "content": '{"count": 1}'},
                ],
                tools=tools,
            )
            self.assertEqual(r2.text, "Found HardFault handler.")
            req2_contents = state.requests[1]["body"]["contents"]
            self.assertEqual(req2_contents[1]["role"], "model")
            self.assertEqual(req2_contents[1]["parts"][0]["functionCall"]["name"], "search_files")
            self.assertEqual(req2_contents[2]["role"], "user")
            self.assertEqual(req2_contents[2]["parts"][0]["functionResponse"]["name"], "search_files")
            self.assertEqual(req2_contents[2]["parts"][0]["functionResponse"]["response"], {"count": 1})
        finally:
            server.shutdown()
            server.server_close()

    def test_mock_provider_and_create_provider_precedence(self) -> None:
        # MockLlmProvider queue & history
        mock_prov = MockLlmProvider(
            [
                LlmResponse(text="step1", tool_calls=[ToolCall(id="1", name="list_files", arguments={})]),
                LlmResponse(text="done"),
            ]
        )
        deltas: list[str] = []
        self.assertEqual(mock_prov.complete([{"role": "user", "content": "hi"}], on_text_delta=deltas.append).text, "step1")
        self.assertEqual(mock_prov.complete([{"role": "user", "content": "next"}]).text, "done")
        self.assertEqual(deltas, ["step1"])
        self.assertEqual(len(mock_prov.call_history), 2)
        with self.assertRaises(LlmError):
            mock_prov.complete([{"role": "user", "content": "exhausted"}])

        # create_provider precedence: explicit > project mcu-harness.toml > user agent.toml > env > defaults
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proj = root / "proj"
            proj.mkdir()
            fake_home = root / "home"
            user_cfg_dir = fake_home / ".config" / "mcu-harness"
            user_cfg_dir.mkdir(parents=True)

            (user_cfg_dir / "agent.toml").write_text(
                '[agent]\nprovider = "deepseek"\nmodel = "deepseek-reasoner"\napi_key = "user-key"\n',
                encoding="utf-8",
            )
            (proj / "mcu-harness.toml").write_text(
                '[agent]\nprovider = "qwen"\nmodel = "qwen-max"\napi_key = "proj-key"\n',
                encoding="utf-8",
            )

            env_override = {
                "MCU_AGENT_PROVIDER": "gemini",
                "MCU_AGENT_MODEL": "env-model",
                "MCU_AGENT_API_KEY": "env-key",
            }
            with mock.patch.dict(os.environ, env_override, clear=False), mock.patch.object(
                Path, "home", return_value=fake_home
            ):
                # Project TOML overrides user TOML and env
                p_proj = create_provider(project_dir=str(proj))
                self.assertEqual(p_proj.provider_name, "qwen")
                self.assertEqual(p_proj.model, "qwen-max")
                self.assertEqual(getattr(p_proj, "api_key"), "proj-key")

                # Explicit arg overrides project TOML
                p_exp = create_provider("anthropic", model="claude-custom", api_key="exp-key", project_dir=str(proj))
                self.assertIsInstance(p_exp, AnthropicProvider)
                self.assertEqual(p_exp.model, "claude-custom")
                self.assertEqual(p_exp.api_key, "exp-key")

                # Without project_dir, user TOML overrides env
                p_user = create_provider()
                self.assertEqual(p_user.provider_name, "deepseek")
                self.assertEqual(p_user.model, "deepseek-reasoner")

                # Without user TOML, env overrides preset
                (user_cfg_dir / "agent.toml").unlink()
                p_env = create_provider()
                self.assertIsInstance(p_env, GeminiProvider)
                self.assertEqual(p_env.model, "env-model")

            # Missing key error for remote provider, allowed for ollama & mock
            clean_env = {k: v for k, v in os.environ.items() if "API_KEY" not in k and not k.startswith("MCU_AGENT_")}
            with mock.patch.dict(os.environ, clean_env, clear=True), mock.patch.object(
                Path, "home", return_value=fake_home
            ):
                with self.assertRaises(LlmError):
                    create_provider("openai")
                with self.assertRaises(LlmError):
                    create_provider("unknown_vendor")
                self.assertIsInstance(create_provider("mock"), MockLlmProvider)
                ollama = create_provider("ollama")
                self.assertEqual(ollama.provider_name, "ollama")
                self.assertEqual(ollama.model, "qwen2.5-coder:14b")

    def test_llm_response_to_dict_includes_reasoning_and_blocks(self) -> None:
        resp = LlmResponse(
            text="answer",
            reasoning="step by step",
            reasoning_blocks=[{"type": "thinking", "thinking": "step by step", "signature": "sig1"}],
            model="claude-sonnet-4-20250514",
            provider="anthropic",
        )
        d = resp.to_dict()
        self.assertEqual(d["reasoning"], "step by step")
        self.assertEqual(d["reasoning_blocks"], [{"type": "thinking", "thinking": "step by step", "signature": "sig1"}])

    def test_think_tag_splitter_across_chunk_boundaries(self) -> None:
        text_deltas: list[str] = []
        reasoning_deltas: list[str] = []
        splitter = _ThinkTagSplitter(on_text=text_deltas.append, on_reasoning=reasoning_deltas.append)

        # Split opening tag "<think>", closing tag "</think>", and include a false "<t" prefix
        chunks = [
            "Intro <t",
            "here! <thi",
            "nk>first ",
            "reason</th",
            "ink>Middle <thin",
            "k>second</thin",
            "k>End<th",
        ]
        for ch in chunks:
            splitter.feed(ch)
        splitter.finish()

        self.assertEqual("".join(reasoning_deltas), "first reasonsecond")
        self.assertEqual("".join(text_deltas), "Intro <there! Middle End<th")
        self.assertEqual(splitter.reasoning, "first reasonsecond")
        self.assertEqual(splitter.text, "Intro <there! Middle End<th")

    def test_openai_sse_reasoning_content_and_nonstream_think_strip(self) -> None:
        state = _MockHttpState()
        sse_chunks = [
            {"id": "1", "model": "deepseek-reasoner", "choices": [{"index": 0, "delta": {"reasoning_content": "Check SCB "}}]},
            {"id": "1", "model": "deepseek-reasoner", "choices": [{"index": 0, "delta": {"reasoning": "and CFSR."}}]},
            {"id": "1", "model": "deepseek-reasoner", "choices": [{"index": 0, "delta": {"content": "<thi"}}]},
            {"id": "1", "model": "deepseek-reasoner", "choices": [{"index": 0, "delta": {"content": "nk>Extra</think>Done."}}]},
            {"id": "1", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        sse_body = "".join(f"data: {json.dumps(c)}\n\n" for c in sse_chunks) + "data: [DONE]\n\n"

        def handle(req: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
            if req["body"].get("stream"):
                return 200, {"Content-Type": "text/event-stream"}, sse_body.encode("utf-8")
            return (
                200,
                {"Content-Type": "application/json"},
                json.dumps(
                    {
                        "model": "qwen3",
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {
                                    "role": "assistant",
                                    "reasoning": "Header thought.",
                                    "content": "<think>Inner thought.</think>Final answer.",
                                },
                            }
                        ],
                    }
                ).encode("utf-8"),
            )

        state.handler_fn = handle
        server, base = _start_local_http_server(state)
        try:
            prov = OpenAICompatProvider(provider_name="deepseek", api_key="ds-key", base_url=f"{base}/v1")
            r_deltas: list[str] = []
            t_deltas: list[str] = []
            resp_stream = prov.complete(
                [{"role": "user", "content": "Diagnose"}],
                on_text_delta=t_deltas.append,
                on_reasoning_delta=r_deltas.append,
            )
            self.assertEqual(r_deltas, ["Check SCB ", "and CFSR.", "Extra"])
            self.assertEqual(t_deltas, ["Done."])
            self.assertEqual(resp_stream.reasoning, "Check SCB and CFSR.Extra")
            self.assertEqual(resp_stream.text, "Done.")

            resp_json = prov.complete([{"role": "user", "content": "Diagnose"}])
            self.assertEqual(resp_json.reasoning, "Header thought.\nInner thought.")
            self.assertEqual(resp_json.text, "Final answer.")
        finally:
            server.shutdown()
            server.server_close()

    def test_anthropic_sse_thinking_text_tool_use_json_fallback_and_budget(self) -> None:
        state = _MockHttpState()
        sse_events = [
            ("message_start", {"type": "message_start", "message": {"id": "msg_1", "model": "claude-sonnet-4-20250514", "usage": {"input_tokens": 42}}}),
            ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Inspecting "}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "CFSR."}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig_part_1"}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "_part_2"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Checking "}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "fault."}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 1}),
            ("content_block_start", {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "toolu_9", "name": "diagnose_crash", "input": {}}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"keep_halted": '}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": "true}"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 2}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 19}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        sse_body = "".join(f"event: {ev}\ndata: {json.dumps(data)}\n\n" for ev, data in sse_events)

        def handle(req: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
            if len(state.requests) == 1:
                return 200, {"Content-Type": "text/event-stream"}, sse_body.encode("utf-8")
            # JSON fallback even when stream=True was requested
            fallback_body = {
                "id": "msg_2",
                "model": "claude-sonnet-4-20250514",
                "stop_reason": "end_turn",
                "content": [
                    {"type": "thinking", "thinking": "Fallback thought", "signature": "sig_fb"},
                    {"type": "text", "text": "Fallback answer"},
                ],
                "usage": {"input_tokens": 60, "output_tokens": 12},
            }
            return 200, {"Content-Type": "application/json"}, json.dumps(fallback_body).encode("utf-8")

        state.handler_fn = handle
        server, base = _start_local_http_server(state)
        try:
            prov = AnthropicProvider(api_key="anth-key", base_url=base, thinking_budget=2048)
            r_deltas: list[str] = []
            t_deltas: list[str] = []
            r1 = prov.complete(
                [{"role": "user", "content": "Diagnose crash"}],
                temperature=0.2,
                max_tokens=1024,
                on_text_delta=t_deltas.append,
                on_reasoning_delta=r_deltas.append,
            )
            req1_body = state.requests[0]["body"]
            self.assertTrue(req1_body["stream"])
            self.assertEqual(req1_body["thinking"], {"type": "enabled", "budget_tokens": 2048})
            self.assertEqual(req1_body["temperature"], 1)
            self.assertEqual(req1_body["max_tokens"], 3072)

            self.assertEqual(r_deltas, ["Inspecting ", "CFSR."])
            self.assertEqual(t_deltas, ["Checking ", "fault."])
            self.assertEqual(r1.reasoning, "Inspecting CFSR.")
            self.assertEqual(r1.text, "Checking fault.")
            self.assertEqual(
                r1.reasoning_blocks,
                [{"type": "thinking", "thinking": "Inspecting CFSR.", "signature": "sig_part_1_part_2"}],
            )
            self.assertEqual(r1.finish_reason, "tool_calls")
            self.assertEqual(r1.tool_calls, [ToolCall(id="toolu_9", name="diagnose_crash", arguments={"keep_halted": True})])
            self.assertEqual(r1.usage, {"prompt_tokens": 42, "completion_tokens": 19, "total_tokens": 61})

            # Roundtrip: reasoning_blocks must be placed FIRST in assistant content array
            r_fb: list[str] = []
            t_fb: list[str] = []
            events_order: list[tuple[str, str]] = []
            r2 = prov.complete(
                [
                    {"role": "user", "content": "Diagnose crash"},
                    {
                        "role": "assistant",
                        "content": r1.text,
                        "reasoning_blocks": r1.reasoning_blocks,
                        "tool_calls": [tc.to_dict() for tc in r1.tool_calls],
                    },
                    {"role": "tool", "tool_call_id": "toolu_9", "name": "diagnose_crash", "content": '{"ok": true}'},
                ],
                on_text_delta=lambda d: (t_fb.append(d), events_order.append(("text", d)))[0],
                on_reasoning_delta=lambda d: (r_fb.append(d), events_order.append(("reasoning", d)))[0],
            )
            req2_msgs = state.requests[1]["body"]["messages"]
            self.assertEqual(req2_msgs[1]["content"][0]["type"], "thinking")
            self.assertEqual(req2_msgs[1]["content"][0]["signature"], "sig_part_1_part_2")
            self.assertEqual(req2_msgs[1]["content"][1]["type"], "text")
            self.assertEqual(req2_msgs[1]["content"][2]["type"], "tool_use")
            self.assertEqual(r_fb, ["Fallback thought"])
            self.assertEqual(t_fb, ["Fallback answer"])
            self.assertEqual(events_order, [("reasoning", "Fallback thought"), ("text", "Fallback answer")])
            self.assertEqual(r2.reasoning_blocks[0]["signature"], "sig_fb")
        finally:
            server.shutdown()
            server.server_close()

        # create_provider thinking_budget config & env precedence
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proj = root / "proj"
            proj.mkdir()
            fake_home = root / "home"
            user_cfg_dir = fake_home / ".config" / "mcu-harness"
            user_cfg_dir.mkdir(parents=True)
            (proj / "mcu-harness.toml").write_text(
                '[agent]\nprovider = "anthropic"\napi_key = "k1"\nthinking_budget = 4096\n',
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {"MCU_AGENT_THINKING_BUDGET": "1024"}, clear=False), mock.patch.object(
                Path, "home", return_value=fake_home
            ):
                p_proj = create_provider(project_dir=str(proj))
                self.assertIsInstance(p_proj, AnthropicProvider)
                assert isinstance(p_proj, AnthropicProvider)
                self.assertEqual(p_proj.thinking_budget, 4096)

                p_env = create_provider("anthropic", api_key="k2")
                assert isinstance(p_env, AnthropicProvider)
                self.assertEqual(p_env.thinking_budget, 1024)

    def test_gemini_sse_thought_parts_and_json_fallback(self) -> None:
        state = _MockHttpState()
        sse_chunks = [
            {"modelVersion": "gemini-2.5-flash", "candidates": [{"content": {"parts": [{"text": "Let me think. ", "thought": True}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "Checking RCC.", "thought": True}, {"text": "Clock is "}]}}]},
            {
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"parts": [{"text": "enabled."}]},
                    }
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 8, "totalTokenCount": 18},
            },
        ]
        sse_body = "".join(f"data: {json.dumps(c)}\n\n" for c in sse_chunks)

        def handle(req: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
            if len(state.requests) == 1:
                return 200, {"Content-Type": "text/event-stream"}, sse_body.encode("utf-8")
            fallback_body = {
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "parts": [
                                {"text": "JSON thought.", "thought": True},
                                {"text": "JSON answer."},
                            ]
                        },
                    }
                ]
            }
            return 200, {"Content-Type": "application/json"}, json.dumps(fallback_body).encode("utf-8")

        state.handler_fn = handle
        server, base = _start_local_http_server(state)
        try:
            prov = GeminiProvider(model="gemini-2.5-flash", api_key="gem-key", base_url=base)
            r_deltas: list[str] = []
            t_deltas: list[str] = []
            r1 = prov.complete(
                [{"role": "user", "content": "Check clock"}],
                on_text_delta=t_deltas.append,
                on_reasoning_delta=r_deltas.append,
            )
            req1 = state.requests[0]
            self.assertEqual(req1["path"], "/v1beta/models/gemini-2.5-flash:streamGenerateContent?alt=sse")
            self.assertEqual(req1["body"]["generationConfig"]["thinkingConfig"], {"includeThoughts": True})
            self.assertEqual(r_deltas, ["Let me think. ", "Checking RCC."])
            self.assertEqual(t_deltas, ["Clock is ", "enabled."])
            self.assertEqual(r1.reasoning, "Let me think. Checking RCC.")
            self.assertEqual(r1.text, "Clock is enabled.")
            self.assertEqual(r1.usage, {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18})

            events_order: list[tuple[str, str]] = []
            r2 = prov.complete(
                [{"role": "user", "content": "Fallback"}],
                on_text_delta=lambda d: events_order.append(("text", d)),
                on_reasoning_delta=lambda d: events_order.append(("reasoning", d)),
            )
            self.assertEqual(events_order, [("reasoning", "JSON thought."), ("text", "JSON answer.")])
            self.assertEqual(r2.reasoning, "JSON thought.")
            self.assertEqual(r2.text, "JSON answer.")
        finally:
            server.shutdown()
            server.server_close()

    def test_mock_provider_chunked_streaming(self) -> None:
        prov = MockLlmProvider(
            [LlmResponse(reasoning="Think123", text="Answer45")],
            stream_chunk_chars=3,
            stream_chunk_delay_s=0.0,
        )
        events: list[tuple[str, str]] = []
        resp = prov.complete(
            [{"role": "user", "content": "hi"}],
            on_reasoning_delta=lambda d: events.append(("reasoning", d)),
            on_text_delta=lambda d: events.append(("text", d)),
        )
        self.assertEqual(resp.reasoning, "Think123")
        self.assertEqual(resp.text, "Answer45")
        self.assertEqual(
            events,
            [
                ("reasoning", "Thi"),
                ("reasoning", "nk1"),
                ("reasoning", "23"),
                ("text", "Ans"),
                ("text", "wer"),
                ("text", "45"),
            ],
        )


class TestWorkspaceTools(unittest.TestCase):
    def test_path_escape_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ws"
            root.mkdir()
            outside = Path(tmp) / "secret.txt"
            outside.write_text("top-secret", encoding="utf-8")

            with self.assertRaises(ValueError):
                _resolve_workspace_path(root, "../secret.txt")
            with self.assertRaises(ValueError):
                _resolve_workspace_path(root, str(outside))
            with self.assertRaises(ValueError):
                tool_read_file(str(root), "../../etc/passwd")
            with self.assertRaises(ValueError):
                tool_write_file(str(root), "../evil.txt", "boom")
            with self.assertRaises(ValueError):
                tool_edit_file(str(root), "../secret.txt", "top", "bad")
            with self.assertRaises(ValueError):
                tool_list_files(str(root), "..")

            # Symlink escape also blocked
            link = root / "escape_link"
            link.symlink_to(outside)
            with self.assertRaises(ValueError):
                tool_read_file(str(root), "escape_link")

    def test_read_write_edit_diff_list_search_and_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # write_file with parent dir creation & overwrite protection
            w1 = tool_write_file(str(root), "src/main.c", "int x = 1;\nint y = 1;\nint z = 3;\n")
            self.assertTrue(w1["success"])
            w2 = tool_write_file(str(root), "src/main.c", "replaced", overwrite=False)
            self.assertFalse(w2["success"])
            self.assertIn("already exists", w2["error"])

            # read_file with line range
            rd = tool_read_file(str(root), "src/main.c", start_line=2, end_line=3)
            self.assertEqual(rd["total_lines"], 3)
            self.assertIn("2: int y = 1;", rd["content"])
            self.assertIn("3: int z = 3;", rd["content"])

            # binary file protection
            (root / "fw.bin").write_bytes(b"\x00\x01\x02\x03")
            rb = tool_read_file(str(root), "fw.bin")
            self.assertIn("binary", rb["error"].lower())

            # preview_edit_diff & tool_edit_file uniqueness validation
            with self.assertRaises(ValueError):
                preview_edit_diff(str(root), "src/main.c", "= 1;", "= 2;")
            bad_edit = tool_edit_file(str(root), "src/main.c", "= 1;", "= 2;")
            self.assertFalse(bad_edit["success"])
            self.assertIn("2 occurrences", bad_edit["error"])

            diff = preview_edit_diff(str(root), "src/main.c", "int z = 3;", "int z = 42;")
            self.assertIn("-int z = 3;", diff)
            self.assertIn("+int z = 42;", diff)

            ok_edit = tool_edit_file(str(root), "src/main.c", "int z = 3;", "int z = 42;")
            self.assertTrue(ok_edit["success"])
            self.assertIn("+int z = 42;", ok_edit["diff"])
            self.assertIn("int z = 42;", (root / "src/main.c").read_text(encoding="utf-8"))

            # list_files skips ignored dirs
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("git", encoding="utf-8")
            (root / "build").mkdir()
            (root / "build" / "app.elf").write_text("elf", encoding="utf-8")
            listed = tool_list_files(str(root))
            listed_paths = [e["path"] for e in listed["entries"]]
            self.assertIn("src/main.c", listed_paths)
            self.assertFalse(any(".git" in p or "build" in p for p in listed_paths))

            # search_files
            search = tool_search_files(str(root), r"int z = \d+", file_glob="*.c")
            self.assertEqual(search["count"], 1)
            self.assertEqual(search["lines"][0], "src/main.c:3: int z = 42;")

            # run_shell
            sh = tool_run_shell(str(root), f"{sys.executable} -c \"print('hello-ws')\"")
            self.assertEqual(sh["return_code"], 0)
            self.assertFalse(sh["timed_out"])
            self.assertIn("hello-ws", sh["stdout"])


class TestToolRegistryAndSecurity(unittest.TestCase):
    EXPECTED_TOOLS: dict[str, str] = {
        # 6 workspace tools
        "read_file": "read_only",
        "list_files": "read_only",
        "search_files": "read_only",
        "edit_file": "workspace_write",
        "write_file": "workspace_write",
        "run_shell": "shell",
        # 21 embedded MCU tools
        "inspect_project": "read_only",
        "build_firmware": "workspace_write",
        "generate_compile_commands": "workspace_write",
        "analyze_footprint": "read_only",
        "inspect_svd": "read_only",
        "probe_control": "hardware_state",
        "read_memory": "read_only",
        "write_memory": "hardware_danger",
        "flash_firmware": "hardware_danger",
        "inspect_peripheral": "read_only",
        "diagnose_peripheral": "read_only",
        "inspect_board": "read_only",
        "watch_variable": "read_only",
        "read_debug_log": "read_only",
        "diagnose_crash": "read_only",
        "check_architecture": "read_only",
        "scaffold_code": "workspace_write",
        "inspect_rtos": "read_only",
        "trace_target": "hardware_state",
        "capture_logic": "read_only",
        "run_hil_tests": "hardware_danger",
    }

    def test_registry_has_all_27_tools_and_no_confirm_in_danger_schemas(self) -> None:
        reg = build_default_registry()
        specs = reg.list_specs()
        self.assertEqual(len(specs), 27)
        self.assertEqual({s.name: s.risk_level for s in specs}, self.EXPECTED_TOOLS)

        schemas = {s["name"]: s for s in reg.to_llm_schemas()}
        self.assertEqual(len(schemas), 27)
        for danger_tool in ("write_memory", "flash_firmware", "run_hil_tests"):
            props = schemas[danger_tool]["parameters"].get("properties", {})
            required = schemas[danger_tool]["parameters"].get("required", [])
            self.assertNotIn("confirm", props, msg=f"{danger_tool} schema must not expose confirm")
            self.assertNotIn("confirm", required, msg=f"{danger_tool} required must not expose confirm")

        # Ensure registry AST does not import server or fastmcp
        reg_text = (Path(__file__).resolve().parents[1] / "src/mcu_harness/agent/registry.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(reg_text)
        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
        self.assertFalse(any("fastmcp" in m for m in imported_modules), msg=f"Unexpected import: {imported_modules}")
        self.assertFalse(
            any(m == "mcu_harness.server" or m.endswith(".server") for m in imported_modules),
            msg=f"Unexpected import: {imported_modules}",
        )

    def test_interlock_confirmation_enforced_over_llm_args(self) -> None:
        reg = build_default_registry()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fw = root / "app.hex"
            fw.write_text(":00000001FF\n", encoding="utf-8")

            pm = ProbeManager()
            probe = MockProbe(device="STM32H563ZI")
            probe.connect()
            pm.set_probe(probe)
            guard = SafetyGuard()
            rtt = RttLogCollector(pm)
            uart = UartLogCollector()

            # Even if LLM hallucinates confirm=True in args, confirmed_by_interlock=False MUST block!
            ctx_unconfirmed = ToolContext(
                project_dir=str(root),
                probe_manager=pm,
                rtt_collector=rtt,
                uart_collector=uart,
                safety_guard=guard,
                confirmed_by_interlock=False,
            )

            wm_spec = reg.get("write_memory")
            fl_spec = reg.get("flash_firmware")
            rm_spec = reg.get("read_memory")
            self.assertIsNotNone(wm_spec)
            self.assertIsNotNone(fl_spec)
            self.assertIsNotNone(rm_spec)
            assert wm_spec is not None and fl_spec is not None and rm_spec is not None

            res_wm_blocked = wm_spec.handler(
                {"address": "0x40020000", "values": [1], "confirm": True},
                ctx_unconfirmed,
            )
            self.assertFalse(res_wm_blocked["allowed"])

            res_fl_blocked = fl_spec.handler(
                {"firmware_path": str(fw), "confirm": True},
                ctx_unconfirmed,
            )
            self.assertFalse(res_fl_blocked["allowed"])

            # When confirmed_by_interlock=True, write_memory & flash_firmware succeed on MockProbe
            ctx_confirmed = ToolContext(
                project_dir=str(root),
                probe_manager=pm,
                rtt_collector=rtt,
                uart_collector=uart,
                safety_guard=guard,
                confirmed_by_interlock=True,
            )
            res_wm_ok = wm_spec.handler({"address": "0x40020000", "values": ["0x12345678"]}, ctx_confirmed)
            self.assertTrue(res_wm_ok["allowed"])
            res_rm = rm_spec.handler({"address": "0x40020000", "count": 1}, ctx_confirmed)
            self.assertEqual(res_rm["values"], [0x12345678])

            res_fl_ok = fl_spec.handler({"firmware_path": str(fw)}, ctx_confirmed)
            self.assertTrue(res_fl_ok["allowed"])


if __name__ == "__main__":
    unittest.main()
