from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class AdminProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str | None = None
    username: str | None = None
    display_name: str | None = None
    password: str | None = None


class RuntimeDocsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    soul_content: str | None = None
    user_content: str | None = None


class RuntimeTelegramChannelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    bot_token: str | None = None


class RuntimeDiscordChannelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    bot_token: str | None = None


class RuntimeDingTalkChannelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    client_id: str | None = None
    client_secret: str | None = None


class RuntimeWeixinChannelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    base_url: str | None = None
    cdn_base_url: str | None = None


class RuntimeWebChannelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None


class RuntimeChannelsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    admin_user_ids: list[str] | None = None
    telegram: RuntimeTelegramChannelPatch | None = None
    discord: RuntimeDiscordChannelPatch | None = None
    dingtalk: RuntimeDingTalkChannelPatch | None = None
    weixin: RuntimeWeixinChannelPatch | None = None
    web: RuntimeWebChannelPatch | None = None


class RuntimeConfigPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    admin_user: AdminProfilePatch | None = None
    docs: RuntimeDocsPatch | None = None
    channels: RuntimeChannelsPatch | None = None
    features: dict[str, bool] | None = None
    cors_allowed_origins: list[str] | None = None
    memory_provider: str | None = None


class RuntimeDocGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["soul", "user"]
    brief: str | None = None
    current_content: str | None = None
    model_key: str | None = None


class ModelsConfigPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models_config: dict[str, Any]


class ModelsLatencyCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["primary", "routing"]
    provider_name: str
    base_url: str | None = None
    api_key: str | None = None
    api_style: str | None = None
    model_id: str
