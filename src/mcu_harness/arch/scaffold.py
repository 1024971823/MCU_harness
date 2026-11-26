"""跨芯片可移植嵌入式框架（PAL / BSP / Service / 四层工程骨架）与零硬件主机单测脚手架生成器。

支持 4 种生成模式：
    - `pal`：生成 `include/pal/` 下的统一硬件抽象层契约头文件（零原厂 HAL 依赖）
    - `bsp`：生成标准四元契约（`cfg` / `ctx` / `data` / `ops`，强制含 `safe_off`）驱动及 Mock PAL 主机单测
    - `service`：生成平台无关非阻塞状态机（`IDLE` / `RUNNING` / `FAULT`）业务逻辑及主机单测
    - `skeleton`：一键初始化完整四层可移植工程（`mcu-harness.toml` + `CMakeLists.txt` + `pal` + `bsp` + `service` + `app`）
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ScaffoldError(RuntimeError):
    """脚手架参数校验或文件写入冲突异常。"""


_VALID_KINDS = ("pal", "bsp", "service", "skeleton")
_RE_MODULE_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


@dataclass(frozen=True)
class ScaffoldResult:
    """脚手架代码生成结果摘要。"""

    project_dir: str
    kind: str
    name: str | None
    created_files: list[str]
    skipped_files: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_dir": self.project_dir,
            "kind": self.kind,
            "name": self.name,
            "created_files": list(self.created_files),
            "skipped_files": list(self.skipped_files),
        }

    def to_text(self) -> str:
        title = f"{self.kind}:{self.name}" if self.name else self.kind
        lines = [
            f"[scaffold] Generated '{title}' in {self.project_dir}",
            f"  created ({len(self.created_files)}):",
        ]
        for rel in self.created_files:
            lines.append(f"    + {rel}")
        if self.skipped_files:
            lines.append(f"  kept existing ({len(self.skipped_files)}):")
            for rel in self.skipped_files:
                lines.append(f"    = {rel}")
        return "\n".join(lines)


def _normalize_module_name(raw_name: str | None, kind: str) -> str:
    if not raw_name or not raw_name.strip():
        raise ScaffoldError(f"Module name is required for scaffold kind={kind!r} (e.g. 'ina226' or 'thermal')")
    cleaned = raw_name.strip().replace("-", "_").lower()
    if not _RE_MODULE_NAME.match(cleaned):
        raise ScaffoldError(f"Invalid C identifier module name {raw_name!r}; use letters, digits, and underscores")
    # 避免用户在 kind="service" 时重复传 `xxx_service` 导致 `xxx_service_service`
    if kind == "service" and cleaned.endswith("_service") and len(cleaned) > len("_service"):
        cleaned = cleaned[: -len("_service")]
    if kind == "bsp" and cleaned.startswith("bsp_") and len(cleaned) > len("bsp_"):
        cleaned = cleaned[len("bsp_") :]
    return cleaned


def scaffold_code(
    project_dir: str | os.PathLike,
    kind: str,
    name: str | None = None,
    *,
    overwrite: bool = False,
) -> ScaffoldResult:
    """在指定目录生成跨芯片可移植 C99/C11 嵌入式模块及主机单元测试代码。"""
    norm_kind = (kind or "").strip().lower()
    if norm_kind not in _VALID_KINDS:
        raise ScaffoldError(f"Unsupported scaffold kind {kind!r}; expected one of {_VALID_KINDS}")

    root = Path(project_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    primary_files: dict[str, str] = {}
    prereq_files: dict[str, str] = {}
    resolved_name: str | None = None

    if norm_kind == "pal":
        primary_files.update(_render_pal_headers())
    elif norm_kind == "bsp":
        resolved_name = _normalize_module_name(name, "bsp")
        prereq_files.update(_render_pal_headers())
        primary_files.update(_render_bsp_module(resolved_name))
    elif norm_kind == "service":
        resolved_name = _normalize_module_name(name, "service")
        primary_files.update(_render_service_module(resolved_name))
    elif norm_kind == "skeleton":
        resolved_name = _normalize_module_name(name, "skeleton") if name else None
        primary_files.update(_render_pal_headers())
        primary_files.update(_render_bsp_module("demo_sensor"))
        primary_files.update(_render_service_module("demo_ctrl"))
        primary_files.update(_render_skeleton_top_files())

    # 原子性预检查：任何主目标文件已存在且未传 overwrite=True 时，提前拒绝且不留半成品
    if not overwrite:
        conflicts = [rel for rel in primary_files if (root / rel).exists()]
        if conflicts:
            joined = ", ".join(conflicts)
            raise ScaffoldError(f"Target file(s) already exist: {joined} (pass overwrite=True to replace)")

    created: list[str] = []
    skipped: list[str] = []

    for rel, content in prereq_files.items():
        dst = root / rel
        if dst.exists() and not overwrite:
            skipped.append(rel)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8")
        created.append(rel)

    for rel, content in primary_files.items():
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(content, encoding="utf-8")
        created.append(rel)

    return ScaffoldResult(
        project_dir=str(root),
        kind=norm_kind,
        name=resolved_name,
        created_files=created,
        skipped_files=skipped,
    )


# ---------------------------------------------------------------------------
# 模板 1：PAL（硬件外设抽象层契约头文件）
# ---------------------------------------------------------------------------


def _render_pal_headers() -> dict[str, str]:
    pal_types = """\
/**
 * @file pal_types.h
 * @brief 跨芯片外设抽象层（PAL）统一状态码与基础类型定义。
 *
 * @note 严格遵循 C99/C11 标准，严禁引入任何原厂 HAL/LL 头文件，确保上层驱动与服务可脱离硬件在宿主机编译测试。
 */

#ifndef PAL_TYPES_H
#define PAL_TYPES_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief PAL 统一返回状态码枚举。
 */
typedef enum {
    PAL_OK = 0,            /**< 操作成功 */
    PAL_ERR_TIMEOUT = -1,  /**< 外设等待或总线传输超时 */
    PAL_ERR_IO = -2,       /**< 底层硬件传输错误（NACK / 总线仲裁丢失 / 越界故障） */
    PAL_ERR_PARAM = -3     /**< 空指针、未初始化或参数越界 */
} pal_status_t;

#ifdef __cplusplus
}
#endif

#endif /* PAL_TYPES_H */
"""

    pal_gpio = """\
/**
 * @file pal_gpio.h
 * @brief 跨芯片通用数字输入输出（GPIO）外设抽象接口契约。
 */

#ifndef PAL_GPIO_H
#define PAL_GPIO_H

#include "pal_types.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief GPIO 操作虚函数表。
 */
typedef struct {
    /**
     * @brief 设置引脚输出电平。
     * @param ctx   底层平台实例上下文指针
     * @param level 输出电平（0 表示低电平，非 0 表示高电平）
     * @return PAL_OK 成功，其他负值表示错误
     */
    pal_status_t (*write)(void *ctx, uint8_t level);

    /**
     * @brief 读取引脚当前电平。
     * @param ctx       底层平台实例上下文指针
     * @param out_level 输出读取到的电平（0 或 1）
     * @return PAL_OK 成功，其他负值表示错误
     */
    pal_status_t (*read)(void *ctx, uint8_t *out_level);

    /**
     * @brief 翻转引脚输出电平。
     * @param ctx 底层平台实例上下文指针
     * @return PAL_OK 成功，其他负值表示错误
     */
    pal_status_t (*toggle)(void *ctx);
} pal_gpio_ops_t;

