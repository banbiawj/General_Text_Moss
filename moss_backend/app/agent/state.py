from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], operator.add]
    canvas_snapshot: str
    focus_element_id: str | None
    session_id: str
    request_id: str
