# Polymarket 套利機器人

自動掃描 Polymarket 預測市場中的定價不一致，發現套利機會後自動執行交易。

## 套利原理

在預測市場中，每個事件的所有結果機率總和應該等於 100%（即 $1.00）。
當市場定價出現偏差，例如 YES 賣 $0.45 + NO 賣 $0.50 = $0.95 < $1.00 時，
同時買入 YES 和 NO，無論結果如何都能獲得 $0.05 的無風險利潤（5.26% 報酬率）。

## 套利類型

1. **二元套利**：YES + NO 價格總和 < $1.00
2. **多結果套利**：多選項市場中，所有結果最低買價總和 < $1.00
3. **跨市場套利**：相關聯的市場之間定價不一致

## 安裝

```bash
# 1. 安裝 Python 3.10+
# 2. 安裝依賴
pip install -r requirements.txt

# 3. 複製設定檔
cp .env.example .env

# 4. 編輯 .env 填入你的私鑰和設定
```

## 設定 .env 檔案

```
# Polygon 錢包私鑰（用於簽名交易）
PRIVATE_KEY=your_polygon_wallet_private_key

# Polymarket API 金鑰（從 https://polymarket.com 取得）
POLY_API_KEY=your_api_key
POLY_API_SECRET=your_api_secret
POLY_PASSPHRASE=your_passphrase

# 套利參數
MIN_SPREAD_PCT=2.0          # 最低套利價差百分比（扣除手續費後）
MAX_POSITION_USDC=50        # 單筆最大投入金額（USDC）
SCAN_INTERVAL_SEC=15        # 掃描間隔（秒）
DRY_RUN=true                # true=模擬模式，false=真實交易

# 通知（可選）
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

## 使用方式

```bash
# 模擬模式（建議先跑幾天觀察）
python main.py

# 確認無誤後，將 .env 中的 DRY_RUN 改為 false 即可真實交易
```

## 風險提示

- 此工具僅供學習和研究用途
- 預測市場交易涉及真實資金風險
- 套利機會可能因延遲、滑點、手續費而消失
- 請先用小額資金測試，確認穩定後再增加金額
- 請確保你所在地區允許使用 Polymarket
