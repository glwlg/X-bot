from fastapi import APIRouter
from api.api.endpoints import admin, health, monitor, rss, scheduler, setup, watchlist, web_chat

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(rss.router, prefix="/rss", tags=["rss"])
api_router.include_router(scheduler.router, prefix="/scheduler", tags=["scheduler"])
api_router.include_router(monitor.router, prefix="/monitor", tags=["monitor"])
api_router.include_router(watchlist.router, prefix="/watchlist", tags=["watchlist"])
api_router.include_router(web_chat.router, prefix="/web-chat", tags=["web-chat"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(setup.router, prefix="/admin", tags=["admin-setup"])
