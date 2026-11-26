"""Phase 5A RTOS 内核透视与栈高水位扫描单元测试（FreeRTOS / Zephyr / RT-Thread）。

使用宿主机真实 `gcc -g -O0` 编译包含三套 RTOS 核心 C 结构体与全局符号的 ELF，
并通过 MockProbe 验证无停机解析、栈高水位扫描、环路与指针越界保护、32 位兜底以及 CLI。

兼容：
    * `PYTHONPATH=src python3 tests/test_rtos.py -v`
    * `PYTHONPATH=src python3 -m unittest discover -s tests -p "test_rtos.py" -v`
    * `pytest`
"""

from __future__ import annotations

import io
import json
import shutil
import struct
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from mcu_harness.dwarf import extract_elf_initialized_memory
from mcu_harness.probe.base import ProbeError
from mcu_harness.probe.mock import MockProbe
from mcu_harness.rtos import (
    RtosError,
    RtosHazard,
    RtosReport,
    RtosTaskInfo,
    detect_rtos,
    inspect_rtos,
)
from mcu_harness.rtos.cli import main as rtos_cli_main

# ---------------------------------------------------------------------------
# 真实 C 源码固件夹具（FreeRTOS / Zephyr / RT-Thread）
# ---------------------------------------------------------------------------

