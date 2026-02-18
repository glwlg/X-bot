import json
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Tuple

from core.config import DATA_DIR


MEMORY_TOOL_NAMES: set[str] = set()

TOKEN_ALIASES = {
    "fs": "group:fs",
    "runtime": "group:execution",
    "exec": "group:execution",
    "execution": "group:execution",
    "coding": "group:coding",
    "browser": "group:research",
    "research": "group:research",
    "feed": "group:feeds",
    "feeds": "group:feeds",
    "ops": "group:ops",
    "automation": "group:automation",
    "finance": "group:finance",
    "account": "group:account",
    "memory": "group:memory",
    "skill-admin": "group:skill-admin",
    "skill_admin": "group:skill-admin",
    "skills": "group:skills",
    "mcp": "group:memory",
    "manager": "group:management",
    "dispatch": "group:management",
    "all": "group:all",
}

SKILL_FUNCTION_GROUPS = {
    "deep_research": {"group:research"},
    "searxng_search": {"group:research"},
    "web_browser": {"group:research"},
    "notebooklm": {"group:research", "group:knowledge"},
    "docker_ops": {"group:ops"},
    "deployment_manager": {"group:ops"},
    "scheduler_manager": {"group:automation"},
    "reminder": {"group:automation"},
    "rss_subscribe": {"group:research", "group:feeds"},
    "stock_watch": {"group:finance"},
    "account_manager": {"group:account", "group:security"},
    "skill_manager": {"group:skill-admin"},
    "download_video": {"group:media"},
    "generate_image": {"group:media"},
    "news_article_writer": {"group:content", "group:research"},
    "xlsx": {"group:data"},
}


