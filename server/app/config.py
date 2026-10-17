from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    openai_api_key: str = ''
    openai_model: str = ''
    google_application_credentials: str = ''
    governing_docs_dir: str = './governing'
    benchmark_spreadsheet_id: str = ''
    batch_size: int = Field(default=10, ge=1, le=100)
    max_retries: int = Field(default=3, ge=1, le=10)
    expensive_batch_warning_usd: float = Field(default=10, ge=0)
    job_spending_limit_usd: float = Field(default=100, ge=0)
    openai_input_usd_per_million: float = Field(default=0, ge=0)
    openai_cached_input_usd_per_million: float = Field(default=0, ge=0)
    openai_output_usd_per_million: float = Field(default=0, ge=0)
    # Optional server-only token for trusted service clients. Empty disables token auth
    # (never opens the API). Browser clients must use cookie sessions.
    api_token: str = ''
    job_index_spreadsheet_id: str = ''
    openai_timeout_seconds: int = Field(default=900, ge=60, le=3600)
    openai_max_output_tokens: int = Field(default=30000, ge=1000, le=100000)
    openai_reasoning_effort: str = ''
    diagnostics_dir: str = './logs'
    runtime_prompt_path: str = ''
    project_references_dir: str = './project_references'
    image_original_max_bytes: int = Field(default=4_000_000, ge=1)
    image_original_max_edge: int = Field(default=2048, ge=256)
    image_resize_max_edge: int = Field(default=2048, ge=256)
    image_jpeg_quality: int = Field(default=95, ge=90, le=100)

    # Shared demo login (Argon2 hash). Missing values fail closed.
    app_login_username: str = ''
    app_login_password_hash: str = ''
    session_ttl_seconds: int = Field(default=43200, ge=300, le=604800)
    cookie_samesite: str = 'lax'
    # auto | true | false — auto uses X-Forwarded-Proto / request scheme
    cookie_secure: str = 'auto'
    # Comma-separated origins allowed for cookie-authenticated mutations, e.g.
    # https://203.0.113.10,http://127.0.0.1
    allowed_origins: str = ''
    login_max_failures: int = Field(default=5, ge=1, le=50)
    login_window_seconds: int = Field(default=300, ge=30, le=3600)
    login_lockout_seconds: int = Field(default=300, ge=30, le=3600)

    # Tri Delta Box 19 F01 checkpoint / Frozen Benchmark harness. Default off —
    # normal operator UI and APIs must not present it as generic verification.
    enable_developer_verification: bool = False

    def validate_live(self):
        from pathlib import Path
        missing = [k for k in ('openai_api_key','openai_model','google_application_credentials') if not getattr(self,k)]
        if missing: raise RuntimeError('Missing configuration: '+', '.join(missing))
        if not Path(self.governing_docs_dir).is_dir() or not list(Path(self.governing_docs_dir).glob('*')):
            raise RuntimeError('Governing documents are required before inspection')
        from app.inspection import _runtime_prompt
        from app.references import references_dir
        _runtime_prompt(self)
        reference_index = references_dir(self) / 'index.json'
        if not reference_index.is_file():
            raise RuntimeError(f'Approved project reference index is missing: {reference_index}')
        if all(v == 0 for v in (self.openai_input_usd_per_million,self.openai_output_usd_per_million)):
            raise RuntimeError('Set model pricing to enforce JOB_SPENDING_LIMIT_USD')
