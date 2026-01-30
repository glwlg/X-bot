import logging
import asyncio
import os
import shutil
import re
from typing import List, Dict, Tuple, Any

logger = logging.getLogger(__name__)


class SkillRegistryService:
    """
    Service to interact with the skill registry using `npx skills`.
    Supports: search, install, check updates, update, and delete skills.
    """
    
    def __init__(self):
        self.cmd_prefix = ["npx", "-y", "skills"]
        # skills 目录路径
        self.skills_base_dir = os.path.abspath("skills")
        self.learned_dir = os.path.join(self.skills_base_dir, "learned")
        self.builtin_dir = os.path.join(self.skills_base_dir, "builtin")

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
        
        # Strip ANSI escape codes
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
                    repo_full = parts[0]  # owner/repo
                    skill_name = parts[1]  # skill-name
                    
                    skills.append({
                        "name": skill_name,
                        "repo": repo_full,
                        "description": f"Skill from {repo_full}",
                        "install_args": [repo_full, skill_name]
                    })
        
        return skills

    async def install_skill(self, repo: str, skill_name: str) -> Tuple[bool, str]:
        """
        Install a skill using `npx skills add -y`.
        Uses -y flag to skip confirmation prompts.
        
        Returns:
            (success: bool, message: str)
        """
        try:
            skill_id = f"{repo}@{skill_name}"
            
            if not os.path.exists(self.learned_dir):
                os.makedirs(self.learned_dir, exist_ok=True)

            # 使用 -y 参数跳过确认
            cmd = self.cmd_prefix + ["add", skill_id, "-y"]
            
            logger.info(f"Running install command: {' '.join(cmd)}")
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=os.getcwd()
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), 
                    timeout=120
                )
            except asyncio.TimeoutError:
                process.kill()
                logger.error("Skill installation timed out")
                return False, "安装超时"

            if process.returncode != 0:
                error_msg = stderr.decode().strip() or stdout.decode().strip()
                logger.error(f"Skill install failed: {error_msg}")
                return False, f"安装失败: {error_msg}"
            
            logger.info(f"Install stdout: {stdout.decode()}")
            
            # Post-install: Locate and move to skills/learned
            source_patterns = [
                os.path.join(os.getcwd(), ".agent", "skills", skill_name),
                os.path.join(os.getcwd(), ".agent", "skills", f"{skill_name}.py"),
                os.path.join(os.getcwd(), skill_name),
                os.path.join(os.getcwd(), f"{skill_name}.py"),
            ]
            
            installed_path = None
            for p in source_patterns:
                if os.path.exists(p):
                    installed_path = p
                    break
            
            if installed_path:
                dest_path = os.path.join(self.learned_dir, os.path.basename(installed_path))
                if os.path.exists(dest_path):
                    if os.path.isdir(dest_path):
                        shutil.rmtree(dest_path)
                    else:
                        os.remove(dest_path)
                
                shutil.move(installed_path, dest_path)
                logger.info(f"Moved installed skill to {dest_path}")
                
                # 安装后处理: 翻译英文描述为中文
                await self._translate_skill_description(dest_path, skill_name)
                
                return True, f"✅ 已安装 {skill_name} 到 skills/learned/"
            else:
                logger.warning(f"Install reported success but could not locate files")
                return False, "安装命令执行成功,但未能定位安装文件"

        except Exception as e:
            logger.error(f"Error installing skill: {e}")
            return False, f"安装异常: {e}"
    
    async def _translate_skill_description(self, skill_path: str, skill_name: str):
        """
        如果技能的 description 是英文,翻译为中文并更新 SKILL.md
        """
        try:
            # 只处理目录格式的技能
            if not os.path.isdir(skill_path):
                return
            
            skill_md_path = os.path.join(skill_path, "SKILL.md")
            if not os.path.exists(skill_md_path):
                return
            
            # 读取 SKILL.md
            with open(skill_md_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 解析 frontmatter
            if not content.startswith("---"):
                return
            
            parts = content.split("---", 2)
            if len(parts) < 3:
                return
            
            import yaml
            frontmatter = yaml.safe_load(parts[1])
            description = frontmatter.get("description", "")
            
            # 检测是否为英文 (简单启发式: 如果包含中文字符则跳过)
            if not description or any('\u4e00' <= char <= '\u9fff' for char in description):
                return
            
            # 调用 AI 翻译
            from core.gemini_client import gemini_client
            
            prompt = f"将以下技能描述翻译为简洁的中文,保持专业性,不要添加任何解释:\n\n{description}"
            
            response = await gemini_client.models.generate_content_async(
                model="gemini-2.0-flash-exp",
                contents=prompt
            )
            
            chinese_desc = response.text.strip()
            
            # 更新 frontmatter
            frontmatter["description"] = chinese_desc
            
            # 重新组装 SKILL.md
            new_content = "---\n" + yaml.dump(frontmatter, allow_unicode=True, sort_keys=False) + "---" + parts[2]
            
            # 写回文件
            with open(skill_md_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            logger.info(f"Translated description for {skill_name}: {description[:50]}... -> {chinese_desc[:50]}...")
            
        except Exception as e:
            logger.warning(f"Failed to translate description for {skill_name}: {e}")

    async def check_updates(self) -> Tuple[bool, str]:
        """
        Check for skill updates using `npx skills check`.
        
        Returns:
            (success: bool, output: str)
        """
        try:
            cmd = self.cmd_prefix + ["check"]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=os.getcwd()
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=60
                )
            except asyncio.TimeoutError:
                process.kill()
                return False, "检查超时"
            
            output = stdout.decode().strip()
            
            # Strip ANSI
            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
            output = ansi_escape.sub('', output)
            
            if process.returncode != 0:
                error = stderr.decode().strip()
                return False, f"检查失败: {error or output}"
            
            if not output:
                return True, "所有技能已是最新版本"
            
            return True, output
            
        except Exception as e:
            logger.error(f"Error checking updates: {e}")
            return False, f"检查异常: {e}"

    async def update_skills(self) -> Tuple[bool, str]:
        """
        Update all installed skills using `npx skills update -y`.
        
        Returns:
            (success: bool, message: str)
        """
        try:
            cmd = self.cmd_prefix + ["update", "-y"]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=os.getcwd()
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=180
                )
            except asyncio.TimeoutError:
                process.kill()
                return False, "更新超时"
            
            output = stdout.decode().strip()
            
            # Strip ANSI
            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
            output = ansi_escape.sub('', output)
            
            if process.returncode != 0:
                error = stderr.decode().strip()
                return False, f"更新失败: {error or output}"
            
            return True, output or "✅ 技能更新完成"
            
        except Exception as e:
            logger.error(f"Error updating skills: {e}")
            return False, f"更新异常: {e}"

    async def delete_skill(self, skill_name: str) -> Tuple[bool, str]:
        """
        Delete a skill from skills/learned directory.
        
        SECURITY: Only skills in 'learned' directory can be deleted.
                  Builtin skills are protected and cannot be modified.
        
        Returns:
            (success: bool, message: str)
        """
        try:
            # 安全检查：获取 skill 信息以确认来源
            from core.skill_loader import skill_loader
            
            skill_info = skill_loader.get_skill(skill_name)
            
            if not skill_info:
                return False, f"❌ 技能 '{skill_name}' 不存在"
            
            # 检查是否为 builtin 技能
            if skill_info.get("source") == "builtin":
                return False, f"🚫 禁止删除内置技能 '{skill_name}'"
            
            # 确认是 learned 技能
            if skill_info.get("source") != "learned":
                return False, f"❌ 技能 '{skill_name}' 来源未知，无法删除"
            
            # 获取技能路径
            skill_path = None
            if skill_info.get("skill_type") == "standard":
                skill_path = skill_info.get("skill_dir")
            elif skill_info.get("skill_type") == "legacy":
                skill_path = skill_info.get("path")
            
            if not skill_path or not os.path.exists(skill_path):
                return False, f"❌ 找不到技能文件: {skill_path}"
            
            # 再次验证路径在 learned 目录下（防止路径遍历攻击）
            skill_path_abs = os.path.abspath(skill_path)
            learned_dir_abs = os.path.abspath(self.learned_dir)
            
            if not skill_path_abs.startswith(learned_dir_abs):
                logger.warning(f"Security: Attempted to delete skill outside learned dir: {skill_path_abs}")
                return False, "🚫 安全限制：只能删除 learned 目录下的技能"
            
            # 执行删除
            if os.path.isdir(skill_path_abs):
                shutil.rmtree(skill_path_abs)
            else:
                os.remove(skill_path_abs)
            
            # 从 loader 中卸载并重新扫描
            skill_loader.unload_skill(skill_name)
            skill_loader.reload_skills()
            
            logger.info(f"Deleted skill: {skill_name} from {skill_path_abs}")
            return True, f"✅ 已删除技能 '{skill_name}'"
            
        except Exception as e:
            logger.error(f"Error deleting skill {skill_name}: {e}")
            return False, f"删除异常: {e}"

    def list_learned_skills(self) -> List[Dict[str, Any]]:
        """
        List all skills in the learned directory.
        
        Returns:
            List of skill info dicts with 'name' and 'path' keys.
        """
        from core.skill_loader import skill_loader
        
        skills = []
        index = skill_loader.get_skill_index()
        
        for name, info in index.items():
            if info.get("source") == "learned":
                skills.append({
                    "name": name,
                    "description": info.get("description", ""),
                    "skill_type": info.get("skill_type"),
                })
        
        return skills


skill_registry = SkillRegistryService()
