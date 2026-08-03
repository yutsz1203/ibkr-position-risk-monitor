import logging

from rich.logging import RichHandler

from config import LOG_LEVEL

NOISY_LOGGERS = ("ib_async.wrapper", "ib_async.ib")


def setup_logging(level: str = LOG_LEVEL) -> None:
    """Configure root logging. Call once, from an entrypoint only."""
    logging.basicConfig(
        level=level, format="%(message)s", datefmt="[%X]", handlers=[RichHandler()]
    )
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
