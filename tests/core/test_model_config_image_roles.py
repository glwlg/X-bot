from core.model_config import ModelsConfig
import core.model_config as model_config_module


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
