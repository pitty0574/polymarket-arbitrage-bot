"""
設定管理模組 v2.0
從 .env 檔案載入所有設定參數
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # --- 錢包 & API ---
    private_key: str = os.getenv("PRIVATE_KEY", "")
    poly_api_key: str = os.getenv("POLY_API_KEY", "")
    poly_api_secret: str = os.getenv("POLY_API_SECRET", "")
    poly_passphrase: str = os.getenv("POLY_PASSPHRASE", "")

    # --- 套利參數 ---
    min_spread_pct: float = float(os.getenv("MIN_SPREAD_PCT", "5.0"))
    max_position_usdc: float = float(os.getenv("MAX_POSITION_USDC", "50"))
    scan_interval_sec: int = int(os.getenv("SCAN_INTERVAL_SEC", "20"))
    dry_run: bool = os.getenv("DRY_RUN", "true").lower() == "true"

    # --- 策略開關 ---
    enable_binary: bool = os.getenv("ENABLE_BINARY", "true").lower() == "true"
    enable_multi_outcome: bool = os.getenv("ENABLE_MULTI_OUTCOME", "true").lower() == "true"
    enable_reverse: bool = os.getenv("ENABLE_REVERSE", "true").lower() == "true"
    enable_cross_market: bool = os.getenv("ENABLE_CROSS_MARKET", "true").lower() == "true"
    enable_imbalance: bool = os.getenv("ENABLE_IMBALANCE", "true").lower() == "true"

    # --- Telegram 通知 ---
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # --- Polymarket API 端點 ---
    gamma_api_url: str = "https://gamma-api.polymarket.com"
    clob_api_url: str = "https://clob.polymarket.com"

    # --- 交易手續費 ---
    taker_fee_pct: float = 0.0
    maker_fee_pct: float = 0.0

    # --- 安全限制 ---
        max_daily_trades: int = int(os.getenv("MAX_DAILY_TRADES", "500"))
        max_daily_loss_usdc: float = float(os.getenv("MAX_DAILY_LOSS_USDC", "50"))
        max_concurrent_positions: int = int(os.getenv("MAX_CONCURRENT_POSITIONS", "10"))

    # --- 進階參數 ---
        min_liquidity: float = float(os.getenv("MIN_LIQUIDITY", "500"))
        min_volume_24h: float = float(os.getenv("MIN_VOLUME_24H", "5000"))
        imbalance_threshold: float = float(os.getenv("IMBALANCE_THRESHOLD", "0.80"))

    def validate(self) -> list[str]:
        errors = []
        if not self.dry_run:
            if not self.private_key:
                errors.append("真實交易模式需要設定 PRIVATE_KEY")
            if not self.poly_api_key:
                errors.append("真實交易模式需要設定 POLY_API_KEY")
            if not self.poly_api_secret:
                errors.append("真實交易模式需要設定 POLY_API_SECRET")
            if not self.poly_passphrase:
                errors.append("真實交易模式需要設定 POLY_PASSPHRASE")
        return errors


config = Config()
