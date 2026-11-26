"""独立第三方 MCU Agent Harness 核心引擎包（LLM 路由、27 工具注册、HITL 硬件互锁与 ReAct 运行时）。"""

from .context import (
    META_COMMANDS,
    PLAYBOOKS,
    PlaybookSpec,
    SlashResolution,
    build_hardware_status_card,
    build_system_prompt,
    expand_slash_command,
    format_slash_help,
)
from .interlock import (
    ApprovalDecision,
    ApprovalRequest,
    HarnessInterlock,
    InterlockOutcome,
    is_safe_readonly_shell_command,
)
from .llm import (
    AnthropicProvider,
    GeminiProvider,
    LlmError,
    LlmProvider,
    LlmResponse,
    MockLlmProvider,
    OpenAICompatProvider,
    ToolCall,
    create_provider,
)
from .registry import (
    ToolContext,
    ToolRegistry,
    ToolSpec,
    build_default_registry,
)
from .rpc import RpcBridge
from .runtime import (
    AgentRuntime,
    HarnessEvent,
    TurnResult,
    list_sessions,
    load_session,
)

__all__ = [
    "AgentRuntime",
    "AnthropicProvider",
    "ApprovalDecision",
    "ApprovalRequest",
    "GeminiProvider",
    "HarnessEvent",
    "HarnessInterlock",
    "InterlockOutcome",
    "LlmError",
    "LlmProvider",
    "LlmResponse",
    "META_COMMANDS",
    "MockLlmProvider",
    "OpenAICompatProvider",
    "PLAYBOOKS",
    "PlaybookSpec",
    "RpcBridge",
    "SlashResolution",
    "ToolCall",
    "ToolContext",
    "ToolRegistry",
    "ToolSpec",
    "TurnResult",
    "build_default_registry",
    "build_hardware_status_card",
    "build_system_prompt",
    "create_provider",
    "expand_slash_command",
    "format_slash_help",
    "is_safe_readonly_shell_command",
    "list_sessions",
    "load_session",
]
