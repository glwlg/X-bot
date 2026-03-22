"""
模型配置模块 - 管理多模型配置、轮询和输入类型支持
"""

import os
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

_MODEL_POOL_ALIASES: dict[str, tuple[str, ...]] = {
    "image": ("image", "vision"),
    "vision": ("vision", "image"),
    "image_gen": ("image_gen", "image_generation"),
    "image_generation": ("image_generation", "image_gen"),
}

_MODEL_ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "primary": ("primary", "main"),
    "routing": ("routing", "route", "router"),
    "vision": ("vision", "image"),
    "image_generation": ("image_generation", "image_gen", "draw", "drawing"),
    "voice": ("voice", "audio"),
}

_MODEL_ROLE_STORAGE_KEYS: dict[str, tuple[str, ...]] = {
    "primary": ("primary",),
    "routing": ("routing",),
    "vision": ("vision", "image"),
    "image_generation": ("image_generation", "image_gen"),
    "voice": ("voice",),
}


def _pool_aliases(pool_type: str) -> tuple[str, ...]:
    normalized = str(pool_type or "primary").strip().lower() or "primary"
    return _MODEL_POOL_ALIASES.get(normalized, (normalized,))


def normalize_model_role(role: str) -> str:
    """将模型角色别名归一化为统一名称。"""
    normalized = str(role or "").strip().lower()
    if not normalized:
        return ""
    for canonical, aliases in _MODEL_ROLE_ALIASES.items():
        if normalized == canonical or normalized in aliases:
            return canonical
    return ""


def resolve_models_config_path(config_path: Optional[str] = None) -> Path:
    """解析 models.json 路径。"""
    raw_path = str(
        config_path or os.getenv("MODELS_CONFIG_PATH", "config/models.json")
    ).strip() or "config/models.json"
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = path.resolve()
    return path


def _resolve_model_storage_key(role: str, model_section: dict[str, Any]) -> str:
    normalized_role = normalize_model_role(role)
    if not normalized_role:
        raise ValueError(f"Unsupported model role: {role}")
    candidates = _MODEL_ROLE_STORAGE_KEYS.get(normalized_role, (normalized_role,))
    for key in candidates:
        if key in model_section:
            return key
    return candidates[0]


@dataclass
class ModelCost:
    """模型成本配置"""

    input: float = 0
    output: float = 0
    cacheRead: float = 0
    cacheWrite: float = 0


@dataclass
class ModelConfig:
    """单个模型配置"""

    id: str
    name: str
    reasoning: bool = False
    input: list[str] = field(
        default_factory=lambda: ["text"]
    )  # 支持的输入类型: text, image
    cost: ModelCost = field(default_factory=ModelCost)
    contextWindow: int = 1000000
    maxTokens: int = 65536

    def supports_input(self, input_type: str) -> bool:
        """检查模型是否支持指定的输入类型"""
        return input_type in self.input


@dataclass
class ProviderConfig:
    """模型提供者配置"""

    baseUrl: str
    apiKey: str
    api: str = "openai-completions"
    models: list[ModelConfig] = field(default_factory=list)


