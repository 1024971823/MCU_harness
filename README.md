# MCU Agent Harness

把 **编译器、编译数据库、内存版图审计、多后端探针（J-Link / OpenOCD）、SEGGER RTT 与串口 UART、CMSIS-SVD、跨外设因果体检医生、DWARF 无停机结构体 Live Watch、RTOS 内核透视、CoreSight DWT 性能剖析与硬件观察点、逻辑分析仪波形/协议解码、声明式 HIL 自动化回归、Cortex-M 全系死机尸检、分级防砖护栏与可移植代码框架引擎** 统一接入大模型的**独立第三方嵌入式 Agent Harness**（自带多厂商大模型路由、不可绕过硬件 HITL 互锁、终端 TUI `mcu-agent`、原生 Qt 6 QML + C++20 商业级桌面 IDE **MCU Harness Studio** `mcu-harness-studio` 与 NDJSON RPC 桥接 `mcu-rpc`，同时兼容 MCP Server `mcu-harness`）。

设计蓝图见 [docs/spec.md](docs/spec.md)，硬件与安全架构记录见 [docs/hardware_target.md](docs/hardware_target.md)。

## 目录结构

```text
MCU_harness/
├── pyproject.toml
├── src/mcu_harness/
│   ├── __init__.py
│   ├── agent/                 # ✅ 独立第三方 Agent Harness（零外部依赖，27 工具 + HITL 互锁 + TUI）
│   │   ├── llm.py             #    多厂商路由：OpenAI / DeepSeek / Qwen / Ollama / Anthropic / Gemini / Mock
│   │   ├── workspace_tools.py #    安全工作区读/改/写/检索/Shell（路径防逃逸 + Diff 预览）
│   │   ├── registry.py        #    统一 27 工具注册表（Schema 无 confirm 开关，强制宿主互锁）
│   │   ├── interlock.py       #    不可绕过 HITL 互锁门（Option Bytes 硬拒绝 + SVD 位域/Diff 预览）
│   │   ├── context.py         #    置顶硬件状态卡 + 5 个嵌入式斜杠 Playbook（/crash /doctor /bringup /refactor /hil）
│   │   ├── runtime.py         #    ReAct 主循环、实时事件总线与 JSONL 会话持久化
│   │   ├── tui.py             #    mcu-agent 终端交互界面（状态栏 + 流式输出 + 审批弹窗）
│   │   ├── rpc.py             #    mcu-rpc NDJSON stdio 桥接（stdout 隔离 + 异步 HITL + 零 Token 遥测）
│   │   └── desktop_launcher.py #   MCU Harness Studio Qt 6 桌面启动器（--build / --install-desktop）
│   ├── server.py              # ✅ MCP Server 入口（FastMCP stdio，21 个全生命周期标准工具）
│   ├── config/                # ✅ 工程配置清单（mcu-harness.toml）与零配置自动探测
│   │   ├── manifest.py        #    自动识别 .ioc / CMake / Makefile / 最新 .elf & .svd
│   │   └── cli.py             #    mcu-config：扫描工程或一键生成 mcu-harness.toml
│   ├── build/                 # ✅ 无头构建、编译数据库生成与内存版图审计
│   │   ├── diagnostics.py     #    编译降噪：GCC / ld / CMake / Make / size
│   │   ├── runner.py          #    自动识别 CMake / Makefile 增量构建
│   │   ├── compdb.py          #    CMake 与传统 Makefile（免 bear）自动生成 compile_commands.json
│   │   ├── footprint.py       #    ELF/.map 分层内存权重、膨胀归因与 DMA(CCMRAM/DTCM)/栈风险审计
│   │   └── cli.py             #    mcu-build：构建、--compdb 导出、--footprint 内存分析
│   ├── svd/                   # ✅ CMSIS-SVD 解析与寄存器位域解码
│   │   ├── models.py          #    Device / Peripheral / Register / Field / Interrupt 模型
│   │   ├── parser.py          #    解析 .svd/.svd.gz（derivedFrom / dim / cluster / interrupt）
│   │   ├── decoder.py         #    按位域解码十六进制值，精简枚举表格
│   │   └── cli.py             #    mcu-svd：离线查表与位域解码
│   ├── probe/                 # ✅ 多探针抽象 + J-Link + OpenOCD + 现场外设解码
│   │   ├── base.py            #    DebugProbe 接口（读写内存/核心寄存器/复位/烧录/RTT）
│   │   ├── jlink.py           #    SEGGER J-Link 非侵入后端（AHB-AP 免停机读 + JLINKARM_Go）
│   │   ├── openocd.py         #    OpenOCD TCL RPC 后端（兼容 ST-Link / CMSIS-DAP / DAPLink）
│   │   ├── mock.py            #    内存与故障注入模拟探针（无硬件测试用）
│   │   ├── manager.py         #    线程安全长驻会话管理器
│   │   ├── peripheral.py      #    SVD + DebugProbe 联合解码外设寄存器
│   │   └── cli.py             #    mcu-probe：连板读写、核心寄存器、安全烧录与外设查看
│   ├── diagnose/              # ✅ 跨外设因果体检医生与板级真机对账（深度硬件感知）
│   │   ├── peripheral_doctor.py #  联合检查 RCC 时钟门控 + NVIC 挂起/使能 + 错误位 + GPIO 复用
│   │   ├── board_context.py   #    解析 .ioc + [board] 外部器件定义，与真机 GPIO 寄存器漂移比对
│   │   └── cli.py             #    mcu-doctor：peripheral / pins / board --live
│   ├── dwarf/                 # ✅ 纯标准库 DWARF v2~v5 无停机变量读取与采样（Live Watch）
│   │   ├── parser.py          #    解析结构体/联合体/枚举/数组/位域/指针（含 zlib 压缩节与降级回退）
│   │   ├── live_watch.py      #    免暂停 CPU 单次读块还原嵌套 C 结构体 + 高频时间序列采样
│   │   └── cli.py             #    mcu-watch：list-vars / read / sample
│   ├── rtos/                  # ✅ RTOS 内核无停机透视与栈高水位扫描
│   │   ├── inspector.py       #    自动识别 FreeRTOS / Zephyr / RT-Thread，遍历任务与水印溢出告警
│   │   └── cli.py             #    mcu-rtos：一键查看任务状态、优先级与栈净空
│   ├── trace/                 # ✅ Cortex-M DWT 非侵入性能剖析与硬件数据观察点
│   │   ├── dwt.py             #    CYCCNT 主频测速、PCSR 热点图采样、Armv7-M/v8-M 数据观察点
│   │   └── cli.py             #    mcu-trace：cycles / profile / watch-set / watch-list / watch-clear
│   ├── hil/                   # ✅ 逻辑分析仪数字波形/协议解码与声明式 HIL 测试引擎
│   │   ├── logic_analyzer.py  #    纯 Python CSV 波形分析（频率/占空比/边沿）+ sigrok-cli 协议解码
│   │   ├── runner.py          #    TOML/JSON HIL 测试编排与强保障 teardown Safe-Off 契约
│   │   └── cli.py             #    mcu-hil：run / logic
│   ├── safety/                # ✅ 分级硬件安全护栏（HITL 防砖与操作审计）
│   │   └── guard.py           #    Option Bytes / Product State 硬拒绝 + 外设/烧录人工确认门
│   ├── log/                   # ✅ 非阻塞双模调试日志模块（SEGGER RTT + 串口 UART）
│   │   ├── ring_buffer.py     #    线程安全环形缓冲（增量 UTF-8 / seq / grep -C）
│   │   ├── rtt_collector.py   #    后台轮询 RTT 上下行（支持 .elf 秒定位 _SEGGER_RTT）
│   │   ├── uart_collector.py  #    原生 POSIX termios / pyserial 非阻塞串口采集与下行发送
│   │   └── cli.py             #    mcu-log：RTT 或 --uart 串口单次拉取 / --follow 跟踪
│   ├── elf/                   # ✅ 纯 Python ELF 符号解析与 DWARF 源码行号映射
│   │   ├── symbols.py         #    解析 ELF32/ELF64 .symtab（Thumb LSB 自动对齐）
│   │   └── addr2line.py       #    跨语言环境 addr2line 内联展开 + 符号偏移降级回退
│   ├── crash/                 # ✅ Cortex-M 全系列死机自动尸检（Armv6-M / v7-M / v8-M）
│   │   ├── cortex_m.py        #    CPUID + CFSR/HFSR/SFSR/MMFAR/BFAR + 异常栈帧还原 + 修复建议
│   │   └── cli.py             #    mcu-crash：一键死机破案（含 --mock 故障注入演示）
│   └── arch/                  # ✅ 嵌入式代码框架守护与跨芯片 PAL/BSP 脚手架
│       ├── analyzer.py        #    分层依赖检查 + ISR 传递调用图阻塞扫描 + 漏写 volatile 检测
│       ├── scaffold.py        #    生成 C11 PAL / BSP(cfg-ctx-data-ops+safe_off) / Service 与主机单测
│       └── cli.py             #    mcu-arch：静态架构体检与可移植代码生成
├── studio/                    # ✅ MCU Harness Studio（商业级原生 Qt 6 QML + C++20 桌面 IDE）
│   ├── CMakeLists.txt         #    Qt 6.11 CMake + CPack DEB + 多分辨率图标 + zh_CN 翻译构建
│   ├── src/                   #    EngineClient (NDJSON RPC)、LicenseManager (Ed25519 + 防回拨试用)、UpdateChecker、CrashHandler
│   ├── qml/                   #    现代 IDE 外壳（ActivityBar / SideBar / BottomPanel / Ctrl+K 命令面板 / 深浅双主题 / 7 大工作台）
│   ├── packaging/             #    Linux (.desktop / AppImage)、Windows (Inno Setup installer.iss) 打包脚本
│   └── tests/                 #    QtTest C++20 + QML 冒烟与 68 张矩阵截图测试
├── tools/
│   ├── fetch_third_party.sh   #    拉取 Inter / JetBrains Mono 字体、Lucide 图标、TweetNaCl 与 AppImage 工具
│   ├── license/mcu_license.py #    离线 Ed25519 授权签发与验签 CLI
│   └── release/make_update_manifest.py # Ed25519 更新清单签名生成 CLI
├── examples/
│   ├── demo_fw/               # CMake 示例工程
│   └── demo_make_fw/          # Makefile 示例工程
└── tests/                     # 单元与端到端集成测试套件（兼容 pytest 与 unittest）
```

