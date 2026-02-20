---
name: playwright-cli
description: Automates browser interactions for web testing, form filling, screenshots, and data extraction. Use when the user needs to navigate websites, interact with web pages, fill forms, take screenshots, test web applications, or extract information from web pages.
allowed-tools: Bash(playwright-cli:*)
---

# Browser Automation with playwright-cli

This skill should bias toward short, deterministic workflows.

## Priority rules

1. If the user gives a URL and asks to read/summarize/extract content, use the snapshot flow first.
2. Prefer `open` + `snapshot` + `read snapshot file` over exploratory command chains.
3. Avoid debug-only commands unless the user explicitly asks for debugging.
4. Only use `eval` when the user explicitly requests JS evaluation.

## Preferred workflow: summarize a web page

```bash
# 1) Open target page directly
playwright-cli open https://example.com

# 2) Capture structured snapshot
playwright-cli snapshot --filename=page.yml

# 3) Read snapshot artifact from filesystem
# /app/.playwright-cli/page.yml

# 4) (Optional) navigate and take another snapshot if needed
playwright-cli click e3
playwright-cli snapshot --filename=page-after-click.yml

# 5) Close browser session
playwright-cli close
```

## Snapshot artifacts

- Snapshot files are the primary source for page extraction.
- In this project container runtime, snapshots are stored under `/app/.playwright-cli/`.
- If `--filename` is omitted, playwright-cli creates a timestamped `.yml` file.
- For summarization tasks, always save with `--filename` to make the read step deterministic.

## Minimal command set (default)

```bash
playwright-cli open <url>
playwright-cli goto <url>
playwright-cli snapshot --filename=<name>.yml
playwright-cli click <ref>
playwright-cli fill <ref> "<text>"
playwright-cli type "<text>"
playwright-cli press <key>
playwright-cli tab-list
playwright-cli tab-select <index>
playwright-cli close
```

## Use-case examples

### Example: README/web article summarization

```bash
playwright-cli open https://github.com/glwlg/X-bot/blob/master/README.md
playwright-cli snapshot --filename=readme.yml
# then read /app/.playwright-cli/readme.yml and summarize
playwright-cli close
```

### Example: Form filling

```bash
playwright-cli open https://example.com/form
playwright-cli snapshot --filename=form-before.yml
playwright-cli fill e1 "user@example.com"
playwright-cli fill e2 "password123"
playwright-cli click e3
playwright-cli snapshot --filename=form-after.yml
playwright-cli close
```

### Example: Navigate one level deeper before extraction

```bash
playwright-cli open https://example.com
playwright-cli snapshot --filename=home.yml
playwright-cli click e5
playwright-cli snapshot --filename=detail.yml
playwright-cli close
```

## Avoid by default

- Avoid `eval` for standard extraction/summarization tasks.
- Avoid debug-centric commands (console/network/tracing/video) unless explicitly requested.
- Avoid command thrashing (open -> snapshot -> goto -> eval loops without reading snapshots).
