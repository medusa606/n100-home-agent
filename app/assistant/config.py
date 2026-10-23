from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # About the user
    user_name: str = "the user"
    interests: str = "AI agents, LLMs"
    location: str = ""
    timezone: str = "UTC"
    digest_time: str = ""

    # LLM
    llm_base_url: str = "http://ollama:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen3:4b"
    llm_no_think: bool = True
    llm_timeout: float = 300
    max_steps: int = 6
    history_turns: int = 6

    # Chat channel: telegram | signal | whatsapp
    channel: str = "telegram"
    allowed_chat_ids: str = ""
    owner_chat_id: str = ""
    telegram_bot_token: str = ""
    signal_url: str = "http://signal:8080"
    signal_number: str = ""

    # WhatsApp via WAHA
    waha_url: str = "http://waha:3000"
    waha_api_key: str = ""
    waha_session: str = "default"

    # Home Assistant
    ha_url: str = "http://host.docker.internal:8123"
    ha_token: str = ""
    ha_allowed_domains: str = "light,switch,climate,cover,fan,media_player,scene,script,sensor,binary_sensor,input_boolean,lock"
    ha_confirm_domains: str = "lock,cover,alarm_control_panel"

    # Search
    searxng_url: str = "http://searxng:8080"

    # LinkedIn
    linkedin_client_id: str = ""
    linkedin_client_secret: str = ""
    linkedin_redirect_uri: str = "http://localhost:8000/linkedin/callback"

    db_path: str = "/data/agent.db"

    @property
    def allowed_chats(self) -> set[str]:
        return set(_csv(self.allowed_chat_ids))

    @property
    def ha_allowed(self) -> set[str]:
        return set(_csv(self.ha_allowed_domains))

    @property
    def ha_confirm(self) -> set[str]:
        return set(_csv(self.ha_confirm_domains))


@lru_cache
def get_settings() -> Settings:
    return Settings()
