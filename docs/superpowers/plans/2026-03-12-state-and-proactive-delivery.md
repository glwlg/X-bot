# State And Proactive Delivery Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore real per-user state ownership and make proactive pushes (RSS, reminders, cron, heartbeat) use durable delivery semantics instead of best-effort sending.

**Architecture:** First lock down canonical user-scoped state in `state_paths`/`state_store`, then add a dedicated proactive-delivery layer that creates canonical delivery jobs and only finalizes producer state after confirmed delivery or explicit suppression. Keep compatibility reads during migration, but stop writing new user-owned data into the shared `"user"` bucket.

**Tech Stack:** Python 3.14, asyncio, APScheduler, FastAPI-adjacent state services, file-backed state under `data/`, pytest/pytest-asyncio

---

Spec: `docs/superpowers/specs/2026-03-12-delivery-reliability-and-state-boundaries-design.md`

Scope: This plan covers spec Workstreams A-C plus the Phase-2 minimum validated delivery-target contract. Workstreams D-F are implemented by the companion plans `2026-03-12-web-runtime-consistency.md` and `2026-03-12-metadata-and-relay-hardening.md`.

Prerequisites/Exit Gates:

- do not start the web/API plan until this plan lands the canonical user-scope and proactive-delivery seam;
- remove heuristic target fallback in this plan only after the minimum validated delivery-target contract is in place;
- do not remove legacy shared-root fallback reads until migration reports show zero unreviewed ambiguous records for the affected domain.

Dependent Verification Before The Overall Program Is Complete:

- `tests/api/test_rss_watchlist_platform_endpoints.py`
- `tests/api/test_scheduler_endpoints.py`
- `tests/api/test_monitor_endpoints.py`

## File Map

- Modify: `src/core/state_paths.py` - canonical user/shared/system path helpers and user enumeration
- Modify: `src/core/state_store.py` - scheduler/reminder/watchlist/RSS reads and writes use true per-user scope
- Create: `src/core/state_migration.py` - idempotent migration helpers and migration reporting for shared-root legacy data
- Create: `src/shared/contracts/proactive_delivery_target.py` - Phase-2 validated delivery-target contract for proactive producers
- Create: `src/core/proactive_delivery.py` - canonical proactive delivery job creation, target resolution, retry result handling
- Modify: `src/core/scheduler.py` - producers call proactive delivery layer and only finalize on confirmed outcome
- Modify: `src/core/background_delivery.py` - delivery executor helper returns structured outcomes usable by proactive delivery
- Modify: `src/core/heartbeat_worker.py` - heartbeat proactive notifications use the same canonical proactive-delivery path
- Modify: `src/manager/relay/delivery_store.py` - support proactive producer metadata/idempotency without introducing a second ledger
- Test: `tests/core/test_state_scope.py` - new regression tests for user isolation and legacy compatibility
- Test: `tests/shared/test_proactive_delivery_target.py` - Phase-2 delivery-target metadata validation and precedence
- Test: `tests/core/test_scheduler_rss_links.py` - target resolution and cron/RSS lifecycle regressions
- Test: `tests/core/test_background_delivery.py` - structured delivery outcome and executor behavior
- Test: `tests/core/test_heartbeat_worker.py` - heartbeat proactive notifications use canonical delivery semantics
- Test: `tests/core/test_delivery_store.py` - proactive job idempotency and health visibility
- Test: `tests/core/test_telegram_adapter_send_message.py` - adapter compatibility after structured background delivery changes
- Test: `tests/core/test_orchestrator_delivery_closure.py` - delivery-state compatibility while Phase-2 proactive changes land

## Chunk 1: User-Scoped State And Proactive Delivery

### Task 1: Lock down user-scoped path behavior with compatibility reads in the same change

**Files:**
- Test: `tests/core/test_state_scope.py`
- Modify: `src/core/state_paths.py`
- Modify: `src/core/state_store.py`
- Create: `src/core/state_migration.py`

- [ ] **Step 1: Write the failing path-isolation tests**

