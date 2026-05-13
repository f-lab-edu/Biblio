import sys

from loguru import logger


def configure_logging(*, level: str = "INFO") -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        serialize=False,
        backtrace=False,
        diagnose=False,
    )
