import argparse
import asyncio
import logging
import sys

from ib_async import Trade

from config import IB_SCRIPT_CLIENT_ID
from engine.ib_client import build_contracts, connect, qualify_contracts
from engine.logging_config import setup_logging
from engine.orders import (
    cancel_order,
    on_commission_report,
    on_exec_details,
    on_order_status,
    place_order,
)

setup_logging()
log = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Place or cancel order.",
        usage="uv run python -m scripts.submit_order [-h] [symbol] [action] [qty] [--limit limit_price] [--cancel order_id] [--timeout TIMEOUT]",
    )
    parser.add_argument("symbol", nargs="?", type=str)
    parser.add_argument("action", nargs="?", type=str)
    parser.add_argument("qty", nargs="?", type=float)
    parser.add_argument(
        "--limit", type=float, metavar="limit_price", dest="limit_price"
    )
    parser.add_argument("--cancel", type=int, metavar="order_id", dest="order_id")
    parser.add_argument("--timeout", type=float, default=30)

    args = parser.parse_args()

    if args.order_id is not None:
        if args.symbol is not None or args.action is not None or args.qty is not None:
            parser.error("symbol/action/qty are not accepted with --cancel.")
        if args.limit_price is not None:
            parser.error(
                "Cancelling at a limit price is meaningless. Remove the limit price argument."
            )
    else:
        if args.symbol is None or args.action is None or args.qty is None:
            parser.error(
                "symbol, action, and qty are required unless --cancel is given."
            )

        args.symbol = args.symbol.upper()

    return args


async def wait_for_terminal(trade: Trade, timeout: float) -> bool:
    done = asyncio.Event()

    def on_status(trade: Trade) -> None:
        if trade.isDone():
            done.set()

    trade.statusEvent += on_status
    if trade.isDone():
        done.set()

    try:
        await asyncio.wait_for(done.wait(), timeout)
    except asyncio.TimeoutError:
        log.warning(
            f"[{trade.order.orderRef}][{trade.order.orderId}] Order still working: symbol: {trade.contract.symbol}, status: {trade.orderStatus.status}, filled: {trade.orderStatus.filled}, remaining: {trade.orderStatus.remaining}, avgFillPrice: {trade.orderStatus.avgFillPrice}."
        )
        return False
    log.info(
        f"<TERMINAL> [{trade.order.orderRef}][{trade.order.orderId}] Order finished: symbol: {trade.contract.symbol}, status: {trade.orderStatus.status}, filled: {trade.orderStatus.filled}, remaining: {trade.orderStatus.remaining}, avgFillPrice: {trade.orderStatus.avgFillPrice}."
    )
    return True


async def main(args: argparse.Namespace) -> int:
    try:
        ib = await connect(IB_SCRIPT_CLIENT_ID)
    except (ConnectionError, asyncio.TimeoutError) as e:
        log.error(f"Couldn't establish connection. {e}")
        return 1

    try:
        ib.orderStatusEvent += on_order_status
        ib.execDetailsEvent += on_exec_details
        ib.commissionReportEvent += on_commission_report

        if args.order_id is not None:
            trade = cancel_order(ib, args.order_id)

            if not trade:
                return 1
        else:
            contracts = build_contracts([args.symbol])
            qualified = await qualify_contracts(ib, contracts)

            if not qualified:
                log.error(f"{args.symbol} could not be qualified, aborting.")
                return 1

            contract = qualified[0]
            try:
                trade = place_order(
                    ib, contract, args.action, args.qty, args.limit_price
                )
            except ValueError as e:
                log.error(f"Order rejected: {e}")
                return 1

        reached_terminal = await wait_for_terminal(trade, args.timeout)
        if reached_terminal:
            await asyncio.sleep(1)

    finally:
        ib.disconnect()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main(parse_args())))
    except KeyboardInterrupt:
        sys.exit(130)
