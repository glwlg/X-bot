import types
from pathlib import Path
from types import SimpleNamespace

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from core.runtime_config_store import runtime_config_store
from extension.skills.registry import SkillRegistry


def test_skill_loader_infers_schema_from_parameter_table(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "builtin" / "web_demo"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        """---
api_version: v3
name: web_demo
description: demo
triggers:
  - web
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
  shell: false
  network: limited
entrypoint: scripts/execute.py
---

| 参数名 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `action` | string | 否 | 操作类型: `visit`, `summarize` |
| `queries` | list | 否 | 搜索词列表 |
| `url` | string | 是 | 目标网页 URL |
""",
        encoding="utf-8",
    )

    loader = SkillRegistry(skills_dir=str(tmp_path / "skills"))
    indexed = loader.scan_skills()
    schema = indexed["web_demo"]["input_schema"]

    properties = schema.get("properties") or {}
    assert "url" in properties
    assert properties["url"]["type"] == "string"
    assert "action" in properties
    assert properties["action"]["enum"] == ["visit", "summarize"]
    assert properties["queries"]["type"] == "array"
    assert properties["queries"]["items"]["type"] == "string"
    assert "url" in list(schema.get("required") or [])


def test_skill_loader_infers_schema_from_json_examples(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "builtin" / "search_demo"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        """---
api_version: v3
name: search_demo
description: demo
triggers:
  - search
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
  shell: false
  network: limited
entrypoint: scripts/execute.py
---

```json
{ "query": "latest ai news", "num_results": 5 }
```
""",
        encoding="utf-8",
    )

    loader = SkillRegistry(skills_dir=str(tmp_path / "skills"))
    indexed = loader.scan_skills()
    schema = indexed["search_demo"]["input_schema"]
    properties = schema.get("properties") or {}

    assert properties["query"]["type"] == "string"
    assert properties["num_results"]["type"] == "integer"


def test_skill_loader_keeps_standard_allowed_tools_without_entrypoint(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "builtin" / "playwright_cli"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        """---
name: playwright-cli
description: Browser automation with playwright-cli.
allowed-tools:
  - Bash(playwright-cli:*)
---

# Playwright CLI Skill
""",
        encoding="utf-8",
    )

    loader = SkillRegistry(skills_dir=str(tmp_path / "skills"))
    indexed = loader.scan_skills()
    info = indexed["playwright-cli"]

    assert info["allowed_tools"] == ["Bash(playwright-cli:*)"]
    assert info["entrypoint"] == ""


def test_skill_loader_builds_contract_defaults(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "learned" / "demo_contract"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        """---
api_version: v3
name: demo_contract
description: demo
triggers:
  - demo
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
  shell: true
entrypoint: scripts/execute.py
---

# Demo Skill
""",
        encoding="utf-8",
    )

    loader = SkillRegistry(skills_dir=str(tmp_path / "skills"))
    indexed = loader.scan_skills()
    contract = indexed["demo_contract"]["contract"]

    assert contract["runtime_target"] == "ikaros"
    assert contract["change_level"] == "learned"
    assert contract["allow_ikaros_modify"] is True
    assert contract["allow_auto_publish"] is True
    assert contract["rollout_target"] == "ikaros"


def test_skill_loader_respects_explicit_contract_fields(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "builtin" / "ops_contract"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        """---
api_version: v3
name: ops_contract
description: demo
triggers:
  - ops
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
  shell: true
allowed_roles:
  - ikaros
runtime_target: ikaros
change_level: builtin
allow_ikaros_modify: true
allow_auto_publish: false
rollout_target: api
dependencies:
  - docker
preflight_commands:
  - python scripts/execute.py --help
entrypoint: scripts/execute.py
---

# Ops Skill
""",
        encoding="utf-8",
    )

    loader = SkillRegistry(skills_dir=str(tmp_path / "skills"))
    indexed = loader.scan_skills()
    contract = indexed["ops_contract"]["contract"]

    assert contract["runtime_target"] == "ikaros"
    assert contract["change_level"] == "builtin"
    assert contract["allow_auto_publish"] is False
    assert contract["rollout_target"] == "api"
    assert contract["dependencies"] == ["docker"]
    assert contract["preflight_commands"] == ["python scripts/execute.py --help"]


