"""
市場診斷工具 v2.1 — 修正資料解析
"""

import json
import time
import requests
from collections import defaultdict

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

session = requests.Session()
session.headers.update({"Accept": "application/json"})


def parse_json_field(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except:
            return []
    return []


def fetch_markets(limit=200):
    markets = []
    offset = 0
    while offset < limit:
        try:
            resp = session.get(
                f"{GAMMA_API}/markets",
                params={
                    "limit": min(100, limit - offset),
                    "offset": offset,
                    "active": "true",
                    "closed": "false",
                    "order": "liquidityNum",
                    "ascending": "false",
                },
                timeout=15,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            markets.extend(batch)
            offset += len(batch)
            if len(batch) < 100:
                break
        except Exception as e:
            print(f"Error: {e}")
            break

    # 過濾有效市場
    valid = []
    for m in markets:
        if m.get("closed", False):
            continue
        clob_ids = parse_json_field(m.get("clobTokenIds", "[]"))
        if not clob_ids:
            continue
        liq = float(m.get("liquidityNum", 0) or m.get("liquidity", 0) or 0)
        if liq <= 0:
            continue
        valid.append(m)

    return valid


def get_orderbook(token_id):
    try:
        resp = session.get(
            f"{CLOB_API}/book",
            params={"token_id": token_id},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except:
        return {"bids": [], "asks": []}


def analyze_market(market):
    clob_ids = parse_json_field(market.get("clobTokenIds", "[]"))
    outcomes = parse_json_field(market.get("outcomes", "[]"))

    if not clob_ids or len(clob_ids) < 2:
        return None
    if len(clob_ids) != len(outcomes):
        return None

    results = []
    total_ask = 0.0
    total_bid = 0.0

    for i in range(len(clob_ids)):
        token_id = str(clob_ids[i])
        outcome = str(outcomes[i]) if i < len(outcomes) else f"Outcome {i}"

        book = get_orderbook(token_id)
        asks = book.get("asks", [])
        bids = book.get("bids", [])

        best_ask = float(min(asks, key=lambda x: float(x["price"]))["price"]) if asks else None
        best_bid = float(max(bids, key=lambda x: float(x["price"]))["price"]) if bids else None
        ask_size = float(min(asks, key=lambda x: float(x["price"]))["size"]) if asks else 0
        bid_size = float(max(bids, key=lambda x: float(x["price"]))["size"]) if bids else 0

        if best_ask is None:
            return None

        total_ask += best_ask
        if best_bid:
            total_bid += best_bid

        results.append({
            "outcome": outcome,
            "best_ask": best_ask,
            "best_bid": best_bid,
            "ask_size": ask_size,
            "bid_size": bid_size,
        })

    return {
        "name": market.get("question", "?")[:70],
        "num_outcomes": len(clob_ids),
        "total_ask": total_ask,
        "total_bid": total_bid,
        "ask_gap": total_ask - 1.0,
        "bid_gap": total_bid - 1.0,
        "tokens": results,
        "volume": float(market.get("volumeNum", 0) or 0),
        "liquidity": float(market.get("liquidityNum", 0) or 0),
    }


def main():
    print("=" * 70)
    print("   Polymarket 市場診斷工具 v2.1")
    print("=" * 70)
    print("正在取得市場資料...\n")

    markets = fetch_markets(200)
    print(f"取得 {len(markets)} 個有效市場，開始分析訂單簿...\n")

    results = []
    for i, market in enumerate(markets):
        r = analyze_market(market)
        if r:
            results.append(r)
        if (i + 1) % 10 == 0:
            print(f"  已分析 {i + 1}/{len(markets)} 個市場...")
            time.sleep(0.3)

    print(f"\n成功分析 {len(results)} 個市場\n")

    # 統計
    print("=" * 70)
    print("YES+NO 賣價總和分布")
    print("=" * 70)

    buckets = defaultdict(int)
    for r in results:
        gap = r["ask_gap"]
        if gap < -0.02:
            buckets["< 0.98 (套利 > 2%)"] += 1
        elif gap < -0.01:
            buckets["0.98~0.99 (套利 1-2%)"] += 1
        elif gap < -0.005:
            buckets["0.99~0.995 (套利 0.5-1%)"] += 1
        elif gap < 0:
            buckets["0.995~1.00 (微套利)"] += 1
        elif gap < 0.005:
            buckets["1.00~1.005 (無套利)"] += 1
        elif gap < 0.01:
            buckets["1.005~1.01"] += 1
        elif gap < 0.02:
            buckets["1.01~1.02"] += 1
        else:
            buckets["> 1.02"] += 1

    for label in [
        "< 0.98 (套利 > 2%)", "0.98~0.99 (套利 1-2%)",
        "0.99~0.995 (套利 0.5-1%)", "0.995~1.00 (微套利)",
        "1.00~1.005 (無套利)", "1.005~1.01", "1.01~1.02", "> 1.02"
    ]:
        count = buckets.get(label, 0)
        bar = "#" * count
        print(f"  {label:35s} | {count:3d} {bar}")

    # TOP 20
    print("\n" + "=" * 70)
    print("TOP 20 最接近套利的市場")
    print("=" * 70)

    results.sort(key=lambda x: x["total_ask"])

    for i, r in enumerate(results[:20]):
        gap_pct = r["ask_gap"] * 100
        status = "YES" if r["ask_gap"] < 0 else "NO "
        print(f"\n  #{i+1} [{status}] 總和: ${r['total_ask']:.4f} ({gap_pct:+.2f}%)")
        print(f"     {r['name']}")
        for t in r["tokens"]:
            bid_str = f"${t['best_bid']:.4f}" if t['best_bid'] else "N/A"
            print(f"     - {t['outcome']:6s}: 賣${t['best_ask']:.4f}(量:{t['ask_size']:.0f}) 買{bid_str}(量:{t['bid_size']:.0f})")
        print(f"     成交量: ${r['volume']:,.0f} | 流動性: ${r['liquidity']:,.0f}")

    # 結論
    arb_count = sum(1 for r in results if r["ask_gap"] < 0)
    near_arb = sum(1 for r in results if 0 <= r["ask_gap"] < 0.01)

    print("\n" + "=" * 70)
    print("診斷結論")
    print("=" * 70)
    print(f"  分析市場數: {len(results)}")
    print(f"  存在套利:   {arb_count} 個")
    print(f"  接近套利:   {near_arb} 個（差距 < 1%）")


if __name__ == "__main__":
    main()
