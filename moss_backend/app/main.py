from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.api.routes import api_router, document_router
from app.core.config import get_settings


settings = get_settings()

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(document_router)


@app.get("/", include_in_schema=False)
async def frontend_entry():
    index_path = Path(__file__).resolve().parents[2] / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return JSONResponse({"status": "ok", "message": "Moss backend is running"})
