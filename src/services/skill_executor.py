"""
Skill 执行器 - 协调 AI 理解和执行标准 Skill
"""
import logging
import asyncio
from typing import Optional, Dict, Any, Tuple, AsyncGenerator

from core.config import gemini_client, GEMINI_MODEL
from core.skill_loader import skill_loader
from services.sandbox_executor import sandbox_executor

logger = logging.getLogger(__name__)

# 标准 Skill 执行的系统提示
SKILL_EXECUTION_PROMPT = """你是一个专业的技术助手。你正在执行一个特定的技能任务。

## 你拥有的能力 (来自 Skill 文档)

{skill_content}

## 可用的辅助脚本

{scripts_list}

## 任务

根据用户的请求，使用上述文档中的知识来完成任务。

如果需要执行代码，请按以下格式输出：

```python
# 你的 Python 代码
```

## 沙箱限制 (重要!)

代码将在安全沙箱中执行，以下是限制：

1. **禁止使用**: subprocess, os.system, eval, exec, socket, urllib
2. **网络请求**: 必须使用 `httpx` 库 (已安装)，不能用 curl 或 subprocess
3. **文件操作**: 只能在当前目录读写文件

如果文档中使用了 curl 或 shell 命令示例，请将其转换为等效的 Python/httpx 代码。

## 网络可靠性提示

- **wttr.in 可能有 SSL 问题**，优先使用 **open-meteo.com** API
- 对于天气查询，推荐使用: `https://api.open-meteo.com/v1/forecast?latitude=LAT&longitude=LON&current_weather=true`
- 需要先通过地名获取经纬度，可以用: `https://geocoding-api.open-meteo.com/v1/search?name=城市名&count=1`
- 所有请求加上 `timeout=30`

示例 (天气查询):
```python
import httpx

# 1. 获取城市坐标
geo = httpx.get("https://geocoding-api.open-meteo.com/v1/search?name=Quzhou&count=1", timeout=30).json()
lat, lon = geo["results"][0]["latitude"], geo["results"][0]["longitude"]

# 2. 获取天气
weather = httpx.get(f"https://api.open-meteo.com/v1/forecast?latitude={{lat}}&longitude={{lon}}&current_weather=true", timeout=30).json()
print(f"温度: {{weather['current_weather']['temperature']}}°C")
```

## 用户请求

{user_request}

## 附加上下文

{extra_context}
"""


