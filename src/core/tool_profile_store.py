import json
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List

from core.config import DATA_DIR


def _default_cost(tool_name: str) -> float:
    name = str(tool_name or "").strip().lower()
    if name in {"read", "write", "edit", "bash"}:
        return 0.2
    if name.startswith("ext_"):
        return 0.8
    if name.startswith("memory"):
        return 0.5
    return 0.6


class ToolProfileStore:
    """Persist tool capability profile: success rate, latency, and cost."""

    def __init__(self):
        self.path = (Path(DATA_DIR) / "TOOL_PROFILES.json").resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._payload = self._read()

    def _default_payload(self) -> Dict[str, Any]:
        return {"version": 1, "tools": {}}

    def _read(self) -> Dict[str, Any]:
        if not self.path.exists():
            return self._default_payload()
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = self._default_payload()
                payload.update(loaded)
                if not isinstance(payload.get("tools"), dict):
                    payload["tools"] = {}
                return payload
        except Exception:
            pass
        return self._default_payload()

    def _write_unlocked(self) -> None:
        self.path.write_text(
            json.dumps(self._payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _ensure_profile_unlocked(self, tool_name: str) -> Dict[str, Any]:
        key = str(tool_name or "").strip()
        tools = self._payload.setdefault("tools", {})
        profile = tools.get(key)
        if not isinstance(profile, dict):
            profile = {
                "attempts": 0,
                "successes": 0,
                "failures": 0,
                "avg_latency_ms": 0.0,
                "last_latency_ms": 0.0,
                "cost_score": _default_cost(key),
            }
        profile["cost_score"] = float(profile.get("cost_score", _default_cost(key)))
        tools[key] = profile
        return profile

    def record(
        self, tool_name: str, *, success: bool, latency_ms: float
    ) -> Dict[str, Any]:
        key = str(tool_name or "").strip()
        if not key:
            return {}
        with self._lock:
            profile = self._ensure_profile_unlocked(key)
            attempts = int(profile.get("attempts", 0)) + 1
            successes = int(profile.get("successes", 0)) + (1 if success else 0)
            failures = int(profile.get("failures", 0)) + (0 if success else 1)
            last_latency = max(0.0, float(latency_ms))
            old_avg = float(profile.get("avg_latency_ms", 0.0))
            if attempts <= 1 or old_avg <= 0:
                avg = last_latency
            else:
                # EMA-lite: keep responsiveness with small sample smoothing.
                avg = old_avg * 0.75 + last_latency * 0.25
            profile.update(
                {
                    "attempts": attempts,
                    "successes": successes,
                    "failures": failures,
                    "avg_latency_ms": round(avg, 3),
                    "last_latency_ms": round(last_latency, 3),
                }
            )
            self._write_unlocked()
            return dict(profile)

    def get_profile(self, tool_name: str) -> Dict[str, Any]:
        key = str(tool_name or "").strip()
        if not key:
            return {}
        with self._lock:
            profile = self._ensure_profile_unlocked(key)
            return dict(profile)

    def score_tool(self, tool_name: str) -> float:
        profile = self.get_profile(tool_name)
        if not profile:
            return -1.0
        attempts = max(0, int(profile.get("attempts", 0)))
        successes = max(0, int(profile.get("successes", 0)))
        success_rate = (successes / attempts) if attempts > 0 else 0.6
        avg_latency_ms = max(
            1.0, float(profile.get("avg_latency_ms", 1200.0) or 1200.0)
        )
        cost = float(profile.get("cost_score", _default_cost(tool_name)))
        latency_penalty = min(2.0, avg_latency_ms / 2500.0)
        return round((success_rate * 1.3) - (latency_penalty * 0.35) - (cost * 0.35), 6)

    def rank_tools(self, tool_names: List[str]) -> List[str]:
        unique: List[str] = []
        for name in tool_names:
            key = str(name or "").strip()
            if key and key not in unique:
                unique.append(key)
        unique.sort(key=lambda name: self.score_tool(name), reverse=True)
        return unique


tool_profile_store = ToolProfileStore()
