import os
import pandas as pd
from typing import Dict, Any
from core.platform.models import UnifiedContext


async def execute(ctx: UnifiedContext, params: dict, runtime=None) -> Dict[str, Any]:
    """
    执行 xlsx skill 操作。

    Args:
        ctx: 统一上下文
        params: 参数字典，包含:
            - file_path: Excel 文件路径
            - action: 'analyze' (默认) 获取文件元数据
    """
    file_path = params.get("file_path")
    action = params.get("action", "analyze")

    if not file_path:
        return {
            "text": "📊 XLSX Skill 已就绪。请提供 `file_path` 参数来分析 Excel 文件，或使用我来生成 Excel 操作的 Python 代码。",
            "ui": {},
        }

    if not os.path.exists(file_path):
        return {"text": f"❌ 错误: 文件不存在: {file_path}", "ui": {}}

    try:
        if action == "analyze":
            # 使用 pandas 进行基础分析
            xl = pd.ExcelFile(file_path)
            sheet_names = xl.sheet_names
            file_name = os.path.basename(file_path)

            result_text = (
                f"🔇🔇🔇📊 **Excel 文件分析结果**\n\n"
                f"**文件名**: {file_name}\n"
                f"**Sheet 数量**: {len(sheet_names)}\n"
                f"**Sheet 列表**: {', '.join(sheet_names)}"
            )

            return {"text": result_text, "ui": {}}
        else:
            return {"text": f"❌ 不支持的操作: {action}", "ui": {}}

    except Exception as e:
        return {"text": f"❌ 读取 Excel 文件时出错: {str(e)}", "ui": {}}


def register_handlers(adapter_manager):
    pass
