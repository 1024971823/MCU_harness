"""MCU Agent Harness.

分层约定（类比嵌入式工程）：
    mcu_harness/<模块>/    —— 纯 Python 核心逻辑，相当于“驱动层”，可单独测试，不依赖 MCP
    mcu_harness/server.py  —— MCP 工具注册，相当于“HAL 接口层”，只做薄封装（后续添加）
"""

__version__ = "0.1.0"
