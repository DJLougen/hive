"""Config loading with documented precedence: env > file > defaults."""

import os

DEFAULTS = {"host": "127.0.0.1", "port": "8080", "debug": "false"}


def load_config(file_cfg: dict[str, str], environ: dict[str, str] | None = None) -> dict[str, str]:
    """Merge defaults, file config and APP_-prefixed env vars.

    Precedence (highest wins): APP_<KEY> env var > file config > defaults.
    """
    environ = os.environ if environ is None else environ
    env_cfg = {
        k[4:].lower(): v for k, v in environ.items() if k.startswith("APP_")
    }
    merged = dict(DEFAULTS)
    merged.update(env_cfg)
    merged.update({k.lower(): v for k, v in file_cfg.items()})
    return merged
