"""MCU Agent Harness.

分层约定（类比嵌入式工程）：
    mcu_harness/<模块>/    —— 纯 Python 核心逻辑（config / build / svd / probe / safety /
                             diagnose / dwarf / rtos / trace / hil / log / elf / crash / arch），
                             相当于“驱动与中间件层”，均可脱离 MCP 独立命令行运行与单元测试
    mcu_harness/agent/     —— 独立第三方 Agent Harness（多厂商 LLM 路由、27 工具注册、
                             不可绕过 HITL 硬件互锁、ReAct 运行时、TUI 与 NDJSON RPC 桥接）
    studio/                —— 原生 Qt 6 QML + C++20 商业级桌面 IDE（MCU Harness Studio，通过 mcu-rpc 通信）
    mcu_harness/server.py  —— MCP 工具注册，面向外部 MCP 客户端的薄封装接口
"""

__version__ = "0.5.0"
