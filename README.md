# IB Paper Trading Engine

## Using the Engine
1. Start IB Gateway.
2. Start the engine.
```bash
uv run python -m engine.main
```
3. Order lifecycle

|Command|Result|
|---|---|
|`uv run python -m scripts.submit_order --help`|Shows the usage.|
|`uv run python -m scripts.submit_order RDDT BUY 10`|Sends a market order.|
|`uv run python -m scripts.submit_order RDDT BUY 10 --limit 150`|Sends a limit order at 150.|
|`uv run python -m scripts.submit_order GOOGL SELL 5`|Sends a market sell order.|
|`uv run python -m scripts.submit_order --cancel 123`|Cancels order 123.|
|`uv run python -m scripts.submit_order UNH BUY 10 --timeout 60`|Waits 60 seconds for a terminal status.|