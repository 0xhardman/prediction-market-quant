"""Logging configuration and utilities."""

import logging
import sys
from pathlib import Path
from typing import Optional


_logger: Optional[logging.Logger] = None


def setup_logger(
    name: str = "arbitrage",
    level: str = "INFO",
    log_to_file: bool = True,
    log_file: str = "arbitrage.log",
) -> logging.Logger:
    """Setup and configure the logger."""
    global _logger

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Clear existing handlers
    logger.handlers.clear()

    # Create formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler
    if log_to_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    _logger = logger
    return logger


def get_logger() -> logging.Logger:
    """Get the configured logger instance."""
    global _logger
    if _logger is None:
        _logger = setup_logger()
    return _logger


class LogContext:
    """Context manager for logging with additional context."""

    def __init__(self, context: str):
        self.context = context
        self.logger = get_logger()

    def __enter__(self):
        self.logger.info(f"[START] {self.context}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.logger.error(f"[ERROR] {self.context}: {exc_val}")
        else:
            self.logger.info(f"[END] {self.context}")
        return False
