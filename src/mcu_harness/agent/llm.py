"""纯标准库多厂商大模型路由层（OpenAI 兼容 / Anthropic / Gemini / Mock）。

职责：
    1. 定义统一的 `ToolCall`、`LlmResponse`、`LlmError` 与 `LlmProvider` 抽象接口。
    2. 实现 4 个纯标准库（`urllib.request`）Provider：
       - `OpenAICompatProvider`：支持 openai / deepseek / qwen / openrouter / ollama / vllm，
         同时支持普通 JSON 响应与 `on_text_delta` 触发时的 SSE 流式解析及分片 `tool_calls` 拼装。
       - `AnthropicProvider`：适配 `/v1/messages`、`input_schema`、`tool_use` 与 `tool_result`。
       - `GeminiProvider`：适配 `/v1beta/models/<model>:generateContent`、`systemInstruction`、
         `functionDeclarations`、`functionCall` 与 `functionResponse`。
       - `MockLlmProvider`：支持预设响应序列或回调函数，记录 `call_history` 供单测与演示使用。
    3. `create_provider()` 按「显式参数 > 工程 mcu-harness.toml [agent] > 用户 ~/.config/mcu-harness/agent.toml [agent]
       > 环境变量 MCU_AGENT_* > 厂商默认配置」解析并实例化 Provider。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]


class LlmError(RuntimeError):
    """大模型调用、配置解析或响应解析异常。"""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": dict(self.arguments),
        }


@dataclass
class LlmResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
    provider: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "finish_reason": self.finish_reason,
            "usage": dict(self.usage),
            "model": self.model,
            "provider": self.provider,
        }


class LlmProvider(ABC):
    """统一大模型调用抽象基类。"""

    provider_name: str
    model: str

    @abstractmethod
    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> LlmResponse:
        """执行一次补全请求并返回统一格式的 `LlmResponse`。"""


# ---------------------------------------------------------------------------
# 内部消息与工具 Schema 规范化辅助函数
# ---------------------------------------------------------------------------


def _extract_tool_call_fields(tc: ToolCall | dict[str, Any], fallback_id: str) -> tuple[str, str, dict[str, Any]]:
    if isinstance(tc, ToolCall):
        return tc.id or fallback_id, tc.name, dict(tc.arguments)
    tc_id = str(tc.get("id") or fallback_id)
    name = str(tc.get("name") or "")
    raw_args = tc.get("arguments")
    if raw_args is None:
        args: dict[str, Any] = {}
    elif isinstance(raw_args, dict):
        args = dict(raw_args)
    elif isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args) if raw_args.strip() else {}
        except json.JSONDecodeError:
            parsed = {}
        args = parsed if isinstance(parsed, dict) else {"raw": parsed}
    else:
        args = {"value": raw_args}
    return tc_id, name, args


def _normalize_tool_schema(tool: dict[str, Any]) -> dict[str, Any]:
    if "function" in tool and isinstance(tool["function"], dict):
        fn = tool["function"]
        return {
            "name": str(fn.get("name") or ""),
            "description": str(fn.get("description") or ""),
            "parameters": dict(fn.get("parameters") or {"type": "object", "properties": {}}),
        }
    return {
        "name": str(tool.get("name") or ""),
        "description": str(tool.get("description") or ""),
        "parameters": dict(tool.get("parameters") or {"type": "object", "properties": {}}),
    }


def _stringify_tool_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


def _safe_int_dict(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for k, v in raw.items():
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out[str(k)] = int(v)
    return out


def _parse_json_arguments(raw_args: Any, *, provider_name: str, tool_name: str) -> dict[str, Any]:
    if raw_args is None:
        return {}
    if isinstance(raw_args, dict):
        return dict(raw_args)
    if isinstance(raw_args, str):
        stripped = raw_args.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise LlmError(
                f"{provider_name} returned invalid JSON arguments for tool {tool_name!r}: {exc} ({stripped[:200]})"
            ) from exc
        if not isinstance(parsed, dict):
            raise LlmError(
                f"{provider_name} returned non-object JSON arguments for tool {tool_name!r}: {type(parsed).__name__}"
            )
        return parsed
    raise LlmError(f"{provider_name} returned unsupported tool arguments type: {type(raw_args).__name__}")


def _http_post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    timeout_s: float,
    provider_name: str,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            pass
        detail = err_body or str(exc.reason)
        raise LlmError(f"{provider_name} HTTP {exc.code} from {url}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LlmError(f"{provider_name} request failed ({url}): {exc}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LlmError(f"{provider_name} returned invalid JSON response: {exc} ({raw[:200]})") from exc
    if not isinstance(parsed, dict):
        raise LlmError(f"{provider_name} returned non-object JSON response: {type(parsed).__name__}")
    return parsed


# ---------------------------------------------------------------------------
# 1. OpenAICompatProvider
# ---------------------------------------------------------------------------


OPENAI_COMPAT_PRESETS: dict[str, dict[str, Any]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o",
        "require_key": True,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
        "require_key": True,
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
        "default_model": "qwen-plus",
        "require_key": True,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "default_model": "anthropic/claude-3.7-sonnet",
        "require_key": True,
    },
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key_env": "OLLAMA_API_KEY",
        "default_model": "qwen2.5-coder:14b",
        "require_key": False,
    },
    "vllm": {
        "base_url": "http://127.0.0.1:8000/v1",
        "api_key_env": "VLLM_API_KEY",
        "default_model": "default",
        "require_key": False,
    },
}


class OpenAICompatProvider(LlmProvider):
    """OpenAI Chat Completions 兼容路由（支持非流式 JSON 与 SSE 流式分片解析）。"""

    def __init__(
        self,
        *,
        provider_name: str = "openai",
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        preset = OPENAI_COMPAT_PRESETS.get(provider_name.lower(), OPENAI_COMPAT_PRESETS["openai"])
        self.provider_name = provider_name.lower()
        self.model = model or str(preset["default_model"])
        self.api_key = api_key or ""
        self.base_url = (base_url or str(preset["base_url"])).rstrip("/")
        self.timeout_s = float(timeout_s)

    def _build_messages(self, messages: list[dict[str, Any]], system: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if system:
            out.append({"role": "system", "content": system})
        for msg in messages:
            role = str(msg.get("role") or "user")
            if role == "system":
                out.append({"role": "system", "content": str(msg.get("content") or "")})
            elif role == "user":
                out.append({"role": "user", "content": str(msg.get("content") or "")})
            elif role == "assistant":
                item: dict[str, Any] = {"role": "assistant"}
                content = msg.get("content")
                raw_tcs = msg.get("tool_calls") or []
                if raw_tcs:
                    item["content"] = content if content not in (None, "") else None
                    tcs_payload: list[dict[str, Any]] = []
                    for idx, tc in enumerate(raw_tcs):
                        tc_id, tc_name, tc_args = _extract_tool_call_fields(tc, f"call_{idx}")
                        tcs_payload.append(
                            {
                                "id": tc_id,
                                "type": "function",
                                "function": {
                                    "name": tc_name,
                                    "arguments": json.dumps(tc_args, ensure_ascii=False),
                                },
                            }
                        )
                    item["tool_calls"] = tcs_payload
                else:
                    item["content"] = str(content or "")
                out.append(item)
            elif role == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(msg.get("tool_call_id") or ""),
                        "content": _stringify_tool_content(msg.get("content")),
                    }
                )
            else:
                out.append({"role": role, "content": _stringify_tool_content(msg.get("content"))})
        return out

    def _parse_json_response(self, data: dict[str, Any]) -> LlmResponse:
        choices = data.get("choices") or []
        if not choices or not isinstance(choices, list):
            raise LlmError(f"{self.provider_name} response missing 'choices': {data}")
        choice = choices[0] or {}
        message = choice.get("message") or {}
        text = str(message.get("content") or "")

        tool_calls: list[ToolCall] = []
        for idx, raw_tc in enumerate(message.get("tool_calls") or []):
            fn = raw_tc.get("function") or {}
            tc_name = str(fn.get("name") or "")
            tc_id = str(raw_tc.get("id") or f"call_{idx}")
            args = _parse_json_arguments(fn.get("arguments"), provider_name=self.provider_name, tool_name=tc_name)
            tool_calls.append(ToolCall(id=tc_id, name=tc_name, arguments=args))

        finish_reason = str(choice.get("finish_reason") or ("tool_calls" if tool_calls else "stop"))
        usage = _safe_int_dict(data.get("usage"))
        model_name = str(data.get("model") or self.model)
        return LlmResponse(
            text=text,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            model=model_name,
            provider=self.provider_name,
        )

    def _complete_streaming(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        on_text_delta: Callable[[str], None],
    ) -> LlmResponse:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")

        text_parts: list[str] = []
        tool_call_acc: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        usage: dict[str, int] = {}
        model_name = self.model

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                content_type = (resp.headers.get("Content-Type") or "").lower()
                if "application/json" in content_type and "text/event-stream" not in content_type:
                    raw = resp.read().decode("utf-8", errors="replace")
                    parsed = json.loads(raw)
                    result = self._parse_json_response(parsed)
                    if result.text:
                        on_text_delta(result.text)
                    return result

                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError as exc:
                        raise LlmError(f"{self.provider_name} SSE chunk invalid JSON: {exc} ({data_str[:200]})") from exc

                    if chunk.get("model"):
                        model_name = str(chunk["model"])
                    if isinstance(chunk.get("usage"), dict):
                        usage = _safe_int_dict(chunk["usage"])

                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0] or {}
                    if choice.get("finish_reason") is not None:
                        finish_reason = str(choice["finish_reason"])

                    delta = choice.get("delta") or {}
                    delta_content = delta.get("content")
                    if isinstance(delta_content, str) and delta_content:
                        text_parts.append(delta_content)
                        on_text_delta(delta_content)

                    for tc_delta in delta.get("tool_calls") or []:
                        idx = int(tc_delta.get("index", 0))
                        slot = tool_call_acc.setdefault(
                            idx,
                            {"id": "", "name": "", "arguments_parts": []},
                        )
                        if tc_delta.get("id"):
                            slot["id"] = str(tc_delta["id"])
                        fn = tc_delta.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += str(fn["name"])
                        if fn.get("arguments") is not None:
                            slot["arguments_parts"].append(str(fn["arguments"]))
        except urllib.error.HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="replace").strip()
            except Exception:
                pass
            raise LlmError(f"{self.provider_name} HTTP {exc.code} from {url}: {err_body or exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LlmError(f"{self.provider_name} streaming request failed ({url}): {exc}") from exc

        tool_calls: list[ToolCall] = []
        for idx in sorted(tool_call_acc.keys()):
            slot = tool_call_acc[idx]
            tc_name = slot["name"]
            tc_id = slot["id"] or f"call_{idx}"
            raw_args = "".join(slot["arguments_parts"])
            args = _parse_json_arguments(raw_args, provider_name=self.provider_name, tool_name=tc_name)
            tool_calls.append(ToolCall(id=tc_id, name=tc_name, arguments=args))

        resolved_finish = finish_reason or ("tool_calls" if tool_calls else "stop")
        return LlmResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=resolved_finish,
            usage=usage,
            model=model_name,
            provider=self.provider_name,
        )

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> LlmResponse:
        url = f"{self.base_url}/chat/completions"
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json" if on_text_delta else "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._build_messages(messages, system),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": _normalize_tool_schema(t),
                }
                for t in tools
            ]
        if on_text_delta is not None:
            payload["stream"] = True
            return self._complete_streaming(url, payload, headers, on_text_delta)

        data = _http_post_json(url, payload, headers, timeout_s=self.timeout_s, provider_name=self.provider_name)
        return self._parse_json_response(data)


# ---------------------------------------------------------------------------
# 2. AnthropicProvider
# ---------------------------------------------------------------------------


class AnthropicProvider(LlmProvider):
    """Anthropic Messages API 路由（`/v1/messages`）。"""

    DEFAULT_BASE_URL = "https://api.anthropic.com"
    DEFAULT_MODEL = "claude-sonnet-4-20250514"
    ANTHROPIC_VERSION = "2023-06-01"

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        self.provider_name = "anthropic"
        self.model = model or self.DEFAULT_MODEL
        self.api_key = api_key or ""
        raw_base = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        if raw_base.endswith("/v1"):
            raw_base = raw_base[:-3]
        self.base_url = raw_base
        self.timeout_s = float(timeout_s)

    def _build_messages(self, messages: list[dict[str, Any]], system: str) -> tuple[str, list[dict[str, Any]]]:
        sys_parts: list[str] = [system] if system else []
        out: list[dict[str, Any]] = []

        def _append_user_blocks(blocks: list[dict[str, Any]]) -> None:
            if out and out[-1]["role"] == "user":
                existing = out[-1]["content"]
                if isinstance(existing, str):
                    out[-1]["content"] = [{"type": "text", "text": existing}] + blocks
                elif isinstance(existing, list):
                    existing.extend(blocks)
            else:
                out.append({"role": "user", "content": blocks})

        for msg in messages:
            role = str(msg.get("role") or "user")
            if role == "system":
                txt = str(msg.get("content") or "")
                if txt:
                    sys_parts.append(txt)
            elif role == "user":
                txt = str(msg.get("content") or "")
                if out and out[-1]["role"] == "user":
                    _append_user_blocks([{"type": "text", "text": txt}])
                else:
                    out.append({"role": "user", "content": txt})
            elif role == "assistant":
                blocks: list[dict[str, Any]] = []
                txt = msg.get("content")
                if isinstance(txt, str) and txt:
                    blocks.append({"type": "text", "text": txt})
                for idx, tc in enumerate(msg.get("tool_calls") or []):
                    tc_id, tc_name, tc_args = _extract_tool_call_fields(tc, f"toolu_{idx}")
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc_id,
                            "name": tc_name,
                            "input": tc_args,
                        }
                    )
                if not blocks:
                    blocks.append({"type": "text", "text": ""})
                out.append({"role": "assistant", "content": blocks})
            elif role == "tool":
                _append_user_blocks(
                    [
                        {
                            "type": "tool_result",
                            "tool_use_id": str(msg.get("tool_call_id") or ""),
                            "content": _stringify_tool_content(msg.get("content")),
                        }
                    ]
                )
            else:
                out.append({"role": role, "content": _stringify_tool_content(msg.get("content"))})

        return "\n\n".join(p for p in sys_parts if p), out

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> LlmResponse:
        url = f"{self.base_url}/v1/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self.ANTHROPIC_VERSION,
        }
        merged_system, anth_messages = self._build_messages(messages, system)
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": anth_messages,
        }
        if merged_system:
            payload["system"] = merged_system
        if tools:
            anth_tools: list[dict[str, Any]] = []
            for t in tools:
                norm = _normalize_tool_schema(t)
                anth_tools.append(
                    {
                        "name": norm["name"],
                        "description": norm["description"],
                        "input_schema": norm["parameters"],
                    }
                )
            payload["tools"] = anth_tools

        data = _http_post_json(url, payload, headers, timeout_s=self.timeout_s, provider_name=self.provider_name)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for idx, block in enumerate(data.get("content") or []):
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text_parts.append(str(block.get("text") or ""))
            elif btype == "tool_use":
                tc_name = str(block.get("name") or "")
                tc_id = str(block.get("id") or f"toolu_{idx}")
                raw_input = block.get("input")
                args = dict(raw_input) if isinstance(raw_input, dict) else {}
                tool_calls.append(ToolCall(id=tc_id, name=tc_name, arguments=args))

        text = "".join(text_parts)
        if on_text_delta is not None and text:
            on_text_delta(text)

        raw_stop = str(data.get("stop_reason") or ("tool_use" if tool_calls else "end_turn"))
        finish_map = {
            "end_turn": "stop",
            "stop_sequence": "stop",
            "tool_use": "tool_calls",
            "max_tokens": "length",
        }
        finish_reason = finish_map.get(raw_stop, raw_stop)

        raw_usage = data.get("usage") or {}
        usage: dict[str, int] = {}
        if isinstance(raw_usage, dict):
            in_tok = raw_usage.get("input_tokens")
            out_tok = raw_usage.get("output_tokens")
            if isinstance(in_tok, int):
                usage["prompt_tokens"] = in_tok
            if isinstance(out_tok, int):
                usage["completion_tokens"] = out_tok
            if "prompt_tokens" in usage and "completion_tokens" in usage:
                usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]

        return LlmResponse(
            text=text,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            model=str(data.get("model") or self.model),
            provider=self.provider_name,
        )


# ---------------------------------------------------------------------------
# 3. GeminiProvider
# ---------------------------------------------------------------------------


class GeminiProvider(LlmProvider):
    """Google Gemini GenerateContent API 路由（`/v1beta/models/<model>:generateContent`）。"""

    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
    DEFAULT_MODEL = "gemini-2.5-flash"

    def __init__(
        self,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_s: float = 120.0,
    ) -> None:
        self.provider_name = "gemini"
        self.model = model or self.DEFAULT_MODEL
        self.api_key = api_key or ""
        raw_base = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        if raw_base.endswith("/v1beta"):
            raw_base = raw_base[:-7]
        self.base_url = raw_base
        self.timeout_s = float(timeout_s)

    def _build_contents(self, messages: list[dict[str, Any]], system: str) -> tuple[str, list[dict[str, Any]]]:
        sys_parts: list[str] = [system] if system else []
        contents: list[dict[str, Any]] = []

        def _append_user_parts(parts: list[dict[str, Any]]) -> None:
            if contents and contents[-1]["role"] == "user":
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": "user", "parts": parts})

        for msg in messages:
            role = str(msg.get("role") or "user")
            if role == "system":
                txt = str(msg.get("content") or "")
                if txt:
                    sys_parts.append(txt)
            elif role == "user":
                _append_user_parts([{"text": str(msg.get("content") or "")}])
            elif role == "assistant":
                parts: list[dict[str, Any]] = []
                txt = msg.get("content")
                if isinstance(txt, str) and txt:
                    parts.append({"text": txt})
                for idx, tc in enumerate(msg.get("tool_calls") or []):
                    _, tc_name, tc_args = _extract_tool_call_fields(tc, f"gemini_call_{idx}")
                    parts.append({"functionCall": {"name": tc_name, "args": tc_args}})
                if not parts:
                    parts.append({"text": ""})
                contents.append({"role": "model", "parts": parts})
            elif role == "tool":
                raw_content = msg.get("content")
                if isinstance(raw_content, dict):
                    resp_obj = raw_content
                elif isinstance(raw_content, str):
                    try:
                        parsed = json.loads(raw_content)
                        resp_obj = parsed if isinstance(parsed, dict) else {"result": parsed}
                    except json.JSONDecodeError:
                        resp_obj = {"result": raw_content}
                else:
                    resp_obj = {"result": raw_content}
                _append_user_parts(
                    [
                        {
                            "functionResponse": {
                                "name": str(msg.get("name") or ""),
                                "response": resp_obj,
                            }
                        }
                    ]
                )
            else:
                _append_user_parts([{"text": _stringify_tool_content(msg.get("content"))}])

        return "\n\n".join(p for p in sys_parts if p), contents

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> LlmResponse:
        url = f"{self.base_url}/v1beta/models/{self.model}:generateContent"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }
        merged_system, contents = self._build_contents(messages, system)
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if merged_system:
            payload["systemInstruction"] = {"parts": [{"text": merged_system}]}
        if tools:
            decls = [_normalize_tool_schema(t) for t in tools]
            payload["tools"] = [{"functionDeclarations": decls}]

        data = _http_post_json(url, payload, headers, timeout_s=self.timeout_s, provider_name=self.provider_name)

        candidates = data.get("candidates") or []
        if not candidates or not isinstance(candidates, list):
            raise LlmError(f"gemini response missing 'candidates': {data}")
        cand = candidates[0] or {}
        content = cand.get("content") or {}
        parts = content.get("parts") or []

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for idx, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            if "text" in part and part["text"] is not None:
                text_parts.append(str(part["text"]))
            if "functionCall" in part and isinstance(part["functionCall"], dict):
                fc = part["functionCall"]
                fc_name = str(fc.get("name") or "")
                fc_id = str(fc.get("id") or f"gemini_call_{idx}")
                raw_args = fc.get("args")
                args = dict(raw_args) if isinstance(raw_args, dict) else {}
                tool_calls.append(ToolCall(id=fc_id, name=fc_name, arguments=args))

        text = "".join(text_parts)
        if on_text_delta is not None and text:
            on_text_delta(text)

        raw_finish = str(cand.get("finishReason") or "STOP").upper()
        if tool_calls:
            finish_reason = "tool_calls"
        elif raw_finish == "STOP":
            finish_reason = "stop"
        elif raw_finish == "MAX_TOKENS":
            finish_reason = "length"
        else:
            finish_reason = raw_finish.lower()

        raw_usage = data.get("usageMetadata") or {}
        usage: dict[str, int] = {}
        if isinstance(raw_usage, dict):
            if isinstance(raw_usage.get("promptTokenCount"), int):
                usage["prompt_tokens"] = raw_usage["promptTokenCount"]
            if isinstance(raw_usage.get("candidatesTokenCount"), int):
                usage["completion_tokens"] = raw_usage["candidatesTokenCount"]
            if isinstance(raw_usage.get("totalTokenCount"), int):
                usage["total_tokens"] = raw_usage["totalTokenCount"]

        return LlmResponse(
            text=text,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            model=str(data.get("modelVersion") or self.model),
            provider=self.provider_name,
        )


# ---------------------------------------------------------------------------
# 4. MockLlmProvider
# ---------------------------------------------------------------------------


class MockLlmProvider(LlmProvider):
    """离线单测与 `--mock-llm` 演示用 Mock 大模型 Provider。"""

    def __init__(
        self,
        responses: (
            list[LlmResponse]
            | Callable[[list[dict[str, Any]], str, list[dict[str, Any]] | None], LlmResponse]
            | None
        ) = None,
        *,
        model: str = "mock-model",
        provider_name: str = "mock",
    ) -> None:
        self.provider_name = provider_name
        self.model = model
        self._callback = responses if callable(responses) else None
        self._queue: deque[LlmResponse] = deque(responses) if isinstance(responses, list) else deque()
        self._has_explicit_queue = isinstance(responses, list)
        self.call_history: list[dict[str, Any]] = []

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        on_text_delta: Callable[[str], None] | None = None,
    ) -> LlmResponse:
        self.call_history.append(
            {
                "messages": list(messages),
                "system": system,
                "tools": list(tools) if tools is not None else None,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )

        if self._callback is not None:
            resp = self._callback(messages, system, tools)
        elif self._has_explicit_queue:
            if not self._queue:
                raise LlmError("MockLlmProvider response queue exhausted")
            resp = self._queue.popleft()
        else:
            resp = LlmResponse(
                text="Mock response",
                finish_reason="stop",
                model=self.model,
                provider=self.provider_name,
            )

        if not resp.model:
            resp.model = self.model
        if not resp.provider:
            resp.provider = self.provider_name
        if on_text_delta is not None and resp.text:
            on_text_delta(resp.text)
        return resp


# ---------------------------------------------------------------------------
# 工厂函数与配置优先级解析
# ---------------------------------------------------------------------------


def _load_agent_toml_section(toml_path: Path) -> dict[str, Any]:
    if not toml_path.is_file():
        return {}
    try:
        parsed = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise LlmError(f"Failed to parse {toml_path}: {exc}") from exc
    sec = parsed.get("agent", {})
    if sec is None:
        return {}
    if not isinstance(sec, dict):
        raise LlmError(f"[agent] section in {toml_path} must be a table")
    return sec


def _pick_first_str(*candidates: Any) -> str | None:
    for item in candidates:
        if item is not None:
            s = str(item).strip()
            if s:
                return s
    return None


def create_provider(
    provider: str | None = None,
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout_s: float = 120.0,
    project_dir: str | None = None,
) -> LlmProvider:
    """按优先级解析配置并创建对应的 `LlmProvider` 实例。

    优先级：
        显式入参 > `<project_dir>/mcu-harness.toml` `[agent]` > `~/.config/mcu-harness/agent.toml` `[agent]`
        > 环境变量 (`MCU_AGENT_PROVIDER`, `MCU_AGENT_MODEL`, `MCU_AGENT_BASE_URL`, `MCU_AGENT_API_KEY`)
        > 厂商默认 preset（及厂商标准 API Key 环境变量）。
    """
    proj_sec: dict[str, Any] = {}
    if project_dir:
        proj_root = Path(project_dir).expanduser().resolve()
        proj_toml = proj_root / "mcu-harness.toml"
        if not proj_toml.is_file():
            alt_toml = proj_root / ".mcu-harness.toml"
            if alt_toml.is_file():
                proj_toml = alt_toml
        proj_sec = _load_agent_toml_section(proj_toml)

    user_toml = Path.home() / ".config" / "mcu-harness" / "agent.toml"
    user_sec = _load_agent_toml_section(user_toml)

    eff_provider = _pick_first_str(
        provider,
        proj_sec.get("provider"),
        user_sec.get("provider"),
        os.environ.get("MCU_AGENT_PROVIDER"),
        "openai",
    )
    assert eff_provider is not None
    eff_provider = eff_provider.lower()

    eff_model = _pick_first_str(
        model,
        proj_sec.get("model"),
        user_sec.get("model"),
        os.environ.get("MCU_AGENT_MODEL"),
    )
    eff_base_url = _pick_first_str(
        base_url,
        proj_sec.get("base_url"),
        user_sec.get("base_url"),
        os.environ.get("MCU_AGENT_BASE_URL"),
    )
    eff_api_key = _pick_first_str(
        api_key,
        proj_sec.get("api_key"),
        user_sec.get("api_key"),
        os.environ.get("MCU_AGENT_API_KEY"),
    )

    if eff_provider == "mock":
        return MockLlmProvider(model=eff_model or "mock-model")

    if eff_provider == "anthropic":
        if not eff_api_key:
            eff_api_key = _pick_first_str(os.environ.get("ANTHROPIC_API_KEY"))
        if not eff_api_key:
            raise LlmError(
                "Missing API key for provider 'anthropic': set ANTHROPIC_API_KEY, MCU_AGENT_API_KEY, or [agent].api_key"
            )
        return AnthropicProvider(
            model=eff_model,
            api_key=eff_api_key,
            base_url=eff_base_url,
            timeout_s=timeout_s,
        )

    if eff_provider == "gemini":
        if not eff_api_key:
            eff_api_key = _pick_first_str(os.environ.get("GEMINI_API_KEY"))
        if not eff_api_key:
            raise LlmError(
                "Missing API key for provider 'gemini': set GEMINI_API_KEY, MCU_AGENT_API_KEY, or [agent].api_key"
            )
        return GeminiProvider(
            model=eff_model,
            api_key=eff_api_key,
            base_url=eff_base_url,
            timeout_s=timeout_s,
        )

    if eff_provider in OPENAI_COMPAT_PRESETS:
        preset = OPENAI_COMPAT_PRESETS[eff_provider]
        env_key_name = str(preset["api_key_env"])
        if not eff_api_key:
            eff_api_key = _pick_first_str(os.environ.get(env_key_name))
        if preset["require_key"] and not eff_api_key:
            raise LlmError(
                f"Missing API key for provider {eff_provider!r}: "
                f"set {env_key_name}, MCU_AGENT_API_KEY, or [agent].api_key"
            )
        return OpenAICompatProvider(
            provider_name=eff_provider,
            model=eff_model,
            api_key=eff_api_key,
            base_url=eff_base_url,
            timeout_s=timeout_s,
        )

    supported = ", ".join(sorted([*OPENAI_COMPAT_PRESETS.keys(), "anthropic", "gemini", "mock"]))
    raise LlmError(f"Unknown LLM provider {eff_provider!r} (supported: {supported})")