_FREERTOS_C_SRC = r"""
#include <stdint.h>
#include <stddef.h>

typedef uintptr_t StackType_t;
typedef uintptr_t UBaseType_t;
typedef intptr_t  BaseType_t;
typedef uint32_t  TickType_t;

struct xLIST_ITEM {
    TickType_t xItemValue;
    struct xLIST_ITEM * pxNext;
    struct xLIST_ITEM * pxPrevious;
    void * pvOwner;
    void * pvContainer;
};
typedef struct xLIST_ITEM ListItem_t;

struct xMINI_LIST_ITEM {
    TickType_t xItemValue;
    struct xLIST_ITEM * pxNext;
    struct xLIST_ITEM * pxPrevious;
};
typedef struct xMINI_LIST_ITEM MiniListItem_t;

typedef struct xLIST {
    UBaseType_t uxNumberOfItems;
    ListItem_t * pxIndex;
    MiniListItem_t xListEnd;
} List_t;

typedef struct tskTaskControlBlock {
    volatile StackType_t * pxTopOfStack;
    ListItem_t xStateListItem;
    ListItem_t xEventListItem;
    UBaseType_t uxPriority;
    StackType_t * pxStack;
    char pcTaskName[16];
    StackType_t * pxEndOfStack;
} tskTCB;
typedef tskTCB TCB_t;

uint8_t stack_run[512]   __attribute__((aligned(16))) = { [0 ... 511] = 0xA5 };
uint8_t stack_ready[512] __attribute__((aligned(16))) = { [0 ... 511] = 0xA5 };
uint8_t stack_blk[256]   __attribute__((aligned(16))) = { [0 ... 255] = 0xA5 };
uint8_t stack_susp[256]  __attribute__((aligned(16))) = { [0 ... 255] = 0xA5 };
uint8_t stack_ovf[256]   __attribute__((aligned(16))) = { [0 ... 255] = 0xA5 };

TCB_t tcb_run;
TCB_t tcb_ready;
TCB_t tcb_blk;
TCB_t tcb_susp;
TCB_t tcb_ovf;

List_t pxReadyTasksLists[3];
List_t xDelayedTaskList1;
List_t xDelayedTaskList2;
List_t xSuspendedTaskList;

List_t * volatile pxDelayedTaskList = &xDelayedTaskList1;
List_t * volatile pxOverflowDelayedTaskList = &xDelayedTaskList2;
TCB_t * volatile pxCurrentTCB = &tcb_run;
volatile UBaseType_t uxCurrentNumberOfTasks = 5;
volatile TickType_t xTickCount = 12345;
volatile BaseType_t xSchedulerRunning = 1;

/* 编译期构造完整任务链表，确保 --mock 直接加载 .data 即可运行 */
TCB_t tcb_run = {
    .pxTopOfStack = (StackType_t *)(stack_run + 320),
    .xStateListItem = { .xItemValue = 0, .pxNext = (ListItem_t *)&pxReadyTasksLists[2].xListEnd, .pxPrevious = (ListItem_t *)&pxReadyTasksLists[2].xListEnd, .pvOwner = &tcb_run, .pvContainer = &pxReadyTasksLists[2] },
    .uxPriority = 2,
    .pxStack = (StackType_t *)stack_run,
    .pcTaskName = "ControlLoop",
    .pxEndOfStack = (StackType_t *)(stack_run + 512),
};

TCB_t tcb_ready = {
    .pxTopOfStack = (StackType_t *)(stack_ready + 128),
    .xStateListItem = { .xItemValue = 0, .pxNext = (ListItem_t *)&pxReadyTasksLists[1].xListEnd, .pxPrevious = (ListItem_t *)&pxReadyTasksLists[1].xListEnd, .pvOwner = &tcb_ready, .pvContainer = &pxReadyTasksLists[1] },
    .uxPriority = 1,
    .pxStack = (StackType_t *)stack_ready,
    .pcTaskName = "Telemetry",
    .pxEndOfStack = (StackType_t *)(stack_ready + 512),
};

TCB_t tcb_blk = {
    .pxTopOfStack = (StackType_t *)(stack_blk + 64),
    .xStateListItem = { .xItemValue = 500, .pxNext = (ListItem_t *)&xDelayedTaskList1.xListEnd, .pxPrevious = (ListItem_t *)&xDelayedTaskList1.xListEnd, .pvOwner = &tcb_blk, .pvContainer = &xDelayedTaskList1 },
    .uxPriority = 1,
    .pxStack = (StackType_t *)stack_blk,
    .pcTaskName = "ModbusRx",
    .pxEndOfStack = (StackType_t *)(stack_blk + 256),
};

TCB_t tcb_ovf = {
    .pxTopOfStack = (StackType_t *)(stack_ovf + 160),
    .xStateListItem = { .xItemValue = 900, .pxNext = (ListItem_t *)&xDelayedTaskList2.xListEnd, .pxPrevious = (ListItem_t *)&xDelayedTaskList2.xListEnd, .pvOwner = &tcb_ovf, .pvContainer = &xDelayedTaskList2 },
    .uxPriority = 0,
    .pxStack = (StackType_t *)stack_ovf,
    .pcTaskName = "Watchdog",
    .pxEndOfStack = (StackType_t *)(stack_ovf + 256),
};

TCB_t tcb_susp = {
    .pxTopOfStack = (StackType_t *)(stack_susp + 200),
    .xStateListItem = { .xItemValue = 0, .pxNext = (ListItem_t *)&xSuspendedTaskList.xListEnd, .pxPrevious = (ListItem_t *)&xSuspendedTaskList.xListEnd, .pvOwner = &tcb_susp, .pvContainer = &xSuspendedTaskList },
    .uxPriority = 0,
    .pxStack = (StackType_t *)stack_susp,
    .pcTaskName = "DiagIdle",
    .pxEndOfStack = (StackType_t *)(stack_susp + 256),
};

List_t pxReadyTasksLists[3] = {
    { .uxNumberOfItems = 0, .pxIndex = (ListItem_t *)&pxReadyTasksLists[0].xListEnd, .xListEnd = { 0xFFFFFFFFu, (ListItem_t *)&pxReadyTasksLists[0].xListEnd, (ListItem_t *)&pxReadyTasksLists[0].xListEnd } },
    { .uxNumberOfItems = 1, .pxIndex = &tcb_ready.xStateListItem, .xListEnd = { 0xFFFFFFFFu, &tcb_ready.xStateListItem, &tcb_ready.xStateListItem } },
    { .uxNumberOfItems = 1, .pxIndex = &tcb_run.xStateListItem, .xListEnd = { 0xFFFFFFFFu, &tcb_run.xStateListItem, &tcb_run.xStateListItem } },
};

List_t xDelayedTaskList1 = {
    .uxNumberOfItems = 1,
    .pxIndex = &tcb_blk.xStateListItem,
    .xListEnd = { 0xFFFFFFFFu, &tcb_blk.xStateListItem, &tcb_blk.xStateListItem },
};

List_t xDelayedTaskList2 = {
    .uxNumberOfItems = 1,
    .pxIndex = &tcb_ovf.xStateListItem,
    .xListEnd = { 0xFFFFFFFFu, &tcb_ovf.xStateListItem, &tcb_ovf.xStateListItem },
};

List_t xSuspendedTaskList = {
    .uxNumberOfItems = 1,
    .pxIndex = &tcb_susp.xStateListItem,
    .xListEnd = { 0xFFFFFFFFu, &tcb_susp.xStateListItem, &tcb_susp.xStateListItem },
};

int main(void) {
    return (int)uxCurrentNumberOfTasks + (int)xTickCount + (int)xSchedulerRunning;
}
"""

