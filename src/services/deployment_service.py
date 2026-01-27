import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Tuple, List, Optional
from pathlib import Path

from core.config import gemini_client, GEMINI_MODEL, DOWNLOAD_DIR
from google.genai import types

logger = logging.getLogger(__name__)

class DockerDeploymentService:
    """
    Service to handle automatic Docker deployment from GitHub repositories.
    """
    
    def __init__(self):
        self.work_base = Path(DOWNLOAD_DIR) / "deployment_staging"
        self.work_base.mkdir(parents=True, exist_ok=True)

    async def deploy_repository(self, repo_url: str, update_callback=None, progress_callback=None) -> Tuple[bool, str]:
        """
        Orchestrate the deployment process.
        
        Args:
            repo_url: GitHub repository URL.
            update_callback: Async function to send phase updates (msg: str).
            progress_callback: Async function to send command logs (msg: str).
            
        Returns:
            (success, message)
        """
        repo_name = repo_url.split("/")[-1].replace(".git", "")
        # Use fixed directory name to avoid duplicates
        deploy_dir_name = repo_name
        local_path = self.work_base / deploy_dir_name
        
        try:
            # 1. Update/Clone Repository
            if local_path.exists():
                 if update_callback: await update_callback(f"🔄 正在更新仓库: {repo_name}...")
                 if not await self._update_repository(local_path):
                     if update_callback: await update_callback(f"⚠️ 更新失败，尝试重新克隆...")
                     shutil.rmtree(local_path, ignore_errors=True)
                     if not await self._clone_repository(repo_url, local_path):
                         return False, "❌ Git Clone 失败，请检查 URL 或网络。"
            else:
                if update_callback: await update_callback(f"⬇️ 正在克隆仓库: {repo_name}...")
                if not await self._clone_repository(repo_url, local_path):
                    return False, "❌ Git Clone 失败，请检查 URL 是否正确或仓库是否公开。"

            # 2. Analyze Context
            if update_callback: await update_callback("🧐 正在分析部署文档...")
            context, config_dir = self._analyze_context(local_path)
            if not context or not config_dir:
                return False, "❌ 未找到 docker-compose.yml, Dockerfile 或 README.md，无法自动部署。"

            # 3. Generate Plan via AI
            if update_callback: await update_callback("🤖 AI 正在生成部署计划...")
            
            # Additional prompt instructions could be added here if needed, 
            # but we rely on conflict handling below.
            plan = await self._generate_plan(repo_name, context, working_dir=config_dir)
            if not plan:
                return False, "❌ AI 无法生成有效的部署计划。"
                
            logger.info(f"Deployment Plan for {repo_name}: {plan}")

            # 4. Execute Deployment with Retry for Port Conflicts
            if update_callback: await update_callback(f"🚀 开始执行部署命令...\nCommands:\n" + "\n".join(plan['commands']))
            
            max_retries = 3
            for attempt in range(max_retries):
                success, execution_log = await self._execute_commands(plan['commands'], cwd=config_dir, progress_callback=progress_callback)
                
                if success:
                    break
                
                # Check for port conflict
                import re
                # "Bind for 0.0.0.0:5000 failed"
                match = re.search(r"Bind for \d+\.\d+\.\d+\.\d+:(\d+) failed", execution_log)
                if match and attempt < max_retries - 1:
                    conflict_port = match.group(1)
                    new_port = self._find_free_port()
                    if update_callback: 
                        await update_callback(f"⚠️ 检测到端口 {conflict_port} 冲突，正在自动切换到 {new_port} 并重试 ({attempt+1}/{max_retries})...")
                    
                    # Try to patch docker-compose.yml
                    # We assume standard syntax:  - "5000:5000" or - 5000:5000
                    # We want to replace the host port (left side)
                    patched = self._patch_docker_compose_port(config_dir, conflict_port, str(new_port))
                    if not patched:
                        return False, f"❌ 端口 {conflict_port} 冲突，且自动修复失败。"
                else:
                    return False, f"❌ 部署命令执行失败:\n\n{execution_log}"

            # 5. Get Access Info
            if update_callback: await update_callback("🔍 正在获取访问地址...")
            access_info = await self._get_access_info(local_path)
            
            return True, f"✅ 部署成功!\n\n{access_info}"

        except Exception as e:
            logger.error(f"Deployment failed: {e}", exc_info=True)
            return False, f"❌ 部署过程中发生未知错误: {str(e)}"
        finally:
            pass

    async def _update_repository(self, local_path: Path) -> bool:
        """Git pull existing repository."""
        try:
            # Reset hard to avoid conflicts
            subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=str(local_path), check=False)
            process = await asyncio.create_subprocess_exec(
                "git", "pull", "--rebase",
                cwd=str(local_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            return process.returncode == 0
        except Exception as e:
            logger.error(f"Git update error: {e}")
            return False

    async def _clone_repository(self, repo_url: str, local_path: Path) -> bool:
        try:
            process = await asyncio.create_subprocess_exec(
                "git", "clone", "--depth", "1", repo_url, str(local_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            return process.returncode == 0
        except Exception as e:
            logger.error(f"Git clone error: {e}")
            return False

    def _find_free_port(self) -> int:
        """Find a free port on the host (randomly selected from 10000-60000)."""
        import random, socket
        while True:
            port = random.randint(10000, 60000)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Check if port is in use
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            if result != 0: # 0 means connected (open), so != 0 means closed (free)
                return port
    
    def _patch_docker_compose_port(self, config_dir: Path, old_port: str, new_port: str) -> bool:
        """Replace host port in docker-compose.yml."""
        try:
            # Check common names
            candidates = ["docker-compose.yml", "docker-compose.yaml"]
            target = None
            for fname in candidates:
                if (config_dir / fname).exists():
                    target = config_dir / fname
                    break
            
            if not target: return False
            
            content = target.read_text(encoding="utf-8")
            
            # Simple regex replacement for host port mapping
            # Matches:  - "5000:5000"  or - 5000:5000
            # Be careful not to replace container port if identical, but usually we replace the first occurrence
            # Regex: (whitespace)(-?)(whitespace?)(['"]?)OLD_PORT:
            import re
            # Only match if followed by a colon (host side)
            # This risks replacing container port if it's "80:80", but better than nothing
            # More precise: match start of string or spaces or dash
            # We replace ` old_port:` with ` new_port:`
            
            # Pattern: (indentation - )(quote?)old_port:(container_port)
            pattern = re.compile(rf'(\s-\s*["\']?){old_port}:')
            
            if not pattern.search(content):
                # Maybe it is defined as "5000:5000" in a single line
                return False
                
            new_content = pattern.sub(rf'\g<1>{new_port}:', content)
            target.write_text(new_content, encoding="utf-8")
            return True
        except Exception as e:
            logger.error(f"Failed to patch docker-compose: {e}")
            return False



    def _analyze_context(self, local_path: Path) -> Tuple[str, Optional[Path]]:
        """
        Collect relevant file contents for AI analysis.
        Recursively searches for docker-compose.yml or Dockerfile.
        
        Returns:
            (context_string, path_to_config_dir)
        """
        context = []
        config_dir = local_path
        
        # Priority: docker-compose in root -> Dockerfile in root -> recursive search
        # 1. Check root first
        root_files = {f.name for f in local_path.iterdir()}
        if "docker-compose.yml" in root_files or "docker-compose.yaml" in root_files:
            target_files = ["docker-compose.yml", "docker-compose.yaml", "Dockerfile", "README.md", "README.zh-CN.md", ".env.example"]
        elif "Dockerfile" in root_files:
            target_files = ["Dockerfile", "README.md", "README.zh-CN.md", "package.json", "requirements.txt"]
        else:
            # 2. Recursive search (max depth 2)
            found_compose = list(local_path.rglob("docker-compose.y*ml"))
            found_dockerfile = list(local_path.rglob("Dockerfile"))
            
            if found_compose:
                config_dir = found_compose[0].parent
                target_files = ["docker-compose.yml", "docker-compose.yaml", "Dockerfile", "README.md", ".env.example"]
            elif found_dockerfile:
                config_dir = found_dockerfile[0].parent
                target_files = ["Dockerfile", "README.md", "package.json", "requirements.txt"]
            else:
                # No config found
                return "", None
        
        # Read files from the determined config directory (or root if not found deeper)
        # Also include root README if we are in a subdir, as it might contain global instructions
        files_to_read = set(target_files)
        
        # Always try to read root README if we are deep
        if config_dir != local_path:
             try:
                 readme = local_path / "README.md"
                 if readme.exists():
                     context.append(f"--- ROOT/README.md ---\n{readme.read_text(encoding='utf-8', errors='replace')[:5000]}\n")
             except: pass

        for fname in files_to_read:
            fpath = config_dir / fname
            if fpath.exists():
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                    context.append(f"--- {fname} ---\n{content[:5000]}\n")
                except Exception as e:
                    logger.warning(f"Error reading {fname}: {e}")
                    
        return "\n".join(context), config_dir

    async def _generate_plan(self, repo_name: str, context: str, working_dir: Path) -> Optional[dict]:
        """Ask AI to generate a deployment plan (commands)."""
        prompt = f"""
You are a DevOps expert. Your task is to deploy the following repository using Docker.
Repo Name: {repo_name}
Working Directory: {working_dir} (All commands will be executed here)

Repository Context (Config files and README):
{context}

Based on the context, determine the correct commands to deploy this application.
Prefer `docker compose` (V2) if available.
If `docker-compose.yml` exists, the command is likely `docker compose up -d --build`.
If only `Dockerfile` exists, you need to `docker build` and `docker run`. Be sure to map ports if mentioned in README.

Output a JSON object ONLY, with this structure:
{{
  "commands": ["command1", "command2"],
  "main_service_name": "name of the main service in docker-compose or container name",
  "reasoning": "explanation"
}}

Rules:
1. Commands will be executed in {working_dir}.
2. Return RAW JSON. No markdown formatting.
3. Ensure unsafe commands are not included.
4. If `docker build` is used, ensure the build context is correct (usually `.` if Dockerfile is in CWD).
5. **CRITICAL**: ALWAYS use `docker compose` (with space), NEVER use `docker-compose` (with hyphen).
        """
        
        try:
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            import json
            return json.loads(response.text)
        except Exception as e:
            logger.error(f"AI Plan Generation Failed: {e}")
            return None

    async def _execute_commands(self, commands: List[str], cwd: Path, progress_callback=None) -> Tuple[bool, str]:
        """Execute a list of shell commands with real-time log streaming."""
        full_log = []
        
        for cmd in commands:
            cmd_start_msg = f"> {cmd}"
            full_log.append(cmd_start_msg)
            if progress_callback:
                await progress_callback(cmd_start_msg)
            
            try:
                process = await asyncio.create_subprocess_shell(
                    cmd,
                    cwd=str(cwd),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT  # Merge stderr into stdout
                )
                
                # Buffer for progress updates
                last_update_time = 0
                accumulated_lines = []
                
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    
                    decoded_line = line.decode("utf-8", errors="replace").rstrip()
                    full_log.append(decoded_line)
                    accumulated_lines.append(decoded_line)
                    
                    # Throttled update: every 2 seconds or 20 lines
                    current_time = asyncio.get_event_loop().time()
                    if progress_callback and (current_time - last_update_time > 2.0 or len(accumulated_lines) > 20):
                        # Join recent lines
                        update_msg = "\n".join(accumulated_lines)
                        await progress_callback(update_msg)
                        accumulated_lines = []
                        last_update_time = current_time
                
                # Final flush
                if progress_callback and accumulated_lines:
                     await progress_callback("\n".join(accumulated_lines))

                await process.wait()
                
                if process.returncode != 0:
                    return False, "\n".join(full_log)
                    
            except Exception as e:
                error_msg = f"Execution Error: {e}"
                full_log.append(error_msg)
                if progress_callback: await progress_callback(error_msg)
                return False, "\n".join(full_log)
                
        return True, "\n".join(full_log)

    async def _get_access_info(self, cwd: Path) -> str:
        """Determine potential access URLs."""
        # 1. Get Host LAN IP
        host_ip = "127.0.0.1"
        try:
            # Trick to get host IP from inside container: run a container on host net
            cmd = "docker run --rm --net=host alpine ip route get 1.1.1.1 | awk '{print $7}'"
            process = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            ip = stdout.decode().strip()
            if ip:
                host_ip = ip
        except Exception as e:
            logger.error(f"Failed to get host IP: {e}")

        # 2. Get Ports via docker compose ps or docker ps
        # We try to parse running containers in this folder
        ports = []
        try:
             # Use docker compose ps if possible to find public ports
            cmd = "docker compose ps --format json"
            process = await asyncio.create_subprocess_shell(
                cmd, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            output = stdout.decode().strip()
            
            import json
            # docker compose ps output depends on version. V2 outputs a json array provided --format json is supported.
            # If not supported, we might fail.
            # Fallback: scan docker ps for containers with this project label usually?
            # Actually simpler: if we just ran `docker-compose up`, we can grep port mappings.
            
            if output:
                try:
                    # Output might be a list of JSON objects (one per line or array)
                    # Checking common formats.
                    # Sometimes it's json lines.
                    services = []
                    if output.startswith("["):
                        services = json.loads(output)
                    else:
                        for line in output.splitlines():
                            if line.strip(): services.append(json.loads(line))
                            
                    for svc in services:
                        # Extract ports
                        # Structure varies. Look for "Publishers" or "Ports"
                        if "Publishers" in svc:
                             for pub in svc["Publishers"]:
                                 if "PublishedPort" in pub and pub["PublishedPort"] > 0:
                                     ports.append(pub["PublishedPort"])
                except:
                   logger.warning("Failed to parse docker compose ps json.")
                
        except Exception as e:
           logger.error(f"Failed to get ports info: {e}")

        if not ports:
            return f"无法自动检测端口，请尝试访问宿主机 IP: {host_ip}"
            
        urls = [f"http://{host_ip}:{p}" for p in ports]
        return "请尝试访问:\n" + "\n".join(urls)

docker_deployment_service = DockerDeploymentService()
