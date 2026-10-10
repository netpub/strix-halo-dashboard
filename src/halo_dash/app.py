from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from halo_dash.collector import Collector
from halo_dash.config import Config


def create_app(config: Config) -> FastAPI:
    collector = Collector(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(collector.run())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            if collector.direct is not None:
                collector.direct.close()

    app = FastAPI(title="Local AI Monitoring", lifespan=lifespan)
    app.state.collector = collector

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "language": config.language}

    @app.get("/api/snapshot")
    def snapshot() -> dict:
        return collector.snapshot

    @app.get("/api/requests")
    def requests() -> dict:
        rows = collector.snapshot.get("requests") or []
        return {"requests": rows}

    @app.get("/api/stream")
    async def stream() -> StreamingResponse:
        async def events():
            last = ""
            ticks = 0
            while True:
                payload = json.dumps(collector.snapshot, ensure_ascii=False)
                if payload != last:
                    yield f"data: {payload}\n\n"
                    last = payload
                elif ticks % 40 == 0:
                    yield ": ping\n\n"
                ticks += 1
                await asyncio.sleep(0.25)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if config.static_dir is not None:
        app.mount("/", StaticFiles(directory=config.static_dir, html=True), name="web")
    else:
        @app.get("/")
        def missing_ui() -> HTMLResponse:
            return HTMLResponse(
                "<p>The UI is not built yet. Run <code>npm run build</code> in the web directory.</p>"
            )
    return app
