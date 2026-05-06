from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncGenerator
from uuid import uuid4

import yaml
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.agent.state import AgentState, AgentTask, TaskType
from app.core.config import get_settings
from app.services.document_content import tailor_context
from app.tools.document_tools import DOCUMENT_TOOLS


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _load_prompt_template(filepath: str | Path) -> PromptTemplate:
    """Load a PromptTemplate from a YAML prompt file (safe replacement for deprecated load_prompt)."""
    path = Path(filepath)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid prompt format in {filepath}: expected a mapping, got {type(data).__name__}")
    return PromptTemplate(
        template=data["template"],
        input_variables=data.get("input_variables", []),
    )

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

    if settings.enable_mock_llm:
        return {
            "task_type": "general_chat",
            "task_reason": "mock（ENABLE_MOCK_LLM=true，跳过意图识别）",
        }

    system_prompt = _load_prompt_template(PROMPTS_DIR / "intent_prompt.yaml").format()

    # Use function-calling method for structured output (compatible with DeepSeek and OpenAI)
    llm = (
        ChatOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
        )
        .with_structured_output(IntentOutput, method="function_calling")
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
        prompt = _load_prompt_template(
        PROMPTS_DIR / "general_chat_prompt.yaml"
        )
    elif task_type == "document_qa":
        # 文档问答需要完整的画布内容用于检索
        tailored = tailor_context(canvas_snapshot, focus_block_id, task_type)
        context_chunks = tailored if tailored else [""]
        prompt = _load_prompt_template(
        PROMPTS_DIR / "document_qa_prompt.yaml"
        )
    elif task_type == "local_edit":
        tailored = tailor_context(canvas_snapshot, focus_block_id, task_type)
        context_chunks = tailored if tailored else [""]
        prompt = _load_prompt_template(
        PROMPTS_DIR / "local_edit_prompt.yaml"
        )
    elif task_type == "global_edit":
        tailored = tailor_context(canvas_snapshot, focus_block_id, task_type)
        context_chunks = tailored if tailored else [""]
        prompt = _load_prompt_template(
        PROMPTS_DIR / "global_edit_prompt.yaml"
        )
    else:
        context_chunks = [""]
        prompt = _load_prompt_template(
        PROMPTS_DIR / "general_chat_prompt.yaml"
        )

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


# ── Execute Node (ReAct) ─────────────────────────────────────────────────


def execute_node(state: AgentState) -> dict[str, Any]:
    """Execute the current task via LLM with optional tool calling (ReAct).

    Uses the task's prompt as system instruction and task_message as working
    memory.  If the LLM emits tool calls they are routed to the tools node;
    otherwise the response is treated as the final answer and the task is
    marked done.
    """
    settings = get_settings()
    current_idx = state["current_task_index"]
    tasks = list(state["tasks"])
    task = tasks[current_idx]

    if settings.enable_mock_llm:
        response = AIMessage(
            content=f"（Mock 回复）收到您的消息，当前任务类型已识别。",
        )
        task_messages = list(task.get("task_message", []))
        updated_messages = task_messages + [response]
        tasks[current_idx] = {**task, "task_message": updated_messages, "status": "done"}
        return {"tasks": tasks, "messages": [response]}

    # Only expose tools allowed for this task
    tools = [t for t in DOCUMENT_TOOLS if t.name in task.get("task_tools", [])]

    llm = ChatOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
    )
    if tools:
        llm = llm.bind_tools(tools)

    task_messages = list(task.get("task_message", []))
    messages = [SystemMessage(content=task["task_prompt"])] + task_messages

    response = llm.invoke(messages)

    # Append the LLM response to task working memory
    updated_messages = task_messages + [response]
    tasks[current_idx] = {**task, "task_message": updated_messages}

    if getattr(response, "tool_calls", None):
        tasks[current_idx] = {**tasks[current_idx], "status": "running"}
        return {"tasks": tasks}
    else:
        # Final answer – mark done and publish to global messages
        tasks[current_idx] = {**tasks[current_idx], "status": "done"}
        return {"tasks": tasks, "messages": [response]}


# ── Custom Tools Node ────────────────────────────────────────────────────


def tools_node(state: AgentState) -> dict[str, Any]:
    """Execute tool calls for the current task and append results to task_message.

    When ``update_canvas_element`` is invoked, captures the mutation args into
    ``pending_mutations`` so that ``stream_agent_events`` can relay them to the
    frontend as ``dom_mutation`` SSE events.
    """
    current_idx = state["current_task_index"]
    tasks = list(state["tasks"])
    task = tasks[current_idx]
    task_messages = list(task.get("task_message", []))
    last_msg = task_messages[-1]

    tool_results: list[ToolMessage] = []
    pending_mutations: list[dict] = []

    for tool_call in last_msg.tool_calls:
        tool = next(
            (t for t in DOCUMENT_TOOLS if t.name == tool_call["name"]),
            None,
        )
        if not tool:
            tool_results.append(
                ToolMessage(
                    content=f"Tool '{tool_call['name']}' not found.",
                    tool_call_id=tool_call["id"],
                )
            )
            continue

        try:
            args = dict(tool_call["args"])
            result = tool.invoke(args)
            result_str = str(result) if result is not None else ""
        except Exception as e:
            result_str = f"Tool error: {e}"

        tool_results.append(
            ToolMessage(content=result_str, tool_call_id=tool_call["id"])
        )

        # Capture DOM mutations from update_canvas_element calls
        if tool_call["name"] == "update_canvas_element":
            pending_mutations.append({
                "element_id": tool_call["args"].get("element_id", ""),
                "action_type": tool_call["args"].get("action_type", ""),
                "new_html": tool_call["args"].get("new_html", ""),
            })

    tasks[current_idx] = {**task, "task_message": task_messages + tool_results}
    return {"tasks": tasks, "pending_mutations": pending_mutations}


