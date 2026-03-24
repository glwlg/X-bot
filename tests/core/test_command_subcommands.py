import importlib.util
from pathlib import Path

from handlers.heartbeat_handlers import _parse_subcommand as parse_heartbeat_subcommand
from handlers.model_handlers import _parse_subcommand as parse_model_subcommand
from handlers.usage_handlers import _parse_subcommand as parse_usage_subcommand


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


def test_heartbeat_default_subcommands():
    assert parse_heartbeat_subcommand("/heartbeat") == ("list", "")
    assert parse_heartbeat_subcommand("/heartbeat help") == ("help", "")
    assert parse_model_subcommand("/model") == ("show", "")
    assert parse_model_subcommand("/model help") == ("help", "")
    assert parse_model_subcommand("/model use demo/fallback") == (
        "use",
        "demo/fallback",
    )
    assert parse_model_subcommand("/model use primary demo/fallback") == (
        "use",
        "primary demo/fallback",
    )
    assert parse_usage_subcommand("/usage") == ("show", "")
    assert parse_usage_subcommand("/usage help") == ("help", "")
    assert parse_usage_subcommand("/usage today") == ("today", "")
    assert parse_usage_subcommand("/usage reset") == ("reset", "")


def test_stock_rss_schedule_deploy_subcommand_parsers():
    stock = _load_module(
        "extension/skills/learned/stock_watch/scripts/execute.py", "stock_watch_subcmd_test"
    )
    rss = _load_module(
        "extension/skills/learned/rss_subscribe/scripts/execute.py", "rss_subcmd_test"
    )
    schedule = _load_module(
        "extension/skills/builtin/scheduler_manager/scripts/execute.py",
        "schedule_subcmd_test",
    )
    deploy = _load_module(
        "extension/skills/builtin/deployment_manager/scripts/execute.py", "deploy_subcmd_test"
    )
    account = _load_module(
        "extension/skills/builtin/account_manager/scripts/execute.py", "account_subcmd_test"
    )
    daily = _load_module(
        "extension/skills/learned/daily_query/scripts/execute.py", "daily_subcmd_test"
    )
    remind = _load_module(
        "extension/skills/learned/reminder/scripts/execute.py", "remind_subcmd_test"
    )
    download = _load_module(
        "extension/skills/builtin/download_video/scripts/execute.py", "download_subcmd_test"
    )

    assert stock._parse_stock_subcommand("/stock") == ("menu", "")
    assert stock._parse_stock_subcommand("/stock add 茅台") == ("add", "茅台")
    assert stock._parse_stock_subcommand("/stock unknown") == ("help", "")

    assert rss._parse_rss_subcommand("/rss") == ("menu", "")
    assert rss._parse_rss_subcommand("/rss add https://example.com/rss.xml") == (
        "add",
        "https://example.com/rss.xml",
    )
    assert rss._parse_rss_subcommand("/rss whatever") == ("help", "")

    assert schedule._parse_schedule_subcommand("/schedule") == ("menu", "")
    assert schedule._parse_schedule_subcommand("/schedule delete 12") == (
        "delete",
        "12",
    )
    assert schedule._parse_schedule_subcommand("/schedule x") == ("help", "")

    assert deploy._parse_deploy_request("/deploy") == ("menu", "")
    assert deploy._parse_deploy_request("/deploy help") == ("help", "")
    assert deploy._parse_deploy_request("/deploy status") == ("status", "")
    assert deploy._parse_deploy_request("/deploy run n8n") == ("run", "n8n")
    assert deploy._parse_deploy_request("/deploy n8n") == ("run", "n8n")

    assert account._parse_account_subcommand("/account") == ("menu", "", "")
    assert account._parse_account_subcommand("/account github") == ("get", "github", "")
    assert account._parse_account_subcommand("/account add github username=alice") == (
        "add",
        "github",
        "username=alice",
    )

    assert daily._parse_daily_subcommand("/daily") == ("menu", "")
    assert daily._parse_daily_subcommand("/daily weather 无锡") == ("weather", "无锡")
    assert daily._parse_daily_subcommand("/daily x") == ("help", "")

    assert remind._parse_remind_command("/remind 10m 喝水") == ("set", "10m", "喝水")
    assert remind._parse_remind_command("/remind") == ("help", "", "")

    assert download._parse_download_command("/download") == ("help", "")
    assert download._parse_download_command("/download audio https://example.com") == (
        "audio",
        "https://example.com",
    )
    assert download._parse_download_command("/download https://example.com") == (
        "video",
        "https://example.com",
    )


def test_skill_command_registration_is_converged():
    stock = _load_module(
        "extension/skills/learned/stock_watch/scripts/execute.py", "stock_watch_register_test"
    )
    rss = _load_module(
        "extension/skills/learned/rss_subscribe/scripts/execute.py", "rss_register_test"
    )
    schedule = _load_module(
        "extension/skills/builtin/scheduler_manager/scripts/execute.py", "schedule_register_test"
    )
    deploy = _load_module(
        "extension/skills/builtin/deployment_manager/scripts/execute.py", "deploy_register_test"
    )
    account = _load_module(
        "extension/skills/builtin/account_manager/scripts/execute.py", "account_register_test"
    )
    daily = _load_module(
        "extension/skills/learned/daily_query/scripts/execute.py", "daily_register_test"
    )
    remind = _load_module(
        "extension/skills/learned/reminder/scripts/execute.py", "remind_register_test"
    )
    download = _load_module(
        "extension/skills/builtin/download_video/scripts/execute.py", "download_register_test"
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

    deploy_manager = _FakeAdapterManager()
    deploy.register_handlers(deploy_manager)
    assert deploy_manager.commands == ["deploy"]

    account_manager = _FakeAdapterManager()
    account.register_handlers(account_manager)
    assert account_manager.commands == ["account"]

    daily_manager = _FakeAdapterManager()
    daily.register_handlers(daily_manager)
    assert daily_manager.commands == ["daily"]

    remind_manager = _FakeAdapterManager()
    remind.register_handlers(remind_manager)
    assert remind_manager.commands == ["remind"]

    download_manager = _FakeAdapterManager()
    download.register_handlers(download_manager)
    assert download_manager.commands == ["download"]
