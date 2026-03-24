from pathlib import Path

from core.prompt_composer import prompt_composer
from core.soul_store import SoulPayload


def test_prompt_composer_minimal_shape():
    text = prompt_composer.compose_base(
        runtime_user_id="123",
        tools=[
            {"name": "read", "description": "Read file content"},
            {"name": "ext_web_search", "description": "Search web results"},
        ],
        runtime_policy_ctx={
            "agent_kind": "core-manager",
            "policy": {"tools": {"allow": ["group:all"], "deny": []}},
        },
        mode="manager",
    )

    assert "【SOUL】" in text


def test_prompt_composer_includes_manager_agents_before_soul(monkeypatch):
    monkeypatch.setattr(
        prompt_composer,
        "_load_manager_agents_doc",
        lambda: "# Manager Manual\n- first read this",
    )
    monkeypatch.setattr(
        "core.prompt_composer.soul_store.resolve_for_runtime_user",
        lambda _user_id: SoulPayload(
            agent_kind="core-manager",
            agent_id="core-manager",
            path="/tmp/SOUL.MD",
            content="# Core Manager SOUL\n- tone: warm",
            updated_at="2026-03-13T00:00:00+08:00",
            latest_version_id="",
        ),
    )
    monkeypatch.setattr(prompt_composer, "_build_skill_catalog", lambda **_kwargs: "")

    text = prompt_composer.compose_base(runtime_user_id="123", platform="telegram")

    assert "【AGENTS】" in text
    assert text.index("【AGENTS】") < text.index("【SOUL】")


def test_prompt_composer_includes_user_identity_doc(monkeypatch):
    monkeypatch.setattr(
        prompt_composer,
        "_load_manager_agents_doc",
        lambda: "",
    )
    monkeypatch.setattr(
        "core.prompt_composer.soul_store.resolve_for_runtime_user",
        lambda _user_id: SoulPayload(
            agent_kind="core-manager",
            agent_id="core-manager",
            path="/tmp/SOUL.MD",
            content="# Core Manager SOUL\n- tone: warm",
            updated_at="2026-03-13T00:00:00+08:00",
            latest_version_id="",
        ),
    )
    monkeypatch.setattr(
        "core.prompt_composer.channel_user_store.load_user_md",
        lambda **_kwargs: "# USER\n- 称呼偏好: 老板",
    )
    monkeypatch.setattr(prompt_composer, "_build_skill_catalog", lambda **_kwargs: "")

    text = prompt_composer.compose_base(
        runtime_user_id="wx-user-1",
        platform="weixin",
    )

    assert "【USER】" in text
    assert "称呼偏好: 老板" in text


