"""
Evolution Router - 负责 Bot 的"进化"决策
实现 "利旧优先，智能创造" 的核心逻辑
"""

import logging
from typing import Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.platform.models import UnifiedContext

from core.config import gemini_client, GEMINI_MODEL
from core.skill_loader import skill_loader

logger = logging.getLogger(__name__)


class EvolutionRouter:
    """
    进化路由器
    当现有技能无法满足用户需求时，决策并执行进化路径
    """

    def __init__(self):
        self._attempt_history: Dict[
            str, list
        ] = {}  # key: f"{user_id}:{hash(request)}", val: list of timestamps

    async def evolve(
        self, user_request: str, user_id: int, ctx: Optional["UnifiedContext"] = None
    ) -> str:
        """
        执行进化流程
        Returns:
            str: 进化结果报告
        """
        logger.info(f"[Evolution] Starting evolution for: {user_request}")

        import time
        import hashlib

        # 0. Loop Detection / Repetition Check (Self-Awareness)
        # Create a signature for this request
        req_hash = hashlib.md5(user_request.encode()).hexdigest()
        history_key = f"{user_id}:{req_hash}"
        now = time.time()

        if history_key not in self._attempt_history:
            self._attempt_history[history_key] = []

        # Clean up old history (> 10 mins)
        self._attempt_history[history_key] = [
            t for t in self._attempt_history[history_key] if now - t < 600
        ]

        # Check frequency
        if len(self._attempt_history[history_key]) >= 2:
            logger.warning(
                f"[Evolution] Detected repetitive evolution attempt for {user_id}: {user_request}"
            )
            return (
                "🛑 **进化暂停**\n\n"
                "我注意到我们在短时间内对同一个需求尝试了多次进化但似乎没有成功。\n"
                "为了避免死循环，让我先暂停一下。\n\n"
                "💡 **建议**：\n"
                "1. 请检查一下是否有未报错但实际没生效的问题（如权限、网络）。\n"
                "2. 尝试换一种说法描述您的需求。\n"
                "3. 如果是代码报错，您可以把错误信息发给我，我来尝试修复现有技能。"
            )

        # Record this attempt attempt
        self._attempt_history[history_key].append(now)

        # 0.5 Direct Adoption Check
        adopt_msg = await self._try_direct_adopt(user_request, user_id, ctx)
        if adopt_msg:
            return adopt_msg

        # 1. 复杂度与意图分析
        analysis = await self._analyze_request(user_request)
        logger.info(f"[Evolution] Analysis: {analysis}")

        if analysis.get("intent") == "unknown":
            return "🤔 我不太理解您的需求，请尝试更详细的描述。"

        strategy = analysis.get("strategy", "create")  # default to create if unsure

        # 2. 策略执行
        if strategy == "abort":
            return f"❌ **进化中止**: {analysis.get('reason', '原因未知')}"

        if strategy == "repair":
            skill_name = analysis.get("skill_name")
            logger.info(f"[Evolution] Strategy: REPAIR existing skill '{skill_name}'")

            # Use skill_creator via dynamic import
            creator = skill_loader.import_skill_module("skill_manager", "creator.py")
            if not creator:
                return f"⚠️ 无法加载 Skill Manager 组件, 修复失败。"

            update_res = await creator.update_skill(
                skill_name, f"Repair/Update request: {user_request}", user_id
            )
            if update_res["success"]:
                # Approve immediately as it's a repair request
                await creator.approve_skill(skill_name)
                skill_loader.reload_skills()

                # Handle Scheduled Tasks (if suggested)
                suggested_crontab = update_res.get("suggested_crontab")
                cron_msg = ""
                if suggested_crontab:
                    try:
                        from repositories.task_repo import add_scheduled_task

                        instruction = (
                            update_res.get("suggested_cron_instruction")
                            or f"Run {skill_name}"
                        )
                        await add_scheduled_task(
                            skill_name, suggested_crontab, instruction
                        )
                        cron_msg = f"\n⏰ **定时任务已自动配置**: `{suggested_crontab}`"
                    except Exception as e:
                        logger.error(
                            f"Failed to auto-schedule task for {skill_name}: {e}"
                        )
                        cron_msg = f"\n⚠️ 定时任务配置失败: {e}"

                msg = (
                    f"🔧 **技能修复/更新完成！**\n\n"
                    f"已对技能 `{skill_name}` 进行了调整，以适应您的新需求。{cron_msg}\n"
                    f"请重试您的操作。"
                )
                return msg
            else:
                return f"⚠️ 技能修复失败: {update_res['error']}"

        if strategy == "reuse_search":
            # 尝试搜索外部资源 (GitHub / Docker Hub)
            found, result = await self._search_and_reuse(user_request, user_id)
            if found:
                return f"✅发现并建议复用外部资源：\n{result}"
            else:
                # Fallback to create
                logger.info(
                    "[Evolution] External search failed, falling back to creation"
                )
                strategy = "create"

        if strategy == "create":
            # Just-in-Time Creation
            result_msg = await self._jit_create_skill(user_request, user_id, ctx)
            success = "❌" not in result_msg and "⚠️" not in result_msg
            await self.record_evolution(
                user_request, "create", success, result_msg[:100]
            )
            return result_msg

        return "⚠️ 进化策略执行失败"

    async def _try_direct_adopt(
        self, user_request: str, user_id: int, ctx: Optional["UnifiedContext"] = None
    ) -> Optional[str]:
        """
        Attempt to directly adopt a skill from a URL in the request.
        Returns result message if adoption was attempted, None otherwise.
        """
        import re

        # Find URLs that look like skill files (.md, .py) or raw text
        url_pattern = r'https?://[^\s<>"]+|www\.[^\s<>"]+'
        urls = re.findall(url_pattern, user_request)

        target_url = None
        for url in urls:
            # Check for file extensions OR standard skill/raw URLs
            if (
                url.endswith(".md")
                or url.endswith(".py")
                or "raw.githubusercontent.com" in url
                or "gist.githubusercontent.com" in url
                or "skill.md" in url
            ):
                target_url = url
                break

        if not target_url:
            return None

        logger.info(f"[Evolution] Detected potential skill URL: {target_url}")

        try:
            import httpx

            # Use a browser-like User-Agent to avoid blocking by some sites (like cloudflare protected)
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            async with httpx.AsyncClient(
                timeout=15.0, headers=headers, follow_redirects=True
            ) as client:
                response = await client.get(target_url)
                if response.status_code != 200:
                    logger.warning(
                        f"Failed to fetch content from {target_url}: {response.status_code}"
                    )
                    return None

                content = response.text

                # Check for indicators
                if (
                    "SKILL_META" not in content
                    and not content.startswith("---")
                    and "# Skill" not in content
                ):
                    logger.info(
                        "Content doesn't look like a valid skill (no metadata/header)"
                    )
                    # We continue anyway if it's .md or .py, maybe it's a simple script
                    if not (target_url.endswith(".md") or target_url.endswith(".py")):
                        return None

                # Attempt adoption
                creator = skill_loader.import_skill_module(
                    "skill_manager", "creator.py"
                )
                if not creator:
                    return None

                result = await creator.adopt_skill(content, user_id)

                if result["success"]:
                    skill_name = result["skill_name"]

                    # 自动批准 (Auto-Approve)
                    approve_res = await creator.approve_skill(skill_name)

                    if approve_res["success"]:
                        skill_loader.reload_skills()
                        msg = (
                            f"📥 **技能已安装并激活！**\n\n"
                            f"来源: {target_url}\n"
                            f"技能名: `{skill_name}`\n"
                            f"您现在可以直接使用此技能了。"
                        )
                        # if ctx: await ctx.reply(msg)
                        return msg
                    else:
                        return f"⚠️ 技能下载成功但安装失败: {approve_res.get('error')}"
                else:
                    logger.warning(f"Adoption failed: {result.get('error')}")
                    # If adoption fails, return None to let other strategies try (maybe create?)
                    # But if it was explicitly a URL request, we should probably warn.
                    return None

        except Exception as e:
            logger.error(f"[Evolution] Error in direct adopt: {e}")
            return None

    async def record_evolution(
        self, request: str, strategy: str, success: bool, details: str
    ):
        """
        Record evolution event to System Memory (Global Wisdom)
        """
        try:
            from mcp_client.manager import mcp_manager
            from mcp_client.memory import register_memory_server

            # Ensure registered
            register_memory_server()

            # Use "SYSTEM" as user_id for global memory
            memory = await mcp_manager.get_server("memory", user_id="SYSTEM")

            outcome = "success" if success else "failure"
            observation = f"Evolution Event - Request: '{request}', Strategy: '{strategy}', Outcome: {outcome}. Details: {details}"

            # Store as observation linked to 'EvolutionSystem' entity
            await memory.call_tool(
                "create_entities",
                {
                    "entities": [
                        {"name": "EvolutionSystem", "type": "System"},
                        {"name": strategy, "type": "Strategy"},
                    ]
                },
            )

            await memory.call_tool(
                "add_observations",
                {
                    "observations": [
                        {
                            "entityNames": ["EvolutionSystem", strategy],
                            "contents": observation,
                        }
                    ]
                },
            )
            logger.info(f"[Evolution] Recorded event to System Memory: {outcome}")

        except Exception as e:
            logger.error(f"[Evolution] Failed to record memory: {e}")

    async def _analyze_request(self, request: str) -> Dict[str, Any]:
        """
        使用 LLM 分析请求复杂度，并决策 Strategy (Create vs Repair)
        """
        # 0. Check for Permission/Config Errors first (heuristic diagnosis)
        # This relies on the context being passed in or global knowledge,
        # but for now let's check the request text itself for clues if users are pasting errors.
        if "Permission" in request or "401" in request or "403" in request:
            return {
                "intent": "error_report",
                "strategy": "abort",
                "reason": "Permission error detected. User needs to configure keys.",
            }

        # 1. Search for similar skills (Repair Candidate Discovery)
        similar_skills = await skill_loader.find_similar_skills(request, threshold=0.6)
        repair_candidate = None
        if similar_skills:
            top_match = similar_skills[0]
            repair_candidate = top_match["name"]
            logger.info(
                f"[Evolution] Found similar skill '{repair_candidate}' (score: {top_match['score']}). Suggesting REPAIR."
            )

            return {
                "intent": "capability_update",
                "strategy": "repair",
                "skill_name": repair_candidate,
                "reason": f"Found existing skill '{repair_candidate}' similar to request.",
            }

        # 2. LLM Analysis for Create vs Reuse vs Config
        prompt = f"""Analyze the following user request for a ChatBot capability evolution.
        
Request: "{request}"

Determine the best strategy:
1. "config_existing": If the request is about scheduling (cron, timer), configuring, enabling/disabling, or changing settings of an EXISTING skill (e.g., "set weather city to Beijing").
2. "reuse_search": If the request implies a complex application, service, or tool likely existing on GitHub or Docker Hub (e.g., "deploy uptime kuma", "run a minecraft server", "file browser").
3. "create": If the request implies a specific calculation, data processing, scriptable task, or simple tool (e.g., "calculate md5", "convert currency", "check website status", "generate uuid").

Return JSON:
{{
  "intent": "capability_request", 
  "strategy": "config_existing" | "reuse_search" | "create",
  "reason": "explanation",
  "skill_name": "target_skill_name_if_config_existing"
}}
"""
        try:
            response = await gemini_client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            import json
            import re

            text = response.text.strip()
            # Clean markdown code blocks
            text = re.sub(r"^```json\s*", "", text)
            text = re.sub(r"^```\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            text = text.strip()

            try:
                analysis = json.loads(text)

                # Check for config strategy
                if analysis.get("strategy") == "config_existing":
                    return {
                        "intent": "capability_update",
                        "strategy": "abort",
                        "reason": f"这不是进化需求，而是配置需求。请调用 `skill_manager` 的 `modify_skill` 来修改 `{analysis.get('skill_name')}` 的配置 (如 crontab)。",
                    }

                return analysis
            except json.JSONDecodeError:
                logger.error(
                    f"Analysis JSON parse failed. Raw response: {response.text}"
                )
                # Fallback: simple heuristic
                if (
                    "github" in request.lower()
                    or "docker" in request.lower()
                    or "deploy" in request.lower()
                ):
                    return {
                        "intent": "capability_request",
                        "strategy": "reuse_search",
                        "reason": "Fallback logic",
                    }
                return {
                    "intent": "capability_request",
                    "strategy": "create",
                    "reason": "Fallback logic",
                }
        except Exception as e:
            logger.error(f"Analysis failed: {e}")
            return {
                "intent": "capability_request",
                "strategy": "create",
                "reason": "Error",
            }

    async def _search_and_reuse(self, request: str, user_id: int) -> tuple[bool, str]:
        """
        搜索 现有 Skills 仓库 + GitHub/Docker 并尝试复用
        """
        import httpx
        from urllib.parse import quote
        from core.config import SEARXNG_URL

        results_msg = []

        # 0. 优先搜索 本地已安装 Skills (Local)
        # 防止重复创建已存在的技能 (Self-Correction)
        try:
            local_skills = skill_loader.get_skills_summary()
            matched_local = []

            # Simple heuristic search first
            req_lower = request.lower()
            for skill in local_skills:
                name = skill["name"].lower()
                desc = skill["description"].lower()
                # Check exact name match or strong keyword match
                if name in req_lower or (len(name) > 4 and name in req_lower):
                    matched_local.append(skill)
                elif any(t.lower() in req_lower for t in skill.get("triggers", [])):
                    matched_local.append(skill)

            if matched_local:
                msg = "**[Local] 发现本地已安装技能：**\n"
                for skill in matched_local[:3]:
                    msg += f"- `{skill['name']}`: {skill['description']}\n  可以直接使用此技能，无需重复创建。\n"
                results_msg.append(msg)

        except Exception as e:
            logger.warning(f"[Evolution] Local skill search failed: {e}")

        # 1. [REMOVED] Skill Registry Search
        # Internal registry via npx is disabled.

        # 2. 搜索 GitHub / Docker Hub
        # Construct query for GitHub and Docker Hub
        # We search primarily for GitHub as it usually contains Dockerfile or instructions
        search_query = f"(site:github.com OR site:hub.docker.com) {request} topic:python OR topic:docker"
        encoded_query = quote(search_query)

        # Use configured URL
        base_url = SEARXNG_URL
        if not base_url:
            # Skip external search if not configured
            return False, ""

        search_url = f"{base_url}?q={encoded_query}&format=json&categories=it"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(search_url)
                if response.status_code != 200:
                    logger.warning(f"[Evolution] Search failed: {response.status_code}")
                    return False, ""

                data = response.json()
                results = data.get("results", [])

                if not results:
                    return False, ""

                # Filter and format results
                valid_results = []
                for res in results[:3]:
                    url = res.get("url", "")
                    title = res.get("title", "")
                    content = res.get("content", "")

                    if "github.com" in url or "hub.docker.com" in url:
                        valid_results.append(
                            f"- **[{title}]({url})**\n  {content[:150]}..."
                        )

                if valid_results:
                    msg = "**[GitHub/Docker] 外部资源建议：**\n" + "\n\n".join(
                        valid_results
                    )
                    results_msg.append(msg)

        except Exception as e:
            logger.error(f"[Evolution] Search error: {e}")

        # Final Result Combination
        if not results_msg:
            return False, ""

        full_msg = (
            f"根据您的需求，发现了以下可复用资源：\n\n"
            + "\n\n---\n\n".join(results_msg)
            + "\n\n您可以参考外部项目进行部署，或者尝试让我再次为您创造新技能。"
        )
        return True, full_msg

    async def _jit_create_skill(
        self, request: str, user_id: int, ctx: Optional["UnifiedContext"] = None
    ) -> str:
        """
        即时创造技能
        """
        # 1. Create with Retry
        max_retries = 1
        last_error = ""

        for attempt in range(max_retries + 1):
            current_req = request
            if attempt > 0:
                logger.info(
                    f"[Evolution] Retrying skill creation (Attempt {attempt + 1})..."
                )
                # Append error hint to help LLM fix itself
                current_req += f"\n\n(IMPORTANT: The previous generation failed with error: {last_error}. Please ensure valid JSON output and correct code structure.)"

            creator = skill_loader.import_skill_module("skill_manager", "creator.py")
            if not creator:
                return "❌ Skill Manager 加载失败"

            result = await creator.create_skill(current_req, user_id)

            if result["success"]:
                break

            last_error = result["error"]

        if not result["success"]:
            # Final failure after retries
            return f"❌ 技能生成失败 (重试 {max_retries} 次后放弃): {result['error']}"

        skill_name = result["skill_name"]
        skill_md = result.get("skill_md", "")

        # 2. Auto-Approve (Direct Activation)
        approve_res = await creator.approve_skill(skill_name)

        if approve_res["success"]:
            skill_loader.reload_skills()

            # 3. Handle Scheduled Tasks (if suggested)
            suggested_crontab = result.get("suggested_crontab")
            cron_msg = ""
            if suggested_crontab:
                try:
                    from repositories.task_repo import add_scheduled_task

                    instruction = (
                        result.get("suggested_cron_instruction") or f"Run {skill_name}"
                    )
                    await add_scheduled_task(skill_name, suggested_crontab, instruction)
                    cron_msg = f"\n⏰ **定时任务已自动配置**: `{suggested_crontab}`"
                except Exception as e:
                    logger.error(f"Failed to auto-schedule task for {skill_name}: {e}")
                    cron_msg = f"\n⚠️ 定时任务配置失败: {e}"

            msg = (
                f"🛠️ **新技能已生成并激活！**\n\n"
                f"技能名: `{skill_name}`\n"
                f"我已经学会了这项新能力，您可以立即测试。{cron_msg}"
            )
        else:
            msg = f"⚠️ 技能生成成功但激活失败: {approve_res.get('error')}"

        # if ctx:
        #     try:
        #         await ctx.reply(msg)
        #     except Exception as e:
        #         logger.error(f"[Evolution] Failed to send msg: {e}")

        return msg


evolution_router = EvolutionRouter()
