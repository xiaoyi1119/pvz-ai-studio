from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    deepseek_base_url: str = Field(
        default="https://api.deepseek.com/v1",
        validation_alias=AliasChoices("DEEPSEEK_BASE_URL", "VLM_BASE_URL"),
    )
    deepseek_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("DEEPSEEK_API_KEY", "VLM_API_KEY"),
    )
    deepseek_model: str = Field(
        default="deepseek-flash",
        validation_alias=AliasChoices("DEEPSEEK_MODEL", "VLM_MODEL"),
    )
    plugin_host: str = "127.0.0.1"
    plugin_port: int = 8090
    agent_goal: str = "自主完成当前关卡"
    channel_name: str = "小盆栽的植物园"
    channel_avatar_path: str = ""
    capture_window_title: str = Field(default="植物大战僵尸中文版", validation_alias="PVZ_WINDOW_TITLE")
    capture_fps: int = Field(default=30, validation_alias="PVZ_CAPTURE_FPS")
    capture_quality: int = Field(default=4, validation_alias="PVZ_CAPTURE_QUALITY")

