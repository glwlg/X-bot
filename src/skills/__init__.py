"""
Skills 模块初始化
"""
from pathlib import Path

# 确保目录存在
SKILLS_DIR = Path(__file__).parent
(SKILLS_DIR / "builtin").mkdir(exist_ok=True)
(SKILLS_DIR / "learned").mkdir(exist_ok=True)
(SKILLS_DIR / "pending").mkdir(exist_ok=True)
