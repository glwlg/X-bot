import uvicorn
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

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


def _serve_spa_html(full_path: str) -> FileResponse | HTMLResponse:
    """Serve index.html with optional accounting PWA overrides."""
    index_path = os.path.join(static_dir, "index.html")

    # Try to serve a real static file first
    file_path = os.path.join(static_dir, full_path)
    if full_path and os.path.isfile(file_path):
        return FileResponse(file_path)

    # Generate Accounting-specific PWA headers on the fly
    if full_path.startswith("accounting"):
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                html = f.read()
            # Swap to accounting manifest
            html = html.replace(
                "/manifest.webmanifest", "/accounting-manifest.webmanifest"
            )
            # Add Apple-specific PWA meta tags
            apple_tags = """
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
  <meta name="apple-mobile-web-app-title" content="智能记账" />
  <link rel="apple-touch-icon" href="/logo.png" />
</head>"""
            html = html.replace("</head>", apple_tags)
            return HTMLResponse(content=html)
        except Exception:
            pass

    return FileResponse(index_path)


@app.exception_handler(StarletteHTTPException)
async def spa_exception_handler(request: Request, exc: StarletteHTTPException):
    """For 404s on non-API paths, serve the SPA index.html (client-side routing)."""
    if exc.status_code == 404 and not request.url.path.startswith("/api/"):
        return _serve_spa_html(request.url.path.lstrip("/"))
    # For API 404s or other HTTP errors, return JSON as normal
    return HTMLResponse(
        content=f'{{"detail":"{exc.detail}"}}',
        status_code=exc.status_code,
        media_type="application/json",
    )


if __name__ == "__main__":
    uvicorn.run("src.api.main:app", host="0.0.0.0", port=8000, reload=True)
