"""
自定义异常类和全局异常处理器

提供统一的异常处理机制，确保API返回一致的错误格式。
"""
from typing import Any, Dict, Optional
from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
import logging

logger = logging.getLogger(__name__)


class AppException(Exception):
    """应用基础异常类"""
    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code or "INTERNAL_ERROR"
        self.details = details or {}
        super().__init__(self.message)


class DatabaseException(AppException):
    """数据库相关异常"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="DATABASE_ERROR",
            details=details
        )


class ValidationException(AppException):
    """验证异常"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            error_code="VALIDATION_ERROR",
            details=details
        )


class NotFoundException(AppException):
    """资源未找到异常"""
    def __init__(self, message: str = "资源不存在", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=status.HTTP_404_NOT_FOUND,
            error_code="NOT_FOUND",
            details=details
        )


class ConfigurationException(AppException):
    """配置错误异常"""
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="CONFIGURATION_ERROR",
            details=details
        )


# 异常处理器
async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """应用异常处理器"""
    logger.error(
        f"AppException: {exc.message}",
        extra={
            "error_code": exc.error_code,
            "status_code": exc.status_code,
            "path": request.url.path,
            "details": exc.details
        }
    )
    
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.error_code,
                "message": exc.message,
                "details": exc.details
            }
        }
    )


def _sanitize_error(error: Dict[str, Any]) -> Dict[str, Any]:
    """清理 Pydantic 错误对象，确保可以 JSON 序列化"""
    sanitized = {
        "type": error.get("type"),
        "loc": error.get("loc"),
        "msg": error.get("msg"),
    }
    # ctx 可能包含不可序列化的对象（如 ValueError），只保留可序列化的部分
    if "ctx" in error:
        ctx = error["ctx"]
        sanitized_ctx = {}
        for key, value in ctx.items():
            try:
                # 尝试将值转换为字符串，如果是异常对象则获取其消息
                if isinstance(value, Exception):
                    sanitized_ctx[key] = str(value)
                else:
                    # 测试是否可序列化
                    import json
                    json.dumps(value)
                    sanitized_ctx[key] = value
            except (TypeError, ValueError):
                sanitized_ctx[key] = str(value)
        if sanitized_ctx:
            sanitized["ctx"] = sanitized_ctx
    return sanitized


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Pydantic 验证异常处理器"""
    logger.warning(
        f"Validation error: {exc.errors()}",
        extra={"path": request.url.path}
    )
    
    # 清理错误对象，确保可以 JSON 序列化
    sanitized_errors = [_sanitize_error(err) for err in exc.errors()]
    
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "请求数据验证失败",
                "details": {
                    "errors": sanitized_errors
                }
            }
        }
    )


async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """SQLAlchemy 异常处理器"""
    logger.error(
        f"Database error: {str(exc)}",
        extra={"path": request.url.path},
        exc_info=True
    )
    
    # 完整性约束错误（如唯一键冲突）
    if isinstance(exc, IntegrityError):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "error": {
                    "code": "INTEGRITY_ERROR",
                    "message": "数据完整性冲突，可能是重复记录",
                    "details": {}
                }
            }
        )
    
    # 其他数据库错误
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": "DATABASE_ERROR",
                "message": "数据库操作失败",
                "details": {}
            }
        }
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """通用异常处理器（兜底）"""
    logger.error(
        f"Unhandled exception: {str(exc)}",
        extra={"path": request.url.path},
        exc_info=True
    )
    
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "服务器内部错误",
                "details": {}
            }
        }
    )
