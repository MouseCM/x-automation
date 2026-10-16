"""Load API keys from the .env file next to the scripts."""

import os


def load_env_file(path: str = ".env") -> None:
    """Load KEY=value lines from .env next to this script. Real environment variables win."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), path)
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if value.startswith("#"):  # "KEY=   # comment" means no value
                value = ""
            value = value.split(" #", 1)[0].strip()  # drop trailing comment
            key, value = key.removeprefix("export ").strip(), value.strip("'\"")
            if value and key not in os.environ:
                os.environ[key] = value