```python
@pytest.mark.asyncio
async def test_user_path_keeps_users_separate(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    alpha = user_path("1001", "automation", "scheduled_tasks.md")
    beta = user_path("2002", "automation", "scheduled_tasks.md")

    assert alpha != beta
    assert "1001" in str(alpha)
    assert "2002" in str(beta)


@pytest.mark.asyncio
async def test_state_store_does_not_merge_two_users_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    await add_scheduled_task("0 8 * * *", "alpha", user_id="1001")
    await add_scheduled_task("0 9 * * *", "beta", user_id="2002")

    alpha = await get_all_active_tasks("1001")
    beta = await get_all_active_tasks("2002")

    assert [row["instruction"] for row in alpha] == ["alpha"]
    assert [row["instruction"] for row in beta] == ["beta"]


@pytest.mark.asyncio
async def test_state_store_keeps_rss_reminders_watchlist_and_heartbeat_separate(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ...
    assert await list_subscriptions("1001") != await list_subscriptions("2002")
    assert await get_pending_reminders("1001") != await get_pending_reminders("2002")
    assert await get_user_watchlist("1001") != await get_user_watchlist("2002")
    assert (await heartbeat_store.get_state("1001")) != (await heartbeat_store.get_state("2002"))
```

- [ ] **Step 2: Run the state-scope tests to verify they fail**

Run: `uv run pytest tests/core/test_state_scope.py -v`
Expected: FAIL because current `user_path()` and `all_user_ids()` collapse user-owned state into the shared root.

- [ ] **Step 3: Implement canonical path helpers and compatibility read helpers together**

```python
def user_path(user_id: int | str, *parts: str) -> Path:
    safe_user = _safe_part(user_id, fallback="0")
    path = (users_root() / safe_user).resolve()
    path.mkdir(parents=True, exist_ok=True)
    for part in parts:
        path = (path / str(part)).resolve()
    return path


def shared_user_path(*parts: str) -> Path:
    path = (_runtime_data_dir() / _PRIVATE_DIR_NAME).resolve()
    path.mkdir(parents=True, exist_ok=True)
    for part in parts:
        path = (path / str(part)).resolve()
    return path
```

- [ ] **Step 4: Route scheduler/reminder/watchlist state reads and writes through the new canonical user helpers**

```python
def _scheduled_tasks_path(user_id: int | str):
    return user_path(user_id, "automation", "scheduled_tasks.md")


async def get_all_active_tasks(user_id: int | str | None = None) -> list[dict[str, Any]]:
    target_users = [str(user_id)] if user_id is not None else all_user_ids()
    merged: list[dict[str, Any]] = []
    for uid in target_users:
        merged.extend([row for row in await _read_user_scheduled_tasks(uid) if row["is_active"]])
    return merged


async def _read_legacy_shared_tasks_for_user(user_id: str) -> list[dict[str, Any]]:
    ...
```

- [ ] **Step 4: Keep compatibility reads active before switching all writers**

```python
async def _read_user_scheduled_tasks(user_id: int | str) -> list[dict[str, Any]]:
    current_rows = await _read_json_tasks(_scheduled_tasks_path(user_id))
    if current_rows:
        return [_normalize_scheduled_task(item, user_id=user_id) for item in current_rows]
    legacy_rows = await _read_legacy_shared_tasks_for_user(str(user_id))
    return [_normalize_scheduled_task(item, user_id=user_id) for item in legacy_rows]
```

- [ ] **Step 5: Re-run the state-scope tests and adjacent regressions**

Run: `uv run pytest tests/core/test_state_scope.py tests/core/test_scheduler_rss_links.py -v`
Expected: PASS for the new isolation tests; any old tests that encoded shared-user collapse should now fail and be updated in later tasks.

- [ ] **Step 6: Commit the user-scope baseline**

```bash
git add tests/core/test_state_scope.py src/core/state_paths.py src/core/state_store.py
git commit -m "fix: restore user-scoped automation state"
```

### Task 2: Add idempotent migration helpers and persisted migration reports

**Files:**
- Create: `src/core/state_migration.py`
- Modify: `src/core/state_store.py`
- Test: `tests/core/test_state_scope.py`

- [ ] **Step 1: Add failing migration tests**

```python
@pytest.mark.asyncio
async def test_migrate_shared_scheduler_rows_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    legacy_path = shared_user_path("automation", "scheduled_tasks.md")
    await write_json(legacy_path, [{"id": 1, "user_id": "1001", "instruction": "alpha", "crontab": "0 8 * * *"}])

    report_one = await migrate_legacy_user_state()
    report_two = await migrate_legacy_user_state()

    assert report_one["migrated"]["scheduled_tasks"] == 1
    assert report_two["migrated"]["scheduled_tasks"] == 0


@pytest.mark.asyncio
async def test_migrate_shared_rss_and_heartbeat_write_persisted_report(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    report = await migrate_legacy_user_state()

    report_path = system_path("migrations", "user_state_report.json")
    assert report_path.exists()
    assert "rss" in report["migrated"]
    assert "heartbeat" in report["migrated"]


@pytest.mark.asyncio
async def test_legacy_shared_reads_work_for_rss_reminders_watchlist_and_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ...
    assert await list_subscriptions("1001")
    assert await get_pending_reminders("1001")
    assert await get_user_watchlist("1001")
    assert await heartbeat_store.get_state("1001")
```

