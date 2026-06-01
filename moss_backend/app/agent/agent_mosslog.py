from __future__ import annotations

import os
from typing import Any


TRUE_VALUES = {"1", "true", "yes", "on"}

_enabled = False


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in TRUE_VALUES


def _compact_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in fields.items() if value is not None}


def mosslog(tag: str, message: object = None, **fields: Any) -> dict[str, Any] | None:
    from app.mosslog import mosslog as emit

    return emit(tag, message, **fields)


def mossview(**kwargs: Any) -> Any:
    from app.mosslog import mossview as start_viewer

    return start_viewer(**kwargs)


def enable_agent_mosslog() -> None:
    global _enabled
    _enabled = True


def disable_agent_mosslog() -> None:
    global _enabled
    _enabled = False


def start_agent_mosslog(
    *,
    enabled: bool | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
    max_events: int = 1000,
) -> Any | None:
    should_enable = _env_enabled("MOSSLOG") if enabled is None else enabled
    if not should_enable:
        disable_agent_mosslog()
        return None

    runtime = mossview(host=host, port=port, open_browser=open_browser, max_events=max_events)
    enable_agent_mosslog()
    return runtime


def _emit(tag: str, message: str, **fields: Any) -> dict[str, Any] | None:
    if not _enabled:
        return None
    return mosslog(tag, message, **_compact_fields(fields))


def log_user_input(text: Any, **context: Any) -> dict[str, Any] | None:
    return _emit("input", "user input", input=text, **context)


def log_llm_request(model: str, prompt: Any, **context: Any) -> dict[str, Any] | None:
    return _emit("llm", "llm request", model=model, prompt=prompt, **context)


def log_llm_response(
    model: str,
    response: Any,
    *,
    tool_call: Any = None,
    duration_ms: int | None = None,
    usage: Any = None,
    **context: Any,
) -> dict[str, Any] | None:
    return _emit(
        "llm",
        "llm response",
        model=model,
        response=response,
        tool_call=tool_call,
        duration_ms=duration_ms,
        usage=usage,
        **context,
    )


def log_tool_call(tool: str, arguments: Any, **context: Any) -> dict[str, Any] | None:
    return _emit("tool", "tool call", tool=tool, input=arguments, **context)


def log_tool_result(
    tool: str,
    result: Any,
    *,
    duration_ms: int | None = None,
    **context: Any,
) -> dict[str, Any] | None:
    return _emit("tool", "tool result", tool=tool, response=result, duration_ms=duration_ms, **context)


def log_node_enter(node: str, state: Any = None, **context: Any) -> dict[str, Any] | None:
    return _emit("node", "node enter", node=node, input=state, **context)


def log_node_exit(node: str, output: Any = None, **context: Any) -> dict[str, Any] | None:
    return _emit("node", f"{node} output", node=node, response=output, **context)


def log_route_decision(route: Any, *, reason: str | None = None, **context: Any) -> dict[str, Any] | None:
    return _emit("node", "intent result", task_type=route, reason=reason, **context)


def log_agent_error(message: str, error: BaseException | Any, **context: Any) -> dict[str, Any] | None:
    return _emit("error", message, error=error, **context)
