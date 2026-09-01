import logging
from dataclasses import replace
from datetime import datetime, timezone

import redis
from ib_async import IB, Trade
from ib_async.objects import UNSET_DOUBLE

from config import BASE_CURRENCY, REDIS_URL

from .models import Holding, OpenOrder, Snapshot

POSITION_INDEX = "positions:symbols"
OPEN_ORDER_INDEX = "orders:open"

log = logging.getLogger(__name__)
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)


def check_redis(r: redis.Redis) -> bool:
    try:
        return r.ping()
    except redis.RedisError:
        log.error(f"Redis is unreachable at {REDIS_URL}.")
        return False


def write_position(client: redis.Redis, holding: Holding) -> None:
    """Write one position to Redis, and index its symbol.

    Args:
        client: A Redis client, or a pipeline.
        holding: The position after the fill.
    """
    client.hset(
        f"position:{holding.symbol}",
        mapping={
            "symbol": holding.symbol,
            "qty": holding.qty,
            "avg_cost": f"{holding.avg_cost:.4f}",
            "commission": holding.commission,
        },
    )
    client.sadd(POSITION_INDEX, holding.symbol)


def remove_position(client: redis.Redis, symbol: str) -> None:
    """Remove one position from Redis, and unindex its symbol.

    Args:
        client: A Redis client, or a pipeline.
        symbol: The symbol of the position to remove.
    """
    client.delete(f"position:{symbol}")
    client.srem(POSITION_INDEX, symbol)


def write_open_order(client: redis.Redis, trade: Trade) -> None:
    """Write one open order to Redis, and index its permId.

    A market order has no order price, so the price is written as 0.0.

    Args:
        client: A Redis client, or a pipeline.
        trade: The Trade to write.
    """
    order = trade.order
    order_status = trade.orderStatus

    if not order.permId:
        log.warning(
            f"Skipped order write, permId not assigned yet. orderRef={order.orderRef}"
        )
        return

    lmtPrice = order.lmtPrice if order.lmtPrice != UNSET_DOUBLE else 0.0

    client.hset(
        f"order:open:{order.permId}",
        mapping={
            "orderId": order.orderId,
            "orderRef": order.orderRef,
            "permId": order.permId,
            "action": order.action,
            "orderType": order.orderType,
            "lmtPrice": f"{lmtPrice:.4f}",
            "symbol": trade.contract.symbol,
            "status": order_status.status,
            "filled": order_status.filled,
            "remaining": order_status.remaining,
        },
    )
    client.sadd(OPEN_ORDER_INDEX, order.permId)


def remove_open_order(client: redis.Redis, permId: int) -> None:
    """Remove one open order from Redis, and unindex its permId.

    Args:
        client: A Redis client, or a pipeline.
        permId: The permId of the order to remove.
    """
    client.delete(f"order:open:{permId}")
    client.srem(OPEN_ORDER_INDEX, permId)


def write_cash(client: redis.Redis, cash: str) -> None:
    """Write the account cash to Redis.

    cash comes from ib.accountValues(), tag "TotalCashValue", currency "USD".

    Args:
        client: A Redis client, or a pipeline.
        cash: The cash value, as IB gives it.
    """
    client.set("account:cash", cash)


def write_fill(client: redis.Redis, holding: Holding, trade: Trade) -> None:
    """Write one fill to Redis, as a single transaction.

    The position and the open order are updated together. A finished order is
    removed instead of updated. Cash is not written here, because IB owns it.

    Args:
        client: A Redis client.
        holding: The position after the fill, from `apply_fill`.
        trade: The Trade that produced the fill.
    """
    with client.pipeline() as pipe:
        write_position(pipe, holding)

        if trade.isDone():
            remove_open_order(pipe, trade.order.permId)
        else:
            write_open_order(pipe, trade)

        pipe.execute()


def write_last_sync(client: redis.Redis) -> None:
    """Stamp the time when the engine last matched IB."""
    client.set("state:last_sync", datetime.now(timezone.utc).isoformat())


def snapshot(client: redis.Redis) -> Snapshot:
    """
    Provide snapshot on positions, open orders, and cash.
    """
    positions = {}
    symbols = sorted(client.smembers(POSITION_INDEX))
    open_orders = {}
    perm_ids = sorted(client.smembers(OPEN_ORDER_INDEX))

    with client.pipeline() as pipe:
        for symbol in symbols:
            pipe.hgetall(f"position:{symbol}")
        for perm_id in perm_ids:
            pipe.hgetall(f"order:open:{perm_id}")
        pipe.get("account:cash")
        pipe.get("state:last_sync")
        rows = pipe.execute()

    n = len(symbols)
    symbol_rows = rows[:n]
    perm_id_rows = rows[n : n + len(perm_ids)]
    cash = rows[-2]
    last_sync = rows[-1]

    for symbol, row in zip(symbols, symbol_rows):
        if not row:
            continue
        positions[symbol] = Holding.from_hash(row)

    for perm_id, row in zip(perm_ids, perm_id_rows):
        if not row:
            continue
        open_orders[int(perm_id)] = OpenOrder.from_hash(row)

    return Snapshot(positions, open_orders, cash, last_sync)


