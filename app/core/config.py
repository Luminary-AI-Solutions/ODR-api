import logging
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv
from pydantic import AliasChoices, BaseModel, Field, HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .exceptions import ConfigurationError

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_repo_env_files() -> None:
    """
    Load project env files without overriding variables already set in the process environment.

    Order: ODR-api.env first, then .env. Shell exports (e.g. from scripts/wsl_env_and_server.sh)
    always win, so sourcing ODR-api.env in bash and also listing it here is safe.
    Using override=False fixes accidental stomping when a sparse .env exists alongside ODR-api.env.
    """
    for fname in ("ODR-api.env", ".env"):
        path = _REPO_ROOT / fname
        if path.is_file():
            load_dotenv(dotenv_path=path, encoding="utf-8", override=False)


_load_repo_env_files()


class SerperConfig(BaseSettings):
    """Configuration settings for the Serper Search API client."""

    api_key: SecretStr = Field(..., alias="SERPER_API_KEY")
    base_url: HttpUrl = Field("https://google.serper.dev", alias="SERPER_BASE_URL")
    default_location: str = Field("us", alias="SERPER_DEFAULT_LOCATION")
    timeout: int = Field(15, alias="SERPER_TIMEOUT", gt=0)

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def check_api_key_present(self) -> "SerperConfig":
        if not self.api_key or not self.api_key.get_secret_value():
            raise ValueError("SERPER_API_KEY must be set and must not be empty.")
        return self


class ApiKeys(BaseModel):
    """Compatibility wrapper for legacy code paths that expect a key bundle."""

    serper_api_key: Optional[SecretStr] = None
    together_api_key: Optional[SecretStr] = None
    cohere_api_key: Optional[SecretStr] = None
    openrouter_api_key: Optional[SecretStr] = None
    firebase_service_account_key_json: Optional[str] = None

    @classmethod
    def from_settings(cls, settings: "AppSettings") -> "ApiKeys":
        return cls(
            serper_api_key=settings.serper_api_key,
            together_api_key=settings.together_api_key,
            cohere_api_key=settings.cohere_api_key,
            openrouter_api_key=settings.openrouter_api_key,
            firebase_service_account_key_json=settings.firebase_service_account_key_json,
        )


class AppSettings(BaseSettings):
    """Shared provider and application settings for all agencies."""

    # Env vars come from os.environ; `_load_repo_env_files()` runs at import so ODR-api.env
    # is applied without relying on cwd. Do not use env_file here with override semantics.
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="allow",
    )

    serper_api_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias=AliasChoices("SERPER_API_KEY"),
    )
    openrouter_api_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPENROUTER_API_KEY",
            "DEEP_RESEARCH_OPENROUTER_API_KEY",
            "CPE_OPENROUTER_API_KEY",
        ),
    )
    anthropic_api_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY"),
    )
    cohere_api_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias=AliasChoices(
            "COHERE_API_KEY",
            "DEEP_RESEARCH_COHERE_API_KEY",
            "CPE_COHERE_API_KEY",
        ),
    )
    together_api_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias=AliasChoices(
            "TOGETHER_API_KEY",
            "DEEP_RESEARCH_TOGETHER_API_KEY",
            "CPE_TOGETHER_API_KEY",
        ),
    )
    firebase_service_account_key_json: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "FIREBASE_SERVICE_ACCOUNT_KEY_JSON",
            "DEEP_RESEARCH_FIREBASE_SERVICE_ACCOUNT_KEY_JSON",
            "CPE_FIREBASE_SERVICE_ACCOUNT_KEY_JSON",
        ),
    )

    openrouter_api_base: HttpUrl = Field(
        "https://openrouter.ai/api/v1",
        validation_alias=AliasChoices("OPENROUTER_API_BASE"),
    )
    serper_base_url: HttpUrl = Field(
        "https://google.serper.dev",
        validation_alias=AliasChoices("SERPER_BASE_URL"),
    )
    cohere_api_base: HttpUrl = Field(
        "https://api.cohere.com",
        validation_alias=AliasChoices("COHERE_API_BASE"),
    )
    together_api_base: HttpUrl = Field(
        "https://api.together.ai",
        validation_alias=AliasChoices("TOGETHER_API_BASE"),
    )
    serper_default_location: str = Field("us", validation_alias=AliasChoices("SERPER_DEFAULT_LOCATION"))
    serper_timeout: int = Field(15, validation_alias=AliasChoices("SERPER_TIMEOUT"), gt=0)
    reranker_model: str = Field("rerank-english-v3.0", validation_alias=AliasChoices("RERANKER_MODEL"), min_length=1)

    scraper_debug: bool = Field(False, description="Enable debug logging for the scraper")
    scraper_download_pdfs: bool = Field(False, alias="SCRAPER_DOWNLOAD_PDFS")
    scraper_pdf_save_dir: str = Field("downloaded_pdfs", alias="SCRAPER_PDF_SAVE_DIR")
    scraper_max_pdf_size_mb: int = Field(512, alias="SCRAPER_MAX_PDF_SIZE_MB", gt=0)

    def _require_secret(self, secret: Optional[SecretStr], env_name: str) -> str:
        if not secret or not secret.get_secret_value():
            raise ConfigurationError(f"{env_name} must be configured.")
        return secret.get_secret_value()

    def get_openrouter_api_key(self) -> str:
        return self._require_secret(self.openrouter_api_key, "OPENROUTER_API_KEY")

    def get_anthropic_api_key(self) -> str:
        return self._require_secret(self.anthropic_api_key, "ANTHROPIC_API_KEY")

    def get_rerank_api_key(self, provider: Literal["cohere", "together"]) -> str:
        if provider == "cohere":
            return self._require_secret(self.cohere_api_key, "COHERE_API_KEY")
        return self._require_secret(self.together_api_key, "TOGETHER_API_KEY")

    def get_rerank_api_base(self, provider: Literal["cohere", "together"]) -> str:
        if provider == "cohere":
            return str(self.cohere_api_base)
        return str(self.together_api_base)

    def build_serper_config(self) -> SerperConfig:
        if not self.serper_api_key or not self.serper_api_key.get_secret_value():
            raise ConfigurationError("SERPER_API_KEY must be configured.")
        return SerperConfig(
            api_key=self.serper_api_key,
            base_url=self.serper_base_url,
            default_location=self.serper_default_location,
            timeout=self.serper_timeout,
        )