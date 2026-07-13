"""Runtime config from environment. Secrets come from the environment / .env,
never from source. All optional with local-first defaults."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str = os.environ.get("LEDGERLINE_DB", "finance.db")
    host: str = os.environ.get("LEDGERLINE_HOST", "127.0.0.1")   # localhost only by default
    port: int = int(os.environ.get("LEDGERLINE_PORT", "8000"))
    # If unset, the UI runs unlocked (dev). Set a value to require it on login.
    passphrase: str | None = os.environ.get("LEDGERLINE_PASSPHRASE") or None
    # Cookie-signing key; ephemeral per process unless pinned (logs everyone out on restart).
    secret_key: str = os.environ.get("LEDGERLINE_SECRET") or secrets.token_hex(16)
    anthropic_api_key: str | None = os.environ.get("ANTHROPIC_API_KEY") or None
    session_max_age: int = 60 * 60 * 24 * 14  # 14 days


settings = Settings()