def write_order_status(client: redis.Redis, trade: Trade) -> None:
    """Write one order-status change to Redis, as a single transaction.

    A live order is written. A finished order is removed. This is the only
    path that maintains `orders:open` for an order that never fills, such as
    a cancelled one.

    Args:
        client: A Redis client.
        trade: The Trade whose status changed.
    """
    perm_id = trade.order.permId
    if not perm_id:
        log.warning(
            f"Skipped order write, permId not assigned yet. orderRef={trade.order.orderRef}"
        )
        return

    with client.pipeline() as pipe:
        if trade.isDone():
            remove_open_order(pipe, perm_id)
        else:
            write_open_order(pipe, trade)

        pipe.execute()


def write_commission(client: redis.Redis, holding: Holding) -> None:
    """Write one commission change to Redis, as a single transaction.

    A commission report changes the position hash alone. The open order does
    not change, so this is not `write_fill`.

    Args:
        client: A Redis client.
        holding: The position after the commission was added.
    """
    with client.pipeline() as pipe:
        write_position(pipe, holding)
        pipe.execute()


def read_ib_positions(ib: IB) -> dict[str, Holding]:
    """Read positions from IB on resync.

    `connectAsync` already ran `reqPositions`, so this reads the list that
    ib_async keeps. IB drops a position at quantity 0, so a symbol that went
    flat is absent from the result.

    Args:
        ib: A connected IB instance.

    Returns:
        A dict of symbol to Holding, for every position in the account.
    """
    holdings: dict[str, Holding] = {}
    for position in ib.positions():
        holding = Holding.from_ib_position(position)
        if holding.symbol in holdings:
            log.warning(
                f"Two IB positions share the symbol {holding.symbol}. "
                f"The schema holds one key per symbol, so the later one wins."
            )
        holdings[holding.symbol] = holding
    return holdings


def read_ib_commissions(ib: IB) -> dict[str, float]:
    """Sum today's commission per symbol from the executions of IB.

    `connectAsync` already ran `reqExecutions`, and ib_async writes every
    commission report into its Fill. So this reads the cache and makes no
    request. `reqExecutions` returns today's executions, so the total means
    "commission today".

    Args:
        ib: A connected IB instance.

    Returns:
        A dict of symbol to the commission summed over today.
    """
    commissions: dict[str, float] = {}
    for fill in ib.fills():
        symbol = fill.contract.symbol
        commissions[symbol] = (
            commissions.get(symbol, 0.0) + fill.commissionReport.commission
        )
    return commissions


def apply_commissions(
    positions: dict[str, Holding], commissions: dict[str, float]
) -> dict[str, Holding]:
    """Fold commission into the positions that IB reports.

    Args:
        positions: The positions from `read_ib_positions`.
        commissions: The totals from `read_ib_commissions`.

    Returns:
        A new dict of symbol to Holding, with the commission set.
    """
    holdings = dict(positions)
    for symbol, commission in commissions.items():
        holding = holdings.get(symbol) or Holding(symbol=symbol, qty=0.0, avg_cost=0.0)
        holdings[symbol] = replace(holding, commission=commission)
    return holdings


def read_ib_account_cash(ib: IB) -> str | None:
    """Read account cash (TotalCashValue) from IB on resync

    Args:
        ib: A connected IB instance.

    Returns:
        Account TotalCashValue or None.
    """
    for val in ib.accountValues():
        if val.tag == "TotalCashValue" and val.currency == BASE_CURRENCY:
            return val.value
    log.warning(f"No account cash is read from IB in {BASE_CURRENCY}.")
    return None


def read_ib_open_trades(ib: IB) -> dict[int, Trade]:
    """Read open trades from IB on resync.

    Args:
        ib: A connected IB instance.

    Returns:
        Dictionary with key as permId and value as the trade.

    """
    open_trades: dict[int, Trade] = {}

    for trade in ib.openTrades():
        if trade.order.permId == 0:
            continue
        open_trades[trade.order.permId] = trade

    return open_trades


def resync_from_ib(ib: IB, client: redis.Redis) -> None:
    """Resync Redis from IB.

    IB is the source of truth. Commission is not in the position feed, so it
    comes from the execution feed of IB.

    Args:
        ib: A connected IB instance.
        client: A Redis client.
    """
    positions = apply_commissions(read_ib_positions(ib), read_ib_commissions(ib))
    cash = read_ib_account_cash(ib)
    open_trades = read_ib_open_trades(ib)

    stale_symbols = client.smembers(POSITION_INDEX).difference(positions.keys())
    redis_perm_ids = {int(perm_id) for perm_id in client.smembers(OPEN_ORDER_INDEX)}
    stale_perm_ids = redis_perm_ids.difference(open_trades.keys())

    with client.pipeline() as pipe:
        for holding in positions.values():
            write_position(pipe, holding)
        for symbol in stale_symbols:
            remove_position(pipe, symbol)

        for trade in open_trades.values():
            write_open_order(pipe, trade)
        for perm_id in stale_perm_ids:
            remove_open_order(pipe, perm_id)

        if cash is not None:
            write_cash(pipe, cash)

        write_last_sync(pipe)
        pipe.execute()

    log.info(
        f"Resync done. positions written={len(positions)} removed={len(stale_symbols)} "
        f"| open orders written={len(open_trades)} removed={len(stale_perm_ids)} "
        f"| cash={cash}"
    )
