"""Project settings: file paths and everything read from .env (see .env.example for every key).

Importing this module loads .env once, so any script or module that imports it sees the values, whatever
order things are imported in. Real environment variables (e.g. `export GEMINI_API_KEY=...`) win over .env.

Values the Studio can change while it runs (X credentials, keys saved on the Account tab) are read through
functions, not frozen into constants, so a change applies without a restart.
"""

import os

ROOT = os.path.dirname(os.path.abspath(__file__))
WORKFLOW = os.path.join(ROOT, "workflow")

ENV_PATH = os.path.join(ROOT, ".env")
POSTS_CSV = os.path.join(ROOT, "post.csv")
TOPICS_CSV = os.path.join(ROOT, "topics.csv")
DB_PATH = os.path.join(WORKFLOW, "studio.db")
BRAND_PATH = os.path.join(WORKFLOW, "brand.md")
PERSONA_PATH = os.path.join(WORKFLOW, "persona.md")
AUTOPILOT_PATH = os.path.join(WORKFLOW, "autopilot.json")
X_SESSION_PATH = os.path.join(WORKFLOW, ".x_session.json")


# --------------------------------------------------------------------------- .env file

def _parse_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, value = line.split("=", 1)
    value = value.strip()
    if value.startswith("#"):  # "KEY=   # comment" means no value
        value = ""
    value = value.split(" #", 1)[0].strip()  # drop trailing comment
    return key.removeprefix("export ").strip(), value.strip("'\"")


def load_env_file(path: str = ENV_PATH) -> None:
    """Load KEY=value lines from .env into os.environ. Variables that are already set win."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            if (kv := _parse_line(line)) and kv[1] and kv[0] not in os.environ:
                os.environ[kv[0]] = kv[1]


def save_env(values: dict[str, str], section: str = "# X account for the Studio (workflow/publisher.py)") -> None:
    """Set KEY=value lines in .env (adding missing keys under `section`) and in os.environ. Blank = unset."""
    lines = open(ENV_PATH, encoding="utf-8").read().splitlines() if os.path.exists(ENV_PATH) else []
    remaining = dict(values)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].removeprefix("export ").strip()
        if "=" in line and not line.lstrip().startswith("#") and key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"
    if remaining:
        if not any(l.startswith(section) for l in lines):
            lines += ["", f"{section}. Never commit this file."]
        lines += [f"{k}={v}" for k, v in remaining.items()]
    tmp = ENV_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)  # holds secrets: owner-only
    os.replace(tmp, ENV_PATH)
    for k, v in values.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


def env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or "").strip() or default


load_env_file()


# --------------------------------------------------------------------------- settings

def gemini_key() -> str:
    return env("GEMINI_API_KEY") or env("GOOGLE_API_KEY")


def summary_model() -> str:
    """Model for one-line crawler summaries."""
    return env("GEMINI_MODEL", "gemini-3.1-flash-lite")


def draft_models() -> list[str]:
    """Writers to try in order, falling back when one is overloaded. Pro models need billing on the account."""
    chain = env("GEMINI_DRAFT_MODEL", "gemini-3.8-flash,gemini-3.7-flash,gemini-3.1-flash-lite")
    return [m.strip() for m in chain.split(",") if m.strip()]


def max_chars() -> int:
    """X's post length limit: 280 for standard accounts; X Premium allows long posts."""
    try:
        return int(env("X_MAX_CHARS", "280"))
    except ValueError:
        return 280
