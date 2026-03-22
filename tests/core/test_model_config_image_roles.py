from core.model_config import (
    ModelConfig,
    ModelManager,
    ModelsConfig,
    ProviderConfig,
    update_configured_model,
)
import core.model_config as model_config_module
import json


def test_models_config_splits_vision_and_image_generation():
    cfg = ModelsConfig(
        model={
            "primary": "openai/gpt-4.1-mini",
            "vision": "openai/gpt-4.1",
            "image_generation": "openai/gpt-image-1",
        }
    )

    assert cfg.get_vision_model() == "openai/gpt-4.1"
    assert cfg.get_image_generation_model() == "openai/gpt-image-1"
    assert cfg.get_image_model() == "openai/gpt-4.1"


def test_models_config_keeps_legacy_image_as_vision_alias():
    cfg = ModelsConfig(
        model={
            "primary": "openai/gpt-4.1-mini",
            "image": "openai/gpt-4.1",
        }
    )

    assert cfg.get_vision_model() == "openai/gpt-4.1"
    assert cfg.get_image_model() == "openai/gpt-4.1"
    assert cfg.get_image_generation_model() == ""


def test_model_config_lazy_loads_generation_model_from_file(tmp_path, monkeypatch):
    config_path = tmp_path / "models.json"
    config_path.write_text(
        """
{
  "model": {
    "primary": "demo/text",
    "image_generation": "demo/image-gen"
  },
  "providers": {
    "demo": {
      "baseUrl": "https://example.invalid/v1",
      "apiKey": "test-key",
      "models": [
        {"id": "text", "name": "text", "input": ["text"]},
        {"id": "image-gen", "name": "image-gen", "input": ["text"]}
      ]
    }
  }
}
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("MODELS_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(model_config_module, "_models_config", None)
    monkeypatch.setattr(model_config_module, "_model_manager", None)
    monkeypatch.setattr(model_config_module, "_primary_model", "")

    assert model_config_module.get_image_generation_model() == "demo/image-gen"


def test_model_config_primary_pool_failover_skips_failed_models(monkeypatch):
    cfg = ModelsConfig(
        model={"primary": "proxy/gpt-5.4"},
        models={
            "primary": {
                "proxy/gpt-5.4": {},
                "proxy/bailian/qwen3.5-flash": {},
            }
        },
        providers={
            "proxy": ProviderConfig(
                baseUrl="https://example.invalid/v1",
                apiKey="test-key",
                models=[
                    ModelConfig(id="gpt-5.4", name="gpt-5.4", input=["text"]),
                    ModelConfig(
                        id="bailian/qwen3.5-flash",
                        name="bailian/qwen3.5-flash",
                        input=["text"],
                    ),
                    ModelConfig(
                        id="gemini-3.1-flash-lite",
                        name="gemini-3.1-flash-lite",
                        input=["text"],
                    ),
                ],
            )
        },
    )
    manager = ModelManager(cfg, "proxy/gpt-5.4")

    monkeypatch.setattr(model_config_module, "_models_config", cfg)
    monkeypatch.setattr(model_config_module, "_model_manager", manager)
    monkeypatch.setattr(model_config_module, "_primary_model", "proxy/gpt-5.4")

    assert model_config_module.get_model_candidates_for_input("text") == [
        "proxy/gpt-5.4",
        "proxy/bailian/qwen3.5-flash",
    ]
    assert (
        model_config_module.get_model_for_input("text", pool_type="primary")
        == "proxy/gpt-5.4"
    )

    model_config_module.mark_model_failed("proxy/gpt-5.4")

    assert model_config_module.get_model_candidates_for_input("text") == [
        "proxy/bailian/qwen3.5-flash"
    ]
    assert (
        model_config_module.get_model_for_input("text", pool_type="primary")
        == "proxy/bailian/qwen3.5-flash"
    )


def test_update_configured_model_preserves_legacy_image_key(tmp_path, monkeypatch):
    config_path = tmp_path / "models.json"
    config_path.write_text(
        """
{
  "model": {
    "primary": "demo/text",
    "image": "demo/vision"
  },
  "providers": {
    "demo": {
      "baseUrl": "https://example.invalid/v1",
      "apiKey": "test-key",
      "models": [
        {"id": "text", "name": "text", "input": ["text"]},
        {"id": "vision", "name": "vision", "input": ["image"]},
        {"id": "vision-next", "name": "vision-next", "input": ["image"]}
      ]
    }
  }
}
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("MODELS_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(model_config_module, "_models_config", None)
    monkeypatch.setattr(model_config_module, "_model_manager", None)
    monkeypatch.setattr(model_config_module, "_primary_model", "")

    result = update_configured_model("vision", "demo/vision-next")
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert result["storage_key"] == "image"
    assert saved["model"]["image"] == "demo/vision-next"
    assert "vision" not in saved["model"]
    assert model_config_module.get_configured_model("vision") == "demo/vision-next"
