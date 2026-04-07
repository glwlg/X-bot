from core.model_config import (
    ModelConfig,
    ModelManager,
    ModelsConfig,
    ProviderConfig,
    get_configured_model,
    select_model_for_role,
    update_configured_model,
)
import core.llm_usage_store as llm_usage_module
import core.model_config as model_config_module
import json
import os


def _usage_row(**overrides):
    row = {
        "requests": 0,
        "success_requests": 0,
        "failed_requests": 0,
        "usage_requests": 0,
        "missing_usage_requests": 0,
        "estimated_token_requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "image_outputs": 0,
        "cache_hit_requests": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    row.update(overrides)
    return row


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
    monkeypatch.setattr(model_config_module, "_loaded_config_path", None)
    monkeypatch.setattr(model_config_module, "_loaded_config_mtime_ns", None)

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
    monkeypatch.setattr(model_config_module, "_loaded_config_path", None)
    monkeypatch.setattr(model_config_module, "_loaded_config_mtime_ns", None)

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


def test_model_config_primary_pool_round_robin_load_balances(monkeypatch):
    cfg = ModelsConfig(
        model={"primary": "demo/a"},
        models={
            "primary": {
                "demo/a": {},
                "demo/b": {},
                "demo/c": {},
            }
        },
        selection={"primary": {"strategy": "round_robin"}},
        providers={
            "demo": ProviderConfig(
                baseUrl="https://example.invalid/v1",
                apiKey="test-key",
                models=[
                    ModelConfig(id="a", name="a", input=["text"]),
                    ModelConfig(id="b", name="b", input=["text"]),
                    ModelConfig(id="c", name="c", input=["text"]),
                ],
            )
        },
    )
    manager = ModelManager(cfg, "demo/a")

    monkeypatch.setattr(model_config_module, "_models_config", cfg)
    monkeypatch.setattr(model_config_module, "_model_manager", manager)
    monkeypatch.setattr(model_config_module, "_primary_model", "demo/a")
    monkeypatch.setattr(model_config_module, "_loaded_config_path", None)
    monkeypatch.setattr(model_config_module, "_loaded_config_mtime_ns", None)

    assert model_config_module.get_model_for_input("text", pool_type="primary") == "demo/a"
    assert model_config_module.get_model_for_input("text", pool_type="primary") == "demo/b"
    assert model_config_module.get_model_for_input("text", pool_type="primary") == "demo/c"
    assert model_config_module.get_model_for_input("text", pool_type="primary") == "demo/a"


def test_model_config_primary_pool_uses_least_usage_strategy(monkeypatch):
    cfg = ModelsConfig(
        model={"primary": "demo/a"},
        models={
            "primary": {
                "demo/a": {},
                "demo/b": {},
            }
        },
        selection={"primary": {"strategy": "least_usage"}},
        providers={
            "demo": ProviderConfig(
                baseUrl="https://example.invalid/v1",
                apiKey="test-key",
                models=[
                    ModelConfig(id="a", name="a", input=["text"]),
                    ModelConfig(id="b", name="b", input=["text"]),
                ],
            )
        },
    )
    manager = ModelManager(cfg, "demo/a")

    monkeypatch.setattr(
        llm_usage_module.llm_usage_store,
        "summarize_models",
        lambda model_keys, day=None: {
            "demo/a": _usage_row(total_tokens=900, requests=9),
            "demo/b": _usage_row(total_tokens=120, requests=2),
        },
    )
    monkeypatch.setattr(model_config_module, "_models_config", cfg)
    monkeypatch.setattr(model_config_module, "_model_manager", manager)
    monkeypatch.setattr(model_config_module, "_primary_model", "demo/a")
    monkeypatch.setattr(model_config_module, "_loaded_config_path", None)
    monkeypatch.setattr(model_config_module, "_loaded_config_mtime_ns", None)

    assert model_config_module.get_model_candidates_for_input("text", "primary") == [
        "demo/b",
        "demo/a",
    ]
    assert model_config_module.get_current_model() == "demo/b"


def test_model_config_primary_pool_switches_when_daily_token_limit_reached(monkeypatch):
    cfg = ModelsConfig(
        model={"primary": "demo/a"},
        models={
            "primary": {
                "demo/a": {},
                "demo/b": {},
            }
        },
        providers={
            "demo": ProviderConfig(
                baseUrl="https://example.invalid/v1",
                apiKey="test-key",
                models=[
                    ModelConfig(
                        id="a",
                        name="a",
                        input=["text"],
                        limits=model_config_module.ModelLimits(dailyTokens=1000),
                    ),
                    ModelConfig(
                        id="b",
                        name="b",
                        input=["text"],
                        limits=model_config_module.ModelLimits(dailyTokens=5000),
                    ),
                ],
            )
        },
    )
    manager = ModelManager(cfg, "demo/a")

    monkeypatch.setattr(
        llm_usage_module.llm_usage_store,
        "summarize_models",
        lambda model_keys, day=None: {
            "demo/a": _usage_row(total_tokens=1000, requests=12),
            "demo/b": _usage_row(total_tokens=200, requests=3),
        },
    )
    monkeypatch.setattr(model_config_module, "_models_config", cfg)
    monkeypatch.setattr(model_config_module, "_model_manager", manager)
    monkeypatch.setattr(model_config_module, "_primary_model", "demo/a")
    monkeypatch.setattr(model_config_module, "_loaded_config_path", None)
    monkeypatch.setattr(model_config_module, "_loaded_config_mtime_ns", None)

    assert model_config_module.get_model_candidates_for_input("text", "primary") == [
        "demo/b"
    ]
    assert model_config_module.get_current_model() == "demo/b"
    assert model_config_module.get_model_for_input("text", pool_type="primary") == "demo/b"


def test_model_config_image_generation_switches_when_daily_image_limit_reached(monkeypatch):
    cfg = ModelsConfig(
        model={"primary": "demo/text", "image_generation": "demo/image-a"},
        models={
            "primary": {"demo/text": {}},
            "image_generation": {
                "demo/image-a": {},
                "demo/image-b": {},
            },
        },
        providers={
            "demo": ProviderConfig(
                baseUrl="https://example.invalid/v1",
                apiKey="test-key",
                models=[
                    ModelConfig(id="text", name="text", input=["text"]),
                    ModelConfig(
                        id="image-a",
                        name="image-a",
                        input=["text"],
                        output=["image"],
                        limits=model_config_module.ModelLimits(dailyImages=3),
                    ),
                    ModelConfig(
                        id="image-b",
                        name="image-b",
                        input=["text"],
                        output=["image"],
                        limits=model_config_module.ModelLimits(dailyImages=10),
                    ),
                ],
            )
        },
    )
    manager = ModelManager(cfg, "demo/text")

    monkeypatch.setattr(
        llm_usage_module.llm_usage_store,
        "summarize_models",
        lambda model_keys, day=None: {
            "demo/image-a": _usage_row(image_outputs=3, requests=3),
            "demo/image-b": _usage_row(image_outputs=1, requests=1),
        },
    )
    monkeypatch.setattr(model_config_module, "_models_config", cfg)
    monkeypatch.setattr(model_config_module, "_model_manager", manager)
    monkeypatch.setattr(model_config_module, "_primary_model", "demo/text")
    monkeypatch.setattr(model_config_module, "_loaded_config_path", None)
    monkeypatch.setattr(model_config_module, "_loaded_config_mtime_ns", None)

    assert model_config_module.get_image_generation_model() == "demo/image-b"
    assert model_config_module.get_model_candidates_for_input("text", "image_generation") == [
        "demo/image-b"
    ]
    assert select_model_for_role("image_generation") == "demo/image-b"


def test_model_config_image_request_does_not_fallback_to_primary(monkeypatch):
    cfg = ModelsConfig(
        model={"primary": "proxy/gpt-5.4", "image": "proxy/gpt-5.4"},
        models={"image": {"proxy/gpt-5.4": {}}},
        providers={
            "proxy": ProviderConfig(
                baseUrl="https://example.invalid/v1",
                apiKey="test-key",
                models=[
                    ModelConfig(id="gpt-5.4", name="gpt-5.4", input=["text"]),
                ],
            )
        },
    )
    manager = ModelManager(cfg, "proxy/gpt-5.4")

    monkeypatch.setattr(model_config_module, "_models_config", cfg)
    monkeypatch.setattr(model_config_module, "_model_manager", manager)
    monkeypatch.setattr(model_config_module, "_primary_model", "proxy/gpt-5.4")
    monkeypatch.setattr(model_config_module, "_loaded_config_path", None)
    monkeypatch.setattr(model_config_module, "_loaded_config_mtime_ns", None)

    assert model_config_module.get_model_candidates_for_input("image", "vision") == []
    assert model_config_module.get_model_for_input("image", pool_type="vision") == ""


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
    monkeypatch.setattr(model_config_module, "_loaded_config_path", None)
    monkeypatch.setattr(model_config_module, "_loaded_config_mtime_ns", None)

    result = update_configured_model("vision", "demo/vision-next")
    saved = json.loads(config_path.read_text(encoding="utf-8"))

    assert result["storage_key"] == "image"
    assert saved["model"]["image"] == "demo/vision-next"
    assert "vision" not in saved["model"]
    assert model_config_module.get_configured_model("vision") == "demo/vision-next"


def test_model_config_auto_reloads_after_file_change(tmp_path, monkeypatch):
    config_path = tmp_path / "models.json"
    config_path.write_text(
        """
{
  "model": {
    "primary": "demo/text",
    "vision": "demo/vision-a"
  },
  "models": {
    "primary": {
      "demo/text": {}
    },
    "vision": {
      "demo/vision-a": {}
    }
  },
  "providers": {
    "demo": {
      "baseUrl": "https://example.invalid/v1",
      "apiKey": "test-key",
      "models": [
        {"id": "text", "name": "text", "input": ["text"]},
        {"id": "vision-a", "name": "vision-a", "input": ["image"]},
        {"id": "vision-b", "name": "vision-b", "input": ["image"]}
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
    monkeypatch.setattr(model_config_module, "_loaded_config_path", None)
    monkeypatch.setattr(model_config_module, "_loaded_config_mtime_ns", None)

    assert get_configured_model("vision") == "demo/vision-a"

    original_mtime_ns = config_path.stat().st_mtime_ns
    config_path.write_text(
        """
{
  "model": {
    "primary": "demo/text",
    "vision": "demo/vision-b"
  },
  "models": {
    "primary": {
      "demo/text": {}
    },
    "vision": {
      "demo/vision-b": {}
    }
  },
  "providers": {
    "demo": {
      "baseUrl": "https://example.invalid/v1",
      "apiKey": "test-key",
      "models": [
        {"id": "text", "name": "text", "input": ["text"]},
        {"id": "vision-a", "name": "vision-a", "input": ["image"]},
        {"id": "vision-b", "name": "vision-b", "input": ["image"]}
      ]
    }
  }
}
""".strip(),
        encoding="utf-8",
    )
    os.utime(
        config_path,
        ns=(original_mtime_ns + 1_000_000, original_mtime_ns + 1_000_000),
    )

    assert get_configured_model("vision") == "demo/vision-b"
