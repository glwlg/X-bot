import asyncio
import json
import sys
import os

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))

from core.tool_registry import tool_registry
from core.prompts import DEFAULT_SYSTEM_PROMPT, MEMORY_MANAGEMENT_GUIDE
from core.config import MCP_MEMORY_ENABLED


async def main():
    print("--- Context Size Analysis ---")

    import datetime
    from core.skill_loader import skill_loader

    current_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S %A")

    # Inject Skill Awareness - User Feedback Optimization
    # Only inject skill_manager details to save context and encourage dynamic lookup
    skill_mgr = skill_loader.get_skill("skill_manager")
    skill_instruction = ""

    if skill_mgr:
        skill_instruction = (
            f"\n\n【系统核心能力】\n"
            f"你不仅仅是一个聊天机器人，你拥有完整的技能管理系统。\n"
            f"{skill_mgr['description']}\n"
        )

    system_instruction = DEFAULT_SYSTEM_PROMPT
    system_instruction += skill_instruction
    system_instruction += "\n⚠️ **提示**：系统可能安装了其他数百个技能。如果你需要特定的能力（如绘制图表、Docker管理等），请务必先调用 `skill_manager` 的 `search_skills` 或 `list_skills` 来查找，而不是假设自己不能做。"

    if MCP_MEMORY_ENABLED:
        # Use memory guide if enabled, but we avoid eager connection
        system_instruction += "\n\n" + MEMORY_MANAGEMENT_GUIDE

    # Append dynamic time context
    system_instruction += f"\n\n【当前系统时间】: {current_time_str}"

    print(system_instruction)
    print(f"System Prompt Length: {len(system_instruction)} chars")


if __name__ == "__main__":
    asyncio.run(main())
