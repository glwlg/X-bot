import importlib.util
import os
from pathlib import Path

import pytest

os.environ.setdefault("LLM_API_KEY", "test-key")


def _load_module():
    module_path = Path("skills/builtin/deployment_manager/scripts/execute.py")
    spec = importlib.util.spec_from_file_location(
        "deployment_manager_execute", module_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_extract_service_from_request_generic():
    module = _load_module()
    service = module._extract_service_from_request("帮我部署一套my service", "")
    assert service == "my-service"


def test_display_host_sanitization():
    module = _load_module()
    assert module._sanitize_display_host("1") == ""
    assert module._sanitize_display_host("localhost") == "localhost"
    assert module._sanitize_display_host("192.168.1.100") == "192.168.1.100"
    assert module._sanitize_display_host("'192.168.1.100'") == "192.168.1.100"
    assert module._sanitize_display_host("https://example.com/path") == "example.com"


def test_resolve_project_path_enforces_workspace():
    module = _load_module()
    resolved = module._resolve_project_path("/tmp/outside", "demo")
    base = module.WORK_BASE.resolve()
    assert str(resolved).startswith(str(base))


def test_extract_published_host_ports():
    module = _load_module()
    ports = module._extract_published_host_ports(
        "0.0.0.0:25678->5678/tcp, [::]:25678->5678/tcp, 127.0.0.1:35678->5678/tcp"
    )
    assert ports == [25678, 35678]


@pytest.mark.asyncio
async def test_search_repo_and_guides_prefers_github(monkeypatch):
    module = _load_module()

    async def fake_search(query, language="zh-CN", num_results=8):
        return [
            {
                "url": "https://docs.example.com/deploy/docker/",
                "title": "App Docker docs",
                "content": "official docker compose guide",
            },
            {
                "url": "https://github.com/example-org/example-app",
                "title": "example-app repository",
                "content": "official repository",
            },
        ]

    monkeypatch.setattr(module, "_search_searxng", fake_search)
    result = await module._search_repo_and_guides(
        "帮我部署一套example app", "example-app"
    )

    assert result["repo_url"] == "https://github.com/example-org/example-app.git"
    assert result["guides"]


@pytest.mark.asyncio
async def test_search_repo_prefers_exact_repo_name_over_fork(monkeypatch):
    module = _load_module()

    async def fake_search(query, language="zh-CN", num_results=8):
        return [
            {
                "url": "https://github.com/other-blowsnow/n8n-i18n-chinese",
                "title": "n8n i18n mirror",
                "content": "community mirror",
            },
            {
                "url": "https://github.com/n8n-io/n8n",
                "title": "n8n official repository",
                "content": "official",
            },
        ]

    monkeypatch.setattr(module, "_search_searxng", fake_search)
    result = await module._search_repo_and_guides("帮我部署一套n8n", "n8n")
    assert result["repo_url"] == "https://github.com/n8n-io/n8n.git"


@pytest.mark.asyncio
async def test_auto_deploy_retries_after_compose_placeholder_fix(monkeypatch, tmp_path):
    module = _load_module()

    project_dir = tmp_path / "n8n"
    project_dir.mkdir(parents=True, exist_ok=True)
    compose_file = project_dir / "docker-compose.yml"
    compose_file.write_text(
        "services:\n  app:\n    image: n8nio/n8n:{version}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        module, "_resolve_project_path", lambda *_args, **_kwargs: project_dir
    )

    async def fake_clone_repo(_params):
        return {"text": "✅ 仓库克隆成功", "ui": {}}

    call_state = {"up_count": 0}

    async def fake_run_shell(command, cwd, timeout=120):
        assert cwd == project_dir
        if " up -d" in command:
            if call_state["up_count"] == 0:
                call_state["up_count"] += 1
                return (
                    1,
                    "unable to get image 'n8nio/n8n:{version}': invalid reference format",
                )
            return 0, "container started"
        if " ps" in command:
            return 0, "service Up 2 seconds"
        return 0, ""

    monkeypatch.setattr(module, "_clone_repo", fake_clone_repo)
    monkeypatch.setattr(module, "_run_shell", fake_run_shell)

    result = await module._auto_deploy(
        {
            "request": "帮我部署一套n8n",
            "repo_url": "https://github.com/n8n-io/n8n.git",
            "host_port": 23011,
        }
    )

    assert result["success"] is True
    assert "自动修复" in result["text"]
    assert "{version}" not in compose_file.read_text(encoding="utf-8")
    assert "latest" in compose_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_clone_repo_switches_directory_on_remote_mismatch(monkeypatch, tmp_path):
    module = _load_module()

    project_dir = tmp_path / "uptime-kuma"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".git").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        module, "_resolve_project_path", lambda *_args, **_kwargs: project_dir
    )

    async def fake_read_git_remote_url(_path):
        return "https://github.com/alice39s/kuma-mieru.git"

    captured = {}

    async def fake_do_clone(repo_url, target_path):
        captured["repo_url"] = repo_url
        captured["target_path"] = str(target_path)
        return {
            "text": "✅ 仓库克隆成功: uptime-kuma",
            "ui": {},
            "success": True,
            "project_path": str(target_path),
            "project_name": "uptime-kuma",
        }

    monkeypatch.setattr(module, "_read_git_remote_url", fake_read_git_remote_url)
    monkeypatch.setattr(module, "_do_clone", fake_do_clone)

    result = await module._clone_repo(
        {"repo_url": "https://github.com/louislam/uptime-kuma.git"}
    )

    assert result["success"] is True
    assert "已自动切换到新目录克隆" in result["text"]
    assert captured["repo_url"] == "https://github.com/louislam/uptime-kuma.git"
    assert captured["target_path"] != str(project_dir)


