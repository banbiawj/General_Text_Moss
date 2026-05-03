from __future__ import annotations

import json
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any
from uuid import uuid4

from langchain_core.prompts import load_prompt
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field

from app.agent.state import AgentState, TaskType
from app.core.config import get_settings
from app.core.llm_logging import log_llm_messages
from app.tools.document_tools import DOCUMENT_TOOLS


MutationEvent = dict[str, Any]





class IntentDecision(BaseModel):
    task_type: TaskType = Field(description="用户本轮请求的任务类型")
    reason: str = Field(description="一句话说明分类原因")


@lru_cache
def get_graph():
    settings = get_settings()
    if not settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY is required")

    intent_llm = ChatOpenAI(
        model=settings.llm_model,
        temperature=0,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        streaming=False,
    ).with_structured_output(IntentDecision)

    llm = ChatOpenAI(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        streaming=True,
    ).bind_tools(DOCUMENT_TOOLS)

    async def intent_node(state: AgentState) -> dict[str, Any]:
        intent_prompt =load_prompt("prompts/intent_prompt.yaml",encoding="utf-8")
        decision = await intent_llm.ainvoke(
            [
                SystemMessage(content=intent_prompt.format()),
                HumanMessage(
                    content=json.dumps(
                        {
                            "user_input": state.get("user_input", ""),
                            "has_document": bool(state.get("canvas_snapshot", "").strip()),
                            "has_focus_element": bool(state.get("focus_element_id")),
                            "has_focus_block": bool(state.get("focus_block_id")),
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
        )
        return {
            "tasks": [
                {
                    "task_id": uuid4().hex,
                    "task_type": decision.task_type,
                    "task_reason":decision.reason,
                    "task_prompt": decision.reason,
                    "task_tools": _default_task_tools(decision.task_type),
                    "allowed_element_ids": [],
                    "status": "pending",
                }
            ],
            "current_task_index": 0,
        }

    async def agent_node(state: AgentState) -> dict[str, list]:
        system_prompt = build_system_prompt(
            canvas_snapshot=state["canvas_snapshot"],
            focus_element_id=state.get("focus_element_id"),
            focus_block_id=state.get("focus_block_id"),
        )
        llm_messages = [SystemMessage(content=system_prompt), *state["messages"]]
        llm_call_id = uuid4().hex
        log_llm_messages(
            session_id=state["session_id"],
            request_id=state["request_id"],
            llm_call_id=llm_call_id,
            direction="to_llm",
            model=settings.llm_model,
            messages=llm_messages,
        )
        response = await llm.ainvoke(llm_messages)
        log_llm_messages(
            session_id=state["session_id"],
            request_id=state["request_id"],
            llm_call_id=llm_call_id,
            direction="from_llm",
            model=settings.llm_model,
            messages=[response],
        )
        return {"messages": [response]}

    def should_continue(state: AgentState) -> str:
        last_message = state["messages"][-1]
        if getattr(last_message, "tool_calls", None):
            return "tools"
        return END

    builder = StateGraph(AgentState)
    builder.add_node("intent", intent_node)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(DOCUMENT_TOOLS))
    builder.set_entry_point("intent")
    builder.add_edge("intent", "agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")
    return builder.compile()


async def stream_agent_events(
    *,
    session_id: str,
    user_input: str,
    focus_element_id: str | None,
    focus_block_id: str | None,
    canvas_snapshot: str,
) -> AsyncIterator[MutationEvent]:
    initial_state: AgentState = {
        "messages": [HumanMessage(content=user_input)],
        "user_input": user_input,
        "canvas_snapshot": canvas_snapshot,
        "focus_element_id": focus_element_id,
        "focus_block_id": focus_block_id,
        "tasks": [],
        "current_task_index": 0,
        "session_id": session_id,
        "request_id": uuid4().hex,
    }
    graph = get_graph()
    config = {"configurable": {"thread_id": session_id}}

    async for raw_event in graph.astream_events(initial_state, config=config, version="v2"):
        event_name = raw_event.get("event")
        node_name = raw_event.get("name")
        data = raw_event.get("data", {})

        if event_name == "on_chat_model_stream":
            content = _chunk_to_text(data.get("chunk"))
            if content:
                yield {"event": "chat_chunk", "data": {"content": content}}

        if event_name == "on_tool_start" and node_name == "update_canvas_element":
            tool_input = _coerce_tool_input(data.get("input"))
            yield {
                "event": "dom_mutation",
                "data": {
                    "element_id": tool_input.get("element_id"),
                    "targetId": tool_input.get("element_id"),
                    "action_type": tool_input.get("action_type", "replace"),
                    "new_html": tool_input.get("new_html", ""),
                },
            }


def _chunk_to_text(chunk: Any) -> str:
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def _coerce_tool_input(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _default_task_tools(task_type: str) -> list[str]:
    if task_type == "general_chat":
        return []
    if task_type == "document_qa":
        return ["search_document_blocks"]
    if task_type in {"local_edit", "global_edit"}:
        return ["search_document_blocks", "update_canvas_element"]
    return []


