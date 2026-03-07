from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    message: str


@router.get("", response_model=HealthResponse)
async def health_check():
    """
    基础心跳接口
    """
    return HealthResponse(status="ok", message="Service is healthy")
