# MCU Agent Harness

把 **编译器、调试探针、串口/RTT、CMSIS-SVD** 接入大模型的嵌入式调试框架。
设计蓝图见 [docs/spec.md](docs/spec.md)。

## 目录结构

```
MCU_harness/
├── pyproject.toml
├── src/mcu_harness/
│   ├── __init__.py
│   ├── server.py              # ✅ MCP Server 入口（FastMCP，stdio 协议）
│   ├── build/                 # ✅ 无头构建模块（CMake + Makefile）
│   │   ├── diagnostics.py     #    编译输出解析：GCC / ld / CMake / Make / size
│   │   ├── runner.py          #    自动识别 CMake / Makefile 并执行构建
│   │   └── cli.py             #    命令行入口，脱离 AI 单独测试
│   ├── svd/                   # ✅ CMSIS-SVD 解析与寄存器位域解码
│   │   ├── models.py          #    Device / Peripheral / Register / Field 模型
│   │   ├── parser.py          #    解析 .svd/.svd.gz（derivedFrom / dim / cluster）
│   │   ├── decoder.py         #    按位域解码十六进制寄存器值
│   │   └── cli.py             #    命令行入口，离线查表/解码
│   ├── probe/                 # ✅ 调试探针抽象 + J-Link 后端 + 现场外设解码
│   │   ├── base.py            #    DebugProbe 抽象接口与 ProbeInfo
│   │   ├── jlink.py           #    基于 pylink-square 的 SEGGER J-Link 实现
│   │   ├── mock.py            #    内存模拟探针（无硬件测试用）
│   │   ├── manager.py         #    长驻会话管理器（线程安全）
│   │   ├── peripheral.py      #    SVD + DebugProbe 联合解码外设寄存器
│   │   └── cli.py             #    命令行入口，验证 J-Link 连板与读寄存器
│   ├── log/                   # ✅ 非阻塞调试日志模块（SEGGER RTT）
│   │   ├── ring_buffer.py     #    线程安全环形缓冲（增量 UTF-8 / seq / grep -C）
│   │   ├── rtt_collector.py   #    后台线程周期轮询 RTT 上行通道（支持 .elf 自动定位）
│   │   └── cli.py             #    命令行入口，单次拉取或 --follow 跟踪
│   └── elf/                   # ✅ 纯 Python ELF 符号表解析（定位 _SEGGER_RTT 等）
│       └── symbols.py
├── examples/
│   ├── demo_fw/               # CMake 示例工程（主机 gcc 可编译）
│   └── demo_make_fw/          # Makefile 示例工程（主机 gcc 可编译）
└── tests/                     # pytest 单元/集成测试
```

## 快速开始（Linux）

```bash
# 运行全部测试（含 MCP Server 端到端测试）
.venv/bin/python -m pytest -v

# 启动 MCP Server（stdio 模式，通常由 Antigravity / MCP 客户端自动拉起）
PYTHONPATH=src .venv/bin/python -m mcu_harness.server

# 编译示例工程（文本报告，给人/AI 看）
PYTHONPATH=src .venv/bin/python -m mcu_harness.build.cli examples/demo_fw

# 故意注入编译错误，看降噪效果
PYTHONPATH=src .venv/bin/python -m mcu_harness.build.cli examples/demo_fw --reconfigure -D DEMO_INJECT_ERROR=ON

# JSON 输出（程序/MCP 使用的格式）
PYTHONPATH=src .venv/bin/python -m mcu_harness.build.cli examples/demo_fw --json

# 编译 Makefile 示例工程（自动识别 Makefile 并解析 size 输出）
PYTHONPATH=src .venv/bin/python -m mcu_harness.build.cli examples/demo_make_fw -j 4

# 编译真实 STM32H563 Makefile 工程
PYTHONPATH=src .venv/bin/python -m mcu_harness.build.cli ~/你的STM32工程路径 -j 8

# 只解析已有日志
PYTHONPATH=src .venv/bin/python -m mcu_harness.build.cli --parse-log tests/fixtures/arm_link_error.log

# 离线查看 SVD 外设 / 解码寄存器值
PYTHONPATH=src .venv/bin/python -m mcu_harness.svd.cli --svd tests/fixtures/stm32h563_mini.svd --list
PYTHONPATH=src .venv/bin/python -m mcu_harness.svd.cli --svd tests/fixtures/stm32h563_mini.svd GPIOA MODER 0x00000400

# 无板卡模拟探针自测 / 实机 J-Link 测试（STM32H563）
PYTHONPATH=src .venv/bin/python -m mcu_harness.probe.cli --mock info
PYTHONPATH=src .venv/bin/python -m mcu_harness.probe.cli --mock inspect --svd tests/fixtures/stm32h563_mini.svd GPIOA MODER
PYTHONPATH=src .venv/bin/python -m mcu_harness.probe.cli info
PYTHONPATH=src .venv/bin/python -m mcu_harness.probe.cli read 0xE000ED00 --count 1
PYTHONPATH=src .venv/bin/python -m mcu_harness.probe.cli inspect --svd /你的/STM32H563.svd GPIOA MODER

# 非阻塞 RTT 日志采集自测 / 实机读取（传入 --elf 可秒定位 _SEGGER_RTT 地址）
PYTHONPATH=src .venv/bin/python -m mcu_harness.log.cli --mock
PYTHONPATH=src .venv/bin/python -m mcu_harness.log.cli --elf /你的/firmware.elf --tail 50
PYTHONPATH=src .venv/bin/python -m mcu_harness.log.cli --elf /你的/firmware.elf --follow
```

## 接入 MCP 客户端（如 Antigravity）

在 MCP 配置中添加如下 stdio 服务：

```json
{
  "mcpServers": {
    "mcu-harness": {
      "command": "/home/Srakoul/Downloads/MCU_harness/.venv/bin/python",
      "args": ["-m", "mcu_harness.server"],
      "env": {
        "PYTHONPATH": "/home/Srakoul/Downloads/MCU_harness/src",
        "MCU_HARNESS_SVD_PATH": "/你的/STM32H563.svd",
        "MCU_HARNESS_ELF_PATH": "/你的/firmware.elf",
        "MCU_HARNESS_DEVICE": "STM32H563ZI"
      }
    }
  }
}
```

## 目标硬件

STM32H563（Cortex-M33，当前未启用 TrustZone）+ SEGGER J-Link + SEGGER RTT（人工调试用 Ozone）。
选型理由与注意事项见 [docs/hardware_target.md](docs/hardware_target.md)。

## 路线图

- [x] Phase 1-1 `build_firmware` 核心逻辑（CMake）
- [x] Phase 1-2 MCP Server 封装（FastMCP）
- [x] Phase 1-2b 为 `build_firmware` 补齐 Makefile（`make`）工程支持
- [x] Phase 1-3a CMSIS-SVD 解析与寄存器位域解码层（`inspect_svd`，离线查表 + 十六进制解码）
- [x] Phase 1-3b `DebugProbe` 抽象 + J-Link 后端（pylink-square）
- [x] Phase 1-4 `inspect_peripheral`（SVD + J-Link 实时读硬件寄存器）
- [x] Phase 1-5 `read_debug_log`（SEGGER RTT 后台环形缓冲）
- [ ] Phase 2 J-Link GDB Server + GDB/MI 会话管理、`diagnose_crash`（Armv8-M）
- [ ] Phase 3 HITL 安全护栏（含 H5 Product State 保护）、闭环编排
