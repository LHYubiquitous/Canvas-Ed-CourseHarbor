"""Cancellation must interrupt stalled I/O, close streams, and leave no worker."""

import asyncio
from threading import Event, Timer
import time
import unittest
import httpx
from canvas_ed_mcp.cancellation import cancellation_scope, OperationCancelled
from canvas_ed_mcp.cancellable_http import CancellableTransport


class CancellableHttpTests(unittest.TestCase):
    def test_cancel_stalled_headers_closes_task_and_transport(self):
        cancelled, started, finished = Event(), Event(), Event()

        async def respond(request):
            started.set()
            try:
                await asyncio.sleep(30)
            finally:
                finished.set()

        transport = CancellableTransport(transport=httpx.MockTransport(respond))
        timer = Timer(0.15, cancelled.set)
        before = time.monotonic()
        try:
            with httpx.Client(transport=transport) as client:
                timer.start()
                with (
                    cancellation_scope(cancelled.is_set),
                    self.assertRaises(OperationCancelled),
                ):
                    client.get("https://example.invalid/stall")
                self.assertTrue(finished.wait(1))
        finally:
            timer.cancel()
        self.assertTrue(started.is_set())
        self.assertLess(time.monotonic() - before, 2)
        self.assertFalse(transport._thread.is_alive())

    def test_cancel_stalled_body_closes_stream_and_client_remains_usable(self):
        cancelled, closed = Event(), Event()

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"first"
                await asyncio.sleep(30)

            async def aclose(self):
                closed.set()

        async def respond(request):
            return httpx.Response(200, stream=Stream())

        transport = CancellableTransport(transport=httpx.MockTransport(respond))
        with httpx.Client(transport=transport) as client:
            with cancellation_scope(cancelled.is_set):
                with client.stream("GET", "https://example.invalid/body") as response:
                    iterator = response.iter_raw()
                    self.assertEqual(b"first", next(iterator))
                    timer = Timer(0.15, cancelled.set)
                    timer.start()
                    before = time.monotonic()
                    try:
                        with self.assertRaises(OperationCancelled):
                            next(iterator)
                    finally:
                        timer.cancel()
                    self.assertLess(time.monotonic() - before, 2)
            self.assertTrue(closed.is_set())
            cancelled.clear()
            with client.stream("GET", "https://example.invalid/again") as response:
                self.assertEqual(b"first", next(response.iter_raw()))
        self.assertFalse(transport._thread.is_alive())

    def test_normal_body_and_redirect_still_use_sync_client_policy(self):
        seen = []

        async def respond(request):
            seen.append((request.url.path, request.headers.get("authorization")))
            if request.url.path == "/start":
                return httpx.Response(
                    302, headers={"location": "https://other.invalid/end"}
                )
            return httpx.Response(200, content=b"complete")

        with httpx.Client(
            transport=CancellableTransport(transport=httpx.MockTransport(respond))
        ) as client:
            result = client.get(
                "https://example.invalid/start",
                headers={"authorization": "Bearer synthetic-token"},
                follow_redirects=True,
            )
        self.assertEqual(b"complete", result.content)
        self.assertEqual([("/start", "Bearer synthetic-token"), ("/end", None)], seen)

    def test_actual_socket_wait_is_cancelled_before_network_timeout(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread

        for send_headers in (False, True):
            with self.subTest(send_headers=send_headers):
                cancelled, entered, disconnected = Event(), Event(), Event()

                class Handler(BaseHTTPRequestHandler):
                    def log_message(self, *args):
                        pass

                    def do_GET(self):
                        if send_headers:
                            self.send_response(200)
                            self.send_header("Content-Length", "99999")
                            self.end_headers()
                        entered.set()
                        self.connection.settimeout(3)
                        try:
                            if self.connection.recv(1) == b"":
                                disconnected.set()
                        except OSError:
                            pass

                server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                server.daemon_threads = True
                thread = Thread(target=server.serve_forever, daemon=True)
                thread.start()
                transport = CancellableTransport()

                def cancel_after_request():
                    if entered.wait(3):
                        cancelled.set()

                cancel_thread = Thread(target=cancel_after_request)
                cancel_thread.start()
                before = time.monotonic()
                try:
                    with httpx.Client(transport=transport, timeout=30) as client:
                        with (
                            cancellation_scope(cancelled.is_set),
                            self.assertRaises(OperationCancelled),
                        ):
                            client.get(
                                "http://127.0.0.1:" + str(server.server_port) + "/stall"
                            )
                    self.assertLess(time.monotonic() - before, 2)
                    self.assertTrue(disconnected.wait(1))
                    self.assertFalse(transport._thread.is_alive())
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()
                    cancel_thread.join()
