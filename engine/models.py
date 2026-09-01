from dataclasses import dataclass

from ib_async import Position


@dataclass(frozen=True)
class Holding:
    """One position, as stored in `position:{symbol}`.

    Field names match the Redis hash keys.

    `avg_cost` is the cost basis per share, commission included.

    `commission` is a separate record of what the fills cost. IB reports
    executions for today only, so a position held from an earlier day comes
    back from a resync with `commission` at 0.0, and its earlier commission
    still inside `avg_cost`.
    """

    symbol: str
    qty: float
    avg_cost: float  # commission included, positive even when short
    commission: float = 0.0

    @classmethod
    def from_hash(cls, row: dict[str, str]) -> "Holding":
        """Build a Holding from one Redis hash.

        Redis returns every value as text, so this is the one place that
        converts.

        Args:
            row: The result of HGETALL on a `position:` key.

        Returns:
            A Holding instance.

        Raises:
            KeyError: A field is absent from the hash.
            ValueError: A numeric field does not parse.
        """
        return cls(
            symbol=row["symbol"],
            qty=float(row["qty"]),
            avg_cost=float(row["avg_cost"]),
            commission=float(row["commission"]),
        )

    @classmethod
    def from_ib_position(cls, position: Position) -> "Holding":
        """Build a Holding from one IB position.

        `avgCost` from IB already includes the commission of the trades that
        opened the position, so the value carries over unchanged.

        Args:
            position: One entry from `ib.positions()`.

        Returns:
            A Holding instance.
        """
        return cls(
            symbol=position.contract.symbol,
            qty=position.position,
            avg_cost=position.avgCost,
            commission=0.0,
        )


@dataclass(frozen=True)
class OpenOrder:
    orderId: int
    orderRef: str
    permId: int
    action: str
    orderType: str
    lmtPrice: float
    symbol: str
    status: str
    filled: float
    remaining: float

    @classmethod
    def from_hash(cls, row: dict[str, str]) -> "OpenOrder":
        """Build an OpenOrder from one Redis hash.

        Redis returns every value as text, so this is the one place that
        converts.

        Args:
            row: The result of HGETALL on an `order:open:` key.

        Returns:
            An OpenOrder instance.

        Raises:
            KeyError: A field is absent from the hash.
            ValueError: A numeric field does not parse.
        """
        return cls(
            orderId=int(row["orderId"]),
            orderRef=row["orderRef"],
            permId=int(row["permId"]),
            action=row["action"],
            orderType=row["orderType"],
            lmtPrice=float(row["lmtPrice"]),
            symbol=row["symbol"],
            status=row["status"],
            filled=float(row["filled"]),
            remaining=float(row["remaining"]),
        )


@dataclass(frozen=True)
class Snapshot:
    positions: dict[str, Holding]
    open_orders: dict[int, OpenOrder]
    cash: str | None
    last_sync: str | None
