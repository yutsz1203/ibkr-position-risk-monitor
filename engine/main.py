import asyncio
import logging

import redis

from config import WATCHLIST

from .ib_client import build_contracts, connect, on_pending, qualify_contracts
from .logging_config import setup_logging
from .orders import (
    on_commission_report,
    on_exec_details,
    on_order_status,
    seed_from_ib,
)
from .state import check_redis, redis_client, resync_from_ib

setup_logging()
log = logging.getLogger(__name__)


async def main():
    log.info("Started")

    if not check_redis(redis_client):
        log.error("Redis is unreachable. The engine runs and every state write fails.")

    ib = await connect()
    try:
        resync_from_ib(ib, redis_client)
    except redis.RedisError:
        log.error("Could not resync to Redis. IB remains the source of truth.")

    seed_from_ib(ib)
    ib.pendingTickersEvent += on_pending

    ib.orderStatusEvent += on_order_status
    ib.execDetailsEvent += on_exec_details
    ib.commissionReportEvent += on_commission_report
    log.info("Order handlers attached. This process owns the Redis state.")

    ib.reqMarketDataType(3)  # set to delayed quote
    log.info("Set to delayed quote.")

    contracts = build_contracts(WATCHLIST)

    qualified_contracts = await qualify_contracts(ib, contracts)

    log.info(f"Qualified symbols: {[qual.symbol for qual in qualified_contracts]}")

    tickers = [ib.reqMktData(con) for con in qualified_contracts]
    log.info(f"Subscribed to {len(tickers)} contracts. Waiting for ticks...")

    try:
        await asyncio.Event().wait()
    finally:
        for con in qualified_contracts:
            ib.cancelMktData(con)
        ib.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Interrupted by user.")
