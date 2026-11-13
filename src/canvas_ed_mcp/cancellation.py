"""Operation-scoped cancellation shared by sync, local I/O, and HTTP waits."""

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar

_callback: ContextVar[Callable[[], bool] | None] = ContextVar(
    "sync_cancellation", default=None
)


class OperationCancelled(Exception):
    """Cooperative cancellation; never an item failure or retry."""


def check_cancelled() -> None:
    callback = _callback.get()
    if callback is not None:
        try:
            cancelled = bool(callback())
        except Exception:
            cancelled = True
        if cancelled:
            raise OperationCancelled("operation_cancelled")


@contextmanager
def cancellation_scope(callback: Callable[[], bool] | None):
    token = _callback.set(callback)
    try:
        yield
    finally:
        _callback.reset(token)
