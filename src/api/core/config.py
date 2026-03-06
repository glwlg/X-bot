"""
配置管理模块
"""

import os
import toml
from typing import Dict, Any
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class GeneralConfig(BaseModel):
    log_level: str = "INFO"


class SQLiteConfig(BaseModel):
    database: str = "sqlite.db"


class AuthConfig(BaseModel):
    """认证配置"""

    secret_key: str = "SUPER_SECRET_KEY_CHANGEME"  # JWT 密钥
    access_token_expire_minutes: int = 60 * 24 * 3650  # Token 有效期，默认 10 年


_toml_config_data: Dict[str, Any] = {}


class AppConfig(BaseSettings):
    """应用配置类"""

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    sqlite: SQLiteConfig = Field(default_factory=SQLiteConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="before")
    @classmethod
    def merge_toml_with_env(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        toml_data = _toml_config_data.copy()
        for key, value in toml_data.items():
            if key not in values or values[key] is None:
                values[key] = value
            elif isinstance(value, dict) and isinstance(values.get(key), dict):
                merged = value.copy()
                merged.update(values[key])
                values[key] = merged
        return values


def load_config(path: str = "config.toml") -> AppConfig:
    global _toml_config_data
    _toml_config_data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                _toml_config_data = toml.load(f)
        except Exception as e:
            print(f"Warning: Error loading TOML config file '{path}': {e}")

    try:
        return AppConfig()
    except Exception as e:
        print(f"FATAL: Error loading configuration: {e}")
        import traceback

        traceback.print_exc()
        exit(1)


# 全局配置实例
settings = load_config()
