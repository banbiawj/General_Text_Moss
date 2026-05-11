from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.agent.checkpointing import open_sqlite_checkpointer
from app.agent.graph import compile_agent_graph
from app.api.routes import api_router, document_router
from app.core.config import get_settings


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = settings.langgraph_checkpoint_path
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    async with open_sqlite_checkpointer(checkpoint_path) as checkpointer:
        app.state.agent_graph = compile_agent_graph(checkpointer=checkpointer)
        yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

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


@app.get("/library", include_in_schema=False)
@app.get("/library.html", include_in_schema=False)
async def library_entry():
    library_path = Path(__file__).resolve().parents[2] / "library.html"
    if library_path.exists():
        return FileResponse(library_path)
    return JSONResponse(
        {"status": "not_found", "message": "library.html is missing"},
        status_code=404,
    )
