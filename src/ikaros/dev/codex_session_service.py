from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from ikaros.dev.runtime import run_coding_backend, run_shell
from ikaros.dev.session_paths import (
    codex_session_log_path,
    codex_session_path,
    new_codex_session_id,
)
from ikaros.dev.workspace_session_service import workspace_session_service


_USER_INPUT_ANCHORS = (
    "请你选择",
    "请选择",
    "请确认",
    "需要先确认",
    "确认再继续",
    "确认后继续",
    "先停下并确认",
    "请回复",
    "请决定",
    "需要你决定",
    "i need you to choose",
    "please choose",
    "please confirm",
    "which option",
    "before i continue",
    "before continuing",
    "should i keep",
)
_NUMBERED_OPTION_RE = re.compile(r"(?m)^\s*[1-9][\).、．]\s+.+$")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _short(text: str, limit: int = 240) -> str:
    payload = str(text or "").strip()
    if len(payload) <= limit:
        return payload
    return payload[:limit].rstrip() + "..."


class CodexSessionService:
    @staticmethod
    def _response(
        *,
        ok: bool,
        summary: str,
        text: str = "",
        data: Dict[str, Any] | None = None,
        error_code: str = "",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "ok": bool(ok),
            "summary": str(summary or "").strip(),
            "text": str(text or summary or "").strip(),
            "data": dict(data or {}),
            "terminal": False,
        }
        if not ok:
            payload["error_code"] = str(error_code or "codex_session_failed").strip()
            payload["message"] = str(text or summary or "codex session failed").strip()
            payload["failure_mode"] = "fatal"
        return payload

    async def _load_state(self, session_id: str) -> Dict[str, Any] | None:
        path = codex_session_path(session_id)
        if not path.exists():
            return None
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return dict(loaded) if isinstance(loaded, dict) else None

    async def _save_state(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        record = dict(payload or {})
        session_id = str(record.get("session_id") or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        record["updated_at"] = _now_iso()
        path = codex_session_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return record

    @staticmethod
    def _extract_question(result: Dict[str, Any]) -> str:
        if not isinstance(result, dict) or not bool(result.get("ok")):
            return ""
        parts = [
            str(result.get("stdout") or "").strip(),
            str(result.get("summary") or "").strip(),
        ]
        text = "\n\n".join([part for part in parts if part]).strip()
        if not text:
            return ""
        lowered = text.lower()
        has_anchor = any(token in lowered for token in _USER_INPUT_ANCHORS)
        has_options = bool(_NUMBERED_OPTION_RE.search(text))
        has_question_mark = "?" in text or "？" in text
        if has_anchor and (has_options or has_question_mark):
            return text[:4000]
        return ""

    async def _workspace_context(self, repo_root: str) -> str:
        status = await run_shell("git status --short --branch", cwd=repo_root)
        branch = await run_shell("git rev-parse --abbrev-ref HEAD", cwd=repo_root)
        lines = []
        branch_name = str(branch.get("stdout") or "").strip()
        if branch_name:
            lines.append(f"Current branch: {branch_name}")
        status_text = str(status.get("stdout") or status.get("summary") or "").strip()
        if status_text:
            lines.extend(["Current git status:", status_text[:2000]])
        return "\n".join(lines).strip()

    async def _resolve_workspace(
        self, *, workspace_id: str = "", cwd: str = ""
    ) -> Dict[str, Any]:
        if str(workspace_id or "").strip():
            inspected = await workspace_session_service.inspect(
                workspace_id=workspace_id
            )
            if not inspected.get("ok"):
                return {
                    "ok": False,
                    "error_code": str(
                        inspected.get("error_code") or "workspace_not_found"
                    ),
                    "message": str(
                        inspected.get("message")
                        or inspected.get("text")
                        or "workspace not found"
                    ),
                }
            return {"ok": True, **dict(inspected.get("data") or {})}
        safe_cwd = str(cwd or "").strip()
        if not safe_cwd:
            return {
                "ok": False,
                "error_code": "invalid_args",
                "message": "workspace_id or cwd is required",
            }
        target = Path(safe_cwd).expanduser().resolve()
        if not target.exists() or not target.is_dir():
            return {
                "ok": False,
                "error_code": "workspace_not_found",
                "message": f"workspace does not exist: {target}",
            }
        return {
            "ok": True,
            "workspace_id": "",
            "repo_root": str(target),
            "branch_name": "",
            "owner": "",
            "repo": "",
        }

    def _continuation_instruction(
        self,
        *,
        base_instruction: str,
        pending_question: str,
        user_reply: str,
        workspace_context: str,
    ) -> str:
        sections = [str(base_instruction or "").strip()]
        sections.append(
            "Continuation context:\n"
            f"- Previous blocking question: {pending_question[:1600]}\n"
            f"- User reply / decision: {user_reply[:1600]}"
        )
        if workspace_context:
            sections.append(workspace_context)
        sections.append(
            "Continue from the current working tree. Reuse existing changes, do not reset user work, and only ask another question if you remain genuinely blocked after checking the repository state."
        )
        return "\n\n".join([section for section in sections if section]).strip()

    async def _run_round(
        self,
        *,
        session_id: str,
        workspace: Dict[str, Any],
        instruction: str,
        backend: str,
        timeout_sec: int,
        source: str,
        user_reply: str = "",
    ) -> Dict[str, Any]:
        repo_root = str(workspace.get("repo_root") or "").strip()
        log_path = str(codex_session_log_path(session_id))
        result = await run_coding_backend(
            instruction=instruction,
            backend=backend,
            cwd=repo_root,
            timeout_sec=max(60, int(timeout_sec or 1800)),
            source=source,
            log_path=log_path,
        )
        question = self._extract_question(result)
        status = "done"
        summary = str(
            result.get("summary") or result.get("message") or "coding backend completed"
        ).strip()
        if not result.get("ok"):
            status = "failed"
        elif question:
            status = "waiting_user"
            summary = _short(question, 300)

        session = await self._load_state(session_id)
        if session is None:
            session = {
                "session_id": session_id,
                "workspace_id": str(workspace.get("workspace_id") or "").strip(),
                "backend": str(backend or "codex").strip() or "codex",
                "instruction": instruction,
                "created_at": _now_iso(),
                "history": [],
            }
        history = list(session.get("history") or [])
        history.append(
            {
                "at": _now_iso(),
                "instruction": instruction,
                "user_reply": str(user_reply or "").strip(),
                "status": status,
                "summary": summary,
                "question": question,
            }
        )
        session.update(
            {
                "workspace_id": str(workspace.get("workspace_id") or "").strip(),
                "repo_root": repo_root,
                "backend": str(result.get("backend") or backend or "codex").strip()
                or "codex",
                "instruction": str(session.get("instruction") or instruction).strip(),
                "last_instruction": instruction,
                "last_user_reply": str(user_reply or "").strip(),
                "status": status,
                "summary": summary,
                "pending_question": question,
                "result": result,
                "log_path": log_path,
                "history": history[-20:],
            }
        )
        await self._save_state(session)
        return session

    async def start(
        self,
        *,
        workspace_id: str = "",
        cwd: str = "",
        instruction: str,
        backend: str = "codex",
        timeout_sec: int = 2400,
        source: str = "",
        skill_name: str = "",
    ) -> Dict[str, Any]:
        safe_instruction = str(instruction or "").strip()
        if not safe_instruction:
            return self._response(
                ok=False,
                summary="codex_session start failed",
                text="instruction is required",
                error_code="invalid_args",
            )
        workspace = await self._resolve_workspace(workspace_id=workspace_id, cwd=cwd)
        if not workspace.get("ok"):
            return self._response(
                ok=False,
                summary="codex_session start failed",
                text=str(workspace.get("message") or "workspace not found"),
                error_code=str(workspace.get("error_code") or "workspace_not_found"),
            )
        session_id = new_codex_session_id()
        initial = {
            "session_id": session_id,
            "workspace_id": str(workspace.get("workspace_id") or "").strip(),
            "repo_root": str(workspace.get("repo_root") or "").strip(),
            "backend": str(backend or "codex").strip() or "codex",
            "source": str(source or "").strip(),
            "skill_name": str(skill_name or "").strip(),
            "instruction": safe_instruction,
            "status": "running",
            "summary": "coding backend started",
            "pending_question": "",
            "result": {},
            "history": [],
            "log_path": str(codex_session_log_path(session_id)),
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        await self._save_state(initial)
        session = await self._run_round(
            session_id=session_id,
            workspace=workspace,
            instruction=safe_instruction,
            backend=str(backend or "codex").strip() or "codex",
            timeout_sec=timeout_sec,
            source="codex_session_start",
        )
        return await self.status(session_id=session_id)

    async def continue_session(
        self,
        *,
        session_id: str,
        user_reply: str,
        timeout_sec: int = 2400,
    ) -> Dict[str, Any]:
        session = await self._load_state(session_id)
        if not session:
            return self._response(
                ok=False,
                summary="codex_session continue failed",
                text="session not found",
                error_code="session_not_found",
            )
        if str(session.get("status") or "").strip().lower() != "waiting_user":
            return self._response(
                ok=False,
                summary="codex_session continue failed",
                text="session is not waiting for user input",
                error_code="invalid_state",
            )
        safe_reply = str(user_reply or "").strip()
        if not safe_reply:
            return self._response(
                ok=False,
                summary="codex_session continue failed",
                text="user_reply is required",
                error_code="invalid_args",
            )
        workspace = await self._resolve_workspace(
            workspace_id=str(session.get("workspace_id") or "").strip(),
            cwd=str(session.get("repo_root") or "").strip(),
        )
        if not workspace.get("ok"):
            return self._response(
                ok=False,
                summary="codex_session continue failed",
                text=str(workspace.get("message") or "workspace not found"),
                error_code=str(workspace.get("error_code") or "workspace_not_found"),
            )
        workspace_context = await self._workspace_context(
            str(workspace.get("repo_root") or "").strip()
        )
        instruction = self._continuation_instruction(
            base_instruction=str(session.get("instruction") or "").strip(),
            pending_question=str(session.get("pending_question") or "").strip(),
            user_reply=safe_reply,
            workspace_context=workspace_context,
        )
        await self._save_state(
            {**session, "status": "running", "last_user_reply": safe_reply}
        )
        await self._run_round(
            session_id=session_id,
            workspace=workspace,
            instruction=instruction,
            backend=str(session.get("backend") or "codex").strip() or "codex",
            timeout_sec=timeout_sec,
            source="codex_session_continue",
            user_reply=safe_reply,
        )
        return await self.status(session_id=session_id)

    async def status(self, *, session_id: str) -> Dict[str, Any]:
        session = await self._load_state(session_id)
        if not session:
            return self._response(
                ok=False,
                summary="codex_session status failed",
                text="session not found",
                error_code="session_not_found",
            )
        status = str(session.get("status") or "unknown").strip().lower() or "unknown"
        summary = str(session.get("summary") or status).strip() or status
        question = str(session.get("pending_question") or "").strip()
        if status == "waiting_user" and question:
            text = question
        elif status == "done":
            text = str(
                dict(session.get("result") or {}).get("summary")
                or dict(session.get("result") or {}).get("stdout")
                or summary
            ).strip()
        elif status == "failed":
            text = str(
                dict(session.get("result") or {}).get("message")
                or dict(session.get("result") or {}).get("summary")
                or summary
            ).strip()
        else:
            text = summary
        return self._response(
            ok=status != "failed",
            summary=summary,
            text=text,
            data={
                "session_id": str(session.get("session_id") or "").strip(),
                "workspace_id": str(session.get("workspace_id") or "").strip(),
                "repo_root": str(session.get("repo_root") or "").strip(),
                "status": status,
                "question": question,
                "log_path": str(session.get("log_path") or "").strip(),
                "history": list(session.get("history") or []),
                "result": dict(session.get("result") or {}),
            },
            error_code="command_failed" if status == "failed" else "",
        )

    async def cancel(self, *, session_id: str) -> Dict[str, Any]:
        session = await self._load_state(session_id)
        if not session:
            return self._response(
                ok=False,
                summary="codex_session cancel failed",
                text="session not found",
                error_code="session_not_found",
            )
        session["status"] = "cancelled"
        session["summary"] = "session cancelled"
        session["pending_question"] = ""
        await self._save_state(session)
        return self._response(
            ok=True,
            summary="session cancelled",
            text=f"Session {session_id} cancelled",
            data={
                "session_id": session_id,
                "workspace_id": str(session.get("workspace_id") or "").strip(),
            },
        )

    async def handle(
        self,
        *,
        action: str = "status",
        session_id: str = "",
        workspace_id: str = "",
        cwd: str = "",
        instruction: str = "",
        user_reply: str = "",
        backend: str = "codex",
        timeout_sec: int = 2400,
        source: str = "",
        skill_name: str = "",
    ) -> Dict[str, Any]:
        safe_action = str(action or "status").strip().lower() or "status"
        if safe_action == "start":
            return await self.start(
                workspace_id=workspace_id,
                cwd=cwd,
                instruction=instruction,
                backend=backend,
                timeout_sec=timeout_sec,
                source=source,
                skill_name=skill_name,
            )
        if safe_action == "continue":
            return await self.continue_session(
                session_id=session_id,
                user_reply=user_reply,
                timeout_sec=timeout_sec,
            )
        if safe_action == "status":
            return await self.status(session_id=session_id)
        if safe_action == "cancel":
            return await self.cancel(session_id=session_id)
        return self._response(
            ok=False,
            summary="codex_session failed",
            text=f"unsupported codex_session action: {safe_action}",
            error_code="unsupported_action",
        )


codex_session_service = CodexSessionService()
