import asyncio
import logging

from config import WATCHLIST

from .ib_client import build_contracts, connect, on_pending, qualify_contracts
from .logging_config import setup_logging

setup_logging()
log = logging.getLogger(__name__)


async def main():
    log.info("Started")
    ib = await connect()
    ib.pendingTickersEvent += on_pending

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
