from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[4]
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import pandas as pd
from core.platform.models import UnifiedContext
from core.skill_cli import (
    add_common_arguments,
    merge_params,
    prepare_default_env,
    run_execute_cli,
)

prepare_default_env(REPO_ROOT)


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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="XLSX skill CLI bridge.",
    )
    add_common_arguments(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="Analyze an Excel file")
    analyze_parser.add_argument("file_path", help="Path to the Excel file")
    return parser


def _params_from_args(args: argparse.Namespace) -> dict:
    command = str(args.command or "").strip().lower()
    if command == "analyze":
        return merge_params(
            args,
            {
                "action": "analyze",
                "file_path": str(args.file_path or "").strip(),
            },
        )
    raise SystemExit(f"unsupported command: {command}")


async def _run() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return await run_execute_cli(execute, args=args, params=_params_from_args(args))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
