
import asyncio
import sys
import os
import importlib

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))

async def measure_mode(mode_name):
    # Set env var
    os.environ["SKILL_INJECTION_MODE"] = mode_name
    
    # Reload config and tool_registry to pick up changes
    from core import config, tool_registry
    importlib.reload(config)
    importlib.reload(tool_registry)
    
    tr = tool_registry.tool_registry # singleton might need refresh or re-instantiation
    # Actually tool_registry.py initializes 'tool_registry = ToolRegistry()' at end
    # so reload(tool_registry) re-creates the instance.
    
    tools = tr.get_all_tools()
    
    # Find call_skill
    st = next((t for t in tools if t.name == "call_skill"), None)
    desc_len = len(st.description) if st else 0
    
    # Estimate total tools JSON size
    tools_str = str([str(t) for t in tools])
    
    print(f"--- Mode: {mode_name} ---")
    print(f"call_skill description length: {desc_len}")
    print(f"Total Tools Payload: {len(tools_str)} chars")
    # print(f"Preview: {st.description[:100]}...")
    print("")

async def main():
    print("Comparing Prompt Optimization Modes...\n")
    
    await measure_mode("full")
    await measure_mode("compact")
    await measure_mode("search_first")

if __name__ == "__main__":
    asyncio.run(main())
