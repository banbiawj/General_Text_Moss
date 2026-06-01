from __future__ import annotations

import threading
import webbrowser
import socket
from dataclasses import dataclass
from typing import Any

import uvicorn

from .hub import EventHub
from .server import create_app


@dataclass
class MossRuntime:
    hub: EventHub
    host: str
    port: int
    server: uvicorn.Server | None = None
    thread: threading.Thread | None = None
    started: bool = False

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


_runtime = MossRuntime(hub=EventHub(), host="127.0.0.1", port=8765)
_runtime_lock = threading.Lock()


def _ensure_port_available(host: str, port: int) -> None:
    if port == 0:
        return

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            raise OSError(f"MossLog viewer port is already in use: {host}:{port}") from exc


def mosslog(tag: str, message: object = None, **fields: Any) -> dict[str, Any] | None:
    try:
        return _runtime.hub.publish(tag, message, **fields)
    except Exception:
        return None


def mossview(
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    max_events: int = 1000,
) -> MossRuntime:
    global _runtime

    with _runtime_lock:
        if _runtime.started:
            if open_browser:
                webbrowser.open(_runtime.url)
            return _runtime

        _runtime = MossRuntime(hub=EventHub(max_events=max_events), host=host, port=port)

        if port == 0:
            _runtime.started = True
            return _runtime

        _ensure_port_available(host, port)

        app = create_app(_runtime.hub)
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, name="mosslog-viewer", daemon=True)

        _runtime.server = server
        _runtime.thread = thread
        _runtime.started = True
        thread.start()

    if open_browser:
        webbrowser.open(_runtime.url)

    return _runtime


def _reset_for_tests() -> None:
    global _runtime
    with _runtime_lock:
        if _runtime.server is not None:
            _runtime.server.should_exit = True
        _runtime = MossRuntime(hub=EventHub(), host="127.0.0.1", port=8765)
