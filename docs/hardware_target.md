# 目标硬件与技术决策记录

> 本文件会随开发推进持续更新，记录“为什么这么选”，方便以后回顾。

## 1. 目标硬件

| 项目 | 内容 | 对 Harness 的影响 |
|---|---|---|
| MCU | **STM32H563**（Cortex-M33，Armv8-M Mainline，FPU，TrustZone） | 死机诊断要按 Armv8-M 处理（见第 4 节） |
| 调试器 | **SEGGER J-Link** | 调试后端以 J-Link 生态为主，不用 OpenOCD |
| 人工调试 IDE | **Ozone** | Ozone 给人看，Harness 给 AI 用，两者要能共存 |
| 主机 | Arch Linux | J-Link 软件包需要从 SEGGER 官网 / AUR 安装 |

## 2. 调试后端选型

| 方案 | 适合做什么 | 计划 |
|---|---|---|
| **pylink-square**（Python 封装 SEGGER JLinkARM.dll / libjlinkarm.so） | 读写内存、暂停/恢复、读核心寄存器、**内置 RTT 读写** | **Phase 1 主力**：`inspect_peripheral`、`read_debug_log`(RTT) |
| **JLinkGDBServerCLExe + arm-none-eabi-gdb + pygdbmi** | 源码级调试：断点、调用栈、局部变量、符号解析 | **Phase 2** 引入，用于 `diagnose_crash` 的源码定位与会话管理 |
| Ozone | 人工图形化调试 | 不由 AI 控制；AI 给出 `file:line`，人在 Ozone 中复核 |

架构上抽象出统一的 `DebugProbe` 接口（read_memory / halt / resume / read_core_regs / rtt_read ...），
上层工具不关心底下是 pylink 还是 GDB，以后换 probe-rs / OpenOCD 也只需新增一个实现。

> [!NOTE] 待实机验证
> J-Link DLL 支持多个应用同时连接同一个探针（例如 Ozone 调试时再开 RTT Viewer）。
> 需要实测：Ozone 打开时，Harness 通过 pylink 读内存 / RTT 是否稳定；以及 AI 暂停内核时 Ozone 的表现。

## 3. TrustZone 注意事项

* STM32H563 的外设有 **非安全别名（0x4xxx_xxxx）** 与 **安全别名（0x5xxx_xxxx）**，SVD 中通常同时包含 `SEC_` 前缀的外设。
* 若开启了 `TZEN`，调试器访问权限与当前安全状态相关，读安全别名可能失败。
* `inspect_peripheral` 需要支持选择别名，并在访问失败时给出明确提示，而不是返回一个错误的 0。

## 4. Cortex-M33 死机诊断要点（Phase 2）

与 Cortex-M3/M4 相比需要额外处理：

* **SFSR / SFAR**（SecureFault，0xE000EDE4 / 0xE000EDE8）
* **CFSR.UFSR.STKOF**：栈溢出（MSPLIM / PSPLIM 栈限制寄存器触发），M33 上非常常见
* **EXC_RETURN** 新位域：`S`（安全状态）、`DCRS`、`FType`（是否带 FPU 扩展栈帧）、`Mode`、`SPSEL`、`ES`
* 栈帧选择：MSP_S / PSP_S / MSP_NS / PSP_NS 四个栈指针

## 5. 安全护栏（HITL）的 H5 特有项

STM32H5 用 **Product State** 取代了 F4 的 RDP Level：

| Product State | 风险 |
|---|---|
| Open | 正常开发状态 |
| Provisioning / iROT-Provisioned / TZ-Closed / Closed | 调试受限，回退需要 Debug Authentication |
| **Locked** | **不可逆**，芯片永久锁死调试 |

策略（初版）：

| 操作 | 策略 |
|---|---|
| 写 FLASH 选项字节寄存器（Product State、TZEN、Boot 地址、写保护等） | **默认硬拒绝**（不只是 HITL 确认），需要在配置文件中显式开启 |
| Mass Erase / 烧录 | HITL 人工确认 |
| 写电源、时钟、PWM/电机相关外设寄存器 | HITL 人工确认 |
| 读内存、读寄存器、RTT | 自动放行 |

## 6. 主机软件依赖（Arch Linux）

```bash
# 交叉工具链
sudo pacman -S arm-none-eabi-gcc arm-none-eabi-newlib arm-none-eabi-gdb
# J-Link 软件包（含 libjlinkarm.so、JLinkGDBServerCLExe、udev 规则）
#   SEGGER 官网下载 .tgz，或 AUR: jlink-software-and-documentation
# Python 依赖（后续步骤按需安装）
pip install fastmcp pylink-square cmsis-svd
```

SVD 文件来源：ST 官网 STM32H5 SVD 包，或 STM32H5xx_DFP（Keil Pack），或 cmsis-svd-data 仓库。

## 7. 已确认的项目上下文

- [x] 固件工程形式：**Makefile 工程**（后续需让 `build_firmware` 同时兼容 `make` 与 `CMake`）
- [x] 当前固件 TrustZone（TZEN）：**未启用**（Phase 1 按单地址空间 `0x4xxx_xxxx` 处理即可，暂不涉及安全别名切换）
- [x] 主机上 J-Link 软件版本与路径：`SEGGER J-Link V9.46`，`JLinkGDBServerCLExe` 和 `arm-none-eabi-gcc` 位于 `~/.local/bin/`（系统 Python 已自带 `pygdbmi 0.11.0.0`）
- [x] 日志通道：**SEGGER RTT** 为主
