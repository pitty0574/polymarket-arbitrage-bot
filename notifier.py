"""
通知模組 v2.0
支援 Telegram 推播，訊息更豐富
"""

import logging
import requests
from datetime import datetime
from config import config

logger = logging.getLogger("notifier")


def send_telegram(message: str) -> bool:
    if not config.telegram_bot_token or not config.telegram_chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": config.telegram_chat_id,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        logger.warning(f"Telegram 發送失敗: {e}")
        return False


def notify_opportunity(opp: dict):
    type_emoji = {
        "binary": "💰", "multi_outcome": "🎯",
        "reverse": "🔄", "cross_market": "🔗",
        "imbalance": "📊",
    }
    emoji = type_emoji.get(opp.get("arb_type", ""), "💰")
    confidence = opp.get("confidence", "medium")
    conf_emoji = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(confidence, "⚪")

    msg = (
        f"{emoji} <b>套利機會</b>\n"
        f"市場: {opp['market_name']}\n"
        f"類型: {opp['arb_type']}\n"
        f"價差: {opp['spread_pct']:.2f}%\n"
        f"買入成本: ${opp['total_cost']:.4f}\n"
        f"預期利潤: ${opp['guaranteed_profit']:.4f}\n"
        f"信心度: {conf_emoji} {confidence}\n"
        f"時間: {datetime.now().strftime('%H:%M:%S')}"
    )
    logger.info(msg.replace("<b>", "").replace("</b>", ""))
    send_telegram(msg)


def notify_trade_executed(trade: dict):
    mode = "🧪 模擬" if config.dry_run else "💰 真實"
    msg = (
        f"{mode} <b>交易執行</b>\n"
        f"市場: {trade['market_name']}\n"
        f"投入: ${trade['amount_usdc']:.2f}\n"
        f"預期利潤: ${trade['expected_profit']:.4f}\n"
        f"時間: {datetime.now().strftime('%H:%M:%S')}"
    )
    logger.info(msg.replace("<b>", "").replace("</b>", ""))
    send_telegram(msg)


def notify_error(error_msg: str):
    msg = f"⚠️ <b>錯誤</b>\n{error_msg}"
    logger.error(error_msg)
    send_telegram(msg)


def notify_daily_summary(summary: dict):
    msg = (
        f"📊 <b>每日結算</b>\n"
        f"掃描次數: {summary['scans']}\n"
        f"發現機會: {summary['opportunities']}\n"
        f"執行交易: {summary['trades']}\n"
        f"總利潤: ${summary['total_profit']:.2f}\n"
        f"類型分布: {summary.get('type_breakdown', 'N/A')}\n"
        f"勝率: {summary['win_rate']:.1f}%"
    )
    logger.info(msg.replace("<b>", "").replace("</b>", ""))
    send_telegram(msg)


def notify_scan_status(scan_count: int, markets: int, opportunities: int):
    """每 50 輪掃描推送一次狀態"""
    if scan_count % 50 != 0:
        return
    msg = (
        f"📡 <b>掃描狀態</b>\n"
        f"已掃描: {scan_count} 輪\n"
        f"市場數: {markets}\n"
        f"累計機會: {opportunities}\n"
        f"時間: {datetime.now().strftime('%H:%M:%S')}"
    )
    send_telegram(msg)
