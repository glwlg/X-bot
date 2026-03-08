from __future__ import annotations

import asyncio
import inspect
import logging
import os
import shlex
import shutil
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from core.heartbeat_store import heartbeat_store
from manager.dev.delivery_policy import (
    normalize_rollout_mode,
    normalize_target_service,
)
from manager.dev.planner import manager_dev_planner
from manager.dev.publisher import manager_dev_publisher
from manager.dev.runtime import run_coding_backend, run_shell
from manager.dev.skill_contracts import (
    resolve_skill_contract,
    resolve_skill_target_dir,
    run_skill_contract_preflight,
    sanitize_skill_name,
)
from manager.dev.task_store import dev_task_store
from manager.dev.validator import manager_dev_validator
from manager.dev.workspace import dev_workspace_manager
from manager.integrations.github_client import GitHubClientError, github_client


logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _clean_list(value: Any) -> List[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        rows = [str(item).strip() for item in value if str(item).strip()]
        return rows or None
    if isinstance(value, str):
        rows = [item.strip() for item in value.split("\n") if item.strip()]
        return rows or None
    return None


def _to_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return int(default)


def _short(text: str, limit: int = 240) -> str:
    payload = str(text or "").strip()
    if len(payload) <= limit:
        return payload
    return payload[:limit].rstrip() + "..."


def _format_elapsed(seconds: Any) -> str:
    try:
        safe_seconds = max(0, int(float(seconds or 0)))
    except Exception:
        safe_seconds = 0
    minutes, secs = divmod(safe_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    if minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _sanitize_skill_name(value: Any) -> str:
    return sanitize_skill_name(value)


def _normalize_target_service(value: Any) -> str:
    return normalize_target_service(value)


def _normalize_rollout_mode(value: Any) -> str:
    return normalize_rollout_mode(value)


class ManagerDevService:
    def __init__(self) -> None:
        self.github = github_client
        self.tasks = dev_task_store
        self.workspace = dev_workspace_manager
        self.planner = manager_dev_planner
        self.validator = manager_dev_validator
        self.publisher = manager_dev_publisher
        self._background_jobs: Dict[str, asyncio.Task[Any]] = {}

    def _response(
        self,
        *,
        ok: bool,
        summary: str,
        task_id: str = "",
        status: str = "",
        text: str = "",
        data: Dict[str, Any] | None = None,
        error_code: str = "",
        terminal: bool = False,
        task_outcome: str = "",
        failure_mode: str = "",
    ) -> Dict[str, Any]:
        resolved_failure_mode = str(failure_mode or "").strip().lower() or (
            "fatal" if not ok else ""
        )
        auto_terminal = bool(terminal) or (not ok and resolved_failure_mode == "fatal")
        payload: Dict[str, Any] = {
            "ok": bool(ok),
            "summary": str(summary or "").strip(),
            "task_id": str(task_id or "").strip(),
            "status": str(status or "").strip(),
            "text": str(text or summary or "").strip(),
            "data": dict(data or {}),
            "terminal": auto_terminal,
        }
        if not ok:
            payload["error_code"] = str(
                error_code or "software_delivery_failed"
            ).strip()
            payload["message"] = str(text or summary or "operation failed").strip()
            payload["failure_mode"] = resolved_failure_mode or "fatal"
        if auto_terminal:
            payload["task_outcome"] = str(task_outcome or "").strip().lower() or (
                "done" if ok else "failed"
            )
        elif task_outcome:
            payload["task_outcome"] = str(task_outcome).strip().lower()
        return payload

    def _append_event(
        self,
        record: Dict[str, Any],
        *,
        name: str,
        detail: str,
        data: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        events = list(record.get("events") or [])
        event: Dict[str, Any] = {
            "at": _now_iso(),
            "name": str(name or "").strip(),
            "detail": _short(detail, 600),
        }
        if isinstance(data, dict) and data:
            event["data"] = data
        events.append(event)
        record["events"] = events[-80:]
        record["updated_at"] = _now_iso()
        return record

    async def _load_task(self, task_id: str) -> Dict[str, Any] | None:
        safe_task_id = str(task_id or "").strip()
        if not safe_task_id:
            return None
        return await self.tasks.load(safe_task_id)

    async def _load_latest_task(self) -> Dict[str, Any] | None:
        try:
            rows = await self.tasks.list_recent(limit=1)
        except Exception:
            return None
        if not rows:
            return None
        first = rows[0]
        if not isinstance(first, dict):
            return None
        return dict(first)

    @staticmethod
    def _task_log_path(task_id: str) -> str:
        safe_task_id = str(task_id or "").strip()
        if not safe_task_id:
            return ""
        data_dir = str(os.getenv("DATA_DIR", "/app/data") or "/app/data").strip()
        root = (Path(data_dir) / "system" / "dev_tasks" / "logs").resolve()
        root.mkdir(parents=True, exist_ok=True)
        return str((root / f"{safe_task_id}.log").resolve())

    @staticmethod
    def _read_log_tail(path: str, limit_chars: int = 8000) -> str:
        safe_path = str(path or "").strip()
        if not safe_path:
            return ""
        try:
            content = Path(safe_path).read_text(encoding="utf-8")
        except Exception:
            return ""
        if len(content) <= limit_chars:
            return content
        return content[-limit_chars:]

    @staticmethod
    def _append_progress_log(path: str, line: str) -> None:
        safe_path = str(path or "").strip()
        payload = str(line or "").strip()
        if not safe_path or not payload:
            return
        try:
            target = Path(safe_path).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as handle:
                handle.write(payload.rstrip() + "\n")
        except Exception:
            return

    @staticmethod
    def _task_draft_id(task_id: str) -> int:
        return max(
            1,
            int(zlib.crc32(str(task_id or "").encode("utf-8")) & 0x7FFFFFFF),
        )

    @staticmethod
    def _progress_interval_sec() -> float:
        try:
            return max(
                0.05,
                float(
                    str(
                        os.getenv("SOFTWARE_DELIVERY_PROGRESS_INTERVAL_SEC", "15")
                        or "15"
                    ).strip()
                ),
            )
        except Exception:
            return 15.0

    @staticmethod
    def _completion_summary_text(record: Dict[str, Any]) -> str:
        implementation = dict(record.get("implementation") or {})
        result = dict(implementation.get("result") or {})
        candidates = [
            str(result.get("stdout") or ""),
            str(result.get("summary") or ""),
            str(result.get("stderr") or ""),
        ]
        for raw in candidates:
            lines: List[str] = []
            for row in str(raw or "").splitlines():
                line = str(row or "").strip()
                if not line:
                    continue
                lowered = line.lower()
                if lowered.startswith("diff --git"):
                    break
                if lowered.startswith("file update:"):
                    break
                if lowered.startswith("@@"):
                    continue
                if lowered.startswith("--- ") or lowered.startswith("+++ "):
                    continue
                if lowered.startswith("index "):
                    continue
                if lowered == "codex":
                    continue
                lines.append(line)
                if len(lines) >= 4:
                    break
            if lines:
                return _short("\n".join(lines), 500)

        events = list(record.get("events") or [])
        if events:
            return _short(str(dict(events[-1]).get("detail") or "").strip(), 500)
        return ""

    @staticmethod
    def _latest_skill_file_hint(cwd: str) -> Dict[str, Any]:
        safe_cwd = str(cwd or "").strip()
        if not safe_cwd:
            return {}
        normalized = safe_cwd.replace("\\", "/")
        if "/skills/" not in normalized:
            return {}
        root = Path(safe_cwd)
        if not root.exists():
            return {}
        latest_path: Path | None = None
        latest_mtime = 0.0
        file_count = 0
        try:
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                file_count += 1
                try:
                    mtime = path.stat().st_mtime
                except Exception:
                    continue
                if mtime >= latest_mtime:
                    latest_mtime = mtime
                    latest_path = path
        except Exception:
            return {}
        if latest_path is None:
            return {"file_count": file_count}
        try:
            relative = latest_path.relative_to(root).as_posix()
        except Exception:
            relative = latest_path.as_posix()
        return {
            "latest_file": relative,
            "latest_mtime": datetime.fromtimestamp(latest_mtime)
            .astimezone()
            .isoformat(timespec="seconds"),
            "file_count": file_count,
        }

    @staticmethod
    def _build_progress_message(record: Dict[str, Any]) -> str:
        task_id = str(record.get("task_id") or "").strip()
        status = str(record.get("status") or "").strip().lower() or "queued"
        goal = str(record.get("goal") or record.get("requirement") or "").strip()
        progress = dict(record.get("progress") or {})
        lines = ["🛠 software_delivery 正在处理", f"任务编号: `{task_id}`"]
        if goal:
            lines.append(f"目标: {goal[:120]}")
        lines.append(f"状态: `{status}`")

        stage = str(progress.get("stage") or "").strip()
        if stage:
            lines.append(f"阶段: `{stage}`")

        elapsed = progress.get("elapsed_sec")
        if elapsed is not None:
            lines.append(f"已耗时: {_format_elapsed(elapsed)}")

        latest_file = str(progress.get("latest_file") or "").strip()
        if latest_file:
            lines.append(f"最新文件: `{latest_file}`")

        file_count = int(progress.get("file_count") or 0)
        if file_count > 0:
            lines.append(f"已发现文件: `{file_count}`")

        note = str(progress.get("note") or "").strip()
        if note:
            lines.append(f"进展: {note[:200]}")

        return "\n".join(lines).strip()

    @staticmethod
    def _copy_ignore_names(_root: str, names: List[str]) -> List[str]:
        ignored = {
            ".git",
            ".github",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            ".idea",
            ".vscode",
            "node_modules",
            ".venv",
            "venv",
            "dist",
            "build",
        }
        return [
            name
            for name in names
            if name in ignored or name.endswith(".pyc") or name.endswith(".pyo")
        ]

    @staticmethod
    def _detect_source_skill_root(source_repo_path: str, skill_name: str) -> Path:
        root = Path(str(source_repo_path or "").strip()).resolve()
        if (root / "SKILL.md").exists():
            return root

        safe_skill_name = sanitize_skill_name(skill_name)
        direct_candidates = [
            root / "skills" / "learned" / safe_skill_name,
            root / "skills" / "builtin" / safe_skill_name,
            root / safe_skill_name,
        ]
        for candidate in direct_candidates:
            if (candidate / "SKILL.md").exists():
                return candidate

        nested = [
            path
            for path in root.rglob("SKILL.md")
            if ".git/" not in path.as_posix()
        ]
        if len(nested) == 1:
            return nested[0].parent

        for path in nested:
            parent_name = sanitize_skill_name(path.parent.name)
            if parent_name == safe_skill_name:
                return path.parent

        return root

    @staticmethod
    def _overlay_local_import_overrides(
        *,
        staging_path: Path,
        existing_target: Path,
    ) -> List[str]:
        applied: List[str] = []
        if not existing_target.exists() or not existing_target.is_dir():
            return applied
        relative_paths = (
            "SKILL.md",
            "scripts/execute.py",
            "agents/openai.yaml",
        )
        for relative in relative_paths:
            source = existing_target / relative
            if not source.exists() or not source.is_file():
                continue
            target = staging_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            applied.append(relative)
        return applied

    @staticmethod
    def _ensure_imported_skill_markdown(
        *,
        skill_dir: str,
        skill_name: str,
        repo_url: str,
    ) -> None:
        skill_root = Path(str(skill_dir or "").strip()).resolve()
        skill_md = skill_root / "SKILL.md"
        if skill_md.exists():
            return

        readme = skill_root / "README.md"
        summary = ""
        if readme.exists():
            try:
                for row in readme.read_text(encoding="utf-8").splitlines():
                    line = str(row or "").strip()
                    if not line or line.startswith("#"):
                        continue
                    summary = line
                    break
            except Exception:
                summary = ""

        description = summary or f"Imported external skill from {repo_url or 'GitHub repository'}."
        content = "\n".join(
            [
                "---",
                f"name: {sanitize_skill_name(skill_name)}",
                f"description: {description}",
                "---",
                "",
                f"# {sanitize_skill_name(skill_name)}",
                "",
                "这是一个从外部仓库导入的技能。",
                "",
                "## 使用方式",
                "",
                "- 先阅读当前目录下的 `README.md` 与 `scripts/` 说明。",
                "- 优先直接使用仓库自带 CLI / shell 命令，不必强制补 `scripts/execute.py`。",
                "- 如需环境变量，参考 `.env.example`。",
                "",
            ]
        ).strip() + "\n"
        skill_md.write_text(content, encoding="utf-8")

    @staticmethod
    def _read_skill_readme_summary(skill_root: Path, repo_url: str) -> str:
        readme = skill_root / "README.md"
        if readme.exists():
            try:
                for row in readme.read_text(encoding="utf-8").splitlines():
                    line = str(row or "").strip()
                    if not line or line.startswith("#"):
                        continue
                    return line[:240]
            except Exception:
                pass
        return f"Imported external skill from {repo_url or 'GitHub repository'}."

    @staticmethod
    def _preserve_upstream_skill_markdown(skill_root: Path) -> None:
        skill_md = skill_root / "SKILL.md"
        if not skill_md.exists() or not skill_md.is_file():
            return
        try:
            current = skill_md.read_text(encoding="utf-8")
        except Exception:
            return
        if "integration_origin: external_skill_import" in current:
            return
        backup = skill_root / "references" / "upstream_SKILL.md"
        if backup.exists():
            return
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill_md, backup)

    @staticmethod
    def _shell_quote_path(path: str) -> str:
        return shlex.quote(str(path or "").replace("\\", "/"))

    @classmethod
    def _candidate_import_entrypoints(
        cls,
        *,
        skill_root: Path,
        skill_name: str,
    ) -> List[Dict[str, str]]:
        safe_skill = sanitize_skill_name(skill_name)
        candidates: List[Dict[str, str]] = []
        seen: set[str] = set()

        def add_python(relative: str, *, kind: str) -> None:
            rel = str(relative or "").replace("\\", "/").strip().lstrip("./")
            if not rel:
                return
            target = skill_root / rel
            if not target.exists() or not target.is_file():
                return
            invoke = f"python {cls._shell_quote_path(rel)}"
            if invoke in seen:
                return
            seen.add(invoke)
            candidates.append(
                {
                    "invoke": invoke,
                    "probe": f"{invoke} --help",
                    "probe_alt": f"{invoke} -h",
                    "relative_path": rel,
                    "kind": kind,
                }
            )

        def add_shell(relative: str, *, kind: str) -> None:
            rel = str(relative or "").replace("\\", "/").strip().lstrip("./")
            if not rel:
                return
            target = skill_root / rel
            if not target.exists() or not target.is_file():
                return
            invoke = f"bash {cls._shell_quote_path(rel)}"
            if invoke in seen:
                return
            seen.add(invoke)
            candidates.append(
                {
                    "invoke": invoke,
                    "probe": f"{invoke} --help",
                    "probe_alt": invoke,
                    "relative_path": rel,
                    "kind": kind,
                }
            )

        add_python("scripts/execute.py", kind="existing_wrapper")
        for relative in (
            "union_search_cli.py",
            "cli.py",
            f"{safe_skill}.py" if safe_skill else "",
            "main.py",
            "run.py",
            "app.py",
            "skill.py",
            "scripts/cli.py",
            "scripts/main.py",
            "scripts/run.py",
            f"scripts/{safe_skill}.py" if safe_skill else "",
        ):
            if relative:
                add_python(relative, kind="native_direct")

        top_level_py = sorted(
            [
                path.name
                for path in skill_root.glob("*.py")
                if path.is_file()
                and path.name
                not in {
                    "setup.py",
                    "conftest.py",
                    "__init__.py",
                }
            ]
        )
        if len(top_level_py) == 1:
            add_python(top_level_py[0], kind="native_direct")

        shell_candidates = (
            "run.sh",
            "cli.sh",
            "main.sh",
            "scripts/run.sh",
            "scripts/cli.sh",
        )
        for relative in shell_candidates:
            add_shell(relative, kind="native_shell")

        return candidates

    async def _probe_imported_skill_entrypoint(
        self,
        *,
        skill_dir: str,
        skill_name: str,
    ) -> Dict[str, Any]:
        skill_root = Path(str(skill_dir or "").strip()).resolve()
        attempts: List[Dict[str, Any]] = []
        for candidate in self._candidate_import_entrypoints(
            skill_root=skill_root,
            skill_name=skill_name,
        ):
            probe_commands = [
                str(candidate.get("probe") or "").strip(),
                str(candidate.get("probe_alt") or "").strip(),
            ]
            for probe_command in probe_commands:
                if not probe_command:
                    continue
                result = await run_shell(
                    probe_command,
                    cwd=str(skill_root),
                    timeout_sec=120,
                )
                attempts.append(
                    {
                        "invoke": str(candidate.get("invoke") or "").strip(),
                        "probe": probe_command,
                        "ok": bool(result.get("ok")),
                        "summary": str(result.get("summary") or "").strip(),
                        "stdout": str(result.get("stdout") or ""),
                        "stderr": str(result.get("stderr") or ""),
                        "kind": str(candidate.get("kind") or "").strip(),
                    }
                )
                if result.get("ok"):
                    help_text = (
                        str(result.get("stdout") or "").strip()
                        or str(result.get("stderr") or "").strip()
                        or str(result.get("summary") or "").strip()
                    )
                    lowered = help_text.lower()
                    return {
                        "ok": True,
                        "invoke": str(candidate.get("invoke") or "").strip(),
                        "probe": probe_command,
                        "kind": str(candidate.get("kind") or "").strip(),
                        "help_text": help_text,
                        "supports_doctor": "doctor" in lowered,
                        "supports_list": " list" in lowered
                        or "\nlist" in lowered
                        or "{search,platform,image,download,list" in lowered,
                        "attempts": attempts,
                    }
        return {
            "ok": False,
            "attempts": attempts,
            "summary": "no verified skill entrypoint detected",
        }

    @staticmethod
    def _render_external_skill_markdown(
        *,
        skill_name: str,
        description: str,
        repo_url: str,
        invoke_command: str,
        probe_command: str,
        integration_mode: str,
        supports_doctor: bool,
        supports_list: bool,
    ) -> str:
        safe_skill = sanitize_skill_name(skill_name)
        summary = str(description or "").strip() or (
            f"Imported external skill from {repo_url or 'GitHub repository'}."
        )
        lines = [
            "---",
            f"name: {safe_skill}",
            f"description: {summary}",
            "integration_origin: external_skill_import",
            "runtime_target: worker",
            "change_level: learned",
            "allow_manager_modify: true",
            "allow_auto_publish: true",
            "rollout_target: worker",
            "preflight_commands:",
            f"  - {probe_command}",
            "---",
            "",
            f"# {safe_skill}",
            "",
            "这是一个从外部仓库导入到 X-Bot 的技能。",
            "",
            "## 当前集成方式",
            "",
            f"- 调用模式：`{integration_mode}`",
            f"- 已验证入口：`{invoke_command}`",
            f"- 预检命令：`{probe_command}`",
        ]
        if repo_url:
            lines.append(f"- 来源仓库：`{repo_url}`")
        lines.extend(
            [
                "",
                "## 强制规则",
                "",
                f"- 进入技能根目录后，优先使用 `{'`' + invoke_command + '`'}`。",
                f"- 先运行 `{'`' + probe_command + '`'}` 确认可用。",
                "- 如果 README、任务正文或其他上下文里还出现了更底层的脚本示例，以这里声明的入口命令为准。",
                "- 优先保留上游原生命令；只有原生命令不可直接用于 X-Bot 时，才增加最薄的兼容层。",
                "",
                "## 推荐步骤",
                "",
                f"1. 运行 `{'`' + probe_command + '`'}` 查看帮助与参数。",
            ]
        )
        next_step = 2
        if supports_doctor:
            lines.append(
                f"{next_step}. 如果需要环境检查，先运行 `{'`' + invoke_command + ' doctor' + '`'}`。"
            )
            next_step += 1
        if supports_list:
            lines.append(
                f"{next_step}. 如果需要查看可用平台或能力，先运行 `{'`' + invoke_command + ' list' + '`'}`。"
            )
            next_step += 1
        lines.extend(
            [
                f"{next_step}. 按用户目标直接调用入口命令并整理结果。",
                "",
                "## 说明",
                "",
                "- 如需环境变量，优先查看 `.env.example`、`README.md` 和 `references/`。",
                "- 如果当前入口再次失败，Manager 应优先尝试自动修复这个 skill，而不是让 Worker 长时间试错底层脚本。",
                "",
            ]
        )
        return "\n".join(lines)

    def _write_external_skill_markdown(
        self,
        *,
        skill_dir: str,
        skill_name: str,
        repo_url: str,
        invoke_command: str,
        probe_command: str,
        integration_mode: str,
        supports_doctor: bool,
        supports_list: bool,
    ) -> None:
        skill_root = Path(str(skill_dir or "").strip()).resolve()
        self._preserve_upstream_skill_markdown(skill_root)
        skill_md = skill_root / "SKILL.md"
        content = self._render_external_skill_markdown(
            skill_name=skill_name,
            description=self._read_skill_readme_summary(skill_root, repo_url),
            repo_url=repo_url,
            invoke_command=invoke_command,
            probe_command=probe_command,
            integration_mode=integration_mode,
            supports_doctor=supports_doctor,
            supports_list=supports_list,
        )
        skill_md.write_text(content.rstrip() + "\n", encoding="utf-8")

    def _build_external_skill_repair_instruction(
        self,
        *,
        skill_name: str,
        skill_dir: str,
        repo_url: str,
        source_repo_path: str,
        probe_summary: Dict[str, Any],
    ) -> str:
        lines = [
            f"请修复已导入的外部技能 `{sanitize_skill_name(skill_name)}`，让它能被 X-Bot worker 直接调用。",
            "目标不是重写整个技能，而是尽量保留上游实现，优先使用原生 CLI/脚本入口；只有在确实缺少稳定入口时，才补一个最薄的兼容层。",
            f"目标技能目录：{str(skill_dir or '').strip()}",
        ]
        if str(repo_url or "").strip():
            lines.append(f"来源仓库：{str(repo_url or '').strip()}")
        if str(source_repo_path or "").strip():
            lines.append(f"参考源码目录：{str(source_repo_path or '').strip()}")
        lines.extend(
            [
                "",
                "硬性要求：",
                "- Worker 通过 `load_skill` 读取 `SKILL.md` 后，必须能按 SOP 直接执行。",
                "- 如果存在原生可运行 CLI，就直接复用它，不要无意义改造。",
                "- 如果不存在稳定入口，再增加最薄的兼容入口，例如 `scripts/execute.py`。",
                "- `SKILL.md` 必须改成 X-Bot 可执行 SOP，并带上可通过的 `preflight_commands`。",
                "- 集成完成后，至少有一个帮助/预检命令可以成功执行。",
            ]
        )
        attempts = list(probe_summary.get("attempts") or [])
        if attempts:
            lines.append("")
            lines.append("已尝试但失败的入口探测：")
            for item in attempts[-6:]:
                if not isinstance(item, dict):
                    continue
                probe = str(item.get("probe") or "").strip()
                summary = str(item.get("summary") or "").strip()
                if not probe:
                    continue
                lines.append(f"- `{probe}` -> {summary or 'failed'}")
        return "\n".join(lines).strip()

    async def _prepare_external_skill_runtime(
        self,
        *,
        task_id: str,
        skill_name: str,
        skill_dir: str,
        repo_url: str,
        source_repo_path: str,
        backend: str,
        source: str,
        timeout_sec: int,
        log_path: str,
    ) -> Dict[str, Any]:
        probe = await self._probe_imported_skill_entrypoint(
            skill_dir=skill_dir,
            skill_name=skill_name,
        )
        if probe.get("ok"):
            self._write_external_skill_markdown(
                skill_dir=skill_dir,
                skill_name=skill_name,
                repo_url=repo_url,
                invoke_command=str(probe.get("invoke") or "").strip(),
                probe_command=str(probe.get("probe") or "").strip(),
                integration_mode=str(probe.get("kind") or "native_direct").strip()
                or "native_direct",
                supports_doctor=bool(probe.get("supports_doctor")),
                supports_list=bool(probe.get("supports_list")),
            )
            return {
                "ok": True,
                "mode": str(probe.get("kind") or "native_direct").strip()
                or "native_direct",
                "invoke_command": str(probe.get("invoke") or "").strip(),
                "probe_command": str(probe.get("probe") or "").strip(),
                "attempts": list(probe.get("attempts") or []),
                "repaired": False,
            }

        repair_instruction = self._build_external_skill_repair_instruction(
            skill_name=skill_name,
            skill_dir=skill_dir,
            repo_url=repo_url,
            source_repo_path=source_repo_path,
            probe_summary=probe,
        )
        repair_result = await self._run_coding_backend_with_progress(
            task_id=task_id,
            stage="skill_import_repair",
            instruction=repair_instruction,
            backend=str(backend or "").strip(),
            cwd=str(skill_dir or "").strip(),
            timeout_sec=max(120, int(timeout_sec or 1800)),
            source=str(source or "external_skill_auto_repair").strip()
            or "external_skill_auto_repair",
            log_path=log_path,
        )
        if not repair_result.get("ok"):
            return {
                "ok": False,
                "summary": str(
                    repair_result.get("summary")
                    or repair_result.get("message")
                    or "external skill auto repair failed"
                ).strip(),
                "repair": repair_result,
                "attempts": list(probe.get("attempts") or []),
            }

        repaired_probe = await self._probe_imported_skill_entrypoint(
            skill_dir=skill_dir,
            skill_name=skill_name,
        )
        if not repaired_probe.get("ok"):
            return {
                "ok": False,
                "summary": "external skill imported, but no callable entrypoint was verified after auto repair",
                "repair": repair_result,
                "attempts": list(repaired_probe.get("attempts") or probe.get("attempts") or []),
            }

        self._write_external_skill_markdown(
            skill_dir=skill_dir,
            skill_name=skill_name,
            repo_url=repo_url,
            invoke_command=str(repaired_probe.get("invoke") or "").strip(),
            probe_command=str(repaired_probe.get("probe") or "").strip(),
            integration_mode=str(repaired_probe.get("kind") or "auto_repaired").strip()
            or "auto_repaired",
            supports_doctor=bool(repaired_probe.get("supports_doctor")),
            supports_list=bool(repaired_probe.get("supports_list")),
        )
        return {
            "ok": True,
            "mode": str(repaired_probe.get("kind") or "auto_repaired").strip()
            or "auto_repaired",
            "invoke_command": str(repaired_probe.get("invoke") or "").strip(),
            "probe_command": str(repaired_probe.get("probe") or "").strip(),
            "attempts": list(repaired_probe.get("attempts") or probe.get("attempts") or []),
            "repaired": True,
            "repair": repair_result,
        }

    def _import_external_skill_from_workspace(
        self,
        *,
        source_repo_path: str,
        target_dir: str,
        skill_name: str,
        repo_url: str,
    ) -> Dict[str, Any]:
        source_root = self._detect_source_skill_root(source_repo_path, skill_name)
        if not source_root.exists() or not source_root.is_dir():
            return {
                "ok": False,
                "error_code": "source_skill_not_found",
                "message": f"source skill root not found: {source_root}",
            }

        target_path = Path(str(target_dir or "").strip()).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        staging_path = target_path.parent / f".{target_path.name}.import-{stamp}"
        backup_path = target_path.parent / f".{target_path.name}.backup-{stamp}"

        try:
            if staging_path.exists():
                shutil.rmtree(staging_path)
            if backup_path.exists():
                shutil.rmtree(backup_path)

            shutil.copytree(
                source_root,
                staging_path,
                ignore=self._copy_ignore_names,
            )
            self._ensure_imported_skill_markdown(
                skill_dir=str(staging_path),
                skill_name=skill_name,
                repo_url=repo_url,
            )
            applied_overrides = self._overlay_local_import_overrides(
                staging_path=staging_path,
                existing_target=target_path,
            )

            if target_path.exists():
                os.replace(target_path, backup_path)
            os.replace(staging_path, target_path)
            if backup_path.exists():
                shutil.rmtree(backup_path)
        except Exception as exc:
            try:
                if target_path.exists():
                    shutil.rmtree(target_path)
                if backup_path.exists():
                    os.replace(backup_path, target_path)
            except Exception:
                pass
            try:
                if staging_path.exists():
                    shutil.rmtree(staging_path)
            except Exception:
                pass
            return {
                "ok": False,
                "error_code": "skill_import_failed",
                "message": str(exc),
            }

        file_count = 0
        for path in target_path.rglob("*"):
            if path.is_file():
                file_count += 1

        return {
            "ok": True,
            "backend": "import",
            "summary": f"imported external skill files into {target_path.name}",
            "source_root": str(source_root),
            "target_dir": str(target_path),
            "file_count": file_count,
            "applied_overrides": applied_overrides,
        }

    async def _mark_failed(
        self, *, task_id: str, summary: str, error_code: str
    ) -> None:
        record = await self._load_task(task_id)
        if not record:
            return
        record["status"] = "failed"
        record["error"] = str(summary or "unknown error").strip()
        self._append_event(
            record,
            name="background_failed",
            detail=record["error"],
            data={"error_code": str(error_code or "background_failed")},
        )
        await self.tasks.save(record)

    @staticmethod
    def _clean_notify_payload(
        *,
        platform: str = "",
        chat_id: str = "",
        user_id: str = "",
    ) -> Dict[str, str]:
        return {
            "platform": str(platform or "").strip(),
            "chat_id": str(chat_id or "").strip(),
            "user_id": str(user_id or "").strip(),
            "completion_sent_at": "",
        }

    @staticmethod
    def _apply_notify_payload(
        record: Dict[str, Any],
        *,
        platform: str = "",
        chat_id: str = "",
        user_id: str = "",
    ) -> None:
        current = dict(record.get("notify") or {})
        merged = {
            "platform": str(platform or current.get("platform") or "").strip(),
            "chat_id": str(chat_id or current.get("chat_id") or "").strip(),
            "user_id": str(user_id or current.get("user_id") or "").strip(),
            "completion_sent_at": str(current.get("completion_sent_at") or "").strip(),
        }
        record["notify"] = merged

    async def _resolve_notify_payload(
        self,
        record: Dict[str, Any],
    ) -> Dict[str, str]:
        payload = self._clean_notify_payload(
            platform=str(dict(record.get("notify") or {}).get("platform") or ""),
            chat_id=str(dict(record.get("notify") or {}).get("chat_id") or ""),
            user_id=str(dict(record.get("notify") or {}).get("user_id") or ""),
        )
        if payload["platform"] and payload["chat_id"]:
            return payload
        if not payload["user_id"]:
            return payload
        target = await heartbeat_store.get_delivery_target(payload["user_id"])
        target_platform = str((target or {}).get("platform") or "").strip()
        target_chat_id = str((target or {}).get("chat_id") or "").strip()
        if target_platform and target_chat_id:
            payload["platform"] = target_platform
            payload["chat_id"] = target_chat_id
        return payload

    @staticmethod
    def _build_completion_message(record: Dict[str, Any]) -> str:
        task_id = str(record.get("task_id") or "").strip()
        status = str(record.get("status") or "").strip().lower() or "unknown"
        goal = str(record.get("goal") or record.get("requirement") or "").strip()
        error = str(record.get("error") or "").strip()
        detail = ManagerDevService._completion_summary_text(record)

        if status in {"done", "validated"}:
            summary = detail or "任务已完成"
            header = "✅ software_delivery 任务已完成"
        else:
            summary = error or detail or "任务执行失败"
            header = "❌ software_delivery 任务失败"

        lines = [header, f"任务编号: `{task_id}`"]
        if goal:
            lines.append(f"目标: {goal[:160]}")
        lines.append(f"状态: `{status}`")
        if summary:
            lines.append(f"摘要: {summary[:500]}")
        return "\n".join(lines)

    async def _notify_task_completion(self, record: Dict[str, Any]) -> None:
        if not isinstance(record, dict):
            return
        status = str(record.get("status") or "").strip().lower()
        if status not in {"done", "failed", "validated"}:
            return

        notify_payload = await self._resolve_notify_payload(record)
        completion_sent_at = str(
            dict(record.get("notify") or {}).get("completion_sent_at") or ""
        ).strip()
        if completion_sent_at:
            return

        platform = notify_payload.get("platform", "")
        chat_id = notify_payload.get("chat_id", "")
        if not platform or not chat_id:
            return

        try:
            from core.platform.registry import adapter_manager

            adapter = adapter_manager.get_adapter(platform)
            send_message = getattr(adapter, "send_message", None)
            if not callable(send_message):
                return
            await send_message(chat_id=chat_id, text=self._build_completion_message(record))
        except Exception:
            logger.debug(
                "software_delivery completion notify failed task_id=%s",
                str(record.get("task_id") or "").strip(),
                exc_info=True,
            )
            return

        self._apply_notify_payload(
            record,
            platform=notify_payload.get("platform", ""),
            chat_id=notify_payload.get("chat_id", ""),
            user_id=notify_payload.get("user_id", ""),
        )
        saved_notify = dict(record.get("notify") or {})
        saved_notify["completion_sent_at"] = _now_iso()
        record["notify"] = saved_notify
        await self.tasks.save(record)

    async def _notify_task_progress(
        self,
        record: Dict[str, Any],
        *,
        force: bool = False,
    ) -> None:
        if not isinstance(record, dict):
            return
        status = str(record.get("status") or "").strip().lower()
        if status in {"done", "failed", "validated"}:
            return

        notify_payload = await self._resolve_notify_payload(record)
        platform = notify_payload.get("platform", "")
        chat_id = notify_payload.get("chat_id", "")
        if not platform or not chat_id:
            return

        text = self._build_progress_message(record)
        if not text:
            return

        notify_state = dict(record.get("notify") or {})
        last_rendered = str(notify_state.get("progress_last_rendered") or "")
        last_sent_raw = str(notify_state.get("progress_last_sent_at") or "0").strip()
        try:
            last_sent = float(last_sent_raw or 0.0)
        except Exception:
            last_sent = 0.0
        now_ts = datetime.now().timestamp()
        if (
            not force
            and text == last_rendered
            and now_ts - last_sent < self._progress_interval_sec()
        ):
            return

        try:
            from core.platform.registry import adapter_manager

            adapter = adapter_manager.get_adapter(platform)
        except Exception:
            return

        delivered = False
        send_draft = getattr(adapter, "send_message_draft", None)
        if platform == "telegram" and callable(send_draft):
            try:
                result_obj = send_draft(
                    chat_id=chat_id,
                    draft_id=self._task_draft_id(str(record.get("task_id") or "").strip()),
                    text=text,
                    fallback_to_message=False,
                )
                if inspect.isawaitable(result_obj):
                    await result_obj
                delivered = True
            except Exception:
                logger.debug(
                    "software_delivery progress draft failed task_id=%s",
                    str(record.get("task_id") or "").strip(),
                    exc_info=True,
                )

        if not delivered and platform != "telegram":
            send_message = getattr(adapter, "send_message", None)
            if callable(send_message):
                try:
                    result_obj = send_message(chat_id=chat_id, text=text)
                    if inspect.isawaitable(result_obj):
                        await result_obj
                    delivered = True
                except Exception:
                    logger.debug(
                        "software_delivery progress notify failed task_id=%s",
                        str(record.get("task_id") or "").strip(),
                        exc_info=True,
                    )

        if not delivered:
            return

        self._apply_notify_payload(
            record,
            platform=notify_payload.get("platform", ""),
            chat_id=notify_payload.get("chat_id", ""),
            user_id=notify_payload.get("user_id", ""),
        )
        saved_notify = dict(record.get("notify") or {})
        saved_notify["progress_last_sent_at"] = str(now_ts)
        saved_notify["progress_last_rendered"] = text
        record["notify"] = saved_notify
        await self.tasks.save(record)

    async def _heartbeat_coding_progress(
        self,
        *,
        task_id: str,
        stage: str,
        cwd: str,
        backend: str,
        log_path: str,
    ) -> None:
        started_at = datetime.now().timestamp()
        while True:
            await asyncio.sleep(self._progress_interval_sec())
            record = await self._load_task(task_id)
            if not record:
                return
            status = str(record.get("status") or "").strip().lower()
            if status in {"done", "failed", "validated"}:
                return

            progress = {
                "stage": str(stage or "").strip(),
                "backend": str(backend or "").strip(),
                "cwd": str(cwd or "").strip(),
                "elapsed_sec": max(0, int(datetime.now().timestamp() - started_at)),
                "heartbeat_at": _now_iso(),
                "note": "后台编码仍在执行",
            }
            progress.update(self._latest_skill_file_hint(cwd))
            record["progress"] = progress
            await self.tasks.save(record)

            latest_file = str(progress.get("latest_file") or "").strip()
            latest_suffix = f" latest_file={latest_file}" if latest_file else ""
            self._append_progress_log(
                log_path,
                (
                    f"[{_now_iso()}] heartbeat stage={progress['stage']} "
                    f"backend={progress['backend']} elapsed={progress['elapsed_sec']}s"
                    f"{latest_suffix}"
                ),
            )
            await self._notify_task_progress(record, force=True)

    async def _run_coding_backend_with_progress(
        self,
        *,
        task_id: str,
        stage: str,
        instruction: str,
        backend: str,
        cwd: str,
        timeout_sec: int,
        source: str,
        log_path: str,
    ) -> Dict[str, Any]:
        heartbeat_task = asyncio.create_task(
            self._heartbeat_coding_progress(
                task_id=task_id,
                stage=stage,
                cwd=cwd,
                backend=backend,
                log_path=log_path,
            ),
            name=f"software_delivery-heartbeat:{task_id}:{stage}",
        )
        try:
            return await run_coding_backend(
                instruction=instruction,
                backend=backend,
                cwd=cwd,
                timeout_sec=timeout_sec,
                source=source,
                log_path=log_path,
            )
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    async def _run_background_job(
        self,
        *,
        task_id: str,
        job_coro: Any,
    ) -> None:
        try:
            await job_coro
        except asyncio.CancelledError:
            await self._mark_failed(
                task_id=task_id,
                summary="background job cancelled",
                error_code="background_cancelled",
            )
            raise
        except Exception as exc:
            logger.exception("software_delivery background job failed: %s", task_id)
            await self._mark_failed(
                task_id=task_id,
                summary=str(exc),
                error_code="background_exception",
            )
        finally:
            record = await self._load_task(task_id)
            if record:
                await self._notify_task_completion(record)
            self._background_jobs.pop(str(task_id or "").strip(), None)

    def _spawn_background(self, *, task_id: str, job_coro: Any) -> None:
        safe_task_id = str(task_id or "").strip()
        if not safe_task_id:
            return
        existing = self._background_jobs.get(safe_task_id)
        if isinstance(existing, asyncio.Task) and not existing.done():
            existing.cancel()
        task = asyncio.create_task(
            self._run_background_job(task_id=safe_task_id, job_coro=job_coro),
            name=f"software_delivery:{safe_task_id}",
        )
        self._background_jobs[safe_task_id] = task

    def _async_dispatch_response(
        self,
        *,
        task_id: str,
        status: str,
        summary: str,
        backend: str,
        mode: str,
    ) -> Dict[str, Any]:
        safe_task_id = str(task_id or "").strip()
        safe_status = str(status or "queued").strip() or "queued"
        safe_summary = str(summary or "software_delivery task queued").strip()
        safe_backend = str(
            backend or os.getenv("CODING_BACKEND_DEFAULT", "codex")
        ).strip()
        text = (
            f"software_delivery task queued: {safe_task_id} "
            f"(status={safe_status}, mode={mode}, backend={safe_backend})"
        )
        return {
            "ok": True,
            "task_id": safe_task_id,
            "status": safe_status,
            "summary": safe_summary,
            "text": text,
            "data": {
                "mode": mode,
                "backend": safe_backend,
                "task_id": safe_task_id,
            },
            "terminal": False,
            "task_outcome": "partial",
            "async_dispatch": True,
            "worker_name": f"software_delivery/{safe_backend or 'codex'}",
        }

    def _build_instruction(self, record: Dict[str, Any]) -> str:
        goal = str(record.get("goal") or record.get("requirement") or "").strip()
        issue_payload = dict(record.get("issue") or {})
        plan_payload = dict(record.get("plan") or {})
        acceptance = [
            str(item).strip()
            for item in list(plan_payload.get("acceptance") or [])
            if str(item).strip()
        ]
        steps = [
            str(item).strip()
            for item in list(plan_payload.get("steps") or [])
            if str(item).strip()
        ]

        sections: List[str] = []
        sections.append("Implement software changes directly in this repository.")
        sections.append(f"Primary goal: {goal}")

        issue_number = int(issue_payload.get("number") or 0)
        if issue_number > 0:
            issue_lines = [
                f"Issue #{issue_number}: {str(issue_payload.get('title') or '').strip()}",
                _short(str(issue_payload.get("body") or ""), 3000),
            ]
            comments = list(issue_payload.get("comments") or [])
            if comments:
                rendered_comments: List[str] = []
                for row in comments[:6]:
                    if not isinstance(row, dict):
                        continue
                    author = str(row.get("user") or "").strip()
                    body = _short(str(row.get("body") or ""), 500)
                    if body:
                        rendered_comments.append(f"- {author}: {body}")
                if rendered_comments:
                    issue_lines.append(
                        "Issue comments:\n" + "\n".join(rendered_comments)
                    )
            sections.append("\n".join([item for item in issue_lines if item]))

        if steps:
            sections.append(
                "Execution steps:\n" + "\n".join([f"- {item}" for item in steps])
            )
        if acceptance:
            sections.append(
                "Acceptance criteria:\n"
                + "\n".join([f"- {item}" for item in acceptance])
            )

        sections.append(
            "Required output:\n"
            "- Modify project files to implement the goal\n"
            "- Update or add tests when behavior changes\n"
            "- Keep changes minimal and aligned with existing conventions"
        )
        return "\n\n".join([item for item in sections if item])

    async def _fetch_issue_if_needed(
        self,
        *,
        issue: str,
        owner: str,
        repo: str,
    ) -> Dict[str, Any]:
        safe_issue = str(issue or "").strip()
        if not safe_issue:
            return {}
        return await self.github.fetch_issue(
            safe_issue,
            default_owner=str(owner or "").strip(),
            default_repo=str(repo or "").strip(),
        )

    async def read_issue(
        self,
        *,
        issue: str,
        owner: str = "",
        repo: str = "",
    ) -> Dict[str, Any]:
        try:
            payload = await self._fetch_issue_if_needed(
                issue=issue,
                owner=owner,
                repo=repo,
            )
        except GitHubClientError as exc:
            return self._response(
                ok=False,
                summary="read_issue failed",
                text=str(exc),
                error_code="read_issue_failed",
            )

        return self._response(
            ok=True,
            summary=f"Issue loaded: #{int(payload.get('number') or 0)}",
            text=f"Issue loaded: {str(payload.get('title') or '').strip()}",
            data={"issue": payload},
        )

    async def plan(
        self,
        *,
        requirement: str,
        issue: str = "",
        repo_path: str = "",
        repo_url: str = "",
        owner: str = "",
        repo: str = "",
        base_branch: str = "",
        target_service: str = "manager",
        rollout: str = "none",
        validate_only: bool = False,
        notify_platform: str = "",
        notify_chat_id: str = "",
        notify_user_id: str = "",
    ) -> Dict[str, Any]:
        workspace = await self.workspace.prepare_workspace(
            repo_path=repo_path,
            repo_url=repo_url,
            owner=owner,
            repo=repo,
        )
        if not workspace.get("ok"):
            return self._response(
                ok=False,
                summary="plan failed",
                text=str(workspace.get("message") or "workspace resolution failed"),
                error_code=str(workspace.get("error_code") or "workspace_failed"),
                data={"workspace": workspace},
            )

        issue_payload: Dict[str, Any] = {}
        if str(issue or "").strip():
            try:
                issue_payload = await self._fetch_issue_if_needed(
                    issue=issue,
                    owner=str(workspace.get("owner") or owner or ""),
                    repo=str(workspace.get("repo") or repo or ""),
                )
            except GitHubClientError as exc:
                return self._response(
                    ok=False,
                    summary="plan failed",
                    text=str(exc),
                    error_code="issue_fetch_failed",
                )

        plan_payload = self.planner.build_plan(
            requirement=str(requirement or "").strip(),
            issue=issue_payload,
            repo_owner=str(workspace.get("owner") or ""),
            repo_name=str(workspace.get("repo") or ""),
        )

        git_payload = {
            "branch_name": str(plan_payload.get("branch_name") or "").strip(),
            "base_branch": str(
                base_branch or workspace.get("default_branch") or "main"
            ).strip()
            or "main",
            "commit_message": str(plan_payload.get("commit_message") or "").strip(),
            "pr_title": str(plan_payload.get("pr_title") or "").strip(),
            "pr_body": str(plan_payload.get("pr_body") or "").strip(),
        }

        record = {
            "status": "planned",
            "requirement": str(requirement or "").strip(),
            "goal": str(plan_payload.get("goal") or "").strip(),
            "issue_ref": str(issue or "").strip(),
            "issue": issue_payload,
            "repo": {
                "path": str(workspace.get("path") or "").strip(),
                "url": str(workspace.get("origin_url") or repo_url or "").strip(),
                "owner": str(workspace.get("owner") or "").strip(),
                "name": str(workspace.get("repo") or "").strip(),
                "default_branch": str(
                    workspace.get("default_branch") or "main"
                ).strip(),
            },
            "plan": {
                "steps": list(plan_payload.get("steps") or []),
                "acceptance": list(plan_payload.get("acceptance") or []),
            },
            "git": git_payload,
            "delivery": {
                "target_service": _normalize_target_service(target_service),
                "rollout": _normalize_rollout_mode(rollout),
                "validate_only": bool(validate_only),
            },
            "notify": self._clean_notify_payload(
                platform=notify_platform,
                chat_id=notify_chat_id,
                user_id=notify_user_id,
            ),
            "implementation": {},
            "validation": {},
            "publish": {},
            "rollout": {},
            "events": [],
            "error": "",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        self._append_event(
            record,
            name="plan_created",
            detail=f"Plan created for goal: {record['goal']}",
        )
        saved = await self.tasks.create(record)

        return self._response(
            ok=True,
            summary="development plan created",
            task_id=str(saved.get("task_id") or "").strip(),
            status=str(saved.get("status") or "planned"),
            text=f"Plan ready: {saved.get('goal')}",
            data={
                "goal": saved.get("goal"),
                "plan": saved.get("plan"),
                "repo": saved.get("repo"),
                "git": saved.get("git"),
            },
        )

    async def implement(
        self,
        *,
        task_id: str,
        backend: str = "",
        branch_name: str = "",
        base_branch: str = "",
        timeout_sec: int = 2400,
    ) -> Dict[str, Any]:
        record = await self._load_task(task_id)
        if not record:
            return self._response(
                ok=False,
                summary="implement failed",
                text="task not found",
                error_code="task_not_found",
            )

        repo_payload = dict(record.get("repo") or {})
        git_payload = dict(record.get("git") or {})
        safe_repo_path = str(repo_payload.get("path") or "").strip()

        branch_result = await self.workspace.ensure_branch(
            repo_path=safe_repo_path,
            branch_name=str(
                branch_name or git_payload.get("branch_name") or ""
            ).strip(),
            base_branch=str(
                base_branch or git_payload.get("base_branch") or ""
            ).strip(),
        )
        if not branch_result.get("ok"):
            record["status"] = "failed"
            record["error"] = str(
                branch_result.get("message") or "failed to prepare branch"
            )
            self._append_event(
                record,
                name="implement_failed",
                detail=record["error"],
                data={"stage": "ensure_branch"},
            )
            await self.tasks.save(record)
            return self._response(
                ok=False,
                summary="implement failed",
                task_id=str(record.get("task_id") or "").strip(),
                status=str(record.get("status") or "failed"),
                text=record["error"],
                error_code="ensure_branch_failed",
                data={"branch": branch_result},
            )

        git_payload["branch_name"] = str(branch_result.get("branch_name") or "").strip()
        if str(branch_result.get("base_branch") or "").strip():
            git_payload["base_branch"] = str(
                branch_result.get("base_branch") or ""
            ).strip()
        record["git"] = git_payload
        record["status"] = "implementing"
        record["error"] = ""
        self._append_event(
            record,
            name="implement_started",
            detail=f"backend={backend or os.getenv('CODING_BACKEND_DEFAULT', 'codex')}",
        )
        await self.tasks.save(record)
        await self._notify_task_progress(record, force=True)

        instruction = self._build_instruction(record)
        task_log_path = self._task_log_path(str(record.get("task_id") or ""))
        result = await self._run_coding_backend_with_progress(
            task_id=str(record.get("task_id") or "").strip(),
            stage="implement",
            instruction=instruction,
            backend=str(backend or "").strip(),
            cwd=safe_repo_path,
            timeout_sec=max(300, int(timeout_sec or 2400)),
            source="manager_dev_service",
            log_path=task_log_path,
        )
        record = await self._load_task(str(record.get("task_id") or "").strip()) or record

        record["implementation"] = {
            "backend": str(result.get("backend") or "").strip(),
            "result": result,
        }
        record["logs"] = {"path": task_log_path}
        if not result.get("ok"):
            record["status"] = "failed"
            record["error"] = str(result.get("summary") or result.get("message") or "")
            self._append_event(
                record,
                name="implement_failed",
                detail=record["error"],
                data={"error_code": str(result.get("error_code") or "")},
            )
            await self.tasks.save(record)
            return self._response(
                ok=False,
                summary="implementation failed",
                task_id=str(record.get("task_id") or "").strip(),
                status="failed",
                text=record["error"],
                error_code=str(result.get("error_code") or "implementation_failed"),
                data={"implementation": record.get("implementation")},
            )

        record["status"] = "implemented"
        record["error"] = ""
        self._append_event(
            record,
            name="implement_done",
            detail=self._completion_summary_text(
                {"implementation": {"result": result}, "events": record.get("events")}
            )
            or "implemented",
        )
        await self.tasks.save(record)
        return self._response(
            ok=True,
            summary="implementation completed",
            task_id=str(record.get("task_id") or "").strip(),
            status="implemented",
            text=str(result.get("summary") or "implementation completed"),
            data={"implementation": record.get("implementation")},
            task_outcome="partial",
        )

    async def validate(
        self,
        *,
        task_id: str,
        validation_commands: List[str] | None = None,
        timeout_sec: int = 1800,
    ) -> Dict[str, Any]:
        record = await self._load_task(task_id)
        if not record:
            return self._response(
                ok=False,
                summary="validate failed",
                text="task not found",
                error_code="task_not_found",
            )

        repo_payload = dict(record.get("repo") or {})
        safe_repo_path = str(repo_payload.get("path") or "").strip()
        record["status"] = "validating"
        record["error"] = ""
        self._append_event(record, name="validate_started", detail="running validation")
        await self.tasks.save(record)

        result = await self.validator.validate(
            repo_path=safe_repo_path,
            validation_commands=validation_commands,
            timeout_sec=max(60, int(timeout_sec or 1800)),
        )
        record["validation"] = result

        if not result.get("ok"):
            record["status"] = "failed"
            record["error"] = str(result.get("summary") or "validation failed")
            self._append_event(
                record,
                name="validate_failed",
                detail=record["error"],
            )
            await self.tasks.save(record)
            return self._response(
                ok=False,
                summary="validation failed",
                task_id=str(record.get("task_id") or "").strip(),
                status="failed",
                text=record["error"],
                error_code="validation_failed",
                data={"validation": result},
            )

        record["status"] = "validated"
        record["error"] = ""
        self._append_event(
            record,
            name="validate_done",
            detail=str(result.get("summary") or "validation passed"),
        )
        await self.tasks.save(record)
        return self._response(
            ok=True,
            summary="validation passed",
            task_id=str(record.get("task_id") or "").strip(),
            status="validated",
            text=str(result.get("summary") or "validation passed"),
            data={"validation": result},
            task_outcome="partial",
        )

    async def publish(
        self,
        *,
        task_id: str,
        commit_message: str = "",
        pr_title: str = "",
        pr_body: str = "",
        base_branch: str = "",
        auto_push: bool = True,
        auto_pr: bool = True,
        target_service: str = "",
        rollout: str = "none",
    ) -> Dict[str, Any]:
        record = await self._load_task(task_id)
        if not record:
            return self._response(
                ok=False,
                summary="publish failed",
                text="task not found",
                error_code="task_not_found",
                terminal=True,
                task_outcome="failed",
            )

        repo_payload = dict(record.get("repo") or {})
        git_payload = dict(record.get("git") or {})
        delivery_payload = dict(record.get("delivery") or {})

        safe_repo_path = str(repo_payload.get("path") or "").strip()
        safe_owner = str(repo_payload.get("owner") or "").strip()
        safe_repo = str(repo_payload.get("name") or "").strip()
        safe_branch_name = str(git_payload.get("branch_name") or "").strip()
        safe_base_branch = (
            str(base_branch or git_payload.get("base_branch") or "").strip() or "main"
        )
        safe_commit_message = (
            str(commit_message or git_payload.get("commit_message") or "").strip()
            or "chore: update project"
        )
        safe_pr_title = (
            str(pr_title or git_payload.get("pr_title") or "").strip()
            or safe_commit_message
        )
        safe_pr_body = str(pr_body or git_payload.get("pr_body") or "").strip()
        resolved_target_service = _normalize_target_service(
            target_service or delivery_payload.get("target_service") or "manager"
        )
        resolved_rollout = _normalize_rollout_mode(
            rollout or delivery_payload.get("rollout") or "none"
        )
        delivery_payload.update(
            {
                "target_service": resolved_target_service,
                "rollout": resolved_rollout,
            }
        )
        record["delivery"] = delivery_payload

        record["status"] = "publishing"
        record["error"] = ""
        self._append_event(record, name="publish_started", detail="publishing changes")
        await self.tasks.save(record)

        publish_result = await self.publisher.publish(
            repo_path=safe_repo_path,
            owner=safe_owner,
            repo=safe_repo,
            branch_name=safe_branch_name,
            base_branch=safe_base_branch,
            commit_message=safe_commit_message,
            pr_title=safe_pr_title,
            pr_body=safe_pr_body,
            auto_push=bool(auto_push),
            auto_pr=bool(auto_pr),
        )
        record["publish"] = publish_result

        if not publish_result.get("ok"):
            record["status"] = "failed"
            record["error"] = str(publish_result.get("message") or "publish failed")
            self._append_event(
                record,
                name="publish_failed",
                detail=record["error"],
            )
            await self.tasks.save(record)
            return self._response(
                ok=False,
                summary="publish failed",
                task_id=str(record.get("task_id") or "").strip(),
                status="failed",
                text=record["error"],
                error_code=str(publish_result.get("error_code") or "publish_failed"),
                data={"publish": publish_result},
                terminal=True,
                task_outcome="failed",
            )

        issue_payload = dict(record.get("issue") or {})
        issue_number = int(issue_payload.get("number") or 0)
        pr_url = str(
            (publish_result.get("pull_request") or {}).get("html_url") or ""
        ).strip()
        if issue_number > 0 and pr_url:
            try:
                comment = await self.github.create_issue_comment(
                    owner=safe_owner,
                    repo=safe_repo,
                    issue_number=issue_number,
                    body=(
                        "Automated update from manager software delivery pipeline.\n\n"
                        f"Pull request: {pr_url}"
                    ),
                )
                record["issue_comment"] = comment
                self._append_event(
                    record,
                    name="issue_commented",
                    detail=f"Issue #{issue_number} commented with PR link",
                )
            except GitHubClientError as exc:
                record["issue_comment_error"] = str(exc)
                self._append_event(
                    record,
                    name="issue_comment_failed",
                    detail=str(exc),
                )

        rollout_result: Dict[str, Any] = {}
        if resolved_rollout == "local":
            record["status"] = "rolling_out"
            self._append_event(
                record,
                name="rollout_started",
                detail=f"target_service={resolved_target_service}",
            )
            await self.tasks.save(record)
            rollout_result = await self.publisher.rollout_local(
                repo_path=safe_repo_path,
                target_service=resolved_target_service,
            )
            record["rollout"] = rollout_result
            if not rollout_result.get("ok"):
                record["status"] = "failed"
                record["error"] = str(
                    rollout_result.get("message") or "rollout failed"
                ).strip()
                self._append_event(
                    record,
                    name="rollout_failed",
                    detail=record["error"],
                    data={"rollback": dict(rollout_result.get("rollback") or {})},
                )
                await self.tasks.save(record)
                return self._response(
                    ok=False,
                    summary="rollout failed",
                    task_id=str(record.get("task_id") or "").strip(),
                    status="failed",
                    text=record["error"],
                    error_code=str(
                        rollout_result.get("error_code") or "rollout_failed"
                    ),
                    data={
                        "publish": publish_result,
                        "rollout": rollout_result,
                        "pr_url": pr_url,
                    },
                    terminal=True,
                    task_outcome="failed",
                )
            self._append_event(
                record,
                name="rollout_done",
                detail=str(
                    rollout_result.get("summary") or "local rollout completed"
                ).strip(),
                data={"target_service": resolved_target_service},
            )

        record["status"] = "done"
        record["error"] = ""
        self._append_event(
            record,
            name="publish_done",
            detail="publish completed",
            data={"pr_url": pr_url},
        )
        await self.tasks.save(record)
        return self._response(
            ok=True,
            summary="publish completed",
            task_id=str(record.get("task_id") or "").strip(),
            status="done",
            text=str(pr_url or "Publish completed"),
            data={
                "publish": publish_result,
                "rollout": rollout_result,
                "pr_url": pr_url,
            },
            terminal=True,
            task_outcome="done",
        )

    def _fallback_template_instruction(
        self,
        *,
        action: str,
        instruction: str,
        skill_name: str,
    ) -> str:
        safe_instruction = str(instruction or "").strip()
        if safe_instruction:
            return safe_instruction

        safe_skill = str(skill_name or "").strip()
        if action == "skill_modify":
            if safe_skill:
                return (
                    f"请修改技能 `{safe_skill}`，根据用户请求更新能力，"
                    "并保持 SKILL.md 与 scripts 结构可加载。"
                )
            return ""

        if safe_skill:
            return (
                f"请创建技能 `{safe_skill}`，根据用户请求实现能力，"
                "并生成有效的 SKILL.md。"
            )
        return "请根据用户请求创建一个新技能，并生成有效的 SKILL.md。"

    def _resolve_skill_template_cwd(self, *, action: str, skill_name: str) -> str:
        return resolve_skill_target_dir(action=action, skill_name=skill_name)

    def _resolve_skill_contract(
        self,
        *,
        action: str,
        skill_name: str,
        cwd: str,
    ) -> Dict[str, Any]:
        return resolve_skill_contract(action=action, skill_name=skill_name, cwd=cwd)

    async def _run_skill_contract_preflight(
        self,
        *,
        contract: Dict[str, Any],
        cwd: str,
    ) -> Dict[str, Any]:
        return await run_skill_contract_preflight(contract=contract, cwd=cwd)

    async def _queue_existing_task(
        self,
        *,
        task_id: str,
        mode: str,
        backend: str,
        summary: str,
        job_coro: Any,
        notify_platform: str = "",
        notify_chat_id: str = "",
        notify_user_id: str = "",
    ) -> Dict[str, Any]:
        def _dispose_coro(value: Any) -> None:
            if asyncio.iscoroutine(value):
                value.close()

        safe_task_id = str(task_id or "").strip()
        if not safe_task_id:
            _dispose_coro(job_coro)
            return self._response(
                ok=False,
                summary="queue failed",
                text="task_id is required",
                error_code="invalid_args",
            )

        record = await self._load_task(safe_task_id)
        if not record:
            _dispose_coro(job_coro)
            return self._response(
                ok=False,
                summary="queue failed",
                text="task not found",
                error_code="task_not_found",
            )

        current_status = str(record.get("status") or "").strip().lower()
        if current_status in {"done", "failed"}:
            _dispose_coro(job_coro)
            return await self.status(task_id=safe_task_id)

        record["status"] = "queued"
        record["error"] = ""
        record.setdefault("logs", {})
        if isinstance(record["logs"], dict):
            record["logs"]["path"] = self._task_log_path(safe_task_id)
        self._apply_notify_payload(
            record,
            platform=notify_platform,
            chat_id=notify_chat_id,
            user_id=notify_user_id,
        )
        self._append_event(
            record,
            name="queued",
            detail=f"queued for async execution: mode={mode}",
            data={"backend": str(backend or "").strip()},
        )
        await self.tasks.save(record)
        self._spawn_background(task_id=safe_task_id, job_coro=job_coro)
        return self._async_dispatch_response(
            task_id=safe_task_id,
            status="queued",
            summary=summary,
            backend=backend,
            mode=mode,
        )

    async def _background_run_pipeline(
        self,
        *,
        task_id: str,
        backend: str,
        branch_name: str,
        base_branch: str,
        commit_message: str,
        pr_title: str,
        pr_body: str,
        validation_commands: List[str] | None,
        auto_publish: bool,
        auto_push: bool,
        auto_pr: bool,
        target_service: str,
        rollout: str,
        validate_only: bool,
    ) -> None:
        impl = await self.implement(
            task_id=task_id,
            backend=backend,
            branch_name=branch_name,
            base_branch=base_branch,
        )
        if not impl.get("ok"):
            return

        val = await self.validate(
            task_id=task_id,
            validation_commands=validation_commands,
        )
        if not val.get("ok"):
            return

        if validate_only:
            record = await self._load_task(task_id)
            if not record:
                return
            record["status"] = "validated"
            record["error"] = ""
            self._append_event(
                record,
                name="validate_only_done",
                detail="validation completed without publish",
            )
            await self.tasks.save(record)
            return

        if auto_publish:
            await self.publish(
                task_id=task_id,
                commit_message=commit_message,
                pr_title=pr_title,
                pr_body=pr_body,
                base_branch=base_branch,
                auto_push=auto_push,
                auto_pr=auto_pr,
                target_service=target_service,
                rollout=rollout,
            )
            return

        record = await self._load_task(task_id)
        if not record:
            return
        record["status"] = "done"
        record["error"] = ""
        self._append_event(
            record,
            name="run_done",
            detail="run pipeline completed without publish",
        )
        await self.tasks.save(record)

    async def _background_resume_pipeline(
        self,
        *,
        task_id: str,
        backend: str,
        commit_message: str,
        pr_title: str,
        pr_body: str,
        base_branch: str,
        validation_commands: List[str] | None,
        auto_publish: bool,
        auto_push: bool,
        auto_pr: bool,
        target_service: str,
        rollout: str,
        validate_only: bool,
    ) -> None:
        await self.resume(
            task_id=task_id,
            backend=backend,
            commit_message=commit_message,
            pr_title=pr_title,
            pr_body=pr_body,
            base_branch=base_branch,
            validation_commands=validation_commands,
            auto_publish=auto_publish,
            auto_push=auto_push,
            auto_pr=auto_pr,
            target_service=target_service,
            rollout=rollout,
            validate_only=validate_only,
        )

    async def _background_implement(
        self,
        *,
        task_id: str,
        backend: str,
        branch_name: str,
        base_branch: str,
    ) -> None:
        await self.implement(
            task_id=task_id,
            backend=backend,
            branch_name=branch_name,
            base_branch=base_branch,
        )

    async def _background_skill_template(
        self,
        *,
        task_id: str,
        action: str,
        instruction: str,
        cwd: str,
        backend: str,
        source: str,
        timeout_sec: int,
        skill_name: str,
        source_repo_url: str,
        source_repo_path: str,
    ) -> None:
        record = await self._load_task(task_id)
        if not record:
            return

        safe_backend = str(backend or "").strip()
        safe_source = str(source or f"software_delivery_{action}").strip()
        safe_cwd = str(cwd or "").strip()
        safe_source_repo_url = str(source_repo_url or "").strip()
        safe_source_repo_path = str(source_repo_path or "").strip()
        log_path = self._task_log_path(task_id)
        contract = dict(record.get("skill_contract") or {})

        record["status"] = "implementing"
        record["error"] = ""
        record.setdefault("logs", {})
        if isinstance(record["logs"], dict):
            record["logs"]["path"] = log_path
        self._append_event(
            record,
            name="skill_template_started",
            detail=f"backend={safe_backend or os.getenv('CODING_BACKEND_DEFAULT', 'codex')}",
            data={"action": action, "skill_name": skill_name},
        )
        await self.tasks.save(record)
        await self._notify_task_progress(record, force=True)

        source_workspace: Dict[str, Any] = {}
        if safe_source_repo_url or safe_source_repo_path:
            source_workspace = await self.workspace.prepare_workspace(
                repo_path=safe_source_repo_path,
                repo_url=safe_source_repo_url,
            )
            record["source_repo"] = dict(source_workspace or {})
            await self.tasks.save(record)
            if not source_workspace.get("ok"):
                record["status"] = "failed"
                record["error"] = str(
                    source_workspace.get("message")
                    or "failed to prepare external skill source workspace"
                ).strip()
                self._append_event(
                    record,
                    name="skill_template_source_repo_failed",
                    detail=record["error"],
                )
                await self.tasks.save(record)
                return

        effective_instruction = str(instruction or "").strip()
        source_repo_path_text = str(source_workspace.get("path") or "").strip()
        if source_repo_path_text:
            effective_instruction = "\n\n".join(
                [
                    effective_instruction,
                    (
                        "外部技能源码参考仓库已准备好，请从该目录提取并适配到当前目标技能目录，"
                        "不要把最终实现留在外部仓库工作区："
                    ),
                    source_repo_path_text,
                ]
            ).strip()

        if (
            action == "skill_create"
            and source_repo_path_text
            and (safe_source_repo_url or safe_source_repo_path)
        ):
            imported = self._import_external_skill_from_workspace(
                source_repo_path=source_repo_path_text,
                target_dir=safe_cwd,
                skill_name=skill_name,
                repo_url=safe_source_repo_url,
            )
            record = await self._load_task(str(record.get("task_id") or "").strip()) or record
            record["implementation"] = {
                "backend": str(imported.get("backend") or "import").strip(),
                "result": imported,
                "action": action,
                "skill_name": skill_name,
                "cwd": safe_cwd,
                "source": safe_source,
            }
            if not imported.get("ok"):
                record["status"] = "failed"
                record["error"] = str(
                    imported.get("message") or "external skill import failed"
                ).strip()
                self._append_event(
                    record,
                    name="skill_template_import_failed",
                    detail=record["error"],
                    data={"error_code": str(imported.get("error_code") or "")},
                )
                await self.tasks.save(record)
                return

            self._append_event(
                record,
                name="skill_template_imported",
                detail=f"imported {int(imported.get('file_count') or 0)} files from external repository",
                data={"source_root": str(imported.get("source_root") or "")},
            )
            self._append_progress_log(
                log_path,
                (
                    f"[{_now_iso()}] imported external skill "
                    f"source_root={str(imported.get('source_root') or '')} "
                    f"target_dir={safe_cwd} files={int(imported.get('file_count') or 0)}"
                ),
            )
            await self.tasks.save(record)

            runtime_ready = await self._prepare_external_skill_runtime(
                task_id=str(record.get("task_id") or "").strip(),
                skill_name=skill_name,
                skill_dir=safe_cwd,
                repo_url=safe_source_repo_url,
                source_repo_path=source_repo_path_text,
                backend=safe_backend,
                source=safe_source,
                timeout_sec=max(60, int(timeout_sec or 1800)),
                log_path=log_path,
            )
            record = (
                await self._load_task(str(record.get("task_id") or "").strip()) or record
            )
            record["integration"] = dict(runtime_ready or {})
            if not runtime_ready.get("ok"):
                record["status"] = "failed"
                record["error"] = str(
                    runtime_ready.get("summary")
                    or "imported external skill is not callable by worker"
                ).strip()
                self._append_event(
                    record,
                    name="skill_template_runtime_prepare_failed",
                    detail=record["error"],
                )
                await self.tasks.save(record)
                return

            self._append_event(
                record,
                name="skill_template_runtime_ready",
                detail=(
                    "worker-callable entry verified: "
                    f"{str(runtime_ready.get('probe_command') or runtime_ready.get('invoke_command') or '').strip()}"
                ).strip(),
                data={
                    "mode": str(runtime_ready.get("mode") or "").strip(),
                    "repaired": bool(runtime_ready.get("repaired")),
                },
            )
            await self.tasks.save(record)
        else:
            run_result = await self._run_coding_backend_with_progress(
                task_id=str(record.get("task_id") or "").strip(),
                stage="skill_template",
                instruction=effective_instruction,
                backend=safe_backend,
                cwd=safe_cwd,
                timeout_sec=max(60, int(timeout_sec or 1800)),
                source=safe_source,
                log_path=log_path,
            )
            record = await self._load_task(str(record.get("task_id") or "").strip()) or record
            record["implementation"] = {
                "backend": str(run_result.get("backend") or safe_backend).strip(),
                "result": run_result,
                "action": action,
                "skill_name": skill_name,
                "cwd": safe_cwd,
                "source": safe_source,
            }
            if not run_result.get("ok"):
                record["status"] = "failed"
                record["error"] = str(
                    run_result.get("summary")
                    or run_result.get("message")
                    or "skill template execution failed"
                ).strip()
                self._append_event(
                    record,
                    name="skill_template_failed",
                    detail=record["error"],
                    data={"error_code": str(run_result.get("error_code") or "")},
                )
                await self.tasks.save(record)
                return

        implementation = dict(record.get("implementation") or {})
        implementation_result = dict(implementation.get("result") or {})

        refreshed_contract = self._resolve_skill_contract(
            action="skill_modify",
            skill_name=skill_name,
            cwd=safe_cwd,
        )
        if isinstance(refreshed_contract, dict) and refreshed_contract:
            contract = refreshed_contract
            record["skill_contract"] = contract

        preflight = await self._run_skill_contract_preflight(
            contract=contract,
            cwd=safe_cwd,
        )
        record["validation"] = preflight
        if not preflight.get("ok"):
            record["status"] = "failed"
            record["error"] = str(
                preflight.get("summary") or "skill preflight failed"
            ).strip()
            self._append_event(
                record,
                name="skill_template_preflight_failed",
                detail=record["error"],
            )
            await self.tasks.save(record)
            return

        record["status"] = "done"
        record["error"] = ""
        self._append_event(
            record,
            name="skill_template_done",
            detail=self._completion_summary_text(
                {
                    "implementation": {"result": implementation_result},
                    "events": record.get("events"),
                }
            )
            or "skill template done",
        )
        await self.tasks.save(record)

    async def _queue_skill_template(
        self,
        *,
        action: str,
        instruction: str,
        cwd: str,
        backend: str,
        skill_name: str,
        source: str,
        timeout_sec: int,
        source_repo_url: str = "",
        source_repo_path: str = "",
        notify_platform: str = "",
        notify_chat_id: str = "",
        notify_user_id: str = "",
    ) -> Dict[str, Any]:
        safe_action = str(action or "").strip().lower()
        if safe_action not in {"skill_create", "skill_modify"}:
            return self._response(
                ok=False,
                summary="skill template failed",
                text="unsupported skill template action",
                error_code="invalid_args",
                terminal=True,
                task_outcome="failed",
            )

        safe_skill_name = _sanitize_skill_name(skill_name)
        safe_instruction = self._fallback_template_instruction(
            action=safe_action,
            instruction=instruction,
            skill_name=safe_skill_name,
        )
        safe_cwd = str(cwd or "").strip() or self._resolve_skill_template_cwd(
            action=safe_action,
            skill_name=safe_skill_name,
        )

        if safe_action == "skill_modify" and not safe_cwd:
            return self._response(
                ok=False,
                summary="skill template failed",
                text="skill_modify 需要有效的 skill_name（且目标技能必须存在且非 builtin）",
                error_code="invalid_args",
                terminal=True,
                task_outcome="failed",
            )
        if not safe_instruction:
            return self._response(
                ok=False,
                summary="skill template failed",
                text="instruction is required",
                error_code="invalid_args",
                terminal=True,
                task_outcome="failed",
            )
        if not safe_cwd:
            return self._response(
                ok=False,
                summary="skill template failed",
                text="cwd is required",
                error_code="invalid_args",
                terminal=True,
                task_outcome="failed",
            )
        if safe_action == "skill_create":
            os.makedirs(safe_cwd, exist_ok=True)

        contract = self._resolve_skill_contract(
            action=safe_action,
            skill_name=safe_skill_name,
            cwd=safe_cwd,
        )
        if not bool(contract.get("allow_manager_modify", True)):
            return self._response(
                ok=False,
                summary="skill template failed",
                text="contract blocks manager modification for this skill target",
                error_code="skill_contract_blocked",
                terminal=True,
                task_outcome="failed",
            )

        record = {
            "status": "queued",
            "goal": "skill template execution",
            "requirement": str(safe_instruction or "").strip(),
            "mode": "skill_template",
            "template": {
                "action": safe_action,
                "instruction": safe_instruction,
                "cwd": safe_cwd,
                "skill_name": safe_skill_name,
                "source": str(source or f"software_delivery_{safe_action}").strip(),
                "backend": str(backend or "").strip(),
                "timeout_sec": max(60, int(timeout_sec or 1800)),
                "allow_auto_publish": bool(contract.get("allow_auto_publish", False)),
                "rollout_target": str(contract.get("rollout_target") or "").strip(),
            },
            "skill_contract": contract,
            "implementation": {},
            "validation": {},
            "publish": {},
            "events": [],
            "error": "",
            "notify": self._clean_notify_payload(
                platform=notify_platform,
                chat_id=notify_chat_id,
                user_id=notify_user_id,
            ),
            "source_repo": {
                "url": str(source_repo_url or "").strip(),
                "path": str(source_repo_path or "").strip(),
            },
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        self._append_event(
            record,
            name="skill_template_queued",
            detail=f"queued {safe_action} for {safe_skill_name or 'new_skill'}",
        )
        saved = await self.tasks.create(record)
        saved_task_id = str(saved.get("task_id") or "").strip()

        await self.tasks.save(
            {
                **saved,
                "logs": {"path": self._task_log_path(saved_task_id)},
            }
        )

        self._spawn_background(
            task_id=saved_task_id,
            job_coro=self._background_skill_template(
                task_id=saved_task_id,
                action=safe_action,
                instruction=safe_instruction,
                cwd=safe_cwd,
                backend=str(backend or "").strip(),
                source=str(source or f"software_delivery_{safe_action}").strip(),
                timeout_sec=max(60, int(timeout_sec or 1800)),
                skill_name=safe_skill_name,
                source_repo_url=str(source_repo_url or "").strip(),
                source_repo_path=str(source_repo_path or "").strip(),
            ),
        )

        return self._async_dispatch_response(
            task_id=saved_task_id,
            status="queued",
            summary="skill template queued",
            backend=str(backend or "").strip(),
            mode=safe_action,
        )

    async def status(self, *, task_id: str) -> Dict[str, Any]:
        requested_task_id = str(task_id or "").strip()
        fallback_to_latest = False
        if requested_task_id:
            record = await self._load_task(requested_task_id)
            if not record:
                record = await self._load_latest_task()
                fallback_to_latest = record is not None
        else:
            record = await self._load_latest_task()
        if not record:
            return self._response(
                ok=True,
                summary="no software_delivery task found",
                status="idle",
                text="no software_delivery task found",
                data={"task": None},
            )

        resolved_task_id = str(record.get("task_id") or "").strip()
        resolved_status = str(record.get("status") or "").strip()
        summary = f"task {resolved_task_id} status: {resolved_status}"
        data: Dict[str, Any] = {"task": record}
        logs_payload = dict(record.get("logs") or {})
        log_path = str(logs_payload.get("path") or "").strip()
        if not log_path:
            implementation = dict(record.get("implementation") or {})
            implementation_result = dict(implementation.get("result") or {})
            log_path = str(implementation_result.get("log_path") or "").strip()
        if log_path:
            data["log_path"] = log_path
            data["log_tail"] = self._read_log_tail(log_path)
        if requested_task_id and fallback_to_latest:
            summary = (
                f"task {requested_task_id} not found; "
                f"returning latest task {resolved_task_id} status: {resolved_status}"
            )
            data["requested_task_id"] = requested_task_id
            data["fallback_to_latest"] = True
        return self._response(
            ok=True,
            summary=summary,
            task_id=resolved_task_id,
            status=resolved_status,
            text=summary,
            data=data,
        )

    async def resume(
        self,
        *,
        task_id: str,
        backend: str = "",
        commit_message: str = "",
        pr_title: str = "",
        pr_body: str = "",
        base_branch: str = "",
        validation_commands: List[str] | None = None,
        auto_publish: bool = True,
        auto_push: bool = True,
        auto_pr: bool = True,
        target_service: str = "",
        rollout: str = "none",
        validate_only: bool = False,
    ) -> Dict[str, Any]:
        record = await self._load_task(task_id)
        if not record:
            return self._response(
                ok=False,
                summary="resume failed",
                text="task not found",
                error_code="task_not_found",
                terminal=True,
                task_outcome="failed",
            )

        status = str(record.get("status") or "").strip().lower()
        if status in {"done", "failed"}:
            return await self.status(task_id=task_id)

        if status in {"planned", "implementing", "implemented"}:
            impl = await self.implement(task_id=task_id, backend=backend)
            if not impl.get("ok"):
                impl["terminal"] = True
                impl["task_outcome"] = "failed"
                return impl

        status_check = await self.status(task_id=task_id)
        next_status = str(status_check.get("status") or "").strip().lower()
        if next_status in {"implemented", "validating", "validated"}:
            val = await self.validate(
                task_id=task_id,
                validation_commands=validation_commands,
            )
            if not val.get("ok"):
                val["terminal"] = True
                val["task_outcome"] = "failed"
                return val

        if validate_only or not auto_publish:
            done = await self.status(task_id=task_id)
            done["terminal"] = True
            done["task_outcome"] = "done"
            return done

        pub = await self.publish(
            task_id=task_id,
            commit_message=commit_message,
            pr_title=pr_title,
            pr_body=pr_body,
            base_branch=base_branch,
            auto_push=auto_push,
            auto_pr=auto_pr,
            target_service=target_service,
            rollout=rollout,
        )
        return pub

    async def software_delivery(
        self,
        *,
        action: str = "run",
        task_id: str = "",
        requirement: str = "",
        instruction: str = "",
        issue: str = "",
        repo_path: str = "",
        repo_url: str = "",
        cwd: str = "",
        skill_name: str = "",
        source: str = "",
        template_kind: str = "",
        owner: str = "",
        repo: str = "",
        backend: str = "",
        branch_name: str = "",
        base_branch: str = "",
        commit_message: str = "",
        pr_title: str = "",
        pr_body: str = "",
        timeout_sec: Any = 1800,
        validation_commands: Any = None,
        auto_publish: Any = True,
        auto_push: Any = True,
        auto_pr: Any = True,
        target_service: str = "manager",
        rollout: str = "none",
        validate_only: Any = False,
        notify_platform: str = "",
        notify_chat_id: str = "",
        notify_user_id: str = "",
    ) -> Dict[str, Any]:
        safe_action = str(action or "run").strip().lower() or "run"
        safe_validation_commands = _clean_list(validation_commands)
        safe_validate_only = _as_bool(validate_only, default=False)
        safe_auto_publish = _as_bool(auto_publish, default=True) and not safe_validate_only
        safe_auto_push = _as_bool(auto_push, default=True)
        safe_auto_pr = _as_bool(auto_pr, default=True)
        safe_timeout_sec = max(60, _to_int(timeout_sec, 1800))
        safe_target_service = _normalize_target_service(target_service)
        safe_rollout = _normalize_rollout_mode(rollout)

        try:
            if safe_action == "read_issue":
                return await self.read_issue(issue=issue, owner=owner, repo=repo)
            if safe_action == "plan":
                return await self.plan(
                    requirement=requirement,
                    issue=issue,
                    repo_path=repo_path,
                    repo_url=repo_url,
                    owner=owner,
                    repo=repo,
                    base_branch=base_branch,
                    target_service=safe_target_service,
                    rollout=safe_rollout,
                    validate_only=safe_validate_only,
                    notify_platform=notify_platform,
                    notify_chat_id=notify_chat_id,
                    notify_user_id=notify_user_id,
                )
            if safe_action == "logs":
                status_result = await self.status(task_id=task_id)
                if not status_result.get("ok"):
                    return status_result
                payload = dict(status_result.get("data") or {})
                log_tail = str(payload.get("log_tail") or "").strip()
                if not log_tail:
                    log_tail = "no logs available"
                return self._response(
                    ok=True,
                    summary="task logs",
                    task_id=str(status_result.get("task_id") or "").strip(),
                    status=str(status_result.get("status") or "").strip(),
                    text=log_tail,
                    data=payload,
                )
            if safe_action == "implement":
                return await self._queue_existing_task(
                    task_id=str(task_id or "").strip(),
                    mode="implement",
                    backend=backend,
                    summary="implementation queued",
                    job_coro=self._background_implement(
                        task_id=str(task_id or "").strip(),
                        backend=backend,
                        branch_name=branch_name,
                        base_branch=base_branch,
                    ),
                    notify_platform=notify_platform,
                    notify_chat_id=notify_chat_id,
                    notify_user_id=notify_user_id,
                )
            if safe_action == "validate":
                return await self.validate(
                    task_id=task_id,
                    validation_commands=safe_validation_commands,
                )
            if safe_action == "publish":
                return await self.publish(
                    task_id=task_id,
                    commit_message=commit_message,
                    pr_title=pr_title,
                    pr_body=pr_body,
                    base_branch=base_branch,
                    auto_push=safe_auto_push,
                    auto_pr=safe_auto_pr,
                    target_service=safe_target_service,
                    rollout=safe_rollout,
                )
            if safe_action == "status":
                return await self.status(task_id=task_id)
            if safe_action == "resume":
                return await self._queue_existing_task(
                    task_id=str(task_id or "").strip(),
                    mode="resume",
                    backend=backend,
                    summary="resume queued",
                    job_coro=self._background_resume_pipeline(
                        task_id=str(task_id or "").strip(),
                        backend=backend,
                        commit_message=commit_message,
                        pr_title=pr_title,
                        pr_body=pr_body,
                        base_branch=base_branch,
                        validation_commands=safe_validation_commands,
                        auto_publish=safe_auto_publish,
                        auto_push=safe_auto_push,
                        auto_pr=safe_auto_pr,
                        target_service=safe_target_service,
                        rollout=safe_rollout,
                        validate_only=safe_validate_only,
                    ),
                    notify_platform=notify_platform,
                    notify_chat_id=notify_chat_id,
                    notify_user_id=notify_user_id,
                )
            if safe_action in {"skill_create", "skill_modify", "skill_template"}:
                resolved_template_action = safe_action
                if safe_action == "skill_template":
                    candidate = str(template_kind or "").strip().lower()
                    if candidate in {"skill_create", "skill_modify"}:
                        resolved_template_action = candidate
                    else:
                        resolved_template_action = "skill_modify"
                return await self._queue_skill_template(
                    action=resolved_template_action,
                    instruction=str(instruction or requirement).strip(),
                    cwd=str(cwd or repo_path).strip(),
                    backend=backend,
                    skill_name=skill_name,
                    source=source,
                    timeout_sec=safe_timeout_sec,
                    source_repo_url=repo_url,
                    source_repo_path=repo_path if str(repo_url or "").strip() else "",
                    notify_platform=notify_platform,
                    notify_chat_id=notify_chat_id,
                    notify_user_id=notify_user_id,
                )
            if str(task_id or "").strip():
                return await self._queue_existing_task(
                    task_id=str(task_id or "").strip(),
                    mode="run",
                    backend=backend,
                    summary="run queued",
                    job_coro=self._background_resume_pipeline(
                        task_id=str(task_id or "").strip(),
                        backend=backend,
                        commit_message=commit_message,
                        pr_title=pr_title,
                        pr_body=pr_body,
                        base_branch=base_branch,
                        validation_commands=safe_validation_commands,
                        auto_publish=safe_auto_publish,
                        auto_push=safe_auto_push,
                        auto_pr=safe_auto_pr,
                        target_service=safe_target_service,
                        rollout=safe_rollout,
                        validate_only=safe_validate_only,
                    ),
                    notify_platform=notify_platform,
                    notify_chat_id=notify_chat_id,
                    notify_user_id=notify_user_id,
                )

            planned = await self.plan(
                requirement=requirement,
                issue=issue,
                repo_path=repo_path,
                repo_url=repo_url,
                owner=owner,
                repo=repo,
                base_branch=base_branch,
                target_service=safe_target_service,
                rollout=safe_rollout,
                validate_only=safe_validate_only,
                notify_platform=notify_platform,
                notify_chat_id=notify_chat_id,
                notify_user_id=notify_user_id,
            )
            if not planned.get("ok"):
                planned["terminal"] = True
                planned["task_outcome"] = "failed"
                return planned

            created_task_id = str(planned.get("task_id") or "").strip()
            return await self._queue_existing_task(
                task_id=created_task_id,
                mode="run",
                backend=backend,
                summary="development run queued",
                job_coro=self._background_run_pipeline(
                    task_id=created_task_id,
                    backend=backend,
                    branch_name=branch_name,
                    base_branch=base_branch,
                    commit_message=commit_message,
                    pr_title=pr_title,
                    pr_body=pr_body,
                    validation_commands=safe_validation_commands,
                    auto_publish=safe_auto_publish,
                    auto_push=safe_auto_push,
                    auto_pr=safe_auto_pr,
                    target_service=safe_target_service,
                    rollout=safe_rollout,
                    validate_only=safe_validate_only,
                ),
                notify_platform=notify_platform,
                notify_chat_id=notify_chat_id,
                notify_user_id=notify_user_id,
            )
        except GitHubClientError as exc:
            return self._response(
                ok=False,
                summary="software_delivery failed",
                text=str(exc),
                error_code="github_error",
                terminal=True,
                task_outcome="failed",
            )
        except Exception as exc:
            return self._response(
                ok=False,
                summary="software_delivery failed",
                text=str(exc),
                error_code="software_delivery_failed",
                terminal=True,
                task_outcome="failed",
            )


manager_dev_service = ManagerDevService()
