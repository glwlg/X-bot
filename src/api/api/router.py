from fastapi import APIRouter
from api.api.endpoints import health, rss, scheduler, monitor, watchlist

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(rss.router, prefix="/rss", tags=["rss"])
api_router.include_router(scheduler.router, prefix="/scheduler", tags=["scheduler"])
api_router.include_router(monitor.router, prefix="/monitor", tags=["monitor"])
api_router.include_router(watchlist.router, prefix="/watchlist", tags=["watchlist"])
