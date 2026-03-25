import os
import re
from logging.config import fileConfig
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import engine_from_config, pool

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def _load_env_files():
    """Load .env from project root."""
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


def _build_db_url() -> str:
    """Build Postgres URL from environment.

    Supports:
    - DATABASE_URL (standard Postgres connection string)
    - SUPABASE_URL + SUPABASE_DB_PASSWORD (Supabase hosted)
    - PATCHCORD_DB_HOST/PORT/USER/PASSWORD/NAME (custom Postgres)
    - Default: localhost with no password
    """
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        return db_url

    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_pw = os.environ.get("SUPABASE_DB_PASSWORD", "") or os.environ.get("PATCHCORD_DB_PASSWORD", "")
    if supabase_url and supabase_pw:
        match = re.search(r"://([a-z0-9]+)\.supabase\.co", supabase_url)
        if match:
            return f"postgresql://postgres:{quote(supabase_pw, safe='')}@db.{match.group(1)}.supabase.co:5432/postgres?sslmode=require"

    host = os.environ.get("PATCHCORD_DB_HOST", "localhost")
    port = os.environ.get("PATCHCORD_DB_PORT", "5432")
    user = os.environ.get("PATCHCORD_DB_USER", "postgres")
    password = os.environ.get("PATCHCORD_DB_PASSWORD", "")
    dbname = os.environ.get("PATCHCORD_DB_NAME", "patchcord")

    if password:
        return f"postgresql://{user}:{quote(password, safe='')}@{host}:{port}/{dbname}"
    return f"postgresql://{user}@{host}:{port}/{dbname}"


_load_env_files()
db_url = _build_db_url()
config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
