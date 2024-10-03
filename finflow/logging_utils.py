"""Structured JSON logging with per-run context.

Every log record is emitted as a single JSON object per line, so pipeline runs
can be grepped, shipped to a log store, or loaded back into Pandas for analysis.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_run_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "finflow_run_context", default={}
)

_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Render log records as JSON, merging run context and ``extra`` fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(_run_context.get())
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """Configure the ``finflow`` logger hierarchy to emit JSON lines."""
    logger = logging.getLogger("finflow")
    logger.setLevel(level.upper())
    logger.handlers.clear()
    logger.propagate = False

    formatter = JsonFormatter()
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    if not name.startswith("finflow"):
        name = f"finflow.{name}"
    return logging.getLogger(name)


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]


def current_run_id() -> str | None:
    return _run_context.get().get("run_id")


@contextmanager
def run_context(**fields: Any) -> Iterator[dict[str, Any]]:
    """Bind fields (e.g. ``run_id``, ``step``) to every log record in this block."""
    merged = {**_run_context.get(), **fields}
    token = _run_context.set(merged)
    try:
        yield merged
    finally:
        _run_context.reset(token)


@contextmanager
def log_step(logger: logging.Logger, step: str, **fields: Any) -> Iterator[dict[str, Any]]:
    """Log start/finish/failure of a pipeline step with its duration.

    The yielded dict can be mutated to attach result metrics to the finish event.
    """
    metrics: dict[str, Any] = {}
    with run_context(step=step):
        logger.info("step_started", extra=fields)
        start = time.perf_counter()
        try:
            yield metrics
        except Exception:
            duration = round(time.perf_counter() - start, 4)
            logger.exception("step_failed", extra={**fields, "duration_s": duration})
            raise
        duration = round(time.perf_counter() - start, 4)
        logger.info("step_finished", extra={**fields, **metrics, "duration_s": duration})
