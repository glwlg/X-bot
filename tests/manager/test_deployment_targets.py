from pathlib import Path

from manager.dev.deployment_targets import DeploymentTargets


def test_deployment_targets_loads_external_config(tmp_path: Path):
    config_path = tmp_path / "deployment_targets.yaml"
    config_path.write_text(
        """targets:
  worker:
    service: worker-green
    image: x-bot-worker-green
""",
        encoding="utf-8",
    )

    targets = DeploymentTargets(config_path=str(config_path))
    worker = targets.get("worker")
    manager = targets.get("manager")

    assert worker == {
        "service": "worker-green",
        "image": "x-bot-worker-green",
    }
    assert manager == {
        "service": "x-bot",
        "image": "x-bot-manager",
    }
