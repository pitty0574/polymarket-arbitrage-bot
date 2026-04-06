"""
交易執行模組 v2.0
支援所有套利策略的執行
"""

import logging
import time
from dataclasses import dataclass, field
from config import config
from scanner import ArbitrageOpportunity

logger = logging.getLogger("executor")

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY
    CLOB_AVAILABLE = True
except ImportError:
    CLOB_AVAILABLE = False
    logger.warning("py-clob-client 未安裝，僅能使用模擬模式")


@dataclass
class TradeRecord:
    market_name: str
    arb_type: str
    amount_usdc: float
    expected_profit: float
    actual_orders: list = field(default_factory=list)
    status: str = "pending"
    timestamp: float = 0.0
    error: str = ""
    confidence: str = "medium"


class TradeExecutor:

    def __init__(self):
        self.clob_client = None
        self.daily_trades: list[TradeRecord] = []
        self.daily_profit: float = 0.0
        self.daily_loss: float = 0.0
        self._init_clob_client()

    def _init_clob_client(self):
        if config.dry_run:
            logger.info("🧪 模擬模式 - 不會執行真實交易")
            return
        if not CLOB_AVAILABLE:
            logger.error("py-clob-client 未安裝")
            return
        if not config.private_key:
            logger.error("未設定 PRIVATE_KEY")
            return
        try:
            self.clob_client = ClobClient(
                host=config.clob_api_url,
                key=config.private_key,
                chain_id=137,
            )
            # 使用已有的 API 憑證，而非重新產生
            from py_clob_client.clob_types import ApiCreds
            self.clob_client.set_api_creds(ApiCreds(
                api_key=config.poly_api_key,
                api_secret=config.poly_api_secret,
                api_passphrase=config.poly_passphrase,
            ))
            logger.info("✅ CLOB 客戶端初始化成功")
        except Exception as e:
            logger.error(f"CLOB 客戶端初始化失敗: {e}")
            self.clob_client = None

    def check_risk_limits(self, amount_usdc: float, opp: ArbitrageOpportunity) -> tuple[bool, str]:
        if len(self.daily_trades) >= config.max_daily_trades:
            return False, f"已達每日交易上限 ({config.max_daily_trades})"
        if self.daily_loss >= config.max_daily_loss_usdc:
            return False, f"已達每日虧損上限 (${config.max_daily_loss_usdc})"
        if amount_usdc > config.max_position_usdc:
            return False, f"超過單筆上限 (${config.max_position_usdc})"

        active = sum(1 for t in self.daily_trades if t.status == "executed")
        if active >= config.max_concurrent_positions:
            return False, f"已達最大同時持倉數 ({config.max_concurrent_positions})"

        # 低信心度交易減少倉位
        if opp.confidence == "low" and amount_usdc > config.max_position_usdc * 0.5:
            return True, "低信心度，建議減半倉位"

        return True, "通過"

    def calculate_order_sizes(self, opp: ArbitrageOpportunity, total_usdc: float) -> list[dict]:
        # 低信心度自動減半
        if opp.confidence == "low":
            total_usdc = total_usdc * 0.5

        if opp.arb_type == "imbalance":
            # 失衡策略：只買一個 token
            token = opp.tokens[0]
            shares = total_usdc / token["best_ask"]
            shares = min(shares, token["size"] * 0.5)  # 最多吃掉 50% 深度
            return [{
                "token_id": token["token_id"],
                "outcome": token["outcome"],
                "price": token["best_ask"],
                "size": shares,
                "cost_usdc": token["best_ask"] * shares,
                "side": BUY if CLOB_AVAILABLE else "BUY",
            }]

        # 標準 / 反向 / 跨市場套利：買入所有結果
        min_affordable = total_usdc / opp.total_cost
        min_available = min(t["size"] for t in opp.tokens)
        shares = min(min_affordable, min_available)

        orders = []
        for token in opp.tokens:
            price = token.get("best_ask", token.get("best_bid", 0.5))
            orders.append({
                "token_id": token["token_id"],
                "outcome": token["outcome"],
                "price": price,
                "size": shares,
                "cost_usdc": price * shares,
                "side": BUY if CLOB_AVAILABLE else "BUY",
            })
        return orders

    def execute_arbitrage(self, opp: ArbitrageOpportunity) -> TradeRecord:
        amount = config.max_position_usdc
        record = TradeRecord(
            market_name=opp.market_name,
            arb_type=opp.arb_type,
            amount_usdc=amount,
            expected_profit=opp.net_profit_after_fees * amount,
            timestamp=time.time(),
            confidence=opp.confidence,
        )

        passed, reason = self.check_risk_limits(amount, opp)
        if not passed:
            record.status = "failed"
            record.error = f"風控未通過: {reason}"
            logger.warning(f"❌ {record.error}")
            self.daily_trades.append(record)
            return record

        orders = self.calculate_order_sizes(opp, amount)

        if config.dry_run:
            return self._simulate_execution(record, orders, opp)
        else:
            return self._real_execution(record, orders, opp)

    def _simulate_execution(self, record, orders, opp):
        logger.info(f"🧪 模擬交易 [{opp.arb_type}]: {opp.market_name[:50]}")

        total_spent = 0.0
        for order in orders:
            total_spent += order["cost_usdc"]
            logger.info(
                f"   模擬買入 {order['outcome']}: "
                f"{order['size']:.2f} 份 @ ${order['price']:.4f} "
                f"= ${order['cost_usdc']:.4f}"
            )
            record.actual_orders.append({
                "outcome": order["outcome"],
                "price": order["price"],
                "size": order["size"],
                "cost": order["cost_usdc"],
                "order_id": f"SIM_{int(time.time())}",
            })

        record.amount_usdc = total_spent
        if opp.arb_type == "imbalance":
            record.expected_profit = total_spent * opp.spread_pct / 100
        else:
            record.expected_profit = (opp.guaranteed_payout - opp.total_cost) * orders[0]["size"]
        record.status = "simulated"

        logger.info(
            f"   ✅ 模擬完成 | 花費: ${total_spent:.4f} | "
            f"預期利潤: ${record.expected_profit:.4f} ({opp.spread_pct:.2f}%) | "
            f"信心: {opp.confidence}"
        )

        self.daily_trades.append(record)
        self.daily_profit += record.expected_profit
        return record

    def _real_execution(self, record, orders, opp):
        if not self.clob_client:
            record.status = "failed"
            record.error = "CLOB 客戶端未初始化"
            self.daily_trades.append(record)
            return record

        logger.info(f"💰 執行真實交易 [{opp.arb_type}]: {opp.market_name[:50]}")

        executed_orders = []
        total_spent = 0.0

        for order in orders:
            try:
                order_args = OrderArgs(
                    price=order["price"],
                    size=order["size"],
                    side=BUY,
                    token_id=order["token_id"],
                )
                signed_order = self.clob_client.create_order(order_args)
                result = self.clob_client.post_order(signed_order, OrderType.GTC)

                order_id = result.get("orderID", "unknown")
                executed_orders.append({
                    "outcome": order["outcome"],
                    "price": order["price"],
                    "size": order["size"],
                    "cost": order["cost_usdc"],
                    "order_id": order_id,
                })
                total_spent += order["cost_usdc"]
                logger.info(f"   ✅ 買入 {order['outcome']}: {order['size']:.2f} 份 | ID: {order_id}")
                time.sleep(0.3)

            except Exception as e:
                logger.error(f"   ❌ 下單失敗 ({order['outcome']}): {e}")
                record.actual_orders = executed_orders
                record.amount_usdc = total_spent
                record.status = "failed"
                record.error = str(e)
                self.daily_trades.append(record)
                self.daily_loss += total_spent
                return record

        record.actual_orders = executed_orders
        record.amount_usdc = total_spent
        record.expected_profit = (opp.guaranteed_payout - opp.total_cost) * orders[0]["size"]
        record.status = "executed"

        logger.info(f"   ✅ 全部成交 | 花費: ${total_spent:.4f} | 利潤: ${record.expected_profit:.4f}")

        self.daily_trades.append(record)
        self.daily_profit += record.expected_profit
        return record

    def get_daily_summary(self) -> dict:
        total = len(self.daily_trades)
        executed = sum(1 for t in self.daily_trades if t.status in ("executed", "simulated"))
        failed = sum(1 for t in self.daily_trades if t.status == "failed")

        by_type = {}
        for t in self.daily_trades:
            by_type[t.arb_type] = by_type.get(t.arb_type, 0) + 1
        type_breakdown = ", ".join(f"{k}: {v}" for k, v in by_type.items()) or "無"

        return {
            "scans": 0,
            "opportunities": 0,
            "trades": total,
            "executed": executed,
            "failed": failed,
            "total_profit": self.daily_profit,
            "total_loss": self.daily_loss,
            "win_rate": (executed / total * 100) if total > 0 else 0,
            "type_breakdown": type_breakdown,
        }

    def reset_daily_stats(self):
        self.daily_trades.clear()
        self.daily_profit = 0.0
        self.daily_loss = 0.0