@dataclass
class ModelsConfig:
    """模型配置主类"""

    mode: str = "merge"
    model: dict[str, str] = field(default_factory=dict)  # primary, routing, image等
    models: dict[str, dict] = field(default_factory=dict)  # 模型池配置
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    _model_index: dict[str, ModelConfig] = field(default_factory=dict, repr=False)
    _provider_clients: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        """构建模型索引"""
        self._model_index.clear()
        for provider_name, provider in self.providers.items():
            for model in provider.models:
                model_key = f"{provider_name}/{model.id}"
                self._model_index[model_key] = model

    def get_model(self, model_key: str) -> Optional[ModelConfig]:
        """获取模型配置"""
        return self._model_index.get(model_key)

    def list_models(self) -> list[str]:
        """列出所有可用模型"""
        return list(self._model_index.keys())

    def get_models_by_input(self, input_type: str) -> list[str]:
        """获取支持指定输入类型的所有模型"""
        return [
            key
            for key, model in self._model_index.items()
            if model.supports_input(input_type)
        ]

    def get_primary_model(self) -> str:
        """获取主模型"""
        return self.model.get("primary", "")

    def get_routing_model(self) -> str:
        """获取路由模型"""
        return self.model.get("routing", "")

    def get_vision_model(self) -> str:
        """获取多模态视觉理解模型。

        优先使用 `model.vision`。为兼容旧配置，缺失时回退到 `model.image`。
        """
        return self.model.get("vision", "") or self.model.get("image", "")

    def get_image_generation_model(self) -> str:
        """获取图片生成模型。"""
        return self.model.get("image_generation", "") or self.model.get(
            "image_gen", ""
        )

    def get_image_model(self) -> str:
        """兼容旧接口：返回视觉理解模型。"""
        return self.get_vision_model()

    def get_voice_model(self) -> str:
        """获取语音模型"""
        return self.model.get("voice", "")

    def get_model_pool(self, pool_type: str = "primary") -> list[str]:
        """获取指定类型的模型池"""
        for candidate in _pool_aliases(pool_type):
            pool = self.models.get(candidate, {})
            if isinstance(pool, dict) and pool:
                return [str(key) for key in pool.keys() if str(key).strip()]
            if isinstance(pool, list) and pool:
                return [str(item) for item in pool if str(item).strip()]
        return []

    def is_model_available(self, model_key: str, pool_type: str = "primary") -> bool:
        """检查模型是否在指定类型的模型池中"""
        return model_key in self.get_model_pool(pool_type)


class ModelManager:
    """模型管理器 - 处理模型轮询、失效切换"""

    def __init__(self, config: ModelsConfig, primary_model: str):
        self.config = config
        self.primary_model = primary_model
        self._current_model = primary_model
        self._failed_models: set[str] = set()
        self._model_order: list[str] = []
        self._initialize_model_order()

    def _initialize_model_order(self):
        """初始化模型轮询顺序"""
        all_models = self.config.list_models()
        # 将primary模型放在最前面
        if self.primary_model in all_models:
            self._model_order = [self.primary_model] + [
                m for m in all_models if m != self.primary_model
            ]
        else:
            self._model_order = all_models
        logger.info(f"[ModelManager] Model order: {self._model_order}")

    def get_current_model(self) -> str:
        """获取当前模型"""
        return self._current_model

    def get_current_model_config(self) -> Optional[ModelConfig]:
        """获取当前模型配置"""
        return self.config.get_model(self._current_model)

    def get_model_id(self, model_key: Optional[str] = None) -> str:
        """获取模型ID（去掉provider前缀）

        Args:
            model_key: 模型键，如 'bailian/qwen3.5-plus'，不传则使用当前模型

        Returns:
            模型ID，如 'qwen3.5-plus'
        """
        key = model_key or self._current_model
        if "/" in key:
            return key.split("/", 1)[1]
        return key

    def get_provider_name(self, model_key: Optional[str] = None) -> str:
        """获取provider名称

        Args:
            model_key: 模型键，如 'bailian/qwen3.5-plus'，不传则使用当前模型

        Returns:
            provider名称，如 'bailian'
        """
        key = model_key or self._current_model
        if "/" in key:
            return key.split("/", 1)[0]
        return ""

    def get_provider_config(
        self, model_key: Optional[str] = None
    ) -> Optional[ProviderConfig]:
        """获取模型对应的provider配置"""
        provider_name = self.get_provider_name(model_key)
        return self.config.providers.get(provider_name)

    def mark_failed(self, model_key: str):
        """标记模型失效"""
        self._failed_models.add(model_key)
        logger.warning(f"[ModelManager] Model failed: {model_key}")

    def mark_success(self, model_key: str):
        """标记模型成功"""
        if model_key in self._failed_models:
            self._failed_models.discard(model_key)
            logger.info(f"[ModelManager] Model recovered: {model_key}")

    def get_candidate_models(
        self,
        required_input_type: str = "text",
        pool_type: str = "primary",
        *,
        preferred_model: Optional[str] = None,
        include_failed: bool = False,
    ) -> list[str]:
        """获取指定池内、支持输入类型的候选模型列表。"""
        pool_models = [
            model_key
            for model_key in self.config.get_model_pool(pool_type)
            if self.config.get_model(model_key)
        ]
        base_order = pool_models or list(self._model_order)

        preferred = str(preferred_model or "").strip()
        if preferred not in base_order:
            preferred = ""
        if not preferred and self._current_model in base_order:
            preferred = self._current_model
        if not preferred and self.primary_model in base_order:
            preferred = self.primary_model

        ordered_models: list[str] = []
        if preferred:
            ordered_models.append(preferred)
        for model_key in base_order:
            if model_key and model_key not in ordered_models:
                ordered_models.append(model_key)

        candidates: list[str] = []
        for model_key in ordered_models:
            model_config = self.config.get_model(model_key)
            if not model_config or not model_config.supports_input(required_input_type):
                continue
            if not include_failed and model_key in self._failed_models:
                continue
            candidates.append(model_key)
        return candidates

    def get_next_available_model(
        self,
        required_input_type: str = "text",
        pool_type: str = "primary",
    ) -> Optional[str]:
        """获取下一个可用的模型（支持指定输入类型）"""
        candidates = self.get_candidate_models(
            required_input_type=required_input_type,
            pool_type=pool_type,
        )
        if candidates:
            selected = candidates[0]
            if selected != self._current_model:
                self._current_model = selected
                logger.info(f"[ModelManager] Switching to model: {selected}")
            return selected

        logger.error("[ModelManager] No available model found")
        return None

    def reset(self):
        """重置所有失败状态"""
        self._failed_models.clear()
        self._current_model = self.primary_model
        self._initialize_model_order()


