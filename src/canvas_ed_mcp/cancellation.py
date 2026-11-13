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


_progress_callback: ContextVar[Callable[[str], None] | None] = ContextVar("sync_progress", default=None)

@contextmanager
def progress_scope(callback: Callable[[str], None] | None):
    token = _progress_callback.set(callback)
    try:
        yield
    finally:
        _progress_callback.reset(token)

def report_progress(stage: str) -> None:
    callback = _progress_callback.get()
    if callback is not None:
        try:
            callback(stage)
        except Exception:
            pass
