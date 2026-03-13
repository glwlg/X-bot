# Web Runtime Consistency Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make scheduler, RSS, watchlist, and heartbeat web/API operations platform-aware and runtime-consistent, and close the `/remind` usability gap.

**Architecture:** Add application-service helpers inside the API package so endpoints stop doing raw state mutations with implicit Telegram defaults. Scheduler and heartbeat mutations must write canonical state and then synchronize runtime state inline, returning structured failure when runtime sync diverges.

**Tech Stack:** FastAPI, SQLAlchemy async session, APScheduler, Python asyncio, pytest/pytest-asyncio

---

Spec: `docs/superpowers/specs/2026-03-12-delivery-reliability-and-state-boundaries-design.md`

Scope: This is the edge-track plan for spec Workstream D plus the `/remind` usability gap. It assumes the controller-owned state/delivery prerequisites from `2026-03-12-state-and-proactive-delivery.md` are already merged.

Prerequisites/Start Gates:

- canonical user-scoped state is already enabled;
- proactive delivery contract and heuristic target removal are already merged;
- this plan must not modify `src/core/state_paths.py`, `src/core/state_store.py`, `src/core/scheduler.py`, or `src/manager/relay/result_relay.py`;
- if runtime sync needs a new core seam, stop and hand that requirement back to the controller-owned track instead of editing core scheduler code here.

## File Map

- Create: `src/api/api/platform_binding_service.py` - explicit platform-aware binding resolution for web/API callers
- Create: `src/api/api/automation_runtime_service.py` - scheduler and heartbeat mutation helpers that sync runtime state
- Modify: `src/api/api/binding_helpers.py` - stop hiding platform dimension behind Telegram defaults
- Modify: `src/api/api/endpoints/rss.py` - platform-aware request handling
- Modify: `src/api/api/endpoints/watchlist.py` - platform-aware request handling
- Modify: `src/api/api/endpoints/scheduler.py` - return all tasks and synchronize APScheduler behavior
- Modify: `src/api/api/endpoints/monitor.py` - add heartbeat control/status endpoints
- Modify: `src/api/api/endpoints/health.py` - add richer operational health surface
- Use: `src/core/scheduler.py:reload_scheduler_jobs` - controller-owned runtime sync seam consumed, not modified
- Use: `src/core/scheduler.py:schedule_reminder` - existing reminder scheduling seam consumed, not redefined
- Use: `src/core/heartbeat_store.py` - canonical heartbeat state/status source
- Create: `src/handlers/reminder_handlers.py` - `/remind` command parser and scheduler integration
- Modify: `src/handlers/start_handlers.py` - update help/menu copy to match real behavior
- Modify: `src/main.py` - register `/remind` and any new monitor/platform-aware hooks
- Test: `tests/api/test_scheduler_endpoints.py`
- Test: `tests/api/test_monitor_endpoints.py`
- Test: `tests/api/test_rss_watchlist_platform_endpoints.py`
- Test: `tests/core/test_reminder_handlers.py`
- Modify: `tests/core/test_command_subcommands.py`
- Modify: `tests/core/test_heartbeat_handlers.py`

## Chunk 1: API Platform Awareness And Runtime Sync

### Task 1: Add failing endpoint tests for explicit platform resolution

**Files:**
- Create: `tests/api/test_rss_watchlist_platform_endpoints.py`
- Modify: `src/api/api/binding_helpers.py`
- Modify: `src/api/api/endpoints/rss.py`
- Modify: `src/api/api/endpoints/watchlist.py`

- [ ] **Step 1: Write failing API tests that expose Telegram-only behavior**

```python
@pytest.mark.asyncio
async def test_rss_endpoint_uses_requested_platform_binding(client, session, user, bind_user):
    await bind_user(user.id, platform="discord", platform_user_id="discord-42")

    response = await client.get("/api/v1/rss", params={"platform": "discord"})

    assert response.status_code == 200
    assert response.json()["resolved_platform"] == "discord"


@pytest.mark.asyncio
async def test_watchlist_endpoint_returns_400_when_requested_platform_not_bound(client, user):
    response = await client.get("/api/v1/watchlist", params={"platform": "dingtalk"})

    assert response.status_code == 400
    assert "dingtalk" in response.json()["detail"].lower()
```

