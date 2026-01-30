import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Tuple, List, Optional
from pathlib import Path

from core.config import gemini_client, GEMINI_MODEL, DOWNLOAD_DIR, DATA_DIR
from google.genai import types

logger = logging.getLogger(__name__)

class DockerDeploymentService:
    """
    Service to handle automatic Docker deployment from GitHub repositories.
    """
    
    def __init__(self):
        # Use absolute path that matches Host path to ensure volume mounting works
        # User confirmed: /DATA/x-box/data/deployment_staging is mapped to same path inside container
        self.work_base = Path("/DATA/x-box/data/deployment_staging")
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
            if update_callback: await update_callback("🤖 正在生成部署计划...")
            
            # Additional prompt instructions could be added here if needed, 
            # but we rely on conflict handling below.
            plan = await self._generate_plan(repo_name, context, working_dir=config_dir)
            if not plan:
                return False, "❌ 无法生成有效的部署计划。"
                
            logger.info(f"Deployment Plan for {repo_name}: {plan}")

            # 3.5 Patch Volumes for Docker-in-Docker (or Socket binding)
            # The user specified `/DATA/x-box/data` as a mount base.
            # We assume the internal `/app` maps to `/DATA/x-box` on host.
            if update_callback: await update_callback("🔧 正在修正 Docker 挂载路径...")
            self._patch_volumes_for_host(config_dir, repo_name)
            
            # 3.9 Pre-emptive Port Randomization
            # Use random high ports (>20000) to avoid conflicts (Zero-Touch)
            if update_callback: await update_callback("⚙️ 正在优化端口配置 (20000+)...")
            self._randomize_ports(config_dir)

            # 4. Execute Deployment with Retry for Port Conflicts
            if update_callback: await update_callback(f"🚀 开始执行部署命令...\nCommands:\n" + "\n".join(plan['commands']))
            
            max_retries = 3
            for attempt in range(max_retries):
                success, execution_log = await self._execute_commands(plan['commands'], cwd=config_dir, progress_callback=progress_callback)
                
                if success:
                    break
                
                # Check for port conflict
                # Check for port conflict
                import re
                # Patterns:
                # 1. "Bind for 0.0.0.0:5000 failed"
                # 2. "bind: address already in use" (often preceded by port)
                # 3. "driver failed programming external connectivity ... port is already allocated"
                
                conflict_port = None
                
                # Try specific match first
                match = re.search(r"Bind for \d+\.\d+\.\d+\.\d+:(\d+) failed", execution_log)
                if match:
                    conflict_port = match.group(1)
                
                if not conflict_port:
                    # Generic "address already in use" - often hard to find which port from log alone if simple regex
                    # But often it appears as: "Listen tcp :3001: bind: address already in use"
                    match_generic = re.search(r":(\d+): bind: address already in use", execution_log)
                    if match_generic:
                        conflict_port = match_generic.group(1)
                        
                if not conflict_port and ("address already in use" in execution_log or "port is already allocated" in execution_log):
                    # Fallback: Assume the mapping we tried failed.
                    # We need to look at what we tried to deploy.
                    # This is tricky without parsing the compose file again. 
                    # But usually we can guess from context or just try a random new port if we know which service failed.
                    # For now, let's try to parse "Allocated port 3001..." if it exists.
                    pass

                if conflict_port and attempt < max_retries - 1:
                    new_port = self._find_free_port()
                    if update_callback: 
                        await update_callback(f"⚠️ 检测到端口 {conflict_port} 由其他服务占用，正在自动切换到 {new_port} 并重试 ({attempt+1}/{max_retries})...")
                    
                    patched = self._patch_docker_compose_port(config_dir, conflict_port, str(new_port))
                    if not patched:
                        return False, f"❌ 端口 {conflict_port} 冲突，且自动修复失败。"
                else:
                    return False, f"❌ 部署命令执行失败:\n\n{execution_log}"

            # 5. Get Access Info
            if update_callback: await update_callback("🔍 正在获取访问地址...")
            # Use config_dir because that's where docker-compose.yml is
            access_info = await self._get_access_info(config_dir)
            logger.info(f"Detected Access Info: {access_info}")
            
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
        """
        Find a free port on the HOST using netstat/ss.
        Range: 20000-60000 (User requirement).
        """
        import random
        import subprocess
        
        # 1. Get all used ports via netstat or ss
        used_ports = set()
        try:
            # Try netstat first (available via net-tools)
            # -t (tcp) -u (udp) -l (listening) -n (numeric)
            cmd = "netstat -tuln"
            result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            output = result.stdout
            
            if result.returncode != 0:
                 # Fallback to ss (iproute2)
                 cmd = "ss -tuln"
                 result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 output = result.stdout

            # Parse output
            # Example: tcp        0      0 0.0.0.0:8080            0.0.0.0:*               LISTEN
            for line in output.splitlines():
                parts = line.split()
                if len(parts) > 3:
                     # address is usually 4th col (index 3)
                     addr = parts[3]
                     if ":" in addr:
                         port_str = addr.split(":")[-1]
                         if port_str.isdigit():
                             used_ports.add(int(port_str))
        except Exception as e:
            logger.error(f"Failed to check ports via netstat: {e}")
            # Fallback to empty set, rely on try/bind check below
            pass

        # 2. Find a random free port in range
        for _ in range(100):
            port = random.randint(20000, 60000)
            if port not in used_ports:
                 return port
                 
        # If crowded, just return a random one
        return random.randint(20000, 60000)
    
    def _randomize_ports(self, config_dir: Path) -> None:
        """
        Scan docker-compose.yml for low ports (< 20000) and replace them with random high ports.
        """
        try:
            candidates = ["docker-compose.yml", "docker-compose.yaml"]
            target = None
            for fname in candidates:
                if (config_dir / fname).exists():
                    target = config_dir / fname
                    break
            
            if not target: return
            
            content = target.read_text(encoding="utf-8")
            import re
            
            # Regex to find "host:container" port mappings
            # Matches:  - "8080:80"  or  - 8080:80  or  published: 8080
            # We focus on the most common format: spaces/dash/quotes + HOST_PORT + : + CONTAINER_PORT
            # Capture group 1: Prefix, Group 2: Host Port, Group 3: Container Part
            # We only touch Host Port if it's < 20000
            
            def replace_port_match(match):
                prefix = match.group(1)
                host_port_str = match.group(2)
                suffix = match.group(3)
                
                try:
                    port = int(host_port_str)
                    if port < 20000:
                        new_port = self._find_free_port()
                        logger.info(f"Randomizing port: {port} -> {new_port}")
                        return f"{prefix}{new_port}{suffix}"
                except:
                    pass
                return match.group(0) # No change

            # Regex breakdown:
            # (\s-\s*["']?) : Prefix (indent, dash, quote)
            # (\d+)         : Host Port
            # (:\d+)        : :Container Port (suffix)
            # We look for standard `- 80:80` content
            pattern = re.compile(r'(\s-\s*["\']?)(\d+)(:\d+)')
            
            new_content = pattern.sub(replace_port_match, content)
            
            if new_content != content:
                target.write_text(new_content, encoding="utf-8")
                
        except Exception as e:
            logger.warning(f"Port randomization failed: {e}")

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
    def _patch_volumes_for_host(self, config_dir: Path, repo_name: str) -> bool:
        """
        Patch docker-compose.yml to use absolute HOST paths for volumes.
        Assumes internal `/app` maps to host `/DATA/x-box`.
        """
        try:
            candidates = ["docker-compose.yml", "docker-compose.yaml"]
            target = None
            for fname in candidates:
                if (config_dir / fname).exists():
                    target = config_dir / fname
                    break
            
            if not target: return False
            
            content = target.read_text(encoding="utf-8")
            
            # Determine Host Path
            # Internal: /DATA/x-box/data/deployment_staging/...
            # Host:     /DATA/x-box/data/deployment_staging/... (Identity Mapping)
            
            internal_path = str(config_dir.resolve())
            host_base = internal_path
                
            logger.info(f"Patching volumes. Internal: {internal_path} -> Host: {host_base}")
            
            import yaml
            try:
                data = yaml.safe_load(content)
            except:
                return False
                
            if not data or "services" not in data:
                return False
                
            modified = False
            for svc_name, svc in data["services"].items():
                if "volumes" in svc:
                    new_volumes = []
                    for vol in svc["volumes"]:
                        if isinstance(vol, str):
                            parts = vol.split(":")
                            if len(parts) >= 2:
                                host_part = parts[0]
                                container_part = parts[1]
                                options = parts[2] if len(parts) > 2 else ""
                                
                                # Check if it's a relative path
                                if host_part.startswith(".") or not host_part.startswith("/"):
                                    # Convert to absolute path on HOST
                                    # ./caddy -> host_base/caddy
                                    # Normalize ./
                                    if host_part.startswith("./"):
                                        clean_host_part = host_part[2:]
                                    else:
                                        clean_host_part = host_part
                                        
                                    abs_host_path = f"{host_base}/{clean_host_part}"
                                    # Reconstruct
                                    new_vol = f"{abs_host_path}:{container_part}"
                                    if options:
                                        new_vol += f":{options}"
                                    new_volumes.append(new_vol)
                                    modified = True
                                else:
                                    new_volumes.append(vol) # Keep absolute or named volumes
                            else:
                                new_volumes.append(vol)
                        else:
                            # Long syntax (dict) is harder to patch simply, stick to strings for now
                            new_volumes.append(vol)
                    
                    if modified:
                        svc["volumes"] = new_volumes
            
            if modified:
                with open(target, "w", encoding="utf-8") as f:
                    yaml.dump(data, f, default_flow_style=False, sort_keys=False)
                return True
                
            return False
        except Exception as e:
            logger.error(f"Failed to patch volumes: {e}")
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
6. **PORT ALLOCATION**:
   - The user requires using high ports **(20000-60000)** for all exposed services to avoid conflicts.
   - If the `docker-compose.yml` uses common ports (80, 443, 8080, 3000, 5000, etc.), you **MUST** map them to a high port on the host.
   - Example rule: `"8080:80"` -> Change to `"20080:80"` (or similar).
   - Example rule: `"5000:5000"` -> Change to `"25000:5000"`.
   - **Reasoning**: Explain which ports you remapped.
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

    async def _get_access_info(self, config_dir: Path) -> str:
        """
        Get access info (IP and Ports)
        """
        from core.config import SERVER_IP
        
        ips = []
        
        # 1. Configured Static IP (Priority)
        if SERVER_IP:
             ips.append(f"{SERVER_IP} (Config)")

        # 2. Get Public IP (Verification)
        try:
            cmd = "curl -s --max-time 2 ifconfig.me"
            process = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            public_ip = stdout.decode().strip()
            if public_ip and len(public_ip) < 20: # Sanity check length
                ips.append(f"{public_ip} (Public)")
        except Exception:
            pass
            
        # 2. Get LAN/Internal IP (Robust Method for Host Network)
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # Use Google DNS as target (doesn't send data) to find outbound interface
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
            s.close()
            
            with open("/app/data/ip_debug.txt", "a") as f:
                f.write(f"Socket detected: {lan_ip}\n")
            
            if lan_ip and lan_ip != "127.0.0.1":
                ips.append(f"{lan_ip} (LAN)")
        except Exception as e:
            with open("/app/data/ip_debug.txt", "a") as f:
                f.write(f"Socket failed: {e}\n")
            logger.warning(f"LAN IP detection failed: {e}")

        # Fallback
        if not ips:
             ips.append("<您的服务器IP>")
             
        display_ip = " / ".join(ips)

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
            return f"无法自动检测端口，请尝试访问: http://{display_ip}:<端口>"
            
        urls = []
        for p in ports:
            # Construct URLs for each IP found
            for ip_entry in ips:
                ip_addr = ip_entry.split()[0] # remove (Public) or (LAN) label logic if needed, but simple is better
                urls.append(f"http://{ip_addr}:{p}")
                
        return "请尝试访问:\n" + "\n".join(urls)

docker_deployment_service = DockerDeploymentService()
