import json
import requests

s = requests.Session()
s.headers.update({"Accept": "application/json"})

print("=== 測試 1: 檢查市場資料結構 ===")
r = s.get("https://gamma-api.polymarket.com/markets", params={"limit": 3, "active": "true"}, timeout=15)
print(f"狀態碼: {r.status_code}")
markets = r.json()
print(f"取得 {len(markets)} 個市場\n")

if markets:
    m = markets[0]
    print("--- 第一個市場的所有欄位 ---")
    for key in m.keys():
        val = m[key]
        val_str = str(val)[:100]
        print(f"  {key}: {val_str}")

    print("\n--- 找 token 相關欄位 ---")
    for key in m.keys():
        low = key.lower()
        if "token" in low or "clob" in low or "condition" in low or "outcome" in low:
            print(f"  {key} = {str(m[key])[:200]}")

print("\n=== 測試 2: 用 events API 試試 ===")
r2 = s.get("https://gamma-api.polymarket.com/events", params={"limit": 2, "active": "true"}, timeout=15)
print(f"狀態碼: {r2.status_code}")
events = r2.json()
print(f"取得 {len(events)} 個事件\n")

if events:
    e = events[0]
    print("--- 第一個事件的所有欄位 ---")
    for key in e.keys():
        val_str = str(e[key])[:150]
        print(f"  {key}: {val_str}")

    event_markets = e.get("markets", [])
    if event_markets:
        em = event_markets[0]
        print(f"\n--- 事件內第一個市場的所有欄位 ---")
        for key in em.keys():
            val_str = str(em[key])[:200]
            print(f"  {key}: {val_str}")

print("\n=== 測試 3: 單一市場詳細 API ===")
if markets:
    cid = markets[0].get("condition_id", "") or markets[0].get("id", "")
    if cid:
        r3 = s.get(f"https://gamma-api.polymarket.com/markets/{cid}", timeout=15)
        print(f"狀態碼: {r3.status_code}")
        if r3.status_code == 200:
            detail = r3.json()
            for key in detail.keys():
                low = key.lower()
                if "token" in low or "clob" in low or "outcome" in low:
                    print(f"  {key} = {str(detail[key])[:200]}")