# 全局配置实例
_models_config: Optional[ModelsConfig] = None
_model_manager: Optional[ModelManager] = None
_primary_model: str = ""


def _parse_models_config_data(data: dict[str, Any]) -> ModelsConfig:
    """将原始 JSON 数据解析为 ModelsConfig。"""
    providers = {}
    for provider_name, provider_data in data.get("providers", {}).items():
        models = []
        for model_data in provider_data.get("models", []):
            cost_data = model_data.get("cost", {})
            model = ModelConfig(
                id=model_data["id"],
                name=model_data.get("name", model_data["id"]),
                reasoning=model_data.get("reasoning", False),
                input=model_data.get("input", ["text"]),
                cost=ModelCost(
                    input=cost_data.get("input", 0),
                    output=cost_data.get("output", 0),
                    cacheRead=cost_data.get("cacheRead", 0),
                    cacheWrite=cost_data.get("cacheWrite", 0),
                ),
                contextWindow=model_data.get("contextWindow", 1000000),
                maxTokens=model_data.get("maxTokens", 65536),
            )
            models.append(model)

        providers[provider_name] = ProviderConfig(
            baseUrl=provider_data["baseUrl"],
            apiKey=provider_data.get("apiKey", ""),
            api=provider_data.get("api", "openai-completions"),
            models=models,
        )

    return ModelsConfig(
        mode=data.get("mode", "merge"),
        model=data.get("model", {}),
        models=data.get("models", {}),
        providers=providers,
    )


def _ensure_models_loaded() -> Optional[ModelsConfig]:
    global _models_config
    if _models_config is None:
        load_models_config()
    return _models_config


