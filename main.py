"""
Polymarket 套利機器人 v2.0 — 主程式

使用方式：
  python main.py              # 正常啟動
  python main.py --dry-run    # 強制模擬模式
  python main.py --scan-once  # 只掃描一次
"""

import argparse
import logging
import signal
import sys
import time
from datetime import datetime

from config import config
from scanner import MarketScanner
from executor import TradeExecutor
from notifier import (
    notify_opportunity,
    notify_trade_executed,
    notify_error,
    notify_daily_summary,
    notify_scan_status,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("arbitrage.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")

running = True


def signal_handler(sig, frame):
    global running
    logger.info("收到停止信號，正在關閉...")
    running = False


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def print_banner():
    mode = "🧪 模擬模式" if config.dry_run else "💰 真實交易模式"
    strategies = []
    if config.enable_binary:
        strategies.append("二元套利")
    if config.enable_multi_outcome:
        strategies.append("多結果套利")
    if config.enable_reverse:
        strategies.append("反向套利")
    if config.enable_cross_market:
        strategies.append("跨市場套利")
    if config.enable_imbalance:
        strategies.append("失衡偵測")

    print("\n" + "=" * 60)
    print("   Polymarket 套利機器人 v2.0")
    print("=" * 60)
    print(f"   模式:         {mode}")
    print(f"   策略:         {', '.join(strategies)}")
    print(f"   最低價差:     {config.min_spread_pct}%")
    print(f"   單筆上限:     ${config.max_position_usdc}")
    print(f"   掃描間隔:     {config.scan_interval_sec} 秒")
    print(f"   每日交易上限: {config.max_daily_trades} 筆")
    print(f"   每日虧損上限: ${config.max_daily_loss_usdc}")
    tg = "✅ 已設定" if config.telegram_bot_token else "❌ 未設定"
    print(f"   Telegram:     {tg}")
    print("=" * 60 + "\n")


def run_scan_cycle(scanner, executor, scan_count):
    logger.info(f"── 第 {scan_count} 輪掃描開始 ──")

    opportunities = scanner.scan_all_markets()

    if not opportunities:
        return 0

    logger.info(f"發現 {len(opportunities)} 個套利機會")
    executed_count = 0

    for opp in opportunities:
        notify_opportunity({
            "market_name": opp.market_name,
            "arb_type": opp.arb_type,
            "spread_pct": opp.spread_pct,
            "total_cost": opp.total_cost,
            "guaranteed_profit": opp.guaranteed_profit,
            "confidence": opp.confidence,
        })

        record = executor.execute_arbitrage(opp)

        if record.status in ("executed", "simulated"):
            executed_count += 1
            notify_trade_executed({
                "market_name": record.market_name,
                "amount_usdc": record.amount_usdc,
                "expected_profit": record.expected_profit,
            })
        elif record.status == "failed":
            notify_error(f"交易失敗: {record.error}")

        time.sleep(1)

    logger.info(
        f"── 第 {scan_count} 輪完成: "
        f"{len(opportunities)} 機會, {executed_count} 筆執行 ──"
    )
    return len(opportunities)


def daily_reset(executor):
    summary = executor.get_daily_summary()
    notify_daily_summary(summary)
    logger.info(
        f"📊 每日結算: {summary['trades']} 筆, "
        f"利潤 ${summary['total_profit']:.2f}, "
        f"類型: {summary['type_breakdown']}"
    )
    executor.reset_daily_stats()


def main():
    parser = argparse.ArgumentParser(description="Polymarket 套利機器人 v2.0")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--scan-once", action="store_true")
    parser.add_argument("--min-spread", type=float)
    args = parser.parse_args()

    if args.dry_run:
        config.dry_run = True
    if args.min_spread:
        config.min_spread_pct = args.min_spread

    errors = config.validate()
    if errors:
        for err in errors:
            logger.error(err)
        sys.exit(1)

    print_banner()

    scanner = MarketScanner()
    executor = TradeExecutor()

    if args.scan_once:
        logger.info("單次掃描模式")
        run_scan_cycle(scanner, executor, 1)
        summary = executor.get_daily_summary()
        print(f"\n掃描結果: {summary['trades']} 筆交易, "
              f"預期利潤 ${summary['total_profit']:.2f}")
        return

    scan_count = 0
    total_opportunities = 0
    last_reset = datetime.now().date()

    logger.info("🚀 機器人 v2.0 啟動，開始持續掃描...")

    while running:
        try:
            today = datetime.now().date()
            if today > last_reset:
                daily_reset(executor)
                last_reset = today

            scan_count += 1
            found = run_scan_cycle(scanner, executor, scan_count)
            total_opportunities += found

            # 定期推送狀態
            notify_scan_status(scan_count, len(scanner._cache_markets), total_opportunities)

            if running:
                for _ in range(config.scan_interval_sec):
                    if not running:
                        break
                    time.sleep(1)

        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"主迴圈錯誤: {e}", exc_info=True)
            notify_error(f"主迴圈錯誤: {e}")
            time.sleep(30)

    logger.info("正在關閉...")
    daily_reset(executor)
    logger.info(f"機器人已停止 | 掃描: {scan_count} 次 | 機會: {total_opportunities} 個")


if __name__ == "__main__":
    main()