/**
 * @brief GPIO 抽象句柄（多实例安全，无全局隐式状态）。
 */
typedef struct {
    void *ctx;                 /**< 平台特定引脚上下文（如端口基址+Pin 或 Mock 结构体） */
    const pal_gpio_ops_t *ops; /**< GPIO 操作函数表指针 */
} pal_gpio_t;

static inline pal_status_t pal_gpio_write(const pal_gpio_t *gpio, uint8_t level) {
    if (gpio == NULL || gpio->ops == NULL || gpio->ops->write == NULL) {
        return PAL_ERR_PARAM;
    }
    return gpio->ops->write(gpio->ctx, level);
}

static inline pal_status_t pal_gpio_read(const pal_gpio_t *gpio, uint8_t *out_level) {
    if (gpio == NULL || out_level == NULL || gpio->ops == NULL || gpio->ops->read == NULL) {
        return PAL_ERR_PARAM;
    }
    return gpio->ops->read(gpio->ctx, out_level);
}

static inline pal_status_t pal_gpio_toggle(const pal_gpio_t *gpio) {
    if (gpio == NULL || gpio->ops == NULL || gpio->ops->toggle == NULL) {
        return PAL_ERR_PARAM;
    }
    return gpio->ops->toggle(gpio->ctx);
}

#ifdef __cplusplus
}
#endif

#endif /* PAL_GPIO_H */
"""

    pal_i2c = """\
/**
 * @file pal_i2c.h
 * @brief 跨芯片 I2C 主机总线外设抽象接口契约。
 */

#ifndef PAL_I2C_H
#define PAL_I2C_H

#include "pal_types.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief I2C 总线操作虚函数表。
 */
typedef struct {
    /**
     * @brief 向目标从机地址发送字节流。
     */
    pal_status_t (*write)(void *ctx, uint16_t dev_addr, const uint8_t *data, size_t len, uint32_t timeout_ms);

    /**
     * @brief 从目标从机地址接收字节流。
     */
    pal_status_t (*read)(void *ctx, uint16_t dev_addr, uint8_t *data, size_t len, uint32_t timeout_ms);

    /**
     * @brief 读取从机指定寄存器/内存地址区间。
     */
    pal_status_t (*mem_read)(
        void *ctx,
        uint16_t dev_addr,
        uint16_t mem_addr,
        uint8_t mem_addr_size,
        uint8_t *data,
        size_t len,
        uint32_t timeout_ms
    );

    /**
     * @brief 写入从机指定寄存器/内存地址区间。
     */
    pal_status_t (*mem_write)(
        void *ctx,
        uint16_t dev_addr,
        uint16_t mem_addr,
        uint8_t mem_addr_size,
        const uint8_t *data,
        size_t len,
        uint32_t timeout_ms
    );
} pal_i2c_ops_t;

/**
 * @brief I2C 总线抽象句柄。
 */
typedef struct {
    void *ctx;                /**< 底层 I2C 控制器实例上下文 */
    const pal_i2c_ops_t *ops; /**< I2C 操作函数表指针 */
} pal_i2c_t;

#ifdef __cplusplus
}
#endif

#endif /* PAL_I2C_H */
"""

    pal_spi = """\
/**
 * @file pal_spi.h
 * @brief 跨芯片 SPI 全双工同步串行总线抽象接口契约。
 */

#ifndef PAL_SPI_H
#define PAL_SPI_H

#include "pal_types.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief SPI 总线操作虚函数表。
 */
typedef struct {
    /**
     * @brief 执行全双工或单向 SPI 数据帧交换（tx_buf 或 rx_buf 允许其一为 NULL）。
     */
    pal_status_t (*transfer)(
        void *ctx,
        const uint8_t *tx_buf,
        uint8_t *rx_buf,
        size_t len,
        uint32_t timeout_ms
    );
} pal_spi_ops_t;

/**
 * @brief SPI 总线抽象句柄。
 */
typedef struct {
    void *ctx;                /**< 底层 SPI 控制器实例上下文 */
    const pal_spi_ops_t *ops; /**< SPI 操作函数表指针 */
} pal_spi_t;

#ifdef __cplusplus
}
#endif

#endif /* PAL_SPI_H */
"""

    pal_pwm = """\
/**
 * @file pal_pwm.h
 * @brief 跨芯片脉宽调制（PWM）输出通道抽象接口契约。
 */

#ifndef PAL_PWM_H
#define PAL_PWM_H

#include "pal_types.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief PWM 通道操作虚函数表（占空比采用千分比 0..1000‰ 定点表示，彻底避免浮点依赖）。
 */
typedef struct {
    /**
     * @brief 设置 PWM 输出占空比千分比（0..1000‰）。
     */
    pal_status_t (*set_duty_permille)(void *ctx, uint16_t duty_permille);

    /**
     * @brief 使能 PWM 波形输出。
     */
    pal_status_t (*enable)(void *ctx);

    /**
     * @brief 立即禁止 PWM 输出并回到安全电平。
     */
    pal_status_t (*disable)(void *ctx);
} pal_pwm_ops_t;

/**
 * @brief PWM 通道抽象句柄。
 */
typedef struct {
    void *ctx;                /**< 底层定时器通道上下文 */
    const pal_pwm_ops_t *ops; /**< PWM 操作函数表指针 */
} pal_pwm_t;

static inline pal_status_t pal_pwm_set_duty_permille(const pal_pwm_t *pwm, uint16_t duty_permille) {
    if (pwm == NULL || pwm->ops == NULL || pwm->ops->set_duty_permille == NULL || duty_permille > 1000U) {
        return PAL_ERR_PARAM;
    }
    return pwm->ops->set_duty_permille(pwm->ctx, duty_permille);
}

static inline pal_status_t pal_pwm_enable(const pal_pwm_t *pwm) {
    if (pwm == NULL || pwm->ops == NULL || pwm->ops->enable == NULL) {
        return PAL_ERR_PARAM;
    }
    return pwm->ops->enable(pwm->ctx);
}

static inline pal_status_t pal_pwm_disable(const pal_pwm_t *pwm) {
    if (pwm == NULL || pwm->ops == NULL || pwm->ops->disable == NULL) {
        return PAL_ERR_PARAM;
    }
    return pwm->ops->disable(pwm->ctx);
}

#ifdef __cplusplus
}
#endif

#endif /* PAL_PWM_H */
"""

    pal_adc = """\
/**
 * @file pal_adc.h
 * @brief 跨芯片模数转换（ADC）采样通道抽象接口契约。
 */

#ifndef PAL_ADC_H
#define PAL_ADC_H

#include "pal_types.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief ADC 采样通道操作虚函数表。
 */
