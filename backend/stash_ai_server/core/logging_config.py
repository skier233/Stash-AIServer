from __future__ import annotations

import logging
from typing import Iterable

_KEEPALIVE_SNIPPETS: tuple[str, ...] = (
    "% sending keepalive ping",
    "% received keepalive pong",
    "> PING",
    "< PONG",
    "> TEXT",
    "< TEXT",
    "task.progress",
)

_NOISY_LOGGERS: tuple[str, ...] = (
    "websockets",
    "websockets.client",
    "websockets.server",
    "websockets.protocol",
    "websockets.connection",
    "websockets.frames",
    "websockets.legacy.protocol",
    "urllib3",
    "urllib3.connectionpool",
    "uvicorn.protocols.websockets.websockets_impl",
)


class _SuppressKeepaliveFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - logging hook
        try:
            message = record.getMessage()
        except Exception:
            return True
        lowered = message.lower()
        for snippet in _KEEPALIVE_SNIPPETS:
            if snippet in message or snippet in lowered:
                return False
        return True


_KEEPALIVE_FILTER = _SuppressKeepaliveFilter()


def _ensure_filter(logger: logging.Logger) -> None:
    for existing in logger.filters:
        if existing is _KEEPALIVE_FILTER:
            return
    logger.addFilter(_KEEPALIVE_FILTER)


def _ensure_stream_handler(logger: logging.Logger) -> None:
    handler_exists = any(isinstance(h, logging.StreamHandler) for h in logger.handlers)
    if handler_exists:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('[%(levelname)s] %(name)s: %(message)s'))
    logger.addHandler(handler)


def configure_logging(level_name: str | None = None) -> None:
    """Configure the root logger and suppress noisy keepalive chatter."""

    try:
        lvl = getattr(logging, (level_name or 'INFO').upper(), logging.INFO)
    except Exception:
        lvl = logging.INFO

    root_logger = logging.getLogger()
    root_logger.setLevel(lvl)
    _ensure_stream_handler(root_logger)
    _ensure_filter(root_logger)

    for noisy_name in _NOISY_LOGGERS:
        logger = logging.getLogger(noisy_name)
        if logger.level < logging.INFO:
            logger.setLevel(logging.INFO)
        logger.propagate = False
        _ensure_filter(logger)

    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        _ensure_filter(logger)
