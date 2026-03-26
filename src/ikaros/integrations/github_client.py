from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List

import httpx


ISSUE_URL_PATTERN = re.compile(
    r"^https?://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/issues/(?P<number>\d+)(?:[/?#].*)?$",
    flags=re.IGNORECASE,
)
ISSUE_SHORT_PATTERN = re.compile(
    r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)#(?P<number>\d+)$",
    flags=re.IGNORECASE,
)
ISSUE_NUMBER_PATTERN = re.compile(r"^#?(?P<number>\d+)$")
REPO_URL_PATTERN = re.compile(
    r"^https?://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$",
    flags=re.IGNORECASE,
)
REPO_SSH_PATTERN = re.compile(
    r"^git@github\.com:(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?$",
    flags=re.IGNORECASE,
)


class GitHubClientError(RuntimeError):
    pass


@dataclass
class GitHubIssueRef:
    owner: str
    repo: str
    number: int


def parse_repo_slug(raw: str) -> tuple[str, str]:
    text = str(raw or "").strip()
    if not text:
        return "", ""

    url_match = REPO_URL_PATTERN.match(text)
    if url_match:
        return (
            str(url_match.group("owner") or "").strip(),
            str(url_match.group("repo") or "").strip(),
        )

    ssh_match = REPO_SSH_PATTERN.match(text)
    if ssh_match:
        return (
            str(ssh_match.group("owner") or "").strip(),
            str(ssh_match.group("repo") or "").strip(),
        )

    if "/" in text and " " not in text and "#" not in text:
        parts = [item.strip() for item in text.split("/", 1)]
        owner = parts[0]
        repo = parts[1].removesuffix(".git")
        if owner and repo:
            return owner, repo
    return "", ""


def parse_issue_reference(
    issue: str,
    *,
    default_owner: str = "",
    default_repo: str = "",
) -> GitHubIssueRef:
    raw = str(issue or "").strip()
    if not raw:
        raise GitHubClientError("issue reference is required")

    url_match = ISSUE_URL_PATTERN.match(raw)
    if url_match:
        return GitHubIssueRef(
            owner=str(url_match.group("owner") or "").strip(),
            repo=str(url_match.group("repo") or "").strip(),
            number=int(url_match.group("number") or "0"),
        )

    short_match = ISSUE_SHORT_PATTERN.match(raw)
    if short_match:
        return GitHubIssueRef(
            owner=str(short_match.group("owner") or "").strip(),
            repo=str(short_match.group("repo") or "").strip(),
            number=int(short_match.group("number") or "0"),
        )

    num_match = ISSUE_NUMBER_PATTERN.match(raw)
    if num_match:
        owner = str(default_owner or "").strip()
        repo = str(default_repo or "").strip()
        if not owner or not repo:
            env_owner = str(os.getenv("GITHUB_DEFAULT_OWNER", "") or "").strip()
            env_repo = str(os.getenv("GITHUB_DEFAULT_REPO", "") or "").strip()
            owner = owner or env_owner
            repo = repo or env_repo
        if not owner or not repo:
            raise GitHubClientError(
                "owner/repo is required when issue reference is only a number"
            )
        return GitHubIssueRef(
            owner=owner,
            repo=repo,
            number=int(num_match.group("number") or "0"),
        )

    raise GitHubClientError(f"unsupported issue reference: {raw}")


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


class GitHubClient:
    def __init__(self) -> None:
        self.api_base = (
            str(os.getenv("GITHUB_API_BASE", "https://api.github.com")).strip()
            or "https://api.github.com"
        )
        self.token = str(os.getenv("GITHUB_TOKEN", "") or "").strip()
        self.allowed_repos = _allowed_repo_set()

    def _repo_key(self, owner: str, repo: str) -> str:
        return f"{str(owner or '').strip().lower()}/{str(repo or '').strip().lower()}"

    def _assert_repo_allowed(self, owner: str, repo: str, *, write: bool) -> None:
        key = self._repo_key(owner, repo)
        if self.allowed_repos and key not in self.allowed_repos:
            raise GitHubClientError(f"repository is not allowed: {key}")
        if write and not self.token:
            raise GitHubClientError("GITHUB_TOKEN is required for write operations")

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ikaros-core-dev",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        payload: Dict[str, Any] | None = None,
        owner: str = "",
        repo: str = "",
        write: bool = False,
    ) -> Any:
        if owner and repo:
            self._assert_repo_allowed(owner, repo, write=write)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.request(
                method=str(method or "GET").upper(),
                url=url,
                headers=self._headers(),
                json=payload,
            )

        if int(response.status_code) >= 400:
            raise GitHubClientError(
                f"github api failed ({response.status_code}): {_truncate(response.text)}"
            )

        if not str(response.text or "").strip():
            return {}

        try:
            return response.json()
        except Exception as exc:
            raise GitHubClientError(f"invalid github response: {exc}") from exc

    async def fetch_repo(self, *, owner: str, repo: str) -> Dict[str, Any]:
        safe_owner = str(owner or "").strip()
        safe_repo = str(repo or "").strip()
        if not safe_owner or not safe_repo:
            raise GitHubClientError("owner/repo is required")

        url = f"{self.api_base}/repos/{safe_owner}/{safe_repo}"
        data = await self._request_json(
            "GET",
            url,
            owner=safe_owner,
            repo=safe_repo,
            write=False,
        )
        if not isinstance(data, dict):
            raise GitHubClientError("unexpected github repo response")

        return {
            "owner": safe_owner,
            "repo": safe_repo,
            "default_branch": str(data.get("default_branch") or "main").strip()
            or "main",
            "html_url": str(data.get("html_url") or "").strip(),
            "private": bool(data.get("private")),
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

        issue_url = f"{self.api_base}/repos/{ref.owner}/{ref.repo}/issues/{ref.number}"
        comments_url = (
            f"{self.api_base}/repos/{ref.owner}/{ref.repo}/issues/{ref.number}/comments"
        )

        issue_data = await self._request_json(
            "GET",
            issue_url,
            owner=ref.owner,
            repo=ref.repo,
            write=False,
        )
        comments_data = await self._request_json(
            "GET",
            comments_url,
            owner=ref.owner,
            repo=ref.repo,
            write=False,
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
        if not safe_owner or not safe_repo:
            raise GitHubClientError("owner/repo is required for create_pull_request")
        safe_title = str(title or "").strip()
        safe_head = str(head or "").strip()
        safe_base = str(base or "").strip()
        if not safe_title or not safe_head or not safe_base:
            raise GitHubClientError(
                "title/head/base are required for create_pull_request"
            )

        url = f"{self.api_base}/repos/{safe_owner}/{safe_repo}/pulls"
        data = await self._request_json(
            "POST",
            url,
            payload={
                "title": safe_title,
                "head": safe_head,
                "base": safe_base,
                "body": str(body or ""),
            },
            owner=safe_owner,
            repo=safe_repo,
            write=True,
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

        url = f"{self.api_base}/repos/{safe_owner}/{safe_repo}/issues/{safe_number}/comments"
        data = await self._request_json(
            "POST",
            url,
            payload={"body": str(body or "")},
            owner=safe_owner,
            repo=safe_repo,
            write=True,
        )
        if not isinstance(data, dict):
            raise GitHubClientError("unexpected github comment response")
        return {
            "id": int(data.get("id") or 0),
            "html_url": str(data.get("html_url") or "").strip(),
        }


github_client = GitHubClient()
