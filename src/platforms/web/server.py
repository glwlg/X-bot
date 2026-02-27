from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque

from core.config import (
    CORE_CHAT_EXECUTION_MODE,
    CORE_CHAT_WORKER_BACKEND,
    HEARTBEAT_ENABLED,
    HEARTBEAT_MODE,
    WEB_DASHBOARD_ALLOW_WRITE,
    WEB_DASHBOARD_ENABLED,
    WEB_DASHBOARD_EVENT_BUFFER,
    WEB_DASHBOARD_HOST,
    WEB_DASHBOARD_POLL_SEC,
    WEB_DASHBOARD_PORT,
    WEB_DASHBOARD_TOKEN,
)
from core.kernel_config_store import kernel_config_store
from core.heartbeat_store import heartbeat_store
from core.soul_store import soul_store
from core.worker_store import worker_registry
from shared.queue.dispatch_queue import dispatch_queue

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_iso(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            return parsed.astimezone()
        return parsed
    except Exception:
        return None


def _to_status_bucket(status: str) -> str:
    raw = str(status or "").strip().lower()
    if raw in {"pending", "running", "done", "failed", "cancelled"}:
        return raw
    return "unknown"


class WebDashboardServer:
    def __init__(self) -> None:
        self.enabled = WEB_DASHBOARD_ENABLED
        self.host = WEB_DASHBOARD_HOST
        self.port = int(WEB_DASHBOARD_PORT)
        self.poll_sec = float(WEB_DASHBOARD_POLL_SEC)
        self.buffer_size = int(WEB_DASHBOARD_EVENT_BUFFER)
        self.allow_write = WEB_DASHBOARD_ALLOW_WRITE
        self.token = WEB_DASHBOARD_TOKEN

        self._web: Any = None
        self._app: Any = None
        self._runner: Any = None
        self._site: Any = None

        self._stop_event = asyncio.Event()
        self._loop_task: asyncio.Task | None = None

        self._snapshot_lock = asyncio.Lock()
        self._snapshot: dict[str, Any] = {"generated_at": _now_iso()}

        self._events_lock = asyncio.Lock()
        self._events: Deque[dict[str, Any]] = deque(maxlen=max(200, self.buffer_size))
        self._next_seq = 0

        self._subscribers: set[asyncio.Queue] = set()
        self._known_task_status: dict[str, str] = {}
        self._known_task_delivery: dict[str, str] = {}
        self._known_heartbeat_runs: dict[str, str] = {}
        self._seeded = False

        self._last_manager_tick_ts = 0.0
        self._last_manager_tick_at = ""

        self._static_dir = (Path(__file__).resolve().parent / "static").resolve()

    async def start(self) -> None:
        if not self.enabled:
            return
        if self._runner is not None:
            return

        try:
            from aiohttp import web
        except Exception as exc:
            logger.warning(
                "Web dashboard disabled: aiohttp import failed (%s)",
                exc,
            )
            return

        self._web = web
        self._app = self._create_app()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host=self.host, port=self.port)
        await self._site.start()

        self._stop_event.clear()
        await self._refresh_state_once()
        self._loop_task = asyncio.create_task(
            self._poll_loop(), name="web-dashboard-poll"
        )

        logger.info(
            "Web dashboard started at http://%s:%s",
            self.host,
            self.port,
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
        self._loop_task = None

        if self._runner is not None:
            with contextlib.suppress(Exception):
                await self._runner.cleanup()

        self._runner = None
        self._site = None
        self._app = None
        self._web = None

        subscribers = list(self._subscribers)
        self._subscribers.clear()
        for queue in subscribers:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait({"type": "server.stop"})

    def _create_app(self) -> Any:
        web = self._web
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/config", self._handle_config)
        app.router.add_static("/assets", path=str(self._static_dir), name="assets")

        app.router.add_get("/api/v1/health", self._api_health)
        app.router.add_get("/api/v1/snapshot", self._api_snapshot)
        app.router.add_get("/api/v1/events", self._api_events)
        app.router.add_get("/api/v1/events/stream", self._api_events_stream)
        app.router.add_get("/api/v1/config/bootstrap", self._api_config_bootstrap)
        app.router.add_get("/api/v1/config/system", self._api_config_system)
        app.router.add_get("/api/v1/config/manager", self._api_config_manager)
        app.router.add_patch("/api/v1/config/manager", self._api_config_manager_update)
        app.router.add_get("/api/v1/config/workers", self._api_config_workers)
        app.router.add_post("/api/v1/config/workers", self._api_config_workers_create)
        app.router.add_patch(
            "/api/v1/config/workers/{worker_id}",
            self._api_config_workers_update,
        )
        app.router.add_delete(
            "/api/v1/config/workers/{worker_id}",
            self._api_config_workers_delete,
        )
        app.router.add_get(
            "/api/v1/config/workers/{worker_id}/soul",
            self._api_config_worker_soul,
        )
        app.router.add_put(
            "/api/v1/config/workers/{worker_id}/soul",
            self._api_config_worker_soul_update,
        )
        app.router.add_get("/api/v1/init/soul", self._api_get_soul)
        app.router.add_post("/api/v1/actions/init", self._api_init_action)
        return app

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._refresh_state_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Web dashboard poll failed: %s", exc, exc_info=True)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_sec)
            except asyncio.TimeoutError:
                continue

    async def _refresh_state_once(self) -> None:
        snapshot = await self._build_snapshot()
        async with self._snapshot_lock:
            self._snapshot = snapshot
        await self._scan_for_events(snapshot)

    async def _build_snapshot(self) -> dict[str, Any]:
        generated_at = _now_iso()

        users: list[dict[str, Any]] = []
        user_ids = await heartbeat_store.list_users()
        for user_id in user_ids:
            state = await heartbeat_store.get_state(user_id)
            spec = dict(state.get("spec") or {})
            status = dict(state.get("status") or {})
            heartbeat = dict(status.get("heartbeat") or {})
            session = dict(status.get("session") or {})
            active_task = session.get("active_task")
            users.append(
                {
                    "user_id": str(user_id),
                    "every": str(spec.get("every") or "30m"),
                    "paused": bool(spec.get("paused", False)),
                    "active_hours": dict(spec.get("active_hours") or {}),
                    "checklist_count": len(list(state.get("checklist") or [])),
                    "heartbeat": {
                        "last_run_at": str(heartbeat.get("last_run_at") or ""),
                        "last_result": str(heartbeat.get("last_result") or ""),
                        "last_level": str(heartbeat.get("last_level") or "OK"),
                        "next_due_at": str(heartbeat.get("next_due_at") or ""),
                    },
                    "session": {
                        "active_worker_id": str(
                            session.get("active_worker_id") or "worker-main"
                        ),
                        "last_event": str(session.get("last_event") or ""),
                        "active_task": active_task
                        if isinstance(active_task, dict)
                        else None,
                    },
                }
            )

        await worker_registry.ensure_default_worker("worker-main")
        workers_raw = await worker_registry.list_workers()
        workers = [
            {
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or ""),
                "backend": str(item.get("backend") or ""),
                "status": str(item.get("status") or "ready"),
                "last_task_id": str(item.get("last_task_id") or ""),
                "last_error": str(item.get("last_error") or ""),
            }
            for item in workers_raw
            if isinstance(item, dict)
        ]

        tasks_raw = [
            task.to_dict() for task in await dispatch_queue.list_tasks(limit=120)
        ]
        tasks: list[dict[str, Any]] = []
        task_user_ids: set[str] = set()
        totals = {
            "pending": 0,
            "running": 0,
            "done": 0,
            "failed": 0,
            "cancelled": 0,
            "unknown": 0,
        }
        for item in tasks_raw:
            metadata = dict(item.get("metadata") or {})
            status = _to_status_bucket(str(item.get("status") or ""))
            totals[status] = int(totals.get(status, 0)) + 1
            user_id = str(metadata.get("user_id") or "").strip()
            if user_id:
                task_user_ids.add(user_id)
            tasks.append(
                {
                    "task_id": str(item.get("task_id") or ""),
                    "worker_id": str(item.get("worker_id") or "worker-main"),
                    "source": str(item.get("source") or ""),
                    "backend": str(item.get("backend") or ""),
                    "status": status,
                    "created_at": str(item.get("created_at") or ""),
                    "updated_at": str(item.get("updated_at") or ""),
                    "started_at": str(item.get("started_at") or ""),
                    "ended_at": str(item.get("ended_at") or ""),
                    "delivered_at": str(item.get("delivered_at") or ""),
                    "error": str(item.get("error") or "")[:240],
                    "retry_count": int(item.get("retry_count") or 0),
                    "user_id": user_id,
                }
            )

        if not users and task_user_ids:
            for user_id in sorted(task_user_ids):
                users.append(
                    {
                        "user_id": user_id,
                        "every": "30m",
                        "paused": False,
                        "active_hours": {},
                        "checklist_count": 0,
                        "heartbeat": {
                            "last_run_at": "",
                            "last_result": "",
                            "last_level": "NOTICE",
                            "next_due_at": "",
                        },
                        "session": {
                            "active_worker_id": "worker-main",
                            "last_event": "",
                            "active_task": None,
                        },
                    }
                )

        pending_users = 0
        for user in users:
            if user.get("paused"):
                continue
            due = _parse_iso(
                str((user.get("heartbeat") or {}).get("next_due_at") or "")
            )
            if due is not None and due <= datetime.now().astimezone():
                pending_users += 1

        return {
            "generated_at": generated_at,
            "manager": {
                "office": "Core Manager",
                "last_tick_at": self._last_manager_tick_at,
                "users_total": len(users),
                "users_due": pending_users,
            },
            "users": users,
            "workers": workers,
            "dispatch": {
                "totals": totals,
                "tasks": tasks,
            },
        }

    async def _scan_for_events(self, snapshot: dict[str, Any]) -> None:
        now = asyncio.get_running_loop().time()
        if self._last_manager_tick_ts <= 0 or now - self._last_manager_tick_ts >= 30:
            self._last_manager_tick_ts = now
            self._last_manager_tick_at = _now_iso()
            await self._publish_event(
                {
                    "type": "manager.tick",
                    "level": "info",
                    "at": self._last_manager_tick_at,
                    "title": "Manager office check",
                    "detail": "Manager reviewed heartbeat schedule and queue.",
                    "payload": {
                        "users_total": int(
                            (snapshot.get("manager") or {}).get("users_total") or 0
                        ),
                        "users_due": int(
                            (snapshot.get("manager") or {}).get("users_due") or 0
                        ),
                    },
                }
            )

        tasks = list((snapshot.get("dispatch") or {}).get("tasks") or [])
        tasks_sorted = sorted(
            [item for item in tasks if isinstance(item, dict)],
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item.get("task_id") or ""),
            ),
        )

        if not self._seeded:
            for item in tasks_sorted[-40:]:
                task_id = str(item.get("task_id") or "")
                if not task_id:
                    continue
                self._known_task_status[task_id] = str(item.get("status") or "pending")
                self._known_task_delivery[task_id] = str(item.get("delivered_at") or "")
            for row in list(snapshot.get("users") or []):
                if not isinstance(row, dict):
                    continue
                user_id = str(row.get("user_id") or "")
                if not user_id:
                    continue
                heartbeat = dict(row.get("heartbeat") or {})
                self._known_heartbeat_runs[user_id] = str(
                    heartbeat.get("last_run_at") or ""
                )
            self._seeded = True
            return

        for item in tasks_sorted:
            task_id = str(item.get("task_id") or "")
            if not task_id:
                continue
            current_status = str(item.get("status") or "pending")
            previous_status = self._known_task_status.get(task_id)

            if previous_status is None:
                await self._publish_event(
                    self._build_task_event(
                        event_type="task.enqueued",
                        level="info",
                        title="Task dispatched",
                        detail=f"Task {task_id} queued for {item.get('worker_id') or 'worker-main'}.",
                        at=str(item.get("created_at") or _now_iso()),
                        task=item,
                    )
                )
            elif previous_status != current_status:
                mapped = self._map_task_status_event(current_status)
                await self._publish_event(
                    self._build_task_event(
                        event_type=mapped[0],
                        level=mapped[1],
                        title=mapped[2],
                        detail=f"Task {task_id} status changed {previous_status} -> {current_status}.",
                        at=self._pick_task_event_time(item, current_status),
                        task=item,
                    )
                )

            previous_delivery = self._known_task_delivery.get(task_id, "")
            current_delivery = str(item.get("delivered_at") or "")
            if current_delivery and current_delivery != previous_delivery:
                await self._publish_event(
                    self._build_task_event(
                        event_type="result.delivered",
                        level="success",
                        title="Result delivered",
                        detail=f"Task {task_id} result delivered back to user.",
                        at=current_delivery,
                        task=item,
                    )
                )

            self._known_task_status[task_id] = current_status
            self._known_task_delivery[task_id] = current_delivery

        users = list(snapshot.get("users") or [])
        for row in users:
            if not isinstance(row, dict):
                continue
            user_id = str(row.get("user_id") or "")
            if not user_id:
                continue
            heartbeat = dict(row.get("heartbeat") or {})
            current_run = str(heartbeat.get("last_run_at") or "")
            previous_run = self._known_heartbeat_runs.get(user_id)
            if previous_run and current_run and current_run != previous_run:
                level = str(heartbeat.get("last_level") or "NOTICE").upper()
                visual_level = "info"
                if level == "ACTION":
                    visual_level = "warning"
                elif level == "OK":
                    visual_level = "success"
                await self._publish_event(
                    {
                        "type": "heartbeat.tick",
                        "level": visual_level,
                        "at": current_run,
                        "title": "Heartbeat check finished",
                        "detail": f"{user_id} heartbeat completed with level {level}.",
                        "payload": {
                            "user_id": user_id,
                            "level": level,
                            "last_result": str(heartbeat.get("last_result") or "")[
                                :400
                            ],
                        },
                    }
                )
            self._known_heartbeat_runs[user_id] = current_run

    def _pick_task_event_time(self, task: dict[str, Any], status: str) -> str:
        if status == "running":
            return str(task.get("started_at") or task.get("updated_at") or _now_iso())
        if status in {"done", "failed", "cancelled"}:
            return str(task.get("ended_at") or task.get("updated_at") or _now_iso())
        return str(task.get("updated_at") or task.get("created_at") or _now_iso())

    @staticmethod
    def _map_task_status_event(status: str) -> tuple[str, str, str]:
        if status == "running":
            return ("task.claimed", "info", "Task claimed")
        if status == "done":
            return ("task.completed", "success", "Task completed")
        if status == "failed":
            return ("task.failed", "error", "Task failed")
        if status == "cancelled":
            return ("task.cancelled", "warning", "Task cancelled")
        if status == "pending":
            return ("task.enqueued", "info", "Task queued")
        return ("task.updated", "info", "Task updated")

    @staticmethod
    def _build_task_event(
        *,
        event_type: str,
        level: str,
        title: str,
        detail: str,
        at: str,
        task: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "type": event_type,
            "level": level,
            "at": at,
            "title": title,
            "detail": detail,
            "payload": {
                "task_id": str(task.get("task_id") or ""),
                "worker_id": str(task.get("worker_id") or "worker-main"),
                "status": str(task.get("status") or "pending"),
                "source": str(task.get("source") or ""),
                "user_id": str(task.get("user_id") or ""),
                "error": str(task.get("error") or "")[:240],
            },
        }

    async def _publish_event(self, event: dict[str, Any]) -> None:
        event_obj = dict(event)
        async with self._events_lock:
            self._next_seq += 1
            event_obj["seq"] = self._next_seq
            self._events.append(event_obj)
            subscribers = list(self._subscribers)

        for queue in subscribers:
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event_obj)

    async def _events_after(self, after_seq: int, limit: int) -> list[dict[str, Any]]:
        safe_after = max(0, int(after_seq))
        safe_limit = max(1, min(1000, int(limit)))
        async with self._events_lock:
            matched = [
                dict(item)
                for item in self._events
                if int(item.get("seq") or 0) > safe_after
            ]
        return matched[:safe_limit]

    async def _snapshot_copy(self) -> dict[str, Any]:
        async with self._snapshot_lock:
            return json.loads(json.dumps(self._snapshot, ensure_ascii=False))

    async def _handle_index(self, request: Any) -> Any:
        _ = request
        return self._web.FileResponse(self._static_dir / "index.html")

    async def _handle_config(self, request: Any) -> Any:
        _ = request
        return self._web.FileResponse(self._static_dir / "config.html")

    async def _api_health(self, request: Any) -> Any:
        _ = request
        return self._web.json_response({"ok": True, "now": _now_iso()})

    async def _api_snapshot(self, request: Any) -> Any:
        _ = request
        return self._web.json_response(await self._snapshot_copy())

    async def _api_events(self, request: Any) -> Any:
        after = self._to_int(request.query.get("after", "0"), default=0)
        limit = self._to_int(request.query.get("limit", "200"), default=200)
        events = await self._events_after(after, limit)
        next_seq = after
        if events:
            next_seq = int(events[-1].get("seq") or after)
        return self._web.json_response(
            {"ok": True, "after": after, "next_seq": next_seq, "events": events}
        )

    async def _api_events_stream(self, request: Any) -> Any:
        after = self._to_int(request.query.get("after", "0"), default=0)
        response = self._web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(request)

        seeded_events = await self._events_after(after, 500)
        for item in seeded_events:
            await self._write_sse(response, item)

        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.add(queue)
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    await response.write(b": keepalive\n\n")
                    continue
                await self._write_sse(response, item)
        except (ConnectionResetError, BrokenPipeError):
            pass
        except asyncio.CancelledError:
            raise
        finally:
            self._subscribers.discard(queue)
            with contextlib.suppress(Exception):
                await response.write_eof()
        return response

    async def _api_config_bootstrap(self, request: Any) -> Any:
        _ = request
        system = self._system_config_payload()
        manager = self._manager_config_payload()
        workers = await self._workers_config_payload()
        return self._web.json_response(
            {
                "ok": True,
                "system": system,
                "manager": manager,
                "workers": workers,
            }
        )

    async def _api_config_system(self, request: Any) -> Any:
        _ = request
        return self._web.json_response(
            {"ok": True, "system": self._system_config_payload()}
        )

    async def _api_config_manager(self, request: Any) -> Any:
        _ = request
        return self._web.json_response(
            {"ok": True, "manager": self._manager_config_payload()}
        )

    async def _api_config_manager_update(self, request: Any) -> Any:
        error_response = self._guard_write(request)
        if error_response is not None:
            return error_response

        body = await self._json_body(request)
        soul_text = str(body.get("soul_content") or "").strip()
        if not soul_text:
            return self._web.json_response(
                {"ok": False, "error": "soul_content is required"},
                status=400,
            )

        soul_store.update_core(
            soul_text,
            actor="web-dashboard",
            reason="web_config_manager_update",
        )
        payload = self._manager_config_payload()
        await self._publish_event(
            {
                "type": "control.action",
                "level": "success",
                "at": _now_iso(),
                "title": "Manager config updated",
                "detail": "Core manager SOUL has been updated.",
                "payload": {
                    "target": "manager",
                    "latest_version_id": str(payload.get("latest_version_id") or ""),
                },
            }
        )
        return self._web.json_response({"ok": True, "manager": payload})

    async def _api_config_workers(self, request: Any) -> Any:
        _ = request
        payload = await self._workers_config_payload()
        return self._web.json_response({"ok": True, "workers": payload})

    async def _api_config_workers_create(self, request: Any) -> Any:
        error_response = self._guard_write(request)
        if error_response is not None:
            return error_response

        body = await self._json_body(request)
        worker_name = str(body.get("name") or "").strip() or "worker"
        backend = self._normalize_worker_backend(str(body.get("backend") or "").strip())
        worker_id = str(body.get("worker_id") or "").strip() or None
        worker = await worker_registry.create_worker(
            name=worker_name,
            backend=backend,
            worker_id=worker_id,
        )
        payload = await self._workers_config_payload()
        await self._publish_event(
            {
                "type": "control.action",
                "level": "success",
                "at": _now_iso(),
                "title": "Worker created",
                "detail": f"Worker {worker.get('id')} created.",
                "payload": {
                    "target": "worker",
                    "worker_id": str(worker.get("id") or ""),
                    "action": "create",
                },
            }
        )
        return self._web.json_response(
            {"ok": True, "worker": worker, "workers": payload}
        )

    async def _api_config_workers_update(self, request: Any) -> Any:
        error_response = self._guard_write(request)
        if error_response is not None:
            return error_response

        worker_id = str(request.match_info.get("worker_id") or "").strip()
        if not worker_id:
            return self._web.json_response(
                {"ok": False, "error": "worker_id is required"},
                status=400,
            )

        body = await self._json_body(request)
        fields: dict[str, Any] = {}
        for key in ("name", "status", "summary"):
            if key in body:
                fields[key] = str(body.get(key) or "").strip()
        if "backend" in body:
            fields["backend"] = self._normalize_worker_backend(
                str(body.get("backend") or "")
            )

        if not fields:
            return self._web.json_response(
                {"ok": False, "error": "no updatable fields"},
                status=400,
            )

        updated = await worker_registry.update_worker(worker_id, **fields)
        if not updated:
            return self._web.json_response(
                {"ok": False, "error": "worker not found"},
                status=404,
            )

        payload = await self._workers_config_payload()
        await self._publish_event(
            {
                "type": "control.action",
                "level": "success",
                "at": _now_iso(),
                "title": "Worker updated",
                "detail": f"Worker {updated.get('id')} updated.",
                "payload": {
                    "target": "worker",
                    "worker_id": str(updated.get("id") or ""),
                    "action": "update",
                },
            }
        )
        return self._web.json_response(
            {"ok": True, "worker": updated, "workers": payload}
        )

    async def _api_config_workers_delete(self, request: Any) -> Any:
        error_response = self._guard_write(request)
        if error_response is not None:
            return error_response

        worker_id = str(request.match_info.get("worker_id") or "").strip()
        if not worker_id:
            return self._web.json_response(
                {"ok": False, "error": "worker_id is required"},
                status=400,
            )
        if worker_id in {"worker-main", "main"}:
            return self._web.json_response(
                {"ok": False, "error": "default worker cannot be deleted"},
                status=400,
            )

        deleted = await worker_registry.delete_worker(worker_id)
        if not deleted:
            return self._web.json_response(
                {"ok": False, "error": "worker not found"},
                status=404,
            )

        payload = await self._workers_config_payload()
        await self._publish_event(
            {
                "type": "control.action",
                "level": "warning",
                "at": _now_iso(),
                "title": "Worker deleted",
                "detail": f"Worker {worker_id} deleted.",
                "payload": {
                    "target": "worker",
                    "worker_id": worker_id,
                    "action": "delete",
                },
            }
        )
        return self._web.json_response({"ok": True, "workers": payload})

    async def _api_config_worker_soul(self, request: Any) -> Any:
        worker_id = (
            str(request.match_info.get("worker_id") or "").strip() or "worker-main"
        )
        payload = soul_store.load_worker(worker_id)
        return self._web.json_response(
            {
                "ok": True,
                "worker_id": worker_id,
                "path": payload.path,
                "updated_at": payload.updated_at,
                "latest_version_id": payload.latest_version_id,
                "content": payload.content,
            }
        )

    async def _api_config_worker_soul_update(self, request: Any) -> Any:
        error_response = self._guard_write(request)
        if error_response is not None:
            return error_response

        worker_id = (
            str(request.match_info.get("worker_id") or "").strip() or "worker-main"
        )
        body = await self._json_body(request)
        content = str(body.get("content") or "").strip()
        if not content:
            return self._web.json_response(
                {"ok": False, "error": "content is required"},
                status=400,
            )

        soul_store.update_worker(
            worker_id,
            content,
            actor="web-dashboard",
            reason="web_config_worker_soul_update",
        )
        payload = soul_store.load_worker(worker_id)
        await self._publish_event(
            {
                "type": "control.action",
                "level": "success",
                "at": _now_iso(),
                "title": "Worker SOUL updated",
                "detail": f"Worker {worker_id} SOUL has been updated.",
                "payload": {
                    "target": "worker_soul",
                    "worker_id": worker_id,
                    "latest_version_id": payload.latest_version_id,
                },
            }
        )
        return self._web.json_response(
            {
                "ok": True,
                "worker_id": worker_id,
                "path": payload.path,
                "updated_at": payload.updated_at,
                "latest_version_id": payload.latest_version_id,
                "content": payload.content,
            }
        )

    @staticmethod
    def _normalize_worker_backend(value: str) -> str:
        raw = str(value or "").strip().lower()
        if raw in {"core", "core-agent", ""}:
            return "core-agent"
        if raw in {"gemini", "gemini-cli"}:
            return "gemini-cli"
        if raw in {"sh", "bash", "shell"}:
            return "shell"
        if raw == "codex":
            return "codex"
        return "core-agent"

    async def _workers_config_payload(self) -> dict[str, Any]:
        await worker_registry.ensure_default_worker("worker-main")
        workers = await worker_registry.list_workers()
        return {
            "count": len(workers),
            "items": workers,
        }

    def _system_config_payload(self) -> dict[str, Any]:
        kernel = kernel_config_store.read()
        snapshot = dict(kernel.get("config") or {})
        effective = {
            "core_chat_execution_mode": CORE_CHAT_EXECUTION_MODE,
            "core_chat_worker_backend": CORE_CHAT_WORKER_BACKEND,
            "heartbeat_enabled": HEARTBEAT_ENABLED,
            "heartbeat_mode": HEARTBEAT_MODE,
            "web_dashboard_enabled": self.enabled,
            "web_dashboard_host": self.host,
            "web_dashboard_port": self.port,
        }
        return {
            "snapshot": snapshot,
            "effective": effective,
            "snapshot_version": int(kernel.get("version") or 1),
        }

    def _manager_config_payload(self) -> dict[str, Any]:
        payload = soul_store.load_core()
        return {
            "scope": "manager",
            "path": payload.path,
            "updated_at": payload.updated_at,
            "latest_version_id": payload.latest_version_id,
            "soul_content": payload.content,
        }

    async def _json_body(self, request: Any) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            return {}
        return payload

    def _guard_write(self, request: Any) -> Any | None:
        if not self.allow_write:
            return self._web.json_response(
                {"ok": False, "error": "WEB_DASHBOARD_ALLOW_WRITE is false"},
                status=403,
            )
        if not self._is_authorized(request):
            return self._web.json_response(
                {"ok": False, "error": "unauthorized"},
                status=401,
            )
        return None

    async def _api_get_soul(self, request: Any) -> Any:
        scope = str(request.query.get("scope", "core")).strip().lower()
        worker_id = (
            str(request.query.get("worker_id", "worker-main")).strip() or "worker-main"
        )
        if scope == "worker":
            payload = soul_store.load_worker(worker_id)
        else:
            scope = "core"
            payload = soul_store.load_core()
        return self._web.json_response(
            {
                "ok": True,
                "scope": scope,
                "worker_id": worker_id,
                "path": payload.path,
                "updated_at": payload.updated_at,
                "latest_version_id": payload.latest_version_id,
                "content": payload.content,
            }
        )

    async def _api_init_action(self, request: Any) -> Any:
        if not self.allow_write:
            return self._web.json_response(
                {"ok": False, "error": "WEB_DASHBOARD_ALLOW_WRITE is false"},
                status=403,
            )

        if not self._is_authorized(request):
            return self._web.json_response(
                {"ok": False, "error": "unauthorized"},
                status=401,
            )

        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}

        scope = str(body.get("scope", "core")).strip().lower()
        mode = str(body.get("mode", "ensure")).strip().lower()
        worker_id = str(body.get("worker_id", "worker-main")).strip() or "worker-main"
        content = str(body.get("content") or "").strip()

        if scope not in {"core", "worker"}:
            return self._web.json_response(
                {"ok": False, "error": "invalid scope"}, status=400
            )
        if mode not in {"ensure", "replace"}:
            return self._web.json_response(
                {"ok": False, "error": "invalid mode"}, status=400
            )
        if mode == "replace" and not content:
            return self._web.json_response(
                {"ok": False, "error": "content is required for replace mode"},
                status=400,
            )

        if scope == "core":
            if mode == "replace":
                soul_store.update_core(
                    content,
                    actor="web-dashboard",
                    reason="web_dashboard_replace_core_soul",
                )
            payload = soul_store.load_core()
        else:
            if mode == "replace":
                soul_store.update_worker(
                    worker_id,
                    content,
                    actor="web-dashboard",
                    reason="web_dashboard_replace_worker_soul",
                )
            payload = soul_store.load_worker(worker_id)

        await self._publish_event(
            {
                "type": "control.action",
                "level": "success",
                "at": _now_iso(),
                "title": "Initialization updated",
                "detail": f"{scope} soul processed with mode={mode}.",
                "payload": {
                    "scope": scope,
                    "mode": mode,
                    "worker_id": worker_id,
                    "path": payload.path,
                    "latest_version_id": payload.latest_version_id,
                },
            }
        )

        return self._web.json_response(
            {
                "ok": True,
                "scope": scope,
                "mode": mode,
                "worker_id": worker_id,
                "path": payload.path,
                "updated_at": payload.updated_at,
                "latest_version_id": payload.latest_version_id,
            }
        )

    def _is_authorized(self, request: Any) -> bool:
        if not self.token:
            return True
        candidate = str(request.headers.get("X-XBOT-Token") or "")
        return secrets.compare_digest(candidate, self.token)

    async def _write_sse(self, response: Any, event: dict[str, Any]) -> None:
        payload = json.dumps(event, ensure_ascii=False)
        frame = f"event: timeline\ndata: {payload}\n\n".encode("utf-8")
        await response.write(frame)

    @staticmethod
    def _to_int(value: str, *, default: int) -> int:
        try:
            return int(str(value).strip())
        except Exception:
            return default


web_dashboard_server = WebDashboardServer()
