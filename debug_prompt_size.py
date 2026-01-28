
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
    
    # 1. System Prompt
    sys_prompt = DEFAULT_SYSTEM_PROMPT
    if MCP_MEMORY_ENABLED:
        sys_prompt = MEMORY_MANAGEMENT_GUIDE
    
    print(f"System Prompt Length: {len(sys_prompt)} chars")
    
    # 2. Tools
    tools = tool_registry.get_all_tools()
    
    # Convert tools to dictionary format (simulating JSON payload)
    # google.genai.types.FunctionDeclaration -> dict
    tools_dict = []
    for t in tools:
        # We need to manually serialize or use their to_dict method if available
        # The SDK objects usually have _to_dict or similar, or we just estimate
        t_json = {
            "name": t.name,
            "description": t.description,
            "parameters": "...", # Schema size estimate
        }
        # A more accurate way is to dump the whole schema structure
        # But for quick estimate, let's just dump the string rep
        tools_dict.append(str(t))

    tools_str = str(tools_dict)
    print(f"Tools Definition Length (Approx): {len(tools_str)} chars")
    
    # Detail on 'call_skill'
    skill_tool = next((t for t in tools if t.name == "call_skill"), None)
    if skill_tool:
        print(f"  - 'call_skill' description length: {len(skill_tool.description)}")
        print(f"  - 'call_skill' description preview:\n{skill_tool.description[:200]}...")

    total = len(sys_prompt) + len(tools_str)
    print(f"Total Static Overhead: ~{total} chars")

if __name__ == "__main__":
    asyncio.run(main())
