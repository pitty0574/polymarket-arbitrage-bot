"""
Polymarket API 憑證產生工具
在韓國伺服器上執行，用你的私鑰衍生 API Key、Secret 和 Passphrase
"""

import os
from py_clob_client.client import ClobClient

# 從環境變數讀取私鑰
private_key = os.getenv("PRIVATE_KEY", "")

if not private_key:
    print("❌ 請設定 PRIVATE_KEY 環境變數")
    exit(1)

print("🔑 正在從私鑰衍生 Polymarket API 憑證...")
print(f"   使用私鑰: {private_key[:6]}...{private_key[-4:]}")

try:
    client = ClobClient(
        "https://clob.polymarket.com",
        key=private_key,
        chain_id=137  # Polygon mainnet
    )
    
    # 衍生 API 憑證
    creds = client.derive_api_key()
    
    print("\n✅ API 憑證產生成功！")
    print("=" * 60)
    print(f"POLY_API_KEY={creds.api_key}")
    print(f"POLY_API_SECRET={creds.api_secret}")
    print(f"POLY_PASSPHRASE={creds.api_passphrase}")
    print("=" * 60)
    print("\n請將以上 3 個值複製到 Zeabur 的環境變數中")
    
except Exception as e:
    print(f"\n❌ 產生失敗: {e}")
    print("請確認私鑰格式正確（不含 0x 開頭）")
