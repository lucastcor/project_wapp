import os

from dotenv import load_dotenv

load_dotenv()


class Settings:
    app_name: str = os.getenv("APP_NAME", "Chatbot API")
    app_version: str = os.getenv("APP_VERSION", "1.0.0")
    environment: str = os.getenv("ENVIRONMENT", "development")
    debug: bool = os.getenv("DEBUG", "true").lower() == "true"

    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5")
    openai_finetuned_model: str | None = os.getenv("OPENAI_FINETUNED_MODEL")
    use_finetuned_model_for_replies: bool = os.getenv("USE_FINETUNED_MODEL_FOR_REPLIES", "false").lower() == "true"
    openai_finetuning_base_model: str = os.getenv("OPENAI_FINETUNING_BASE_MODEL", "gpt-4.1-mini")
    openai_finetuning_suffix: str = os.getenv("OPENAI_FINETUNING_SUFFIX", "project-wapp-style")
    enable_finetuning_pipeline: bool = os.getenv("ENABLE_FINETUNING_PIPELINE", "false").lower() == "true"
    classification_provider: str = os.getenv("CLASSIFICATION_PROVIDER", "mock")
    outbound_provider: str = os.getenv("OUTBOUND_PROVIDER", "mock")
    outbound_default_channel: str = os.getenv("OUTBOUND_DEFAULT_CHANNEL", "whatsapp")

    whatsapp_api_base_url: str | None = os.getenv("WHATSAPP_API_BASE_URL")
    whatsapp_api_token: str | None = os.getenv("WHATSAPP_API_TOKEN")
    whatsapp_phone_number_id: str | None = os.getenv("WHATSAPP_PHONE_NUMBER_ID")


settings = Settings()