from __future__ import annotations

import json
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from app.agent.skill_runtime import (
    build_skill_system_prompt,
    build_task_from_skill,
    load_skill_registry,
    route_skill,
)
from app.agent.state import AgentState
from app.core.config import get_settings
from app.core.llm_logging import log_llm_messages
from app.tools.document_tools import DOCUMENT_TOOLS


MutationEvent = dict[str, Any]


@lru_cache
def get_graph():
    settings = get_settings()
    if not settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY is required")

    llm = ChatOpenAI(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        streaming=True,
    )
    skill_registry = load_skill_registry()
    tools_by_name = {tool.name: tool for tool in DOCUMENT_TOOLS}

    async def intent_node(state: AgentState) -> dict[str, Any]:
        selected_skill = route_skill(state.get("user_input", ""), skill_registry)
        task = build_task_from_skill(
            skill=selected_skill,
            user_input=state.get("user_input", ""),
            focus_block_id=state.get("focus_block_id"),
            canvas_snapshot=state.get("canvas_snapshot", ""),
        )
        return {
            "tasks": [task],
            "current_task_index": 0,
        }

    async def agent_node(state: AgentState) -> dict[str, list]:
        task = _current_task(state)
        system_prompt = build_skill_system_prompt(task)
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
        allowed_tools = [tools_by_name[name] for name in task.get("task_tools", []) if name in tools_by_name]
        runnable_llm = llm.bind_tools(allowed_tools) if allowed_tools else llm
        response = await runnable_llm.ainvoke(llm_messages)
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


def _current_task(state: AgentState) -> dict[str, Any]:
    tasks = state.get("tasks") or []
    current_index = int(state.get("current_task_index") or 0)
    if not tasks or current_index >= len(tasks):
        return {
            "skill_id": "general-chat",
            "task_type": "general_chat",
            "task_prompt": "Answer the user directly.",
            "task_tools": [],
            "canvas_context": "",
            "allowed_element_ids": [],
        }
    return tasks[current_index]


