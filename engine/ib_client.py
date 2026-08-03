import logging
import math

from ib_async import IB, Contract, Stock, Ticker

from config import IB_CLIENT_ID, IB_HOST, IB_PORT, WATCHLIST

DATA_FARM_STATUS_CODES = {2104, 2106, 2107, 2158, 10167}
CONNECTIVITY_CODES = {1100, 1101, 1102}

log = logging.getLogger(__name__)


def on_error(
    reqId: int, errorCode: int, errorString: str, contract: Contract | None
) -> None:
    """
    Error event handler.
    """
    symbol = contract.symbol if contract else "-"
    msg = f"symbol: {symbol}, errorCode: {errorCode}, errorString: {errorString}, reqId: {reqId}"
    if errorCode in DATA_FARM_STATUS_CODES:
        log.debug(msg)
    elif errorCode in CONNECTIVITY_CODES:
        log.warning(msg)
    else:
        log.error(msg)


def on_connect() -> None:
    """
    Connection event handler.
    """
    log.info("Connection established.")


def on_disconnect() -> None:
    """
    Disconnection event handler.
    """
    log.info("Disconnected.")


def on_pending(tickers: set[Ticker]) -> None:
    """
    Pending ticker event handler.
    """
    for ticker in tickers:
        if (not math.isnan(ticker.last)) or (not math.isnan(ticker.close)):
            log.info(
                f"{ticker.contract.symbol} - Last: {ticker.last}; Close: {ticker.close}; Time: {ticker.time}"
            )


async def connect(client_id: int = IB_CLIENT_ID) -> IB:
    ib = IB()
    ib.connectedEvent += on_connect
    ib.disconnectedEvent += on_disconnect
    ib.errorEvent += on_error
    await ib.connectAsync(IB_HOST, IB_PORT, client_id)
    return ib


def build_contracts(symbols: list[str] = WATCHLIST) -> list[Stock]:
    return [
        Stock(symbol=symbol, exchange="SMART", currency="USD") for symbol in symbols
    ]


async def qualify_contracts(ib: IB, contracts: list[Stock]) -> list[Stock]:
    res = await ib.qualifyContractsAsync(*contracts)
    qualified_contracts = []
    for contract, qualified in zip(contracts, res):
        if qualified is None:
            log.warning(f"{contract.symbol} not qualified.")
        else:
            qualified_contracts.append(qualified)

    return qualified_contracts
