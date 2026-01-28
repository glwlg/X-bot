
import asyncio
import json
import sys
import os
from datetime import datetime

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))

from core.tool_registry import tool_registry
from core.prompts import DEFAULT_SYSTEM_PROMPT, MEMORY_MANAGEMENT_GUIDE
from core.config import MCP_MEMORY_ENABLED

async def main():
    output_file = "prompt_dump.md"
    
    # 1. System Prompt
    sys_prompt = DEFAULT_SYSTEM_PROMPT
    if MCP_MEMORY_ENABLED:
        sys_prompt = MEMORY_MANAGEMENT_GUIDE
    
    # 2. Tools
    tools = tool_registry.get_all_tools()
    tools_data = []
    for t in tools:
        # Manually extract fields to avoid serialization issues
        params_schema = "object" # simplified
        if hasattr(t, 'parameters') and t.parameters:
            # Try to get properties if available
            if hasattr(t.parameters, 'properties'):
                params_schema = str(t.parameters.properties)
            else:
                params_schema = str(t.parameters)

        tools_data.append({
            "name": t.name,
            "description": t.description,
            "parameters": params_schema
        })
    
    tools_json = json.dumps(tools_data, indent=2, ensure_ascii=False)
    
    # 3. Simulated History (20 items = 10 turns)
    history = []
    for i in range(10):
        history.append({"role": "user", "parts": [f"This is user message #{i+1}. It might be a bit longer than hello."]})
        history.append({"role": "model", "parts": [f"This is AI response #{i+1}. I am helpful and polite."]})
    
    history_json = json.dumps(history, indent=2, ensure_ascii=False)
    
    # Stats
    len_sys = len(sys_prompt)
    len_tools = len(tools_json)
    len_hist = len(history_json)
    total = len_sys + len_tools + len_hist
    
    # Write Report
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"# Prompt Context Analysis\n\n")
        f.write(f"**Generated at**: {datetime.now()}\n")
        f.write(f"**Total Characters**: {total}\n")
        f.write(f"**MCP Memory Enabled**: {MCP_MEMORY_ENABLED}\n\n")
        
        f.write(f"## Breakdown\n")
        f.write(f"- System Prompt: {len_sys} ({len_sys/total*100:.1f}%)\n")
        f.write(f"- Tools Definition: {len_tools} ({len_tools/total*100:.1f}%)\n")
        f.write(f"- History (20 msgs): {len_hist} ({len_hist/total*100:.1f}%)\n\n")
        
        f.write(f"## 1. System Prompt\n")
        f.write(f"```text\n{sys_prompt}\n```\n\n")
        
        f.write(f"## 2. Tools Definitions\n")
        f.write(f"```json\n{tools_json}\n```\n\n")
        
        f.write(f"## 3. Simulated History\n")
        f.write(f"```json\n{history_json}\n```\n")

    print(f"Dumped context to {output_file}. Total size: {total} chars.")

if __name__ == "__main__":
    asyncio.run(main())
