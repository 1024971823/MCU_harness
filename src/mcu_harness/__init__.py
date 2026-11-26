"""MCU Agent Harness.

分层约定（类比嵌入式工程）：
    mcu_harness/<模块>/    —— 纯 Python 核心逻辑（config / build / svd / probe / safety /
                             diagnose / dwarf / rtos / trace / hil / log / elf / crash / arch），
                             相当于“驱动与中间件层”，均可脱离 MCP 独立命令行运行与单元测试
    mcu_harness/server.py  —— MCP 工具注册，相当于面向大模型的“HAL 接口层”，只做薄封装
"""

__version__ = "0.4.0"
