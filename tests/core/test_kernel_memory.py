import json

from core.audit_store import audit_store
from core.kernel_memory import KernelMemoryStore


def _redirect_audit_paths(tmp_path):
    audit_root = (tmp_path / "audit").resolve()
    versions_root = (tmp_path / "versions").resolve()
    audit_root.mkdir(parents=True, exist_ok=True)
    versions_root.mkdir(parents=True, exist_ok=True)
    audit_store.audit_root = audit_root
    audit_store.versions_root = versions_root
    audit_store.events_path = (audit_root / "events.jsonl").resolve()


def _build_store(tmp_path) -> KernelMemoryStore:
    store = KernelMemoryStore()
    store.root = (tmp_path / "kernel-memory").resolve()
    store.short_root = (store.root / "short_term").resolve()
    store.long_root = (store.root / "long_term").resolve()
    store.user_long_root = (store.long_root / "users").resolve()
    store.short_root.mkdir(parents=True, exist_ok=True)
    store.user_long_root.mkdir(parents=True, exist_ok=True)
    return store


def test_kernel_memory_short_term_and_confirm_candidate(tmp_path):
    _redirect_audit_paths(tmp_path)
    store = _build_store(tmp_path)

    store.append_short_term("u1", role="user", text="请记住我喜欢 Python", source="chat")
    short_path = store._short_path("u1")
    payload = json.loads(short_path.read_text(encoding="utf-8"))
    assert payload["recent_context"]

    candidate = store.propose_user_memory(
        "u1",
        text="喜欢 Python",
        source="heartbeat:auto",
        confidence=0.6,
        memory_type="preference",
    )
    assert candidate["status"] == "pending"

    ok = store.confirm_candidate("u1", memory_id=candidate["id"], actor="u1")
    assert ok is True

    user_long = store._load_user_long("u1")
    assert not user_long["candidates"]
    assert user_long["confirmed"]
    assert user_long["confirmed"][0]["status"] == "confirmed"


def test_kernel_memory_deduplicate_and_confidence_growth(tmp_path):
    _redirect_audit_paths(tmp_path)
    store = _build_store(tmp_path)

    first = store.add_user_memory_confirmed(
        "u2",
        text="我喜欢深色主题",
        source="user_explicit",
        confidence=0.7,
    )
    second = store.add_user_memory_confirmed(
        "u2",
        text="我喜欢 深色主题",
        source="user_explicit",
        confidence=0.8,
    )
    data = store._load_user_long("u2")
    assert len(data["confirmed"]) == 1
    assert second["id"] == first["id"]
    assert float(data["confirmed"][0]["confidence"]) >= 0.8
