from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .hub import EventHub


STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_PATH = STATIC_DIR / "index.html"
FALLBACK_INDEX = "<!doctype html><html><head><title>MossLog</title></head><body>MossLog</body></html>"


def encode_sse(event: dict[str, Any]) -> str:
    return "event: mosslog\n" + f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def create_app(hub: EventHub) -> FastAPI:
    app = FastAPI(title="MossLog")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        if INDEX_PATH.exists():
            return HTMLResponse(INDEX_PATH.read_text(encoding="utf-8"))
        return HTMLResponse(FALLBACK_INDEX)

    @app.get("/snapshot")
    async def snapshot() -> JSONResponse:
        return JSONResponse({"events": hub.snapshot(), "max_events": hub.max_events})

    @app.get("/events")
    async def events(request: Request) -> StreamingResponse:
        queue = hub.subscribe()

        async def stream():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield encode_sse(event)
            finally:
                hub.unsubscribe(queue)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/clear")
    async def clear() -> JSONResponse:
        hub.clear()
        return JSONResponse({"ok": True})

    return app
