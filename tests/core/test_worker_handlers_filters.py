from handlers.worker_handlers import _parse_subcommand, _parse_tasks_filters


def test_worker_tasks_filter_defaults_to_user_chat_without_heartbeat():
    include, exclude = _parse_tasks_filters("")
    assert include == ["user_chat"]
    assert exclude == ["heartbeat"]


def test_worker_tasks_filter_all():
    include, exclude = _parse_tasks_filters("all")
    assert include is None
    assert exclude is None


def test_worker_tasks_filter_custom_args():
    include, exclude = _parse_tasks_filters(
        "source=user_cmd,user_chat exclude=heartbeat,system"
    )
    assert include == ["user_cmd", "user_chat"]
    assert exclude == ["heartbeat", "system"]


def test_worker_subcommand_defaults_to_list_and_supports_help():
    assert _parse_subcommand("/worker") == ("list", "")
    assert _parse_subcommand("/worker help") == ("help", "")
