"""Markdown → HTML conversion for platform-adaptive file delivery.

Skills generate Markdown (less tokens), but some platforms (e.g. Telegram)
cannot open .md files natively. This module provides a lightweight conversion
utility so the relay/delivery layer can auto-convert .md → .html before sending.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Minimal CSS for readable standalone HTML reports
_REPORT_CSS = """\
body {
  max-width: 860px;
  margin: 2em auto;
  padding: 0 1em;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  font-size: 15px;
  line-height: 1.7;
  color: #1a1a1a;
  background: #fdfdfd;
}
h1, h2, h3, h4 { margin-top: 1.4em; color: #111; }
h1 { font-size: 1.6em; border-bottom: 2px solid #ddd; padding-bottom: .3em; }
h2 { font-size: 1.3em; border-bottom: 1px solid #eee; padding-bottom: .2em; }
a { color: #0366d6; text-decoration: none; }
a:hover { text-decoration: underline; }
blockquote {
  border-left: 4px solid #ddd;
  margin: 1em 0;
  padding: .5em 1em;
  color: #555;
  background: #f9f9f9;
}
code {
  background: #f4f4f4;
  padding: 2px 5px;
  border-radius: 3px;
  font-size: 0.92em;
}
pre {
  background: #f4f4f4;
  padding: 1em;
  border-radius: 6px;
  overflow-x: auto;
}
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; }
th, td { border: 1px solid #ddd; padding: 8px 12px; text-align: left; }
th { background: #f6f6f6; font-weight: 600; }
tr:nth-child(even) { background: #fafafa; }
ul, ol { padding-left: 1.5em; }
hr { border: none; border-top: 1px solid #ddd; margin: 2em 0; }
"""


def md_to_html(md_content: str, title: str = "") -> str:
    """Convert Markdown text to a self-contained HTML document.

    Uses mistune for fast rendering.  Falls back to a <pre> wrapper if
    mistune is unavailable.
    """
    try:
        import mistune

        html_body = mistune.html(md_content)
    except ImportError:
        logger.warning("mistune not installed, using plain <pre> wrapper")
        from html import escape as html_escape

        html_body = f"<pre>{html_escape(md_content)}</pre>"

    safe_title = (title or "Report").replace("<", "&lt;").replace(">", "&gt;")

    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"  <title>{safe_title}</title>\n"
        f"  <style>{_REPORT_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        f"{html_body}\n"
        "</body>\n"
        "</html>\n"
    )


def adapt_md_file_for_platform(
    *,
    file_bytes: bytes,
    filename: str,
    platform: str,
) -> tuple[bytes, str]:
    """Return (content_bytes, adapted_filename) for the target platform.

    - Telegram / DingTalk → converts .md to self-contained .html
    - Discord / others    → keeps .md as-is
    """
    lower_name = str(filename or "").strip().lower()
    lower_platform = str(platform or "").strip().lower()

    if not lower_name.endswith(".md"):
        return file_bytes, filename

    # Platforms that natively preview .md
    md_native_platforms = {"discord"}
    if lower_platform in md_native_platforms:
        return file_bytes, filename

    # Convert .md → .html for Telegram, DingTalk, etc.
    try:
        md_text = file_bytes.decode("utf-8", errors="replace")
    except Exception:
        return file_bytes, filename

    # Derive a title from the first heading or filename
    title = Path(filename).stem.replace("_", " ").replace("-", " ").title()
    for line in md_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped.lstrip("# ").strip()
            break

    html_content = md_to_html(md_text, title=title)
    html_filename = str(Path(filename).with_suffix(".html"))
    return html_content.encode("utf-8"), html_filename
