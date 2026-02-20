from pathlib import Path

from core.skill_loader import SkillLoader


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

    loader = SkillLoader(skills_dir=str(tmp_path / "skills"))
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

    loader = SkillLoader(skills_dir=str(tmp_path / "skills"))
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

    loader = SkillLoader(skills_dir=str(tmp_path / "skills"))
    indexed = loader.scan_skills()
    info = indexed["playwright-cli"]

    assert info["allowed_tools"] == ["Bash(playwright-cli:*)"]
    assert info["entrypoint"] == ""