def test_prompt_composer_media_image_hides_accounting_hint_when_disabled(monkeypatch):
    monkeypatch.setattr(prompt_composer, "_load_manager_agents_doc", lambda: "")
    monkeypatch.setattr(
        "core.prompt_composer.soul_store.resolve_for_runtime_user",
        lambda _user_id: SoulPayload(
            agent_kind="core-manager",
            agent_id="core-manager",
            path="/tmp/SOUL.MD",
            content="# Core Manager SOUL\n- tone: warm",
            updated_at="2026-03-13T00:00:00+08:00",
            latest_version_id="",
        ),
    )
    monkeypatch.setattr(
        "core.prompt_composer.channel_user_store.load_user_md",
        lambda **_kwargs: "",
    )
    monkeypatch.setattr(
        "core.prompt_composer.is_channel_feature_enabled",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(prompt_composer, "_build_skill_catalog", lambda **_kwargs: "")

    text = prompt_composer.compose_base(
        runtime_user_id="wx-user-1",
        platform="weixin",
        mode="media_image",
    )

    assert "这是一次图片分析请求" in text
    assert "quick_accounting" not in text


def test_prompt_composer_does_not_inject_manager_agents_for_subagent(monkeypatch):
    monkeypatch.setattr(
        prompt_composer,
        "_load_manager_agents_doc",
        lambda: "# Manager Manual\n- manager only",
    )
    monkeypatch.setattr(
        "core.prompt_composer.soul_store.resolve_for_runtime_user",
        lambda _user_id: SoulPayload(
            agent_kind="subagent",
            agent_id="subagent-main",
            path="/tmp/subagent/SOUL.MD",
            content="# Subagent SOUL\n- focused execution",
            updated_at="2026-03-13T00:00:00+08:00",
            latest_version_id="",
        ),
    )
    monkeypatch.setattr(prompt_composer, "_build_skill_catalog", lambda **_kwargs: "")

    text = prompt_composer.compose_base(
        runtime_user_id="subagent::subagent-main::123",
        platform="subagent_kernel",
    )

    assert "【AGENTS】" not in text


def test_prompt_composer_filters_skill_catalog_by_runtime_policy(monkeypatch):
    monkeypatch.setattr(
        "extension.skills.registry.skill_registry.get_skills_summary",
        lambda: [
            {
                "name": "stock_watch",
                "description": "行情查询",
                "allowed_roles": [],
            },
            {
                "name": "deployment_manager",
                "description": "部署管理",
                "allowed_roles": ["manager"],
            },
            {
                "name": "skill_manager",
                "description": "技能治理",
                "allowed_roles": [],
            },
        ],
    )

    def fake_allowed(**kwargs):
        return (
            kwargs["tool_name"] in {"ext_deployment_manager", "ext_skill_manager"},
            {},
        )

    monkeypatch.setattr(
        "core.tool_access_store.tool_access_store.is_tool_allowed",
        fake_allowed,
    )

    text = prompt_composer._build_skill_catalog(
        runtime_user_id="u-1",
        platform="telegram",
    )

    assert "deployment_manager" in text
    assert "skill_manager" in text
    assert "stock_watch" not in text
    assert "以 SOP 为准" in text


def test_prompt_composer_hides_manager_only_roles_from_subagent(monkeypatch):
    monkeypatch.setattr(
        "extension.skills.registry.skill_registry.get_skills_summary",
        lambda: [
            {
                "name": "stock_watch",
                "description": "行情查询",
                "allowed_roles": [],
            },
            {
                "name": "deployment_manager",
                "description": "部署管理",
                "allowed_roles": ["manager"],
            },
            {
                "name": "skill_manager",
                "description": "技能治理",
                "allowed_roles": ["manager"],
            },
        ],
    )
    monkeypatch.setattr(
        "core.tool_access_store.tool_access_store.is_tool_allowed",
        lambda **_kwargs: (True, {}),
    )

    text = prompt_composer._build_skill_catalog(
        runtime_user_id="subagent::subagent-main::u-1",
        platform="subagent_kernel",
    )

    assert "stock_watch" in text
    assert "deployment_manager" not in text
    assert "skill_manager" not in text


def test_prompt_composer_filters_skill_catalog_by_allowed_skill_names(monkeypatch):
    monkeypatch.setattr(
        "extension.skills.registry.skill_registry.get_skills_summary",
        lambda: [
            {
                "name": "stock_watch",
                "description": "行情查询",
                "allowed_roles": [],
            },
            {
                "name": "web_search",
                "description": "网页检索",
                "allowed_roles": [],
            },
        ],
    )
    monkeypatch.setattr(
        "core.tool_access_store.tool_access_store.is_tool_allowed",
        lambda **_kwargs: (True, {}),
    )

    text = prompt_composer._build_skill_catalog(
        runtime_user_id="u-1",
        platform="telegram",
        allowed_skill_names=["web_search"],
    )

    assert "web_search" in text
    assert "stock_watch" not in text


def test_prompt_composer_builds_manager_tool_guidance_from_skill_metadata(
    monkeypatch,
):
    monkeypatch.setattr(
        "core.prompt_composer.tool_registry.get_skill_tools",
        lambda **_kwargs: [
            {
                "name": "repo_workspace",
                "description": "准备工作区",
            },
            {
                "name": "codex_session",
                "description": "编程会话",
            },
        ],
    )
    monkeypatch.setattr(
        "extension.skills.registry.skill_registry.get_tool_export",
        lambda name: {
            "repo_workspace": {
                "prompt_hint": "开发任务先准备 `repo_workspace`。",
            },
            "codex_session": {
                "prompt_hint": "代码实现优先走 `codex_session`。",
            },
        }.get(name),
    )
    monkeypatch.setattr(
        "core.tool_access_store.tool_access_store.is_tool_allowed",
        lambda **_kwargs: (True, {}),
    )

    text = prompt_composer._build_manager_tool_guidance(
        runtime_user_id="u-1",
        platform="telegram",
    )

    assert "repo_workspace" in text
    assert "codex_session" in text


def test_prompt_composer_manager_prompt_emphasizes_direct_dev_toolchain(monkeypatch):
    monkeypatch.setattr(prompt_composer, "_load_manager_agents_doc", lambda: "")
    monkeypatch.setattr(
        "core.prompt_composer.soul_store.resolve_for_runtime_user",
        lambda _user_id: SoulPayload(
            agent_kind="core-manager",
            agent_id="core-manager",
            path="/tmp/SOUL.MD",
            content="# Core Manager SOUL\n- tone: warm",
            updated_at="2026-03-13T00:00:00+08:00",
            latest_version_id="",
        ),
    )
    monkeypatch.setattr(prompt_composer, "_build_skill_catalog", lambda **_kwargs: "")
    monkeypatch.setattr(
        prompt_composer,
        "_build_manager_tool_guidance",
        lambda **_kwargs: "- 开发任务先准备 `repo_workspace`。\n- 代码实现优先走 `codex_session`。",
    )

    text = prompt_composer.compose_base(
        runtime_user_id="u-1",
        platform="telegram",
        mode="manager",
    )

    assert "当用户已经给出足够的创意或风格方向时，不要先追问风格偏好" in text
    assert (
        "仓库开发优先按 `repo_workspace` → `codex_session` → `git_ops` → `gh_cli` 推进"
        in text
    )
    assert "`gh_cli auth_status` 成功只是内部预检" in text


def test_prompt_composer_mentions_waiting_external_and_task_tracker(monkeypatch):
    monkeypatch.setattr(prompt_composer, "_load_manager_agents_doc", lambda: "")
    monkeypatch.setattr(
        "core.prompt_composer.soul_store.resolve_for_runtime_user",
        lambda _user_id: SoulPayload(
            agent_kind="core-manager",
            agent_id="core-manager",
            path="/tmp/SOUL.MD",
            content="# Core Manager SOUL\n- tone: warm",
            updated_at="2026-03-13T00:00:00+08:00",
            latest_version_id="",
        ),
    )
    monkeypatch.setattr(prompt_composer, "_build_skill_catalog", lambda **_kwargs: "")
    monkeypatch.setattr(
        prompt_composer,
        "_build_manager_tool_guidance",
        lambda **_kwargs: "- 用 `task_tracker` 查看和更新未完成任务。",
    )

    text = prompt_composer.compose_base(
        runtime_user_id="u-1",
        platform="telegram",
        mode="manager",
    )

    assert "waiting_external" in text
    assert "task_tracker" in text
    assert "events.jsonl" in text


def test_prompt_composer_manager_contract_blocks_default_memory_file_reads(monkeypatch):
    monkeypatch.setattr(
        prompt_composer,
        "_load_manager_agents_doc",
        lambda: "旧说明：先读 `data/user/MEMORY.md`。",
    )
    monkeypatch.setattr(
        "core.prompt_composer.soul_store.resolve_for_runtime_user",
        lambda _user_id: SoulPayload(
            agent_kind="core-manager",
            agent_id="core-manager",
            path="/tmp/SOUL.MD",
            content="# Core Manager SOUL\n- tone: warm",
            updated_at="2026-03-17T00:00:00+08:00",
            latest_version_id="",
        ),
    )
    monkeypatch.setattr(prompt_composer, "_build_skill_catalog", lambda **_kwargs: "")
    monkeypatch.setattr(
        prompt_composer,
        "_build_manager_tool_guidance",
        lambda **_kwargs: "- 用当前工具完成任务。",
    )

    text = prompt_composer.compose_base(
        runtime_user_id="u-1",
        platform="telegram",
        mode="manager",
    )

    assert "【当前会话上下文约束】" in text
    assert "默认不要再调用 `read` 直接读取长期记忆存储或近期记忆 trace 文件" in text
    assert "本块优先级高于旧文档里任何“先读长期记忆文件”" in text


def test_manager_agents_doc_does_not_expose_auto_loaded_core_files():
    agents_path = Path(__file__).resolve().parents[2] / "config" / "AGENTS.md"
    text = agents_path.read_text(encoding="utf-8")

    assert "Ikaros" in text
    assert "X-Bot" not in text
    assert "subagent" in text
    assert "Worker" not in text
    assert "`data/SOUL.MD`" not in text
    assert "`data/AGENTS.md`" not in text
