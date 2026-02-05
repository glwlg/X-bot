import os
import asyncio
from core.platform.models import UnifiedContext
from typing import AsyncGenerator, Dict, Any

# 定义允许操作的根目录名称
ALLOWED_ROOTS = ["data", "downloads"]


def is_safe_path(path: str) -> bool:
    """
    安全检查：确保路径位于允许的目录(data/ 或 downloads/)下
    """
    try:
        # 获取目标路径的绝对路径
        abs_path = os.path.abspath(path)
        cwd = os.getcwd()

        for root_name in ALLOWED_ROOTS:
            # 构建允许目录的绝对路径
            allowed_path = os.path.abspath(os.path.join(cwd, root_name))
            # 检查路径前缀
            if abs_path.startswith(allowed_path):
                return True
        return False
    except Exception:
        return False


def _read_file_sync(path: str) -> str:
    """同步读取文件"""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_file_sync(path: str, content: str) -> None:
    """同步写入文件，自动创建目录"""
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _list_dir_sync(path: str) -> str:
    """同步列出目录内容，区分文件和文件夹"""
    items = os.listdir(path)
    if not items:
        return "(目录为空)"

    dirs = []
    files = []
    for item in items:
        full_path = os.path.join(path, item)
        if os.path.isdir(full_path):
            dirs.append(f"📁 {item}/")
        else:
            files.append(f"📄 {item}")

    # 排序：文件夹优先，然后按名称排序
    dirs.sort()
    files.sort()
    return "\n".join(dirs + files)


async def execute(
    ctx: UnifiedContext, params: dict
) -> AsyncGenerator[str | Dict[str, Any], None]:
    """
    执行文件读写及列表技能
    """
    action = params.get("action")
    path = params.get("path")
    content = params.get("content")

    # 1. 基础参数校验
    if not action or not path:
        yield {"text": "🔇🔇🔇❌ 错误: 缺少必要参数 `action` 或 `path`。"}
        return

    # 2. 安全路径校验 (Rule #2)
    if not is_safe_path(path):
        yield {
            "text": f"🔇🔇🔇❌ 安全警告: 禁止访问路径 `{path}`。\n为了系统安全，仅允许操作 `data/` 或 `downloads/` 目录下的文件。"
        }
        return

    try:
        if action == "read":
            yield f"正在读取文件: `{path}`..."

            if not os.path.exists(path):
                yield {"text": f"❌ 错误: 文件 `{path}` 不存在。"}
                return

            if not os.path.isfile(path):
                yield {"text": f"❌ 错误: `{path}` 不是一个文件。"}
                return

            file_content = await asyncio.to_thread(_read_file_sync, path)

            if not file_content:
                file_content = "(文件内容为空)"

            yield {
                "text": f"🔇🔇🔇📄 **文件内容 ({path})**:\n\n```text\n{file_content}\n```"
            }

        elif action == "write":
            if content is None:
                yield {"text": "🔇🔇🔇❌ 错误: 写入操作需要提供 `content` 参数。"}
                return

            yield f"正在写入文件: `{path}`..."

            await asyncio.to_thread(_write_file_sync, path, content)

            yield {"text": f"🔇🔇🔇✅ 成功写入文件: `{path}`"}

        elif action == "list":
            yield f"正在扫描目录: `{path}`..."

            if not os.path.exists(path):
                yield {"text": f"🔇🔇🔇❌ 错误: 路径 `{path}` 不存在。"}
                return

            if not os.path.isdir(path):
                yield {"text": f"🔇🔇🔇❌ 错误: `{path}` 不是一个目录。"}
                return

            dir_content = await asyncio.to_thread(_list_dir_sync, path)

            yield {"text": f"🔇🔇🔇📂 **目录列表 ({path})**:\n\n{dir_content}"}

        else:
            yield {
                "text": f"❌ 未知操作: `{action}`。仅支持 `read`, `write` 或 `list`。"
            }

    except PermissionError:
        yield {"text": f"❌ 权限错误: 无法访问路径 `{path}`。"}
    except Exception as e:
        yield {"text": f"❌ 系统错误: {str(e)}"}


def register_handlers(adapter_manager: Any):
    pass