def load_models_config(
    config_path: Optional[str] = None,
    *,
    force_reload: bool = False,
) -> ModelsConfig:
    """加载模型配置并自动初始化ModelManager"""
    global _models_config, _model_manager, _primary_model

    if _models_config is not None and not force_reload:
        return _models_config

    config_file = resolve_models_config_path(config_path)
    _model_manager = None
    _primary_model = ""

    if not config_file.exists():
        logger.warning(f"[ModelManager] Config file not found: {config_file}")
        _models_config = ModelsConfig()
        return _models_config

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        _models_config = _parse_models_config_data(data)

        logger.info(
            f"[ModelManager] Loaded {len(_models_config.list_models())} models from {config_file}"
        )

        # 自动初始化ModelManager（使用配置中的primary模型）
        primary_model = _models_config.get_primary_model()
        if primary_model:
            _primary_model = primary_model
            _model_manager = ModelManager(_models_config, primary_model)
            logger.info(
                f"[ModelManager] Auto-initialized with primary model: {primary_model}"
            )

        return _models_config

    except Exception as e:
        logger.error(f"[ModelManager] Failed to load config: {e}")
        _models_config = ModelsConfig()
        return _models_config


def reload_models_config(config_path: Optional[str] = None) -> ModelsConfig:
    """强制重载模型配置。"""
    return load_models_config(config_path=config_path, force_reload=True)


def init_model_manager(
    primary_model: str, config_path: Optional[str] = None
) -> ModelManager:
    """初始化模型管理器"""
    global _model_manager, _primary_model

    config = load_models_config(config_path)
    _primary_model = primary_model
    _model_manager = ModelManager(config, primary_model)

    logger.info(f"[ModelManager] Initialized with primary model: {primary_model}")
    return _model_manager


def get_model_manager() -> Optional[ModelManager]:
    """获取模型管理器实例"""
    return _model_manager


def get_models_config() -> Optional[ModelsConfig]:
    """获取模型配置实例"""
    return _models_config


def get_configured_model(role: str) -> str:
    """获取指定角色当前配置的模型。"""
    normalized_role = normalize_model_role(role)
    if not normalized_role:
        return ""
    _ensure_models_loaded()
    if _models_config is None:
        return ""
    if normalized_role == "primary":
        return _models_config.get_primary_model()
    if normalized_role == "routing":
        return _models_config.get_routing_model()
    if normalized_role == "vision":
        return _models_config.get_vision_model()
    if normalized_role == "image_generation":
        return _models_config.get_image_generation_model()
    if normalized_role == "voice":
        return _models_config.get_voice_model()
    return ""


