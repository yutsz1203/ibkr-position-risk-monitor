import logging
import uuid
from dataclasses import dataclass, replace

from ib_async import (
    IB,
    CommissionReport,
    Contract,
    Fill,
    LimitOrder,
    MarketOrder,
    Order,
    Trade,
)

from config import WATCHLIST

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Holding:
    symbol: str
    qty: float
    avg_cost: float  # positive even when short
    commission: float = 0.0


positions: dict[str, Holding] = {}  # key: symbol
seen_exec: dict[str, Fill] = {}  # key: exec_id
commissioned: dict[str, float] = {}  # key: exec_id
trades: dict[int, Trade] = {}  # key: orderID


def validate_order_request(
    contract: Contract, action: str, qty: float, limit_price: float | None = None
) -> str:
    """Validate order requests.

    Perform simple checking on the action, symbol, contract id, and quantity.

    Action: Must be either `BUY` or `SELL`.
    Symbol: Must be in `WATCHLIST`.
    Contract ID: Must not be 0 (unqualified).
    Qty: Must be > 0.
    Limit Price: If given, it should be > 0.

    Args:
        contract: A IB Contract instance.
        action: BUY / SELL.
        qty: Quantity to buy / sell.
        limit_price: Price for limit order. If not given, it is a market order.

    Returns:
        String of `BUY` or `SELL`

    Raises:
        ValueError: An error occurred when one of the above conditions is violated.
    """
    norm_action = action.upper()
    if norm_action not in {"BUY", "SELL"}:
        raise ValueError("Action should be either 'BUY' or 'SELL'.")
    if contract.symbol not in WATCHLIST:
        raise ValueError(f"{contract.symbol} is not on watchlist.")
    if contract.conId == 0:
        raise ValueError("Contract not qualified.")
    if qty <= 0:
        raise ValueError(f"Quantity should be larger than 0, got {qty}.")
    if limit_price is not None and not (limit_price > 0):
        raise ValueError("Limit price should be larger than 0.")
    return norm_action


def build_order(
    action: str, qty: float, limit_price: float | None = None, order_ref: str = ""
) -> Order:
    """Build IB order instance.

    Args:
        action: `BUY` or `SELL`
        qty: Quantity for action.
        limit_price: Price for limit orders. If not provided, it is a market order.
        order_ref: Order reference. Format in `prm-xxxxxxxx`

    Returns:
        Order instance (LimitOrder/MarketOrder)
    """
    order = (
        MarketOrder(action, qty)
        if limit_price is None
        else LimitOrder(action, qty, round(limit_price, 2))
    )
    order.orderRef = order_ref
    order.tif = "DAY"
    return order


def place_order(
    ib: IB,
    contract: Contract,
    action: str,
    qty: float,
    limit_price: float | None = None,
) -> Trade:
    """To place an order

    Args:
        ib: An IB instance.
        contract: A Contract instance.
        action: `BUY` / `SELL`.
        qty: Quantity for the action.
        limit_price: Price for limit orders. If not provided, it is a market order.
    Returns:
        a Trade instance
    """
    if limit_price is not None:
        limit_price = round(limit_price, 2)
    action = validate_order_request(contract, action, qty, limit_price)
    order_ref = f"prm-{uuid.uuid4().hex[:8]}"
    order = build_order(action, qty, limit_price, order_ref)
    order_type = order.orderType
    if order_type == "LMT":
        log.info(
            f"[{order_ref}] Submitting limit order: {contract.symbol} - {action} {qty} at {order.lmtPrice}."
        )
    else:
        log.info(
            f"[{order_ref}] Submitting market order: {contract.symbol} - {action} {qty}."
        )
    trade = ib.placeOrder(contract, order)
    trades[trade.order.orderId] = trade
    log.info(
        f"[{order_ref}] Trade accepted. Order ID: {trade.order.orderId}, Status: {trade.orderStatus.status}."
    )
    return trade


def cancel_order(ib: IB, order_id: int) -> Trade | None:
    """Cancel an order.

    First check if trade is in trades.
    If not, fall back to check if in IB opentrades. Log warning if not in IB open trades.
    Then, check if the trade is done.

    Args:
        ib: An IB instance.
        order_id: The order_id to cancel.

    Returns:
        trade: the trade to cancel, or None if there is nothing to cancel.

    """
    trade = trades.get(order_id)

    if trade is None:
        open_trades = ib.openTrades()
        for t in open_trades:
            if order_id == t.order.orderId:
                log.info(f"Found Order id: {order_id} in IB open trades.")
                trade = t
                trades[order_id] = trade
                break
        if trade is None:
            log.warning(f"Order id: {order_id} not found. Could not cancel.")
            return None

    if trade.isDone():
        log.warning(
            f"[{trade.order.orderRef}][{trade.order.orderId}] CANCEL SKIPPED symbol: {trade.contract.symbol}, status: {trade.orderStatus.status}, filled: {trade.orderStatus.filled}, remaining: {trade.orderStatus.remaining}, avgFillPrice: {trade.orderStatus.avgFillPrice}."
        )
        return None

    log.info(
        f"[{trade.order.orderRef}][{trade.order.orderId}] CANCEL REQUESTED: symbol: {trade.contract.symbol}, status: {trade.orderStatus.status}, filled: {trade.orderStatus.filled}, remaining: {trade.orderStatus.remaining}, avgFillPrice: {trade.orderStatus.avgFillPrice}."
    )
    ib.cancelOrder(trade.order)
    return trade


