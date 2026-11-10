"""Run one Uvicorn server inside the bot's event loop without taking its signals."""

import asyncio
from contextlib import contextmanager, suppress

import uvicorn


class _SharedLoopServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        yield


class ApiServer:
    def __init__(self, app, settings):
        config = uvicorn.Config(
            app,
            host=settings.api_host,
            port=settings.api_port,
            access_log=False,
            proxy_headers=False,
            server_header=False,
            lifespan="off",
            ws="none",
            timeout_graceful_shutdown=5,
        )
        self.server = _SharedLoopServer(config)
        self.task = None

    async def _serve(self):
        try:
            await self.server.serve()
        except SystemExit:
            # Uvicorn exits on a bind failure; do not terminate the shared loop.
            raise RuntimeError("The API server could not start") from None

    async def start(self):
        if self.task is not None:
            raise RuntimeError("The API server has already been started")
        self.task = asyncio.create_task(self._serve(), name="monitoring-api")
        try:
            async with asyncio.timeout(10):
                while not self.server.started:
                    if self.task.done():
                        await self.task
                        raise RuntimeError("The API server stopped before startup")
                    await asyncio.sleep(0.01)
        except BaseException:
            await self.close()
            raise

    async def wait(self):
        if self.task is None:
            raise RuntimeError("The API server has not been started")
        await asyncio.shield(self.task)

    async def close(self):
        if self.task is None:
            return
        self.server.should_exit = True
        try:
            async with asyncio.timeout(7):
                await asyncio.shield(self.task)
        except TimeoutError:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        except (Exception, asyncio.CancelledError):
            # wait()/start() is responsible for reporting serving failures.
            pass
