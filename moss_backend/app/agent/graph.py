from __future__ import annotations

import asyncio
import html
import json
import re
from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from app.agent.prompt import build_system_prompt
from app.agent.state import AgentState
from app.core.config import get_settings
from app.core.llm_logging import log_llm_messages
from app.tools.document_tools import DOCUMENT_TOOLS


MutationEvent = dict[str, Any]


@lru_cache
def get_graph():
    settings = get_settings()
    if not settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY is required when ENABLE_MOCK_LLM=false")

    llm = ChatOpenAI(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        streaming=True,
    ).bind_tools(DOCUMENT_TOOLS)

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
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(DOCUMENT_TOOLS))
    builder.set_entry_point("agent")
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
    settings = get_settings()

    if settings.enable_mock_llm or not settings.llm_api_key:
        async for event in _mock_stream(user_input, focus_element_id, focus_block_id, canvas_snapshot):
            yield event
        return

    initial_state: AgentState = {
        "messages": [HumanMessage(content=user_input)],
        "canvas_snapshot": canvas_snapshot,
        "focus_element_id": focus_element_id,
        "focus_block_id": focus_block_id,
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


async def _mock_stream(
    user_input: str,
    focus_element_id: str | None,
    focus_block_id: str | None,
    canvas_snapshot: str,
) -> AsyncIterator[MutationEvent]:
    target_id = _select_target_id(focus_element_id, focus_block_id, canvas_snapshot)
    wants_mutation = _looks_like_mutation(user_input)

    if not wants_mutation:
        reply = "我已经读取当前文档快照。当前处于本地 Mock 模式；配置真实大模型后，我会基于同一 SSE 协议进行上下文问答和文档修改。"
        for chunk in _split_reply(reply):
            await asyncio.sleep(0.02)
            yield {"event": "chat_chunk", "data": {"content": chunk}}
        return

    action_type = "replace"
    if any(word in user_input for word in ("删除", "移除", "删掉")):
        action_type = "delete"
        new_html = ""
        reply = f"好的，已为你删除目标区块 `{target_id}`。"
    elif any(word in user_input for word in ("追加", "新增", "插入", "补充")):
        action_type = "append"
        new_html = (
            f'<p id="block-{html.escape(target_id)}-append">'
            f"根据指令补充：{html.escape(user_input)}</p>"
        )
        reply = f"好的，已在 `{target_id}` 中追加一段内容。"
    else:
        new_html = _mock_rewrite_html(target_id, user_input)
        reply = f"好的，已按你的指令更新 `{target_id}`。"

    for chunk in _split_reply(reply):
        await asyncio.sleep(0.02)
        yield {"event": "chat_chunk", "data": {"content": chunk}}

    await asyncio.sleep(0.05)
    yield {
        "event": "dom_mutation",
        "data": {
            "element_id": target_id,
            "targetId": target_id,
            "action_type": action_type,
            "new_html": new_html,
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


def _looks_like_mutation(text: str) -> bool:
    mutation_hints = (
        "修改",
        "优化",
        "润色",
        "改写",
        "替换",
        "调整",
        "删除",
        "移除",
        "新增",
        "追加",
        "插入",
        "精简",
        "扩写",
        "排版",
    )
    return any(hint in text for hint in mutation_hints)


def _select_target_id(
    focus_element_id: str | None,
    focus_block_id: str | None,
    canvas_snapshot: str,
) -> str:
    if focus_element_id:
        return focus_element_id
    if focus_block_id:
        return focus_block_id
    match = re.search(r'id=["\']([^"\']+)["\']', canvas_snapshot)
    return match.group(1) if match else "demo-section"


def _mock_rewrite_html(target_id: str, user_input: str) -> str:
    safe_target = html.escape(target_id, quote=True)
    safe_input = html.escape(user_input)
    return (
        f'<div id="{safe_target}" class="transition-colors duration-500 rounded-lg p-2 -mx-2 mt-4">'
        "<h2>AI 协作更新</h2>"
        f"<p>已根据指令整理此区块：{safe_input}</p>"
        "<p>这是本地 Mock 模式生成的结构化 HTML。接入真实大模型后，"
        "后端会继续通过相同的 dom_mutation 事件驱动前端局部更新。</p>"
        "</div>"
    )


def _split_reply(reply: str) -> list[str]:
    return [reply[index : index + 8] for index in range(0, len(reply), 8)]
