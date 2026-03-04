"""Config file R/W, token storage, atomic writes.

Config lives at ~/.config/jl/config.toml (XDG-compliant via platformdirs).
Token precedence: explicit arg > JL_API_KEY env var > config file > error.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import tomllib
from pathlib import Path

import tomli_w
from platformdirs import user_config_dir

CONFIG_DIR_NAME = "jl"
CONFIG_FILE = "config.toml"
ENV_VAR = "JL_API_KEY"


def config_dir() -> Path:
    """Platform-appropriate config directory (e.g. ~/.config/jl/)."""
    return Path(user_config_dir(CONFIG_DIR_NAME))


def config_path() -> Path:
    return config_dir() / CONFIG_FILE


def load_config() -> dict:
    """Read config.toml. Returns empty dict if file doesn't exist or is malformed."""
    path = config_path()
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return {}


def save_config(config: dict) -> None:
    """Atomically write config dict to config.toml with 0o600 permissions."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file in same dir, then atomic rename
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            tomli_w.dump(config, f)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        # Clean up temp file if anything goes wrong
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def resolve_token(explicit: str | None = None) -> str | None:
    """Return the API token using precedence: explicit > env > config file.

    Returns None if no token found anywhere (caller decides how to handle).
    """
    if explicit:
        return explicit

    from_env = os.environ.get(ENV_VAR)
    if from_env:
        return from_env

    config = load_config()
    return config.get("auth", {}).get("token")