## 独立第三方 Harness（`mcu-agent` TUI + MCU Harness Studio 原生 Qt 6 桌面 IDE）

无需依赖任何外部 IDE 或第三方 Python SDK（纯标准库即可运行 Python 引擎），内置 **27 个工具**（6 个工作区代码/Shell 工具 + 21 个嵌入式硬件与架构工具）：

1. **多厂商模型路由配置**（优先级：`CLI 参数` > `mcu-harness.toml [agent]` > `~/.config/mcu-harness/agent.toml [agent]` > 环境变量 `MCU_AGENT_PROVIDER` / `MCU_AGENT_MODEL` / `MCU_AGENT_API_KEY`）：
   - **OpenAI 兼容生态**：`openai`、`deepseek`（`DEEPSEEK_API_KEY`）、`qwen`（`DASHSCOPE_API_KEY`）、`openrouter`（`OPENROUTER_API_KEY`），以及断网实验室本地模型 **`ollama` / `vllm`**；
   - **Anthropic Claude**：`anthropic`（`ANTHROPIC_API_KEY`）；
   - **Google Gemini**：`gemini`（`GEMINI_API_KEY`）；
   - **离线演示**：`--mock-llm`。
2. **不可绕过的 HITL 硬件安全互锁**：
   - LLM 看到的 `write_memory`、`flash_firmware`、`run_hil_tests` Schema 中**不存在 `confirm` 参数**，模型无法自行伪造确认；
   - 写 STM32 Option Bytes / `Product State` 等防砖区间直接由 `HarnessInterlock` **硬拒绝**；写普通外设寄存器前自动解码对应 **CMSIS-SVD 位域变更预览**，改代码前自动生成 **Unified Diff 预览**，交由终端或桌面弹窗人工批准后方可执行。
