from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT_DIR / "config"
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-luna"

    database_url: str = "sqlite:///./data/remote_apply.db"
    persona_output_root: str = "./data/applications"

    auto_submit: bool = False

    cors_origins: str = "*"
    access_token: str = ""
    host: str = "0.0.0.0"
    port: int = 8000

    octo_enabled: bool = True
    octo_api_url: str = "http://127.0.0.1:58888"
    octo_profile_uuid: str = ""
    octo_profile_password: str = ""
    browser_cdp_url: str = ""
    apply_allow_launch_chromium: bool = False

    apply_login_email: str = ""
    apply_login_password: str = ""

    # Batch concurrency (OpenAI / network bound)
    # Keep low if TPM is ~30k (a resume call is a large one).
    tailor_concurrency: int = 2
    deep_scan_concurrency: int = 3

    # Minimum ATS keyword score (0-100) a generated resume must reach. Below
    # it the resume is regenerated with the missing terms fed back in. 0 is
    # off. Each retry is a full tailor call, so the ceiling is deliberately low.
    min_ats_score: int = 0
    ats_max_attempts: int = 3

    # Applied-job Google Sheet log. The target belongs in .env — a personal
    # spreadsheet id as a code default follows the repo to anyone who clones it.
    applied_sheet_enabled: bool = True
    applied_sheet_id: str = ""
    applied_sheet_gid: str = ""
    applied_sheet_tab: str = ""
    applied_sheets_webhook_url: str = ""
    google_service_account_json: str = ""

    # Company discovery (Google Custom Search or SerpAPI)
    google_cse_api_key: str = ""
    google_cse_cx: str = ""
    serpapi_key: str = ""

    # Jobright's search endpoints answer 401 without a signed-in session. The
    # cookie is a login and it expires, so it lives in .env rather than here —
    # a session id committed to the repo follows it to everyone who clones it.
    # Empty means the Jobright fetcher reads their public pages instead.
    jobright_cookie: str = ""

    @property
    def root_dir(self) -> Path:
        return ROOT_DIR

    @property
    def config_dir(self) -> Path:
        return CONFIG_DIR

    @property
    def output_root(self) -> Path:
        path = Path(self.persona_output_root)
        if not path.is_absolute():
            path = ROOT_DIR / path
        return path

    @property
    def db_path(self) -> Path:
        url = self.database_url
        if url.startswith("sqlite:///"):
            raw = url.replace("sqlite:///", "", 1)
            path = Path(raw)
            if not path.is_absolute():
                path = ROOT_DIR / path
            return path
        return ROOT_DIR / "data" / "remote_apply.db"

    @property
    def sqlalchemy_url(self) -> str:
        path = self.db_path
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{path.as_posix()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
