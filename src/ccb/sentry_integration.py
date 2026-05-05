"""Sentry error tracking integration for ccb-py.

Wraps sentry_sdk with graceful fallback when the package is absent.
Provides exception capture, breadcrumbs, user context, and tool-execution
span wrapping.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Generator

log = logging.getLogger(__name__)

_HAS_SENTRY = False
try:
    import sentry_sdk
    from sentry_sdk import capture_exception as _sdk_capture_exception
    from sentry_sdk import capture_message as _sdk_capture_message
    from sentry_sdk import configure_scope as _sdk_configure_scope
    from sentry_sdk import add_breadcrumb as _sdk_add_breadcrumb
    from sentry_sdk import start_span as _sdk_start_span
    _HAS_SENTRY = True
except ImportError:
    sentry_sdk = None  # type: ignore[assignment]


_initialized = False


def init_sentry(
    dsn: str = "",
    environment: str = "production",
    release: str = "",
    traces_sample_rate: float = 0.1,
) -> bool:
    """Initialize Sentry error tracking.

    Returns True if sentry_sdk is available and init succeeded.
    Looks for ``SENTRY_DSN`` env var if *dsn* is empty.
    """
    global _initialized
    if not _HAS_SENTRY:
        log.debug("sentry_sdk not installed — Sentry integration disabled")
        return False

    dsn = dsn or os.environ.get("SENTRY_DSN", "")
    if not dsn:
        log.debug("No Sentry DSN provided — skipping init")
        return False

    release = release or os.environ.get("SENTRY_RELEASE", "ccb-py@dev")
    environment = environment or os.environ.get("SENTRY_ENVIRONMENT", "production")

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=release,
            traces_sample_rate=traces_sample_rate,
            before_send=_before_send,
        )
        _initialized = True
        log.info("Sentry initialized (env=%s, release=%s)", environment, release)
        return True
    except Exception as exc:
        log.warning("Sentry init failed: %s", exc)
        return False


def is_initialized() -> bool:
    """Return whether Sentry has been successfully initialized."""
    return _initialized and _HAS_SENTRY


def capture_exception(
    exc: BaseException,
    context: dict[str, Any] | None = None,
) -> str | None:
    """Capture an exception with optional extra context.

    Returns the Sentry event_id if sent, else None.
    """
    if not is_initialized():
        return None
    try:
        with _sdk_configure_scope() as scope:
            if context:
                for key, value in context.items():
                    scope.set_extra(key, value)
        return _sdk_capture_exception(exc)
    except Exception as e:
        log.debug("Sentry capture_exception failed: %s", e)
        return None


def capture_message(msg: str, level: str = "info") -> str | None:
    """Capture a message at the given severity level.

    Returns the Sentry event_id if sent, else None.
    """
    if not is_initialized():
        return None
    try:
        return _sdk_capture_message(msg, level=level)
    except Exception as e:
        log.debug("Sentry capture_message failed: %s", e)
        return None


def set_user(user_id: str, username: str = "") -> None:
    """Set the current user context for Sentry events."""
    if not is_initialized():
        return
    try:
        with _sdk_configure_scope() as scope:
            scope.user = {"id": user_id, "username": username or user_id}
    except Exception:
        pass


def add_breadcrumb(
    category: str,
    message: str,
    data: dict[str, Any] | None = None,
    level: str = "info",
) -> None:
    """Add a breadcrumb for the current scope.

    Breadcrumbs are included in subsequent exception reports to provide
    context about what happened before the error.
    """
    if not is_initialized():
        return
    try:
        _sdk_add_breadcrumb(
            category=category,
            message=message,
            data=data,
            level=level,
            timestamp=time.time(),
        )
    except Exception:
        pass


@contextmanager
def tool_span(tool_name: str, input_data: dict[str, Any] | None = None) -> Generator[None, None, None]:
    """Context manager that wraps tool execution in a Sentry span.

    Usage::

        with sentry_tool_span("Read", {"file_path": "/tmp/x.py"}):
            result = await tool.execute(input, cwd)
    """
    if not is_initialized():
        yield
        return
    try:
        with _sdk_start_span(op="tool", description=tool_name) as span:
            if input_data:
                for key, val in list(input_data.items())[:10]:
                    span.set_data(key, str(val)[:500])
            yield
    except Exception:
        # If span setup fails, still allow execution
        yield


def wrap_tool_call(tool_name: str) -> Any:
    """Decorator version of tool_span for async tool functions."""
    def decorator(func: Any) -> Any:
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            with tool_span(tool_name, kwargs.get("input_data")):
                return await func(*args, **kwargs)
        return wrapper
    return decorator


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Filter out noisy exceptions before they reach Sentry."""
    exc_info = hint.get("exc_info")
    if exc_info and issubclass(exc_info[0], KeyboardInterrupt):
        return None
    return event