class SkillExecutor:
    """
    标准 Skill 执行器
    
    工作流程：
    1. 加载 SKILL.md 内容
    2. 将内容注入到 AI 上下文
    3. AI 理解任务并生成解决方案（可能包含代码）
    4. 如果有代码，在沙箱中执行
    5. 返回结果
    """
    
    async def execute_standard_skill(
        self,
        skill_name: str,
        user_request: str,
        extra_context: str = "",
        input_files: Dict[str, bytes] = None,
        **kwargs
    ) -> AsyncGenerator[Tuple[str, Optional[Dict[str, bytes]]], None]:
        """
        执行标准 Skill
        
        Yields:
            (status_message, output_files)
        """
        # 1. 获取 Skill 信息
        skill_info = skill_loader.get_skill(skill_name)
        if not skill_info:
            yield f"❌ 找不到技能: {skill_name}", None
            return
        
        if skill_info.get("skill_type") != "standard":
            yield f"❌ {skill_name} 不是标准协议技能", None
            return
        
        skill_content = skill_info.get("skill_md_content", "")
        skill_dir = skill_info.get("skill_dir", "")
        scripts = skill_info.get("scripts", [])
        source = skill_info.get("source", "")
        
        yield f"📚 正在使用技能 **{skill_name}** 处理您的请求...", None
        
        # **关键优化**: 如果有 execute.py, 直接导入并调用 (支持 builtin 和 learned)
        if "execute.py" in scripts:
            import os
            import sys
            import importlib.util
            
            execute_script = os.path.join(skill_dir, "scripts", "execute.py")
            
            yield "⚙️ 正在执行内置脚本...", None
            
            try:
                # 动态导入 execute.py
                spec = importlib.util.spec_from_file_location(f"{skill_name}_execute", execute_script)
                module = importlib.util.module_from_spec(spec)
                sys.modules[f"{skill_name}_execute"] = module
                spec.loader.exec_module(module)
                
                # 调用 execute 函数
                if not hasattr(module, "execute"):
                    yield f"❌ {execute_script} 中没有 execute 函数", None
                    return
                
                # 准备参数 - 使用 AI 解析
                update = kwargs.get("update")
                context = kwargs.get("context")
                
                # INJECTION: Inject 'run_skill' into context to enable Skill Composition
                # We attach it directly to 'context' (ephemeral) instead of 'bot_data' (persistent)
                # to avoid PickleError (local functions cannot be pickled).
                async def run_skill_helper(target_skill: str, target_params: dict) -> str:
                    """
                    Helper injected into context to allow skills to call other skills.
                    Returns the final text result.
                    """
                    logger.info(f"[SkillComposition] {skill_name} calling {target_skill}...")
                    final_output = []
                    # Reuse the same executor instance (self)
                    async for msg, files in self.execute_skill(target_skill, "", params=target_params, update=update, context=context):
                         if msg: final_output.append(msg)
                    
                    return "\n".join(final_output)

                if context:
                    # Monkey-patch context object for this execution scope
                    # This is not persisted, so it's safe.
                    setattr(context, 'run_skill', run_skill_helper)
                
                # 使用 AI 从 SKILL.md 中解析参数
                params = {}
                if user_request:
                    try:
                        from google.genai import types
                        import json
                        import re
                        
                        logger.info(f"[PARAM_EXTRACT] Starting parameter extraction for {skill_name}")
                        logger.info(f"[PARAM_EXTRACT] User request: {user_request}")
                        
                        prompt = (
                            f"Extract parameters for skill '{skill_name}' from the user instruction.\n\n"
                            f"Skill Documentation:\n{skill_content[:2000]}\n\n"
                            f"User Instruction: {user_request}\n\n"
                            "Based on the skill documentation, extract the required parameters from the user instruction.\n"
                            "Return ONLY a JSON object with the extracted parameters."
                        )
                        
                        response = gemini_client.models.generate_content(
                            model=GEMINI_MODEL,
                            contents=prompt,
                            config={
                                "response_mime_type": "application/json",
                            }
                        )
                        response_text = response.text.strip() if response.text else ""
                        logger.info(f"[PARAM_EXTRACT] AI response: {response_text}")
                        
                        # Clean markdown code blocks if present
                        if response_text.startswith("```"):
                            response_text = re.sub(r"^```json\s*", "", response_text)
                            response_text = re.sub(r"^```\s*", "", response_text)
                            response_text = re.sub(r"\s*```$", "", response_text)
                        
                        if response_text:
                            params = json.loads(response_text)
                            logger.info(f"[PARAM_EXTRACT] Extracted params for {skill_name}: {params}")
                        else:
                            logger.warning(f"[PARAM_EXTRACT] Empty response from AI")
                            params = {"instruction": user_request}
                    except Exception as e:
                        logger.error(f"[PARAM_EXTRACT] Param extraction failed: {e}", exc_info=True)
                        params = {"instruction": user_request}
                else:
                    params = {"instruction": user_request}
                
                # Check if params is a list (concurrent execution)
                if isinstance(params, list):
                    logger.info(f"Detected multiple tasks ({len(params)}), executing concurrently...")
                    yield f"🔄 检测到 {len(params)} 个子任务，正在并发执行...", None
                    
                    async def run_single_task(p):
                        try:
                            if asyncio.iscoroutinefunction(module.execute):
                                return await module.execute(update, context, p)
                            else:
                                return module.execute(update, context, p)
                        except Exception as e:
                            logger.error(f"Subtask failed: {e}")
                            return f"❌ 子任务失败: {e}"

                    results = await asyncio.gather(*(run_single_task(p) for p in params))
                    
                    # Merge results
                    final_result = "\n".join([str(r) for r in results if r])
                    yield f"✅ 并发执行完成 ({len(results)}/{len(results)})", None
                    if final_result:
                         yield final_result, None
                    return

                # Single execution
                if asyncio.iscoroutinefunction(module.execute):
                    result = await module.execute(update, context, params)
                else:
                    result = module.execute(update, context, params)
                
                # 返回结果
                if isinstance(result, str):
                    yield result, None
                else:
                    yield f"✅ 执行完成: {str(result)}", None
                
                return
                
            except Exception as e:
                logger.error(f"Error executing builtin script: {e}", exc_info=True)
                yield f"❌ 执行错误: {e}", None
                
                # --- Self-Healing (Reactive Repair) ---
                try:
                    update_obj = kwargs.get("update")
                    if update_obj and update_obj.effective_user:
                        yield f"🔧 监测到异常，正在尝试生成修复补丁...", None
                        
                        from services.skill_creator import update_skill
                        user_id = update_obj.effective_user.id
                        
                        repair_req = f"Fix execution error: {str(e)}\nOriginal Request: {user_request}"
                        
                        result = await update_skill(skill_name, repair_req, user_id)
                        
                        if result["success"]:
                            success_msg = (
                                f"✅ 已自动生成修复方案！\n\n"
                                f"请运行以下命令批准修改生效：\n"
                                f"`approve_skill {skill_name}`"
                            )
                            yield success_msg, None
                            
                            # Record Success
                            from core.evolution_router import evolution_router
                            await evolution_router.record_evolution(
                                request=f"Fix skill {skill_name}: {str(e)}",
                                strategy="reactive_repair",
                                success=True,
                                details=f"Generated fix for error: {str(e)[:100]}"
                            )
                            
                        else:
                             err_msg = f"⚠️ 自动修复尝试失败: {result.get('error')}"
                             yield err_msg, None
                             
                             # Record Failure
                             from core.evolution_router import evolution_router
                             await evolution_router.record_evolution(
                                request=f"Fix skill {skill_name}: {str(e)}",
                                strategy="reactive_repair",
                                success=False,
                                details=f"Fix failed: {result.get('error')}"
                            )
                             
                except Exception as he:
                    logger.error(f"Self-healing failed: {he}")
                
                return
        
        # 2. 构建提示 (learned 技能或没有 execute.py 的 builtin 技能)
        scripts_list = "\n".join([f"- {s}" for s in scripts]) if scripts else "无"
        
        prompt = SKILL_EXECUTION_PROMPT.format(
            skill_content=skill_content[:8000],  # 截断过长内容
            scripts_list=scripts_list,
            user_request=user_request,
            extra_context=extra_context or "无",
        )
        
        # 3. 调用 AI 生成解决方案
        yield "🤔 正在分析任务...", None
        
        try:
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={
                    "system_instruction": "你是一个代码执行助手。根据技能文档完成用户任务。如果需要生成文件，务必使用代码实现。请注意：你返回的所有非代码文本内容将直接作为 Telegram Bot 的回复发送给用户，请保持语气友好、简洁，并使用 Markdown 格式。",
                },
            )
            
            if not response.text:
                yield "❌ AI 无法生成解决方案", None
                return
            
            ai_response = response.text
            
        except Exception as e:
            logger.error(f"AI generation error: {e}")
            yield f"❌ AI 服务错误: {e}", None
            return
        
        # 4. 检查是否包含代码块
        import re
        code_blocks = re.findall(r"```python\n(.*?)```", ai_response, re.DOTALL)
        
        if code_blocks:
            yield "⚙️ 正在执行代码...", None
            
            # 执行所有代码块
            all_output_files = {}
            execution_output = ""
            
            for i, code in enumerate(code_blocks):
                success, output, output_files = await sandbox_executor.execute_code(
                    code=code,
                    input_files=input_files,
                    skill_dir=skill_dir,
                )
                
                execution_output += f"\n[代码块 {i+1}]\n{output}\n"
                all_output_files.update(output_files)
                
                if not success:
                    yield f"⚠️ 代码执行出现问题:\n```\n{output}\n```", None
            
            # 5. 返回结果
            if all_output_files:
                yield f"✅ 执行完成！生成了 {len(all_output_files)} 个文件。", all_output_files
            else:
                # 没有生成文件，返回 AI 的文字回复
                # 移除代码块，只保留解释文字
                clean_response = re.sub(r"```python\n.*?```", "[已执行]", ai_response, flags=re.DOTALL)
                yield clean_response, None
                
                if execution_output.strip():
                    yield f"\n📋 执行输出:\n```\n{execution_output.strip()}\n```", None
        else:
            # 没有代码，直接返回 AI 回复
            yield ai_response, None
    
    async def execute_skill(
        self,
        skill_name: str,
        user_request: str,
        **kwargs
    ) -> AsyncGenerator[Tuple[str, Optional[Dict[str, bytes]]], None]:
        """
        统一执行入口 - 自动判断 Skill 类型
        """
        skill_info = skill_loader.get_skill(skill_name)
        
        if not skill_info:
            yield f"❌ 找不到技能: {skill_name}", None
            return
        
        if skill_info.get("skill_type") == "standard":
            async for msg, files in self.execute_standard_skill(skill_name, user_request, **kwargs):
                yield msg, files
        elif skill_info.get("skill_type") == "legacy":
            async for msg, files in self.execute_legacy_skill(skill_name, user_request, **kwargs):
                yield msg, files
        else:
            yield f"❌ 未知技能类型: {skill_info.get('skill_type')}", None

    async def execute_legacy_skill(
        self,
        skill_name: str,
        user_request: str,
        **kwargs
    ) -> AsyncGenerator[Tuple[str, Optional[Dict[str, bytes]]], None]:
        """
        执行旧版 .py Skill (直接在进程内运行)
        Legacy .py skills 必须包含 `execute(update, context, params)` 函数
        """
        try:
            # 1. 加载模块
            module = skill_loader.load_legacy_skill(skill_name)
            if not module:
                yield f"❌ 无法加载旧版技能: {skill_name}", None
                return

            # 2. 准备参数
            # 旧版 skill 通常期望 execute(update, context, params)
            update = kwargs.get("update")
            context = kwargs.get("context")
            
            # 使用 AI 解析参数
            params = kwargs.get("params", {})
            skill_params_schema = skill_loader.get_skill(skill_name).get("params", {})
            
            if not params and skill_params_schema and user_request:
                # Need to extract params from user_request based on schema
                yield f"🤔 正在解析参数...", None
                try:
                    from google.genai import types
                    # Construct simple extraction prompt
                    prompt = (
                        f"Extract parameters for function '{skill_name}' from the instruction.\n"
                        f"Instruction: {user_request}\n"
                        f"Parameters Schema: {skill_params_schema}\n"
                        "Return ONLY a JSON object."
                    )
                    
                    response = gemini_client.models.generate_content(
                        model=GEMINI_MODEL,
                        contents=prompt,
                        config={
                            "response_mime_type": "application/json",
                        }
                    )
                    import json
                    import re
                    response_text = response.text.strip() if response.text else ""
                    
                    # Clean markdown code blocks if present
                    if response_text.startswith("```"):
                        response_text = re.sub(r"^```json\s*", "", response_text)
                        response_text = re.sub(r"^```\s*", "", response_text)
                        response_text = re.sub(r"\s*```$", "", response_text)
                        
                    if response_text:
                        params = json.loads(response_text)
                    else:
                        logger.warning("AI returned empty response for param extraction")
                        params = {"instruction": user_request}
                    yield f"✅ 解析参数: {params}", None
                except Exception as e:
                    logger.error(f"Param extraction failed: {e}")
                    # Fallback: pass the raw instruction as a param
                    params = {"instruction": user_request}
                    yield f"⚠️ 参数解析失败，使用原始指令继续执行.", None
            
            yield f"⚙️ 正在执行 {skill_name}...", None
            
            # 3. 执行
            if not asyncio.iscoroutinefunction(module.execute):
                # 同步函数
                result = module.execute(update, context, params)
            else:
                # 异步函数
                result = await module.execute(update, context, params)
            
            # 4. 返回结果
            # 旧版 execute 通常返回字符串 result
            if isinstance(result, str):
                yield result, None
            else:
                yield f"✅ 执行完成: {str(result)}", None
                
        except Exception as e:
            logger.error(f"Error executing legacy skill {skill_name}: {e}", exc_info=True)
            yield f"❌ 执行出错: {str(e)}", None
            
            # --- Self-Healing (Reactive Repair) ---
            try:
                update_obj = kwargs.get("update")
                if update_obj and update_obj.effective_user:
                    yield f"🔧 监测到异常，正在尝试生成修复补丁...", None
                    
                    from services.skill_creator import update_skill
                    user_id = update_obj.effective_user.id
                    
                    repair_req = f"Fix execution error: {str(e)}\nOriginal Request: {user_request}"
                    
                    result = await update_skill(skill_name, repair_req, user_id)
                    
                    if result["success"]:
                        yield (
                            f"✅ 已自动生成修复方案！\n\n"
                            f"请运行以下命令批准修改生效：\n"
                            f"`approve_skill {skill_name}`"
                        ), None
                    else:
                         yield f"⚠️ 自动修复尝试失败: {result.get('error')}", None
            except Exception as he:
                logger.error(f"Self-healing failed: {he}")


# 全局单例
skill_executor = SkillExecutor()
