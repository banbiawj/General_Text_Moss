from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from markdownify import markdownify as html_to_markdown

from app.agent.graph import stream_agent_events
from app.agent.graph import get_conversation_messages
from app.api.schemas import (
    ChatRequest,
    CreateNoteConversationResponse,
    ConversationMessagesResponse,
    CreateNoteResponse,
    DeleteNoteConversationResponse,
    DeleteNoteResponse,
    DocumentUploadResponse,
    ExportDocumentRequest,
    HealthResponse,
    NoteDetailResponse,
    NoteConversationResponse,
    NoteConversationsResponse,
    NoteListResponse,
    NoteSummaryResponse,
    SaveDocumentRequest,
    SaveDocumentResponse,
    SaveNoteSnapshotRequest,
    SaveNoteSnapshotResponse,
    UpdateNoteConversationRequest,
    UpdateNoteRequest,
    UpdateNoteResponse,
    UploadResponse,
)
from app.core.config import get_settings
from app.services.file_parser import ParsedDocument, parse_upload_file
from app.services.conversations import (
    DEFAULT_USER_ID,
    ConversationStore,
    InvalidConversationId,
)
from app.services.notes import InvalidNoteId, NoteStore, _UNSET
from app.tools.document_tools import DOWNLOAD_CACHE


api_router = APIRouter(prefix="/api/v1", tags=["api-v1"])
document_router = APIRouter(prefix="/api/document", tags=["document"])


