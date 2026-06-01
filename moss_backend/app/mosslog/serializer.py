from __future__ import annotations

import json
import traceback
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from typing import Any


def safe_serialize(value: Any) -> Any:
    if isinstance(value, BaseException):
        return {
            "type": type(value).__name__,
            "message": str(value),
            "traceback": traceback.format_exception(type(value), value, value.__traceback__),
        }

    if is_dataclass(value) and not isinstance(value, type):
        return safe_serialize(asdict(value))

    if isinstance(value, Mapping):
        return {str(key): safe_serialize(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set, frozenset)):
        return [safe_serialize(item) for item in value]

    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)

    return value
