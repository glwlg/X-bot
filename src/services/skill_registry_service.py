import json
import logging
import asyncio
import os
import shutil
from typing import List, Dict, Optional, Any
from core.config import DATA_DIR

logger = logging.getLogger(__name__)

class SkillRegistryService:
    """
    Service to interact with the skill registry using `npx skills`.
    """
    
    def __init__(self):
        self.cmd_prefix = ["npx", "-y", "skills"]

    async def search_skills(self, query: str) -> List[Dict[str, Any]]:
        """
        Search for skills in the registry.
        """
        if not query:
            return []
            
        try:
            cmd = self.cmd_prefix + ["find", query]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                logger.error(f"Skill search failed: {stderr.decode()}")
                return []
                
            output = stdout.decode()
            return self._parse_search_output(output)
            
        except Exception as e:
            logger.error(f"Error searching skills: {e}")
            return []

    def _parse_search_output(self, output: str) -> List[Dict[str, Any]]:
        """
        Parse output from `npx skills find`.
        Expected format:
        owner/repo@skill-name
        └ https://skills.sh/...
        """
        skills = []
        import re
        
        # Strip ANSI
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        output = ansi_escape.sub('', output)
        
        lines = output.splitlines()
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('└') or line.startswith('http'):
                continue
            
            # Skip help message
            if "npx skills add" in line:
                continue
                
            # Assume line is an ID: owner/repo@skill or similar
            # Example: ypares/agent-skills@searxng-search
            if '@' in line and '/' in line:
                parts = line.split('@')
                if len(parts) >= 2:
                    repo_full = parts[0] # owner/repo
                    skill_name = parts[1] # skill-name
                    
                    skills.append({
                        "name": skill_name,
                        "repo": repo_full,
                        # Description isn't provided in the list view of npx skills find currently
                        # We use a placeholder
                        "description": f"Skill from {repo_full} (Description not available in preview)",
                        "install_args": [repo_full, skill_name]
                    })
        
        return skills

    async def install_skill(self, repo: str, skill_name: str) -> bool:
        """
        Install a skill using `npx skills add`.
        Constructs ID as repo@skill_name.
        """
        try:
            # ID format: owner/repo@skill
            skill_id = f"{repo}@{skill_name}"
            
            # Target dir for our bot
            target_dir = os.path.abspath("skills/learned") 
            if not os.path.exists(target_dir):
                os.makedirs(target_dir, exist_ok=True)

            cmd = self.cmd_prefix + ["add", skill_id]
            
            logger.info(f"Running install command: {' '.join(cmd)}")
            
            # npx skills add might prompt or just install. 
            # We assume it installs to .agent/skills or similar relative path.
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE, 
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=os.getcwd()
            )
            
            # Send 'y\n' (yes) just in case it asks to initiate/confirm
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(input=b"y\n"), timeout=120)
            except asyncio.TimeoutError:
                process.kill()
                logger.error("Skill installation timed out")
                return False

            if process.returncode != 0:
                logger.error(f"Skill install failed: {stderr.decode()} | OUT: {stdout.decode()}")
                return False
            
            logger.info(f"Install stdout: {stdout.decode()}")
            
            # Post-install: Locate and move
            # Check likely locations
            # 1. .agent/skills/<skill_name>
            # 2. <skill_name> in root
            source_patterns = [
                os.path.join(os.getcwd(), ".agent", "skills", f"{skill_name}.py"),
                os.path.join(os.getcwd(), ".agent", "skills", skill_name),
                os.path.join(os.getcwd(), f"{skill_name}.py"),
                os.path.join(os.getcwd(), skill_name)
            ]
            
            installed_path = None
            for p in source_patterns:
                if os.path.exists(p):
                    installed_path = p
                    break
            
            if installed_path:
                dest_path = os.path.join(target_dir, os.path.basename(installed_path))
                if os.path.exists(dest_path):
                     if os.path.isdir(dest_path):
                         shutil.rmtree(dest_path)
                     else:
                         os.remove(dest_path)
                
                shutil.move(installed_path, dest_path)
                logger.info(f"Moved installed skill to {dest_path}")
                return True
            else:
                logger.warning(f"Install successful but could not locate file to move from {source_patterns}")
                # If we assume success based on exit code 0, we return True, 
                # but better to return False if we can't manage the file, unless it installed in-place in skills/learned (unlikely).
                return False

        except Exception as e:
            logger.error(f"Error installing skill: {e}")
            return False

skill_registry = SkillRegistryService()