- [ ] **Step 2: Run the platform-binding endpoint tests to verify they fail**

Run: `uv run pytest tests/api/test_rss_watchlist_platform_endpoints.py -v`
Expected: FAIL because endpoints currently hard-code Telegram resolution.

- [ ] **Step 3: Introduce an explicit platform binding service**

```python
async def resolve_platform_binding(
    *,
    user_id: int,
    session: AsyncSession,
    platform: str | None,
) -> dict[str, str]:
    safe_platform = str(platform or "telegram").strip().lower()
    platform_user_id = await get_primary_platform_user_id(user_id, session, platform=safe_platform)
    if not platform_user_id:
        raise HTTPException(status_code=400, detail=f"No platform binding found for {safe_platform}")
    return {"platform": safe_platform, "platform_user_id": platform_user_id, "used_default_platform": platform is None}
```

- [ ] **Step 4: Update RSS and watchlist endpoints to use the resolved platform explicitly**

```python
binding = await resolve_platform_binding(user_id=current_user.id, session=session, platform=requested_platform)
rows = await state_store.list_subscriptions(binding["platform_user_id"])
return {"resolved_platform": binding["platform"], "used_default_platform": binding["used_default_platform"], "data": rows}
```

- [ ] **Step 5: Re-run the endpoint tests**

Run: `uv run pytest tests/api/test_rss_watchlist_platform_endpoints.py -v`
Expected: PASS with explicit per-platform behavior and compatibility defaulting visible in the response.

- [ ] **Step 6: Commit the platform-aware binding layer**

```bash
git add src/api/api/platform_binding_service.py src/api/api/binding_helpers.py src/api/api/endpoints/rss.py src/api/api/endpoints/watchlist.py tests/api/test_rss_watchlist_platform_endpoints.py
git commit -m "feat: make web automation APIs platform-aware"
```

### Task 2: Add scheduler runtime-sync semantics and list-all behavior without editing core scheduler

**Files:**
- Create: `tests/api/test_scheduler_endpoints.py`
- Create: `src/api/api/automation_runtime_service.py`
- Modify: `src/api/api/endpoints/scheduler.py`
- Modify: `tests/core/test_main_bootstrap.py`

- [ ] **Step 1: Write failing scheduler endpoint tests**

```python
@pytest.mark.asyncio
async def test_scheduler_list_returns_inactive_tasks(client, bound_user, seeded_tasks):
    response = await client.get("/api/v1/scheduler", params={"platform": "telegram"})

    assert response.status_code == 200
    assert any(item["is_active"] is False for item in response.json()["data"])


@pytest.mark.asyncio
async def test_scheduler_update_returns_runtime_sync_failed_when_reload_breaks(client, monkeypatch, bound_user):
    monkeypatch.setattr("api.api.automation_runtime_service.reload_scheduler_jobs", AsyncMock(side_effect=RuntimeError("boom")))

    response = await client.put("/api/v1/scheduler/1/status", json={"is_active": False, "platform": "telegram"})

    assert response.status_code == 409
    assert response.json()["error_code"] == "runtime_sync_failed"


@pytest.mark.asyncio
async def test_scheduler_create_update_delete_all_mark_dirty_and_reconcile(client, monkeypatch, bound_user):
    ...
    assert response.json()["runtime_sync"]["state"] in {"synced", "dirty"}


@pytest.mark.asyncio
async def test_scheduler_sync_prefers_incremental_then_falls_back_to_full_reload(client, monkeypatch, bound_user):
    ...
    assert response.json()["runtime_sync"]["mode"] in {"incremental", "full_reload"}


@pytest.mark.asyncio
async def test_scheduler_endpoint_reports_resolved_platform_and_defaulting(client, bound_user):
    response = await client.get("/api/v1/scheduler")
    assert response.status_code == 200
    assert "resolved_platform" in response.json()
    assert "used_default_platform" in response.json()
```

- [ ] **Step 2: Run the scheduler endpoint tests to verify they fail**

Run: `uv run pytest tests/api/test_scheduler_endpoints.py -v`
Expected: FAIL because the current API only lists active tasks and does not report runtime sync outcome.

- [ ] **Step 3: Implement a scheduler runtime service with inline sync semantics**