_ZEPHYR_C_SRC = r"""
#include <stdint.h>
#include <stddef.h>

struct _dnode {
    struct _dnode *head;
    struct _dnode *tail;
};

struct _thread_base {
    union {
        struct _dnode qnode_dlist;
        void *qnode_rb;
    };
    uint8_t thread_state;
    int8_t user_options;
    int8_t prio;
    uint8_t sched_locked;
};

struct _callee_saved {
    uintptr_t psp;
};

struct _thread_stack_info {
    uintptr_t start;
    size_t size;
};

struct k_thread {
    struct _thread_base base;
    struct _callee_saved callee_saved;
    struct _thread_stack_info stack_info;
    struct k_thread *next_thread;
    char name[32];
};

struct _ready_q {
    struct k_thread *cache;
    struct _dnode runq;
};

struct _cpu {
    uint32_t nested;
    char *irq_stack;
    struct k_thread *current;
};

struct z_kernel {
    struct _cpu cpus[1];
    struct _ready_q ready_q;
    struct k_thread *threads;
};

uint8_t z_stack_main[512] __attribute__((aligned(16))) = { [0 ... 511] = 0xAA };
uint8_t z_stack_coop[256] __attribute__((aligned(16))) = { [0 ... 255] = 0xAA };
uint8_t z_stack_pend[256] __attribute__((aligned(16))) = { [0 ... 255] = 0xAA };
uint8_t z_stack_susp[256] __attribute__((aligned(16))) = { [0 ... 255] = 0xAA };

struct k_thread thr_main;
struct k_thread thr_coop;
struct k_thread thr_pend;
struct k_thread thr_susp;
struct z_kernel _kernel;

struct k_thread thr_susp = {
    .base = { .thread_state = 0x10, .prio = 7 },
    .callee_saved = { .psp = (uintptr_t)(z_stack_susp + 192) },
    .stack_info = { .start = (uintptr_t)z_stack_susp, .size = 256 },
    .next_thread = NULL,
    .name = "logger_susp",
};

struct k_thread thr_pend = {
    .base = { .thread_state = 0x02, .prio = 5 },
    .callee_saved = { .psp = (uintptr_t)(z_stack_pend + 32) },
    .stack_info = { .start = (uintptr_t)z_stack_pend, .size = 256 },
    .next_thread = &thr_susp,
    .name = "comms_pend",
};

struct k_thread thr_coop = {
    .base = { .thread_state = 0x80, .prio = -2 },
    .callee_saved = { .psp = (uintptr_t)(z_stack_coop + 40) },
    .stack_info = { .start = (uintptr_t)z_stack_coop, .size = 256 },
    .next_thread = &thr_pend,
    .name = "sensor_coop",
};

struct k_thread thr_main = {
    .base = { .thread_state = 0x80, .prio = 0 },
    .callee_saved = { .psp = (uintptr_t)(z_stack_main + 384) },
    .stack_info = { .start = (uintptr_t)z_stack_main, .size = 512 },
    .next_thread = &thr_coop,
    .name = "main",
};

struct z_kernel _kernel = {
    .cpus = { { .nested = 0, .irq_stack = NULL, .current = &thr_main } },
    .ready_q = { .cache = &thr_main, .runq = { .head = &_kernel.ready_q.runq, .tail = &_kernel.ready_q.runq } },
    .threads = &thr_main,
};

int main(void) {
    return (int)_kernel.cpus[0].current->base.prio;
}
"""

