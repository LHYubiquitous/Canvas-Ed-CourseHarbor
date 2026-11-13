"""Synchronous HTTPX surface with interruptible async network waits underneath.

Redirects/auth remain controlled by the outer Client and provider policies. The
inner client supplies normal TLS, connection pooling and environment proxy support.
No worker writes course files: cancelled network tasks are joined before returning.
"""

import asyncio
from concurrent.futures import CancelledError, TimeoutError as FutureTimeout
from threading import Event, Lock, Thread
import httpx
from .cancellation import OperationCancelled, check_cancelled


class CancellableTransport(httpx.BaseTransport):
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(transport=transport, follow_redirects=False)
        self._loop = asyncio.new_event_loop()
        self._thread: Thread | None = None
        self._lock = Lock()
        self._closed = False

    def _start(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("transport_closed")
            if self._thread is None:
                ready = Event()

                def run():
                    asyncio.set_event_loop(self._loop)
                    ready.set()
                    self._loop.run_forever()

                self._thread = Thread(target=run, name="courseharbor-http", daemon=True)
                self._thread.start()
                ready.wait()

    def _run(self, coroutine, *, cancellable=True):
        try:
            self._start()
        except BaseException:
            coroutine.close()
            raise
        started, finished = Event(), Event()
        tasks = []

        async def execute():
            tasks.append(asyncio.current_task())
            started.set()
            try:
                return await coroutine
            finally:
                finished.set()

        future = asyncio.run_coroutine_threadsafe(execute(), self._loop)
        started.wait()
        while True:
            try:
                if cancellable:
                    check_cancelled()
                return future.result(timeout=0.05)
            except FutureTimeout:
                if future.done():
                    return future.result()
            except OperationCancelled:
                # Cancel on the event-loop thread. Cancelling the concurrent Future
                # directly could discard a response that just finished, leaking it.
                self._loop.call_soon_threadsafe(tasks[0].cancel)
                finished.wait()
                try:
                    return future.result()
                except CancelledError:
                    raise OperationCancelled("operation_cancelled") from None

    def handle_request(self, request):
        check_cancelled()
        async_request = httpx.Request(
            request.method,
            request.url,
            headers=request.headers,
            content=request.read(),
            extensions=request.extensions,
        )
        response = self._run(self._client.send(async_request, stream=True))
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            stream=_Stream(self, response),
            extensions=response.extensions,
        )

    def close(self):
        if self._closed:
            return
        self._run(self._client.aclose(), cancellable=False)
        with self._lock:
            self._closed = True
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()
        self._loop.close()


class _Stream(httpx.SyncByteStream):
    def __init__(self, transport, response):
        self._transport = transport
        self._response = response

    def __iter__(self):
        if self._response.is_stream_consumed:
            check_cancelled()
            yield self._response.content
            return
        iterator = self._response.aiter_raw().__aiter__()

        async def next_chunk():
            try:
                return await iterator.__anext__()
            except StopAsyncIteration:
                return None

        while True:
            chunk = self._transport._run(next_chunk())
            if chunk is None:
                break
            yield chunk

    def close(self):
        self._transport._run(self._response.aclose(), cancellable=False)


def cancellable_client(**kwargs) -> httpx.Client:
    return httpx.Client(transport=CancellableTransport(), **kwargs)
