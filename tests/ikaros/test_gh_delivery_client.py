import json

import pytest

from ikaros.integrations.gh_delivery_client import GhDeliveryClient
from ikaros.integrations.github_client import GitHubClientError


@pytest.mark.asyncio
async def test_fetch_issue_uses_gh_api_and_normalizes_payload(monkeypatch):
    client = GhDeliveryClient()
    calls = []

    async def fake_exec(*, argv, cwd="", timeout_sec=120):
        _ = (cwd, timeout_sec)
        calls.append(list(argv))
        if "/comments" in str(argv[5]):
            payload = [
                {
                    "id": 9,
                    "user": {"login": "octocat"},
                    "body": "hello",
                    "created_at": "2026-03-12T12:00:00Z",
                    "updated_at": "2026-03-12T12:01:00Z",
                }
            ]
        else:
            payload = {
                "title": "Fix bug",
                "body": "details",
                "state": "open",
                "labels": [{"name": "bug"}, {"name": "urgent"}],
                "html_url": "https://github.com/acme/project/issues/7",
                "pull_request": None,
            }
        return {
            "ok": True,
            "text": json.dumps(payload),
            "data": {"stdout": json.dumps(payload), "stderr": "", "argv": argv},
        }

    monkeypatch.setattr(
        "ikaros.integrations.gh_delivery_client.gh_cli_service.exec", fake_exec
    )

    payload = await client.fetch_issue("acme/project#7")

    assert payload["owner"] == "acme"
    assert payload["repo"] == "project"
    assert payload["number"] == 7
    assert payload["labels"] == ["bug", "urgent"]
    assert payload["comments"][0]["user"] == "octocat"
    assert calls[0][0] == "api"
    assert calls[0][5] == "repos/acme/project/issues/7"
    assert calls[1][5] == "repos/acme/project/issues/7/comments"


@pytest.mark.asyncio
async def test_create_pull_request_uses_gh_api_post(monkeypatch):
    client = GhDeliveryClient()
    captured = {}

    async def fake_exec(*, argv, cwd="", timeout_sec=120):
        _ = (cwd, timeout_sec)
        captured["argv"] = list(argv)
        payload = {
            "number": 18,
            "html_url": "https://github.com/acme/project/pull/18",
            "state": "open",
        }
        return {
            "ok": True,
            "text": json.dumps(payload),
            "data": {"stdout": json.dumps(payload), "stderr": "", "argv": argv},
        }

    monkeypatch.setattr(
        "ikaros.integrations.gh_delivery_client.gh_cli_service.exec", fake_exec
    )

    payload = await client.create_pull_request(
        owner="acme",
        repo="project",
        title="Fix bug",
        head="feature/bugfix",
        base="main",
        body="summary",
    )

    assert payload["number"] == 18
    assert captured["argv"][5] == "repos/acme/project/pulls"
    assert "POST" in captured["argv"]
    assert "title=Fix bug" in captured["argv"]
    assert "head=feature/bugfix" in captured["argv"]


@pytest.mark.asyncio
async def test_create_issue_comment_uses_gh_api_post(monkeypatch):
    client = GhDeliveryClient()
    captured = {}

    async def fake_exec(*, argv, cwd="", timeout_sec=120):
        _ = (cwd, timeout_sec)
        captured["argv"] = list(argv)
        payload = {
            "id": 33,
            "html_url": "https://github.com/acme/project/issues/7#issuecomment-33",
        }
        return {
            "ok": True,
            "text": json.dumps(payload),
            "data": {"stdout": json.dumps(payload), "stderr": "", "argv": argv},
        }

    monkeypatch.setattr(
        "ikaros.integrations.gh_delivery_client.gh_cli_service.exec", fake_exec
    )

    payload = await client.create_issue_comment(
        owner="acme",
        repo="project",
        issue_number=7,
        body="done",
    )

    assert payload["id"] == 33
    assert captured["argv"][5] == "repos/acme/project/issues/7/comments"
    assert "body=done" in captured["argv"]


@pytest.mark.asyncio
async def test_fetch_issue_surfaces_auth_error(monkeypatch):
    client = GhDeliveryClient()

    async def fake_exec(*, argv, cwd="", timeout_sec=120):
        _ = (argv, cwd, timeout_sec)
        return {
            "ok": False,
            "error_code": "not_authenticated",
            "message": "gh auth login required",
            "text": "gh auth login required",
        }

    monkeypatch.setattr(
        "ikaros.integrations.gh_delivery_client.gh_cli_service.exec", fake_exec
    )

    with pytest.raises(GitHubClientError, match="gh_cli` with `action=auth_start"):
        await client.fetch_issue("acme/project#7")