typedef struct {
    /**
     * @brief 读取 ADC 原始量化码值。
     */
    pal_status_t (*read_raw)(void *ctx, uint32_t *out_raw);

    /**
     * @brief 读取换算后的毫伏电压值（mV）。
     */
    pal_status_t (*read_mv)(void *ctx, int32_t *out_mv);
} pal_adc_ops_t;

/**
 * @brief ADC 采样通道抽象句柄。
 */
typedef struct {
    void *ctx;                /**< 底层 ADC 通道实例上下文 */
    const pal_adc_ops_t *ops; /**< ADC 操作函数表指针 */
} pal_adc_t;

static inline pal_status_t pal_adc_read_raw(const pal_adc_t *adc, uint32_t *out_raw) {
    if (adc == NULL || out_raw == NULL || adc->ops == NULL || adc->ops->read_raw == NULL) {
        return PAL_ERR_PARAM;
    }
    return adc->ops->read_raw(adc->ctx, out_raw);
}

static inline pal_status_t pal_adc_read_mv(const pal_adc_t *adc, int32_t *out_mv) {
    if (adc == NULL || out_mv == NULL || adc->ops == NULL || adc->ops->read_mv == NULL) {
        return PAL_ERR_PARAM;
    }
    return adc->ops->read_mv(adc->ctx, out_mv);
}

#ifdef __cplusplus
}
#endif

#endif /* PAL_ADC_H */
"""

    return {
        "include/pal/pal_types.h": pal_types,
        "include/pal/pal_gpio.h": pal_gpio,
        "include/pal/pal_i2c.h": pal_i2c,
        "include/pal/pal_spi.h": pal_spi,
        "include/pal/pal_pwm.h": pal_pwm,
        "include/pal/pal_adc.h": pal_adc,
    }


# ---------------------------------------------------------------------------
# 模板 2：BSP（板级器件驱动四元契约 + Mock PAL 主机单元测试）
# ---------------------------------------------------------------------------


def _render_bsp_module(name: str) -> dict[str, str]:
    guard = f"BSP_{name.upper()}_H"

    header = f"""\
/**
 * @file {name}.h
 * @brief `{name}` 板级器件驱动标准四元契约（cfg / ctx / data / ops）。
 *
 * @details
 *  1. 零原厂 HAL 头文件耦合，仅通过 PAL 抽象接口访问底层外设；
 *  2. 严禁隐式全局可变状态，所有运行时状态保存在 `bsp_{name}_ctx_t` 实例中，支持多路同型器件并发实例；
 *  3. 强制提供 `safe_off` 紧急安全关断契约，异常时立即切断执行器输出并拉低使能引脚。
 */

#ifndef {guard}
#define {guard}

#include "pal/pal_adc.h"
#include "pal/pal_gpio.h"
#include "pal/pal_pwm.h"
#include "pal/pal_types.h"

#ifdef __cplusplus
extern "C" {{
#endif

/**
 * @brief `{name}` 只读硬件绑定与安全边界配置结构体（四元契约之 cfg）。
 */
typedef struct {{
    const pal_adc_t *adc;          /**< 传感器采样 ADC 通道句柄（可选，不用时置 NULL） */
    const pal_pwm_t *pwm;          /**< 执行器控制 PWM 通道句柄（可选，不用时置 NULL） */
    const pal_gpio_t *enable_gpio; /**< 硬件安全使能引脚句柄（可选，不用时置 NULL） */
    int32_t min_valid_mv;          /**< 采样电压安全下限（mV），低于此值判定断线/短路故障 */
    int32_t max_valid_mv;          /**< 采样电压安全上限（mV），高于此值判定过压/过载故障 */
    uint16_t max_output_permille;  /**< 允许下发的最大控制千分比上限（0..1000‰） */
}} bsp_{name}_cfg_t;

/**
 * @brief `{name}` 采样与执行状态快照结构体（四元契约之 data）。
 */
typedef struct {{
    uint32_t raw_counts;       /**< 最近一次 ADC 原始码值 */
    int32_t measured_mv;       /**< 最近一次采样物理电压（mV） */
    uint16_t applied_permille; /**< 当前实际生效的输出千分比（0..1000‰） */
    uint32_t sample_seq;       /**< 累计采样序号 */
    bool fault_latched;        /**< 越界或通信故障锁存标志 */
}} bsp_{name}_data_t;

/**
 * @brief `{name}` 运行时实例上下文结构体（四元契约之 ctx，禁止隐式全局状态）。
 */
typedef struct {{
    const bsp_{name}_cfg_t *cfg; /**< 绑定的只读配置指针 */
    bsp_{name}_data_t snapshot;  /**< 最新数据快照 */
    bool initialized;            /**< 实例是否已完成初始化 */
    bool safe_state;             /**< 当前是否处于安全关断态 */
}} bsp_{name}_ctx_t;

/**
 * @brief `{name}` 标准驱动操作接口表（四元契约之 ops，强制包含 `safe_off`）。
 */
typedef struct {{
    pal_status_t (*init)(bsp_{name}_ctx_t *ctx, const bsp_{name}_cfg_t *cfg);
    pal_status_t (*sample)(bsp_{name}_ctx_t *ctx, bsp_{name}_data_t *out_data);
    pal_status_t (*apply)(bsp_{name}_ctx_t *ctx, uint16_t output_permille);
    pal_status_t (*safe_off)(bsp_{name}_ctx_t *ctx);
}} bsp_{name}_ops_t;

/**
 * @brief 初始化 `{name}` 驱动实例并确保硬件进入安全关断初始态。
 */
pal_status_t bsp_{name}_init(bsp_{name}_ctx_t *ctx, const bsp_{name}_cfg_t *cfg);

/**
 * @brief 执行一次传感器采样；若采样越界则锁存故障并自动触发 `safe_off`。
 */
pal_status_t bsp_{name}_sample(bsp_{name}_ctx_t *ctx, bsp_{name}_data_t *out_data);

/**
 * @brief 下发控制量千分比（若超出 `max_output_permille` 或处于故障态则拦截并返回错误）。
 */
pal_status_t bsp_{name}_apply(bsp_{name}_ctx_t *ctx, uint16_t output_permille);

/**
 * @brief 紧急安全关断：无条件将 PWM 归零并禁能、拉低硬件使能引脚。
 */
pal_status_t bsp_{name}_safe_off(bsp_{name}_ctx_t *ctx);

/**
 * @brief 获取 `{name}` 默认只读操作虚表指针。
 */
const bsp_{name}_ops_t *bsp_{name}_default_ops(void);

#ifdef __cplusplus
}}
#endif

#endif /* {guard} */
"""

    source = f"""\
/**
 * @file {name}.c
 * @brief `{name}` 板级器件驱动可移植实现（纯 C11，零原厂 HAL 依赖）。
 */

#include "{name}.h"

#include <string.h>