def on_order_status(trade: Trade) -> None:
    """Event handler for statusEvent

    Args:
        trade: A Trade Instance.
    """
    log.info(
        f"[{trade.order.orderRef}][{trade.order.orderId}] Order status update: permId: {trade.order.permId}, symbol: {trade.contract.symbol}, status: {trade.orderStatus.status}, filled: {trade.orderStatus.filled}, remaining: {trade.orderStatus.remaining}, avgFillPrice: {trade.orderStatus.avgFillPrice}."
    )


def on_exec_details(trade: Trade, fill: Fill) -> None:
    """Event handler for execution.

    If execution existed, a warning is logged. Otherwise, the fill is applied, recorded to seen_exec, and log info.

    Args:
        trade: A Trade instance.
        fill: A Fill instance.

    """
    e = fill.execution
    exec_id = e.execId
    if exec_id in seen_exec:
        prev = seen_exec[e.execId].execution
        if prev.price == e.price and prev.shares == e.shares:
            # seen, identical
            log.warning(
                f"[{e.orderRef}][{e.orderId}] DUPLICATE fill ignored "
                f"| exec_id={e.execId} symbol={fill.contract.symbol}"
            )
        else:
            # seen, different values
            log.warning(
                f"[{e.orderRef}][{e.orderId}] CORRECTED fill "
                f"| exec_id={e.execId} symbol={fill.contract.symbol} "
                f"| was {prev.shares}@{prev.price} now {e.shares}@{e.price}"
            )
        return
    # not seen
    log.info(
        f"[{e.orderRef}][{e.orderId}] FILL {e.side} {e.shares} {fill.contract.symbol} @ {e.price} "
        f"| exec_id={e.execId} time={e.time} | ib_cum={e.cumQty} ib_avg={e.avgPrice}"
    )
    symbol = fill.contract.symbol
    before = positions.get(symbol)
    after = apply_fill(before, fill)
    positions[symbol] = after
    seen_exec[exec_id] = fill

    before_qty = before.qty if before else 0.0
    before_avg = before.avg_cost if before else 0.0
    signed = after.qty - before_qty

    if before and before.qty * after.qty < 0:
        log.warning(
            f"[{e.orderRef}][{e.orderId}] CROSSED ZERO {symbol} "
            f"| {before_qty}@{before_avg} -> {after.qty}@{after.avg_cost} "
            f"| exec_id={exec_id} signed={signed}"
        )

    log.info(
        f"[{e.orderRef}][{e.orderId}] POSITION {symbol} | before {before_qty}@{before_avg} | after {after.qty}@{after.avg_cost} | signed={signed} price={e.price}"
    )


def on_commission_report(trade: Trade, fill: Fill, report: CommissionReport) -> None:
    """Event handler for commissionReportEvent.

    If the commission is seen before, a warning is logged.
    Otherwise, check if it is one of the holding in positions.
    Finally, update the commission of that holding.

    Args:
        trade: A Trade instance.
        fill: A Fill instance.
        report: A CommissionReport instance.
    """
    exec_id = report.execId
    symbol = fill.contract.symbol
    order_ref = fill.execution.orderRef
    order_id = fill.execution.orderId
    commission = report.commission
    currency = report.currency
    realized_pnl = report.realizedPNL

    if exec_id in commissioned:
        if commission == commissioned[exec_id]:
            # seen, identical
            log.warning(
                f"[{order_ref}][{order_id}] DUPLICATE commission ignored "
                f"| exec_id={exec_id} symbol={symbol}"
            )
        else:
            # seen, different values
            log.warning(
                f"[{order_ref}][{order_id}] CORRECTED commission "
                f"| exec_id={exec_id} symbol={symbol} "
                f"| was {commissioned[exec_id]} now {commission}"
            )
        return

    if symbol not in positions:
        log.warning(f"{symbol} not in positions. Could not update commission.")
        return

    # not seen and holding exists
    holding = positions[symbol]
    old_commission = holding.commission
    new_commission = old_commission + commission
    positions[symbol] = replace(holding, commission=new_commission)
    commissioned[exec_id] = commission
    log.info(
        f"[{order_ref}][{order_id}] COMMISSION {fill.contract.symbol} "
        f"| exec_id={exec_id} | old_commission={old_commission} | new_commission={new_commission} | currency={currency} | realized_pnl={realized_pnl}"
    )


def apply_fill(holding: Holding | None, fill: Fill) -> Holding:
    """Apply fill orders on holding, updating its quantity and average cost.

    avg_cost is the average price paid for the shares currently holding. Buying more changes, selling doesn't.

    Args:
        holding: A Holding instance. Can be none (no position yet).
        fill: A Fill instance.

    Returns:
        holding: A Holding instance.

    Raises:
        ValueError: If fill.execution.side is not either `BOT` or `SLD`
    """
    holding = holding or Holding(symbol=fill.contract.symbol, qty=0.0, avg_cost=0.0)
    e = fill.execution
    side = e.side
    if side not in {"BOT", "SLD"}:
        raise ValueError(f"Unexpected execution side: {side!r}")
    shares = e.shares
    signed_qty = shares if side == "BOT" else -shares
    price = e.price

    old_qty = holding.qty
    old_avg = holding.avg_cost
    new_qty = old_qty + signed_qty

    # opening or increasing
    if old_qty == 0 or old_qty * signed_qty > 0:
        new_avg = (abs(old_qty) * old_avg + shares * price) / abs(new_qty)
    # reducing, not crossing
    elif shares <= abs(old_qty):
        new_avg = 0.0 if abs(new_qty) < 1e-9 else old_avg
    # crossing zero - flip long to short
    else:
        new_avg = price

    return replace(holding, qty=new_qty, avg_cost=new_avg)
