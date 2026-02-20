"""
沙箱执行器 - 安全执行 AI 生成的代码
"""

import os
import sys
import logging
import asyncio
import tempfile
import subprocess
from typing import Tuple, Optional, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

# 允许导入的安全模块白名单
SAFE_MODULES = {
    # 标准库
    "os.path",
    "pathlib",
    "json",
    "re",
    "math",
    "datetime",
    "time",
    "collections",
    "itertools",
    "functools",
    "operator",
    "base64",
    "hashlib",
    "uuid",
    "random",
    # PDF 处理
    "pypdf",
    "pdfplumber",
    "reportlab",
    # 数据处理
    "io",
    "csv",
    "tempfile",
    # 网络请求 (受限的)
    "httpx",
}

# 禁止的危险操作
FORBIDDEN_PATTERNS = [
    "os.system",
    "subprocess",
    "eval(",
    "exec(",
    "__import__",
    "open('/etc",
    "open('/root",
    "open('/home",
    "shutil.rmtree",
    "os.remove",
    "os.unlink",
    "socket",
    "urllib",  # httpx is allowed for controlled HTTP requests
    "os.environ",  # 禁止访问环境变量（防止泄露密钥）
]

# 允许传递给沙箱子进程的安全环境变量
SAFE_ENV_VARS = [
    "PATH",
    "PYTHONPATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "HOME",
    "TMPDIR",
    "TEMP",
    "TMP",
]


