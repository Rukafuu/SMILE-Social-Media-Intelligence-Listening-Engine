"""Minimal local environment loader; secrets remain in ignored `.env` files."""
from __future__ import annotations

import os
from pathlib import Path


SUPPORTED_KEYS = {"OPENROUTER_API_KEY", "OPENROUTER_MODEL", "MASTODON_BASE_URL", "MASTODON_TOKEN",
                  "MASTODON_CLIENT_ID", "MASTODON_CLIENT_SECRET", "MASTODON_AUTHORIZATION_CODE",
                  "MASTODON_REDIRECT_URI"}


def load_local_env(path: str = ".env") -> None:
    """Load simple KEY=VALUE entries without evaluating shell syntax.

    Existing process variables take precedence, and values are never logged.
    """
    env_file = Path(path)
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in SUPPORTED_KEYS and key not in os.environ:
            os.environ[key] = value.strip().strip("\"'")


def upsert_local_env_value(key: str, value: str, path: str = ".env", remove_keys=()) -> None:
    """Write a secret locally without logging it, preserving unrelated entries."""
    env_file = Path(path)
    existing = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    replaced = []
    keys_to_remove = set(remove_keys) | {key}
    for line in existing:
        entry_key = line.split("=", 1)[0].strip() if "=" in line else ""
        if entry_key not in keys_to_remove:
            replaced.append(line)
    replaced.append(key + "=" + value)
    env_file.write_text("\n".join(replaced) + "\n", encoding="utf-8")
    env_file.chmod(0o600)
