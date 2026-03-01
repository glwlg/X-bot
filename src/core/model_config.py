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

    def get_image_model(self) -> str:
        """获取画图模型"""
        return self.model.get("image", "")

    def get_voice_model(self) -> str:
        """获取语音模型"""
        return self.model.get("voice", "")

    def get_model_pool(self, pool_type: str = "primary") -> list[str]:
        """获取指定类型的模型池"""
        pool = self.models.get(pool_type, {})
        return list(pool.keys())

    def is_model_available(self, model_key: str, pool_type: str = "primary") -> bool:
        """检查模型是否在指定类型的模型池中"""
        return model_key in self.models.get(pool_type, {})


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

    def get_next_available_model(
        self, required_input_type: str = "text"
    ) -> Optional[str]:
        """获取下一个可用的模型（支持指定输入类型）"""
        # 优先尝试当前模型
        if self._current_model not in self._failed_models:
            current_config = self.config.get_model(self._current_model)
            if current_config and current_config.supports_input(required_input_type):
                return self._current_model

        # 遍历所有模型找可用的
        for model_key in self._model_order:
            if model_key in self._failed_models:
                continue
            model_config = self.config.get_model(model_key)
            if model_config and model_config.supports_input(required_input_type):
                self._current_model = model_key
                logger.info(f"[ModelManager] Switching to model: {model_key}")
                return model_key

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


def load_models_config(config_path: Optional[str] = None) -> ModelsConfig:
    """加载模型配置并自动初始化ModelManager"""
    global _models_config, _model_manager, _primary_model

    if _models_config is not None:
        return _models_config

    # 默认配置路径
    if config_path is None:
        config_path = os.getenv("MODELS_CONFIG_PATH", "config/models.json")

    config_file = Path(config_path)
    if not config_file.exists():
        logger.warning(f"[ModelManager] Config file not found: {config_path}")
        _models_config = ModelsConfig()
        return _models_config

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 解析providers
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

        _models_config = ModelsConfig(
            mode=data.get("mode", "merge"),
            model=data.get("model", {}),
            models=data.get("models", {}),
            providers=providers,
        )

        logger.info(
            f"[ModelManager] Loaded {len(_models_config.list_models())} models from {config_path}"
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


def get_primary_model() -> str:
    """获取主模型"""
    return _primary_model


# 便捷函数 - 兼容现有代码
def get_current_model() -> str:
    """获取当前使用的模型（用于AiService）"""
    if _model_manager:
        return _model_manager.get_current_model()
    return _primary_model


def get_model_for_input(input_type: str = "text") -> str:
    """获取支持指定输入类型的模型"""
    if _model_manager:
        model = _model_manager.get_next_available_model(input_type)
        if model:
            return model
    return _primary_model


def get_model_id_for_api(model_key: Optional[str] = None) -> str:
    """获取用于API调用的模型ID（去掉provider前缀）

    这是关键函数：在调用OpenAI API时使用

    Example:
        model_key = 'bailian/qwen3.5-plus'
        returns = 'qwen3.5-plus'
    """
    if _model_manager:
        return _model_manager.get_model_id(model_key)

    # fallback: 如果传入的是完整key
    key = model_key or _primary_model
    if "/" in key:
        return key.split("/", 1)[1]
    return key


def get_api_key_for_model(model_key: Optional[str] = None) -> str:
    """获取模型对应的API Key"""
    if _model_manager:
        provider_config = _model_manager.get_provider_config(model_key)
        if provider_config:
            return provider_config.apiKey
    return ""


def get_base_url_for_model(model_key: Optional[str] = None) -> Optional[str]:
    """获取模型对应的baseUrl"""
    if _model_manager:
        provider_config = _model_manager.get_provider_config(model_key)
        if provider_config:
            return provider_config.baseUrl
    return None


def get_routing_model() -> str:
    """获取路由模型"""
    if _models_config:
        return _models_config.get_routing_model()
    return _primary_model


def get_voice_model() -> str:
    """获取语音模型"""
    if _models_config:
        return _models_config.get_voice_model()
    return _primary_model


def get_image_model() -> str:
    """获取图像模型"""
    if _models_config:
        return _models_config.get_image_model()
    return _primary_model
