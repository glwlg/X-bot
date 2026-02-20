from __future__ import annotations

import copy
import re
from typing import Any

import yaml

STATE_BEGIN_MARKER = "<!-- XBOT_STATE_BEGIN -->"
STATE_END_MARKER = "<!-- XBOT_STATE_END -->"


def _strip_yaml_fence(text: str) -> str:
    section = str(text or "").strip()
    if section.startswith("```yaml"):
        section = section[len("```yaml") :].lstrip("\r\n")
    if section.endswith("```"):
        section = section[:-3].rstrip()
    return section.strip()


def extract_state_yaml_payload(text: str) -> str:
    raw = str(text or "")
    marker_start = raw.find(STATE_BEGIN_MARKER)
    marker_end = raw.find(STATE_END_MARKER)
    has_marker = marker_start >= 0 or marker_end >= 0
    if marker_start >= 0 and marker_end > marker_start:
        section = raw[marker_start + len(STATE_BEGIN_MARKER) : marker_end]
        return _strip_yaml_fence(section)
    if has_marker:
        return ""

    fence = re.search(r"```yaml\s*(.*?)\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        return str(fence.group(1) or "")

    front = raw.strip()
    if front.startswith("---"):
        lines = front.splitlines()
        if lines and lines[0].strip() == "---":
            for idx in range(1, len(lines)):
                if lines[idx].strip() == "---":
                    return "\n".join(lines[1:idx]).strip()

    return raw


def parse_state_payload(text: str) -> tuple[bool, Any]:
    payload_text = extract_state_yaml_payload(text)
    if not str(payload_text or "").strip():
        return False, None
    try:
        loaded = yaml.safe_load(payload_text)
    except Exception:
        return False, None
    if loaded is None:
        return False, None
    return True, loaded


def normalize_payload_for_write(payload: Any) -> Any:
    data = copy.deepcopy(payload)
    if isinstance(data, dict) and "version" not in data:
        normalized: dict[str, Any] = {"version": 1}
        normalized.update(data)
        return normalized
    return data


def render_state_markdown(payload: Any, *, title: str) -> str:
    normalized = normalize_payload_for_write(payload)
    body = yaml.safe_dump(
        normalized,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).strip()
    return (
        f"# {str(title or 'Data').strip()}\n\n"
        "<!-- x-bot-state-file: edit via read/write/edit when needed -->\n"
        "<!-- payload format: fenced YAML block below -->\n\n"
        f"{STATE_BEGIN_MARKER}\n"
        "```yaml\n"
        f"{body}\n"
        "```\n"
        f"{STATE_END_MARKER}\n"
    )
