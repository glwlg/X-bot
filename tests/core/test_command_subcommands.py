import importlib.util
from pathlib import Path

from handlers.heartbeat_handlers import _parse_subcommand as parse_heartbeat_subcommand
from handlers.worker_handlers import _parse_subcommand as parse_worker_subcommand


def _load_module(relative_path: str, module_name: str):
    path = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeAdapterManager:
    def __init__(self):
        self.commands: list[str] = []
        self.callbacks: list[str] = []

    def on_command(self, command, handler, description=None):
        _ = handler
        _ = description
        self.commands.append(str(command))

    def on_callback_query(self, pattern, handler):
        _ = handler
        self.callbacks.append(str(pattern))

    def get_adapter(self, platform_name: str):
        _ = platform_name
        raise ValueError("adapter not available in test")


def test_heartbeat_and_worker_default_subcommands():
    assert parse_heartbeat_subcommand("/heartbeat") == ("list", "")
    assert parse_heartbeat_subcommand("/heartbeat help") == ("help", "")
    assert parse_worker_subcommand("/worker") == ("list", "")
    assert parse_worker_subcommand("/worker help") == ("help", "")


def test_stock_rss_schedule_remind_deploy_subcommand_parsers():
    stock = _load_module(
        "skills/builtin/stock_watch/scripts/execute.py", "stock_watch_subcmd_test"
    )
    rss = _load_module(
        "skills/builtin/rss_subscribe/scripts/execute.py", "rss_subcmd_test"
    )
    schedule = _load_module(
        "skills/builtin/scheduler_manager/scripts/execute.py",
        "schedule_subcmd_test",
    )
    remind = _load_module(
        "skills/builtin/reminder/scripts/execute.py", "remind_subcmd_test"
    )
    deploy = _load_module(
        "skills/builtin/deployment_manager/scripts/execute.py", "deploy_subcmd_test"
    )

    assert stock._parse_stock_subcommand("/stock") == ("list", "")
    assert stock._parse_stock_subcommand("/stock add 茅台") == ("add", "茅台")
    assert stock._parse_stock_subcommand("/stock unknown") == ("help", "")

    assert rss._parse_rss_subcommand("/rss") == ("list", "")
    assert rss._parse_rss_subcommand("/rss monitor AI") == ("monitor", "AI")
    assert rss._parse_rss_subcommand("/rss whatever") == ("help", "")

    assert schedule._parse_schedule_subcommand("/schedule") == ("list", "")
    assert schedule._parse_schedule_subcommand("/schedule delete 12") == (
        "delete",
        "12",
    )
    assert schedule._parse_schedule_subcommand("/schedule x") == ("help", "")

    assert remind._parse_remind_command("/remind") == ("help", "", "")
    assert remind._parse_remind_command("/remind help") == ("help", "", "")
    assert remind._parse_remind_command("/remind 10m 喝水") == (
        "set",
        "10m",
        "喝水",
    )

    assert deploy._parse_deploy_request("/deploy") == ("help", "")
    assert deploy._parse_deploy_request("/deploy help") == ("help", "")
    assert deploy._parse_deploy_request("/deploy run n8n") == ("run", "n8n")
    assert deploy._parse_deploy_request("/deploy n8n") == ("run", "n8n")


def test_skill_command_registration_is_converged():
    stock = _load_module(
        "skills/builtin/stock_watch/scripts/execute.py", "stock_watch_register_test"
    )
    rss = _load_module(
        "skills/builtin/rss_subscribe/scripts/execute.py", "rss_register_test"
    )
    schedule = _load_module(
        "skills/builtin/scheduler_manager/scripts/execute.py", "schedule_register_test"
    )
    remind = _load_module(
        "skills/builtin/reminder/scripts/execute.py", "remind_register_test"
    )
    deploy = _load_module(
        "skills/builtin/deployment_manager/scripts/execute.py", "deploy_register_test"
    )

    stock_manager = _FakeAdapterManager()
    stock.register_handlers(stock_manager)
    assert stock_manager.commands == ["stock"]

    rss_manager = _FakeAdapterManager()
    rss.register_handlers(rss_manager)
    assert rss_manager.commands == ["rss"]

    schedule_manager = _FakeAdapterManager()
    schedule.register_handlers(schedule_manager)
    assert schedule_manager.commands == ["schedule"]

    remind_manager = _FakeAdapterManager()
    remind.register_handlers(remind_manager)
    assert remind_manager.commands == ["remind"]

    deploy_manager = _FakeAdapterManager()
    deploy.register_handlers(deploy_manager)
    assert deploy_manager.commands == ["deploy"]
