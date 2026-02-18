# Issues

- 2026-02-18: Could not run LSP diagnostics validation for changed Python files because `basedpyright-langserver` is not installed in this environment.
- 2026-02-18: Resolved by installing `basedpyright` via `uv tool install basedpyright`; diagnostics now run for changed files.
- 2026-02-18: `lsp_diagnostics` on `src/core/state_store.py` still reports pre-existing `reportImplicitRelativeImport` errors for `repositories.*` imports; left unchanged in task 9 to keep scope minimal.
- 2026-02-18: `lsp_diagnostics` on `src/core/state_store.py` continues to report the same pre-existing `reportImplicitRelativeImport` errors for `repositories.*` imports during task 10; not changed to avoid unrelated import-style refactor.
- 2026-02-18: No new migration blockers in task 11; import-related diagnostics from prior tasks were neutralized by switching changed files to dynamic module loading (`importlib.import_module`) without broader package import-style refactors.
- 2026-02-18: Command-line `rg` is unavailable in this environment (`zsh: command not found: rg`); repository-import verification was completed via the built-in content-search tool on `src/` and `tests/` instead.
- 2026-02-18: No new blockers in final migration gate; migration apply/dry-run + full pytest passed. Environment still emits a non-blocking uv warning about deprecated `tool.uv.dev-dependencies` metadata.
- 2026-02-18: LSP cleanliness check is N/A for final-note updates because this environment has no `.md` LSP configured (`No LSP server configured for extension: .md`); no Python/TS source files were modified in this task.