_RTTHREAD_C_SRC = r"""
#include <stdint.h>
#include <stddef.h>

struct rt_list_node {
    struct rt_list_node *next;
    struct rt_list_node *prev;
};
typedef struct rt_list_node rt_list_t;

enum rt_object_class_type {
    RT_Object_Class_Thread = 1,
    RT_Object_Class_Semaphore = 2
};

struct rt_object_information {
    enum rt_object_class_type type;
    rt_list_t object_list;
    size_t object_size;
};

struct rt_thread {
    char name[8];
    uint8_t type;
    uint8_t flags;
    rt_list_t list;
    rt_list_t tlist;
    void *sp;
    void *entry;
    void *parameter;
    void *stack_addr;
    uint32_t stack_size;
    uint32_t error;
    uint8_t stat;
    uint8_t current_priority;
    uint8_t init_priority;
};
typedef struct rt_thread *rt_thread_t;

uint8_t rtt_stack_shell[512]  __attribute__((aligned(16))) = { [0 ... 511] = '#' };
uint8_t rtt_stack_worker[256] __attribute__((aligned(16))) = { [0 ... 255] = '#' };
uint8_t rtt_stack_idle[256]   __attribute__((aligned(16))) = { [0 ... 255] = '#' };

struct rt_thread thr_shell;
struct rt_thread thr_worker;
struct rt_thread thr_idle;
struct rt_object_information rt_object_container[2];

struct rt_thread thr_shell = {
    .name = "tshell",
    .type = 1,
    .flags = 0,
    .list = { .next = &thr_worker.list, .prev = &rt_object_container[0].object_list },
    .sp = rtt_stack_shell + 350,
    .stack_addr = rtt_stack_shell,
    .stack_size = 512,
    .stat = 0x03,
    .current_priority = 20,
    .init_priority = 20,
};

struct rt_thread thr_worker = {
    .name = "worker",
    .type = 1,
    .flags = 0,
    .list = { .next = &thr_idle.list, .prev = &thr_shell.list },
    .sp = rtt_stack_worker + 80,
    .stack_addr = rtt_stack_worker,
    .stack_size = 256,
    .stat = 0x01,
    .current_priority = 10,
    .init_priority = 10,
};

struct rt_thread thr_idle = {
    .name = "tidle",
    .type = 1,
    .flags = 0,
    .list = { .next = &rt_object_container[0].object_list, .prev = &thr_worker.list },
    .sp = rtt_stack_idle + 40,
    .stack_addr = rtt_stack_idle,
    .stack_size = 256,
    .stat = 0x02,
    .current_priority = 31,
    .init_priority = 31,
};

struct rt_object_information rt_object_container[2] = {
    {
        .type = RT_Object_Class_Thread,
        .object_list = { .next = &thr_shell.list, .prev = &thr_idle.list },
        .object_size = sizeof(struct rt_thread),
    },
    {
        .type = RT_Object_Class_Semaphore,
        .object_list = { .next = &rt_object_container[1].object_list, .prev = &rt_object_container[1].object_list },
        .object_size = 32,
    },
};

rt_thread_t rt_current_thread = &thr_shell;
volatile uint32_t rt_tick = 9876;

int main(void) {
    return (int)rt_tick + thr_shell.current_priority;
}
"""