- [ ] **Step 2: Run the migration tests to verify they fail**

Run: `uv run pytest tests/core/test_state_scope.py -k migrate -v`
Expected: FAIL because no migration helper exists yet.

- [ ] **Step 3: Implement an idempotent migration/report helper**

```python
async def migrate_legacy_user_state() -> dict[str, Any]:
    report = {"migrated": {}, "skipped": {}, "ambiguous": []}
    report["migrated"]["scheduled_tasks"] = await _migrate_scheduled_tasks(report)
    report["migrated"]["rss"] = await _migrate_rss_state(report)
    report["migrated"]["reminders"] = await _migrate_reminders(report)
    report["migrated"]["watchlist"] = await _migrate_watchlists(report)
    report["migrated"]["heartbeat"] = await _migrate_heartbeat_state(report)
    await write_json(system_path("migrations", "user_state_report.json"), report)
    return report
```

- [ ] **Step 4: Wire compatibility reads so legacy shared data remains readable until migrated**

```python
async def _read_user_scheduled_tasks(user_id: int | str) -> list[dict[str, Any]]:
    rows = await _read_json_tasks(_scheduled_tasks_path(user_id))
    if rows:
        return [_normalize_scheduled_task(item, user_id=user_id) for item in rows]
    legacy_rows = await _read_legacy_shared_tasks_for_user(str(user_id))
    return [_normalize_scheduled_task(item, user_id=user_id) for item in legacy_rows]


async def _read_user_reminders(user_id: int | str) -> list[dict[str, Any]]:
    ...


async def _read_user_subscriptions(user_id: int | str) -> list[dict[str, Any]]:
    ...


async def _read_watchlist(user_id: int | str) -> list[dict[str, Any]]:
    ...


async def _read_heartbeat_state(user_id: int | str) -> dict[str, Any]:
    ...
```

- [ ] **Step 5: Re-run migration and state-scope tests**

Run: `uv run pytest tests/core/test_state_scope.py -v`
Expected: PASS, with migration tests proving idempotency and compatibility reads.

- [ ] **Step 6: Commit the migration layer**

```bash
git add src/core/state_migration.py src/core/state_store.py tests/core/test_state_scope.py
git commit -m "feat: add legacy user-state migration helpers"
```

### Task 3: Add the Phase-2 minimum validated delivery-target contract before removing fallback

**Files:**
- Create: `src/shared/contracts/proactive_delivery_target.py`
- Create: `tests/shared/test_proactive_delivery_target.py`
- Modify: `src/core/proactive_delivery.py`
- Modify: `src/core/scheduler.py`

- [ ] **Step 1: Write failing contract and precedence tests**

```python
def test_proactive_delivery_target_normalizes_worker_runtime_alias():
    target = ProactiveDeliveryTarget.from_legacy({"platform": "worker_runtime", "chat_id": "257675041", "user_id": "1001"})
    assert target.platform == "telegram"


def test_proactive_delivery_target_rejects_cross_user_mismatch():
    with pytest.raises(ValueError, match="cross-user"):
        ProactiveDeliveryTarget.from_legacy({"platform": "telegram", "chat_id": "2002", "user_id": "1001"})
```

- [ ] **Step 2: Run the contract tests to verify they fail**

Run: `uv run pytest tests/shared/test_proactive_delivery_target.py -v`
Expected: FAIL because the Phase-2 validated target contract does not exist yet.

- [ ] **Step 3: Implement the minimal validated target contract and boundary normalization**

```python
@dataclass(frozen=True)
class ProactiveDeliveryTarget:
    version: str
    owner_user_id: str
    platform: str
    chat_id: str

    @classmethod
    def from_legacy(cls, raw: Mapping[str, Any]) -> "ProactiveDeliveryTarget":
        ...
```

- [ ] **Step 4: Make proactive target resolution honor spec precedence**

```python
async def resolve_proactive_target(*, owner_user_id: str, platform: str, metadata: Mapping[str, Any] | None = None) -> tuple[str, str]:
    explicit = ProactiveDeliveryTarget.maybe_from_metadata(metadata)
    if explicit is not None:
        return explicit.platform, explicit.chat_id
    resource_binding = await load_resource_delivery_binding(owner_user_id=owner_user_id, platform=platform)
    if resource_binding is not None:
        return resource_binding.platform, resource_binding.chat_id
    default_binding = await load_user_default_binding(owner_user_id=owner_user_id, platform=platform)
    if default_binding is not None:
        return default_binding.platform, default_binding.chat_id
    return "", ""
```

