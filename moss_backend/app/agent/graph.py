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

from app.agent.state import AgentState, AgentTask, TaskType
from app.core.config import get_settings
from app.services.document_content import tailor_context

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# 根据任务类型映射可用的工具列表
TASK_TYPE_TOOLS: dict[TaskType, list[str]] = {
    "general_chat": [],
    "document_qa": ["search_document_blocks"],
    "local_edit": ["search_document_blocks", "update_canvas_element"],
    "global_edit": ["update_canvas_element"],
}



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


# ── Task Assemble Node ────────────────────────────────────────────────────


def task_assemble_node(state: AgentState) -> dict[str, Any]:
    """根据意图分类结果组装任务列表：裁剪上下文、获取工具列表、生成提示词。"""
    task_type: TaskType = state.get("task_type", "general_chat")
    canvas_snapshot = state.get("canvas_snapshot", "")
    focus_block_id = state.get("focus_block_id")
    focus_element_id = state.get("focus_element_id", "")
    user_input = state.get("user_input", "")

    # 1. 根据 task_type 获取允许的工具列表
    task_tools = TASK_TYPE_TOOLS.get(task_type, [])

    # 2. 根据 task_type 裁剪 canvas 上下文
    if task_type == "general_chat":
        context_chunks = [""]
        prompt = load_prompt(
        str(PROMPTS_DIR / "general_chat_prompt.yaml"), encoding="utf-8"
        )
    elif task_type == "document_qa":
        # 文档问答需要完整的画布内容用于检索
        tailored = tailor_context(canvas_snapshot, focus_block_id, task_type)
        context_chunks = tailored if tailored else [""]
        prompt = load_prompt(
        str(PROMPTS_DIR / "document_qa_prompt.yaml"), encoding="utf-8"
        )
    elif task_type == "local_edit":
        tailored = tailor_context(canvas_snapshot, focus_block_id, task_type)
        context_chunks = tailored if tailored else [""]
        prompt = load_prompt(
        str(PROMPTS_DIR / "local_edit_prompt.yaml"), encoding="utf-8"
        )
    elif task_type == "glocal_edit":
        tailored = tailor_context(canvas_snapshot, focus_block_id, task_type)
        context_chunks = tailored if tailored else [""]
        prompt = load_prompt(
        str(PROMPTS_DIR / "glocal_edit_prompt.yaml"), encoding="utf-8"
        )
    else:
        context_chunks = [""]

    # 3. 加载提示词模板并组装 task_prompt


    tasks: list[AgentTask] = []
    for chunk in context_chunks:
        task_prompt = prompt.format(
            user_input=user_input,
            canvas_context=chunk,
            focus_element_id=focus_element_id or "",
            focus_block_id=focus_block_id or "",
            task_tools=str(task_tools),
        )
        task = AgentTask(
            task_id=uuid4().hex,
            task_message=[],
            canvas_context=chunk,
            task_prompt=task_prompt,
            task_tools=task_tools,
            allowed_element_ids=[],
            status="pending",
        )
        tasks.append(task)

    return {"tasks": tasks}


# ── Graph Definition ─────────────────────────────────────────────────────

builder = StateGraph(AgentState)
builder.add_node("intent", intent_node)
builder.add_node("task_assemble", task_assemble_node)
builder.add_edge(START, "intent")
builder.add_edge("intent", "task_assemble")
builder.add_edge("task_assemble", END)

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
        elif kind == "on_chain_start" and name == "task_assemble":
            yield {"event": "node_start", "data": {"node": "task_assemble"}}
        elif kind == "on_chain_end" and name == "task_assemble":
            output = event.get("data", {}).get("output", {})
            yield {
                "event": "node_end",
                "data": {"node": "task_assemble", "output": output},
            }