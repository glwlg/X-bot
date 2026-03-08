from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List

from manager.dev.runtime import run_shell


def sanitize_skill_name(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    if not raw:
        return ""
    safe_chars = [ch if (ch.isalnum() or ch == "_") else "_" for ch in raw]
    token = "".join(safe_chars)
    while "__" in token:
        token = token.replace("__", "_")
    token = token.strip("_")
    if not token:
        return ""
    if token[0].isdigit():
        token = f"skill_{token}"
    return token[:64]


def resolve_skill_target_dir(
    *,
    action: str,
    skill_name: str,
) -> str:
    safe_action = str(action or "").strip().lower()
    safe_skill = sanitize_skill_name(skill_name)

    try:
        from core.skill_loader import skill_loader

        skills_root = str(getattr(skill_loader, "skills_dir", "") or "").strip()
        if not skills_root:
            skills_root = os.path.abspath(os.path.join(os.getcwd(), "skills"))

        if safe_action == "skill_modify":
            if not safe_skill:
                return ""
            info = skill_loader.get_skill(safe_skill) or {}
            if str(info.get("source") or "").strip() == "builtin":
                return ""
            target = str(info.get("skill_dir") or "").strip()
            if target:
                return target
            return ""

        target_name = safe_skill or f"skill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        return os.path.abspath(os.path.join(skills_root, "learned", target_name))
    except Exception:
        fallback_root = os.path.abspath(os.path.join(os.getcwd(), "skills", "learned"))
        target_name = safe_skill or f"skill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        return os.path.abspath(os.path.join(fallback_root, target_name))


def resolve_skill_contract(
    *,
    action: str,
    skill_name: str,
    cwd: str,
) -> Dict[str, Any]:
    try:
        from core.skill_loader import skill_loader

        safe_action = str(action or "").strip().lower()
        safe_skill_name = sanitize_skill_name(skill_name)
        safe_cwd = str(cwd or "").strip()
        if safe_action == "skill_modify" and safe_skill_name:
            info = skill_loader.get_skill(safe_skill_name) or {}
            contract = dict(info.get("contract") or {})
            contract.setdefault("skill_name", safe_skill_name)
            contract.setdefault("source", str(info.get("source") or ""))
            return contract

        change_level = "builtin" if "/skills/builtin/" in safe_cwd else "learned"
        runtime_target = "manager" if change_level == "builtin" else "worker"
        rollout_target = "manager" if runtime_target == "manager" else "worker"
        return {
            "skill_name": safe_skill_name,
            "source": change_level,
            "runtime_target": runtime_target,
            "change_level": change_level,
            "allow_manager_modify": True,
            "allow_auto_publish": change_level == "learned",
            "rollout_target": rollout_target,
            "dependencies": [],
            "preflight_commands": [],
            "permissions": {},
            "allowed_roles": [],
        }
    except Exception:
        return {
            "skill_name": sanitize_skill_name(skill_name),
            "source": "learned",
            "runtime_target": "worker",
            "change_level": "learned",
            "allow_manager_modify": True,
            "allow_auto_publish": True,
            "rollout_target": "worker",
            "dependencies": [],
            "preflight_commands": [],
            "permissions": {},
            "allowed_roles": [],
        }


async def run_skill_contract_preflight(
    *,
    contract: Dict[str, Any],
    cwd: str,
) -> Dict[str, Any]:
    commands = [
        str(item).strip()
        for item in list(contract.get("preflight_commands") or [])
        if str(item).strip()
    ]
    if not commands:
        return {
            "ok": True,
            "summary": "no skill preflight commands configured",
            "commands": [],
        }

    rows: List[Dict[str, Any]] = []
    for command in commands:
        result = await run_shell(
            command,
            cwd=str(cwd or "").strip(),
            timeout_sec=600,
        )
        rows.append({"command": command, **result})
        if not result.get("ok"):
            return {
                "ok": False,
                "summary": str(result.get("summary") or "skill preflight failed"),
                "commands": rows,
            }
    return {"ok": True, "summary": "skill preflight passed", "commands": rows}
