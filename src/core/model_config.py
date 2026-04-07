"""
模型配置模块 - 管理多模型配置、轮询和输入类型支持
"""

import os
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional
from pathlib import Path

from core.app_paths import models_config_path
from core.audit_store import audit_store

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

_MODEL_SELECTION_STRATEGIES = {"priority", "round_robin", "least_usage"}

_MODEL_ROLE_SELECTION_PARAMS: dict[str, tuple[str, str]] = {
    "primary": ("text", "primary"),
    "routing": ("text", "routing"),
    "vision": ("image", "vision"),
    "image_generation": ("text", "image_generation"),
    "voice": ("voice", "voice"),
}


def _pool_aliases(pool_type: str) -> tuple[str, ...]:
    normalized = str(pool_type or "primary").strip().lower() or "primary"
    return _MODEL_POOL_ALIASES.get(normalized, (normalized,))


def normalize_pool_type(pool_type: str) -> str:
    """将池名别名归一化为统一名称。"""
    normalized = str(pool_type or "primary").strip().lower() or "primary"
    normalized_role = normalize_model_role(normalized)
    if normalized_role:
        return normalized_role
    aliases = _pool_aliases(normalized)
    return aliases[0] if aliases else normalized


def normalize_selection_strategy(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in _MODEL_SELECTION_STRATEGIES:
        return token
    return "priority"


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
    if config_path is not None and str(config_path).strip():
        return Path(str(config_path).strip()).expanduser().resolve()
    return models_config_path()


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
class ModelLimits:
    """模型日用量限制配置。0 表示不限。"""

    dailyTokens: int = 0
    dailyImages: int = 0


@dataclass
class ModelConfig:
    """单个模型配置"""

    id: str
    name: str
    reasoning: bool = False
    input: list[str] = field(
        default_factory=lambda: ["text"]
    )  # 支持的输入类型: text, image
    output: list[str] = field(default_factory=list)  # 支持的输出类型: text, image, voice, video
    cost: ModelCost = field(default_factory=ModelCost)
    limits: ModelLimits = field(default_factory=ModelLimits)
    contextWindow: int = 1000000
    maxTokens: int = 65536

    def supports_input(self, input_type: str) -> bool:
        """检查模型是否支持指定的输入类型"""
        return input_type in self.input

    def supports_output(self, output_type: str) -> bool:
        """检查模型是否支持指定的输出类型"""
        return output_type in self.output


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
    selection: dict[str, dict[str, Any]] = field(default_factory=dict)
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

    def get_models_by_output(self, output_type: str) -> list[str]:
        """获取支持指定输出类型的所有模型。"""
        return [
            key
            for key, model in self._model_index.items()
            if model.supports_output(output_type)
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

    def get_configured_model_for_pool(self, pool_type: str = "primary") -> str:
        """根据池类型返回该角色当前配置的默认模型。"""
        normalized_pool = normalize_pool_type(pool_type)
        if normalized_pool == "primary":
            return self.get_primary_model()
        if normalized_pool == "routing":
            return self.get_routing_model()
        if normalized_pool == "vision":
            return self.get_vision_model()
        if normalized_pool == "image_generation":
            return self.get_image_generation_model()
        if normalized_pool == "voice":
            return self.get_voice_model()
        return ""

    def get_model_pool_entries(
        self, pool_type: str = "primary"
    ) -> list[tuple[str, dict[str, Any]]]:
        """获取指定池的模型与元数据，保留配置顺序。"""
        for candidate in _pool_aliases(pool_type):
            pool = self.models.get(candidate, {})
            if isinstance(pool, dict) and pool:
                entries: list[tuple[str, dict[str, Any]]] = []
                for raw_key, raw_meta in pool.items():
                    model_key = str(raw_key or "").strip()
                    if not model_key:
                        continue
                    entries.append(
                        (
                            model_key,
                            dict(raw_meta) if isinstance(raw_meta, dict) else {},
                        )
                    )
                return entries
            if isinstance(pool, list) and pool:
                return [
                    (model_key, {})
                    for item in pool
                    if (model_key := str(item or "").strip())
                ]
        return []

    def get_model_pool(self, pool_type: str = "primary") -> list[str]:
        """获取指定类型的模型池"""
        return [model_key for model_key, _meta in self.get_model_pool_entries(pool_type)]

    def get_model_pool_meta(
        self,
        pool_type: str,
        model_key: str,
    ) -> dict[str, Any]:
        """读取指定池中某个模型的元数据。"""
        safe_model_key = str(model_key or "").strip()
        if not safe_model_key:
            return {}
        for candidate_key, meta in self.get_model_pool_entries(pool_type):
            if candidate_key == safe_model_key:
                return dict(meta)
        return {}

    def get_selection_config(self, pool_type: str = "primary") -> dict[str, Any]:
        """获取指定角色池的选择配置。"""
        normalized_pool = normalize_pool_type(pool_type)
        raw_config = self.selection.get(normalized_pool)
        if isinstance(raw_config, dict):
            normalized = dict(raw_config)
        elif isinstance(raw_config, str):
            normalized = {"strategy": raw_config}
        else:
            normalized = {}
        normalized["strategy"] = normalize_selection_strategy(
            normalized.get("strategy")
        )
        return normalized

    def get_selection_strategy(self, pool_type: str = "primary") -> str:
        """获取指定池的模型选择策略。"""
        return self.get_selection_config(pool_type).get("strategy", "priority")

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
        self._round_robin_next_index: dict[str, int] = {}
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

    @staticmethod
    def _pool_priority(meta: dict[str, Any], index: int) -> tuple[float, int]:
        raw_priority = meta.get("priority") if isinstance(meta, dict) else None
        try:
            return float(raw_priority), index
        except Exception:
            return float(1_000_000 + index), index

    def _base_order_for_pool(self, pool_type: str) -> list[str]:
        pool_entries = self.config.get_model_pool_entries(pool_type)
        if pool_entries:
            sorted_entries = sorted(
                enumerate(pool_entries),
                key=lambda item: self._pool_priority(item[1][1], item[0]),
            )
            return [pool_entries[index][0] for index, _entry in sorted_entries]
        return list(self._model_order)

    def _usage_metric_key_for_pool(self, pool_type: str) -> str:
        normalized_pool = normalize_pool_type(pool_type)
        if normalized_pool == "image_generation":
            return "image_outputs"
        return "total_tokens"

    def _limit_value_for_pool(
        self,
        model_config: ModelConfig,
        *,
        pool_type: str,
    ) -> int:
        normalized_pool = normalize_pool_type(pool_type)
        if normalized_pool == "image_generation":
            return max(0, int(model_config.limits.dailyImages or 0))
        return max(0, int(model_config.limits.dailyTokens or 0))

    def _needs_usage_snapshot(self, pool_type: str, strategy: str, model_keys: list[str]) -> bool:
        if strategy == "least_usage":
            return True
        for model_key in model_keys:
            model = self.config.get_model(model_key)
            if model is None:
                continue
            if self._limit_value_for_pool(model, pool_type=pool_type) > 0:
                return True
        return False

    def _load_usage_snapshot(self, model_keys: list[str]) -> dict[str, dict[str, int]]:
        if not model_keys:
            return {}
        try:
            from core.llm_usage_store import llm_usage_store

            return llm_usage_store.summarize_models(model_keys, day=None)
        except Exception:
            logger.debug("Failed to load llm usage summary for model selection", exc_info=True)
            return {}

    def _within_usage_limit(
        self,
        *,
        model_key: str,
        pool_type: str,
        usage_snapshot: dict[str, dict[str, int]],
    ) -> bool:
        model = self.config.get_model(model_key)
        if model is None:
            return False
        limit = self._limit_value_for_pool(model, pool_type=pool_type)
        if limit <= 0:
            return True
        metric_key = self._usage_metric_key_for_pool(pool_type)
        used = int(usage_snapshot.get(model_key, {}).get(metric_key, 0) or 0)
        return used < limit

    def _preferred_model_for_priority(
        self,
        *,
        pool_type: str,
        candidates: list[str],
        preferred_model: Optional[str],
    ) -> str:
        preferred = str(preferred_model or "").strip()
        if preferred in candidates:
            return preferred
        if normalize_pool_type(pool_type) == "primary" and self._current_model in candidates:
            return self._current_model
        configured = self.config.get_configured_model_for_pool(pool_type)
        if configured in candidates:
            return configured
        if self.primary_model in candidates:
            return self.primary_model
        return ""

    def _round_robin_order(self, *, candidates: list[str], base_order: list[str], pool_type: str) -> list[str]:
        if not candidates or not base_order:
            return []
        candidate_set = set(candidates)
        positions = [index for index, model_key in enumerate(base_order) if model_key in candidate_set]
        if not positions:
            return []
        pool_key = normalize_pool_type(pool_type)
        start_index = self._round_robin_next_index.get(pool_key, 0)
        if len(base_order) > 0:
            start_index = start_index % len(base_order)
        ordered_positions = [index for index in positions if index >= start_index]
        ordered_positions.extend(index for index in positions if index < start_index)
        return [base_order[index] for index in ordered_positions]

    def _least_usage_order(
        self,
        *,
        candidates: list[str],
        base_order: list[str],
        pool_type: str,
        usage_snapshot: dict[str, dict[str, int]],
    ) -> list[str]:
        base_index = {model_key: index for index, model_key in enumerate(base_order)}
        metric_key = self._usage_metric_key_for_pool(pool_type)
        return sorted(
            candidates,
            key=lambda model_key: (
                int(usage_snapshot.get(model_key, {}).get(metric_key, 0) or 0),
                int(usage_snapshot.get(model_key, {}).get("requests", 0) or 0),
                base_index.get(model_key, len(base_order)),
                model_key,
            ),
        )

    @staticmethod
    def _move_preferred_to_front(
        ordered_models: list[str],
        preferred_model: Optional[str],
    ) -> list[str]:
        preferred = str(preferred_model or "").strip()
        if preferred and preferred in ordered_models:
            return [preferred] + [model for model in ordered_models if model != preferred]
        return ordered_models

    def _ordered_candidates(
        self,
        *,
        required_input_type: str,
        pool_type: str,
        preferred_model: Optional[str],
        include_failed: bool,
        consume: bool,
    ) -> list[str]:
        base_order = self._base_order_for_pool(pool_type)
        strategy = self.config.get_selection_strategy(pool_type)
        usage_snapshot: dict[str, dict[str, int]] = {}
        if self._needs_usage_snapshot(pool_type, strategy, base_order):
            usage_snapshot = self._load_usage_snapshot(base_order)

        candidates: list[str] = []
        for model_key in base_order:
            model_config = self.config.get_model(model_key)
            if model_config is None or not model_config.supports_input(required_input_type):
                continue
            if not include_failed and model_key in self._failed_models:
                continue
            if not self._within_usage_limit(
                model_key=model_key,
                pool_type=pool_type,
                usage_snapshot=usage_snapshot,
            ):
                continue
            candidates.append(model_key)

        ordered_models: list[str]
        if strategy == "least_usage":
            ordered_models = self._least_usage_order(
                candidates=candidates,
                base_order=base_order,
                pool_type=pool_type,
                usage_snapshot=usage_snapshot,
            )
            ordered_models = self._move_preferred_to_front(ordered_models, preferred_model)
        elif strategy == "round_robin":
            ordered_models = self._round_robin_order(
                candidates=candidates,
                base_order=base_order,
                pool_type=pool_type,
            )
            ordered_models = self._move_preferred_to_front(ordered_models, preferred_model)
        else:
            ordered_models = list(candidates)
            ordered_models = self._move_preferred_to_front(
                ordered_models,
                self._preferred_model_for_priority(
                    pool_type=pool_type,
                    candidates=ordered_models,
                    preferred_model=preferred_model,
                ),
            )

        if consume and ordered_models:
            selected = ordered_models[0]
            pool_key = normalize_pool_type(pool_type)
            if selected in base_order and base_order:
                self._round_robin_next_index[pool_key] = (
                    base_order.index(selected) + 1
                ) % len(base_order)
            if pool_key == "primary" and selected != self._current_model:
                self._current_model = selected
                logger.info(f"[ModelManager] Switching to model: {selected}")
        return ordered_models

    def peek_next_available_model(
        self,
        required_input_type: str = "text",
        pool_type: str = "primary",
    ) -> Optional[str]:
        candidates = self._ordered_candidates(
            required_input_type=required_input_type,
            pool_type=pool_type,
            preferred_model=None,
            include_failed=False,
            consume=False,
        )
        return candidates[0] if candidates else None

    def get_candidate_models(
        self,
        required_input_type: str = "text",
        pool_type: str = "primary",
        *,
        preferred_model: Optional[str] = None,
        include_failed: bool = False,
    ) -> list[str]:
        """获取指定池内、支持输入类型的候选模型列表。"""
        return self._ordered_candidates(
            required_input_type=required_input_type,
            pool_type=pool_type,
            preferred_model=preferred_model,
            include_failed=include_failed,
            consume=False,
        )

    def get_next_available_model(
        self,
        required_input_type: str = "text",
        pool_type: str = "primary",
    ) -> Optional[str]:
        """获取下一个可用的模型（支持指定输入类型）"""
        candidates = self._ordered_candidates(
            required_input_type=required_input_type,
            pool_type=pool_type,
            preferred_model=None,
            include_failed=False,
            consume=True,
        )
        if candidates:
            return candidates[0]

        logger.error("[ModelManager] No available model found")
        return None

    def reset(self):
        """重置所有失败状态"""
        self._failed_models.clear()
        self._current_model = self.primary_model
        self._round_robin_next_index.clear()
        self._initialize_model_order()


# 全局配置实例
_models_config: Optional[ModelsConfig] = None
_model_manager: Optional[ModelManager] = None
_primary_model: str = ""
_loaded_config_path: Optional[Path] = None
_loaded_config_mtime_ns: Optional[int] = None


def _config_mtime_ns(path: Path) -> Optional[int]:
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _parse_models_config_data(data: dict[str, Any]) -> ModelsConfig:
    """将原始 JSON 数据解析为 ModelsConfig。"""
    def _non_negative_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except Exception:
            return 0

    providers = {}
    for provider_name, provider_data in data.get("providers", {}).items():
        models = []
        for model_data in provider_data.get("models", []):
            cost_data = model_data.get("cost", {})
            limits_data = model_data.get("limits", {})
            model = ModelConfig(
                id=model_data["id"],
                name=model_data.get("name", model_data["id"]),
                reasoning=model_data.get("reasoning", False),
                input=model_data.get("input", ["text"]),
                output=model_data.get("output", []),
                cost=ModelCost(
                    input=cost_data.get("input", 0),
                    output=cost_data.get("output", 0),
                    cacheRead=cost_data.get("cacheRead", 0),
                    cacheWrite=cost_data.get("cacheWrite", 0),
                ),
                limits=ModelLimits(
                    dailyTokens=_non_negative_int(limits_data.get("dailyTokens")),
                    dailyImages=_non_negative_int(limits_data.get("dailyImages")),
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
        selection=data.get("selection", {}),
        providers=providers,
    )


def _ensure_models_loaded() -> Optional[ModelsConfig]:
    global _models_config
    if _models_config is None:
        load_models_config()
        return _models_config

    if _loaded_config_path is None:
        return _models_config

    config_file = resolve_models_config_path()
    current_mtime_ns = _config_mtime_ns(config_file)
    if config_file != _loaded_config_path or current_mtime_ns != _loaded_config_mtime_ns:
        load_models_config(config_path=str(config_file), force_reload=True)
    return _models_config


def load_models_config(
    config_path: Optional[str] = None,
    *,
    force_reload: bool = False,
) -> ModelsConfig:
    """加载模型配置并自动初始化ModelManager"""
    global _models_config, _model_manager, _primary_model
    global _loaded_config_path, _loaded_config_mtime_ns

    if _models_config is not None and not force_reload:
        return _models_config

    config_file = resolve_models_config_path(config_path)
    _model_manager = None
    _primary_model = ""
    _loaded_config_path = config_file
    _loaded_config_mtime_ns = _config_mtime_ns(config_file)

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
    actor: str = "system",
    reason: str = "update_configured_model",
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

    rendered = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    audit_store.write_versioned(
        config_file,
        rendered,
        actor=actor,
        reason=reason,
        category="models_config",
    )

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
        current = _model_manager.peek_next_available_model("text", "primary")
        return current or ""
    return _primary_model


def get_model_for_input(input_type: str = "text", pool_type: str = "primary") -> str:
    """获取支持指定输入类型的模型"""
    _ensure_models_loaded()
    if _model_manager:
        model = _model_manager.get_next_available_model(input_type, pool_type)
        return model or ""
    normalized_input_type = str(input_type or "text").strip().lower() or "text"
    if normalized_input_type != "text":
        return ""
    return _primary_model


def _selection_params_for_role(role: str) -> tuple[str, str]:
    normalized_role = normalize_model_role(role)
    if not normalized_role:
        return "text", "primary"
    return _MODEL_ROLE_SELECTION_PARAMS.get(normalized_role, ("text", "primary"))


def peek_model_for_role(role: str) -> str:
    """读取某个角色当前可用的解析结果，不推进轮询状态。"""
    normalized_role = normalize_model_role(role)
    if not normalized_role:
        return ""
    _ensure_models_loaded()
    input_type, pool_type = _selection_params_for_role(normalized_role)
    if _model_manager:
        model = _model_manager.peek_next_available_model(input_type, pool_type)
        return model or ""
    if _models_config:
        return _models_config.get_configured_model_for_pool(pool_type)
    if normalized_role == "primary":
        return _primary_model
    return ""


def select_model_for_role(role: str) -> str:
    """为执行请求选择某个角色当前应使用的模型。"""
    normalized_role = normalize_model_role(role)
    if not normalized_role:
        return ""
    _ensure_models_loaded()
    input_type, pool_type = _selection_params_for_role(normalized_role)
    if _model_manager:
        model = _model_manager.get_next_available_model(input_type, pool_type)
        return model or ""
    if _models_config:
        return _models_config.get_configured_model_for_pool(pool_type)
    if normalized_role == "primary":
        return _primary_model
    return ""


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
    return peek_model_for_role("routing")


def get_voice_model() -> str:
    """获取语音模型"""
    return peek_model_for_role("voice")


def get_vision_model() -> str:
    """获取多模态视觉理解模型。"""
    return peek_model_for_role("vision")


def get_image_generation_model() -> str:
    """获取图片生成模型。"""
    return peek_model_for_role("image_generation")


def get_image_model() -> str:
    """兼容旧接口：返回视觉理解模型。"""
    return get_vision_model()
