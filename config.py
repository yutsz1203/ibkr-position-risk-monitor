import os

from dotenv import load_dotenv

load_dotenv()

IB_HOST = os.getenv("IB_HOST", "127.0.0.1")
IB_PORT = int(os.getenv("IB_PORT", "4002"))
IB_CLIENT_ID = int(os.getenv("IB_CLIENT_ID", "1"))

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

DB_PATH = os.getenv("DB_PATH", "data/risk_monitor.db")

WATCHLIST = ["SPY", "AAPL", "MSFT", "AMZN", "GOOGL"]