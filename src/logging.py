"""Logging configuration for prediction market clients."""

import logging
import sys
from typing import Optional


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Get a configured logger instance.

    Args:
        name: Logger name (usually __name__ or module name)
        level: Logging level (default INFO)

    Returns:
        Configured logger instance.
    """
    logger = logging.getLogger(name)

    # Only configure if not already configured
    if not logger.handlers:
        logger.setLevel(level)

        # Console handler with formatting
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(level)

        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        # Prevent propagation to root logger
        logger.propagate = False

    return logger


def set_log_level(name: str, level: int) -> None:
    """Set log level for a specific logger.

    Args:
        name: Logger name
        level: New logging level
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    for handler in logger.handlers:
        handler.setLevel(level)


# Pre-configured loggers for clients
pm_logger = get_logger("pm-client")
pf_logger = get_logger("pf-client")
