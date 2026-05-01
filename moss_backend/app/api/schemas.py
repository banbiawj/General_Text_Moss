from __future__ import annotations

from time import time
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatContext(BaseModel):
    document_html: str | None = Field(default=None, alias="documentHTML")
    cursor_position: str | None = Field(default=None, alias="cursorPosition")
    history: list[dict] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class ChatRequest(BaseModel):
    """Canonical chat payload consumed by the LangGraph entrypoint.

    The model also accepts the template's earlier `message/context` shape so the
    frontend can evolve without breaking the backend contract.
    """

    session_id: str = Field(default_factory=lambda: f"session-{uuid4().hex}")
    user_input: str = ""
    focus_element_id: str | None = None
    focus_block_id: str | None = None
    canvas_snapshot: str = ""

    message: str | None = None
    context: ChatContext | None = None

    @model_validator(mode="after")
    def normalize_legacy_payload(self) -> "ChatRequest":
        if not self.user_input and self.message:
            self.user_input = self.message
        if self.context:
            if not self.canvas_snapshot and self.context.document_html:
                self.canvas_snapshot = self.context.document_html
            if not self.focus_element_id and self.context.cursor_position:
                self.focus_element_id = self.context.cursor_position
        if not self.user_input.strip():
            raise ValueError("user_input/message cannot be empty")
        return self


class UploadResponse(BaseModel):
    status: str = "success"
    filename: str
    text: str
    html_content: str = Field(alias="htmlContent")

    model_config = ConfigDict(populate_by_name=True)


class DocumentUploadResponse(BaseModel):
    status: str = "success"
    filename: str
    text_content: str = Field(alias="textContent")
    html_content: str = Field(alias="htmlContent")

    model_config = ConfigDict(populate_by_name=True)


class SaveDocumentRequest(BaseModel):
    doc_id: str = Field(default_factory=lambda: f"doc-{uuid4().hex}", alias="docId")
    content: str
    timestamp: float = Field(default_factory=time)

    model_config = ConfigDict(populate_by_name=True)


class SaveDocumentResponse(BaseModel):
    status: str = "success"
    message: str = "Saved successfully"
    doc_id: str = Field(alias="docId")

    model_config = ConfigDict(populate_by_name=True)


class ExportDocumentRequest(BaseModel):
    format: Literal["markdown", "html", "pdf"]
    content: str
    filename: str = "moss-document"


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "moss-backend"
