from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncGenerator
from uuid import uuid4

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_core.prompts import load_prompt
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.agent.state import AgentState,AgentTask, TaskType
from app.core.config import get_settings

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"



# ── Intent Node ──────────────────────────────────────────────────────────


class IntentOutput(BaseModel):
    """Structured output from the intent classifier LLM."""

    task_type: TaskType = Field(description="意图分类结果")
    task_reason: str = Field(description="判断原因，一句话说明为什么归为该类别")


def intent_node(state: AgentState) -> dict[str, Any]:
    """Use LLM to classify user intent, then create a task in state.tasks."""
    settings = get_settings()
    system_prompt = load_prompt("prompts/intent_prompt.yaml",encoding="utf-8").format()


    llm = (
        ChatOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
        )
        .with_structured_output(IntentOutput)
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=state.get("user_input", "")),
    ]

    result: IntentOutput = llm.invoke(messages)

    return {
        "task_type": result.task_type,
        "task_reason": result.task_reason,
    }


# ── Graph Definition ─────────────────────────────────────────────────────

builder = StateGraph(AgentState)
builder.add_node("intent", intent_node)
builder.add_edge(START, "intent")
builder.add_edge("intent", END)

graph = builder.compile()


# ── Streaming Entrypoint ─────────────────────────────────────────────────


async def stream_agent_events(
    session_id: str,
    user_input: str,
    focus_element_id: str | None,
    focus_block_id: str | None,
    canvas_snapshot: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run the agent graph and yield SSE-compatible events.

    Each yielded dict has the shape ``{"event": str, "data": dict}``,
    which the caller (routes.py) serialises into an SSE frame.
    """
    initial_state: dict[str, Any] = {
        "messages": [],
        "user_input": user_input,
        "canvas_snapshot": canvas_snapshot,
        "focus_element_id": focus_element_id,
        "focus_block_id": focus_block_id,
        "task_type": "general_chat",
        "task_reason": "",
        "tasks": [],
        "current_task_index": 0,
        "session_id": session_id,
        "request_id": uuid4().hex,
    }

    async for event in graph.astream_events(initial_state, version="v2"):
        kind = event["event"]
        name = event.get("name", "")

        if kind == "on_chain_start" and name == "intent":
            yield {"event": "node_start", "data": {"node": "intent"}}
        elif kind == "on_chain_end" and name == "intent":
            output = event.get("data", {}).get("output", {})
            yield {
                "event": "node_end",
                "data": {"node": "intent", "output": output},
            }