- [ ] **Step 5: Re-run the contract tests plus scheduler target-resolution regressions**

Run: `uv run pytest tests/shared/test_proactive_delivery_target.py tests/core/test_scheduler_rss_links.py -v`
Expected: PASS, with no platform-wide recent-task fallback remaining.

- [ ] **Step 6: Commit the Phase-2 target contract**

```bash
git add src/shared/contracts/proactive_delivery_target.py src/core/proactive_delivery.py src/core/scheduler.py tests/shared/test_proactive_delivery_target.py tests/core/test_scheduler_rss_links.py
git commit -m "feat: add validated proactive delivery target contract"
```

### Task 4: Write failing tests for proactive delivery lifecycle and strict target ownership

**Files:**
- Modify: `tests/core/test_scheduler_rss_links.py`
- Modify: `tests/core/test_background_delivery.py`
- Modify: `tests/core/test_heartbeat_worker.py`
- Test: `tests/core/test_delivery_store.py`

- [ ] **Step 1: Add failing tests that remove recent-task fallback and protect lifecycle semantics**

```python
@pytest.mark.asyncio
async def test_resolve_proactive_delivery_target_never_uses_recent_task_fallback(monkeypatch):
    monkeypatch.setattr(scheduler_module, "_recent_delivery_target_for_platform", lambda *_: (_ for _ in ()).throw(AssertionError("must not be used")))
    monkeypatch.setattr(heartbeat_store, "get_delivery_target", AsyncMock(return_value={"platform": "", "chat_id": ""}))

    target = await scheduler_module._resolve_proactive_delivery_target("user-1", "telegram")

    assert target == ("", "")


@pytest.mark.asyncio
async def test_rss_updates_not_marked_read_when_delivery_fails(monkeypatch):
    monkeypatch.setattr(scheduler_module, "deliver_proactive_text", AsyncMock(return_value={"status": "retrying", "delivered": False}))
    mark_read = AsyncMock()
    monkeypatch.setattr(scheduler_module, "_mark_feed_updates_as_read", mark_read)

    await scheduler_module._send_feed_updates({("telegram", "1001"): [sample_update()]})

    mark_read.assert_not_awaited()
```

- [ ] **Step 2: Run only the new lifecycle tests to verify they fail**

Run: `uv run pytest tests/core/test_scheduler_rss_links.py tests/core/test_background_delivery.py tests/core/test_heartbeat_worker.py tests/core/test_delivery_store.py -k "fallback or marked_read or reminder or heartbeat" -v`
Expected: FAIL because producers still call the old best-effort path.

- [ ] **Step 3: Add a minimal structured delivery-result shape in tests first**

```python
delivery_result = {
    "job_id": "proactive:rss:sub-4:item-9",
    "status": "delivered",
    "delivered": True,
    "target_platform": "telegram",
    "target_chat_id": "257675041",
    "reason": "",
}
```

- [ ] **Step 4: Update tests to expect structured outcomes instead of boolean side effects**

Run: `uv run pytest tests/core/test_background_delivery.py -v`
Expected: FAIL until the executor and producers return structured results.

- [ ] **Step 5: Commit the failing lifecycle coverage**

```bash
git add tests/core/test_scheduler_rss_links.py tests/core/test_background_delivery.py tests/core/test_heartbeat_worker.py tests/core/test_delivery_store.py
git commit -m "test: cover proactive delivery lifecycle regressions"
```

### Task 5: Implement canonical proactive delivery service and refactor producers

**Files:**
- Create: `src/core/proactive_delivery.py`
- Modify: `src/core/background_delivery.py`
- Modify: `src/core/scheduler.py`
- Modify: `src/core/heartbeat_worker.py`
- Modify: `src/manager/relay/delivery_store.py`

- [ ] **Step 1: Add a minimal proactive-delivery service that owns job creation and result interpretation**

```python
async def deliver_proactive_text(
    *,
    producer_type: str,
    producer_id: str,
    owner_user_id: str,
    platform: str,
    metadata: Mapping[str, Any] | None,
    resource_scope: str = "",
    text: str,
    filename_prefix: str = "background",
) -> dict[str, Any]:
    target_platform, target_chat_id = await resolve_proactive_target(
        owner_user_id=owner_user_id,
        platform=platform,
        metadata=metadata,
        resource_scope=resource_scope,
    )
    if not target_platform or not target_chat_id:
        return await ensure_missing_target_retry(...)
    job = await delivery_store.ensure_proactive_job(...)
    return await execute_proactive_job(job=job, text=text, filename_prefix=filename_prefix)
```

