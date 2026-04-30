from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from markdownify import markdownify as html_to_markdown

from app.agent.graph import stream_agent_events
from app.api.schemas import (
    ChatRequest,
    DocumentUploadResponse,
    ExportDocumentRequest,
    HealthResponse,
    SaveDocumentRequest,
    SaveDocumentResponse,
    UploadResponse,
)
from app.core.config import get_settings
from app.services.file_parser import ParsedDocument, parse_upload_file
from app.tools.document_tools import DOWNLOAD_CACHE


api_router = APIRouter(prefix="/api/v1", tags=["api-v1"])
document_router = APIRouter(prefix="/api/document", tags=["document"])


@api_router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@api_router.post("/chat-stream")
async def chat_stream(payload: ChatRequest) -> StreamingResponse:
    async def generator():
        try:
            async for event in stream_agent_events(
                session_id=payload.session_id,
                user_input=payload.user_input,
                focus_element_id=payload.focus_element_id,
                canvas_snapshot=payload.canvas_snapshot,
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