```python
async def update_scheduler_task_status(...):
    await state_store.update_task_status(task_id, is_active, platform_uid)
    try:
        await apply_scheduler_incremental_sync(platform_uid=platform_uid)
    except Exception as exc:
        try:
            await reload_scheduler_jobs()
        except Exception as reload_exc:
            await mark_runtime_sync_dirty(feature="scheduler", owner_id=platform_uid, reason=str(reload_exc))
            return {"ok": False, "error_code": "runtime_sync_failed", "detail": str(reload_exc), "runtime_sync": {"state": "dirty", "mode": "full_reload_failed"}}
        return {"ok": True, "effective_state": "synced", "runtime_sync": {"state": "synced", "mode": "full_reload"}}
    await clear_runtime_sync_dirty(feature="scheduler", owner_id=platform_uid)
    return {"ok": True, "effective_state": "synced", "runtime_sync": {"state": "synced", "mode": "incremental"}}
```

- [ ] **Step 4: Update scheduler endpoints to return structured effective status**

```python
result = await automation_runtime_service.update_scheduler_task_status(...)
if not result["ok"]:
    raise HTTPException(status_code=409, detail=result)
return {"success": True, **result}
```

- [ ] **Step 5: Re-run scheduler API tests including create/update/delete/status and bootstrap reconciliation**

Run: `uv run pytest tests/api/test_scheduler_endpoints.py tests/core/test_main_bootstrap.py -v`
Expected: PASS, with bootstrap tests still confirming scheduler startup registration.

- [ ] **Step 6: Commit the scheduler runtime-sync changes**

```bash
git add src/api/api/automation_runtime_service.py src/api/api/endpoints/scheduler.py tests/api/test_scheduler_endpoints.py tests/core/test_main_bootstrap.py
git commit -m "feat: sync scheduler API changes with runtime state"
```

### Task 3: Expose heartbeat control/status and richer health details

**Files:**
- Create: `tests/api/test_monitor_endpoints.py`
- Create: `src/api/api/automation_runtime_service.py`
- Modify: `src/api/api/endpoints/monitor.py`
- Modify: `src/api/api/endpoints/health.py`
- Modify: `src/core/heartbeat_store.py`
- Modify: `tests/core/test_heartbeat_handlers.py`

- [ ] **Step 1: Write failing monitor/health endpoint tests**

```python
@pytest.mark.asyncio
async def test_monitor_endpoint_returns_heartbeat_runtime_fields(client, bound_user):
    response = await client.get("/api/v1/monitor", params={"platform": "telegram"})

    assert response.status_code == 200
    assert "last_level" in response.json()
    assert "paused" in response.json()


@pytest.mark.asyncio
async def test_health_details_reports_delivery_summary(client):
    response = await client.get("/api/v1/health/details")

    assert response.status_code == 200
    assert "delivery_health" in response.json()


@pytest.mark.asyncio
async def test_monitor_mutations_return_runtime_sync_failed_when_runtime_refresh_breaks(client, monkeypatch, bound_user):
    monkeypatch.setattr("api.api.automation_runtime_service.refresh_heartbeat_runtime", AsyncMock(side_effect=RuntimeError("boom")))
    response = await client.post("/api/v1/monitor/pause", json={"platform": "telegram"})
    assert response.status_code == 409
    assert response.json()["error_code"] == "runtime_sync_failed"


@pytest.mark.asyncio
async def test_monitor_supports_resume_run_every_and_hours(client, bound_user):
    ...
    assert all(key in response.json() for key in ["paused", "last_level", "last_run_at", "last_error", "next_due", "resolved_platform", "used_default_platform"])


@pytest.mark.asyncio
async def test_health_details_report_operational_fields(client):
    response = await client.get("/api/v1/health/details")
    body = response.json()
    assert "retrying" in body["delivery_health"]
    assert "dead_letter" in body["delivery_health"]
    assert "oldest_undelivered_age_sec" in body["delivery_health"]
    assert "recent_errors" in body["delivery_health"]
    assert "scheduler_sync" in body
    assert "heartbeat_summary" in body
```

- [ ] **Step 2: Run the new monitor/health tests to verify they fail**

Run: `uv run pytest tests/api/test_monitor_endpoints.py -v`
Expected: FAIL because `monitor.py` only exposes checklist CRUD and `health.py` is still liveness-only.

- [ ] **Step 3: Add heartbeat control/status endpoints and operational health details**

