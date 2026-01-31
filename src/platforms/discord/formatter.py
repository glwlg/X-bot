import re


def markdown_to_discord_compat(text: str) -> str:
    """
    Convert generic Markdown to Discord-friendly Markdown.

    Features:
    - Tables: Converts Markdown tables to Code Blocks (to define columns)
    """
    if not text:
        return ""

    # Detect Markdown Tables
    # Pattern: Line with | ... | followed by |---|---|
    # We will try to detect a block of table and wrap it in a code block if it is not already in one.

    lines = text.split("\n")
    in_table = False
    table_buffer = []
    output_lines = []

    # Helper to flush table buffer
    def flush_table():
        if not table_buffer:
            return
        # Naive: Just wrap in code block.
        # Better: We could try to align columns using `tabulate` but we don't want extra deps.
        # Just wrapping in code block preserves the piped structure which monospaced font makes readable.
        output_lines.append("```")
        output_lines.extend(table_buffer)
        output_lines.append("```")
        table_buffer.clear()

    for i, line in enumerate(lines):
        striped = line.strip()
        is_table_row = striped.startswith("|") and striped.endswith("|")

        if is_table_row:
            # Check if this is likely a table part
            if not in_table:
                # Look ahead to see if next line is splitter |---|
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if next_line.startswith("|") and "-" in next_line:
                        in_table = True

            if in_table:
                table_buffer.append(line)
            else:
                output_lines.append(line)
        else:
            if in_table:
                in_table = False
                flush_table()
            output_lines.append(line)

    # Flush at end
    if in_table:
        flush_table()

    return "\n".join(output_lines)
