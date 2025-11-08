from __future__ import annotations

"""Runtime helpers for coordinating configuration refreshes after setting changes."""

import asyncio
import inspect
import logging
from typing import Awaitable, Callable, Dict, Tuple

_log = logging.getLogger(__name__)

# Registered callbacks invoked when a refresh is requested. These should be
# idempotent and safe to call repeatedly – they typically reread configuration
# from persistent storage and rebuild lightweight caches or client instances.
_REFRESH_HANDLERS: Dict[str, Tuple[int, int, Callable[[], Awaitable[None] | None]]] = {}
_handler_counter = 0

_refresh_task: asyncio.Task[None] | None = None
_refresh_pending = False
_refresh_running = False
_refresh_again = False


def register_backend_refresh_handler(
    name: str, callback: Callable[[], Awaitable[None] | None], *, priority: int = 0
) -> None:
    """Register a named callback that should run when configuration changes.

    Later registrations using the same name replace the previous handler – this
    lets modules update their refresh logic without accumulating duplicates.
    """

    if not name:
        raise ValueError("refresh handler name is required")
    if not callable(callback):
        raise TypeError("refresh handler must be callable")
    global _handler_counter

    _handler_counter += 1
    _REFRESH_HANDLERS[name] = (priority, _handler_counter, callback)


async def _execute_refresh(delay: float) -> None:
    global _refresh_pending, _refresh_running, _refresh_task, _refresh_again

    try:
        if delay > 0:
            await asyncio.sleep(delay)
        if not _REFRESH_HANDLERS:
            _log.info("backend refresh requested but no handlers registered")
            return

        _log.info(
            "refreshing backend runtime state via %d handler(s)",
            len(_REFRESH_HANDLERS),
        )
        ordered_handlers = sorted(
            _REFRESH_HANDLERS.items(), key=lambda item: (item[1][0], item[1][1])
        )
        for name, (_, _, handler) in ordered_handlers:
            try:
                result = handler()
                if inspect.isawaitable(result):
                    await result
            except Exception:  # pragma: no cover - defensive logging
                _log.exception("backend refresh handler %s failed", name)
        _log.info("backend refresh complete")
    finally:
        _refresh_running = False
        _refresh_task = None
        if _refresh_again:
            _log.debug("queuing another backend refresh run after in-flight execution")
            _refresh_again = False
            schedule_backend_restart(delay)


def _start_refresh_task(loop: asyncio.AbstractEventLoop, delay: float) -> None:
    global _refresh_task, _refresh_running

    _refresh_running = True
    _refresh_task = loop.create_task(_execute_refresh(max(0.0, delay)))


def schedule_backend_restart(delay: float = 0.5) -> None:  # noqa: ARG001 - keep signature
    """Schedule a configuration refresh for the running process.

    Historical callers expect this helper to trigger a full process restart.
    We now treat it as a request to rerun lightweight refresh hooks so updated
    settings take effect without killing the server.
    """

    global _refresh_pending, _refresh_running, _refresh_again

    if _refresh_running:
        _refresh_again = True
        return
    if _refresh_pending:
        return

    _refresh_pending = True

    try:
        loop = asyncio.get_running_loop()
        _refresh_pending = False
        _start_refresh_task(loop, delay)
    except RuntimeError:
        # No running loop – execute synchronously for CLI/test contexts.
        _refresh_pending = False
        _refresh_running = True
        asyncio.run(_execute_refresh(max(0.0, delay)))