3. **商业级 Qt 6 QML + C++20 桌面 IDE（MCU Harness Studio）**：
   - 现代 IDE 工作区布局（左侧 ActivityBar、可折叠 SideBar 与底部日志/串口 Console、StatusBar、`Ctrl+K` 命令面板、`Ctrl+B` / `Ctrl+J` 面板切换、深浅双主题与英文 / 简体中文 `zh_CN` 实时切换）；
   - **7 大工作台**：AI Agent（含 5 个嵌入式 Playbooks 与内嵌 HITL 审批卡片）、Hardware（探针 + 串口 + 外设寄存器与引脚体检）、Live Watch（无停机 DWARF 采样趋势图）、RTOS（内核任务与栈高水位）、Sessions（历史会话）、Audit（安全审计与脱敏诊断导出 `.zip`）、Settings（模型/外观/授权/更新）；
   - **商业化基础设施**：TweetNaCl Ed25519 离线授权码（`MCUH1.<payload>.<sig>`）与 14 天 HMAC-SHA256 防时钟回拨试用、Ed25519 签名更新检查、POSIX/Windows 崩溃捕获与首次引导向导。
4. **内置嵌入式斜杠工作流（Playbooks）与零 Token 元命令**：
   - Playbooks：`/crash`（硬故障+RTOS+RTT 破案）、`/doctor [PERIPH]`（四步外设因果联查）、`/bringup`（新板点亮体检）、`/refactor`（架构/ISR 检查与重构）、`/hil <spec>`（编译-烧录-HIL 闭环）；
   - 本地元命令（0 LLM Token）：`/status`、`/audit`、`/compact`、`/clear`、`/sessions`、`/help`、`/exit`。