def update_configured_model(
    role: str,
    model_key: str,
    *,
    config_path: Optional[str] = None,
) -> dict[str, str]:
    """更新指定角色的模型配置并写回 models.json。"""
    normalized_role = normalize_model_role(role)
    if not normalized_role:
        raise ValueError(f"Unsupported model role: {role}")

    normalized_model_key = str(model_key or "").strip()
    if not normalized_model_key:
        raise ValueError("Model key is required")

    config_file = resolve_models_config_path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_file}")

    with open(config_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("Invalid models config root")

    parsed = _parse_models_config_data(data)
    if not parsed.get_model(normalized_model_key):
        raise ValueError(f"Unknown model key: {normalized_model_key}")

    raw_model_section = data.get("model", {})
    if raw_model_section is None:
        raw_model_section = {}
    if not isinstance(raw_model_section, dict):
        raise ValueError("Invalid models config: model must be an object")

    storage_key = _resolve_model_storage_key(normalized_role, raw_model_section)
    previous = str(raw_model_section.get(storage_key, "") or "")
    raw_model_section[storage_key] = normalized_model_key
    data["model"] = raw_model_section

    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

    reload_models_config(str(config_file))
    return {
        "role": normalized_role,
        "storage_key": storage_key,
        "previous": previous,
        "current": normalized_model_key,
        "config_path": str(config_file),
    }


def get_primary_model() -> str:
    """获取主模型"""
    return _primary_model


# 便捷函数 - 兼容现有代码
def get_current_model() -> str:
    """获取当前使用的模型（用于AiService）"""
    _ensure_models_loaded()
    if _model_manager:
        return _model_manager.get_current_model()
    return _primary_model


def get_model_for_input(input_type: str = "text", pool_type: str = "primary") -> str:
    """获取支持指定输入类型的模型"""
    _ensure_models_loaded()
    if _model_manager:
        model = _model_manager.get_next_available_model(input_type, pool_type)
        if model:
            return model
    return _primary_model


def get_model_candidates_for_input(
    input_type: str = "text",
    pool_type: str = "primary",
    *,
    preferred_model: Optional[str] = None,
    include_failed: bool = False,
) -> list[str]:
    """获取指定输入类型的候选模型列表。"""
    _ensure_models_loaded()
    if _model_manager:
        return _model_manager.get_candidate_models(
            required_input_type=input_type,
            pool_type=pool_type,
            preferred_model=preferred_model,
            include_failed=include_failed,
        )
    if _models_config is None:
        return []

    pool_models = [
        model_key
        for model_key in _models_config.get_model_pool(pool_type)
        if _models_config.get_model(model_key)
    ]
    base_order = pool_models or _models_config.list_models()
    candidates: list[str] = []
    for model_key in base_order:
        model_config = _models_config.get_model(model_key)
        if not model_config or not model_config.supports_input(input_type):
            continue
        candidates.append(model_key)
    return candidates


def mark_model_failed(model_key: str) -> None:
    """标记模型失败，用于后续请求跳过该模型。"""
    _ensure_models_loaded()
    if _model_manager and model_key:
        _model_manager.mark_failed(model_key)


def mark_model_success(model_key: str) -> None:
    """标记模型恢复成功。"""
    _ensure_models_loaded()
    if _model_manager and model_key:
        _model_manager.mark_success(model_key)


def get_model_id_for_api(model_key: Optional[str] = None) -> str:
    """获取用于API调用的模型ID（去掉provider前缀）

    这是关键函数：在调用OpenAI API时使用

    Example:
        model_key = 'bailian/qwen3.5-plus'
        returns = 'qwen3.5-plus'
    """
    _ensure_models_loaded()
    if _model_manager:
        return _model_manager.get_model_id(model_key)

    # fallback: 如果传入的是完整key
    key = model_key or _primary_model
    if "/" in key:
        return key.split("/", 1)[1]
    return key


def get_api_key_for_model(model_key: Optional[str] = None) -> str:
    """获取模型对应的API Key"""
    _ensure_models_loaded()
    if _model_manager:
        provider_config = _model_manager.get_provider_config(model_key)
        if provider_config:
            return provider_config.apiKey
    return ""


def get_base_url_for_model(model_key: Optional[str] = None) -> Optional[str]:
    """获取模型对应的baseUrl"""
    _ensure_models_loaded()
    if _model_manager:
        provider_config = _model_manager.get_provider_config(model_key)
        if provider_config:
            return provider_config.baseUrl
    return None


def get_routing_model() -> str:
    """获取路由模型"""
    _ensure_models_loaded()
    if _models_config:
        return _models_config.get_routing_model()
    return _primary_model


def get_voice_model() -> str:
    """获取语音模型"""
    _ensure_models_loaded()
    if _models_config:
        return _models_config.get_voice_model()
    return _primary_model


def get_vision_model() -> str:
    """获取多模态视觉理解模型。"""
    _ensure_models_loaded()
    if _models_config:
        return _models_config.get_vision_model()
    return _primary_model


def get_image_generation_model() -> str:
    """获取图片生成模型。"""
    _ensure_models_loaded()
    if _models_config:
        return _models_config.get_image_generation_model()
    return ""


def get_image_model() -> str:
    """兼容旧接口：返回视觉理解模型。"""
    return get_vision_model()
