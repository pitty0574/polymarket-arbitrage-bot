"""
市場掃描模組 v2.1 — 修正 API 資料解析

修正：
- Gamma API 的 token 資料在 clobTokenIds + outcomes 欄位（JSON 字串）
- 過濾已關閉和零流動性市場
- 加入多種套利策略
"""

import json
import logging
import time
import requests
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import config

logger = logging.getLogger("scanner")

GAMMA_API = config.gamma_api_url
CLOB_API = config.clob_api_url


@dataclass
class ArbitrageOpportunity:
    market_id: str
    market_name: str
    market_slug: str
    arb_type: str
    tokens: list = field(default_factory=list)
    total_cost: float = 0.0
    guaranteed_payout: float = 1.0
    spread_pct: float = 0.0
    guaranteed_profit: float = 0.0
    net_profit_after_fees: float = 0.0
    volume_24h: float = 0.0
    liquidity: float = 0.0
    end_date: str = ""
    timestamp: float = 0.0
    confidence: str = "medium"
    depth_score: float = 0.0
    related_markets: list = field(default_factory=list)


@dataclass
class OrderBookSnapshot:
    token_id: str
    best_bid: float = 0.0
    best_ask: float = 1.0
    bid_size: float = 0.0
    ask_size: float = 0.0
    bid_depth_10pct: float = 0.0
    ask_depth_10pct: float = 0.0
    spread: float = 1.0
    imbalance_ratio: float = 0.0


def parse_json_field(value):
    """解析可能是 JSON 字串或已解析的欄位"""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def extract_tokens(market: dict) -> list[dict]:
    """
    從市場資料中提取 token 資訊
    Gamma API 的 token 資料分散在多個欄位：
    - clobTokenIds: JSON 字串，包含 token ID 列表
    - outcomes: JSON 字串，包含結果名稱列表
    - tokens: 有時是完整物件列表（新版 API）
    """
    # 方法一：直接用 tokens 欄位（新版 API）
    tokens_field = market.get("tokens", [])
    if tokens_field and isinstance(tokens_field, list) and len(tokens_field) > 0:
        if isinstance(tokens_field[0], dict) and "token_id" in tokens_field[0]:
            return tokens_field

    # 方法二：從 clobTokenIds + outcomes 組合
    clob_ids = parse_json_field(market.get("clobTokenIds", "[]"))
    outcomes = parse_json_field(market.get("outcomes", "[]"))

    if not clob_ids or not outcomes:
        return []

    if len(clob_ids) != len(outcomes):
        return []

    tokens = []
    for i in range(len(clob_ids)):
        tokens.append({
            "token_id": str(clob_ids[i]),
            "outcome": str(outcomes[i]),
        })

    return tokens


def is_valid_market(market: dict) -> bool:
    """檢查市場是否值得掃描"""
    # 跳過已關閉的市場
    if market.get("closed", False):
        return False

    # 跳過沒有 CLOB token 的市場
    clob_ids = parse_json_field(market.get("clobTokenIds", "[]"))
    if not clob_ids:
        return False

    # 跳過零流動性
    liq = float(market.get("liquidityNum", 0) or market.get("liquidity", 0) or 0)
    if liq <= 0:
        return False

    # 跳過價格為 0 的市場（已結算）
    prices = parse_json_field(market.get("outcomePrices", "[]"))
    if prices:
        try:
            total = sum(float(p) for p in prices)
            if total <= 0.01:
                return False
        except (ValueError, TypeError):
            pass

    return True