def test_skill_loader_parses_tool_exports(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "builtin" / "ikaros_ops"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        """---
api_version: v3
name: ikaros_ops
description: ikaros tool demo
allowed_roles:
  - ikaros
tool_exports:
  - name: queue_status
    description: Query queue status
    handler: ikaros.queue.status
    parameters:
      type: object
      properties:
        executor_id:
          type: string
---

# Ikaros Ops
""",
        encoding="utf-8",
    )

    loader = SkillRegistry(skills_dir=str(tmp_path / "skills"))
    indexed = loader.scan_skills()
    exports = indexed["ikaros_ops"]["tool_exports"]

    assert len(exports) == 1
    assert exports[0]["name"] == "queue_status"
    assert exports[0]["handler"] == "ikaros.queue.status"
    assert exports[0]["skill_name"] == "ikaros_ops"
    assert exports[0]["parameters"]["properties"]["executor_id"]["type"] == "string"


def test_skill_loader_parses_policy_groups_and_platform_handlers(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "builtin" / "media_ops"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        """---
api_version: v3
name: media_ops
description: media ops
policy_groups:
  - media
platform_handlers: true
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
entrypoint: scripts/execute.py
---

# Media Ops
""",
        encoding="utf-8",
    )

    loader = SkillRegistry(skills_dir=str(tmp_path / "skills"))
    indexed = loader.scan_skills()
    info = indexed["media_ops"]

    assert info["policy_groups"] == ["group:media"]
    assert info["platform_handlers"] is True


