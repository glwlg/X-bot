import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.api.router import api_router
from src.api.auth.router import router as auth_router
from src.api.core.config import settings
from src.api.core.database import init_db


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


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
