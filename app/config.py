"""
Configuration, loaded from environment variables (K8s ConfigMap/Secret friendly).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Service identity ---
    service_name: str = "fasterwhisper-api"
    environment: str = "production"

    # --- Whisper model ---
    whisper_model: str = (
        "large-v3"  # any faster-whisper / CTranslate2 model name or local path
    )
    whisper_device: str = "cuda"  # "cuda" or "cpu"
    whisper_compute_type: str = "float16"  # float16/int8_float16/int8 (int8 for CPU)
    whisper_download_root: str = (
        "/models"  # mount a PVC here so the model persists across pod restarts
    )
    whisper_num_workers: int = 1  # translation/decode threads per worker
    whisper_cpu_threads: int = 4  # only relevant when device=cpu

    # --- Concurrency ---
    # A single GPU can typically only run one faster-whisper inference at a time.
    # This gates the async job worker pool; raise only if you have multiple GPUs/replicas.
    gpu_concurrency: int = 1

    # --- Uploads ---
    max_upload_mb: int = 500
    tmp_upload_dir: str = "/tmp/fasterwhisper-uploads"

    # --- Sync vs async cutover ---
    # Files estimated to run longer than this (by size heuristic) via the
    # OpenAI-compatible sync endpoint are rejected with a hint to use the async endpoint,
    # since sync callers (e.g. Open WebUI) will otherwise hit their own HTTP timeout.
    sync_max_upload_mb: int = 25

    # --- Auth: static pre-shared keys ---
    # Comma-separated "key:label" pairs, e.g. "sk-abc123:internal-tools". Useful
    # for service accounts that don't have an Open WebUI user (see below for the
    # Open WebUI-backed lookup). A bare key with no label is accepted too, labelled
    # "unlabelled". Can be left empty if every caller authenticates via Open WebUI.
    api_keys_raw: str = ""

    # --- Auth: Open WebUI-backed keys ---
    # Lets anyone with a valid Open WebUI personal API key (Settings > Account >
    # API keys, in Open WebUI itself) call this service directly, with no key
    # management here at all. Point this at Open WebUI's Postgres database
    # (asyncpg URL) — leave blank to disable this lookup entirely (static keys
    # above still work). A read-only DB role is enough: this only ever SELECTs,
    # unless openwebui_update_last_used is turned on.
    openwebui_database_url: str = ""
    openwebui_db_pool_size: int = 2
    # How long a (key -> user) lookup is cached in memory before re-querying Open
    # WebUI's database. Keeps steady traffic from hitting that DB on every request;
    # a revoked key can still be used elsewhere for up to this long.
    openwebui_key_cache_seconds: int = 30
    # Mirror Open WebUI's own behaviour of stamping last_used_at when a key is used.
    # Off by default since it needs UPDATE, not just SELECT, on Open WebUI's api_key
    # table — turn on only if you've granted that.
    openwebui_update_last_used: bool = False

    # --- Database (metadata / request tracking — this service's own DB, distinct
    # from openwebui_database_url above) ---
    # postgresql+asyncpg://user:pass@host:5432/dbname
    database_url: str = (
        "postgresql+asyncpg://fasterwhisper:fasterwhisper@localhost:5432/fasterwhisper"
    )
    db_pool_size: int = 5
    db_echo: bool = False

    # --- Retention ---
    job_result_retention_days: int = 30

    # --- Logging ---
    log_level: str = "INFO"
    log_json: bool = True

    @property
    def api_keys(self) -> dict[str, str]:
        """Parsed {api_key: label} map."""
        keys: dict[str, str] = {}
        for entry in self.api_keys_raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" in entry:
                key, label = entry.split(":", 1)
            else:
                key, label = entry, "unlabelled"
            keys[key.strip()] = label.strip()
        return keys

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def sync_max_upload_bytes(self) -> int:
        return self.sync_max_upload_mb * 1024 * 1024

    @staticmethod
    def _as_asyncpg_url(url: str) -> str:
        """Upgrade a plain `postgresql://` URL to `postgresql+asyncpg://`.

        CNPG's auto-generated app-user Secret (the `uri` key) hands out bare
        `postgresql://` URLs, which is the right generic default but isn't
        enough on its own for SQLAlchemy's async engine — without an explicit
        driver, it defaults to the sync `psycopg2` dialect and refuses to
        build an AsyncEngine. Normalizing here means DATABASE_URL/
        OPENWEBUI_DATABASE_URL can point straight at a CNPG-managed secret's
        `uri` key with no manual rewriting.
        """
        if url.startswith("postgresql://"):
            return "postgresql+asyncpg://" + url[len("postgresql://") :]
        if url.startswith("postgres://"):
            return "postgresql+asyncpg://" + url[len("postgres://") :]
        return url

    @property
    def database_url_async(self) -> str:
        return self._as_asyncpg_url(self.database_url)

    @property
    def openwebui_database_url_async(self) -> str:
        return self._as_asyncpg_url(self.openwebui_database_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
