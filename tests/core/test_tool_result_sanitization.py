from core.skill_cli import TOOL_RESULT_PREFIX, _json_default, _normalize_raw_tool_result
from services.ai_service import AiService, MAX_TOOL_HISTORY_STRING


def test_skill_cli_json_default_compacts_binary_payload():
    rendered = _json_default(b"\x00\x01\x02")

    assert rendered == "<binary:3 bytes>"


def test_ai_service_sanitize_tool_result_truncates_huge_strings():
    huge = "x" * (MAX_TOOL_HISTORY_STRING + 256)

    result = AiService._sanitize_tool_result_for_history(
        {
            "ok": True,
            "text": huge,
            "data": {"output": huge},
        }
    )

    assert isinstance(result, dict)
    assert str(result["text"]).endswith("...[truncated]")
    assert len(str(result["text"])) < len(huge)
    assert str(result["data"]["output"]).endswith("...[truncated]")


def test_skill_cli_normalize_raw_tool_result_collapses_progress_stream():
    normalized = _normalize_raw_tool_result(
        [
            "🔐 正在检查公众号发布权限与 IP 白名单...",
            {
                "ok": False,
                "failure_mode": "fatal",
                "text": "❌ 发布前检查失败。",
                "terminal": True,
            },
        ]
    )

    assert normalized["ok"] is False
    assert normalized["failure_mode"] == "fatal"
    assert normalized["progress_messages"] == [
        "🔐 正在检查公众号发布权限与 IP 白名单..."
    ]
    assert TOOL_RESULT_PREFIX == "tool_result="