```bash
# 离线零硬件体验独立第三方 Agent TUI
PYTHONPATH=src python3 -m mcu_harness.agent.tui -p "/status" --mock-llm

# 使用 DeepSeek / Ollama / Gemini 启动交互式终端 TUI
DEEPSEEK_API_KEY=sk-xxx PYTHONPATH=src python3 -m mcu_harness.agent.tui --provider deepseek --model deepseek-chat /你的/固件工程
PYTHONPATH=src python3 -m mcu_harness.agent.tui --provider ollama --model qwen2.5-coder:14b /你的/固件工程

# 编译、安装桌面快捷方式并启动 MCU Harness Studio（Qt 6 原生桌面 IDE）
PYTHONPATH=src python3 -m mcu_harness.agent.desktop_launcher --build
PYTHONPATH=src python3 -m mcu_harness.agent.desktop_launcher --project-dir /你的/固件工程 --provider deepseek
PYTHONPATH=src python3 -m mcu_harness.agent.desktop_launcher --install-desktop

# 或直接运行已构建的二进制 / 构建 Linux .deb 与 AppImage 安装包
./studio/build/release/mcu-harness-studio --mock-llm /你的/固件工程
cd studio && cmake --build --preset release --target package   # 输出 dist/mcu-harness-studio_1.0.0_amd64.deb
bash studio/packaging/appimage/build_appimage.sh              # 输出 dist/MCU_Harness_Studio-1.0.0-x86_64.AppImage

# 单独运行 NDJSON stdio RPC 后端（供桌面前端或自动化脚本对接）
PYTHONPATH=src python3 -m mcu_harness.agent.rpc /你的/固件工程 --mock-llm
```

## 快速开始（Linux，14 个独立命令行工具）

