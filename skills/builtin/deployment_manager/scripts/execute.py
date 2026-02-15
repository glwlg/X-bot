"""
Deployment Manager Skill - 基础操作模块

提供部署相关的基础文件操作，供 Skill Agent 调度使用。
Agent 通过 SKILL.md 中定义的 SOP 编排 searxng_search、web_browser、docker_ops 完成部署。
"""

import asyncio
import logging
import os
import re
import shutil
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

from core.config import (
    X_DEPLOYMENT_STAGING_PATH,
    is_user_allowed,
    SERVER_IP,
)
from core.platform.models import UnifiedContext

logger = logging.getLogger(__name__)
DEFAULT_HOST_PORT = 20080
COMPOSE_FILENAMES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)

def _is_valid_ipv4(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(part) <= 255 for part in parts)
    except Exception:
        return False


def _sanitize_display_host(raw_value: str) -> str:
    raw = str(raw_value or "").strip().strip("\"'")
    if not raw:
        return ""

    parsed = raw
    if raw.startswith(("http://", "https://")):
        parsed = (urlparse(raw).hostname or "").strip()
    if not parsed:
        return ""

    lowered = parsed.lower()
    if lowered == "localhost":
        return "localhost"
    if _is_valid_ipv4(parsed):
        return parsed
    # Allow domain-like hostnames (must contain dot to avoid accidental '1' etc.)
    if "." in parsed and re.fullmatch(r"[a-zA-Z0-9.-]+", parsed):
        return parsed
    return ""


def _resolve_display_host() -> str:
    for candidate in (
        SERVER_IP,
        os.getenv("PUBLIC_HOST", ""),
        os.getenv("PUBLIC_IP", ""),
        os.getenv("HOST_IP", ""),
    ):
        host = _sanitize_display_host(candidate)
        if host:
            return host
    return "localhost"


# 工作目录 - 必须是宿主机绝对路径
if not X_DEPLOYMENT_STAGING_PATH:
    logger.warning(
        "⚠️ X_DEPLOYMENT_STAGING_PATH 未配置！部署功能可能无法正常工作。"
        "请在 .env 中设置为宿主机绝对路径。"
    )
    WORK_BASE = Path("/tmp/deployment_staging")  # Fallback, 不推荐
else:
    WORK_BASE = Path(X_DEPLOYMENT_STAGING_PATH)

WORK_BASE.mkdir(parents=True, exist_ok=True)


async def execute(ctx: UnifiedContext, params: dict, runtime=None):
    """
    执行部署管理器的基础操作。

    可用 action:
    - auto_deploy: 自动部署常见服务/仓库
    - status: 查看已部署项目
    - delete_project: 删除项目目录（谨慎）
    - get_access_info: 获取项目访问信息
    - verify_access: 检查服务是否可访问
    """
    action = params.get("action", "status")

    if action == "auto_deploy":
        return await _auto_deploy(params)

    elif action == "status":
        return await _get_status()

    elif action == "delete_project":
        return await _delete_project(params)

    elif action == "get_access_info":
        return await _get_access_info(params)

    elif action == "verify_access":
        return await _verify_access(params)

    else:
        return {
            "text": (
                f"❌ 未知操作: {action}。"
                "支持: auto_deploy, status, delete_project, get_access_info, verify_access"
            ),
            "ui": {},
        }


def _extract_repo_name(repo_url: str) -> str:
    return repo_url.rstrip("/").split("/")[-1].replace(".git", "")


def _resolve_project_path(target_dir: str | None, repo_name: str) -> Path:
    raw = (target_dir or repo_name or "").strip() or repo_name
    base = WORK_BASE.resolve()
    target_path = Path(raw)
    if target_path.is_absolute():
        resolved = target_path.resolve()
    else:
        resolved = (base / target_path).resolve()

    # Enforce path standard: deployment workspace only.
    if not str(resolved).startswith(str(base)):
        return (base / repo_name).resolve()
    return resolved


def _extract_repo_url_from_text(text: str) -> str:
    if not text:
        return ""
    match = re.search(r"https?://[^\s)]+", text)
    if not match:
        return ""
    return match.group(0).rstrip(".,);")


def _find_compose_file(project_path: Path) -> Path | None:
    for filename in COMPOSE_FILENAMES:
        candidate = project_path / filename
        if candidate.exists():
            return candidate
    return None


def _normalize_service_name(value: str) -> str:
    cleaned = (value or "").strip(" ,，。.!！?？")
    cleaned = re.sub(r"(服务|系统|平台)$", "", cleaned)
    cleaned = re.sub(r"\s+", "-", cleaned.strip())
    return cleaned.lower()


def _canonical_service_key(value: str) -> str:
    return _normalize_service_name(value)


