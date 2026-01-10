import os
import logging
from mcp import StdioServerParameters
from mcp.types import CallToolRequest, CallToolRequestParams, CallToolResult
from .base import MCPServerBase

logger = logging.getLogger(__name__)

class MemoryMCPServer(MCPServerBase):
    """
    Knowledge Graph Memory MCP Server (Per User)
    
    Uses @modelcontextprotocol/server-memory
    Runs locally via npx (since usage is inside a container already)
    """

    def __init__(self, user_id: int = None):
        super().__init__()
        self.user_id = user_id

    # 使用 Node 18 镜像来运行 npx
    DOCKER_IMAGE = "node:18-slim"
    
    @property
    def server_name(self) -> str:
        return "memory"
    
    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """
        覆盖基类方法，绕过 MCP SDK 的严格 Schema 校验。
        
        @modelcontextprotocol/server-memory 返回的数据包含额外的 'type' 字段，
        会导致 SDK 的 Pydantic 校验失败。此处直接使用 send_request 调用。
        """
        if not self.is_connected:
            await self.connect()
            
        logger.info(f"[MCP:{self.server_name}] Calling tool (raw): {tool_name} with args: {arguments}")
        
        if not self.session:
            raise RuntimeError("Session not initialized")

        # 使用 send_request 直接发送 tools/call 请求，绕过 _validate_tool_result
        # The result is expected to be a CallToolResult model or dict
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=tool_name, arguments=arguments)
        )
        result = await self.session.send_request(request, CallToolResult)
        
        # result 应该是一个包含 content 的对象 (CallToolResult model)
        # 即使 send_request 返回的是 model，只要不调用 session.call_tool 里的 _validate_tool_result 就可以
        
        logger.debug(f"[MCP:{self.server_name}] Tool result (raw): {result}")
        
        # 兼容性处理：尝试获取 content
        if hasattr(result, 'content'):
            return result.content
        elif isinstance(result, dict) and 'content' in result:
            return result['content']
            
        return result

    def get_server_params(self) -> StdioServerParameters:
        """返回启动参数"""
        
        if not self.user_id:
            raise ValueError("MemoryMCPServer requires user_id for physical isolation.")

        # 用户专属记忆文件
        # 路径: /app/data/users/{user_id}/memory.json
        user_data_dir = f"/app/data/users/{self.user_id}"
        memory_file_path = f"{user_data_dir}/memory.json"
            
        # 确保目录存在
        import os
        os.makedirs(user_data_dir, exist_ok=True)
        
        # 使用 npx 直接在本地运行 (Assuming 'npm' is installed in Dockerfile)
        # 这样可以直接访问 /app/data 挂载点，无需 DIND 挂载复杂性
        return StdioServerParameters(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-memory"],
            env={
                "PATH": os.environ.get("PATH"), # 保留 PATH 以找到 npx
                "MEMORY_FILE_PATH": memory_file_path
            }
        )

def register_memory_server():
    """注册 Memory 服务到 MCPManager"""
    from .manager import mcp_manager
    mcp_manager.register_server_class("memory", MemoryMCPServer)