# ── Task Advance Node ────────────────────────────────────────────────────


def task_advance_node(state: AgentState) -> dict[str, Any]:
    """Advance to the next task index."""
    return {"current_task_index": state["current_task_index"] + 1}


# ── Routers ──────────────────────────────────────────────────────────────


def router_execute(state: AgentState) -> str:
    """From execute: route to tools if the last message has tool_calls, else advance."""
    current_idx = state["current_task_index"]
    task = state["tasks"][current_idx]
    msgs = task.get("task_message", [])
    if msgs and getattr(msgs[-1], "tool_calls", None):
        return "tools"
    return "task_advance"


def router_task_advance(state: AgentState) -> str:
    """From task_advance: route to execute if more tasks remain, otherwise END."""
    current_idx = state["current_task_index"]
    if current_idx < len(state["tasks"]):
        return "execute"
    return END


# ── Graph Definition ─────────────────────────────────────────────────────

builder = StateGraph(AgentState)
builder.add_node("intent", intent_node)
builder.add_node("task_assemble", task_assemble_node)
builder.add_node("execute", execute_node)
builder.add_node("tools", tools_node)
builder.add_node("task_advance", task_advance_node)

builder.add_edge(START, "intent")
builder.add_edge("intent", "task_assemble")
builder.add_edge("task_assemble", "execute")
builder.add_edge("tools", "execute")

builder.add_conditional_edges(
    "execute",
    router_execute,
    {"tools": "tools", "task_advance": "task_advance"},
)
builder.add_conditional_edges(
    "task_advance",
    router_task_advance,
    {"execute": "execute", END: END},
)

graph = builder.compile()


# ── Streaming Entrypoint ─────────────────────────────────────────────────


def _sanitize_output(output: Any) -> Any:
    """Remove non-JSON-serializable objects (e.g. BaseMessage) from node output before SSE."""
    if isinstance(output, dict):
        return {k: _sanitize_output(v) for k, v in output.items() if not k.startswith("_")}
    if isinstance(output, list):
        return [_sanitize_output(item) for item in output]
    # Exclude BaseMessage and other non-serializable types
    if hasattr(output, "content") and hasattr(output, "type"):
        return {"content": str(getattr(output, "content", "")), "type": getattr(output, "type", "unknown")}
    try:
        import json
        json.dumps(output)
        return output
    except (TypeError, ValueError):
        return str(output)


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
        "pending_mutations": [],
        "session_id": session_id,
        "request_id": uuid4().hex,
    }

    async for event in graph.astream_events(initial_state, version="v2"):
        kind = event["event"]
        name = event.get("name", "")

        if kind == "on_chain_start" and name == "intent":
            yield {"event": "node_start", "data": {"node": "intent"}}
        elif kind == "on_chain_end" and name == "intent":
            output = _sanitize_output(event.get("data", {}).get("output", {}))
            yield {
                "event": "node_end",
                "data": {"node": "intent", "output": output},
            }
        elif kind == "on_chain_start" and name == "task_assemble":
            yield {"event": "node_start", "data": {"node": "task_assemble"}}
        elif kind == "on_chain_end" and name == "task_assemble":
            output = _sanitize_output(event.get("data", {}).get("output", {}))
            yield {
                "event": "node_end",
                "data": {"node": "task_assemble", "output": output},
            }
        elif kind == "on_chain_start" and name == "execute":
            yield {"event": "node_start", "data": {"node": "execute"}}
        elif kind == "on_chain_end" and name == "execute":
            raw = event.get("data", {}).get("output", {})
            output = _sanitize_output(raw)
            yield {
                "event": "node_end",
                "data": {"node": "execute", "output": output},
            }
            # Publish AI message to frontend as chat_chunk
            for msg in raw.get("messages", []):
                content = getattr(msg, "content", "") or ""
                if content:
                    yield {"event": "chat_chunk", "data": {"content": content, "done": True}}
        elif kind == "on_chain_start" and name == "tools":
            yield {"event": "node_start", "data": {"node": "tools"}}
        elif kind == "on_chain_end" and name == "tools":
            raw = event.get("data", {}).get("output", {})
            output = _sanitize_output(raw)
            yield {
                "event": "node_end",
                "data": {"node": "tools", "output": output},
            }
            # Emit dom_mutation events to the frontend for each pending mutation
            for mutation in raw.get("pending_mutations", []):
                yield {"event": "dom_mutation", "data": mutation}
        elif kind == "on_chain_start" and name == "task_advance":
            yield {"event": "node_start", "data": {"node": "task_advance"}}
        elif kind == "on_chain_end" and name == "task_advance":
            output = _sanitize_output(event.get("data", {}).get("output", {}))
            yield {
                "event": "node_end",
                "data": {"node": "task_advance", "output": output},
            }