def _note_conversation_response(conversation) -> NoteConversationResponse:
    if conversation.note_id is None:
        raise ValueError("conversation is not attached to a note")
    return NoteConversationResponse(
        conversation_id=conversation.conversation_id,
        note_id=conversation.note_id,
        title=conversation.title,
        is_default=conversation.is_default,
        pinned_at=conversation.pinned_at,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def get_conversation_store() -> ConversationStore:
    settings = get_settings()
    return ConversationStore(settings.conversation_metadata_path)


def get_note_store() -> NoteStore:
    settings = get_settings()
    return NoteStore(
        settings.conversation_metadata_path,
        checkpoint_db_path=settings.langgraph_checkpoint_path,
    )


@api_router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@api_router.get("/notes", response_model=NoteListResponse)
async def list_notes() -> NoteListResponse:
    notes = get_note_store().list_notes(DEFAULT_USER_ID)
    return NoteListResponse(
        notes=[
            NoteSummaryResponse(
                note_id=note.note_id,
                default_conversation_id=note.default_conversation_id,
                active_conversation_id=note.active_conversation_id,
                title=note.title,
                display_title=note.display_title,
                effective_title=note.effective_title,
                preview_text=note.preview_text,
                pinned_at=note.pinned_at,
                created_at=note.created_at,
                updated_at=note.updated_at,
            )
            for note in notes
        ]
    )


@api_router.post("/notes", response_model=CreateNoteResponse)
async def create_note() -> CreateNoteResponse:
    created = get_note_store().create_note(DEFAULT_USER_ID)
    return CreateNoteResponse(
        note_id=created.note.note_id,
        default_conversation_id=created.default_conversation.conversation_id,
    )


@api_router.get("/notes/{note_id}", response_model=NoteDetailResponse)
async def get_note(note_id: str) -> NoteDetailResponse:
    try:
        note = get_note_store().get_note(DEFAULT_USER_ID, note_id)
    except InvalidNoteId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return NoteDetailResponse(
        note_id=note.note_id,
        default_conversation_id=note.default_conversation_id,
        active_conversation_id=note.active_conversation_id,
        last_opened_conversation_id=note.last_opened_conversation_id,
        title=note.title,
        display_title=note.display_title,
        effective_title=note.effective_title,
        canvas_snapshot=note.canvas_snapshot,
        preview_text=note.preview_text,
        pinned_at=note.pinned_at,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


@api_router.put("/notes/{note_id}/snapshot", response_model=SaveNoteSnapshotResponse)
async def save_note_snapshot(
    note_id: str,
    payload: SaveNoteSnapshotRequest,
) -> SaveNoteSnapshotResponse:
    try:
        saved = get_note_store().save_snapshot(
            DEFAULT_USER_ID,
            note_id,
            payload.canvas_snapshot,
        )
    except InvalidNoteId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SaveNoteSnapshotResponse(
        note_id=saved.note_id,
        title=saved.title,
        preview_text=saved.preview_text,
        updated_at=saved.updated_at,
    )


@api_router.patch("/notes/{note_id}", response_model=UpdateNoteResponse)
async def update_note(
    note_id: str,
    payload: UpdateNoteRequest,
) -> UpdateNoteResponse:
    try:
        display_title = (
            payload.display_title
            if "display_title" in payload.model_fields_set
            else _UNSET
        )
        pinned = payload.pinned if "pinned" in payload.model_fields_set else _UNSET
        updated = get_note_store().update_note(
            DEFAULT_USER_ID,
            note_id,
            display_title=display_title,
            pinned=pinned,
        )
    except InvalidNoteId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return UpdateNoteResponse(
        note_id=updated.note_id,
        title=updated.title,
        display_title=updated.display_title,
        effective_title=updated.effective_title,
        preview_text=updated.preview_text,
        pinned_at=updated.pinned_at,
        updated_at=updated.updated_at,
    )


@api_router.delete("/notes/{note_id}", response_model=DeleteNoteResponse)
async def delete_note(note_id: str) -> DeleteNoteResponse:
    try:
        deleted = get_note_store().delete_note(DEFAULT_USER_ID, note_id)
    except InvalidNoteId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return DeleteNoteResponse(
        note_id=deleted.note_id,
        deleted_at=deleted.deleted_at,
    )


@api_router.get(
    "/notes/{note_id}/conversations",
    response_model=NoteConversationsResponse,
)
async def list_note_conversations(note_id: str) -> NoteConversationsResponse:
    try:
        store = get_note_store()
        note = store.get_note(DEFAULT_USER_ID, note_id)
        conversations = store.list_note_conversations(DEFAULT_USER_ID, note_id)
    except InvalidNoteId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return NoteConversationsResponse(
        conversations=[
            _note_conversation_response(conversation)
            for conversation in conversations
        ],
        active_conversation_id=note.active_conversation_id,
    )


@api_router.post(
    "/notes/{note_id}/conversations",
    response_model=CreateNoteConversationResponse,
)
async def create_note_conversation(note_id: str) -> CreateNoteConversationResponse:
    try:
        conversation = get_note_store().create_conversation_for_note(
            DEFAULT_USER_ID,
            note_id,
        )
    except InvalidNoteId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    response = _note_conversation_response(conversation)
    return CreateNoteConversationResponse(**response.model_dump())


@api_router.patch(
    "/notes/{note_id}/conversations/{conversation_id}",
    response_model=NoteConversationResponse,
)
async def update_note_conversation(
    note_id: str,
    conversation_id: str,
    payload: UpdateNoteConversationRequest,
) -> NoteConversationResponse:
    try:
        title = payload.title if "title" in payload.model_fields_set else _UNSET
        pinned = payload.pinned if "pinned" in payload.model_fields_set else _UNSET
        conversation = get_note_store().update_conversation(
            DEFAULT_USER_ID,
            note_id,
            conversation_id,
            title=title,
            pinned=pinned,
        )
    except (InvalidConversationId, InvalidNoteId, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return _note_conversation_response(conversation)


@api_router.delete(
    "/notes/{note_id}/conversations/{conversation_id}",
    response_model=DeleteNoteConversationResponse,
)
async def delete_note_conversation(
    note_id: str,
    conversation_id: str,
) -> DeleteNoteConversationResponse:
    try:
        deleted = get_note_store().delete_conversation(
            DEFAULT_USER_ID,
            note_id,
            conversation_id,
        )
    except (InvalidConversationId, InvalidNoteId) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return DeleteNoteConversationResponse(
        conversation_id=deleted.conversation_id,
        deleted_at=deleted.deleted_at,
    )


@api_router.get(
    "/notes/{note_id}/conversations/{conversation_id}/messages",
    response_model=ConversationMessagesResponse,
)
async def list_conversation_messages(
    note_id: str,
    conversation_id: str,
    request: Request,
) -> ConversationMessagesResponse:
    try:
        conversation = get_note_store().mark_conversation_opened(
            DEFAULT_USER_ID,
            note_id,
            conversation_id,
        )
        compiled_graph = getattr(request.app.state, "agent_graph", None)
        if compiled_graph is None:
            return ConversationMessagesResponse(messages=[])
        messages = await get_conversation_messages(
            compiled_graph,
            conversation.conversation_id,
        )
    except InvalidConversationId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InvalidNoteId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ConversationMessagesResponse(messages=messages)


@api_router.post("/chat-stream")
async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
    try:
        if payload.note_id and payload.conversation_id:
            note_store = get_note_store()
            conversation = note_store.verify_conversation_for_note(
                DEFAULT_USER_ID,
                payload.note_id,
                payload.conversation_id,
            )
            note_store.save_snapshot(
                DEFAULT_USER_ID,
                payload.note_id,
                payload.canvas_snapshot,
            )
            note_store.mark_conversation_opened(
                DEFAULT_USER_ID,
                payload.note_id,
                payload.conversation_id,
            )
            conversation = note_store.touch_conversation(
                conversation.conversation_id,
                title_hint=payload.user_input,
            )
            resolved_conversation_id = conversation.conversation_id
            resolved_user_id = conversation.user_id
            emit_conversation_event = False
        else:
            resolved = get_conversation_store().resolve(
                user_id=DEFAULT_USER_ID,
                conversation_id=payload.conversation_id,
            )
            resolved_conversation_id = resolved.record.conversation_id
            resolved_user_id = resolved.record.user_id
            emit_conversation_event = resolved.created
    except InvalidConversationId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except InvalidNoteId as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    async def generator():
        try:
            if emit_conversation_event:
                yield _sse(
                    "conversation",
                    {
                        "conversation_id": resolved_conversation_id,
                        "user_id": resolved_user_id,
                    },
                )

            async for event in stream_agent_events(
                session_id=payload.session_id,
                conversation_id=resolved_conversation_id,
                user_input=payload.user_input,
                focus_element_id=payload.focus_element_id,
                focus_block_id=payload.focus_block_id,
                canvas_snapshot=payload.canvas_snapshot,
                compiled_graph=getattr(request.app.state, "agent_graph", None),
            ):
                yield _sse(event["event"], event.get("data", {}))
            yield _sse("done", {"status": "ok"})
        except Exception as exc:
            yield _sse("error", {"message": str(exc)})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@api_router.post("/upload", response_model=UploadResponse)
async def upload(file: UploadFile = File(...)) -> UploadResponse:
    parsed = await _parse_or_400(file)
    return UploadResponse(
        filename=parsed.filename,
        text=parsed.text,
        htmlContent=parsed.html,
    )


@api_router.get("/download/{token}")
async def download_prepared_file(token: str) -> Response:
    artifact = DOWNLOAD_CACHE.get(token)
    if not artifact:
        raise HTTPException(status_code=404, detail="下载凭证不存在或已过期")

    export_format = artifact.get("format", "markdown")
    content = artifact.get("content", "")
    filename = f"moss-export.{_extension_for(export_format)}"
    media_type = _media_type_for(export_format)
    body = content.encode("utf-8")
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@document_router.post("/upload", response_model=DocumentUploadResponse)
async def document_upload(file: UploadFile = File(...)) -> DocumentUploadResponse:
    parsed = await _parse_or_400(file)
    return DocumentUploadResponse(
        filename=parsed.filename,
        textContent=parsed.text,
        htmlContent=parsed.html,
    )


@document_router.post("/save", response_model=SaveDocumentResponse)
async def save_document(payload: SaveDocumentRequest) -> SaveDocumentResponse:
    settings = get_settings()
    safe_doc_id = _safe_filename(payload.doc_id)
    document_dir = Path(settings.storage_dir) / "documents"
    document_dir.mkdir(parents=True, exist_ok=True)

    html_path = document_dir / f"{safe_doc_id}.html"
    meta_path = document_dir / f"{safe_doc_id}.json"
    html_path.write_text(payload.content, encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "docId": payload.doc_id,
                "timestamp": payload.timestamp,
                "savedAt": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return SaveDocumentResponse(docId=payload.doc_id)


@document_router.post("/export")
async def export_document(payload: ExportDocumentRequest) -> Response:
    safe_name = _safe_filename(payload.filename or "moss-document")

    if payload.format == "markdown":
        content = html_to_markdown(payload.content, heading_style="ATX")
        return Response(
            content=content.encode("utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.md"'},
        )

    if payload.format == "html":
        return Response(
            content=payload.content.encode("utf-8"),
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{safe_name}.html"'},
        )

    try:
        from weasyprint import HTML
    except ImportError as exc:
        raise HTTPException(
            status_code=501,
            detail="PDF 导出需要安装可选依赖 weasyprint",
        ) from exc

    pdf_bytes = HTML(string=payload.content).write_pdf()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'},
    )


async def _parse_or_400(file: UploadFile) -> ParsedDocument:
    try:
        return await parse_upload_file(file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")
    return cleaned or "moss-document"


def _extension_for(export_format: str) -> str:
    return {"markdown": "md", "html": "html", "pdf": "pdf"}.get(export_format, "txt")


def _media_type_for(export_format: str) -> str:
    return {
        "markdown": "text/markdown; charset=utf-8",
        "html": "text/html; charset=utf-8",
        "pdf": "application/pdf",
    }.get(export_format, "text/plain; charset=utf-8")

