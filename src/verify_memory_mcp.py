
import asyncio
import logging
import sys
import os

# 将 src 目录添加到 Python 路径
sys.path.append(os.path.join(os.getcwd(), 'src'))

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from mcp_client.manager import mcp_manager
from mcp_client.memory import MemoryMCPServer

async def verify_memory():
    """验证 Memory MCP 功能"""
    
    # 注册 Memory 服务
    logger.info("Registering Memory MCP server...")
    # Register the server implementation
    mcp_manager.register_server_class("memory", MemoryMCPServer)
    
    logger.info("Connecting to memory MCP server (user_id=99999)...")
    # 获取特定用户的实例
    try:
        server = await mcp_manager.get_server("memory", user_id=99999)
    except Exception as e:
        logger.error(f"Connection failed: {e}")
        return

    logger.info("Connected!")
        
    server_type = "memory" # Keep this for subsequent calls
    
    try:
        # 2. 创建实体 (写入测试)
        logger.info("Creating test entity...")
        test_entities = [{
            "name": "TestUser_123",
            "entityType": "Person",
            "observations": ["Created during verification test", "Likes Python"]
        }]
        
        await mcp_manager.call_tool(
            server_type, 
            "create_entities", 
            {"entities": test_entities}
        )
        logger.info("Entity created successfully.")
        
        # 3. 搜索测试 (跳过 read_graph 因为它返回的数据经常无法通过 schema 校验)
        logger.info("Reading knowledge graph (via search_nodes)...")
        
        # 搜索刚刚创建的实体
        search_results = await mcp_manager.call_tool(
            server_type,
            "search_nodes",
            {"query": "TestUser_123"}
        )
        logger.info(f"Search results type: {type(search_results)}")
        
        found = False
        if isinstance(search_results, list):
            for item in search_results:
                if hasattr(item, 'text'):
                    logger.info(f"Result content: {item.text}")
                    if "TestUser_123" in item.text:
                        found = True
        
        if found:
            logger.info("✅ Verification Successful: Entity found via search.")
        else:
            logger.error("❌ Verification Failed: Entity NOT found via search.")
            
    except Exception as e:
        logger.error(f"Verification failed: {e}")
        import traceback
        traceback.print_exc()
        
    except Exception as e:
        logger.error(f"Verification failed: {e}")
    finally:
        # 清理连接
        await mcp_manager.cleanup_all()

if __name__ == "__main__":
    asyncio.run(verify_memory())