class ToolAccessStore:
    """Agent tool grouping and allow/deny policy store."""

    CORE_MANAGER_DEFAULT_ALLOW = [
        "group:primitives",
        "group:management",
    ]
    WORKER_DEFAULT_DENY = [
        "group:coding",
        "group:management",
    ]

    def __init__(self):
        self.path = (Path(DATA_DIR) / "kernel" / "tool_access.json").resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._payload = self._read()
        self._write_unlocked()

    def _default_payload(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "core_manager": {
                "tools": {
                    "allow": list(self.CORE_MANAGER_DEFAULT_ALLOW),
                    "deny": [],
                }
            },
            "worker_default": {
                "tools": {
                    "allow": ["group:all"],
                    "deny": list(self.WORKER_DEFAULT_DENY),
                }
            },
            "workers": {},
        }

    def _normalize_entries(self, items: List[str] | None) -> List[str]:
        normalized: List[str] = []
        for raw in items or []:
            token = str(raw or "").strip().lower()
            if not token:
                continue
            token = TOKEN_ALIASES.get(token, token)
            if token == "*":
                token = "group:all"
            if token not in normalized:
                normalized.append(token)
        return normalized

    def _normalize_policy(
        self, policy: Dict[str, Any] | None, fallback: Dict[str, Any]
    ) -> Dict[str, Any]:
        base = dict(fallback or {})
        base.update(policy or {})
        tools = dict((base.get("tools") or {}))
        allow = self._normalize_entries(tools.get("allow"))
        deny = self._normalize_entries(tools.get("deny"))
        return {"tools": {"allow": allow, "deny": deny}}

    def _read(self) -> Dict[str, Any]:
        default = self._default_payload()
        if not self.path.exists():
            return default
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                merged = dict(default)
                merged.update(loaded)
                workers = merged.get("workers")
                if not isinstance(workers, dict):
                    workers = {}
                merged["workers"] = workers
                core_policy = self._normalize_policy(
                    merged.get("core_manager"),
                    default["core_manager"],
                )
                core_allow = list((core_policy.get("tools") or {}).get("allow") or [])
                core_deny = list((core_policy.get("tools") or {}).get("deny") or [])
                if core_allow == ["group:management"] and not core_deny:
                    core_policy["tools"]["allow"] = list(
                        self.CORE_MANAGER_DEFAULT_ALLOW
                    )
                    core_allow = list(
                        (core_policy.get("tools") or {}).get("allow") or []
                    )
                if core_allow == ["group:all"] and not core_deny:
                    core_policy["tools"]["allow"] = list(
                        self.CORE_MANAGER_DEFAULT_ALLOW
                    )
                    core_allow = list(
                        (core_policy.get("tools") or {}).get("allow") or []
                    )
                legacy_core_allow_sets = [
                    {
                        "group:management",
                    },
                    {
                        "group:fs",
                        "group:execution",
                        "group:coding",
                        "group:management",
                    },
                    {
                        "group:fs",
                        "group:execution",
                        "group:coding",
                        "group:management",
                        "group:ops",
                    },
                    {
                        "group:fs",
                        "group:execution",
                        "group:coding",
                        "group:management",
                        "group:ops",
                        "group:automation",
                        "group:skills",
                    },
                ]
                if not core_deny and any(
                    set(core_allow) == item and len(core_allow) == len(item)
                    for item in legacy_core_allow_sets
                ):
                    core_policy["tools"]["allow"] = list(
                        self.CORE_MANAGER_DEFAULT_ALLOW
                    )
                merged["core_manager"] = core_policy

                worker_default = self._normalize_policy(
                    merged.get("worker_default"),
                    default["worker_default"],
                )
                worker_allow = list(
                    (worker_default.get("tools") or {}).get("allow") or []
                )
                worker_deny = list(
                    (worker_default.get("tools") or {}).get("deny") or []
                )
                if worker_allow == ["group:all"] and worker_deny == ["group:coding"]:
                    worker_default["tools"]["deny"] = list(self.WORKER_DEFAULT_DENY)
                merged["worker_default"] = worker_default

                worker_entries = merged.get("workers")
                if isinstance(worker_entries, dict):
                    normalized_workers: Dict[str, Any] = {}
                    for wid, policy in worker_entries.items():
                        normalized = self._normalize_policy(policy, worker_default)
                        allow_entries = list(
                            (normalized.get("tools") or {}).get("allow") or []
                        )
                        deny_entries = list(
                            (normalized.get("tools") or {}).get("deny") or []
                        )
                        if allow_entries == ["group:all"] and deny_entries == [
                            "group:coding"
                        ]:
                            normalized["tools"]["deny"] = list(self.WORKER_DEFAULT_DENY)
                        normalized_workers[str(wid)] = normalized
                    merged["workers"] = normalized_workers
                return merged
        except Exception:
            pass
        return default

    def _write_unlocked(self) -> None:
        self.path.write_text(
            json.dumps(self._payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def get_group_catalog(self) -> Dict[str, str]:
        return {
            "group:all": "所有工具/技能/MCP",
            "group:fs": "文件系统工具：read/write/edit",
            "group:execution": "执行类能力：bash/exec/process 与 Worker 执行后端",
            "group:coding": "编码类能力：codex/gemini-cli",
            "group:research": "研究检索类：web/search/deep_research/notebooklm",
            "group:feeds": "信息订阅类：rss/news feed",
            "group:ops": "部署运维类：deployment/docker",
            "group:automation": "自动化类：scheduler/reminder（不含 rss/stock）",
            "group:finance": "金融行情类：stock_watch",
            "group:media": "多媒体类：download_video/generate_image",
            "group:account": "账号凭据类：account_manager",
            "group:memory": "记忆类：用户 MEMORY.md",
            "group:skill-admin": "技能治理类：skill_manager",
            "group:skills": "扩展技能总开关：ext_*",
            "group:management": "管理调度类：list_workers/dispatch_worker/worker_status",
        }

    def groups_for_tool(self, tool_name: str, *, kind: str = "tool") -> List[str]:
        name = str(tool_name or "").strip().lower()
        groups = {"group:all"}
        if name:
            groups.add(f"tool:{name}")
            groups.add(name)

        if kind == "backend":
            groups.add("group:execution")
            groups.add("group:backend")
            if name in {"codex", "gemini-cli", "gemini"}:
                groups.add("group:coding")
            if name in {"shell", "bash", "sh", "core-agent"}:
                groups.add("group:execution")
            return sorted(groups)

        if name in {"read", "write", "edit"}:
            groups.add("group:fs")
            groups.add("group:primitives")
        if name in SKILL_FUNCTION_GROUPS:
            groups.add("group:skills")
            groups.add(f"group:skill:{name}")
            for item in SKILL_FUNCTION_GROUPS.get(name, set()):
                groups.add(item)
        if name in {"bash", "exec", "process"}:
            groups.add("group:execution")
            groups.add("group:primitives")
        if name in {"list_workers", "dispatch_worker", "worker_status"}:
            groups.add("group:management")
            groups.add("group:execution")
        if name in {"run_extension", "list_extensions"}:
            groups.add("group:skills")
        if name.startswith("ext_"):
            groups.add("group:skills")
            skill_name = name.removeprefix("ext_")
            groups.add(f"group:skill:{skill_name}")
            for item in SKILL_FUNCTION_GROUPS.get(skill_name, set()):
                groups.add(item)
            if any(
                key in skill_name for key in ("browser", "search", "web", "research")
            ):
                groups.add("group:research")
            if "rss" in skill_name or "feed" in skill_name:
                groups.add("group:feeds")
            if any(key in skill_name for key in ("deploy", "docker", "ops")):
                groups.add("group:ops")
            if any(
                key in skill_name for key in ("reminder", "schedule", "cron", "task")
            ):
                groups.add("group:automation")
            if any(key in skill_name for key in ("stock", "finance", "quote")):
                groups.add("group:finance")
            if any(key in skill_name for key in ("video", "image", "media")):
                groups.add("group:media")
            if any(key in skill_name for key in ("account", "credential", "auth")):
                groups.add("group:account")
            if "skill_manager" in skill_name:
                groups.add("group:skill-admin")
        if name in MEMORY_TOOL_NAMES:
            groups.add("group:memory")
            groups.add("group:mcp")
        if kind == "mcp":
            groups.add("group:memory")
            groups.add("group:mcp")
        if "browser" in name:
            groups.add("group:research")
        return sorted(groups)

    @staticmethod
    def _matches_entry(entry: str, *, tool_name: str, groups: List[str]) -> bool:
        token = str(entry or "").strip().lower()
        if not token:
            return False
        if token == "group:all":
            return True
        if token.startswith("group:"):
            return token in groups
        if token.startswith("tool:"):
            return token == f"tool:{tool_name}"
        return token == tool_name

    def _policy_allows(
        self, policy: Dict[str, Any], *, tool_name: str, groups: List[str]
    ) -> Tuple[bool, str]:
        tools_cfg = dict((policy or {}).get("tools") or {})
        allow = self._normalize_entries(tools_cfg.get("allow"))
        deny = self._normalize_entries(tools_cfg.get("deny"))
        if allow:
            matched_allow = any(
                self._matches_entry(item, tool_name=tool_name, groups=groups)
                for item in allow
            )
            if not matched_allow:
                return False, "not_in_allow_list"
        blocked = any(
            self._matches_entry(item, tool_name=tool_name, groups=groups)
            for item in deny
        )
        if blocked:
            return False, "matched_deny_list"
        return True, "allowed"

    def get_core_policy(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._payload.get("core_manager") or {})

    def get_worker_default_policy(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._payload.get("worker_default") or {})

    def ensure_worker_policy(
        self, worker_id: str, policy: Dict[str, Any] | None = None
    ) -> Dict[str, Any]:
        safe_id = str(worker_id or "").strip() or "worker-main"
        with self._lock:
            workers = self._payload.setdefault("workers", {})
            fallback = dict(self._payload.get("worker_default") or {})
            current = workers.get(safe_id)
            if current is None:
                workers[safe_id] = self._normalize_policy(policy, fallback)
                self._write_unlocked()
            return dict(workers.get(safe_id) or fallback)

    def get_worker_policy(self, worker_id: str) -> Dict[str, Any]:
        safe_id = str(worker_id or "").strip() or "worker-main"
        with self._lock:
            workers = self._payload.setdefault("workers", {})
            policy = workers.get(safe_id)
            if isinstance(policy, dict):
                return dict(policy)
            return dict(self._payload.get("worker_default") or {})

    def set_worker_policy(
        self,
        worker_id: str,
        *,
        allow: List[str] | None = None,
        deny: List[str] | None = None,
        actor: str = "core-manager",
    ) -> Tuple[bool, str]:
        safe_id = str(worker_id or "").strip()
        if not safe_id:
            return False, "invalid_worker_id"
        if safe_id in {"core-manager", "core_manager"}:
            return False, "core_manager_policy_is_readonly"
        with self._lock:
            workers = self._payload.setdefault("workers", {})
            fallback = dict(self._payload.get("worker_default") or {})
            current = self._normalize_policy(workers.get(safe_id), fallback)
            if allow is not None:
                current["tools"]["allow"] = self._normalize_entries(allow)
            if deny is not None:
                current["tools"]["deny"] = self._normalize_entries(deny)
            workers[safe_id] = current
            self._write_unlocked()
        return True, "updated"

    def reset_worker_policy(self, worker_id: str) -> Tuple[bool, str]:
        safe_id = str(worker_id or "").strip()
        if not safe_id:
            return False, "invalid_worker_id"
        with self._lock:
            workers = self._payload.setdefault("workers", {})
            if safe_id in workers:
                del workers[safe_id]
                self._write_unlocked()
                return True, "reset"
            return True, "already_default"

    def resolve_runtime_policy(
        self,
        *,
        runtime_user_id: str,
        platform: str = "",
    ) -> Dict[str, Any]:
        uid = str(runtime_user_id or "").strip()
        platform_name = str(platform or "").strip().lower()
        if uid.startswith("worker::"):
            parts = uid.split("::")
            worker_id = parts[1].strip() if len(parts) >= 2 else "worker-main"
            return {
                "agent_kind": "worker",
                "agent_id": worker_id or "worker-main",
                "policy": self.get_worker_policy(worker_id or "worker-main"),
            }
        if platform_name == "heartbeat_daemon":
            return {
                "agent_kind": "core-manager",
                "agent_id": "core-manager",
                "policy": self.get_core_policy(),
            }
        # Regular user-facing orchestration runs in core-manager role.
        return {
            "agent_kind": "core-manager",
            "agent_id": "core-manager",
            "policy": self.get_core_policy(),
        }

    def is_tool_allowed(
        self,
        *,
        runtime_user_id: str,
        tool_name: str,
        kind: str = "tool",
        platform: str = "",
    ) -> Tuple[bool, Dict[str, Any]]:
        resolved = self.resolve_runtime_policy(
            runtime_user_id=runtime_user_id,
            platform=platform,
        )
        policy = dict(resolved.get("policy") or {})
        normalized_name = str(tool_name or "").strip().lower()
        groups = self.groups_for_tool(normalized_name, kind=kind)
        # Hard boundary: worker execution runtime cannot access memory MCP tools.
        if str(resolved.get("agent_kind") or "").strip().lower() == "worker":
            if (
                normalized_name in MEMORY_TOOL_NAMES
                or "group:memory" in groups
                or kind == "mcp"
            ):
                detail = {
                    "agent_kind": resolved.get("agent_kind"),
                    "agent_id": resolved.get("agent_id"),
                    "tool_name": normalized_name,
                    "groups": groups,
                    "reason": "worker_memory_disabled",
                }
                return False, detail
        allowed, reason = self._policy_allows(
            policy,
            tool_name=normalized_name,
            groups=groups,
        )
        detail = {
            "agent_kind": resolved.get("agent_kind"),
            "agent_id": resolved.get("agent_id"),
            "tool_name": normalized_name,
            "groups": groups,
            "reason": reason,
        }
        return allowed, detail

    def is_backend_allowed(
        self, *, worker_id: str, backend: str
    ) -> Tuple[bool, Dict[str, Any]]:
        policy = self.get_worker_policy(worker_id)
        backend_name = str(backend or "").strip().lower()
        groups = self.groups_for_tool(backend_name, kind="backend")
        allowed, reason = self._policy_allows(
            policy,
            tool_name=backend_name,
            groups=groups,
        )
        return allowed, {
            "agent_kind": "worker",
            "agent_id": str(worker_id),
            "tool_name": backend_name,
            "groups": groups,
            "reason": reason,
        }


tool_access_store = ToolAccessStore()
