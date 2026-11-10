import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from deye_notifier.api.server import ApiServer


class ApiServerTests(unittest.IsolatedAsyncioTestCase):
    def make_server(self, server_type):
        with patch("deye_notifier.api.server._SharedLoopServer", server_type):
            return ApiServer(None, SimpleNamespace(api_host="127.0.0.1", api_port=8000))

    async def test_waiter_cancellation_does_not_cancel_server_and_close_drains(self):
        class FakeServer:
            def __init__(self, config):
                self.started = False
                self.should_exit = False

            async def serve(self):
                self.started = True
                while not self.should_exit:
                    await asyncio.sleep(0)

        server = self.make_server(FakeServer)
        await server.start()
        waiter = asyncio.create_task(server.wait())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertFalse(server.task.done())
        await server.close()
        self.assertTrue(server.task.done())

    async def test_bind_failure_surfaces_without_system_exit(self):
        class FailedServer:
            def __init__(self, config):
                self.started = False

            async def serve(self):
                raise SystemExit(1)

        server = self.make_server(FailedServer)
        with self.assertRaisesRegex(RuntimeError, "could not start"):
            await server.start()
