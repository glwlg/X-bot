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
    Service to interact with the specialized skill registry using `npx ctx7 skills`.
    """
    
    def __init__(self):
        # We'll valid if npx is available
        self.cmd_prefix = ["npx", "-y", "ctx7", "skills"]
        # Install skills to a temporary location first or directly to skills/external?
        # The tool prompts for location. We need to control this.
        # Based on help, `install` takes options. Let's check if we can suppress prompt.
        # If not, we might need a wrapper or assume we can't fully automate without user interaction in CLI?
        # But for the bot, we need non-interactive.
        # We will assume "yes" to prompts if possible or ignore if it fails silently?
        # Actually, `npx -y` handles the npx part. `ctx7 skills install` might have flags.
        # The user test showed it asks for path.
        pass

    async def search_skills(self, query: str) -> List[Dict[str, Any]]:
        """
        Search for skills in the registry.
        Returns a list of skill info dicts.
        """
        if not query:
            return []
            
        try:
            # Run search command
            # Use --json if available? The help didn't show it, but let's try to parse text.
            # Output format:
            # ◯ skill_name  (repo)  Description...
            
            cmd = self.cmd_prefix + ["search", query]
            
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
        Parse the CLI output from `ctx7 skills search`.
        """
        skills = []
        import re
        
        # Step 1: Strip ANSI escape codes
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        output = ansi_escape.sub('', output)
        
        # Step 2: Remove all non-ASCII characters (bullets, special symbols)
        # Keep only printable ASCII + common punctuation
        clean_output = ""
        for char in output:
            if 32 <= ord(char) <= 126 or char in '\n\r\t':
                clean_output += char
            else:
                clean_output += " "  # Replace with space
        
        # Step 3: Parse lines
        # After cleaning, lines should look like:
        #   weather                   (/steipete/clawdis) Get current weather...
        # Pattern: skill_name followed by (repo) followed by description
        
        pattern = re.compile(r'^\s*([\w][\w-]*)\s+\((/[^)]+)\)\s*(.*)')
        
        lines = clean_output.splitlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Skip obvious non-skill lines
            skip_keywords = ['select', 'navigate', 'searching', 'found', 'install', 
                            'submit', 'space', 'invert', 'cancelled']
            if any(kw in line.lower() for kw in skip_keywords):
                continue
            if line.startswith('?') or line.startswith('-'):
                continue
                
            match = pattern.match(line)
            if match:
                name = match.group(1).strip()
                repo = match.group(2).strip()
                desc = match.group(3).strip()
                
                # Filter out obvious headers or bad matches
                if name.lower() in ["loading", "searching", "found", "select", "install"]:
                    continue

                skills.append({
                    "name": name,
                    "repo": repo,
                    "description": desc,
                    "install_args": [repo, name]
                })
                
                logger.debug(f"Parsed skill: name={name}, repo={repo}")
        
        logger.info(f"Parsed {len(skills)} skills from search output")
        return skills

    async def install_skill(self, repo: str, skill_name: str) -> bool:
        """
        Install a skill from the registry.
        """
        try:
            # We want to install to skills/external (learned)
            # Use --antigravity flag to force install to .agent/skills (relative to CWD)
            
            # Target dir for our bot
            target_dir = os.path.abspath("skills/learned") 
            if not os.path.exists(target_dir):
                os.makedirs(target_dir, exist_ok=True)

            cmd = self.cmd_prefix + ["install", repo, skill_name, "--antigravity"]
            
            # Note: Even with the flag, it might prompt for confirmation if the file implies overwriting?
            # Or if it detects multiple agents. But --antigravity should target specifically.
            
            logger.info(f"Running install command: {' '.join(cmd)}")
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=os.getcwd() # Run in root
            )
            
            # Send 'y\n' just in case it asks to initiate/confirm
            try:
                # Wait for longer time as network might be slow
                stdout, stderr = await asyncio.wait_for(process.communicate(input=b"y\n"), timeout=120)
            except asyncio.TimeoutError:
                process.kill()
                logger.error("Skill installation timed out")
                return False

            if process.returncode != 0:
                logger.error(f"Skill install failed code={process.returncode}: {stderr.decode()} | OUT: {stdout.decode()}")
                return False
            
            logger.info(f"Install stdout: {stdout.decode()}")
            
            # Check where it went: .agent/skills
            source_dir = os.path.join(os.getcwd(), ".agent", "skills")
            installed_path = None
            
            # It could be a file or a folder
            potential_file = os.path.join(source_dir, f"{skill_name}.py")
            potential_dir = os.path.join(source_dir, skill_name)
            
            if os.path.exists(potential_file):
                installed_path = potential_file
            elif os.path.exists(potential_dir):
                installed_path = potential_dir
            
            if installed_path:
                # Move to skills/learned
                dest_path = os.path.join(target_dir, os.path.basename(installed_path))
                if os.path.exists(dest_path):
                     if os.path.isdir(dest_path):
                         shutil.rmtree(dest_path)
                     else:
                         os.remove(dest_path)
                
                shutil.move(installed_path, dest_path)
                logger.info(f"Moved installed skill from {installed_path} to {dest_path}")
                return True
            else:
                logger.error(f"Could not find installed skill file in {source_dir}")
                return False

        except Exception as e:
            logger.error(f"Error installing skill: {e}")
            return False

skill_registry = SkillRegistryService()
