import pytest

from ikaros.dev.codex_app_server_client import CodexAppServerClient


@pytest.mark.asyncio
async def test_app_server_client_collects_agent_message_and_turn_completion(tmp_path):
    client = CodexAppServerClient(
        command=["codex", "app-server"],
        cwd=str(tmp_path),
        env={},
        timeout_sec=30,
    )
    await client._handle_payload(
        {
            "jsonrpc": "2.0",
            "method": "item/agentMessage/delta",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "itemId": "msg-1",
                "delta": "hel",
            },
        }
    )
    await client._handle_payload(
        {
            "jsonrpc": "2.0",
            "method": "item/agentMessage/delta",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "itemId": "msg-1",
                "delta": "lo",
            },
        }
    )
    await client._handle_payload(
        {
            "jsonrpc": "2.0",
            "method": "turn/completed",
            "params": {
                "threadId": "thread-1",
                "turn": {"id": "turn-1", "status": "completed", "items": []},
            },
        }
    )

    turn = await client.wait_for_turn_completed(turn_id="turn-1")
    result = client.build_result(
        thread_id="thread-1",
        turn_id="turn-1",
        turn=turn,
        loaded_existing_thread=False,
    )

    assert result["ok"] is True
    assert result["stdout"] == "hello"
    assert result["transport"] == "app-server"
    assert result["transport_session_id"] == "thread-1"


def test_app_server_client_prefers_available_approval_decision(tmp_path):
    client = CodexAppServerClient(
        command=["codex", "app-server"],
        cwd=str(tmp_path),
        env={},
        timeout_sec=30,
        approval_decision="accept",
    )

    assert (
        client._select_decision(["decline", "acceptForSession"], default="accept")
        == "acceptForSession"
    )
