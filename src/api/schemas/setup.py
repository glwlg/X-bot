from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SetupAdminProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str | None = None
    username: str | None = None
    display_name: str | None = None
    password: str | None = None


class SetupModelRoleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_name: str
    base_url: str
    api_key: str
    api_style: str = "openai-completions"
    model_id: str
    display_name: str | None = None
    reasoning: bool = False
    input_types: list[str] = Field(default_factory=lambda: ["text"])


class SetupModelsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: SetupModelRoleInput | None = None
    routing: SetupModelRoleInput | None = None


class SetupDocsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    soul_content: str | None = None
    user_content: str | None = None


class SetupChannelsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platforms: dict[str, bool] | None = None
    admin_user_ids: list[str] | None = None
    telegram_bot_token: str | None = None
    discord_bot_token: str | None = None
    dingtalk_client_id: str | None = None
    dingtalk_client_secret: str | None = None
    weixin_enable: bool | None = None
    weixin_base_url: str | None = None
    weixin_cdn_base_url: str | None = None
    web_channel_enable: bool | None = None


class SetupPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    admin_user: SetupAdminProfilePatch | None = None
    models: SetupModelsPatch | None = None
    docs: SetupDocsPatch | None = None
    channels: SetupChannelsPatch | None = None


class SetupGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["soul", "user"]
    brief: str | None = None
    current_content: str | None = None
    model_key: str | None = None

