from __future__ import annotations

from time import time
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.conversations import is_valid_conversation_id
from app.services.notes import is_valid_note_id


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
    note_id: str | None = None
    conversation_id: str | None = None
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
        if self.conversation_id is not None and not is_valid_conversation_id(self.conversation_id):
            raise ValueError("conversation_id must match conv-[A-Za-z0-9_-]{8,64}")
        if self.note_id is not None and not is_valid_note_id(self.note_id):
            raise ValueError("note_id must match note-[A-Za-z0-9_-]{8,64}")
        if not self.user_input.strip():
            raise ValueError("user_input/message cannot be empty")
        return self


class NoteSummaryResponse(BaseModel):
    note_id: str
    default_conversation_id: str
    active_conversation_id: str
    title: str
    display_title: str | None = None
    effective_title: str
    preview_text: str
    pinned_at: str | None = None
    created_at: str
    updated_at: str


class NoteListResponse(BaseModel):
    notes: list[NoteSummaryResponse]


class CreateNoteResponse(BaseModel):
    note_id: str
    default_conversation_id: str


class NoteDetailResponse(BaseModel):
    note_id: str
    default_conversation_id: str
    active_conversation_id: str
    last_opened_conversation_id: str | None = None
    title: str
    display_title: str | None = None
    effective_title: str
    canvas_snapshot: str
    preview_text: str
    pinned_at: str | None = None
    created_at: str
    updated_at: str


class SaveNoteSnapshotRequest(BaseModel):
    canvas_snapshot: str = ""


class SaveNoteSnapshotResponse(BaseModel):
    note_id: str
    title: str
    preview_text: str
    updated_at: str


class UpdateNoteRequest(BaseModel):
    display_title: str | None = None
    pinned: bool | None = None


class UpdateNoteResponse(BaseModel):
    note_id: str
    title: str
    display_title: str | None = None
    effective_title: str
    preview_text: str
    pinned_at: str | None = None
    updated_at: str


class DeleteNoteResponse(BaseModel):
    note_id: str
    deleted_at: str


class NoteConversationResponse(BaseModel):
    conversation_id: str
    note_id: str
    title: str
    is_default: bool
    pinned_at: str | None = None
    created_at: str
    updated_at: str


class NoteConversationsResponse(BaseModel):
    conversations: list[NoteConversationResponse]
    active_conversation_id: str


class CreateNoteConversationResponse(BaseModel):
    conversation_id: str
    note_id: str
    title: str
    is_default: bool
    pinned_at: str | None = None
    created_at: str
    updated_at: str


class UpdateNoteConversationRequest(BaseModel):
    title: str | None = None
    pinned: bool | None = None


class DeleteNoteConversationResponse(BaseModel):
    conversation_id: str
    deleted_at: str


class ConversationMessageResponse(BaseModel):
    role: Literal["user", "ai"]
    content: str


class ConversationMessagesResponse(BaseModel):
    messages: list[ConversationMessageResponse]


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