```bash
# 1. 自动扫描任意嵌入式工程并生成 mcu-harness.toml
PYTHONPATH=src python3 -m mcu_harness.config.cli inspect examples/demo_fw
PYTHONPATH=src python3 -m mcu_harness.config.cli init ./你的固件工程

# 2. 无头构建、Makefile 免 bear 生成 compile_commands.json 与 .elf/.map 内存版图审计
PYTHONPATH=src python3 -m mcu_harness.build.cli examples/demo_make_fw -j 4
PYTHONPATH=src python3 -m mcu_harness.build.cli examples/demo_make_fw --compdb
PYTHONPATH=src python3 -m mcu_harness.build.cli examples/demo_make_fw --footprint

# 3. 跨外设因果体检医生（自动抓 RCC 漏开时钟 / NVIC 未使能 / ORE 溢出 / 引脚漂移）
PYTHONPATH=src python3 -m mcu_harness.diagnose.cli peripheral USART1 --svd tests/fixtures/stm32h563_mini.svd --mock
PYTHONPATH=src python3 -m mcu_harness.diagnose.cli pins --svd tests/fixtures/stm32h563_mini.svd --mock

# 4. DWARF 无停机实时结构体变量观察（Live Watch）与趋势采样
PYTHONPATH=src python3 -m mcu_harness.dwarf.cli list-vars --elf /你的/firmware.elf
PYTHONPATH=src python3 -m mcu_harness.dwarf.cli read g_motor_ctrl g_motor_ctrl.state --elf /你的/firmware.elf
PYTHONPATH=src python3 -m mcu_harness.dwarf.cli sample g_motor_ctrl.rpm --elf /你的/firmware.elf -n 30 --interval-ms 5

# 5. RTOS 内核透视与 DWT 非侵入性能剖析 / 数据观察点
PYTHONPATH=src python3 -m mcu_harness.rtos.cli --elf /你的/firmware.elf --rtos auto
PYTHONPATH=src python3 -m mcu_harness.trace.cli cycles --mock --duration-ms 20
PYTHONPATH=src python3 -m mcu_harness.trace.cli profile --mock --samples 50
PYTHONPATH=src python3 -m mcu_harness.trace.cli watch-set 0x20000100 --access write --mock

# 6. 逻辑分析仪 CSV/sigrok 解码与声明式 HIL 自动化回归
PYTHONPATH=src python3 -m mcu_harness.hil.cli logic --csv capture.csv --time-unit 1e-6
PYTHONPATH=src python3 -m mcu_harness.hil.cli run hil_suite.toml --mock

# 7. Cortex-M 死机自动尸检（--mock 可离线体验 HardFault 空指针解引用破案报告）
PYTHONPATH=src python3 -m mcu_harness.crash.cli --mock
PYTHONPATH=src python3 -m mcu_harness.crash.cli --elf /你的/firmware.elf

# 8. 非阻塞 SEGGER RTT 与串口 UART 日志采集
PYTHONPATH=src python3 -m mcu_harness.log.cli --mock
PYTHONPATH=src python3 -m mcu_harness.log.cli --list-ports
PYTHONPATH=src python3 -m mcu_harness.log.cli --uart /dev/ttyACM0 --baud 115200 --tail 50

# 9. 代码框架静态检查（分层边界 / ISR 阻塞调用链 / volatile）与跨芯片 PAL/BSP 脚手架
PYTHONPATH=src python3 -m mcu_harness.arch.cli scaffold skeleton /tmp/demo_portable_fw
PYTHONPATH=src python3 -m mcu_harness.arch.cli scaffold bsp ina226 --dir /tmp/demo_portable_fw
PYTHONPATH=src python3 -m mcu_harness.arch.cli check /tmp/demo_portable_fw
```

## MCP 工具全景（21 个标准工具）

