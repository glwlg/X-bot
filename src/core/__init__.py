"""
Core 模块 - 配置、常量和核心调度
"""

from pathlib import Path
import sys

_CORE_DIR = Path(__file__).resolve().parent
_SRC_DIR = _CORE_DIR.parent
_REPO_ROOT = _SRC_DIR.parent

for _path in (str(_REPO_ROOT), str(_SRC_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from .config import *
