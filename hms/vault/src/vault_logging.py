#!/usr/bin/env python3

import json
import logging
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Custom formatter that outputs structured JSON logs."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON with ts, level, and message fields."""
        timestamp = (
            datetime.now(timezone.utc)
            .replace(tzinfo=None)
            .isoformat(timespec="milliseconds")
        )

        payload = {
            "ts": timestamp,
            "level": record.levelname,
            "message": record.getMessage(),
        }
        return json.dumps(payload, separators=(",", ":"))


def setup_json_logging() -> logging.Logger:
    """
    Configure logging to output structured JSON.

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger("converter")
    logger.setLevel(logging.INFO)

    # Remove any existing handlers
    logger.handlers.clear()

    # Create handlers for stdout (INFO) and stderr (ERROR)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.addFilter(lambda record: record.levelno < logging.ERROR)
    stdout_handler.setFormatter(JSONFormatter())

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.ERROR)
    stderr_handler.setFormatter(JSONFormatter())

    logger.addHandler(stdout_handler)
    logger.addHandler(stderr_handler)

    # Prevent propagation to root logger
    logger.propagate = False

    return logger