| 领域 | 工具名 | 核心能力 |
| :--- | :--- | :--- |
| **工程、构建与内存** | `inspect_project` | 自动扫描 `.ioc` / CMake / Makefile / 最新 `.elf` & `.svd`，或生成 `mcu-harness.toml` |
| | `generate_compile_commands` | 为 CMake 或传统 Makefile（零 `bear` 依赖）生成 `compile_commands.json` |
| | `build_firmware` | 无头增量构建 CMake / Makefile 工程，输出去重错误/警告与段占用 |
| | `analyze_footprint` | 解析 `.elf` 与 `.map` 统计分层权重、膨胀元凶及 DMA(`CCMRAM`/`DTCM`)/栈净空风险 |
| **深度硬件感知** | `inspect_board` | 解析 `.ioc` 与板级外部器件定义，支持 `live_check=True` 实时核验真机 GPIO 漂移 |
| | `diagnose_peripheral` | 一键串联检查 `RCC` 时钟门控、`NVIC` 中断状态、外设错误标志与 `GPIO` 复用 |
| | `inspect_peripheral` | 实时连板读取外设寄存器并按 SVD 解码（支持 `diff_only=True` 仅看非复位值） |
| | `inspect_svd` | 离线查询芯片外设寄存器地址与位域枚举定义 |
| **无停机变量与内核** | `watch_variable` | 基于 DWARF v2~v5 不暂停 CPU 读取 C 嵌套结构体/枚举/数组/指针或高频趋势采样 |
| | `inspect_rtos` | 无停机透视 FreeRTOS / Zephyr / RT-Thread 任务状态、优先级与栈高水位溢出告警 |
| **性能剖析与死机破案** | `trace_target` | 非侵入 DWT 周期测速（估算 SYSCLK）、`DWT_PCSR` 热点采样与硬件数据观察点 |
| | `diagnose_crash` | Cortex-M 全系异常寄存器 + `EXC_RETURN` 压栈帧解包 + DWARF 源码行号还原 |
| | `read_debug_log` | 后台非阻塞 **SEGGER RTT** 与 **串口 UART** 环形日志增量拉取及下行命令注入 |
| **仪器与 HIL 闭环** | `capture_logic` | 纯 Python 数字 CSV 波形统计（频率/占空比/边沿）或 `sigrok-cli` SPI/I2C/UART 解码 |
| | `run_hil_tests` | 声明式 `.toml`/`.json` 硬件在环自动化测试执行器（含无条件 `teardown` Safe-Off 契约） |
| **探针与安全护栏** | `probe_control` | 管理 J-Link / OpenOCD 会话：`info / connect / disconnect / halt / resume / reset / regs / audit` |
| | `read_memory` | 按 8/16/32 位读取任意内存或寄存器原始值 |
| | `write_memory` | 受安全护栏保护的内存/寄存器写入（硬拦截 Option Bytes，外设写入强制 HITL 确认） |
| | `flash_firmware` | 受 HITL 确认保护的 `.elf/.hex/.bin` 安全烧录与校验复位 |
| **代码框架引擎** | `check_architecture` | 嵌入式分层依赖防腐检查、ISR 中断传递调用图阻塞扫描、共享变量漏加 `volatile` 检测 |
| | `scaffold_code` | 一键生成跨芯片 C11 `PAL` / `BSP(cfg-ctx-data-ops + safe_off)` / `Service` 模块及主机单元测试 |

## 接入 MCP 客户端（如 Antigravity）

```json
{
  "mcpServers": {
    "mcu-harness": {
      "command": "/home/srakoul/Projects/MCU_harness/.venv/bin/python",
      "args": ["-m", "mcu_harness.server"],
      "env": {
        "PYTHONPATH": "/home/srakoul/Projects/MCU_harness/src"
      }
    }
  }
}
```

## 演进路线图

- [x] Phase 1 无头构建（CMake/Make）、CMSIS-SVD 解码、J-Link 非侵入探针、SEGGER RTT 环形缓冲与 ELF 符号定位
- [x] Phase 2 工程自发现清单（`mcu-harness.toml`）、OpenOCD 多探针后端、Cortex-M 全系 `diagnose_crash` 与分级防砖安全护栏
- [x] Phase 3 嵌入式代码框架引擎（分层边界 + ISR 阻塞调用链 + `volatile` 扫描 + 跨芯片 PAL/BSP/Service 脚手架）
- [x] Phase 4 深度硬件感知与工程分析（DWARF 无停机结构体 `watch_variable`、跨外设因果体检 `diagnose_peripheral`、`.ioc` 真机引脚漂移核验、`.map` 内存/DMA 审计、Makefile `compile_commands.json` 与原生串口 UART 日志流）
- [x] Phase 5 内核/总线与闭环自动化（`inspect_rtos` 任务与栈水位透视、CoreSight DWT 主频测速/PC 热点/硬件数据观察点 `trace_target`、`capture_logic` 波形与协议解码、`run_hil_tests` 声明式 HIL 自动化测试引擎）
- [x] Phase 6 独立第三方 Agent Harness（`agent/` 多厂商模型路由 + 27 工具统一注册 + 不可绕过 HITL 互锁 + 斜杠 Playbooks + JSONL 会话持久化 + `mcu-agent` TUI + `mcu-desktop` 原生 Rust 桌面软件 / `mcu-rpc` NDJSON 桥接）
