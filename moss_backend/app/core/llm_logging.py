from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from app.core.config import get_settings


_WRITE_LOCK = threading.Lock()
_LOGGER = logging.getLogger(__name__)


def log_llm_messages(
    *,
    session_id: str,
    request_id: str,
    llm_call_id: str,
    direction: str,
    model: str,
    messages: list[BaseMessage],
) -> None:
    """Append one JSONL record for each message sent to or returned by the LLM."""

    settings = get_settings()
    if not settings.enable_llm_logging:
        return

    timestamp = _utc_now()
    records = [
        {
            "timestamp": timestamp,
            "session_id": session_id,
            "request_id": request_id,
            "llm_call_id": llm_call_id,
            "event": "llm_message",
            "direction": direction,
            "model": model,
            "message_index": index,
            **_message_payload(message),
        }
        for index, message in enumerate(messages)
    ]
    try:
        _append_jsonl_records(records)
    except OSError:
        _LOGGER.exception("Failed to write LLM JSONL logs")


def _append_jsonl_records(records: list[dict[str, Any]]) -> None:
    if not records:
        return

    settings = get_settings()
    log_path = Path(settings.llm_log_file)
    if not log_path.is_absolute():
        log_path = Path(settings.storage_dir) / log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [json.dumps(record, ensure_ascii=False, default=str) for record in records]
    with _WRITE_LOCK:
        with log_path.open("a", encoding="utf-8") as file:
            file.write("\n".join(lines))
            file.write("\n")


def _message_payload(message: BaseMessage) -> dict[str, Any]:
    message_type = getattr(message, "type", message.__class__.__name__.lower())
    payload: dict[str, Any] = {
        "sender": _sender_for(message_type),
        "message_type": message_type,
        "message_class": message.__class__.__name__,
        "content": _jsonable(getattr(message, "content", "")),
        "name": getattr(message, "name", None),
        "message_id": getattr(message, "id", None),
    }

    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        payload["tool_calls"] = _jsonable(tool_calls)

    tool_call_id = getattr(message, "tool_call_id", None)
    if tool_call_id:
        payload["tool_call_id"] = tool_call_id

    usage_metadata = getattr(message, "usage_metadata", None)
    if usage_metadata:
        payload["usage_metadata"] = _jsonable(usage_metadata)

    response_metadata = getattr(message, "response_metadata", None)
    if response_metadata:
        payload["response_metadata"] = _jsonable(response_metadata)

    additional_kwargs = getattr(message, "additional_kwargs", None)
    if additional_kwargs:
        payload["additional_kwargs"] = _jsonable(additional_kwargs)

    return payload


def _sender_for(message_type: str) -> str:
    return {
        "human": "human",
        "ai": "ai",
        "system": "system",
        "tool": "tool",
    }.get(message_type, message_type)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        return repr(value)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
