
import asyncio
import os
import sys
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)

async def verify():
    # Ensure src is in path
    sys.path.insert(0, os.path.abspath("src"))
    
    from core.evolution_router import evolution_router
    
    print("1. Testing Evolution Router with a simple creation task...")
    request = "Write a python script to calculate SHA256 hash of a text"
    user_id = 12345
    
    # We expect this to trigger "create" strategy
    result = await evolution_router.evolve(request, user_id)
    
    print(f"\nResult:\n{result}\n")
    
    if "新能力已进化" in result or "技能名" in result:
        print("✅ Verification Successful: Router created a new skill!")
    else:
        print("❌ Verification Failed: Router did not create a skill.")

if __name__ == "__main__":
    asyncio.run(verify())
