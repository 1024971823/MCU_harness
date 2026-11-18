# MCU 调试友好型 Agent Harness 开发需求与架构汇总

> 本文档整理自前期技术沟通，旨在作为项目设计蓝图与开发输入，可直接提供给 Antigravity 等 AI 编程助手用于搭建项目脚手架与逐步开发。

## 1. 项目定位与核心目标

本项目旨在构建一套**专为嵌入式 MCU 调试优化的 Agent Harness（AI 闭环控制框架）**。

正如开源项目 [SensorsIot/Embedded-AI-Harness](https://github.com/SensorsIot/Embedded-AI-Harness) 所阐释的理念：大语言模型本身只具备推理能力，无法直接接触物理硬件；Harness 的作用就是把大模型连接到**编译器、调试探针、串口/RTT 通道与寄存器描述文件**，形成完整的嵌入式闭环：

**修改代码 → 无头编译 → 烧录/运行 → 采集寄存器与日志 → 自动诊断修复**

### 与普通编程 Agent 的核心差异

| 调试痛点 | 普通编程 Agent 的局限 | MCU 调试友好型 Harness 的解决方案 |
| :--- | :--- | :--- |
| **外设寄存器状态** | 盲猜内存地址，或把上千页数据手册塞进上下文导致溢出 | 集成 **CMSIS-SVD** 解析器，自动将十六进制地址翻译为外设位域名称与注释 |
| **调试器交互** | 依赖解析杂乱的 GDB 终端文本，极易出错 | 使用 **GDB/MI 机器接口**或 **probe-rs** 返回结构化 JSON |
| **异步与实时日志** | 同步读取串口或调试通道时容易无限阻塞，卡死 Agent 循环 | 后台异步线程持续采集 **RTT / UART 环形缓冲区**，按需切片返回 |
| **硬件安全与防砖** | AI 误发擦除、锁芯片或失控输出指令可能损坏硬件 | 对高危指令（Mass Erase、Option Bytes、PWM/执行器使能）实施 **HITL 分级拦截** |

---

## 2. 开发者背景与技术选型映射

开发者具备扎实的 **MCU 嵌入式开发基础**，但刚接触 Agent 工具开发。为了降低上手门槛并保证工程可控性，采用 Python 生态与标准协议栈：

| 嵌入式概念类比 | Agent 开发对应组件 | 推荐技术栈与参考项目 |
| :--- | :--- | :--- |
| **主控 CPU** | 大语言模型（LLM） | Claude / Gemini 等支持 Tool Use 的模型 |
| **外设驱动 / HAL 库** | MCP Server（标准工具接口） | [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) / [FastMCP](https://gofastmcp.com/docs/getting-started/welcome) |
| **`while(1)` 主循环** | Agent Loop（ReAct 循环） | [Claude Tool Use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview) / [OpenAI Function Calling](https://developers.openai.com/api/docs/guides/function-calling) |
| **调试器底层驱动** | 硬件桥接层 | [pygdbmi](https://github.com/cs01/pygdbmi)（控制 `arm-none-eabi-gdb`）、[embedded-debugger-mcp](https://github.com/adancurusul/embedded-debugger-mcp)（`probe-rs` / OpenOCD） |
| **芯片数据手册** | 外设寄存器语义层 | [cmsis-svd](https://github.com/cmsis-svd/cmsis-svd) + [cmsis-svd-data](https://github.com/cmsis-svd/cmsis-svd-data)（参考 [OpenOCD MCU](https://mcpmarket.com/server/openocd-mcu)） |
| **看门狗 / 写保护** | 安全护栏与权限控制（HITL） | Agent SDK 权限拦截钩子（参考 [OpenCode](https://innfactory.ai/en/ai-harness/opencode/) 的分级权限思路） |
| **无板卡仿真测试** | 虚拟硬件在环（可选扩展） | [Renode](https://github.com/renode/renode)（社区推进中的 [Renode MCP](https://github.com/renode/renode/issues/975)） |

---

## 3. 核心功能模块设计

### 3.1 无头构建与代码索引模块
* 封装 `subprocess` 调用命令行工具链（`CMake` + `Ninja` + `arm-none-eabi-gcc`），屏蔽图形 IDE 依赖。
* 自动裁剪超长的编译输出，仅提取 `error:` / `warning:` 关键行及对应文件行号返回给模型，避免浪费上下文窗口。

### 3.2 结构化调试桥接模块（GDB/MI 或 probe-rs）
* 托管 OpenOCD / J-Link Server 作为后台长驻进程，避免每次工具调用都重新握手复位、冲掉 Bug 现场。
* 使用 [pygdbmi](https://github.com/cs01/pygdbmi) 启动 `arm-none-eabi-gdb --interpreter=mi2`，将断点命中、调用栈（Backtrace）、局部变量及内存读取结果转为标准 JSON 字典。

### 3.3 CMSIS-SVD 寄存器翻译层
* 基于 [cmsis-svd](https://github.com/cmsis-svd/cmsis-svd) 加载目标芯片的 `.svd` 文件。
* 向 AI 暴露 `inspect_peripheral(peripheral_name, register_name)` 工具：Harness 先通过调试器读取对应基地址的内存值，再按 SVD 位域定义解码为可读表格（包含字段名、当前值与功能说明）返回给 AI。

### 3.4 死机现场自动诊断与非阻塞日志采集
* **HardFault 自动分析**：当芯片异常停机或看门狗超时时，一键读取 Cortex-M 核心寄存器（`CFSR`、`HFSR`、`MMFAR`、`BFAR`、异常栈帧 `PC`/`LR`），并结合 `.elf` 符号表定位出事的 C 文件与代码行。
* **后台环形日志缓冲**：使用 Python 独立线程或 `asyncio` 持续读取串口（`pyserial`）或 RTT 通道，存入固定长度的环形缓冲区；AI 按需拉取“最近 N 行”或“关键词上下文”，防止阻塞 Agent 主循环。

### 3.5 分级安全护栏（HITL 权限控制）

| 风险等级 | 典型硬件/调试操作 | Harness 处理策略 |
| :--- | :--- | :--- |
| **低风险（只读观察）** | 读取内存、读取外设寄存器、拉取 RTT/串口日志、查看调用栈 | 自动放行，直接返回结果 |
| **中风险（状态变更）** | 增量编译、设置/清除断点、单步执行、软复位（Soft Reset） | 放行并记录会话状态机日志 |
| **高危操作（物理/不可逆风险）** | 整片擦除（Mass Erase）、修改 Option Bytes（如 RDP 保护）、直接写电源/电机/PWM 关键控制寄存器 | **强制暂停并弹窗/终端确认（HITL）**，获得人工批准后方可执行 |

---

## 4. MVP（最小可行版本）开发路线

为避免初期过度复杂，建议按以下 3 个阶段渐进开发（基于 Windows 环境 + Python + [FastMCP](https://gofastmcp.com/docs/getting-started/welcome)）：

### Phase 1：MCP 工具底座（3 个核心只读/构建工具）
1. `build_firmware`：调用 `cmake --build` 编译工程并返回精简报错。
2. `inspect_peripheral`：结合 OpenOCD/GDB 与 `cmsis-svd` 读取并解码指定外设寄存器。
3. `read_debug_log`：从非阻塞后台串口/RTT 环形缓冲区读取最新日志。

### Phase 2：结构化调试与死机诊断
1. 引入 `pygdbmi` 管理调试会话生命周期（连接、暂停、恢复、查栈）。
2. 实现 `diagnose_crash` 工具，自动解析 Cortex-M HardFault 寄存器并映射 `.elf` 源码行号。

### Phase 3：闭环编排与安全护栏
1. 接入 Agent SDK 主循环与工具分级拦截（HITL 确认机制）。
2. （进阶）参考 [arXiv 嵌入式 AI Agent 研究](https://arxiv.org/html/2603.19583v1) 的思路，接入 [Renode](https://github.com/renode/renode) 仿真或硬件在环（HIL）自动化断言脚本，实现无人值守的“改代码 → 验证行为正确”闭环。

---

## 5. 交给 Antigravity 的开发起步指令参考

当你把本文档交给 Antigravity（`agy`）时，可以附上类似下面这段指令开始 Phase 1 开发：

> 请阅读这份设计文档。我是一名有 MCU 基础但没有 Agent 工具开发经验的工程师。请帮我用 Python 和 `FastMCP` 在当前目录下搭建 Phase 1 的项目骨架：
> 1. 生成清晰的项目目录结构、虚拟环境配置（`pyproject.toml` 或 `requirements.txt`）以及中文注释。
> 2. 先实现 `build_firmware` 和 `inspect_peripheral`（基于 `cmsis-svd`）两个基础工具的最小可运行代码，并保留 OpenOCD/GDB 连接的接口抽象。
> 3. 每写完一个模块，请告诉我如何在 Windows 终端里用命令行单独测试它。
