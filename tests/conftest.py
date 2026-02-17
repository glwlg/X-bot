"""
Pytest 配置文件
"""

import sys
import os
import pytest

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
