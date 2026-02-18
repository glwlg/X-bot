# Decisions

- 2026-02-18: Added `src/core/state_file.py` as canonical business-state markdown protocol utility (markers, tolerant extraction, parse helper, and canonical rendering).
- 2026-02-18: `write_json` now creates `{name}.bak-YYYYMMDD-HHMMSS` backups before overwriting non-empty unparsable state files to reduce corruption risk.
- 2026-02-18: Added `src/core/state_migration.py` CLI (`python -m core.state_migration`) with deterministic scoped classification (`missing`/`canonical`/`legacy`/`unparsable`) and `--apply` normalization that writes backup files before rewriting legacy payloads via `render_state_markdown`.
- 2026-02-18: Added repository-focused tests in `tests/test_repositories.py` for canonical render shape, legacy parser fallbacks, backup-on-risk write behavior, and scoped domain roundtrip coverage (`settings/subscriptions/watchlist/reminders/scheduled_tasks/allowed_users/cache/counters`).
- 2026-02-18: Inlined `set_translation_mode` and `get_user_settings` into `src/core/state_store.py` and deleted `src/repositories/user_settings_repo.py`, keeping `state_store` as app-facing API while preserving canonical markdown persistence via repository base primitives.
- 2026-02-18: Inlined RSS subscription APIs (`add_subscription`, `delete_subscription`, `delete_subscription_by_id`, `get_user_subscriptions`, `get_all_subscriptions`, `update_subscription_status`) into `src/core/state_store.py`, deleted `src/repositories/subscription_repo.py`, and repointed tests/imports to `core.state_store`.
