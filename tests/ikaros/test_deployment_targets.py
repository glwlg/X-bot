from pathlib import Path

from ikaros.dev.deployment_targets import DeploymentTargets


def test_deployment_targets_loads_external_config(tmp_path: Path):
    config_path = tmp_path / "deployment_targets.yaml"
    config_path.write_text(
        """targets:
  api:
    service: ikaros-api-blue
    image: ikaros-api-blue
""",
        encoding="utf-8",
    )

    targets = DeploymentTargets(config_path=str(config_path))
    ikaros = targets.get("ikaros")
    api = targets.get("api")

    assert ikaros == {
        "service": "ikaros",
        "image": "ikaros-core",
    }
    assert api == {
        "service": "ikaros-api-blue",
        "image": "ikaros-api-blue",
    }
