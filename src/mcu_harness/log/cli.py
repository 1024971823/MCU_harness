"""RTT 日志命令行工具 —— 脱离 AI 在终端直接读取或持续跟踪 SEGGER RTT 日志。

用法示例：
    # 模拟模式自测（无硬件）
    python -m mcu_harness.log.cli --mock

    # 实机读取最近 50 行 RTT 日志（Channel 0）
    python -m mcu_harness.log.cli --tail 50

    # 按关键词过滤并附带上下文
    python -m mcu_harness.log.cli --pattern ERROR --context 2

    # 持续跟踪实时打印（类似 tail -f，Ctrl+C 退出）
    python -m mcu_harness.log.cli --follow
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from mcu_harness.probe import MockProbe, ProbeError, ProbeManager

from .rtt_collector import RttLogCollector


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mcu-log", description="Non-blocking SEGGER RTT debug log reader")
    ap.add_argument("--device", default=os.environ.get("MCU_HARNESS_DEVICE", "STM32H563ZI"), help="目标芯片型号")
    ap.add_argument("--channel", type=int, default=0, help="RTT 上行通道号，默认 0")
    ap.add_argument("--block-addr", help="RTT 控制块地址（可选，如 0x20000000）")
    ap.add_argument("-n", "--tail", type=int, default=50, help="返回最近 N 行，默认 50")
    ap.add_argument("--pattern", help="按关键词/正则过滤")
    ap.add_argument("--regex", action="store_true", help="将 --pattern 作为正则表达式")
    ap.add_argument("-C", "--context", type=int, default=2, help="匹配行的前后上下文行数")
    ap.add_argument("--wait", type=float, default=0.3, help="单次拉取前等待采集秒数，默认 0.3s")
    ap.add_argument("-f", "--follow", action="store_true", help="持续跟踪输出（Ctrl+C 退出）")
    ap.add_argument("--mock", action="store_true", help="使用模拟探针注入测试日志")
    ap.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = ap.parse_args(argv)

    block_addr = int(args.block_addr, 0) if args.block_addr else None
    pm = ProbeManager()
    collector = RttLogCollector(pm)

    try:
        if args.mock:
            mock = MockProbe(device=args.device)
            mock.connect()
            mock.push_rtt(
                "[00:00.001] [INFO] STM32H563 boot ok, SYSCLK=250MHz\n"
                "[00:00.050] [WARN] I2C1 busy, retrying...\n"
                "[00:00.062] [ERROR] Sensor timeout on addr 0x48\n"
                "[00:00.070] [INFO] Recovery complete\n",
                channel=args.channel,
            )
            pm.set_probe(mock)
        else:
            pm.connect(backend="jlink", device=args.device)

        collector.start(channel=args.channel, block_address=block_addr)

        if args.follow:
            last_seq = 0
            try:
                while True:
                    res, _ = collector.read(
                        tail_lines=args.tail,
                        since_seq=last_seq if last_seq else None,
                        pattern=args.pattern,
                        regex=args.regex,
                        context_lines=args.context,
                        include_partial=False,
                        auto_start=False,
                    )
                    for e in res.entries:
                        print(f"[{e.seq}] {e.text}", flush=True)
                        last_seq = max(last_seq, e.seq)
                    time.sleep(0.05)
            except KeyboardInterrupt:
                return 0

        time.sleep(max(0.0, args.wait))
        res, status = collector.read(
            tail_lines=args.tail,
            pattern=args.pattern,
            regex=args.regex,
            context_lines=args.context,
            auto_start=False,
        )
        if args.json:
            print(json.dumps({"status": status.to_dict(), **res.to_dict()}, indent=2, ensure_ascii=False))
        else:
            print(res.to_text())
        return 0
    except (ProbeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        collector.stop()
        pm.disconnect()


if __name__ == "__main__":
    sys.exit(main())