def test_skill_registry_registers_only_skill_extension_subclasses(tmp_path: Path):
    enabled_dir = tmp_path / "skills" / "builtin" / "enabled_skill"
    enabled_dir.mkdir(parents=True, exist_ok=True)
    (enabled_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (enabled_dir / "SKILL.md").write_text(
        """---
api_version: v3
name: enabled_skill
description: enabled
platform_handlers: true
entrypoint: scripts/execute.py
---
""",
        encoding="utf-8",
    )
    (enabled_dir / "scripts" / "execute.py").write_text(
        "from core.extension_base import SkillExtension\n"
        "class EnabledSkill(SkillExtension):\n"
        "    def register(self, runtime):\n"
        "        runtime.adapter_manager.on_command('enabled', lambda *args, **kwargs: None)\n",
        encoding="utf-8",
    )

    disabled_dir = tmp_path / "skills" / "builtin" / "disabled_skill"
    disabled_dir.mkdir(parents=True, exist_ok=True)
    (disabled_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (disabled_dir / "SKILL.md").write_text(
        """---
api_version: v3
name: disabled_skill
description: disabled
entrypoint: scripts/execute.py
---
""",
        encoding="utf-8",
    )
    (disabled_dir / "scripts" / "execute.py").write_text(
        "def register_handlers(adapter_manager):\n"
        "    adapter_manager.on_command('disabled', lambda *args, **kwargs: None)\n",
        encoding="utf-8",
    )

    class _AdapterManager:
        def __init__(self) -> None:
            self.commands: list[str] = []

        def on_command(self, command, handler, description=None):
            _ = (handler, description)
            self.commands.append(str(command))

    loader = SkillRegistry(skills_dir=str(tmp_path / "skills"))
    loader.scan_skills()
    adapter = _AdapterManager()
    loader._register_skill_management = lambda runtime: None

    loader.register_extensions(
        types.SimpleNamespace(
            adapter_manager=adapter,
            scheduler=object(),
        )
    )

    assert adapter.commands == ["enabled"]


def test_builtin_command_skills_remain_opted_in_for_platform_handlers():
    loader = SkillRegistry()
    indexed = loader.scan_skills()

    assert indexed["credential_manager"]["platform_handlers"] is True
    assert indexed["daily_query"]["platform_handlers"] is True
    assert indexed["download_video"]["platform_handlers"] is True
    assert indexed["deployment_manager"]["platform_handlers"] is True
    assert indexed["reminder"]["platform_handlers"] is True
    assert indexed["rss_subscribe"]["platform_handlers"] is True
    assert indexed["scheduler_manager"]["platform_handlers"] is True
    assert indexed["stock_watch"]["platform_handlers"] is True
    assert indexed["video_to_text"]["platform_handlers"] is True


def test_skill_loader_get_skill_accepts_hyphen_underscore_aliases(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "learned" / "union_search_skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        """---
api_version: v3
name: union_search_skill
description: demo
triggers:
  - search
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
entrypoint: scripts/execute.py
---
""",
        encoding="utf-8",
    )

    loader = SkillRegistry(skills_dir=str(tmp_path / "skills"))
    loader.scan_skills()

    assert loader.get_skill("union_search_skill")["name"] == "union_search_skill"
    assert loader.get_skill("union-search-skill")["name"] == "union_search_skill"


def test_skill_loader_refreshes_when_skill_tree_changes(tmp_path: Path):
    root = tmp_path / "skills"
    first_dir = root / "learned" / "first_skill"
    first_dir.mkdir(parents=True, exist_ok=True)
    (first_dir / "SKILL.md").write_text(
        """---
api_version: v3
name: first_skill
description: first
triggers: [first]
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
entrypoint: scripts/execute.py
---
""",
        encoding="utf-8",
    )

    loader = SkillRegistry(skills_dir=str(root))
    assert loader.get_skill("first_skill")["name"] == "first_skill"
    assert loader.get_skill("second_skill") is None

    second_dir = root / "learned" / "second_skill"
    second_dir.mkdir(parents=True, exist_ok=True)
    (second_dir / "SKILL.md").write_text(
        """---
api_version: v3
name: second_skill
description: second
triggers: [second]
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
entrypoint: scripts/execute.py
---
""",
        encoding="utf-8",
    )

    assert loader.get_skill("second_skill")["name"] == "second_skill"


def test_skill_loader_imports_relative_script_skills_and_registers_jobs():
    repo_root = Path(__file__).resolve().parents[2]
    loader = SkillRegistry(skills_dir=str(repo_root / "extension" / "skills"))
    loader.scan_skills()

    stock_module = loader.import_skill_module("stock_watch")
    download_module = loader.import_skill_module("download_video")

    assert stock_module is not None
    assert download_module is not None

    class _FakeAdapterManager:
        def on_command(self, command, handler, description=None):
            _ = (command, handler, description)

        def on_callback_query(self, pattern, handler):
            _ = (pattern, handler)

    scheduler = AsyncIOScheduler()
    runtime = SimpleNamespace(
        scheduler=scheduler,
        adapter_manager=_FakeAdapterManager(),
    )

    stock_module.StockWatchSkillExtension().register(runtime)

    assert scheduler.get_job("skill_stock_watch_push") is not None


def test_skill_loader_filters_disabled_skills_from_enabled_views(tmp_path: Path, monkeypatch):
    runtime_config_path = (tmp_path / "runtime" / "runtime-config.json").resolve()
    runtime_config_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(runtime_config_store, "path", runtime_config_path)

    disabled_dir = tmp_path / "skills" / "learned" / "disabled_demo"
    disabled_dir.mkdir(parents=True, exist_ok=True)
    (disabled_dir / "SKILL.md").write_text(
        """---
api_version: v3
name: disabled_demo
description: disabled demo
triggers:
  - disabled
tool_exports:
  - name: disabled_demo_tool
    description: disabled tool
    handler: demo.disabled
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
entrypoint: scripts/execute.py
---
""",
        encoding="utf-8",
    )
    (disabled_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (disabled_dir / "scripts" / "execute.py").write_text(
        "VALUE = 'disabled'\n",
        encoding="utf-8",
    )

    enabled_dir = tmp_path / "skills" / "learned" / "enabled_demo"
    enabled_dir.mkdir(parents=True, exist_ok=True)
    (enabled_dir / "SKILL.md").write_text(
        """---
api_version: v3
name: enabled_demo
description: enabled demo
triggers:
  - enabled
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
entrypoint: scripts/execute.py
---
""",
        encoding="utf-8",
    )
    (enabled_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (enabled_dir / "scripts" / "execute.py").write_text(
        "VALUE = 'enabled'\n",
        encoding="utf-8",
    )

    runtime_config_store.set_skill_enabled(
        "disabled_demo",
        False,
        actor="test",
        reason="disable_for_test",
    )

    loader = SkillRegistry(skills_dir=str(tmp_path / "skills"))
    loader.scan_skills()

    assert loader.get_skill("disabled_demo") is not None
    assert loader.get_enabled_skill("disabled_demo") is None
    assert set(loader.get_enabled_skill_index()) == {"enabled_demo"}
    assert [item["name"] for item in loader.get_skills_summary()] == ["enabled_demo"]
    assert loader.get_tool_export("disabled_demo_tool") is None
    assert loader.import_skill_module("disabled_demo") is None

    raw_module = loader.import_skill_module("disabled_demo", include_disabled=True)
    assert raw_module is not None
    assert getattr(raw_module, "VALUE", "") == "disabled"
