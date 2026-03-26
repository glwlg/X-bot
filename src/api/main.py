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
from api.core.database import get_session_maker
from api.services.bootstrap_admin import ensure_bootstrap_admin
from core.runtime_config_store import runtime_config_store


def _allowed_origins() -> list[str]:
    configured = str(os.getenv("CORS_ALLOW_ORIGINS", "")).strip()
    origins: list[str] = []
    if configured:
        origins.extend(
            [item.strip() for item in configured.split(",") if item.strip()]
        )
    runtime_origins = (
        ((runtime_config_store.read().get("cors") or {}).get("allowed_origins"))
        or []
    )
    if isinstance(runtime_origins, list):
        origins.extend([str(item).strip() for item in runtime_origins if str(item).strip()])
    if not origins:
        origins = [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
    deduped: list[str] = []
    for origin in origins:
        if origin not in deduped:
            deduped.append(origin)
    return deduped


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 初始化数据库
    print("Initializing database...")
    await init_db()
    session_maker = get_session_maker()
    async with session_maker() as session:
        await ensure_bootstrap_admin(session, reason="startup")
        await session.commit()
    yield
    print("Shutting down...")


app = FastAPI(
    title="Template Backend",
    description="FastAPI Backend Template with Auth and DB",
    version="0.1.0",
    lifespan=lifespan,
    redirect_slashes=False,
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
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

SPA_HTML_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}


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
            return HTMLResponse(content=html, headers=SPA_HTML_HEADERS)
        except Exception:
            pass

    return FileResponse(index_path, headers=SPA_HTML_HEADERS)


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