class _CountingMockProbe(MockProbe):
    """监控 halt() 是否被误调，确保全过程严格无停机。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.halt_calls = 0
        self.read_calls = 0

    def halt(self) -> bool:
        self.halt_calls += 1
        return super().halt()

    def read_memory(self, address: int, num_bytes: int) -> bytes:
        self.read_calls += 1
        return super().read_memory(address, num_bytes)


def _load_elf_to_mock(elf_path: Path) -> _CountingMockProbe:
    probe = _CountingMockProbe()
    for seg_addr, seg_bytes in extract_elf_initialized_memory(elf_path).items():
        for off, b in enumerate(seg_bytes):
            probe._mem[seg_addr + off] = b
    return probe


def _paint_stack_usage(probe: MockProbe, stack_base: int, stack_size: int, free_min: int, fill_byte: int) -> None:
    """模拟栈从高地址向低地址压栈：底部保留 free_min 字节水印，上方涂写非水印数据。"""
    for i in range(stack_size):
        if i < free_min:
            probe._mem[stack_base + i] = fill_byte
        else:
            probe._mem[stack_base + i] = (fill_byte ^ 0x5A) & 0xFF


def _build_minimal_elf32_symtab_only(out_path: Path, symbols: list[tuple[str, int, int, int]]) -> None:
    """构造一个无 .debug_info 的最小 ELF32（ET_EXEC, EM_ARM）用于测试 32 位 Cortex-M 兜底路径。"""
    strtab = bytearray(b"\x00")
    sym_entries = bytearray(struct.pack("<IIIBBH", 0, 0, 0, 0, 0, 0))
    for name, addr, size, st_type in symbols:
        name_off = len(strtab)
        strtab.extend(name.encode("ascii") + b"\x00")
        st_info = (1 << 4) | (st_type & 0x0F)  # STB_GLOBAL
        sym_entries.extend(struct.pack("<IIIBBH", name_off, addr, size, st_info, 0, 1))

    shstrtab = b"\x00.symtab\x00.strtab\x00.shstrtab\x00"
    off_symtab = 52
    off_strtab = off_symtab + len(sym_entries)
    off_shstrtab = off_strtab + len(strtab)
    off_shdrs = off_shstrtab + len(shstrtab)

    ehdr = struct.pack(
        "<16sHHIIIIIHHHHHH",
        b"\x7fELF\x01\x01\x01" + b"\x00" * 9,  # ELF32, LSB
        2,     # ET_EXEC
        40,    # EM_ARM
        1,     # EV_CURRENT
        0, 0, off_shdrs, 0,
        52, 0, 0, 40, 4, 3,
    )
    sh0 = struct.pack("<IIIIIIIIII", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    sh1 = struct.pack("<IIIIIIIIII", 1, 2, 0, 0, off_symtab, len(sym_entries), 2, 1, 4, 16)
    sh2 = struct.pack("<IIIIIIIIII", 9, 3, 0, 0, off_strtab, len(strtab), 0, 0, 1, 0)
    sh3 = struct.pack("<IIIIIIIIII", 17, 3, 0, 0, off_shstrtab, len(shstrtab), 0, 0, 1, 0)

    out_path.write_bytes(ehdr + sym_entries + strtab + shstrtab + sh0 + sh1 + sh2 + sh3)


@unittest.skipUnless(shutil.which("gcc") is not None, "requires gcc to build real DWARF test ELFs")
class RtosInspectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmpdir.name)

        cls.freertos_elf = cls._compile_c(tmp, "freertos_test", _FREERTOS_C_SRC)
        cls.zephyr_elf = cls._compile_c(tmp, "zephyr_test", _ZEPHYR_C_SRC)
        cls.rtthread_elf = cls._compile_c(tmp, "rtthread_test", _RTTHREAD_C_SRC)

        cls.plain_elf = cls._compile_c(tmp, "plain_test", "int g_dummy = 42; int main(void) { return g_dummy; }\n")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmpdir.cleanup()

    @classmethod
    def _compile_c(cls, tmp: Path, name: str, source: str) -> Path:
        c_path = tmp / f"{name}.c"
        elf_path = tmp / f"{name}.elf"
        c_path.write_text(source, encoding="utf-8")
        res = subprocess.run(
            ["gcc", "-g", "-O0", str(c_path), "-o", str(elf_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise RuntimeError(f"gcc failed for {name}:\n{res.stderr}")
        return elf_path

    # ---- 1. 自动识别测试 -------------------------------------------------

    def test_detect_rtos(self) -> None:
        self.assertEqual(detect_rtos(self.freertos_elf), "freertos")
        self.assertEqual(detect_rtos(self.zephyr_elf), "zephyr")
        self.assertEqual(detect_rtos(self.rtthread_elf), "rtthread")
        self.assertIsNone(detect_rtos(self.plain_elf))

    # ---- 2. FreeRTOS 完整内核透视、高水位扫描与安全保护 -------------------

    def test_freertos_inspect_and_hazards(self) -> None:
        probe = _load_elf_to_mock(self.freertos_elf)
        from mcu_harness.dwarf import load_dwarf_index

        idx = load_dwarf_index(self.freertos_elf)
        ptr_sz = idx.pointer_size

        # 涂写 3 种栈高水位模式：正常 (256 B free)、危险 (<64 B: 32 B free)、溢出 (0 B free)
        _paint_stack_usage(probe, idx.get_variable("stack_run").address, 512, 256, 0xA5)
        _paint_stack_usage(probe, idx.get_variable("stack_ready").address, 512, 32, 0xA5)
        _paint_stack_usage(probe, idx.get_variable("stack_blk").address, 256, 0, 0xA5)
        _paint_stack_usage(probe, idx.get_variable("stack_susp").address, 256, 160, 0xA5)
        _paint_stack_usage(probe, idx.get_variable("stack_ovf").address, 256, 128, 0xA5)

        with probe:
            report = inspect_rtos(probe, self.freertos_elf)

        self.assertEqual(probe.halt_calls, 0)
        self.assertIsInstance(report, RtosReport)
        self.assertEqual(report.rtos, "freertos")
        self.assertEqual(report.pointer_size, ptr_sz)
        self.assertTrue(report.dwarf_resolved)
        self.assertEqual(report.tick_count, 12345)
        self.assertTrue(report.scheduler_running)
        self.assertEqual(report.reported_task_count, 5)
        self.assertEqual(report.current_task, "ControlLoop")
        self.assertEqual(len(report.tasks), 5)

        by_name = {t.name: t for t in report.tasks}
        self.assertEqual(by_name["ControlLoop"].state, "RUNNING")
        self.assertEqual(by_name["ControlLoop"].priority, 2)
        self.assertEqual(by_name["ControlLoop"].stack_size_bytes, 512)
        self.assertEqual(by_name["ControlLoop"].stack_free_min_bytes, 256)
        self.assertEqual(by_name["ControlLoop"].stack_used_peak_pct, 50.0)

        self.assertEqual(by_name["Telemetry"].state, "READY")
        self.assertEqual(by_name["Telemetry"].stack_free_min_bytes, 32)

        self.assertEqual(by_name["ModbusRx"].state, "BLOCKED")
        self.assertEqual(by_name["ModbusRx"].stack_free_min_bytes, 0)
        self.assertEqual(by_name["ModbusRx"].stack_used_peak_pct, 100.0)

        self.assertEqual(by_name["Watchdog"].state, "BLOCKED")
        self.assertEqual(by_name["DiagIdle"].state, "SUSPENDED")

        codes = {(h.code, h.severity, h.task_name) for h in report.hazards}
        self.assertIn(("STACK_CRITICAL", "warning", "Telemetry"), codes)
        self.assertIn(("STACK_OVERFLOW", "critical", "ModbusRx"), codes)

        # 验证序列化与文本报告
        d = report.to_dict()
        self.assertEqual(d["summary"]["task_count"], 5)
        txt = report.to_text()
        self.assertIn("FreeRTOS", txt)
        self.assertIn("ControlLoop", txt)
        self.assertIn("STACK_OVERFLOW", txt)

    def test_freertos_cycle_and_invalid_pointer_protection(self) -> None:
        probe = _load_elf_to_mock(self.freertos_elf)
        from mcu_harness.dwarf import load_dwarf_index

        idx = load_dwarf_index(self.freertos_elf)
        ptr_sz = idx.pointer_size
        tcb_ready_addr = idx.get_variable("tcb_ready").address
        tcb_struct = idx.get_variable("tcb_ready").dwarf_type.resolve()
        item_off = tcb_struct.find_member("xStateListItem").byte_offset
        item_addr = tcb_ready_addr + item_off
        px_next_off = tcb_struct.find_member("xStateListItem").member_type.resolve().find_member("pxNext").byte_offset

        # 构造自环节点：tcb_ready.xStateListItem.pxNext = &tcb_ready.xStateListItem
        raw_self = item_addr.to_bytes(ptr_sz, "little")
        for i, b in enumerate(raw_self):
            probe._mem[item_addr + px_next_off + i] = b

        # 构造非对齐非法指针：pxDelayedTaskList = 0x1003
        delayed_var_addr = idx.get_variable("pxDelayedTaskList").address
        raw_bad = (0x1003).to_bytes(ptr_sz, "little")
        for i, b in enumerate(raw_bad):
            probe._mem[delayed_var_addr + i] = b

        with probe:
            report = inspect_rtos(probe, self.freertos_elf, max_tasks=10, check_stack=False)

        self.assertEqual(probe.halt_calls, 0)
        hazard_codes = [h.code for h in report.hazards]
        self.assertIn("CYCLE_DETECTED", hazard_codes)
        self.assertIn("INVALID_POINTER", hazard_codes)
        # check_stack=False 时不做栈扫描
        for t in report.tasks:
            self.assertIsNone(t.stack_free_min_bytes)

    # ---- 3. Zephyr 适配器测试 --------------------------------------------

    def test_zephyr_inspect_and_cycle_protection(self) -> None:
        probe = _load_elf_to_mock(self.zephyr_elf)
        from mcu_harness.dwarf import load_dwarf_index

        idx = load_dwarf_index(self.zephyr_elf)
        ptr_sz = idx.pointer_size

        _paint_stack_usage(probe, idx.get_variable("z_stack_main").address, 512, 300, 0xAA)
        _paint_stack_usage(probe, idx.get_variable("z_stack_coop").address, 256, 24, 0xAA)
        _paint_stack_usage(probe, idx.get_variable("z_stack_pend").address, 256, 0, 0xAA)
        _paint_stack_usage(probe, idx.get_variable("z_stack_susp").address, 256, 128, 0xAA)

        with probe:
            report = inspect_rtos(probe, self.zephyr_elf)

        self.assertEqual(probe.halt_calls, 0)
        self.assertEqual(report.rtos, "zephyr")
        self.assertTrue(report.dwarf_resolved)
        self.assertEqual(report.current_task, "main")
        self.assertEqual(len(report.tasks), 4)

        by_name = {t.name: t for t in report.tasks}
        self.assertEqual(by_name["main"].state, "RUNNING")
        self.assertEqual(by_name["main"].priority, 0)
        self.assertEqual(by_name["main"].stack_free_min_bytes, 300)

        self.assertEqual(by_name["sensor_coop"].state, "READY")
        self.assertEqual(by_name["sensor_coop"].priority, -2)
        self.assertEqual(by_name["sensor_coop"].stack_free_min_bytes, 24)

        self.assertEqual(by_name["comms_pend"].state, "BLOCKED")
        self.assertEqual(by_name["comms_pend"].stack_free_min_bytes, 0)

        self.assertEqual(by_name["logger_susp"].state, "SUSPENDED")
        self.assertEqual(by_name["logger_susp"].stack_free_min_bytes, 128)

        codes = {(h.code, h.severity, h.task_name) for h in report.hazards}
        self.assertIn(("STACK_CRITICAL", "warning", "sensor_coop"), codes)
        self.assertIn(("STACK_OVERFLOW", "critical", "comms_pend"), codes)

        # 注入 Zephyr next_thread 自环：thr_coop.next_thread = &thr_coop
        thr_coop_addr = idx.get_variable("thr_coop").address
        kthread_st = idx.get_variable("thr_coop").dwarf_type.resolve()
        next_off = kthread_st.find_member("next_thread").byte_offset
        for i, b in enumerate(thr_coop_addr.to_bytes(ptr_sz, "little")):
            probe._mem[thr_coop_addr + next_off + i] = b

        with probe:
            cyc_report = inspect_rtos(probe, self.zephyr_elf, check_stack=False)
        self.assertIn("CYCLE_DETECTED", [h.code for h in cyc_report.hazards])

    # ---- 4. RT-Thread 适配器测试 -----------------------------------------

    def test_rtthread_inspect_and_cycle_protection(self) -> None:
        probe = _load_elf_to_mock(self.rtthread_elf)
        from mcu_harness.dwarf import load_dwarf_index

        idx = load_dwarf_index(self.rtthread_elf)
        ptr_sz = idx.pointer_size

        _paint_stack_usage(probe, idx.get_variable("rtt_stack_shell").address, 512, 256, 0x23)
        _paint_stack_usage(probe, idx.get_variable("rtt_stack_worker").address, 256, 40, 0x23)
        _paint_stack_usage(probe, idx.get_variable("rtt_stack_idle").address, 256, 0, 0x23)

        with probe:
            report = inspect_rtos(probe, self.rtthread_elf)

        self.assertEqual(probe.halt_calls, 0)
        self.assertEqual(report.rtos, "rtthread")
        self.assertTrue(report.dwarf_resolved)
        self.assertEqual(report.tick_count, 9876)
        self.assertEqual(report.current_task, "tshell")
        self.assertEqual(len(report.tasks), 3)

        by_name = {t.name: t for t in report.tasks}
        self.assertEqual(by_name["tshell"].state, "RUNNING")
        self.assertEqual(by_name["tshell"].priority, 20)
        self.assertEqual(by_name["tshell"].stack_free_min_bytes, 256)

        self.assertEqual(by_name["worker"].state, "READY")
        self.assertEqual(by_name["worker"].priority, 10)
        self.assertEqual(by_name["worker"].stack_free_min_bytes, 40)

        self.assertEqual(by_name["tidle"].state, "SUSPENDED")
        self.assertEqual(by_name["tidle"].priority, 31)
        self.assertEqual(by_name["tidle"].stack_free_min_bytes, 0)

        codes = {(h.code, h.severity, h.task_name) for h in report.hazards}
        self.assertIn(("STACK_CRITICAL", "warning", "worker"), codes)
        self.assertIn(("STACK_OVERFLOW", "critical", "tidle"), codes)

        # 注入双向循环链表回环：thr_worker.list.next = &thr_shell.list
        thr_shell_addr = idx.get_variable("thr_shell").address
        thr_worker_addr = idx.get_variable("thr_worker").address
        rt_thread_st = idx.get_variable("thr_shell").dwarf_type.resolve()
        list_off = rt_thread_st.find_member("list").byte_offset
        shell_node = thr_shell_addr + list_off
        worker_node = thr_worker_addr + list_off
        for i, b in enumerate(shell_node.to_bytes(ptr_sz, "little")):
            probe._mem[worker_node + i] = b

        with probe:
            cyc_report = inspect_rtos(probe, self.rtthread_elf, check_stack=False)
        self.assertIn("CYCLE_DETECTED", [h.code for h in cyc_report.hazards])

    # ---- 5. 无 DWARF 32 位 Cortex-M 兜底 + 64KB 栈上限保护 ---------------

    def test_cortex_m_32bit_fallback_no_dwarf_and_64kb_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            elf32 = Path(tmpdir) / "cm33_freertos.elf"
            # 32 位 RAM 地址布置
            px_current_tcb_addr = 0x20000100
            px_ready_lists_addr = 0x20000200
            ux_num_tasks_addr = 0x20000104
            x_tick_addr = 0x20000108
            x_sched_addr = 0x2000010C
            tcb_addr = 0x20001000
            stack_base = 0x20002000

            _build_minimal_elf32_symtab_only(
                elf32,
                [
                    ("pxCurrentTCB", px_current_tcb_addr, 4, 1),
                    ("pxReadyTasksLists", px_ready_lists_addr, 20, 1),
                    ("uxCurrentNumberOfTasks", ux_num_tasks_addr, 4, 1),
                    ("xTickCount", x_tick_addr, 4, 1),
                    ("xSchedulerRunning", x_sched_addr, 4, 1),
                ],
            )
            self.assertEqual(detect_rtos(elf32), "freertos")

            probe = _CountingMockProbe()
            probe.set_word(px_current_tcb_addr, tcb_addr)
            probe.set_word(ux_num_tasks_addr, 1)
            probe.set_word(x_tick_addr, 777)
            probe.set_word(x_sched_addr, 1)

            # Ready List[0] (20B): uxNumberOfItems=1, pxIndex=tcb+4, xListEnd(0xFFFFFFFF, next=tcb+4, prev=tcb+4)
            list_item_addr = tcb_addr + 4
            probe.set_word(px_ready_lists_addr + 0, 1)
            probe.set_word(px_ready_lists_addr + 4, list_item_addr)
            probe.set_word(px_ready_lists_addr + 8, 0xFFFFFFFF)
            probe.set_word(px_ready_lists_addr + 12, list_item_addr)
            probe.set_word(px_ready_lists_addr + 16, list_item_addr)

            # TCB (32-bit fallback offsets): pxTopOfStack@0, xStateListItem@4, uxPriority@44, pxStack@48, pcTaskName@52
            probe.set_word(tcb_addr + 0, stack_base + 128)
            probe.set_word(list_item_addr + 4, px_ready_lists_addr + 8)  # pxNext -> xListEnd
            probe.set_word(list_item_addr + 12, tcb_addr)  # pvOwner -> TCB
            probe.set_word(tcb_addr + 44, 3)
            probe.set_word(tcb_addr + 48, stack_base)
            for i, b in enumerate(b"MotorCtrl\x00"):
                probe._mem[tcb_addr + 52 + i] = b

            with probe:
                report = inspect_rtos(probe, elf32)

            self.assertEqual(probe.halt_calls, 0)
            self.assertFalse(report.dwarf_resolved)
            self.assertEqual(report.pointer_size, 4)
            self.assertEqual(report.tick_count, 777)
            self.assertEqual(len(report.tasks), 1)
            self.assertEqual(report.tasks[0].name, "MotorCtrl")
            self.assertEqual(report.tasks[0].state, "RUNNING")
            self.assertEqual(report.tasks[0].priority, 3)
            self.assertEqual(report.tasks[0].stack_base, "0x20002000")

            # 验证 64 KB 栈上限保护 & ProbeError 优雅捕获
            class _FailStackProbe(_CountingMockProbe):
                def read_memory(self, address: int, num_bytes: int) -> bytes:
                    if num_bytes > 65536:
                        raise AssertionError("Exceeded 64 KB stack cap!")
                    if address == 0x20004000:
                        raise ProbeError("Bus fault on unmapped RAM")
                    return super().read_memory(address, num_bytes)

            from mcu_harness.rtos.inspector import _scan_stack_watermark

            fprobe = _FailStackProbe()
            hazards: list[RtosHazard] = []
            with fprobe:
                f1, p1 = _scan_stack_watermark(
                    fprobe, rtos="freertos", task_name="Huge", stack_base=0x20002000, stack_size=128 * 1024, ptr_size=4, hazards=hazards
                )
                f2, p2 = _scan_stack_watermark(
                    fprobe, rtos="freertos", task_name="BadBus", stack_base=0x20004000, stack_size=256, ptr_size=4, hazards=hazards
                )
            self.assertIsNone(f1)
            self.assertIsNone(p1)
            self.assertIsNone(f2)
            self.assertIsNone(p2)
            self.assertEqual(len(hazards), 2)
            self.assertTrue(all(h.code == "INVALID_POINTER" for h in hazards))

    # ---- 6. CLI mcu-rtos 验证 --------------------------------------------

    def test_cli_mock_json_and_text(self) -> None:
        for elf_path, expected_rtos in [
            (self.freertos_elf, "freertos"),
            (self.zephyr_elf, "zephyr"),
            (self.rtthread_elf, "rtthread"),
        ]:
            out = io.StringIO()
            err = io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = rtos_cli_main(["--elf", str(elf_path), "--mock", "--json"])
            self.assertEqual(rc, 0, msg=err.getvalue())
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["rtos"], expected_rtos)
            self.assertGreaterEqual(len(payload["tasks"]), 3)

        # --no-stack + 文本输出
        out2 = io.StringIO()
        with redirect_stdout(out2):
            rc2 = rtos_cli_main(["--elf", str(self.freertos_elf), "--rtos", "freertos", "--no-stack", "--max-tasks", "3", "--mock"])
        self.assertEqual(rc2, 0)
        self.assertIn("FreeRTOS", out2.getvalue())
        self.assertIn("TRUNCATED", out2.getvalue())

        # 错误参数处理
        err3 = io.StringIO()
        with redirect_stderr(err3):
            rc3 = rtos_cli_main(["--elf", str(self.plain_elf), "--mock"])
        self.assertEqual(rc3, 1)
        self.assertIn("error:", err3.getvalue())


if __name__ == "__main__":
    unittest.main()
