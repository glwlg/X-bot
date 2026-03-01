import uvicorn
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from api.api.router import api_router
from api.auth.router import router as auth_router
from api.api.binding_router import router as binding_router
from api.api.accounting_router import router as accounting_router
from api.core.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 初始化数据库
    print("Initializing database...")
    await init_db()
    yield
    print("Shutting down...")


app = FastAPI(
    title="Template Backend",
    description="FastAPI Backend Template with Auth and DB",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发阶段允许所有源，生产环境请修改
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 注册路由
app.include_router(api_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1/auth")
app.include_router(binding_router, prefix="/api/v1/binding", tags=["binding"])
app.include_router(accounting_router, prefix="/api/v1/accounting", tags=["accounting"])


# Create static wrapper for SPA fallback
static_dir = os.path.join(os.path.dirname(__file__), "static/dist")
os.makedirs(os.path.join(static_dir, "assets"), exist_ok=True)

app.mount(
    "/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets"
)


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    # Try to serve requested file
    file_path = os.path.join(static_dir, full_path)
    if os.path.isfile(file_path):
        return FileResponse(file_path)
    # SPA Fallback
    return FileResponse(os.path.join(static_dir, "index.html"))


if __name__ == "__main__":
    uvicorn.run("src.api.main:app", host="0.0.0.0", port=8000, reload=True)