```python
async def pause_heartbeat(...):
    spec = await hstore.set_heartbeat_spec(platform_uid, paused=True)
    runtime = await refresh_heartbeat_runtime(platform_uid)
    if not runtime["ok"]:
        return runtime
    return {"success": True, "paused": spec.get("paused"), "runtime_sync": runtime["runtime_sync"]}


@router.get("/status")
async def get_monitor_status(...):
    state = await hstore.get_state(platform_uid)
    return _serialize_monitor_state(state)


@router.post("/resume")
async def resume_monitor(...):
    ...


@router.post("/run")
async def run_monitor_now(...):
    ...


@router.put("/every")
async def update_monitor_every(...):
    ...


@router.put("/hours")
async def update_monitor_hours(...):
    ...
```

- [ ] **Step 4: Re-run monitor, health, and heartbeat handler tests**

Run: `uv run pytest tests/api/test_monitor_endpoints.py tests/core/test_heartbeat_handlers.py -v`
Expected: PASS with web-facing heartbeat status matching the CLI feature set.

- [ ] **Step 5: Commit the observability improvements**

```bash
git add src/api/api/automation_runtime_service.py src/api/api/endpoints/monitor.py src/api/api/endpoints/health.py tests/api/test_monitor_endpoints.py tests/core/test_heartbeat_handlers.py
git commit -m "feat: add heartbeat web controls and health details"
```

### Task 4: Make `/remind` real and align help text with behavior

**Files:**
- Create: `src/handlers/reminder_handlers.py`
- Create: `tests/core/test_reminder_handlers.py`
- Modify: `src/main.py`
- Modify: `src/handlers/start_handlers.py`
- Use: `src/core/scheduler.py:schedule_reminder`
- Modify: `tests/core/test_command_subcommands.py`

- [ ] **Step 1: Write the failing `/remind` handler tests**

```python
@pytest.mark.asyncio
async def test_remind_command_schedules_relative_reminder(monkeypatch):
    ctx = make_context("/remind 10m 喝水")
    schedule = AsyncMock(return_value=True)
    monkeypatch.setattr(reminder_handlers, "schedule_reminder", schedule)

    await remind_command(ctx)

    schedule.assert_awaited()


@pytest.mark.asyncio
async def test_remind_help_returns_usage(monkeypatch):
    ctx = make_context("/remind help")
    await remind_command(ctx)
    assert "10m" in ctx.replies[-1]
```

- [ ] **Step 2: Run the reminder tests to verify they fail**

Run: `uv run pytest tests/core/test_reminder_handlers.py tests/core/test_command_subcommands.py -v`
Expected: FAIL because `/remind` is advertised but not registered or implemented.

- [ ] **Step 3: Implement a minimal command parser and register it**

```python
async def remind_command(ctx: UnifiedContext) -> None:
    raw = str(ctx.message.text or "").strip()
    if raw in {"/remind", "/remind help"}:
        await ctx.reply(_remind_usage())
        return
    seconds, payload = _parse_relative_reminder(raw)
    trigger_time = datetime.datetime.now().astimezone() + datetime.timedelta(seconds=seconds)
    await schedule_reminder(user_id=str(ctx.message.user.id), chat_id=str(ctx.message.chat.id), message=payload, trigger_time=trigger_time, platform=ctx.message.platform)
    await ctx.reply("✅ 提醒已创建")
```

- [ ] **Step 4: Keep `/remind` scope limited to command registration/help and delegation**

Do not re-implement reminder delivery semantics in this plan. This handler must only parse input, call `src/core/scheduler.py:schedule_reminder`, and report success/failure using the canonical behavior introduced by the proactive-delivery plan.

- [ ] **Step 5: Update help/menu copy to point to a real command path**

Run: `uv run pytest tests/core/test_reminder_handlers.py tests/core/test_command_subcommands.py tests/core/test_start_handlers_stop.py -v`
Expected: PASS, proving `/remind` is now callable and help text matches reality.

- [ ] **Step 6: Commit the reminder usability fix**

```bash
git add src/handlers/reminder_handlers.py src/main.py src/handlers/start_handlers.py tests/core/test_reminder_handlers.py tests/core/test_command_subcommands.py
git commit -m "feat: add remind command entrypoint"
```