- [ ] **Step 2: Change the raw background executor to return structured results**

```python
async def push_background_text(...):
    if not safe_platform or not safe_chat_id:
        return {"delivered": False, "status": "retrying", "reason": "missing_delivery_target"}
    ...
    if not sent:
        return {"delivered": False, "status": "retrying", "reason": "adapter_send_failed"}
    return {"delivered": True, "status": "delivered", "reason": ""}
```

- [ ] **Step 3: Route all proactive target resolution through the validated Phase-2 contract**

```python
async def _resolve_proactive_delivery_target(user_id: int | str, platform: str) -> tuple[str, str]:
    return await resolve_proactive_target(
        owner_user_id=str(user_id or "").strip(),
        platform=platform,
        metadata=None,
        resource_scope="",
    )
```

- [ ] **Step 4: Refactor RSS, reminders, cron, and heartbeat to finalize state only after confirmed delivery**

```python
result = await deliver_proactive_text(...)
if result.get("delivered"):
    await _mark_feed_updates_as_read(delivered_updates)
elif result.get("status") == "suppressed":
    await _mark_feed_updates_as_read(delivered_updates)
else:
    logger.warning("RSS delivery deferred job=%s reason=%s", result.get("job_id"), result.get("reason"))
```

- [ ] **Step 5: Re-run focused tests for producers and delivery storage**

Run: `uv run pytest tests/core/test_state_scope.py tests/shared/test_proactive_delivery_target.py tests/core/test_scheduler_rss_links.py tests/core/test_background_delivery.py tests/core/test_heartbeat_worker.py tests/core/test_delivery_store.py tests/core/test_telegram_adapter_send_message.py -v`
Expected: PASS, with no more tests asserting platform-wide recent-task fallback.

- [ ] **Step 6: Run the broader related suite**

Run: `uv run pytest tests/core/test_worker_result_relay.py tests/core/test_worker_result_relay_heartbeat.py tests/core/test_orchestrator_delivery_closure.py tests/core/test_main_bootstrap.py -v`
Expected: PASS, proving relay and startup behavior still work while proactive delivery now shares the canonical ledger.

- [ ] **Step 7: Commit the proactive delivery refactor**

```bash
git add src/core/proactive_delivery.py src/core/background_delivery.py src/core/scheduler.py src/core/heartbeat_worker.py src/manager/relay/delivery_store.py tests/core/test_scheduler_rss_links.py tests/core/test_background_delivery.py tests/core/test_heartbeat_worker.py tests/core/test_delivery_store.py
git commit -m "feat: unify proactive delivery reliability"
```

### Task 6: Surface dead-letter and retry health for proactive producers

**Files:**
- Modify: `src/manager/relay/delivery_store.py`
- Modify: `tests/core/test_delivery_store.py`
- Modify: `tests/core/test_background_delivery.py`

- [ ] **Step 1: Add failing tests for proactive dead-letter visibility**

```python
@pytest.mark.asyncio
async def test_delivery_health_counts_proactive_dead_letters(...):
    ...
    assert health["dead_letter"] == 1
    assert health["recent_errors"][0]["reason"] == "missing_delivery_target"
```

- [ ] **Step 2: Run the health-focused tests to verify they fail**

Run: `uv run pytest tests/core/test_delivery_store.py tests/core/test_background_delivery.py -k "dead_letter or oldest_undelivered or recent_errors" -v`
Expected: FAIL until proactive producers emit canonical ledger events and health fields.

- [ ] **Step 3: Extend delivery-store summaries for proactive producers without creating a second ledger**

```python
health = await delivery_store.delivery_health(...)
assert "oldest_undelivered_age_sec" in health
assert "dead_letter_rows" in health
```

- [ ] **Step 4: Re-run delivery-health tests and the focused producer suite**

Run: `uv run pytest tests/core/test_delivery_store.py tests/core/test_background_delivery.py tests/core/test_scheduler_rss_links.py -v`
Expected: PASS.

- [ ] **Step 5: Commit the proactive health surface**

```bash
git add src/manager/relay/delivery_store.py tests/core/test_delivery_store.py tests/core/test_background_delivery.py
git commit -m "feat: expose proactive delivery health states"
```
