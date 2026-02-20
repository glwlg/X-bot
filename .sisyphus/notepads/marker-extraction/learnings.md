Marker extraction findings (auto-record):
- HTML marker found in task_file_store.py: TASK_BEGIN_MARKER = <!-- XBOT_TASKS_BEGIN -->, TASK_END_MARKER = <!-- XBOT_TASKS_END -->
- YAML/fenced YAML markers: in src/repositories/base.py, extraction uses fenced YAML blocks and optional front matter for payloads
- Legacy heartbeat backup behavior exists in src/core/heartbeat_store.py via backup_legacy_path() and usage when parsing legacy heartbeat

- Prompt-level guardrails now require payload-only edits between `<!-- XBOT_STATE_BEGIN -->` and `<!-- XBOT_STATE_END -->` for scoped business-state Markdown files.
- Prompt guidance now explicitly excludes chat transcripts, memory files, `SKILL.md`, and heartbeat runtime files from marker-scoped payload editing.