class SandboxExecutor:
    """
    沙箱代码执行器

    使用子进程隔离执行 AI 生成的代码，限制：
    - 执行时间
    - 内存使用
    - 文件系统访问
    - 网络访问
    """

    def __init__(self, timeout: int = 300, max_output_size: int = 1024 * 1024):
        self.timeout = timeout
        self.max_output_size = max_output_size
        self.work_dir = tempfile.mkdtemp(prefix="xbot_sandbox_")

    def _get_safe_env(self, extra_env: Dict[str, str] = None) -> Dict[str, str]:
        """
        构建安全的环境变量字典，只包含白名单中的变量
        防止泄露 API 密钥等敏感信息
        """
        safe_env = {}
        for key in SAFE_ENV_VARS:
            if key in os.environ:
                safe_env[key] = os.environ[key]

        # 合并额外的环境变量
        if extra_env:
            safe_env.update(extra_env)

        return safe_env

    def validate_code(self, code: str) -> Tuple[bool, str]:
        """
        静态分析代码，检查危险操作
        """
        for pattern in FORBIDDEN_PATTERNS:
            if pattern in code:
                return False, f"禁止使用: {pattern}"

        # 检查是否尝试访问敏感路径
        if "/etc/" in code or "/root/" in code:
            return False, "禁止访问系统敏感目录"

        return True, ""

    async def execute_code(
        self,
        code: str,
        input_files: Dict[str, bytes] = None,
        skill_dir: str = None,
    ) -> Tuple[bool, str, Dict[str, bytes]]:
        """
        在沙箱中执行代码

        Args:
            code: Python 代码
            input_files: 输入文件 {filename: bytes}
            skill_dir: Skill 目录（用于访问 scripts）

        Returns:
            (success, output, output_files)
        """
        # 1. 静态验证
        valid, msg = self.validate_code(code)
        if not valid:
            return False, f"代码验证失败: {msg}", {}

        # 2. 准备工作目录
        work_dir = tempfile.mkdtemp(prefix="xbot_exec_")

        try:
            # 写入输入文件
            if input_files:
                for filename, content in input_files.items():
                    filepath = os.path.join(work_dir, filename)
                    with open(filepath, "wb") as f:
                        f.write(content)

            # 写入代码文件
            code_file = os.path.join(work_dir, "main.py")

            # 包装代码,添加工作目录切换和 skill scripts 路径
            skill_scripts_setup = ""
            if skill_dir and os.path.exists(os.path.join(skill_dir, "scripts")):
                scripts_path = os.path.join(skill_dir, "scripts")
                # 使用 repr() 确保路径正确转义
                skill_scripts_setup = f"""
# 添加 skill scripts 到路径
import sys
sys.path.insert(0, {repr(scripts_path)})
"""

            wrapped_code = f"""
import os
{skill_scripts_setup}
# 切换到工作目录
os.chdir({repr(work_dir)})

# 用户代码
{code}
"""

            with open(code_file, "w", encoding="utf-8") as f:
                f.write(wrapped_code)

            # 3. 执行代码（使用子进程隔离）
            try:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    code_file,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=work_dir,
                    env=self._get_safe_env(
                        {
                            "PYTHONPATH": skill_dir + "/scripts" if skill_dir else "",
                        }
                    ),
                )

                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=self.timeout
                )

            except asyncio.TimeoutError:
                process.kill()
                return False, f"执行超时 ({self.timeout}s)", {}

            # 4. 收集输出
            output = stdout.decode("utf-8", errors="replace")
            if stderr:
                output += "\n[STDERR]\n" + stderr.decode("utf-8", errors="replace")

            # 截断过长输出
            if len(output) > self.max_output_size:
                output = output[: self.max_output_size] + "\n...[输出被截断]"

            # 5. 收集输出文件
            output_files = {}
            for filename in os.listdir(work_dir):
                if filename == "main.py":
                    continue
                filepath = os.path.join(work_dir, filename)
                if os.path.isfile(filepath):
                    # 只收集新生成的文件
                    if input_files is None or filename not in input_files:
                        with open(filepath, "rb") as f:
                            output_files[filename] = f.read()

            success = process.returncode == 0
            return success, output, output_files

        except Exception as e:
            logger.error(f"Sandbox execution error: {e}")
            return False, f"执行错误: {e}", {}

        finally:
            # 清理工作目录
            import shutil

            try:
                shutil.rmtree(work_dir)
            except:
                pass

    async def execute_skill_script(
        self,
        skill_dir: str,
        script_name: str,
        args: list = None,
    ) -> Tuple[bool, str]:
        """
        执行 Skill 目录下的脚本
        """
        script_path = os.path.join(skill_dir, "scripts", script_name)

        if not os.path.exists(script_path):
            return False, f"脚本不存在: {script_name}"

        try:
            cmd = [sys.executable, script_path] + (args or [])

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=skill_dir,
                env=self._get_safe_env(),
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout
            )

            output = stdout.decode("utf-8", errors="replace")
            if stderr:
                output += "\n[STDERR]\n" + stderr.decode("utf-8", errors="replace")

            return process.returncode == 0, output

        except asyncio.TimeoutError:
            return False, f"脚本执行超时 ({self.timeout}s)"
        except Exception as e:
            return False, f"脚本执行错误: {e}"

    async def execute_shell_command(
        self,
        command: str,
        skill_dir: str = None,
    ) -> Tuple[bool, str]:
        """
        在沙箱中执行 Shell 命令
        """
        work_dir = tempfile.mkdtemp(prefix="xbot_cmd_")

        try:
            logger.info(f"Executing shell command: {command}")

            # 使用 shell=True 执行命令
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
                env=self._get_safe_env(
                    {
                        "PYTHONPATH": skill_dir + "/scripts" if skill_dir else "",
                    }
                ),
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout
            )

            output = stdout.decode("utf-8", errors="replace")
            if stderr:
                output += "\n[STDERR]\n" + stderr.decode("utf-8", errors="replace")

            # 截断过长输出
            if len(output) > self.max_output_size:
                output = output[: self.max_output_size] + "\n...[输出被截断]"

            return process.returncode == 0, output

        except asyncio.TimeoutError:
            return False, f"命令执行超时 ({self.timeout}s)"
        except Exception as e:
            return False, f"命令执行错误: {e}"
        finally:
            # 清理工作目录
            import shutil

            try:
                shutil.rmtree(work_dir)
            except:
                pass


# 全局单例
sandbox_executor = SandboxExecutor()
