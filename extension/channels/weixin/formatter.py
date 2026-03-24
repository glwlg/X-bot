from __future__ import annotations

import re


_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def markdown_to_weixin_text(text: str) -> str:
    """Best-effort markdown downgrade for Weixin plain text delivery."""
    rendered = str(text or "").replace("\r\n", "\n")
    if not rendered:
        return ""

    rendered = _LINK_RE.sub(
        lambda match: f"{match.group(1)}: {match.group(2)}", rendered
    )
    rendered = re.sub(r"^\s{0,3}#{1,6}\s*", "", rendered, flags=re.MULTILINE)
    rendered = rendered.replace("```", "")

    for pattern in (
        r"\*\*(.*?)\*\*",
        r"__(.*?)__",
        r"~~(.*?)~~",
        r"`([^`]+)`",
    ):
        rendered = re.sub(pattern, r"\1", rendered, flags=re.DOTALL)

    rendered = re.sub(r"\n{3,}", "\n\n", rendered)
    return rendered.strip()
