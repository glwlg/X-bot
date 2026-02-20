"""
Pytest 配置文件
"""

import atexit
import os
from pathlib import Path
import shutil
import sys
import tempfile

import pytest


_PYTEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="xbot-pytest-data-")).resolve()
os.environ["DATA_DIR"] = str(_PYTEST_DATA_DIR)


def _cleanup_pytest_data_dir() -> None:
    keep = str(os.getenv("PYTEST_KEEP_DATA_DIR", "")).strip().lower()
    if keep in {"1", "true", "yes", "on"}:
        return
    shutil.rmtree(_PYTEST_DATA_DIR, ignore_errors=True)


atexit.register(_cleanup_pytest_data_dir)


def pytest_sessionfinish(session, exitstatus):  # type: ignore[unused-argument]
    _cleanup_pytest_data_dir()


# 将 src 目录添加到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


@pytest.fixture
def mock_db(tmp_path, monkeypatch):
    """使用临时目录创建测试数据目录"""
    test_data_dir = tmp_path / "data"
    test_data_dir.mkdir()

    # 修改 DATA_DIR 为临时目录
    monkeypatch.setenv("DATA_DIR", str(test_data_dir))

    return test_data_dir


@pytest.fixture
def sample_stock_data():
    """示例股票数据"""
    return [
        {"code": "sh601006", "name": "大秦铁路", "market": "上海"},
        {"code": "sz000001", "name": "平安银行", "market": "深圳"},
    ]