class MarketScanner:

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "PolymarketArbitrageBot/2.1",
        })
        self._cache_markets = []
        self._cache_time = 0
        self._market_cache_ttl = 120
        self._orderbook_cache = {}
        self._orderbook_cache_ttl = 5
        self.executor = ThreadPoolExecutor(max_workers=8)

    # ─── 市場資料 ───

    def fetch_active_markets(self, limit: int = 1000) -> list[dict]:
        """取得活躍市場，過濾已關閉和無效的"""
        now = time.time()
        if self._cache_markets and (now - self._cache_time) < self._market_cache_ttl:
            return self._cache_markets

        all_markets = []
        offset = 0
        page_size = 100

        while offset < limit:
            try:
                resp = self.session.get(
                    f"{GAMMA_API}/markets",
                    params={
                        "limit": min(page_size, limit - offset),
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
                all_markets.extend(batch)
                offset += len(batch)
                if len(batch) < page_size:
                    break
            except requests.RequestException as e:
                logger.error(f"取得市場列表失敗 (offset={offset}): {e}")
                break

        # 過濾有效市場
        valid_markets = [m for m in all_markets if is_valid_market(m)]

        self._cache_markets = valid_markets
        self._cache_time = now
        logger.info(f"取得 {len(all_markets)} 個市場，其中 {len(valid_markets)} 個有效")
        return valid_markets

    def fetch_events(self) -> list[dict]:
        try:
            resp = self.session.get(
                f"{GAMMA_API}/events",
                params={
                    "active": "true",
                    "closed": "false",
                    "limit": 200,
                    "order": "liquidityNum",
                    "ascending": "false",
                },
                timeout=15,
            )
            resp.raise_for_status()
            events = resp.json()
            # 過濾有效事件
            valid = [e for e in events if not e.get("closed", False)]
            return valid
        except requests.RequestException as e:
            logger.error(f"取得事件列表失敗: {e}")
            return []

    def fetch_orderbook_detailed(self, token_id: str) -> OrderBookSnapshot:
        now = time.time()
        if token_id in self._orderbook_cache:
            cached_time, cached_data = self._orderbook_cache[token_id]
            if (now - cached_time) < self._orderbook_cache_ttl:
                return cached_data

        snapshot = OrderBookSnapshot(token_id=token_id)

        try:
            resp = self.session.get(
                f"{CLOB_API}/book",
                params={"token_id": token_id},
                timeout=10,
            )
            resp.raise_for_status()
            book = resp.json()
        except requests.RequestException:
            self._orderbook_cache[token_id] = (now, snapshot)
            return snapshot

        bids = book.get("bids", [])
        asks = book.get("asks", [])

        if bids:
            best_bid_entry = max(bids, key=lambda x: float(x.get("price", 0)))
            snapshot.best_bid = float(best_bid_entry.get("price", 0))
            snapshot.bid_size = float(best_bid_entry.get("size", 0))
        if asks:
            best_ask_entry = min(asks, key=lambda x: float(x.get("price", 1)))
            snapshot.best_ask = float(best_ask_entry.get("price", 1))
            snapshot.ask_size = float(best_ask_entry.get("size", 0))

        snapshot.spread = snapshot.best_ask - snapshot.best_bid

        if snapshot.best_bid > 0:
            bid_threshold = snapshot.best_bid * 0.9
            snapshot.bid_depth_10pct = sum(
                float(b.get("size", 0)) for b in bids
                if float(b.get("price", 0)) >= bid_threshold
            )
        if snapshot.best_ask < 1:
            ask_threshold = snapshot.best_ask * 1.1
            snapshot.ask_depth_10pct = sum(
                float(a.get("size", 0)) for a in asks
                if float(a.get("price", 0)) <= ask_threshold
            )

        total_depth = snapshot.bid_depth_10pct + snapshot.ask_depth_10pct
        if total_depth > 0:
            snapshot.imbalance_ratio = snapshot.bid_depth_10pct / total_depth

        self._orderbook_cache[token_id] = (now, snapshot)
        return snapshot

    def fetch_orderbooks_parallel(self, token_ids: list[str]) -> dict[str, OrderBookSnapshot]:
        results = {}
        futures = {
            self.executor.submit(self.fetch_orderbook_detailed, tid): tid
            for tid in token_ids
        }
        for future in as_completed(futures):
            tid = futures[future]
            try:
                results[tid] = future.result()
            except Exception:
                results[tid] = OrderBookSnapshot(token_id=tid)
        return results

    # ─── 策略一：標準套利（買入所有結果 < $1.00）───

    def scan_standard_arbitrage(self, market: dict) -> ArbitrageOpportunity | None:
        tokens = extract_tokens(market)
        if len(tokens) < 2:
            return None

        is_binary = len(tokens) == 2
        arb_type = "binary" if is_binary else "multi_outcome"

        if arb_type == "binary" and not config.enable_binary:
            return None
        if arb_type == "multi_outcome" and not config.enable_multi_outcome:
            return None

        token_ids = [t["token_id"] for t in tokens]
        snapshots = self.fetch_orderbooks_parallel(token_ids)

        token_info_list = []
        total_cost = 0.0

        for token in tokens:
            tid = token["token_id"]
            outcome = token["outcome"]
            snap = snapshots.get(tid)

            if not snap or snap.ask_size <= 0:
                return None

            token_info_list.append({
                "token_id": tid,
                "outcome": outcome,
                "best_ask": snap.best_ask,
                "size": snap.ask_size,
                "depth": snap.ask_depth_10pct,
            })
            total_cost += snap.best_ask

        gross_profit = 1.0 - total_cost
        if gross_profit <= 0:
            return None

        total_fee = total_cost * (config.taker_fee_pct / 100) * len(token_info_list)
        net_profit = gross_profit - total_fee
        spread_pct = (net_profit / total_cost) * 100

        if spread_pct < config.min_spread_pct:
            return None

        min_size = min(t["size"] for t in token_info_list)
        if min_size < 1.0:
            return None

        min_depth = min(t["depth"] for t in token_info_list)
        depth_score = min(min_depth / 100, 1.0)

        confidence = "low"
        if spread_pct >= 2.0 and min_size >= 10:
            confidence = "high"
        elif spread_pct >= 1.0 and min_size >= 5:
            confidence = "medium"

        return ArbitrageOpportunity(
            market_id=market.get("conditionId", market.get("condition_id", "")),
            market_name=market.get("question", "Unknown"),
            market_slug=market.get("slug", ""),
            arb_type=arb_type,
            tokens=token_info_list,
            total_cost=total_cost,
            guaranteed_payout=1.0,
            spread_pct=spread_pct,
            guaranteed_profit=net_profit,
            net_profit_after_fees=net_profit,
            volume_24h=float(market.get("volume24hr", 0) or 0),
            liquidity=float(market.get("liquidityNum", 0) or market.get("liquidity", 0) or 0),
            end_date=market.get("endDateIso", ""),
            timestamp=time.time(),
            confidence=confidence,
            depth_score=depth_score,
        )

    # ─── 策略二：反向套利（賣價總和 > $1.00）───

    def scan_reverse_arbitrage(self, market: dict) -> ArbitrageOpportunity | None:
        if not config.enable_reverse:
            return None

        tokens = extract_tokens(market)
        if len(tokens) < 2:
            return None

        token_ids = [t["token_id"] for t in tokens]
        snapshots = self.fetch_orderbooks_parallel(token_ids)

        token_info_list = []
        total_bid = 0.0

        for token in tokens:
            tid = token["token_id"]
            outcome = token["outcome"]
            snap = snapshots.get(tid)

            if not snap or snap.best_bid <= 0 or snap.bid_size <= 0:
                return None

            token_info_list.append({
                "token_id": tid,
                "outcome": outcome,
                "best_bid": snap.best_bid,
                "best_ask": snap.best_ask,
                "size": snap.bid_size,
                "depth": snap.bid_depth_10pct,
            })
            total_bid += snap.best_bid

        gross_profit = total_bid - 1.0
        if gross_profit <= 0:
            return None

        total_fee = total_bid * (config.taker_fee_pct / 100) * len(token_info_list)
        net_profit = gross_profit - total_fee
        spread_pct = (net_profit / 1.0) * 100

        if spread_pct < config.min_spread_pct:
            return None

        min_size = min(t["size"] for t in token_info_list)
        if min_size < 1.0:
            return None

        min_depth = min(t["depth"] for t in token_info_list)

        return ArbitrageOpportunity(
            market_id=market.get("conditionId", ""),
            market_name=market.get("question", "Unknown"),
            market_slug=market.get("slug", ""),
            arb_type="reverse",
            tokens=token_info_list,
            total_cost=1.0,
            guaranteed_payout=total_bid,
            spread_pct=spread_pct,
            guaranteed_profit=net_profit,
            net_profit_after_fees=net_profit,
            volume_24h=float(market.get("volume24hr", 0) or 0),
            liquidity=float(market.get("liquidityNum", 0) or 0),
            end_date=market.get("endDateIso", ""),
            timestamp=time.time(),
            confidence="medium" if spread_pct >= 1.5 else "low",
            depth_score=min(min_depth / 100, 1.0),
        )

    # ─── 策略三：跨市場套利 ───

    def scan_cross_market_arbitrage(self, events: list[dict]) -> list[ArbitrageOpportunity]:
        if not config.enable_cross_market:
            return []

        opportunities = []

        for event in events:
            event_markets = event.get("markets", [])
            if len(event_markets) < 2:
                continue

            market_prices = []
            for market in event_markets:
                if market.get("closed", False):
                    continue

                tokens = extract_tokens(market)
                if len(tokens) != 2:
                    continue

                token_ids = [t["token_id"] for t in tokens]
                snapshots = self.fetch_orderbooks_parallel(token_ids)

                yes_token = None
                no_token = None
                for t in tokens:
                    snap = snapshots.get(t["token_id"])
                    if not snap:
                        continue
                    if t["outcome"].upper() in ("YES", "是"):
                        yes_token = {"token": t, "snap": snap}
                    else:
                        no_token = {"token": t, "snap": snap}

                if yes_token and no_token:
                    market_prices.append({
                        "market": market,
                        "yes": yes_token,
                        "no": no_token,
                        "yes_ask": yes_token["snap"].best_ask,
                        "no_ask": no_token["snap"].best_ask,
                    })

            for i in range(len(market_prices)):
                for j in range(i + 1, len(market_prices)):
                    opp = self._check_cross_market_pair(market_prices[i], market_prices[j])
                    if opp:
                        opportunities.append(opp)

        return opportunities

    def _check_cross_market_pair(self, m1, m2):
        combos = [
            (m1["yes_ask"], m2["no_ask"], "yes_m1"),
            (m1["no_ask"], m2["yes_ask"], "no_m1"),
        ]

        for cost_a, cost_b, direction in combos:
            total_cost = cost_a + cost_b
            if total_cost >= 0.95:
                continue

            net_profit = 1.0 - total_cost
            spread_pct = (net_profit / total_cost) * 100

            if spread_pct < max(config.min_spread_pct, 2.0):
                continue

            m1_name = m1["market"].get("question", "?")[:40]
            m2_name = m2["market"].get("question", "?")[:40]

            if "yes_m1" in direction:
                tokens = [
                    {"token_id": m1["yes"]["token"]["token_id"],
                     "outcome": f"YES ({m1_name})", "best_ask": cost_a,
                     "size": m1["yes"]["snap"].ask_size},
                    {"token_id": m2["no"]["token"]["token_id"],
                     "outcome": f"NO ({m2_name})", "best_ask": cost_b,
                     "size": m2["no"]["snap"].ask_size},
                ]
            else:
                tokens = [
                    {"token_id": m1["no"]["token"]["token_id"],
                     "outcome": f"NO ({m1_name})", "best_ask": cost_a,
                     "size": m1["no"]["snap"].ask_size},
                    {"token_id": m2["yes"]["token"]["token_id"],
                     "outcome": f"YES ({m2_name})", "best_ask": cost_b,
                     "size": m2["yes"]["snap"].ask_size},
                ]

            min_size = min(t["size"] for t in tokens)
            if min_size < 1.0:
                continue

            return ArbitrageOpportunity(
                market_id=f"cross_{m1['market'].get('conditionId', '')}",
                market_name=f"跨市場: {m1_name} vs {m2_name}",
                market_slug="cross_market",
                arb_type="cross_market",
                tokens=tokens,
                total_cost=total_cost,
                guaranteed_payout=1.0,
                spread_pct=spread_pct,
                guaranteed_profit=net_profit,
                net_profit_after_fees=net_profit,
                timestamp=time.time(),
                confidence="medium",
                depth_score=0.5,
                related_markets=[m1_name, m2_name],
            )
        return None

    # ─── 策略四：訂單簿失衡 ───

    def scan_orderbook_imbalance(self, market: dict) -> ArbitrageOpportunity | None:
        if not config.enable_imbalance:
            return None

        tokens = extract_tokens(market)
        if len(tokens) != 2:
            return None

        volume = float(market.get("volume24hr", 0) or market.get("volumeNum", 0) or 0)
        if volume < config.min_volume_24h:
            return None

        token_ids = [t["token_id"] for t in tokens]
        snapshots = self.fetch_orderbooks_parallel(token_ids)

        for token in tokens:
            tid = token["token_id"]
            outcome = token["outcome"]
            snap = snapshots.get(tid)

            if not snap or snap.best_ask >= 0.90 or snap.best_ask <= 0.10:
                continue
            if snap.ask_size < 5:
                continue

            if snap.imbalance_ratio > config.imbalance_threshold and snap.bid_depth_10pct > 50:
                expected_move = (snap.imbalance_ratio - 0.5) * 0.1
                spread_pct = expected_move / snap.best_ask * 100

                if spread_pct < config.min_spread_pct:
                    continue

                return ArbitrageOpportunity(
                    market_id=market.get("conditionId", ""),
                    market_name=f"[失衡] {market.get('question', 'Unknown')}",
                    market_slug=market.get("slug", ""),
                    arb_type="imbalance",
                    tokens=[{
                        "token_id": tid,
                        "outcome": outcome,
                        "best_ask": snap.best_ask,
                        "size": snap.ask_size,
                        "depth": snap.ask_depth_10pct,
                        "imbalance": snap.imbalance_ratio,
                    }],
                    total_cost=snap.best_ask,
                    guaranteed_payout=snap.best_ask * (1 + expected_move),
                    spread_pct=spread_pct,
                    guaranteed_profit=expected_move,
                    net_profit_after_fees=expected_move,
                    volume_24h=volume,
                    liquidity=float(market.get("liquidityNum", 0) or 0),
                    end_date=market.get("endDateIso", ""),
                    timestamp=time.time(),
                    confidence="low",
                    depth_score=min(snap.bid_depth_10pct / 200, 1.0),
                )
        return None

    # ─── 主掃描 ───

    def scan_market_for_arbitrage(self, market: dict) -> list[ArbitrageOpportunity]:
        opportunities = []

        opp = self.scan_standard_arbitrage(market)
        if opp:
            opportunities.append(opp)

        opp = self.scan_reverse_arbitrage(market)
        if opp:
            opportunities.append(opp)

        opp = self.scan_orderbook_imbalance(market)
        if opp:
            opportunities.append(opp)

        return opportunities

    def scan_all_markets(self) -> list[ArbitrageOpportunity]:
        markets = self.fetch_active_markets()
        opportunities = []

        for i, market in enumerate(markets):
            try:
                opps = self.scan_market_for_arbitrage(market)
                for opp in opps:
                    opportunities.append(opp)
                    emoji = {
                        "binary": "💰", "multi_outcome": "🎯",
                        "reverse": "🔄", "imbalance": "📊",
                    }.get(opp.arb_type, "💰")
                    logger.info(
                        f"{emoji} {opp.arb_type} | {opp.market_name[:50]} | "
                        f"價差: {opp.spread_pct:.2f}% | 信心: {opp.confidence} | "
                        f"利潤: ${opp.guaranteed_profit:.4f}"
                    )
            except Exception as e:
                logger.debug(f"掃描市場 {i} 時出錯: {e}")
                continue

            if (i + 1) % 20 == 0:
                time.sleep(0.3)

        # 跨市場套利
        try:
            events = self.fetch_events()
            if events:
                cross_opps = self.scan_cross_market_arbitrage(events)
                for opp in cross_opps:
                    opportunities.append(opp)
                    logger.info(
                        f"🔗 跨市場套利 | {opp.market_name[:60]} | "
                        f"價差: {opp.spread_pct:.2f}%"
                    )
        except Exception as e:
            logger.debug(f"跨市場掃描出錯: {e}")

        confidence_order = {"high": 0, "medium": 1, "low": 2}
        opportunities.sort(
            key=lambda x: (confidence_order.get(x.confidence, 2), -x.spread_pct)
        )

        by_type = {}
        for opp in opportunities:
            by_type[opp.arb_type] = by_type.get(opp.arb_type, 0) + 1
        type_summary = ", ".join(f"{k}: {v}" for k, v in by_type.items()) or "無"

        logger.info(
            f"掃描完成: {len(markets)} 個有效市場 | "
            f"發現 {len(opportunities)} 個機會 | 分布: {type_summary}"
        )
        return opportunities
