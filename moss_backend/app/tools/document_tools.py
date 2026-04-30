from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from langchain_core.tools import tool
from pydantic import BaseModel, Field


class CanvasMutationArgs(BaseModel):
    element_id: str = Field(description="前端文档中需要操作的 DOM 节点 id")
    action_type: Literal["replace", "append", "insert", "delete"] = Field(
        description="文档操作类型：replace/append/insert/delete"
    )
    new_html: str = Field(default="", description="新的 HTML 片段，delete 时可以为空")


class DownloadLinkArgs(BaseModel):
    export_format: Literal["markdown", "html", "pdf"] = Field(description="导出格式")
    content: str = Field(default="", description="需要导出的文档内容")


DOWNLOAD_CACHE: dict[str, dict] = {}


@tool(args_schema=CanvasMutationArgs)
def update_canvas_element(element_id: str, action_type: str, new_html: str = "") -> str:
    """Dispatch a structured document mutation to the browser canvas."""

    return (
        f"文档修改指令已派发至前端：element_id={element_id}, "
        f"action_type={action_type}。"
    )


@tool(args_schema=DownloadLinkArgs)
def generate_download_link(export_format: str, content: str = "") -> str:
    """Prepare a temporary download URL for the requested document format."""

    token = uuid4().hex
    DOWNLOAD_CACHE[token] = {
        "format": export_format,
        "content": content,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return f"/api/v1/download/{token}"


DOCUMENT_TOOLS = [update_canvas_element, generate_download_link]

