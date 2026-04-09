#!/usr/bin/env python3
"""
鲸鱼预警模拟交易启动器
运行: python run_paper_trade.py
"""
import argparse
import asyncio
import signal
import logging
import threading

from alerts.logger import setup_logging
from main import WhaleAlertService


def main():
    parser = argparse.ArgumentParser(description="Whale Alert Paper Trading")
    parser.add_argument("--capital", type=float, default=10000, help="Initial capital (USD)")
    parser.add_argument("--backtest", action="store_true", help="Backtest mode (future)")
    parser.add_argument("--no-web", action="store_true", help="Disable web dashboard")
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger(__name__)

    service = WhaleAlertService()
    service.trader.initial_capital = args.capital

    logger.info(f"🐋 Paper Trading Mode | Capital: ${args.capital:,.0f}")

    if not args.no_web:
        from web.dashboard import start_dashboard, set_managers
        set_managers(service.token_manager, service.pump_monitor, service.scanner)
        threading.Thread(target=start_dashboard, daemon=True).start()
        logger.info("📊 Dashboard: http://localhost:8888")

    signal.signal(signal.SIGINT, lambda *_: service.stop())
    signal.signal(signal.SIGTERM, lambda *_: service.stop())

    try:
        asyncio.run(service.run_loop())
    finally:
        stats = service.trader.get_stats(30)
        print("\n" + "=" * 50)
        print(service.trader.format_stats_message(stats))
        print("=" * 50)


if __name__ == "__main__":
    main()
