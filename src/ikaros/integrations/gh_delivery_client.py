from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from ikaros.integrations.gh_cli_service import gh_cli_service
from ikaros.integrations.github_client import GitHubClientError, parse_issue_reference


def _allowed_repo_set() -> set[str]:
    raw = str(os.getenv("GITHUB_ALLOWED_REPOS", "") or "").strip()
    if not raw:
        return set()
    rows = [item.strip().lower() for item in raw.split(",") if item.strip()]
    return set(rows)


def _truncate(text: str, limit: int = 800) -> str:
    payload = str(text or "")
    if len(payload) <= limit:
        return payload
    return payload[:limit].rstrip() + "..."


class GhDeliveryClient:
    def __init__(self) -> None:
        self.allowed_repos = _allowed_repo_set()

    @staticmethod
    def _repo_key(owner: str, repo: str) -> str:
        return f"{str(owner or '').strip().lower()}/{str(repo or '').strip().lower()}"

    def _assert_repo_allowed(self, owner: str, repo: str) -> None:
        key = self._repo_key(owner, repo)
        if self.allowed_repos and key not in self.allowed_repos:
            raise GitHubClientError(f"repository is not allowed: {key}")

    @staticmethod
    def _stdout_from_exec(result: Dict[str, Any]) -> str:
        data = dict(result.get("data") or {})
        stdout = str(data.get("stdout") or "")
        if stdout.strip():
            return stdout
        return str(result.get("text") or "")

    @staticmethod
    def _raise_exec_error(result: Dict[str, Any], *, fallback: str) -> None:
        error_code = str(result.get("error_code") or "").strip().lower()
        if error_code == "not_authenticated":
            raise GitHubClientError(
                "GitHub CLI is not authenticated. Please run `gh_cli` with `action=auth_start` first."
            )
        message = (
            str(result.get("message") or "").strip()
            or str(result.get("text") or "").strip()
            or str(result.get("summary") or "").strip()
            or fallback
        )
        raise GitHubClientError(message)

    @classmethod
    def _json_from_exec(cls, result: Dict[str, Any], *, fallback: str) -> Any:
        if not bool(result.get("ok")):
            cls._raise_exec_error(result, fallback=fallback)
        stdout = cls._stdout_from_exec(result)
        if not stdout.strip():
            raise GitHubClientError(fallback)
        try:
            return json.loads(stdout)
        except Exception as exc:
            raise GitHubClientError(f"invalid gh json response: {exc}") from exc

    @staticmethod
    def _looks_like_not_found(result: Dict[str, Any]) -> bool:
        text = "\n".join(
            [
                str(result.get("message") or ""),
                str(result.get("text") or ""),
                str(result.get("summary") or ""),
            ]
        ).lower()
        return "404" in text or "not found" in text

    async def _gh_api_exec(
        self,
        *,
        path: str,
        method: str = "GET",
        fields: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        argv: List[str] = [
            "api",
            "--hostname",
            "github.com",
            "--header",
            "Accept: application/vnd.github+json",
            str(path or "").strip(),
            "--method",
            str(method or "GET").strip().upper() or "GET",
        ]
        for key, value in dict(fields or {}).items():
            argv.extend(["--raw-field", f"{str(key).strip()}={str(value or '')}"])
        return await gh_cli_service.exec(argv=argv)

    async def _gh_api_json(
        self,
        *,
        path: str,
        method: str = "GET",
        fields: Dict[str, Any] | None = None,
    ) -> Any:
        result = await self._gh_api_exec(path=path, method=method, fields=fields)
        return self._json_from_exec(result, fallback="gh api request failed")

    async def get_authenticated_user(self) -> Dict[str, Any]:
        data = await self._gh_api_json(path="user")
        if not isinstance(data, dict):
            raise GitHubClientError("unexpected github user response")
        login = str(data.get("login") or "").strip()
        if not login:
            raise GitHubClientError("unable to resolve authenticated GitHub user")
        return {
            "login": login,
            "html_url": str(data.get("html_url") or "").strip(),
            "id": int(data.get("id") or 0),
        }

    async def ensure_fork(self, *, owner: str, repo: str) -> Dict[str, Any]:
        safe_owner = str(owner or "").strip()
        safe_repo = str(repo or "").strip()
        if not safe_owner or not safe_repo:
            raise GitHubClientError("owner/repo is required for ensure_fork")

        viewer = await self.get_authenticated_user()
        viewer_login = str(viewer.get("login") or "").strip()
        existing = await self._gh_api_exec(path=f"repos/{viewer_login}/{safe_repo}")
        if bool(existing.get("ok")):
            payload = self._json_from_exec(
                existing, fallback="existing fork lookup failed"
            )
            if not isinstance(payload, dict):
                raise GitHubClientError("unexpected fork repository response")
            parent_full_name = str(
                ((payload.get("parent") or {}).get("full_name")) or ""
            ).strip()
            source_full_name = str(
                ((payload.get("source") or {}).get("full_name")) or ""
            ).strip()
            upstream_full_name = f"{safe_owner}/{safe_repo}"
            if parent_full_name and parent_full_name != upstream_full_name:
                raise GitHubClientError(
                    f"repository {viewer_login}/{safe_repo} already exists but is not a fork of {upstream_full_name}"
                )
            if source_full_name and source_full_name != upstream_full_name:
                raise GitHubClientError(
                    f"repository {viewer_login}/{safe_repo} points to unexpected source {source_full_name}"
                )
            return {
                "owner": viewer_login,
                "repo": safe_repo,
                "full_name": str(payload.get("full_name") or "").strip(),
                "html_url": str(payload.get("html_url") or "").strip(),
                "default_branch": str(payload.get("default_branch") or "").strip(),
                "created": False,
            }

        if not self._looks_like_not_found(existing):
            self._raise_exec_error(existing, fallback="fork lookup failed")

        created = await self._gh_api_json(
            path=f"repos/{safe_owner}/{safe_repo}/forks",
            method="POST",
            fields={"default_branch_only": "true"},
        )
        if not isinstance(created, dict):
            raise GitHubClientError("unexpected fork creation response")
        return {
            "owner": viewer_login,
            "repo": safe_repo,
            "full_name": str(created.get("full_name") or "").strip(),
            "html_url": str(created.get("html_url") or "").strip(),
            "default_branch": str(created.get("default_branch") or "").strip(),
            "created": True,
        }

    async def fetch_issue(
        self,
        issue: str,
        *,
        default_owner: str = "",
        default_repo: str = "",
    ) -> Dict[str, Any]:
        ref = parse_issue_reference(
            issue,
            default_owner=default_owner,
            default_repo=default_repo,
        )
        self._assert_repo_allowed(ref.owner, ref.repo)

        issue_data = await self._gh_api_json(
            path=f"repos/{ref.owner}/{ref.repo}/issues/{ref.number}",
        )
        comments_data = await self._gh_api_json(
            path=f"repos/{ref.owner}/{ref.repo}/issues/{ref.number}/comments",
        )

        if not isinstance(issue_data, dict):
            raise GitHubClientError("unexpected github issue response")

        labels_raw = issue_data.get("labels")
        labels: List[str] = []
        if isinstance(labels_raw, list):
            for item in labels_raw:
                if isinstance(item, dict):
                    token = str(item.get("name") or "").strip()
                else:
                    token = str(item or "").strip()
                if token and token not in labels:
                    labels.append(token)

        comments: List[Dict[str, Any]] = []
        if isinstance(comments_data, list):
            for row in comments_data:
                if not isinstance(row, dict):
                    continue
                comments.append(
                    {
                        "id": int(row.get("id") or 0),
                        "user": str(
                            ((row.get("user") or {}).get("login")) or ""
                        ).strip(),
                        "body": str(row.get("body") or ""),
                        "created_at": str(row.get("created_at") or "").strip(),
                        "updated_at": str(row.get("updated_at") or "").strip(),
                    }
                )

        return {
            "owner": ref.owner,
            "repo": ref.repo,
            "number": ref.number,
            "title": str(issue_data.get("title") or "").strip(),
            "body": str(issue_data.get("body") or ""),
            "state": str(issue_data.get("state") or "").strip(),
            "labels": labels,
            "html_url": str(issue_data.get("html_url") or "").strip(),
            "is_pull_request": bool(issue_data.get("pull_request")),
            "comments": comments,
        }

    async def create_pull_request(
        self,
        *,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str,
    ) -> Dict[str, Any]:
        safe_owner = str(owner or "").strip()
        safe_repo = str(repo or "").strip()
        safe_title = str(title or "").strip()
        safe_head = str(head or "").strip()
        safe_base = str(base or "").strip()
        if not safe_owner or not safe_repo:
            raise GitHubClientError("owner/repo is required for create_pull_request")
        if not safe_title or not safe_head or not safe_base:
            raise GitHubClientError(
                "title/head/base are required for create_pull_request"
            )
        self._assert_repo_allowed(safe_owner, safe_repo)

        data = await self._gh_api_json(
            path=f"repos/{safe_owner}/{safe_repo}/pulls",
            method="POST",
            fields={
                "title": safe_title,
                "head": safe_head,
                "base": safe_base,
                "body": str(body or ""),
            },
        )
        if not isinstance(data, dict):
            raise GitHubClientError("unexpected github pull request response")
        return {
            "number": int(data.get("number") or 0),
            "html_url": str(data.get("html_url") or "").strip(),
            "state": str(data.get("state") or "").strip(),
        }

    async def create_issue_comment(
        self,
        *,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
    ) -> Dict[str, Any]:
        safe_owner = str(owner or "").strip()
        safe_repo = str(repo or "").strip()
        safe_number = int(issue_number or 0)
        if not safe_owner or not safe_repo or safe_number <= 0:
            raise GitHubClientError("owner/repo/issue_number are required")
        self._assert_repo_allowed(safe_owner, safe_repo)

        data = await self._gh_api_json(
            path=f"repos/{safe_owner}/{safe_repo}/issues/{safe_number}/comments",
            method="POST",
            fields={"body": str(body or "")},
        )
        if not isinstance(data, dict):
            raise GitHubClientError("unexpected github comment response")
        return {
            "id": int(data.get("id") or 0),
            "html_url": str(data.get("html_url") or "").strip(),
        }

    async def check_auth(self) -> Dict[str, Any]:
        result = await gh_cli_service.auth_status(hostname="github.com")
        if not bool(result.get("ok")):
            self._raise_exec_error(result, fallback="gh auth status failed")
        data = dict(result.get("data") or {})
        auth_status = dict(data.get("auth_status") or {})
        authenticated = bool(auth_status.get("authenticated"))
        return {
            "authenticated": authenticated,
            "text": str(auth_status.get("text") or result.get("text") or "").strip(),
        }

    def format_auth_error(self, action: str) -> GitHubClientError:
        auth_hint = (
            f"GitHub CLI is not authenticated, cannot {action}. "
            "Please run `gh_cli` with `action=auth_start` first."
        )
        return GitHubClientError(_truncate(auth_hint, 600))


gh_delivery_client = GhDeliveryClient()