pal_status_t bsp_{name}_safe_off(bsp_{name}_ctx_t *ctx) {{
    if (ctx == NULL || ctx->cfg == NULL) {{
        return PAL_ERR_PARAM;
    }}

    pal_status_t result = PAL_OK;

    if (ctx->cfg->pwm != NULL) {{
        pal_status_t st_duty = pal_pwm_set_duty_permille(ctx->cfg->pwm, 0U);
        pal_status_t st_dis = pal_pwm_disable(ctx->cfg->pwm);
        if (st_duty != PAL_OK) {{
            result = st_duty;
        }} else if (st_dis != PAL_OK) {{
            result = st_dis;
        }}
    }}

    if (ctx->cfg->enable_gpio != NULL) {{
        pal_status_t st_gpio = pal_gpio_write(ctx->cfg->enable_gpio, 0U);
        if (result == PAL_OK && st_gpio != PAL_OK) {{
            result = st_gpio;
        }}
    }}

    ctx->snapshot.applied_permille = 0U;
    ctx->safe_state = true;
    return result;
}}

pal_status_t bsp_{name}_init(bsp_{name}_ctx_t *ctx, const bsp_{name}_cfg_t *cfg) {{
    if (ctx == NULL || cfg == NULL) {{
        return PAL_ERR_PARAM;
    }}
    if (cfg->min_valid_mv > cfg->max_valid_mv || cfg->max_output_permille > 1000U) {{
        return PAL_ERR_PARAM;
    }}

    memset(ctx, 0, sizeof(*ctx));
    ctx->cfg = cfg;

    pal_status_t st = bsp_{name}_safe_off(ctx);
    if (st != PAL_OK) {{
        return st;
    }}

    ctx->initialized = true;
    return PAL_OK;
}}

pal_status_t bsp_{name}_sample(bsp_{name}_ctx_t *ctx, bsp_{name}_data_t *out_data) {{
    if (ctx == NULL || !ctx->initialized || ctx->cfg == NULL) {{
        return PAL_ERR_PARAM;
    }}

    if (ctx->cfg->adc != NULL) {{
        uint32_t raw = 0U;
        int32_t mv = 0;

        if (ctx->cfg->adc->ops != NULL && ctx->cfg->adc->ops->read_raw != NULL) {{
            pal_status_t st_raw = pal_adc_read_raw(ctx->cfg->adc, &raw);
            if (st_raw != PAL_OK) {{
                ctx->snapshot.fault_latched = true;
                (void)bsp_{name}_safe_off(ctx);
                return st_raw;
            }}
            ctx->snapshot.raw_counts = raw;
        }}

        pal_status_t st_mv = pal_adc_read_mv(ctx->cfg->adc, &mv);
        if (st_mv != PAL_OK) {{
            ctx->snapshot.fault_latched = true;
            (void)bsp_{name}_safe_off(ctx);
            return st_mv;
        }}

        ctx->snapshot.measured_mv = mv;
        ctx->snapshot.sample_seq += 1U;

        if (mv < ctx->cfg->min_valid_mv || mv > ctx->cfg->max_valid_mv) {{
            ctx->snapshot.fault_latched = true;
            (void)bsp_{name}_safe_off(ctx);
            if (out_data != NULL) {{
                *out_data = ctx->snapshot;
            }}
            return PAL_ERR_IO;
        }}
    }}

    if (out_data != NULL) {{
        *out_data = ctx->snapshot;
    }}
    return PAL_OK;
}}

pal_status_t bsp_{name}_apply(bsp_{name}_ctx_t *ctx, uint16_t output_permille) {{
    if (ctx == NULL || !ctx->initialized || ctx->cfg == NULL) {{
        return PAL_ERR_PARAM;
    }}
    if (ctx->snapshot.fault_latched) {{
        return PAL_ERR_IO;
    }}
    if (output_permille > ctx->cfg->max_output_permille || output_permille > 1000U) {{
        return PAL_ERR_PARAM;
    }}

    if (ctx->cfg->enable_gpio != NULL) {{
        uint8_t en_level = (output_permille > 0U) ? 1U : 0U;
        pal_status_t st_en = pal_gpio_write(ctx->cfg->enable_gpio, en_level);
        if (st_en != PAL_OK) {{
            return st_en;
        }}
    }}

    if (ctx->cfg->pwm != NULL) {{
        pal_status_t st_duty = pal_pwm_set_duty_permille(ctx->cfg->pwm, output_permille);
        if (st_duty != PAL_OK) {{
            return st_duty;
        }}
        if (output_permille > 0U) {{
            pal_status_t st_pwm_en = pal_pwm_enable(ctx->cfg->pwm);
            if (st_pwm_en != PAL_OK) {{
                return st_pwm_en;
            }}
        }} else {{
            pal_status_t st_pwm_dis = pal_pwm_disable(ctx->cfg->pwm);
            if (st_pwm_dis != PAL_OK) {{
                return st_pwm_dis;
            }}
        }}
    }}

    ctx->snapshot.applied_permille = output_permille;
    ctx->safe_state = (output_permille == 0U);
    return PAL_OK;
}}

static const bsp_{name}_ops_t k_bsp_{name}_default_ops = {{
    .init = bsp_{name}_init,
    .sample = bsp_{name}_sample,
    .apply = bsp_{name}_apply,
    .safe_off = bsp_{name}_safe_off,
}};

const bsp_{name}_ops_t *bsp_{name}_default_ops(void) {{
    return &k_bsp_{name}_default_ops;
}}
"""

    host_test = f"""\
/**
 * @file test_bsp_{name}.c
 * @brief `{name}` BSP 驱动零硬件宿主机单元测试（内置 Mock PAL 桩）。
 */

#include "bsp/{name}.h"

#include <stdio.h>

