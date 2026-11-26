#include "controllers/DemoSeeder.h"
#include "controllers/WorkbenchController.h"
#include "core/DiagnosticsService.h"
#include "core/UpdateManager.h"
#include "models/AuditModel.h"
#include "models/ConversationModel.h"
#include "models/LogModel.h"
#include "models/RtosTaskModel.h"
#include "models/SessionModel.h"
#include "models/WatchModel.h"

#include <QDateTime>
#include <QJsonArray>
#include <QJsonObject>

void DemoSeeder::seed(WorkbenchController *wb,
                      UpdateManager *updater,
                      DiagnosticsService *diagnostics,
                      const QString &state) {
    if (!wb) {
        return;
    }

    wb->setEngineReadyDemo(QStringLiteral("deepseek"),
                           QStringLiteral("deepseek-chat"),
                           QStringLiteral("interactive"),
                           QStringLiteral("sess-20261126-foc"));
    wb->setTokenUsageText(QStringLiteral("tokens: 3,420 (2,680 in / 740 out)"));

    QVariantMap target;
    target.insert(QStringLiteral("projectDir"), QStringLiteral("/workspace/stm32h563_foc_drive"));
    target.insert(QStringLiteral("device"), QStringLiteral("STM32H563VIT6"));
    target.insert(QStringLiteral("core"), QStringLiteral("Cortex-M33"));
    target.insert(QStringLiteral("buildSystem"), QStringLiteral("cmake"));
    target.insert(QStringLiteral("elfPath"), QStringLiteral("/workspace/stm32h563_foc_drive/build/Release/motor_foc.elf"));
    target.insert(QStringLiteral("elfExists"), true);
    target.insert(QStringLiteral("svdPath"), QStringLiteral("/workspace/stm32h563_foc_drive/svd/STM32H563.svd"));
    target.insert(QStringLiteral("svdExists"), true);
    target.insert(QStringLiteral("iocPath"), QStringLiteral("/workspace/stm32h563_foc_drive/motor_foc.ioc"));
    target.insert(QStringLiteral("iocExists"), true);
    wb->setTargetMap(target);

    QVariantMap probe;
    probe.insert(QStringLiteral("connected"), true);
    probe.insert(QStringLiteral("halted"), false);
    probe.insert(QStringLiteral("device"), QStringLiteral("STM32H563VIT6"));
    probe.insert(QStringLiteral("backend"), QStringLiteral("jlink"));
    probe.insert(QStringLiteral("interface"), QStringLiteral("SWD"));
    probe.insert(QStringLiteral("serialNo"), QStringLiteral("601029481"));
    wb->setProbeMap(probe);

    QVariantMap rtt;
    rtt.insert(QStringLiteral("running"), true);
    rtt.insert(QStringLiteral("channel"), 0);
    rtt.insert(QStringLiteral("bufferedLines"), 6);
    rtt.insert(QStringLiteral("bytesReceived"), 412);
    wb->setRttMap(rtt);

    QVariantMap uart;
    uart.insert(QStringLiteral("running"), true);
    uart.insert(QStringLiteral("port"), QStringLiteral("/dev/ttyACM0"));
    uart.insert(QStringLiteral("baudrate"), 115200);
    uart.insert(QStringLiteral("bufferedLines"), 3);
    uart.insert(QStringLiteral("bytesReceived"), 188);
    wb->setUartMap(uart);

    wb->setStatusCardText(QStringLiteral(
        "### Hardware & Project Status\n"
        "- **Project**: `/workspace/stm32h563_foc_drive`\n"
        "- **Target**: `STM32H563VIT6` | Core: `Cortex-M33` | Build: `cmake`\n"
        "- **Artifacts**: ELF: OK (`motor_foc.elf`) | SVD: OK (`STM32H563.svd`) | IOC: OK (`motor_foc.ioc`)\n"
        "- **Probe**: `connected` (running) | backend=`jlink` | device=`STM32H563VIT6`\n"
        "- **Debug Logs**: RTT=`running` (6 lines, 412B) | UART=`running` (`/dev/ttyACM0`, 3 lines, 188B)"));

    // RTT / UART / Engine logs
    const double baseTs = 1764142600.0;
    wb->rttLogs()->clear();
    wb->rttLogs()->appendEntries({
        {1, baseTs + 0.10, QString(), QStringLiteral("[BOOT] STM32H563VIT6 SYSCLK=250MHz HSE=24MHz"), QStringLiteral("info")},
        {2, baseTs + 0.25, QString(), QStringLiteral("[FOC] TIM1 center-aligned PWM 20kHz armed"), QStringLiteral("info")},
        {3, baseTs + 0.42, QString(), QStringLiteral("[RTOS] ControlTask high-water warning: 360B free"), QStringLiteral("warn")},
        {4, baseTs + 0.70, QString(), QStringLiteral("[FAULT] UsageFault/BusFault caught at usart.c:145 (CFSR=0x00008200)"), QStringLiteral("error")},
        {5, baseTs + 0.95, QString(), QStringLiteral("[FOC] Iq_ref=1200mA Iq_meas=1185mA Vbus=24.1V"), QStringLiteral("info")},
    });

    wb->uartLogs()->clear();
    wb->uartLogs()->appendEntries({
        {1, baseTs + 0.15, QString(), QStringLiteral("UART3 DMA RX initialized @ 115200 8N1"), QStringLiteral("info")},
        {2, baseTs + 0.60, QString(), QStringLiteral("CMD> status -> MOTOR_RUN_CLOSED_LOOP"), QStringLiteral("info")},
    });

    wb->engineLogs()->clear();
    wb->engineLogs()->appendEntries({
        {1, baseTs + 0.01, QString(), QStringLiteral("[rpc] Ready: session=sess-20261126-foc backend=jlink"), QStringLiteral("info")},
        {2, baseTs + 0.50, QString(), QStringLiteral("[interlock] mode=interactive, hard_deny ranges loaded (STM32H563)"), QStringLiteral("info")},
    });

    // Live Watch
    QJsonObject watchObj;
    watchObj.insert(QStringLiteral("g_foc_iq_ma"), QJsonObject{
        {QStringLiteral("address_hex"), QStringLiteral("0x20000410")},
        {QStringLiteral("type_name"), QStringLiteral("int32_t")},
        {QStringLiteral("kind"), QStringLiteral("int")},
        {QStringLiteral("display"), QStringLiteral("1185")},
        {QStringLiteral("hex"), QStringLiteral("0x000004A1")},
    });
    watchObj.insert(QStringLiteral("g_pwm_duty_pct"), QJsonObject{
        {QStringLiteral("address_hex"), QStringLiteral("0x20000418")},
        {QStringLiteral("type_name"), QStringLiteral("float")},
        {QStringLiteral("kind"), QStringLiteral("float")},
        {QStringLiteral("display"), QStringLiteral("42.6")},
        {QStringLiteral("hex"), QStringLiteral("0x422A6666")},
    });
    watchObj.insert(QStringLiteral("g_vbus_mv"), QJsonObject{
        {QStringLiteral("address_hex"), QStringLiteral("0x20000420")},
        {QStringLiteral("type_name"), QStringLiteral("uint32_t")},
        {QStringLiteral("kind"), QStringLiteral("int")},
        {QStringLiteral("display"), QStringLiteral("24120")},
        {QStringLiteral("hex"), QStringLiteral("0x00005E38")},
    });
    watchObj.insert(QStringLiteral("g_fault_flags"), QJsonObject{
        {QStringLiteral("address_hex"), QStringLiteral("0x20000428")},
        {QStringLiteral("type_name"), QStringLiteral("uint16_t")},
        {QStringLiteral("kind"), QStringLiteral("int")},
        {QStringLiteral("display"), QStringLiteral("0")},
        {QStringLiteral("hex"), QStringLiteral("0x0000")},
    });
    const QStringList watchExprs = {
        QStringLiteral("g_foc_iq_ma"),
        QStringLiteral("g_pwm_duty_pct"),
        QStringLiteral("g_vbus_mv"),
        QStringLiteral("g_fault_flags"),
    };
    wb->watch()->updateFromSnapshot(watchObj, watchExprs);
    if (state == QStringLiteral("watch")) {
        // Trigger one value delta so changeSerial highlight is visible
        watchObj[QStringLiteral("g_foc_iq_ma")] = QJsonObject{
            {QStringLiteral("address_hex"), QStringLiteral("0x20000410")},
            {QStringLiteral("type_name"), QStringLiteral("int32_t")},
            {QStringLiteral("kind"), QStringLiteral("int")},
            {QStringLiteral("display"), QStringLiteral("1204")},
            {QStringLiteral("hex"), QStringLiteral("0x000004B4")},
        };
        wb->watch()->updateFromSnapshot(watchObj, watchExprs);
    }

    // RTOS
    QVariantMap rtosMap;
    rtosMap.insert(QStringLiteral("rtos"), QStringLiteral("freertos"));
    rtosMap.insert(QStringLiteral("elfPath"), QStringLiteral("/workspace/stm32h563_foc_drive/build/Release/motor_foc.elf"));
    rtosMap.insert(QStringLiteral("tickCount"), 184920);
    rtosMap.insert(QStringLiteral("schedulerRunning"), true);
    rtosMap.insert(QStringLiteral("currentTask"), QStringLiteral("ControlTask"));
    wb->setRtosMap(rtosMap);

    QJsonArray tasksArr{
        QJsonObject{
            {QStringLiteral("name"), QStringLiteral("ControlTask")},
            {QStringLiteral("tcb_address"), QStringLiteral("0x20001800")},
            {QStringLiteral("state"), QStringLiteral("RUNNING")},
            {QStringLiteral("priority"), 5},
            {QStringLiteral("stack_size_bytes"), 2048},
            {QStringLiteral("stack_free_min_bytes"), 240},
            {QStringLiteral("stack_used_peak_pct"), 88.3},
        },
        QJsonObject{
            {QStringLiteral("name"), QStringLiteral("TelemetryTask")},
            {QStringLiteral("tcb_address"), QStringLiteral("0x20002200")},
            {QStringLiteral("state"), QStringLiteral("READY")},
            {QStringLiteral("priority"), 3},
            {QStringLiteral("stack_size_bytes"), 2048},
            {QStringLiteral("stack_free_min_bytes"), 560},
            {QStringLiteral("stack_used_peak_pct"), 72.7},
        },
        QJsonObject{
            {QStringLiteral("name"), QStringLiteral("UartDmaTask")},
            {QStringLiteral("tcb_address"), QStringLiteral("0x20002C00")},
            {QStringLiteral("state"), QStringLiteral("BLOCKED")},
            {QStringLiteral("priority"), 2},
            {QStringLiteral("stack_size_bytes"), 1536},
            {QStringLiteral("stack_free_min_bytes"), 940},
            {QStringLiteral("stack_used_peak_pct"), 38.8},
        },
        QJsonObject{
            {QStringLiteral("name"), QStringLiteral("IDLE")},
            {QStringLiteral("tcb_address"), QStringLiteral("0x20003400")},
            {QStringLiteral("state"), QStringLiteral("READY")},
            {QStringLiteral("priority"), 0},
            {QStringLiteral("stack_size_bytes"), 512},
            {QStringLiteral("stack_free_min_bytes"), 416},
            {QStringLiteral("stack_used_peak_pct"), 18.8},
        },
    };
    wb->rtosTasks()->updateTasks(tasksArr, QStringLiteral("ControlTask"));

    QJsonArray hazardsArr{
        QJsonObject{
            {QStringLiteral("severity"), QStringLiteral("critical")},
            {QStringLiteral("code"), QStringLiteral("STACK_WATERMARK_CRITICAL")},
            {QStringLiteral("task_name"), QStringLiteral("ControlTask")},
            {QStringLiteral("message"), QStringLiteral("Stack peak reached 88.3% (only 240 B free); risk of stack overflow during nested FPU ISR.")},
        },
        QJsonObject{
            {QStringLiteral("severity"), QStringLiteral("warning")},
            {QStringLiteral("code"), QStringLiteral("STACK_CAUTION")},
            {QStringLiteral("task_name"), QStringLiteral("TelemetryTask")},
            {QStringLiteral("message"), QStringLiteral("Stack peak reached 72.7% during printf/RTT formatting.")},
        },
    };
    wb->rtosHazards()->updateHazards(hazardsArr);

    // Audit Log
    const double nowSec = static_cast<double>(QDateTime::currentSecsSinceEpoch());
    QJsonArray auditArr{
        QJsonObject{
            {QStringLiteral("seq"), 4},
            {QStringLiteral("timestamp"), nowSec - 12},
            {QStringLiteral("operation"), QStringLiteral("write_memory")},
            {QStringLiteral("target"), QStringLiteral("0x40022040 (FLASH->OPTSR_PRG)")},
            {QStringLiteral("level"), QStringLiteral("hardware_danger")},
            {QStringLiteral("allowed"), false},
            {QStringLiteral("detail"), QStringLiteral("HARD DENY: Option Bytes write blocked by SafetyGuard anti-brick gate")},
        },
        QJsonObject{
            {QStringLiteral("seq"), 3},
            {QStringLiteral("timestamp"), nowSec - 48},
            {QStringLiteral("operation"), QStringLiteral("flash_firmware")},
            {QStringLiteral("target"), QStringLiteral("build/Release/motor_foc.elf")},
            {QStringLiteral("level"), QStringLiteral("hardware_danger")},
            {QStringLiteral("allowed"), true},
            {QStringLiteral("detail"), QStringLiteral("Approved once by human operator; verified 184,320 B")},
        },
        QJsonObject{
            {QStringLiteral("seq"), 2},
            {QStringLiteral("timestamp"), nowSec - 115},
            {QStringLiteral("operation"), QStringLiteral("edit_file")},
            {QStringLiteral("target"), QStringLiteral("Core/Src/usart.c")},
            {QStringLiteral("level"), QStringLiteral("workspace_write")},
            {QStringLiteral("allowed"), true},
            {QStringLiteral("detail"), QStringLiteral("Approved once by human operator")},
        },
        QJsonObject{
            {QStringLiteral("seq"), 1},
            {QStringLiteral("timestamp"), nowSec - 240},
            {QStringLiteral("operation"), QStringLiteral("probe_connect")},
            {QStringLiteral("target"), QStringLiteral("STM32H563VIT6")},
            {QStringLiteral("level"), QStringLiteral("hardware_state")},
            {QStringLiteral("allowed"), true},
            {QStringLiteral("detail"), QStringLiteral("J-Link SWD 4000 kHz")},
        },
    };
    wb->audit()->updateEntries(auditArr);

    // Sessions
    QJsonArray sessionsArr{
        QJsonObject{
            {QStringLiteral("session_id"), QStringLiteral("sess-20261126-foc")},
            {QStringLiteral("message_count"), 6},
            {QStringLiteral("updated_at"), nowSec - 30},
            {QStringLiteral("preview"), QStringLiteral("/crash BusFault during UART3 DMA RX + TIM1 FOC loop")},
        },
        QJsonObject{
            {QStringLiteral("session_id"), QStringLiteral("sess-20261125-bringup")},
            {QStringLiteral("message_count"), 12},
            {QStringLiteral("updated_at"), nowSec - 7200},
            {QStringLiteral("preview"), QStringLiteral("/bringup Verify STM32H563 HSE 24MHz PLL and SWD probe")},
        },
        QJsonObject{
            {QStringLiteral("session_id"), QStringLiteral("sess-20261124-hil")},
            {QStringLiteral("message_count"), 9},
            {QStringLiteral("updated_at"), nowSec - 90000},
            {QStringLiteral("preview"), QStringLiteral("/hil tests/hil_foc_overcurrent.toml regression pass")},
        },
    };
    wb->sessions()->updateSessions(sessionsArr, QStringLiteral("sess-20261126-foc"));

    // Conversation timeline
    wb->conversation()->clear();
    if (state != QStringLiteral("agent-empty")) {
        wb->conversation()->appendUserMessage(
            QStringLiteral("/crash Investigate the BusFault on STM32H563 when UART3 DMA RX starts during FOC motor spin."));
        wb->conversation()->addToolCall(
            QStringLiteral("tc-1"),
            QStringLiteral("diagnose_crash"),
            QJsonObject{{QStringLiteral("elf_path"), QStringLiteral("build/Release/motor_foc.elf")}});
        wb->conversation()->upsertToolResult(
            QStringLiteral("tc-1"),
            QStringLiteral("diagnose_crash"),
            true,
            false,
            false,
            QJsonObject{
                {QStringLiteral("fault_type"), QStringLiteral("BusFault (PRECISERR)")},
                {QStringLiteral("cfsr"), QStringLiteral("0x00008200")},
                {QStringLiteral("bfar"), QStringLiteral("0x00000000")},
                {QStringLiteral("pc"), QStringLiteral("0x08004A1C")},
                {QStringLiteral("source_location"), QStringLiteral("Core/Src/usart.c:145")},
                {QStringLiteral("summary"), QStringLiteral("PRECISERR BusFault at Core/Src/usart.c:145 (NULL DMA buffer pointer)")},
            },
            QString());
        wb->conversation()->addToolCall(
            QStringLiteral("tc-2"),
            QStringLiteral("inspect_peripheral"),
            QJsonObject{{QStringLiteral("peripheral"), QStringLiteral("USART3")}, {QStringLiteral("diff_only"), true}});
        wb->conversation()->upsertToolResult(
            QStringLiteral("tc-2"),
            QStringLiteral("inspect_peripheral"),
            true,
            false,
            false,
            QJsonObject{
                {QStringLiteral("peripheral"), QStringLiteral("USART3")},
                {QStringLiteral("cr1"), QStringLiteral("0x0000002D (UE=1, RE=1, TE=1, RXNEIE=1)")},
                {QStringLiteral("cr3"), QStringLiteral("0x00000040 (DMAR=1)")},
                {QStringLiteral("summary"), QStringLiteral("USART3 enabled @ 115200 bps with DMAR=1")},
            },
            QString());
        wb->conversation()->finishAssistantMessage(QStringLiteral(
            "### Root Cause Identified\n"
            "The Cortex-M33 **BusFault (`PRECISERR`, `BFARVALID`)** occurred at `Core/Src/usart.c:145` "
            "because `Uart_StartRx()` passed a `NULL` buffer pointer into `HAL_UART_Receive_DMA(&huart3, buf, len)`.\n\n"
            "#### Recommended Fix\n"
            "1. Guard `buf == NULL || len == 0U` in `Uart_StartRx()` before configuring GPDMA1.\n"
            "2. Increase `ControlTask` FreeRTOS stack from `2048 B` to `3072 B` (currently at **88.3%** peak watermark)."));
    }

    if (state == QStringLiteral("update-available") && updater) {
        updater->setDemoAvailableState();
    }
    if (state == QStringLiteral("crash") && diagnostics) {
        diagnostics->seedDemoCrashReport();
    }
}
