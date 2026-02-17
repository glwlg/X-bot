import pytest
from core.prompt_composer import prompt_composer
from unittest.mock import patch, MagicMock


class TestIdentity:
    def test_manager_identity_prompt(self):
        # Mock soul store to return a specific SOUL content
        with patch(
            "core.soul_store.soul_store.resolve_for_runtime_user"
        ) as mock_resolve:
            mock_soul = MagicMock()
            mock_soul.content = "I am a helpful assistant with a distinct personality."
            mock_resolve.return_value = mock_soul

            base = prompt_composer.compose_base(
                runtime_user_id="core-manager",
                mode="manager",
                runtime_policy_ctx={"agent_kind": "core-manager"},
            )

            assert "你是 X-Bot 的 Core Manager" in base
            assert "SOUL" in base
            assert "I am a helpful assistant with a distinct personality." in base
            assert "不要暴露内部实现细节" in base

    def test_worker_identity_prompt(self):
        with patch(
            "core.soul_store.soul_store.resolve_for_runtime_user"
        ) as mock_resolve:
            mock_soul = MagicMock()
            mock_soul.content = "I am a worker."
            mock_resolve.return_value = mock_soul

            base = prompt_composer.compose_base(
                runtime_user_id="worker::abc",
                mode="worker",
                runtime_policy_ctx={"agent_kind": "worker"},
            )

            assert "SOUL" in base
            assert "I am a worker." in base