#define TEST_ASSERT(cond)                                                           \\
    do {{                                                                           \\
        if (!(cond)) {{                                                             \\
            fprintf(stderr, "[FAIL] %s:%d: %s\\n", __FILE__, __LINE__, #cond);      \\
            return 1;                                                               \\
        }}                                                                          \\
    }} while (0)

typedef struct {{
    uint32_t raw_val;
    int32_t mv_val;
    pal_status_t next_status;
}} mock_adc_t;

typedef struct {{
    uint16_t duty_permille;
    bool enabled;
}} mock_pwm_t;

typedef struct {{
    uint8_t level;
}} mock_gpio_t;

static pal_status_t mock_adc_read_raw(void *ctx, uint32_t *out_raw) {{
    mock_adc_t *m = (mock_adc_t *)ctx;
    if (m->next_status != PAL_OK) {{
        return m->next_status;
    }}
    *out_raw = m->raw_val;
    return PAL_OK;
}}

static pal_status_t mock_adc_read_mv(void *ctx, int32_t *out_mv) {{
    mock_adc_t *m = (mock_adc_t *)ctx;
    if (m->next_status != PAL_OK) {{
        return m->next_status;
    }}
    *out_mv = m->mv_val;
    return PAL_OK;
}}

static pal_status_t mock_pwm_set_duty(void *ctx, uint16_t duty_permille) {{
    mock_pwm_t *m = (mock_pwm_t *)ctx;
    m->duty_permille = duty_permille;
    return PAL_OK;
}}

static pal_status_t mock_pwm_enable(void *ctx) {{
    mock_pwm_t *m = (mock_pwm_t *)ctx;
    m->enabled = true;
    return PAL_OK;
}}

static pal_status_t mock_pwm_disable(void *ctx) {{
    mock_pwm_t *m = (mock_pwm_t *)ctx;
    m->enabled = false;
    return PAL_OK;
}}

static pal_status_t mock_gpio_write(void *ctx, uint8_t level) {{
    mock_gpio_t *m = (mock_gpio_t *)ctx;
    m->level = (level != 0U) ? 1U : 0U;
    return PAL_OK;
}}

static pal_status_t mock_gpio_read(void *ctx, uint8_t *out_level) {{
    mock_gpio_t *m = (mock_gpio_t *)ctx;
    *out_level = m->level;
    return PAL_OK;
}}

static pal_status_t mock_gpio_toggle(void *ctx) {{
    mock_gpio_t *m = (mock_gpio_t *)ctx;
    m->level = (m->level == 0U) ? 1U : 0U;
    return PAL_OK;
}}

static const pal_adc_ops_t k_mock_adc_ops = {{
    .read_raw = mock_adc_read_raw,
    .read_mv = mock_adc_read_mv,
}};

static const pal_pwm_ops_t k_mock_pwm_ops = {{
    .set_duty_permille = mock_pwm_set_duty,
    .enable = mock_pwm_enable,
    .disable = mock_pwm_disable,
}};

static const pal_gpio_ops_t k_mock_gpio_ops = {{
    .write = mock_gpio_write,
    .read = mock_gpio_read,
    .toggle = mock_gpio_toggle,
}};

int main(void) {{
    mock_adc_t adc_hw = {{ .raw_val = 2048U, .mv_val = 1650, .next_status = PAL_OK }};
    mock_pwm_t pwm_hw = {{ .duty_permille = 500U, .enabled = true }};
    mock_gpio_t gpio_hw = {{ .level = 1U }};

    pal_adc_t pal_adc = {{ .ctx = &adc_hw, .ops = &k_mock_adc_ops }};
    pal_pwm_t pal_pwm = {{ .ctx = &pwm_hw, .ops = &k_mock_pwm_ops }};
    pal_gpio_t pal_gpio = {{ .ctx = &gpio_hw, .ops = &k_mock_gpio_ops }};

    bsp_{name}_cfg_t cfg = {{
        .adc = &pal_adc,
        .pwm = &pal_pwm,
        .enable_gpio = &pal_gpio,
        .min_valid_mv = 200,
        .max_valid_mv = 3100,
        .max_output_permille = 800U,
    }};

    const bsp_{name}_ops_t *ops = bsp_{name}_default_ops();
    bsp_{name}_ctx_t dev;

    /* 1. 验证初始化自动进入安全关断态 */
    TEST_ASSERT(ops->init(&dev, &cfg) == PAL_OK);
    TEST_ASSERT(dev.initialized == true);
    TEST_ASSERT(dev.safe_state == true);
    TEST_ASSERT(pwm_hw.duty_permille == 0U);
    TEST_ASSERT(pwm_hw.enabled == false);
    TEST_ASSERT(gpio_hw.level == 0U);

    /* 2. 验证正常采样与合规控制输出 */
    bsp_{name}_data_t snap;
    TEST_ASSERT(ops->sample(&dev, &snap) == PAL_OK);
    TEST_ASSERT(snap.measured_mv == 1650);
    TEST_ASSERT(snap.raw_counts == 2048U);
    TEST_ASSERT(snap.sample_seq == 1U);
    TEST_ASSERT(snap.fault_latched == false);

    TEST_ASSERT(ops->apply(&dev, 600U) == PAL_OK);
    TEST_ASSERT(pwm_hw.duty_permille == 600U);
    TEST_ASSERT(pwm_hw.enabled == true);
    TEST_ASSERT(gpio_hw.level == 1U);
    TEST_ASSERT(dev.safe_state == false);

    /* 3. 验证越界控制量拦截（> max_output_permille 800‰） */
    TEST_ASSERT(ops->apply(&dev, 900U) == PAL_ERR_PARAM);
    TEST_ASSERT(pwm_hw.duty_permille == 600U);

    /* 4. 验证采样越界自动锁存故障并执行 safe_off 紧急安全关断 */
    adc_hw.mv_val = 3250;
    TEST_ASSERT(ops->sample(&dev, &snap) == PAL_ERR_IO);
    TEST_ASSERT(snap.fault_latched == true);
    TEST_ASSERT(dev.safe_state == true);
    TEST_ASSERT(pwm_hw.duty_permille == 0U);
    TEST_ASSERT(pwm_hw.enabled == false);
    TEST_ASSERT(gpio_hw.level == 0U);

    /* 故障锁存态下拒绝再次下发控制量 */
    TEST_ASSERT(ops->apply(&dev, 300U) == PAL_ERR_IO);

    printf("[PASS] test_bsp_{name}\\n");
    return 0;
}}
"""

    return {
        f"bsp/{name}.h": header,
        f"bsp/{name}.c": source,
        f"tests/host/test_bsp_{name}.c": host_test,
    }


# ---------------------------------------------------------------------------
# 模板 3：Service（纯业务逻辑非阻塞状态机 + 主机单元测试）
# ---------------------------------------------------------------------------


def _render_service_module(name: str) -> dict[str, str]:
    upper = name.upper()
    guard = f"SERVICE_{upper}_SERVICE_H"

    header = f"""\
/**
 * @file {name}_service.h
 * @brief `{name}` 领域服务层平台无关非阻塞状态机契约。
 *
 * @details
 *  1. 完全不依赖任何芯片寄存器、RTOS 或原厂 HAL 头文件，仅使用标准 C99/C11 类型；
 *  2. 采用显式 `step_tick(dt_ms)` 非阻塞节拍推进，严禁任何阻塞延时；
 *  3. 内置 `IDLE / RUNNING / FAULT` 三态流转与 `safe_off` 紧急关断钩子。
 */

#ifndef {guard}
#define {guard}

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {{
#endif

/**
 * @brief `{name}` 服务运行状态枚举。
 */
typedef enum {{
    SERVICE_{upper}_STATE_IDLE = 0,    /**< 待机空闲态（输出归零） */
    SERVICE_{upper}_STATE_RUNNING = 1, /**< 闭环/受控运行态 */
    SERVICE_{upper}_STATE_FAULT = 2    /**< 故障安全锁存态 */
}} service_{name}_state_t;

/**
 * @brief `{name}` 服务返回码枚举。
 */
typedef enum {{
    SERVICE_{upper}_OK = 0,
    SERVICE_{upper}_ERR_PARAM = -1,
    SERVICE_{upper}_ERR_STATE = -2,
    SERVICE_{upper}_ERR_FAULT = -3
}} service_{name}_status_t;

/**
 * @brief `{name}` 服务只读配置与底层执行回调绑定。
 */
typedef struct {{
    int32_t min_setpoint;                        /**< 允许的设定值下限 */
    int32_t max_setpoint;                        /**< 允许的设定值上限 */
    uint32_t max_continuous_run_ms;              /**< 单次最长连续运行超时保护（0 表示不限时） */
    void *cb_user_data;                          /**< 回调上下文指针（绑定下层 BSP 实例等） */
    int (*apply_output_cb)(void *user_data, int32_t setpoint); /**< 输出下发回调（可选） */
    void (*safe_off_cb)(void *user_data);        /**< 紧急安全关断回调（可选） */
}} service_{name}_cfg_t;

/**
 * @brief `{name}` 服务运行时状态机实例上下文（多实例安全，零全局变量）。
 */
typedef struct {{
    const service_{name}_cfg_t *cfg; /**< 绑定的只读配置 */
    service_{name}_state_t state;    /**< 当前状态机状态 */
    int32_t target_setpoint;         /**< 当前目标设定值 */
    uint32_t active_elapsed_ms;      /**< 本轮运行已持续毫秒数 */
    uint32_t total_ticks;            /**< 累计节拍推进次数 */
    uint32_t safe_off_count;         /**< 累计触发安全关断次数 */
    bool initialized;                /**< 初始化完成标志 */
}} service_{name}_ctx_t;

/**
 * @brief 初始化 `{name}` 服务状态机实例，初始进入 `IDLE` 安全态。
 */
service_{name}_status_t service_{name}_init(
    service_{name}_ctx_t *ctx,
    const service_{name}_cfg_t *cfg
);

/**
 * @brief 请求启动服务并加载目标设定值（仅在非 `FAULT` 且设定值合法时切入 `RUNNING`）。
 */
service_{name}_status_t service_{name}_request_start(
    service_{name}_ctx_t *ctx,
    int32_t target_setpoint
);

/**
 * @brief 请求正常停机并归零输出（从 `RUNNING` 平滑回到 `IDLE`）。
 */
service_{name}_status_t service_{name}_request_stop(service_{name}_ctx_t *ctx);

/**
 * @brief 非阻塞周期节拍推进函数（由主循环或定时任务按固定 `dt_ms` 调用）。
 */
service_{name}_status_t service_{name}_step_tick(
    service_{name}_ctx_t *ctx,
    uint32_t dt_ms
);

/**
 * @brief 紧急安全关断：清零目标输出、触发底层 `safe_off_cb`，若未处于 `FAULT` 则回到 `IDLE`。
 */
service_{name}_status_t service_{name}_safe_off(service_{name}_ctx_t *ctx);

#ifdef __cplusplus
}}
#endif

#endif /* {guard} */
"""

    source = f"""\
/**
 * @file {name}_service.c
 * @brief `{name}` 领域服务非阻塞状态机实现（纯标准 C11，零硬件头文件依赖）。
 */

#include "{name}_service.h"

#include <string.h>

service_{name}_status_t service_{name}_safe_off(service_{name}_ctx_t *ctx) {{
    if (ctx == NULL || ctx->cfg == NULL) {{
        return SERVICE_{upper}_ERR_PARAM;
    }}

    ctx->target_setpoint = 0;
    ctx->active_elapsed_ms = 0U;
    ctx->safe_off_count += 1U;

    if (ctx->cfg->safe_off_cb != NULL) {{
        ctx->cfg->safe_off_cb(ctx->cfg->cb_user_data);
    }}

    if (ctx->state != SERVICE_{upper}_STATE_FAULT) {{
        ctx->state = SERVICE_{upper}_STATE_IDLE;
    }}
    return SERVICE_{upper}_OK;
}}

service_{name}_status_t service_{name}_init(
    service_{name}_ctx_t *ctx,
    const service_{name}_cfg_t *cfg
) {{
    if (ctx == NULL || cfg == NULL || cfg->min_setpoint > cfg->max_setpoint) {{
        return SERVICE_{upper}_ERR_PARAM;
    }}

    memset(ctx, 0, sizeof(*ctx));
    ctx->cfg = cfg;
    ctx->state = SERVICE_{upper}_STATE_IDLE;
    ctx->initialized = true;

    if (cfg->safe_off_cb != NULL) {{
        cfg->safe_off_cb(cfg->cb_user_data);
    }}
    return SERVICE_{upper}_OK;
}}

service_{name}_status_t service_{name}_request_start(
    service_{name}_ctx_t *ctx,
    int32_t target_setpoint
) {{
    if (ctx == NULL || !ctx->initialized || ctx->cfg == NULL) {{
        return SERVICE_{upper}_ERR_PARAM;
    }}
    if (ctx->state == SERVICE_{upper}_STATE_FAULT) {{
        return SERVICE_{upper}_ERR_STATE;
    }}
    if (target_setpoint < ctx->cfg->min_setpoint || target_setpoint > ctx->cfg->max_setpoint) {{
        return SERVICE_{upper}_ERR_PARAM;
    }}

    ctx->target_setpoint = target_setpoint;
    ctx->active_elapsed_ms = 0U;
    ctx->state = SERVICE_{upper}_STATE_RUNNING;
    return SERVICE_{upper}_OK;
}}

service_{name}_status_t service_{name}_request_stop(service_{name}_ctx_t *ctx) {{
    if (ctx == NULL || !ctx->initialized) {{
        return SERVICE_{upper}_ERR_PARAM;
    }}
    if (ctx->state == SERVICE_{upper}_STATE_FAULT) {{
        return SERVICE_{upper}_ERR_STATE;
    }}

    return service_{name}_safe_off(ctx);
}}

service_{name}_status_t service_{name}_step_tick(
    service_{name}_ctx_t *ctx,
    uint32_t dt_ms
) {{
    if (ctx == NULL || !ctx->initialized || ctx->cfg == NULL) {{
        return SERVICE_{upper}_ERR_PARAM;
    }}

    ctx->total_ticks += 1U;

    if (ctx->state == SERVICE_{upper}_STATE_FAULT) {{
        return SERVICE_{upper}_ERR_FAULT;
    }}

    if (ctx->state == SERVICE_{upper}_STATE_IDLE) {{
        return SERVICE_{upper}_OK;
    }}

    /* RUNNING 状态：检查连续运行超时保护并驱动执行回调 */
    ctx->active_elapsed_ms += dt_ms;
    if (ctx->cfg->max_continuous_run_ms > 0U &&
        ctx->active_elapsed_ms > ctx->cfg->max_continuous_run_ms) {{
        ctx->state = SERVICE_{upper}_STATE_FAULT;
        (void)service_{name}_safe_off(ctx);
        return SERVICE_{upper}_ERR_FAULT;
    }}

    if (ctx->cfg->apply_output_cb != NULL) {{
        int rc = ctx->cfg->apply_output_cb(ctx->cfg->cb_user_data, ctx->target_setpoint);
        if (rc != 0) {{
            ctx->state = SERVICE_{upper}_STATE_FAULT;
            (void)service_{name}_safe_off(ctx);
            return SERVICE_{upper}_ERR_FAULT;
        }}
    }}

    return SERVICE_{upper}_OK;
}}
"""

    host_test = f"""\
/**
 * @file test_service_{name}.c
 * @brief `{name}` 领域服务非阻塞状态机宿主机单元测试。
 */

#include "service/{name}_service.h"

#include <stdio.h>

#define TEST_ASSERT(cond)                                                           \\
    do {{                                                                           \\
        if (!(cond)) {{                                                             \\
            fprintf(stderr, "[FAIL] %s:%d: %s\\n", __FILE__, __LINE__, #cond);      \\
            return 1;                                                               \\
        }}                                                                          \\
    }} while (0)

typedef struct {{
    int32_t last_applied_setpoint;
    uint32_t apply_calls;
    uint32_t safe_off_calls;
    int inject_apply_err;
}} mock_actuator_sink_t;

static int mock_apply_cb(void *user_data, int32_t setpoint) {{
    mock_actuator_sink_t *sink = (mock_actuator_sink_t *)user_data;
    sink->apply_calls += 1U;
    if (sink->inject_apply_err != 0) {{
        return sink->inject_apply_err;
    }}
    sink->last_applied_setpoint = setpoint;
    return 0;
}}

static void mock_safe_off_cb(void *user_data) {{
    mock_actuator_sink_t *sink = (mock_actuator_sink_t *)user_data;
    sink->last_applied_setpoint = 0;
    sink->safe_off_calls += 1U;
}}

int main(void) {{
    mock_actuator_sink_t sink = {{
        .last_applied_setpoint = -1,
        .apply_calls = 0U,
        .safe_off_calls = 0U,
        .inject_apply_err = 0,
    }};

    service_{name}_cfg_t cfg = {{
        .min_setpoint = 0,
        .max_setpoint = 500,
        .max_continuous_run_ms = 1000U,
        .cb_user_data = &sink,
        .apply_output_cb = mock_apply_cb,
        .safe_off_cb = mock_safe_off_cb,
    }};

    service_{name}_ctx_t svc;

    /* 1. 验证初始化进入 IDLE 且执行初始安全关断 */
    TEST_ASSERT(service_{name}_init(&svc, &cfg) == SERVICE_{upper}_OK);
    TEST_ASSERT(svc.state == SERVICE_{upper}_STATE_IDLE);
    TEST_ASSERT(sink.safe_off_calls == 1U);

    /* 2. 验证越界设定值被拒绝 */
    TEST_ASSERT(service_{name}_request_start(&svc, 800) == SERVICE_{upper}_ERR_PARAM);
    TEST_ASSERT(svc.state == SERVICE_{upper}_STATE_IDLE);

    /* 3. 验证启动 -> step_tick 非阻塞推进 -> request_stop 平滑回到 IDLE */
    TEST_ASSERT(service_{name}_request_start(&svc, 350) == SERVICE_{upper}_OK);
    TEST_ASSERT(svc.state == SERVICE_{upper}_STATE_RUNNING);
    TEST_ASSERT(service_{name}_step_tick(&svc, 200U) == SERVICE_{upper}_OK);
    TEST_ASSERT(sink.last_applied_setpoint == 350);
    TEST_ASSERT(svc.active_elapsed_ms == 200U);

    TEST_ASSERT(service_{name}_request_stop(&svc) == SERVICE_{upper}_OK);
    TEST_ASSERT(svc.state == SERVICE_{upper}_STATE_IDLE);
    TEST_ASSERT(sink.last_applied_setpoint == 0);

    /* 4. 验证连续运行超时自动切入 FAULT 并触发 safe_off */
    TEST_ASSERT(service_{name}_request_start(&svc, 250) == SERVICE_{upper}_OK);
    TEST_ASSERT(service_{name}_step_tick(&svc, 600U) == SERVICE_{upper}_OK);
    TEST_ASSERT(service_{name}_step_tick(&svc, 500U) == SERVICE_{upper}_ERR_FAULT);
    TEST_ASSERT(svc.state == SERVICE_{upper}_STATE_FAULT);
    TEST_ASSERT(sink.last_applied_setpoint == 0);

    /* FAULT 锁存态下拒绝再次启动 */
    TEST_ASSERT(service_{name}_request_start(&svc, 100) == SERVICE_{upper}_ERR_STATE);

    printf("[PASS] test_service_{name}\\n");
    return 0;
}}
"""

    return {
        f"service/{name}_service.h": header,
        f"service/{name}_service.c": source,
        f"tests/host/test_service_{name}.c": host_test,
    }


# ---------------------------------------------------------------------------
# 模板 4：Skeleton（完整四层工程骨架：TOML + CMake + App + 端到端主机测试）
# ---------------------------------------------------------------------------


def _render_skeleton_top_files() -> dict[str, str]:
    manifest_toml = """\
# MCU Agent Harness 四层可移植工程配置文件

[target]
device = "STM32H563ZI"
probe_backend = "mock"
interface = "SWD"
speed = "auto"

[build]
system = "cmake"
build_type = "Debug"
generator = "Ninja"

[safety]
allow_flash_without_confirm = false
allow_peripheral_write_without_confirm = false
allow_option_bytes = false
protected_peripherals = ["FLASH", "PWR", "RCC", "TIM1", "TIM8", "HRTIM1", "IWDG", "WWDG"]

[architecture]
preset = "layered-4"
"""

    cmake_lists = """\
cmake_minimum_required(VERSION 3.16)
project(mcu_portable_firmware VERSION 0.1.0 LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

if(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
    add_compile_options(-Wall -Wextra -Werror)
endif()

file(GLOB BSP_SOURCES CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/bsp/*.c")
file(GLOB SERVICE_SOURCES CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/service/*.c")
file(GLOB APP_SOURCES CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/app/*.c")

add_library(fw_core STATIC
    ${BSP_SOURCES}
    ${SERVICE_SOURCES}
    ${APP_SOURCES}
)

target_include_directories(fw_core PUBLIC
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
    "${CMAKE_CURRENT_SOURCE_DIR}"
    "${CMAKE_CURRENT_SOURCE_DIR}/bsp"
    "${CMAKE_CURRENT_SOURCE_DIR}/service"
    "${CMAKE_CURRENT_SOURCE_DIR}/app"
)

enable_testing()

file(GLOB HOST_TEST_SOURCES CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/tests/host/test_*.c")
foreach(test_src IN LISTS HOST_TEST_SOURCES)
    get_filename_component(test_name "${test_src}" NAME_WE)
    add_executable(${test_name} "${test_src}")
    target_link_libraries(${test_name} PRIVATE fw_core)
    add_test(NAME ${test_name} COMMAND ${test_name})
endforeach()
"""

    app_main_h = """\
/**
 * @file app_main.h
 * @brief 应用编排层（App Layer）主控制器契约：装配 Service 与 BSP，零原厂 HAL 依赖。
 */

#ifndef APP_MAIN_H
#define APP_MAIN_H

#include "bsp/demo_sensor.h"
#include "pal/pal_types.h"
#include "service/demo_ctrl_service.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief 应用层顶层运行实例上下文（多实例可测，零隐式全局状态）。
 */
typedef struct {
    bsp_demo_sensor_ctx_t sensor;       /**< 板级传感器/执行器驱动实例 */
    service_demo_ctrl_cfg_t ctrl_cfg;   /**< 控制服务配置表 */
    service_demo_ctrl_ctx_t ctrl_svc;   /**< 控制服务状态机实例 */
    bsp_demo_sensor_data_t last_sample; /**< 最新采样数据快照 */
    bool initialized;                   /**< 应用初始化完成标志 */
} app_main_ctx_t;

/**
 * @brief 初始化应用层并完成 Service -> BSP 回调装配。
 */
pal_status_t app_main_init(app_main_ctx_t *app, const bsp_demo_sensor_cfg_t *sensor_cfg);

/**
 * @brief 应用层非阻塞主循环节拍函数（先采样验证安全边界，再推进业务状态机）。
 */
pal_status_t app_main_step_tick(app_main_ctx_t *app, uint32_t dt_ms);

/**
 * @brief 应用层全局紧急停机入口。
 */
void app_main_emergency_stop(app_main_ctx_t *app);

#ifdef __cplusplus
}
#endif

#endif /* APP_MAIN_H */
"""

    app_main_c = """\
/**
 * @file app_main.c
 * @brief 应用编排层实现：连接 `demo_ctrl` 领域服务与 `demo_sensor` 板级驱动。
 */

#include "app_main.h"

#include <string.h>

static int app_bridge_apply_output(void *user_data, int32_t setpoint) {
    bsp_demo_sensor_ctx_t *sensor = (bsp_demo_sensor_ctx_t *)user_data;
    if (sensor == NULL || setpoint < 0 || setpoint > 1000) {
        return -1;
    }
    pal_status_t st = bsp_demo_sensor_apply(sensor, (uint16_t)setpoint);
    return (st == PAL_OK) ? 0 : -1;
}

static void app_bridge_safe_off(void *user_data) {
    bsp_demo_sensor_ctx_t *sensor = (bsp_demo_sensor_ctx_t *)user_data;
    if (sensor != NULL) {
        (void)bsp_demo_sensor_safe_off(sensor);
    }
}

pal_status_t app_main_init(app_main_ctx_t *app, const bsp_demo_sensor_cfg_t *sensor_cfg) {
    if (app == NULL || sensor_cfg == NULL) {
        return PAL_ERR_PARAM;
    }

    memset(app, 0, sizeof(*app));

    pal_status_t st_bsp = bsp_demo_sensor_init(&app->sensor, sensor_cfg);
    if (st_bsp != PAL_OK) {
        return st_bsp;
    }

    app->ctrl_cfg.min_setpoint = 0;
    app->ctrl_cfg.max_setpoint = (int32_t)sensor_cfg->max_output_permille;
    app->ctrl_cfg.max_continuous_run_ms = 5000U;
    app->ctrl_cfg.cb_user_data = &app->sensor;
    app->ctrl_cfg.apply_output_cb = app_bridge_apply_output;
    app->ctrl_cfg.safe_off_cb = app_bridge_safe_off;

    if (service_demo_ctrl_init(&app->ctrl_svc, &app->ctrl_cfg) != SERVICE_DEMO_CTRL_OK) {
        return PAL_ERR_PARAM;
    }

    app->initialized = true;
    return PAL_OK;
}

pal_status_t app_main_step_tick(app_main_ctx_t *app, uint32_t dt_ms) {
    if (app == NULL || !app->initialized) {
        return PAL_ERR_PARAM;
    }

    pal_status_t st_sample = bsp_demo_sensor_sample(&app->sensor, &app->last_sample);
    if (st_sample != PAL_OK) {
        (void)service_demo_ctrl_safe_off(&app->ctrl_svc);
        return st_sample;
    }

    service_demo_ctrl_status_t st_svc = service_demo_ctrl_step_tick(&app->ctrl_svc, dt_ms);
    if (st_svc != SERVICE_DEMO_CTRL_OK) {
        return PAL_ERR_IO;
    }

    return PAL_OK;
}

void app_main_emergency_stop(app_main_ctx_t *app) {
    if (app == NULL || !app->initialized) {
        return;
    }
    (void)service_demo_ctrl_safe_off(&app->ctrl_svc);
    (void)bsp_demo_sensor_safe_off(&app->sensor);
}
"""

    test_app_main = """\
/**
 * @file test_app_main.c
 * @brief 四层架构（App -> Service -> BSP -> Mock PAL）端到端宿主机联调单测。
 */

#include "app/app_main.h"

#include <stdio.h>

#define TEST_ASSERT(cond)                                                           \\
    do {                                                                            \\
        if (!(cond)) {                                                              \\
            fprintf(stderr, "[FAIL] %s:%d: %s\\n", __FILE__, __LINE__, #cond);      \\
            return 1;                                                               \\
        }                                                                           \\
    } while (0)

typedef struct {
    int32_t mv;
    uint16_t pwm_permille;
    bool pwm_enabled;
} sim_hw_t;

static pal_status_t sim_adc_read_mv(void *ctx, int32_t *out_mv) {
    sim_hw_t *hw = (sim_hw_t *)ctx;
    *out_mv = hw->mv;
    return PAL_OK;
}

static pal_status_t sim_pwm_set_duty(void *ctx, uint16_t duty_permille) {
    sim_hw_t *hw = (sim_hw_t *)ctx;
    hw->pwm_permille = duty_permille;
    return PAL_OK;
}

static pal_status_t sim_pwm_enable(void *ctx) {
    sim_hw_t *hw = (sim_hw_t *)ctx;
    hw->pwm_enabled = true;
    return PAL_OK;
}

static pal_status_t sim_pwm_disable(void *ctx) {
    sim_hw_t *hw = (sim_hw_t *)ctx;
    hw->pwm_enabled = false;
    return PAL_OK;
}

static const pal_adc_ops_t k_sim_adc_ops = {
    .read_raw = NULL,
    .read_mv = sim_adc_read_mv,
};

static const pal_pwm_ops_t k_sim_pwm_ops = {
    .set_duty_permille = sim_pwm_set_duty,
    .enable = sim_pwm_enable,
    .disable = sim_pwm_disable,
};

int main(void) {
    sim_hw_t hw = { .mv = 1500, .pwm_permille = 0U, .pwm_enabled = false };
    pal_adc_t adc = { .ctx = &hw, .ops = &k_sim_adc_ops };
    pal_pwm_t pwm = { .ctx = &hw, .ops = &k_sim_pwm_ops };

    bsp_demo_sensor_cfg_t sensor_cfg = {
        .adc = &adc,
        .pwm = &pwm,
        .enable_gpio = NULL,
        .min_valid_mv = 100,
        .max_valid_mv = 3000,
        .max_output_permille = 750U,
    };

    app_main_ctx_t app;
    TEST_ASSERT(app_main_init(&app, &sensor_cfg) == PAL_OK);
    TEST_ASSERT(service_demo_ctrl_request_start(&app.ctrl_svc, 500) == SERVICE_DEMO_CTRL_OK);
    TEST_ASSERT(app_main_step_tick(&app, 50U) == PAL_OK);
    TEST_ASSERT(hw.pwm_permille == 500U);
    TEST_ASSERT(hw.pwm_enabled == true);

    app_main_emergency_stop(&app);
    TEST_ASSERT(hw.pwm_permille == 0U);
    TEST_ASSERT(hw.pwm_enabled == false);

    printf("[PASS] test_app_main\\n");
    return 0;
}
"""

    return {
        "mcu-harness.toml": manifest_toml,
        "CMakeLists.txt": cmake_lists,
        "app/app_main.h": app_main_h,
        "app/app_main.c": app_main_c,
        "tests/host/test_app_main.c": test_app_main,
    }