def _extract_service_from_request(request_text: str, explicit_service: str = "") -> str:
    if explicit_service:
        normalized = _canonical_service_key(explicit_service)
        if normalized:
            return normalized

    text = request_text.strip()
    patterns = [
        r"(?:部署|安装|搭建|启动)\s*(?:一套|一个|个|套)?\s*([a-zA-Z0-9._\-\u4e00-\u9fff]+(?:\s+[a-zA-Z0-9._\-\u4e00-\u9fff]+)?)",
        r"(?:deploy|install|setup)\s+([a-zA-Z0-9._\-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidate = _canonical_service_key(match.group(1))
            if candidate:
                return candidate

    return _canonical_service_key(explicit_service)


def _normalize_host_port(raw_value: object, default: int) -> int:
    try:
        port = int(raw_value)
    except Exception:
        return default
    if 20000 <= port <= 60000:
        return port
    return default


def _extract_published_host_ports(ps_output: str) -> list[int]:
    ports: list[int] = []
    raw = str(ps_output or "")
    for match in re.finditer(
        r"(?:0\.0\.0\.0|\[::\]|::|localhost|127\.0\.0\.1):(\d{2,5})\s*->",
        raw,
        flags=re.IGNORECASE,
    ):
        try:
            port = int(match.group(1))
        except Exception:
            continue
        if 1 <= port <= 65535 and port not in ports:
            ports.append(port)
    return ports


def _build_access_urls(host_port: int) -> tuple[str, str]:
    display_host = _resolve_display_host()
    local_url = f"http://127.0.0.1:{host_port}"
    public_url = f"http://{display_host}:{host_port}"
    if display_host in {"localhost", "127.0.0.1"}:
        return local_url, local_url
    return local_url, public_url


def _normalize_github_repo_url(url: str) -> str:
    if not url:
        return ""
    match = re.match(r"^https?://github\.com/([^/\s]+)/([^/\s#?]+)", url.strip())
    if not match:
        return ""
    owner = match.group(1)
    repo = match.group(2).replace(".git", "")
    if repo.lower() in {"issues", "pull", "pulls", "releases", "tags", "wiki"}:
        return ""
    return f"https://github.com/{owner}/{repo}.git"


def _split_github_repo(repo_url: str) -> tuple[str, str]:
    match = re.match(r"^https?://github\.com/([^/\s]+)/([^/\s#?]+)", repo_url.strip())
    if not match:
        return "", ""
    owner = match.group(1).strip().lower()
    repo = match.group(2).replace(".git", "").strip().lower()
    return owner, repo


def _compact_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _safe_suffix(value: str, default: str = "repo") -> str:
    suffix = re.sub(r"[^a-z0-9-]+", "-", str(value or "").strip().lower()).strip("-")
    return suffix or default


def _build_conflict_clone_path(target_path: Path, repo_url: str) -> Path:
    owner, _repo = _split_github_repo(repo_url)
    suffix = _safe_suffix(owner, default="fresh")
    base_name = f"{target_path.name}-{suffix}"
    candidate = target_path.parent / base_name
    index = 1
    while candidate.exists():
        candidate = target_path.parent / f"{base_name}-{index}"
        index += 1
    return candidate


def _has_redeploy_confirmation(text: str) -> bool:
    """
    Check whether user explicitly confirms redeploy.
    Avoid matching generic "部署" wording.
    """
    raw = (text or "").strip().lower()
    if not raw:
        return False
    confirmations = (
        "继续重部署",
        "确认重部署",
        "重新部署",
        "重部署",
        "redeploy",
        "force redeploy",
    )
    return any(token in raw for token in confirmations)


def _classify_failure_mode(output: str) -> str:
    lowered = str(output or "").lower()
    recoverable_tokens = (
        "env file",
        ".env not found",
        "no such file or directory",
        "address already in use",
        "port is already allocated",
        "bind for",
        "name is already in use",
        "conflict",
    )
    if any(token in lowered for token in recoverable_tokens):
        return "recoverable"
    return "fatal"


async def _search_searxng(query: str, language: str = "zh-CN", num_results: int = 8) -> list[dict]:
    base_url = os.getenv("SEARXNG_URL", "").strip()
    if not base_url:
        return []

    if not base_url.endswith("/search"):
        if not base_url.endswith("/"):
            base_url += "/"
        base_url += "search"

    search_url = (
        f"{base_url}?q={quote(query)}&format=json"
        f"&categories=general,it,science&language={language}&time_range=year"
    )

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(search_url)
            response.raise_for_status()
            data = response.json()
            return (data.get("results") or [])[: max(1, min(20, num_results))]
    except Exception as exc:
        logger.warning("SearXNG search failed for query=%s: %s", query, exc)
        return []


async def _search_repo_and_guides(request_text: str, service_hint: str) -> dict:
    base = service_hint or request_text or ""
    queries = [
        f"{base} github docker compose",
        f"{base} official deployment docker compose",
    ]
    all_results: list[dict] = []
    seen_url: set[str] = set()
    for query in queries:
        results = await _search_searxng(query)
        for item in results:
            url = str(item.get("url", "")).strip()
            if not url or url in seen_url:
                continue
            seen_url.add(url)
            all_results.append(item)

    repo_candidates: list[tuple[int, str, str]] = []
    guides: list[str] = []
    service_lower = service_hint.lower()
    service_compact = _compact_name(service_lower)
    for item in all_results:
        url = str(item.get("url", "")).strip()
        title = str(item.get("title", "")).lower()
        content = str(item.get("content", "")).lower()
        github_repo = _normalize_github_repo_url(url)
        if github_repo:
            score = 5
            if service_lower and service_lower in f"{url} {title} {content}":
                score += 3
            owner, repo_name = _split_github_repo(github_repo)
            repo_compact = _compact_name(repo_name)
            if service_compact and repo_compact == service_compact:
                score += 8
            elif service_compact and repo_compact.startswith(service_compact):
                score += 5
            elif service_compact and service_compact in repo_compact:
                score += 2
            if service_compact and service_compact in _compact_name(owner):
                score += 2
            if "official" in f"{title} {content}" or "官方" in f"{title} {content}":
                score += 1
            if (
                repo_name
                and repo_name != service_lower
                and any(tag in repo_name for tag in ("i18n", "chinese", "mirror", "fork"))
            ):
                score -= 2
            repo_candidates.append((score, github_repo, url))

        # Keep top deployment references.
        if len(guides) < 4:
            lowered_url = url.lower()
            if any(tag in lowered_url for tag in ("docs", "docker", "compose", "github")):
                guides.append(url)

    repo_candidates.sort(key=lambda item: item[0], reverse=True)
    dedup_candidates: list[dict] = []
    seen_repo: set[str] = set()
    for score, repo, source in repo_candidates:
        if repo in seen_repo:
            continue
        seen_repo.add(repo)
        dedup_candidates.append(
            {
                "repo_url": repo,
                "source_url": source,
                "score": score,
            }
        )

    repo_url = dedup_candidates[0]["repo_url"] if dedup_candidates else ""
    repo_source = dedup_candidates[0]["source_url"] if dedup_candidates else ""

    return {
        "queries": queries,
        "repo_url": repo_url,
        "repo_source": repo_source,
        "repo_candidates": dedup_candidates,
        "guides": guides,
    }


async def _search_repo_candidates_via_github(
    request_text: str,
    service_hint: str,
    per_query: int = 6,
) -> list[dict]:
    """
    Generic fallback repository search using GitHub Search API.
    Used only when searxng results are unavailable or weak.
    """
    base = (service_hint or request_text or "").strip()
    if not base:
        return []

    queries = [
        f"{base} docker compose",
        f"{base} docker",
    ]
    seen_repo: set[str] = set()
    candidates: list[dict] = []
    service_compact = _compact_name(service_hint or base)

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "x-bot-deployment-manager",
    }
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
        for query in queries:
            try:
                response = await client.get(
                    "https://api.github.com/search/repositories",
                    params={
                        "q": query,
                        "sort": "stars",
                        "order": "desc",
                        "per_page": max(1, min(10, int(per_query))),
                    },
                )
                if response.status_code >= 400:
                    continue
                payload = response.json()
            except Exception as exc:
                logger.warning("GitHub fallback search failed for query=%s: %s", query, exc)
                continue

            for item in payload.get("items", []) or []:
                html_url = str(item.get("html_url", "")).strip()
                repo_url = _normalize_github_repo_url(html_url)
                if not repo_url or repo_url in seen_repo:
                    continue
                seen_repo.add(repo_url)

                full_name = str(item.get("full_name", "")).lower()
                repo_desc = str(item.get("description", "") or "").lower()
                stars = int(item.get("stargazers_count") or 0)
                archived = bool(item.get("archived"))
                fork = bool(item.get("fork"))

                score = 5 + min(20, stars // 1000)
                repo_compact = _compact_name(full_name)
                desc_compact = _compact_name(repo_desc)
                if service_compact and (
                    service_compact in repo_compact or service_compact in desc_compact
                ):
                    score += 6
                if not fork:
                    score += 2
                if archived:
                    score -= 6

                candidates.append(
                    {
                        "repo_url": repo_url,
                        "source_url": html_url,
                        "score": score,
                    }
                )

    candidates.sort(key=lambda item: int(item.get("score", 0)), reverse=True)
    return candidates


def _rewrite_compose_host_port(
    compose_file: Path,
    container_port: int,
    host_port: int,
) -> tuple[bool, str]:
    """
    Force host port mapping to `host_port:container_port` in compose file.
    Returns (changed, error_message).
    """
    try:
        original = compose_file.read_text(encoding="utf-8")
    except Exception as exc:
        return False, f"读取 compose 文件失败: {exc}"

    updated = original

    # Handles forms like "8080:8080", '8080:8080', 8080:8080
    updated, count = re.subn(
        rf'(?P<prefix>["\']?)(?P<host>\d{{2,5}})\s*:\s*{container_port}(?P<suffix>["\']?)',
        rf"\g<prefix>{host_port}:{container_port}\g<suffix>",
        updated,
        count=1,
    )

    if count == 0:
        return False, f"未找到可替换的 `*:{container_port}` 端口映射，保持原配置。"

    if updated == original:
        return False, ""

    try:
        compose_file.write_text(updated, encoding="utf-8")
        return True, ""
    except Exception as exc:
        return False, f"写入 compose 文件失败: {exc}"


async def _run_shell(command: str, cwd: Path, timeout: int = 120) -> tuple[int, str]:
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except Exception:
            pass
        return -1, f"命令超时 ({timeout}s): {command}"

    out_text = (stdout or b"").decode("utf-8", errors="replace")
    err_text = (stderr or b"").decode("utf-8", errors="replace")
    combined = out_text
    if err_text:
        combined = f"{combined}\n[stderr]\n{err_text}" if combined else f"[stderr]\n{err_text}"
    return process.returncode, combined.strip()


def _patch_compose_known_issues(
    compose_file: Path,
    deploy_output: str,
) -> list[str]:
    """
    Apply deterministic fixes for common compose issues.
    Returns a list of applied fix notes.
    """
    notes: list[str] = []
    try:
        original = compose_file.read_text(encoding="utf-8")
    except Exception:
        return notes

    updated = original
    lowered_output = (deploy_output or "").lower()

    # Normalize unresolved version placeholders in generic compose templates.
    if "invalid reference format" in lowered_output:
        updated = re.sub(r"\$\{version\}", "latest", updated, flags=re.IGNORECASE)
        updated = re.sub(r"\{version\}", "latest", updated, flags=re.IGNORECASE)
        if updated != original:
            notes.append("检测到镜像标签占位符异常，已替换为 `latest`。")

    if updated == original:
        return notes

    try:
        compose_file.write_text(updated, encoding="utf-8")
    except Exception:
        return []

    if not notes:
        notes.append("已自动修复 compose 中的已知问题后重试。")
    return notes


async def _auto_deploy(params: dict) -> dict:
    """
    Deterministic one-shot deployment path.
    Search-first behavior:
    1) Resolve repo URL from user input / search.
    2) Clone repo into deployment workspace.
    3) Resolve compose method (repo compose file or service template fallback).
    4) Bring up container and report URL.
    """
    request_text = str(params.get("request", "") or "").strip()
    service_input = str(params.get("service", "") or params.get("name", "")).strip()
    repo_url = str(params.get("repo_url", "") or "").strip()
    repo_from_user = bool(repo_url)
    search_summary_lines: list[str] = []
    force_redeploy = bool(params.get("force_redeploy")) or _has_redeploy_confirmation(
        request_text
    )

    # 0) Infer service name.
    service_key = _extract_service_from_request(request_text, service_input)
    if service_key:
        search_summary_lines.append(f"- 服务识别: `{service_key}`")

    # 1) Try explicit URL in text first.
    if not repo_url:
        repo_url = _extract_repo_url_from_text(request_text)
        repo_url = _normalize_github_repo_url(repo_url) or repo_url
        if repo_url:
            repo_from_user = True
            search_summary_lines.append(f"- 从用户输入提取仓库: `{repo_url}`")

    repo_candidates: list[dict] = []
    if repo_url:
        repo_candidates.append(
            {
                "repo_url": repo_url,
                "source_url": "user_input",
                "score": 9999 if repo_from_user else 1000,
            }
        )

    # 2) Search repository and deployment references when URL missing.
    search_result = {"queries": [], "repo_url": "", "repo_source": "", "guides": [], "repo_candidates": []}
    if not repo_candidates:
        search_result = await _search_repo_and_guides(request_text, service_key)
        for candidate in search_result.get("repo_candidates", []):
            if not isinstance(candidate, dict):
                continue
            repo_candidates.append(
                {
                    "repo_url": str(candidate.get("repo_url", "")).strip(),
                    "source_url": str(candidate.get("source_url", "")).strip(),
                    "score": int(candidate.get("score", 0)),
                }
            )

        if repo_candidates:
            best = repo_candidates[0]
            search_summary_lines.append(f"- 自动搜索命中仓库: `{best.get('repo_url', '')}`")
            if best.get("source_url"):
                search_summary_lines.append(f"- 命中来源: {best.get('source_url')}")

        for guide in search_result.get("guides", [])[:3]:
            search_summary_lines.append(f"- 部署参考: {guide}")

    # 3) Generic fallback search via GitHub API.
    if not repo_candidates:
        github_candidates = await _search_repo_candidates_via_github(
            request_text=request_text,
            service_hint=service_key,
        )
        if github_candidates:
            repo_candidates.extend(github_candidates)
            best = github_candidates[0]
            search_summary_lines.append("- 已启用 GitHub API 通用兜底搜索。")
            search_summary_lines.append(f"- GitHub 命中仓库: `{best.get('repo_url', '')}`")
            if best.get("source_url"):
                search_summary_lines.append(f"- 命中来源: {best.get('source_url')}")

    # 4) Deduplicate candidate list while keeping order.
    deduped_candidates: list[dict] = []
    seen_repos: set[str] = set()
    for candidate in repo_candidates:
        candidate_repo = _normalize_github_repo_url(candidate.get("repo_url", "")) or str(
            candidate.get("repo_url", "")
        ).strip()
        if not candidate_repo or candidate_repo in seen_repos:
            continue
        seen_repos.add(candidate_repo)
        deduped_candidates.append(
            {
                "repo_url": candidate_repo,
                "source_url": str(candidate.get("source_url", "")).strip(),
                "score": int(candidate.get("score", 0)),
            }
        )
    repo_candidates = deduped_candidates

    if not repo_candidates:
        query_text = "\n".join(search_result.get("queries", []))
        return {
            "text": (
                "❌ 无法自动部署：未识别到可部署仓库。\n\n"
                "已尝试搜索但未找到可靠仓库。\n"
                f"搜索词:\n```\n{query_text or request_text}\n```\n\n"
                "请提供 GitHub 仓库链接，或明确说明目标服务名称。"
            ),
            "ui": {},
            "success": False,
            "terminal": True,
            "task_outcome": "failed",
            "failure_mode": "fatal",
        }

    # Use the top candidate as initial target; if it is not deployable, we will
    # automatically try the next candidates below.
    repo_url = repo_candidates[0]["repo_url"]

    repo_name = _extract_repo_name(repo_url) or "project"
    target_dir = str(params.get("target_dir", "") or params.get("project_name", "")).strip()
    target_path = _resolve_project_path(target_dir, repo_name)
    existing_compose = _find_compose_file(target_path) if target_path.exists() else None

    # If an existing deployment is already running, report first and ask for confirmation.
    if existing_compose:
        has_unresolved_version_placeholder = False
        try:
            compose_text = existing_compose.read_text(encoding="utf-8")
            has_unresolved_version_placeholder = bool(
                re.search(
                    r"\$\{version\}|\{version\}",
                    compose_text,
                    flags=re.IGNORECASE,
                )
            )
        except Exception:
            has_unresolved_version_placeholder = False

        ps_cmd = f"docker compose -f {existing_compose.name} ps"
        ps_code, ps_output = await _run_shell(ps_cmd, target_path, timeout=60)
        already_running = ps_code == 0 and (
            "Up" in ps_output or "running" in ps_output.lower()
        )
        if already_running and not force_redeploy and not has_unresolved_version_placeholder:
            existing_port = _normalize_host_port(
                params.get("host_port", DEFAULT_HOST_PORT), DEFAULT_HOST_PORT
            )
            local_url, public_url = _build_access_urls(existing_port)
            return {
                "text": (
                    "ℹ️ 检测到目标目录已有运行中的部署。\n\n"
                    f"目录: `{target_path}`\n"
                    f"compose: `{existing_compose.name}`\n"
                    f"访问地址(本机): {local_url}\n"
                    f"访问地址(局域网/公网): {public_url}\n\n"
                    "如需重部署，请明确回复：`继续重部署`。"
                ),
                "ui": {},
                "success": True,
                "terminal": True,
                "task_outcome": "partial",
                "needs_confirmation": True,
                "project_name": repo_name,
                "project_path": str(target_path),
                "url": public_url,
            }

    compose_file = None
    compose_notes: list[str] = []
    candidate_attempt_notes: list[str] = []

    # Try top candidates in order; pick the first that is cloneable and contains compose.
    for candidate in repo_candidates[:3]:
        candidate_repo = str(candidate.get("repo_url", "")).strip()
        if not candidate_repo:
            continue
        candidate_repo_name = _extract_repo_name(candidate_repo) or "project"
        candidate_target_path = _resolve_project_path(target_dir, candidate_repo_name)

        clone_result = await _clone_repo(
            {
                "repo_url": candidate_repo,
                "target_dir": str(candidate_target_path),
            }
        )
        if clone_result.get("text", "").startswith("❌"):
            candidate_attempt_notes.append(
                f"- `{candidate_repo}`: 克隆失败 ({clone_result.get('text', '')[:160]})"
            )
            continue

        resolved_project_path = str(clone_result.get("project_path") or "").strip()
        if resolved_project_path:
            candidate_target_path = Path(resolved_project_path).resolve()
        resolved_project_name = str(clone_result.get("project_name") or "").strip()
        if resolved_project_name:
            candidate_repo_name = resolved_project_name

        candidate_compose = _find_compose_file(candidate_target_path)
        if not candidate_compose:
            candidate_attempt_notes.append(
                f"- `{candidate_repo}`: 未找到 compose 文件"
            )
            continue

        repo_url = candidate_repo
        repo_name = candidate_repo_name
        target_path = candidate_target_path
        compose_file = candidate_compose
        if candidate_repo != repo_candidates[0]["repo_url"]:
            search_summary_lines.append(
                f"- 初始候选不可部署，已自动切换到: `{candidate_repo}`"
            )
        break

    if not compose_file:
        attempts = "\n".join(candidate_attempt_notes) or "- 无可用候选仓库"
        return {
            "text": (
                "❌ 未找到可直接部署的仓库（缺少 compose 或克隆失败）。\n\n"
                f"尝试记录:\n{attempts}\n\n"
                "请提供更明确的仓库链接，或补充部署文档。"
            ),
            "ui": {},
            "success": False,
            "terminal": True,
            "task_outcome": "failed",
            "failure_mode": "fatal",
            "project_path": str(target_path),
        }

    if repo_url:
        search_summary_lines.append(f"- 最终部署仓库: `{repo_url}`")

    default_port = DEFAULT_HOST_PORT
    host_port = _normalize_host_port(params.get("host_port", default_port), default_port)
    rewrite_note = ""
    raw_container_port = params.get("container_port")
    container_port: int | None = None
    try:
        parsed = int(raw_container_port) if raw_container_port is not None else 0
        if 1 <= parsed <= 65535:
            container_port = parsed
    except Exception:
        container_port = None

    if container_port is not None:
        changed, rewrite_error = _rewrite_compose_host_port(
            compose_file=compose_file,
            container_port=container_port,
            host_port=host_port,
        )
        if rewrite_error:
            rewrite_note = f"\n⚠️ {rewrite_error}"
        elif changed:
            rewrite_note = f"\n✅ 已将服务端口映射到 `{host_port}:{container_port}`。"

    up_cmd = f"docker compose -f {compose_file.name} up -d"
    up_code, up_output = await _run_shell(up_cmd, target_path, timeout=300)
    if up_code != 0:
        patch_notes = _patch_compose_known_issues(compose_file, up_output)
        if patch_notes:
            compose_notes.extend(patch_notes)
            retry_code, retry_output = await _run_shell(up_cmd, target_path, timeout=300)
            if retry_code == 0:
                up_code, up_output = retry_code, retry_output
            else:
                combined_output = (
                    f"{up_output}\n\n[自动修复后重试输出]\n{retry_output}"
                ).strip()
                notes_text = "\n".join([f"- {item}" for item in patch_notes])
                return {
                    "text": (
                        f"❌ 部署启动失败 (exit={retry_code})\n"
                        f"目录: `{target_path}`\n"
                        f"命令: `{up_cmd}`\n\n"
                        f"自动修复:\n{notes_text}\n\n"
                        f"输出:\n```\n{combined_output[:3000]}\n```"
                    ),
                    "ui": {},
                    "success": False,
                    "terminal": True,
                    "task_outcome": "failed",
                    "failure_mode": _classify_failure_mode(combined_output),
                    "project_path": str(target_path),
                }
        else:
            return {
                "text": (
                    f"❌ 部署启动失败 (exit={up_code})\n"
                    f"目录: `{target_path}`\n"
                    f"命令: `{up_cmd}`\n\n"
                    f"输出:\n```\n{up_output[:3000]}\n```"
                ),
                "ui": {},
                "success": False,
                "terminal": True,
                "task_outcome": "failed",
                "failure_mode": _classify_failure_mode(up_output),
                "project_path": str(target_path),
            }

    if up_code != 0:
        return {
            "text": (
                f"❌ 部署启动失败 (exit={up_code})\n"
                f"目录: `{target_path}`\n"
                f"命令: `{up_cmd}`\n\n"
                f"输出:\n```\n{up_output[:3000]}\n```"
            ),
            "ui": {},
            "success": False,
            "terminal": True,
            "task_outcome": "failed",
            "failure_mode": _classify_failure_mode(up_output),
            "project_path": str(target_path),
        }

    ps_cmd = f"docker compose -f {compose_file.name} ps"
    ps_code, ps_output = await _run_shell(ps_cmd, target_path, timeout=60)
    running = ps_code == 0 and ("Up" in ps_output or "running" in ps_output.lower())

    published_ports = _extract_published_host_ports(ps_output)
    effective_host_port = published_ports[0] if published_ports else host_port
    local_url, public_url = _build_access_urls(effective_host_port)
    status_line = "🟢 容器已启动" if running else "🟡 容器已创建，请检查状态"
    search_summary = "\n".join(search_summary_lines)
    search_block = f"\n自动检索结果:\n{search_summary}\n" if search_summary else ""
    compose_block = ""
    if compose_notes:
        compose_block = "\n自动修复:\n" + "\n".join([f"- {item}" for item in compose_notes]) + "\n"
    access_block = (
        f"- 本机: {local_url}\n- 局域网/公网: {public_url}"
        if public_url != local_url
        else f"- 本机: {local_url}"
    )

    return {
        "text": (
            f"✅ 自动部署流程完成\n\n"
            f"{search_block}"
            f"项目: `{repo_name}`\n"
            f"目录: `{target_path}`\n"
            f"compose: `{compose_file.name}`\n"
            f"状态: {status_line}\n"
            f"访问地址:\n{access_block}"
            f"{compose_block}"
            f"{rewrite_note}\n\n"
            f"`docker compose ps` 输出:\n```\n{ps_output[:2500]}\n```"
        ),
        "ui": {},
        "success": running,
        "terminal": True,
        "task_outcome": "done" if running else "partial",
        "project_name": repo_name,
        "project_path": str(target_path),
        "url": public_url,
    }


async def _clone_repo(params: dict) -> dict:
    """克隆 GitHub 仓库"""
    repo_url = params.get("repo_url", "")
    if not repo_url:
        return {"text": "❌ 缺少参数: repo_url", "ui": {}}

    # 解析项目名
    repo_name = _extract_repo_name(repo_url)
    target_dir = params.get("target_dir")
    target_path = _resolve_project_path(target_dir, repo_name)

    try:
        if target_path.exists():
            # 非破坏性更新已有仓库：仅快进更新，不覆盖本地改动。
            logger.info(f"Updating existing repository (non-destructive): {target_path}")
            if not (target_path / ".git").exists():
                return {
                    "text": (
                        f"⚠️ 目录已存在但不是 Git 仓库：`{target_path}`。\n"
                        "请确认目录内容，或更换 target_dir 后重试。"
                    ),
                    "ui": {},
                    "success": False,
                    "project_path": str(target_path),
                }

            requested_repo = _normalize_github_repo_url(str(repo_url)) or str(repo_url).strip()
            existing_remote = await _read_git_remote_url(target_path)
            existing_repo = _normalize_github_repo_url(existing_remote) or existing_remote
            if requested_repo and existing_repo and requested_repo != existing_repo:
                new_target = _build_conflict_clone_path(target_path, requested_repo)
                logger.info(
                    "Repository mismatch at %s: existing=%s requested=%s -> clone to %s",
                    target_path,
                    existing_repo,
                    requested_repo,
                    new_target,
                )
                clone_result = await _do_clone(requested_repo, new_target)
                if clone_result.get("success"):
                    clone_result["text"] = (
                        "ℹ️ 检测到目标目录已存在其他仓库，已自动切换到新目录克隆。\n\n"
                        f"原目录: `{target_path}`\n"
                        f"原仓库: `{existing_repo}`\n"
                        f"目标仓库: `{requested_repo}`\n\n"
                        f"{clone_result.get('text', '')}"
                    )
                return clone_result

            process = await asyncio.create_subprocess_exec(
                "git",
                "pull",
                "--ff-only",
                cwd=str(target_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            output = (stdout or b"").decode("utf-8", errors="replace")
            error = (stderr or b"").decode("utf-8", errors="replace")

            if process.returncode != 0:
                return {
                    "text": (
                        f"⚠️ 仓库已存在，但无法进行非破坏更新。\n"
                        f"路径: `{target_path}`\n\n"
                        f"输出:\n```\n{(output + error)[:2000]}\n```\n"
                        "请先处理本地分支/改动后再重试，或指定新目录部署。"
                    ),
                    "ui": {},
                    "success": False,
                    "project_path": str(target_path),
                }

            return {
                "text": f"✅ 仓库已更新（非破坏）: {repo_name}\n\n路径: `{target_path}`",
                "ui": {},
                "success": True,
                "project_path": str(target_path),
                "project_name": repo_name,
            }
        else:
            return await _do_clone(repo_url, target_path)

    except Exception as e:
        logger.error(f"Clone error: {e}")
        return {"text": f"❌ 克隆失败: {e}", "ui": {}}


async def _read_git_remote_url(target_path: Path) -> str:
    process = await asyncio.create_subprocess_exec(
        "git",
        "config",
        "--get",
        "remote.origin.url",
        cwd=str(target_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _stderr = await process.communicate()
    if process.returncode != 0:
        return ""
    return (stdout or b"").decode("utf-8", errors="replace").strip()


async def _do_clone(repo_url: str, target_path: Path) -> dict:
    """执行 git clone"""
    repo_name = target_path.name

    process = await asyncio.create_subprocess_exec(
        "git",
        "clone",
        "--depth",
        "1",
        repo_url,
        str(target_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode == 0:
        return {
            "text": f"✅ 仓库克隆成功: {repo_name}\n\n路径: `{target_path}`",
            "ui": {},
            "success": True,
            "project_path": str(target_path),
            "project_name": repo_name,
        }
    else:
        error_msg = stderr.decode("utf-8", errors="replace")
        return {
            "text": f"❌ 克隆失败:\n```\n{error_msg}\n```",
            "ui": {},
            "success": False,
        }


async def _get_status() -> dict:
    """获取已部署项目状态"""
    projects = []

    try:
        # 列出工作目录下的所有项目
        for item in WORK_BASE.iterdir():
            if item.is_dir():
                # 检查是否有 docker-compose.yml
                compose_file = item / "docker-compose.yml"
                if not compose_file.exists():
                    compose_file = item / "docker-compose.yaml"

                has_compose = compose_file.exists()
                projects.append(
                    {
                        "name": item.name,
                        "path": str(item),
                        "has_compose": has_compose,
                    }
                )

        if not projects:
            return {
                "text": "📭 暂无部署项目。\n\n工作目录: `" + str(WORK_BASE) + "`",
                "ui": {},
            }

        # 获取运行中的容器及其端口
        container_ports = {}  # {container_name: [ports]}
        try:
            process = await asyncio.create_subprocess_shell(
                "docker ps --format '{{.Names}}|{{.Ports}}'",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
            for line in stdout.decode().strip().split("\n"):
                if "|" in line:
                    name, ports_str = line.split("|", 1)
                    # 解析端口，如 "0.0.0.0:21000->8080/tcp"
                    ports = []
                    import re

                    for match in re.findall(r"0\.0\.0\.0:(\d+)->", ports_str):
                        ports.append(int(match))
                    container_ports[name] = ports
        except Exception:
            pass

        # 构建输出
        lines = ["📋 **已部署项目**:\n"]
        for proj in projects:
            name = proj["name"]

            # 查找匹配的容器
            matching_ports = []
            for container_name, ports in container_ports.items():
                if name in container_name:
                    matching_ports.extend(ports)

            if matching_ports:
                status = "🟢 运行中"
                urls = []
                for port in sorted(set(matching_ports)):
                    local_url, public_url = _build_access_urls(port)
                    for item in (local_url, public_url):
                        if item not in urls:
                            urls.append(item)
                access_info = " | ".join(urls)
                lines.append(f"• **{name}**: {status}")
                lines.append(f"  📍 访问: {access_info}")
            else:
                status = "⚪ 未运行"
                compose_status = (
                    "✓ docker-compose" if proj["has_compose"] else "✗ 无配置"
                )
                lines.append(f"• **{name}**: {status} ({compose_status})")

        lines.append(f"\n工作目录: `{WORK_BASE}`")

        return {"text": "\n".join(lines), "ui": {}}

    except Exception as e:
        logger.error(f"Get status error: {e}")
        return {"text": f"❌ 获取状态失败: {e}", "ui": {}}


async def _get_access_info(params: dict) -> dict:
    """获取特定项目的访问信息"""
    import re

    name = params.get("name", "")
    if not name:
        return {"text": "❌ 缺少参数: name", "ui": {}}

    try:
        # 查询 docker ps 获取端口信息
        process = await asyncio.create_subprocess_shell(
            "docker ps --format '{{.Names}}|{{.Ports}}'",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()

        ports = []
        for line in stdout.decode().strip().split("\n"):
            if "|" in line:
                container_name, ports_str = line.split("|", 1)
                if name in container_name:
                    # 解析端口
                    for match in re.findall(r"0\.0\.0\.0:(\d+)->", ports_str):
                        ports.append(int(match))

        if not ports:
            return {
                "text": f"⚠️ 未找到运行中的容器: {name}\n\n请先确保服务已启动。",
                "ui": {},
            }

        urls = []
        for port in sorted(set(ports)):
            local_url, public_url = _build_access_urls(port)
            for item in (local_url, public_url):
                if item not in urls:
                    urls.append(item)

        result = f"✅ **{name}** 访问信息:\n\n"
        for url in urls:
            result += f"📍 {url}\n"

        return {"text": result, "ui": {}, "urls": urls}

    except Exception as e:
        logger.error(f"Get access info error: {e}")
        return {"text": f"❌ 获取访问信息失败: {e}", "ui": {}}


async def _verify_access(params: dict) -> dict:
    """
    验证部署的服务是否可访问。

    使用 httpx 检查 URL 是否可达。
    如果不可达，返回诊断信息供 AI 继续处理。
    """
    import httpx

    name = params.get("name", "")
    url = params.get("url", "")
    timeout = params.get("timeout", 10)  # 默认 10 秒超时

    # 如果没有提供 URL，尝试从容器获取
    if not url and name:
        access_result = await _get_access_info({"name": name})
        urls = access_result.get("urls", [])
        if urls:
            url = urls[0]  # 使用第一个端口

    if not url:
        return {
            "text": "❌ 缺少参数: 需要 `url` 或 `name` 来确定检查目标。",
            "ui": {},
            "success": False,
        }

    # 确保 URL 有协议前缀
    if not url.startswith("http"):
        url = f"http://{url}"

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)

            if response.status_code < 400:
                return {
                    "text": f"✅ **服务验证成功!**\n\n"
                    f"📍 访问地址: {url}\n"
                    f"📊 状态码: {response.status_code}\n"
                    f"📄 响应长度: {len(response.content)} bytes",
                    "ui": {},
                    "success": True,
                    "url": url,
                    "status_code": response.status_code,
                }
            else:
                return {
                    "text": f"⚠️ **服务响应异常**\n\n"
                    f"📍 URL: {url}\n"
                    f"📊 状态码: {response.status_code}\n\n"
                    f"服务可能需要更多时间初始化，或配置有误。",
                    "ui": {},
                    "success": False,
                    "url": url,
                    "status_code": response.status_code,
                }

    except httpx.ConnectError:
        # 连接失败 - 可能服务未启动
        diag = await _get_container_diagnostics(name) if name else ""
        return {
            "text": f"❌ **连接失败**: 无法连接到 {url}\n\n"
            f"**可能原因**:\n"
            f"• 服务尚未完全启动（需要等待）\n"
            f"• 端口映射配置错误\n"
            f"• 容器内服务崩溃\n\n"
            f"{diag}",
            "ui": {},
            "success": False,
            "error": "connect_error",
            "url": url,
        }

    except httpx.TimeoutException:
        return {
            "text": f"⏰ **连接超时**: {url} 在 {timeout} 秒内无响应\n\n"
            f"**建议**:\n"
            f"• 等待几秒后重试\n"
            f"• 检查容器日志",
            "ui": {},
            "success": False,
            "error": "timeout",
            "url": url,
        }

    except Exception as e:
        logger.error(f"Verify access error: {e}")
        return {
            "text": f"❌ **验证失败**: {e}",
            "ui": {},
            "success": False,
            "error": str(e),
        }


async def _get_container_diagnostics(name: str) -> str:
    """获取容器诊断信息"""
    try:
        # 检查容器是否在运行
        process = await asyncio.create_subprocess_shell(
            f"docker ps -a --filter 'name={name}' --format '{{{{.Names}}}}|{{{{.Status}}}}|{{{{.Ports}}}}'",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        output = stdout.decode().strip()

        if not output:
            return f"**诊断**: 未找到名称包含 `{name}` 的容器。\n请检查是否已执行 `docker compose up`。"

        lines = []
        for line in output.split("\n"):
            if "|" in line:
                parts = line.split("|")
                container_name = parts[0]
                status = parts[1] if len(parts) > 1 else "Unknown"
                ports = parts[2] if len(parts) > 2 else "None"
                lines.append(f"• `{container_name}`: {status}")
                if ports:
                    lines.append(f"  端口: {ports}")

        # 获取最近日志
        log_process = await asyncio.create_subprocess_shell(
            f"docker logs --tail 5 $(docker ps -q --filter 'name={name}' | head -1) 2>&1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        log_stdout, _ = await log_process.communicate()
        recent_logs = log_stdout.decode().strip()

        result = "**容器状态**:\n" + "\n".join(lines)
        if recent_logs:
            result += f"\n\n**最近日志**:\n```\n{recent_logs[:500]}\n```"

        return result

    except Exception as e:
        return f"**诊断失败**: {e}"


async def _delete_project(params: dict) -> dict:
    """删除部署项目"""
    name = params.get("name", "")
    if not name:
        return {"text": "❌ 缺少参数: name", "ui": {}}

    project_path = WORK_BASE / name

    if not project_path.exists():
        return {"text": f"❌ 项目不存在: {name}", "ui": {}}

    try:
        shutil.rmtree(project_path)
        return {"text": f"✅ 项目已删除: {name}", "ui": {}}
    except Exception as e:
        logger.error(f"Delete project error: {e}")
        return {"text": f"❌ 删除失败: {e}", "ui": {}}


# =============================================================================
# Handler Registration (for /deploy command)
# =============================================================================
def register_handlers(adapter_manager):
    """注册 /deploy 命令"""

    async def deploy_command(ctx: UnifiedContext):
        """
        Handle /deploy <描述或URL>
        这是入口命令，实际部署逻辑由 Skill Agent 通过 SKILL.md SOP 编排
        """
        if not await is_user_allowed(ctx.message.user.id):
            return

        args = ctx.platform_ctx.args if ctx.platform_ctx else []
        if not args:
            await ctx.reply(
                "⚠️ 请提供部署目标。\n\n"
                "用法:\n"
                "• `/deploy https://github.com/user/repo` - 部署 GitHub 项目\n"
                "• `/deploy Uptime Kuma` - 智能搜索并部署"
            )
            return

        # 将请求转发给 Agent 处理
        from core.agent_orchestrator import agent_orchestrator

        user_input = " ".join(args)
        full_request = f"部署: {user_input}"

        await ctx.reply(f"🚀 收到部署请求: {user_input}\n\n正在分析...")

        # 调用 Agent 处理
        message_history = [{"role": "user", "parts": [{"text": full_request}]}]
        async for response in agent_orchestrator.handle_message(
            ctx=ctx,
            message_history=message_history,
        ):
            if response and response.strip():
                await ctx.reply(response)

    adapter_manager.on_command("deploy", deploy_command, description="智能部署服务")