@pytest.mark.asyncio
async def test_auto_deploy_switches_to_next_candidate_when_first_missing_compose(
    monkeypatch, tmp_path
):
    module = _load_module()

    async def fake_search_repo_and_guides(*_args, **_kwargs):
        return {
            "queries": ["demo docker compose"],
            "repo_url": "https://github.com/org/no-compose.git",
            "repo_source": "https://github.com/org/no-compose",
            "repo_candidates": [
                {
                    "repo_url": "https://github.com/org/no-compose.git",
                    "source_url": "https://github.com/org/no-compose",
                    "score": 10,
                },
                {
                    "repo_url": "https://github.com/org/with-compose.git",
                    "source_url": "https://github.com/org/with-compose",
                    "score": 9,
                },
            ],
            "guides": [],
        }

    monkeypatch.setattr(module, "_search_repo_and_guides", fake_search_repo_and_guides)

    monkeypatch.setattr(
        module,
        "_resolve_project_path",
        lambda target_dir, repo_name: (tmp_path / (target_dir or repo_name)).resolve(),
    )

    async def fake_clone_repo(params):
        project_path = Path(params["target_dir"]).resolve()
        project_path.mkdir(parents=True, exist_ok=True)
        repo_url = params.get("repo_url", "")
        if repo_url.endswith("with-compose.git"):
            (project_path / "docker-compose.yml").write_text(
                "services:\n  app:\n    image: nginx:stable-alpine\n",
                encoding="utf-8",
            )
        return {
            "text": "✅ 仓库克隆成功",
            "ui": {},
            "success": True,
            "project_path": str(project_path),
            "project_name": project_path.name,
        }

    async def fake_run_shell(command, cwd, timeout=120):
        if " up -d" in command:
            return 0, "container started"
        if " ps" in command:
            return 0, "service Up"
        return 0, ""

    monkeypatch.setattr(module, "_clone_repo", fake_clone_repo)
    monkeypatch.setattr(module, "_run_shell", fake_run_shell)

    result = await module._auto_deploy({"request": "帮我部署 demo", "host_port": 23012})

    assert result["success"] is True
    assert "自动切换到" in result["text"]
    assert "with-compose.git" in result["text"]


@pytest.mark.asyncio
async def test_auto_deploy_uses_github_fallback_when_searxng_empty(
    monkeypatch, tmp_path
):
    module = _load_module()

    async def fake_search_repo_and_guides(*_args, **_kwargs):
        return {
            "queries": ["demo docker compose"],
            "repo_url": "",
            "repo_source": "",
            "repo_candidates": [],
            "guides": [],
        }

    async def fake_search_repo_candidates_via_github(*_args, **_kwargs):
        return [
            {
                "repo_url": "https://github.com/org/demo-compose.git",
                "source_url": "https://github.com/org/demo-compose",
                "score": 20,
            }
        ]

    monkeypatch.setattr(module, "_search_repo_and_guides", fake_search_repo_and_guides)
    monkeypatch.setattr(
        module,
        "_search_repo_candidates_via_github",
        fake_search_repo_candidates_via_github,
    )
    monkeypatch.setattr(
        module,
        "_resolve_project_path",
        lambda target_dir, repo_name: (tmp_path / (target_dir or repo_name)).resolve(),
    )

    async def fake_clone_repo(params):
        project_path = Path(params["target_dir"]).resolve()
        project_path.mkdir(parents=True, exist_ok=True)
        (project_path / "docker-compose.yml").write_text(
            "services:\n  app:\n    image: nginx:stable-alpine\n",
            encoding="utf-8",
        )
        return {
            "text": "✅ 仓库克隆成功",
            "ui": {},
            "success": True,
            "project_path": str(project_path),
            "project_name": project_path.name,
        }

    async def fake_run_shell(command, cwd, timeout=120):
        if " up -d" in command:
            return 0, "container started"
        if " ps" in command:
            return 0, "service Up"
        return 0, ""

    monkeypatch.setattr(module, "_clone_repo", fake_clone_repo)
    monkeypatch.setattr(module, "_run_shell", fake_run_shell)

    result = await module._auto_deploy({"request": "帮我部署 demo", "host_port": 23013})

    assert result["success"] is True
    assert "GitHub API 通用兜底搜索" in result["text"]
